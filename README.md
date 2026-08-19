# HandZoo

**Handwritten math notes → compilable LaTeX.** Local-first, for reMarkable PDF exports.

HandZoo is not "OCR for math" — that exists and is mature. HandZoo is **the tool that refuses
to hand you broken LaTeX.**

The villain is *silent corruption*: tools that emit plausible-looking output which quietly
breaks, costing more time to debug than transcribing by hand would have saved. Existing
exporters emit bare Unicode with nothing defining it and unbalanced `$` delimiters. Those two
failures are the bar to clear, and clearing them is the whole point.

Every acceptance gate is a feature:

- **Zero non-ASCII** in LaTeX output, unless the emitted preamble explicitly supports it
- **Delimiter and environment balance** — imbalance is a hard fail, not a warning
- **It compiles**, headless, with zero errors, or it does not ship
- **No silent mark loss** — a mark that entered must leave, or the page fails

Never fabricates `tikz`. Hand-drawn diagrams are cropped as vector, referenced, and flagged for
a human — never invented.

## Status: early. The tool does not work yet.

This repository currently contains the design, the measured evidence behind it, and the first
piece of the engine.

| | State |
|---|---|
| `handzoo/core/normalize.py` | **Working** — ten rules, each traced to a measured failure |
| `handzoo/core/declarations.py` | **Working** — guarded declarations for macros the recognizer invents |
| Rasterizer, recognizer, gates, emitter, pipeline, CLI | **Not built** |
| Tests | **None** — a fixture corpus serves as a regression suite, run by hand |

There is no entry point. You cannot yet run `handzoo convert`.

**The unsolved problem is substitution, not syntax.** Vision models silently "improve" what is
on the page — a measured phenomenon across 15 VLMs at 42–66%, which prompting moves by about
four points. In our corpus, tally marks become Roman numerals and a page gained a fabricated
equation. That output is ASCII-clean, balanced, compiles — and is false. Nothing here solves it
yet, and saying so plainly matters more than a green badge.

See [`.specify/features/m0-walking-skeleton/`](.specify/features/m0-walking-skeleton/) for the
decision record, technical design, plan, and two multi-perspective design reviews.

## ⚖️ License & Trademark

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).

### Trademark Policy

**HandZoo™** and the slogan **"let there be text"™** are trademarks belonging to the project
maintainers. While the source code is entirely open and free to modify under our open-source
license, that permission **does not** extend to our brand name, slogans, or visual assets.

If you fork this project or use our code in your own application, please review our
[TRADEMARK.md](TRADEMARK.md) file for explicit guidelines on how to safely manage branding and
naming.
