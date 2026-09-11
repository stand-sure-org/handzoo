# In-app ingestion — Decision Document

**Version:** 1.0
**Date:** 2026-09-11
**Status:** Scope and preconditions decided in conversation; design follows in this folder
**Inputs:** the author's ask (below) · `m0-walking-skeleton/DESIGN.md` §11.1.3, §11.1.3a, §11.4 ·
the code as it stands at `fdfd8be`

---

## The ask (author, 2026-09-11)

> I keep wanting to ask for ingestion to be in-app (not sure how I want to handle the waiting
> for the model to process pages bit)

Today a run is a terminal command, and `handzoo-ui` is started afterwards on its output. The
ask is to drop a PDF into the surface and have it become pages there.

## D1. The author does not wait for the run — review runs behind recognition

The waiting is real only if ingestion is modelled as a batch that finishes before review
begins. Nothing in the code requires that:

- `pipeline.convert()` is already a generator: it yields each page as it completes, and says
  why in its docstring — *the caller sees page 1 before page 51 is attempted*.
- `Run.record` appends the manifest **per page**, not at the end.
- The UI reads the manifest fresh on every request, so a page that lands is a page it can show.

And the rates favour the reader. One recognition call measured a 4s median on ch16 (a page is
two calls plus the gates); the author's median *correction* is 77.3s (§11.0.1). Recognition
outruns review, so after page 1 the queue is ahead of the author, not behind.

**So the page list shows each page as *queued*, *recognizing*, or *ready*, and page 1 is
reviewable while page 2 is still being recognized.** The wait shrinks to the first page. There
is no batch progress bar, because there is no batch.

## D2. Order of work (author, 2026-09-11)

1. **In-app ingestion**, including the preconditions in D3 — they are part of it, not before it.
2. **Lexicon diagnostic.** Across all corpora no replaced pair recurs on two different pages
   (§11.2.7 records three). Before concluding that more corpus is needed, re-mine the rows
   already on disk with LaTeX-aware tokens — strip trailing punctuation, treat `\cmd{arg}` as
   one token — because a miner that sees `IR,` and `IR` as different strings cannot find a
   repeat whether one exists or not. If it still finds none, ingestion is how the corpus grows.
3. **Formal-check spike** — `../formal-check/DECISION.md`.

## D3. The preconditions are the work; the wiring is not

