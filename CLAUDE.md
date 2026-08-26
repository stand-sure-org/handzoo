# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**HandZoo** converts handwritten math notes (reMarkable PDF exports) into compilable LaTeX, gated so that broken output is refused rather than emitted.

**Positioning is load-bearing, not marketing.** HandZoo is not "OCR for math" — that is Mathpix's product and it is mature. HandZoo is **the tool that refuses to hand you broken LaTeX.** The villain is *silent corruption*: plausible-looking output that quietly breaks and costs more to debug than hand-transcription would have saved. Every gate is a feature.

**But the claim has a hard limit, and it must always ship with the hedge:** the gates prove output *builds*, not that it is *true*. Semantic substitution — well-typeset, ASCII-clean, balanced, compiling, and factually wrong — is uncaught at M0. Never print an unqualified PASS.

## Start here

The design is settled and evidence-backed. Read in this order:

| File | What it holds |
|---|---|
| `.specify/features/m0-walking-skeleton/DECISION.md` | Decisions D1–D8, and the measured baseline that drove them |
| `.specify/features/m0-walking-skeleton/DESIGN.md` | Technical design v1.1, post-review |
| `.specify/features/m0-walking-skeleton/PLAN.md` | 74 points of work in 6 waves, with the dependency graph |
| `.specify/features/m0-walking-skeleton/reviews/` | Two Delphi panels — the opinion poll stopped the first spec; the design review imposed 9 binding conditions |
| `docs/handoff.md` | The original brief. **Provenance, not specification** — its header lists what the baseline falsified |

## Naming

The original brief calls this `inkwell`. That name is taken and abandoned. The binary, package, and module name is **`handzoo`**. Do not scaffold anything named `inkwell`.

## Commands

Python end-to-end with `uv` (D3). The PDF is the first positional argument — there is no
`convert` subcommand and no `--target` flag; both appeared in earlier drafts of this file and
never existed in the code.

```
handzoo notes.pdf -o out/                 # fragments + chapter.tex, ready to open
handzoo notes.pdf -o out/ --standalone    # complete documents; compile gate can run
handzoo notes.pdf --pages 1-5 --resume    # triage a range; resume from the manifest
```

The correction loop is a **separate binary**, `handzoo-review` (not a `handzoo` subcommand):

```
handzoo-review out/                 # walk pages with findings, record a verdict each
handzoo-review out/ --transcribe 4  # time yourself typing page 4 from blank (exit criterion)
handzoo-review out/ --fix 5         # time yourself correcting page 5 — the other arm
handzoo-review out/ --fix 5 --mode pdf-annotate   # ...by annotating the typeset PDF
handzoo-review out/ --fix 5 --mode paper --seconds 480   # ...a time you measured yourself
handzoo-review out/ --summary       # the log, and both exit-criterion arms
```

`c` on a finding crops the region from the source as vector and drops it in where the marker
was — the fix for the 45-of-49 findings that are fabricated diagrams. `--transcribe` refuses a
page you have already reviewed, because reading the emitted text contaminates the timing.

Environment, verified on this machine:

| Binary | Role | Status |
|---|---|---|
| `pdftoppm` | rasterize PDF → page PNGs (`-png -r 150`) | present |
| `pdflatex` (MacTeX) | compile gate — **hardcoded for M0** | present |
| `ollama` | recognizer host | present; **use `qwen3-vl:8b-instruct`** |
| `uv`, `pylatexenc` | toolchain + Normalizer basis | `.venv` present |
| `lean`, `agda` | deferred formal checking (§5.5.3) | installed, not wired in |
| `tectonic` | CI-reproducibility swap, later | not installed, not blocking |

## Hard constraints

