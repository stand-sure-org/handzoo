"""Ingesting a PDF from the review surface (DESIGN, in-app ingestion D1-D4).

The recognizer is a stub -- CI never calls a model. What is asserted is what the surface shows
while a run is still going, and what it refuses: the author reviews behind recognition, so the
page list has to be right *during* a run, not only after it.
"""

from __future__ import annotations

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

from handzoo.adapters import cli_ui
from handzoo.adapters.ui_server import Handler, configure
from handzoo.core.pipeline import read_manifest
from handzoo.core.recognize.base import Recognition
from handzoo.core.recognize.ollama_vlm import RecognitionError

pytestmark = pytest.mark.skipif(
    not (shutil.which("pdftoppm") and shutil.which("pdflatex")),
    reason="poppler and/or pdflatex not installed")


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> bytes:
    tmp = tmp_path_factory.mktemp("pdf")
    (tmp / "d.tex").write_text(
        "\\documentclass{article}\\begin{document}A\\newpage B\\newpage C\\end{document}\n",
        encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "d.tex"],
                   cwd=tmp, capture_output=True, check=False)
    return (tmp / "d.pdf").read_bytes()


def _page_of(image: Path) -> int:
    return int(image.stem.split("-")[1])


class Stub:
    """Recognizes instantly, unless told to hold on a page until released."""

    def __init__(self, *, hold: int | None = None, fail_once: int | None = None) -> None:
        self.seen: list[int] = []
        self.hold = hold
        self.release = threading.Event()
        self.fail_once = fail_once

    def recognize(self, image: Path) -> Recognition:
        n = _page_of(image)
        self.seen.append(n)
        if n == self.hold:
            self.release.wait(timeout=20)
        if n == self.fail_once:
            self.fail_once = None
            raise RecognitionError(f"stub: ollama unreachable on page {n}")
        return Recognition(markup=f"Page {n} body.", inventory=(), provider="stub",
                           model="stub-model")


@pytest.fixture
def start(tmp_path: Path):
    servers = []

    def _start(recognizer, out_dir: Path | None = None) -> tuple[str, Path]:
        out = out_dir or tmp_path / "project"
        configure(out, recognizer=recognizer)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", out

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _get(base: str, path: str) -> dict:
    with urlopen(base + path) as r:
        return json.loads(r.read())


