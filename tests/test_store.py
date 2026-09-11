"""The project store, and appending a second PDF to a project (DESIGN, in-app ingestion D5).

The author's corpora hold the only copies of their corrections, so the tests that matter most
are the ones about what is *not* written: adoption reads and never writes, a capture that missed
a page refuses rather than proceeding, and undo removes only what the import added.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from handzoo.core import pipeline, project, rasterize, store
from handzoo.core.corrections import Correction, CorrectionLog, current, protected_pages
from handzoo.core.recognize.base import Recognition

pytestmark = pytest.mark.skipif(
    not (shutil.which("pdftoppm") and shutil.which("pdflatex") and shutil.which("pdftocairo")),
    reason="poppler and/or pdflatex not installed")


def _pdf(folder: Path, name: str, lines: list[tuple[int, int]]) -> Path:
    """One page per line, each a stroke of a different shape -- so every page renders and
    crops differently, and reading the wrong one shows."""
    body = "\\newpage ".join(f"\\tikz\\draw (0,0)--({x},{y});" for x, y in lines)
    (folder / f"{name}.tex").write_text(
        "\\documentclass{article}\\usepackage{tikz}\\pagestyle{empty}\\begin{document}"
        f"{body}\\end{{document}}\n", encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", f"{name}.tex"], cwd=folder,
                   capture_output=True, check=False)
    return folder / f"{name}.pdf"


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("pdfs")
    return {"a": _pdf(tmp, "a", [(1, 2), (4, 1), (6, 5)]),
            "b": _pdf(tmp, "b", [(2, 3), (5, 2), (3, 7), (7, 1)])}


class Stub:
    def __init__(self) -> None:
        self.seen: list[Path] = []

    def recognize(self, image: Path) -> Recognition:
        self.seen.append(image)
        return Recognition(markup=f"Text of {image.stem}.", inventory=(), provider="stub",
                           model="stub")


def _project(tmp_path: Path, pdf: Path) -> Path:
    out = tmp_path / "project"
    list(pipeline.convert(pdf, out, Stub()))
    return out


def _tree(out: Path) -> dict[str, str]:
    """Every file outside the store, by content."""
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file() and store.STORE not in p.parts}


def _correct(out: Path, page: int, text: str, verdict: str = "edited") -> None:
    row = next(o for o in pipeline.read_manifest(out) if o.page == page)
    Path(row.output).write_text(text, encoding="utf-8")
    CorrectionLog.for_run(out).append(Correction(
        page=page, verdict=verdict, source_image="p.png", before="emitted", after=text))


def _append(out: Path, pdf: Path, start: int) -> dict:
    run = project.begin_append(out, pdf, start=start)
    list(pipeline.convert(pdf, out, Stub(), first=run["first"], last=run["last"],
                          offset=run["offset"], exclude=set(run["exclude"])))
    project.finish_import(out)
    return run


# ------------------------------------------------------------------------------ adoption


def test_adopting_a_project_writes_nothing_outside_the_store(tmp_path, pdfs) -> None:
    """The corpora hold the only copies of 88 corrected rows and every GOLD verdict. Adoption
    reads them and writes only `.store/`."""
    out = _project(tmp_path, pdfs["a"])
    _correct(out, 2, "the author's words\n")
    before = _tree(out)

    snap = project.capture(out, {"kind": "adopt"})

    assert _tree(out) == before, "adoption changed a working file"
    assert store.head(out) == snap and store.extent(out) == 3
    entry = next(e for e in store.read_snapshot(out, snap)["pages"] if e["page"] == 2)
    assert store.get(out, entry["text"]) == b"the author's words\n"


def test_a_capture_that_cannot_copy_a_corrected_page_refuses(tmp_path, pdfs) -> None:
    """A capture that silently missed a page is indistinguishable from a good one until the
    undo that needed it. So it refuses, and writes no snapshot."""
    out = _project(tmp_path, pdfs["a"])
    _correct(out, 2, "mine\n")
    (out / "page-0002.tex").unlink()

    with pytest.raises(store.StoreError, match="carry your work"):
        project.capture(out, {"kind": "adopt"})
    assert store.snapshots(out) == []


def test_a_capture_whose_copy_does_not_read_back_refuses(tmp_path, pdfs, monkeypatch) -> None:
    out = _project(tmp_path, pdfs["a"])
    monkeypatch.setattr(store, "get", lambda d, h: b"not what was written")
    with pytest.raises(store.StoreError, match="does not match"):
        project.capture(out, {"kind": "adopt"})
    assert store.snapshots(out) == []


# ------------------------------------------------------------------ plan: the real exports


def _letters(s: str) -> list[str]:
    return s.split()


A_TO_V = _letters("A B C D E F G H I J K L M N O P Q R S T U V")


@pytest.mark.parametrize("case,project_pages,incoming,expect", [
    # The notebook grew by two pages between exports: append only those.
    ("grown", A_TO_V, A_TO_V + ["W", "X"],
     {"offset": 0, "append_from": 23, "present": 22, "changed": []}),
    # The same notebook exported starting at page 2: it lines up one page late, by itself.
    ("from page 2", A_TO_V, A_TO_V[1:] + ["W", "Y"],
     {"offset": 1, "append_from": 22, "present": 21, "changed": []}),
    # One page edited on the tablet: nothing new to append, and page 24 has changed.
    ("edited", A_TO_V + ["W", "Y"], A_TO_V + ["W", "X"],
     {"offset": 0, "append_from": None, "present": 23, "changed": [24]}),
    # Three hand-picked pages: all present, and no single alignment.
    ("specific", A_TO_V, ["A", "C", "E"],
     {"offset": None, "append_from": None, "present": 3, "changed": []}),
    # A notebook the project has never seen: append it all.
    ("unrelated", A_TO_V, ["Z1", "Z2"],
     {"offset": None, "append_from": 1, "present": 0, "changed": []}),
])
def test_the_plan_matches_what_the_six_real_exports_showed(case, project_pages, incoming,
                                                          expect) -> None:
    p = project.plan(project_pages, incoming)
    assert p["offset"] == expect["offset"], case
    assert p["append_from"] == expect["append_from"], case
    assert p["present"] == expect["present"], case
    assert [c["project_page"] for c in p["changed"]] == expect["changed"], case


# --------------------------------------------------------------------------------- append


def test_an_append_lands_after_the_last_page_and_remembers_its_source_page(tmp_path,
                                                                           pdfs) -> None:
    out = _project(tmp_path, pdfs["a"])
    _append(out, pdfs["b"], start=2)

    rows = {o.page: o for o in pipeline.read_manifest(out)}
    assert sorted(rows) == [1, 2, 3, 4, 5, 6]
    assert [rows[n].pdf_page for n in (4, 5, 6)] == [2, 3, 4]
    assert rows[1].source_page is None, "the first PDF's pages are their own numbers"
    assert project.page_image(out, 4) is not None
    assert store.extent(out) == 6


def test_an_append_never_rewrites_a_page_it_did_not_add(tmp_path, pdfs) -> None:
    out = _project(tmp_path, pdfs["a"])
    _correct(out, 2, "corrected before the import\n")
    before = {n: (out / f"page-{n:04d}.tex").read_bytes() for n in (1, 2, 3)}
    _append(out, pdfs["b"], start=1)
    assert {n: (out / f"page-{n:04d}.tex").read_bytes() for n in (1, 2, 3)} == before


def test_the_crop_tool_reads_the_source_page_not_the_project_page(tmp_path, pdfs) -> None:
    """An appended page is page 5 of the project and page 3 of its PDF. Cutting page 5 of
    that PDF would return the right-looking band from the wrong page -- silent, and easy to
    mistake for a bad recognition."""
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    from handzoo.adapters.ui_server import Handler, configure

    out = _project(tmp_path, pdfs["a"])
    _append(out, pdfs["b"], start=2)
    configure(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/regions?page=5") as r:
            got = [g["points"] for g in json.loads(r.read())["regions"]]
    finally:
        srv.shutdown()
        srv.server_close()
    right = [b.region for b in rasterize.page_blocks(pdfs["b"], 3)]
    wrong = [b.region for b in rasterize.page_blocks(pdfs["b"], 4)]
    assert right != wrong, "the fixture must tell the pages apart"
    assert got == right


# ----------------------------------------------------------------------------------- undo


def test_undo_removes_what_the_import_added_and_nothing_else(tmp_path, pdfs) -> None:
    """A correction made after the import to a page it did not touch is not part of the import,
    and undoing the import does not roll it back. A correction to a page the import *added*
    leaves the project with that page -- and is kept in the store, and reported."""
    out = _project(tmp_path, pdfs["a"])
    _append(out, pdfs["b"], start=2)
    _correct(out, 1, "fixed page 1 after the import\n")
    _correct(out, 5, "fixed an imported page\n")

    result = project.undo_last_import(out)

    assert [o.page for o in pipeline.read_manifest(out)] == [1, 2, 3]
    assert (out / "page-0001.tex").read_text() == "fixed page 1 after the import\n"
    assert result["kept_in_history"] == [5]
    before_undo = next(s for s in store.snapshots(out)
                       if store.read_snapshot(out, s)["action"]["kind"] == "before-undo")
    kept = next(e for e in store.read_snapshot(out, before_undo)["pages"] if e["page"] == 5)
    assert store.get(out, kept["text"]) == b"fixed an imported page\n"


def test_there_is_nothing_to_undo_before_an_import(tmp_path, pdfs) -> None:
    out = _project(tmp_path, pdfs["a"])
    project.capture(out, {"kind": "adopt"})
    with pytest.raises(store.StoreError, match="no import to undo"):
        project.undo_last_import(out)


def test_a_reused_page_number_does_not_inherit_the_old_pages_corrections(tmp_path,
                                                                         pdfs) -> None:
    """Undo an import after correcting its page 5, then append again: the new page 5 is not
    the old one. The old row must neither protect it (the run would keep the undone page's text
    in place of the new page) nor label it "edited" for work nobody did on it."""
    out = _project(tmp_path, pdfs["a"])
    _append(out, pdfs["b"], start=2)
    _correct(out, 5, "work on the page that will leave\n")
    project.undo_last_import(out)

    _append(out, pdfs["b"], start=1)

    assert 5 not in protected_pages(out)
    assert all(r.page != 5 for r in current(out))
    assert "Text of" in (out / "page-0005.tex").read_text(), "the new page 5 was written"
    assert any(r.page == 5 for r in CorrectionLog.for_run(out).read()), "history is untouched"
