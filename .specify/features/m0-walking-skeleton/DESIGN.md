# HandZoo M0 — Technical Design

**Version:** 1.3 (num_ctx, pylatexenc, expanded corpus)
**Date:** 2026-08-18
**Changes since 1.2:** `num_ctx` established as a **correctness** constraint — Ollama's 262k default allocated 42 GB and swamped the host, causing nondeterminism that read as hard pages. Preflight health check added (§3.1). Normalizer rebuilt on `pylatexenc`: 71% → 88% on identical input. Emitter now always owns the preamble. Assembly model (`\input`/`\include`) specified (§6.1). Corpus expanded to four documents (§10.1); `--target markdown` cut challenged by a prose-only document.
**Changes since 1.1:** Normalizer rules R1–R4 added, each traced to a measured gate failure. Diagram markers must never be emitted raw into the body (design correction, not just normalization). Corpus identified as **two distributions** that must be measured separately. Latency revised — symbolic content runs 3–4× faster than pedagogical.
**1.1 changes:** D6 mechanism revised to two independent passes (empirically adjudicated). `handzoo review` added to scope. Confidence markers, colour, streaming/resume added. Four cuts applied. Golden-test strategy corrected.
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

- **`num_ctx` MUST be set explicitly. This is correctness, not tuning.** Ollama's default
  context for `qwen3-vl` is **262,144**, which allocates a KV cache far larger than the
  weights and, on a 64 GB machine, guarantees system-wide swapping.

  | | default (262144) | `num_ctx: 8192` |
  |---|---|---|
  | Resident size (`qwen3-vl:4b`, 3.3 GB weights) | **42 GB** | — |
  | Resident size (`qwen3-vl:8b`, 6.1 GB weights) | — | **6.6 GB** |
  | ch16 p9 latency | 89s, when it completed at all | **40.4s** |

  A page image plus prompt plus response needs a few thousand tokens, not 262k.

These are provider quirks and live in `ollama_vlm.py`. The port's contract is only that recognition either returns a non-empty `Recognition` or raises.

### 3.1 Preflight health check — the failure is environmental, not content

Every measurement before 2026-08-18 15:00 was taken on a machine at **87% memory pressure
with 29.7 GB of 30.7 GB swap consumed**, because two VLMs were resident at 256k context.

The consequence is not slowness, it is **nondeterminism that looks like a hard page**.
ch16 p9 produced, on identical input and settings: empty ×3 in the batch, a >10-minute stall
with zero completed attempts on re-run, and a clean 89s success in a controlled experiment.
Nothing about the page explains that — it was diagnosed only by looking at the host.

Do not treat "2/26 pages fail" as a page property. It is a per-attempt failure rate that can
hit any page, driven by host state.

`recognize()` must therefore preflight and refuse rather than produce garbage:

| Check | Source | Refuse when |
|---|---|---|
| Resident model context | `GET /api/ps` → `context` | far larger than configured `num_ctx` |
| Model fully on GPU | `GET /api/ps` → `size_vram == size` | partial offload |
| Swap headroom | `sysctl vm.swapusage` | free swap near zero |
| Concurrent requests | in-process | more than one in flight (they starve each other) |

A per-attempt **timeout** is also mandatory. Unbounded retry cannot recover from a stall of
unbounded duration; the 10-minute hang would have consumed the whole run.

**On the GPU:** Ollama is already fully Metal-accelerated — measured at 100% GPU, model
entirely in VRAM, on an M5 Max (40 cores, Metal 4). There is no unused accelerator to switch
on. Apple Silicon's memory is *unified*, so an oversized KV cache does not merely fill "VRAM"
— it starves the whole machine and forces swap, which is why the GPU sat at 7% and 0.0 W
while stalled. MLX is the nearer-to-the-metal alternative runtime, but it would not have
helped here: the bottleneck was a context-window default, not the compute path.

## 4. Normalizer

Four rules, each written because a measured run failed a gate. Prototype validated on
Cheng ch16 pages 1 and 3, which the raw recognizer failed to compile.

