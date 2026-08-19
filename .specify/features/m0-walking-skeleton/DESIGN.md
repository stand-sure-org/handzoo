# HandZoo M0 — Technical Design

**Version:** 1.7 (INSTRUCT checkpoint — supersedes the blank investigation)
**Date:** 2026-08-18
**Changes since 1.6:** `qwen3-vl:8b` identified as an alias for the *Thinking* checkpoint. Switching to `qwen3-vl:8b-instruct` eliminated blanks entirely (2/26 -> 0/26), raised pass rate 81% -> 96%, and cut median latency 92s -> 4s. Root cause was our checkpoint choice, not Ollama (§3.0). Most of §3.1-3.3 is now investigation record.
**Changes since 1.5:** Silent truncation identified as a failure worse than the blank and invisible to every gate (§3.2.1) — no completeness check exists, and one naive form was tried and failed. Split-and-recover found untestable: blanks cannot be reproduced, now on five flipped pages (§3.2.2).
**Changes since 1.4:** Self-prediction probes measured and rejected as failure predictors; the reasoned form retained as a review attention router (§3.3). Naive symbolic checking downgraded — the `6 x 9 = 42` fixture is a correct page a naive checker would flag (§5.5.3). Formal verification (Lean/Agda) scoped and deferred.
**Changes since 1.3:** Blank-response root cause identified as an open Ollama defect; the documented workaround tested and rejected; early detection measured and rejected (§3.2). Substitution named as "over-correction" with prompting shown ineffective (§5.5.1) and defences ranked by evidence (§5.5.2). HITL research folded in — `keep` split into reviewed/unreviewed (§7.1). `--target markdown` reinstated as M1. Competitive position recorded in DECISION.
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

### 3.0 Use the INSTRUCT checkpoint. This supersedes most of §3.1–§3.3.

**`qwen3-vl:8b` is an alias for the *Thinking* checkpoint.** `qwen3-vl:8b-instruct` is a
separate model — different post-training weights, not a renderer setting. Qwen positions
Instruct for *"high-volume production, OCR pipelines"*; Thinking for STEM tutoring and
step-reasoning tasks. **We had been using a reasoning model for a transcription task.**

Full ch16 corpus, same prompt, same options, same normalizer, same gates — only the
checkpoint differs:

| | Thinking (`qwen3-vl:8b`) | **Instruct (`qwen3-vl:8b-instruct`)** |
|---|---|---|
| Blank responses | 2 / 26 | **0 / 26** |
| ASCII gate | 21 / 24 | **26 / 26** |
| Compile gate | 21 / 24 | **25 / 26** |
| Overall pass | 21 / 26 (81%) | **25 / 26 (96%)** |
| Median latency | 92s | **4s** |
| Corpus wall clock | ~60 min | **1.9 min** |

**23× faster, zero blanks, and higher accuracy.** Quality improved on both diagnostic
failures, not just throughput:

- **`Quick sheets` p42** — Thinking truncated after four lines, deleting the base-13
  justification (§3.2.1). Instruct transcribed the whole argument including `6_{13} \times
  9_{13} = 42_{13}` and the subscripted relation `6 \times 9 =_{13} 42`.
- **Naive Math p1** — Thinking silently deleted the inline glyphs, producing two contradictory
  bullets. Instruct emits a **placeholder at each glyph position** instead of dropping it. Not
  correct identification, but loud rather than silent — exactly what D6 requires, and now the
  coverage gate has something to match.

**The root cause was our checkpoint choice, not Ollama.** Ollama's broken `think` control
(§3.2) is real and blocked the obvious mitigation, but the deeper error was selecting a
reasoning model by accepting a default alias. The blanks were reasoning loops in a model that
should never have been reasoning.

