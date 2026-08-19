"""Emitter, pipeline and CLI — with the model stubbed throughout.

The pipeline's job is not to be clever; it is to lose nothing. These tests are mostly about
what survives a run that goes wrong.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from handzoo.adapters import cli_convert
from handzoo.core import pipeline
from handzoo.core.emit import Emission, emit, report
from handzoo.core.recognize.base import Mark, Recognition
from handzoo.core.recognize.ollama_vlm import RecognitionError
from handzoo.core.validate.base import GateResult


def _recognition(markup: str = "Some \\emph{markup}.", marks: int = 0) -> Recognition:
    inventory = tuple(
        Mark(kind="unknown", description="d", context="c", placement="inline")
        for _ in range(marks))
    return Recognition(markup=markup, inventory=inventory,
                       provider="stub", model="stub-model")


class _StubRecognizer:
    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.seen: list[Path] = []

    def recognize(self, page: Path) -> Recognition:
        self.seen.append(page)
        n = int(page.stem.split("-")[1].split("_")[0])
        if n in self.fail_on:
            raise RecognitionError(f"stub refused page {n}")
        return _recognition(f"Page {n} body.")


# --------------------------------------------------------------------------- emitter


def test_fragment_is_the_default_mode() -> None:
    """A reMarkable page break is not a manuscript page break.

    Pages are assembled with `\\input`; emitting standalone by default would bake a page
    boundary into the book.
    """
    fragment = emit(_recognition())
    assert "\\documentclass" not in fragment.text
    assert "\\documentclass" in emit(_recognition(), mode="standalone").text


def test_provenance_is_emitted_not_assumed() -> None:
    """A .tex with no record of what produced it is worth much less once the model moves."""
    text = emit(_recognition(marks=2), page=7).text
    assert "stub/stub-model" in text
    assert "page 7" in text
    assert "marks inventoried: 2" in text


def test_every_emission_states_what_was_not_checked() -> None:
    assert "NOT CHECKED" in emit(_recognition()).text


def test_an_unimplemented_target_raises_rather_than_silently_emitting_latex() -> None:
    with pytest.raises(ValueError, match="not implemented"):
        emit(_recognition(), target="markdown")  # type: ignore[arg-type]


def test_report_never_prints_an_unqualified_pass() -> None:
    """A guardian that says PASS trains the reader to stop checking where it is weakest."""
    text = report(Emission(text="x", gates=(GateResult("ascii"),)))
    assert "PASS" in text
    assert "not that it is true" in text


def test_unverified_gates_are_named_and_not_counted_as_passes() -> None:
    e = Emission(text="x", gates=(GateResult("ascii"), GateResult("compile", checked=False)))
    assert not e.passed
    assert e.unverified == ("compile",)
    assert "unverified: compile" in report(e)


# --------------------------------------------------------------------------- pipeline

pytestmark_pdf = pytest.mark.skipif(
    not pipeline.rasterize.shutil.which("pdftoppm")
    or not pipeline.rasterize.shutil.which("pdflatex"),
    reason="poppler and/or pdflatex not installed")


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> Path:
    import subprocess
    tmp = tmp_path_factory.mktemp("pdf")
    (tmp / "d.tex").write_text(
        "\\documentclass{article}\\begin{document}A\\newpage B\\newpage C\\end{document}\n",
        encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "d.tex"],
                   cwd=tmp, capture_output=True, check=False)
    if not (tmp / "d.pdf").exists():
        pytest.skip("pdflatex produced no PDF")
    return tmp / "d.pdf"


@pytestmark_pdf
def test_pages_stream_as_they_finish(pdf: Path, tmp_path: Path) -> None:
    """A run that reveals its work only at the end is one you cannot interrupt or trust."""
    produced = pipeline.convert(pdf, tmp_path, _StubRecognizer())
    first = next(produced)
    assert first.page == 1
    assert Path(first.output).exists(), "page 1 is on disk before page 3 is attempted"


@pytestmark_pdf
def test_manifest_is_written_per_page_not_at_the_end(pdf: Path, tmp_path: Path) -> None:
    """The case a manifest exists for is the run that did not finish."""
    gen = pipeline.convert(pdf, tmp_path, _StubRecognizer())
    next(gen)
    lines = (tmp_path / pipeline.MANIFEST).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["page"] == 1


@pytestmark_pdf
def test_resume_skips_completed_pages(pdf: Path, tmp_path: Path) -> None:
    """A crash on page 40 of 51 must not discard the first 39."""
    list(pipeline.convert(pdf, tmp_path, _StubRecognizer(), last=2))

    second = _StubRecognizer()
    done = list(pipeline.convert(pdf, tmp_path, second, resume=True))

    assert [o.page for o in done] == [3], "pages 1 and 2 were already recorded"
    assert len(second.seen) == 1, "a resumed run must not re-recognize completed pages"


@pytestmark_pdf
def test_a_page_the_recognizer_refuses_does_not_abort_the_run(pdf: Path,
                                                              tmp_path: Path) -> None:
    """One bad page in fifty must not cost the other forty-nine."""
    outcomes = list(pipeline.convert(pdf, tmp_path, _StubRecognizer(fail_on={2})))
    assert [o.page for o in outcomes] == [1, 2, 3]
    assert [o.done for o in outcomes] == [True, False, True]
    assert "stub refused" in outcomes[1].error


@pytestmark_pdf
def test_failing_pages_are_written_where_a_build_cannot_consume_them(pdf: Path,
                                                                    tmp_path: Path) -> None:
    """Discarding a failing page throws away the thing a human needs in order to correct it.

    So it is written — under a name a build will not pick up by accident.
    """
    class Dropper(_StubRecognizer):
        def recognize(self, page: Path) -> Recognition:
            return _recognition("no markers here", marks=3)

    (outcome, *_) = list(pipeline.convert(pdf, tmp_path, Dropper(), last=1))
    assert outcome.verdict == "fail"
    assert outcome.output.endswith(".fail.tex")
    assert Path(outcome.output).exists()


@pytestmark_pdf
def test_fragments_report_compile_as_unverified_not_passed(pdf: Path, tmp_path: Path) -> None:
    """A fragment has no preamble, so compiling it in isolation proves nothing."""
    (outcome, *_) = list(pipeline.convert(pdf, tmp_path, _StubRecognizer(), last=1))
    assert outcome.gates["compile"] == "skipped"


# --------------------------------------------------------------------------- cli


def test_cli_refuses_a_thinking_checkpoint_before_doing_any_work(tmp_path: Path) -> None:
    out = io.StringIO()
    code = cli_convert.main([str(tmp_path / "x.pdf"), "--model", "qwen3-vl:8b"], stream=out)
    assert code == 2
    assert "Thinking checkpoint" in out.getvalue()


@pytest.mark.parametrize("spec,expected", [
    (None, (1, None)),
    ("4", (4, 4)),
    ("3-9", (3, 9)),
])
def test_cli_page_ranges(spec, expected) -> None:
    assert cli_convert._parse_range(spec) == expected


# --------------------------------------------------------------------------- verdicts


def _emission(*gates: GateResult) -> Emission:
    return Emission(text="x", gates=gates)


def test_three_verdicts_because_two_cannot_say_this_honestly() -> None:
    """Found by running the CLI: with two states, *every fragment failed*.

    Fragments have no preamble, so the compile gate can never run on them. Folding
    "not verified" into "failed" made the signal carry no information; folding it into
    "passed" would claim a check that never happened.
    """
    from handzoo.core.validate.base import Failure

    clean = _emission(GateResult("ascii"), GateResult("delimiters"))
    skipped = _emission(GateResult("ascii"), GateResult("compile", checked=False))
    broken = _emission(GateResult("ascii", (Failure(detail="nope"),)))

    assert clean.verdict == "pass"
    assert skipped.verdict == "unverified"
    assert broken.verdict == "fail"


def test_unverified_is_not_quarantined_only_failure_is() -> None:
    """Nothing refused an unverified page, so it must not be filed as though something did."""
    from handzoo.core.validate.base import Failure

    assert not _emission(GateResult("compile", checked=False)).failed
    assert _emission(GateResult("ascii", (Failure(detail="x"),))).failed
