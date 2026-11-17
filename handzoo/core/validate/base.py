"""Shared shape for the validation gates.

A gate never returns a bare bool. The failure detail is the product: it is what the CLI
prints, what `handzoo review` routes a human to, and what a correction log will later record.
A boolean throws all of that away at the moment it is cheapest to keep.
"""

from __future__ import annotations

from dataclasses import dataclass

ADVISORY_GATES = frozenset({"reference", "pasted"})
"""Gates that flag for a human and never refuse the page (`GateResult.advisory`).

Named here because the manifest records only a gate's *name* against its findings, so a reader
deciding whether a page is broken has nothing else to go on. The surface had no list and
treated a pasted capture -- a decision to make, not a defect -- as a failure.
"""


@dataclass(frozen=True, slots=True)
class Failure:
    """One reason a gate refused the document."""

    detail: str
    line: int | None = None
    """1-indexed, where the gate can attribute the failure to a line."""
    pair_line: int | None = None
    """The *other* end of the pair, 1-indexed, where the failure has two ends and the gate
    knows both.

    A mismatched `\\end{...}` is reported at the closer but caused by what is open; a reader
    sent to one line without the other has to go hunting for the half that matters. `None` is
    "there is no second end, or it is not known" -- a closer with no opener and an opener with
    no closer each have exactly one location, and inventing a number for the missing one would
    point at a line the gate never examined (constraint #6).
    """
    column: int | None = None
    excerpt: str = ""
    """The offending text, trimmed. For a human to recognise, not for matching on."""

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "document"
        if self.column is not None:
            where += f", col {self.column}"
        if self.pair_line is not None:
            where += f" (opened line {self.pair_line})"
        text = f"  {where}: {self.detail}"
        return f"{text}\n    {self.excerpt}" if self.excerpt else text


@dataclass(frozen=True, slots=True)
class GateResult:
    """The verdict of a single gate."""

    gate: str
    failures: tuple[Failure, ...] = ()
    checked: bool = True
    """False when the gate could not run at all — a missing compiler, say.

    A gate that did not run is **not** a gate that passed, and the distinction has to survive
    into the report. Silent skips are how a suite goes green while checking nothing.
    """
    advisory: bool = False
    """True when findings are for a human to look at, not grounds to refuse the page.

    Every gate before this one enforced a property that is true or false — ASCII, balance,
    compilation. The reference gate enforces a *convention*: the author underlines the claims
    Cheng numbered, **normally**. A convention that holds most of the time cannot be a hard
    fail; a reader who meets a red page over a defensible exception learns to bypass the gate,
    and that costs more than the defect. So the finding stays visible and the page stays
    usable — the three-verdict reasoning (DESIGN 6) applied to a gate rather than a page.

    An advisory gate must never be counted as a pass either. It reports what it saw.
    """
    note: str = ""
    """Why it could not run. "SKIPPED" tells a reader that nothing was checked; it does not
    tell them whether that is expected, fixable, or a broken installation — and those call for
    different actions. A skip without a reason is only half the honesty."""

    def as_dicts(self) -> list[dict]:
        """The findings as the manifest and the review surface carry them.

        Hand-rolled at three call sites before this existed -- `pipeline.convert` and both
        halves of the surface's re-gate -- so a field added to `Failure` was dropped by all
        three while every test stayed green. The gate's own name is in scope here, which is
        the thing the copies each had to repeat.
        """
        return [{"gate": self.gate, "detail": f.detail, "line": f.line,
                 "pair_line": f.pair_line, "excerpt": f.excerpt} for f in self.failures]

    @property
    def passed(self) -> bool:
        return self.checked and not self.failures

    def __bool__(self) -> bool:
        return self.passed

    def report(self) -> str:
        if not self.checked:
            why = f" — {self.note}" if self.note else ""
            return f"{self.gate}: SKIPPED (could not run) — not a pass{why}"
        if not self.failures:
            return f"{self.gate}: PASS"
        lines = [f"{self.gate}: FAIL ({len(self.failures)})"]
        lines.extend(str(f) for f in self.failures)
        return "\n".join(lines)