**Measurements taken on the Thinking checkpoint are obsolete for latency and blank behaviour**
— the two-distributions latency split (§10), the `:4b` comparison, and everything in
§3.2–§3.3 describe a model we no longer use. They are retained as investigation record, and
because the *memory* finding (`num_ctx`, §3 below) and the *content* findings (§5.5
substitution, §5.6) are checkpoint-independent and still hold.

#### What the Instruct switch did NOT fix: over-correction

Measured on Naive Math p2, the tally-marks page, immediately after the switch:

| Source | Instruct emitted |
|---|---|
| tally marks `\|\|\|` `\|\|\|` `\|\|\|\|` in a table | `III` `III` `IIII` — Roman numerals, identical to Thinking |
| `1 → 11 → 111 → 1111 → ЖHT` (tallies) | `I \to II \to III \to IIII \to V \to VI` |
| a tally expression `ЖHT ЖHT ЖHT ЖHT III = …` | **`$$IV + IX = XIV$$` — a fabricated equation, and false (4+9≠14)** |
| `))))` vs `ЖHT` | `III < V in fingers` — wrong counts |

**The Instruct checkpoint fixes blanks and truncation. It does not touch substitution**, and on
this page it additionally *invented* a false equation where the source had a tally expression.

This is exactly what §5.5.1 predicts: over-correction was measured across **15 different VLMs**
at 42–66%. It is a property of the model class, not of a checkpoint. Do not expect a model
swap to solve it — the defences in §5.5.2 remain necessary.

**Re-measure required** before trusting: per-document pass rates and the two-distribution claim.

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

### 3.2 The blank response — root cause known, early detection measured and rejected

**Root cause is an open Ollama defect, not our usage.** `qwen3-vl:8b` ships
`RENDERER qwen3-vl-thinking` with a bare `TEMPLATE {{ .Prompt }}` carrying **no**
think-control logic — verified directly. `ollama/ollama#13353` confirms `Qwen3VLRenderer.Render()`
discards the `thinkValue` argument and the parser never forwards it; **open, unfixed**.
Related: `#14716` (vision inputs route output into `thinking`, content stays empty),
`#14798` (VL template lacks the `$.IsThinkSet` logic the text variants have), `#14793`
(`/api/generate` ignores `think:false`, matching our measurement).

**The documented workaround does not work — tested.** Rebuilding the model with
`RENDERER/PARSER qwen3-vl-instruct` does not stop the model thinking; it stops the parser
*separating* the thinking. Raw `<think>` text lands in `content` and the model entered a
repetition loop (one line 23×, another 10×): 22,778 chars of non-LaTeX where the page
yields ~650. That is worse than the bug, because it is plausible-looking garbage rather than
an obvious empty. Variant deleted.

**Early detection: measured, and it does not work.** Streaming `/api/chat` and recording time
to first content token across three good and three blank pages:

| | t_first | think at first content | think total | rep/1k chars |
|---|---|---|---|---|
| good ch16 p3 | 3.1s | 6,110 | 6,110 | 66 |
| good ch16 p8 | 5.1s | 12,848 | 12,848 | 100 |
| good ch16 p1 | 2.0s | **14,912** | 14,912 | 136 |
| blank top p7 | 8.7s | — | 15,716 | **127** |
| blank nt p10 | 2.2s | — | 16,364 | 243 |
| blank nt p40 | 7.1s | — | 15,254 | 346 |

- **Time to first token does not separate** the classes (2.0–8.7s in both).
- **Thinking length overlaps.** A good page reached 14,912 characters before emitting content;
  blanks finish at 15,254–16,364. Any threshold catching blanks also aborts good pages.
- **Repetition density overlaps.** Good `ch16 p1` (136/1k) scores *higher* than blank
  `top p7` (127/1k).
- Blanks **terminate normally** at thinking lengths comparable to successes. There is no
  runaway to catch; the difference only becomes observable when `done` arrives.

**Therefore: do not predict, detect and retry.** An empty `content` with `done_reason: "stop"`
is a normal outcome to be caught at completion, which is what the recognizer port already
specifies. The lever is cost per attempt, not foresight.

