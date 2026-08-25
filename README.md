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
- **No silent colour loss** — ink colour carries meaning; a raster source reports *not checked*, never *clean*
- **Numbered claims keep their marking** — advisory, because it is a convention rather than a rule

Never fabricates `tikz`. Hand-drawn diagrams are cropped as vector, referenced, and flagged for
a human — never invented.

## Status: the walking skeleton works. It is not a finished tool.

The pipeline runs end to end and has cleared its own go/no-go test — see below.

| | State |
|---|---|
| Rasterizer, recognizer, normalizer, emitter, pipeline, CLI | **Working** |
| Six gates: ascii, delimiters, compile, coverage, colour, reference | **Working** |
| `handzoo-review` — the correction loop | **Working**, including vector diagram cropping |
| Chapter assembly — pages `\input` in order | **Working** |
| Optional lexicon — name an author's shorthands to the recognizer | **Working** |
| Markdown target | **Not built** (M1) |
| Tests | 242, plus a frozen fixture corpus as a regression suite |

```
$ handzoo notes.pdf --pages 1-3
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
four points. In our corpus tally marks became Roman numerals, a page gained a fabricated
equation, and `Sps` — an author's "Suppose" — was emitted as `\Rightarrow`, turning a
hypothesis into an inference and inverting a proof. Every one of those is ASCII-clean,
balanced, compiles, and is false.

Two mechanisms find some of it: running a second provider and comparing, and asking a model
whether its own output matches the image. Both are thin, and neither is a gate. **No gate here
proves the transcription is true**, only that it builds — the tool never prints an unqualified
PASS, and saying so plainly matters more than a green badge.

## Correcting what it emits

Gates route a human to what the tool is unsure about. `handzoo-review` walks those findings and
records a decision for each:

```
$ handzoo-review out/
=== page 4 — fail (2 finding(s)) ===
  source: out/pages/p-0004-04.png
  [coverage] recognizer fabricated a drawing here — crop the region instead
  [k]eep  [e]dit  [c]rop  [f]lag  [s]kip  [q]uit >
```

`c` cuts the region from the source **as vector** and drops it in where the marker was — the
fix for 45 of 49 findings on one real run. Pressing enter skips: the default records nothing
rather than manufacturing an approval, and a page you accepted without opening is logged
differently from one you read.

## Does it beat just typing the page out?

That is the only question that decides whether any of this is worth running, so it is measured
rather than assumed. `--transcribe` times an author typing a page from a blank file;
`--fix` times the same author correcting our output, under the identical interaction.

On the author's own corpus, correcting was **cheaper than transcribing on the median** — so the
tool has positive value for that author on that manuscript.

**No speed-up figure is published, and that is deliberate.** The denominator is a property of
the person: another writer types at a different speed. The numerator is no better behaved —
correction time depends on how the author composes, how much visual recognition a page demands,
and a stochastic process where a reader reconstructs an ambiguous symbol from comprehension of
the surrounding argument. None of that transfers between people. Quoting a multiplier would be
publishing one author's typing speed as a product claim.

Run the two arms yourself if you want the answer for you. `handzoo-review out/ --summary`
reports both.

## An optional lexicon, and the thing it must never do

Every author has private shorthand. A recognizer meeting an unfamiliar string resolves it into
a familiar one: on a real page `Sps` came back as `\Rightarrow`, which compiles, reads
plausibly, and **inverts the logic of a proof**.

A lexicon names those tokens to the model. It has two halves and they are not interchangeable —
the recognizer is told the **token exists**, never what it **means**. Telling it that `Sps`
means "Suppose" licenses writing the expansion where the page says the abbreviation, which is
text the author did not write. `prompt_fragment()` takes a tuple of strings rather than the
lexicon object, so there is no path to the meanings at all; a test asserts it.

Measured on the page that produced the defect: `Sps` recovered **0/4 without, 4/4 with**, and
`Suppose` appeared zero times either way. See [`examples/lexicon.example.toml`](examples/lexicon.example.toml).

## Documentation

See [`.specify/features/m0-walking-skeleton/`](.specify/features/m0-walking-skeleton/) for the
decision record, technical design, plan, and two multi-perspective design reviews. The design
notes are unusually candid about failures — several sections exist because a check was found
reporting clean without having run.

## ⚖️ License & Trademark

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).

### Trademark Policy

**HandZoo™** and the slogan **"let there be text"™** are trademarks belonging to the project
maintainers. While the source code is entirely open and free to modify under our open-source
license, that permission **does not** extend to our brand name, slogans, or visual assets.

If you fork this project or use our code in your own application, please review our
[TRADEMARK.md](TRADEMARK.md) file for explicit guidelines on how to safely manage branding and
naming.
