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

No source code exists yet — `PLAN.md` Wave 0 is the starting point. Python end-to-end with `uv` (D3).

```
handzoo convert notes.pdf --target latex --standalone   # → .tex
handzoo convert notes.pdf --pages 1-5 --resume          # triage; resume from manifest
handzoo review page-01                                  # terminal correction loop
```

Environment, verified on this machine:

| Binary | Role | Status |
|---|---|---|
| `pdftoppm` | rasterize PDF → page PNGs (`-png -r 150`) | present |
| `pdflatex` (MacTeX) | compile gate — **hardcoded for M0** | present |
| `ollama` | recognizer host | present, `qwen3-vl:8b` + `:4b` pulled |
| `uv`, `ruff`, `pytest` | toolchain | `uv` present |
| `tectonic` | CI-reproducibility swap, later | not installed, not blocking |

## Hard constraints

1. **No per-character recognizer.** Glyph recognition is solved; 2-D structure is the hard part. End-to-end image→markup, never classify-then-assemble.
2. **No heuristic segmenter** (D4). The VLM recovers reading order for free; a bbox segmenter would have to reconstruct it.
3. **Four gates, all hard fails:** zero non-ASCII (unless the `--standalone` preamble supports it) · delimiter and environment balance · compiles clean under `pdflatex` · **no silent mark loss**.
4. **Never fabricate `tikz`.** Diagrams are cropped, referenced, and flagged `% TODO: author diagram`.
5. **Never silently drop, reword, or renotate a mark.** This is the same principle as (4), applied where the baseline proved it was missing.
6. **Local-first.** `fixtures/` is gitignored — the manuscript is unpublished IP and this repo is intended for public release.

### The ASCII gate is a trap in the brief

Use `iconv -f ASCII -t ASCII out.tex` (or `s.encode("ascii")`). The brief's `! grep -P '[^\x00-\x7F]' out.tex` is **broken**: `-P` is a GNU/ugrep extension, stock macOS `/usr/bin/grep` errors on it, and the leading `!` inverts that error into a pass. The gate reports clean without ever looking.

## Recognizer — measured constraints, not guesses

`qwen3-vl:8b` via Ollama (supersedes the brief's Qwen2.5-VL). Working reference implementation: `baseline/recognize.py`.

- Reasoning goes to `message.thinking` and **counts against `num_predict`** → always `num_predict: -1`. A 1400-token cap returned empty; 3000 returned empty on one run and valid on another.
- **`think: false` is not honored** on ollama 0.30.7 for this model. Documented, not a bug to fix — do not spend time on it.
- Empty `content` with `done_reason: "stop"` is **normal**. Retry, bounded.
- Use `/api/chat`, not `/api/generate`.
- **Two passes per page:** transcription, then an independent inventory pass. The inventory is trustworthy about *where* marks are and **untrustworthy about what they are** — build the coverage gate on presence and position only.

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

*(Note: `README.md` still says `[MIT / Apache 2.0 - CHOOSE ONE]` while `LICENSE` is Apache-2.0, and `TRADEMARK.md` has an unfilled `[INSERT_EMAIL_ADDRESS]`. Author's call to resolve.)*
