"""The typesetting target: what the output is built into, and by which binary.

Three things were hardcoded in three places and they are not three things. `unicode-math`
**cannot run under pdftex** -- measured, it is a hard error -- so the preamble decides the
engine, and it decides whether non-ASCII is a defect or the correct answer.
"""

from __future__ import annotations

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
