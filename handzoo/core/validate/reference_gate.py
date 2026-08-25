r"""Reference gate — a numbered claim that arrived without its marking.

**Built from measurement, not anticipation.** The first labelled corpus (DESIGN 11.0.1a) put
lost emphasis at the top of the defect table: three of eight author corrections, and nothing in
the project looked for it.

**The underline is not emphasis.** Cheng numbers claims but does not number equations, and the
author underlines the thing she numbered. The mark is doing the work a `\label` does in a typed
document — it says *this is the referent*. Dropping it does not make the page plainer, it
removes the anchor a later "by Prop 18.2" points at. That is why this is a gate rather than a
style preference, and it is the same argument the colour gate rests on: the ink is carrying
meaning, so losing it silently is a D6 violation.

Measured on ch18: **0 of 5** numbered claim references in raw recognizer output carried any
marking. The single marked one is on a page the author had already corrected, and the author is
who marked it.

**This is a text-level check and deliberately so.** Reading underline strokes out of the vector
source would be stronger and is the eventual answer; it also cannot run on a scan (DESIGN 8.1)
and needs geometry this milestone does not have. A label followed by a number is a pattern in
the emitted text, costs nothing, and catches the case that actually occurred.

**It is advisory.** The convention is *normally*, not always.
"""

from __future__ import annotations

import re

from .base import Failure, GateResult

GATE = "reference"

# The author's own vocabulary. `Prop` is contextually Proposition *or* Property, which the
# recognizer must disambiguate and can silently get wrong -- one more reason a human looks.
LABELS = ("Defn", "Def", "Definition", "Prop", "Proposition", "Property", "Thm", "Theorem",
          "Lem", "Lemma", "Cor", "Corollary", "Example", "Ex", "Remark", "Claim", "Axiom")

_CLAIM = re.compile(r"\b(" + "|".join(LABELS) + r")\s*\.?\s*(\d+(?:\.\d+)*)\b")

# Commands that constitute marking. `\section*{...}` counts: a claim promoted to a heading has
# been marked, just not with a rule under it.
_MARKS = ("underline", "textbf", "emph", "textit", "section", "subsection", "paragraph",
          "textsc", "uline", "textbf")


def _is_marked(body: str, start: int) -> bool:
    """Does a marking command open close enough before this label to be wrapping it?

    Scans a short window rather than parsing. A brace-accurate answer would need the walker,
    and the cost of being wrong here is one advisory line a human dismisses -- not a refused
    page.
    """
    window = body[max(0, start - 40):start]
    return any(f"\\{m}{{" in window or f"\\{m}*{{" in window for m in _MARKS)


def check(latex: str) -> GateResult:
    """Flag numbered claim references that carry no marking.

    Args:
        latex: the emitted document, preamble included.

    Returns:
        An **advisory** `GateResult`. Findings are for a human to look at; the page is not
        refused. An empty body returns `checked=False` — there was nothing to look at, and
        that must not read as clean (DESIGN 5.7).
    """
    if not latex.strip():
        return GateResult(GATE, checked=False, advisory=True,
                          note="empty document — nothing to check for lost marking")

    body = latex.split("\\begin{document}", 1)[-1]
    offset = len(latex) - len(body)

    failures = []
    for m in _CLAIM.finditer(body):
        if _is_marked(body, m.start()):
            continue
        line = latex.count("\n", 0, offset + m.start()) + 1
        failures.append(Failure(
            detail=(f"{m.group(0)!r} is a numbered claim with no marking. The author "
                    f"underlines what the text numbered, so this may be a lost mark rather "
                    f"than a plain label — check the page."),
            line=line, excerpt=m.group(0)))
    return GateResult(GATE, tuple(failures), advisory=True)
