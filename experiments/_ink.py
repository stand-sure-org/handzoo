"""Shared ink extraction from a vector PDF page.

**Ruled guide lines are separated by geometry, not by colour.** A rule spans the page and has
no height. Hue does not work: the first version of this keyed off "grey and uniform", which
held on the pages it was written against and fails on `Cheng 217-220` p3, where 17 paths of
deliberate grey ink -- the base-diagram arrows of a cone, drawn against green ink for the cone
legs -- would have been discarded as furniture. Measured there: rules are 685.1 pt wide and
0.0 tall, the grey ink is 6.5 x 8.2.

Chapter 18 is unaffected by the correction. Both filters keep an identical 576/304/431/291
paths and the same 1.903 median, so the stroke-width result stands -- the rule was wrong, not
the number. It was wrong in a way that had not yet mattered, which is the only reason it
survived: exactly the failure mode DESIGN 5.7.1 names.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_EL = re.compile(r"<path\s+([^>]*?)/>", re.S)
_NUM = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")

RULE_MIN_WIDTH = 300.0
"""A ruled line runs the width of the page. Real ink strokes do not."""
RULE_MAX_HEIGHT = 1.0
"""...and is flat. A stroke with height is a mark, whatever colour it is."""


def paths(pdf: Path, page: int, *, keep_rules: bool = False) -> list[dict]:
    """Every stroked path on a page, with effective width and extent in points.

    `stroke-width` alone is meaningless -- each path carries its own transform scale, and the
    number that matters is the product.
    """
    svg = subprocess.run(
        ["pdftocairo", "-svg", "-f", str(page), "-l", str(page), str(pdf), "/dev/stdout"],
        capture_output=True, text=True).stdout
    out: list[dict] = []
    for attrs in _EL.findall(svg):
        colour = re.search(r'stroke="rgb\(([^)]*)\)"', attrs)
        width = re.search(r'stroke-width="([0-9.]+)"', attrs)
        scale = re.search(r'transform="matrix\(([0-9.eE-]+)', attrs)
        data = re.search(r'\sd="([^"]*)"', attrs)
        if not (colour and width and scale and data):
            continue
        coords = [float(x) for x in _NUM.findall(data.group(1))]
        xs, ys = coords[0::2], coords[1::2]
        if len(xs) < 2:
            continue
        s = float(scale.group(1))
        w_span, h_span = (max(xs) - min(xs)) * s, (max(ys) - min(ys)) * s
        is_rule = w_span > RULE_MIN_WIDTH and h_span < RULE_MAX_HEIGHT
        if is_rule and not keep_rules:
            continue
        rgb = tuple(round(float(v.strip().rstrip("%")) * 255 / 100)
                    for v in colour.group(1).split(","))
        out.append({"w": float(width.group(1)) * s, "width": w_span, "height": h_span,
                    "rgb": rgb, "rule": is_rule})
    return out
