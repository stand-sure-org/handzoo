"""The coverage gate — the only gate that catches a failure the other three cannot see.

The case it exists for is real and measured. On Naive Math page 1 the recognizer read
"THE BLUE HOUSE HAS MORE [stick figure] THAN THE OTHERS" and "THE GREEN HOUSE HAS MORE
[dog] THAN THE OTHERS", silently deleted both glyphs, and emitted two bullets that
contradict each other — ASCII-clean, balanced, and compiling.
"""

from __future__ import annotations

import pytest

from handzoo.core.rasterize import InkProfile
from handzoo.core.recognize.base import Mark
from handzoo.core.validate import coverage_gate


def _mark(placement="inline", count=1, context="HAS MORE THAN THE OTHERS",
          description="a stick figure") -> Mark:
    return Mark(kind="unknown", description=description, context=context,
                placement=placement, count=count)


# --------------------------------------------------------------- the motivating case


def test_the_page_one_failure_is_caught() -> None:
    """Four inline glyphs seen, none emitted. This is the regression the gate exists for."""
    latex = ("\\item The Blue House, \"B\", has more than the others\n"
             "\\item The Green House, \"G\", has more than the others.\n")
    inventory = tuple(_mark() for _ in range(4))

    result = coverage_gate.check(latex, inventory)

    assert not result.passed
    assert "4 mark(s) seen" in result.failures[0].detail
    assert "0 accounted for" in result.failures[0].detail


def test_a_page_that_accounts_for_its_marks_passes() -> None:
    latex = "has more [[DIAGRAM: a stick figure]] than the others"
    assert coverage_gate.check(latex, (_mark(),)).passed


@pytest.mark.parametrize("marker", [
    "[[DIAGRAM: raw from the recognizer]]",
    "\\texttt{[TODO diagram: after the Normalizer]}",
])
def test_both_marker_shapes_count(marker: str) -> None:
    """The gate may run before or after normalization, and both forms are legitimate."""
    assert coverage_gate.check(f"text {marker} text", (_mark(),)).passed


def test_counts_are_respected_not_just_presence() -> None:
    """One marker does not discharge a mark the inventory saw three of."""
    latex = "one [[DIAGRAM: x]] only"
    assert not coverage_gate.check(latex, (_mark(count=3),)).passed


# --------------------------------------------------------------- what it is built on


def test_the_verdict_never_depends_on_the_description() -> None:
    """The recognizer names marks confidently and wrongly — it called a dog "a stick figure
    lying down" and tally marks "two people" — while getting counts and placements right.

    Same page, same positions, wildly different descriptions: the verdict must not move.
    """
    latex = "text with no markers at all"
    a = coverage_gate.check(latex, (_mark(description="a dog"),))
    b = coverage_gate.check(latex, (_mark(description="a stick figure lying down"),))
    assert a.passed == b.passed is False
    assert a.failures[0].detail == b.failures[0].detail


def test_block_marks_are_not_required_by_default() -> None:
    """A block diagram may legitimately be emitted as a cropped figure instead of a marker.
    An inline mark has nowhere else to go, which is why only those are demanded."""
    latex = "no markers here"
    assert coverage_gate.check(latex, (_mark(placement="block"),)).passed
    assert not coverage_gate.check(latex, (_mark(placement="block"),),
                                   require_block_marks=True).passed


def test_failures_point_at_a_line_using_context_not_description() -> None:
    latex = "first line\nthe BLUE HOUSE line is here\nthird line"
    result = coverage_gate.check(latex, (_mark(context="THE BLUE HOUSE HAS MORE"),))
    located = [f for f in result.failures if f.line]
    assert located and located[0].line == 2


# --------------------------------------------------------------- absence of evidence


def test_a_failed_inventory_does_not_pass() -> None:
    """The pass could not be read, so we do not know what is on the page.

    Reporting it green would be exactly the false confidence this gate exists to prevent.
    """
    result = coverage_gate.check("some markup", (), inventory_failed=True)
    assert not result.passed
    assert not result.checked
    assert "not a pass" in result.report()


def test_an_inventory_that_ran_and_found_nothing_is_evidence() -> None:
    """A check that ran and found nothing is not the same as a check that did not run.

    Measured: a symbolic page the author confirmed correct reported zero pictures. Treating
    that as unverifiable would leave every clean page permanently unverified — the content
    this tool handles best. The residual risk (a silently under-reporting inventory) is
    documented in the gate rather than papered over.
    """
    assert coverage_gate.check("some markup", ()).passed


def test_ink_distinguishes_a_blank_page_from_an_under_reporting_inventory() -> None:
    """Ink is the one signal here no vision model produced.

    Everything else — transcription, inventory, any model-reported confidence — comes from the
    same family of system and can be wrong in correlated ways. Ink cannot.
    """
    blank = InkProfile(points=3, bands=(0.0,) * 10)

    assert coverage_gate.check("markup", (), ink=blank).passed
    assert not coverage_gate.check("markup", (), ink=blank, inventory_failed=True).checked


# --------------------------------------------------------------- the honest limit


def test_the_gate_cannot_see_substitution() -> None:
    r"""Stated as a test so nobody mistakes coverage for correctness.

    `|||| < ||||` is `4 < 4`. Tally marks became Roman numerals. In both cases the mark is
    still *present* — it was replaced, not dropped — so a coverage check passes. Nothing in
    M0 detects this, and the suite should say so out loud.
    """
    substituted = r"\texttt{||||} < \texttt{||||}  % was 4 tallies vs a 5-bar gate"
    inventory = (_mark(count=2, description="tally strokes"),)

    # Two markers present, so coverage is satisfied — and the statement is still false.
    latex = substituted + " [[DIAGRAM: a]] [[DIAGRAM: b]]"
    assert coverage_gate.check(latex, inventory).passed


def test_a_flood_of_marks_is_capped_and_says_so() -> None:
    """Measured: a page of tally marks inventoried 285 separate marks.

    A reviewer facing 285 findings reviews none of them carefully — inspectors miss 20-30%
    of defects under repetitive load. The cap is announced, because a truncated list that
    reads as complete is the same lie as a skipped check that reads as a pass.
    """
    result = coverage_gate.check("no markers", tuple(_mark() for _ in range(60)))

    assert not result.passed
    assert len(result.failures) <= coverage_gate.MAX_LOCATED + 2
    assert any("more not listed" in f.detail for f in result.failures)
    assert any("upper bound" in f.detail for f in result.failures)


def test_an_unreadable_inventory_is_not_a_blank_page() -> None:
    """Both give an empty inventory. Only one of them is evidence about the page."""
    blank = InkProfile(points=3, bands=(0.0,) * 10)
    assert coverage_gate.check("x", (), ink=blank).passed
    assert not coverage_gate.check("x", (), ink=blank, inventory_failed=True).checked


def test_a_fabrication_report_is_not_evidence_a_mark_survived() -> None:
    """R9 replaces invented tikz with a marker. That marker says the model made something
    up — the opposite of evidence that a real mark was preserved.

    Measured: a page emitted 22 fabrication markers and passed a coverage check it should
    have failed, because both marker kinds shared one syntax.
    """
    fabricated = " ".join("\\texttt{[TODO fabricated: invented tikz]}" for _ in range(22))
    assert not coverage_gate.check(fabricated, (_mark(), _mark())).passed

    real = "\\texttt{[TODO diagram: a stick figure]} " * 2
    assert coverage_gate.check(real, (_mark(), _mark())).passed
