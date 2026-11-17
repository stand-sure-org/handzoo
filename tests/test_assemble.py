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


@pytest.mark.skipif(not compile_gate.engine_available(), reason="pdflatex not installed")
def test_a_macro_the_recognizer_invented_does_not_break_the_chapter(tmp_path: Path) -> None:
    r"""A standalone page gets a guarded declaration for every macro the recognizer invented
    (R8). A fragment is told "not declared here" -- and the master declared only unmapped
    characters, never macros. So a fragment using `\foo{x}` built as a standalone page and
    broke the chapter, and nobody saw it, because fragments were never compiled.

    The master now declares them, the same way a standalone page does -- still flagged
    `TODO`, so an invented name is visible, not quietly accepted."""
    assemble(tmp_path, [_outcome(tmp_path, 1, body=r"By \foo{x} we mean the thing.")])
    text = (tmp_path / MASTER).read_text(encoding="utf-8")
    result = compile_gate.check(text, base_dir=tmp_path)
    assert result.passed, result.report()
    assert "TODO" in text and "foo" in text


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


# ---------------------------------- the manifest is a record, not the disk


def test_a_page_whose_file_is_gone_is_a_placeholder_not_a_crash(tmp_path: Path) -> None:
    """Every path here comes from a row written earlier, and the file it names can be gone by
    now. `assemble` read it unguarded, so a run of several hours ended in a bare
    `FileNotFoundError` instead of a chapter -- and through the ingest thread that surfaced as
    `state: "failed"`, which is a lie: every page had been recognized and written, and only
    the assembly broke.
    """
    rows = [_outcome(tmp_path, 1), _outcome(tmp_path, 2)]
    (tmp_path / "page-0001.tex").unlink()

    master = assemble(tmp_path, rows)          # must not raise
    text = master.read_text(encoding="utf-8")
    assert "\\input{page-0001}" not in text, "a file that is not there cannot be \\input"
    assert "PAGE 1" in text, "and must not vanish from the chapter either"
    assert "\\input{page-0002}" in text, "the pages that are there still assemble"


def test_a_missing_file_is_not_reported_as_a_gate_failure(tmp_path: Path) -> None:
    """Two different facts that send the author to two different places: one says the
    recognizer produced something the gates refused, the other says their text is not where
    the manifest says it is. Collapsing them sends someone hunting in the wrong file -- the
    same distinction the excluded marker already draws."""
    rows = [_outcome(tmp_path, 1)]
    (tmp_path / "page-0001.tex").unlink()

    text = assemble(tmp_path, rows).read_text(encoding="utf-8")
    said = [ln for ln in text.splitlines() if "page-0001" in ln or "PAGE 1" in ln]
    assert any("not on disk" in ln for ln in said), said
    # Not the chapter's standing header, which legitimately explains that pages which *did*
    # fail a gate appear as placeholders -- only what this page's own lines claim about it.
    assert not any("fail" in ln.lower() for ln in said), said


def test_a_quarantined_sibling_is_named_rather_than_guessed_at(tmp_path: Path) -> None:
    """The realistic way a recorded path goes missing. A corrected page is re-gated on save,
    and a page that now fails is renamed to `.fail.tex` while the manifest is amended -- two
    operations, so they can disagree. "Your text is in quarantine" and "your text has
    vanished" are very different things to read after a long run."""
    rows = [_outcome(tmp_path, 1)]
    (tmp_path / "page-0001.tex").rename(tmp_path / "page-0001.fail.tex")

    text = assemble(tmp_path, rows).read_text(encoding="utf-8")
    assert "quarantine" in text
    assert "page-0001.fail.tex" in text


def test_an_empty_page_file_is_a_hole_and_is_marked_as_one(tmp_path: Path) -> None:
    """An emitted page carries at least its provenance comment, so a blank file is damage.
    `\\input`ing it contributes nothing and leaves a chapter that reads as complete -- which
    is the case this module exists to refuse."""
    rows = [_outcome(tmp_path, 1), _outcome(tmp_path, 2)]
    (tmp_path / "page-0001.tex").write_text("   \n\n", encoding="utf-8")

    text = assemble(tmp_path, rows).read_text(encoding="utf-8")
    assert "\\input{page-0001}" not in text
    assert "empty" in text
    assert "\\input{page-0002}" in text


def test_the_caller_hears_about_every_page_it_could_not_read(tmp_path: Path) -> None:
    """The chapter says it in itself, but a chapter is only read when someone opens it and the
    author reads the terminal. A warning computed and never printed is the same as none."""
    rows = [_outcome(tmp_path, n) for n in (1, 2, 3)]
    (tmp_path / "page-0001.tex").unlink()
    (tmp_path / "page-0002.tex").write_text("", encoding="utf-8")

    heard: list[tuple[int, str]] = []
    assemble(tmp_path, rows, on_absent=lambda page, why: heard.append((page, why)))

    assert [page for page, _ in heard] == [1, 2]
    assert "not on disk" in heard[0][1] and "empty" in heard[1][1]


