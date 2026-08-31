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

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core import rasterize
from ..core.corrections import Correction, CorrectionLog
from ..core.pipeline import MANIFEST, PageOutcome
from ..core.validate import (ascii_gate, colour_gate, compile_gate, delimiter_gate,
                             reference_gate, repetition_gate)

HERE = Path(__file__).resolve().parent / "ui"

# What the two buttons write. `edited` feeds the exit criterion and the defect taxonomy;
# `authored` feeds neither, and is excluded from lexicon mining.
MODES = {"fix": "edited", "author": "authored", "accept": "keep-reviewed"}
"""What each action writes.

`fix`    — corrected the transcription. Feeds the exit criterion and the defect taxonomy.
`author` — revised one's own prose. Feeds neither (DESIGN 11.3.1).
`accept` — read it and it is right. **GOLD**, and the datum the CLI's `--fix` already
           collected by asking about an unchanged document. Its absence here cost a whole
           run: an author who read 35 pages and found them correct produced an empty log,
           because an unchanged save recorded nothing. Reading is the expensive part and it
           left no trace.
"""


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

    def pristine(self, page: int) -> Path:
        """Where the pre-edit text is kept while a page is being worked on.

        Autosave overwrites the page file, so by the time a verdict is recorded the on-disk
        text *is* the edit — `before` and `after` would be identical and the diff empty. The
        defect taxonomy (DESIGN 11.0.1a) is built from those diffs, so an empty one is not a
        cosmetic loss.

        On disk rather than in memory so a browser reload, or a restarted server, does not
        silently reset the baseline mid-edit.
        """
        return self.out_dir / ".pristine" / f"p{page:04d}.tex"


def _pages(review: Review) -> list[dict]:
    """The page list, with each page's worst gate state.

    Advisory findings are reported as `flag`, never as `fail`: the reference gate marks a
    convention for a human to look at and does not refuse the page (DESIGN 11.0.1b). Collapsing
    the two would train the reader to ignore both.
    """
    seen = {r.page for r in review.log().read()}
    out = []
    for o in review.outcomes():
        findings = o.findings or []
        advisory = any(f.get("gate") == "reference" for f in findings)
        hard = [f for f in findings if f.get("gate") != "reference"]
        state = "fail" if hard else ("flag" if advisory else "ok")
        # A page whose *only* complaint is an invented drawing needs the crop tool, not the
        # editor. Marking it lets a text-only pass skip it without opening it -- opening it
        # would put its content on screen and cost the page as a transcription subject.
        diagram_only = bool(findings) and all(
            "fabricated" in f.get("detail", "") or "diagram" in f.get("detail", "").lower()
            for f in findings)
        out.append({"page": o.page, "state": state, "verdict": o.verdict,
                    "findings": findings, "reviewed": o.page in seen,
                    "diagram_only": diagram_only,
                    "has_image": review.image(o.page) is not None})
    return out


# Shared with the CLI rather than restated: the two paths must agree about what a marker is,
# or a crop made in one tool is invisible to the other.
from .cli_review import CROP_WIDTH, _MARKER  # noqa: E402


def _region_fractions(region: dict, *, page_width: float, page_height: float) -> dict:
    """A candidate region as fractions of the page.

    Three coordinate spaces are in play: `page_blocks` returns points at 72 dpi, the page
    image is rendered at 150, and the browser scales that image to fit its pane. Fractions
    survive all three, so the overlay lines up without the client knowing any of them.
    """
    return {"left": region["x"] / page_width,
            "top": region["y"] / page_height,
            "width": region["width"] / page_width,
            "height": region["height"] / page_height}


def _fabrications_cleared(findings: list[dict], text: str) -> bool:
    """Were *all* this page's complaints invented drawings, and are they now gone?

    The coverage gate cannot re-run here — it needs the mark inventory from the recognition
    pass. But fabrication findings are recorded as markers in the text, and
    `coverage_gate.fabrications()` reads them from the text alone, so the exact check that
    failed can be re-run.

    A page carrying any other coverage finding stays quarantined. That one is genuinely
    unre-checkable at this point, and releasing it would claim a check that never ran
    (DESIGN 5.7).
    """
    from ..core.validate import coverage_gate

    if not findings:
        return False
    if not all("fabricated" in f.get("detail", "") for f in findings):
        return False
    return not coverage_gate.fabrications(text)


