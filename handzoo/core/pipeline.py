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

import hashlib
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
    page_hash: str = ""
    """sha256 of the page render this text was written against.

    Not identity -- DESIGN 11.1.3a settled that the author asserts correspondence and we never
    infer it. This answers the one question hashing *can* answer: has the page changed since we
    read it? A protected page is kept on a re-run, and without this the author is never told
    that the thing their correction describes has moved. Defaulted, so manifests written before
    it existed still load; an empty hash means *not checked*, never *unchanged*."""
    render_dpi: int = 0
    """The resolution `page_hash` was taken at. A render at another DPI is a different image of
    the same page, so comparing across them would report every page as changed -- and a warning
    that fires on everything is one the author learns to scroll past. 0 means unrecorded."""

    @property
    def pdf_page(self) -> int:
        return self.source_page or self.page

    @property
    def done(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class Kept:
    """A page a run did not touch because it carries the author's work, and what became of the
    source underneath it.

    `drift` is one of `changed`, `unchanged`, `unknown`. The third is not a tidy default: a
    page recorded before `page_hash` existed, or read at a different DPI, cannot be compared,
    and saying so is the difference between this being information and being a lie (#6).
    """

    page: int
    why: str
    """The correction verdict that protects it -- `edited`, `authored`, `keep-reviewed`..."""
    drift: str


def page_digest(image: Path) -> str:
    """The render's sha256. One definition, because two would compare unequal forever."""
    return hashlib.sha256(image.read_bytes()).hexdigest()


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
            # A page belongs to the project it is read from, not to the directory it was
            # written in. `output` is stored absolute, so a project that was copied, renamed
            # or moved used to hand every reader a path to somewhere else -- measured, and
            # the way it fails is the worst available: a copy made to experiment in writes
            # through to the original, silently, because the file at the old path is still
            # there and still writable.
            #
            # Deciding it here is the same argument this function's docstring already makes
            # about stale rows. Four readers took `Path(output).name` and looked inside the
            # project they were given; the rest trusted the string. One of those two groups
            # was wrong, and it was not the one doing extra work.
            #
            # `source` is deliberately untouched: the PDF really does live outside the
            # project, and DESIGN 8.1 says an absolute path is right for it.
            #
            # Resolved, not merely joined: `handzoo-ui out/` hands this a relative directory,
            # and a relative row would then depend on the caller's working directory -- a
            # second way for the same string to mean two places.
            if row.output:
                row = replace(row, output=str(out_dir.resolve() / Path(row.output).name))
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
            offset: int = 0, target=None,
            on_page: Callable[[PageOutcome], None] | None = None,
            on_kept: Callable[[Kept], None] | None = None) -> Iterator[PageOutcome]:
    """Convert a page range, yielding each outcome as it completes.

    A generator rather than a list: the caller sees page 1 before page 51 is attempted, which
    is what makes a long run interruptible and a bad prompt cheap to discover.

    `on_kept` hears about the pages this run did *not* touch because they carry author work.
    They are not yielded -- nothing happened to them, and a manifest row would say something
    did -- but the run is the only place that knows whether the source moved under them, since
    it holds the fresh render and the recorded one at the same moment.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if target is None:
        from .target import Target
        target = Target.load(out_dir)
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

    recorded = {o.page: o for o in read_manifest(out_dir)}

    def announce_kept(n: int, image: Path) -> None:
        """What became of the source under a page we are about to skip.

        This is the only moment it can be asked. The run re-renders into the project, so by
        the time anyone looks afterwards the stored image *is* the new one and the old render
        is gone -- the comparison has to happen here, against what the manifest recorded.
        """
        if on_kept is None:
            return
        was = recorded.get(n)
        if was is None or not was.page_hash or was.render_dpi != dpi:
            drift = "unknown"          # cannot compare; must not read as unchanged (#6)
        else:
            drift = "unchanged" if was.page_hash == page_digest(image) else "changed"
        on_kept(Kept(page=n, why=protected_pages(out_dir).get(n, ""), drift=drift))

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
        if kept(page.number):
            announce_kept(page.number, page.image)
            continue
        if page.number in already:
            continue

        try:
            recognition = recognizer.recognize(page.image)
        except RecognitionError as exc:
            outcome = PageOutcome(page=page.number, output=None, verdict="fail",
                                  gates={}, error=str(exc), source=str(pdf),
                                  source_page=src_page(file_page),
                                  page_hash=page_digest(page.image), render_dpi=dpi)
            run.record(outcome)
            if on_page:
                on_page(outcome)
            yield outcome
            continue

        emission = _validate(recognition, pdf, file_page, mode=mode, out_dir=out_dir,
                             target=target,
                             page_number=page.number)
        if kept(page.number):
            # The author started on this page while the model was reading it. Their work
            # wins; this recognition is discarded, and nothing is recorded that would point
            # the manifest away from the file they are editing.
            continue
        dest = out_dir / f"page-{page.number:04d}.tex"
        # A failing page is still written, but under a name a build cannot pick up by
        # accident. Discarding it would throw away the very thing a human needs to correct.
        # Unverified is NOT quarantined: nothing refused it.
        if emission.failed:
            dest = dest.with_suffix(".fail.tex")
        dest.write_text(emission.text, encoding="utf-8")

        outcome = PageOutcome(
            page=page.number,
            output=str(dest),
            verdict=emission.verdict,
            gates={g.gate: ("pass" if g.passed else "skipped" if not g.checked else "fail")
                   for g in emission.gates},
            rules=len(emission.rules),
            source=str(pdf),
            source_page=src_page(file_page),
            # What this text was written against. Recorded on every page, because which page
            # will later carry a correction is not knowable now.
            page_hash=page_digest(page.image),
            render_dpi=dpi,
            findings=[d for g in emission.gates for d in g.as_dicts()],
        )
        run.record(outcome)
        if on_page:
            on_page(outcome)
        yield outcome


def _validate(recognition: Recognition, pdf: Path, page: int, *, mode: str,
              out_dir: Path | None = None, page_number: int | None = None,
              target=None) -> Emission:
    """`page` is the page *of the PDF* -- what the ink and colour gates read. `page_number` is
    its number in the project, which is what the emitted provenance names."""
    engine = compile_gate.ENGINE if target is None else target.engine
    draft = emit(recognition, mode=mode, page=page_number or page, base_dir=out_dir,
                 target=target)
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
        ascii_gate.check(draft.text, fragment=(mode != "standalone"),
                         accepts_unicode=bool(target and target.accepts_unicode)),
        delimiter_gate.check(draft.text),
        compile_gate.check(draft.text, base_dir=out_dir, engine=engine) if mode == "standalone"
        else compile_gate.check_fragment(
            draft.text, preamble=chapter_preamble([draft.text], target=target),
            base_dir=out_dir, engine=engine),
        coverage_gate.check(draft.text, recognition.inventory, ink=ink,
                            inventory_failed=recognition.inventory_failed),
        colour_gate.check(draft.text, colours=colours),
        reference_gate.check(draft.text),
        repetition_gate.check(draft.text),
        pasted_gate.check(_pasted_count(pdf, page), regions=_pasted_regions(pdf, page)),
    )
    # Reuse the draft rather than normalising a second time. Deterministic today, but
    # nothing enforced that the two runs agreed, and a divergence would have meant the
    # gates judged text the caller never receives.
    return replace(draft, gates=gates)


def _pasted_regions(pdf: Path, page: int) -> tuple[dict[str, float], ...]:
    """Where the pasted images are, or nothing when the tool could not look."""
    try:
        return rasterize.pasted_regions(pdf, page)
    except rasterize.RasterizeError:
        return ()


def _pasted_count(pdf: Path, page: int) -> int | None:
    """Images pasted onto the page, or None when the tool could not look."""
    try:
        return rasterize.embedded_images(pdf, page)
    except rasterize.RasterizeError:
        return None


