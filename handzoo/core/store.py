"""The project store: immutable objects, append-only snapshots, and two tiny pointers.

DESIGN, in-app ingestion D5. Modelled on Snowflake / Iceberg snapshots and on the author's
standing preference -- *append, never annihilate*:

- `objects/` holds every page text, page render and source PDF ever captured, named by the
  SHA-256 of its bytes. Written once, never modified, never deleted.
- `snapshots/NNNN.json` records the project at one moment: each page's newest manifest row,
  and the hashes of its text and render. Snapshots are only ever added.
- `HEAD` names the newest snapshot.
- `extent` is the number of pages the project has *now*. It is what an import raises and what
  undoing an import lowers -- the one pointer that decides whether page 23 exists, so no reader
  has to learn a new verdict and the manifest stays a plain log (`pipeline.read_manifest`
  applies it, in that one place).

The store only ever writes inside `.store/`. Adopting an existing project reads its files and
changes none of them.

No dependency on the pipeline: rows are dicts here, so the pipeline can read `extent` without a
cycle.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

STORE = ".store"


class StoreError(RuntimeError):
    """The store is not in a state an operation can safely proceed from."""


def exists(out_dir: Path) -> bool:
    return (out_dir / STORE).is_dir()


def extent(out_dir: Path) -> int | None:
    """How many pages the project has, or None if it has no store (every page exists)."""
    path = out_dir / STORE / "extent"
    if not path.exists():
        return None
    return int(path.read_text(encoding="utf-8").strip())


def set_extent(out_dir: Path, pages: int) -> None:
    _write_atomic(out_dir / STORE / "extent", f"{pages}\n")


def born(out_dir: Path) -> dict[int, float]:
    """When each page number was last created by an append. Page numbers are reused after an
    undo: page 23 of a later import is not page 23 of the undone one. Correction rows and edit
    snapshots older than a page's birth are about content that has left the project, and the
    state readers (`corrections.protected_pages`, the surface's page list) ignore them. The log
    itself is untouched -- they remain history."""
    path = out_dir / STORE / "born.json"
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def mark_born(out_dir: Path, pages: range, at: float) -> None:
    births = born(out_dir)
    births.update({n: at for n in pages})
    _write_atomic(out_dir / STORE / "born.json",
                  json.dumps({str(k): v for k, v in sorted(births.items())}) + "\n")


def put(out_dir: Path, data: bytes) -> str:
    """Store bytes once, by content. Returns their hash."""
    digest = hashlib.sha256(data).hexdigest()
    path = _object(out_dir, digest)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return digest


def get(out_dir: Path, digest: str) -> bytes:
    data = _object(out_dir, digest).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        # An object that no longer matches its name is damage, and must never be served as
        # the author's text.
        raise StoreError(f"object {digest[:12]} does not match its hash")
    return data


def head(out_dir: Path) -> str | None:
    path = out_dir / STORE / "HEAD"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def read_snapshot(out_dir: Path, snap: str) -> dict:
    return json.loads((out_dir / STORE / "snapshots" / f"{snap}.json").read_text(encoding="utf-8"))


def snapshots(out_dir: Path) -> list[str]:
    folder = out_dir / STORE / "snapshots"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def write_snapshot(out_dir: Path, doc: dict) -> str:
    """Add a snapshot and move HEAD to it. Never overwrites an existing snapshot."""
    folder = out_dir / STORE / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    snap = f"{len(snapshots(out_dir)) + 1:04d}"
    path = folder / f"{snap}.json"
    if path.exists():
        raise StoreError(f"snapshot {snap} already exists -- refusing to overwrite history")
    doc = {**doc, "id": snap, "parent": head(out_dir), "at": time.time()}
    _write_atomic(path, json.dumps(doc, indent=1, sort_keys=True) + "\n")
    _write_atomic(out_dir / STORE / "HEAD", snap + "\n")
    return snap


def _object(out_dir: Path, digest: str) -> Path:
    return out_dir / STORE / "objects" / digest[:2] / digest


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
