"""Effective ink stroke width per page, from the vector source.

The reMarkable bakes the writing zoom into the export: `stroke-width` alone is meaningless
because each path carries its own transform scale. The number that matters is the product.

Ruled guide lines are separated by geometry, not colour -- see `_ink.py` for why the original
colour rule was wrong and why chapter 18's result is unaffected by the correction.
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ink import paths  # noqa: E402

if __name__ == "__main__":
    pdf, lo, hi = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    print(f"  {'pg':>3} {'ink paths':>9} {'median w':>9} {'mean w':>8} {'colours':>8}")
    for pg in range(lo, hi + 1):
        p = paths(pdf, pg)
        if not p:
            print(f"  {pg:>3} {0:>9}")
            continue
        w = [x["w"] for x in p]
        print(f"  {pg:>3} {len(p):>9} {statistics.median(w):>9.3f} "
              f"{statistics.mean(w):>8.3f} {len({x['rgb'] for x in p}):>8}")
