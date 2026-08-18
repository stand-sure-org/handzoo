# HandZoo M0 — Technical Design

**Version:** 1.1 (Post-Delphi design review)
**Date:** 2026-08-18
**Changes:** D6 mechanism revised to two independent passes (empirically adjudicated). `handzoo review` added to scope. Confidence markers, colour, streaming/resume added. Four cuts applied. Golden-test strategy corrected.
**Depends on:** `DECISION.md` (D1–D8) · `reviews/delphi-design-2026-08-18.md` (9 binding conditions)

---

## 1. Shape

Ports and adapters. The engine is a plain Python library that knows nothing about its caller. M0 ships two CLI adapters; MCP (M1) and HTTP/UI (M2) come later against the same engine. No logic in any adapter.

```
handzoo/
  core/                     # the engine — imports nothing from adapters/
    rasterize.py            # PDF -> page PNGs                  (pdftoppm subprocess)
    recognize/
      base.py               # PORT: protocol + Recognition/Region/Inline types
      ollama_vlm.py         # qwen3-vl — transcribe pass + inventory pass
    normalize.py            # Unicode policy, delimiter policy, hardcoded macro seed
    validate/
      ascii_gate.py
      delimiter_gate.py
      compile_gate.py       # pdflatex subprocess
      coverage_gate.py      # D6 — two-pass cross-check
    emit.py                 # target + standalone/fragment + confidence markers
    corrections.py          # append-only correction log
    pipeline.py             # the only orchestrator; per-page streaming + manifest
  adapters/
    cli_convert.py          # handzoo convert
    cli_review.py           # handzoo review
```

**Dependency rule:** `core/` imports nothing from `adapters/`. Enforced by a test that walks each `core/` module's AST and fails on any `adapters` import. ~20 lines. Both Structural and Operational endorsed keeping this and nothing more — *"ports-and-adapters at this size is a boundary, not an architecture."* No DI container, no plugin registry.

## 2. Language decision — the two alternatives (D3)

| | **Python end-to-end** (chosen) | **.NET core, Python behind the recognizer port** |
|---|---|---|
| Fallback recognizers (`pix2tex`, `texify`, `TrOCR`) | Native imports | Subprocess/service per call |
| Ollama access | `urllib` — no SDK | Same |
| `pdftoppm`, `pdflatex` | Subprocess | Subprocess — no difference |
| Coverage-gate image work | Pillow/numpy in-process | Marshal images across the seam, or duplicate CV |
| Process seams | 0 | 1, on the hottest path |
| Author's primary stack | Against the grain | With it |

**Chosen: Python end-to-end.** The quarantine buys stack familiarity and costs a process boundary exactly where the data is largest (page images) and iteration fastest (prompt tuning). If the engine were mostly business logic the calculus would invert; it is glue around Python-native ML tooling. Unanimously approved by the panel.

**Toolchain:** `uv`, `ruff`, `pytest`. Runtime deps: `pillow`, `numpy`. No ML framework in the engine — the recognizer talks to Ollama over HTTP.

## 3. The Recognizer port

Structural's critique of v1.0 was upheld: a flat `regions` tuple could not express containment, colour, or inline-vs-block placement. Revised to a minimal **inline** annotation layer — *not* a full document AST, because the baseline showed block structure (tables, itemize, sections) survives the LaTeX round-trip intact. Everything silently lost lived inline.

```python
Inline = Text(str)
       | Mark(kind, description, placement, confidence)
       | Tally(count: int)              # an int cannot drift to \mathrm{IV}; a string can
       | ColorSpan(color: str, inlines: list[Inline])

@dataclass(frozen=True)
class Recognition:
    markup: str                     # transcription pass output
    inventory: tuple[Mark, ...]     # INDEPENDENT second pass — see §5.4
    provider: str
    model: str
```

`Tally` exists specifically because baseline page 2 converted `1 → 11 → 111` into `I → II → III`, collapsing the exact distinction the page teaches. An integer count cannot make that drift.

### Hard constraints on any Ollama-backed provider (measured, D5)

