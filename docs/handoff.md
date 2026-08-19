> ## Provenance notice — read before following this document
>
> This is the **original design brief** for the project, written under the working name
> *Inkwell* (now **HandZoo** — see `CLAUDE.md`). It is preserved as **provenance, not
> specification.** It holds the reasoning behind M1–M3, the fixture descriptions, and the
> Unicode mapping table, none of which exist elsewhere.
>
> **A measured baseline on 2026-08-18 falsified four of its assumptions.** Do not follow
> the sections below without reading
> `.specify/features/m0-walking-skeleton/DECISION.md` first.
>
> | § | Assumption | Status |
> |---|---|---|
> | §5 | Diagrams are separable regions that can be cropped and referenced | **False for this manuscript.** Glyphs are terms *inside* sentences — a stick figure is a noun. Cropping destroys the sentence. |
> | §3 | A heuristic stroke-density segmenter is the right start | **Dropped.** The VLM recovers reading order for free; a bbox segmenter would have to reconstruct it. |
> | §4 | Local Qwen2.5-VL is the default recognizer | **Superseded** by `qwen3-vl:8b`. Also: `qwen2-math` is text-only and cannot recognize — it is a *semantic checker* candidate, not a recognizer. |
> | §8 | `! grep -P '[^\x00-\x7F]' out.tex` gates non-ASCII | **Broken gate.** `-P` is a GNU/ugrep extension; on stock macOS grep it errors, and `!` inverts the error into a pass. Use `iconv -f ASCII -t ASCII`. |
> | — | Ink colour | **Unmodelled here, load-bearing in the manuscript.** R/G/B houses are written in red/green/blue. |
> | §6 | Compile + ASCII + delimiter gates are sufficient | **Insufficient.** Baseline output passed all three and was factually false. See D6. |

# Handoff: Handwriting-notes → LaTeX/Markdown MCP tool

**Working name:** Inkwell (rename freely)
**Audience for this doc:** Claude Code (CLI), building the walking skeleton and first milestones.
**Author's north star:** get *my own* handwritten math notes (reMarkable PDFs) into clean, compilable LaTeX — and Markdown — with a correction loop that learns *my* hand over time.

---

## 0. Read this first (interaction contract)

- **Build the walking skeleton before anything else** (see M0). Prove the core end-to-end on the provided fixtures, then layer adapters and learning on top.
- **Do NOT build a per-character recognizer.** Glyph recognition is the easy part and is already solved well enough by off-the-shelf pieces. The hard part is **2-D structure** (sub/superscripts, fraction stacking, root scope, matrix/brace layout). Use end-to-end *image→markup*, not classify-then-assemble.
- **Wire existing components; write glue, not models.** The differentiated code is the **Emitter** (normalize + validate + target selection) and the **Learning Store**, not the recognizer.
- **Dogfood on the author's real pages** (fixtures below). Generic OCR benchmarks (CROHME et al.) are *not* the eval target — the author's handwriting is.
- **Diagrams are not auto-converted.** Detect diagram regions, crop them, reference them as images, and flag for manual authoring. Do **not** hallucinate `tikz-cd`. (See §5.)

---

## 1. Context (why this exists)

The author is writing a first-principles math book ("Naive Math") on a reMarkable. Notes currently live write-only as ink; typesetting means re-transcribing by hand. Existing tools (Mathpix, Typora export) are "so-so" — specifically, **Typora's LaTeX export emits bare Unicode without defining it and produces unbalanced `$` delimiters**. Those two failures are the bar to clear.

This is also an MVP in a "ship good-enough, gather data + buzz" program (Roci). Two consequences:

1. **Favor a shippable vertical slice over completeness.** A narrow tool that nails *the author's* notes beats a general tool that's mediocre on everyone's.
2. **The corpus is the asset.** Human-verified `(page-image, correct-LaTeX)` pairs are the point. Design so every correction is captured.

The commodity part (image→LaTeX) is mostly a solved vision task; frontier VLMs do a passable job already. The **defensible** part is the *personalized notes→manuscript loop, drivable by both humans and agents*. Build for that.

---

## 2. Architecture — ports & adapters

