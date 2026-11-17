"""Descriptions on cropped figures: the one place author prose crosses into LaTeX syntax.

Two measured facts drive every decision here, and both were read back out of the PDF rather
than assumed from the source:

- **`alt=` is silent without tagging.** A page carrying a description compiles clean, exit 0,
  no warning, and the PDF comes out `Tagged: no` with zero `/Alt` entries. A surface that
  accepted a description without saying so would be promising a reader who is never reached.
- **The PDF-string conversion does not expand text commands.** `\\%`, `\\{`, `\\}`, `\\&`,
  `\\$`, `\\#` and `\\_` round-trip exactly; `\\textbackslash`, `\\textasciitilde` and
  `\\textasciicircum` arrive *literally*, as those words.

The description must never be derived from the recognizer. Model-written diagram descriptions
are the measured fabrication class (DESIGN 5.5.6b) and alt text is aimed at the one reader who
cannot check them against the drawing -- so it is author-typed or absent.
"""

from __future__ import annotations

import binascii
import re
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from handzoo.core.emit import describe, figure_tex
from handzoo.core.validate import ascii_gate, delimiter_gate

WIDTH = r"0.6\textwidth"
NASTY = "50% of {x} & $y$ plus a\\b and 3#4 with x_1 ~ ^ done"


def _alt_of(pdf: Path) -> str | None:
    """The `/Alt` string, decoded. Stored as UTF-16BE hex, not as a literal `(...)` string --
    grepping for the latter is why an earlier check reported no alt text at all."""
    raw = pdf.read_bytes()
    for m in re.finditer(rb"stream\r?\n", raw):
        start = m.end()
        end = raw.find(b"endstream", start)
        try:
            out = zlib.decompress(raw[start:end])
        except Exception:
            continue
        found = re.search(rb"/Alt\s*<([0-9A-Fa-f]+)>", out)
        if found:
            return binascii.unhexlify(found.group(1)).decode("utf-16-be").lstrip("\ufeff")
    return None


# ------------------------------------------------------------------------------- the escaping


def test_every_latex_special_in_a_description_is_escaped() -> None:
    """An unbalanced `}` ends the key early and a `%` comments out the rest of the line, so
    an un-escaped description could fail a page's compile over prose the author never wrote
    as markup."""
    out = describe(NASTY)
    assert "\\%" in out and "\\{" in out and "\\}" in out
    assert "\\&" in out and "\\$" in out and "\\#" in out and "\\_" in out
    assert "\\string\\" in out, "a bare backslash would start a control sequence"


def test_the_three_text_commands_that_do_not_survive_are_not_used() -> None:
    """Measured: they reach `/Alt` as the words `\\textbackslash` and so on. Using them would
    put LaTeX source into the text a screen reader speaks."""
    out = describe("a \\ b ~ c ^ d")
    assert "textbackslash" not in out
    assert "textasciitilde" not in out
    assert "textasciicircum" not in out


def test_a_caret_is_left_alone_and_a_tilde_becomes_a_space() -> None:
    """Both measured rather than reasoned: `^` compiles clean and reaches `/Alt` unchanged,
    and a `~` arrives as a space whatever we do, so it is made one up front."""
    assert describe("a ^ b") == "a ^ b"
    assert describe("a~b") == "a b"


def test_newlines_collapse_into_one_line() -> None:
    """A blank line inside a macro argument ends the paragraph and takes the argument with
    it. The description is one string in the PDF regardless."""
    assert describe("line one\n\n  line two   with   runs  ") == "line one line two with runs"


def test_an_escaped_description_does_not_break_the_gates() -> None:
    """The failure mode that would be worst: a page refused because of its own alt text."""
    page = "Some prose.\n" + figure_tex("fig.pdf", width=WIDTH, description=NASTY) + "\n"
    assert delimiter_gate.check(page).passed, delimiter_gate.check(page).report()
    assert ascii_gate.check(page, fragment=True).passed


# --------------------------------------------------------------------------- what gets emitted


def test_without_a_description_the_emitted_figure_is_what_it_is_today() -> None:
    """Every crop in all nine projects on disk goes through this path. The a11y work must not
    rewrite them."""
    assert figure_tex("fig.pdf", width=WIDTH) == f"\\includegraphics[width={WIDTH}]{{fig.pdf}}"


def test_a_missing_description_is_noted_only_when_tagging_is_on() -> None:
    """Under tagging the silence is a real gap and worth a line in the file. Without tagging
    the same remark would be true of every crop ever cropped, which is noise."""
    assert "unlabelled for a screen reader" in figure_tex("fig.pdf", width=WIDTH, tagging=True)
    assert "unlabelled" not in figure_tex("fig.pdf", width=WIDTH)


def test_the_note_sits_on_its_own_line() -> None:
    """The marker a crop replaces can sit mid-sentence, so a trailing `%` would comment out
    whatever followed it on that line."""
    emitted = figure_tex("fig.pdf", width=WIDTH, tagging=True)
    first, _, rest = emitted.partition("\n")
    assert "%" not in first, first
    assert rest.lstrip().startswith("%")


def test_a_description_is_emitted_even_without_tagging() -> None:
    """It is the author's work, it costs nothing, and it starts working the day they add
    `\\DocumentMetadata{tagging=on}`. Discarding it would be the surface deciding that effort
    was wasted. The surface says it is currently unread; it does not throw it away."""
    assert "alt={A square}" in figure_tex("fig.pdf", width=WIDTH, description="A square")


