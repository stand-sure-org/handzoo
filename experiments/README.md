# experiments

Scripts that produced measurements recorded in `.specify/features/m0-walking-skeleton/DESIGN.md`.

They are kept so a claim can be re-run rather than taken on trust, and they are deliberately
outside `handzoo/` — nothing here is imported by the package, and the architecture test would
fail if it were.

| Script | Measured | Recorded in |
|---|---|---|
| `cardan_grille.py` | Masking a diagram out of the raster before transcription, to stop the model fabricating structure it cannot name | DESIGN §5.5.4 |
| `stroke_width.py` | Effective ink stroke width per page (stroke-width x transform scale), excluding ruled guide lines | DECISION, capture-side variable |
| `self_verification.py` | Showing the model the page and its own transcription and asking whether they match. Discrimination test, including a deliberately corrupted control | DESIGN §5.5.5 |
| `cardan_grille_batch.py` | The same, over a whole document, scoring fabrication and prose retention. Produced `grille-n16-2026-08-19.csv` | DESIGN §5.5.4 |

Each needs a rasterised page and a running Ollama with `qwen3-vl:8b-instruct`.

## `glyph_extent.py` — does writing size move gate pass rate?

The follow-on to `stroke_width.py`. Stroke width is fixed, so the variable zoom actually moves
is stroke-to-glyph ratio. Measures per-path ink extent from the vector source and reports the
ratio per page.

Answer on Cheng ch18 (n=14, 3 failures): **no.** Ratio spans 4.74-6.74; pass mean 5.83, fail
mean 5.79. More usefully, all three failures have identified causes unrelated to glyph size --
one fabricated `\includegraphics`, two fabricated `tikzcd` environments. Ruled out by cause,
not by coefficient; three failures cannot resolve a correlation statistically.

## `_ink.py` — shared ink extraction

Ruled guide lines are separated from ink **by geometry, not colour**. The original rule was
"grey and uniform", which held on the pages it was written against and fails on Cheng 217-220
p3, where 17 paths of deliberate grey ink would have been discarded as furniture. Chapter 18 is
unaffected: both filters keep an identical 576/304/431/291 paths and the same 1.903 median.

## `ink_colour.py` — what colours are actually on a page

Measured on Cheng 217-220 p3: violet writing, grey base-diagram arrows, green cone legs, where
green against grey *is* the lesson. Twelve recognizer runs over those four pages emitted no
colour at all and the gates passed. See DESIGN §6.