- Reasoning goes to `message.thinking` and **counts against `num_predict`**. Never cap generation — `num_predict: -1`. A 1400-token cap produced empty output; 3000 produced empty on one run and valid on another.
- **`think: false` is not honored** on ollama 0.30.7 for `qwen3-vl` (20,874 and 28,306 characters of thinking observed with the flag set).
- Empty `content` with `done_reason: "stop"` is a **normal outcome**, not an error. Retry, bounded.
- Use `/api/chat`, not `/api/generate`.
- **Do not issue concurrent requests to one Ollama instance.** Observed 2026-08-18: a second client competing for the same instance serialized behind the first and starved into a socket `TimeoutError` despite a 30-minute timeout. With ~138s transcription plus ~106s inventory per page, `pipeline.py` must process pages **sequentially** — parallelism buys nothing here and converts a slow run into a failed one. This reinforces §8's streaming/`--resume` requirement rather than offering an alternative to it.

These are provider quirks and live in `ollama_vlm.py`. The port's contract is only that recognition either returns a non-empty `Recognition` or raises.

## 4. Normalizer

1. **Unicode → LaTeX command** via lookup table, seeded from the brief and extended as fixtures demand. Produces ASCII.
2. **Delimiter policy.** `\( … \)` inline, `\[ … \]` display; rewrite bare `$`/`$$`.
3. **Macro substitution — a hardcoded dict, not a loadable mechanism.** Cut per panel: `⊕` section markers stripped, `S`/successor → `\mathrm{S}`. A loadable per-user dictionary is M1 infrastructure for a corpus that has no first turn.

**Constraint from the baseline:** a macro pass must never rewrite *meaning*, only *notation*. The tally→Roman conversion the recognizer performed is exactly what a careless macro pass would also do. Any substitution that changes the referent rather than the rendering is rejected at load.

## 5. The gates

Gates are the product (D2). Each returns a structured result, never a bare bool.

### 5.1 ASCII gate
`s.encode("ascii")` semantics, reporting offending codepoints and line numbers. Bypassed only when `--standalone` emits a preamble that supports them.

> **Do not** use the brief's `! grep -P '[^\x00-\x7F]' out.tex`. `-P` is a GNU/ugrep extension; on stock macOS grep it errors, and the leading `!` inverts that error into a pass — the gate reports clean without looking.

### 5.2 Delimiter gate
Balance of `\(`/`\)`, `\[`/`\]`, `$`/`$$`, `\begin{env}`/`\end{env}` by name. No math mode open at end of block.

### 5.3 Compile gate
Headless `pdflatex`, zero errors. **Hardcoded** — the dual-backend selection in v1.0 was cut as speculative generality for a compiler not installed. `tectonic` is a later swap for CI reproducibility.

Caught a real defect on baseline page 2: `\section*{\oplus COUNTING}` — math-mode command in text mode.

### 5.4 Coverage gate (D6) — **two independent passes**

v1.0 had the recognizer return `regions` alongside `markup` from one call. Structural objected that a self-report from the pass being audited is near-worthless — that call has already decided to drop the glyph — and that the capability was never tested. **Both points were upheld, and the mechanism was tested and revised.**

**Experiment (2026-08-18, baseline page 1).** A separate inventory-only pass, given no transcription task, returned all ten marks — *including every glyph the transcription pass had silently folded into prose*:

```json
{"description":"two people drawing for Greater Than symbol","context":"Greater Than >","inline_or_block":"inline","count":1}
```

Because the inventory call is not conditioned on the transcription call's choices, it surfaced exactly what transcription discarded. **The mechanism works as two passes and would not have worked as one field.**

**The critical limitation, also measured:** the inventory's *descriptions* are unreliable — it read a dog glyph as "a stick figure lying down" and the tally marks as "two people". Its *counts and placements* were correct.

> **The inventory pass is trustworthy about *where* marks are, and untrustworthy about *what* they are.**

The gate is therefore built on presence and position only:

1. Transcription pass → `markup`. Inventory pass → `inventory`.
2. Every inline `Mark` in the inventory must have a corresponding marker in the emitted output, matched by position.
3. A mark in the inventory with no marker in the output is a **hard fail**.
4. **Raster ink-density cross-check** — the only signal not sourced from a VLM at all — catches an inventory pass that under-reports itself. Structural's recommendation.

Nothing is built on the descriptions.

Cost: one extra VLM call per page (~106s measured).

