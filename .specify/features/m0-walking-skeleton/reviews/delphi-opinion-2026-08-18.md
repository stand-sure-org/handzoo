**Version:** 1.0
**Date:** 2026-08-18
**Type:** Pre-spec opinion phase (speckit-opinion, 7 personas)
**Gate result:** **STOP — reframe before writing SPEC**

# Delphi Opinion Poll: HandZoo M0 Walking Skeleton

**Panel:** 7 personas, all completed.
**Input:** `inkwell-handoff.md` (design brief), `CLAUDE.md`, verified environment state.
**Problem statement put to the panel:** *The author wants to stop re-transcribing handwritten math notes by hand, by building a local-first pipeline that converts reMarkable PDFs into compilable LaTeX/Markdown, with a correction loop that builds a personal corpus over time. M0 is a CLI-only walking skeleton gated on tectonic compile, zero non-ASCII, balanced delimiters, and diagram passthrough.*

## Verdict tally — "Is this the right problem to solve?"

| Persona | Lens | Verdict |
|---|---|---|
| Structural | Technical Architect | Yes |
| Value | Skeptic / Contrarian | **No / unsure** |
| Operational | Pragmatist | Yes |
| Temporal | Five Whys | **No — wrong-problem flag** |
| Usability | Design Thinker | **Unsure** |
| Viability | SB7 / StoryBrand | Yes |
| Divergent | Kantian | Yes |

4 of 7 agree. Per the opinion-phase rubric, *"4 or fewer agree → STOP, surface divergence"* **and** *"any wrong-problem flag → STOP, reframe before proceeding."* Both conditions fired. The spec was not written.

Note the shape of the dissent: nobody argues the transcription tax is imaginary. The disagreement is entirely about **whether M0 as specified is the right first move**, and it is unanimous among dissenters that the design is committed to unmeasured assumptions.

---

## Consensus Finding 1 — the spec is designed against imagined input

**Raised independently by Operational, Value, Viability, and Usability.** The highest-priority issue.

Operational put it flatly: *"a stack of unknowns pretending to be a spec."* Every downstream design choice — segmenter heuristics, recognizer strategy, macro-dictionary schema — is being made against input nobody has run through anything.

Verified against the actual machine, not assumed:

| Assumption in the brief | Reality |
|---|---|
| Fixture pages drive eval | **No fixture PDFs exist.** Named in the brief, never supplied. |
| `tectonic` is the compile gate | **Not installed.** But MacTeX *is* — `pdflatex`, `xelatex`, `lualatex`, `latexmk` all present. |
| Local Qwen2.5-VL via Ollama is the default recognizer | **No vision model is pulled.** Ollama holds `glm-4.7-flash`, `llama3.2`, `mistral`, `qwen3:4b`, `nomic-embed-text`, `x/z-image-turbo` — none of them a VLM that can do this job. |
| `pdftoppm` rasterizes reMarkable exports acceptably | Binary present; output quality on real ink **never tested**. |

The default recognizer — the backbone of the entire pipeline — does not exist on this box. That single fact reframes M0 from "build the skeleton" to "find out whether the premise holds."

Value's sharpening: the brief itself concedes *"the zero-data experience must already be good enough to be worth correcting, or users bounce."* That is a **testable claim that has never been tested**, and every milestone after it is contingent on it being true.

## Consensus Finding 2 — cut the heuristic segmenter from M0

**Structural and Operational converged on this independently, from opposite directions.**

Operational ranked the segmenter the **hidden tarpit** — not a safe default: *"text and inline math interleave on the same line, and diagram vs. equation boundaries are not clean rectangles."*

Structural identified the deeper reason — the Recognizer/Normalizer seam is drawn in the wrong place:

> Structural correctness (sub/superscript nesting, fraction stacking, matrix layout) is a property of the *recognizer's* output, not something the Normalizer can fix post-hoc from Unicode soup — so "swap the recognizer, keep the Normalizer" only works if every candidate recognizer emits structurally-correct LaTeX already, which pix2tex/texify/TrOCR do NOT.

The swappable-strategy abstraction is therefore weaker than the brief assumes: what is stable across recognizer swaps is much less than a clean `recognize(image) -> raw_markup` interface implies.

Both proposed the same fix: **let the VLM bbox + classify + transcribe in one pass.** Add a dedicated segmenter only when you can point at specific failures it would fix. This also preserves the diagram-passthrough requirement — the VLM marks diagram regions, the pipeline crops and flags them.

Structural's additional note, worth carrying into DESIGN: LaTeX-as-intermediate conflates the recognition target with the emit target, forcing the Normalizer to do semantic correction and syntax rendering through one string format. An intermediate AST/MathML is the alternative.

## Consensus Finding 3 — M0 produces no data and no reason to run twice

**Usability and Value, with Viability supporting.**

The brief states the correction UI *"is the acquisition surface"* and *"the data flywheel"* — then ships it **third**, after two milestones with no correction path at all. Usability called this a sequencing error and the brief self-contradictory.

The concrete day-one loop for M0 as specified:

> Run the CLI → get a `.tex` plus cropped diagram PNGs and `% TODO` comments → open it in an editor → fix delimiter and recognition errors by hand → retype diagram descriptions from scratch. *That is the manual transcription loop they already do today, with an extra compile-and-diff step and no record of what was corrected.*

