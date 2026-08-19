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

## Status: early. It runs; it is not finished.

The pipeline runs end to end. It is not yet a tool you should rely on.

| | State |
|---|---|
| Rasterizer, recognizer, normalizer, gates, emitter, pipeline, CLI | **Working** |
| Four gates: ascii, delimiters, compile, coverage | **Working** |
| `handzoo review` — the correction loop | **Not built** |
| Markdown target | **Not built** (M1) |
| Tests | 102, plus a frozen fixture corpus as a regression suite |

`handzoo convert` runs:

```
$ handzoo convert notes.pdf --pages 1-3
page    1  FAIL  ascii=pass  delimiters=pass  compile=skipped  coverage=fail
page    2  ok    ascii=pass  delimiters=pass  compile=pass     coverage=pass
page    3  ?     ascii=pass  delimiters=pass  compile=skipped  coverage=pass
```

**Three verdicts, not two.** `ok` means everything checkable was checked and nothing refused.
`FAIL` means a gate refused the page — it is written as `.fail.tex` so a build cannot consume
it by accident. **`?` means a gate could not run**, which is neither a pass nor a failure:
a fragment has no preamble, so it cannot be compiled in isolation. Use `--standalone` to
check a single page fully.

Collapsing that third state is a mistake this project has made and measured. Reported as a
failure, every fragment failed and the signal meant nothing; reported as a pass, it would
claim a check that never happened.

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
