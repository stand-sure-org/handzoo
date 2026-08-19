"""The Recognizer port — the contract everything downstream codes against.

Two things here are load-bearing and were established by measurement, not by design
preference. Both are encoded in the types so they cannot be quietly forgotten.

**1. A recognizer's descriptions are untrustworthy; its positions are not.**

A separate inventory pass over a page reliably surfaced *every* mark, including glyphs the
transcription pass had silently folded into prose. Its counts and placements were correct.
Its descriptions were not: a dog glyph came back as "a stick figure lying down", and tally
marks as "two people".

So the coverage gate (DESIGN §5.4) is built on presence and position **only**. Nothing may
be built on `Mark.description` — see the field's own note.

**2. Structure that must not drift is not a string.**

`Tally(count=4)` exists because the recognizer converted `1 → 11 → 111 → 1111` into
`I → II → III → IIII`, collapsing the exact distinction the source page was teaching. A
string can drift under an "improving" model. An integer cannot.

This is the one place the design pushes back on *substitution* — the failure that produces
output which is ASCII-clean, delimiter-balanced, compiles, and is false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

Placement = Literal["inline", "block"]
"""Where a mark sits relative to the sentence.

`inline` marks are **terms in the sentence grammar** — a stick figure used as a noun, a
curved arrow annotating the line above, a checkmark asserting an axiom holds. They cannot be
cropped out without destroying the sentence, which is why the brief's crop-and-reference
policy does not cover them.

`block` marks are separable figures, and may be cropped (as vector — DESIGN §6.0).
"""

MarkKind = Literal["figure", "glyph", "arrow", "checkmark", "rule", "unknown"]


@dataclass(frozen=True, slots=True)
class Text:
    """A run of ordinary transcribed text."""

    value: str


@dataclass(frozen=True, slots=True)
class Mark:
    """A non-text mark on the page: a drawing, glyph, arrow or checkmark.

    Trust boundary, measured:

    - `context`, `placement`, `count` — **trustworthy.** Use these for the coverage gate.
    - `description` — **NOT trustworthy.** The recognizer names marks confidently and wrongly.
      It is here so a human reviewer has something to read, and for nothing else. Do not
      match on it, branch on it, or emit it as fact.
    """

    kind: MarkKind
    description: str
    context: str
    """Surrounding words the mark sits between — the positional anchor the gate matches on."""
    placement: Placement
    count: int = 1
    confidence: float | None = None
    """Populated only when a provider reports it. `None` means unknown, never "certain"."""


@dataclass(frozen=True, slots=True)
class Tally:
    """Tally marks, held as a count so they cannot drift into another notation."""

    count: int


@dataclass(frozen=True, slots=True)
class ColorSpan:
    """Ink colour carrying meaning.

    On the Naive Math fixtures the houses are labelled R/G/B *and written in* red, green and
    blue — the colour is the label. Losing it silently is the same class of failure as
    dropping a glyph, so it is modelled rather than discarded (DESIGN §6).
    """

    color: str
    inlines: tuple[Inline, ...]


Inline = Text | Mark | Tally | ColorSpan
"""One element of a page's inline content."""


@dataclass(frozen=True, slots=True)
class Recognition:
    """What a recognizer returns for a single page.

    `inventory` comes from a **second, independent pass**, not from the same call that
    produced `markup`. That separation is the whole point: a self-report from the pass being
    audited has already decided to drop the glyph. Measured — the independent pass surfaced
    exactly what transcription discarded.
    """

    markup: str
    inventory: tuple[Mark, ...] = ()
    provider: str = ""
    model: str = ""
    inlines: tuple[Inline, ...] = field(default_factory=tuple)
    """Structured inline content, where a provider can supply it. Empty is legitimate:
    a provider that only returns markup is still a valid recognizer."""

    def marks(self, placement: Placement | None = None) -> tuple[Mark, ...]:
        """Inventory marks, optionally filtered by placement."""
        if placement is None:
            return self.inventory
        return tuple(m for m in self.inventory if m.placement == placement)


@runtime_checkable
class Recognizer(Protocol):
    """Port: page image in, `Recognition` out.

    Implementations either return a `Recognition` with non-empty `markup`, or raise. An empty
    result is never returned as success — on the checkpoint we no longer use, an empty
    response arrived with `done_reason: "stop"` and looked exactly like a clean one.
    """

    def recognize(self, page: Path) -> Recognition: ...
