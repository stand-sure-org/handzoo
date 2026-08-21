"""Does the recognizer return usable bounding boxes for diagrams?

This gates the whole auto-crop direction, and nothing had measured it.

Two routes to an automatic crop, and only one is open. **Geometry** -- group ink into bands and
classify each as prose or diagram -- is precisely the heuristic segmenter D4 refuses, and D4
says one is added "only against a named failure it demonstrably fixes". **Asking the model** is
the other, and it sits on the interesting edge of the section 3 trust boundary: a box is a
*position*, which the boundary calls trustworthy, but that trust was measured on presence and
reading order, never on pixel geometry.

So: ask, and score against a region already verified by eye.

Qwen-VL reports boxes on a **0-1000 grid**, not in pixels and not as percentages.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handzoo.core.recognize.ollama_vlm import OllamaRecognizer  # noqa: E402

PROMPT = """Look at this page of handwritten notes.

List every DIAGRAM on the page — a drawing, chart, or arrow diagram. Do not list handwriting.

For each, give its bounding box on a 0-1000 grid where (0,0) is the top-left of the page and
(1000,1000) is the bottom-right.

Reply with JSON only:
{"diagrams": [{"what": "...", "box": [x0, y0, x1, y1]}]}

If there are no diagrams, reply {"diagrams": []}."""


def ask(recognizer: OllamaRecognizer, image: Path) -> list[dict]:
    raw = recognizer._ask(image, PROMPT)          # noqa: SLF001 - experiment
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0)).get("diagrams", [])
    except json.JSONDecodeError:
        return []


def to_points(box: list[float], width: float, height: float) -> dict:
    x0, y0, x1, y1 = box
    return {"x": int(x0 / 1000 * width), "y": int(y0 / 1000 * height),
            "width": int((x1 - x0) / 1000 * width), "height": int((y1 - y0) / 1000 * height)}


def iou(a: dict, b: dict) -> float:
    ax1, ay1 = a["x"] + a["width"], a["y"] + a["height"]
    bx1, by1 = b["x"] + b["width"], b["y"] + b["height"]
    ix = max(0, min(ax1, bx1) - max(a["x"], b["x"]))
    iy = max(0, min(ay1, by1) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["width"] * a["height"] + b["width"] * b["height"] - inter
    return inter / union if union else 0.0


if __name__ == "__main__":
    from handzoo.core.rasterize import page_blocks, page_size, rasterize

    pdf = Path(sys.argv[1])
    page = int(sys.argv[2])
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    width, height = page_size(pdf)
    tmp = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("/tmp/boxes")
    image = rasterize(pdf, tmp, first=page, last=page)[0].image

    blocks = [b.region for b in page_blocks(pdf, page)]
    print(f"page {page} is {width:.0f} x {height:.0f} pt")
    print("ink bands (geometry, for reference):")
    for i, b in enumerate(blocks, 1):
        print(f"   {i}: {b}")

    rec = OllamaRecognizer()
    for run in range(1, runs + 1):
        found = ask(rec, image)
        print(f"\nrun {run}: model reported {len(found)} diagram(s)")
        for d in found:
            box = d.get("box")
            if not (isinstance(box, list) and len(box) == 4):
                print(f"   unusable box: {box!r}")
                continue
            region = to_points([float(v) for v in box], width, height)
            best = max((iou(region, b) for b in blocks), default=0.0)
            print(f"   {str(d.get('what'))[:38]:<40} {region}   best IoU vs a band: {best:.2f}")