**Blanks are per-attempt, not per-page.** Three separate pages have now flipped between blank
and success on identical input and settings: ch16 p9 (blank ×3, then a >10-minute stall, then
a clean 89s success), and nt p40 (success at 182s in the corpus batch, blank on re-run). Never
record a page as "hard" on the strength of a blank.

### 3.2.1 Silent truncation — worse than the blank, and no gate sees it

Attempting to recover a blank by splitting the page produced a failure far more dangerous
than the one it was meant to fix.

`Quick sheets.pdf` p42, bottom half. **Passes every gate** — ASCII-clean, balanced, compiles:

```latex
$6 \times 9 = 42$
which is true
$6 \times 9 = 42 \pmod{13}$
$4 \times 13 - \leq ?$
```

Then it stops. Absent: `52+2=54`, `6×9=54`, `6₁₃ × 9₁₃ = 42₁₃`, the `=₁₃` notation, the arrow
chain, the clock diagram. The top half stopped after "Why?" — 50 bytes for a third of a page.

**It kept the claim and deleted the justification.** A correct, carefully argued page became an
apparently false assertion that compiles cleanly. Worse, a symbolic checker (§5.5.3) would then
flag it as false — a correct flag, on wrong output, for entirely the wrong reason.

Blank and truncation are the same failure — premature stop — but truncation is **invisible to
every gate we have**, because what it emits is well-formed. The blank at least announces itself.

**Nothing in M0 currently checks that the output covers the page.** The coverage gate (§5.4)
compares marks, not text extent. This is a real hole.

Ink distribution is computable exactly from the vector source and shows what should have been
covered — p42 has ink in all ten vertical bands, with 15% of it in the bottom 10% (the clock).
A completeness check is therefore possible in principle. One naive form was tried and failed:
an ink-points-per-output-character ratio does not separate (complete pages span 6.5–16.9, and
the one truncated sample lands inside that range once measured correctly).

**Researched 2026-08-18: there is no prior art. Nobody ships this.**

- Textract, Google Document AI and Azure Document Intelligence expose per-field confidence and
  route low-confidence work to humans — but confidence is computed **only on what was
  extracted**. None flag absence.
- OCR-D's quality spec requires ground-truth transcriptions to compute CER/WER — unusable
  per-page in production.
- Transkribus treats completeness as a human judgement made during correction.
- **GutenOCR** (arXiv 2601.14490) is the closest anyone has come: it designs bounding boxes so
  that *"missing text manifests as gaps in box coverage"* — and then explicitly delegates
  spotting those gaps to human reviewers. No algorithm.
- olmOCR's retry-with-temperature-escalation targets the **opposite** failure (repetition
  looping). Our case — well-formed, schema-valid, EOS emitted on time — sails straight through
  its gate.
- No published work predicts expected transcription length from image features.

So the ink-ratio idea failing is not us missing a known technique; **the field has no answer.**
Two directions worth building, neither borrowed:

1. **Ink-band diff.** Ask the fast VLM (4s/page) for the lowest ink band its transcription
   reaches, and diff against the band distribution computed independently from the vector
   source. This is GutenOCR's human-facing idea, automated using data we already have.
2. **Direct completeness question** as a second pass — *"does this transcription account for
   all handwritten content visible, and describe anything below the last transcribed line."*
   Trivially cheap at current latency.

The positional route also composes with D6: the inventory pass already returns marks with
placement, so a mark reported in the bottom band with no corresponding output is detectable by
machinery the design already specifies.

### 3.2.2 Split-and-recover — untestable, because blanks cannot be reproduced

The recovery hypothesis (halve the page, reduce what the model holds at once) could not be
tested: **both pages that blanked reliably an hour earlier succeeded on the whole-page control
run** — `qs42` in 19s having blanked at 216s, `top7` in 146s having blanked at 201s. The
experiment lost its control.

