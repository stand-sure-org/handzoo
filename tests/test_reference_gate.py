"""The reference gate — a numbered claim that lost its marking.

Built from the first labelled corpus (DESIGN 11.0.1a), not from a guess about what might go
wrong. Lost emphasis was the most frequent real defect: three of eight author corrections.

**The load-bearing insight is that the underline is not emphasis.** Cheng numbers claims but
does not number equations, and the author underlines the thing she numbered — so the mark is
what a `\\label` would be in a typed document. Losing it does not make the page look plainer,
it removes the referent. That is why this is a gate and not a style preference.

Measured on ch18: **0 of 5** numbered claim references in raw recognizer output carried any
marking; the one on a page the author corrected carried it, and the author is the one who put
it there.
"""

from __future__ import annotations

import pytest

from handzoo.core.validate import reference_gate


def test_an_unmarked_numbered_claim_is_flagged() -> None:
    r = reference_gate.check(r"Defn 18.1 A product is an object with two projections.")
    assert r.checked
    assert not r.passed
    assert "Defn 18.1" in r.failures[0].detail


@pytest.mark.parametrize("marked", [
    r"\underline{Defn 18.1} A product is an object.",
    r"\textbf{Prop 18.2} The function is unique.",
    r"\emph{Theorem 4.7} follows immediately.",
    r"\section*{Defn 18.3}",
])
def test_a_marked_numbered_claim_passes(marked: str) -> None:
    assert reference_gate.check(marked).passed, reference_gate.check(marked).report()


@pytest.mark.parametrize("body", [
    "The product of 18 and 2 is 36.",
    r"page 18 of the notes",
    "In chapter 18 we saw that",
    r"$x_{18}$ and $y_2$",
])
def test_ordinary_numbers_are_not_claims(body: str) -> None:
    """A gate that fires on every integer would be turned off within a day."""
    assert reference_gate.check(body).passed, reference_gate.check(body).report()


def test_every_measured_form_is_recognised() -> None:
    """The five that actually occurred in the corpus, plus the abbreviations the author uses."""
    for label in ("Defn 18.1", "Defn 18.3", "Prop 18", "Proposition 18.5", "Defn 18.6",
                  "Thm 2.1", "Lemma 3", "Cor 4.2"):
        r = reference_gate.check(f"{label} some statement here.")
        assert not r.passed, f"{label!r} was not recognised as a numbered claim"


def test_it_reports_a_line_a_human_can_go_to() -> None:
    (f,) = reference_gate.check("first line\n\nDefn 18.1 the definition").failures
    assert f.line == 3


def test_it_flags_for_review_rather_than_failing_the_page() -> None:
    r"""The author's convention is *normally* to underline a numbered claim, not always.

    A hard fail on a convention that holds most of the time trains the reader to bypass the
    gate, which costs more than the defect does. `advisory` keeps the finding visible while
    letting the page through — the three-verdict model applied to a gate rather than a page.
    """
    assert reference_gate.check("Defn 18.1 x").advisory


def test_an_empty_document_is_not_silently_clean() -> None:
    """DESIGN 5.7: nothing to check and checked-and-clean must not read the same."""
    r = reference_gate.check("")
    assert not r.checked, "an empty body cannot have been checked for lost marking"
    assert not r.passed


def test_a_translated_mark_is_not_a_lost_one() -> None:
    r"""Measured, and it corrected a wrong reading of the corpus.

    An earlier count claimed no numbered claim in raw output carried marking, having searched
    only for `\underline`. Four of five carried `\textbf`: the recognizer usually *translates*
    the author's underline into a LaTeX-conventional mark rather than dropping it. A gate that
    demanded the author's exact command would fire on all four and be turned off within a day.
    """
    assert reference_gate.check(r"\textbf{Defn 18.3} A product is an object.").passed


def test_the_defect_the_author_actually_fixed_is_caught() -> None:
    r"""Frozen from the labelled set: p11 arrived with `Prop 18.2` bare and the author
    underlined it. Synthetic text — the page itself is third-party published content.
    """
    r = reference_gate.check(
        "Prop 18.2 The function is the unique morphism making the diagram commute.")
    assert not r.passed
    assert r.failures[0].excerpt == "Prop 18.2"


def test_an_advisory_finding_does_not_fail_the_page() -> None:
    """The author asked for a review flag, not a refusal — so `failed` must ignore it."""
    from handzoo.core.emit import Emission
    from handzoo.core.validate.base import Failure, GateResult

    advisory = GateResult("reference", (Failure(detail="unmarked claim"),), advisory=True)
    hard = GateResult("ascii", (Failure(detail="non-ASCII"),))

    assert not Emission(text="x", gates=(advisory,)).failed
    assert Emission(text="x", gates=(advisory, hard)).failed
