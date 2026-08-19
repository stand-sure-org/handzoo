"""Minimal recognizer probe: page PNG -> LaTeX via qwen3-vl on ollama.

Findings this encodes:
  - qwen3-vl emits reasoning into `message.thinking`, which COUNTS against num_predict.
  - `think: false` is NOT honored by ollama 0.30.7 for this model.
  - => never cap num_predict, and always verify content is non-empty.
"""
import base64, json, sys, time, urllib.request

PROMPT = (
    "Transcribe this handwritten page to LaTeX. Preserve headings, bullets, tables and inline math. "
    "Mark hand-drawn pictures inline as [[DIAGRAM: description]] at the exact point they occur. "
    "Never invent tikz. Output LaTeX only."
)


def recognize(png_path, model="qwen3-vl:8b", attempts=3):
    img = base64.b64encode(open(png_path, "rb").read()).decode()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT, "images": [img]}],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": -1},  # uncapped: thinking shares the budget
    }
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        t = time.time()
        r = json.loads(urllib.request.urlopen(req, timeout=1800).read())
        msg = r.get("message", {})
        content = (msg.get("content") or "").strip()
        thinking = len(msg.get("thinking") or "")
        print(
            f"  attempt {attempt}: {time.time()-t:.1f}s  eval={r.get('eval_count')} "
            f"thinking={thinking}ch content={len(content)}ch done={r.get('done_reason')}",
            file=sys.stderr,
        )
        if content:
            for fence in ("```latex", "```tex", "```"):
                if content.startswith(fence):
                    content = content[len(fence):]
                    break
            return content.removesuffix("```").strip()
    raise RuntimeError(f"empty content after {attempts} attempts (thinking consumed the response)")


if __name__ == "__main__":
    out = recognize(sys.argv[1])
    open(sys.argv[2], "w").write(out + "\n")
    print(f"wrote {sys.argv[2]} ({len(out)} chars)", file=sys.stderr)