def _post(base: str, path: str, body: bytes = b"{}", ctype: str = "application/json"):
    req = Request(base + path, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urlopen(req) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _upload(base: str, data: bytes, name: str = "notes.pdf", exclude: str = ""):
    q = f"?name={name}" + (f"&exclude={exclude}" if exclude else "")
    return _post(base, "/api/ingest" + q, data, "application/pdf")


def _wait(base: str, pred, timeout: float = 30.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = _get(base, "/api/pages")
        if pred(r):
            return r
        time.sleep(0.05)
    raise AssertionError(f"timed out; last state: {r['ingest']}")


def _finished(r: dict) -> bool:
    return r["ingest"]["state"] in ("done", "stopped", "failed")


def _states(r: dict) -> dict[int, str]:
    return {p["page"]: p["state"] for p in r["pages"]}


# ------------------------------------------------------------------------- the happy path


def test_a_pdf_dropped_into_an_empty_project_becomes_pages(start, pdf: bytes) -> None:
    base, out = start(Stub())
    code, r = _upload(base, pdf)
    assert code == 202 and r["total"] == 3

    final = _wait(base, _finished)

    assert final["ingest"]["state"] == "done"
    assert sorted(_states(final)) == [1, 2, 3]
    assert (out / "source" / "notes.pdf").read_bytes() == pdf, "the source stays with the project"
    chapter = (out / "chapter.tex").read_text(encoding="utf-8")
    assert all(f"\\input{{page-{n:04d}}}" in chapter for n in (1, 2, 3))


def test_pages_appear_as_they_land_not_when_the_run_ends(start, pdf: bytes) -> None:
    """D1: the author reviews page 1 while page 2 is being read. The list has to say which
    pages are ready, which is being read now, and which are still waiting."""
    stub = Stub(hold=2)
    base, _ = start(stub)
    _upload(base, pdf)

    mid = _wait(base, lambda r: 1 in {o.page for o in read_manifest(Path(r["dir"]))}
                and r["ingest"].get("current") == 2)
    states = _states(mid)
    assert states[2] == "recognizing" and states[3] == "queued"
    assert states[1] not in ("recognizing", "queued", "unrecognized"), "page 1 is ready"

    stub.release.set()
    assert _wait(base, _finished)["ingest"]["state"] == "done"


# ----------------------------------------------------------------------------- refusals


def test_a_project_that_already_has_pages_is_refused(start, pdf: bytes, tmp_path: Path) -> None:
    """Importing into an existing project is D5's append / replace flow, which is not built
    yet. Until it is, a second PDF must not be poured over pages that may carry the author's
    work -- and the refusal says what to do instead."""
    base, out = start(Stub())
    _upload(base, pdf)
    _wait(base, _finished)

    code, r = _upload(base, pdf, name="more.pdf")

    assert code == 409
    assert "already has pages" in r["error"]
    assert not (out / "source" / "more.pdf").exists()


def test_the_upload_name_cannot_choose_where_the_file_lands(start, pdf: bytes,
                                                            tmp_path: Path) -> None:
    """The one place a client-supplied string becomes a filesystem path."""
    base, out = start(Stub())
    code, _ = _upload(base, pdf, name="../../escaped.pdf")
    assert code == 202
    _wait(base, _finished)
    assert (out / "source" / "escaped.pdf").exists()
    assert not (tmp_path / "escaped.pdf").exists()
    assert not (out.parent.parent / "escaped.pdf").exists()


def test_a_file_that_is_not_a_pdf_is_refused(start) -> None:
    """By its bytes, not its name: `notes.pdf` holding anything else is still not a PDF. And
    refused *before* it is written or handed to a parser -- `pdfinfo` would refuse it too, but
    only after the bytes were on disk and parsed."""
    base, out = start(Stub())
    code, r = _upload(base, b"this is not a pdf", name="notes.pdf")
    assert code == 400 and "not a PDF" in r["error"]
    assert not (out / "source").exists() or not any((out / "source").iterdir())


def test_an_excluded_page_never_reaches_the_recognizer(start, pdf: bytes) -> None:
    """D4: a page that is not the author's to reproduce is cut before any model sees it, so
    the choice has to exist before the run starts."""
    stub = Stub()
    base, _ = start(stub)
    _upload(base, pdf, exclude="2")
    final = _wait(base, _finished)

    assert stub.seen == [1, 3]
    assert _states(final)[2] == "excluded"


def test_a_cut_page_shows_as_cut_before_the_run_reaches_it(start, pdf: bytes) -> None:
    """Measured on a real 22-page run: page 4 was cut, and read "queued" until the run got
    there -- as though the choice had not been heard."""
    stub = Stub(hold=1)
    base, _ = start(stub)
    _upload(base, pdf, exclude="3")
    mid = _wait(base, lambda r: r["ingest"].get("current") == 1)
    stub.release.set()
    assert _states(mid)[3] == "excluded"
    _wait(base, _finished)


def test_a_second_ingest_while_one_runs_is_refused(start, pdf: bytes) -> None:
    stub = Stub(hold=1)
    base, _ = start(stub)
    _upload(base, pdf)
    code, r = _upload(base, pdf, name="again.pdf")
    stub.release.set()
    assert code == 409 and "running" in r["error"]
    _wait(base, _finished)


# ------------------------------------------------------------------ interrupted and failed


def test_stop_keeps_what_landed_and_resume_finishes_the_rest(start, pdf: bytes) -> None:
    """Picking the wrong file, or needing the machine back, should cost the pages not yet
    read -- never the ones already done."""
    stub = Stub(hold=2)
    base, _ = start(stub)
    _upload(base, pdf)
    _wait(base, lambda r: r["ingest"].get("current") == 2)
    _post(base, "/api/ingest/stop")
    # Measured on a real run: a runaway page took over two minutes, and stop is honoured only
    # between pages. Until the page in hand lands, the surface has to say it is stopping --
    # not keep offering a Stop that appears to do nothing.
    assert _get(base, "/api/ingest")["state"] == "stopping"
    stub.release.set()

    stopped = _wait(base, _finished)
    assert stopped["ingest"]["state"] == "stopped"
    assert _states(stopped)[3] == "unrecognized"

    code, _ = _post(base, "/api/ingest/resume")
    assert code == 202
    done = _wait(base, lambda r: _finished(r) and r["ingest"]["state"] == "done")
    assert "unrecognized" not in _states(done).values()
    assert stub.seen.count(1) == 1, "resume must not re-read a page that already landed"


def test_a_page_the_recognizer_failed_on_can_be_retried(start, pdf: bytes) -> None:
    """An Ollama restart mid-run errors pages; retrying them must not mean re-running the PDF."""
    stub = Stub(fail_once=2)
    base, _ = start(stub)
    _upload(base, pdf)
    first = _wait(base, _finished)
    p2 = next(p for p in first["pages"] if p["page"] == 2)
    assert p2["state"] == "fail" and "unreachable" in p2["error"]

    _post(base, "/api/ingest/resume")
    _wait(base, lambda r: _finished(r) and
                  next(p for p in r["pages"] if p["page"] == 2)["state"] != "fail")
    assert stub.seen.count(1) == 1 and stub.seen.count(2) == 2


def test_an_interrupted_run_is_still_visible_after_a_restart(start, pdf: bytes) -> None:
    """The run's state lives in memory and dies with the server. The pages it had not reached
    must not vanish with it -- the stored source PDF says how many there are."""
    stub = Stub(hold=2)
    base, out = start(stub)
    _upload(base, pdf)
    _wait(base, lambda r: r["ingest"].get("current") == 2)
    _post(base, "/api/ingest/stop")
    stub.release.set()
    _wait(base, _finished)

    base2, _ = start(Stub(), out_dir=out)          # a fresh server on the same project
    after = _get(base2, "/api/pages")
    assert after["ingest"]["state"] == "idle"
    assert _states(after)[3] == "unrecognized"


# ------------------------------------------------------------------------------ the CLI


def test_the_ui_opens_on_a_new_project_folder(tmp_path: Path, monkeypatch) -> None:
    """A project that does not exist yet is where ingestion starts."""
    served = {}
    monkeypatch.setattr(cli_ui, "serve", lambda d, **kw: served.setdefault("dir", d))
    assert cli_ui.main([str(tmp_path / "fresh"), "--no-open"]) == 0
    assert served["dir"] == tmp_path / "fresh"


def test_the_ui_refuses_a_folder_that_is_not_a_project(tmp_path: Path, monkeypatch) -> None:
    """Pointing it at the folder that *holds* the projects, by mistake, must not turn that
    folder into a project -- it holds the author's corrected corpora."""
    (tmp_path / "ch22").mkdir()
    (tmp_path / "ch22" / "manifest.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_ui, "serve", lambda d, **kw: pytest.fail("must not serve"))
    import io
    said = io.StringIO()
    assert cli_ui.main([str(tmp_path), "--no-open"], stream=said) == 2
    assert "not a project" in said.getvalue()
