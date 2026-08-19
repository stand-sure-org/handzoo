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
handzoo notes.pdf -o out/                 # fragments, for assembly with \input
handzoo notes.pdf -o out/ --standalone    # complete documents; compile gate can run
handzoo notes.pdf --pages 1-5 --resume    # triage a range; resume from the manifest
```

`handzoo review` does not exist yet — PLAN Wave 5.

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
3. **Four gates, all hard fails:** zero non-ASCII (unless the `--standalone` preamble supports it) · delimiter and environment balance · compiles clean under `pdflatex` · **no silent mark loss**.
4. **Never fabricate `tikz`.** Diagrams are cropped, referenced, and flagged `% TODO: author diagram`.
5. **Never silently drop, reword, or renotate a mark.** This is the same principle as (4), applied where the baseline proved it was missing.
6. **Absence of evidence is not evidence of absence.** A check that did not run must never
   read as a check that passed. The codebase has rediscovered this three times —
   `GateResult.checked`, `coverage_gate` on an empty inventory, and `Emission.verdict` — each
   time by defaulting an unknown into the reassuring answer. **When adding a gate, decide what
   it returns when it cannot run, and test that case directly.** See DESIGN §5.7.
7. **Local-first.** `fixtures/` is gitignored — the manuscript is unpublished IP and this repo is intended for public release.

## What is built, and what is not

| | State |
|---|---|
| `handzoo/core/normalize.py` | **Working.** Ten rules (R1–R10), each traced to a measured gate failure. Built on `pylatexenc`, not regex. |
| `handzoo/core/declarations.py` | **Working.** Generates `\ifdefined`-guarded declarations for macros the recognizer invents. |
| Rasterizer, recognizer, gates, emitter, pipeline, CLI | **Working.** `handzoo convert` is a real command. |
| `handzoo review` — the correction loop | **Not built.** PLAN Wave 5, and the M0 exit criterion depends on it. |
| Tests | **102**, plus the frozen `baseline/` corpus as a regression suite. CI never calls a model. |

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

## M0 exit criterion

Not a code artifact, and no amount of green gates substitutes for it:

> **Author-timed** minutes to correct emitted `.tex` to ground truth, versus minutes to transcribe the same page from blank. If correction ≥ transcription, M0 has negative value.

## Branding

`HandZoo™` and `"let there be text"™` are trademarks; the SVGs in `assets/brand/` are trademarked assets, not freely-licensed source. Code is Apache-2.0. See `TRADEMARK.md`.

*(Note: `TRADEMARK.md` still has an unfilled `[INSERT_EMAIL_ADDRESS]`. Author's call.)*