| # | Rule | Failure that motivated it |
|---|---|---|
| **R1** | Unicode → LaTeX command via lookup table | ch16 p3 emitted a literal `§` |
| **R2** | **Wrap math-only commands appearing in text mode** | naive p2 `\oplus`; ch16 p1 `\uparrow`/`\downarrow` ×4 |
| **R3** | Diagram marker → comment + escaped placeholder | ch16 p3 `[[DIAGRAM: 0 \to 1 …]]` |
| **R4** | Fragment → standalone when `--standalone` | ch16 p3 emitted no preamble; p1 did |

**Built on `pylatexenc`, not regex.** Implemented in `handzoo/core/normalize.py`. The first
version hand-rolled a Unicode table and guessed at math spans with regex; it reached 71% on
the ch16 corpus. Rebuilding on a real LaTeX parser reached **88% on identical input**:

| | v1 (regex, hand-built table) | v2 (`pylatexenc`) |
|---|---|---|
| ASCII + compile, 24 ch16 pages | 17/24 (71%) | **21/24 (88%)** |

Three things the library gives that regex cannot:

- `latexencode.unicode_to_latex()` — a complete Unicode table. The hand-built dict missed `Σ`
  and `ü`; every new document would have found fresh gaps. Its output uses `\ensuremath{}`,
  which is a no-op in math mode, so the transform is **idempotent and mode-agnostic**.
- `latexwalker` — parses to a node tree that knows which spans are math. This is what makes
  R2 work for *argument-taking* macros: `\mathbb{Z}` must be wrapped together with its
  argument, which a span-based regex cannot see.
- Correct handling of nested groups and environments when rebuilding.

**The emitter owns the preamble — always.** v2 initially kept a model-emitted `\documentclass`
when present; those preambles declared `\begin{proposition}` and `\begin{proof}` while loading
neither `amsthm` nor any `\newtheorem` (ch16 p7, p10). The standalone preamble is now always
substituted, and carries `amsmath`, `amssymb`, `amsthm`, `fontenc`, `geometry`, and
`\newtheorem` for the environments the recognizer actually emits unprompted: `definition`,
`proposition`, `theorem`, `lemma`, `corollary`, `example`, `remark`. Those environments are
*good* recognition the emitter was failing to support.

**R2 is the highest-value rule in the Normalizer and was invisible until real pages ran.**
The recognizer emits math-mode commands into text mode on **3 of 4 measured pages**, across
two different documents and two different content styles. It is systematic, not incidental.
Implementation must skip spans already inside `$…$` — naive substitution double-wraps.

**R3 is a design correction, not just a normalization step.** v1.0 specified the
`[[DIAGRAM: …]]` marker as body text. That holds only while descriptions are plain English,
which they were on the Naive Math pages. On ch16 p3 the description contained `\to`, and the
marker itself broke the build. **Markers must never be emitted raw into the body** — emit an
escaped `\texttt{}` placeholder plus a `% TODO: author diagram` comment.

Then, unchanged from v1.0:

5. **Delimiter policy.** `\( … \)` inline, `\[ … \]` display; rewrite bare `$`/`$$`.
6. **Macro substitution — a hardcoded dict, not a loadable mechanism.** Cut per panel: `⊕` section markers stripped, `S`/successor → `\mathrm{S}`.

Residual non-ASCII after R1 is a **hard fail**, never papered over — an unmapped glyph is an
unknown glyph, and silently dropping it is the D6 violation in another costume. A loadable per-user dictionary is M1 infrastructure for a corpus that has no first turn.

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

The ch16 run added three more, all of which pass every gate after normalization:

| Source | Emitted | What was lost |
|---|---|---|
| `↰` / `↳` annotation arrows | `\uparrow` / `\downarrow` | The arrows point *at* what they annotate. Flattened, the nesting is gone — "multiplicative identity" now annotates the equation rather than "a defn of one". |
| `∀ object X ∈ 𝒞` | "for any object $X \in \mathcal{C}$" | A formal quantifier silently became English prose — **while `∀ n ∈ ℕ` four lines later was kept as `\forall`**. Same symbol, same page, two treatments. |
| `0 → 1 → 2 → 3 → ⋯` | `[[DIAGRAM: …]]` | Inline math misclassified as a diagram. Content survives, but is flagged for hand-authoring it does not need — a false positive that costs human time. |

The second is the most instructive: inconsistency *within a single page* means this cannot be
fixed by a better prompt alone. Two identical inputs took different paths in one pass.

