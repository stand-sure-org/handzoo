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

**And the manifest is a record, not the disk.** Every path here comes from a row written at
some earlier moment, and the file it names can be gone by now — deleted, moved with the
project, or renamed by a re-gate that quarantined the page. Until this was handled, `assemble`
read it unguarded: a run of several hours ended in a bare `FileNotFoundError` instead of a
chapter, and through the ingest thread it surfaced as `state: "failed"`, which is a lie — every
page had been recognized and written, and only the assembly broke. The standalone check had
already anticipated exactly that file being absent, one line earlier, and swallowed the error
-- so the knowledge was there and only the second read was unguarded. It is now one read,
decided from what came back.

A page whose text is not on disk is now the same kind of event as a page that failed a gate:
**a visible placeholder, never a crash and never a silent hole**, and worded as its own fact.
"the file is gone" and "a gate refused it" send the author to different places, which is the
distinction the excluded marker already draws.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .normalize import chapter_preamble
from .pipeline import PageOutcome

MASTER = "chapter.tex"


def _standalone_placeholder(outcome: PageOutcome) -> str:
    stem = Path(outcome.output).name if outcome.output else f"page-{outcome.page:04d}"
    return (f"% {stem} is a standalone document and cannot be \\input.\n"
            f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: standalone, not "
            f"assemblable --- re-run without --standalone]}}}}\\end{{center}}\n")


QUARANTINED = ".fail.tex"


def _quarantined_sibling(source: Path) -> Path | None:
    """The quarantine name for a page file, if a file is sitting under it.

    This is the realistic way a recorded path goes missing. A corrected page is re-gated on
    save, and a page that now fails is renamed to `.fail.tex` while the manifest is amended to
    match -- two operations, so they can disagree, and `_amend_manifest`'s own docstring says
    what it costs: "a rename it does not know about points them all at a file that is gone."
    Naming the sibling turns "your text has vanished" into "your text is in quarantine", which
    are very different messages to read after a long run.
    """
    if source.name.endswith(QUARANTINED):
        return None
    sibling = source.with_name(source.name[: -len(".tex")] + QUARANTINED)
    return sibling if sibling.exists() else None


def _absent_placeholder(outcome: PageOutcome, source: Path) -> str:
    """Said as its own fact. Not "failed": nothing refused this page, its file is not there."""
    sibling = _quarantined_sibling(source)
    if sibling is not None:
        return (f"% {source.name} is not on disk; {sibling.name} is. The page is quarantined "
                f"and the manifest has not caught up.\n"
                f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: in quarantine as "
                f"{_tt(sibling.name)} --- not included]}}}}\\end{{center}}\n")
    return (f"% {source.name} is recorded as usable but is not on disk.\n"
            f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: text file missing "
            f"--- {_tt(source.name)} is not on disk]}}}}\\end{{center}}\n")


def _released_sibling(source: Path) -> Path | None:
    """The un-quarantined name for a page file, if a file is sitting under it.

    The mirror of `_quarantined_sibling`, and reachable the same way. A save that fixes a
    quarantined page renames it back to `.tex` and amends the manifest -- two operations, and
    if only the rename lands, the newest row still says `fail`. Worth its own message because
    a bare failure placeholder would tell the author their page is broken *after they fixed
    it*, which is the most discouraging thing this file could say.
    """
    if not source.name.endswith(QUARANTINED):
        return None
    sibling = source.with_name(source.name[: -len(QUARANTINED)] + ".tex")
    return sibling if sibling.exists() else None


def _empty_placeholder(outcome: PageOutcome, source: Path) -> str:
    """Blank by damage or blank on purpose -- either way it must not be an invisible hole.

    An *emitted* page is never blank: it carries at least its provenance comment. But the
    surface writes whatever the author types and does not refuse an empty save, so a page can
    also be cleared deliberately. `\\input`ing it either way contributes nothing and leaves a
    chapter that reads as complete, which is the case this module exists to refuse -- so the
    chapter says the page is empty, and the author can see it and decide. Cutting a page
    properly is `--exclude N`, which is recorded and says so in the chapter in its own words.
    """
    return (f"% {source.name} is on disk but has no content.\n"
            f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: text file is empty "
            f"--- {_tt(source.name)}]}}}}\\end{{center}}\n")


def _tt(name: str) -> str:
    r"""A filename inside `\texttt`. Page files are `page-0001.tex`, so the underscore that
    would end the math-free run is not expected -- but the placeholder exists to survive a
    surprise, and a placeholder that breaks the build it is reporting on is worse than the
    problem."""
    return name.replace("\\", "/").replace("_", "\\_").replace("#", "\\#")


def _placeholder(outcome: PageOutcome) -> str:
    failed = ", ".join(g for g, v in (outcome.gates or {}).items() if v == "fail") or "a gate"
    stem = Path(outcome.output).name if outcome.output else f"page-{outcome.page:04d}"
    return (f"% {stem} failed: {failed}\n"
            f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page} MISSING --- failed "
            f"{failed}; not included]}}}}\\end{{center}}\n")


