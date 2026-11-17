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

import re
from pathlib import Path

from dataclasses import dataclass
from typing import Literal

from .normalize import normalize
from .recognize.base import Recognition
from .validate.base import GateResult

Format = Literal["latex"]
"""The output format. Renamed from `Target`, which now means the typesetting target
(`core.target.Target`) -- what the document is built *into*, and by which engine."""
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
        return any(g.checked and g.failures and not g.advisory for g in self.gates)

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
         fmt: Format = "latex", mode: Mode = "fragment",
         page: int | None = None, with_provenance: bool = True,
         base_dir: Path | None = None, target=None) -> Emission:
    """Normalize the recognized markup and render it for the chosen target and mode.

    `base_dir` is the run's output directory. R9 needs it to tell an invented
    `\\includegraphics` from one that points at a file actually on disk -- the crop
    verdict (DESIGN 7.2) emits the latter. Without it every reference is treated as
    fabricated, which is the safe default and not the useful one.
    """
    if fmt != "latex":
        raise ValueError(f"format {fmt!r} is not implemented in M0; only 'latex' is")

    result = normalize(recognition.markup, standalone=(mode == "standalone"),
                       base_dir=base_dir, target=target)
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


# ----------------------------------------------------------------- figures and descriptions

_WHITESPACE = re.compile(r"\s+")

# Measured on TeX Live 2026, reading the `/Alt` string back out of the PDF rather than
# trusting the source. `\%`, `\{`, `\}`, `\&`, `\$`, `\#` and `\_` round-trip exactly.
# `\textbackslash`, `\textasciitilde` and `\textasciicircum` do **not**: the PDF-string
# conversion does not expand text commands and they arrive literally, as the words
# "\textbackslash" and so on. So those three are not usable here, whatever LaTeX habit says.
_ALT_ESCAPES = (
    ("\\", "\\string\\"),   # first: every rule below introduces backslashes of its own
    ("{", "\\{"), ("}", "\\}"),
    ("%", "\\%"), ("&", "\\&"), ("$", "\\$"), ("#", "\\#"), ("_", "\\_"),
)


def describe(description: str) -> str:
    r"""The author's prose, made safe to sit inside `alt={...}`.

    This is the one place author text crosses into LaTeX syntax, and getting it wrong does
    not fail loudly: an unbalanced `}` ends the key early and a `%` comments out the rest of
    the line, so a description could fail a page's compile -- or its delimiter gate -- over
    something the author never wrote as markup.

    `^` is passed through, and a `~` becomes a space, because that is what each already does:
    measured, both compile clean and `^` reaches `/Alt` unchanged while `~` arrives as a
    space either way. A backslash becomes `\string\`, which yields exactly one backslash and
    is safe in the three places that looked fragile -- before letters, before a space, and at
    the very end, where eating the key's closing brace was the worry. It absorbs one following
    space when the backslash runs into letters, which is the only loss here and is cosmetic.

    Newlines collapse: the description is one string in the PDF, and a blank line inside a
    macro argument would end the paragraph and take the argument with it.
    """
    text = _WHITESPACE.sub(" ", description.replace("~", " ")).strip()
    for char, escape in _ALT_ESCAPES:
        text = text.replace(char, escape)
    return text


def figure_tex(name: str, *, width: str, description: str = "", tagging: bool = False) -> str:
    r"""The `\includegraphics` for a crop, and what it says about being described.

    `alt=` is **silent without tagging**: measured, a page carrying a description compiles
    clean, exit 0, no warning, and the PDF comes out `Tagged: no` with zero `/Alt` entries.
    The description is still emitted -- it is the author's work, it costs nothing, and it
    starts working the day they add `\DocumentMetadata{tagging=on}` -- but a surface that
    took it without saying so would be promising a reader who is never reached.

    The missing-description note is written only under tagging. Without it the remark would
    be true of every crop in every project on disk, which is noise rather than information.
    """
    keys = f"width={width}"
    if description.strip():
        keys += f", alt={{{describe(description)}}}"
    figure = f"\\includegraphics[{keys}]{{{name}}}"
    if tagging and not description.strip():
        # On its own line: the marker it replaces can sit mid-sentence, and a trailing `%`
        # would comment out whatever followed it on that line.
        figure += "\n% handzoo: no description given -- this figure is unlabelled for a "
        figure += "screen reader.\n"
    return figure
