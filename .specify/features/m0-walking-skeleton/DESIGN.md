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

### 5.5.4 The inverted Cardan grille — a proposal for the inline-glyph problem

**Author's idea, 2026-08-19.** Research found *no prior art* for inline pictorial elements
functioning as grammatical constituents, so this is the first concrete mechanism anyone has
put forward for it here.

A Cardan grille is a mask with holes that reveals hidden text. **Inverted**, it masks the
glyph out and leaves everything else visible:

```
1. locate the inline glyph regions (the inventory pass already reports where they are)
2. white them out of the page raster
3. transcribe the masked image  -> clean prose with visible gaps
4. handle each masked region separately: vector-crop it, or classify it
5. reassemble, putting a marker back at each hole
```

**Why it should work where prompting does not.** Over-correction is a model smoothing over
what it cannot name — it reads "HAS MORE 🧍 THAN" and writes the sentence it expects. A hole
cannot be smoothed over the same way: there is nothing there to read, so the model has no
plausible reading to substitute. The failure mode changes from *silent* to *structural*, which
is the whole thesis of this project applied one level deeper.

It also composes with what already exists: step 1 is the inventory pass, step 4 is
`rasterize.crop_vector` (§6.0), and step 5 is the marker convention the coverage gate checks.

#### Tested 2026-08-19, n=3. Both risks cleared; the effect is real but not uniform.

Fixtures: three pages of `Cheng pp161-179`, chosen because the author describes his own
drawings there as sloppy.

**Localisation works.** Asked for bounding boxes, the model returns them on a **0–1000
normalised grid** regardless of being asked for percentages — detect the scale rather than
assume it, since a coordinate above 100 cannot be a percentage. On p9 the returned box
(x 231–483, y 318–439 px) matched the diagram closely enough to mask cleanly, with only two
small fragments surviving at the right edge. Pad the box: a clipped fragment is exactly the
thing the model will try to interpret.

**Masking does not degrade the surrounding text.** On all three pages the prose survived
intact. On p9 it improved: `gfs = gft` became `g \circ f_s = g \circ f_t`, the composition
made explicit.

**The target effect, on p9.** Unmasked, the model *fabricated mathematical structure* — it
emitted an `array` with two `\downarrow` arrows and a `1 \longrightarrow 1` row that are not
on the page, and it emitted **no marker at all**. Masked, the fabrication is simply gone and
the prose either side is preserved.

That is the hypothesis holding: the model did not smooth over the hole, because a hole offers
no plausible reading to substitute. It is worth being precise about what happened — the
content was not *marked*, it was *omitted*. Which is the intended outcome, because **we hold
the coordinates.** Step 5 of the grille is ours to perform: reinstate a marker and a vector
crop at a location we already know. The model is not asked to be honest about the gap; it is
prevented from filling it.

**Not uniform.** On p5 and p14 masking changed nothing measurable — no fabrication either way.
So the grille addresses pages that provoke fabrication, which is a subset.

#### Extended to the full document (2026-08-19). 65% fewer fabricated constructs.

All 19 pages of `Cheng pp161-179`; **16 had a drawing detected.** Fabrication is proxied by
LaTeX constructs that render 2-D structure (`array`, `tikzpicture`, `xrightarrow`, `\downarrow`
and kin) — a page transcribed honestly should not need them, a page where the model invented a
diagram will. Prose retention is the fraction of unmasked content words still present after
masking, and it doubles as a mask-quality check.

Of the 13 runs where the mask left the page readable (retention ≥ 0.5):
**8 reduced, 4 unchanged, 1 worse — 49 fabricated constructs became 17, a 65% reduction.**

The interesting rows:

| page | boxes | fabrication | prose kept | |
|---|---|---|---|---|
| 4 | 3 | 10 → 0 | 0.86 | the best case |
| 9 | 1 | 8 → 0 | 0.93 | clean elimination |
| 2 | 6 | 6 → 0 | 0.78 | |
| 16 | 3 | 4 → 0 | 0.74 | |
| 1 | 3 | 6 → **9** | 0.57 | worse |
| 13 | 3 | 3 → **20** | 0.48 | void, and dramatic |
| 10 | 1 | 4 → 0 | 0.00 | void — a blank, not a clean page |

**The failures are the useful part, and page 13 states the mechanism outright.** Masking
destroyed half its text, and the model responded by inventing **twenty** structural constructs
where there had been three. Page 10's mask blanked nearly everything, so its "4 → 0" is a blank
transcription rather than a clean one. Page 1, the only usable run that got worse, had the
lowest retention of any usable run.

So: **a mask that eats text forces the model to reconstruct, and reconstruction is what invites
fabrication.** Masking too much is not a milder version of masking correctly; it is the original
failure with a larger hole to fill.

**Prose retention is therefore the guard**, and it needs no ground truth — compare content words
before and after. On the full set it is *conservative rather than perfect*: three of the four
runs below 0.7 failed to help, and the fourth (page 19) gave a mild 2 → 1 win the guard would
discard. Trading one mild win to prevent one 3 → 20 is the right side of that bargain, but the
threshold is a safety rail on n=16, not a tuned parameter.

#### Prior art, researched 2026-08-19

