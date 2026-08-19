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

    @property
    def passed(self) -> bool:
        return self.checked and not self.failures

    def __bool__(self) -> bool:
        return self.passed

    def report(self) -> str:
        if not self.checked:
            return f"{self.gate}: SKIPPED (could not run) — not a pass"
        if not self.failures:
            return f"{self.gate}: PASS"
        lines = [f"{self.gate}: FAIL ({len(self.failures)})"]
        lines.extend(str(f) for f in self.failures)
        return "\n".join(lines)
