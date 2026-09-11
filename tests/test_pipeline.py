"""Emitter, pipeline and CLI — with the model stubbed throughout.

The pipeline's job is not to be clever; it is to lose nothing. These tests are mostly about
what survives a run that goes wrong.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest

from handzoo.adapters import cli_convert
from handzoo.core import pipeline
from handzoo.core.corrections import Correction, CorrectionLog, pristine_path
from handzoo.core.emit import Emission, emit, report
from handzoo.core.recognize.base import Mark, Recognition
from handzoo.core.recognize.ollama_vlm import RecognitionError
from handzoo.core.validate import compile_gate
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
def test_a_fragment_run_is_compile_gated(pdf: Path, tmp_path: Path) -> None:
    """Formerly `skipped`: a fragment has no preamble, so compiling it alone proved nothing.
    It is now compiled inside the chapter's preamble, which is where it will be built."""
    (outcome, *_) = list(pipeline.convert(pdf, tmp_path, _StubRecognizer(), last=1))
    assert outcome.gates["compile"] == "pass"


def test_the_manifest_reads_as_its_newest_row_per_page(tmp_path: Path) -> None:
    """The manifest is a log: `--resume` and a re-gate on save both add a row for a page that
    already has one. The log is right to keep both. A *reader* that takes the first row serves
    the stale one, and every consumer that did so got a different page wrong."""
    rows = [
        {"page": 2, "output": None, "verdict": "fail", "gates": {}, "error": "ollama down"},
        {"page": 1, "output": "page-0001.tex", "verdict": "pass", "gates": {}},
        {"page": 2, "output": "page-0002.tex", "verdict": "pass", "gates": {}},
    ]
    (tmp_path / pipeline.MANIFEST).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    read = pipeline.read_manifest(tmp_path)

    assert [o.page for o in read] == [1, 2], "one row per page, in page order"
    assert read[1].done and read[1].output == "page-0002.tex", "the newest row wins"


def test_concurrent_appends_never_interleave_a_row(tmp_path: Path) -> None:
    """A run in a background thread and a save from the surface both append.

    Each row must reach the file in one write: on an O_APPEND file a single write lands whole,
    so concurrent writers can only interleave *between* rows. A row written in two calls --
    the JSON, then the newline -- lets another writer's row in between and glues two rows into
    one corrupt line, which `read_manifest` rightly refuses to skip. Measured: that variant
    fails this test every time; the single write passes.
    """
    big = [{"gate": "coverage", "detail": "x" * 200, "line": i, "excerpt": "y" * 200}
           for i in range(60)]                          # ~25 KB, several times a real row

    def writer(page: int) -> None:
        for _ in range(40):
            pipeline.append_manifest(tmp_path, pipeline.PageOutcome(
                page=page, output=None, verdict="fail", gates={}, findings=big))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / pipeline.MANIFEST).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 160
    assert all(json.loads(line)["findings"] == big for line in lines)


def test_a_missing_manifest_reads_as_no_pages_not_an_error(tmp_path: Path) -> None:
    """The UI polls a directory a run may not have written to yet."""
    assert pipeline.read_manifest(tmp_path) == []


@pytestmark_pdf
def test_a_resumed_run_assembles_every_page_not_only_the_ones_it_ran(
        pdf: Path, tmp_path: Path, monkeypatch) -> None:
    """Measured on the l11 run: its chapter began at page 3.

    A resumed page is skipped, not yielded, and the CLI assembled only what this invocation
    yielded -- so every page finished by an earlier invocation was absent from `chapter.tex`,
    with no placeholder. That is the omission `assemble()` exists to prevent.
    """
    monkeypatch.setattr(cli_convert, "OllamaRecognizer",
                        lambda **_: _StubRecognizer())
    out = tmp_path / "run"
    assert cli_convert.main([str(pdf), "-o", str(out), "--pages", "1-2"],
                            stream=io.StringIO()) == 0
    assert cli_convert.main([str(pdf), "-o", str(out), "--resume"],
                            stream=io.StringIO()) == 0

    chapter = (out / "chapter.tex").read_text(encoding="utf-8")
    for page in (1, 2, 3):
        assert f"\\input{{page-{page:04d}}}" in chapter, f"page {page} missing from the chapter"


# ------------------------------------------------------- author work is not overwritten


def _pages_seen(recognizer: _StubRecognizer) -> list[int]:
    return [int(p.stem.split("-")[1].split("_")[0]) for p in recognizer.seen]


def _corrected(out_dir: Path, page: int, verdict: str, text: str) -> Path:
    """A page the author has worked on: their text on disk, and the log row that says so."""
    target = out_dir / f"page-{page:04d}.tex"
    target.write_text(text, encoding="utf-8")
    CorrectionLog.for_run(out_dir).append(Correction(
        page=page, verdict=verdict, source_image="p.png", before="emitted", after=text))
    return target


@pytestmark_pdf
@pytest.mark.parametrize("verdict", ["edited", "cropped", "keep-reviewed", "authored"])
def test_a_rerun_does_not_overwrite_a_page_that_carries_author_work(
        pdf: Path, tmp_path: Path, verdict: str) -> None:
    """DESIGN 11.1.3 bug #2: a re-run wrote every page unconditionally, over the author's
    corrections. A button that ingests a PDF makes that one click away. The page is kept, and
    it is not sent to the recognizer at all."""
    list(pipeline.convert(pdf, tmp_path, _StubRecognizer()))
    mine = _corrected(tmp_path, 2, verdict, "what the author made it\n")

    again = _StubRecognizer()
    list(pipeline.convert(pdf, tmp_path, again))

    assert mine.read_text(encoding="utf-8") == "what the author made it\n"
    assert _pages_seen(again) == [1, 3], "a kept page must not be recognized"


