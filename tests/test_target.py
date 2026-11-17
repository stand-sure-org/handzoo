"""The typesetting target: what the output is built into, and by which binary.

Three things were hardcoded in three places and they are not three things. `unicode-math`
**cannot run under pdftex** -- measured, it is a hard error -- so the preamble decides the
engine, and it decides whether non-ASCII is a defect or the correct answer.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from handzoo.core import target as T

UNICODE_PREAMBLE = (
    "\\documentclass{report}\n"
    "\\usepackage{graphicx}\n"
    "\\usepackage{unicode-math}\n"
    "\\setmathfont{Latin Modern Math}\n"
)


def test_a_project_with_no_preamble_builds_exactly_as_it_does_today() -> None:
    """Every corpus in the author's hands was built with pdflatex and our own preamble. The
    target exists to let a project say otherwise, never to change what a silent project does."""
    t = T.Target.generated()
    assert t.engine == "pdflatex"
    assert not t.accepts_unicode
    assert "\\documentclass{article}" in t.base
    assert t.origin == "generated"


def test_a_preamble_that_cannot_run_on_pdflatex_selects_the_engine_itself() -> None:
    """Measured on the author's file: `! Package unicode-math Error: Cannot be run with
    pdftex!`. The engine is therefore *derived*, never configured -- asking the author to
    name it as well would be asking them to repeat themselves and to get it wrong."""
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="common-preamble.tex")
    assert t.engine == "lualatex"
    assert t.accepts_unicode


@pytest.mark.parametrize("line", [
    "\\usepackage[math-style=ISO]{unicode-math}",
    "\\RequirePackage{fontspec}",
    "\\usepackage{amsmath,unicode-math}",
])
def test_the_engine_is_detected_through_the_spellings_people_actually_use(line: str) -> None:
    assert T.Target.from_text(f"\\documentclass{{article}}\n{line}\n").engine == "lualatex"


def test_a_commented_out_package_does_not_change_the_engine() -> None:
    """The author's preamble carries commented-out alternatives -- `% \\setmathfont{Stix Two
    Math}`, and more. A detector that reads comments would switch the engine on a line the
    document never executes, and the failure would arrive as a compile error nobody ordered."""
    t = T.Target.from_text(
        "\\documentclass{article}\n"
        "% \\usepackage{unicode-math}  % considered, not used\n"
        "\\usepackage{amsmath}\n")
    assert t.engine == "pdflatex"
    assert not t.accepts_unicode


def test_a_preamble_with_no_documentclass_is_given_one_and_says_so() -> None:
    """The author's `common-preamble.tex` starts at `\\usepackage{graphicx}`; their `main.tex`
    supplies `\\documentclass{report}`. A preamble we cannot compile alone is no use to a gate,
    so we add the line -- visibly, never silently, because the class we pick is a guess."""
    t = T.Target.from_text("\\usepackage{unicode-math}\n", origin="common-preamble.tex",
                           document_class="report")
    assert t.base.startswith("\\documentclass{report}")
    assert t.added_document_class


def test_a_preamble_that_brings_its_own_class_keeps_it() -> None:
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex", document_class="article")
    assert "\\documentclass{report}" in t.base
    assert "\\documentclass{article}" not in t.base
    assert not t.added_document_class


def test_declarations_are_still_generated_under_an_author_preamble() -> None:
    """An invented macro is still invented. The author's preamble is authoritative about what
    *it* defines; it says nothing about what the recognizer made up, and that guard stays."""
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    preamble = t.preamble_for(["$\\invented{x}$"])
    assert "\\documentclass{report}" in preamble
    assert "invented" in preamble


def test_the_origin_and_its_hash_are_recorded() -> None:
    """The author called this preamble tentative and intends to rework it, so the copy in the
    project will drift from the file it came from. The copy is what builds -- reproducibility --
    and the hash is what lets a later run say the original has moved on. A hint, never identity
    (DESIGN 11.1.3)."""
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="/somewhere/common-preamble.tex")
    assert t.origin == "/somewhere/common-preamble.tex"
    assert len(t.origin_hash) == 64
    assert T.Target.from_text(UNICODE_PREAMBLE + "\n% edited\n",
                              origin="/somewhere/common-preamble.tex").origin_hash != t.origin_hash


# --------------------------------------------- what the target changes in the Normalizer

SCRIPT_C, BLACKBOARD_C = "\U0001D49E", "ℂ"


def test_under_a_pdflatex_target_r1_still_maps_unicode_away() -> None:
    """Unchanged, and it must stay unchanged: under pdflatex a bare codepoint is the silent
    corruption the gate exists for (R1, ch16 p3's literal section sign)."""
    from handzoo.core.normalize import normalize
    r = normalize(f"a {BLACKBOARD_C} b", standalone=False)
    assert BLACKBOARD_C not in r.text
    assert any(rule.startswith("R1") for rule in r.rules)


def test_under_a_unicode_target_the_letterforms_are_left_alone() -> None:
    """DESIGN 11.0.1g: the author writes a doubled blackboard-bold and a curly script as
    *different letters*, and we measured the recognizer flattening both to `\\mathcal`. R1 was
    the second place that distinction died -- `unicode_to_latex` rewrites the codepoint into a
    command before any gate sees it.

    Measured under `unicode-math`: `\\mathcal{C}` typesets U+1D49E, `\\mathbb{C}` typesets
    U+2102, and the raw characters round-trip to exactly those codepoints. So the pipeline no
    longer has to choose -- whether the *model* distinguishes them is a separate question, and
    still open.
    """
    from handzoo.core.normalize import normalize
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    r = normalize(f"a {BLACKBOARD_C} and {SCRIPT_C} b", standalone=False, target=t)
    assert BLACKBOARD_C in r.text and SCRIPT_C in r.text
    assert not any(rule.startswith("R1") for rule in r.rules)


def test_a_unicode_target_does_not_declare_characters_as_bullets() -> None:
    """Today a page carrying the author's own notation emits

        \\DeclareUnicodeCharacter{2102}{\\ensuremath{\\bullet}}  % TODO: no mapping

    and typesets a bullet. Under an engine that can simply draw the character, generating that
    stub *is* the defect -- it renotates a mark, which is constraint #5."""
    from handzoo.core.normalize import normalize
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    r = normalize(f"${BLACKBOARD_C}$", standalone=True, target=t)
    assert "DeclareUnicodeCharacter" not in r.text
    assert "\\bullet" not in r.text
    assert BLACKBOARD_C in r.text


def test_a_standalone_page_is_built_inside_the_targets_preamble() -> None:
    """R4 wrote our own `\\documentclass{article}` preamble into every standalone page. A
    project that named a preamble would have been compiled against a different document than
    the one it assembles into."""
    from handzoo.core.normalize import normalize
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    r = normalize("Hello.", standalone=True, target=t)
    assert "\\usepackage{unicode-math}" in r.text
    assert "\\documentclass{article}" not in r.text


# ------------------------------------------------------ the project remembers its target

def test_a_project_keeps_its_own_copy_of_the_preamble(tmp_path) -> None:
    """The copy is what builds. The author called this preamble tentative and intends to rework
    the theorem machinery, so the file it came from *will* move on -- and a project that read
    the original on every run would silently change what it produces when a file it does not
    own is edited. Reproducibility lives in the copy; the hash is how a later run notices the
    original has drifted (DESIGN 11.1.3's pattern, applied to a preamble)."""
    origin = tmp_path / "common-preamble.tex"
    origin.write_text(UNICODE_PREAMBLE, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    T.Target.from_file(origin).save(project)
    loaded = T.Target.load(project)

    assert loaded.engine == "lualatex" and loaded.accepts_unicode
    assert "unicode-math" in loaded.base
    assert loaded.origin == str(origin)
    assert not loaded.drifted_from(origin)

    no_class = T.Target.from_text("\\usepackage{unicode-math}\n", origin=str(origin),
                                  document_class="report")
    other = tmp_path / "p2"; other.mkdir()
    no_class.save(other)
    assert T.Target.load(other).added_document_class, "how it was made must survive the copy"

    origin.write_text(UNICODE_PREAMBLE + "\n% the author kept writing\n", encoding="utf-8")
    assert loaded.drifted_from(origin), "a moved original must be noticeable, never silent"


def test_a_project_that_never_named_a_preamble_loads_the_generated_target(tmp_path) -> None:
    (tmp_path / "project").mkdir()
    assert T.Target.load(tmp_path / "project").engine == "pdflatex"


def test_the_saved_copy_is_a_compilable_preamble_not_a_config_file(tmp_path) -> None:
    """It is written as LaTeX, with provenance in `%` comments, because the thing an author
    most wants to do with a preamble we copied is read it and edit it."""
    project = tmp_path / "project"
    project.mkdir()
    T.Target.from_text(UNICODE_PREAMBLE, origin="/x/common-preamble.tex").save(project)
    saved = (project / "preamble.tex").read_text(encoding="utf-8")
    assert saved.startswith("%")
    assert "/x/common-preamble.tex" in saved
    assert "\\usepackage{unicode-math}" in saved
    # and the comments must not be able to change the engine (they are stripped before detection)
    assert T.Target.from_text(saved).engine == "lualatex"


def test_a_cached_render_is_keyed_on_the_target_as_well_as_the_source() -> None:
    """Adopting a preamble changes the engine and the document a page is built inside without
    touching one page file. A cache keyed on the source alone would go on serving pdflatex
    renders of the old preamble -- a stale pane that looks authoritative."""
    from handzoo.adapters.ui_server import typeset_key
    page = "Hello.\n"
    assert typeset_key(page, T.Target.generated()) != typeset_key(
        page, T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex"))


@pytest.mark.skipif(not shutil.which("lualatex"), reason="lualatex not installed")
def test_a_letterform_survives_from_fragment_to_typeset_page(tmp_path) -> None:
    """The whole point, checked at the only place that settles it: the PDF.

    This is the test the first attempt needed and did not have. `normalize`'s standalone branch
    learned to stop declaring characters as bullets; `chapter_preamble` -- which is what a
    *fragment* reaches, through both the compile gate and `chapter.tex` -- did not, so a page
    carrying the author's own letterforms still got

        \\DeclareUnicodeCharacter{2102}{\\ensuremath{\\bullet}}

    in the document it was actually built into. An end-to-end assertion catches that; a
    unit test of the branch I happened to be editing did not.
    """
    from handzoo.core.assemble import assemble
    from handzoo.core.normalize import chapter_preamble
    from handzoo.core.pipeline import PageOutcome

    target = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    fragment = f"Let ${BLACKBOARD_C}$ be a category, and ${SCRIPT_C}$ a presheaf on it.\n"

    preamble = chapter_preamble([fragment], target=target)
    assert "DeclareUnicodeCharacter" not in preamble, "the fragment's own preamble, too"
    assert "\\bullet" not in preamble

    page = tmp_path / "page-0001.tex"
    page.write_text(fragment, encoding="utf-8")
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass", gates={})],
                      target=target)
    assert "page-0001" in master.read_text(encoding="utf-8"), "a Unicode page stays in the chapter"

    subprocess.run([target.engine, "-interaction=nonstopmode", "-halt-on-error", master.name],
                   cwd=tmp_path, capture_output=True, timeout=180, check=False)
    pdf = master.with_suffix(".pdf")
    assert pdf.exists(), "the chapter must build"
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
    assert BLACKBOARD_C in text, "the blackboard-bold reached the page"
    assert SCRIPT_C in text, "and the script letter did, as a different character"


# ------------------------- characters no font draws, under an engine that draws everything else

# Measured: no math font carries these three classes, so under `unicode-math` they are not a
# letterform the engine can simply render -- they are a silent drop waiting to happen. That is
# the distinction this section defends: ℂ is left alone because the font *has* it; ✗ is repaired
# because nothing does.
BALLOT_X, CHECK, CIRCLED_1 = "\u2717", "\u2713", "\u2460"
BALLOT_PAGE = f"The second axiom fails {BALLOT_X} on this example.\n"


def test_a_commented_out_package_does_not_count_as_supplied() -> None:
    """The mirror of `test_a_commented_out_package_does_not_change_the_engine`, and the author
    told us why it matters: their preamble is still being figured out, so a commented-out line
    is a thing they are *not* loading. Counting it as supplied withholds the package the
    repair needs and leaves `\\ding` undefined -- an error, from a line meant to prevent one."""
    from handzoo.core.declarations import packages_in
    assert "pifont" not in packages_in("% \\usepackage{pifont}\n\\usepackage{graphicx}\n")
    assert "graphicx" in packages_in("% \\usepackage{pifont}\n\\usepackage{graphicx}\n")


def test_a_measured_character_is_repaired_under_a_unicode_target() -> None:
    """The repair path was dead code under exactly the engine we now default to.

    `chapter_preamble` passed an empty character list whenever the target accepted Unicode, so
    `CHARACTER_REPAIRS` -- eight checkmarks, five ballot crosses and thirteen circled digits
    measured in the corpus -- never fired. The character reached LuaLaTeX, no font carried it,
    and it left the page as a `Missing character` line in a log nobody reads.

    `\\DeclareUnicodeCharacter` cannot be the mechanism here: it is an inputenc feature and
    inert under LuaLaTeX. `\\newunicodechar` is the one that works.
    """
    from handzoo.core.normalize import chapter_preamble
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    preamble = chapter_preamble([BALLOT_PAGE], target=t)

    assert "\\usepackage{newunicodechar}" in preamble
    assert f"\\newunicodechar{{{BALLOT_X}}}{{\\ding{{55}}}}" in preamble
    assert "DeclareUnicodeCharacter" not in preamble, "inert under this engine"


def test_the_package_a_repair_needs_is_emitted_not_assumed() -> None:
    """Our own preamble loads `pifont`; the author's does not, and nothing noticed.

    The repair maps ✗ to `\\ding{55}`, which lives in pifont. `CHARACTER_REPAIRS` has carried
    the package name all along and `declarations_for` threw it away (`glyph, _pkg = repair`),
    which was invisible while the only preamble in play already supplied it.
    """
    from handzoo.core.normalize import chapter_preamble
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    assert "pifont" not in t.base, "the fixture must not supply it, or this proves nothing"

    preamble = chapter_preamble([BALLOT_PAGE], target=t)
    assert "\\usepackage{pifont}" in preamble
    assert preamble.index("\\usepackage{pifont}") < preamble.index("\\newunicodechar"), \
        "a package must be loaded before the mapping that uses it"


def test_a_letterform_is_left_to_the_font_under_a_unicode_target() -> None:
    """The counterpart, and the line this must not cross. ℂ is the author's own notation and
    the math font draws it, so repairing it would be the renotation of constraint #5 -- the
    very defect the Unicode target was built to stop."""
    from handzoo.core.normalize import chapter_preamble
    t = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    preamble = chapter_preamble([f"Let ${BLACKBOARD_C}$ be a category.\n"], target=t)
    assert "newunicodechar" not in preamble
    assert "pifont" not in preamble, "no repair fired, so no package is owed"


def test_the_pdflatex_path_keeps_the_mechanism_that_works_there() -> None:
    """Regression. Under our generated preamble the engine is pdflatex, where
    `\\DeclareUnicodeCharacter` is the working mechanism and `\\newunicodechar` is not what
    126 pages were measured against."""
    from handzoo.core.normalize import chapter_preamble
    preamble = chapter_preamble([BALLOT_PAGE])
    assert f"\\DeclareUnicodeCharacter{{{ord(BALLOT_X):04X}}}" in preamble
    assert "newunicodechar" not in preamble


@pytest.mark.skipif(not shutil.which("lualatex"), reason="lualatex not installed")
def test_the_repaired_characters_are_not_dropped_by_the_engine(tmp_path) -> None:
    """Settled where it is actually settled: the engine's own log, through the real path.

    A unit test can only prove we emitted a mapping. Whether the character survives is a
    question for LuaLaTeX, which answers it as `Missing character: There is no ✗ (U+2717)` --
    the exact line `compile_gate._dropped_characters` reads.

    Nothing here hands `assemble` a preamble: it calls `chapter_preamble` itself and inlines
    the result, so this exercises the path a run takes. An earlier draft also wrote
    `preamble.tex` beside the pages, which `assemble` never reads -- the test would have gone
    green on a file nothing used, which is the failure mode this suite has hit before.

    All three repaired classes appear, because the risky line is the one that withholds
    `amssymb`: loading it under `unicode-math` is four "already defined" errors, and the gate
    would refuse a page that is in fact correct.
    """
    from handzoo.core.assemble import assemble
    from handzoo.core.pipeline import PageOutcome

    target = T.Target.from_text(UNICODE_PREAMBLE, origin="x.tex")
    body = f"The second axiom fails {BALLOT_X}, the first holds {CHECK}, see case {CIRCLED_1}.\n"
    page = tmp_path / "page-0001.tex"
    page.write_text(body, encoding="utf-8")

    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass", gates={})],
                      target=target)
    assert "\\usepackage{amssymb}" not in master.read_text(encoding="utf-8"), \
        "unicode-math already supplies \\checkmark, and adding amssymb is a hard error"

    subprocess.run([target.engine, "-interaction=nonstopmode", "-halt-on-error", master.name],
                   cwd=tmp_path, capture_output=True, timeout=180, check=False)

    assert master.with_suffix(".pdf").exists(), "the chapter must build"
    log = master.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    assert not [ln for ln in log.splitlines() if ln.startswith("!")], "the build errored"
    for ch in (BALLOT_X, CHECK, CIRCLED_1):
        assert f"U+{ord(ch):04X}" not in log, f"the engine dropped {ch!r}"

    text = subprocess.run(["pdftotext", str(master.with_suffix(".pdf")), "-"],
                          capture_output=True, text=True).stdout
    assert "second axiom fails" in text, "the page was typeset, not merely compiled"
    # Known and not fixed here: pifont and \textcircled render correctly but their text layer
    # extracts as "7" and "1" -- a substitution one layer below the one we gate. A text font
    # carrying U+2717 natively (the luaotfload fallback item) is what fixes both.


# ------------------------------------------------------- \DocumentMetadata and the class line

TAGGED_PREAMBLE = (
    "\\DocumentMetadata{lang=en-US, tagging=on}\n"
    "\\usepackage{graphicx}\n"
)


def test_the_added_class_line_goes_below_documentmetadata_not_above_it() -> None:
    """`\\DocumentMetadata` is required to come *before* `\\documentclass`, and LaTeX refuses
    the document outright when it does not -- `\\DocumentMetadata should be used only before`,
    measured, fatal, no PDF.

    So the one case `added_document_class` exists for is also the case that breaks it: a
    preamble that turns tagging on and leaves the class to us. Prepending at the top put our
    line first and the project could never have built.
    """
    t = T.Target.from_text(TAGGED_PREAMBLE)
    assert t.added_document_class
    assert t.base.index("\\DocumentMetadata") < t.base.index("\\documentclass"), t.base
    # and the metadata's own argument is not cut in half by the insertion
    assert "\\DocumentMetadata{lang=en-US, tagging=on}" in t.base


def test_a_multiline_documentmetadata_argument_is_not_split() -> None:
    """The argument is routinely written across lines, and the class line goes after all of
    it -- inserting on a line boundary would land inside the braces."""
    t = T.Target.from_text(
        "\\DocumentMetadata{\n  lang=en-US,\n  tagging=on,\n  pdfstandard=ua-2,\n}\n"
        "\\usepackage{graphicx}\n")
    before, _, after = t.base.partition("\\documentclass")
    assert before.count("{") == before.count("}"), f"class line landed inside braces: {before!r}"
    assert "pdfstandard=ua-2" in before
    assert "\\usepackage{graphicx}" in after


def test_a_commented_out_documentmetadata_does_not_move_the_class_line() -> None:
    """A commented directive is not a directive -- the same rule `_DOCUMENTCLASS` already
    applies to `\\documentclass` itself."""
    t = T.Target.from_text("% \\DocumentMetadata{tagging=on}\n\\usepackage{graphicx}\n")
    assert t.base.startswith("\\documentclass")


def test_a_preamble_that_already_has_both_is_left_alone() -> None:
    t = T.Target.from_text(
        "\\DocumentMetadata{tagging=on}\n\\documentclass{book}\n\\usepackage{graphicx}\n")
    assert not t.added_document_class
    assert t.base.startswith("\\DocumentMetadata")
    assert "\\documentclass{book}" in t.base


def test_a_tagging_project_actually_builds(tmp_path) -> None:
    """The only test that settles this one, because the failure is the engine's.

    This test passed once while the bug was present, and the reason is the second half of
    this fix: `assemble` was never handed the target, so the preamble under test never
    reached the document being compiled. The structural assertions below run *before* the
    compile so the vacuous pass cannot come back -- a build that proves nothing must not
    read as a build that proved something (constraint #6).

    Measured, so the engine here is not a guess: `\\DocumentMetadata{tagging=on}` builds
    under **pdflatex** as well as lualatex (exit 0, `Tagged: yes`, PDF 2.0), and this
    preamble loads neither `unicode-math` nor `fontspec`, so `engine` is pdflatex. The skip
    therefore names the engine the target actually chose.
    """
    from handzoo.core.assemble import assemble
    from handzoo.core.pipeline import PageOutcome

    target = T.Target.from_text(TAGGED_PREAMBLE)
    assert target.added_document_class
    page = tmp_path / "page-0001.tex"
    page.write_text("A tagged page.\n", encoding="utf-8")
    target.save(tmp_path)
    # No `target=`: the point is that the project's own preamble arrives without being asked
    # for, which is what every caller but one actually does.
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass",
                                             gates={})])
    written = master.read_text(encoding="utf-8")
    assert "\\DocumentMetadata" in written, "the author's preamble never reached the chapter"
    assert written.index("\\DocumentMetadata") < written.index("\\documentclass"), written[:400]

    if shutil.which(target.engine) is None:
        pytest.skip(f"{target.engine} not installed")
    run = subprocess.run(
        [target.engine, "-interaction=nonstopmode", "-halt-on-error", master.name],
        cwd=tmp_path, capture_output=True, text=True, timeout=180)
    log = (tmp_path / master.with_suffix(".log").name)
    text = log.read_text(errors="replace") if log.exists() else run.stdout
    assert "DocumentMetadata should be used only before" not in text, text[-2000:]
    assert run.returncode == 0, text[-2000:]
    assert (tmp_path / master.with_suffix(".pdf").name).exists()


# ------------------------------------------------------------------------- tagging detection


@pytest.mark.parametrize("text,expected", [
    ("\\DocumentMetadata{tagging=on}\n\\documentclass{article}\n", True),
    ("\\DocumentMetadata{ tagging = on }\n\\documentclass{article}\n", True),
    ("\\DocumentMetadata{\n  lang=en-US,\n  tagging=on,\n}\n\\documentclass{article}\n", True),
    ("\\DocumentMetadata{tagging=off}\n\\documentclass{article}\n", False),
    ("\\DocumentMetadata{lang=en-US}\n\\documentclass{article}\n", False),
    ("% \\DocumentMetadata{tagging=on}\n\\documentclass{article}\n", False),
    ("\\documentclass{article}\n\\usepackage{graphicx}\n", False),
    ("\\documentclass{article}\n% a note about tagging=on in prose\n", False),
])
def test_tagging_is_read_from_the_directives_own_braces(text: str, expected: bool) -> None:
    """`tagging=off` is the documented way to turn it back off, so the value is parsed rather
    than the key merely found -- and a commented directive is not a directive, the same rule
    `\\documentclass` detection already follows."""
    assert T.Target.from_text(text).tagging is expected


def test_the_generated_target_does_not_turn_tagging_on() -> None:
    """Nine corpora were built without it. The target exists to let a project say otherwise."""
    assert T.Target.generated().tagging is False


def test_a_tagging_project_gets_the_tikzcd_shim_and_others_do_not(tmp_path) -> None:
    """Measured: `tagging=on` plus a `tikzcd` environment is a fatal error under both engines
    (`File ended while scanning use of \\\\__math_grab_dollar_delim:w`, no PDF). The shim is
    emitted on tagging alone -- the author can add a diagram later, and the hook is inert when
    `tikz-cd` is never loaded.
    """
    from handzoo.core.normalize import chapter_preamble

    tagged = chapter_preamble(["body\n"], target=T.Target.from_text(TAGGED_PREAMBLE))
    assert "\\AddToHook{env/tikzcd/begin}{\\MathCollectFalse}" in tagged

    for other in (None, T.Target.generated(), T.Target.from_text(UNICODE_PREAMBLE)):
        assert "AddToHook" not in chapter_preamble(["body\n"], target=other), other


def test_a_tikzcd_page_in_a_tagging_project_builds(tmp_path) -> None:
    """Through `assemble`, and asserted structurally *before* the compile -- the lesson of
    6.2.1, where an end-to-end test passed because the preamble never arrived.

    The author draws commutative diagrams by hand; we never emit `tikzcd` ourselves
    (constraint #4). This is the page they authored.
    """
    from handzoo.core.assemble import assemble
    from handzoo.core.pipeline import PageOutcome

    target = T.Target.from_text(TAGGED_PREAMBLE + "\\usepackage{tikz-cd}\n")
    assert target.tagging
    page = tmp_path / "page-0001.tex"
    page.write_text("Before.\n\\begin{tikzcd}\nA \\ar[r] & B\n\\end{tikzcd}\nAfter.\n",
                    encoding="utf-8")
    target.save(tmp_path)
    master = assemble(tmp_path, [PageOutcome(page=1, output=str(page), verdict="pass",
                                             gates={})])
    written = master.read_text(encoding="utf-8")
    assert "\\AddToHook{env/tikzcd/begin}" in written, "the shim never reached the chapter"
    assert written.index("\\DocumentMetadata") < written.index("\\documentclass")

    if shutil.which(target.engine) is None:
        pytest.skip(f"{target.engine} not installed")
    run = subprocess.run(
        [target.engine, "-interaction=nonstopmode", "-halt-on-error", master.name],
        cwd=tmp_path, capture_output=True, text=True, timeout=180)
    log = tmp_path / master.with_suffix(".log").name
    text = log.read_text(errors="replace") if log.exists() else run.stdout
    assert "math_grab_dollar_delim" not in text, text[-1500:]
    assert run.returncode == 0, text[-2000:]
