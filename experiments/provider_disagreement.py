"""Two providers disagreeing is a substitution detector.

The insight is that it needs neither model to be *right*. It needs them to be **independent**,
and then a passage only one of them produced is a passage worth a human glance. That is a
detector for the failure class nothing else in this project catches (§5.5) — and unlike
self-verification it does not ask a model to audit itself.

Measured on ch17 p1, the one page with established ground truth. The local run invented a
divisor pair (`3, 10`, correct arithmetic, not on the page). Gemini did not. A content-level
comparison isolates it:

    insert   gemini=''   local='* 3, 10 *'

**Compare text, not markup.** The naive form of this — diffing the emitted `.tex` — does not
work: the providers differ in *formatting* far more than in content (`\\item` against `\\\\`,
`\\section` against `\\section*`), and the real finding drowns in style. Stripping to words first
is what makes the signal visible.

**It is a router, not a verdict.** On that page it produced seven disagreements, of which one
was the fabrication. Seven things to glance at is a manageable review queue; it is not a
judgement about which provider is correct, and must never be presented as one.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

from pylatexenc.latex2text import LatexNodes2Text

_MARKER = re.compile(r"\\texttt\{\[TODO[^\]]*\]\}", re.S)
_BULLET = re.compile(r"^(\*|\d+\)|-)$")


def words(tex: Path) -> list[str]:
    """The document as plain words, with diagram markers collapsed to one token."""
    body = tex.read_text(encoding="utf-8")
    body = body.split("\\begin{document}", 1)[-1].split("\\end{document}")[0]
    body = _MARKER.sub(" [DIAGRAM] ", body)
    plain = LatexNodes2Text().latex_to_text(body)
    return [w for w in re.split(r"\s+", plain) if w.strip()]


def disagreements(a: list[str], b: list[str], *, drop_bullets: bool = True) -> list[tuple]:
    """Spans present in one transcription and not the other.

    List markers are dropped by default: providers choose `\\item` or `1)` freely and the
    difference is never a finding, only noise that hides one.
    """
    if drop_bullets:
        a = [w for w in a if not _BULLET.match(w)]
        b = [w for w in b if not _BULLET.match(w)]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append((tag, " ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return out


if __name__ == "__main__":
    left, right = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = words(left), words(right)
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    print(f"{left.name}: {len(a)} words · {right.name}: {len(b)} words · similarity {ratio:.2f}")
    found = disagreements(a, b)
    print(f"\n{len(found)} passage(s) to glance at — a queue, not a verdict:")
    for tag, x, y in found:
        print(f"   {tag:<8} {left.stem[:14]}={x[:44]!r:<46} {right.stem[:14]}={y[:44]!r}")