That is itself the finding, and it now rests on **five** flipped pages. Blank-recovery
strategies cannot be evaluated because a blank cannot be reliably produced. Retry is not merely
the pragmatic answer; it is the only one that can be validated.

### 3.3 Asking the model to predict its own failure — measured

Three probes tested. Two fail as failure predictors; the third is valuable for something else.

| Probe | Cost | Result |
|---|---|---|
| "How many distinct non-text marks?" | **100s** | **BLANK.** Counting requires careful enumeration, which drives long thinking and triggers the same failure it was meant to detect. Rejected. |
| "EASY or HARD?" | 17–37s | **Does not predict blanks.** All three blank-prone pages answered EASY. Tracks content complexity, not failure risk. |
| **"EASY or HARD, and if HARD why?"** | 20–85s | **Does not predict blanks either — but is useful.** See below. |

**The reasoned form is an attention router, not a gate.** On the Naive Math houses page it
answered HARD and named *"colored house icons, stick figures"* — **precisely the marks its own
transcription pass silently deletes**. On Topology p5 it flagged *"`{a,b3}` intended as
`{a,b}`"*, catching the curly-brace-versus-3 ambiguity in this hand. On the base-13 page it
correctly identified the circular arrow diagram as needing TikZ.

Two consequences:

1. **The information is available to the model; the transcription pass just does not preserve
   it.** This is independent confirmation of the two-pass coverage design (§5.4), reached from
   a different direction than the inventory experiment.
2. **It belongs in `handzoo review`, ordering what the human looks at first** — which the
   research (§7.1) identifies as standard practice. It must never be presented as a
   correctness signal, because it demonstrably is not one.

**Self-prediction of failure is rejected.** The model has no privileged access to whether it
is about to blank.

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
2. Every inline `Mark` in the inventory must be accounted for by a marker in the emitted
   output. **As implemented this is a count comparison, not positional matching** — any *n*
   markers discharge any *n* marks. Position is used only to tell the human where a missing
   mark probably belonged, never to decide the verdict. Positional matching remains open work;
   the count check already catches the measured failure (7 seen, 1 emitted).
3. A mark in the inventory with no marker in the output is a **hard fail**.
4. **Ink cross-check** — the only signal not sourced from a VLM at all. **As implemented it
   fires only when the inventory is empty**, distinguishing a genuinely blank page from a
   failed inventory pass. It does *not* yet catch a non-empty inventory that under-reports,
   which was Structural's original recommendation and is still open.

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

### 5.5.1 This failure has a name, and prompting will not fix it

