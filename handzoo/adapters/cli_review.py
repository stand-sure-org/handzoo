"""`handzoo review` — the correction loop. All logic lives in `handzoo.core`.

A validator with no way to act on what it validates is a linter, not a product. This is the
step that turns a gate failure into a decision, and every decision into a row of corpus.

**Findings first, not pages uniformly.** Confidence routing is settled practice across
Transkribus, eScriptorium, OCR-D and Textract: surface what the tool is unsure about rather
than everything at equal weight. The coverage gate already reports *"7 marks seen, 1
accounted for"* with lines, so review opens there.

**The reviewed/unreviewed distinction is enforced by the interaction, not promised.** Walking
a finding and pressing `k` records `keep-reviewed` because the text was on screen. Accepting a
page you never opened records `keep-unreviewed`. Those are different facts about the corpus and
the log keeps them apart, because automation bias is measured at 20-30% missed defects under
repetitive load and a tool that flatters its reviewer produces a corpus that flatters its model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from ..core import rasterize
from ..core.assemble import assemble
from ..core.corrections import BASELINE, Correction, CorrectionLog
from ..core.pipeline import MANIFEST, PageOutcome

PROMPT = "[k]eep  [e]dit  [c]rop  [f]lag  [s]kip  [q]uit > "

_MARKER = re.compile(r"\\texttt\{\[TODO (?:diagram|fabricated):.*?\]\}", re.S)
"""What R3 leaves behind where a drawing was. Replacing one with a real figure is the crop
verdict's entire job."""

CROP_WIDTH = r"0.6\textwidth"


def load_outcomes(out_dir: Path) -> list[PageOutcome]:
    path = out_dir / MANIFEST
    if not path.exists():
        raise FileNotFoundError(f"no {MANIFEST} in {out_dir} — run `handzoo` on a PDF first")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(PageOutcome(**json.loads(line)))
    return rows


def page_image(out_dir: Path, page: int) -> Path | None:
    hits = sorted((out_dir / "pages").glob(f"p-{page:04d}*.png"))
    return hits[0] if hits else None


def finding_key(page: int, finding: dict) -> tuple:
    """Identity of a decision, shared by the writer and the resume reader.

    Line is part of it: the same gate reporting the same detail on two different lines is
    two defects, and collapsing them would retire one the human never saw.
    """
    return (page, f"{finding['gate']}: {finding['detail']}", finding.get("line"))


def _group(page: int, findings: list[dict]) -> list[list[dict]]:
    """Findings that are identical in everything the human can act on, presented once.

    A gate may report one defect many times: ch18 page 25 carried 32 byte-identical
    fabrication findings, all on line 35. One per prompt, they rendered 32 consecutive frames
    with the same text and no repeat of the source filename -- which reads as a stalled tool.
    Nothing is discarded; the group keeps its members and the decision records the count.
    """
    order: dict[tuple, int] = {}
    groups: list[list[dict]] = []
    for finding in findings:
        key = finding_key(page, finding)
        if key in order:
            groups[order[key]].append(finding)
        else:
            order[key] = len(groups)
            groups.append([finding])
    return groups


def _context(text: str, line: int | None, radius: int = 2) -> str:
    """The emitted text around a finding, so the human sees it in place."""
    if not line:
        return ""
    lines = text.splitlines()
    lo, hi = max(0, line - 1 - radius), min(len(lines), line + radius)
    out = []
    for i in range(lo, hi):
        marker = ">>" if i == line - 1 else "  "
        out.append(f"  {marker} {i + 1:>4} | {lines[i][:90]}")
    return "\n".join(out)


def _edit(path: Path, line: int | None) -> str:
    """Open $EDITOR at the offending line and return what came back."""
    editor = os.environ.get("EDITOR", "vi")
    cmd = [editor, str(path)]
    if line and Path(editor).name in {"vi", "vim", "nvim", "nano", "emacs"}:
        cmd = [editor, f"+{line}", str(path)]
    subprocess.run(cmd, check=False)
    return path.read_text(encoding="utf-8")


