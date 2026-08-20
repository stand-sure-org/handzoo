"""Per-path ink extent from the vector source: a proxy for writing size.

Stroke width is fixed within a document, so the variable zoom actually moves is the
stroke-to-glyph ratio. This measures the glyph half.

Answered on Cheng ch18 (n=14, 3 failures): the ratio does not predict gate outcome. Ruled out
by cause rather than coefficient -- every failure had an identified reason unrelated to glyph
size. See DECISION, "Capture-side variable".
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ink import paths  # noqa: E402

if __name__ == "__main__":
    pdf, lo, hi = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    print(f"{'pg':>3} {'paths':>6} {'strokeW':>8} {'medH':>7} {'p75H':>7} {'ratio':>7}")
    for pg in range(lo, hi + 1):
        p = paths(pdf, pg)
        if not p:
            print(f"{pg:>3} {0:>6}")
            continue
        sw = statistics.median(x["w"] for x in p)
        # Upper quartile of path heights: the median is dominated by tiny strokes (dots,
        # joins); the tall strokes are the ascenders that actually set the writing size.
        hs = sorted(x["height"] for x in p)
        p75 = hs[int(len(hs) * 0.75)]
        print(f"{pg:>3} {len(p):>6} {sw:>8.3f} {statistics.median(hs):>7.2f} "
              f"{p75:>7.2f} {p75 / sw:>7.2f}")
