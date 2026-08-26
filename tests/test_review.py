"""The correction loop.

The interaction is stubbed — `read_line` and `stream` are injected — so these assert what the
loop *records*, which is the part that becomes corpus and outlives the session.
"""

from __future__ import annotations

import io
import subprocess
import json
from pathlib import Path

import pytest

from handzoo.adapters import cli_review
from handzoo.core.corrections import Correction, CorrectionLog
from handzoo.core.pipeline import MANIFEST, PageOutcome


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


def test_the_arms_the_cli_can_actually_produce_are_still_compared(tmp_path: Path) -> None:
    """The paired report could never fire on data the product creates.

    `--transcribe` refuses a page already reviewed; `--fix` refuses a page already
    transcribed. Both guards are correct -- reading the emitted text destroys the
    transcription measurement, and having typed the page destroys the correction one. But
    together they guarantee **no page ever carries both arms**, and the paired report shows
    only pages that do. So it printed nothing on a real run with six corrections and five
    transcriptions in the log, and the suite stayed green because this test built same-page
    rows straight through the log API, which the CLI cannot do.

    A reporter that cannot run on real data is DESIGN 5.7 with the check on the outside: the
    summary said nothing, and nothing read as no-comparison-available rather than as
    the-comparison-is-unpaired.
    """
    from handzoo.core.corrections import FIX_MARKER, Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    for page, seconds in ((9, 42.9), (14, 60.4), (11, 65.2)):
        log.append(Correction(page=page, verdict="edited", source_image="", before="a",
                              seconds=seconds, finding=FIX_MARKER))
    for page, seconds in ((2, 449.6), (12, 595.8)):
        log.append(Correction(page=page, verdict="transcribed", source_image="", before="",
                              after="typed text", seconds=seconds))

    arms = log.summary()["unpaired_arms"]
    assert arms["correcting"]["n"] == 3
    assert arms["transcribing"]["n"] == 2
    assert arms["correcting"]["median"] == 60.4
    assert arms["transcribing"]["median"] == 522.7


def test_a_finding_walk_row_is_not_a_correction_arm_measurement(tmp_path: Path) -> None:
    """The review loop times one keypress on one finding. `--fix` times a whole page.

    Summing the first and calling it the correction arm compares a whole-page task against a
    pile of keystrokes. Measured on the first real log: 75 finding rows produced a correcting
    median of **0.8s** against a transcription median of 522.7s, which reads as a spectacular
    result and is a category error.
    """
    from handzoo.core.corrections import FIX_MARKER, Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    for _ in range(40):
        log.append(Correction(page=25, verdict="keep-reviewed", source_image="", before="x",
                              seconds=0.8, finding="coverage: a fabricated diagram"))
    log.append(Correction(page=9, verdict="edited", source_image="", before="a", seconds=42.9,
                          finding=FIX_MARKER))
    log.append(Correction(page=2, verdict="transcribed", source_image="", before="",
                          after="typed", seconds=449.6))

    arms = log.summary()["unpaired_arms"]
    assert arms["correcting"]["n"] == 1, "40 finding-walk rows must not enter the arm"
    assert arms["correcting"]["median"] == 42.9


def test_an_unpaired_arm_alone_still_reports_nothing(tmp_path: Path) -> None:
    """One arm answers nothing. That was true of the paired report and stays true here."""
    from handzoo.core.corrections import Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="transcribed", source_image="", before="",
                          after="typed", seconds=100.0))
    assert log.summary()["unpaired_arms"] == {}


def test_an_abandoned_attempt_is_excluded_from_the_unpaired_arms_too(tmp_path: Path) -> None:
    """The empty-transcript exclusion has to hold on both paths, or the baseline arm is
    understated -- in the direction that flatters the tool."""
    from handzoo.core.corrections import FIX_MARKER, Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="transcribed", source_image="", before="",
                          after="", seconds=4.9))
    log.append(Correction(page=2, verdict="transcribed", source_image="", before="",
                          after="real", seconds=196.0))
    log.append(Correction(page=9, verdict="edited", source_image="", before="a", seconds=42.9,
                          finding=FIX_MARKER))

    arms = log.summary()["unpaired_arms"]
    assert arms["transcribing"]["n"] == 1, "the abandoned attempt must not count"
    assert arms["transcribing"]["median"] == 196.0


def test_a_timing_carries_the_mode_it_was_taken_in(tmp_path: Path, monkeypatch) -> None:
    """Correction cost depends on what the human edits *against* — the ink, the rendered PDF,
    an intermediary format, or a prompt to an agent (DESIGN §11.1). Those are different
    numbers, and pooling them silently makes the average meaningless.

    Evidence this is real: given a blank file and no instruction, the author wrote markdown
    headings and `% insert snip`, using one LaTeX command in three pages. The target format was
    not the working format, and nothing recorded that.
    """
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    monkeypatch.setattr(cli_review, "_edit", lambda path, line: "## a heading\n")

    cli_review.main([str(out), "--transcribe", "1", "--mode", "markdown"],
                    stream=io.StringIO(), read_line=_keys(""), open_file=lambda p: None)

    (row,) = CorrectionLog.for_run(out).read()
    assert row.mode == "markdown"