1. **No per-character recognizer.** Glyph recognition is solved; 2-D structure is the hard part. End-to-end image→markup, never classify-then-assemble.
2. **No heuristic segmenter** (D4). The VLM recovers reading order for free; a bbox segmenter would have to reconstruct it.
3. **Five gates, all hard fails:** zero non-ASCII (unless the `--standalone` preamble supports it) · delimiter and environment balance · compiles clean under `pdflatex` · **no silent mark loss** · **no silent colour loss** (read from the vector source; a raster source reports *not checked*, never *clean*).
4. **Never fabricate `tikz`.** Diagrams are cropped, referenced, and flagged `% TODO: author diagram`.
5. **Never silently drop, reword, or renotate a mark.** This is the same principle as (4), applied where the baseline proved it was missing.
5b. **Never silently *add*.** ch17 p1 lists three divisor pairs and an ellipsis; the emitted
   document carried four. The extra pair was correct arithmetic and was not on the page, and
   the author welcomed it — which is exactly why the class is dangerous. An addition the
   author welcomes is one they stop checking for. Mark it (`% handzoo: not on the page`),
   do not suppress it. See DESIGN §7.1.1.
6. **Absence of evidence is not evidence of absence.** A check that did not run must never
   read as a check that passed. The codebase has rediscovered this three times —
   `GateResult.checked`, `coverage_gate` on an empty inventory, and `Emission.verdict` — each
   time by defaulting an unknown into the reassuring answer. **When adding a gate, decide what
   it returns when it cannot run, and test that case directly.** See DESIGN §5.7.
7. **Local-first.** `fixtures/` is gitignored — the manuscript is unpublished IP and this repo
   is intended for public release. `--provider gemini` exists and **sends page images to
   Google**; it is opt-in, never the default, announces itself on every run, and reads its
   key only from `$GEMINI_API_KEY`. A test asserts no key can live in the tree. What is
   absolute is that no page content reaches the repository.

## What is built, and what is not

| | State |
|---|---|
| `handzoo/core/normalize.py` | **Working.** Ten rules (R1–R10), each traced to a measured gate failure. Built on `pylatexenc`, not regex. |
| `handzoo/core/lexicon.py` | **Working**, optional. The author's shorthands named to the recognizer — **tokens only, never meanings**. `Sps` recovery went 0/4 → 4/4 on the page that produced `\Rightarrow`. See DESIGN §11.0.1c. |
| `handzoo/core/declarations.py` | **Working.** Generates `\ifdefined`-guarded declarations for macros the recognizer invents. |
| `handzoo/core/recognize/gemini_vlm.py` | **Working**, opt-in. Same port, same prompts, over the wire. Two providers disagreeing is the project's only self-audit-free substitution detector — DESIGN §5.5.6. |
| Rasterizer, recognizer, gates, emitter, pipeline, CLI | **Working.** The command is `handzoo <pdf>` — see Commands above; there is no `convert` subcommand. |
| `handzoo/core/assemble.py` | **Working.** Writes `chapter.tex` after a run — pages `\input` in order, failures as visible placeholders. A `--standalone` page cannot be assembled and says so. |
| `handzoo-review` — the correction loop | **Built** (PLAN Wave 5). Walks gate findings, records a verdict per page, and can **crop** a diagram from the source as vector (`c`) — the fix for 45 of 49 findings on a real run. **The M0 exit criterion has now been run through it** — see below. |
| Tests | **242**, plus the frozen `baseline/` corpus as a regression suite. CI never calls a model. |

Measured state of the Normalizer, on identical raw recognizer output (Naive Math, the hardest
document): 16/22 → **22/22**. Older Thinking-checkpoint corpora hold at 30/34 as a fixed
regression set.

**The unsolved problem is substitution, not syntax.** "Over-correction" — the model silently
improving what is on the page — persists on Instruct and is measured across 15 VLMs at 42–66%.
No checkpoint swap and no prompt fixes it. See DESIGN §5.5.

### The ASCII gate is a trap in the brief

Use `iconv -f ASCII -t ASCII out.tex` (or `s.encode("ascii")`). The brief's `! grep -P '[^\x00-\x7F]' out.tex` is **broken**: `-P` is a GNU/ugrep extension, stock macOS `/usr/bin/grep` errors on it, and the leading `!` inverts that error into a pass. The gate reports clean without ever looking.

