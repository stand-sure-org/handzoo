"""Gate: no mark may be dropped silently.

This is the gate the whole design turns on, and the only one that catches a failure the other
three cannot see.

Measured, on a real page: the transcription pass read *"THE BLUE HOUSE HAS MORE 🧍 THAN THE
OTHERS"* and *"THE GREEN HOUSE HAS MORE 🐕 THAN THE OTHERS"*, silently deleted both inline
glyphs, and emitted two bullets that directly contradict each other. That output is
ASCII-clean, delimiter-balanced, and compiles. Three green gates on a false page.

**How it works, and why it is not a self-report.** The inventory comes from a *second,
independent* pass. A self-report from the pass being audited has already decided to drop the
glyph — measured, the independent pass surfaced exactly what transcription discarded.

**What it is built on.** Presence and position, never descriptions. The inventory names marks
confidently and wrongly (a dog came back as "a stick figure lying down") while getting their
counts and placements right. `Mark.description` is carried for a human to read and is used
here for nothing.

**What it does NOT catch.** This is a *coverage* test, not a correctness test. It sees that
four glyphs entered and none left. It cannot see that `|||| < ||||` is false, or that tally
marks became Roman numerals — those are substitution, the mark is still present, and nothing
in M0 detects them.
"""

from __future__ import annotations

import re

from ..rasterize import InkProfile
from ..recognize.base import Mark
from .base import Failure, GateResult

GATE = "coverage"

MAX_LOCATED = 10
"""How many individual marks to locate before summarising. See the note where it is used."""

# Markers, in both the shapes they legitimately take: raw from the recognizer, and after the
# Normalizer has made them safe to typeset.
_MARKER = re.compile(r"\[\[DIAGRAM:.*?\]\]|\[TODO diagram:.*?\]", re.DOTALL)


def count_markers(latex: str) -> int:
    return len(_MARKER.findall(latex))


def check(latex: str, inventory: tuple[Mark, ...], *,
          require_block_marks: bool = False, ink: InkProfile | None = None,
          inventory_failed: bool = False) -> GateResult:
    """Fail when the inventory saw marks the output does not account for.

    Args:
        latex: Emitted document, before or after normalization.
        inventory: Marks from the independent second pass.
        require_block_marks: Also require a marker for block marks. Off by default —
            a block diagram may legitimately be emitted as a cropped figure reference
            instead of a marker, whereas an inline mark has nowhere else to go.
    """
    if not inventory:
        # Two different things produce an empty inventory, and only one of them is evidence.
        #
        # The pass FAILED to be read — we do not know what is on the page. Not a pass.
        if inventory_failed:
            return GateResult(GATE, checked=False)
        # The pass RAN and found no pictures. That is a finding, not a silence, and treating
        # it as unverifiable would leave every clean symbolic page permanently unverified —
        # which is the content this tool handles best.
        #
        # Residual risk, stated rather than hidden: an inventory that silently under-reports
        # is indistinguishable from one with nothing to report. Ink cannot separate them
        # either, since on a symbolic page all the ink is legitimately notation.
        return GateResult(GATE)

    expected = [m for m in inventory
                if m.placement == "inline" or (require_block_marks and m.placement == "block")]
    if not expected:
        return GateResult(GATE)

    wanted = sum(m.count for m in expected)
    found = count_markers(latex)
    if found >= wanted:
        return GateResult(GATE)

    failures = [Failure(
        detail=f"{wanted} mark(s) seen on the page, {found} accounted for in the output "
               f"— {wanted - found} dropped silently",
    )]
    # Cap the located failures. Measured: a page of tally marks produced 285 of them, and a
    # reviewer facing 285 items reviews none of them carefully — inspectors miss 20-30% of
    # defects under repetitive load. The cap is announced rather than silent, because a
    # bounded list that reads as complete is the same lie as a skipped check that reads as
    # a pass (DESIGN 5.7).
    shown = expected[:MAX_LOCATED]
    failures.extend(_locate(latex, m) for m in shown)
    if len(expected) > MAX_LOCATED:
        failures.append(Failure(
            detail=f"...and {len(expected) - MAX_LOCATED} more not listed. A page reporting "
                   f"{len(expected)} separate marks usually means the inventory counted "
                   "strokes rather than marks; treat the count as an upper bound."))
    return GateResult(GATE, tuple(failures))


def _locate(latex: str, mark: Mark) -> Failure:
    """Point a human at where a mark probably belonged.

    Uses `Mark.context` — words from the page, which the recognizer reports reliably — to find
    a plausible line. The description is quoted only so the reader recognises the thing; it is
    not matched on, because it is not trustworthy.
    """
    anchor = _longest_word(mark.context)
    line = None
    if anchor:
        for i, text in enumerate(latex.splitlines(), start=1):
            if anchor.lower() in text.lower():
                line = i
                break
    return Failure(
        detail=f"{mark.placement} mark x{mark.count} unaccounted for "
               f"(recognizer called it {mark.description!r} — not reliable)",
        line=line,
        excerpt=mark.context[:80],
    )


def _longest_word(context: str) -> str:
    words = [w.strip(".,;:!?\"'()[]{}") for w in context.split()]
    real = [w for w in words if len(w) > 3 and w.isalpha()]
    return max(real, key=len) if real else ""
