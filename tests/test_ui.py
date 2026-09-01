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


def test_each_button_writes_a_different_verdict(server) -> None:
    """The whole point of the surface. DESIGN §11.3.1.

    Correcting the transcription and revising one's own prose leave the same shape of diff,
    so the mode is chosen *before* typing and the label is structural rather than recalled.

    `accept` was missing at first and the omission cost a full run: with nothing to press on
    a page that was already right, an author who read 35 pages produced an empty log.
    """
    base, run = server
    assert MODES == {"fix": "edited", "author": "authored", "accept": "keep-reviewed"}

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


def test_a_correction_that_breaks_the_build_is_quarantined(server) -> None:
    r"""Re-gating has to run in both directions, and this was measured in the wild.

    ch18 p13 passed every gate, the author corrected it through `--fix`, and their correction
    added `\square` — a math-mode command — in text mode. The page stopped compiling and
    nothing noticed, because re-validation only ran on pages that were *already* quarantined.
    It sat broken.

    The author is not the recognizer, but they are equally capable of writing LaTeX that does
    not build, and a page that silently stops compiling is exactly what the compile gate
    exists to refuse.
    """
    base, run = server
    (run / "page-0002.tex").write_text(
        "\\documentclass{article}\\usepackage{amsmath,amssymb}\\begin{document}\n"
        "fine\n\\end{document}\n", encoding="utf-8")

    broken = ("\\documentclass{article}\\usepackage{amsmath,amssymb}\\begin{document}\n"
              "Since it is unique. \\square\n\\end{document}\n")
    r = _post(base, "/api/save", {"page": 2, "mode": "fix", "text": broken})

    assert r["saved"] is True
    assert r["quarantined"] is True, "a correction that breaks the build must be refused"
    assert (run / "page-0002.fail.tex").exists()
    assert not (run / "page-0002.tex").exists()

    rows = [json.loads(l) for l in (run / "manifest.jsonl").read_text().splitlines() if l.strip()]
    row = [x for x in rows if x["page"] == 2][0]
    assert row["verdict"] == "fail"
    assert any("Missing $" in f["detail"] for f in row["findings"]), row["findings"]


def test_authoring_never_quarantines(server) -> None:
    """Revising one's own prose is not a claim about the recognizer, and a gate result on it
    would be a verdict on the author. The file is still written; it is simply not judged."""
    base, run = server
    _post(base, "/api/save", {"page": 2, "mode": "author", "text": "my own words\n"})
    assert (run / "page-0002.tex").exists()
    assert not (run / "page-0002.fail.tex").exists()


# ------------------------------------------------------------------ typeset


def test_a_page_that_does_not_typeset_says_why(server) -> None:
    r"""An empty typeset pane reads as "nothing on this page", which is the §5.7 failure in
    visual form. A 409 carrying the compile log is the honest answer.
    """
    base, run = server
    (run / "page-0002.tex").write_text(
        "\\documentclass{article}\\usepackage{amsmath,amssymb}\\begin{document}\n"
        "Since it is unique. \\square\n\\end{document}\n", encoding="utf-8")

    from urllib.error import HTTPError
    with pytest.raises(HTTPError) as e:
        _get(base, "/api/typeset?page=2")
    assert e.value.code == 409
    body = e.value.read().decode()
    assert "Missing $" in body, body[:200]


@pytest.mark.skipif(
    __import__("handzoo.core.validate.compile_gate", fromlist=["x"]).engine_available() is False,
    reason="pdflatex not installed")
def test_a_compiling_page_is_served_as_a_picture_not_a_pdf(server) -> None:
    r"""Whether a browser renders an embedded PDF depends on the viewer it happens to use — a
    PDF *extension* commonly does not hook iframes, and the pane silently goes white.
    Measured on the author's Chrome. `pdftoppm` is already a hard dependency, so rasterising
    server-side removes the variable entirely.

    The PDF is still reachable with `as=pdf`: it is the right artefact to annotate.
    """
    base, run = server
    (run / "page-0002.tex").write_text(
        "\\documentclass{article}\\begin{document}\nHello $x^2$.\n\\end{document}\n",
        encoding="utf-8")

    status, body = _get(base, "/api/typeset?page=2")
    assert status == 200
    assert body[:8] == b"\x89PNG\r\n\x1a\n", "the proofing pane gets a picture"

    status, body = _get(base, "/api/typeset?page=2&as=pdf")
    assert status == 200
    assert body[:5] == b"%PDF-"


