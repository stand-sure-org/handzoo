**Version:** 1.0
**Date:** 2026-08-18
**Type:** Design-stage Delphi deliberation
**Gate result:** Approve with conditions (4 of 5), one reject — conditions are binding

# Delphi Panel Review: HandZoo M0 Design

**Panel:** 5 personas, all completed.
**Input:** `DESIGN.md` v1.0, `DECISION.md` v1.0, `baseline/` artifacts.
**Two adjudicating experiments were run during the review** — see "Empirical adjudication" below. Both changed the outcome.

## Verdicts

| Persona | Verdict | One-line position |
|---|---|---|
| Structural | Approve with conditions | Shape is sound; §3 and §5.4 rest on an untested capability |
| Operational | Approve with conditions | Buildable today; fix golden-test nondeterminism and measure the model tradeoff |
| Value / Skeptic | **Reject as scoped** | Baseline sharpened the earlier "no": gates go green on confidently wrong output |
| Divergent | Approve with conditions | D6 is real progress but discharges only half the objection |
| Usability | Approve with conditions | Moved off "unsure"; a validator with no way to act on it is a linter, not a product |

---

## Consensus Finding 1 — D6 catches omission; the demonstrated failure was substitution

**Raised independently by Structural, Value and Divergent. The most important finding of the review.**

D6 and DESIGN §5.4 fail a `Region{kind: "mark"}` that has no corresponding marker in the output. But baseline page 1 did not *omit* the glyphs and leave a hole — it **folded them into prose**. "HAS MORE 🧍 THAN THE OTHERS" became "has more than the others." Divergent stated the gap precisely:

> D6 converts silent *loss* into loud loss. It leaves silent *substitution* exactly as silent as it was.

Value went further and questioned whether the gate catches its own motivating example at all:

> A model that silently *resolves* ambiguity, rather than *omitting* it, produces a clean inventory and a clean gate pass. "Refuses to hand you broken LaTeX" is true only for two failure modes out of the three the baseline produced.

Structural added the sharpest technical objection: the baseline prompt in `recognize.py` **never asked for a region inventory**. `Recognition.regions` was untested surface, and D6's central claim had zero evidence behind it — *"if that capability doesn't hold under real prompting, the fourth gate is vaporware."*

### Empirical adjudication — experiment 1

The objection was testable, so it was tested. A **separate inventory pass** was run against baseline page 1, asking only for non-text marks and their sentence context, with no transcription.

The model returned all ten marks, including every one the transcription pass had silently folded away:

```json
{"description":"two people drawing for Greater Than symbol","context":"Greater Than >","inline_or_block":"inline","count":1}
{"description":"one person drawing for Equal symbol","context":"Equal =","inline_or_block":"inline","count":1}
```

**Verdict: the objection is upheld against the design as written, and resolved by a change to it.**

- Structural is **correct** that a `regions` field returned *from the same call* is near-worthless — that call has already decided to drop the glyph.
- The mechanism **does work as an independent second pass.** Because the inventory call is not conditioned on the transcription call's choices, it surfaced exactly what transcription discarded.
- Value is **partially correct**: the inventory's *descriptions* are unreliable (it read a dog glyph as "a stick figure lying down," and the tally/dog marks as "two people"). Its *counts and placements* were right.

That split is the design constraint: **the inventory pass is trustworthy about where marks are, and untrustworthy about what they are.** A coverage gate built only on presence and position is sound. Anything built on the descriptions is not.

**Binding condition:** D6's mechanism changes from "recognizer returns regions alongside markup" to **two independent passes, cross-checked.** Cost: one extra VLM call per page (~106s measured).

## Consensus Finding 2 — the correction loop belongs in M0

**Usability decisively, Value concurring, and this is the condition on which both hinge.**

Usability moved off "unsure" specifically because the baseline proved every page needs adjudication, and that two distinct failure classes need two distinct actions — accept-a-marker for page 1's dropped glyphs, reject-and-flag for page 2's `|||| < ||||`.

> Approving M0 as scoped ships a validator with no way to *act* on what it validates. That's not a product, it's a linter.

The proposed minimum affordance requires no web stack:

```
handzoo review <page>
```

A line-at-a-time terminal walk. For each mark region and each gate failure: show the source crop coordinates, the emitted line, prompt `[k]eep / [e]dit / [f]lag / [s]kip`. `e` opens `$EDITOR` at that line. Every decision appends `{region, original, correction, verdict}` to `.handzoo/corrections.jsonl`. No Learning Store, no HTTP — a flat append log that is the flywheel's first turn.

Value's independent framing: without it, M0's deliverable is *"a `.tex` file that compiles and looks audited but that the author cannot trust without re-reading the source page"* — the unchanged manual loop, now with four gates producing false confidence on top.

**Binding condition:** `handzoo review` enters M0 scope. It is cheaper than the coverage gate and closes a larger gap.

## Consensus Finding 3 — green gates on wrong output is the new villain

**Divergent and Usability converged; Value stated it as the reject rationale.**

Divergent named the second-order consequence of the D2 positioning:

> A guardian that says PASS trains the user to stop checking exactly the content it doesn't check. Hand-transcription keeps a human wary the whole time; a PASS banner invites relaxation precisely where the recognizer confabulates most confidently.

Usability, from the adoption angle, reached the same place: *"retyping doesn't lie to you with a green checkmark first."*

