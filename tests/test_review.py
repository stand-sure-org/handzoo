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
