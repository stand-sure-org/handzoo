"""A local review surface: page image and emitted text, side by side.

**Why a browser and not the terminal.** Correction is a *visual* comparison — the page beside
what we made of it — and a terminal cannot show both. The author's own note put file-open and
drag-and-drop first, which points the same way. Python's `http.server` and one HTML file cost
no new dependencies, and the browser supplies image rendering and PDF preview for free.

**Local only, and it says so.** Bound to 127.0.0.1. Page images are unpublished manuscript
content (constraint #7) and never leave the machine.

**Two save actions, not one.** *Fix transcription* and *edit my notes* are different acts that
leave the same shape of diff, and recording them together contaminates the exit-criterion
timing, the defect taxonomy, and any correction-mined lexicon (DESIGN 11.3.1). The separation
is **structural rather than a question**: the human picks the mode before typing, so the label
is unambiguous and there is nothing to recall afterwards.

This is an adapter. All logic lives in `handzoo.core`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core.corrections import Correction, CorrectionLog
from ..core.pipeline import MANIFEST, PageOutcome
from ..core.validate import ascii_gate, colour_gate, compile_gate, delimiter_gate,\
    reference_gate

HERE = Path(__file__).resolve().parent / "ui"

# What the two buttons write. `edited` feeds the exit criterion and the defect taxonomy;
# `authored` feeds neither, and is excluded from lexicon mining.
MODES = {"fix": "edited", "author": "authored"}


@dataclass
class Review:
    """One run's output directory, read fresh on every request.

    Deliberately stateless: `handzoo` may be writing into this directory while the browser is
    open, and a cached page list would show a run that has moved on.
    """

    out_dir: Path

    def outcomes(self) -> list[PageOutcome]:
        path = self.out_dir / MANIFEST
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(PageOutcome(**json.loads(line)))
        return rows

    def image(self, page: int) -> Path | None:
        hits = sorted((self.out_dir / "pages").glob(f"p-{page:04d}*.png"))
        return hits[0] if hits else None

    def log(self) -> CorrectionLog:
        return CorrectionLog.for_run(self.out_dir)


def _pages(review: Review) -> list[dict]:
    """The page list, with each page's worst gate state.

    Advisory findings are reported as `flag`, never as `fail`: the reference gate marks a
    convention for a human to look at and does not refuse the page (DESIGN 11.0.1b). Collapsing
    the two would train the reader to ignore both.
    """
    seen = {r.page for r in review.log().read()}
    out = []
    for o in review.outcomes():
        advisory = any(f.get("gate") == "reference" for f in (o.findings or []))
        hard = [f for f in (o.findings or []) if f.get("gate") != "reference"]
        state = "fail" if hard else ("flag" if advisory else "ok")
        out.append({"page": o.page, "state": state, "verdict": o.verdict,
                    "findings": o.findings or [], "reviewed": o.page in seen,
                    "has_image": review.image(o.page) is not None})
    return out


def _revalidate(text: str, target: Path) -> tuple[bool, list[dict], str]:
    """Re-run the gates on what the author actually saved.

    A `.fail.tex` name is one a build cannot pick up by accident. Once the defect is fixed,
    keeping that name is a lie in the other direction: the page is good, the filename says
    otherwise, and `chapter.tex` still carries a placeholder where real content now sits.

    **The gate decides, not the act of saving.** A half-fix keeps its quarantine.

    The coverage gate is not re-run: it needs the inventory from the recognition pass, which
    exists only during a run (see `PageOutcome.findings`). So a page quarantined *solely* for
    coverage cannot be released here — which is the honest outcome, since nothing available
    at this point can confirm the marks are accounted for. It is reported as still failing
    rather than promoted on faith (DESIGN §5.7).
    """
    standalone = "\\begin{document}" in text
    gates = [
        ascii_gate.check(text, fragment=not standalone),
        delimiter_gate.check(text),
        reference_gate.check(text),
        colour_gate.check(text, colours=None),
    ]
    if standalone:
        gates.append(compile_gate.check(text, base_dir=target.parent))

    findings = [{"gate": g.gate, "detail": f.detail, "line": f.line, "excerpt": f.excerpt}
                for g in gates if g.checked and not g.advisory
                for f in g.failures]
    advisory = [{"gate": g.gate, "detail": f.detail, "line": f.line, "excerpt": f.excerpt}
                for g in gates if g.checked and g.advisory
                for f in g.failures]
    state = {g.gate: ("pass" if g.passed else "skipped" if not g.checked else "fail")
             for g in gates}
    return (not findings), findings + advisory, json.dumps(state)


def _rewrite_manifest(out_dir: Path, page: int, **fields) -> None:
    """Update one page's row in place. Every reader — `handzoo-review`, `assemble`, this UI —
    goes through the manifest, so a rename it does not know about points them all at a file
    that is gone."""
    path = out_dir / MANIFEST
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        if r.get("page") == page:
            r.update(fields)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    review: Review  # set by serve()

    def log_message(self, *args) -> None:  # noqa: D102 - silence per-request stderr noise
        pass

    # -- helpers ----------------------------------------------------------------

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page is manuscript content. Nothing here should be cached to disk by the browser.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj).encode("utf-8"), "application/json", code)

    # -- routes -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        url = urlparse(self.path)
        q = parse_qs(url.query)

        if url.path in ("/", "/index.html"):
            self._send((HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/pages":
            self._json({"dir": str(self.review.out_dir), "pages": _pages(self.review)})
        elif url.path == "/api/text":
            page = int(q["page"][0])
            match = [o for o in self.review.outcomes() if o.page == page]
            if not match or not match[0].output or not Path(match[0].output).exists():
                self._json({"error": "no output on disk for this page"}, 404)
                return
            self._json({"page": page,
                        "text": Path(match[0].output).read_text(encoding="utf-8")})
        elif url.path == "/api/image":
            img = self.review.image(int(q["page"][0]))
            if not img:
                self._json({"error": "no page image"}, 404)
                return
            self._send(img.read_bytes(), "image/png")
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path != "/api/save":
            self._json({"error": "not found"}, 404)
            return

        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
        mode = payload.get("mode")
        if mode not in MODES:
            self._json({"error": f"mode must be one of {sorted(MODES)}"}, 400)
            return

        page = int(payload["page"])
        match = [o for o in self.review.outcomes() if o.page == page]
        if not match or not match[0].output:
            self._json({"error": "no output on disk for this page"}, 404)
            return

        target = Path(match[0].output)
        before = target.read_text(encoding="utf-8")
        after = payload.get("text", "")
        if after == before:
            # Unchanged is not nothing, and it is not a correction either. Same reasoning as
            # `--fix` (DESIGN 11.1.1): it may mean the output was already right, which is the
            # most valuable datum here, or that the editor was opened and closed.
            self._json({"saved": False, "reason": "unchanged"})
            return

        target.write_text(after, encoding="utf-8")

        # Only a correction can release a quarantine. Revising one's own prose says nothing
        # about whether the recognizer's output was refusable.
        revalidated = False
        if mode == "fix" and target.name.endswith(".fail.tex"):
            clean, findings, gates = _revalidate(after, target)
            if clean:
                released = target.with_name(target.name.replace(".fail.tex", ".tex"))
                target.rename(released)
                _rewrite_manifest(self.review.out_dir, page, output=str(released),
                                  verdict="pass", findings=findings,
                                  gates=json.loads(gates))
                target, revalidated = released, True
            else:
                _rewrite_manifest(self.review.out_dir, page, findings=findings,
                                  gates=json.loads(gates))

        img = self.review.image(page)
        self.review.log().append(Correction(
            page=page, verdict=MODES[mode],  # type: ignore[arg-type]
            source_image=str(img) if img else "",
            before=before, after=after,
            seconds=float(payload.get("seconds", 0.0)),
            mode=payload.get("ui_mode", "web"),
            finding=("exit criterion: correction of emitted output" if mode == "fix"
                     else "author revised their own text"),
        ))
        self._json({"saved": True, "verdict": MODES[mode],
                    "revalidated": revalidated})


def serve(out_dir: Path, port: int = 8765, *, open_browser: bool = True) -> None:
    """Serve the review UI for one run directory. Blocks until interrupted."""
    Handler.review = Review(out_dir)
    # 127.0.0.1, never 0.0.0.0: page images are unpublished manuscript content.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"handzoo review UI: {url}\n  serving {out_dir}\n  local only — nothing leaves this "
          "machine. ctrl-c to stop.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
        time.sleep(0)
