r"""Crop in the review surface.

**Why this is the next thing built.** 45 of 49 findings on a real run are fabricated diagrams
(DESIGN §7.2), and the UI could not address any of them — 10 of ch22's 35 pages were
diagram-only and unfinishable there. The gates' majority complaint had no answer in the tool
the author actually uses.

**Regions come from the ink, not from a drag.** `rasterize.page_blocks` already groups strokes
into candidate bands, which the CLI offers as a numbered list. The surface can do better with
the same data: draw them over the page and let the human click one. Freehand is kept as the
fallback, because the bands are *an assist, not evidence* — an empty list means "no
suggestions", never "no diagram here".
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from handzoo.adapters.ui_server import Handler, Review
from handzoo.core.pipeline import PageOutcome


def _get(base, path):
    with urlopen(base + path) as r:
        return r.status, r.read()


def _post(base, path, payload):
    req = Request(base + path, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req) as r:
        return json.loads(r.read())


@pytest.fixture
def cropserver(tmp_path: Path):
    (tmp_path / "pages").mkdir()
    marked = tmp_path / "page-0001.tex"
    marked.write_text("Before it.\n\n\\texttt{[TODO diagram: an invented drawing]}\n\nAfter.\n",
                      encoding="utf-8")
    (tmp_path / "manifest.jsonl").write_text(json.dumps({
        "page": 1, "output": str(marked), "verdict": "fail", "gates": {},
        "source": "",     # deliberately empty — see the test below
        "findings": [{"gate": "coverage", "detail": "recognizer fabricated a drawing here"}],
    }) + "\n", encoding="utf-8")

    Handler.review = Review(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()
    srv.server_close()


def test_a_manifest_without_a_source_says_so_rather_than_offering_nothing(cropserver) -> None:
    r"""Manifests written before `PageOutcome.source` existed have none, and ch18's is one.

    An empty region list would read as "no diagram here" — the §5.7 failure. The reason has
    to reach the surface, because the fix is a re-run and nobody can guess that.
    """
    base, _ = cropserver
    with pytest.raises(HTTPError) as e:
        _get(base, "/api/regions?page=1")
    assert e.value.code == 409
    assert "source" in e.value.read().decode().lower()


def test_regions_are_returned_as_fractions_so_display_scale_cannot_break_them(tmp_path) -> None:
    """The page image is rendered at 150 dpi, the blocks are in 72 dpi points, and the browser
    scales the img to fit its pane. Three coordinate spaces is two too many: fractions of the
    page survive all of them."""
    from handzoo.adapters.ui_server import _region_fractions

    got = _region_fractions({"x": 72, "y": 144, "width": 144, "height": 72},
                            page_width=288.0, page_height=576.0)
    assert got == {"left": 0.25, "top": 0.25, "width": 0.5, "height": 0.125}


def test_cropping_a_page_with_no_marker_is_refused(cropserver) -> None:
    r"""The crop replaces a marker. With none there is nothing to replace, and inserting the
    figure at a guessed position would put content somewhere the author did not choose.

    The source is stubbed present so the *marker* branch is what is exercised: missing-source
    is checked first because it is the more actionable failure (the fix is a re-run).
    """
    base, run = cropserver
    stub = run / "src.pdf"
    stub.write_bytes(b"%PDF-1.4 stub")
    rows = [json.loads(l) for l in (run / "manifest.jsonl").read_text().splitlines() if l.strip()]
    rows[0]["source"] = str(stub)
    (run / "manifest.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (run / "page-0001.tex").write_text("No marker anywhere.\n", encoding="utf-8")
    with pytest.raises(HTTPError) as e:
        _post(base, "/api/crop", {"page": 1, "region": {"x": 0, "y": 0, "w": 10, "h": 10}})
    assert e.value.code == 409
    assert "marker" in e.value.read().decode().lower()


def test_the_marker_is_replaced_by_a_figure_and_the_prose_survives() -> None:
    r"""Regression for the substitution bug the CLI hit: `re.sub` reads backslashes in a
    replacement *string* as escapes, and `\i` of `\includegraphics` is not a valid one — the
    figure blew up the substitution rather than the document."""
    from handzoo.adapters.ui_server import _replace_marker

    out = _replace_marker("Before.\n\n\\texttt{[TODO diagram: x]}\n\nAfter.\n",
                          "fig-p0001-1.pdf")
    assert "\\includegraphics" in out
    assert "fig-p0001-1.pdf" in out
    assert "Before." in out and "After." in out
    assert "TODO diagram" not in out


def test_only_the_first_marker_goes_at_a_time() -> None:
    """A page can carry several invented drawings, and they are different regions. Replacing
    them all with one crop would be a fabrication of our own."""
    from handzoo.adapters.ui_server import _replace_marker

    two = "\\texttt{[TODO diagram: a]}\nmiddle\n\\texttt{[TODO diagram: b]}\n"
    out = _replace_marker(two, "fig.pdf")
    assert out.count("includegraphics") == 1
    assert "TODO diagram: b" in out


def test_a_fully_cropped_page_is_released_from_quarantine(tmp_path: Path) -> None:
    r"""Without this a cropped page never clears, which makes the tool a dead end.

    The coverage gate as a whole cannot re-run here — it needs the mark inventory from the
    recognition pass, which exists only during a run. But *fabrication* findings are a
    different thing: R9 turns an invented drawing into a `[[FABRICATED: ...]]` marker, and
    `coverage_gate.fabrications()` reads them from the text alone. So the exact check that
    failed **can** be re-run, and a page whose only complaint was invented drawings can be
    released once none remain.

    A page with any *other* coverage finding stays quarantined: that one is genuinely
    unre-checkable here, and releasing it would be claiming a check that never ran (§5.7).
    """
    from handzoo.adapters.ui_server import _fabrications_cleared

    fab = [{"gate": "coverage", "detail": "recognizer fabricated a drawing here"}]
    mixed = fab + [{"gate": "coverage", "detail": "3 marks seen, 1 accounted for"}]
    clean = "\\documentclass{article}\\begin{document}\n\\includegraphics{f.pdf}\n\\end{document}"
    dirty = ("\\documentclass{article}\\begin{document}\n"
             "\\texttt{[TODO fabricated: still invented]}\n\\end{document}")

    assert _fabrications_cleared(fab, clean) is True
    assert _fabrications_cleared(fab, dirty) is False, "a marker still stands"
    assert _fabrications_cleared(mixed, clean) is False, "the other finding cannot be rechecked"
    assert _fabrications_cleared([], clean) is False, "nothing was quarantined for fabrication"


def test_release_uses_the_same_pattern_the_replacement_consumes() -> None:
    r"""Release asks "is a marker left?"; the crop asks "is there one to replace?". Those must
    be the same question, so both go through `_MARKER`.

    `coverage_gate.fabrications` matches the emitted form today as well, so this is not a live
    bug — it is a guard against the two drifting apart, since they are written against
    different stages of the pipeline (`fabrications` also reads the pre-normalization
    `[[FABRICATED: ...]]` form, which never reaches an emitted page).
    """
    from handzoo.adapters.ui_server import _fabrications_cleared

    fab = [{"gate": "coverage", "detail": "recognizer fabricated a drawing here"}]
    one_left = ("\\includegraphics{fig-p0006-1.pdf}\n"
                "\\texttt{[TODO fabricated: a second invented drawing]}\n")
    none_left = "\\includegraphics{fig-p0006-1.pdf}\n\\includegraphics{fig-p0006-2.pdf}\n"

    assert _fabrications_cleared(fab, one_left) is False, "one still stands"
    assert _fabrications_cleared(fab, none_left) is True


def test_a_region_can_be_placed_where_there_is_no_marker(cropserver) -> None:
    r"""Not every drawing the author wants is one the recognizer flagged.

    The marker path replaces something the gates objected to. This one inserts at a position
    the author chose, because there is nothing to replace — and the position has to come from
    the author for the same reason the marker path exists: putting it somewhere we guessed
    would be a placement we invented.
    """
    from handzoo.adapters.ui_server import _insert_at

    text = "First line.\nSecond line.\nThird line.\n"
    at = text.index("Second")
    out = _insert_at(text, "fig-p0001-1.pdf", at)

    assert out.startswith("First line.\n")
    assert "\\includegraphics" in out
    assert out.index("includegraphics") < out.index("Second line.")
    assert "Third line." in out


def test_an_insert_point_past_the_end_lands_at_the_end(cropserver) -> None:
    """A stale cursor offset must not raise or truncate."""
    from handzoo.adapters.ui_server import _insert_at

    out = _insert_at("short\n", "f.pdf", 9999)
    assert out.startswith("short\n")
    assert "\\includegraphics" in out


def test_the_typeset_pane_shows_the_page_not_an_assembly_placeholder(tmp_path: Path) -> None:
    r"""A failing fragment rendered as `[PAGE 3 MISSING --- failed delimiters]`.

    `typeset` reuses `assemble` so the master owns the preamble a fragment lacks. But
    `assemble` also decides *what to include*, and it excludes a failed page by design — a
    chapter must not silently carry one. For a **preview** that rule is wrong: the author is
    looking at the page precisely because it failed, and the placeholder compiles cleanly, so
    the pane reported success over a document with none of their content in it.

    Worse than unhelpful — it is the §5.7 shape, where a valid render of nothing reads as a
    valid render.

    The preview includes the content whatever the gates said. A page that cannot compile then
    fails to compile, and the pane shows the real error.
    """
    from handzoo.adapters.ui_server import typeset

    (tmp_path / "pages").mkdir()
    broken = tmp_path / "page-0003.fail.tex"
    broken.write_text("\\begin{itemize}\n\\item never closed\n", encoding="utf-8")
    outcome = PageOutcome(page=3, output=str(broken), verdict="fail", gates={},
                          findings=[{"gate": "delimiters", "detail": "never closed"}])

    pdf, err = typeset(tmp_path, outcome)
    assert pdf is None, "a page with an unclosed environment must not typeset"
    assert "MISSING" not in err, "the reason must be the compile error, not an assembly note"
    assert err.strip(), "and it must say something"