## Recognizer — measured constraints, not guesses

### Use `qwen3-vl:8b-instruct`. Never the bare `qwen3-vl:8b`.

**`qwen3-vl:8b` is an alias for the *Thinking* checkpoint** — a reasoning model, which this
task does not want. Measured on the full ch16 corpus, changing only the checkpoint:

| | `qwen3-vl:8b` (Thinking) | `qwen3-vl:8b-instruct` |
|---|---|---|
| Blank responses | 2/26 | **0/26** |
| Gate pass | 81% | **96%** |
| Median latency | 92s | **4s** |

An entire day went into diagnosing "blanks" that were reasoning loops in a model that should
never have been reasoning. Do not repeat it.

**Required options:** `num_ctx: 8192` and `num_predict: -1`, via `/api/chat` (not
`/api/generate`). `num_ctx` is **correctness, not tuning** — Ollama's 262,144 default
allocates ~42 GB for a 6 GB model and swamps the host into swap.

> `baseline/recognize.py` is **historical**, kept as the artifact that produced the original
> baseline. It uses the Thinking checkpoint and caps nothing. Do not copy it.

- **Two passes per page:** transcription, then an independent inventory pass. The inventory is trustworthy about *where* marks are and **untrustworthy about what they are** — build the coverage gate on presence and position only.
- Empty `content` with `done_reason: "stop"` still needs a bounded retry, and a **per-attempt timeout** — but is no longer the dominant failure on Instruct.

`qwen2-math` is **text-only**. It cannot recognize. It is a candidate *semantic checker* for M1+ — the only identified mechanism against semantic substitution.

## What the baseline proved

Run 2026-08-18 on real fixtures, artifacts in `baseline/`. This is why the design departs from the brief:

- Page 1 **passed all three original gates and was factually false.** Inline glyphs are *terms in the sentence* — a stick figure is a noun. Stripping them turned two consistent bullets into a direct contradiction.
- Page 2 emitted `|||| < ||||` (`4 < 4`) and silently converted tally marks into Roman numerals — collapsing the exact distinction the page teaches.
- **Ink colour is semantic** (R/G/B houses in red/green/blue) and unmodelled in the brief.
- Diagrams are **not separable regions** on this content, which is what killed the brief's crop-and-reference policy as a complete answer.

Treat these as regression fixtures. Page 1 must fail the coverage gate; page 2 must fail the compile gate.

## Testing

TDD, consumer-first (ISP) — `pipeline.py` is written against the ports before providers exist.

**CI never calls the recognizer.** Golden tests feed the frozen `.tex` bytes in `baseline/` directly into gate functions. Recognizer accuracy is nondeterministic and model-versioned: measure and record it, never assert it.

## M0 exit criterion — measured 2026-08-25: it passes

> **Author-timed** minutes to correct emitted `.tex` to ground truth, versus minutes to
> transcribe the same page from blank. If correction ≥ transcription, M0 has negative value.

**Both arms are now run, on the same protocol** (`--fix` against `--transcribe`, whole page
each, on different pages because either order contaminates the other).

| arm | n | median |
|---|---|---|
| `--fix` | 6 | **77.3s** |
| `--transcribe` (ch18) | 2 | 522.7s |
| `--transcribe` (ch19) | 3 | 196.0s |

Correction is cheaper than transcription on the median under every baseline above.

**Do not publish a speed-up figure.** The ratio is a go/no-go gate for *this* author on *this*
corpus, not a product metric. Another writer types at a different speed, so the denominator is
a property of the person; and correction time is driven by the author's composition traits, the
visual recognition the page demands, and a stochastic resolution process in which the reader
reconstructs ambiguous symbols from comprehension. None of that transfers. Quoting an
improvement number would be reporting the author's typing speed as a product claim.