def _open(path: Path) -> None:
    """Show the human the crop. Cut, look, decide — a region is not judgeable as numbers."""
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)


def _choose_region(outcome: PageOutcome, blocks, *, stream, read_line) -> dict | None:
    """A numbered candidate, or four numbers. Returns None if the human backs out."""
    if blocks:
        print("\n  candidate regions (points, from the ink itself):", file=stream)
        for i, b in enumerate(blocks, 1):
            r = b.region
            print(f"    {i}  x={r['x']:>4} y={r['y']:>4} w={r['width']:>4} h={r['height']:>4}"
                  f"   ({b.paths} strokes)", file=stream)
    else:
        print("\n  no candidate regions — a scan has no vector paths to group.", file=stream)
    print('  pick a number, or type "x y w h" in points, or [b]ack > ', end="",
          file=stream, flush=True)
    answer = (read_line() or "").strip()
    if not answer or answer[:1].lower() == "b":
        return None
    if answer.isdigit() and blocks and 1 <= int(answer) <= len(blocks):
        return blocks[int(answer) - 1].region
    parts = answer.split()
    if len(parts) == 4:
        try:
            x, y, w, h = (int(float(v)) for v in parts)
        except ValueError:
            print("  not four numbers — nothing cropped.", file=stream)
            return None
        return {"x": x, "y": y, "width": w, "height": h}
    print("  did not understand that — nothing cropped.", file=stream)
    return None


def crop(outcome: PageOutcome, out_dir: Path, text: str, *, stream, read_line,
         open_file) -> tuple[str, str] | None:
    """Cut the region from the source and put it where the marker was.

    Returns `(new_text, figure_name)`, or None if nothing was changed. **Vector**, which is why
    this also solves colour for block diagrams for free: the crop *is* the image, so green
    cone legs against grey base-diagram arrows survive without anyone naming them (DESIGN §6).
    """
    if not outcome.source or not Path(outcome.source).exists():
        print("  no source PDF recorded for this page, so there is nothing to crop from.\n"
              "  (Manifests written before `source` existed have none — re-run `handzoo`.)",
              file=stream)
        return None
    if not _MARKER.search(text):
        print("  no diagram marker on this page to replace.", file=stream)
        return None

    pdf = Path(outcome.source)
    try:
        blocks = rasterize.page_blocks(pdf, outcome.page)
        width, height = rasterize.page_size(pdf)
        print(f"  page is {width:.0f} x {height:.0f} pt", file=stream)
    except rasterize.RasterizeError as exc:
        print(f"  cannot read the source: {exc}", file=stream)
        return None

    n = 1
    while True:
        region = _choose_region(outcome, blocks, stream=stream, read_line=read_line)
        if region is None:
            return None
        name = f"fig-p{outcome.page:04d}-{n}.pdf"
        try:
            figure = rasterize.crop_vector(pdf, outcome.page, out_dir / name, **region)
        except rasterize.RasterizeError as exc:
            print(f"  crop failed: {exc}", file=stream)
            return None
        size = figure.stat().st_size
        print(f"  cropped -> {name}  ({size:,} bytes, vector)", file=stream)
        open_file(figure)
        print("  keep it? [y]es  [r]etry  [c]ancel > ", end="", file=stream, flush=True)
        answer = (read_line() or "c").strip().lower()[:1] or "c"
        if answer == "y":
            figure_tex = f"\\includegraphics[width={CROP_WIDTH}]{{{name}}}"
            # A function replacement, not a string: `re.sub` treats backslashes in a
            # replacement string as escapes, and `\i` of `\includegraphics` is not a valid
            # one. The figure would have blown up the substitution rather than the document.
            replaced = _MARKER.sub(lambda _m: figure_tex, text, count=1)
            return replaced, name
        figure.unlink(missing_ok=True)
        if answer != "r":
            return None
        n += 1


