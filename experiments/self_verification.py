"""Does asking the model "does this output match the page?" detect substitution?

The value of a checker is its DISCRIMINATION, not its agreement rate. A checker that says
"matches" to everything is worse than no checker, because it manufactures confidence. So the
cohort includes a page the author confirmed correct, the same page with an error injected on
purpose, and a page known to have dropped content.
"""
import base64, json, sys, urllib.request
from pathlib import Path

VERIFY = (
    "Below is a LaTeX transcription that was produced from this handwritten page.\n\n"
    "Compare it against the image carefully. Report ONLY differences you can actually see: "
    "content on the page that is missing from the transcription, content in the transcription "
    "that is not on the page, and any symbol or notation that was changed into something else.\n"
    "Ignore formatting and LaTeX style. Do not comment on quality.\n"
    'STRICT JSON only: {"matches": true|false, "missing": ["..."], "invented": ["..."], '
    '"changed": ["..."]}\n\n--- TRANSCRIPTION ---\n'
)


def ask(img: Path, text: str) -> dict:
    b = base64.b64encode(img.read_bytes()).decode()
    body = {"model": "qwen3-vl:8b-instruct",
            "messages": [{"role": "user", "content": VERIFY + text, "images": [b]}],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": -1, "num_ctx": 8192}}
    r = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(r, timeout=600).read())
    t = (out.get("message", {}).get("content") or "").strip()
    s, e = t.find("{"), t.rfind("}")
    if s < 0:
        return {"matches": None, "raw": t[:120]}
    try:
        return json.loads(t[s:e + 1])
    except json.JSONDecodeError:
        return {"matches": None, "raw": t[:120]}


def body_of(p: Path) -> str:
    t = p.read_text(encoding="utf-8")
    return t.split(r"\begin{document}")[-1].split(r"\end{document}")[0].strip()


if __name__ == "__main__":
    S = Path(sys.argv[1])
    cases = []
    good = body_of(S / "v6/page-0003.tex")
    cases.append(("cheng p3  GOOD (author confirmed)", S / "v6/pages", "p-0003", good))
    # Injected error: swap a real claim for a false one, and delete a line.
    corrupted = good.replace("initial", "terminal", 2)
    corrupted = "\n".join(corrupted.splitlines()[:-3])
    cases.append(("cheng p3  CORRUPTED on purpose", S / "v6/pages", "p-0003", corrupted))
    cases.append(("naive p1  KNOWN to drop glyphs", S / "v4/pages", "p-0001",
                  body_of(S / "v4/page-0001.fail.tex")))
    for label, d, stem, text in cases:
        img = sorted(d.glob(f"{stem}*.png"))[0]
        r = ask(img, text)
        m = r.get("matches")
        print(f"  {label}")
        print(f"    matches={m}  missing={len(r.get('missing',[]) or [])} "
              f"invented={len(r.get('invented',[]) or [])} changed={len(r.get('changed',[]) or [])}")
        for k in ("missing", "invented", "changed"):
            for item in (r.get(k) or [])[:2]:
                print(f"      {k}: {str(item)[:72]}")
