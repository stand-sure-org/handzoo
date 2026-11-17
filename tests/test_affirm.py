"""The author's affirmation that a correction still holds for the page beneath it.

DESIGN 11.1.3c. A corrected page is kept by every later run, and the run warns when the source
moved under it -- but before this there was no way to *answer* that warning. Both `changed` and
`unknown` were permanent, so every corrected page in all nine projects on disk read as adrift
for ever, and a warning that can never be answered is one the reader learns to scroll past.

The shape of the answer was the author's call, 2026-11-17: **explicit and per page**. No bulk
baseline, and a save does not quietly affirm. The only thing that may clear the warning is a
person looking at the page as it now stands and saying the text still holds.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from handzoo.core import store
from handzoo.core.corrections import AFFIRMATIONS_NAME, Affirmation, affirm, affirmations
from handzoo.core.pipeline import PageOutcome, baseline_for, drift_between


def test_an_absent_file_is_no_affirmations_not_an_error(tmp_path: Path) -> None:
    """A project that has never been affirmed is the normal case, including all nine on disk."""
    assert affirmations(tmp_path) == {}


def test_affirmations_are_appended_never_rewritten(tmp_path: Path) -> None:
    """Append-only for the same reason the correction log is: a record that can be rewritten is
    a record of what someone last believed, not of what happened. The newest row per page is
    what counts, and the older ones stay as history."""
    affirm(tmp_path, Affirmation(page=3, page_hash="aaa", render_dpi=150, at=100.0))
    affirm(tmp_path, Affirmation(page=3, page_hash="bbb", render_dpi=150, at=200.0))
    affirm(tmp_path, Affirmation(page=9, page_hash="ccc", render_dpi=200, at=150.0))

    lines = (tmp_path / AFFIRMATIONS_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, "nothing was overwritten"
    assert [json.loads(ln)["page_hash"] for ln in lines] == ["aaa", "bbb", "ccc"]

    newest = affirmations(tmp_path)
    assert newest[3].page_hash == "bbb"
    assert (newest[9].page_hash, newest[9].render_dpi) == ("ccc", 200)


def test_an_affirmation_about_a_page_that_left_does_not_cover_its_replacement(
        tmp_path: Path) -> None:
    """Page numbers are reused after an undo, and page 3 of a later import is not page 3 of the
    undone one. An affirmation that carried over would hand the newcomer a baseline nobody ever
    looked at -- which is precisely the claim this whole mechanism exists to prevent, arriving
    through the back door.

    Same birth filter as `corrections.current()`, and for the same reason.
    """
    (tmp_path / store.STORE).mkdir()
    affirm(tmp_path, Affirmation(page=3, page_hash="about-the-old-page",
                                 render_dpi=150, at=100.0))
    store.mark_born(tmp_path, range(3, 4), at=200.0)

    assert affirmations(tmp_path) == {}, "the page it described has left the project"

    affirm(tmp_path, Affirmation(page=3, page_hash="about-the-new-one",
                                 render_dpi=150, at=300.0))
    assert affirmations(tmp_path)[3].page_hash == "about-the-new-one"


def test_an_affirmation_outranks_what_the_run_recorded(tmp_path: Path) -> None:
    """It is the newer and the stronger claim: a person looked at the page as it then stood,
    where the manifest only records what the recognizer was handed."""
    recorded = PageOutcome(page=1, output=None, verdict="pass", gates={},
                           page_hash="what-the-run-saw", render_dpi=150)
    assert baseline_for(tmp_path, 1, recorded) == ("what-the-run-saw", 150)

    affirm(tmp_path, Affirmation(page=1, page_hash="what-the-author-saw", render_dpi=150,
                                 supersedes="what-the-run-saw"))
    assert baseline_for(tmp_path, 1, recorded) == ("what-the-author-saw", 150)


def test_an_affirmation_stops_applying_once_the_page_is_read_again(tmp_path: Path) -> None:
    """An affirmation is about a correction, and it has to retire when that correction does.

    `--replace N` re-reads the page and records a new hash, while the log row that protects
    the page survives -- so without this the stale affirmation kept outranking the newer row
    and the next run reported `changed` on text written against exactly that render. Measured
    through `convert` as well, which is where it was found.
    """
    affirm(tmp_path, Affirmation(page=1, page_hash="what-the-author-saw", render_dpi=150,
                                 supersedes="the-old-row"))
    stale = PageOutcome(page=1, output=None, verdict="pass", gates={},
                        page_hash="the-old-row", render_dpi=150)
    assert baseline_for(tmp_path, 1, stale) == ("what-the-author-saw", 150)

    re_read = PageOutcome(page=1, output=None, verdict="pass", gates={},
                          page_hash="read-again-since", render_dpi=150)
    assert baseline_for(tmp_path, 1, re_read) == ("read-again-since", 150), \
        "the manifest is believed again the moment it records something else"


def test_a_page_with_no_record_at_all_has_no_baseline(tmp_path: Path) -> None:
    """`("", 0)` rather than anything that could compare equal to something."""
    assert baseline_for(tmp_path, 1, None) == ("", 0)


def test_only_matching_evidence_at_a_matching_resolution_reads_unchanged() -> None:
    """`unchanged` is the one answer that requires positive evidence. Everything else -- a
    missing hash on either side, a missing resolution, two different resolutions -- is
    *unknown*, because a comparison that could not run must never read as one that found
    nothing (constraint #6)."""
    same = {"was_hash": "a", "was_dpi": 150, "now_hash": "a", "now_dpi": 150}
    assert drift_between(**same) == "unchanged"
    assert drift_between(**{**same, "now_hash": "b"}) == "changed"

    for broken in ({"was_hash": ""}, {"now_hash": ""}, {"was_dpi": 0},
                   {"now_dpi": 0}, {"now_dpi": 200}, {"was_dpi": 200}):
        assert drift_between(**{**same, **broken}) == "unknown", broken


def test_an_affirmation_records_when_it_was_made(tmp_path: Path) -> None:
    """The surface shows the date rather than letting the row vanish: "nobody ever checked" and
    "a person checked, on this day" are different facts, and the second is worth keeping."""
    before = time.time()
    affirm(tmp_path, Affirmation(page=1, page_hash="a", render_dpi=150))
    assert affirmations(tmp_path)[1].at >= before