**What it does not license.** The arms do not share a target. A transcript is ground truth by
construction; a correction is what the author judged right *after reading our output*, which
anchors them. Plausible substitution survives correction and would not survive transcription.
And on ch19 p1/p3 the human arm was the less accurate one — see DESIGN §11.0.

**What the author actually corrected** (six labelled pages, all of which passed all five
gates) — DESIGN §11.0.1a:

| class | n | caught? |
|---|---|---|
| lost emphasis (underline dropped) | 3 | 1 of 3, by the **reference gate** (advisory) |
| semantic substitution (`Sps` emitted as `\Rightarrow`) | 1 | no |
| dropped mark (a *dashed* arrow; a missing `\square`) | 2 | no |
| notation degradation (blackboard-bold R read as `IR`) | 1 | no |
| lost sentence boundary | 1 | no |

**The underline is a label, not emphasis.** Cheng numbers claims but not equations, and the
author underlines the thing she numbered — so the mark does what `\label` does. The
**reference gate** flags a numbered claim carrying no marking: 1 true positive, 2 true
negatives, **0 false positives across 44 pages**. It is **advisory** (`GateResult.advisory`) —
the convention is *normally*, not always, so it flags for review and does not refuse the page.

Note the recognizer usually *translates* the mark (`\underline` → `\textbf`) rather than
dropping it; the gate accepts any marking. `Sps` → `\Rightarrow` is the first substitution
found in the wild rather than injected, and it inverts the logic of a proof.

**The author's abbreviations are a private lexicon** — `Defn`, `Prop` (Proposition *or*
Property, by context), `Sps`, `b/w`, `wts`, `iff`. Every private token is a place the model can
resolve an unfamiliar string into a familiar one. Whether that contributed to defects beyond
`Sps` is **unmeasured**: ground truth exists for six pages only.

**`mode` was not recorded on any row**, so these timings cannot be compared against a future
`--mode pdf-annotate` or `--mode paper` run.

## Known bugs from the mutable-source analysis (DESIGN §11.1.3)

The PDF is not immutable — the author keeps writing, inserts, reorders, edits. Three verified
consequences, none fixed:

1. **Page number is not page identity.** `PageOutcome.page` and `Correction.page` are both
   ordinals. Insert a page anywhere but the end and the manifest, correction log, crops, and
   the `--transcribe`/`--fix` guards all silently point at different content.
2. **A re-run overwrites author corrections.** `pipeline.convert` writes unconditionally;
   `--fix` writes to the same path. Recoverable by hand from the log's `after` field —
   *external* edits have no log row, so not even that.
3. **`--resume` protects a corrected page only by accident.** It keys on "recognized without
   error", not "carries author work". Nothing in the write path knows a page has GOLD rows,
   so there is no way to re-recognize one page while keeping a correction on another.

Hashing page content answers four of the five actions; **"edit a page" is the one it cannot**,
and it is the one most likely to land on a page already corrected.

**The author's answer: require an explicit replacement instruction** ("replace page 2 with this").
That dissolves the hard case rather than solving it — the human asserts the correspondence, so
nothing has to infer it, and a wrong instruction fails loudly instead of misattaching silently.
Hashing stays useful for the other four actions and for detecting *that* a page changed.

**A screen-share screenshot works as a source, with one honest gap** (DESIGN §11.1.3b). Measured
on a real one: app chrome was **not** transcribed (42% of the frame), the lexicon token survived,
the transcription silently dropped a diagram — and the independent inventory pass caught it, so
the **coverage gate refused the page**. But colour reports **NOT CHECKED**, correctly: a
screenshot is raster and colour is read from vector. A screenshot also has no identity of any
kind, which makes explicit replacement mandatory there rather than merely cleanest.

## Branding

`HandZoo™` and `"let there be text"™` are trademarks; the SVGs in `assets/brand/` are trademarked assets, not freely-licensed source. Code is Apache-2.0. See `TRADEMARK.md`.

*(Note: `TRADEMARK.md` still has an unfilled `[INSERT_EMAIL_ADDRESS]`. Author's call.)*