Usability's conclusion: *"tolerable once out of curiosity, not twice — there's no reward for a second run because nothing got easier or was remembered."*

Value's corollary: a corpus is only an asset if something consumes it. Building learning-store infrastructure before proving the day-one output clears the bar is investment in a flywheel with no first turn.

---

## Divergence: the wrong-problem flag

**Temporal, alone but specific, and the reason this poll stopped rather than proceeded with caveats.**

The five-whys chain bottoms out somewhere the brief never examines:

1. Re-transcription is needed because VLM/Mathpix/Typora output is unreliable.
2. Output is unreliable because the capture format (reMarkable ink) carries no symbol or structure metadata — recognition is guesswork after the fact.
3. Ink-first capture was chosen for flow and distraction-free thinking, not for downstream typesetting.
4. LaTeX output is needed because the real deliverable is a **book manuscript**, not archived ink.
5. Direct structured capture (typing with macros, or live-recognition pen input) **was never tested** — it was assumed worse for first-principles thinking, without evidence.

> "The problem is upstream of *conversion is bad* — it's *I picked a capture method that structurally cannot produce what I need downstream.*"

Temporal's sharpest point is about incentive alignment, not technology: *"the corpus is the asset" rewards continuing to write by hand indefinitely to keep feeding the dataset, entrenching the bottleneck rather than resolving it.*

**Panel synthesis on this flag:** the dissent and the majority converge on the same next action. A baseline measurement on one real page settles it either way — strong zero-data output vindicates ink capture and the whole pipeline; weak output makes the capture question live before any infrastructure is built. The flag does not require abandoning the project; it requires not skipping the measurement.

---

## Unique insights by persona

**Viability — reposition from recognizer to guardian.** The strongest single reframe in the panel:

> "The tool that refuses to hand you broken LaTeX" is a sharper story than "OCR for math," and it's the one differentiator Mathpix doesn't structurally have — they optimize for recognition accuracy, not hard-fail compile gates.

Named villain: **silent corruption** — tools that emit plausible-looking output that quietly breaks and costs more to debug than hand-transcription would have saved. This preserves the entire Emitter/Validator investment while fixing the story. Viability also flagged the hero mismatch: the spec is written for a corpus-of-one, but the program context implies a second hero with zero corpus whose day-one experience is admitted to be the generic VLM pass — *"the same experience Mathpix already sells."*

**Divergent — the never-fabricate rule is applied once, and it should be applied everywhere.** The compile gate catches syntax; it **never catches semantically wrong but well-typeset math**. That is the same failure mode as fabricated `tikz`, just uncaught. The principle should extend to: (a) glyph substitutions the VLM guesses under uncertainty — no silent best-guess; (b) the opt-in frontier-VLM "quality" path, which hallucinates more confidently; (c) macro-dictionary application that could silently rewrite meaning rather than notation. This is a **gap in the M0 acceptance criteria**, not a philosophical aside.

Divergent also separated architecture from governance on pooling: local-first + opt-in gets the architecture right, but *"nothing described prevents a future release from quietly changing the default"* — and Apache-2.0 licenses the code while saying nothing about the corpus's fate.

**Viability — branding before code.** Blunt: a trademark policy, license, slogan and three logo SVGs exist before a line of source. *"Displacement activity — it produces artifacts, but none of them reduce the actual risk."* Recorded as panel opinion; the artifacts are cheap and already sunk.

**Operational — smallest honest definition of M0 done:** one committed sample handwritten-math page (a phone photo of a notebook page, clearly labeled as a stand-in, if no reMarkable export is available) run end-to-end, producing a `.tex` that compiles with zero errors under **whatever LaTeX engine is verified working this week** — not necessarily `tectonic`. No unmeasured accuracy claims.

**Value — alternatives worth naming:** buy into an existing Snip/Overleaf workflow and ship the book faster than M0 ships a CLI; or, if the VLM pass is genuinely passable, run it as a one-shot script with manual cleanup and skip the segmentation/validation/macro machinery until it is proven necessary.

---

## Recommended reframe

Replace M0-as-briefed with a measurement spike. Everything else in the brief survives; it just stops being built on an untested premise.

**M0-spike — "does the zero-data pass clear the bar?"**

1. Acquire one real page of the author's handwritten math (reMarkable export preferred; a photo is an acceptable labeled stand-in).
2. Pull an actual vision model (`qwen2.5vl`) — currently absent.
3. Single VLM pass over the whole page: bbox + classify + transcribe. **No segmenter stage.**
4. Normalize (Unicode→LaTeX map, delimiter policy) → validate (balance + compile via installed MacTeX; adopt `tectonic` later for CI reproducibility) → emit.
5. **Measure:** compile success, and human edit-distance from emitted LaTeX to correct LaTeX.

That number is the gate. It decides whether the pipeline is worth building, whether the correction UI must move ahead of the MCP adapter, and whether Temporal's capture question needs answering — none of which can be decided from the brief alone.

## Open items carried into DECISION.md

1. First-increment scope — spike vs. full M0 as briefed.
2. Positioning — recognizer vs. validator/guardian ("refuses to hand you broken LaTeX").
3. Correction-path sequencing — does a minimal accept/reject loop move into the first increment?
4. Engine language/runtime — still undecided; every fallback recognizer in the brief is Python.
5. Extending the never-fabricate rule beyond diagrams to uncertain glyphs and macro application.
6. Corpus governance as a technical invariant, not a policy statement.
