# HandZoo M0 — Implementation Plan

**Version:** 1.0
**Date:** 2026-08-18
**Depends on:** `DESIGN.md` v1.1 · `DECISION.md` v1.0 · `reviews/delphi-design-2026-08-18.md`

Estimates are complexity points (Fibonacci), not time. Ordering follows the Operational persona's dependency analysis, amended for the two-pass coverage gate and `handzoo review`.

---

## Wave 0 — Foundations (blocking; everything else depends on shape)

| # | Task | Pts | Notes |
|---|---|---|---|
| 0.1 | `uv` project scaffold, `ruff` + `pytest` config, `handzoo/` package layout per DESIGN §1 | 2 | No src-layout debate; match DESIGN §1 exactly |
| 0.2 | `core/recognize/base.py` — `Inline`, `Mark`, `Tally`, `ColorSpan`, `Recognition` | 3 | The contract every later task codes against (ISP: consumer-first) |
| 0.3 | AST import-boundary test — `core/` may not import `adapters/` | 1 | ~20 lines; the one architectural invariant worth enforcing |

**Gate:** 0.2 is merged before Wave 2 starts. Nothing downstream is meaningful without the type contract.

## Wave 1 — Pure-logic gates (fully parallel, zero model dependency)

Start immediately alongside Wave 0. TDD — tests before implementation, each against hand-built fixtures.

| # | Task | Pts | Notes |
|---|---|---|---|
| 1.1 | `ascii_gate.py` + tests | 2 | `s.encode("ascii")`; report codepoints + line numbers. **Not** the brief's broken `grep -P` form |
| 1.2 | `delimiter_gate.py` + tests | 3 | `\(`/`\)`, `\[`/`\]`, `$`/`$$`, `\begin`/`\end` by name; no open math at end of block |
| 1.3 | `compile_gate.py` + tests | 2 | `pdflatex` hardcoded (dual-backend selection cut). Verified present on this machine |
| 1.4 | `normalize.py` — Unicode table, delimiter policy, hardcoded macro seed | 5 | Seed the table from the two baseline pages' actual failures, not speculatively |

**Verification:** 1.3 must reproduce the real defect — `\section*{\oplus COUNTING}` from baseline page 2 fails with "Missing $ inserted."

## Wave 2 — Recognizer (parallel with Wave 1; already prototyped)

| # | Task | Pts | Notes |
|---|---|---|---|
| 2.1 | `rasterize.py` — `pdftoppm` wrapper | 2 | Working invocation already proven: `-png -r 150` |
| 2.2 | `ollama_vlm.py` transcribe pass | 3 | Port `baseline/recognize.py`. Carry the three hard constraints: uncapped generation, `/api/chat`, empty-content retry |
| 2.3 | `ollama_vlm.py` inventory pass → `tuple[Mark, ...]` | 5 | Prompt proven in DESIGN §5.4. Parse strict JSON; treat descriptions as untrusted |

**Constraint:** `think: false` is not honored on ollama 0.30.7. Do not spend time trying to make it work — it is documented, not a bug to fix.

## Wave 3 — Coverage gate (blocked on 0.2 and 2.3)

| # | Task | Pts | Notes |
|---|---|---|---|
| 3.1 | `coverage_gate.py` — inventory ↔ marker cross-check by position | 5 | Presence and position **only**. Nothing built on inventory descriptions — they were measurably wrong |
| 3.2 | Raster ink-density cross-check | 5 | The only signal not sourced from a VLM. Catches an inventory pass that under-reports itself |

**Verification:** baseline page 1 must FAIL this gate. It currently passes all three existing gates while dropping four glyphs — that is the regression this gate exists to prevent.

## Wave 4 — Emitter and pipeline

| # | Task | Pts | Notes |
|---|---|---|---|
| 4.1 | `emit.py` — latex target, standalone/fragment, `% TODO` for block marks | 3 | `--target markdown` cut |
| 4.2 | Confidence/provenance markers on emitted output | 3 | Binding condition 4 |
| 4.3 | `ColorSpan` emission; silent colour loss fails the coverage gate | 3 | Binding condition 5 |
| 4.4 | `pipeline.py` — per-page streaming, manifest, `--resume`, `--pages N-M` | 5 | Operational's top-ranked risk: no checkpointing loses hours of work |
| 4.5 | `adapters/cli_convert.py` — including the never-unqualified-PASS output | 3 | A passing page must report what was *not* checked |

## Wave 5 — Correction loop

| # | Task | Pts | Notes |
|---|---|---|---|
| 5.1 | `corrections.py` — append-only `.handzoo/corrections.jsonl` | 2 | Flat log, no schema migration, no Learning Store |
| 5.2 | `adapters/cli_review.py` — `[k]eep / [e]dit / [f]lag / [s]kip`, `$EDITOR` at line | 5 | Binding condition 2. Cheaper than the coverage gate, closes a larger gap |

## Wave 6 — Test suite consolidation

| # | Task | Pts | Notes |
|---|---|---|---|
| 6.1 | Golden gate-regression tests on frozen `.tex` bytes | 3 | **CI never calls `recognize()`.** Page 1 fails coverage; page 2 fails compile |
| 6.2 | End-to-end smoke on one page, marked slow, excluded from default CI | 2 | Nondeterministic by nature |

**Total: 74 points.** No single task exceeds 5 — nothing needs decomposition.

---

## Parallelisation

```
Wave 0.1 ─┬─ 0.2 ──────────────┬─ Wave 3 ─┬─ Wave 4 ─── Wave 5 ─── Wave 6
          └─ 0.3               │          │
Wave 1 (1.1─1.4) ──────────────┤          │
Wave 2 (2.1─2.3) ──────────────┘──────────┘
```

Waves 1 and 2 run concurrently with each other and with 0.2/0.3. Wave 3 is the first real join.

## Open items not on the critical path

| Item | Status |
|---|---|
| `qwen3-vl:4b` vs `:8b` comparison | Running. Decision rule in DESIGN §10. Pure measurement — blocks nothing |
| `brew install tectonic` | Deferred. `pdflatex` is present and hardcoded; tectonic is a CI-reproducibility swap |
| `qwen2-math` as semantic checker | M1+. The only identified mechanism against semantic substitution |
| Repository `git init` baseline commit | **Not yet done — zero commits exist.** Awaiting author |

## Exit criterion

Per DESIGN §11 and binding condition 9 — this is what "M0 done" means, and it is not a code artifact:

> Author-timed minutes to correct emitted `.tex` to ground truth, versus minutes to transcribe the same page from blank. If correction ≥ transcription, M0 has negative value regardless of gate colour.

Requires the author. Cannot be automated, and no amount of green gates substitutes for it.

## Explicitly out of M0

MCP adapter · Learning Store · web correction UI · macro-dictionary learning · diagram auto-conversion · cross-user pooling · fine-tuning · `--target markdown` · provider registry · tectonic dual-backend.