def _replace_marker(text: str, figure_name: str) -> str:
    r"""Put the figure where the marker was — **one** marker, the first.

    A function replacement, not a string: `re.sub` treats backslashes in a replacement string
    as escapes and `\i` of `\includegraphics` is not a valid one, so the figure would blow up
    the substitution rather than the document. The CLI hit this; the same shape is used here.

    One at a time because a page can carry several invented drawings and they are different
    regions. Replacing them all with one crop would be a fabrication of our own.
    """
    figure = f"\\includegraphics[width={CROP_WIDTH}]{{{figure_name}}}"
    return _MARKER.sub(lambda _m: figure, text, count=1)


def typeset(out_dir: Path, outcome: PageOutcome) -> tuple[Path | None, str]:
    r"""Compile one page so the author can proof against the *rendered* result.

    Proofing against typeset output is faster than proofing against source — a dropped
    subscript is obvious in a rendered formula and easy to miss in a line of markup. It is
    also the only view that shows what the reader will actually see.

    Reuses `cli_review._typeset` rather than inventing a second path to a document: a
    one-page master owns the preamble a fragment lacks and keeps crop figures within reach of
    a relative `\includegraphics` (DESIGN 6.1).

    Returns `(pdf, error)`. A page that does not compile returns `(None, log)` and the log is
    shown — **"could not typeset" must never render as an empty pane**, which reads as "nothing
    on this page" (DESIGN 5.7).
    """
    from .cli_review import _typeset

    # Content-addressed, and that is not an optimisation. `Cache-Control: no-store` makes the
    # browser re-request, so an iframe fires a *second* compile that overwrites the PDF while
    # the first one is still being read -- the viewer renders a few bytes and then gives up.
    # Measured as "I did see it flash". Keying on the source hash makes a repeat request a
    # file read instead of a race.
    source_text = Path(outcome.output).read_text(encoding="utf-8") if outcome.output else ""
    if not source_text:
        return None, "no output on disk for this page."
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16]
    cached = out_dir / ".typeset" / f"p{outcome.page:04d}-{digest}.pdf"
    if cached.exists():
        return cached, ""

    pdf = _typeset(outcome, out_dir)
    if pdf:
        cached.parent.mkdir(exist_ok=True)
        cached.write_bytes(pdf.read_bytes())
        return cached, ""
    text = Path(outcome.output).read_text(encoding="utf-8") if outcome.output else ""
    result = compile_gate.check(text, base_dir=out_dir)
    detail = "\n".join(f"line {f.line}: {f.detail}" if f.line else f.detail
                        for f in result.failures) or "pdflatex produced no output."
    return None, detail