def transcribe(outcome: PageOutcome, out_dir: Path, log: CorrectionLog, *,
               stream, read_line, open_file, mode: str = "") -> int:
    """Time the author typing a page from blank — the exit criterion's control arm.

    The emitted `.tex` is never shown. That is the point: the measurement is *minutes from a
    blank file*, and a glance at the tool's output makes it something else.
    """
    prior = [r for r in log.read() if r.page == outcome.page and r.verdict not in BASELINE]
    if prior:
        print(f"page {outcome.page} has already been reviewed ({len(prior)} decision(s)).\n"
              "Transcription time cannot be measured on a page whose emitted text you have\n"
              "already read — you now know what is on it. Pick a page you have not reviewed.\n"
              "A contaminated number that looks clean is worse than no number, and this is the\n"
              "one measurement M0 turns on.", file=stream)
        return 2

    image = page_image(out_dir, outcome.page)
    target = out_dir / f"transcript-p{outcome.page:04d}.tex"
    if target.exists() and target.read_text(encoding="utf-8").strip():
        print(f"{target.name} already has content. Move it aside first.", file=stream)
        return 2
    target.write_text("", encoding="utf-8")

    print(f"\n=== transcribe page {outcome.page} from blank ===", file=stream)
    if image:
        print(f"  source: {image}", file=stream)
        open_file(image)
    print("  An empty file opens next. Type the page as you would want it to read, then save\n"
          "  and quit. Timing starts when the editor opens.\n"
          "  press enter when ready > ", end="", file=stream, flush=True)
    read_line()

    started = time.monotonic()
    text = _edit(target, None)
    seconds = round(time.monotonic() - started, 2)

    if not text.strip():
        # Opening an editor and closing it is not a transcription. Measured on the first real
        # run: two abandoned attempts were logged at 14.4s and 4.9s with zero words, inflating
        # the baseline arm by 19.3s before a single real number existed -- and inflating it in
        # the direction that flatters the tool.
        print(f"\n  nothing was typed after {seconds:.1f}s, so nothing was recorded.\n"
              "  An abandoned attempt is not a measurement.", file=stream)
        return 1

    log.append(Correction(
        page=outcome.page, verdict="transcribed",
        source_image=str(image) if image else "",
        before="", after=text, seconds=seconds, mode=mode,
        finding="exit criterion: transcription from blank",
    ))
    print(f"\n  {seconds:.1f}s, {len(text.split())} words -> {target.name}", file=stream)
    return 0


def _typeset(outcome: PageOutcome, out_dir: Path) -> Path | None:
    """Compile one page to a PDF the author can annotate.

    Reuses the assembler rather than inventing a second path to a document: a one-page master
    owns the preamble the fragment lacks, and puts the crop figures within reach of a relative
    `\\includegraphics` (DESIGN 6.1). `-synctex=1` is passed because a point in the result
    resolves back to a source line, which is what an annotation loop needs (DESIGN 7.3).
    """
    source = Path(outcome.output) if outcome.output else None
    if source and source.exists() and "\\documentclass" in source.read_text(encoding="utf-8"):
        # Already a document. Assembling it would produce the "standalone, not assemblable"
        # placeholder and nothing else -- a page with none of the author's content on it,
        # handed over and timed. A measurement of nothing, reported as a measurement.
        master = out_dir / f"review-p{outcome.page:04d}.tex"
        master.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        master = assemble(out_dir, [outcome], name=f"review-p{outcome.page:04d}.tex")
    proc = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-synctex=1", master.name],
        cwd=out_dir, capture_output=True, text=True, check=False)
    pdf = master.with_suffix(".pdf")
    return pdf if proc.returncode == 0 and pdf.exists() else None