Divergent also flagged a documentation honesty problem: DECISION.md states the unqualified slogan in the document that sets positioning, while the qualification — "does not catch semantic falsity" — lives four sections later in DESIGN §5.4. **The louder claim ships before the hedge.**

**Binding conditions:**
1. The qualification moves into DECISION.md D2 itself, not a downstream section.
2. The CLI never prints an unqualified PASS. A passing page reports what was *not* checked.
3. Add a confidence/provenance marker (Divergent §3) — the inventory pass is already being built for D6, so low-confidence spans are nearly free to surface.

## Decision-by-decision verdicts

| # | Decision | Verdict | Notes |
|---|---|---|---|
| D1 | Full M0, not a spike | Approve | Author's call; measurement already taken during spec |
| D2 | Validator/guardian positioning | **Approve with condition** | Must carry its own qualification — see Finding 3 |
| D3 | Python end-to-end | Unanimous approve | No persona contested it |
| D4 | No heuristic segmenter | Unanimous approve | Baseline supports; VLM recovers reading order free |
| D5 | `qwen3-vl:8b` + uncapped generation | Approve | Model-size tradeoff to be measured, not assumed |
| D6 | No-silent-loss gate | **Approve, mechanism revised** | Two independent passes — Finding 1 |
| D7 | Fixtures never tracked | Unanimous approve | — |
| D8 | Handoff as provenance | Approve | — |

## Answers to the design questions

**Q1 — intermediate AST?** *(Structural)* **No full document AST for M0.** Page 1's tables, itemize and section structure survived the LaTeX round-trip intact; the compile gate caught a syntax bug, not block-structure loss. Everything silently lost lived **inline**. Adopt a minimal inline annotation layer over the existing markup string instead:

```
Inline = Text(str)
       | Mark(kind, description, placement)
       | Tally(count: int)          # cannot drift to \mathrm{IV} the way a string can
       | ColorSpan(color, inlines)
```

A full block AST is justified later only by target-divergent normalization, or by M1 corrections needing to address a node rather than a string offset.

**Q2 — is the coverage gate worth building?** Yes, **as revised**. Structural's recommendation stands on the residual risk: make the raster ink-density cross-check the hard signal (it is the only one not sourced from a VLM at all), and treat inventory matching as the primary check now that the two-pass experiment shows it works.

**Q3 — 138s/page.** Test specified by Operational, **now running**: `qwen3-vl:4b` against both baseline pages. Decision rule — if 4b's failure catalogue is no worse and wall-clock drops below ~70s, 4b becomes the M0 default; if 4b invents *more* structure (smaller models often compensate for uncertainty by confabulating, which is fatal for this project's thesis), keep 8b. Do not test `:2b` first. Results appended to DESIGN.md §8 when complete.

Usability's independent condition regardless of model: **streaming per-page output, `--resume` from a manifest, and `--pages 1-5`.** Operational named the same gap as the top operational risk — no checkpointing means a crash on page 40 of 51 loses everything.

**Q4 — correction loop in M0?** **Yes.** Finding 2.

**Q5 — colour.** *(Divergent)* Discarding it silently violates the same principle D6 encodes. DECISION.md names colour as load-bearing but it appears in no decision D1–D8 — *"the shape of a decision that looks made but isn't."* Minimum: colour-bearing ink is treated as a mark; its silent loss is a hard fail under the extended gate, even if faithful reconstruction is deferred.

## Cuts — where the panel agreed to remove scope

| Cut | Raised by | Rationale |
|---|---|---|
| `tectonic`/`pdflatex` dual-backend selection (§5.3) | Structural | Speculative generality for a compiler not installed. Hardcode `pdflatex`. |
| Macro dictionary as loadable mechanism (§4) | Structural, Value | Hardcode the two known substitutions. Build the dictionary when a corpus exists. |
| Provider registry `recognize/__init__.py` (§3) | Value | One provider. Build the second recognizer before the seam for it. |
| `--target markdown` (§6) | Value | The deliverable is LaTeX; no evidence Markdown is needed. |

Structural endorsed keeping the AST import-boundary test — *"~20 lines, cheap insurance"* — and Operational concurred: *"ports-and-adapters at this size is a boundary, not an architecture."*

## The dissent, recorded

**Value rejects M0 as scoped** and did not move on the baseline evidence. The demand attached to that reject is concrete and unmet:

> Human minutes to correct emitted `.tex` to ground truth vs. human minutes to transcribe the page from a blank file — both timed on the same two baseline pages, by the actual author. If correction time ≥ transcription time, the tool has negative value regardless of how green the gates are.

This is the one open question no experiment in this review answered, and it cannot be answered without the author. **It is the M0 exit criterion**, not a milestone-3 nicety. Edit-distance alone is insufficient: catching `|||| < ||||` requires re-reading the source page, which is most of the transcription cost, and edit-distance scores that diff as small.

## Binding conditions on DESIGN v1.1

1. D6 mechanism → two independent passes, cross-checked (Finding 1).
2. `handzoo review` enters M0 scope (Finding 2).
3. D2's qualification moves into D2; the CLI never prints unqualified PASS (Finding 3).
4. Confidence/provenance markers on emitted output.
5. Colour promoted from open question to decision.
6. Golden tests feed frozen `.tex` bytes into gate functions; never invoke the recognizer in CI.
7. Streaming output, `--resume`, `--pages N-M`.
8. Cuts applied per the table above.
9. Author-timed correct-vs-retype measurement adopted as the M0 exit criterion.
