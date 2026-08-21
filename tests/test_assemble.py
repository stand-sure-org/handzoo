"""Assembly — the whole chapter as one document.

Without this a run leaves loose fragments and the author must open a LaTeX application just to
see the chapter, which makes HandZoo a step in someone else's pipeline by construction rather
than by choice (DESIGN §11.4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handzoo.core.assemble import MASTER, assemble
from handzoo.core.pipeline import PageOutcome
from handzoo.core.validate import compile_gate


def _outcome(tmp_path: Path, page: int, *, verdict: str = "pass", body: str = "Body.") -> PageOutcome:
    name = f"page-{page:04d}" + (".fail.tex" if verdict == "fail" else ".tex")
    (tmp_path / name).write_text(body + "\n", encoding="utf-8")
    return PageOutcome(page=page, output=str(tmp_path / name), verdict=verdict,
                       gates={"compile": "pass"}, findings=[])


def test_pages_are_input_in_order(tmp_path: Path) -> None:
    master = assemble(tmp_path, [_outcome(tmp_path, 2), _outcome(tmp_path, 1)])
    text = master.read_text(encoding="utf-8")
    assert text.index("page-0001") < text.index("page-0002"), "page order is the document"


def test_a_failed_page_is_neither_included_nor_hidden(tmp_path: Path) -> None:
    """The absence rule, applied to a document rather than a gate.

    Silently `\\input`ing a page that failed lets a broken page into the build — the thing the
    project exists to refuse. Silently omitting it produces a chapter with a hole in it that
    reads as complete, which is worse: the author has no way to notice page 4 is missing.
    """
    master = assemble(tmp_path, [_outcome(tmp_path, 1),
                                 _outcome(tmp_path, 2, verdict="fail")])
    text = master.read_text(encoding="utf-8")
    assert "\\input{page-0002" not in text, "a failing page must not reach the build"
    assert "page-0002" in text, "and must not vanish from it either"
    assert "2" in text and "MISSING" in text.upper()


@pytest.mark.skipif(not compile_gate.engine_available(), reason="pdflatex not installed")
def test_the_assembled_document_actually_builds(tmp_path: Path) -> None:
    """Fragments carry no preamble, so the master owns it — and that is also what makes a
    relative `\\includegraphics` resolve, since the crop figures sit beside the master
    (§7.2's fragment caveat, resolved by assembly)."""
    assemble(tmp_path, [_outcome(tmp_path, 1, body="Hello $x^2$."),
                        _outcome(tmp_path, 2, body="More prose.")])
    result = compile_gate.check((tmp_path / MASTER).read_text(encoding="utf-8"),
                                base_dir=tmp_path)
    assert result.passed, result.report()


def test_a_run_with_nothing_usable_says_so(tmp_path: Path) -> None:
    master = assemble(tmp_path, [_outcome(tmp_path, 1, verdict="fail")])
    assert "no page passed" in master.read_text(encoding="utf-8").lower()


def test_a_standalone_page_cannot_be_input_and_says_so(tmp_path: Path) -> None:
    r"""The tension `--standalone` creates, made visible instead of producing a broken master.

    A standalone page carries its own `\documentclass`, and `\input`ing one inside a document
    fails with *"Can be used only in preamble."* Measured on the author's real ch19 run, which
    used `--standalone` — because that is what makes the compile gate run per page.

    So the two things pull against each other: `--standalone` to verify a page alone,
    fragments to assemble a chapter. The resolution is that a chapter *is* the better unit to
    compile-check — it catches what per-page checking cannot — but that is a design change, not
    a silent workaround here.
    """
    page = tmp_path / "page-0001.tex"
    page.write_text("\\documentclass{article}\n\\begin{document}\nBody.\n\\end{document}\n",
                    encoding="utf-8")
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass",
                                             gates={}, findings=[])])
    text = master.read_text(encoding="utf-8")
    assert "\\input{page-0001}" not in text
    assert "standalone" in text.lower()
    assert "--standalone" in text


def test_the_master_declares_characters_no_fragment_could(tmp_path: Path) -> None:
    r"""Found by the 126-page Number theory run: 9 pages failed the ASCII gate on characters
    `pylatexenc` has no mapping for — checkmarks, ballot crosses, circled digits.

    They are not exotic. A checkmark asserting an axiom holds is a *term in the sentence*
    (`Placement.inline`), and Topology is in the corpus specifically to stress "checkmarks as
    inline annotations".

    The mechanism for an unmappable character is `\DeclareUnicodeCharacter`, which only a
    preamble can carry — so a **fragment has no remedy at all**, and fragments became the
    default when assembly landed. The master owns the preamble, so the master owns this.
    """
    page = tmp_path / "page-0001.tex"
    page.write_text("Axiom holds ✓ and fails ✗, case ①.\n", encoding="utf-8")
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass",
                                             gates={}, findings=[])])
    text = master.read_text(encoding="utf-8")
    for codepoint in ("2713", "2717", "2460"):
        assert f"DeclareUnicodeCharacter{{{codepoint}}}" in text, f"U+{codepoint} undeclared"


def test_only_characters_actually_present_are_declared(tmp_path: Path) -> None:
    """A preamble that declares everything imaginable is noise, and hides the TODOs that
    matter — every generated declaration is a decision the author still owes."""
    page = tmp_path / "page-0001.tex"
    page.write_text("Plain ASCII only.\n", encoding="utf-8")
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass",
                                             gates={}, findings=[])])
    assert "DeclareUnicodeCharacter" not in master.read_text(encoding="utf-8")


def test_a_failed_page_contributes_no_declarations(tmp_path: Path) -> None:
    """Its content is not in the document, so declaring for it would describe a page the
    reader cannot see."""
    page = tmp_path / "page-0001.fail.tex"
    page.write_text("Held back ✓\n", encoding="utf-8")
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="fail",
                                             gates={"coverage": "fail"}, findings=[])])
    assert "DeclareUnicodeCharacter" not in master.read_text(encoding="utf-8")
