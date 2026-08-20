# HandZoo M0 Walking Skeleton — Decision Document

**Version:** 1.0 (Post-opinion-panel, post-baseline)
**Date:** 2026-08-18
**Status:** Decisions locked; DESIGN pending design-stage Delphi review
**Inputs:** `inkwell-handoff.md` (design brief) · `reviews/delphi-opinion-2026-08-18.md` · measured baseline on real fixtures

---

## The Why (Problem Statement)

The author is writing a first-principles math book ("Naive Math") by hand on a reMarkable. The ink is write-only: three PDF exports on the desktop hold 39 pages of manuscript with **zero extractable text characters**. Typesetting means re-transcribing by hand.

Existing tools clear part of the bar and miss the important part. Typora's LaTeX export emits bare Unicode with nothing defining it and unbalanced `$` delimiters. Mathpix optimizes recognition accuracy. **Neither refuses to hand you output that is wrong.**

The measured baseline (below) shows why that distinction is the entire product.

## The Villain: silent corruption

Adopted from the Viability persona, and now empirically confirmed rather than asserted.

> Tools that emit plausible-looking output which quietly breaks — costing more time to debug than hand-transcription would have saved.

HandZoo's promise is therefore **not** "best handwriting recognition." It is:

> **The tool that refuses to hand you broken LaTeX.**

This is the one differentiator Mathpix structurally lacks — they optimize accuracy, not hard-fail gates. It also reframes the Emitter and Validator from "supporting stages" into the product itself, which matches the brief's own instinct that the differentiated code is the Emitter, not the recognizer.

---

## The Baseline That Changed The Design

Run 2026-08-18 against `fixtures/naive-math/counting-and-monoids.pdf`, pages 1–2, using `qwen3-vl:8b` via Ollama, uncapped generation, temperature 0.1. Artifacts in `baseline/`.

### Page 1 — passes every gate, and is false

| Gate | Result |
|---|---|
| Zero non-ASCII | **PASS** |
| Delimiter / environment balance | **PASS** (16 `$`, even; 5 `\begin` / 5 `\end` matched) |
| Compiles under pdfLaTeX, zero errors | **PASS** |

The emitted bullets:

```
\item The Blue House, "B", has more than the others
\item The Green House, "G", has more than the others.
```

The source page reads *"THE BLUE HOUSE HAS MORE 🧍 THAN THE OTHERS"* and *"THE GREEN HOUSE HAS MORE 🐕 THAN THE OTHERS"* — consistent statements about two different quantities. **The inline glyphs were the disambiguator.** Stripped, the two bullets directly contradict each other. The notation table lost all four glyph prefixes the same way: `🐕: G > R` ("green has more dogs than red") became a bare `G > R`, unmoored from what is being compared.

Ink colour is also semantic on this page — R/G/B are written in red, green and blue — and is discarded entirely.

### Page 2 — worse, and in a new way

| Source | Emitted | Failure |
|---|---|---|
| `1 → 11 → 111 → 1111 → ЖHT` (tally marks) | `1 \to \mathrm{II} \to \mathrm{III} \to \mathrm{IIII} \to \mathrm{V} \to \mathrm{X}` | Tallies **silently converted to Roman numerals** — collapsing the exact distinction the page teaches |
| `))))` vs `ЖHT` with hand drawings | `\texttt{||||} < \texttt{||||}` | Emitted **`4 < 4`** — self-evidently false |
| "IV IS V LESS 1 FINGER" | `\texttt{III} is \texttt{V} less 1 finger` | Wrong numeral |
| Tally expression `ЖHT ЖHT ЖHT ЖHT III = …` | *(absent)* | Content **silently deleted** |
| `⊕ COUNTING` | `\section*{\oplus COUNTING}` | Math-mode command in text mode → **compile failure** |

Page 2 fails the compile gate — on the `\oplus`, a pure syntax error. **Every one of its semantic errors passes every gate that exists.**