def assemble(out_dir: Path, outcomes: list[PageOutcome], *, name: str = MASTER,
             target=None,
             on_absent: Callable[[int, str], None] | None = None) -> Path:
    """Write the master document that `\\input`s every page that passed.

    Ordering is by page number, because page order *is* the document — a manifest written with
    `--resume` or a page range is not necessarily in order, and sorting the caller's list would
    be relying on how it happened to be built.

    `on_absent` hears `(page, why)` for every page whose text was not where the row said. The chapter
    says so in itself, but a chapter is only read when someone opens it, and the author reads
    the terminal: a warning computed and never printed is the same as none. Same idiom as
    `convert`'s `on_kept`, and the same reason -- this is the one moment the comparison can be
    made, since nothing afterwards knows which rows the manifest expected to find.
    """
    if target is None:
        # Loaded here rather than asked of the caller, because three of the four callers
        # forgot -- `cli_convert` among them, with `target` in scope, which is the command
        # that takes `--preamble` in the first place. So a project built inside the author's
        # preamble got a `chapter.tex` carrying *our* generated one, and the claim that
        # `--preamble` governs what the project produces was false for the master document
        # from the moment it shipped. The feature's own tests passed `target=` explicitly, so
        # nothing was testing the path any caller actually takes.
        #
        # `Target.load` answers `generated()` for a project with no `preamble.tex`, which is
        # every corpus on disk -- so this changes nothing for them, and a fifth caller cannot
        # reintroduce it.
        from .target import Target
        target = Target.load(out_dir)
    body: list[str] = []
    included: list[str] = []
    absent: list[int] = []
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
        if not outcome.output:
            body.append(_placeholder(outcome))
            continue
        source = Path(outcome.output)
        if outcome.verdict == "fail":
            # Refused by a gate: never read, never included. But check whether the file moved
            # out from under the row before repeating the row's verdict at the author -- a
            # release that renamed without amending would otherwise have the chapter calling
            # a page broken after they had fixed it.
            released = None if source.exists() else _released_sibling(source)
            if released is None:
                body.append(_placeholder(outcome))
            else:
                body.append(
                    f"% {source.name} is not on disk; {released.name} is. The page was "
                    f"released from quarantine and the manifest has not caught up.\n"
                    f"\\begin{{center}}\\fbox{{\\texttt{{[PAGE {outcome.page}: released from "
                    f"quarantine as {_tt(released.name)} --- the run still records it as "
                    f"refused, so it is not included]}}}}\\end{{center}}\n")
                if on_absent:
                    on_absent(outcome.page,
                              f"recorded as refused, but released to {released.name}")
            continue
        # Read once, here, and decide from what came back. Asking `exists()` and then reading
        # is two answers about one file with a gap in between -- and the gap is real, because
        # the surface re-gates and renames pages while a run is assembling.
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            body.append(_absent_placeholder(outcome, source))
            absent.append(outcome.page)
            sibling = _quarantined_sibling(source)
            if on_absent:
                on_absent(outcome.page,
                          f"quarantined as {sibling.name}" if sibling is not None
                          else f"{source.name} is not on disk")
            continue
        if not text.strip():
            body.append(_empty_placeholder(outcome, source))
            absent.append(outcome.page)
            if on_absent:
                on_absent(outcome.page, f"{source.name} is empty")
            continue
        if "\\documentclass" in text:
            # A page carrying its own `\\documentclass` cannot be `\\input` into another
            # document: it fails with "Can be used only in preamble." -- measured on a real
            # run that used `--standalone`, which is what makes the compile gate run per page.
            # The two pull against each other, and the tension is surfaced, not worked around.
            body.append(_standalone_placeholder(outcome))
            continue
        body.append(f"\\input{{{source.name.removesuffix('.tex')}}}\n")
        included.append(text)
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
        elif absent:
            # "no page passed its gates" would be false and would send the author looking at
            # the recognizer. These pages passed; their text is not where the manifest says.
            body.append(f"% {len(absent)} page(s) passed their gates but no text was found on "
                        f"disk for any of them, so this document has no content.\n"
                        f"\\begin{{center}}\\texttt{{[no page text found on disk --- nothing "
                        f"to assemble]}}\\end{{center}}\n")
        else:
            body.append("% no page passed its gates, so this document has no content.\n"
                        "\\begin{center}\\texttt{[no page passed --- nothing to assemble]}"
                        "\\end{center}\n")

    # The master owns the preamble, so it owns every declaration a fragment cannot make for
    # itself: characters `pylatexenc` cannot map (measured on the 126-page Number theory run,
    # where 9 pages failed the ASCII gate with no remedy inside a fragment -- a checkmark
    # asserting an axiom holds is a term in the sentence, not decoration), and macros the
    # recognizer invented, which a fragment is only told about and a standalone page declares.
    preamble = chapter_preamble(included, target=target)

    master = out_dir / name
    master.write_text(
        preamble
        + "% --- assembled by handzoo. Pages that failed a gate appear as placeholders,\n"
          "% --- never silently omitted: a chapter with an invisible hole reads as complete.\n"
        + "\\begin{document}\n" + "\n".join(body) + "\\end{document}\n",
        encoding="utf-8")
    return master