Make the **engine** a plain library/service that has no idea who is calling it. MCP and the HTML UI are *adapters*, not the core. (This mirrors the author's clean-core instinct on the Cygnus side.) Putting MCP at the center would force the human UI to speak an LLM-shaped protocol — avoid that inversion.

```mermaid
flowchart LR
  subgraph IN[Input]
    RM[reMarkable PDF] --> RAS[Rasterizer<br/>pages → page images]
    RAS --> SEG[Segmenter<br/>text/math vs diagram regions]
  end

  subgraph CORE[Core engine  — knows nothing about caller]
    SEG --> REC[Recognizer<br/>image → raw markup<br/>VLM pass · pix2tex · texify]
    SEG -. diagram crops .-> EMIT
    REC --> NORM[Normalizer<br/>Unicode policy · macro expansion · delimiter policy]
    NORM --> VAL[Validator<br/>delimiter balance · headless compile]
    VAL --> EMIT[Emitter<br/>target: markdown | latex]
    STORE[(Learning Store<br/>image↔LaTeX pairs<br/>personal macro dict)]
    STORE --> REC
    STORE --> NORM
  end

  subgraph AD[Adapters]
    MCP[MCP adapter<br/>for agents]
    UI[HTTP + HTML UI<br/>for humans]
  end

  MCP --> SEG
  UI  --> SEG
  UI  -- human-verified corrections --> STORE
  MCP -- tool-use traces --> STORE
  VAL -- failures + fixes --> STORE
```

**Two data doors, on purpose.** The human UI yields *verified ground truth* (gold). The MCP adapter yields *tool-use traces* (different dataset, also useful). Instrument both; treat the human corrections as the higher-grade corpus.

---

## 3. Pipeline stages

| Stage | Job | Start-simple choice |
|---|---|---|
| **Rasterizer** | reMarkable PDF → per-page PNG | `pdftoppm` (already reliable) |
| **Segmenter** | Split each page into text/math regions vs diagram regions | Heuristic first (stroke-density / connected-component bounding boxes); a model later only if needed |
| **Recognizer** | Region image → raw markup | Swappable provider (see §4) |
| **Normalizer** | Enforce Unicode + delimiter + macro policy | **Differentiated code — see §6** |
| **Validator** | Prove it's balanced and it compiles | `tectonic` headless compile gate |
| **Emitter** | Serialize to the requested target | `--target markdown\|latex`, `--standalone\|--fragment` |
| **Learning Store** | Persist pairs + personal macros | SQLite + a flat pairs dir; local-first |

---

## 4. Recognizer — providers to wire (not build)

Make this a strategy interface with a couple of implementations behind a common `recognize(image) -> raw_markup`:

- **VLM pass (primary).** Local **Qwen2.5-VL via Ollama** for on-box/privacy default; a frontier VLM as an opt-in "quality" provider. This is the realistic backbone.
- **`pix2tex` (LaTeX-OCR)** — direct math-image→LaTeX, easy to wire, good baseline for isolated equations.
- **`texify`** (Surya/Marker ecosystem) — math+text blocks → LaTeX/Markdown.
- **`TrOCR`** — general handwriting; useful for prose regions.
- **`Nougat`** — strong but aimed at *printed* academic PDFs; likely poor fit for ink. Keep as a note, not a dependency.

Default policy: VLM pass for whole regions; keep `pix2tex`/`texify` available for isolated-equation fallback and for cross-checking during eval.

---

## 5. Diagram handling (explicit scope boundary)

Hand-drawn diagrams (commutative squares, the clock/successor loops, phase sketches) are **out of scope for auto-conversion**. The recognizer must not emit `tikz`/`tikz-cd` for them. Instead: detect the region, crop to PNG, emit a referenced image plus a `% TODO: author diagram` marker.

Rationale + the target a *human* would hand-author later (canonical fixture — the author's `len` monoid-homomorphism square):

```latex
% What a surviving diagram becomes by hand — NOT something the tool should generate.
\[
\begin{tikzcd}
\text{Text}\times\text{Text} \arrow[r, "\mathbin{+}"] \arrow[d, "\mathrm{len}"'] & \text{Text} \arrow[d, "\mathrm{len}"] \\
\mathbb{N}\times\mathbb{N} \arrow[r, "\mathbin{+}"'] & \mathbb{N}
\end{tikzcd}
\]
```

If the tool ever attempts diagram conversion, that's a *separate, later* milestone with its own eval — not part of M0–M2.

---

## 6. The Emitter — where we beat Typora (make this first-class)

This is the answer to "should the tool offer output options?" — **yes**, and the LaTeX target is the whole competitive point, *because* of the two correctness gates below. Treat these as **acceptance criteria, not aspirations.**

### 6.1 Unicode policy (fixes: "bare Unicode with nothing defining it")

Default: **map math Unicode → LaTeX commands** via a lookup table, so output is portable pdfLaTeX-clean ASCII. Only fall back to a Unicode-aware preamble for unmapped glyphs.

Starter map (extend as fixtures demand):

| Glyph | LaTeX | Glyph | LaTeX | Glyph | LaTeX |
|---|---|---|---|---|---|
| ∈ | `\in` | ⊕ | `\oplus` | → | `\to` / `\mapsto` |
| ∉ | `\notin` | ⊗ | `\otimes` | ↦ | `\mapsto` |
| ∅ | `\varnothing` | ∘ | `\circ` | × | `\times` |
| ≠ | `\neq` | ∪ | `\cup` | ≤ | `\leq` |
| ≥ | `\geq` | ∩ | `\cap` | √ | `\sqrt{}` |
| α β … | `\alpha \beta …` | ℕ ℤ ℝ | `\mathbb{N}` etc. | ∀ ∃ | `\forall \exists` |

- **Hard rule:** the LaTeX output must contain **no non-ASCII byte** unless the emitted `--standalone` preamble explicitly supports it (i.e. a `unicode-math`+`fontspec` XeLaTeX/LuaLaTeX preamble, or `newunicodechar` definitions under pdfLaTeX). A grep gate enforces this (see §8).
- `--standalone` emits a known-good preamble the author controls; `--fragment` emits body-only for pasting into an existing manuscript.

### 6.2 Delimiter policy (fixes: "dollar-sign imbalance")

- Prefer `\( … \)` for inline and `\[ … \]` for display; avoid bare `$`/`$$` in LaTeX output. (Markdown target keeps `$`/`$$` since that's what Typora/GitHub render.)
- Validator asserts every opener has its matching closer and that no math mode is left open at end-of-block. Imbalance is a **hard fail**, not a warning.

### 6.3 Personal macros

The Normalizer consults a per-user macro dictionary (seeded empty, grown from corrections): e.g. the author's `S` (successor) → `\mathrm{S}`, `⊕` section marks stripped, recurring shorthands expanded. This dict is the concrete form of "it learns my hand."

### 6.4 Compile gate

Every LaTeX emission is validated by an **actual headless compile with `tectonic`** (self-contained single binary, fetches packages on demand — no system TeX install, reproducible in CI). If it doesn't compile, it doesn't ship; the failure + any auto-fix is written to the Learning Store.

---

## 7. Learning loop, privacy, and what the moat actually is

- **Store** every `(region-image, emitted-LaTeX, human-corrected-LaTeX?)` triple. Human-corrected rows are gold.
- **Local-first by default.** Math notes can be unpublished IP; models and corpus live on the author's box. Any pooling of corrections across users is **strictly opt-in**.
- **Be honest about the moat:** personalization is *retention / switching cost*, not acquisition — a brand-new user has no corpus and gets only the generic VLM pass on day one. Therefore **the zero-data experience must already be good enough to be worth correcting**, or users bounce before learning kicks in. The generic pass + a fast, satisfying correction UI *are* the acquisition surface.

---

## 8. Milestones & acceptance criteria

### M0 — Walking skeleton (CLI only, no UI, no MCP, no learning)
`inkwell convert notes.pdf --target latex --standalone` → `.tex`; `--target markdown` → `.md`.
Pipeline: rasterize → segment → VLM recognize → normalize → validate → emit.
**Done when, on the provided fixtures:**
- [ ] Emitted `.tex` compiles under `tectonic` with **zero errors**.
- [ ] **Zero non-ASCII bytes** in LaTeX output unless a supporting preamble is emitted (`! grep -P '[^\x00-\x7F]' out.tex` passes, or preamble check passes).
- [ ] Delimiter validator passes (balanced; no open math mode).
- [ ] Diagram regions are cropped to PNG + referenced + `% TODO` flagged; **no fabricated tikz**.

### M1 — MCP adapter + Learning Store
- [ ] MCP tools exposed: `convert_page`, `convert_document`, `list_targets`.
- [ ] Store persists pairs; Normalizer reads the personal macro dict from the store.
- [ ] Tool-use traces logged per MCP call.

### M2 — Human correction UI (the data flywheel)
- [ ] Side-by-side: page image ⟷ editable, live-rendered LaTeX (KaTeX/MathJax preview).
- [ ] "Accept" writes a verified pair to the store; edits update the macro dict.
- [ ] Runs against the same engine via the HTTP adapter (no logic in the UI).

### M3 — Later / optional
- [ ] Fine-tune-vs-keep-VLM decision, evaluated on a held-out set of *the author's own* pages.
- [ ] Opt-in pooling. Possible diagram-conversion experiment (separate eval).

---

## 9. Fixtures (use the author's real pages)

Seed the eval set from Naive Math pages already discussed — they stress different structure:

- **SNACKTIME abstraction** — set-builder braces, successor `s(·)`, aligned equations.
- **Peano / von Neumann sets** — deeply nested braces `{ {}, 1, 2 }` (structure torture test).
- **`fold` pseudocode** — mixed prose + `←` assignment + inline math.
- **`len` commutative square** — the diagram **passthrough** test (must crop+flag, not convert).
- **Gibbs sketch (later)** — axes + curved regions (diagram passthrough).

Eval metric: compile-success rate + human-edit-distance to correct LaTeX on these pages. Not CROHME.

---

## 10. Open decisions (leave as flags, don't silently pick)

1. **Default VLM provider** — local Qwen2.5-VL (privacy) vs frontier (quality). Swappable; default local.
2. **Segmenter** — heuristic vs small model. Start heuristic; escalate only if fixtures demand.
3. **UI build** — custom minimal HTML/React vs wrapping an existing MCP-UI CLI. Prefer minimal-custom so the correction event is fully instrumented.
4. **Macro-dict schema & seeding** — how personal macros are proposed from repeated corrections.
