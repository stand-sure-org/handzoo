"""The review surface. An adapter, so these tests exercise it the way a browser would.

Deliberately **not** through the internal API: DESIGN §11.0.2 recorded a reporter that could
never run on real data while its test stayed green, because the test built state the product
could not produce. These go over HTTP.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from handzoo.adapters.ui_server import MODES, Handler, Review


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "p-0001-01.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    (tmp_path / "page-0001.tex").write_text("Prop 18.2 The function is unique.\n",
                                            encoding="utf-8")
    (tmp_path / "page-0002.tex").write_text("clean page\n", encoding="utf-8")
    rows = [
        {"page": 1, "output": str(tmp_path / "page-0001.tex"), "verdict": "pass",
         "gates": {"reference": "fail"},
         "findings": [{"gate": "reference", "detail": "'Prop 18.2' is unmarked", "line": 1}]},
        {"page": 2, "output": str(tmp_path / "page-0002.tex"), "verdict": "pass",
         "gates": {}, "findings": []},
    ]
    (tmp_path / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def server(run_dir: Path):
    Handler.review = Review(run_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", run_dir
    srv.shutdown()
    srv.server_close()


def _get(base: str, path: str):
    with urlopen(base + path) as r:
        return r.status, r.read()


def _post(base: str, path: str, payload: dict):
    req = Request(base + path, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ the separation


def test_the_two_buttons_write_different_verdicts(server) -> None:
    """The whole point of the surface. DESIGN §11.3.1.

    Correcting the transcription and revising one's own prose leave the same shape of diff.
    The mode is chosen *before* typing, so the label is structural rather than recalled.
    """
    base, run = server
    assert MODES == {"fix": "edited", "author": "authored"}

    r = _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "corrected\n", "seconds": 4})
    assert r["verdict"] == "edited"

    r = _post(base, "/api/save", {"page": 2, "mode": "author", "text": "rewritten\n",
                                  "seconds": 90})
    assert r["verdict"] == "authored"

    from handzoo.core.corrections import CorrectionLog
    log = CorrectionLog.for_run(run)
    arms = log.summary()["unpaired_arms"]
    # One transcription arm is absent, so no comparison — but the authored row must not have
    # entered the correcting arm regardless.
    correcting = [r for r in log.read() if r.verdict == "edited"]
    authored = [r for r in log.read() if r.verdict == "authored"]
    assert len(correcting) == 1 and len(authored) == 1
    assert arms == {}, "one arm alone still reports nothing"


def test_an_unknown_mode_is_refused(server) -> None:
    """A typo must not silently land as a correction — the label is the measurement."""
    base, _ = server
    from urllib.error import HTTPError
    with pytest.raises(HTTPError) as e:
        _post(base, "/api/save", {"page": 1, "mode": "polish", "text": "x"})
    assert e.value.code == 400


def test_an_unchanged_save_records_nothing(server) -> None:
    r"""Same reasoning as `--fix` (§11.1.1): unchanged is ambiguous, so it is not a datum."""
    base, run = server
    same = (run / "page-0001.tex").read_text(encoding="utf-8")
    r = _post(base, "/api/save", {"page": 1, "mode": "fix", "text": same})
    assert r["saved"] is False
    assert not (run / "corrections.jsonl").exists()


# ------------------------------------------------------------------ the surface


def test_an_advisory_finding_is_shown_as_a_flag_not_a_failure(server) -> None:
    """Collapsing advisory into fail teaches the reader to ignore both (§11.0.1b)."""
    base, _ = server
    _, body = _get(base, "/api/pages")
    pages = {p["page"]: p for p in json.loads(body)["pages"]}
    assert pages[1]["state"] == "flag"
    assert pages[2]["state"] == "ok"


def test_a_saved_page_is_marked_reviewed(server) -> None:
    base, _ = server
    _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "corrected\n"})
    _, body = _get(base, "/api/pages")
    pages = {p["page"]: p for p in json.loads(body)["pages"]}
    assert pages[1]["reviewed"] and not pages[2]["reviewed"]


def test_the_edit_is_written_to_the_page_file(server) -> None:
    base, run = server
    _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "corrected text\n"})
    assert (run / "page-0001.tex").read_text(encoding="utf-8") == "corrected text\n"


def test_the_page_list_reads_the_manifest_fresh_every_time(server) -> None:
    """`handzoo` may still be writing while the browser is open; a cached list would lie."""
    base, run = server
    _, body = _get(base, "/api/pages")
    assert len(json.loads(body)["pages"]) == 2
    with (run / "manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"page": 3, "output": None, "verdict": "fail",
                             "gates": {}, "findings": []}) + "\n")
    _, body = _get(base, "/api/pages")
    assert len(json.loads(body)["pages"]) == 3


def test_a_missing_page_is_a_404_not_an_empty_editor(server) -> None:
    """An empty editor over a missing file invites typing a page into the void."""
    base, _ = server
    from urllib.error import HTTPError
    with pytest.raises(HTTPError) as e:
        _get(base, "/api/text?page=99")
    assert e.value.code == 404


# ------------------------------------------------------------------ re-validation


@pytest.fixture
def quarantined(tmp_path: Path) -> Path:
    """A page the gates refused, written under the name a build cannot pick up."""
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "p-0001-01.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    bad = tmp_path / "page-0001.fail.tex"
    bad.write_text("\\documentclass{article}\\begin{document}\n"
                   "\\includegraphics{diagram181.png}\n\\end{document}\n", encoding="utf-8")
    (tmp_path / "manifest.jsonl").write_text(json.dumps({
        "page": 1, "output": str(bad), "verdict": "fail",
        "gates": {"coverage": "fail"},
        "findings": [{"gate": "coverage", "detail": "fabricated a drawing", "line": 2}],
    }) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def qserver(quarantined: Path):
    Handler.review = Review(quarantined)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", quarantined
    srv.shutdown()
    srv.server_close()


def test_a_corrected_page_stops_being_quarantined(qserver) -> None:
    r"""`.fail.tex` is a name a build cannot pick up by accident. Once the author has fixed
    the thing that failed, keeping that name is a lie in the other direction — the page is
    good and the filename says otherwise, so `chapter.tex` still carries a placeholder.

    Gates re-run on what was actually saved. Nothing is renamed on the author's say-so.
    """
    base, run = qserver
    fixed = ("\\documentclass{article}\\begin{document}\n"
             "The diagram is described in words here.\n\\end{document}\n")
    r = _post(base, "/api/save", {"page": 1, "mode": "fix", "text": fixed})

    assert r["saved"] is True
    assert r["verdict"] == "edited"
    assert r["revalidated"] is True, "gates must re-run on the saved text"
    assert not (run / "page-0001.fail.tex").exists(), "the quarantine name must be released"
    assert (run / "page-0001.tex").read_text(encoding="utf-8") == fixed


def test_a_still_failing_page_keeps_its_quarantine(qserver) -> None:
    """A half-fix must not be promoted. The gate decides, not the act of saving."""
    base, run = qserver
    still_bad = ("\\documentclass{article}\\begin{document}\n"
                 "\\includegraphics{another-invented-file.png}\n\\end{document}\n")
    r = _post(base, "/api/save", {"page": 1, "mode": "fix", "text": still_bad})

    assert r["saved"] is True
    assert r["revalidated"] is False
    assert (run / "page-0001.fail.tex").exists()
    assert not (run / "page-0001.tex").exists()


def test_the_manifest_learns_the_new_path_and_verdict(qserver) -> None:
    """`handzoo-review`, `assemble` and the UI all read the manifest. A rename it does not
    know about leaves every one of them pointing at a file that is gone."""
    base, run = qserver
    _post(base, "/api/save", {"page": 1, "mode": "fix",
                              "text": "\\documentclass{article}\\begin{document}\n"
                                      "words\n\\end{document}\n"})
    rows = [json.loads(l) for l in (run / "manifest.jsonl").read_text().splitlines() if l.strip()]
    assert Path(rows[-1]["output"]).name == "page-0001.tex"
    assert rows[-1]["verdict"] != "fail"
    assert not rows[-1]["findings"], "the findings that were fixed must not persist"
