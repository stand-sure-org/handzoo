"""Per-path ink extent from the vector source: a proxy for writing size."""
import re, statistics, subprocess, sys
from pathlib import Path

# Each <path> in pdftocairo -svg output: style attrs then d="..." then transform.
PATH_EL = re.compile(r'<path\s+([^>]*?)/>', re.S)
NUM = re.compile(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?')

def paths(pdf, page):
    svg = subprocess.run(["pdftocairo", "-svg", "-f", str(page), "-l", str(page),
                          str(pdf), "/dev/stdout"], capture_output=True, text=True).stdout
    for attrs in PATH_EL.findall(svg):
        m_rgb = re.search(r'stroke="rgb\(([^)]*)\)"', attrs)
        m_w   = re.search(r'stroke-width="([0-9.]+)"', attrs)
        m_sc  = re.search(r'transform="matrix\(([0-9.eE-]+)', attrs)
        m_d   = re.search(r'\sd="([^"]*)"', attrs)
        if not (m_rgb and m_w and m_sc and m_d):
            continue
        vals = [float(v.strip().rstrip("%")) for v in m_rgb.group(1).split(",")]
        if len(vals) == 3 and max(vals) - min(vals) < 3 and vals[0] > 50:
            continue  # grey uniform -> ruled guide line
        scale = float(m_sc.group(1))
        coords = [float(x) for x in NUM.findall(m_d.group(1))]
        xs, ys = coords[0::2], coords[1::2]
        if len(ys) < 2:
            continue
        yield {"w": float(m_w.group(1)) * scale,
               "h": (max(ys) - min(ys)) * scale,
               "wd": (max(xs) - min(xs)) * scale}

if __name__ == "__main__":
    pdf, lo, hi = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    print(f"{'pg':>3} {'paths':>6} {'strokeW':>8} {'medH':>7} {'p75H':>7} {'ratio':>7}")
    for pg in range(lo, hi + 1):
        p = list(paths(pdf, pg))
        if not p:
            print(f"{pg:>3} {0:>6}"); continue
        sw = statistics.median(x["w"] for x in p)
        # Ink extent: use the upper quartile of path heights. Median is dominated by
        # tiny strokes (dots, joins); the tall strokes are the ascenders that set size.
        hs = sorted(x["h"] for x in p)
        medh = statistics.median(hs)
        p75 = hs[int(len(hs) * 0.75)]
        print(f"{pg:>3} {len(p):>6} {sw:>8.3f} {medh:>7.2f} {p75:>7.2f} {p75/sw:>7.2f}")