def test_the_typeset_result_is_cached_on_the_source_hash(server) -> None:
    r"""`Cache-Control: no-store` makes the browser re-request, so an iframe fired a *second*
    compile that overwrote the PDF while the first was still being read — the viewer showed a
    few bytes and gave up. The author saw it flash. Keying on the source hash turns a repeat
    request into a file read instead of a race, and the bytes are identical.
    """
    base, run = server
    (run / "page-0002.tex").write_text(
        "\\documentclass{article}\\begin{document}\nstable\n\\end{document}\n",
        encoding="utf-8")
    first = _get(base, "/api/typeset?page=2")[1]
    second = _get(base, "/api/typeset?page=2")[1]
    assert first == second and len(first) > 0


def test_a_diagram_only_page_is_marked_so_a_text_pass_can_skip_it(tmp_path: Path) -> None:
    r"""There is no crop tool in the UI yet, so a page whose only complaint is an invented
    drawing cannot be finished here. Marking it lets a text-only pass skip it **without
    opening it** — and not opening it matters, because reading the emitted text is what
    disqualifies a page as a `--transcribe` subject later (§11.1.1).
    """
    (tmp_path / "pages").mkdir()
    (tmp_path / "page-0001.tex").write_text("a\n", encoding="utf-8")
    (tmp_path / "page-0002.tex").write_text("b\n", encoding="utf-8")
    rows = [
        {"page": 1, "output": str(tmp_path / "page-0001.tex"), "verdict": "fail", "gates": {},
         "findings": [{"gate": "coverage", "detail": "recognizer fabricated a drawing here"}]},
        {"page": 2, "output": str(tmp_path / "page-0002.tex"), "verdict": "fail", "gates": {},
         "findings": [{"gate": "coverage", "detail": "recognizer fabricated a drawing here"},
                      {"gate": "delimiters", "detail": "math mode never closed"}]},
    ]
    (tmp_path / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    Handler.review = Review(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        pages = {p["page"]: p for p in json.loads(_get(base, "/api/pages")[1])["pages"]}
        assert pages[1]["diagram_only"] is True
        assert pages[2]["diagram_only"] is False, "a page with other work is not skippable"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_page_read_and_accepted_can_be_recorded(server) -> None:
    r"""The gap that cost a whole run.

    `--fix` asks, on an unchanged document, *"was the output already correct, or did you
    abandon the attempt?"* and records `keep-reviewed` for the first — DESIGN §11.1.1 calls it
    "the most valuable datum this project can collect".

    The UI ported only the other half. An unchanged save recorded nothing, so an author who
    read 35 pages and found them right produced an **empty log**: no verdicts, no timings, no
    evidence. Reading is the expensive part and it left no trace.

    `keep-reviewed` is GOLD — it is a human saying the output is right, which no gate can say.
    """
    base, run = server
    from handzoo.core.corrections import GOLD
    assert "keep-reviewed" in GOLD

    same = (run / "page-0001.tex").read_text(encoding="utf-8")
    r = _post(base, "/api/save", {"page": 1, "mode": "accept", "text": same, "seconds": 61.0})

    assert r["saved"] is True
    assert r["verdict"] == "keep-reviewed"

    from handzoo.core.corrections import CorrectionLog
    (row,) = CorrectionLog.for_run(run).read()
    assert row.is_gold
    assert row.seconds == 61.0


def test_accepting_never_enters_the_correction_arm(server) -> None:
    """Reading a page and finding it right is not correcting one. Folding the time in would
    inflate the arm with pages that needed no work."""
    base, run = server
    _post(base, "/api/save", {"page": 1, "mode": "accept",
                              "text": (run / "page-0001.tex").read_text(encoding="utf-8"),
                              "seconds": 61.0})
    _post(base, "/api/save", {"page": 2, "mode": "fix", "text": "corrected\n", "seconds": 42.9})

    from handzoo.core.corrections import CorrectionLog
    rows = CorrectionLog.for_run(run).read()
    fix_rows = [r for r in rows if r.finding.startswith("exit criterion: correction")]
    assert len(fix_rows) == 1, "only the actual correction is an arm measurement"


def test_accepting_a_changed_page_is_refused(server) -> None:
    """"Looks right" must mean what it says. If the text was edited, it is a correction."""
    base, _ = server
    from urllib.error import HTTPError
    with pytest.raises(HTTPError) as e:
        _post(base, "/api/save", {"page": 1, "mode": "accept", "text": "something else\n"})
    assert e.value.code == 400


# ------------------------------------------------------------------ autosave


def test_navigating_away_no_longer_loses_an_edit(server) -> None:
    """Today an edit vanishes if you click another page. Autosave writes the file."""
    base, run = server
    _post(base, "/api/autosave", {"page": 1, "text": "half-finished edit\n"})
    assert (run / "page-0001.tex").read_text(encoding="utf-8") == "half-finished edit\n"


def test_autosave_records_no_verdict(server) -> None:
    """Saving the file and recording a decision are different acts. A half-typed line is not
    a judgement about the page, and a log full of them would be worse than losing the text."""
    base, run = server
    _post(base, "/api/autosave", {"page": 1, "text": "mid-edit\n"})
    assert not (run / "corrections.jsonl").exists()


def test_autosave_does_not_destroy_the_before_text(server) -> None:
    r"""The trap, and the reason this is done server-side.

    `before` is read from disk when a verdict is recorded. Once autosave has written the
    file, the on-disk text *is* the edit — so `before` and `after` would be identical, the
    diff would be empty, and the defect taxonomy (§11.0.1a) built from those diffs would show
    a correction that changed nothing.

    So the first autosave for a page snapshots the pristine text, and the verdict uses that.
    """
    base, run = server
    pristine = (run / "page-0001.tex").read_text(encoding="utf-8")

    _post(base, "/api/autosave", {"page": 1, "text": "partial\n"})
    _post(base, "/api/autosave", {"page": 1, "text": "corrected fully\n"})
    _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "corrected fully\n",
                              "seconds": 30.0})

    from handzoo.core.corrections import CorrectionLog
    (row,) = CorrectionLog.for_run(run).read()
    assert row.before == pristine, "the diff must span the whole edit, not the last keystroke"
    assert row.after == "corrected fully\n"


