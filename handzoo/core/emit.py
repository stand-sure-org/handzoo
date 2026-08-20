"""Emitter: a `Recognition` and its verdicts become a document, plus an honest report.

Two things here are deliberate and easy to get wrong.

**`--fragment` is the normal mode, not a convenience flag.** A manuscript is assembled from
pages with `\\input`, and a reMarkable page break is not a manuscript page break. `--standalone`
is for *reviewing a single page*. Emitting standalone by default would bake a page boundary
into the book.

**Provenance is emitted, not assumed.** The corpus of `(page, output, correction)` triples is
the long-term asset; a `.tex` file with no record of which model produced it is worth much
less six months from now, when the model has moved and the output has not.
"""

from __future__ import annotations

from pathlib import Path

from dataclasses import dataclass
from typing import Literal

from .normalize import normalize
from .recognize.base import Recognition
from .validate.base import GateResult

Target = Literal["latex"]
"""`markdown` is an M1 feature. A prose-only document in the corpus, and the author's existing
Typora/`render_tikz.py` workflow, both argue for it — but it is not M0."""

Mode = Literal["standalone", "fragment"]


@dataclass(frozen=True, slots=True)
class Emission:
    """A rendered page and everything a caller needs to decide what to do with it."""

    text: str
    gates: tuple[GateResult, ...]
    rules: tuple[str, ...] = ()
    page: int | None = None

    @property
    def failed(self) -> bool:
        """A gate actively refused this document."""
        return any(g.checked and g.failures for g in self.gates)

    @property
    def unverified(self) -> tuple[str, ...]:
        """Gates that could not run. Not failures, and emphatically not passes."""
        return tuple(g.gate for g in self.gates if not g.checked)

    @property
    def passed(self) -> bool:
        """Everything that could be checked was, and none of it refused."""
        return not self.failed and not self.unverified

    @property
    def verdict(self) -> str:
        """Three states, because two cannot express this honestly.

        `--fragment` output has no preamble, so the compile gate can never run on it. Folding
        "not verified" into "failed" would mark every fragment as a failure and make the
        signal carry no information; folding it into "passed" would claim a check that never
        happened. Neither is true, so the third state exists.
        """
        if self.failed:
            return "fail"
        return "unverified" if self.unverified else "pass"


def provenance(recognition: Recognition, *, page: int | None = None) -> str:
    """A comment block recording what produced this, and what was not checked."""
    where = f" page {page}" if page is not None else ""
    return (
        f"% handzoo{where}\n"
        f"% recognizer: {recognition.provider}/{recognition.model}\n"
        f"% marks inventoried: {len(recognition.inventory)}\n"
        "% NOT CHECKED: semantic correctness. The gates prove this document builds,\n"
        "% not that it says what the page says.\n"
    )


def emit(recognition: Recognition, gates: tuple[GateResult, ...] = (), *,
         target: Target = "latex", mode: Mode = "fragment",
         page: int | None = None, with_provenance: bool = True,
         base_dir: Path | None = None) -> Emission:
    """Normalize the recognized markup and render it for the chosen target and mode.

    `base_dir` is the run's output directory. R9 needs it to tell an invented
    `\\includegraphics` from one that points at a file actually on disk -- the crop
    verdict (DESIGN 7.2) emits the latter. Without it every reference is treated as
    fabricated, which is the safe default and not the useful one.
    """
    if target != "latex":
        raise ValueError(f"target {target!r} is not implemented in M0; only 'latex' is")

    result = normalize(recognition.markup, standalone=(mode == "standalone"),
                       base_dir=base_dir)
    text = result.text
    if with_provenance:
        text = provenance(recognition, page=page) + text

    return Emission(text=text, gates=tuple(gates), rules=tuple(result.rules), page=page)


def report(emission: Emission) -> str:
    """A verdict a human can act on, which never claims more than was checked.

    The rule this enforces: **never print an unqualified PASS.** A guardian that says PASS
    trains the reader to stop checking exactly the content it does not check, and substitution
    — output that is well-formed and false — is not checked by anything here.
    """
    lines = [g.report() for g in emission.gates]
    if emission.unverified:
        lines.append("  unverified: " + ", ".join(emission.unverified))
    lines.append(
        f"{emission.verdict.upper()} — gates prove this builds, not that it is true. "
        "Semantic substitution is not checked."
    )
    return "\n".join(lines)
