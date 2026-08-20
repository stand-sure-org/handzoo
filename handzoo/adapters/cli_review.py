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
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from ..core.corrections import Correction, CorrectionLog
from ..core.pipeline import MANIFEST, PageOutcome

PROMPT = "[k]eep  [e]dit  [f]lag  [s]kip  [q]uit > "


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


def review_page(outcome: PageOutcome, out_dir: Path, log: CorrectionLog, *,
                stream, read_line) -> str:
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


def main(argv: list[str] | None = None, *, stream=None, read_line=None) -> int:
    stream = stream or sys.stdout
    read_line = read_line or (lambda: sys.stdin.readline())

    parser = argparse.ArgumentParser(
        prog="handzoo-review",
        description="Walk gate findings and record what you decided about each.")
    parser.add_argument("out_dir", type=Path, help="the directory `handzoo` wrote to")
    parser.add_argument("--page", type=int, help="review one page")
    parser.add_argument("--all", action="store_true",
                        help="include pages with no findings")
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
                       read_line=read_line) == "quit":
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
