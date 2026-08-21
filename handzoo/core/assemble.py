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

from .normalize import PREAMBLE
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
    usable = 0
    for outcome in sorted(outcomes, key=lambda o: o.page):
        if outcome.verdict == "fail" or not outcome.output:
            body.append(_placeholder(outcome))
            continue
        if _is_standalone(Path(outcome.output)):
            body.append(_standalone_placeholder(outcome))
            continue
        stem = Path(outcome.output).name
        if stem.endswith(".tex"):
            stem = stem[: -len(".tex")]
        body.append(f"\\input{{{stem}}}\n")
        usable += 1

    if not usable:
        body.append("% no page passed its gates, so this document has no content.\n"
                    "\\begin{center}\\texttt{[no page passed --- nothing to assemble]}"
                    "\\end{center}\n")

    master = out_dir / name
    master.write_text(
        PREAMBLE
        + "% --- assembled by handzoo. Pages that failed a gate appear as placeholders,\n"
          "% --- never silently omitted: a chapter with an invisible hole reads as complete.\n"
        + "\\begin{document}\n" + "\n".join(body) + "\\end{document}\n",
        encoding="utf-8")
    return master
