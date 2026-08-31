r"""Assembly — the loose pages of a run as one document.

Until this existed a run produced fragments and nothing else, so the author had to open a LaTeX
application just to *see* the chapter. That made HandZoo a step in someone else's pipeline by
construction rather than by choice (DESIGN §11.4).

**Fragments carry no preamble**, deliberately: `--fragment` is the normal mode because a
manuscript is assembled from parts, and a fragment that declared its own document class could
not be `\input` anywhere. So the master owns the preamble. That is also what makes a relative
`\includegraphics` resolve — LaTeX resolves graphics against the *master's* directory, and the
crop figures sit beside it, which quietly settles the caveat left open in §7.2.

**A failed page is neither included nor hidden.** Including it lets a broken page into the
build, which is the thing this project exists to refuse. Omitting it produces a chapter with a
hole that reads as complete, which is worse — the author has no way to notice page 4 is gone.
It appears as a visible placeholder naming the page and what failed.
"""

from __future__ import annotations

from pathlib import Path

from .declarations import declarations_for
from .normalize import PREAMBLE
from .validate.ascii_gate import non_ascii_chars
from .pipeline import PageOutcome

MASTER = "chapter.tex"


def _is_standalone(path: Path) -> bool:
    """A page that carries its own `\\documentclass` cannot be `\\input` into another document.

    `\\input`ing one fails with *"Can be used only in preamble."* -- measured on a real run that
    used `--standalone`, which is what makes the compile gate run per page. The two pull
    against each other, and the tension is surfaced rather than worked around.
    """
    try:
        return "\\documentclass" in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _standalone_placeholder(outcome: PageOutcome) -> str:
    stem = Path(outcome.output).name if outcome.output else f"page-{outcome.page:04d}"
    return (f"% {stem} is a standalone document and cannot be \\input.\n"
            f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: standalone, not "
            f"assemblable --- re-run without --standalone]}}}}\\end{{center}}\n")


def _placeholder(outcome: PageOutcome) -> str:
    failed = ", ".join(g for g, v in (outcome.gates or {}).items() if v == "fail") or "a gate"
    stem = Path(outcome.output).name if outcome.output else f"page-{outcome.page:04d}"
    return (f"% {stem} failed: {failed}\n"
            f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page} MISSING --- failed "
            f"{failed}; not included]}}}}\\end{{center}}\n")


def assemble(out_dir: Path, outcomes: list[PageOutcome], *, name: str = MASTER) -> Path:
    """Write the master document that `\\input`s every page that passed.

    Ordering is by page number, because page order *is* the document — a manifest written with
    `--resume` or a page range is not necessarily in order, and sorting the caller's list would
    be relying on how it happened to be built.
    """
    body: list[str] = []
    included: list[str] = []
    usable = 0
    for outcome in sorted(outcomes, key=lambda o: o.page):
        if outcome.verdict == "excluded":
            # Visible, and *not* worded as a failure. A cut page is a decision; a failed page
            # is a defect. Calling one the other sends the author hunting for a problem that
            # is not there -- while omitting it entirely would leave the hole `_placeholder`
            # exists to prevent.
            body.append(
                f"% page {outcome.page} was excluded by the author and never transcribed.\n"
                f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: excluded by the "
                f"author --- not transcribed]}}}}\\end{{center}}\n")
            continue
        if outcome.verdict == "fail" or not outcome.output:
            body.append(_placeholder(outcome))
            continue
        source = Path(outcome.output)
        if _is_standalone(source):
            body.append(_standalone_placeholder(outcome))
            continue
        stem = Path(outcome.output).name
        if stem.endswith(".tex"):
            stem = stem[: -len(".tex")]
        body.append(f"\\input{{{stem}}}\n")
        included.append(source.read_text(encoding="utf-8"))
        usable += 1

    if not usable:
        # "no page passed its gates" is wrong when nothing was refused and the author simply
        # cut everything. Reporting a failure over a decision sends them looking for a defect
        # that does not exist -- the same distinction the excluded marker draws above.
        excluded = [o for o in outcomes if o.verdict == "excluded"]
        if excluded and len(excluded) == len(outcomes):
            body.append("% every page was excluded by the author, so there is nothing to "
                        "assemble.\n\\begin{center}\\texttt{[all pages excluded --- nothing "
                        "to assemble]}\\end{center}\n")
        else:
            body.append("% no page passed its gates, so this document has no content.\n"
                        "\\begin{center}\\texttt{[no page passed --- nothing to assemble]}"
                        "\\end{center}\n")

    # Characters `pylatexenc` cannot map -- checkmarks, ballot crosses, circled digits -- have
    # no remedy inside a fragment, because `\\DeclareUnicodeCharacter` only works in a preamble.
    # The master owns the preamble, so the master owns this. Measured on the 126-page Number
    # theory run, where 9 pages failed the ASCII gate with no way to fix them; a checkmark
    # asserting an axiom holds is a term in the sentence, not decoration.
    residual = sorted({c for text in included for c in non_ascii_chars(text)})
    declarations = declarations_for([], residual) if residual else ""

    master = out_dir / name
    master.write_text(
        PREAMBLE
        + declarations
        + "% --- assembled by handzoo. Pages that failed a gate appear as placeholders,\n"
          "% --- never silently omitted: a chapter with an invisible hole reads as complete.\n"
        + "\\begin{document}\n" + "\n".join(body) + "\\end{document}\n",
        encoding="utf-8")
    return master