### What this falsifies in the brief

1. **Diagram separability.** The brief's policy — "detect the region, crop to PNG, emit a referenced image plus `% TODO`" — assumes diagrams are separable blocks. On this content, glyphs are **terms in the sentence grammar**. A stick figure inside "HAS MORE 🧍 THAN THE OTHERS" is a noun. You cannot crop it out without destroying the sentence. This is a policy gap, not a segmenter tuning problem.
2. **Content shape.** The brief anticipates equation-dense math (fraction stacking, root scope, matrix layout). The actual manuscript is pedagogical prose with tables, tally marks, Roman numerals, inline drawings and semantic colour. The 2-D structure problem is real but it is not the problem these pages present.
3. **Colour is unmodelled.** The brief never mentions it. It is load-bearing here.
4. **The recognizer's failure mode is confabulation, not illegibility.** It does not fail to read the marks; it reads them and confidently normalizes them into something it finds more plausible. That is the same failure class as fabricating `tikz` — which the brief already forbids — arriving through a door the brief left open.

---

## Decisions

### D1 — Scope: full M0, not a reduced spike
**Decided by the author.** The opinion panel recommended shrinking M0 to a measurement spike. The measurement has now been taken as part of specification rather than as a separate milestone, so the spike's purpose is served and full M0 proceeds.

### D2 — Positioning: validator / guardian
Adopt the Viability reframe. The hero moment is the refusal, not the transcription. Every acceptance gate is a feature, not a chore.

### D3 — Language: Python end-to-end
Every fallback recognizer named in the brief (`pix2tex`, `texify`/Surya, `TrOCR`) is Python; `uv` is present; `pdftoppm` and the LaTeX engine are subprocess calls from any host language. A .NET or TypeScript core would buy a process seam and no capability. **Alternatives compared in DESIGN.md** per process requirement.

### D4 — No heuristic segmenter in M0
Structural and Operational converged on this independently, and the baseline supports it: the VLM already emits inline region markers in reading order, which a bounding-box segmenter would have to reconstruct. Reading-order recovery is the hard part and the VLM does it for free. A dedicated segmenter is added only against a named failure it demonstrably fixes.

### D5 — Recognizer: `qwen3-vl:8b-instruct` (REVISED 2026-08-18)

**Use the Instruct checkpoint, not Thinking.** `qwen3-vl:8b` is an alias for Thinking; `-instruct` is separate weights. Measured on the full ch16 corpus: blanks 2/26 -> **0/26**, pass rate 81% -> **96%**, median latency 92s -> **4s**, corpus wall clock ~60min -> **1.9min**. Quality improved on both diagnostic failures. See DESIGN 3.0.

**Original D5 (Thinking checkpoint), retained as record:**
Current flagship; OCR across 32 languages (up from 10), explicitly better on blurred marks and rare characters, tuned to reconstruct fine structure in long documents. 6.1GB against qwen2.5vl:7b's 6.0GB — same cost, newer model.

**Two hard constraints on the recognizer port, discovered by measurement:**
- `qwen3-vl` emits reasoning into a separate `thinking` field which **counts against `num_predict`**. A 1400-token cap produced an empty response; a 3000-token cap produced an empty response on one run and valid output on another.
- **`think: false` is not honored** by ollama 0.30.7 for this model (observed thinking of 20,874 and 28,306 characters with the flag set).
- Therefore: **never cap generation, and always verify content is non-empty before proceeding.** Encoded in `baseline/recognize.py`.

### D6 — Fourth acceptance gate: no silent glyph loss
**The central decision of this document.** The brief forbids fabricating `tikz`. The baseline demonstrates the dual failure — glyphs silently deleted, silently reworded, or silently normalized into a different notation — with nothing flagged and every existing gate green.

> **No mark may be dropped, reworded, or resolved to a different notation silently.** Every non-transcribable mark becomes an explicit inline marker. A page whose emitted text carries no marker for a region the recognizer reported is a hard fail.