def fix(outcome: PageOutcome, out_dir: Path, log: CorrectionLog, *,
        stream, read_line, open_file, mode: str = "", seconds: float | None = None) -> int:
    """Time the author correcting the emitted document — the same protocol as `--transcribe`,
    seeded with our output instead of a blank file.

    Why identical protocol matters: the arms were previously measured through *different
    interactions*. Transcription opened an editor; correction was a walk through findings, one
    keypress at a time. That compared the interaction as much as the content, and the
    finding-walk is not how anyone actually corrects a page. Here the only difference is what
    the file starts with, which is the difference the criterion is about.
    """
    prior = [r for r in log.read() if r.page == outcome.page and r.verdict in BASELINE]
    if prior:
        clean = sorted({o.page for o in load_outcomes(out_dir)}
                       - {r.page for r in log.read()})
        print(f"page {outcome.page} was transcribed from blank already.\n"
              "Having typed it out you know it by heart, so correcting it now measures memory\n"
              "rather than tooling — and in the direction that flatters the tool. The two arms\n"
              "have to run on different pages.\n"
              + (f"Untouched pages: {', '.join(f'page {p}' for p in clean[:8])}"
                 if clean else "No untouched pages remain in this run."),
              file=stream)
        return 2

    if not outcome.output or not Path(outcome.output).exists():
        print(f"page {outcome.page} has no output on disk to correct.", file=stream)
        return 2

    target = Path(outcome.output)
    before = target.read_text(encoding="utf-8")
    image = page_image(out_dir, outcome.page)

    print(f"\n=== correct page {outcome.page} against the ink ===", file=stream)
    if image:
        print(f"  source: {image}", file=stream)
        open_file(image)

    if seconds is not None:
        # A mode the tool cannot watch -- paper, or a device. Refusing to record it would push
        # the author back to a stopwatch and a notebook, which is what this harness exists to
        # end. Marked self-reported: a number the tool took and a number the author took are
        # different evidence and the log must not blur them (DESIGN 5.7).
        log.append(Correction(
            page=outcome.page, verdict="edited",
            source_image=str(image) if image else "",
            before=before, after="", seconds=float(seconds), mode=mode,
            finding="exit criterion: correction of emitted output (self-reported time)",
        ))
        print(f"  recorded {seconds:.1f}s, self-reported, mode={mode or 'unset'}", file=stream)
        return 0

    if mode.startswith("pdf"):
        # The author reviews by annotating the typeset output, not by editing source. Timing
        # them in an editor would measure a workflow they do not use -- and measure it worse
        # than their real one, understating the tool through an artefact of the harness.
        pdf = _typeset(outcome, out_dir)
        if pdf is None:
            print("  this page does not compile on its own, so there is no typeset PDF to\n"
                  "  annotate. Correct it as source instead, or use --seconds.", file=stream)
            return 2
        print(f"  typeset: {pdf}\n"
              "  Annotate it however you review — on the device, or on paper. The clock runs\n"
              "  from the next keypress until you say you are done, so it includes getting the\n"
              "  file there and back. That is real cost in this workflow; use --seconds if you\n"
              "  would rather report correction time alone.\n"
              "  press enter when you begin > ", end="", file=stream, flush=True)
        read_line()
        open_file(pdf)
        started = time.monotonic()
        print("  press enter when you are done > ", end="", file=stream, flush=True)
        read_line()
        elapsed = round(time.monotonic() - started, 2)
        log.append(Correction(
            page=outcome.page, verdict="edited",
            source_image=str(image) if image else "",
            before=before, after=str(pdf), seconds=elapsed, mode=mode,
            finding="exit criterion: correction by annotating the typeset output",
        ))
        print(f"\n  {elapsed:.1f}s annotating {pdf.name}", file=stream)
        return 0

    print(f"  {target.name} opens next, with what the tool produced. Fix it until it says what\n"
          "  the page says, then save and quit. Timing starts when the editor opens.\n"
          "  press enter when ready > ", end="", file=stream, flush=True)
    read_line()

    started = time.monotonic()
    after = _edit(target, None)
    seconds = round(time.monotonic() - started, 2)

    verdict = "edited"
    if after == before:
        # `--transcribe` can spot an abandoned attempt by its empty file. This cannot: an
        # unchanged document means either that the output was already correct -- the most
        # valuable datum this project can collect -- or that the editor was opened and closed.
        # They are opposites, and guessing either way corrupts the arm.
        print(f"\n  {seconds:.1f}s and nothing changed.\n"
              "  Was the output already correct, or did you abandon the attempt?\n"
              "  [y]es it was correct   [a]bandoned > ", end="", file=stream, flush=True)
        answer = (read_line() or "a").strip().lower()[:1] or "a"
        if answer != "y":
            print("  nothing recorded. An abandoned attempt is not a measurement.", file=stream)
            return 1
        verdict = "keep-reviewed"

    log.append(Correction(
        page=outcome.page, verdict=verdict,
        source_image=str(image) if image else "",
        before=before, after=after, seconds=seconds, mode=mode,
        finding="exit criterion: correction of emitted output",
    ))
    changed = ("correct as emitted" if after == before
               else f"{abs(len(after) - len(before))} chars different")
    print(f"\n  {seconds:.1f}s, {changed}", file=stream)
    return 0


