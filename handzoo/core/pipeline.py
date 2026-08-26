"""The only orchestrator: PDF in, per-page documents and verdicts out.

Three properties are load-bearing, and all three come from measurement rather than taste.

**Sequential.** Concurrent requests to one Ollama instance starve each other — a competing
client hit a socket timeout despite a thirty-minute deadline. Parallelism buys nothing here
and converts a slow run into a failed one.

**Streamed, not batched.** Each page is written the moment it is done. A run of a hundred
pages that reveals its work only at the end is a run you cannot interrupt, inspect, or trust.

**Resumable.** With recognition at seconds-to-minutes per page, a crash on page 40 of 51 must
not discard the first 39. The manifest is the record that makes `--resume` possible, and it
is written after every page, not at the end.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from . import rasterize
from .emit import Emission, emit
from .recognize.base import Recognition, Recognizer
from .recognize.ollama_vlm import RecognitionError
from .validate import (ascii_gate, colour_gate, compile_gate, coverage_gate,
                       delimiter_gate, reference_gate, repetition_gate)

MANIFEST = "manifest.jsonl"


@dataclass(frozen=True, slots=True)
class PageOutcome:
    page: int
    output: str | None
    verdict: str
    """pass | unverified | fail. See `Emission.verdict` for why two states are not enough."""
    gates: dict[str, str]
    error: str | None = None
    rules: int = 0
    findings: list[dict] = field(default_factory=list)
    """Gate findings, persisted so `handzoo review` can route a human to specific lines.
    Re-deriving them would mean re-running the recognizer, since the coverage gate needs an
    inventory that exists only during the run."""
    source: str = ""
    """The PDF this page came from. The crop verdict cannot cut a region without it, so this
    is the first provenance field (DESIGN 8.1) to become load-bearing rather than merely
    desirable. Defaulted so manifests written before it existed still load.

    An absolute path is acceptable *here*: the manifest is the run's local record, not the
    shareable artifact. 8.1's hash-and-purgable-sidecar rule governs what goes into the
    emitted `.tex`, which is the file that travels."""

    @property
    def done(self) -> bool:
        return self.error is None


@dataclass
class Run:
    """State of one conversion, persisted so it can be picked up again."""

    out_dir: Path
    outcomes: list[PageOutcome] = field(default_factory=list)

    @property
    def manifest_path(self) -> Path:
        return self.out_dir / MANIFEST

    def completed_pages(self) -> set[int]:
        return {o.page for o in self.outcomes if o.done}

    def load(self) -> Run:
        if self.manifest_path.exists():
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.outcomes.append(PageOutcome(**json.loads(line)))
        return self

    def record(self, outcome: PageOutcome) -> None:
        self.outcomes.append(outcome)
        # Appended per page, not at the end: a manifest written only on success is no
        # manifest at all, since the case it exists for is the run that did not finish.
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(outcome)) + "\n")


def convert(pdf: Path, out_dir: Path, recognizer: Recognizer, *,
            first: int = 1, last: int | None = None, mode: str = "fragment",
            resume: bool = False, dpi: int = rasterize.DEFAULT_DPI,
            on_page: Callable[[PageOutcome], None] | None = None) -> Iterator[PageOutcome]:
    """Convert a page range, yielding each outcome as it completes.

    A generator rather than a list: the caller sees page 1 before page 51 is attempted, which
    is what makes a long run interruptible and a bad prompt cheap to discover.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    run = Run(out_dir).load() if resume else Run(out_dir)
    already = run.completed_pages() if resume else set()

    pages = rasterize.rasterize(pdf, out_dir / "pages", first=first, last=last, dpi=dpi)

    for page in pages:
        if page.number in already:
            continue

        try:
            recognition = recognizer.recognize(page.image)
        except RecognitionError as exc:
            outcome = PageOutcome(page=page.number, output=None, verdict="fail",
                                  gates={}, error=str(exc))
            run.record(outcome)
            if on_page:
                on_page(outcome)
            yield outcome
            continue

        emission = _validate(recognition, pdf, page.number, mode=mode,
                             out_dir=out_dir)
        target = out_dir / f"page-{page.number:04d}.tex"
        # A failing page is still written, but under a name a build cannot pick up by
        # accident. Discarding it would throw away the very thing a human needs to correct.
        # Unverified is NOT quarantined: nothing refused it.
        if emission.failed:
            target = target.with_suffix(".fail.tex")
        target.write_text(emission.text, encoding="utf-8")

        outcome = PageOutcome(
            page=page.number,
            output=str(target),
            verdict=emission.verdict,
            gates={g.gate: ("pass" if g.passed else "skipped" if not g.checked else "fail")
                   for g in emission.gates},
            rules=len(emission.rules),
            source=str(pdf),
            findings=[
                {"gate": g.gate, "detail": f.detail, "line": f.line, "excerpt": f.excerpt}
                for g in emission.gates for f in g.failures
            ],
        )
        run.record(outcome)
        if on_page:
            on_page(outcome)
        yield outcome


def _validate(recognition: Recognition, pdf: Path, page: int, *, mode: str,
              out_dir: Path | None = None) -> Emission:
    draft = emit(recognition, mode=mode, page=page, base_dir=out_dir)
    try:
        ink = rasterize.ink_profile(pdf, page)
    except rasterize.RasterizeError:
        ink = None
    try:
        colours = rasterize.ink_colours(pdf, page)
    except rasterize.RasterizeError:
        # Could not ask, so the colour gate must not answer. None is "unknown", not "clean".
        colours = None
    gates = (
        ascii_gate.check(draft.text, fragment=(mode != "standalone")),
        delimiter_gate.check(draft.text),
        compile_gate.check(draft.text, base_dir=out_dir) if mode == "standalone"
        else _skip_compile(),
        coverage_gate.check(draft.text, recognition.inventory, ink=ink,
                            inventory_failed=recognition.inventory_failed),
        colour_gate.check(draft.text, colours=colours),
        reference_gate.check(draft.text),
        repetition_gate.check(draft.text),
    )
    # Reuse the draft rather than normalising a second time. Deterministic today, but
    # nothing enforced that the two runs agreed, and a divergence would have meant the
    # gates judged text the caller never receives.
    return replace(draft, gates=gates)


def _skip_compile():
    """A fragment has no preamble, so compiling it in isolation proves nothing.

    Reported as unverified rather than passed — the distinction is the whole point of
    `GateResult.checked`.
    """
    from .validate.base import GateResult
    return GateResult(compile_gate.GATE, checked=False)
