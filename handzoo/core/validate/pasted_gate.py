r"""Pasted raster content: text on the page that the author did not write.

**Measured, and it is the quietest failure the project has found.** On Leinster 1.1 the
author pastes screenshots of the book's printed exercises above their handwritten answers.
Page 14 **passed every gate** — ASCII, delimiters, compile, coverage, colour, reference,
repetition — and emitted Leinster's exercise text verbatim, while dropping the author's own
underlined `1.1.12` label.

Every other mechanism is blind to it, each for a different reason:

- the **colour gate** reads stroke colour, and a raster has none. Black-print-against-
  coloured-pen was the discriminator on the *previous* Leinster corpus, where the printed page
  was the PDF background; here it reports one colour and passes.
- **`page_blocks`** groups vector paths, so no band is offered over the pasted region — the
  crop tool cannot even reach it.
- there is **no text layer**, so nothing downstream knows the pixels are words.

`pdfimages` answers it directly, which makes this the cheapest check here and the only one
that sees the case at all.

**Advisory, not a refusal.** An author may legitimately paste their own figure, and this
cannot tell whose picture it is. What it can say is *there is text-shaped content here that
did not come from your pen* — which is exactly the moment to decide whether to transcribe it,
crop it, or cut the page (`--exclude`).
"""

from __future__ import annotations

from .base import Failure, GateResult

GATE = "pasted"


def check(count: int | None) -> GateResult:
    """Flag a page carrying pasted raster images.

    Args:
        count: images on the page, from `rasterize.embedded_images`. `None` when the check
            could not run — which is **not** the same as a clean page and does not report as
            one (DESIGN §5.7).
    """
    if count is None:
        return GateResult(GATE, checked=False, advisory=True,
                          note="could not read the page's embedded images")
    if count == 0:
        return GateResult(GATE, advisory=True)
    plural = "s" if count > 1 else ""
    return GateResult(GATE, (Failure(
        detail=(f"{count} pasted image{plural} on this page. If it is typeset text, the "
                f"transcription above may reproduce writing that is not yours — decide "
                f"whether to keep it, crop it, or cut the page with --exclude. No other gate "
                f"can see this: a raster has no stroke colour and no text layer."),),),
        advisory=True)