@pytestmark_pdf
def test_an_edit_in_progress_is_author_work_too(pdf: Path, tmp_path: Path) -> None:
    """Autosave writes the page file and records nothing until a verdict -- by design, so a
    half-typed line is never logged as a judgement. So the log cannot be the only evidence: the
    pre-edit snapshot is what marks a page as mid-edit, and a re-run over it would destroy an
    edit that has no log row to recover it from."""
    list(pipeline.convert(pdf, tmp_path, _StubRecognizer()))
    target = tmp_path / "page-0003.tex"
    snapshot = pristine_path(tmp_path, 3)
    snapshot.parent.mkdir(exist_ok=True)
    snapshot.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text("half way through a fix\n", encoding="utf-8")

    list(pipeline.convert(pdf, tmp_path, _StubRecognizer()))

    assert target.read_text(encoding="utf-8") == "half way through a fix\n"


@pytestmark_pdf
@pytest.mark.parametrize("when", ["before the run reaches it", "while it is recognized"])
def test_work_the_author_starts_during_a_run_is_kept_too(pdf: Path, tmp_path: Path,
                                                         when: str) -> None:
    """In-app ingestion overlaps review with recognition -- that is its whole design (DESIGN,
    in-app ingestion D1). Protection read once at the start of a run misses a page the author
    starts on after it: they fix page 3 of the previous run while this one is on page 1, or
    while page 3 itself is being recognized. Either way the run reached page 3 and overwrote
    it. So the check is made again at the moment of writing."""
    list(pipeline.convert(pdf, tmp_path, _StubRecognizer()))
    trigger = 1 if when == "before the run reaches it" else 3

    class AuthorAtWork(_StubRecognizer):
        def recognize(self, page: Path) -> Recognition:
            result = super().recognize(page)
            if _pages_seen(self)[-1] == trigger:
                _corrected(tmp_path, 3, "edited", "fixed mid-run\n")
            return result

    list(pipeline.convert(pdf, tmp_path, AuthorAtWork()))

    assert (tmp_path / "page-0003.tex").read_text(encoding="utf-8") == "fixed mid-run\n"


@pytestmark_pdf
def test_replace_is_the_explicit_instruction_that_overrides_it(pdf: Path,
                                                               tmp_path: Path) -> None:
    """The author's rule (DESIGN 11.1.3a): "replace page 2 with this". The human asserts the
    correspondence, so nothing has to infer it."""
    list(pipeline.convert(pdf, tmp_path, _StubRecognizer()))
    mine = _corrected(tmp_path, 2, "edited", "stale correction\n")

    again = _StubRecognizer()
    list(pipeline.convert(pdf, tmp_path, again, replacing={2}))

    assert "Page 2 body." in mine.read_text(encoding="utf-8")
    assert _pages_seen(again) == [1, 2, 3]


@pytestmark_pdf
@pytest.mark.parametrize("verdict", ["flagged", "skipped", "keep-unreviewed"])
def test_a_page_the_author_only_passed_through_is_not_protected(
        pdf: Path, tmp_path: Path, verdict: str) -> None:
    """These verdicts leave no author text on the page. Protecting them would stop a re-run
    from fixing the very pages the author flagged as wrong."""
    list(pipeline.convert(pdf, tmp_path, _StubRecognizer()))
    CorrectionLog.for_run(tmp_path).append(Correction(
        page=2, verdict=verdict, source_image="p.png", before="emitted"))

    again = _StubRecognizer()
    list(pipeline.convert(pdf, tmp_path, again))

    assert _pages_seen(again) == [1, 2, 3]


@pytestmark_pdf
def test_a_fragment_run_without_pdflatex_reads_unverified_not_passed(
        pdf: Path, tmp_path: Path, monkeypatch) -> None:
    """Constraint #6, for the new gate entry point: a compile that could not run must not
    read as one that passed. Fragment compiles used to be skipped unconditionally, so a
    fragment run's verdict did not depend on the engine; now it does."""
    monkeypatch.setattr(compile_gate, "engine_available", lambda: False)
    (outcome, *_) = list(pipeline.convert(pdf, tmp_path, _StubRecognizer(), last=1))
    assert outcome.gates["compile"] == "skipped"
    assert outcome.verdict == "unverified"


@pytestmark_pdf
def test_the_cli_says_which_pages_it_kept_and_how_to_replace_one(
        pdf: Path, tmp_path: Path, monkeypatch) -> None:
    """Kept silently would be the mirror image of overwritten silently: the author re-runs to
    pick up a new page and cannot tell why an old one did not change."""
    monkeypatch.setattr(cli_convert, "OllamaRecognizer", lambda **_: _StubRecognizer())
    out = tmp_path / "run"
    cli_convert.main([str(pdf), "-o", str(out)], stream=io.StringIO())
    _corrected(out, 2, "edited", "mine\n")

    said = io.StringIO()
    assert cli_convert.main([str(pdf), "-o", str(out)], stream=said) == 0

    text = said.getvalue()
    assert "keeping 1 page" in text and "2" in text
    assert "--replace" in text


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
