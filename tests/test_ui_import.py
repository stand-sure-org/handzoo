"""Adding a PDF to a project that already has pages, from the surface (DESIGN, D5).

Append only: Replace-pages-from is not offered yet. The recognizer is a stub.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from handzoo.adapters import cli_convert
from handzoo.adapters.ui_server import Handler, configure
from handzoo.core import pipeline, store
from handzoo.core.recognize.base import Recognition

pytestmark = pytest.mark.skipif(
    not (shutil.which("pdftoppm") and shutil.which("pdflatex")),
    reason="poppler and/or pdflatex not installed")

LINES = [(1, 2), (4, 1), (6, 5), (2, 8), (5, 5)]


def _pdf(folder: Path, name: str, lines) -> bytes:
    body = "\\newpage ".join(f"\\tikz\\draw (0,0)--({x},{y});" for x, y in lines)
    (folder / f"{name}.tex").write_text(
        "\\documentclass{article}\\usepackage{tikz}\\pagestyle{empty}\\begin{document}"
        f"{body}\\end{{document}}\n", encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", f"{name}.tex"], cwd=folder,
                   capture_output=True, check=False)
    return (folder / f"{name}.pdf").read_bytes()


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory) -> dict[str, bytes]:
    tmp = tmp_path_factory.mktemp("pdfs")
    # The same notebook, exported before and after two more pages were written.
    return {"first": _pdf(tmp, "first", LINES[:3]), "grown": _pdf(tmp, "grown", LINES)}


class Stub:
    def __init__(self, *, hold: int | None = None) -> None:
        self.seen: list[int] = []
        self.hold, self.release = hold, threading.Event()

    def recognize(self, image: Path) -> Recognition:
        n = int(image.stem.split("-")[1])
        self.seen.append(n)
        if n == self.hold:
            self.release.wait(timeout=20)
        return Recognition(markup=f"Page {n} body.", inventory=(), provider="stub", model="stub")


@pytest.fixture
def served(tmp_path: Path, pdfs):
    """A project made from the first export, served, with its first run finished."""
    servers = []

    def _serve(stub: Stub):
        out = tmp_path / "project"
        configure(out, recognizer=stub)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        _post(base, "/api/ingest?name=notes.pdf", pdfs["first"], "application/pdf")
        _wait(base, lambda r: r["ingest"]["state"] == "done")
        return base, out

    yield _serve
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _get(base, path):
    with urlopen(base + path) as r:
        return json.loads(r.read())


def _post(base, path, body=b"{}", ctype="application/json"):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    req = Request(base + path, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urlopen(req) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _wait(base, pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = _get(base, "/api/pages")
        if pred(r):
            return r
        time.sleep(0.05)
    raise AssertionError(f"timed out: {r['ingest']}")


def _preview(base, data, name="notes.pdf"):
    return _post(base, f"/api/import/preview?name={name}", data, "application/pdf")


def _tree(out: Path) -> dict[str, str]:
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file() and store.STORE not in p.parts}


def _pages(r) -> list[int]:
    return [p["page"] for p in r["pages"]]


# ----------------------------------------------------------------------------------------


def test_the_preview_says_how_the_file_lines_up_and_writes_nothing(served, pdfs) -> None:
    """The commonest mistake (D5 pre-mortem 3): appending a grown notebook from page 1, so
    every old page arrives twice. The preview sees the first three are already here."""
    base, out = served(Stub())
    before = _tree(out)

    code, r = _preview(base, pdfs["grown"])

    assert code == 200 and r["total"] == 5 and r["project_pages"] == 3
    assert r["plan"]["present"] == 3 and r["plan"]["append_from"] == 4
    assert _tree(out) == before, "a preview must not touch the project"
    assert store.extent(out) is None, "and must not adopt it -- that waits for a commit"


def test_an_append_lands_after_the_last_page(served, pdfs) -> None:
    stub = Stub()
    base, out = served(stub)
    _, r = _preview(base, pdfs["grown"])
    stub.seen.clear()

    code, started = _post(base, "/api/import/commit", {"token": r["token"], "start": 4})
    done = _wait(base, lambda x: x["ingest"]["state"] == "done")

    assert code == 202 and started["pages"] == [4, 5]
    assert _pages(done) == [1, 2, 3, 4, 5]
    assert stub.seen == [4, 5], "only the new pages are read"
    rows = {o.page: o for o in pipeline.read_manifest(out)}
    assert rows[4].pdf_page == 4 and Path(rows[4].source).name == "notes-2.pdf", \
        "a second export with the same name must not overwrite the first"
    assert "\\input{page-0005}" in (out / "chapter.tex").read_text()
    assert done["imports"]["undo"]["pages"] == [4, 5]


def test_undo_takes_the_added_pages_back_out(served, pdfs) -> None:
    base, out = served(Stub())
    _, r = _preview(base, pdfs["grown"])
    _post(base, "/api/import/commit", {"token": r["token"], "start": 4})
    _wait(base, lambda x: x["ingest"]["state"] == "done")

    code, result = _post(base, "/api/import/undo")

    assert code == 200 and result["removed"] == [4, 5]
    after = _get(base, "/api/pages")
    assert _pages(after) == [1, 2, 3]
    assert "page-0004" not in (out / "chapter.tex").read_text()
    assert after["imports"]["undo"] is None
    code, again = _post(base, "/api/import/undo")
    assert code == 409 and "no import to undo" in again["error"]


def test_a_stopped_import_resumes_from_where_it_stopped(served, pdfs) -> None:
    stub = Stub(hold=4)
    base, _ = served(stub)
    _, r = _preview(base, pdfs["grown"])
    _post(base, "/api/import/commit", {"token": r["token"], "start": 4})
    _wait(base, lambda x: x["ingest"].get("current") == 4)
    _post(base, "/api/ingest/stop")
    stub.release.set()
    stopped = _wait(base, lambda x: x["ingest"]["state"] == "stopped")
    assert {p["page"]: p["state"] for p in stopped["pages"]}[5] == "unrecognized"
    assert stopped["imports"]["pending"] is True

    _post(base, "/api/ingest/resume")
    done = _wait(base, lambda x: x["ingest"]["state"] == "done")

    assert _pages(done) == [1, 2, 3, 4, 5] and stub.seen.count(4) == 1
    assert done["imports"]["pending"] is False and done["imports"]["undo"]["pages"] == [4, 5]


def test_a_stopped_import_can_be_undone_as_it_stands(served, pdfs) -> None:
    stub = Stub(hold=4)
    base, _ = served(stub)
    _, r = _preview(base, pdfs["grown"])
    _post(base, "/api/import/commit", {"token": r["token"], "start": 4})
    _wait(base, lambda x: x["ingest"].get("current") == 4)
    _post(base, "/api/ingest/stop")
    stub.release.set()
    _wait(base, lambda x: x["ingest"]["state"] == "stopped")

    code, _ = _post(base, "/api/import/undo")

    assert code == 200 and _pages(_get(base, "/api/pages")) == [1, 2, 3]


def test_a_cut_file_page_never_reaches_the_recognizer(served, pdfs) -> None:
    stub = Stub()
    base, _ = served(stub)
    _, r = _preview(base, pdfs["grown"])
    stub.seen.clear()
    _post(base, "/api/import/commit", {"token": r["token"], "start": 4, "exclude": "5"})
    done = _wait(base, lambda x: x["ingest"]["state"] == "done")
    assert stub.seen == [4]
    assert {p["page"]: p["state"] for p in done["pages"]}[5] == "excluded"


def test_a_preview_that_was_replaced_cannot_be_committed(served, pdfs) -> None:
    base, _ = served(Stub())
    _, old = _preview(base, pdfs["grown"])
    _preview(base, pdfs["grown"])
    code, r = _post(base, "/api/import/commit", {"token": old["token"], "start": 4})
    assert code == 409 and "expired" in r["error"]


def test_an_import_waits_for_the_first_run_to_finish(tmp_path, pdfs) -> None:
    """A project whose first run stopped at page 1 of 3 has pages it has not read yet. An
    append would count only the pages that landed, hide the rest, and put the new pages in
    their places. So it is refused until the first run is finished."""
    stub = Stub(hold=1)
    out = tmp_path / "project"
    configure(out, recognizer=stub)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _post(base, "/api/ingest?name=notes.pdf", pdfs["first"], "application/pdf")
        _wait(base, lambda x: x["ingest"].get("current") == 1)
        _post(base, "/api/ingest/stop")
        stub.release.set()
        _wait(base, lambda x: x["ingest"]["state"] == "stopped")

        code, r = _preview(base, pdfs["grown"])

        assert code == 409 and "not finished" in r["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_cli_refuses_a_project_that_imports_manage(served, pdfs, tmp_path) -> None:
    """The CLI places pages by the file's numbers. Into a project with appended pages, page 3
    of a new file would land on page 3 of the project."""
    base, out = served(Stub())
    _, r = _preview(base, pdfs["grown"])
    _post(base, "/api/import/commit", {"token": r["token"], "start": 4})
    _wait(base, lambda x: x["ingest"]["state"] == "done")
    (tmp_path / "x.pdf").write_bytes(pdfs["first"])

    said = io.StringIO()
    assert cli_convert.main([str(tmp_path / "x.pdf"), "-o", str(out)], stream=said) == 2
    assert "managed by imports" in said.getvalue()