def review_page(outcome: PageOutcome, out_dir: Path, log: CorrectionLog, *,
                stream, read_line, open_file) -> str:
    """Walk one page's findings. Returns "quit" if the human stopped."""
    if not outcome.output or not Path(outcome.output).exists():
        print(f"page {outcome.page}: no output on disk ({outcome.error or 'unknown'})",
              file=stream)
        return "next"

    target = Path(outcome.output)
    image = page_image(out_dir, outcome.page)
    findings = outcome.findings or []

    groups = _group(outcome.page, findings)
    decisions = ("" if len(groups) == len(findings)
                 else f", {len(groups)} to decide")
    print(f"\n=== page {outcome.page} — {outcome.verdict} "
          f"({len(findings)} finding(s){decisions}) ===", file=stream)
    if image:
        print(f"  source: {image}", file=stream)

    if not findings:
        print("  nothing flagged. Gates cannot see substitution, so this is not "
              "a statement that the page is correct.", file=stream)
        return "next"

    text = target.read_text(encoding="utf-8")
    for group in groups:
        finding, repeats = group[0], len(group)
        started = time.monotonic()
        count = f" x{repeats}" if repeats > 1 else ""
        print(f"\n  [{finding['gate']}]{count} {finding['detail']}", file=stream)
        if finding.get("excerpt"):
            print(f"       {finding['excerpt']}", file=stream)
        ctx = _context(text, finding.get("line"))
        if ctx:
            print(ctx, file=stream)

        print(PROMPT, end="", file=stream, flush=True)
        choice = (read_line() or "s").strip().lower()[:1] or "s"

        if choice == "q":
            return "quit"

        verdict, after, reason = "skipped", "", ""
        if choice == "k":
            # The text was on screen when they pressed this, so it is a reviewed keep.
            verdict = "keep-reviewed"
        elif choice == "e":
            after = _edit(target, finding.get("line"))
            text = after
            verdict = "edited"
        elif choice == "c":
            result = crop(outcome, out_dir, text, stream=stream, read_line=read_line,
                          open_file=open_file)
            if result is not None:
                text, figure = result
                target.write_text(text, encoding="utf-8")
                after, verdict = figure, "cropped"
        elif choice == "f":
            print("  why? > ", end="", file=stream, flush=True)
            reason = (read_line() or "").strip()
            verdict = "flagged"

        log.append(Correction(
            page=outcome.page,
            verdict=verdict,  # type: ignore[arg-type]
            source_image=str(image) if image else "",
            before=finding.get("excerpt", "") or finding["detail"],
            after=after,
            reason=reason,
            seconds=round(time.monotonic() - started, 2),
            finding=f"{finding['gate']}: {finding['detail']}",
            line=finding.get("line"),
            instances=repeats,
        ))
    return "next"


