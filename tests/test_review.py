"""The correction loop.

The interaction is stubbed — `read_line` and `stream` are injected — so these assert what the
loop *records*, which is the part that becomes corpus and outlives the session.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from handzoo.adapters import cli_review
from handzoo.core.corrections import Correction, CorrectionLog
from handzoo.core.pipeline import MANIFEST


def _manifest(out_dir: Path, *outcomes: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)
    with (out_dir / MANIFEST).open("w", encoding="utf-8") as fh:
        for o in outcomes:
            fh.write(json.dumps(o) + "\n")
    return out_dir


def _page(out_dir: Path, page: int, *, findings: list[dict], body: str = "line one\nline two\n"):
    (out_dir / "pages").mkdir(parents=True, exist_ok=True)
    tex = out_dir / f"page-{page:04d}.tex"
    tex.write_text(body, encoding="utf-8")
    (out_dir / "pages" / f"p-{page:04d}-01.png").write_bytes(b"\x89PNG")
    return {"page": page, "output": str(tex), "verdict": "fail", "gates": {}, "error": None,
            "rules": 0, "findings": findings}


def _keys(*presses: str):
    it = iter(presses)
    return lambda: next(it, "q")


FINDING = {"gate": "coverage", "detail": "7 marks seen, 1 accounted for",
           "line": 2, "excerpt": "HAS MORE THAN"}


# --------------------------------------------------------------- the corpus record


def test_keep_after_seeing_the_text_is_reviewed_not_merely_kept(tmp_path: Path) -> None:
    """The distinction is the point.

    Automation bias is measured at 20-30% missed defects under repetitive load. A page kept
    after inspection and a page never opened are different facts, and a log that conflates
    them produces a corpus that flatters the model.
    """
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out)], stream=io.StringIO(),
                    read_line=_keys("k"))

    (row,) = CorrectionLog.for_run(out).read()
    assert row.verdict == "keep-reviewed"
    assert row.is_gold


def test_the_wrong_output_is_kept_alongside_the_right_one(tmp_path: Path) -> None:
    """Teams that stored only corrected text regretted it: (image, wrong, correct) triples
    are what make evaluation and regression tracking possible later."""
    log = CorrectionLog(tmp_path / "c.jsonl")
    log.append(Correction(page=1, verdict="edited", source_image="p1.png",
                          before="4 < 4", after="|||| < HHT"))
    (row,) = log.read()
    assert row.before == "4 < 4"
    assert row.after == "|||| < HHT"
    assert row.source_image == "p1.png"


def test_time_is_recorded_because_the_exit_criterion_is_a_timing_question(
        tmp_path: Path) -> None:
    """M0 is done when correcting beats retyping. No published study gives that threshold,
    so the log is the fastest route to a real number for this corpus."""
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("k"))

    (row,) = CorrectionLog.for_run(out).read()
    assert row.seconds >= 0.0
    assert isinstance(row.at, float)


@pytest.mark.parametrize("key,verdict", [
    ("k", "keep-reviewed"),
    ("s", "skipped"),
    ("", "skipped"),
])
def test_each_keystroke_records_its_own_verdict(tmp_path: Path, key: str,
                                                verdict: str) -> None:
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys(key))
    assert CorrectionLog.for_run(out).read()[0].verdict == verdict


def test_flagging_captures_the_reason_in_the_humans_words(tmp_path: Path) -> None:
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out)], stream=io.StringIO(),
                    read_line=_keys("f", "the dog glyph is a noun here"))

    (row,) = CorrectionLog.for_run(out).read()
    assert row.verdict == "flagged"
    assert "noun" in row.reason
    assert not row.is_gold, "a flag is a problem recorded, not a problem solved"


# --------------------------------------------------------------- what it surfaces


def test_review_opens_on_findings_not_on_every_page(tmp_path: Path) -> None:
    """Confidence routing is settled practice: surface what the tool is unsure about."""
    out = _manifest(tmp_path,
                    _page(tmp_path, 1, findings=[]),
                    _page(tmp_path, 2, findings=[FINDING]))
    s = io.StringIO()
    cli_review.main([str(out)], stream=s, read_line=_keys("k"))

    assert "page 2" in s.getvalue()
    assert "page 1" not in s.getvalue(), "a clean page is not worth a human's attention by default"


def test_all_includes_clean_pages_and_still_refuses_to_call_them_correct(
        tmp_path: Path) -> None:
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[]))
    s = io.StringIO()
    cli_review.main([str(out), "--all"], stream=s, read_line=_keys("q"))

    assert "page 1" in s.getvalue()
    assert "substitution" in s.getvalue(), "silence from the gates is not a clean bill"


def test_the_offending_line_is_shown_in_context(tmp_path: Path) -> None:
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING],
                                    body="one\ntwo\nthree\n"))
    s = io.StringIO()
    cli_review.main([str(out)], stream=s, read_line=_keys("k"))

    assert ">>    2 | two" in s.getvalue(), "the human needs to see it in place, not a line number"


def test_quitting_keeps_what_was_already_decided(tmp_path: Path) -> None:
    out = _manifest(tmp_path,
                    _page(tmp_path, 1, findings=[FINDING]),
                    _page(tmp_path, 2, findings=[FINDING]))
    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("k", "q"))

    rows = CorrectionLog.for_run(out).read()
    assert len(rows) == 1 and rows[0].page == 1


# --------------------------------------------------------------- the summary


def test_the_summary_never_reports_unexamined_pages_as_verified(tmp_path: Path) -> None:
    """`keep-unreviewed` and `skipped` record that a human passed through. That is worth
    knowing and worth never mistaking for verification."""
    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="edited", source_image="", before="a", after="b"))
    log.append(Correction(page=2, verdict="skipped", source_image="", before="c"))
    log.append(Correction(page=3, verdict="keep-unreviewed", source_image="", before="d"))

    s = io.StringIO()
    cli_review.main([str(tmp_path), "--summary"], stream=s)

    assert log.summary()["gold_pairs"] == 1
    assert log.summary()["unexamined"] == 2
    assert "not verification" in s.getvalue()


def test_a_corrupt_row_raises_rather_than_being_skipped(tmp_path: Path) -> None:
    """Silently dropping a row understates the corpus, which is the wrong direction for a
    record whose entire job is to be trustworthy."""
    p = tmp_path / "c.jsonl"
    p.write_text('{"page": 1, "verdict": "edited", "source_image": "", "before": "a"}\n'
                 "not json at all\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a valid correction row"):
        CorrectionLog(p).read()


def test_missing_manifest_explains_what_to_do(tmp_path: Path) -> None:
    s = io.StringIO()
    assert cli_review.main([str(tmp_path)], stream=s) == 2
    assert "run `handzoo`" in s.getvalue()


# --------------------------------------------------------------- resuming


def test_rerunning_skips_what_was_already_decided(tmp_path: Path) -> None:
    """Quitting prints "re-run to continue". That has to be true.

    Measured on a real run: it was not. Two findings were decided `keep-reviewed` on page 1,
    the session quit, and the next run opened on page 1 again with identical text. A reviewer
    who trusts the message re-reviews everything, and cannot tell a replay from a stall.
    """
    out = _manifest(tmp_path,
                    _page(tmp_path, 1, findings=[FINDING]),
                    _page(tmp_path, 2, findings=[FINDING]))

    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("k", "q"))
    second = io.StringIO()
    cli_review.main([str(out)], stream=second, read_line=_keys("q"))

    assert "=== page 2" in second.getvalue()
    assert "=== page 1" not in second.getvalue(), "page 1 was decided and replayed anyway"


def test_a_skip_is_deferral_not_a_decision_and_comes_back(tmp_path: Path) -> None:
    """`skipped` is the bare-enter default. Treating it as decided would let a reviewer
    silently retire findings by leaning on the return key — the automation bias this loop
    exists to resist."""
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))

    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("s"))
    second = io.StringIO()
    cli_review.main([str(out)], stream=second, read_line=_keys("q"))

    assert "=== page 1" in second.getvalue()


def test_hidden_findings_are_announced_never_silently_dropped(tmp_path: Path) -> None:
    """A count that vanishes without a word is indistinguishable from a gate that stopped
    running (DESIGN 5.7)."""
    out = _manifest(tmp_path,
                    _page(tmp_path, 1, findings=[FINDING]),
                    _page(tmp_path, 2, findings=[FINDING]))

    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("k", "q"))
    second = io.StringIO()
    cli_review.main([str(out)], stream=second, read_line=_keys("q"))

    assert "already decided" in second.getvalue()


# --------------------------------------------------------------- repeated findings


def test_identical_findings_are_one_decision_not_thirty_three(tmp_path: Path) -> None:
    """Measured on the author's ch18 page 25: 35 findings, 32 of them byte-identical and all
    pointing at line 35.

    Rendered one per prompt they produced 32 consecutive frames with the same detail, the same
    context lines and no repeat of the source filename — which reads as the tool being stuck,
    and is how this was reported. Worse, mashing `k` through them wrote 32 `keep-reviewed`
    rows into the gold corpus: 32 assertions about correctness bought with one act of
    attention. That is the automation bias the module docstring cites, manufactured by our own
    display.

    They are one defect. They get one decision, and the row says how many it covered.
    """
    same = {"gate": "coverage", "detail": "recognizer emitted a diagram environment",
            "line": 2, "excerpt": ""}
    other = {"gate": "compile", "detail": "Missing $ inserted.", "line": 1, "excerpt": ""}
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[same, same, same, other]))

    s = io.StringIO()
    cli_review.main([str(out)], stream=s, read_line=_keys("k", "k"))

    rows = CorrectionLog.for_run(out).read()
    assert len(rows) == 2, f"four findings should collapse to two decisions, got {len(rows)}"
    grouped = next(r for r in rows if "diagram environment" in r.finding)
    assert grouped.instances == 3, "the row must say how many findings it covered"
    assert "x3" in s.getvalue(), "the human has to see the count before deciding"


def test_findings_that_differ_are_never_merged(tmp_path: Path) -> None:
    """Two fabricated files on one line are two artefacts. Merging them would hide one, which
    is the silent-loss failure (constraint 5) wearing a tidier interface."""
    a = {"gate": "coverage", "detail": "nonexistent file diagram181.png", "line": 2,
         "excerpt": ""}
    b = {"gate": "coverage", "detail": "nonexistent file diagram182.png", "line": 2,
         "excerpt": ""}
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[a, b]))

    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("k", "k"))
    assert len(CorrectionLog.for_run(out).read()) == 2


def test_the_summary_counts_findings_not_only_keypresses(tmp_path: Path) -> None:
    """Grouping must not make the corpus look smaller than it is. Both numbers are true and
    both are reported: decisions taken, and findings those decisions covered."""
    same = {"gate": "coverage", "detail": "d", "line": 2, "excerpt": ""}
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[same, same, same]))

    s = io.StringIO()
    cli_review.main([str(out)], stream=s, read_line=_keys("k"))

    assert CorrectionLog.for_run(out).summary()["findings_covered"] == 3
    assert "3 finding(s)" in s.getvalue()


# --------------------------------------------------------------- the crop verdict


def _diagram_page(out_dir: Path, page: int, *, source: Path, markers: int = 1):
    body = ("\\begin{document}\nSome prose.\n"
            + "".join(f"\\texttt{{[TODO diagram: drawing {i}]}}\n" for i in range(markers))
            + "More prose.\n\\end{document}\n")
    tex = out_dir / f"page-{page:04d}.tex"
    tex.write_text(body, encoding="utf-8")
    (out_dir / "pages").mkdir(parents=True, exist_ok=True)
    (out_dir / "pages" / f"p-{page:04d}-01.png").write_bytes(b"\x89PNG")
    finding = {"gate": "coverage", "detail": "recognizer fabricated a drawing here",
               "line": 3, "excerpt": ""}
    return {"page": page, "output": str(tex), "verdict": "fail", "gates": {}, "error": None,
            "rules": 0, "source": str(source), "findings": [finding] * markers}


def test_crop_replaces_the_marker_with_the_drawing(tmp_path: Path, monkeypatch) -> None:
    r"""The verdict the review loop was missing.

    45 of 49 findings on a real run are fabricated diagrams, and a terminal cannot show a
    drawing — so `edit` is useless when the correct fix is "here is the picture". The crop is
    cut from the source as **vector**, which also preserves ink colour for free: page 3's green
    cone legs against grey base-diagram arrows survive without anyone naming them.
    """
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path, _diagram_page(tmp_path, 1, source=src))

    cut = {}

    def fake_crop(pdf, page, target, **region):
        cut.update(region)
        target.write_bytes(b"%PDF-1.4 cropped\n")
        return target

    monkeypatch.setattr(cli_review.rasterize, "crop_vector", fake_crop)
    monkeypatch.setattr(cli_review.rasterize, "page_blocks",
                        lambda *a, **k: (cli_review.rasterize.Block(10, 20, 100, 80, 5),))
    monkeypatch.setattr(cli_review.rasterize, "page_size", lambda p: (514.0, 685.0))

    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("c", "1", "y"),
                    open_file=lambda p: None)

    text = (tmp_path / "page-0001.tex").read_text()
    assert "includegraphics" in text, "the drawing must replace the marker"
    assert "TODO diagram" not in text, "the marker must be gone once a real figure replaces it"
    assert cut == {"x": 10, "y": 20, "width": 100, "height": 80}

    (row,) = CorrectionLog.for_run(out).read()
    assert row.verdict == "cropped" and row.is_gold
    assert row.after.endswith(".pdf")


def test_a_rejected_crop_leaves_the_document_untouched(tmp_path: Path, monkeypatch) -> None:
    """Cut, look, decide. A crop the human rejects must not have edited anything — the marker
    is the only evidence a diagram was there."""
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path, _diagram_page(tmp_path, 1, source=src))
    before = (tmp_path / "page-0001.tex").read_text()

    monkeypatch.setattr(cli_review.rasterize, "crop_vector",
                        lambda pdf, page, target, **r: (target.write_bytes(b"x"), target)[1])
    monkeypatch.setattr(cli_review.rasterize, "page_blocks",
                        lambda *a, **k: (cli_review.rasterize.Block(10, 20, 100, 80, 5),))
    monkeypatch.setattr(cli_review.rasterize, "page_size", lambda p: (514.0, 685.0))

    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("c", "1", "n", "q"),
                    open_file=lambda p: None)
    assert (tmp_path / "page-0001.tex").read_text() == before


def test_crop_needs_a_source_and_says_so_when_there_is_none(tmp_path: Path) -> None:
    """Manifests written before `source` existed have none. Crashing on them, or silently
    doing nothing, are both worse than saying why."""
    page = _diagram_page(tmp_path, 1, source=Path("/nonexistent.pdf"))
    page.pop("source")
    out = _manifest(tmp_path, page)

    s = io.StringIO()
    cli_review.main([str(out)], stream=s, read_line=_keys("c", "q"), open_file=lambda p: None)
    assert "source" in s.getvalue().lower()


# --------------------------------------------------------------- the exit criterion


def test_transcribing_from_blank_records_the_other_arm(tmp_path: Path, monkeypatch) -> None:
    """The M0 exit criterion is a comparison, and only one side of it was ever measured.

    `handzoo-review` timed corrections. Nothing timed transcription from a blank file, so the
    question the whole milestone turns on — is correcting faster than typing it yourself —
    needed a stopwatch and a separate notebook. Both arms now land in one log.
    """
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))

    monkeypatch.setattr(cli_review, "_edit",
                        lambda path, line: (path.write_text("what the author typed\n"),
                                            "what the author typed\n")[1])

    s = io.StringIO()
    assert cli_review.main([str(out), "--transcribe", "1"], stream=s,
                           read_line=_keys(""), open_file=lambda p: None) == 0

    (row,) = CorrectionLog.for_run(out).read()
    assert row.verdict == "transcribed"
    assert row.after.strip() == "what the author typed"
    assert row.seconds >= 0


def test_a_transcription_is_not_evidence_about_the_emitted_document(tmp_path: Path) -> None:
    """It is ground truth for the *page*, and says nothing about what the tool produced.

    Folding it into GOLD would inflate the count of rows that judge the output with rows that
    never looked at it — the same conflation `keep-unreviewed` exists to prevent.
    """
    from handzoo.core.corrections import GOLD

    assert "transcribed" not in GOLD


def test_transcribing_a_page_already_reviewed_is_refused(tmp_path: Path) -> None:
    """Reading the emitted text contaminates the timing.

    Once a page has been reviewed, the author knows what is on it, and "minutes to transcribe
    from blank" is no longer measurable there. Refusing is the whole value: a contaminated
    number that looks clean is worse than no number, and this is the one measurement M0 turns
    on.
    """
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out)], stream=io.StringIO(), read_line=_keys("k"),
                    open_file=lambda p: None)

    s = io.StringIO()
    assert cli_review.main([str(out), "--transcribe", "1"], stream=s,
                           read_line=_keys(""), open_file=lambda p: None) == 2
    assert "already been reviewed" in s.getvalue()
    assert len([r for r in CorrectionLog.for_run(out).read()
                if r.verdict == "transcribed"]) == 0


def test_the_summary_compares_the_two_arms_when_both_exist(tmp_path: Path) -> None:
    from handzoo.core.corrections import Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="cropped", source_image="", before="a", seconds=30.0))
    log.append(Correction(page=1, verdict="edited", source_image="", before="b", seconds=20.0))
    log.append(Correction(page=1, verdict="transcribed", source_image="", before="",
                          after="x", seconds=200.0))

    s = io.StringIO()
    cli_review.main([str(tmp_path), "--summary"], stream=s)
    text = s.getvalue()
    assert "exit criterion" in text.lower()
    assert "50" in text and "200" in text, "both arms have to be visible as numbers"


def test_an_abandoned_transcription_is_not_a_measurement(tmp_path: Path, monkeypatch) -> None:
    """Measured on the author's first real run: two attempts were opened and closed without
    typing anything, and both were logged — 14.4s and 4.9s, zero words — inflating the baseline
    arm by 19.3s before a single real number existed.

    Opening an editor and closing it is not a transcription. Recording it as one corrupts the
    exact measurement the milestone turns on, in the direction that flatters the tool.
    """
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    monkeypatch.setattr(cli_review, "_edit", lambda path, line: "")

    s = io.StringIO()
    cli_review.main([str(out), "--transcribe", "1"], stream=s, read_line=_keys(""),
                    open_file=lambda p: None)

    assert CorrectionLog.for_run(out).read() == []
    assert "nothing was typed" in s.getvalue().lower()


def test_the_exit_criterion_ignores_empty_transcriptions_already_logged(tmp_path: Path) -> None:
    """The log is append-only by design — it records what happened, and is not rewritten. So
    the *interpretation* has to exclude the abandoned attempts, or every log written before the
    guard existed reports a baseline that is too large."""
    from handzoo.core.corrections import Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="transcribed", source_image="", before="",
                          after="", seconds=14.4))
    log.append(Correction(page=1, verdict="transcribed", source_image="", before="",
                          after="real text here", seconds=146.7))
    log.append(Correction(page=1, verdict="edited", source_image="", before="a", seconds=30.0))

    arms = log.summary()["exit_criterion"][1]
    assert arms["transcribing"] == 146.7, "the abandoned 14.4s attempt must not count"