### 5.4.1 Three failure modes, not two

The taxonomy matters because the gate only addresses one of them. Baseline page 1 produced all three:

| Mode | Example (page 1) | Caught? |
|---|---|---|
| **Omission** — mark dropped, nothing emitted | `HAS MORE 🧍 THAN` → "has more than" | **Yes** — coverage gate |
| **Substitution** — mark transcribed into wrong notation | tallies → Roman numerals; `\mathcal{H}` for a drawing (4b) | **No** — see §5.5 |
| **Untransformed literal** — mark rendered as bare English prose | `have the same amount of [stick figure]` | **Yes, incidentally** |

The third mode is neither dropped nor renotated: the recognizer emitted the words `[stick figure]` into the document body. It fails the coverage gate because a bare `[stick figure]` is not the `[[DIAGRAM: …]]` marker grammar, so the position match reports MISSING.

That outcome is correct, but it is correct **by accident of grammar, not by design**. Whoever implements `coverage_gate.py` will hit this on the first golden test and be tempted to "fix" the matcher to accept it. **Do not.** A bare literal is a mark the emitter failed to handle, and it must fail. The marker grammar is exact.

### 5.5 What no gate catches — and where this is stated

Semantic **substitution**: output that is well-typeset, ASCII-clean, balanced, compiles, preserves every mark — and is still false. `|||| < ||||` passes every gate here.

D6 converts silent *loss* into loud loss. It leaves silent *substitution* as silent as it was. The only mechanism identified is a math-reasoning model reading the emitted LaTeX (`qwen2-math`, M1+).

**Per binding condition 3, this qualification lives in DECISION.md D2 alongside the positioning claim, not only here — the louder claim must not ship before the hedge.** The CLI never prints an unqualified PASS; a passing page reports what was *not* checked.

## 6. Emitter

`--target latex`, `--standalone|--fragment`. `--target markdown` **cut** — the deliverable is LaTeX and no evidence exists that Markdown is needed.

Block marks emit as a referenced cropped PNG plus `% TODO: author diagram`. Inline marks emit as an inline marker that survives to the human. Never `tikz`.

**Confidence markers** (binding condition 4): low-confidence spans are wrapped so a reader can distinguish transcription from guess. The inventory pass is already being built for D6, so this is nearly free. An artifact that performs certainty it does not have is the same dishonesty the never-fabricate rule exists to prevent.

**Colour** (binding condition 5): colour-bearing ink is a `ColorSpan`. Silent loss of it is a hard fail under the coverage gate. Faithful reconstruction is deferred; *silent* discard is not permitted. On the fixtures, R/G/B houses written in red/green/blue carry the labelling.

## 7. `handzoo review` — the correction loop (in M0 scope)

Added per binding condition 2. Usability: *"a validator with no way to act on what it validates is not a product, it's a linter."*

```
handzoo review <page>
```

Line-at-a-time terminal walk — no curses, no HTTP. For each inventory mark and each gate failure: show the source crop coordinates, the emitted line, prompt `[k]eep / [e]dit / [f]lag / [s]kip`. `e` opens `$EDITOR` at that line. Every decision appends to `.handzoo/corrections.jsonl`:

```json
{"page": 1, "region": 3, "original": "...", "correction": "...", "verdict": "edit"}
```

A flat append log — no Learning Store, no schema migration. It is the flywheel's first turn, and it is cheaper to build than the coverage gate.

## 8. CLI behaviour

Per binding condition 7 and Operational's top-ranked risk (no checkpointing: a crash on page 40 of 51 loses 39 pages):

- **Streaming** — per-page results written as each completes, never batched at the end.
- **`--resume`** — from a manifest keyed on last-completed page.
- **`--pages 1-5`** — triage before committing to a full run.
- Failing pages write to a `.fail.tex` path with a nonzero exit, so a build cannot silently consume them.

Failure output is explicit about what failed and what to do next:

```
[4/4] validate
  ascii ................. PASS
  delimiters ............ PASS
  compile ............... PASS
  coverage .............. FAIL

COVERAGE GATE FAILED — 4 marks seen, 0 markers emitted
  region 1  "stick figure"  (inline, after "HAS MORE")  -> MISSING
  ...
  NOT checked: semantic correctness. Gates prove it builds, not that it is true.
  -> run: handzoo review page-01
```