def test_a_description_without_tagging_says_so_in_the_file() -> None:
    """The author's call, 2026-11-17: keep the text, and let the file say it reaches nobody.

    The alternative was refusing the field until tagging is on, which would cost a
    re-description of every crop made before. Accepting it in *silence* was never one of the
    options: the whole point of the field is a reader who cannot check it against the drawing,
    and the author is the only person who can know it is currently unread.
    """
    emitted = figure_tex("fig.pdf", width=WIDTH, description="A square")
    assert "alt={A square}" in emitted, "the description is kept"
    assert "inert" in emitted and "tagging=on" in emitted, emitted


@pytest.mark.parametrize("description, tagging, note", [
    ("A square", False, "inert"),
    # The quadrant that caught a real bug: an `elif` on the wrong condition printed "inert"
    # over a description that works perfectly. The correct case is the one that exposed it.
    ("A square", True, None),
    ("", True, "unlabelled"),
    ("", False, None),
    ("   ", False, None),
    ("   ", True, "unlabelled"),
])
def test_exactly_one_note_or_none_per_quadrant(description, tagging, note) -> None:
    """Two notes, four quadrants, and never both. Each note is a claim about who reads this
    figure, and two claims at once would be a contradiction in the author's own file."""
    emitted = figure_tex("fig.pdf", width=WIDTH, description=description, tagging=tagging)
    present = {word for word in ("inert", "unlabelled") if word in emitted}
    assert present == ({note} if note else set()), emitted


def test_the_inert_note_sits_on_its_own_line_too() -> None:
    """Same reason as the missing-description note, and a separate test because it is a
    separate branch: a trailing `%` would comment out the rest of the author's sentence."""
    emitted = figure_tex("fig.pdf", width=WIDTH, description="A square")
    first, _, rest = emitted.partition("\n")
    assert "%" not in first.replace("\\%", ""), first
    assert rest.lstrip().startswith("%")


def test_the_inert_note_does_not_break_the_gates() -> None:
    """It names `\\DocumentMetadata{tagging=on}`, so it carries braces -- inside a comment,
    which the delimiter gate must skip. A note about accessibility that refused the page
    would be the worst possible trade."""
    page = ("Some prose.\n"
            + figure_tex("fig.pdf", width=WIDTH, description="A square {with} 100% $ink$")
            + "\n")
    assert delimiter_gate.check(page).passed, delimiter_gate.check(page).report()
    assert ascii_gate.check(page, fragment=True).passed


# ------------------------------------------------------------------------------- the engine


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_the_description_reaches_the_structure_tree(tmp_path) -> None:
    """Settled where it is actually settled: in the PDF.

    A unit test can only prove we wrote `alt=`. Whether the author's prose survives escaping,
    the keyval parser and the PDF-string conversion is a question for the engine.
    """
    (tmp_path / "fig.tex").write_text(
        "\\documentclass{standalone}\n\\usepackage{tikz}\n"
        "\\begin{document}\\begin{tikzpicture}\\draw (0,0) rectangle (1,1);"
        "\\end{tikzpicture}\\end{document}\n", encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "fig.tex"],
                   cwd=tmp_path, capture_output=True, timeout=180)

    (tmp_path / "doc.tex").write_text(
        "\\DocumentMetadata{tagging=on}\n\\documentclass{article}\n\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        + figure_tex("fig.pdf", width="2cm", description=NASTY, tagging=True)
        + "\n\\end{document}\n", encoding="utf-8")
    run = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "doc.tex"],
                         cwd=tmp_path, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stdout[-1500:]

    alt = _alt_of(tmp_path / "doc.pdf")
    assert alt is not None, "the description never reached the structure tree"
    # Exact, with the two documented and measured transformations: a `~` is a space, and a
    # backslash that runs into letters absorbs the one space after it.
    assert alt == "50% of {x} & $y$ plus a\\band 3#4 with x_1 ^ done", repr(alt)


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_a_description_without_tagging_reaches_nobody_and_still_compiles(tmp_path) -> None:
    """The measurement the whole design rests on, asserted rather than remembered.

    If this ever starts producing an `/Alt`, the warning the surface shows is wrong and
    should go -- which is exactly the kind of thing that rots silently in a comment.
    """
    (tmp_path / "fig.tex").write_text(
        "\\documentclass{standalone}\n\\usepackage{tikz}\n"
        "\\begin{document}\\begin{tikzpicture}\\draw (0,0) rectangle (1,1);"
        "\\end{tikzpicture}\\end{document}\n", encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "fig.tex"],
                   cwd=tmp_path, capture_output=True, timeout=180)

    (tmp_path / "doc.tex").write_text(
        "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"
        + figure_tex("fig.pdf", width="2cm", description="A plain square outline")
        + "\n\\end{document}\n", encoding="utf-8")
    run = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "doc.tex"],
                         cwd=tmp_path, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, "it compiles clean, which is why the silence is dangerous"
    assert _alt_of(tmp_path / "doc.pdf") is None, \
        "no tagging, so the description reaches nobody -- the surface must keep saying so"
    assert "inert" in (tmp_path / "doc.tex").read_text(encoding="utf-8"), \
        "and the file says so, next to the description, where the author will see it"
