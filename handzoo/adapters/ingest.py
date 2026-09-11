"""Ingesting a PDF from the review surface: one background run per project.

**The author does not wait for the run** (DESIGN, in-app ingestion D1). `pipeline.convert`
yields page by page and appends the manifest as it goes, and the surface reads the manifest
on every request -- so a page is reviewable the moment it lands, and the wait shrinks to the
first page. This module only has to run `convert` on a thread and say where it is.

Local only: the recognizer is Ollama. The cloud providers exist on the CLI, where they
announce themselves on every run (D4); the surface does not offer them.

Adapter code -- threads and a recognizer built from config. The pipeline stays ignorant of it.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..core import pipeline, project, rasterize
from ..core.assemble import assemble
from ..core.recognize.base import Recognition, Recognizer

SOURCE_DIR = "source"
MAX_UPLOAD = 1 << 30
"""1 GiB. The largest real export is under 2 MB; this bounds a mistake, not a manuscript."""


class IngestError(ValueError):
    """A refusal the surface reports to the author as it is worded."""


def source_pdf(out_dir: Path) -> Path | None:
    """The PDF this project was ingested from, kept with it so an interrupted run can resume
    and the page count survives a restart."""
    folder = out_dir / SOURCE_DIR
    hits = sorted(folder.glob("*.pdf")) if folder.is_dir() else []
    return hits[0] if hits else None


def _free_name(folder: Path, name: str, data: bytes) -> Path:
    """`name`, or `name-2`, `-3`... -- never overwriting a different file of the same name.
    A second export of one notebook usually arrives with the first one's filename."""
    dest = folder / name
    n = 2
    while dest.exists() and dest.read_bytes() != data:
        dest = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return dest


def store_upload(out_dir: Path, name: str, data: bytes) -> tuple[Path, int]:
    """Keep an uploaded PDF inside the project. Returns where it went and its page count.

    The one place a client-supplied string becomes a filesystem path, so the name is reduced
    to its last component and the result is checked to sit in the project's source folder.
    Refused by content, not by name: `notes.pdf` holding anything else is still not a PDF.
    """
    if not data.startswith(b"%PDF-"):
        raise IngestError("that file is not a PDF -- it does not begin the way every PDF does")
    safe = Path(name or "").name.strip() or "source.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    folder = out_dir / SOURCE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    dest = _free_name(folder, safe, data)
    if dest.resolve().parent != folder.resolve():
        raise IngestError(f"refusing to write {name!r} outside the project")
    dest.write_bytes(data)
    try:
        return dest, rasterize.page_count(dest)
    except rasterize.RasterizeError as exc:
        dest.unlink(missing_ok=True)
        raise IngestError(f"that PDF could not be read: {exc}") from exc


def _page_number(image: Path) -> int | None:
    # Rasterized pages are named p-<page>-<n>.png.
    parts = image.stem.split("-")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


class _Watched:
    """Passes recognition through, noting which page is being read right now."""

    def __init__(self, inner: Recognizer, ingest: Ingest) -> None:
        self.inner, self.ingest = inner, ingest

    def recognize(self, image: Path) -> Recognition:
        self.ingest.current = _page_number(image)
        return self.inner.recognize(image)


class Ingest:
    """One project's background run: its state, and the thread doing it.

    State lives in memory and dies with the server. That is acceptable because nothing is
    lost with it: every page that landed is in the manifest, and the source PDF kept with the
    project says how many there are, so a restarted surface still shows the pages a run did
    not reach -- as unrecognized, with resume available.
    """

    def __init__(self, out_dir: Path, *, recognizer: Recognizer | None = None) -> None:
        self.out_dir = out_dir
        self._recognizer = recognizer
        self.state = "idle"          # idle | running | done | stopped | failed
        self.total: int | None = None
        self.current: int | None = None
        self.error = ""
        self.note = ""
        self.exclude: set[int] = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"

    def status(self) -> dict:
        # "stopping" is reported, not stored: stop is honoured between pages, and a runaway
        # page has taken over two minutes. Until the page in hand lands the run is still going,
        # and the surface must say it heard the request rather than keep offering Stop.
        state = "stopping" if self.running and self._stop.is_set() else self.state
        return {"state": state, "total": self.total, "current": self.current,
                "error": self.error, "note": self.note}

    def start(self, pdf: Path, *, exclude: set[int] | None = None, resume: bool = False,
              append: dict | None = None) -> None:
        """Run `pdf` into the project. `append` is a plan from `project.begin_append`: the
        file's pages go after the project's last page, and the run is recorded as an import
        that can be undone."""
        with self._lock:
            if self.running:
                raise IngestError("a run is already running in this project -- stop it first")
            self.total = append["pages"][1] if append else rasterize.page_count(pdf)
            self.state, self.current, self.error = "running", None, ""
            self.exclude = set(append["exclude"]) if append else set(exclude or ())
            self._stop.clear()
            self._thread = threading.Thread(target=self._run,
                                            args=(pdf, self.exclude, resume, append),
                                            name="handzoo-ingest", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        # Honoured between pages: the page being read finishes and is kept.
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _build_recognizer(self) -> Recognizer:
        if self._recognizer is not None:
            return self._recognizer
        from ..core.lexicon import discover
        from ..core.recognize.ollama_vlm import DEFAULT_MODEL, OllamaRecognizer
        lexicon = discover(self.out_dir)
        # Announced for the same reason the CLI announces it: it changes the prompt, and two
        # runs with different prompts are not comparable unless something says so.
        self.note = (f"lexicon: {len(lexicon.tokens)} author shorthand(s) named to the "
                     "recognizer -- tokens only, never their meanings" if lexicon else "")
        return OllamaRecognizer(model=DEFAULT_MODEL, lexicon_tokens=lexicon.tokens)

    def _run(self, pdf: Path, exclude: set[int], resume: bool, append: dict | None) -> None:
        try:
            recognizer = _Watched(self._build_recognizer(), self)
            where = ({"first": append["first"], "last": append["last"],
                      "offset": append["offset"]} if append else {})
            for _ in pipeline.convert(pdf, self.out_dir, recognizer, exclude=exclude,
                                      resume=resume, **where):
                if self._stop.is_set():
                    break
            # An import is recorded once it has read every page; a stopped one stays pending,
            # to be resumed -- or finished as it stands and undone.
            if append and not self._stop.is_set():
                project.finish_import(self.out_dir)
            # The chapter is the run as it stands -- every page, not only this run's.
            assemble(self.out_dir, pipeline.read_manifest(self.out_dir))
            self.state = "stopped" if self._stop.is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - a run that dies must say so, not vanish
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
        finally:
            self.current = None