def main(argv: list[str] | None = None, *, stream=None, read_line=None,
         open_file=None) -> int:
    stream = stream or sys.stdout
    read_line = read_line or (lambda: sys.stdin.readline())
    open_file = open_file or _open

    parser = argparse.ArgumentParser(
        prog="handzoo-review",
        description="Walk gate findings and record what you decided about each.")
    parser.add_argument("out_dir", type=Path, help="the directory `handzoo` wrote to")
    parser.add_argument("--page", type=int, help="review one page")
    parser.add_argument("--all", action="store_true",
                        help="include pages with no findings")
    parser.add_argument("--transcribe", type=int, metavar="PAGE",
                        help="time yourself typing this page from blank — the exit "
                             "criterion's other arm. Refuses a page you have already reviewed.")
    parser.add_argument("--fix", type=int, metavar="PAGE",
                        help="time yourself correcting this page's emitted .tex against the "
                             "ink — the exit criterion's other arm, measured through the same "
                             "interaction as --transcribe. Refuses a page you transcribed.")
    parser.add_argument("--seconds", type=float, metavar="N",
                        help="record a time you measured yourself, for a mode the tool cannot "
                             "watch — reviewing on paper, or on a device. Marked self-reported, "
                             "because a number the tool took and one you took are different "
                             "evidence.")
    parser.add_argument("--mode", default="",
                        help="what you are working against — e.g. tex, pdf, markdown, agent. "
                             "Recorded with the timing, because correction cost is one number "
                             "per mode and pooling them makes the average meaningless.")
    parser.add_argument("--summary", action="store_true",
                        help="print what the correction log says and exit")
    args = parser.parse_args(argv)

    log = CorrectionLog.for_run(args.out_dir)

    if args.summary:
        return _print_summary(log, stream)

    try:
        outcomes = load_outcomes(args.out_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=stream)
        return 2

    if args.transcribe:
        match = [o for o in outcomes if o.page == args.transcribe]
        if not match:
            print(f"page {args.transcribe} is not in the manifest.", file=stream)
            return 2
        return transcribe(match[0], args.out_dir, log, stream=stream, read_line=read_line,
                          open_file=open_file, mode=args.mode)

    if args.fix:
        match = [o for o in outcomes if o.page == args.fix]
        if not match:
            print(f"page {args.fix} is not in the manifest.", file=stream)
            return 2
        return fix(match[0], args.out_dir, log, stream=stream, read_line=read_line,
                   open_file=open_file, mode=args.mode, seconds=args.seconds)

    if args.page:
        outcomes = [o for o in outcomes if o.page == args.page]

    # "re-run to continue" has to be true. A decision already in the log is not shown again
    # -- but `skipped` is the bare-enter default and means *deferred*, not decided, so it
    # comes back. Retiring findings by leaning on the return key is precisely the automation
    # bias this loop exists to resist.
    decided = {(row.page, row.finding, row.line)
               for row in log.read() if row.verdict != "skipped"}
    hidden = 0
    if decided:
        pruned = []
        for outcome in outcomes:
            keep = [f for f in (outcome.findings or [])
                    if finding_key(outcome.page, f) not in decided]
            hidden += len(outcome.findings or []) - len(keep)
            pruned.append(replace(outcome, findings=keep))
        outcomes = pruned
    if hidden:
        # Announced, never silent: a count that vanishes without a word is indistinguishable
        # from a gate that stopped running (DESIGN 5.7).
        print(f"{hidden} finding(s) already decided in the log are hidden. "
              "Delete the log to review them again.", file=stream)

    if not args.all:
        outcomes = [o for o in outcomes if o.findings]

    if not outcomes:
        print("nothing to review. Gates found no failures — which is not the same as "
              "the transcription being correct.", file=stream)
        return 0

    for outcome in outcomes:
        if review_page(outcome, args.out_dir, log, stream=stream,
                       read_line=read_line, open_file=open_file) == "quit":
            print("\nstopped. Progress is in the log; re-run to continue.", file=stream)
            break

    return _print_summary(log, stream)


