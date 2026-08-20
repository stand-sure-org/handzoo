"""The three pure-logic gates.

Where possible these run against the **frozen recognizer output in `baseline/`** rather than
hand-written examples. Those bytes are real measured failures, and pinning them means a
refactor cannot quietly stop catching what they caught.

No test here calls a model. Recognition is nondeterministic and is measured, never asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handzoo.core.validate import ascii_gate, compile_gate, delimiter_gate
from handzoo.core.validate.base import Failure, GateResult

BASELINE = Path(__file__).resolve().parent.parent / "baseline"


def _standalone(body: str) -> str:
    return ("\\documentclass{article}\n\\usepackage{amsmath,amssymb}\n"
            f"\\begin{{document}}\n{body}\n\\end{{document}}\n")


# --------------------------------------------------------------------------- base


def test_a_gate_that_could_not_run_is_not_a_pass() -> None:
    """The distinction has to survive into the report, or a suite goes green on nothing."""
    skipped = GateResult("compile", checked=False)
    assert not skipped.passed
    assert not skipped
    assert "SKIPPED" in skipped.report()
    assert "not a pass" in skipped.report()


def test_failures_carry_detail_not_just_a_bool() -> None:
    r = GateResult("x", (Failure(detail="broken", line=7, excerpt="\\oplus"),))
    assert "line 7" in r.report()
    assert "broken" in r.report()


# --------------------------------------------------------------------------- ascii


def test_ascii_accepts_plain_output() -> None:
    assert ascii_gate.check("Nothing but ASCII here.").passed


@pytest.mark.parametrize("ch", ["§", "Σ", "ü", "⋯", "→"])
def test_ascii_rejects_each_character_measured_in_the_corpus(ch: str) -> None:
    """Every one of these actually appeared in recognizer output and had to be mapped."""
    result = ascii_gate.check(f"text {ch} more")
    assert not result.passed
    assert ch in result.failures[0].detail


def test_ascii_reports_position_and_codepoint() -> None:
    """"There is non-ASCII somewhere" is not actionable; a line and a codepoint are."""
    (failure,) = ascii_gate.check("clean\nbad Σ here").failures
    assert failure.line == 2
    assert "U+03A3" in failure.detail
    assert failure.column == 5


def test_ascii_exempts_a_preamble_that_declares_support() -> None:
    """The rule is "no *undefined* Unicode", not "no Unicode"."""
    declared = "\\usepackage{fontspec}\n\\usepackage{unicode-math}\nΣ"
    assert ascii_gate.check(declared).passed
    assert not ascii_gate.check(declared, allow_declared=False).passed


def test_ascii_gate_is_not_the_broken_grep() -> None:
    """Regression guard for the brief's `! grep -P '[^\\x00-\\x7F]'` form.

    That construction reports clean on any platform whose grep lacks `-P`, because `!`
    inverts the error into a pass. A gate whose failure path looks like success is worse
    than no gate, so assert we detect what it would have missed.
    """
    assert not ascii_gate.check("Σ").passed


# --------------------------------------------------------------------------- delimiters


@pytest.mark.parametrize("body", [
    r"inline \(x\) and display \[y\]",
    r"dollars $x$ and $$y$$",
    r"\begin{align}a\end{align}",
    r"escaped \$ is not a delimiter",
    "a comment % $ unclosed in here\nand text after",
    r"nested \begin{center}\begin{tabular}{c}a\end{tabular}\end{center}",
])
def test_delimiters_accept_balanced_input(body: str) -> None:
    assert delimiter_gate.check(body).passed, delimiter_gate.check(body).report()


@pytest.mark.parametrize("body,expect", [
    (r"open \( and never close", "never closed"),
    (r"stray \) alone", "no matching"),
    (r"$ unclosed math", "never closed"),
    (r"\begin{align} unclosed", "never closed"),
    (r"\begin{align}x\end{center}", "not what is open"),
])
def test_delimiters_reject_imbalance(body: str, expect: str) -> None:
    result = delimiter_gate.check(body)
    assert not result.passed
    assert any(expect in f.detail for f in result.failures), result.report()


def test_delimiters_report_where_it_opened_not_where_it_ended() -> None:
    """Pointing at end-of-document is useless; the opener is the thing to go fix."""
    (failure,) = delimiter_gate.check("line one\nline two $ opens here\nline three").failures
    assert failure.line == 2


# --------------------------------------------------------------------------- compile


@pytest.mark.skipif(not compile_gate.engine_available(), reason="pdflatex not installed")
def test_compile_accepts_a_valid_document() -> None:
    assert compile_gate.check(_standalone(r"Hello $x^2$.")).passed


@pytest.mark.skipif(not compile_gate.engine_available(), reason="pdflatex not installed")
def test_compile_catches_the_real_oplus_defect() -> None:
    r"""Frozen regression: `\section*{\oplus COUNTING}` from measured page 2.

    A math-mode command in text mode. This is the failure that proved the compile gate earns
    its place — it is invisible to both static gates.
    """
    result = compile_gate.check(_standalone(r"\section*{\oplus COUNTING}"))
    assert not result.passed
    assert any("Missing $" in f.detail for f in result.failures), result.report()


@pytest.mark.skipif(not compile_gate.engine_available(), reason="pdflatex not installed")
def test_compile_attributes_the_error_to_a_line() -> None:
    result = compile_gate.check(_standalone("fine\n\n" + r"\section*{\oplus X}"))
    assert not result.passed
    assert any(f.line for f in result.failures), result.report()


# --------------------------------------------------------------------------- frozen corpus


@pytest.mark.skipif(not (BASELINE / "page-02-qwen3vl-8b.tex").exists(),
                    reason="baseline artefact absent")
def test_frozen_page_two_still_fails_to_compile() -> None:
    """Measured page 2 must keep failing, and for the reason we recorded.

    A gate that stops catching a known-bad page has regressed, however green it looks.
    """
    latex = (BASELINE / "page-02-qwen3vl-8b.tex").read_text(encoding="utf-8")
    assert ascii_gate.check(latex).passed, "page 2 was ASCII-clean; that has not changed"
    if compile_gate.engine_available():
        result = compile_gate.check(latex)
        assert not result.passed
        assert any("Missing $" in f.detail for f in result.failures), result.report()


@pytest.mark.skipif(not (BASELINE / "page-01-qwen3vl-8b.tex").exists(),
                    reason="baseline artefact absent")
def test_frozen_page_one_passes_every_gate_and_is_still_false() -> None:
    """The page that motivates the whole design.

    ASCII-clean, balanced, compiles — and it dropped four inline glyphs, turning two
    consistent bullets into a contradiction. Asserting that it *passes* is the point: these
    three gates cannot see semantic substitution, and the suite should say so out loud
    rather than let anyone believe green means correct.
    """
    latex = (BASELINE / "page-01-qwen3vl-8b.tex").read_text(encoding="utf-8")
    assert ascii_gate.check(latex).passed
    assert delimiter_gate.check(latex).passed
    if compile_gate.engine_available():
        assert compile_gate.check(latex).passed


# --------------------------------------------------------------- normalizer regressions


def test_a_comment_never_precedes_the_structure_it_describes() -> None:
    r"""R7 injected `% TODO` immediately before the `\end{tabular}` it was preserving.

    `%` comments to end of line, so the closer vanished: the parser treated the environment
    as open, swallowed the following prose into the table, and a synthesised closer landed
    after it. **Both the delimiter and compile gates passed on the result** — content was not
    dropped, it was silently relocated, which is worse.
    """
    from handzoo.core.normalize import normalize

    markup = ("\\begin{tabular}{l}\\begin{itemize}\\item a\\item b\\end{itemize}"
              "\\end{tabular}\n\nThis sentence must survive after the table.\n")
    body = normalize(markup).text.split("\\begin{document}", 1)[1]

    assert body.count("\\end{tabular}") == 1, "a commented-out closer gets duplicated"
    table = body[body.index("\\begin{tabular}"):body.index("\\end{tabular}")]
    assert "must survive" not in table, "prose after the table was swallowed into it"


def test_normalization_converges() -> None:
    """`handzoo review` will re-normalize corrected pages.

    Unstripped, R4 re-wrapped an already-standalone document and added two newlines *per
    pass*, without bound — a document that grows every time it is touched is exactly wrong
    for a correction loop.
    """
    from handzoo.core.normalize import normalize

    text = "\\section*{x}\nbody\n"
    sizes = []
    for _ in range(4):
        text = normalize(text).text
        sizes.append(len(text))
    assert len(set(sizes[1:])) == 1, f"normalization does not converge: {sizes}"


@pytest.mark.parametrize("env", ["tikzpicture", "tikzcd", "CD", "xy", "forest", "prooftree"])
def test_no_fabricated_diagram_environment_survives(env: str) -> None:
    r"""R9 was written against the one environment that had been measured.

    Cheng ch18 is category theory, so the recognizer reached for `tikzcd` instead of
    `tikzpicture` — and R9, matching that literal string, passed it straight through to a
    "Environment tikzcd undefined" build failure on two pages. The rule is *never fabricate
    a diagram*, so it has to match the family, not the one member already seen.

    Loading `tikz-cd` would make these compile. That is the wrong fix and is forbidden by
    hard constraint #4: it converts a caught fabrication into a plausible, compiling,
    invented commutative diagram — the silent corruption the project exists to refuse.

    Bodies are synthetic. The measured pages are third-party published text and stay out
    of the repo.
    """
    from handzoo.core.normalize import normalize

    for markup in (
        f"before \\begin{{{env}}}A \\arrow[r] & B\\end{{{env}}} after",
        f"before \\begin{{{env}}}[scale=0.5]A \\to B no closer at all",
        f"before A \\to B \\end{{{env}}} orphan closer",
    ):
        out = normalize(markup).text
        assert env not in out, f"{env} reached the document: {out[:200]}"
        assert "before" in out, "surrounding prose must survive"


def test_no_tikzpicture_survives_normalization() -> None:
    r"""An unterminated `\begin{tikzpicture}` slipped past the paired pattern and failed the
    build with "Environment tikzpicture undefined" (Naive Math p1). The recognizer is told
    never to invent tikz; when it does anyway, none of it may reach the document.
    """
    from handzoo.core.normalize import normalize

    for markup in (
        r"before \begin{tikzpicture}[scale=0.5]\draw (0,0);\end{tikzpicture} after",
        r"before \begin{tikzpicture}[scale=0.5]\draw (0,0); no closer at all",
    ):
        out = normalize(markup).text
        assert "tikzpicture" not in out, out[:200]
        assert "fabricated" in out, "the fabrication must be recorded, not merely deleted"