def test_the_summary_says_when_it_is_pooling_modes(tmp_path: Path) -> None:
    """Silently averaging across modes is the same class of error as a gate that cannot run
    reporting a pass: the number looks like one thing and is another."""
    from handzoo.core.corrections import Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="edited", source_image="", before="a", seconds=10.0,
                          mode="tex"))
    log.append(Correction(page=1, verdict="transcribed", source_image="", before="", after="x",
                          seconds=100.0, mode="markdown"))

    s = io.StringIO()
    cli_review.main([str(tmp_path), "--summary"], stream=s)
    assert "mode" in s.getvalue().lower()


# --------------------------------------------------- the correcting arm, measured the same way


def test_fix_times_the_same_interaction_seeded_with_our_output(tmp_path: Path,
                                                               monkeypatch) -> None:
    """The two arms were being measured through *different interactions*, not different
    starting points.

    `--transcribe` opened an editor and timed it. The correcting arm was a walk through
    findings, one keypress at a time. Comparing them measured the interaction as much as the
    content — and the finding-walk is not how anyone actually corrects a page. `--fix` runs the
    identical protocol as `--transcribe`, differing only in what the file starts with.
    """
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING],
                                    body="what the tool emitted\n"))

    seen = {}

    def fake_edit(path, line):
        seen["seeded"] = path.read_text()
        path.write_text("what the author fixed\n")
        return "what the author fixed\n"

    monkeypatch.setattr(cli_review, "_edit", fake_edit)
    assert cli_review.main([str(out), "--fix", "1", "--mode", "tex"], stream=io.StringIO(),
                           read_line=_keys(""), open_file=lambda p: None) == 0

    assert seen["seeded"] == "what the tool emitted\n", "the fix arm starts from our output"
    (row,) = CorrectionLog.for_run(out).read()
    assert row.verdict == "edited" and row.is_gold
    assert row.mode == "tex" and row.seconds >= 0
    assert row.before == "what the tool emitted\n"


def test_fixing_a_page_you_transcribed_is_refused(tmp_path: Path, monkeypatch) -> None:
    """The symmetric contamination, and the one that would have quietly favoured the tool.

    Having typed a page from blank, the author knows it by heart; correcting the same page is
    then far faster than correcting it cold, and the saving is memory rather than tooling. The
    arms have to run on **different pages**, which costs the pairing and is the honest trade —
    page difficulty varies, so several pages are needed either way.
    """
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path,
                    _page(tmp_path, 1, findings=[FINDING]),
                    _page(tmp_path, 2, findings=[FINDING]))
    monkeypatch.setattr(cli_review, "_edit", lambda path, line: "typed it\n")
    cli_review.main([str(out), "--transcribe", "1"], stream=io.StringIO(),
                    read_line=_keys(""), open_file=lambda p: None)

    s = io.StringIO()
    assert cli_review.main([str(out), "--fix", "1"], stream=s, read_line=_keys(""),
                           open_file=lambda p: None) == 2
    assert "transcribed" in s.getvalue()
    assert "page 2" in s.getvalue(), "it should say which pages are still clean"


def test_an_unchanged_fix_is_asked_about_rather_than_assumed(tmp_path: Path,
                                                             monkeypatch) -> None:
    """`--transcribe` can tell an abandoned attempt by its empty file. `--fix` cannot.

    A fix that changed nothing has two meanings, and they are opposites: the output was already
    correct — the most valuable datum this project can collect — or the editor was opened and
    closed. Guessing either way corrupts the arm, so it asks.
    """
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING], body="already right\n"))
    monkeypatch.setattr(cli_review, "_edit", lambda path, line: "already right\n")

    # abandoned -> nothing recorded
    cli_review.main([str(out), "--fix", "1"], stream=io.StringIO(),
                    read_line=_keys("", "a"), open_file=lambda p: None)
    assert CorrectionLog.for_run(out).read() == []

    # already correct -> recorded, and it is gold
    cli_review.main([str(out), "--fix", "1"], stream=io.StringIO(),
                    read_line=_keys("", "y"), open_file=lambda p: None)
    (row,) = CorrectionLog.for_run(out).read()
    assert row.verdict == "keep-reviewed" and row.is_gold


def test_fix_can_hand_over_the_typeset_pdf_instead_of_the_source(tmp_path: Path,
                                                                 monkeypatch) -> None:
    """The author reviews by annotating the typeset output, not by editing `.tex`.

    Timing them in an editor would measure a workflow they do not use, and would measure it
    *worse* than their real one — so the exit criterion's correcting arm would understate the
    tool through an artefact of the harness. §11.1 said mode changes the number; this is the
    author's mode.
    """
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING], body="Body text.\n"))

    handed = {}
    monkeypatch.setattr(cli_review, "_typeset", lambda o, d: (handed.setdefault("pdf", d / "x.pdf"),
                                                              d / "x.pdf")[1])
    cli_review.main([str(out), "--fix", "1", "--mode", "pdf-annotate"], stream=io.StringIO(),
                    read_line=_keys("", ""), open_file=lambda p: handed.setdefault("opened", p))

    (row,) = CorrectionLog.for_run(out).read()
    assert row.mode == "pdf-annotate"
    assert row.verdict in {"edited", "keep-reviewed"}
    assert handed.get("opened") is not None, "the author must be handed the typeset PDF"