def _print_summary(log: CorrectionLog, stream) -> int:
    s = log.summary()
    print(f"\n{s['rows']} decision(s) covering {s['findings_covered']} finding(s) "
          f"across {s['pages_touched']} page(s), {s['total_seconds']}s", file=stream)
    for verdict, n in sorted(s["by_verdict"].items()):  # type: ignore[union-attr]
        print(f"  {verdict:<16} {n}", file=stream)
    print(f"\n{s['gold_pairs']} row(s) carry evidence about correctness. "
          f"{s['unexamined']} record only that a human passed through — "
          "keep-unreviewed and skipped are not verification.", file=stream)
    _print_exit_criterion(s.get("exit_criterion") or {}, stream)
    _print_unpaired_arms(s.get("exit_criterion") or {}, s.get("unpaired_arms") or {}, stream)
    modes = s.get("modes") or []
    if len(modes) > 1:
        print(f"\n  NOTE: these rows mix modes ({', '.join(modes)}). Correction cost is one\n"
              "  number per mode; pooling them makes the average mean less than it looks.",
              file=stream)
    elif modes:
        print(f"\n  mode: {modes[0]}", file=stream)
    else:
        print("\n  mode: not recorded — pass --mode so a timing can be compared to another.",
              file=stream)
    return 0


def _print_unpaired_arms(paired: dict, arms: dict, stream) -> None:
    """The comparison the tool can actually make, when the paired one cannot exist.

    Suppressed when a paired comparison printed: that one controls for page difficulty and
    should not be second-guessed by a weaker number underneath it.
    """
    if paired or not arms:
        return
    c, t = arms["correcting"], arms["transcribing"]
    print("\nEXIT CRITERION — unpaired, because the arms run on different pages by design",
          file=stream)
    print(f"  correcting     median {c['median']:>7.1f}s   over {c['n']} page(s), --fix",
          file=stream)
    print(f"  transcribing   median {t['median']:>7.1f}s   over {t['n']} page(s)", file=stream)
    if c["median"] >= t["median"]:
        print("\n  Correction is not cheaper than transcription here. M0 has negative value\n"
              "  on this sample, and no amount of green gates changes that.", file=stream)
    else:
        print(f"\n  Correcting is cheaper on the median "
              f"({c['median'] / t['median']:.2f}x transcription).", file=stream)
    print("\n  Unpaired: the two arms never share a page, so page difficulty is not\n"
          "  controlled for. A sample, not a result.", file=stream)


def _print_exit_criterion(pages: dict, stream) -> None:
    """The milestone's actual question, once both arms exist for the same page.

    Pages with only one arm are not shown. One number answers nothing, and printing it as
    though it did is how a half-measurement gets quoted as a result.
    """
    if not pages:
        return
    print("\nEXIT CRITERION — seconds to correct against seconds to type from blank",
          file=stream)
    total_c = total_t = 0.0
    for page, arms in pages.items():
        c, t = arms["correcting"], arms["transcribing"]
        total_c += c
        total_t += t
        verdict = "correcting wins" if c < t else "TRANSCRIBING WINS"
        print(f"  page {page:>3}   correcting {c:>7.1f}s   transcribing {t:>7.1f}s   "
              f"{verdict}", file=stream)
    if len(pages) > 1:
        print(f"  {'total':>8}   correcting {total_c:>7.1f}s   "
              f"transcribing {total_t:>7.1f}s", file=stream)
    if total_c >= total_t:
        print("\n  Correction is not cheaper than transcription on these pages. "
              "M0 has negative\n  value here, and no amount of green gates changes that.",
              file=stream)
    print(f"\n  {len(pages)} page(s) carry both arms. This is a sample, not a result.",
          file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