Substitution is a named, measured phenomenon in the 2026 literature: **"over-correction"** —
a VLM silently improving what is on the page instead of transcribing it. Measured across 15
VLMs at **42–66% of outputs**, *worse in stronger models*, and prompting ("transcribe exactly
as written") reduced it by only **~4 points**
([arXiv 2604.22774](https://arxiv.org/html/2604.22774v1), PINK metric).

Our tally-marks→Roman-numerals case is a textbook instance: the model "corrected" a
pedagogical distinction into the notation it considered more plausible.

**Consequence: prompt engineering is not a candidate mitigation.** Retire it.

### 5.5.2 Candidate defences, ranked by evidence

Researched 2026-08-18. Two of the three mechanisms this design was leaning toward are weaker
than assumed.

| Defence | Verdict |
|---|---|
| **Deterministic symbolic check** on structured claims | **Downgraded — see §5.5.3.** Catches `\|\|\|\| < \|\|\|\|` for free, but a naive arithmetic evaluator produces false positives on correct pages that declare a non-standard context. Only safe on claims whose context is explicit. |
| **LLM-as-judge self-contradiction pass** over emitted output | Directly targets the contradictory-bullets case. General method well evidenced; domain-specific evidence thin. |
| **Round-trip render-and-compare** to the source crop | **Weaker than expected.** No precedent for comparing against the *original ink* without ground-truth LaTeX. Fatal case: tally `\|\|` and Roman `II` are near-identical as strokes, so it would likely miss precisely what we most need. Would catch dropped-glyph shape mismatches. |
| **Self-consistency**, N samples | **Structurally blind to our worst case.** Catches genuine variance — the `\mathcal{I}` / `\mathbf{I}` instability — but systematic biases reproduce identically every run, so tally→Roman passes. |
| **Logprob / entropy flagging** | Available: Ollama exposes logprobs since 0.12.11. Unvalidated as a transcription-error signal — instrument opportunistically, never gate on it. |
| **Independent second reader (Tesseract) + Jaccard** | Already measured and rejected — see §5.6. |

D6 converts silent *loss* into loud loss. It leaves silent *substitution* as silent as it was. The only mechanism identified is a math-reasoning model reading the emitted LaTeX (`qwen2-math`, M1+).

**Per binding condition 3, this qualification lives in DECISION.md D2 alongside the positioning claim, not only here — the louder claim must not ship before the hedge.** The CLI never prints an unqualified PASS; a passing page reports what was *not* checked.

### 5.5.3 The `6 × 9 = 42` counterexample — why naive symbolic checking is unsafe

Fixture: `Quick sheets.pdf` page 42 (42 pages; the answer is on page 42). The page asserts
`6 × 9 = 42` and then writes **"which is true"**. A naive arithmetic checker flags it.

The checker would be wrong. The page establishes its own context:

```
4 × 13 = 52
52 + 2 = 54
6 × 9  = 54
so   6₁₃ × 9₁₃ = 42₁₃      i.e.  42 in base 13 = 4·13 + 2 = 54 = 6 × 9
```

The claim is **correct**, under a base the page declares three lines earlier.

**This is a false positive on the defence ranked highest in §5.5.2**, and it is the more
dangerous failure direction: a validator that cries wrong on correct work destroys trust far
faster than one that misses an error. It also mirrors the recognizer's own failure — the model
"corrects" tallies into Roman numerals by assuming a context the page did not intend, and a
naive checker would "correct" base 13 into base 10 the same way. **Both errors are the same
mistake: supplying a context the author did not state.**

Constraint that follows: a symbolic check may only fire on claims whose **context is explicit
in the extracted structure**. `|||| < ||||` qualifies — it is self-contradictory in any
ordering. `6 × 9 = 42` does not, because the base is carried in a subscript three lines up.

#### Formal verification (Lean / Agda) — deferred, but this is the principled answer

Raised as a long-standing wish. Its relevance here is specific: a proof assistant **cannot
express a claim without its context**. `6 * 9 = 42` does not typecheck until you say *in what
structure*, which is exactly the discipline a naive evaluator lacks.

Honest scoping:

- **As an M0 transcription gate: no.** Most pages are prose and sketches, not formalizable
  propositions. (`lean`, `lake` and `agda` were installed 2026-08-18; Lean still needs
  `elan default stable`. A worked example of the page-42 chain is in the session scratchpad.) Translating handwritten notes to a
  proof assistant is a strictly harder problem than transcribing them.
- **As a narrow near-term play: plausible.** Extract only claims that are decidable *and*
  context-complete — this page's own chain (`4×13=52`, `52+2=54`, `6×9=54`) is entirely
  decidable by `norm_num`, or frankly by Python.
- **As a feature of the book rather than the tool: strong.** "Naive Math" is a
  first-principles text; machine-checked claims would differentiate the manuscript. That is a
  goal for the author, not a gate in the pipeline.

Recorded so the design does not preclude it: the structured-claim extraction that a symbolic
check needs is the same extraction a proof assistant would consume.

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

## 5.7 Absence of evidence is not evidence of absence

**A check that did not run must never read as a check that passed.**

This is stated as a standing principle rather than a note on one bug, because the codebase has
now rediscovered it three separate times — each in a different component, each time by
defaulting an unknown into the reassuring answer:

| Where | The temptation | What it would have caused |
|---|---|---|
| `GateResult.checked` (§5) | a gate that could not run returns `passed` | a missing `pdflatex` turns the suite green while verifying nothing |
| `coverage_gate` (§5.4) | an empty inventory means "no marks on the page" | a page whose inventory pass failed reports full coverage |
| `Emission.verdict` (§6) | "not verified" folds into "failed" | **measured:** every fragment failed, so the verdict carried no information at all |

The third was found by running the CLI, not by review. Fragments have no preamble, so the
compile gate can never run on them; folding that into `fail` marked every page red, and folding
it into `pass` would have claimed a check that never happened. Neither is true, which is why
there are three verdicts.

**Test for it directly.** Each of the three has a test asserting the *unverified* case
specifically — not merely that failures fail. A suite that only checks pass and fail cannot
tell the difference between "verified good" and "never looked", which is the whole point.

**When adding a gate**, answer this before writing it: *what does this return when it cannot
run?* If the answer is "it always can", say why in a comment — that assumption is the one that
breaks first on someone else's machine.

## 6. Emitter

`--target latex`, `--standalone|--fragment`.

**`--target markdown` — reinstated as an M1 feature, not M0.** The panel cut it as scope creep
on the grounds that "the deliverable is LaTeX." That was true of the two documents visible at
the time and is false of the corpus. Three independent arguments accumulated since:

1. **Team of Teams contains no mathematics at all** — 17 pages of book notes. Forcing prose
   through a LaTeX compile gate is the wrong target for that document entirely.
2. **The author's downstream workflow is Markdown** (Typora with embedded LaTeX blocks), not a
   `.tex` manuscript.
3. **`render_tikz.py` already exists** and consumes ` ```latex ` fenced blocks inside Markdown —
   the author-later diagram path (§6.0) is Markdown-shaped.

Deferred out of M0 to keep the first increment honest, but the design must not preclude it:
the Normalizer's delimiter policy already branches on target, and diagram disposition (§6.0)
is written target-agnostically for this reason.

Block marks emit as a referenced cropped PNG plus `% TODO: author diagram`. Inline marks emit as an inline marker that survives to the human. Never `tikz`.

**Confidence markers** (binding condition 4): low-confidence spans are wrapped so a reader can distinguish transcription from guess. The inventory pass is already being built for D6, so this is nearly free. An artifact that performs certainty it does not have is the same dishonesty the never-fabricate rule exists to prevent.

**Colour** (binding condition 5): colour-bearing ink is a `ColorSpan`. Silent loss of it is a hard fail under the coverage gate. Faithful reconstruction is deferred; *silent* discard is not permitted. On the fixtures, R/G/B houses written in red/green/blue carry the labelling.

### 6.0 Diagram disposition — three outcomes, not two

v1.0 treated every diagram identically: crop, reference, `% TODO: author diagram`. That framing
assumes the crop is a **placeholder awaiting replacement**. For a first-principles book it
often is not — the hand-drawn houses and stick figures in Naive Math *are* the content, and
redrawing them in `tikz` would make the book worse. A universal TODO is also actively harmful:
if most will never be actioned, the TODO list becomes noise, and noise trains the author to
stop reading TODOs — the same vigilance erosion the panel flagged about unqualified PASS.

| Disposition | Output | TODO? |
|---|---|---|
| **Keep as drawing** | vector crop + `\includegraphics` | **No.** This is the finished artifact. |
| **Author later** | vector crop + a `tikzcd` stub the human fills in | Yes |
| **Auto-convert** | — | **Never.** Unchanged. |

**Default is a project-level binary preference**, because it follows from what the document
*is*, not from what each diagram looks like: a pedagogical book keeps drawings, a formal paper
authors them. Per-diagram override belongs in `handzoo review` as a "use the drawing instead"
action — which for most users will never be touched, and that is the point.

**Inline marks are not diagrams and are not covered by this.** A stick figure used as a noun
inside a sentence (Naive Math), a `↰` annotation arrow (ch16), a `✓` under `∪` (Topology)
cannot be cropped out without destroying the sentence. They remain the coverage gate's
problem (§5.4), not the emitter's.

#### The crop must be vector

Measured 2026-08-18: reMarkable exports contain **no embedded rasters and no fonts** — the ink
is vector (550 paths on one page). The PNG we feed the recognizer is a lossy derivative *we*
generate.

This matters precisely because "keep as drawing" makes the crop final. A 150 DPI raster is
adequate for a model and poor for a printed figure; a vector crop is neither.

```
pdftocairo -pdf -f N -l N -x X -y Y -W W -H H -r 72  notes.pdf  fig.pdf
```

Verified end to end: a cropped region of the houses diagram retained 133 vector paths, is
14 KB, and drops straight into `\includegraphics` and compiles.

Consequences: the rasterizer produces *two* derivatives from one source — a raster for
recognition (DPI is ours to choose, and can be raised for hard pages) and a vector crop for
emission. Diagram crops should never be resampled from the recognition raster.

#### Downstream, for the Markdown target

Where a diagram is *authored later*, the Markdown target should emit a `​```latex` fenced block
containing a `tikzcd` stub rather than a bare comment, because that is the exact shape the
author's existing `render_tikz.py` consumes: it compiles such blocks via `latex` → `dvisvgm`
to a content-hashed SVG and appends an image link, idempotently. The TODO then becomes
actionable in place — author the diagram, re-run, get a rendered figure.

Verified against the brief's own `len` commutative square: 13 KB SVG, 18 paths, first run.
Note this is a **DVI-route** dependency (`latex`, `dvisvgm`, `tikz-cd`), distinct from the
`pdflatex` binary the compile gate uses.

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

### 7.1 What the research changes about this

**The interaction shape is settled practice.** Transkribus, eScriptorium and OCR-D all converge
on **line-by-line stacked correction**: the segmented line image directly above its editable
text, Enter to accept and advance. Confidence-routing — showing only what the model is unsure
about rather than everything uniformly — is standard (Textract A2I, Prodigy). No controlled
evidence was found comparing side-by-side against in-image overlay editing; every serious tool
uses crop-adjacent-to-text, which is convergent design rather than measured proof.

**`keep` must not be one verdict.** Automation bias is measured, not theoretical: agreement
with incorrect AI output is the most consistent finding across a 35-study review, and human
inspectors miss 20–30% of defects under repetitive review load. A page kept *without being
read* and a page kept *after inspection* are different facts about the corpus, and collapsing
them into "verified" manufactures exactly the false confidence D2's positioning is supposed to
prevent. **The log must record `keep-reviewed` and `keep-unreviewed`/`skip` distinctly.**

**Log the wrong output, not just the right one.** The convergent regret across OCR fine-tuning
pipelines is teams that stored only the corrected text: `(image, wrong, correct)` triples are
what make evaluation and regression tracking possible later. §7's schema already does this.

**The exit criterion has literature behind it.** Machine-translation post-editing research
finds correction runs 14–65% faster than working from scratch — **but the advantage shrinks
and can invert when baseline quality is poor**. No study measures the abandonment threshold
for a transcription tool directly. This is precisely §11's open question, and it means the
`keep`/`edit`/`flag` timestamps in `corrections.jsonl` are the fastest route to a real answer
for this corpus — instrument them from the first run.

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
| **Quick sheets** | **42** | black | **`6 x 9 = 42` in base 13** — a correct claim a naive arithmetic checker flags; shape-recognized circle; arrow chain wrapping mod 13 |
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
