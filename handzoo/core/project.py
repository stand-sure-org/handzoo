"""Importing into a project that already has pages: capture, plan, append, undo.

DESIGN, in-app ingestion D5. The operations are few on purpose. Pages are only ever *appended*
(this module) or replaced from a chosen page (not built yet), so a page's number never shifts
under it -- the correction log, keyed by page number, stays pointed at the right page.

**Every change starts with a capture, and a capture checks itself before it counts.** A capture
that silently missed a page would look exactly like one that did not, until the undo that needed
it. So each page's stored text is read back and compared with the file, and every page carrying
the author's work must be in the snapshot -- or the import is refused before anything runs.

**The hash is a hint, never identity** (the author doubts a reMarkable update will keep page
bytes stable). `plan` only suggests; the author chooses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import pipeline, rasterize, store
from .corrections import protected_pages

PENDING = "pending.json"


def recipe() -> dict:
    """How a page render was made. A different poppler or DPI makes every page look new --
    safe, but slow -- so each snapshot says which recipe its hashes came from."""
    try:
        out = subprocess.run([rasterize.RASTERIZER, "-v"], capture_output=True, text=True,
                             check=False)
        version = (out.stderr or out.stdout).splitlines()[0].strip()
    except (OSError, IndexError):
        version = "unknown"
    return {"renderer": version, "dpi": rasterize.DEFAULT_DPI}


def page_image(out_dir: Path, page: int) -> Path | None:
    hits = sorted((out_dir / "pages").glob(f"p-{page:04d}*.png"))
    return hits[0] if hits else None


# ------------------------------------------------------------------------------ capture


def capture(out_dir: Path, action: dict) -> str:
    """Snapshot the project as it stands. Reads the working files; writes only `.store/`.

    The first capture of an existing project is its adoption, and changes none of its files.
    """
    rows = pipeline.read_manifest(out_dir)
    entries = []
    for o in rows:
        entry = {"page": o.page, "row": asdict(o), "text": None, "output": None,
                 "image": None, "image_name": None}
        if o.output and Path(o.output).exists():
            entry["text"] = store.put(out_dir, Path(o.output).read_bytes())
            entry["output"] = Path(o.output).name
        if (img := page_image(out_dir, o.page)) is not None:
            entry["image"] = store.put(out_dir, img.read_bytes())
            entry["image_name"] = img.name
        entries.append(entry)
    pages = store.extent(out_dir)
    if pages is None:
        pages = max((o.page for o in rows), default=0)
    doc = {"action": action, "extent": pages, "recipe": recipe(), "pages": entries}
    _verify(out_dir, rows, doc)
    snap = store.write_snapshot(out_dir, doc)
    if store.extent(out_dir) is None:
        store.set_extent(out_dir, pages)
    return snap


def _verify(out_dir: Path, rows: list, doc: dict) -> None:
    by_page = {e["page"]: e for e in doc["pages"]}
    for e in doc["pages"]:
        if e["text"] is None:
            continue
        on_disk = Path(e["row"]["output"]).read_bytes()
        if store.get(out_dir, e["text"]) != on_disk:
            raise store.StoreError(f"page {e['page']}: the stored text does not match the file")
    missing = sorted(n for n in protected_pages(out_dir)
                     if n <= doc["extent"] and (n not in by_page or by_page[n]["text"] is None))
    if missing:
        raise store.StoreError(
            f"page(s) {', '.join(map(str, missing))} carry your work but could not be "
            "captured -- refusing to go on without a copy of them")


# --------------------------------------------------------------------------------- plan


def plan(project: list[str | None], incoming: list[str]) -> dict:
    """Suggest how an incoming PDF lines up with the project. Pure: two lists of page hashes.

    Measured on six real exports of one notebook (D5): a page's hash depends on the page, not on
    the export -- so a match means the same page, and the most common offset between matches is
    how the file lines up with the project. Each incoming page is then *identical* (same page,
    same place), *changed* (the place exists and holds something else -- an edit), *new* (beyond
    the project's end), or *elsewhere* (present, at another page). With no agreeing offset --
    three hand-picked pages, say -- nothing is aligned and only presence is reported.

    `append_from` is the first file page such that it and everything after it is new: the start
    that appends only what the project lacks. None when there is nothing new to append.
    """
    where: dict[str, int] = {}
    for i, h in enumerate(project):
        if h is not None:
            where.setdefault(h, i + 1)
    votes = Counter(where[h] - (j + 1) for j, h in enumerate(incoming) if h in where)
    offset = None
    if votes:
        best, n = votes.most_common(1)[0]
        # One agreeing pair is a coincidence of position; two is an alignment.
        if n >= 2 or (len(votes) == 1 and n == len(incoming)):
            offset = best
    pages = []
    for j, h in enumerate(incoming):
        entry = {"file_page": j + 1, "status": "unplaced", "project_page": where.get(h)}
        if offset is not None:
            pos = j + 1 + offset
            if pos <= len(project) and project[pos - 1] == h:
                entry = {**entry, "status": "identical", "project_page": pos}
            elif h in where:
                entry["status"] = "elsewhere"
            elif pos <= len(project):
                entry = {**entry, "status": "changed", "project_page": pos}
            else:
                entry = {**entry, "status": "new", "project_page": pos}
        elif h in where:
            entry["status"] = "elsewhere"
        pages.append(entry)

    if offset is None:
        new_from = 1 if not where.keys() & set(incoming) else None
    else:
        new_from = None
        for j in range(len(pages) - 1, -1, -1):
            if pages[j]["status"] != "new":
                break
            new_from = j + 1
    return {"offset": offset, "pages": pages, "append_from": new_from,
            "present": sum(p["status"] in ("identical", "elsewhere") for p in pages),
            "changed": [p for p in pages if p["status"] == "changed"]}


def project_hashes(out_dir: Path) -> list[str | None]:
    """The project's page renders, by page, from its newest snapshot."""
    snap = store.head(out_dir)
    if snap is None:
        return []
    doc = store.read_snapshot(out_dir, snap)
    by_page = {e["page"]: e["image"] for e in doc["pages"]}
    return [by_page.get(n) for n in range(1, doc["extent"] + 1)]


def incoming_hashes(pdf: Path, staging: Path) -> list[str]:
    """Render an incoming PDF the way the project's pages were rendered, and hash each page."""
    import hashlib
    if staging.exists():
        shutil.rmtree(staging)
    pages = rasterize.rasterize(pdf, staging)
    return [hashlib.sha256(p.image.read_bytes()).hexdigest() for p in pages]


# ------------------------------------------------------------------------ append / undo


def begin_append(out_dir: Path, pdf: Path, *, start: int, exclude: set[int] | None = None) -> dict:
    """Capture the project, then make room for the incoming pages. Returns the run's plan.

    `start` and `exclude` are in the *file's* page numbers, as the author sees them; the run
    uses project numbers. File page `start` lands after the project's last page.
    """
    if (out_dir / store.STORE / PENDING).exists():
        raise store.StoreError("an import is already in progress in this project")
    total = rasterize.page_count(pdf)
    if not 1 <= start <= total:
        raise ValueError(f"start page {start} is outside the file's {total} pages")
    before = capture(out_dir, {"kind": "before-import"})
    have = store.extent(out_dir) or 0
    offset = have - (start - 1)
    run = {"pre": before, "source": str(pdf), "source_hash": store.put(out_dir, pdf.read_bytes()),
           "first": start, "last": total, "offset": offset,
           "pages": [have + 1, have + total - start + 1],
           "exclude": sorted(n + offset for n in (exclude or set()) if start <= n <= total)}
    _write_json(out_dir / store.STORE / PENDING, run)
    # These numbers may have held pages an undo removed; what arrives now is not those pages.
    store.mark_born(out_dir, range(run["pages"][0], run["pages"][1] + 1), time.time())
    store.set_extent(out_dir, run["pages"][1])
    return run


def pending(out_dir: Path) -> dict | None:
    path = out_dir / store.STORE / PENDING
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def finish_import(out_dir: Path) -> str:
    """Record the import as its own snapshot -- the one an undo reverts."""
    run = pending(out_dir)
    if run is None:
        raise store.StoreError("no import in progress")
    snap = capture(out_dir, {"kind": "import", "mode": "append", **run})
    (out_dir / store.STORE / PENDING).unlink()
    return snap


def undo_last_import(out_dir: Path) -> dict:
    """Revert what the last import changed -- and only that.

    An append changed nothing but the pages it added, so undoing it lowers `extent` back and
    those pages leave the project. Pages it did not touch keep whatever the author has done to
    them since: rolling back an unrelated correction is not what "undo import" means.

    Nothing is deleted. The project is captured first, so a correction made to an added page
    after the import stays in the store; it is reported, because it is no longer in the
    project. Carrying such corrections back is an open question (D5 pre-mortem 4), left open.
    """
    if pending(out_dir) is not None:
        raise store.StoreError("an import is still in progress -- stop it before undoing")
    target = _last_import(out_dir)
    if target is None:
        raise store.StoreError("there is no import to undo")
    doc = store.read_snapshot(out_dir, target)
    first, last = doc["action"]["pages"]
    since = protected_pages(out_dir)
    capture(out_dir, {"kind": "before-undo", "of": target})
    store.set_extent(out_dir, first - 1)
    after = capture(out_dir, {"kind": "undo", "of": target})
    return {"snapshot": after, "undid": target, "removed": [first, last],
            "kept_in_history": sorted(n for n in since if first <= n <= last)}


def _last_import(out_dir: Path) -> str | None:
    undone = set()
    for snap in reversed(store.snapshots(out_dir)):
        action = store.read_snapshot(out_dir, snap)["action"]
        if action.get("kind") == "undo":
            undone.add(action["of"])
        elif action.get("kind") == "import" and snap not in undone:
            return snap
    return None


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