def typeset_png(out_dir: Path, outcome: PageOutcome) -> tuple[Path | None, str]:
    """The typeset page as a PNG.

    **Deliberately not a PDF in an iframe.** Whether a browser renders an embedded PDF depends
    on the viewer it happens to be using — a PDF *extension* commonly does not hook iframes at
    all, and the pane silently goes white. Measured on this author's Chrome.

    Rasterising server-side removes the variable: `pdftoppm` is already a hard dependency (it
    is how page images are made at all), the result is an `<img>` like the ink pane beside it,
    and nothing about the viewer's configuration can change the answer.

    The PDF endpoint stays — it is the right artefact to annotate or download — but proofing
    reads a picture.
    """
    pdf, err = typeset(out_dir, outcome)
    if pdf is None:
        return None, err
    png = pdf.with_suffix(".png")
    if png.exists():
        return png, ""
    proc = subprocess.run(
        ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1", "-singlefile",
         str(pdf), str(pdf.with_suffix(""))],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not png.exists():
        return None, f"typeset, but could not rasterise it:\n{proc.stderr[:400]}"
    return png, ""


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
        repetition_gate.check(text),
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
        elif url.path == "/api/typeset":
            page = int(q["page"][0])
            match = [o for o in self.review.outcomes() if o.page == page]
            if not match:
                self._json({"error": "no such page"}, 404)
                return
            want_pdf = q.get("as", [""])[0] == "pdf"
            fn = typeset if want_pdf else typeset_png
            pdf, err = fn(self.review.out_dir, match[0])
            if pdf is None:
                # 200 with the reason, not 404: the pane must say *why* rather than go blank.
                self._send(err.encode("utf-8"), "text/plain; charset=utf-8", 409)
                return
            self._send(pdf.read_bytes(),
                       "application/pdf" if want_pdf else "image/png")
        elif url.path == "/api/regions":
            page = int(q["page"][0])
            match = [o for o in self.review.outcomes() if o.page == page]
            if not match:
                self._json({"error": "no such page"}, 404)
                return
            outcome = match[0]
            if not outcome.source or not Path(outcome.source).exists():
                # An empty list would read as "no diagram here" — the DESIGN 5.7 failure. The
                # reason has to reach the surface, because the fix is a re-run of `handzoo`
                # and nobody can guess that from silence.
                self._send(b"no source PDF recorded for this page, so there is nothing to "
                           b"crop from. Manifests written before `source` existed have none "
                           b"-- re-run `handzoo` on the PDF.",
                           "text/plain; charset=utf-8", 409)
                return
            pdf = Path(outcome.source)
            try:
                blocks = rasterize.page_blocks(pdf, page)
                pw, ph = rasterize.page_size(pdf)
            except rasterize.RasterizeError as exc:
                self._send(str(exc).encode(), "text/plain; charset=utf-8", 409)
                return
            self._json({
                "page": page, "page_width": pw, "page_height": ph,
                "markers": len(_MARKER.findall(
                    Path(outcome.output).read_text(encoding="utf-8"))) if outcome.output else 0,
                "regions": [{"points": b.region, "paths": b.paths,
                             **_region_fractions(b.region, page_width=pw, page_height=ph)}
                            for b in blocks],
            })
        elif url.path == "/api/figure":
            fig = self.review.out_dir / Path(q["name"][0]).name
            if not fig.exists() or fig.suffix != ".pdf":
                self._json({"error": "no such figure"}, 404)
                return
            png = fig.with_suffix(".png")
            if not png.exists():
                subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile",
                                str(fig), str(fig.with_suffix(""))],
                               capture_output=True, check=False)
            self._send(png.read_bytes() if png.exists() else fig.read_bytes(),
                       "image/png" if png.exists() else "application/pdf")
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
        if url.path not in ("/api/save", "/api/autosave", "/api/crop",
                            "/api/crop/confirm"):
            self._json({"error": "not found"}, 404)
            return

        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")

        if url.path == "/api/crop":
            page = int(payload["page"])
            match = [o for o in self.review.outcomes() if o.page == page]
            if not match or not match[0].output:
                self._json({"error": "no output on disk for this page"}, 404)
                return
            outcome = match[0]
            target = Path(outcome.output)
            if not outcome.source or not Path(outcome.source).exists():
                self._send(b"no source PDF recorded for this page", "text/plain", 409)
                return
            text = target.read_text(encoding="utf-8")
            if not _MARKER.search(text):
                # The crop replaces a marker. With none there is nothing to replace, and
                # inserting the figure at a guessed position would put content somewhere the
                # author did not choose -- a placement we invented.
                self._send(b"no diagram marker on this page to replace", "text/plain", 409)
                return

            r = payload["region"]
            existing = len(list(self.review.out_dir.glob(f"fig-p{page:04d}-*.pdf")))
            name = f"fig-p{page:04d}-{existing + 1}.pdf"
            try:
                figure = rasterize.crop_vector(
                    Path(outcome.source), page, self.review.out_dir / name,
                    x=int(r["x"]), y=int(r["y"]),
                    width=int(r.get("w", r.get("width"))),
                    height=int(r.get("h", r.get("height"))))
            except rasterize.RasterizeError as exc:
                self._send(str(exc).encode(), "text/plain; charset=utf-8", 409)
                return
            # Cut but not committed: the author sees it before the marker is replaced. A
            # crop that turns out to hold the wrong band is one keystroke from being retried.
            self._json({"cropped": True, "name": name,
                        "bytes": figure.stat().st_size,
                        "preview": f"/api/figure?name={name}"})
            return

        if url.path == "/api/crop/confirm":
            page = int(payload["page"])
            name = Path(payload["name"]).name
            match = [o for o in self.review.outcomes() if o.page == page]
            if not match or not match[0].output:
                self._json({"error": "no output on disk for this page"}, 404)
                return
            target = Path(match[0].output)
            before = target.read_text(encoding="utf-8")
            after = _replace_marker(before, name)
            if after == before:
                self._send(b"no marker was replaced", "text/plain", 409)
                return
            target.write_text(after, encoding="utf-8")

            img = self.review.image(page)
            self.review.log().append(Correction(
                page=page, verdict="cropped",
                source_image=str(img) if img else "",
                before=before, after=name,
                seconds=float(payload.get("seconds", 0.0)),
                mode=payload.get("ui_mode", "web"),
                # `cropped` is GOLD and counted separately: diagrams are 45 of 49 findings on
                # a real run, so seconds-per-crop is most of the exit criterion rather than a
                # footnote in it. It is not the correction arm's marker -- supplying a drawing
                # the tool refused to invent is a different act from fixing transcribed text.
                finding="supplied a cropped figure for an invented drawing",
            ))
            released = False
            if target.name.endswith(".fail.tex") and _fabrications_cleared(
                    match[0].findings or [], after):
                clean, findings, gates = _revalidate(after, target)
                if clean:
                    freed = target.with_name(target.name.replace(".fail.tex", ".tex"))
                    target.rename(freed)
                    _rewrite_manifest(self.review.out_dir, page, output=str(freed),
                                      verdict="pass", findings=findings,
                                      gates=json.loads(gates))
                    released = True

            self._json({"saved": True, "verdict": "cropped", "released": released,
                        "remaining": len(_MARKER.findall(after))})
            return

        if url.path == "/api/autosave":
            # Writes the file, records nothing. Losing an edit on navigation is bad; recording
            # a half-typed line as a judgement about the page is worse, and a log full of them
            # would drown the verdicts that mean something.
            page = int(payload["page"])
            match = [o for o in self.review.outcomes() if o.page == page]
            if not match or not match[0].output:
                self._json({"error": "no output on disk for this page"}, 404)
                return
            target = Path(match[0].output)
            snapshot = self.review.pristine(page)
            if not snapshot.exists():
                snapshot.parent.mkdir(exist_ok=True)
                snapshot.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            target.write_text(payload.get("text", ""), encoding="utf-8")
            self._json({"autosaved": True})
            return

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
        snapshot = self.review.pristine(page)
        before = (snapshot.read_text(encoding="utf-8") if snapshot.exists()
                  else target.read_text(encoding="utf-8"))
        after = payload.get("text", "")

        if mode == "accept":
            if after != before:
                self._json({"error": "the text was changed — that is a fix, not an accept"},
                           400)
                return
            img = self.review.image(page)
            self.review.log().append(Correction(
                page=page, verdict="keep-reviewed",
                source_image=str(img) if img else "",
                before=before, after="",
                seconds=float(payload.get("seconds", 0.0)),
                mode=payload.get("ui_mode", "web"),
                # Deliberately NOT the exit-criterion marker: reading a page and finding it
                # right is not correcting one, and folding the time into the correction arm
                # would inflate it with pages that needed no work.
                finding="read and accepted as correct",
            ))
            snapshot.unlink(missing_ok=True)
            self._json({"saved": True, "verdict": "keep-reviewed"})
            return

        if after == before:
            # Unchanged is not nothing, and it is not a correction either. Same reasoning as
            # `--fix` (DESIGN 11.1.1): it may mean the output was already right, which is the
            # most valuable datum here, or that the editor was opened and closed.
            self._json({"saved": False, "reason": "unchanged",
                        "hint": "nothing changed — use \"Looks right\" to record that you "
                                "read it and it is correct, which is evidence a gate cannot "
                                "produce"})
            return

        target.write_text(after, encoding="utf-8")

        # Re-gate in **both** directions. Measured in the wild: ch18 p13 passed every gate,
        # the author's own correction added `\square` -- a math-mode command -- in text mode,
        # and the page stopped compiling. Nothing noticed, because re-validation ran only on
        # pages that were already quarantined, and it sat broken. The author is not the
        # recognizer but is equally able to write LaTeX that does not build.
        #
        # `authored` never re-gates: revising one's own prose is not a claim about what the
        # recognizer produced, and a verdict on it would be a verdict on the author.
        revalidated = quarantined = False
        if mode == "fix":
            was_quarantined = target.name.endswith(".fail.tex")
            clean, findings, gates = _revalidate(after, target)
            if clean and was_quarantined:
                released = target.with_name(target.name.replace(".fail.tex", ".tex"))
                target.rename(released)
                _rewrite_manifest(self.review.out_dir, page, output=str(released),
                                  verdict="pass", findings=findings, gates=json.loads(gates))
                target, revalidated = released, True
            elif not clean and not was_quarantined:
                held = target.with_name(target.name.replace(".tex", ".fail.tex"))
                target.rename(held)
                _rewrite_manifest(self.review.out_dir, page, output=str(held),
                                  verdict="fail", findings=findings, gates=json.loads(gates))
                target, quarantined = held, True
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
        snapshot.unlink(missing_ok=True)
        self._json({"saved": True, "verdict": MODES[mode],
                    "revalidated": revalidated, "quarantined": quarantined})


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
