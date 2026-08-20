r"""Colour gate — is colour-bearing ink present, and does the document carry any of it?

**Binding condition 5** says colour-bearing ink is a `ColorSpan` and that silent loss of it is
a hard fail. Measured 2026-08-20, it was neither: `ColorSpan` is declared and nothing anywhere
constructs one, so the coverage gate had nothing to check against. `Cheng 217-220` p3 carries
violet writing, grey base-diagram arrows and green cone legs — where green against grey *is*
the lesson — and twelve runs emitted no colour at all while every gate passed. The condition
was asserted and never wired.

**Evidence class: file ground truth, not model report.** Colour comes from the vector source in
one `pdftocairo` call. That is why this is a separate gate rather than an extension of
`coverage_gate`, whose evidence is the recognizer's own inventory. Asking the model to *name* a
colour would put the answer on the untrustworthy side of the §3 boundary; reading it from the
file does not.

**A finding, not a hard fail.** Nothing in the pipeline can preserve colour yet, so failing
hard would paint every multi-ink page red forever and teach the reviewer to ignore it — the
`Emission.verdict` mistake (§5.7). The gate's job here is to make the loss *loud*, which is
what constraint 5 actually forbids: silence.
"""

from __future__ import annotations

import re

from .base import Failure, GateResult

GATE = "colour"

_COLOUR_COMMAND = re.compile(r"\\(?:textcolor|color|colorbox|fcolorbox|pagecolor|definecolor)\b"
                             r"|\\usepackage(?:\[[^\]]*\])?\{[^}]*\bxcolor\b")
"""Structural, deliberately. Searching for colour *words* false-positives on prose about the
four-colour theorem, and on Naive Math where "red house" is colour information genuinely
carried in words. The question is whether the document contains a colour *command*."""

MIN_DISTINGUISHING = 2
"""One ink carries no contrast, so rendering it black drops no distinction. The gate fires on
ink that distinguishes, not on ink that merely exists."""


def check(latex: str, *, colours: tuple[tuple[int, int, int], ...] | None) -> GateResult:
    """Compare ink actually on the page against colour the document carries.

    Args:
        latex: the emitted document.
        colours: distinct ink colours from the source, or **None** meaning *could not be
            determined* — a raster source has no vector paths to read, and so does a blank
            page. `None` returns `checked=False`, never a pass: a scan is precisely where
            colour is hardest to recover, and reporting clean there would be the failure this
            project exists to refuse (§5.7).
    """
    if colours is None:
        return GateResult(GATE, checked=False)

    if len(colours) < MIN_DISTINGUISHING or _COLOUR_COMMAND.search(latex):
        return GateResult(GATE)

    swatches = ", ".join(f"rgb{c}" for c in colours[:6])
    more = f" and {len(colours) - 6} more" if len(colours) > 6 else ""
    return GateResult(GATE, (Failure(
        detail=(f"{len(colours)} distinct ink colours on the page ({swatches}{more}) and the "
                "document carries none. Where colour distinguishes two things — arrows "
                "belonging to a diagram against arrows belonging to a cone — rendering them "
                "alike drops the distinction. Crop the region to keep it."),
    ),))