def test_the_snapshot_is_released_once_a_verdict_lands(server) -> None:
    """Otherwise the next session's `before` is last session's text, and every later diff is
    measured from a point the author has forgotten."""
    base, run = server
    _post(base, "/api/autosave", {"page": 1, "text": "one\n"})
    _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "one\n"})

    _post(base, "/api/autosave", {"page": 1, "text": "two\n"})
    _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "two\n"})

    from handzoo.core.corrections import CorrectionLog
    rows = CorrectionLog.for_run(run).read()
    assert rows[-1].before == "one\n", "the second edit starts from where the first ended"


def test_a_resumed_page_uses_its_latest_row_not_its_first(tmp_path: Path) -> None:
    r"""The manifest is append-only, so `--resume` leaves two rows for a retried page.

    Measured: a run interrupted by an Ollama restart recorded 18 pages as errored with
    `output: None`; resuming appended a good row after each. Every reader took `match[0]` —
    the *first* — so the surface served the stale failure and accepting the page did nothing.

    Append-only is right: the log records what happened. The interpretation has to prefer the
    newest, which is the same reasoning that makes an abandoned transcription attempt stay in
    the log and out of the arms (§11.0.2).
    """
    (tmp_path / "pages").mkdir()
    good = tmp_path / "page-0003.tex"
    good.write_text("the retry succeeded\n", encoding="utf-8")
    rows = [
        {"page": 3, "output": None, "verdict": "fail", "gates": {},
         "error": "produced nothing after 3 attempts", "findings": []},
        {"page": 3, "output": str(good), "verdict": "pass", "gates": {}, "findings": []},
    ]
    (tmp_path / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    review = Review(tmp_path)
    outcomes = review.outcomes()
    assert len(outcomes) == 1, "one page, one row — the newest"
    assert outcomes[0].verdict == "pass"
    assert outcomes[0].output == str(good)


def test_the_page_list_shows_a_resumed_page_once(tmp_path: Path) -> None:
    """Two rows for one page listed the page twice in the nav."""
    (tmp_path / "pages").mkdir()
    f = tmp_path / "page-0003.tex"
    f.write_text("x\n", encoding="utf-8")
    rows = [{"page": 3, "output": None, "verdict": "fail", "gates": {}, "error": "e",
             "findings": []},
            {"page": 3, "output": str(f), "verdict": "pass", "gates": {}, "findings": []}]
    (tmp_path / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    Handler.review = Review(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        pages = json.loads(_get(base, "/api/pages")[1])["pages"]
        assert [p["page"] for p in pages] == [3]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_page_list_says_what_the_human_did_not_just_that_they_looked(server) -> None:
    r"""One word over every outcome flattened opposite claims.

    "seen" read the same on a page the author accepted as correct and one they skipped — and
    those are exactly the two the corpus must never conflate (`keep-unreviewed` exists to keep
    them apart). The list now carries the verdict.
    """
    base, run = server
    same = (run / "page-0001.tex").read_text(encoding="utf-8")
    _post(base, "/api/save", {"page": 1, "mode": "accept", "text": same})
    _post(base, "/api/save", {"page": 2, "mode": "fix", "text": "corrected\n"})

    pages = {p["page"]: p for p in json.loads(_get(base, "/api/pages")[1])["pages"]}
    assert pages[1]["did"] == "accepted"
    assert pages[2]["did"] == "edited"


def test_accepting_a_page_twice_does_not_log_it_twice(server) -> None:
    r"""Measured: one page carried **five** `keep-reviewed` rows.

    The author clicked *Looks right* repeatedly because nothing near the button confirmed it —
    the status line sits in the opposite corner of the window. Every click recorded, so the
    corpus counted one accepted page five times and the exit-criterion arms inherited it.

    An accept is idempotent by nature: the claim is *"I read this and it is right"*, which is
    either already recorded or not. Re-asserting it is not a second datum.
    """
    base, run = server
    same = (run / "page-0001.tex").read_text(encoding="utf-8")

    first = _post(base, "/api/save", {"page": 1, "mode": "accept", "text": same, "seconds": 9})
    again = _post(base, "/api/save", {"page": 1, "mode": "accept", "text": same, "seconds": 31})

    assert first["saved"] is True
    assert again["saved"] is False and again["reason"] == "already accepted"

    from handzoo.core.corrections import CorrectionLog
    accepts = [r for r in CorrectionLog.for_run(run).read() if r.verdict == "keep-reviewed"]
    assert len(accepts) == 1, "one page read once is one row"


def test_a_second_action_on_a_page_is_timed_from_the_first(server) -> None:
    r"""Every timing was measured from when the page was *opened*, so a second action on the
    same page carried the whole visit again.

    Measured: `edited 211.4s` then `keep-reviewed 212.6s` on one page — 1.2s of work reported
    as 212.6s. Summing a page's rows therefore counted its time as many times as it had
    actions, and the correction arm (§11.0.1) is built from those seconds.

    The server cannot fix the client's clock, but it can say when the last action landed so
    the next one is measured from there.
    """
    base, run = server
    _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "corrected\n", "seconds": 50})
    r = _post(base, "/api/save", {"page": 1, "mode": "fix", "text": "corrected twice\n",
                                  "seconds": 60})
    assert "restart_timer" in r and r["restart_timer"] is True
