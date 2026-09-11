# Formal checking (Lean) — Decision Document

**Version:** 1.0
**Date:** 2026-09-11
**Status:** Scope decided; spike not started. Third in the order (`../in-app-ingestion/DECISION.md` D2)
**Inputs:** the author's ask (below) · `m0-walking-skeleton/DESIGN.md` §5.5.3 · this machine's
toolchain, surveyed 2026-09-11

---

## The ask (author, 2026-09-11)

> we can probably wire in formal verification at any time in our work -- although lean is going
> to generally want more steps shown than is typical in most text books

## D1. Statements, not proofs

The author's point decides the scope. A textbook proof omits steps Lean requires, and supplying
them is authorship, not transcription — constraint #5b, *never silently add*, applied to proofs.
So the check **formalizes a claim's statement and leaves its proof as `sorry`**, then asks two
questions: does the statement type-check, and can it be refuted?

## D2. It is a one-sided detector, and must read as one

| outcome | what it means |
|---|---|
| refuted (it type-checks, and its negation is proved by `decide` / `norm_num` / `simp`, or `plausible` finds a counterexample) | **flag** |
| type-checks, not refuted | **proves nothing** |
| does not type-check, or could not be formalized | **not checked** — never a pass (§5.7) |

**Corrected 2026-09-11: "does not type-check" is not a flag.** The first version of this table
flagged it, on the example of `A \leq A \neq A`. But a 2026 evaluation of the three open 7B
statement autoformalizers (Kimina, Herald, DeepSeek-Prover-V2) found their output *compiles*
only **11–24%** of the time on undergraduate statements (ProofNet#), the failures dominated by
Mathlib names that do not exist (34–50% of failures) and malformed syntax. When the formalizer's
own output fails three times in four, a compile failure says almost nothing about the page —
and a gate that flagged it would bury the author in false alarms. Only *refuted* survives as a
flag. Ill-typedness may yet become a signal *differentially* — the same formalizer compiling the
page's neighbouring statements but not this one — which is unmeasured.

A statement that type-checks has not been verified, because the formalizer is itself a model
translating text, and can substitute exactly as the recognizer does. §5.5.3's constraint also
binds: a claim whose context is not explicit on the page is not checkable (`6 × 9 = 42` is true
in base 13, declared three lines up). The formalizer must not supply a context the author did
not state; a claim it cannot formalize without one is *not checked*.

## D3. One Lean project per HandZoo install — not one per manuscript

- **Disk.** Mathlib is several GB of prebuilt files, and Lake does not share packages between
  projects. Ten manuscripts would be ten copies.
- **Consistency.** Mathlib renames and moves lemmas between releases, and the formalizer's
  output depends on the version. Projects per manuscript could check two chapters against
  different Mathlibs and disagree for reasons that have nothing to do with the notes.
- **Nothing needs it.** An author's own definitions go at the top of each statement file.

What that means for distribution:

- **elan is the only prerequisite.** The project's `lean-toolchain` names the Lean version and
  elan fetches it; users never choose one.
- **A HandZoo release pins one Mathlib tag and one toolchain.** Upgrading Mathlib is a release
  decision, tested, not a user action.
- **The installed project lives in the user's data directory** (e.g. `~/.local/share/handzoo/lean/`),
  not inside the Python package — an upgrade replaces the package and would take the download
  with it. The app compares its pinned tag with what is installed and prompts a re-setup when
  they differ.
- **Statement files are manuscript content.** They go in the run's output directory, never the
  repository, and are checked with the shared environment (`lake env lean <file>`).
- **Optional.** Without Lean the gate reports *not checked*. Nobody installs several GB of
  Mathlib to convert notes.
- **The helper script** installs elan, then runs something like `handzoo lean setup`: create the
  project, `lake exe cache get`. That downloads prebuilt files and sends nothing, so local-first
  holds.

## This machine, surveyed 2026-09-11

- Lean **4.33.1** (elan default, `stable`), Lake 5.0.0; a 4.33.0 toolchain is also installed.
- **No Mathlib, no Batteries, no Lake project** anywhere on disk.
- Mathlib has a **`v4.33.1` tag — an exact match**. Its master already pins `v4.34.0-rc2`, so
  the project pins the tag, never master.

## The spike

The test cases already exist: the two substitutions found in real runs rather than injected.

| page | emitted | on the page |
|---|---|---|
| Leinster 1.1 p10 (§11.2.7) | `A \leq A \neq A` | `A \leq A \quad \forall A` |
| ch18 p13 (§11.0.1a) | `\Rightarrow` | `Sps` |

The question: **does formalizing the emitted line expose a defect that formalizing the correct
line does not?** The first should — reflexivity became a contradiction. The second may be
invisible: *suppose P* and *P ⇒ Q* can formalize to the same implication, and finding that out
is what the spike is for.

**Open:**

- **The formalizer.** Researched 2026-09-11, none yet run: **Kimina-Autoformalizer-7B** (trained
  on competition-style problems, not textbook prose); **DeepSeek-Prover-V2-7B**, which has
  quantized GGUF builds that load in Ollama; **Goedel-Prover-V2** is the strongest open *prover*
  at that size, but proves rather than translates. Expect the compile rate above, so most
  statements will come back *not checked*: the spike measures reach as much as detection.
  Sources: arXiv 2604.23135 (*Characterizing Paraphrase-Induced Failures in Lean 4
  Autoformalization*); huggingface.co/AI-MO/Kimina-Autoformalizer-7B;
  huggingface.co/unsloth/DeepSeek-Prover-V2-7B-GGUF.
- **Throughput.** Loading Mathlib takes seconds per process; a page with a dozen claims likely
  wants a persistent Lean process (the community `repl`). A harness decision, not a reason to
  split projects.
