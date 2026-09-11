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

from . import rasterize, store
from .corrections import protected_pages
from .emit import Emission, emit
from .normalize import chapter_preamble
from .recognize.base import Recognition, Recognizer
from .recognize.ollama_vlm import RecognitionError
from .validate import (ascii_gate, colour_gate, compile_gate, coverage_gate,
                       delimiter_gate, reference_gate, repetition_gate, pasted_gate)

MANIFEST = "manifest.jsonl"


def parse_excluded(spec: str | None) -> set[int]:
    """Pages the author has cut, as `1,4` or `1-3,7`.

    **Refused rather than guessed on anything malformed.** A silently-misread range would
    transcribe a page the author meant to cut, which is the one outcome this exists to
    prevent — and on the corpus that motivated it (DESIGN 11.2.4) that page is someone else's
    published writing.
    """
    if not spec:
        return set()
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            if not (lo.strip().isdigit() and hi.strip().isdigit()):
                raise ValueError(f"cannot read {part!r} as a page range")
            a, b = int(lo), int(hi)
            if a > b:
                raise ValueError(f"range {part!r} counts backwards")
            out.update(range(a, b + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError(f"cannot read {part!r} as a page number")
    return out


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
    source_page: int | None = None
    """This page's number *in `source`*, when it differs from its number in the project -- a
    page appended from a second PDF. Anything that reads the source (the crop tool, the ink and
    colour gates) must use `pdf_page`, or it cuts the right region from the wrong page."""

    @property
    def pdf_page(self) -> int:
        return self.source_page or self.page

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
        self.outcomes.extend(read_manifest(self.out_dir))
        return self

    def record(self, outcome: PageOutcome) -> None:
        self.outcomes.append(outcome)
        # Appended per page, not at the end: a manifest written only on success is no
        # manifest at all, since the case it exists for is the run that did not finish.
        append_manifest(self.out_dir, outcome)


def append_manifest(out_dir: Path, outcome: PageOutcome) -> None:
    """The only way anything writes the manifest: one new row, appended, in one write.

    Nothing rewrites a row. A run and a save from the surface can both be writing, and a
    writer that reads the file, changes a row and writes it all back drops whatever the other
    appended in between. Appending cannot, and `read_manifest` makes the newest row win. The
    row goes in a single write because an O_APPEND write lands whole; two writes per row would
    let another writer's row in between them.
    """
    with (out_dir / MANIFEST).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(outcome)) + "\n")


def read_manifest(out_dir: Path) -> list[PageOutcome]:
    """The run as it stands: the newest row for each page, in page order.

    The manifest is append-only, so a page can carry several rows -- `--resume` leaves the
    original failure and the retry after it; a re-gate on save adds another. The log is right to
    keep them all, since it records what happened. A reader must take the newest, and every
    reader must take it the same way: two did not, and each served a stale row (DESIGN, in-app
    ingestion D3 P1). No manifest reads as no pages -- a run may not have written one yet.
    """
    path = out_dir / MANIFEST
    if not path.exists():
        return []
    latest: dict[int, PageOutcome] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = PageOutcome(**json.loads(line))
            latest[row.page] = row
    # A project with a store has an extent: the pages it has *now*. Undoing an append lowers
    # it, and the pages beyond it have left -- decided here, once, so no reader has to learn
    # what a removed page looks like (DESIGN, in-app ingestion D5).
    limit = store.extent(out_dir)
    return [latest[k] for k in sorted(latest) if limit is None or k <= limit]


