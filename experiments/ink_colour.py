"""What ink colours are on a page, straight from the vector source.

Measured on `Cheng 217-220` p3: violet writing, grey base-diagram arrows, green cone legs --
where green against grey *is* the lesson. Twelve recognizer runs over those pages emitted no
colour at all, and the gates passed. See DESIGN section 6.

This exists to make the cheap answer obvious: colour *presence* is one `pdftocairo` call away
and needs nothing from the model. Asking the recognizer to name a colour puts the answer on the
untrustworthy side of the section 3 boundary; reading it here does not.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ink import paths  # noqa: E402

if __name__ == "__main__":
    pdf, lo, hi = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    for pg in range(lo, hi + 1):
        c = Counter(x["rgb"] for x in paths(pdf, pg))
        print(f"page {pg}: {len(c)} ink colour(s)")
        for rgb, n in c.most_common():
            print(f"    {n:>4} paths  rgb{rgb}")