## 9. Testing

TDD, consumer-first (ISP): `pipeline.py` is written against the ports before providers exist.

- **Unit:** each gate against hand-built fixtures — known-imbalanced, known-non-ASCII, known-uncompilable, known-glyph-dropping.
- **Golden (corrected per binding condition 6):** the checked-in baseline `.tex` bytes are fed **directly into the gate functions**. CI never invokes `recognize()`. v1.0's prose implied an end-to-end run, which would have pinned tests to the least stable part of the system — the baseline showed run-to-run variance in quote style and two runs returning nothing at all. Page 1 must fail the coverage gate; page 2 must fail the compile gate. These are gate-regression tests on frozen strings.
- **Contract:** `core/` imports no adapter (§1).
- **Not tested in CI:** recognizer accuracy. Nondeterministic and model-versioned — measured and recorded in `baseline/`, never asserted.

## 10. Measured performance

| Run | Wall clock | Eval tokens | Thinking |
|---|---|---|---|
| `qwen3-vl:8b` transcribe, page 1 | 138.5s | 6,275 | 20,874 ch |
| `qwen3-vl:8b` transcribe, page 2 | 144.1s | 8,834 | 28,306 ch |
| `qwen3-vl:8b` inventory, page 1 | 105.8s | — | 29,137 ch |

Thinking dominates — roughly 80% of generated tokens are discarded reasoning. With the two-pass coverage gate, budget ~245s/page; a 51-page document is ≈3.5 hours, which is what makes streaming and `--resume` mandatory rather than nice-to-have.

### `qwen3-vl:4b` comparison — **resolved: keep 8b**

Operational's decision rule: adopt 4b only if its failure catalogue is no worse **and** wall-clock drops below ~70s.

| | `qwen3-vl:8b` | `qwen3-vl:4b` |
|---|---|---|
| Page 1 wall clock | 138.5s | **129.1s** (7% faster) |
| Thinking | 20,874 ch | 17,210 ch |
| Content | 848 ch | 758 ch |

**Both criteria fail.**

1. **Speed.** 129.1s against a 70s bar. Halving parameters bought 7%, because wall clock is dominated by *thinking* tokens, which scale with reasoning effort rather than model size. **No smaller model fixes the latency problem** — the fix, if one is needed, is suppressing thinking, and that is not available on ollama 0.30.7 (see §3).
2. **Failure catalogue is worse in the way that matters.** 4b reproduced the contradictory-bullets failure identically, but in the notation table it emitted `$\mathcal{H}$:` where a hand-drawn glyph belongs:

   ```latex
   $\mathcal{H}$: $G > R$ & $\mathcal{H}$: $R < G$ \\
   ```

   8b *deleted* those glyphs. 4b **invented a plausible-looking LaTeX symbol for a drawing** — output that reads as real math and is not. Operational predicted exactly this: *"smaller models often compensate for uncertainty by inventing plausible content — worse for a project whose entire thesis is confabulation is the enemy."*

   Note the irony: 4b preserved the glyph *positions* 8b discarded, which the coverage gate would reward. It is the confabulated *symbol* that makes it worse, and no gate in M0 catches that.

**Coverage caveat, stated rather than buried:** the 4b page-2 run did not complete — the harness hit a 10-minute timeout after page 1. **The decision was made on page 1 alone.** It holds on both criteria independently (129.1s against a 70s bar; a confabulated `\mathcal{H}` for a drawing), but the comparison is one page deep, not two. Re-run page 2 if 4b is ever reconsidered.

`:2b` is not tested — Operational advised against it, and the latency finding removes the motive.

## 11. M0 exit criterion

Adopted from Value's dissent (binding condition 9), which no experiment in this review could settle:

> **Author-timed:** minutes to correct emitted `.tex` to ground truth, versus minutes to transcribe the same page from a blank file. Both on the same two baseline pages, by the author.

If correction time ≥ transcription time, M0 has negative value regardless of gate colour. Edit-distance alone is insufficient — catching `|||| < ||||` requires re-reading the source page, which is most of the transcription cost, and edit-distance scores that diff as small.

This requires the author and cannot be automated.
