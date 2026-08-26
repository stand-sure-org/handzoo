r"""Runaway generation: text invented at volume, which no other gate can see.

**Found by accident, which is why it is worth stating how.** Re-running ch18 with the lexicon
produced 28% less prose than the first run. That looked like dropped content — the worst
outcome this project has — and it was not. The *first* run held a page carrying 23,000
characters of a single repeated sentence, and removing it accounted for nearly the whole
difference. Chasing that found five such pages across three chapters, **four of which passed
every gate**: ASCII-clean, delimiter-balanced, compiling, coverage satisfied, and 27,118
characters of *"The diagram is drawn with a pencil, and the text is written with a pen."*

**A third defect class.** Substitution replaces a mark with a wrong one (§5.5); omission drops
it (§5.4). This one *invents* — and unlike the other two it is cheap and reliable to catch,
because degenerate repetition does not resemble writing.

**The threshold is measured, not chosen.** Across 86 passing pages of three chapters the
highest legitimate 8-gram repetition is **3**; the degenerate pages begin at **122**. Twenty
sits about 40x from either side — the widest separation of any gate here, and the reason this
one can be a hard fail while the reference gate (§11.0.1b) cannot.

Note what it does *not* claim: a page that passes is not a page free of invention, only free of
invention that repeats. A model that fabricated a paragraph once would sail through.
"""

from __future__ import annotations

from collections import Counter

from .base import Failure, GateResult

GATE = "repetition"

WINDOW = 8
"""Words per n-gram. Long enough that ordinary phrasing does not collide, short enough to
catch a repeated clause rather than only a repeated paragraph."""

LIMIT = 20
"""Occurrences of one n-gram before the page is refused. See the module note: measured
maximum on legitimate pages is 3, minimum on degenerate pages is 122."""


def worst_repeat(latex: str) -> tuple[str, int]:
    """The most-repeated n-gram and its count. `("", 0)` when the text is too short."""
    words = latex.split()
    if len(words) <= WINDOW:
        return "", 0
    grams = Counter(" ".join(words[i:i + WINDOW]) for i in range(len(words) - WINDOW))
    phrase, count = grams.most_common(1)[0]
    return phrase, count


def check(latex: str) -> GateResult:
    """Refuse a page whose text collapses into one repeated phrase.

    Args:
        latex: the emitted document.

    Returns:
        A `GateResult`. An empty document returns `checked=False` — there was nothing to look
        at, and that must not read as clean (DESIGN §5.7).
    """
    if not latex.strip():
        return GateResult(GATE, checked=False,
                          note="empty document — nothing to check for runaway repetition")

    phrase, count = worst_repeat(latex)
    if count <= LIMIT:
        return GateResult(GATE)

    line = latex[:latex.find(phrase)].count("\n") + 1 if phrase in latex else None
    return GateResult(GATE, (Failure(
        detail=(f"a phrase repeats {count} times — the recognizer ran away rather than "
                f"transcribing. This is invented text, not a transcription defect, and the "
                f"page cannot be corrected into one."),
        line=line, excerpt=phrase[:80]),))