def test_a_time_measured_off_tool_can_be_recorded(tmp_path: Path) -> None:
    """Reviewing on paper, or on a device the tool cannot see, still produces a real number.

    Refusing to record it would push the author back to a stopwatch and a notebook, which is
    the situation the harness exists to end.
    """
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out), "--fix", "1", "--mode", "paper", "--seconds", "480"],
                    stream=io.StringIO(), read_line=_keys(""), open_file=lambda p: None)

    (row,) = CorrectionLog.for_run(out).read()
    assert row.seconds == 480.0 and row.mode == "paper"


def test_an_off_tool_time_is_marked_as_self_reported(tmp_path: Path) -> None:
    """A number the tool measured and a number the author reported are different evidence, and
    the log must not blur them (DESIGN §5.7)."""
    out = _manifest(tmp_path, _page(tmp_path, 1, findings=[FINDING]))
    cli_review.main([str(out), "--fix", "1", "--mode", "paper", "--seconds", "480"],
                    stream=io.StringIO(), read_line=_keys(""), open_file=lambda p: None)

    (row,) = CorrectionLog.for_run(out).read()
    assert "self-reported" in row.finding


def test_a_standalone_page_is_typeset_directly_not_through_assembly(tmp_path: Path) -> None:
    r"""Caught before it could produce a wrong number.

    `_typeset` assembled a one-page master, and `assemble` cannot `\input` a standalone page —
    so on a `--standalone` run it produced a document containing only the placeholder *"PAGE 5:
    standalone, not assemblable"*. The author would have been handed a page with none of their
    content on it, and timed while annotating it. A measurement of nothing, reported as a
    measurement.

    A standalone page needs no assembly: it is already a document. Compile it as it stands.
    """
    page = tmp_path / "page-0001.tex"
    page.write_text("\\documentclass{article}\n\\begin{document}\nReal content here.\n"
                    "\\end{document}\n", encoding="utf-8")
    outcome = PageOutcome(page=1, output=str(page), verdict="pass", gates={}, findings=[])

    pdf = cli_review._typeset(outcome, tmp_path)
    if pdf is None:
        pytest.skip("pdflatex not installed")
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True,
                          text=True, check=False).stdout
    assert "Real content" in text
    assert "not assemblable" not in text


# --------------------------------------------------------------- authored


def test_authoring_is_not_a_correction(tmp_path: Path) -> None:
    r"""A page the author *rewrote* is not evidence about what the recognizer did.

    Correcting the transcription and improving one's own prose are different acts that leave
    the same shape of diff. Recorded together they contaminate three consumers (DESIGN
    §11.3.1): the exit-criterion timing, the defect taxonomy built from before/after, and —
    worst — a correction-mined lexicon, which would learn the author's taste as notation and
    feed it back to the recognizer as a token instruction.

    So `authored` exists, and is excluded from everything that reasons about recognizer
    quality.
    """
    from handzoo.core.corrections import BASELINE, GOLD, Correction, CorrectionLog

    assert "authored" not in GOLD, "authoring says nothing about what the recognizer produced"
    assert "authored" not in BASELINE, "it is not the control arm either"

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="authored", source_image="", before="a", after="b",
                          seconds=300.0, finding="author revised their own text"))
    assert log.summary()["gold_pairs"] == 0


def test_authoring_never_enters_the_correction_arm(tmp_path: Path) -> None:
    """It would inflate the arm with time spent on work the tool did not cause."""
    from handzoo.core.corrections import FIX_MARKER, Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=9, verdict="edited", source_image="", before="a", seconds=42.9,
                          finding=FIX_MARKER))
    log.append(Correction(page=10, verdict="authored", source_image="", before="a",
                          after="rewritten", seconds=600.0, finding=FIX_MARKER))
    log.append(Correction(page=2, verdict="transcribed", source_image="", before="",
                          after="typed", seconds=449.6))

    arms = log.summary()["unpaired_arms"]
    assert arms["correcting"]["n"] == 1, "the authored row must not enter the arm"
    assert arms["correcting"]["median"] == 42.9


def test_authored_rows_are_reported_separately_not_hidden(tmp_path: Path) -> None:
    """Excluded from the measurements, still visible. A row that vanishes is a row nobody
    can audit, and the author did spend that time."""
    from handzoo.core.corrections import Correction, CorrectionLog

    log = CorrectionLog(tmp_path / "corrections.jsonl")
    log.append(Correction(page=1, verdict="authored", source_image="", before="a", after="b",
                          seconds=300.0))
    s = log.summary()
    assert s["by_verdict"]["authored"] == 1
    assert s["rows"] == 1
