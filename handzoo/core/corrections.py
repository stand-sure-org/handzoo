"""The correction log: an append-only record of what a human decided about each page.

This is the flywheel's first turn, and the file it writes is the project's long-term asset.
Three decisions in here came from evidence rather than taste.

**`keep` is two verdicts, not one.** A page kept *after inspection* and a page kept *without
being read* are different facts about the corpus, and collapsing them into "verified"
manufactures exactly the false confidence this project exists to refuse. Automation bias is
measured, not theoretical: agreement with incorrect AI output is the most consistent finding
across a 35-study review, and inspectors miss 20-30% of defects under repetitive load.

**The wrong output is logged, not just the right one.** The convergent regret across OCR
fine-tuning pipelines is teams that stored only corrected text. `(image, wrong, correct)`
triples are what make evaluation and regression tracking possible later; corrected text alone
is a training set with no way to measure whether anything improved.

**Time is recorded.** The M0 exit criterion is *author-timed minutes to correct emitted LaTeX
versus minutes to transcribe the page from blank*, and no published study gives an abandonment
threshold for a transcription tool. Instrumenting the log is the fastest route to a real number
for this corpus, so review must not be the only thing that fails to measure itself.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

LOG_NAME = "corrections.jsonl"

Verdict = Literal["keep-reviewed", "keep-unreviewed", "edited", "cropped", "flagged",
                  "skipped", "transcribed"]
"""What the human did.

`keep-reviewed` — looked at it and accepted it.
`keep-unreviewed` — accepted without inspection. Honest, common, and NOT evidence of quality.
`edited`         — corrected it. The gold rows.
`cropped`        — supplied the drawing the tool refused to invent. Gold, and counted
                   separately: diagrams are 45 of 49 findings on a real run, so
                   seconds-per-crop is most of the exit criterion, not a footnote in it.
`flagged`        — wrong, and the human could not or would not fix it now.
`skipped`        — deferred. Same evidentiary weight as `keep-unreviewed`: none.
`transcribed`    — typed the page from blank, against the image alone. **Not a verdict on the
                   emitted document at all** — it is the control arm of the exit criterion, and
                   ground truth for the page. Deliberately outside GOLD: folding it in would
                   inflate the count of rows that judge the output with rows that never looked
                   at it, the same conflation `keep-unreviewed` exists to prevent.
"""

BASELINE: frozenset[str] = frozenset({"transcribed"})
"""The control arm. Timed against the page image with no emitted text in sight."""

GOLD: frozenset[str] = frozenset({"edited", "cropped", "keep-reviewed"})
"""Verdicts that carry evidence about correctness. The others record only that a human passed
through, which is worth knowing and worth never mistaking for verification."""


@dataclass(frozen=True, slots=True)
class Correction:
    """One human decision about one span of one page."""

    page: int
    verdict: Verdict
    source_image: str
    """Path to the page image. The pair is (image, text) — without it a row cannot train
    or evaluate anything."""
    before: str
    """What the tool emitted. Kept even when unchanged, so a later reader can tell a
    correction from an acceptance without diffing against a regenerated file."""
    after: str = ""
    """What the human made it. Empty unless `verdict == "edited"`."""
    reason: str = ""
    """Why it was flagged, in the human's words."""
    seconds: float = 0.0
    """Time spent on this decision. Feeds the exit criterion, which is a timing question."""
    finding: str = ""
    """The gate finding this decision responds to, if it came from one."""
    instances: int = 1
    """How many identical findings this one decision covered.

    A gate can report the same defect many times -- ch18 page 25 emitted 32 byte-identical
    fabrication findings on one line. Those are one defect and get one decision, but the
    corpus must not read as though only one finding existed. Stored, so a grouped row can
    never be mistaken for a singleton."""
    line: int | None = None
    """Line the finding pointed at. Part of the identity of a decision: two findings can
    share a gate and a detail and still be different defects on different lines."""
    at: float = field(default_factory=time.time)

    @property
    def is_gold(self) -> bool:
        return self.verdict in GOLD


class CorrectionLog:
    """Append-only JSONL. No schema migration, no database, no Learning Store.

    Append-only because a correction log that can be rewritten is not a record of what
    happened; it is a record of what someone last believed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_run(cls, out_dir: Path) -> CorrectionLog:
        return cls(out_dir / LOG_NAME)

    def append(self, correction: Correction) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(correction)) + "\n")

    def read(self) -> list[Correction]:
        """Rows in order. A malformed line raises rather than being skipped — silently
        dropping a row would understate the corpus and overstate nothing, which is the
        wrong direction for a record whose whole job is to be trustworthy."""
        if not self.path.exists():
            return []
        rows = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    rows.append(Correction(**json.loads(line)))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError(f"{self.path}:{n} is not a valid correction row") from exc
        return rows

    def summary(self) -> dict[str, object]:
        """What the log says, stated so it cannot be read as more than it is."""
        rows = self.read()
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        gold = [r for r in rows if r.is_gold]
        return {
            "rows": len(rows),
            "findings_covered": sum(r.instances for r in rows),
            "by_verdict": counts,
            "gold_pairs": len(gold),
            "unexamined": counts.get("keep-unreviewed", 0) + counts.get("skipped", 0),
            "total_seconds": round(sum(r.seconds for r in rows), 1),
            "pages_touched": len({r.page for r in rows}),
            "exit_criterion": _exit_criterion(rows),
        }


def _exit_criterion(rows: list[Correction]) -> dict[int, dict[str, float]]:
    """Per page, seconds spent correcting against seconds spent transcribing from blank.

    This is the milestone's actual question — *"minutes to correct emitted `.tex` to ground
    truth, versus minutes to transcribe the same page from a blank file"* — and until both arms
    were recorded here it lived in a stopwatch and a notebook.

    Only pages carrying **both** numbers appear. One arm alone answers nothing, and showing it
    as though it did is how a half-measurement gets quoted as a result.
    """
    correcting: dict[int, float] = {}
    transcribing: dict[int, float] = {}
    for r in rows:
        if r.verdict in BASELINE:
            # An abandoned attempt produced no text. The log is append-only and keeps it,
            # because it records what happened; the interpretation must not count it, or every
            # log written before that guard existed reports a baseline that is too large.
            if not (r.after or "").strip():
                continue
            transcribing[r.page] = transcribing.get(r.page, 0.0) + r.seconds
        else:
            correcting[r.page] = correcting.get(r.page, 0.0) + r.seconds
    return {
        page: {"correcting": round(correcting[page], 1),
               "transcribing": round(transcribing[page], 1)}
        for page in sorted(set(correcting) & set(transcribing))
    }
