"""Effective ink stroke width per page, from the vector source.

The reMarkable bakes the writing zoom into the export: `stroke-width` alone is meaningless
because each path carries its own transform scale. The number that matters is the product.

Ruled guide lines are excluded -- they are grey (~75% on all three channels) and uniform;
ink is coloured. Including them would swamp the statistic.
"""
import re, statistics, subprocess, sys
from pathlib import Path

PATH = re.compile(r'stroke-width="([0-9.]+)"[^/]*?stroke="rgb\(([^)]*)\)"[^/]*?'
                  r'transform="matrix\(([0-9.eE-]+)', re.S)


def page_strokes(pdf: Path, page: int) -> list[float]:
    svg = subprocess.run(["pdftocairo", "-svg", "-f", str(page), "-l", str(page),
                          str(pdf), "/dev/stdout"], capture_output=True, text=True).stdout
    out = []
    for w, rgb, scale in PATH.findall(svg):
        vals = [float(v.strip().rstrip("%")) for v in rgb.split(",")]
        if len(vals) == 3 and max(vals) - min(vals) < 3 and vals[0] > 50:
            continue  # grey and uniform -> ruled guide line, not ink
        out.append(float(w) * float(scale))
    return out


if __name__ == "__main__":
    pdf = Path(sys.argv[1])
    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    print(f"  {'pg':>3} {'ink paths':>9} {'median w':>9} {'mean w':>8}")
    for pg in range(lo, hi + 1):
        s = page_strokes(pdf, pg)
        if not s:
            print(f"  {pg:>3} {0:>9}")
            continue
        print(f"  {pg:>3} {len(s):>9} {statistics.median(s):>9.3f} "
              f"{statistics.mean(s):>8.3f}")
