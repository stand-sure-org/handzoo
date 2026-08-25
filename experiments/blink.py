r"""**Blink** — does a transformation of a `.tex` still say the same thing? Compare the render.

Named for the **blink comparator**: two photographic plates of the same star field, alternated
rapidly, so that anything which moved jumps out. Clyde Tombaugh found Pluto with one in 1930.
The instrument has no idea what it is looking at. It knows only that something changed — which
is this tool's exact capability and its exact limitation.

The other resonance is apt too. Weeping Angels move only when unobserved, and silent corruption
is the same creature: the substitution happens where nobody is looking. Blink is the deliberate
look.

*Names considered and rejected: `Veritas`, `Ma'at`. Both claim truth, and this project's
load-bearing hedge is that the gates prove output **builds**, not that it is **true** — a
component reporting PRESERVED under a name meaning "truth" would undercut the positioning from
inside the codebase. `Horus`, `Heimdall`: a watcher sees and understands; this does not.*

**Why not a source diff.** `\mid` against `\vert`, `\to` against `\rightarrow` — LaTeX has many
spellings per glyph, so a textual diff reports differences that are not differences. Normalising
them away is unbounded work with a long tail.

**Why not `pdftotext`.** Measured, and it is blind exactly where this project is not allowed to
be: `\underline{Prop 18.2}` and `Prop 18.2` extract identically, as do `\textcolor{red}{R}` and
`R`, and `$x^2$` and `$x2$`. Those are the label mark, semantic ink colour, and notation
degradation — three of the five measured defect classes (DESIGN §11.0.1a). A text diff would
call a round trip clean after dropping every one.

It is not conservative either: `\mid` and `\vert` extract the same but are different math
classes, so they typeset with different spacing and the pages genuinely differ.

**So compare pixels.** Two documents are equal exactly when they look identical, which is what
"renders the same" means. Everything semantic is caught; true aliases still compare equal.

Pixels are an **oracle, not an explanation** — they say *that* a page changed, never what. Run a
source diff afterwards on the pages that failed.
"""

from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
import zlib
from pathlib import Path


def _png_pixels(path: Path) -> str:
    """Hash a PNG's decompressed pixel data, skipping metadata chunks.

    Hashing the file would compare encoder output; two identical renders can differ in their
    PNG headers. The IDAT stream is the picture.
    """
    data = path.read_bytes()
    idat = b""
    i = 8
    while i < len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        if data[i + 4:i + 8] == b"IDAT":
            idat += data[i + 8:i + 8 + length]
        i += 12 + length
    return hashlib.sha256(zlib.decompress(idat)).hexdigest()


def render(tex: Path, work: Path, *, dpi: int = 100) -> list[str]:
    """Compile and rasterise, returning one pixel hash per page. Empty if it will not build."""
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdflatex", "-interaction=batchmode", "-output-directory", str(work),
                    str(tex)], capture_output=True)
    pdf = work / (tex.stem + ".pdf")
    if not pdf.exists():
        return []
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(work / "pg")],
                   capture_output=True)
    return [_png_pixels(p) for p in sorted(work.glob("pg-*.png"))]


def compare(before: Path, after: Path, work: Path) -> tuple[bool, str]:
    """Did the transformation preserve what the page renders?

    A document that stops compiling is a failure, not an absence of evidence (DESIGN §5.7):
    "it no longer builds" is the most serious answer this can give, and must never read as
    "nothing changed".
    """
    a = render(before, work / "a")
    b = render(after, work / "b")
    if not a:
        return False, "the original does not compile — nothing can be concluded"
    if not b:
        return False, "the round trip produced a document that does not compile"
    if len(a) != len(b):
        return False, f"page count changed: {len(a)} -> {len(b)}"
    differing = [i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if differing:
        return False, (f"{len(differing)} of {len(a)} page(s) render differently: "
                       f"{differing[:10]}. Diff the source for those pages to see what.")
    return True, f"all {len(a)} page(s) render identically"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: roundtrip_fidelity.py BEFORE.tex AFTER.tex [workdir]")
    work = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/handzoo-roundtrip")
    ok, why = compare(Path(sys.argv[1]), Path(sys.argv[2]), work)
    print(("PRESERVED  " if ok else "CHANGED    ") + why)
    raise SystemExit(0 if ok else 1)