def test_a_chapter_with_nothing_on_disk_does_not_blame_the_gates(tmp_path: Path) -> None:
    """"No page passed its gates" would be false and would send the author to the recognizer.
    These pages passed; their text is not where the manifest says it is."""
    rows = [_outcome(tmp_path, 1), _outcome(tmp_path, 2)]
    for n in (1, 2):
        (tmp_path / f"page-{n:04d}.tex").unlink()

    text = assemble(tmp_path, rows).read_text(encoding="utf-8")
    assert "no page text found on disk" in text
    assert "no page passed" not in text


@pytest.mark.skipif(not compile_gate.engine_available(), reason="pdflatex not installed")
def test_a_chapter_full_of_placeholders_still_builds(tmp_path: Path) -> None:
    """The promise, and the reason a placeholder is the right answer rather than a refusal:
    whatever has gone wrong with the files, the author still gets a document they can open and
    a page they can point at. A placeholder that broke the build it reports on would be worse
    than the problem it describes."""
    rows = [_outcome(tmp_path, n) for n in (1, 2, 3, 4)]
    (tmp_path / "page-0001.tex").unlink()
    (tmp_path / "page-0002.tex").rename(tmp_path / "page-0002.fail.tex")
    (tmp_path / "page-0003.tex").write_text("", encoding="utf-8")

    assemble(tmp_path, rows)
    result = compile_gate.check((tmp_path / MASTER).read_text(encoding="utf-8"),
                                base_dir=tmp_path)
    assert result.passed, result.report()


def test_a_page_released_from_quarantine_is_not_called_broken(tmp_path: Path) -> None:
    """The mirror of the quarantine case, and reachable the same way: a save that fixes a
    quarantined page renames it back to `.tex` and amends the manifest, two operations that
    can disagree. If only the rename lands, the newest row still says `fail`.

    A bare failure placeholder would then tell the author their page is broken *after they
    fixed it*, which is the most discouraging thing this file could say. It is still not
    included -- the record says refused, and a filename is not grounds to overrule that -- but
    the reason given is the true one.
    """
    outcome = _outcome(tmp_path, 1, verdict="fail")
    (tmp_path / "page-0001.fail.tex").rename(tmp_path / "page-0001.tex")

    heard: list[tuple[int, str]] = []
    text = assemble(tmp_path, [outcome],
                    on_absent=lambda page, why: heard.append((page, why))
                    ).read_text(encoding="utf-8")

    assert "released from quarantine" in text
    assert "\\input{page-0001}" not in text, "the run still records it as refused"
    assert heard and "released" in heard[0][1]


def test_a_page_that_really_did_fail_still_says_so(tmp_path: Path) -> None:
    """The check above must not soften the ordinary case: a quarantined page whose file is
    exactly where the manifest says reads as refused, with the gate named."""
    outcome = _outcome(tmp_path, 1, verdict="fail")

    text = assemble(tmp_path, [outcome]).read_text(encoding="utf-8")
    assert "MISSING" in text and "released" not in text


@pytest.mark.parametrize("damage", ["delete", "quarantine", "release", "empty", "directory"])
def test_no_state_of_the_disk_makes_assembly_raise(tmp_path: Path, damage: str) -> None:
    """The invariant all three callers depend on, asserted once rather than three times.

    `cli_convert` had no guard at all, so a raise ended a run of several hours in a traceback;
    `ingest` caught it and reported `state: "failed"`, which claims the *run* failed when every
    page had been recognized and written; `ui_server` had none either, so an uncaught exception
    in the handler drops the connection with no response at all, after the project has already
    been mutated. Three surfaces, three wrong answers, one cause -- so the fix belongs here and
    the test belongs here.

    `directory` is not a realistic case; it is the one that makes the guard an `OSError` guard
    rather than a `FileNotFoundError` one, which is the difference between handling what went
    wrong and handling the one way it went wrong first.
    """
    rows = [_outcome(tmp_path, 1), _outcome(tmp_path, 2)]
    page = tmp_path / "page-0001.tex"
    if damage == "delete":
        page.unlink()
    elif damage == "quarantine":
        page.rename(tmp_path / "page-0001.fail.tex")
    elif damage == "release":
        rows[0] = _outcome(tmp_path, 1, verdict="fail")
        (tmp_path / "page-0001.fail.tex").rename(page)
    elif damage == "empty":
        page.write_text("", encoding="utf-8")
    elif damage == "directory":
        page.unlink()
        page.mkdir()

    master = assemble(tmp_path, rows)      # the assertion is that this returns
    assert "PAGE 1" in master.read_text(encoding="utf-8"), "and never silently omits the page"