def convert(pdf: Path, out_dir: Path, recognizer: Recognizer, *,
            first: int = 1, last: int | None = None, mode: str = "fragment",
            resume: bool = False, dpi: int = rasterize.DEFAULT_DPI,
            exclude: set[int] | None = None, replacing: set[int] | None = None,
            offset: int = 0,
            on_page: Callable[[PageOutcome], None] | None = None) -> Iterator[PageOutcome]:
    """Convert a page range, yielding each outcome as it completes.

    A generator rather than a list: the caller sees page 1 before page 51 is attempted, which
    is what makes a long run interruptible and a bad prompt cheap to discover.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    run = Run(out_dir).load() if resume else Run(out_dir)
    already = run.completed_pages() if resume else set()
    replaced = replacing or set()

    def kept(n: int) -> bool:
        # Author work is never overwritten by a run -- only by the author saying so. Enforced
        # here rather than in each caller, so an adapter that forgets to check still cannot
        # destroy a correction; adapters only *announce* what was kept (DESIGN 11.1.3 bug #2,
        # 11.1.3a). Read each time, not once per run: with ingestion in the surface the author
        # reviews while the run is still going, and a page they start on after it began is
        # theirs by the time the run reaches it. One log read, next to a model call.
        return n not in replaced and n in protected_pages(out_dir)

    # `first`, `last` and the rasterized numbers are the *file's*; everything recorded -- the
    # manifest, the output name, `exclude`, `replacing`, protection -- uses the project's.
    # They differ only for an append (`offset`), which renders aside and places each page at
    # its project number.
    if offset:
        placed = []
        for page in rasterize.rasterize(pdf, out_dir / ".incoming", first=first, last=last,
                                        dpi=dpi):
            n = page.number + offset
            for stale in (out_dir / "pages").glob(f"p-{n:04d}*.png"):
                stale.unlink()   # a render of a page that left; it is in the store
            dest = out_dir / "pages" / f"p-{n:04d}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            page.image.replace(dest)
            placed.append((n, page.number, dest))
    else:
        placed = [(p.number, p.number, p.image) for p in
                  rasterize.rasterize(pdf, out_dir / "pages", first=first, last=last, dpi=dpi)]
    cut = exclude or set()
    src_page = (lambda s: s) if offset else (lambda s: None)

    for number, file_page, image in placed:
        page = rasterize.Page(number=number, image=image)
        if page.number in cut:
            # Recorded, and never sent to a model. The point is not tidier output: it is that
            # no transcription of the page is produced at all, which is what the author wants
            # for a page that is not theirs to reproduce (DESIGN 11.2.4).
            outcome = PageOutcome(page=page.number, output=None, verdict="excluded",
                                  gates={}, source=str(pdf), source_page=src_page(file_page))
            run.record(outcome)
            if on_page:
                on_page(outcome)
            yield outcome
            continue
        if page.number in already or kept(page.number):
            continue

        try:
            recognition = recognizer.recognize(page.image)
        except RecognitionError as exc:
            outcome = PageOutcome(page=page.number, output=None, verdict="fail",
                                  gates={}, error=str(exc), source=str(pdf),
                                  source_page=src_page(file_page))
            run.record(outcome)
            if on_page:
                on_page(outcome)
            yield outcome
            continue

        emission = _validate(recognition, pdf, file_page, mode=mode, out_dir=out_dir,
                             page_number=page.number)
        if kept(page.number):
            # The author started on this page while the model was reading it. Their work
            # wins; this recognition is discarded, and nothing is recorded that would point
            # the manifest away from the file they are editing.
            continue
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
            source_page=src_page(file_page),
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
              out_dir: Path | None = None, page_number: int | None = None) -> Emission:
    """`page` is the page *of the PDF* -- what the ink and colour gates read. `page_number` is
    its number in the project, which is what the emitted provenance names."""
    draft = emit(recognition, mode=mode, page=page_number or page, base_dir=out_dir)
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
        else compile_gate.check_fragment(draft.text, preamble=chapter_preamble([draft.text]),
                                         base_dir=out_dir),
        coverage_gate.check(draft.text, recognition.inventory, ink=ink,
                            inventory_failed=recognition.inventory_failed),
        colour_gate.check(draft.text, colours=colours),
        reference_gate.check(draft.text),
        repetition_gate.check(draft.text),
        pasted_gate.check(_pasted_count(pdf, page)),
    )
    # Reuse the draft rather than normalising a second time. Deterministic today, but
    # nothing enforced that the two runs agreed, and a divergence would have meant the
    # gates judged text the caller never receives.
    return replace(draft, gates=gates)


def _pasted_count(pdf: Path, page: int) -> int | None:
    """Images pasted onto the page, or None when the tool could not look."""
    try:
        return rasterize.embedded_images(pdf, page)
    except rasterize.RasterizeError:
        return None