**Status 2026-09-11: P1–P4 built** (PR #52, 331 tests). **D1 built** — ingestion into a new
project, in the surface (`handzoo-ui <new folder>`).

*Measured with the real model*, on the author's exports in a scratch project:

- 3 pages: page 1 reviewable **22 s** after upload, while pages 2–3 were still waiting; done
  at 37 s. Page 1 landed quarantined — a stray `&` the fragment compile gate (P4) now catches;
  before #52 it would have read *not checked* and broken `chapter.tex`.
- 22 pages with page 4 cut: stop, a server restart, and resume all behaved — the restarted
  surface still listed the 16 pages the run had not reached, and resume re-read none of the
  pages that had landed (one manifest row each).
- **Stop is honoured between pages, and one page took over two minutes**: runaway generation,
  18 KB, caught by the repetition gate. So the surface reports *stopping* until the page in
  hand lands, rather than a Stop that appears to do nothing. Interrupting the page itself needs
  the per-attempt timeout the recognizer notes already call for — not built.

**P1 — One way to read the manifest: newest row per page.** The manifest is a log; a page can
carry several rows (a `--resume`, a re-gate on save). The UI already collapses to the newest.
Two other readers did not, and both were found live while scoping this:

- **`--resume` silently dropped earlier pages from `chapter.tex`.** `cli_convert` assembled only
  the outcomes *this invocation* produced, and a resumed page is skipped, not yielded — so it
  never reached the assembler. The l11 run's chapter began at page 3; pages 1–2 were absent
  with no placeholder. That is the exact omission `assemble()` exists to prevent.
- **`handzoo-review --fix` and `--transcribe` took `match[0]`** — the *oldest* row for the page.
  The same bug the UI fixed after the author's p3 accept failed to register (§11.2.7).

**P2 — A write race that ingestion creates.** The pipeline appends to the manifest; the UI's
quarantine release and crop paths read the whole file and write it back (`_rewrite_manifest`).
A row appended between that read and that write is lost: the page disappears from the list,
and a later `--resume` recognizes it again. Unreachable today only because a run and a review
never overlap — which is precisely what D1 changes.

*Default taken:* **append-only everywhere.** Every write is a new row; P1's reader makes the
newest one win. The manifest is already a log, and this keeps the CLI and the UI writing it the
same way. The alternative — one writer, the server owning the manifest and the pipeline
reporting through `on_page` — is recorded and not chosen. The author can overrule.

**P3 — No silent overwrite of author work.** A re-run overwrites corrections (§11.1.3 bug #2),
and a button turns that into one click. Ingestion **refuses a page that carries a GOLD or
`authored` row** unless the author has said "replace page N" — the explicit-replacement rule
the author already chose (§11.1.3a), now enforced rather than remembered.

*Checked at the moment of writing, not once per run.* The first version read the protected set
before the loop, which is right for the CLI and wrong for this design: the author reviews while
the run is going, and a page they start on after it began — before the run reaches it, or while
the model is reading it — was overwritten. Found in review, not by the tests, which only ran
sequentially; both timings are tested now.

**P4 — Fragments, gated by wrapping.** UI runs should produce fragments, because only fragments
assemble into a chapter; but in fragment mode the compile gate cannot run, and ch17 came back
8 of 13 `unverified` for exactly that reason (§11.4). Wrapping each fragment in the preamble to
compile it — and writing the fragment — removes the choice the `--standalone` flag forces. The
chapter compile stays the stronger check, since it catches cross-page definitions a single page
cannot.

*Found while building it:* there were two preambles. In fragment mode the normalizer declares
nothing, and the master declared only unmapped characters — never the macros the recognizer
invented. A fragment using one built fine as a standalone page and broke `chapter.tex` with
"Undefined control sequence", unseen, because fragments were never compiled.
`normalize.chapter_preamble()` is now the one definition — master, per-page gate, and the UI's
re-gate on save all use it. One function, not one output: the gate gives it one page and the
master every page, so their declarations agree in the ordinary case rather than by
construction, and the chapter compile remains the backstop. On the real corpus (no model called) all 20 l11 fragments and the
3 remaining leinster-cut fragments pass; 19 had read `skipped`, and none is newly refused.

## D4. Constraints carried over unchanged

- **Local by default.** Choosing `--provider gemini` from the UI announces itself exactly as the
  CLI does, and the key is still read only from `$GEMINI_API_KEY`.
- **127.0.0.1 only**; the PDF lands in the project directory, never the repository.
- **`--exclude` at ingest** — a page the author does not own is cut before any model sees it
  (DESIGN §11.2.4), so the choice has to exist *before* the run starts, not after.
- **One ingest at a time per project.**
- A page that errors (Ollama restarted mid-run) is already resumable — the newest row wins — and
  the surface needs a retry for it rather than a re-run of the whole PDF.

## D5. Ingesting into an existing project (author, 2026-09-11)

**Neither a new folder per ingest nor a merge.** A new folder duplicates every page and strands
the author's work in the old one; a merge changes things in place. The store is modelled on
Snowflake / Iceberg snapshots instead, which matches the author's standing preference —
*append, never annihilate*:

- **One project per manuscript.** Pages and their texts (recognized, corrected) are stored once,
  named by content hash, and never modified.
- **Every import writes a snapshot**: the ordered page list, each entry pointing at that page's
  current text. Entries the import did not change are referenced, not copied.
- **A small pointer file marks the current snapshot.** Nothing is deleted; going back is moving
  the pointer.
- **The working folder is a copy of the current snapshot**, so `page-0003.tex` and `chapter.tex`
  keep stable names for any LaTeX editor. A working file whose hash no longer matches the
  snapshot is an edit made outside HandZoo, and is saved as a new version rather than lost —
  closing the gap P3 leaves.

**Ask, don't infer.** Choosing a file for an existing project offers a mode — **Append**
(default) or **Replace pages from…** — and a *start at page* input, default 1.

**The hash is a hint, never identity.** The author has low confidence that a future reMarkable
update will keep page bytes stable across exports. So an exact hash match is used only to skip
re-recognizing an identical page and to *suggest* an action, never to decide one. A broken hash
costs speed and suggestions, never work.

**Measured 2026-09-11** — the author exported one 24-page notebook six ways over eight minutes;
pages compared by a SHA-256 of the 150-DPI render, matched by hash rather than position:

| export | pages | result |
|---|---|---|
| full, then full again 5 min later, unchanged | 24, 24 | **24 of 24 byte-identical** |
| starting at page 2 | 23 | each page identical to the same page of the full export, one position earlier |
| three chosen pages (1, 3, 5) | 3 | 3 of 3 identical to those pages of the full export |
| after editing one page | 24 | **only the edited page differs** — 0.53% of its pixels, in one band; the other 23 identical |
| an earlier export, before two pages were added | 22 | identical to the first 22 of the later one |

So on this device, today: **a page's hash depends on the page, not on the export** — not on its
position in the file, not on which pages accompany it, not on the moment of export. An edit
changes that page's hash and no other. And each hint in the pre-mortem below works on this data:
the grown notebook is found already present for its first 22 pages; the export that starts at
page 2 aligns to the project by hash with no input; the edit is flagged on exactly one page.

What it does not show: one notebook, one device, one firmware, one day. The author's doubt about
a future update stands, so the hash stays a hint. The render is part of the hash recipe — a
poppler upgrade or a DPI change would make every page look new (safe, but slow) — so each
snapshot records the recipe, and the store keeps the source PDFs so both sides can be hashed
again under a new one.

### Pre-mortem

1. **Replace, start at 1** *(author)*. Identical pages are skipped by hash. For the mess case:
   **Undo import** moves the pointer back to the snapshot before it — the whole import, not page
   by page — and the undone import stays in the store.
2. **Replace, start at N ≠ 1** *(author: "the Gordian knot")*. It assumes the device's page
   numbers correspond to the project's, which nothing guarantees; an author who did the work
   recently will know, one who did not may not. *Proposed:* show the correspondence instead of
   assuming it — a preview pairing each project page's ink with the incoming page's, each marked
   *identical*, *changed* or *has your corrections*, which the author confirms or shifts. Hash
   matches, when they exist, propose N; when they do not, the author aligns by eye. The human
   asserts the correspondence, as in §11.1.3a; the tool makes it visible. *Author: "don't know,
   but it seems reasonable"* — adopted as the working design, to be revisited against real use.
3. **Append, start at 1, on a notebook that grew** *(added — likely the commonest mistake)*.
   Every page is appended, most already present. Hash matches catch it ("15 of these are already
   in the project — start at 16?"); if hashes broke, the duplicates are visible in the page list
   and Undo import removes them.
4. **Undo an import after correcting pages since** *(added; author's answer)*. It splits on
   whether the import re-recognized the page:
   - **The page's image changed, so its text was recognized again.** The corrections were made
     to text the earlier snapshot never had. There is no free way to carry them back or
     reconcile them, so they do not return with the undo — they stay in the store, reachable,
     but not in the current version. This is stated, not hidden.
   - **The page's image did not change**, so the import carried its entry over untouched. A
     correction made afterwards is to the same text the earlier snapshot holds, and *might* be
     cherry-picked onto it. A candidate, not a decision — rework behaviour that likely varies
     between authors and needs their feedback.

**Size, roughly:** re-export hash check — done · store, snapshots, pointer 5
· working copy and outside-edit detection 5 · replace preview and import undo 5.
