"""Shared shape for the validation gates.

A gate never returns a bare bool. The failure detail is the product: it is what the CLI
prints, what `handzoo review` routes a human to, and what a correction log will later record.
A boolean throws all of that away at the moment it is cheapest to keep.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Failure:
    """One reason a gate refused the document."""

    detail: str
    line: int | None = None
    """1-indexed, where the gate can attribute the failure to a line."""
    column: int | None = None
    excerpt: str = ""
    """The offending text, trimmed. For a human to recognise, not for matching on."""

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "document"
        if self.column is not None:
            where += f", col {self.column}"
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
