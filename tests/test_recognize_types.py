"""The recognizer contract, pinned.

These assert the properties the rest of the design leans on, so a later refactor cannot
quietly remove them.
"""

from __future__ import annotations

import dataclasses

import pytest

from handzoo.core.recognize.base import (
    ColorSpan,
    Mark,
    Recognition,
    Recognizer,
    Tally,
    Text,
)


def _mark(**kw) -> Mark:
    base = {"kind": "glyph", "description": "a stick figure",
            "context": "HAS MORE ... THAN", "placement": "inline"}
    return Mark(**{**base, **kw})


def test_tally_holds_an_integer_not_a_string() -> None:
    """The recognizer turned `1 -> 11 -> 111` into `I -> II -> III`.

    A count cannot make that drift; a string can. This is the design's one structural
    defence against substitution, so assert the type rather than trust it.
    """
    assert isinstance(Tally(count=4).count, int)


@pytest.mark.parametrize("obj", [
    Text(value="x"),
    _mark(),
    Tally(count=3),
    ColorSpan(color="red", inlines=()),
    Recognition(markup="x"),
])
def test_everything_is_immutable(obj) -> None:
    """Recognition results are evidence. Evidence that can be edited in place is not."""
    assert dataclasses.is_dataclass(obj)
    field_name = next(iter(dataclasses.fields(obj))).name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field_name, "mutated")


def test_confidence_defaults_to_unknown_not_certain() -> None:
    """`None` must mean "the provider did not say", never "certain"."""
    assert _mark().confidence is None


def test_marks_filters_by_placement() -> None:
    """The coverage gate cares about inline marks specifically — they cannot be cropped."""
    inline, block = _mark(), _mark(placement="block")
    r = Recognition(markup="x", inventory=(inline, block))

    assert r.marks() == (inline, block)
    assert r.marks("inline") == (inline,)
    assert r.marks("block") == (block,)


def test_empty_inventory_is_legitimate() -> None:
    """A provider that returns only markup is still a valid recognizer.

    The inventory is a second, independent pass; a recognizer that does not implement one
    should degrade, not fail construction.
    """
    assert Recognition(markup="x").inventory == ()


def test_protocol_is_satisfied_by_shape_alone() -> None:
    """The port is structural: no base class, no registration, no import of an adapter."""

    class Stub:
        def recognize(self, page):
            return Recognition(markup="stub")

    assert isinstance(Stub(), Recognizer)
