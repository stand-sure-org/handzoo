r"""Dropping a page from a run, and from the document it assembles into.

**Why this exists.** A corpus can contain pages that are not the author's to transcribe. The
Leinster exercises interleave photographed pages of a published book with the author's answers
(DESIGN §11.2.4), and running those pages produced a verbatim reproduction of the printed text
— which is both the wrong half of the page and someone else's writing.

**Excluded is not the same as absent.** `assemble` already refuses to omit a failed page
silently, on the grounds that *a chapter with an invisible hole reads as complete*. A page the
author deliberately cut is the same shape of claim and gets the same treatment: a visible
marker, not a gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from handzoo.core.assemble import assemble
from handzoo.core.pipeline import PageOutcome


def _passing(page: int, out_dir: Path) -> PageOutcome:
    f = out_dir / f"page-{page:04d}.tex"
    f.write_text(f"Content of page {page}.\n", encoding="utf-8")
    return PageOutcome(page=page, output=str(f), verdict="pass", gates={})


def test_an_excluded_page_leaves_a_visible_marker(tmp_path: Path) -> None:
    """Never silently omitted — the same rule a failed page gets."""
    outcomes = [_passing(1, tmp_path),
                PageOutcome(page=2, output=None, verdict="excluded", gates={}),
                _passing(3, tmp_path)]
    master = assemble(tmp_path, outcomes)
    text = master.read_text(encoding="utf-8")

    assert "page-0001" in text and "page-0003" in text
    assert "PAGE 2" in text
    assert "excluded" in text.lower()


def test_an_excluded_page_does_not_read_as_a_failure(tmp_path: Path) -> None:
    """A cut page is a decision, and a failed page is a defect. Saying "MISSING --- failed"
    over an author's choice would send them hunting for a problem that is not there."""
    outcomes = [PageOutcome(page=1, output=None, verdict="excluded", gates={})]
    text = assemble(tmp_path, outcomes).read_text(encoding="utf-8")

    assert "excluded by the author" in text
    assert "MISSING" not in text, "a cut page is not a missing one"
    # The header explains both kinds of placeholder, so "failed" appears there legitimately.
    # What must not happen is the *marker for this page* calling a decision a defect.
    marker = [l for l in text.splitlines() if "PAGE 1" in l][0]
    assert "failed" not in marker.lower()
    # ...and the summary line has to agree: "no page passed its gates" over a document the
    # author deliberately emptied is the same misreport one level up.
    assert "no page passed" not in text
    assert "all pages excluded" in text


def test_excluded_pages_are_never_recognized_at_all(tmp_path: Path) -> None:
    r"""The point is not to tidy the output. It is that the page is never sent to a model and
    never transcribed, so no reproduction of it exists to begin with.
    """
    from handzoo.core.pipeline import parse_excluded

    assert parse_excluded("1,4") == {1, 4}
    assert parse_excluded("1-3,7") == {1, 2, 3, 7}
    assert parse_excluded("") == set()
    assert parse_excluded(None) == set()


@pytest.mark.parametrize("bad", ["nope", "3-", "-2", "4-2"])
def test_a_malformed_exclusion_is_refused_not_guessed(bad: str) -> None:
    """Guessing here would silently transcribe a page the author meant to cut, which is the
    one outcome this feature exists to prevent."""
    from handzoo.core.pipeline import parse_excluded

    with pytest.raises(ValueError):
        parse_excluded(bad)


def test_a_new_verdict_cannot_crash_a_long_run() -> None:
    r"""`_format` indexed a dict by verdict, so `excluded` killed a run at the first cut page
    — after the work, and with a KeyError rather than anything a reader could act on.

    Adding a member to a verdict set touches every reader of it. This one now falls back
    instead of raising, because a run three hours in should not die over a label.
    """
    from handzoo.adapters.cli_convert import _format

    cut = PageOutcome(page=4, output=None, verdict="excluded", gates={})
    line = _format(cut)
    assert "CUT" in line
    assert "not transcribed" in line

    invented = PageOutcome(page=5, output=None, verdict="something-new", gates={})
    assert "????" in _format(invented), "an unknown verdict is reported, not raised"
