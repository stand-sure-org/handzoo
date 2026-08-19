"""Inverted Cardan grille: mask the picture, transcribe the holes.

EXPERIMENT, not pipeline code. Kept because it produced the measurement in DESIGN 5.5.4 and
because re-running it is the only way to extend n=3. Not imported by `handzoo/`.

    python experiments/cardan_grille.py <page.png>


A Cardan grille is a mask with holes that reveals hidden text. Inverted, it hides the picture
and leaves the text. The hypothesis is that over-correction is a model smoothing over what it
cannot name -- and that a hole cannot be smoothed over the same way, because there is no
plausible reading to substitute.
"""
import base64, json, sys, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw

MODEL = "qwen3-vl:8b-instruct"

BOX_PROMPT = (
    "Find every hand-drawn DIAGRAM or PICTURE on this page: shapes, arrows between labelled "
    "objects, sketches. Ignore ordinary lines of handwritten text.\n"
    "Give each one's bounding box as PERCENTAGES of image width and height, 0-100.\n"
    'STRICT JSON only: {"boxes":[{"what":"short label","x0":N,"y0":N,"x1":N,"y1":N}]}'
)

TRANSCRIBE = (
    "Transcribe this handwritten page to LaTeX. Preserve headings, bullets, tables and inline "
    "math. Mark hand-drawn pictures inline as [[DIAGRAM: description]] at the exact point they "
    "occur. Never invent tikz. Output LaTeX only."
)


def ask(img_path, prompt, timeout=600):
    img = base64.b64encode(Path(img_path).read_bytes()).decode()
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt, "images": [img]}],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": -1, "num_ctx": 8192}}
    r = urllib.request.Request("http://localhost:11434/api/chat",
                               data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    t = (d.get("message", {}).get("content") or "").strip()
    for f in ("```json", "```latex", "```tex", "```"):
        if t.startswith(f):
            t = t[len(f):]
            break
    return t.removesuffix("```").strip()


def boxes(img_path):
    raw = ask(img_path, BOX_PROMPT)
    s, e = raw.find("{"), raw.rfind("}")
    if s < 0:
        return []
    try:
        return json.loads(raw[s:e + 1]).get("boxes", [])
    except json.JSONDecodeError:
        return []


def mask(img_path, bxs, out_path, pad=1.5):
    """White out each box. Padding because a box that clips the drawing leaves fragments,
    and a fragment is exactly the thing the model will try to interpret."""
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    # The model answers on a 0-1000 normalised grid despite being asked for percentages.
    # Detect rather than assume: a coordinate above 100 cannot be a percentage.
    scale = 1000.0 if any(max(b["x1"], b["y1"]) > 100 for b in bxs) else 100.0
    for b in bxs:
        k = pad * scale / 100
        x0 = max(0, (b["x0"] - k) / scale * W)
        y0 = max(0, (b["y0"] - k) / scale * H)
        x1 = min(W, (b["x1"] + k) / scale * W)
        y1 = min(H, (b["y1"] + k) / scale * H)
        if x1 <= x0 or y1 <= y0:
            continue
        d.rectangle([x0, y0, x1, y1], fill="white")
    im.save(out_path)
    return out_path


if __name__ == "__main__":
    src = Path(sys.argv[1])
    bxs = boxes(src)
    print(f"  boxes found: {len(bxs)}")
    for b in bxs:
        print(f"    {b.get('what','?')[:40]:<42} "
              f"x {b['x0']:.0f}-{b['x1']:.0f}%  y {b['y0']:.0f}-{b['y1']:.0f}%")
    if bxs:
        out = src.with_name(src.stem + "-masked.png")
        mask(src, bxs, out)
        print(f"  masked -> {out}")