**We are not reinventing a named technique, but the structure is not new.** MinerU
([arXiv 2409.18839](https://arxiv.org/abs/2409.18839)) masks inline-formula regions using
detector coordinates, OCRs the masked page, and reinserts the formula — structurally identical
to this. It is framed as a reading-order convenience and **never evaluated for hallucination
suppression**, so the measurement above appears to be the new part rather than the pipeline.

**Nothing in the steganography literature crosses over.** The Cardan grille is real (Cardano,
1550) and has a modern digital form ([arXiv 1803.09219](https://arxiv.org/abs/1803.09219)), but
entirely for information hiding. The *inversion* — mask the region to be excluded rather than
revealed — does not appear as a named concept anywhere, and no redaction literature discusses
whether a reader confabulates over a redacted region.

**Occlusion is established as a diagnostic, not a correction.** "Peek-a-Boo Reasoning"
([arXiv 2512.08976](https://arxiv.org/abs/2512.08976)) uses region masking to probe reasoning
faithfulness and finds models "hallucinate when evidence is missing" — which is our premise,
used to measure rather than to fix. Most inference-time anti-hallucination work (MaskCD, CMVED,
SPIN) masks *inside* the model — attention heads, value vectors — and still shows it the
diagram. We never do.

**The nearest theoretical support is from human perception.** Amodal-completion work finds
people fill in occluded regions but hold *measurably lower confidence* in the filled-in content
([PMC12786398](https://pmc.ncbi.nlm.nih.gov/articles/PMC12786398/)) — an explicit occluder is
recognised as an occluder rather than as absence. That is our mechanism, in humans, and nobody
has drawn the inference across to VLMs.

**The strongest counter-evidence, recorded rather than buried.**
[arXiv 2502.15389](https://arxiv.org/abs/2502.15389) tested masking-based visual prompting and
found it **not reliably effective**, with surrounding context mattering more than isolating the
target. It is the inverse setup — masking everything *except* a target — so not a direct
contradiction. But it predicts exactly what page 1 did, and it is the paper a reviewer will
raise.

**Not M0.** Recorded so the design does not preclude it, and because it is the only proposal
on the table for a problem with no published solution.

### 5.5.5 Post-run self-verification — the first thing that has caught substitution

**Author's idea, 2026-08-20:** after transcribing, show the model the page *and* its own output
and ask whether they match.

This is distinct from the round-trip render-and-compare rejected in §5.5.2, which compared a
*rendering* of the output against the source. Here the comparison is against the **original
ink**, with the transcription supplied as text.

#### Tested for discrimination, not agreement

A checker that answers "matches" to everything is worse than no checker, because it
manufactures confidence. So the cohort deliberately included a page the author confirmed
correct, **the same page with an error injected on purpose**, and a page known to drop content.

| case | verdict | what it said |
|---|---|---|
| Cheng p3, author-confirmed correct | `matches: true` | nothing flagged |
| Cheng p3 with `initial` → `terminal` swapped and three lines deleted | `matches: false` | *"The word 'terminal' in the transcription is incorrect; the handwritten n[ote]…"* |
| Naive p1, known to drop inline glyphs | `matches: false` | missing, invented and changed items all reported |

**Three for three.** It passed the good page, caught a single-word semantic swap it had never
been told about, and failed the page we already knew was wrong.

That is the first mechanism in this project to detect **substitution** — output that is
ASCII-clean, delimiter-balanced, compiles, preserves every mark, and is false. Every gate in
§5 is blind to it by construction.

#### The same trust boundary as everywhere else

The *verdicts* were right three times. The *details* were mixed: one "missing" item was
incoherent (it quoted the same string as both present and absent), and one "invented" item
correctly identified text that is not on the page — but it was **our own R9 fabrication
marker**, an annotation rather than a model error.

This is the third independent measurement of the same boundary. The inventory pass is
trustworthy about *where* marks are and not *what* they are (§5.4). The self-rating probe names
real problems but cannot predict failure (§3.3). And now the verifier is trustworthy about
*whether* output is wrong and much less so about *what* is wrong.

**So build on the verdict, not the explanation.** Route a human to the page; do not print the
model's account of the defect as though it were a finding.

#### Cost and standing

One extra call per page, comparable to the inventory pass. n=3, and the injected-error case is
the only true negative control — that wants extending before this becomes a gate. Recorded as
the strongest available lead on the problem the project has never had an answer for.
Script: `experiments/self_verification.py`.

### 5.5.8 Tier sweep: the defects are model-family, not model-size — measured 2026-08-21

The author's instinct was that a larger Google model would do better, and that Google's
efficiency lets one reach for it cheaply. The first half is not what the measurement shows, and
the second half turns out not to matter.

Page 1 of ch17 — the only page with established ground truth — through five Gemini tiers,
scored on both known local defects:

| model | invents `3, 10` | keeps `17` | words |
|---|---|---|---|
| gemini-3-flash-preview | no | yes | 141 |
| gemini-3.7-flash | no | yes | 146 |
| gemini-3.5-flash | no | yes | 141 |
| gemini-3.1-pro-preview | no | yes | 154 |
| gemini-2.5-pro | no | yes | 177 |

**Every tier gets both right, including the oldest and smallest.** So does claude-sonnet-5. Only
qwen3-vl:8b-instruct gets both wrong.

That is a stronger result than "bigger is better", and a different one: **the two defects are
not a capability threshold.** Nothing here needed a pro tier — a flash model from a prior
generation cleared both. What separates the outputs is the model *family*, not its size, which
means the local model's fabrication is a property of that model rather than of the page being
hard.

**Consequences.**

- For the disagreement detector (§5.5.6), a cheap tier is a perfectly good second opinion. The
  detector needs independence, not capability, and paying for pro buys neither.
- The word counts drift upward with tier (141 → 177), which is *not* obviously good. More words
  on a fixed page means more description, and description is the untrustworthy half of the §3
  boundary. `gemini-2.5-pro` produced 25% more text than `3.5-flash` for the same ink; whether
  that is more fidelity or more elaboration is unmeasured, and elaboration is what over-correction
  looks like before it becomes an error.
- **I picked badly and should say so.** The first cloud run used `gemini-3-flash-preview` —
  a preview of an older generation — chosen from memory rather than from the model list this
  project had already fetched. It happened not to matter. That it happened not to matter is
  luck, not method.

### 5.5.11 Literature on disagreement-as-detector — 2026-08-21

Commissioned research on §5.5.6. Sources attributed; inferences marked.

#### Every component has a literature; the assembly appears not to

**Classical multi-recognizer work uses disagreement to produce a better output, not to flag one
for review.** ROVER (Fiscus, IEEE ASRU 1997) aligns N transcriptions into a word transition
network and votes; multi-engine OCR voting and classifier combination (Xu, Krzyżak & Suen, *IEEE
Trans. SMC* 1992 — a **handwriting** paper) do the same. Disagreement is consumed internally and
a single hypothesis comes out.

The flag-and-route framing lives in three *other* literatures: selective prediction with a
reject option (Chow 1970; SelectiveNet, arXiv:1901.09192), disagreement-based active learning
(Query-by-Committee, Seung et al. 1992 — *literally* this algorithm, with a human oracle, but
aimed at picking training data), and industrial **double data entry**, which is the closest
procedural match and reports 0.14% residual error against 0.29% for single entry.

So §5.5.6 is a bridge between known things rather than a new thing — and the bridge does not
appear to be written down for handwritten mathematics. **Multi-model disagreement to localise
errors in handwritten *math* recognition appears to be a genuine hole**; that literature's
error detection is grammar-based on a single system's output.

#### Nobody publishes the number we have

**No published precision-of-disagreement-flag figure exists for any transcription task.** Fusion
papers report WER/CER reduction; error-detection papers report token P/R against labelled error
corpora using *confidence scores* rather than cross-engine disagreement.

So the ch17 measurement — ~50% of flags real, 5.3 flags per page — is a quantity the field does
not report. **It should be stated as precision-at-coverage on a risk–coverage curve**, which is
the selective-prediction vocabulary and makes it comparable to a literature that currently has
no entry for handwritten maths.

#### The correction: this tempers §5.5.6, and it contradicts advice I gave

*Correlated Errors in Large Language Models* (Kim, Garg, Peng & Garg, ICML 2025,
arXiv:2506.07962), across 350+ models: **models agree 60% of the time when both err**, and the
paper states this degrades disagreement as a reliability signal. *Great Models Think Alike*
(Goel et al., ICML 2025 spotlight, arXiv:2502.04313) proposes **CAPA**, a chance-corrected
similarity defined on *overlap in mistakes*, and finds **mistakes become more similar as
capability increases**.

That last finding cuts against what I recommended when asked whether to add Sonnet or Haiku. I
argued for Sonnet on the grounds that a weaker model contributes its own errors as false flags.
That is still true — and the opposite pressure is now documented: **a more capable third model
is more likely to share the other strong models' mistakes**, which is exactly the failure a
third opinion is meant to prevent. The choice is a genuine trade rather than the one-sided call
I made, and it should be measured rather than argued.

**The metric for it is twenty years old.** Kuncheva & Whitaker (*Machine Learning* 51(2), 2003)
survey ensemble diversity measures; the one that matters here is the **double-fault measure** —
the proportion of items *both* classifiers get wrong. That is precisely "agree and both wrong",
and a two-model detector should report it beside its precision.

**My inference, not a source's:** the 42–66% over-correction persisting across 15 VLMs
(§5.5) *is* a double-fault floor on the substitution mode. It bounds what any disagreement
detector can catch, and it is consistent with both ICML papers rather than contradicted by them.

#### The representation problem is a known wall, hit at a known place

Comparing raw LaTeX failed and comparing extracted words worked (§5.5.6). Three communities
found this independently:

- **CDM** (arXiv:2409.03643) renders predicted and ground-truth LaTeX to *images* and matches
  characters spatially, explicitly because string metrics score formatting as content error.
- **Label graphs / symLG** (Zanibbi, Mouchère & Viard-Gaudin, DRR 2013; the CROHME series)
  convert LaTeX to a graph before scoring. The general framing — **presentation markup versus
  content markup** — is the nearest thing to a standard name.
- **Semantic entropy** (Farquhar, Kossen, Kuhn & Gal, *Nature* 630, 2024) clusters generations
  by entailment *before* measuring, on the same argument in a different domain.

Our fix sits between the word level and CDM's image level. Worth citing all three: it turns "we
tried something and it failed" into "we hit a documented wall where others hit it."

#### The nearest published neighbour to this whole project

**TexOCR** (arXiv:2604.22880, April 2026) trains a 2B model with RL against **LaTeX unit tests
enforcing compilability and referential integrity**. That is this project's gate philosophy
inside a training loop. Single model, no disagreement, no normalisation discussion — so it does
not scoop the thesis, and anyone assessing novelty should be told it exists.

### 5.5.7 One call for both passes would break D6 — noted 2026-08-21

I suggested combining transcription and inventory into a single request to halve the round
trips against a cloud provider. **That was careless, and the author's question is why.**

Asked whether context flushes between the two calls, the answer is **yes, structurally, on all
three providers.** Each `_ask` builds a fresh single-turn body — one user message, no history,
no session — so the inventory pass has never seen the transcription and cannot be influenced by
it.

That is not an implementation detail. It is D6, and it was adjudicated empirically: *"a
self-report from the pass being audited has already decided to drop the glyph."* The independent
pass surfaced exactly what transcription discarded, which is the entire basis of the coverage
gate. Producing both in one call would give the inventory the transcription's decisions as
context — and an auditor that has read the thing it is auditing is not an auditor.

**So the saving is not free. It costs the only mechanism that catches dropped marks.**

**What can safely combine, and what cannot.** The test is whether one output is a *check* on the
other:

| combination | safe? | why |
|---|---|---|
| transcription + inventory | **no** | the inventory audits the transcription |
| transcription + boxes | **no** | boxes would inherit whatever the transcription decided was a mark |
| inventory + boxes | **yes** | both describe marks, neither checks the other — a box is a refinement of "where", which the inventory already reports |

So the efficiency available is one call fewer *if boxes are ever added*, not one call fewer now.
Transcription stays alone, and stays first.

**A separate question the same instinct raises**, and this one is open: the two-pass separation
was established by measuring qwen3-vl. Whether a cloud model needs it is untested — it may be
that a stronger model's single-call inventory stays honest, or it may be that a stronger model
is *better* at rationalising what it already wrote. The measurement is cheap (run both ways on a
page with a known dropped mark) and has not been done. Until it is, D6 holds for every provider,
because it was derived from a failure that has never been shown to be model-specific.

### 5.5.10 Literature on over-correction — what is known, 2026-08-21

Commissioned research. Findings attributed to their sources; inferences marked as such.

#### The measured null this project already relies on

*When VLMs "Fix" Students* (arXiv:2604.22774), the source of the 42–66% figure, states that explicit
anti-correction instructions "marginally reduced over-correction, [but] simultaneously degraded
overall transcription accuracy, resulting in no net improvement" — attributing it to
over-correction being "deeply entangled" with core reasoning rather than a surface behaviour.
§5.5.1's conclusion stands, now with a citation rather than only our own runs.

#### Scale: my hypothesis was half right, and the other half explains the tier sweep

I proposed that over-correction *worsens* with capability, because the mechanism is helpfulness
rather than incapacity. Then §5.5.8 found every Gemini tier avoiding both ch17 p1 defects,
which looked like a refutation. The paper resolves both at once:

- **Within a family, larger is worse.** The paper reports larger models "consistently exhibit
  higher over-correction rates, suggesting this behavior may be an emergent property of advanced
  reasoning capabilities."
- **Across families, capability does not predict faithfulness.** The 42–66% spread crosses
  vendors and is therefore confounded. Gemini 2.5 Flash was the *most faithful* model in the
  study, rising from 10th under BLEU to 1st under the faithfulness-penalising metric; GPT-4o
  moved the other way.

So the tier sweep is not a refutation — it is the cross-family effect, and both observations
hold. **Family, not size, is the variable that matters**, which is also what §5.5.6's detector
needs: independence comes from choosing a different lineage, not a bigger model.

#### The finding that lands on our own two-pass design

*Verification Mirage* (arXiv:2605.10850), on self-verification in medical VQA, reports
false-positive rates above 60% — verifiers systematically accept incorrect answers — and
**verifier error 57× higher when the generator fails**, describing the result as "a consistency
check over the model's own answer rather than an independent correctness check."

Cross-model verification helps **asymmetrically**: false-accepts drop 12–20%, discrimination
error only 2–5%.

**Bearing on D6, and it is uncomfortable.** Our two passes are structurally independent —
separate calls, no shared context (§5.5.7) — but they are *the same model*. Shared blind spots
produce agreement, and the coverage gate reads agreement as coverage. The existing hedge
("trustworthy about *where*, untrustworthy about *what*") is the right instinct and this is
evidence for it.

**The actionable form:** run the inventory pass on a **different family** from the
transcription pass. Three providers now exist, so this is configuration rather than
engineering. Expect fewer false accepts, *not* better reading — and measure it rather than
assume it, because the domain gap (medical VQA → handwritten maths) is real and untested.

#### Perturbation probing — the eval this project lacks

*Do VLMs Read or Rewrite?* (arXiv:2607.21617) supplies the method: **deliberately corrupt the
source** — scrambled characters, visually-similar swaps — and measure whether the model
reproduces the corruption or silently rewrites it into something plausible. General VLMs
degrade up to 4.5 WER points; OCR-specialised ones 0.2–2; traditional pipelines under 0.6.

This is what §11.2 has been missing. We have **one** page with established ground truth and no
way to tell whether a change helped. Perturbed fixtures are *synthetic*, which also resolves the
tension in constraint 7: they can be shared where the manuscript cannot.

Pair it with **olmOCR-Bench**'s key-phrase **absence** tests, so an addition is its own failure
class rather than a few insertions in an edit distance. Evidence that this matters and is not
merely tidy: in 2604.22774 the metric choice *inverts the leaderboard* — GPT-4o 3rd under BLEU
and 6th under the faithfulness metric, Gemini 2.5 Flash 10th and 1st.

#### Confirmed absent

No published work on fine-tuning specifically for refusal-to-embellish, and no sample-efficiency
numbers for such a narrow behavioural fine-tune. §5.5.9's caution stands, with one addition:
*Training large language models on narrow tasks can lead to broad misalignment* (Nature, 2025)
warns that narrow behavioural fine-tunes have effects outside their target — relevant if
"never add content" also suppresses inference we want.

Also ranked down: latent-representation probes (arXiv:2511.19806) need hidden states Ollama does
not expose; contrastive decoding has **no** transcription-faithfulness evidence, and
arXiv:2504.10020 argues its object-hallucination gains are metric artifacts.

#### The caveat that matters most

**None of this addresses baseline page 1.** Every technique above targets unsupported additions
and instability. Multi-view consensus will agree, five times over, that a stick figure is not
text. The dropped-glyph failure — where meaning inverts because an inline mark was treated as
decoration — remains unaddressed by the literature as well as by us.

### 5.5.6 Two providers disagreeing is a detector — measured 2026-08-21

The author's proposal was **selective escalation**: send parts of a document to a frontier
model, ad hoc from an intelligence pane or offered as the reviewer works, and never compulsory —
"sometimes a user doesn't need the boost and opts for the pre-AI way".

Framed as a capability upgrade that is a modest idea. Framed as a **second independent opinion**
it is the first substitution detector in this project that does not ask a model to audit itself.

**It needs neither model to be right. It needs them to be independent.** A passage only one
provider produced is a passage worth a human glance, whichever one is correct.

Measured on ch17 p1, the only page with established ground truth — the local run invented
`3, 10` and Gemini did not. `experiments/provider_disagreement.py` compares the two and
produces **four passages to glance at**, of which **two are real defects**:

| flag | what it is |
|---|---|
| `local='3, 10'` | **the fabrication.** Correct arithmetic, not on the page |
| `gemini='17'` | **a silent omission I had missed.** The page reads *17 DUALITY*; local emitted `\section{Duality}` and dropped the chapter number, so LaTeX numbered it 1 |
| diagram-marker ordering ×2 | noise |

Two real findings out of four flags, on a page every gate passed. The dropped `17` is a
constraint 5 violation that no gate saw and no human noticed, including me, across a chapter I
had already read and shipped.

**Compare text, not markup.** The naive form fails: diffing the emitted `.tex` surfaces
formatting (`\item` against `\\`, `\section` against `\section*`) and buries the finding. Words
first, and list markers dropped, takes the same page from seven flags to four.

**A router, not a verdict.** It says *look here*, never *this one is right*. Presenting a
frontier model's reading as authoritative would recreate the delegation hazard (§11.1.2) with
the additional problem that the reviewer paid for the second opinion and will believe it.

**It fits the privacy constraint rather than fighting it.** Escalating a *region* means only
the parts the author chooses leave the machine — local by default, cloud by exception, at
sub-page granularity. `crop_vector` already cuts regions, so the mechanism exists.

**Cost of the idea, stated:** two recognitions per escalated region, and a detector whose
precision on one page was 50%. n is one page. What it establishes is that the signal exists and
is cheap to extract, not how well it performs.

### 5.5.9 Fine-tuning — out of scope, and what it is downstream of (author, 2026-08-21)

Raised as out of scope and recorded because the reasoning is short and the conclusion is not
obvious: **the tier sweep makes fine-tuning look more attractive than it is, and the corpus
question makes it look further away than it seems.**

**The case is real.** Every cloud tier avoids both of ch17 p1's defects and the local model
avoids neither, so this is not a task-difficulty ceiling — the gap is closable in principle.
Closing it locally would preserve constraint 7, which is the constraint that actually matters.
LoRA on a VLM is cheap; compute is not the barrier.

**Data is the barrier, and the number is small.** Across every run so far the correction log
holds 74 rows — of which **42 are `skipped`**, 26 are `keep-reviewed`, and exactly **3** are
(image, human-written text) pairs. Fine-tuning is downstream of the review loop rather than
parallel to it, and the loop has barely run.

**`GOLD` is already the right filter, for a reason worth naming.** It was built to answer *does
this row carry evidence about correctness?* — and that is the same predicate as *is this row
usable as a training pair?* Both require that a human actually looked. `keep-unreviewed` and
`skipped` are worthless as evidence and worthless as labels, for the identical reason. The
honesty distinction and the data-quality distinction turn out to be one distinction.

**The obvious corpus is contaminated.** The author's from-blank transcriptions look like clean
ground truth and are not: §11.0 measured them containing a misread word that changes meaning in
category theory, two typos, and a malformed `\underline` that would not compile. Training on
those teaches the author's error rate as truth. This is the same finding as "the human arm is
not ground truth", arriving where it does more damage.

**The practical path is distillation, and the machinery exists.** Where two or more independent
providers *agree* on a passage, that is a high-confidence label costing no author time — and
§5.5.6's detector already computes exactly that, from the other direction. It was built to
surface disagreement for review; its complement is agreement, which is a label. Local at
inference, cloud at training time, constraint 7 intact where it counts.

**Target named failures, not general accuracy.** "Do not complete a list the author left open"
is a narrow behaviour and narrow behaviours need dozens of examples rather than thousands.
General transcription accuracy is where the corpus is too small; specific refusals may not be.

**The blocker is evaluation, not training.** There is exactly **one** page with established
ground truth in this entire project. Fine-tuning without a held-out eval set is not an
experiment, and building that set costs the same author-time as the exit criterion — which is
also still unrun. Both roads lead through the same hour of the author's attention.

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

### 5.7.1 The sibling failure: a rule written against the instance, not the class

Related but distinct, and worth naming because it produces the same outward symptom — a
defence that looks present and is not.

R9 strips fabricated diagram markup. It was written when the recognizer emitted
`\begin{tikzpicture}` (Naive Math p21), and it matched **that literal string**. Cheng ch18 is
category theory, so on commutative diagrams the recognizer reached for `\begin{tikzcd}`
instead — and R9, asked whether this was fabricated tikz, said no. Two pages failed the build
with *"Environment tikzcd undefined"*. The rule was real, tested, and blind.

**The fix is the family, not the name.** R9 now matches `tikzpicture|tikzcd|circuitikz|forest|
prooftree|CD|xy` — diagram-markup environments no standard preamble provides, so any of them
arriving from the recognizer is fabrication by definition. All three paths close together:
paired, orphan opener, orphan closer. Each fails the build on its own.

**Loading `tikz-cd` is the wrong fix**, and it will be proposed. It makes those pages compile
by rendering an invented commutative diagram — converting a caught fabrication into
well-typeset, ASCII-clean, compiling, *false* output. That is precisely the silent corruption
in the positioning statement, and hard constraint #4 forbids it. A fabricated diagram must stay
visible as a marker.

**Still open, and a §5.7 instance in its own right:** there is no general undefined-*environment*
detection anywhere. `find_undefined()` scans `\\([a-zA-Z]+)`, and an environment name only ever
appears as `{tikzcd}` — so the check that would have caught this structurally has never run, and
nothing in the output says so. The general fix is not a mirror of the macro path: stubbing an
unknown environment would silently swallow its body, violating constraint #5 worse than a
compile failure does. The marker treatment is only correct for R9 because tikz bodies *are*
fabricated markup. Prose is not. That decision is unmade.

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

**Colour** (binding condition 5): colour-bearing ink is a `ColorSpan`. Silent loss of it is a hard fail — enforced by `colour_gate` since 2026-08-20, and by nothing at all before that. Reconstruction remains deferred; silence does not. Faithful reconstruction is deferred; *silent* discard is not permitted. On the fixtures, R/G/B houses written in red/green/blue carry the labelling.

#### Measured 2026-08-20: colour is silently discarded, and binding condition 5 is unimplemented

`Cheng 217-220` is the first corpus written in more than one ink. Page 3 carries three:

| ink | paths | what it is |
|---|---|---|
| `rgb(192,127,210)` violet | 343 | the writing |
| `rgb(144,144,144)` grey | 17 | the base diagram's own arrows |
| `rgb(145,218,113)` green | 11 | the cone's legs, from the vertex V |

The green/grey distinction **is the lesson on that page.** A cone is a vertex with morphisms
into a diagram; the green arrows are the cone, the grey ones are the thing being coned over.
Rendered in one colour, the picture stops teaching the difference it exists to teach.

Twelve runs over these four pages emitted **no colour information of any kind** -- not a
`\textcolor`, not a word, not a note in the diagram marker. Page 3's marker reads *"morphisms
to multiple objects... arrows indicating commutativity"*, which is true of both arrow families
and distinguishes neither.

**Pages 1 and 3 passed every gate.** So the state of affairs is precisely the one the paragraph
above forbids: colour-bearing ink was silently discarded and the coverage gate reported clean.

The reason is structural, not a model failure. `ColorSpan` is defined in the port and **nothing
anywhere constructs one** -- a grep for it outside its own definition returns nothing, as does
one for `Recognition.inlines`. The inventory pass returns marks with kind, description, context
and placement, and no colour field, so the coverage gate has nothing to check colour against.
The condition was written, the type was declared, and no code path was ever wired between them.

This is DESIGN 5.7 again, in its purest form: **a check that does not exist reads exactly like
a check that passed.** Three prior instances defaulted an unknown into the reassuring answer;
this one never asked the question at all, and the design document asserts it as settled.

**Fixed 2026-08-20: `colour_gate`.** Colour is read from the vector source
(`rasterize.ink_colours`) and compared against what the document carries. The second of the
two directions below was taken.

- **Evidence is file ground truth, not a model report** — which is why it is a separate gate
  rather than an extension of `coverage_gate`, whose evidence is the recognizer's inventory.
  Mixing the two would blur what a coverage failure means.
- **The predicate is structural**: does the document contain a colour *command*
  (`\textcolor`, `\color`, `xcolor`...). Searching for colour *words* false-positives on prose
  about the four-colour theorem, and on Naive Math where "red house" is colour information
  genuinely carried in words.
- **Two inks is the threshold.** One ink carries no contrast, so rendering it black drops no
  distinction. The gate fires on ink that distinguishes, not ink that merely exists.
- **A raster source returns `None`, not "clean".** A scan has no vector paths to read, and
  neither does a blank page. Reporting no-colour-to-lose on a scan would be the fourth §5.7
  instance, introduced in the same change that documents the third — so `None` means *could
  not determine* and yields `checked=False`. Verified against a raster PDF rebuilt from the
  author's own scanner output.

**It is precise rather than noisy**, which is what makes hard-failing defensible: chapter 18
flags **0 of 14** pages, and `Cheng 217-220` flags **2 of 4** — exactly the two carrying
colour. The concern that a colour gate would paint every page red does not materialise,
because most pages are written in one ink.

**Still deferred: reconstruction.** The gate says colour was present and is gone. It does not
carry the colour into the output, and nothing does. Binding condition 5 always separated these
— "faithful reconstruction is deferred; *silent* discard is not permitted" — and it is the
silence that has been fixed.

**Superseded reasoning, kept because the choice was real.** Two directions were open: The recognizer could be
asked for colour spans, which adds a trust question -- colour naming is a *description*, and
descriptions are the untrustworthy half of the boundary in section 3. Or colour presence could
be measured from the vector source, which is cheap and reliable (the table above is one
`pdftocairo` call) and tells the gate *that* colour was there without asking the model to name
it -- which is exactly the shape of the coverage gate's existing presence-and-position rule.

The second was the better fit and is what shipped.

**A crop preserves colour by construction.** Where a coloured mark is a block diagram, the crop
verdict (7.2) resolves this for free -- the crop is the image, so the green and grey survive
without anyone having to name them. That covers page 3's cone. It does not cover colour used
inline in prose, which is the Naive Math R/G/B case.

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

### 7.1.1 Silent *addition* is a failure class nobody named

Constraint 4 forbids fabricating a diagram. Constraint 5 forbids silently dropping, rewording
or renotating a mark. **Nothing forbids silently adding text**, because until ch17 p1 it had
not occurred to anyone that the model would.

It did: the page lists three divisor pairs of 30 and an ellipsis, and the emitted document
carries four. `3, 10` is correct arithmetic and is not on the page.

**The author's reaction is the important part of this finding.** Asked whether the addition was
an error, they said no — they had followed the book, and had been *annoyed* that the book
stopped at three. The insertion was welcome.

That is exactly why the class is dangerous, and the argument is the automation-bias one from §7
turned around. A fabrication the author welcomes is a fabrication the author stops checking
for. The tool did not judge the completion safe; it had no idea it was completing anything. The
mechanism that supplies a helpful divisor pair supplies a reversed morphism with equal
confidence, and by then the reviewer has learned that additions are usually improvements.

**So the answer is not suppression, it is disclosure** — the rule the rest of the project
already runs on, applied to a direction it never faced. An addition is permitted and is marked:

```
\item $3, 10$  % handzoo: not on the page
```

Symmetric with `[[FABRICATED:]]` for diagrams. The author keeps the completion they wanted, and
keeps the ability to see that it was one.

**Detection is the same unsolved problem as substitution**, and the same mechanism addresses
it: self-verification (§5.5.5) compares emitted text against the image, and an added line is
precisely what that comparison sees. This adds a requirement to it — when it fires, the verdict
is *mark*, not *delete*.

### 7.2 The crop verdict — **built 2026-08-20**

**Status: built (2026-08-20).** The blocker below was cleared first, as this note said it had
to be.

`c` on a finding offers **candidate regions derived from the ink itself** — paths grouped into
horizontal bands separated by whitespace — or four numbers. The region is cut with
`crop_vector`, shown to the human, and only on acceptance does it replace the marker. A
rejected crop changes nothing: the marker is the only evidence a diagram was there.

Two things had to be fixed to make the cut usable:

- **Coordinates are the full affine transform, not the scale factor.** Using only the scale put
  page 3's blocks at y=694..1307 on a 685pt page. Every proposal would have cropped the wrong
  region, silently.
- **`pdftocairo -pdf -x -y -W -H` clips content and leaves the page box full size.** Asking for
  240x190pt of a 514x685 page produced a 514x685 PDF with the diagram in one corner, so
  `\includegraphics` imported a mostly-blank page. `crop_vector` now tightens with `pdfcrop`,
  best-effort, since it ships with the same distribution as the hardcoded `pdflatex`.

`cropped` is a distinct verdict in `GOLD` rather than folded into `edited`: the exit criterion
is a timing question, and with diagrams at 45 of 49 findings, seconds-per-crop is most of the
answer rather than a footnote in it.

**It required the first provenance field.** `PageOutcome.source` records the PDF a page came
from — without it there is nothing to cut from. An absolute path is acceptable there because
the manifest is the run's local record; §8.1's hash-and-sidecar rule governs the emitted
`.tex`, which is the file that travels.

Verified end to end on `Cheng 217-220` p3: the cone diagram cropped to 8,463 bytes of vector,
the marker replaced, the *second* marker on the page correctly left alone, and the document
compiles with the figure in place.

#### Why a verdict, and not a better viewer

The CLI loop is quick, and inherently limited when the finding is a diagram: a terminal can
say *this drawing is wrong*, and cannot show it. The available verdicts collapse to keep, flag
and skip -- `edit` is useless when the correct fix is "draw this."

That would be a minor gap if diagrams were one category among several. They are not. Across the
author's full 44-page ch18 run:

| finding kind | count |
|---|---|
| fabricated diagram | **45** |
| compile | 4 |
| missing mark | 0 |
| non-ASCII | 0 |

**45 of 49 findings, on 8 pages.** Diagrams are not a category of the review burden, they are
substantially all of it. Any interface investment that is not aimed at them is aimed at 8% of
the work.

The author's own measurement points the same way: page 4 needed "just a snip of [Image]", and
"5 seconds for a human with a WYSIWYG UI." The missing capability is not better viewing. It is
a way to say *here is the picture*, in one keystroke.

#### What the verdict does

`c` -- the marker is replaced by a reference to a real cropped region, and the decision is
logged as a correction rather than an acceptance.

Two ways to obtain the region, and the difference matters:

1. **The human supplies it.** They snip the region in whatever tool they already use, and the
   loop wires the file in. No new trust in the model is required. This matches how the author
   already works and is the version to build first.
2. **`crop_vector()` extracts it** (§6.0). Already built and measured -- coordinates in points
   at `-r 72`, a cropped diagram retaining 133 vector paths at 14 KB. What is missing is not
   the extraction, it is knowing *where* to cut.

**Why the model cannot supply the region today.** `Mark` carries `context` -- the surrounding
words -- and no coordinates. The trust boundary in §3 says the recognizer is reliable about
*where* marks are, but that was measured on presence and ordering, not on pixel geometry, and
no bounding box the model returns has ever been checked against ground truth. Cutting a region
from an untested coordinate would fabricate a figure: well-formed, compiling, and showing the
wrong part of the page. That is substitution in image form, and no gate here would catch it.

#### The blocker: R9 destroyed a real crop and blamed the recognizer — **cleared 2026-08-20**

`_FAB_GRAPHIC.sub()` rewrites **every** `\includegraphics` into a fabrication marker. It never
checks whether the file exists, although its own message says "nonexistent file."

Demonstrated -- file written to disk, referenced, normalized:

```
input   \includegraphics{/tmp/.../fig-25-1.png}      <- the file exists
output  \texttt{[TODO fabricated: recognizer referenced a
        nonexistent file /tmp/.../fig-25-1.png]}
rules   R9 invented \includegraphics -> fabrication marker
```

Two failures in one line. The human's work is discarded, which is the silent-loss violation
(constraint 5). And the record **attributes it to the recognizer** -- a false provenance claim
about who produced what, in the file that is supposed to be the honest record.

**Direction:** a reference that resolves is not a fabrication. R9 must test existence, relative
to the output directory. That is a signature change, not a one-liner: `normalize()` takes
markup and nothing else today, so it has no idea where the run is writing.

**Cleared.** `normalize(..., base_dir=)` resolves a reference against the run's output
directory, and one that resolves is left alone. `emit()` and the pipeline pass it through, so
the capability is live rather than declared.

Three things had to move together, and only the first was foreseen:

1. **R9 checks existence.** No `base_dir` means the question cannot be asked, so the answer
   stays "fabricated" -- the safe direction, since defaulting the other way would let any
   invented filename through unexamined (DESIGN 5.7). The crop verdict must therefore always
   pass it. Resolution is confined to the output directory: `../../etc/passwd` exists, and that
   is not evidence the recognizer meant it.
2. **The preamble loads `graphicx`.** Letting the reference through is half the job. Nothing
   declared `\includegraphics`, so R8 stubbed it as an unknown macro and the optional
   `[width=...]` failed with *"Missing number, treated as zero."* Trading "the crop is
   destroyed" for "the document does not build" is not a fix. Unlike hyperref (see 8.1)
   graphicx redefines nothing, so it is loaded unconditionally.
3. **The compile gate can see assets.** It compiles in a scratch directory, which makes every
   relative reference unresolvable -- the gate would have rejected the crop verdict's own
   correct output. `base_dir` now joins `TEXINPUTS`. It is added to the *search path*, not the
   working directory, so intermediates still land in scratch and no run litters `.aux` and
   `.log` into the author's output.

Verified end to end on a real vector crop cut from `Cheng 217-220` p3 with `crop_vector` --
8,009 bytes, survives normalization, and the document compiles with the figure in place, while
an invented reference is still caught. A live pipeline run over that page produces a body
byte-identical to the pre-change output; the only difference anywhere is the added `graphicx`
line.

**Caveat, noted and unsolved:** LaTeX resolves a relative graphics path in a *fragment* against
the master document's directory, not the fragment's. "Exists under the output directory" is
exact for standalone output and a heuristic for fragments.

#### What the log records

A crop is a **correction**, not an acceptance: the human produced the artefact the tool refused
to invent. It belongs in `GOLD`.

It should be its own verdict rather than folded into `edited`. Two reasons. The exit criterion
is a timing question, and if 92% of findings are diagrams then *seconds per crop* is close to
the whole answer -- it deserves to be countable on its own. And a corpus row that says "the fix
was a diagram extraction" carries information that "the text changed" does not.

The cropped file path goes in `after`, which already means "what the human made it."

#### Measured 2026-08-21: the model does return boxes, and they are confidently wrong

`experiments/diagram_boxes.py`. Asked for diagram bounding boxes on a 0-1000 grid, three runs
over `Cheng 217-220` p3:

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| cone | x71 y82 w195 h137 | x71 y75 w200 h143 | x71 y75 w195 h143 |
| pullback | x190 y595 w77 h41 | x190 y582 w66 h47 | x205 y595 w77 h47 |

**Remarkably stable — and stability is not accuracy.** Cutting the boxes and looking at them:

- The **pullback box is wrong.** It contains the word *"shape"* from the line above and an edge
  of the box beneath. Three runs agreed on it. Auto-cropping would have shipped a figure of a
  word, captioned as a pullback diagram, and nothing downstream could tell.
- The **cone box clips its own caption**, cutting *"More complicated one"* in half.

Three identical answers read as a confident one. That is the same shape as §5.5's substitution:
the failure is not noise, it is consistent, and consistency is what makes it convincing.

**Geometry and the model each know half.** The ink bands (`page_blocks`) got the cone's extent
right — a clean crop, verified by eye — and could not separate the pullback square from the
prose paragraph it sits under. The model found *both* diagrams and knew the pullback was one,
which geometry cannot, and placed neither accurately. Snapping a model box to the nearest ink
extent is the obvious synthesis and is **untested**.

This is also the answer to D4 for this feature: the geometric route needs a prose-vs-diagram
classifier, which is the heuristic segmenter D4 refuses; the model route is D4-compatible and
is not yet good enough alone. Neither is a reason to ship an unreviewed crop.

#### What must never happen — and why refusal is the wrong guard

The earlier form of this rule was *"no auto-crop without the human confirming the region"*,
argued from correctness. **The author's framing is better**, and it comes from using the
alternative: repairing a bad crop in a competitor's output means opening the ink PDF, finding
the page, cropping by hand, deciding where to save it for import, working out which
`\includegraphics` it belongs to, replacing it, and recompiling to check.

So the guard is not refusal. **It is cheap reversibility.** A wrong crop is tolerable when
re-cutting is one keystroke with the image already on screen; it is intolerable when fixing it
costs seven steps across three applications. That reframing points at auto-crop as the
destination rather than the hazard — proposed automatically, marked unreviewed, confirmed or
re-cut in the loop that already exists (`c` in §7).

It also fits machinery already built: an unconfirmed auto-crop is a `crop-unreviewed`, exactly
the distinction `keep-reviewed` against `keep-unreviewed` already draws. The document carries
it, it compiles, and the log says plainly that nobody looked.

Two rules survive unchanged:

- **No marker deleted without a file that resolves.** Removing the marker is the *only*
  evidence that a diagram was there; deleting it on a broken reference converts a visible gap
  into a silent one.
- **No crop presented as reviewed that was not.** The measurement above is why: the boxes are
  wrong often enough, and confidently enough, that "the tool cropped it" must never read as
  "someone checked it".

#### Open

- **Vector sources only.** `crop_vector()` reads paths. A scanned page has none (§8.1), so the
  scan workflow needs a raster path or an explicit refusal.
- **Inline marks cannot be cropped at all.** `Placement.inline` marks are *terms in the
  sentence* -- a stick figure used as a noun cannot be lifted out without destroying the
  sentence, which is what killed the brief's crop-and-reference policy as a complete answer.
  The crop verdict serves `block` marks. Inline ones remain the grille's problem.

### 7.3 Annotate the typeset PDF — the review loop the author actually uses

**Stated 2026-08-21:** the author reviews best by annotating the typeset output — on paper by
preference, and in practice by importing the PDF into the reMarkable and marking it by hand.

That is not the loop anything here was built for, and it is a better one, because it is the
loop they already perform. Every interface discussed so far (§7, §11.1.2) asks the author to
come to the tool. This one lets the tool go where the reviewing already happens.

**It closes on itself, which is the pleasing part.** The corrections arrive as *handwriting on
a page* — which is the problem this project already solves. The tool that reads your notes
reads your corrections in the same way, with the same recognizer and the same refusals.

#### Why it is tractable rather than speculative

Three mechanisms, all verified:

1. **Annotation ink separates from typeset text by kind, not by guesswork.** The typeset layer
   is text in fonts; the annotations are vector paths. `pdftotext` sees one, `pdftocairo -svg`
   sees the other, and both are already in the pipeline (§6.0, `ink_colours`). No classifier is
   needed, so D4 is not engaged.
2. **A point in the PDF resolves to a source line.** `pdflatex -synctex=1` plus
   `synctex edit -o "page:x:y:chapter.pdf"` returns the input file and line. Verified on the
   real assembled chapter: page 2 of the PDF resolves to `page-0006.tex` line 18, through the
   `\input` chain, correctly.
3. **The annotation's own position is its anchor.** An ink stroke has coordinates; (2) turns
   those into a source location. A margin mark beside a wrong word lands on the line holding
   that word.

So the loop is: emit `chapter.pdf` with synctex → author annotates on the device → export →
extract ink → resolve each mark to a source line → show the author their own annotation beside
the line it points at, and record a decision.

#### What is genuinely hard, and must not be hand-waved

- **Recognizing an instruction is not recognizing prose.** "delete this", a caret with a word
  above it, a circled term with an arrow — these are *edit operations*, and misreading one
  changes the document rather than merely describing it wrongly. This is substitution with a
  larger blast radius.
- **Applying an edit automatically is the delegation hazard** of §11.1.2, arriving by a
  different road. An annotation the author *made* carries even more borrowed authority than an
  agent's suggestion.
- **Therefore: resolve and present, do not apply.** The loop's output is *the author's mark,
  attached to the right line, with the source image nearby* — which is most of the value, and
  it does not require the tool to interpret a single instruction. Interpretation is a later
  question and a separate decision.

#### Why this outranks the four-pane application

The panes (§11.1.2) are a bet on where the author will want to work. This is a measurement of
where the author already works. It reuses the recognizer, the ink extraction and the assembly
that exist, adds one verified mechanism (synctex), and needs no UI at all in its first form.

**The exit criterion needed it immediately, so the timing half is built.** The author's
clarification was specifically about *this review*, which makes it a measurement problem rather
than a roadmap item: `--fix` timed them editing `.tex`, a mode they do not use. That would have
measured an artificial workflow and measured it *worse* than their real one, understating the
tool through an artefact of the harness.

- `--fix PAGE --mode pdf-annotate` typesets the page, hands it over, and times from *begin* to
  *done*. The elapsed figure includes getting the file onto the device and back, which is real
  cost in that workflow and is stated rather than hidden.
- `--seconds N` records a time the author measured, for modes the tool cannot watch — paper
  above all. Marked **self-reported**, because a number the tool took and a number the author
  took are different evidence.

**One trap caught before it produced a number.** `_typeset` assembled a one-page master, and
`assemble` cannot `\input` a standalone page — so on a `--standalone` run it produced a
document containing only the *"standalone, not assemblable"* placeholder. The author would have
been handed a page with none of their content on it and timed while annotating it: a
measurement of nothing, reported as a measurement. A standalone page needs no assembly, so it
is now compiled as it stands, and both run modes work.

Not scoped for M0 beyond that. The rest — reading annotations back — is recorded as the
strongest known candidate for what follows.

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

## 8.1 Provenance — deferred, and what it has to carry

**Status: designed, not built (2026-08-20).** Recorded now because the R9 fix proved the
cost of not having it, and because the scan-to-paper experiment will arrive before this does.

### Why it is not optional

`PageOutcome` records page, output, verdict, gates, error, rules and findings. It records
nothing about *what produced them* — no model, no code version, no source identity.

That collides directly with §9's testing policy: recognizer accuracy is "nondeterministic and
model-versioned: measure and record it, never assert it." A corpus that cannot say which model
and which normalizer produced it has recorded a measurement with the units left off.

This is not hypothetical. Widening R9 (§5.7.1) changed emitted output on pages already
transcribed, and re-ruling the inventory prompt on layout changed it again. Rows produced
before and after those changes are not comparable, and nothing in the manifest distinguishes
them.

### The organizing question

*Could this row be reproduced, and if not, exactly what changed?* Every field below earns its
place by answering that, or by surviving the deletion of something that will not last.

| Field | What it answers |
|---|---|
| source `sha256` | identity that outlives the source file itself |
| source basename, size, mtime | human recognition; mtime approximates scan time |
| PDF `CreationDate`, `Producer` | **the embedded time claim.** ch18 reports `PDFium`, `2026-08-20 09:09:29`; a scanner writes its own software and scan time here |
| page count, page size | 514x871 against 514x685 already mattered once (DECISION, zoom) |
| `vector` or `raster` | which downstream features exist at all — see below |
| model **digest** (`0533d74300e4`) | a tag can be re-pointed to different weights; a digest cannot |
| prompt hash | prompts have changed twice and changed output both times |
| handzoo git commit | the R9 lesson |
| dpi, `num_ctx`, `num_predict` | the knobs that alter output |
| attempts, latency | blanks are per-attempt, not per-page (§5.5) |
| *which* rules fired | only a count is kept today |
| `standalone` | so `handzoo-review` reads the mode back instead of asking the human |

**Destructible sources sharpen this.** A reMarkable export can be regenerated from the device.
A scan of paper cannot: the PDF is the only artifact that claims a time, and it is exactly the
artifact a paper-first author discards. The hash and the embedded `CreationDate` are what
survive it.

### Vector against raster is a capability split, not a detail

A reMarkable export is vector ink. A scan is raster. `crop_vector()` (§6.0), the stroke-width
measurement, and the ink-colour work all read path attributes that a scan does not have -- and
ink colour is *semantic* on the Naive Math fixtures. On a scan that becomes pixel
classification, which is a different problem.

So the source's nature is recorded, and features that cannot run on it must say so rather than
return empty. This is §5.7 applied to a capability instead of a check.

### Measured: a scan destabilizes recognition; vector does not

**2026-08-20.** The first scanned source ran end to end. It works -- ASCII, delimiters and
compile all pass, coverage fails on fabricated diagrams, exactly as a reMarkable page does. But
repeated runs on one unchanging page do not agree.

Same page, same model, same options, only the run repeated:

| source | runs | verdicts | findings | char spread |
|---|---|---|---|---|
| scan (letter, 300 dpi, JPEG) | 6 | **1 pass / 5 fail** | 0-10 | **44%** |
| reMarkable p4 (no diagrams) | 3 | 3 pass | 0 every time | 20% |
| reMarkable p23 (diagram page) | 3 | 3 fail | 2 every time | 8% |

The obvious confound is content: a page that provokes diagram fabrication might simply be
unstable. **It is ruled out.** The vector *diagram* page is the most stable of the three by
character spread, and its finding count is identical across all three runs. Diagram-provoking
content does not destabilize a vector page. The scan flips across the pass/fail boundary; six
vector runs produced zero verdict flips.

**Consequences.**

- A single run's verdict is trustworthy on vector input and is not on a scan. The ch18 corpus
  is one sample per page, and that remains a reasonable basis for vector sources.
- The exit criterion is a timing question asked of a specific emitted document. On a scan, "the
  document" is not a stable object, so a scanned page must be timed against the output the
  author actually reviewed, never against a re-run.
- A scan needs repeated runs before any measurement made from it means anything. Budget for it.

**Not explained.** Candidate causes -- JPEG artefacts on ink edges, paper texture against the
reMarkable's uniform ground, resampling of already-fixed pixels, variable ink density from a
physical pen -- are untested. n is one scanned page; this establishes that the difference
exists and its direction, not its magnitude or its mechanism.

**The pipeline halves a scan by default.** `--dpi 150` rasterizes a 2550x3300 scan to
1275x1650. For vector ink that is a rendering choice and costs nothing; for a scan it discards
half of what the scanner recorded. Whether it matters is unresolved -- the run-to-run variance
above is far larger than any difference 150 against 300 produced, so at three runs per arm the
question cannot be answered. Recorded as unmeasured.

**Formats are not the variable.** A scanner offering PDF, TIFF and JPEG offers three JPEGs: the
TIFF measured here is JPEG-compressed internally, and the PDF wraps a JPEG. There is no
lossless option among them, and the ~2/255 mean pixel difference between containers is far
below the noise floor established above. Also, `handzoo` reads only PDF -- poppler rejects JPEG
and TIFF outright -- so for a scan workflow PDF is a requirement, not a preference.

### Where it goes

**Manifest first.** It is where the evidence lives, and `handzoo-review` needs `standalone`
regardless. Note `load_outcomes` does `PageOutcome(**json.loads(line))`, so new fields need
defaults or existing manifests stop loading.

**A comment block in the `.tex` second.** `provenance()` already exists and is thin. Comments
are the only channel that works in **fragment** mode, which is the default output.

**PDF-level metadata is optional and standalone-only.** `hypersetup` cannot appear in a
fragment, hyperref redefines a great deal and conventionally loads last, and this project's
gate *is* "does it compile" -- a heavyweight package bought for metadata adds failure surface
unrelated to transcription. pdfTeX's `\pdfinfo{}` primitive does the same job with no package,
at the cost of being engine-specific if `tectonic` ever swaps in (§10).

### `fancyhdr` is refused, and the need behind it renamed

A fragment is `\input` into the author's document. `\pagestyle{fancy}` inside one mutates the
**parent** document's page style -- an unrequested side effect on a file that is not ours, the
same class of failure as R7's comment injection (§5.7). In standalone mode it is cosmetic.

The underlying need is real: reviewing a printed PDF against paper, the page should say which
source and which page it came from. That is a **review-print** feature, opt-in and
standalone-only. It is not provenance, and conflating the two is how the side effect gets in.

### Two traps, both verified

**1. Provenance can fail our own ASCII gate.** `emit()` prepends the provenance block before
the gates run, so a source filename containing non-ASCII fails the page:

```
% recognizer: ollama/qwen3-vl:8b-instruct - Notes - Ch1.pdf   <- en-dashes, U+2013
ascii gate: FAIL -- non-ASCII (U+2013, EN DASH)
```

An en-dash in a filename is ordinary output from scanning software. The failure would read as a
transcription defect. **Every field carrying external text is sanitized at the boundary**, and
that is tested directly rather than assumed.

**2. Absolute paths leak unpublished IP.** `fixtures/` is gitignored because the manuscript is
unpublished and this repo is intended for public release. Embedding a full source path in every
emitted `.tex` puts the author's directory structure and the manuscript's identity into files
built for sharing.

**Resolution: the artifact carries the hash; the mapping stays local and purgable.** A sidecar
file in the output directory maps hash to path for the author's own use. It is local, it is
never part of the emitted `.tex`, and deleting it costs only the human-readable name -- the hash
still identifies the source, and still verifies against it if the file is produced again.

### The rule this section inherits

A provenance field that records a **guess** as a fact is worse than an absent field. If the
model digest cannot be read, it records unknown -- never the tag, which would look verified and
would be wrong the moment the tag is re-pointed. Same principle as §5.7, applied to metadata.

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
| **Number theory** | **126** | **red + green** (catalogued monochrome; the colour gate found otherwise, 2026-08-21) | Long-run consistency; the only document large enough to test drift — **run 2026-08-21: no drift.** 126 pages, 0 recognition failures, failures do not rise with position |
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

### 11.0 What the criterion is actually asking (author, 2026-08-21)

The criterion as written compares correcting emitted `.tex` against transcribing the same page
from blank. The author's objection: **an author will review every page at some point anyway.**
Transcription is not an alternative to review, it is a way of doing it — you cannot type a page
out without reading it. So the two worlds are not "review" against "no review":

| | world without the tool | world with it |
|---|---|---|
| read the page against the ink | yes | yes |
| type | all of it | only the corrections |
| verify text you did not write | no | **yes** |

Reading is the constant. The criterion therefore reduces to

> **(verify + fix) against (type).**

That is a better question than the one originally written, and it is worse for the tool in one
specific way the original framing hides: **the tool adds a task that did not exist.**
Verification is not authorship. Reading text someone else produced, against a source, is a
different cognitive act from writing it, and it fails differently — a reader accepts plausible
wrong text that a writer would never have produced. That is the project's own villain,
relocated into the human.

**And the first ground truth cuts the other way.** On ch19 p1 the emitted document was *more
accurate than the author's own from-blank transcript*: three discrepancies against the page
image, all of them the author's — a misread word that changes meaning in category theory, a
typo, and an unmarked expansion of an abbreviation. Page 3 repeated it, including a malformed
`\underline` that would not have compiled. So verification is not simply the weaker act; on
these pages it had less to go wrong with.

Two things follow. The human arm is **not ground truth** — it is a second transcription with
its own error rate, and the criterion compares two fallible processes rather than one against
truth. And the tool's value is the typing avoided, less the verification added, which is a
smaller and more honest claim than "faster than transcribing".

### 11.0.1 Measured, 2026-08-25: correction is cheaper, and by how much depends on the baseline

**The author ran both arms.** First real numbers, from `~/handzoo-out/ch18` and `ch19`.

| arm | n | median | pages |
|---|---|---|---|
| `--fix` (whole page, seeded with our `.tex`) | 6 | **77.3s** (1.3 min) | ch18 9, 10, 11, 13, 14, 15 |
| `--transcribe` (whole page, from blank) — ch18 | 2 | **522.7s** (8.7 min) | ch18 2, 12 |
| `--transcribe` — ch19 | 3 | **196.0s** (3.3 min) | ch19 1, 2, 3 |

Both arms ran the same protocol (§11.1.1), so this is the criterion as designed rather than a
proxy for it.

**Report the baseline range, not the flattering end.** The control arm spans 2.7x between
chapters — 146.7s to 595.8s per page — and which chapter is used as the denominator moves the
answer more than anything the tool does:

| baseline | ratio | |
|---|---|---|
| ch18 median, 522.7s | **0.15x** | the generous reading |
| pooled median, 236.1s | **0.33x** | |
| ch19 median, 196.0s | **0.39x** | the conservative reading |

**The criterion passes on every one of them.** Correction is cheaper than transcription on the
median under every baseline in the table.

**The ratio is not a product metric, and must not be published as one** (author, 2026-08-25).
It is a go/no-go gate for *this* author on *this* corpus. Another writer types at a different
speed, so the denominator is a property of the person, not of the tool — and the numerator is
no better behaved. Correction time is driven by at least three things the tool does not
control: the author's **composition traits**, the **visual recognition** the page demands, and
a **stochastic resolution process** in which the reader reconstructs an ambiguous symbol from
comprehension of the surrounding argument. None of those transfer between people.

So the number answers *"does M0 have positive value for the author who built it"* — yes — and
nothing else. Quoting an improvement figure would be reporting the author's typing speed as a
product claim.

**It does not pass everywhere.** The slowest `--fix` (ch18 p10, 230.4s) exceeds the fastest
transcription (ch19 p1, 146.7s). That is worst-case against best-case across different
chapters, so it is not a counter-result -- but it is the "almost" in the author's own summary,
and it marks where the margin goes.

**What the number does not license.** The two arms do not measure time-to-the-same-target. A
transcript is ground truth by construction; a correction is *what the author judged correct
after reading our output*, and reading it anchors them. Substitution that reads plausibly
survives correction and would not survive transcription -- §5.5's hole, reappearing inside the
criterion that was supposed to clear the milestone. §11.0 already found the sharper version of
this: on ch19 p1 and p3 the human arm was the *less* accurate one.

**Recorded as unmeasurable from this data:** `mode` is empty on every row, so none of these
timings can be compared against a future `--mode pdf-annotate` or `--mode paper` run (§11.1).
The comparison the author most wants is the one this sample cannot support.

### 11.0.1a What the author actually corrected — the first real defect taxonomy

The `--fix` rows carry the full page **before and after**, so the six pages are a labelled set:
four the author changed, two confirmed correct unchanged. Every one of them had **passed all
five gates**. This is the first empirical answer to *what gets through*, as opposed to the
theoretical one.

| class | instances | example | caught by any gate? |
|---|---|---|---|
| **lost emphasis** | 3 | `Prop 18.2` and `Proof` were underlined on the page; both came back plain (p11). `as products` likewise (p14) | no |
| **semantic substitution** | 1 | `Sps` (the author's "Suppose") emitted as `\Rightarrow` (p13) | no |
| **dropped mark** | 2 | the *dashed* arrow in a diagram description (p10); a missing `\square` closing a proof (p13) | no |
| **notation degradation** | 1 | blackboard-bold R read as the two letters `IR` (p10) | no |
| **lost sentence boundary** | 1 | two sentences run together (p13) | no |

**Lost emphasis is the most frequent defect, and nothing looked for it.** Three of eight edits.
There is a colour gate because ink colour is semantic, and the author's underline is semantic in
a stronger way still — see §11.0.1b, where it turns out not to be emphasis at all.

**A correction to the first reading of this.** An earlier pass here claimed that *0 of 5*
numbered claim references in raw output carried any marking, counted by searching for
`\underline`. That was wrong: four of the five are marked with `\textbf`. The recognizer
usually *translates* the mark into a LaTeX-conventional one rather than dropping it. The
defect is real — p11 lost the mark entirely — but it is less frequent than first stated, and
the difference matters because a gate built on the wrong frequency would have been built to
the wrong precision.

**`Sps` → `\Rightarrow` is the first substitution caught in the wild.** Every previous instance
was injected by us to test a detector. This one is the real thing, and it is the bad kind: the
output is ASCII-clean, balanced, compiles, and **inverts the logic of a proof**. "Suppose there
exists a morphism k" is a hypothesis; "⇒ there exists a morphism k" is an inference. One opens
an argument, the other claims it. A reader who trusts the output reads a different proof.

Note what makes it dangerous rather than merely wrong: `Sps` is an abbreviation *this author*
uses, and the model resolved an unfamiliar token into a familiar one from context. That is the
"stochastic resolution process" named above, running in the machine instead of the human — and
it is precisely §5.5's over-correction, with a concrete instance at last.

**These belong in `baseline/` as fixtures.** The pages themselves are third-party published
content and stay out of the repo; the *defects* are ours to keep, as minimal synthetic cases.

### 11.0.1b The underline is a label, not emphasis — and the gate that follows

**The author's convention, in their own words:** Cheng numbers claims but not equations, and
the author underlines the thing she numbered. `Defn` is Definition; `Prop` is Proposition *or*
Property, resolved by context.

That reframes the defect. The underline is doing what `\label` does in a typed document — it
marks the referent that a later *"by Prop 18.2"* points at. Losing it does not make the page
plainer; it removes the anchor. This is the colour gate's argument exactly: ink carrying
meaning, so silent loss is a D6 violation.

**It also names a substitution surface the project had not considered.** `Prop` is genuinely
ambiguous, and the recognizer must choose. A wrong choice is invisible — well-typeset,
compiling, and wrong about which kind of claim is being stated. The author's abbreviations are
a *private lexicon*, and every private token is a place where the model resolves an unfamiliar
string into a familiar one. `Sps` → `\Rightarrow` (§11.0.1a) is that mechanism caught in the
act.

**The reference gate** (`handzoo/core/validate/reference_gate.py`) flags a numbered claim
reference carrying no marking at all. Measured against the labelled set:

| page | author's verdict | gate |
|---|---|---|
| 11 | changed — added `\underline{Prop 18.2}` | **flagged it** |
| 9, 15 | confirmed correct unchanged | silent |
| 10, 13, 14 | changed for other defect classes | silent |

**Zero false positives across all 44 pages of ch18.** One true positive, two true negatives,
and three misses that are out of scope by construction — `Proof` and `as products` are
underlined but not numbered, so the gate does not see them.

**It is advisory, not a hard fail.** The convention is *normally*, not always, and the author
asked for a review flag rather than a refusal. A red page over a defensible exception teaches
the reader to bypass the gate, which costs more than the defect does. `GateResult.advisory`
carries this: the finding stays visible, the page stays usable, and an advisory gate is still
never counted as a pass.

**What it does not do.** It catches one of the three measured emphasis losses. General emphasis
loss needs the underline strokes read out of the vector source — stronger, and unavailable on a
scan (§8.1). This is the cheap text-level case that actually occurred, not the complete answer.

### 11.0.1c The lexicon: tell the model the token exists, never what it means

The author supplied a sheet of their own shorthands. It holds **five different mechanisms**,
and `handzoo/core/lexicon.py` models exactly one:

| mechanism | example from the sheet | modelled |
|---|---|---|
| word abbreviation | `Sps`, `Defn`, `BWOI`, `BWOC`, `WLOG` | **yes** |
| positional rule | `g:` = "given", *only at the start of a line in proofs* | no — a flat list cannot say where |
| acknowledged ambiguity | `Prop` = Proposition **or** Property; `§` = section **or** integral | no — the model must choose; a lexicon cannot resolve it |
| mark, not token | the contradiction bolt, `→←`, the ampersand glyph | no — inventory and coverage path |
| rendering convention | "generally draw the letter 2x" for `\mathbb{A}` | no — about strokes, not strings |

Naming the four it does **not** cover is the point. Otherwise the file accretes entries and
everyone assumes something reads them.

**The trap is the obvious file format.** `Sps -> Suppose` is the natural way to write this and
the wrong thing to send. Handing the model the expansion licenses it to write *"Suppose"* where
the page says `Sps` — constraint #5b, *never silently add*, built into the seed rather than
arrived at by mistake. And 5b's own warning applies with force: an addition the author agrees
with is one they stop checking for.

So the halves are separated **at the type level, not by discipline**. `Lexicon.tokens` is
prompt-visible; `Lexicon.meanings` is for a human and a gate that does not exist. And
`prompt_fragment()` takes `tokens: tuple[str, ...]` rather than the `Lexicon`, so the prompt
builder has no path to the meanings even after a careless edit. `test_architecture.py` asserts
no module outside `lexicon.py` reads `.meanings` at all.

**Measured, on the page that produced the defect.** CI never calls a model (§9), so a prompt
change is unmeasurable by policy unless someone runs the experiment. ch18 p13 is the page where
`Sps` became `\Rightarrow`; four paired runs, changing only whether the tokens were in the
prompt:

| | `Sps` recovered | `\Rightarrow` | `Suppose` |
|---|---|---|---|
| without lexicon | **0 / 4** | 2 / 4 | 0 / 4 |
| with lexicon | **4 / 4** | 0 / 4 | **0 / 4** |

Two things in that table. The token is recovered every time — and in the two runs where it did
not become `\Rightarrow` it did not survive either, so the failure is not always the same
symbol. And `Suppose` never appears, which is the constraint holding: the model was told the
token exists, not what it means, and it did not expand.

**No lexicon gate, deliberately.** Tallied against the six labelled pages it would have caught
nothing. `Sps` → `\Rightarrow` is detectable only as an *absence*; expansions are unverifiable
because the author does sometimes write the word out; the `IR` misreads sit inside a TODO block
already flagged. The reference gate earned its place on 1 true positive and 0 false positives
across 44 pages. This has neither, and a gate built on six pages of evidence would be built to
the wrong precision — the mistake §11.0.1a already made once.

**The learned lexicon is blocked, not nearly-free.** The author's idea — infer the convention
from repeated corrections — needs *repeated* before→after pairs for the same token. Six pages
and eight edits contain **zero repeats**. And the author's own example, `def^{\underline{n}}`,
is a **typographic pattern rather than a string**, which a token-mapping learner cannot
represent at all. The correction log is the right place to mine and it does not yet hold enough
to mine.

### 11.0.1d The diagram-description path has weaker notation fidelity

Separate finding, from the same investigation. Blackboard-bold `\mathbb{R}` is emitted
correctly **29 times** across ch18. Every failure — `IR` four times, `IR2` three times — sits
inside a single `[TODO diagram: ...]` block on p10.

Inside a description the model is writing free prose about a picture, and none of the
transcription discipline applies: no math mode, no notation, no structure to preserve. The
author's rendering convention (a doubled stroke for blackboard bold) then reads as two letters.

**This predicts that everything inside a TODO description is lower quality than the page around
it**, which is a fact about how `handzoo-review` should present them — a description is a
prompt for the author to redraw, never text to be trusted. It is not a gate: the block is
already flagged, and the author is already going to replace it.

### 11.0.2 The reporter could never have run

Finding both arms in the log and no comparison printed exposed two defects. Neither was
visible from a green suite.

**1. The paired report is unreachable.** `_exit_criterion` compares the arms **on the same
page**. `--transcribe` refuses a page already reviewed; `--fix` refuses a page already
transcribed. Both guards are right and §11.1.1 argues for them. Together they guarantee that
**no page ever carries both arms**, so the paired report shows nothing -- on a log holding six
corrections and five transcriptions. It printed nothing, and nothing reads as *no data* rather
than *the comparison is unpaired*: §5.7 with the check on the outside.

The suite stayed green because the test built same-page rows straight through the log API,
which the CLI cannot do. **A test that exercises a path the product cannot reach asserts only
that the code runs.** That is the §5.7 lineage's newest member and the reason the principle is
stated as "test the case directly" rather than "have a test".

The fix reports the arms as **distributions** when they cannot be paired, with `n` beside each
median and the confound named: unpaired means page difficulty is uncontrolled. The paired
report is kept and still preferred when it can fire, and suppresses the weaker one.

**2. The correction arm was summing the wrong rows.** Everything not `transcribed` counted,
which swept in the finding-walk -- one keypress on one finding. On the real log that produced a
correcting median of **0.8s** against 522.7s: a spectacular result and a category error. Only
whole-page `--fix` rows are the correction arm, and a test now asserts that 40 finding-walk
rows cannot enter it.

### 11.1 The unmodelled variable: where the human's attention sits

Correction cost is not one number. It depends on what the author edits *against*, and nothing
in the design has ever said which:

| mode | what it needs | what it costs |
|---|---|---|
| the `.tex` against the ink | image and source side by side, LaTeX literacy | what `handzoo-review` does today |
| the rendered PDF against the ink | a compile per edit; the author's own proposal | slower loop, but the artefact is what ships |
| an intermediary, converted afterwards | a second format and a converter | cheap to edit, another translation to get wrong |
| prompting an agent | no manual editing at all | unmeasured, and a new substitution surface |

**The harness captured evidence for the third by accident.** Given a blank file and a page
image, with no instruction about format, the author wrote **markdown headings**, used exactly
one LaTeX command (`\underline`, six times, once misspelled), and left `% insert snip` where
each diagram belonged. Nobody asked for that shape. It is the strongest available signal about
how this author actually wants to work at the correction stage, and it says the target format
is not the working format.

The deferred-snip placeholder is worth noting separately: **the author independently invented
the crop verdict** (§7.2) as a note to self, before using it. A tool that emits a marker and
lets the human cut the region later is not an imposed workflow; it is the one already in use.

**Consequence for the criterion.** A timing taken in one mode does not transfer to another, so
the mode has to be recorded alongside the number, or the measurement means less than it looks.
It is currently not recorded at all.

### 11.1.1 `--fix`: the arms now share one protocol (2026-08-21)

The author's suggestion — *give me the tex we generated instead of a blank file* — corrected a
flaw in the measurement, not just its convenience.

The two arms were being compared through **different interactions**. `--transcribe` opened an
editor and timed it; correction was a walk through findings, one keypress at a time. That
measured the interaction as much as the content, and the finding-walk is not how anyone
actually corrects a page. `--fix PAGE` runs the identical protocol, differing only in what the
file starts with — which is the difference the criterion is about.

**It also exposed the symmetric contamination.** `--transcribe` refuses a page already
reviewed. `--fix` must refuse a page already *transcribed*: having typed a page out, the author
knows it by heart, and correcting it then measures memory rather than tooling — in the
direction that flatters the tool. So the two arms run on **different pages**. That costs the
pairing, which is a real loss since page difficulty varies, and is the honest trade. Several
pages per arm, not one.

**An unchanged fix is asked about, never assumed.** `--transcribe` spots an abandoned attempt
by its empty file; `--fix` cannot. A document that came back unchanged means either that the
output was already correct — the most valuable datum this project can collect — or that the
editor was opened and closed. Those are opposites. It asks, and records `keep-reviewed` for the
first and nothing at all for the second.

### 11.1.3 The source is not immutable — three bugs now, one architecture later

The author's *"Real world things"* and *"User experience"* notes (2026-08-25) both rest on one
premise the code does not hold: **the PDF keeps changing.** The author writes more, inserts a
page, reorders, edits a page already converted. Every action in that first note is an edit to a
sequence HandZoo has already recorded work against.

#### Verified now, and in M0

**1. Page number is not page identity.** `PageOutcome.page` and `Correction.page` are both the
ordinal `page.number`. Insert a page anywhere but the end and every later page shifts, so the
manifest, the correction log, the crops, and the `--transcribe`/`--fix` contamination guards
all silently point at different content than they were written about. Silent misattachment of
author work, which is the most expensive artefact in the system.

**2. A re-run overwrites author corrections.** `pipeline.convert` does
`target.write_text(emission.text)` with no guard. `--fix` writes the corrected page back to
that same path, so re-running without `--resume` destroys it. The text survives in the
correction log's `after` field and can be recovered by hand from JSON — and the *external*
text edits the author describes have no log row at all, so for those not even that applies.

**3. `--resume` protects a corrected page only by accident.** `completed_pages()` keys on
*"recognized without error"*, not *"carries author work"*. Nothing in the write path knows a
page has GOLD rows against it — which is also why there is no way to re-recognize page 5 while
keeping the correction on page 10. Those should be independent and are not.

#### The FSM question, answered in two halves

*Does a finite-state machine work here?* **For the page lifecycle, yes** — and it already
exists implicitly: recognized → gated → reviewed → corrected, with `verdict` and the correction
verdicts as its states. Making it explicit would be a tidying, not a discovery.

**For document mutation, no.** An FSM models a system with states and transitions; this is
edits to a *sequence*, and what it needs is stable identity plus reconciliation — *"which pages
in this new export do I already have work for?"* That is a diff problem wearing a state
machine's clothes.

#### Hashing answers four of the five actions, and the fifth is the dangerous one

| action | page content | recoverable by hash? |
|---|---|---|
| add page(s) at end | unchanged | yes |
| add page(s) anywhere | unchanged | yes |
| delete a page | unchanged | yes |
| reorder pages | unchanged | yes |
| **edit a page** | **changed** | **no** |

Four leave content untouched, so a per-page content hash recognizes work wherever it moved.
**"Edit a page" breaks it, and it is the action most likely to land on a page already
corrected** — the author adds a stroke to page 7 on the device after correcting page 7 here,
the hash changes, and the work is orphaned. That case needs a similarity threshold or explicit
author confirmation; it cannot be resolved by lookup. *That* is the answer to "which do we need
to model": the fifth one, in a way the other four do not require.

#### Naming: the author's instinct is right

From the UX note: *"I don't foresee a way to avoid a name upfront unless we map names to work
directories."*

Content-hashing the source **does not** rescue this, and it is worth saying so before someone
builds on it. The premise of the first note is that the PDF changes every time the author
writes more — so the source hash is different on every export, and matching a new export to an
existing project is the reconciliation problem again rather than a lookup. A name upfront is
probably correct. Filename as a *suggestion*, as the note proposes, is the right shape.

#### Page-level read-only protection is not a nicety

The UX note asks whether to offer it. It is the mechanism that protects the most expensive
artefact HandZoo produces — measured author time, 77.3s median per corrected page (§11.0.1),
against 4s of recognition. `GOLD` already identifies which pages carry that work. The
protection is therefore not a new concept, only a new consumer of one that exists.

**Out of M0 except for bug 2.** The identity rework is architecture; a guard on the write path
is not. The policy — refuse the page, write alongside as `.new.tex`, or ask — is the author's
call and is deliberately left open here.

### 11.1.3a Explicit replacement dissolves the hard case rather than solving it

The author's proposal (2026-08-25): *require the user to tell us to replace page 2 with a
result of a new PDF source, or with page 3 in a modified version of the original.*

This is better than it first sounds, and for a reason beyond convenience. §11.1.3 found that
hashing page content answers four of the five mutation actions and **cannot answer "edit a
page"** — the changed page has a new hash, and matching it to the work already done needs a
similarity threshold nobody can tune from six labelled pages.

**Explicit replacement removes that requirement entirely.** The human asserts the
correspondence, so HandZoo never has to infer it. The one action that resisted a mechanical
answer becomes the one action that does not need one.

It also fails better. An explicit instruction *can* be wrong — the author says page 2 and means
page 3 — but that is a visible error with an inspectable result, not the silent misattachment
of §11.1.3 bug 1, where work quietly attaches to different content and nothing anywhere says
so. Trading a silent failure for a loud one is the trade this project makes everywhere else.

**Implication for the identity work:** hashing is still worth having, for the four actions it
does answer and for detecting *that* a page changed. It stops being the mechanism that decides
*what a changed page corresponds to*. That is the author's call, by design.

### 11.1.3b A screen-share screenshot as a source — measured, not speculated

The author's second thought: the reMarkable screen-shares, so a surgical change could arrive as
a screenshot. Run through the pipeline unmodified (2000x1533, a page of category theory in
magenta on lined paper, inside the reMarkable app window):

| | result |
|---|---|
| app chrome transcribed? | **no** — and 42% of the frame was chrome |
| lexicon token `Consdr comp` | **survived verbatim** |
| top diagram | **dropped from the transcription with no marker** |
| lower diagrams | fabricated as `\includegraphics{diagram.png}` |
| independent inventory pass | **found both diagrams** |
| coverage gate | **FAIL** — page refused |
| colour gate | **NOT CHECKED** |

**The chrome result was not expected.** Two backgrounds are present — white for the page,
`(250,246,241)` cream for the app — along with the reMarkable wordmark, a "Screen Share" title
and a full toolbar in black, against a page whose ink is entirely magenta. None of it reached
the output. Auto-cropping to the white region is mechanically trivial if it ever does leak, but
it is not needed today.

**The safety net held, and held for the designed reason.** The transcription pass silently lost
the top diagram; the *independent* inventory pass found both. That separation (§3, `Recognition`)
exists precisely because a self-report from the pass being audited has already decided to drop
the mark. Coverage then refused the page over the fabricated `\includegraphics`.

**Colour is the real limitation, and it is honest about it.** This page is written entirely in
magenta and the gate reports **NOT CHECKED**, because colour is read from the vector source and
a screenshot has none (§8.1). That is the correct answer and it is not a satisfying one: if a
screenshot is ever the *only* source for a page, colour becomes unverifiable for it. Worth
saying plainly rather than reaching for pixel classification, which would be a different
mechanism with a different error profile.

**And a screenshot has no identity at all** — no vector, no device `CreationDate`, no page
ordinal, nothing to hash against a source. It is therefore *exactly* the case where §11.1.3a's
explicit replacement is not merely the cleanest option but the **only** one: there is nothing
to reconcile against. The author's two thoughts are one conclusion.

**Also observed:** `Consdr` (for "Consider") is not on the author's seed sheet. The lexicon will
always be incomplete, which is an argument for the correction-mined path (§11.0.1c) rather than
against the file.

### 11.1.4 `\sps` — the third lexicon mode, and the author solved 5b again

The clearest idea in either note, and the merged lexicon design (§11.0.1c) does not model it:

> *definitions (e.g. my "Sps", define `\sps` to streamline replacement and styling in one go)*

§11.0.1c has two modes — emit the token **literally**, or expand it (**forbidden**, constraint
#5b). This is a third: emit it as a **macro**.

**And it is safe exactly where expansion is not.** `\sps` preserves token identity — still
greppable, still restylable in one place — while asserting *nothing about meaning* in the
document text. The author gets the styling and replacement they want without HandZoo ever
writing a word they did not. That is constraint #5b satisfied rather than dodged, and the
author arrived at it unprompted — the same pattern as inventing the crop verdict before using
it (§11.1.2).

**The machinery already exists and was verified end to end.** Emitting `\sps` puts it through
`declarations.py` unchanged:

```
find_undefined(r"\sps \exists a morphism") -> ['sps']
-> \ifdefined\sps\else\DeclareMathOperator{\sps}{sps}\fi  % TODO: confirm operator name
```

The `\ifdefined` guard means the author's own definition in their master preamble wins and
ours becomes a no-op — which is precisely "one go". The generated fallback is
`\DeclareMathOperator`, wrong for a text token, and it arrives carrying its own TODO; that is
the design working, not a defect.

So a lexicon entry needs a per-token **mode** (`literal` or `macro`), and the `macro` path is
the one the author actually asked for. Not built: it changes the lexicon file format, and the
format should change once.

### 11.1.2 The four-pane sketch — recorded, not scoped

The author's sketch (2026-08-21, explicitly "not prescriptive"): **image** and **typeset**
above, **tex** and **intelligence** below. Click the image to clip or zoom; click the typeset or
the tex to edit; click intelligence to *delegate* the edit.

Worth recording because the panes are not arbitrary — they are the four artefacts that already
exist in a run, and clicking each one names a mode from §11.1 that was previously abstract.

**One thing makes this more buildable than it looks.** Clicking the typeset output and landing
on the right source line is the pane that sounds hardest, and it is solved: `pdflatex
-synctex=1` emits a `.synctex.gz`, and `synctex edit -o "page:x:y:file.pdf"` returns the input
file and line. Verified locally. The compile gate already runs `pdflatex`, so the link costs a
flag.

**One thing is more dangerous than it looks.** "Delegate edit" is a new substitution surface,
and a worse one than the recognizer's. An agent rewriting the `.tex` can silently improve what
is on the page — the failure this project exists to refuse — and the human *asked for the
change*, so it arrives with borrowed authority and gets less scrutiny than the original
transcription did. If that pane is built, delegated edits must be attributable and diffable
before they land, not applied and reported. The rule that a fabricated diagram stays visible as
a marker is the same rule.

**Not scoped, and out of M0.** It is an application, and M0 is a walking skeleton. Recorded so
that the `--fix` timings tell it something: each mode measured now is a pane priced in advance.

### 11.3 Polish, not fix — and what that implies about what this is

**Two framings, and they must not be confused.** The author's intent for the seeded editor was
*"help me polish this"*, not *"fix what is broken"*. `--fix` names a defect; polish names a
finish. The difference is not cosmetic — the emitted page is usually mostly right, and calling
the interaction a repair mis-describes the work and primes the reviewer to hunt for errors
rather than read for sense.

**The measurement must stay narrow anyway.** `--fix`'s prompt says *"fix it until it says what
the page says"*, deliberately. Polishing includes authorial improvement — better phrasing, a
clearer break — which would happen in any world and is not caused by the tool. If that leaks
into a timed run the number stops measuring the tool and starts measuring the author's taste.
So: the **product** is polish, the **measurement** is correction to what the page says, and the
prompt keeps them apart.

### 11.4 Converter or workspace — the question this raises (author, 2026-08-21)

The author's framing: if the flow is *tablet → handzoo → some LaTeX application*, HandZoo is
useful. If there is **no need to open the LaTeX application until the galley stage**, HandZoo
replaces most of the use case rather than feeding it.

That is a positioning question, and the positioning statement is load-bearing rather than
marketing, so it is recorded here and **not decided**.

**What it would cost.** "The tool that refuses to hand you broken LaTeX" is a narrow, defensible
claim against a field of one — nobody else is gating handwritten conversion on buildability. An
editor competes with Overleaf, TeXShop, VS Code and a dozen others, and the differentiator
dilutes to nothing if the claim becomes "it also edits".

**What would make it defensible anyway.** None of those have the ink, and none of them know
which parts of the text are unverified. The claim is not *it is an editor*; it is **the only
place where the source page and the record of doubt stay attached to the text**. That is a
different product from a LaTeX editor with a PDF preview, and the gates are what make it one.

**Which reframes the gates.** As a converter's exit criteria they are gatekeepers: pass or
refuse. In a workspace they are **marginalia** — *this diagram is fabricated, this colour is
gone, this page is unverified* — attached to a location, and worked off over time. Same
findings, different job.

The author's own evidence supports the reframing. Their from-blank transcripts contain
`% insert snip` at every diagram: a note to self about unfinished business, attached to a
place. **That is the same species as a gate finding.** In a converter a finding is an error; in
a workspace it is a task list the author was already keeping by hand.

**What was actually blocking it, now built.** A run produced loose fragments and no document,
so the author had to open a LaTeX application simply to *see* the chapter — the pipeline
position was enforced by the absence of `chapter.tex`, not chosen. `assemble()` writes the
master (§6.1's model, finally implemented): pages `\input` in order, failures as **visible
placeholders** rather than silent omissions, and the master owning the preamble. Verified on a
real four-page fragment run: the assembled chapter compiles.

**The tension it exposes, unresolved.** `--standalone` exists so the compile gate can run on a
page; fragments exist so a chapter can be assembled. They pull against each other, and the
author's ch19 run used `--standalone`, so its pages cannot be `\input` at all — surfaced as a
placeholder rather than a broken build.

The resolution is probably that **the chapter is the better unit to compile-check**: it catches
what per-page checking cannot (a macro defined on page 3 and used on page 7), it lets fragments
be the default, and it removes the flag that forces the author to choose. That is a change to
the gate model and is not made here.

**Measured on the first full-chapter run (ch17, 13 pages, 2026-08-21).** In fragment mode the
compile gate cannot run, so **8 of 13 pages came back `unverified`** — neither pass nor fail.
The assembled `chapter.tex` then **compiled**. One compile verified what eight per-page checks
could not run at all, which is the argument stated as a measurement rather than a preference.

**The same run found a normalizer gap that thirteen pages exposed and one page could not.**
`\section{§17.2 Dual Category}` reached the ASCII gate with its section sign intact. R1
rebuilt macro nodes with `latex_verbatim()`, which carries the macro *and its arguments*
through untouched, so text inside any `{}` was never converted — while bare text always was,
which is exactly why it went unnoticed. The rule was right and its **reach** was not. Fixed:
the walker descends into braced arguments, except for macros whose braces hold an identifier or
a path (`\label`, `\ref`, `\includegraphics`), where rewriting a character silently breaks a
reference instead of a word.

### 11.2 Measured against Mathpix (ch17, 13 pages, 2026-08-21)

The author ran the same chapter through Mathpix. Both directions of the result matter, and the
one that matters most is against us.

#### We committed the substitution this project exists to refuse

Page 1 lists divisor pairs of 30. The source, read at 200 dpi, carries **three** pairs and an
ellipsis:

```
1, 30      2, 15      5, 6      . . .
```

HandZoo emitted **four**:

```
1, 30      2, 15      3, 10      5, 6      \dots
```

`3, 10` is a real divisor pair of 30. It is mathematically correct, it is what a helpful
assistant would add, and **it is not on the page.** The model completed the author's list.

Every gate passed. ASCII, delimiters, coverage, colour — all clean, and the page was
`unverified` only because the compile gate cannot run on a fragment. This is §5.5's
over-correction, caught in our own output, on a page that the whole apparatus reported as fine.

**It is the better regression fixture than baseline page 1**, because the failure is smaller.
Page 1 of the baseline dropped four glyphs and produced a contradiction a reader would notice.
This adds one line that no reader would question, in a document about category theory, where a
list of divisors is scenery rather than argument.

Mathpix, on the same list, misread the `6` as a `4` and dropped the ellipsis. **It did not
invent anything.** That is the sharper distinction than accuracy: a misreading is wrong and
looks wrong; an invention is wrong and looks right.

#### On text and symbol fidelity, we lead substantially

The chapter's central object is a script `C` — the category. Counted across both outputs:

| rendering of the category object | Mathpix | HandZoo |
|---|---|---|
| `\mathcal{C}` (correct) | — | **40** |
| bare `e` | 17 | 0 |
| `\tau` | 4 | 0 |
| `\mathscr{C}` | 1 | 0 |
| `C` | 7 | 0 |

Mathpix read one glyph four different ways, and `e^{op}` for `\mathcal{C}^{op}` makes the
mathematics meaningless while typesetting perfectly. Alongside it: *eategory*, *co-Cuthor*,
*mathametician*, *womorphism*, *monie*, *eoprojections*, and `\$17.3` where the section sign
became a dollar. HandZoo's rendering of the same content is consistent and, on the passages
compared, correct.

**So the author's impression is confirmed on this document** — and one document by one author
in one hand is not a benchmark. What it does establish is that the claim in §11.2's earlier
form ("recorded, unmeasured") can no longer be waved at.

#### Where Mathpix is plainly better, and what it costs

It **crops diagrams automatically.** Thirteen regions extracted with bounding boxes, embedded
with `\includegraphics` and captions, in colour, without being asked. The divisibility lattice
on page 1 came out clean. HandZoo emits `[TODO diagram: ...]` and waits for a human (§7.2).

That is the capability gap, and it is real. But it is also §7.2's argument made concrete from
the other side: Mathpix's crops arrive **unflagged**. Nothing distinguishes a well-placed box
from a badly-placed one, and a wrong crop is a plausible figure of the wrong region. The design
decision to require human confirmation is a cost paid deliberately; this comparison is the
first evidence of what is bought and what is spent.

#### What the comparison actually says about positioning

Mathpix produced a complete, compiling, well-typeset document with images, for all thirteen
pages, and it is **wrong in ways nothing in the artefact discloses**. HandZoo produced nine
pages, held four back with visible placeholders, marked every diagram it would not draw — and
still slipped an invented line past every gate.

Neither of those is "better OCR". The difference is the disclosure, and our own page 1 is proof
that disclosure is not yet good enough: the gates say nothing about substitution, and this is
what that silence costs.

It matters because the positioning depends on it being *false enough*: HandZoo is deliberately
not "OCR for math", on the grounds that Mathpix owns that and is mature. If HandZoo's text
fidelity genuinely leads on handwritten mathematical prose, that is a second claim, and it
needs a measured A/B on the fixture corpus before it may be said out loud.

### 11.3 The criterion as originally written


Adopted from Value's dissent (binding condition 9), which no experiment in this review could settle:

> **Author-timed:** minutes to correct emitted `.tex` to ground truth, versus minutes to transcribe the same page from a blank file. Both on the same two baseline pages, by the author.

If correction time ≥ transcription time, M0 has negative value regardless of gate colour. Edit-distance alone is insufficient — catching `|||| < ||||` requires re-reading the source page, which is most of the transcription cost, and edit-distance scores that diff as small.

This requires the author and cannot be automated. **The measurement can be, and now is
(2026-08-20).**

`handzoo-review OUT --transcribe PAGE` times the control arm: the page image opens, an empty
file opens, and the clock runs until the editor closes. The emitted `.tex` is never shown —
the measurement is *minutes from blank*, and a glance at the tool's output makes it something
else.

**It refuses a page that has already been reviewed.** Once the author has read the emitted
text they know what is on the page, and transcription time is no longer measurable there. This
is the guard that matters most: a contaminated number that looks clean is worse than no number,
and this is the one measurement the milestone turns on.

`transcribed` is deliberately **outside `GOLD`**. It is ground truth for the page and says
nothing about what the tool emitted; folding it in would inflate the count of rows that judge
the output with rows that never looked at it — the conflation `keep-unreviewed` exists to
prevent.

`--summary` reports both arms per page, and **only for pages carrying both**. One arm answers
nothing, and printing it as though it did is how a half-measurement gets quoted as a result.
When correcting is not cheaper, the summary says so in the milestone's own words rather than
leaving the reader to do the subtraction:

```
EXIT CRITERION — seconds to correct against seconds to type from blank
  page   1   correcting    50.0s   transcribing   310.0s   correcting wins
  page   2   correcting   265.0s   transcribing   180.0s   TRANSCRIBING WINS

  2 page(s) carry both arms. This is a sample, not a result.
```

## 12. The correction surface — designed, not scoped (2026-08-25)

M0 is cleared, and the author's judgement is that ingestion and correction are not yet
approachable enough for anyone else to generate the labelled pages everything else needs. So
the UI comes before more harness work. This section records what is settled, what is refused,
and the one question that has to be answered before any of it is built.

### 12.1 The question is the round trip, not the format

The author framed the tension precisely: markdown opens a route to a WYSIWYG surface, at the
risk of inheriting Typora's failure modes or of **being judged on the editor** rather than on
what the tool refuses.

The second risk is not a risk, it is the default outcome. Ship an editing surface and every
user compares it to the editor they already have; the refusal — the actual product — becomes
chrome around a text box. That argues for a **review tool that can edit**, not an editor that
can review: findings-first, page-at-a-time, editing as something done *to* a finding. It is
what `handzoo-review` already is, and it is defensible ground precisely because no one is
competing there.

But the format question underneath it is real, and it is not *markdown or LaTeX*. It is:

> **Is the artifact the human approved the artifact that ships?**

Today it is. The author corrects the emitted `.tex`, the gates run on that `.tex`, and that
`.tex` is what gets built. Introduce a rendered surface and a translation sits between the two,
and **the translation can lose exactly what the gates exist to protect** — the underline that
is a label (§11.0.1b), the ink colour, the mark. That is the same class as the "delegate edit"
pane in §11.1.2: a new substitution surface, arriving with the author's own authority.

**The concrete mechanism, and why "just render it" does not settle it.** KaTeX and MathJax
implement a *subset* of LaTeX math — no arbitrary packages, no `tikz`, no document structure.
Our compile gate is `pdflatex`. Those are two different languages that share a syntax, so:

- a block can render cleanly in the editor and **fail the compile gate**
- a block can fail to render and **compile perfectly**

An editor that shows a green preview while the artifact fails is the project's own villain
wearing a friendlier face. Whatever is built, **the gate result and the preview must be
distinguishable on screen**, and the gate is the one that decides whether a page ships.

**So the prerequisite is a fidelity test, not a framework choice.** Take the emitted `.tex`
for a chapter, round-trip it through whatever intermediate representation the editor uses, and
diff. If it survives, the editor is a rendering choice. If it does not, the losses are the
specification for what the editor must preserve. That is cheap, mechanical, and it can be run
before a line of UI exists.

**First cut, measured on the 44 emitted pages of ch18.** A math renderer covers math spans;
everything else is the editor's own problem:

| | count | |
|---|---|---|
| math spans | 269 | what KaTeX/MathJax actually render |
| sectioning | 45 | document structure |
| `[TODO diagram: …]` markers | 22 | **a gate's output** |
| line breaks | 22 | |
| marking (`\underline` / `\textbf`) | 26 | **the label mark — §11.0.1b** |
| lists | 11 | |
| tabular | 1 | |

The distribution is the finding. Roughly a third of the constructs on a page sit outside what
a math renderer touches — and **the gate-protected ones are concentrated entirely in that
third**. The diagram markers are a gate's output; the marking is the mark §11.0.1b exists to
defend. So the editor's fidelity burden falls precisely on what this project refuses to lose,
and "we'll render the math and pass the rest through" is not a plan, it is the whole risk.

### 12.1.1 The instrument: compare the render, not the source — but not via `pdftotext`

**Measured 2026-08-25.** The author's reasoning: a `.tex` diff is too noisy to be useful, since
`\mid` / `\vert` / `\middle|` and the arrow variants differ textually while meaning the same
thing; normalising to a convention is a fool's errand. Better to diff the *typeset* output, up
to an isomorphism that is "probably whitespace and layout".

**The premise is right and the instrument is wrong**, and the second half is the interesting
part. `pdftotext` is blind in exactly the places this project is not allowed to be.

Semantically different pairs, compiled and extracted:

| pair | extraction A | extraction B | |
|---|---|---|---|
| `\underline{Prop 18.2}` vs plain | `Prop 18.2 holds.` | `Prop 18.2 holds.` | **collapsed** |
| `\textcolor{red}{R}` vs plain | `R house` | `R house` | **collapsed** |
| `$x^2$` vs `$x2$` | `x2` | `x2` | **collapsed** |
| `$\frac{a}{b}$` vs `$a/b$` | `ab` | `a/b` | distinguished |
| `$\sum_{i=1}^{n}x_i$` vs `$\sum x$` | `Pni=1 xi` | `Px` | distinguished |

The first three are **the label mark (§11.0.1b), semantic ink colour (§6), and notation
degradation (§11.0.1a)** — three of the five classes in the measured defect taxonomy. A
`pdftotext` diff would report a clean round trip on a transformation that dropped every one of
them. Its lossiness is not incidental to our use; it is aligned with our subject.

**And it is not even conservative about the noise it was chosen to suppress.** `\mid` and
`\vert` extract identically — but they are different math classes, `\mathrel` against
`\mathord`, so they typeset with different spacing and the pages do not match. The construct
offered as the example of noise turns out to be a real difference that the text extractor was
hiding. `\to` and `\rightarrow` genuinely are aliases, and those do match.

**Pixels are the isomorphism the author was reaching for.** Compile both sides, rasterise, hash
the pixel data. Two documents compare equal exactly when they *look* identical, which is the
definition of "renders the same" and is a stricter and more honest reading of "up to whitespace
and layout" than any text extraction can give:

| pair | pixels | |
|---|---|---|
| `\to` vs `\rightarrow` | identical | correctly equivalent |
| `\mid` vs `\vert` | differ | **caught** — different spacing class |
| underline vs plain | differ | **caught** |
| colour vs plain | differ | **caught** |
| `x^2` vs `x2` | differ | **caught** |
| fraction, sum limits | differ | **caught** |

Everything semantic is caught; the one true alias is correctly called equivalent. `pdftoppm` is
already a dependency (`rasterize.py`), so this costs nothing new.

**It is called Blink** (`experiments/blink.py`), for the blink comparator — two plates of the
same star field alternated until something moves, the instrument Tombaugh found Pluto with. It
knows only that something changed, which is both the capability and the limit.

Two names were rejected on substance rather than taste. `Veritas` and `Ma'at` both claim
**truth**, and this project's load-bearing hedge is that the gates prove output *builds*, not
that it is *true* (§5.5, and "never print an unqualified PASS"). A component reporting
PRESERVED under a name meaning truth would undercut the positioning from inside the codebase.
`Horus` and `Heimdall` describe watchers, and a watcher understands what it sees; this does not.

**How to use it.** Pixels are an **oracle, not an explanation** — they say *that* something
changed and where on the page, never what. So the instrument is two-layer: pixel comparison
decides pass/fail and localises the region; a source diff, run only on pages that already
failed, describes it. That also makes the `.tex` diff's noisiness harmless, because it is no
longer the thing deciding.

One property to respect: pixel equality is intolerant of reflow, so a single dropped word
shifts everything after it and the whole page differs. That is a defect for comparing two
*different* documents and exactly right for a round trip, where the correct answer is
byte-identical layout. Do not reach for it to compare a corrected page against its original.

**A note on the corpus.** The `.tex` on disk for ch18 has been edited in place by `--fix` runs,
so it is no longer pristine recognizer output. A round-trip test needs **fresh runs on a long
document**, as the author noted — both to have an unedited baseline and to give the comparison
enough pages to be worth trusting.

### 12.2 What the survey got right, and what we already have for it

The author's research (Gemini, 2026-08-25) landed on a **block-based document editor** —
Tiptap/ProseMirror, Lexical, Editor.js, Milkdown — with a three-state node: rendered by
default, region-review on hover, raw source behind an explicit toggle. That is a good fit, and
several pieces already exist here:

| the pattern | what handzoo already has |
|---|---|
| per-block error badge, rest of document stays clean | `GateResult.failures` carry a line and an excerpt; `advisory` already distinguishes *look at this* from *refused* |
| click a block, highlight the source region | `crop_vector()` cuts a region as vector; ink-region detection finds boxes |
| hover a formula, see the original handwriting | the crop verdict already does this, non-interactively |
| per-block provenance ("Origin: local / cloud") | DESIGN §8.1 — designed, and this is a second consumer for it |

**The per-block cloud upgrade is better than what we ship today.** `--provider gemini` sends
*whole page images* to Google. Sending only one block's crop, only when the author asks, is
strictly less exposure for the same benefit — and it makes the local-first default meaningful
rather than nominal. If the online provider survives into the UI, this is the shape it should
take.

Two mechanical notes worth keeping: streaming output must suspend preview compilation between
`\begin{...}` and its closer or the screen thrashes on every partial token (we already salvage
truncated JSON, which is the same problem one layer down); and a two-pane anchor map is what
the author sketched in §11.1.2, now with a component vocabulary attached.

### 12.3 What is refused

**Never render fabricated `tikz`.** The survey's advice for hand-drawn figures is to prompt a
cloud model to *"return only clean executable TikZ"* and run it through TikZJax in the browser.
That is the single thing this project must not do. Hard constraint #4 forbids it; §5.7.1
already refused the compile-time version of it; R9 exists to turn invented diagram markup into
a visible marker. Rendering invented TikZ converts a caught fabrication into a beautiful,
typeset, **wrong** diagram that the author will accept because it looks finished — 5b's warning
exactly, with better production values.

The crop verdict is the answer and it is already built: cut the author's real ink as vector,
place it, flag it. A UI should make that *one click* rather than replace it with generation.

TikZJax may still be useful for previewing `tikz` **the author wrote**. The distinction is
provenance, and §8.1 is what carries it.

#### 12.3.1 A commented skeleton is not a fabrication — if it guesses nothing

The author's refinement: *for someone who wants `tikz`, a skeleton to uncomment and edit is not
awful.* Correct, and the reason it is correct is worth stating precisely, because the line is
narrow.

**What made rendered TikZ dangerous was never the markup. It was that the artifact looked
finished.** A rendered diagram is accepted at a glance; a beautiful wrong picture is worse than
an obviously absent one. A commented block inverts every part of that:

- it cannot render, so there is nothing to accept at a glance
- it cannot compile into the document, so no gate can pass on its account
- it becomes live only by an explicit act, and uncommenting requires reading it

That is the same shape as `advisory` (§11.0.1b) and as the crop verdict: **inert by default,
and activation requires attention.** So a skeleton is admissible where a rendering is not.

**But the anchoring risk is real and is the reason for the constraint below.** §11.0 measured
it on humans: a correction is what the author judged right *after reading our output*, and
reading it anchors them. A skeleton with three nodes on a page that has four invites the author
to uncomment, adjust, and never notice the missing one — the plausible-substitution failure,
relocated into scaffolding.

**The line is the project's own trust boundary, and it already exists.** `Mark.context`,
`placement` and `count` are measured-trustworthy; `Mark.description` is measured-untrustworthy
(`recognize/base.py`). So:

| may go into a skeleton | may not |
|---|---|
| the environment and preamble — pure boilerplate | node **labels** or arrow **names** |
| a grid sized from *positions* the inventory reported | anything read out of `description` |
| a count of marks detected in the region | a guess at what the diagram *says* |

A skeleton built from position is **structure**. A skeleton built from description is a
fabrication wearing a comment, and the comment is exactly what would stop anyone noticing.

**Three provenances, three treatments** — which is the general rule this makes explicit:

| origin | treatment |
|---|---|
| recognizer emitted `tikz` | stripped to a visible marker (R9) — it was told not to, and did |
| handzoo generated a skeleton | commented, inert, structural only, never from `description` |
| the author wrote it | left alone; previewable |

Not built. Recorded so that when someone adds it, the constraint arrives with it rather than
being discovered afterwards. The natural home is beside the crop verdict in `handzoo-review`:
`c` cuts the real ink, and a sibling key offers scaffolding for an author who would rather
draw it in `tikz` than paste an image.

**Astryx: verified real, and not the thing this project needs.** Checked against primary
sources 2026-08-25 (`github.com/facebook/astryx` via the REST API, the in-repo launch post, the
npm registry).

Real, genuinely Meta — MIT, `Copyright (c) Meta Platforms, Inc.`, commits from `@meta.com`
addresses, docs on `atmeta.com`, 12.4K stars, pushed the same day it was checked. The MCP
server, the JSON CLI manifest and the StyleX architecture all exist as described. The survey's
release date was wrong by ten days (**2026-06-18**, per the repo's own post; 06-28 appears to
be drift from a secondary article) — a small error, but the kind that arrives via reblog rather
than source.

**It does not address our gap.** It is a general-purpose component library, and the roster was
enumerated rather than assumed: no math rendering (no KaTeX or MathJax anywhere in the repo),
no PDF viewer, no image-region or annotation surface, no diff view. A page-review UI needs
exactly three things — LaTeX math rendering, source-page display with vector region cropping
(the `c` key, which answered 45 of 49 findings), and side-by-side source/output comparison —
and Astryx supplies none of them. It would supply the chrome around them competently. The one
package that maps to document editing, `packages/richtext` (Lexical-based), is `private: true`,
canary-only, with no stable release and an explicitly unstable API.

Two things to weigh if a GUI ever becomes a committed milestone. Its MCP server is
**remote-hosted** at `astryx.atmeta.com/mcp` rather than run locally, which is worth naming
against §7's local-first constraint — it carries only component queries, never page content,
but the default is someone else's server. And it is Beta at `0.5.0`.

**One reported behaviour is worth recording for its own sake.** An independent tester (single
hands-on account, not a study) found that outside about four components Astryx **silently falls
back to its own default colours** instead of the supplied brand — demonstrated by re-running
with a brand whose blue happened to match the default and getting a correct-looking render.
That is this project's villain in another domain entirely: output that looks right, is not, and
whose wrongness is invisible precisely because the plausible answer was substituted for the
specified one. A useful reminder that the failure mode is not peculiar to VLMs.

**Bottom line: correctly identified as real, and orthogonal.** Adopting it presupposes a much
larger decision — build a desktop or web review app — which should be made on its own merits
first.

### 12.4 The ordering this implies

1. **Round-trip fidelity test** — mechanical, no UI, and it decides the format question.
2. **Refuse-first framing** — the surface presents findings and gate verdicts; editing serves
   them.
3. **Provenance per block** (§8.1) — needed the moment two models can touch one document.
4. Then the editor, whose framework choice is the *least* consequential decision here.

Nothing above is scoped into a milestone. It is written down so the first UI commit does not
have to make these calls under deadline.