D6 converts silent *loss* into loud loss. It leaves silent *substitution* as silent as it was. The only mechanism identified is a math-reasoning model reading the emitted LaTeX (`qwen2-math`, M1+).

**Per binding condition 3, this qualification lives in DECISION.md D2 alongside the positioning claim, not only here — the louder claim must not ship before the hedge.** The CLI never prints an unqualified PASS; a passing page reports what was *not* checked.

### 5.6 Independent second reader (deferred to M1) — measured, does not work yet

Proposed: run Tesseract per page as a reader with *uncorrelated* failure modes and score
agreement with the VLM (Jaccard of token sets), both to tune prompts and to flag pages
needing human attention. The target is right — semantic substitution is uncaught by every
gate, and D6's second pass is the same model, so its errors correlate.

**Measured on all 26 ch16 pages, 2026-08-18. It does not separate good pages from bad.**

| Metric | Range | Median |
|---|---|---|
| word-set Jaccard | 0.000 – 0.176 | 0.095 |
| char-trigram Jaccard | 0.066 – 0.281 | 0.197 |

- Pages independently known to fail gates (04, 06, 07, 10, 16, 18, 25) scored 0.198–0.251 —
  at or **above** the median.
- The single lowest score (p02, 0.066) is a page that **passed every gate**; Tesseract found
  8 words there against the VLM's 36.
- Tesseract's own mean confidence is no better: p25 lowest (32) and p06 highest (57) are
  *both* failures.

Low scores track **Tesseract failing**, not the VLM failing. At roughly 20–40% word accuracy
on this handwriting, the referee's variance swamps the signal it is meant to measure.

**The blocking problem is more general: no proxy metric can be validated without ground
truth.** Any score — Jaccard, confidence, word-count ratio, ink density — is unfalsifiable
until there is a corrected corpus to correlate it against. This is the same dependency as the
fine-tuning question: downstream of `handzoo review`, not upstream of it.

**Revisit when 20–30 corrected pages exist**, then test which proxy correlates with measured
edit distance. Tesseract costs **0.2s/page against the VLM's 92s**, so if a proxy ever
validates it is effectively free to run on every page. Worth keeping for that reason alone.

## 6. Emitter

`--target latex`, `--standalone|--fragment`. `--target markdown` **cut** — the deliverable is LaTeX and no evidence exists that Markdown is needed.

Block marks emit as a referenced cropped PNG plus `% TODO: author diagram`. Inline marks emit as an inline marker that survives to the human. Never `tikz`.

**Confidence markers** (binding condition 4): low-confidence spans are wrapped so a reader can distinguish transcription from guess. The inventory pass is already being built for D6, so this is nearly free. An artifact that performs certainty it does not have is the same dishonesty the never-fabricate rule exists to prevent.

**Colour** (binding condition 5): colour-bearing ink is a `ColorSpan`. Silent loss of it is a hard fail under the coverage gate. Faithful reconstruction is deferred; *silent* discard is not permitted. On the fixtures, R/G/B houses written in red/green/blue carry the labelling.

### 6.1 Assembly — pages into sections into chapters

M0 emits per-page files. It must not paint itself out of assembling them, so the file layout
is a design decision now even though multi-page assembly is not M0 scope.

- **`\input` for page fragments.** No implicit `\clearpage`, nestable, works anywhere. A page
  is not a page break — the manuscript's structure is independent of where the reMarkable
  export happened to break.
- **`\include` for chapters only.** It forces a `\clearpage` and is legal only at top level,
  but it enables `\includeonly`, which makes partial rebuilds fast while drafting.

```
manuscript.tex          % \include{ch16}
  ch16.tex              % \input{ch16/p-01} \input{ch16/p-02} ...
    ch16/p-01.tex       % --fragment output: body only, no preamble
```

Consequence for the Emitter: `--fragment` is not a convenience flag, it is **the normal
output mode for assembly**, and `--standalone` is for reviewing a single page. Fragments must
therefore carry no preamble and no `\begin{document}`, which is already how R4 treats them.

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
| Naive Math p1, transcribe | 138.5s | 6,275 | 20,874 ch |
| Naive Math p2, transcribe | 144.1s | 8,834 | 28,306 ch |
| Naive Math p1, inventory | 105.8s | — | 29,137 ch |
| **Cheng ch16 p1, transcribe** | **49.2s** | 2,936 | 8,975 ch |
| **Cheng ch16 p3, transcribe** | **32.1s** | 2,269 | 6,286 ch |