Without this gate, "refuses to hand you broken LaTeX" is true only for syntax, and the positioning in D2 is marketing rather than engineering.

### D7 — Fixtures are never tracked
`fixtures/` is gitignored. The manuscript is unpublished IP and this repository is intended for public release. Derived page rasters are ignored for the same reason. Emitted `.tex` baselines are tracked as evidence.

### D8 — The handoff brief becomes provenance, not specification
Extract `inkwell-handoff.md` from `.handoff.zip` to `docs/handoff.md` and track it, with a header recording which of its assumptions the 2026-08-18 baseline falsified. Untracked in a zip it is lost; tracked without the header, the next reader follows it into the same wall. It retains unique value: M1–M3 rationale, fixture descriptions, and the Unicode mapping table.

---

## Scope

### In scope (M0)
Rasterize → recognize (single VLM pass, no segmenter) → normalize → validate → emit, driven by `handzoo convert`, with four gates: ASCII-clean, delimiter-balanced, compiles clean, **no silent glyph loss**.

### Out of scope (M0)
MCP adapter, Learning Store, correction UI, macro-dictionary learning, diagram auto-conversion, cross-user pooling, fine-tuning.

### Deferred but now specified
- **`qwen2-math` as semantic checker (M1+).** Text-only, so it cannot recognize; but it can read emitted LaTeX and flag statements that do not follow — the `|||| < ||||` class. Alibaba's own multimodal math demo uses exactly this pairing: a VLM to see, a math model to reason. This is the only mechanism identified that addresses semantic falsity.
- **Colour as a first-class channel.** Unmodelled in the brief, load-bearing in the manuscript.

## Competitive position — researched 2026-08-18

**D2's differentiator is real but narrower than claimed.** Two 2025–2026 papers independently
converged on compilability-as-signal: **TexOCR** (arXiv 2604.22880) trains a 2B model with RL
rewards from "LaTeX unit tests that directly enforce compilability," and **olmOCR 2**
(arXiv 2510.19817) uses renderable-HTML unit tests as reward. Both use it as a **training
signal**. Neither ships an inference-time, user-facing refusal.

- **Good news:** independent convergence says the idea is sound, not a dead end.
- **Bad news:** we cannot borrow their evaluation numbers. "Refuses to hand you broken LaTeX"
  as a *product behaviour* still needs its own validation.

**Duplication risk to check before assuming greenfield:** **Pix2Text** — actively maintained
(pushed Feb 2026, 3.2k stars), explicitly positioned as a free Mathpix alternative, handles
layout/tables/formulas → Markdown. This is the Skeptic's dissent with a URL attached. Nothing
found does reMarkable *vector ink* → LaTeX locally via a VLM, but Pix2Text should be run
against our fixtures before further investment.

**One clean gap, and it is our hardest problem.** Inline pictorial elements functioning as
*grammatical constituents* — a stick figure as a noun, a `↰` annotating the line above, a `✓`
asserting an axiom holds — have **no prior art**. Everything found treats figures as
block-level regions to caption or segment. Budget this as novel R&D, not integration.

**Also confirmed:** VLMs hallucinate and omit more than classical OCR on document
transcription (Qwen3-VL-235B at 80.6% vs specialist PP-OCRv6 at 93.2% on a hallucination
benchmark), which is the risk D6 and §5.5 exist to manage.

### Alternative checkpoints worth testing (researched 2026-08-18)

Having found that checkpoint choice mattered enormously (D5), the obvious follow-up is whether
a *better* one exists. No candidate has direct handwriting evidence beating
`qwen3-vl:8b-instruct` outright, but three are worth an A/B:

