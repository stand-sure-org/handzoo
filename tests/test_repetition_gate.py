r"""The repetition gate — runaway generation, which every other gate is blind to.

**Found by comparison, not by design.** Re-running ch18 with the lexicon produced 28% less
prose, which looked like dropped content. It was not: the *first* run had a page carrying
23,000 characters of one sentence repeated. Chasing that turned up five such pages across three
chapters, and **four of them passed every gate** — ASCII-clean, balanced, compiling, coverage
satisfied, and 27,000 characters of *"The diagram is drawn with a pencil, and the text is
written with a pen."*

This is a third defect class, distinct from the two already modelled. Substitution replaces a
mark with a wrong one; omission drops it. This *invents* text — at volume — and it is the only
one of the three that is cheap and reliable to catch.

**The threshold is measured, not chosen.** Across 86 passing pages of three chapters the
highest legitimate 8-gram repetition is **3**. The degenerate pages begin at **122**. A
threshold of 20 sits roughly 40x from both sides, which is the widest separation any gate in
this project has.
"""

from __future__ import annotations

import pytest

from handzoo.core.validate import repetition_gate


def test_ordinary_prose_passes() -> None:
    body = ("Prove the cartesian product together with its projections is a categorical "
            "product of A and B. Also prove for B times A. What is the unique isomorphism?")
    assert repetition_gate.check(body).passed


def test_runaway_repetition_is_refused() -> None:
    r"""Frozen from ch18-v2 p21, which passed all five gates carrying 27,118 characters of
    this. Synthetic text; the page itself is third-party published content."""
    body = "The diagram is drawn with a pencil, and the text is written with a pen. " * 40
    result = repetition_gate.check(body)
    assert not result.passed
    assert "40" in result.failures[0].detail or "repeat" in result.failures[0].detail.lower()


def test_the_failure_quotes_what_was_repeated() -> None:
    """"Something repeats" is not actionable; the phrase and the count are."""
    body = "the arrows from a to b are curved downward and the arrows go on " * 30
    (failure,) = repetition_gate.check(body).failures
    assert "curved downward" in failure.excerpt or "arrows" in failure.excerpt


@pytest.mark.parametrize("body", [
    # Legitimate repetition measured in the corpus: alignment padding and list structure.
    r"\begin{itemize}" + r"\item a point here about products " * 8 + r"\end{itemize}",
    r"$a \quad b \quad c \quad d$",
    "The product is the universal cone. " * 3,
])
def test_legitimate_repetition_is_not_flagged(body: str) -> None:
    """Across 86 passing pages the highest real 8-gram repetition was 3. Firing below that
    would refuse ordinary lists and alignment."""
    assert repetition_gate.check(body).passed, repetition_gate.check(body).report()


def test_a_short_document_cannot_repeat_enough_to_fail() -> None:
    """Guard against dividing by nothing on a nearly-empty page."""
    assert repetition_gate.check("short").passed


def test_an_empty_document_is_not_silently_clean() -> None:
    """DESIGN §5.7 — nothing to check must not read the same as checked-and-clean."""
    r = repetition_gate.check("")
    assert not r.checked
    assert not r.passed


def test_it_is_a_hard_fail_not_advisory() -> None:
    """The reference gate is advisory because it enforces a *convention*. Twenty-seven
    thousand characters of one sentence is not a convention the author might have meant."""
    body = "The diagram is drawn with a pencil and the text is written with a pen. " * 40
    assert not repetition_gate.check(body).advisory
