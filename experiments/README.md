# experiments

Scripts that produced measurements recorded in `.specify/features/m0-walking-skeleton/DESIGN.md`.

They are kept so a claim can be re-run rather than taken on trust, and they are deliberately
outside `handzoo/` — nothing here is imported by the package, and the architecture test would
fail if it were.

| Script | Measured | Recorded in |
|---|---|---|
| `cardan_grille.py` | Masking a diagram out of the raster before transcription, to stop the model fabricating structure it cannot name | DESIGN §5.5.4 |

Each needs a rasterised page and a running Ollama with `qwen3-vl:8b-instruct`.