| Candidate | Signal | Availability |
|---|---|---|
| **olmOCR-2-7B** | Scored highest on semantic similarity and cursive consistency in an independent handwriting benchmark — the strongest direct handwriting signal found | MLX build exists (`mlx-community/olmocr-2`) |
| **Nanonets-OCR2-3B** | Explicitly trained on handwriting *and* math/equations; but weakest in one general benchmark and reportedly poor on cursive | GGUF for Ollama, and MLX |
| `q8_0` of our current model | One Qwen2-VL data point: BF16 81.0% vs 4-bit 78.8–79.5% OCR accuracy — a ~2pt gap is gate-pass-rate-sized, and 64 GB has headroom | already available |

**Ruled out:** GOT-OCR2.0 (explicitly weak on handwriting), InternVL3-8B (lower OCRBench, no
handwriting evidence), dots.ocr (immature GGUF, open Ollama/macOS vision bugs). No evidence
either way for Florence-2, SmolVLM, Granite-Vision, Moondream — all trained on printed corpora.

**Specialization is measurably worth something:** under text-perturbation stress, general VLMs
degraded up to 4.5 WER points, OCR-specialized VLMs 0.2–2, traditional OCR under 0.6
(arXiv 2607.21617). That ordering argues for testing a specialist against our general VLM.

**On the ensemble idea — the right version.** Our Tesseract/Jaccard rejection was correct, but
for a fixable reason: we compared against a *weak* reader using literal token overlap.
**Consensus Entropy** (arXiv 2504.11101, CVPR 2026) uses two *comparably capable* VLMs and
measures output-distribution agreement entropy rather than token match, improving error
detection F1 by 42% over VLM-as-judge, training-free. `qwen3-vl:8b-instruct` + `olmOCR-2-7B`
is the natural pairing, and it targets the substitution class nothing else catches.

### Runtime alternatives — BaseRT assessed and declined (2026-08-20)

Real product, not a garbled name: a native-Metal inference runtime for Apple Silicon
(`github.com/basecompute/baseRT`, arXiv 2607.00501), written straight against Metal with no
MLX or Core ML dependency.

**Declined, and for the opposite reason to the one that prompted the look.** The xda article
repeats BaseRT's own M5 Pro figures — up to 6.4× prefill over llama.cpp, 3.9× over MLX — but
those are vendor-published and unreproduced, and the article's own author notes the
*generation*-speed gain is barely noticeable in practice. Generation is the half nearer our
workload; we send one page image per call, not a long agentic context, so the prefill multiple
is largely irrelevant to us.

The real finding is a risk rather than a win: **the inference engine is closed-source with a
proprietary quantization format (`.base`), and Qwen3-VL support is unconfirmed.** Moving a
faithfulness-gated pipeline onto a closed engine with an unverified quantization scheme trades
the thing we care about for the thing we do not. Latency is already 4–15s per page.

Revisit only if VLM support is confirmed, and then the test is output divergence from the
current baseline on the fixture corpus — not a benchmark number.

### Capture-side variable, untested (author, 2026-08-20)

The author wrote notes at different zoom levels on the reMarkable and reports "some effect on
the notes", uncertain whether pen stroke width actually changes. This is the first variable
identified on the **capture** side rather than the recognition side, and it is one the author
controls directly.

Testable and unmeasured: rasterise pages written at different magnifications, compare stroke
width in the vector source (`pdftocairo -svg` exposes path widths), and check whether gate pass
rate varies with it. If it does, "write at this zoom" is the cheapest quality lever available
anywhere in this project — no model change, no code.

## Open questions for the design panel

1. Should the recognizer target an intermediate AST rather than LaTeX text directly? Structural argued LaTeX-as-intermediate forces the Normalizer to do semantic correction and syntax rendering through one string format. The colour and glyph findings sharpen this.
2. Can the no-silent-loss gate (D6) actually be enforced, given the recognizer is a single opaque pass with no independent region inventory to check against?
3. Is 138s/page (measured, uncapped) acceptable? 51 pages ≈ 2 hours. Do `qwen3-vl:4b`/`2b` trade accuracy acceptably?
4. Does the correction loop need to move into M0 after all, given the baseline shows output requiring human adjudication on every page?