### The corpus has two distributions, and they behave differently

This is the most consequential thing the ch16 run showed, and it revises the eval plan.

| | **Pedagogical** (Naive Math) | **Symbolic** (Cheng notes) |
|---|---|---|
| Content | Prose, tables, tally marks, drawings | Dense notation: `∃!`, `∈ℝ`, `∀`, `𝒞`, `≤`, `→` |
| Inline glyphs as sentence terms | **Yes** — a stick figure is a noun | No |
| Semantic colour | **Yes** — R/G/B in red/green/blue | No, monochrome |
| Wall clock | 138–144s | **32–49s** |
| Thinking | 21–28k chars | 6–9k chars |
| Dominant failure | Silent glyph loss, contradiction | Math-mode-in-text-mode, notation substitution |

Sloppier handwriting was **not** the problem. Recognition on dense symbolic math was
markedly better *and* 3–4× faster — ambiguity, not legibility, drives both cost and error.
`\exists !`, `\in \mathbb{R}`, `\forall`, `\mathcal{C}`, `\leq` all came through clean.

**Consequence for §11 and the fixture set:** the symbolic distribution is far closer to
working, and the two must be measured separately. A single blended accuracy number would
hide that one class is nearly usable while the other is not. The M0 exit criterion should be
timed on **both** distributions, not an average.

**Consequence for latency:** the 138s figure generalises worse than assumed. Budget by
content type, not per page.

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

## 10.1 Fixture corpus

Four documents, deliberately unlike each other. All are the author's own hand; none are
tracked (D7). Every latency figure in §10 predates the `num_ctx` fix and was measured on a
swapping machine — treat them as upper bounds, not characteristics.

| Document | Pages | Ink | What it stresses |
|---|---|---|---|
| Naive Math | 39 | multi-colour | Inline glyph-as-noun, **semantic colour**, tables, tally marks |
| Cheng ch14–16 | 26+ | monochrome purple | Dense symbolic math, labelled arrows, theorem environments |
| **Number theory** | **126** | monochrome | Long-run consistency; the only document large enough to test drift |
| **Topology** | **8** | blue | Deep set-brace nesting, `∪`/`∩`/`∉`, **checkmarks as inline annotations**, circled and boxed elements |
| **Team of Teams** | **17** | black | **Prose only, zero math** — plus rotated marginalia, enclosure-as-emphasis, margin markers |

### Two findings from the new documents, before any run

**1. `--target markdown` was cut too early.** Team of Teams contains no mathematics at all —
it is book notes: headings, statements, emphasis. Forcing it through a LaTeX pipeline with a
compile gate is the wrong target entirely; Markdown is the natural one. The panel cut the
Markdown target on the grounds that "the deliverable is LaTeX", which was true of the two
documents visible at the time. It is not true of the corpus. **Reinstate `--target markdown`,
or state explicitly that prose-only documents are out of scope.**

**2. Three new mark types with no obvious LaTeX representation**, none of which appear in the
earlier fixtures:

- **Rotated marginalia** — Team of Teams has text written vertically in the margin at 90°.
- **Enclosure as emphasis** — a hand-drawn ellipse around a phrase, meaning "this matters".
  Not a diagram, not a box; a *semantic* annotation on the enclosed text.
- **Checkmarks as inline annotations** — Topology has `✓` under `∪` and `∩`, asserting "this
  axiom holds". The checkmark is a claim about the symbol above it, i.e. the same
  annotates-what-is-above relationship as the `↰` arrows in ch16.

All three are the inline-mark class the coverage gate (§5.4) exists for. None can be cropped
out without destroying meaning, which is further evidence against the brief's separability
assumption.

## 11. M0 exit criterion

Adopted from Value's dissent (binding condition 9), which no experiment in this review could settle:

> **Author-timed:** minutes to correct emitted `.tex` to ground truth, versus minutes to transcribe the same page from a blank file. Both on the same two baseline pages, by the author.

If correction time ≥ transcription time, M0 has negative value regardless of gate colour. Edit-distance alone is insufficient — catching `|||| < ||||` requires re-reading the source page, which is most of the transcription cost, and edit-distance scores that diff as small.

This requires the author and cannot be automated.
