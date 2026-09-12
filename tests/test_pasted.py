r"""Detecting pasted raster content — the one thing nothing else on a page can see.

**Measured on Leinster 1.1 (DESIGN §11.2.6).** Pages 14, 15, 17 and 18 carry screenshots of
the book's printed exercises, pasted above the author's handwritten answers. p14 **passed
every gate** and emitted Leinster's exercise text verbatim.

Nothing existing could see it:

- the **colour gate** reports one ink colour, because a raster has no stroke colour — the
  discriminator that worked on the previous corpus (black print against purple pen) is blind
  here;
- **`page_blocks`** groups vector paths, so no band is offered over the pasted region and the
  crop tool cannot reach it;
- there is **no text layer**, so nothing downstream knows the pixels are words.

`pdfimages` answers it directly, which makes this the cheapest check in the project.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from handzoo.core import rasterize
from handzoo.core.validate import pasted_gate


def _pdf_with_image(d: Path) -> Path | None:
    """A one-page PDF carrying an embedded raster, built rather than committed."""
    png = d / "blob.png"
    # A 2x2 PNG, written by hand so the fixture needs no image library.
    png.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000200000002080200000057dd52f8"
        "0000000f4944415408d763f8cfc0f01f0405000d0a02fdb0e2b1350000000049454e44ae426082"))
    (d / "m.tex").write_text(
        "\\documentclass{article}\\usepackage{graphicx}\\pagestyle{empty}\n"
        "\\begin{document}\\includegraphics[width=2in]{blob.png}\\end{document}\n",
        encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "m.tex"],
                   cwd=d, capture_output=True, check=False)
    return (d / "m.pdf") if (d / "m.pdf").exists() else None


@pytest.mark.skipif(not shutil.which("pdfimages"), reason="poppler not installed")
def test_a_page_with_no_pasted_raster_reports_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "m.tex").write_text(
            "\\documentclass{article}\\pagestyle{empty}\n"
            "\\begin{document}just words\\end{document}\n", encoding="utf-8")
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "m.tex"],
                       cwd=d, capture_output=True, check=False)
        if not (d / "m.pdf").exists():
            pytest.skip("could not build the fixture")
        assert rasterize.embedded_images(d / "m.pdf", 1) == 0


@pytest.mark.skipif(not (shutil.which("pdfimages") and shutil.which("pdflatex")),
                    reason="poppler or pdflatex not installed")
def test_a_pasted_raster_is_counted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        pdf = _pdf_with_image(d)
        if pdf is None:
            pytest.skip("could not build the fixture")
        assert rasterize.embedded_images(pdf, 1) >= 1


def test_a_missing_tool_is_not_reported_as_a_clean_page() -> None:
    r"""DESIGN §5.7. "No pasted image" and "could not look" must not be the same answer, and
    here they would differ by a `FileNotFoundError` nobody sees.
    """
    import pytest as _pytest

    with tempfile.TemporaryDirectory() as tmp:
        with _pytest.raises(rasterize.RasterizeError):
            rasterize.embedded_images(Path(tmp) / "does-not-exist.pdf", 1)


PASTED_SVG = '''<svg width="514pt" height="685pt" viewBox="0 0 514 685">
<defs>
<image id="source-7" x="0" y="0" width="1162" height="323" xlink:href="data:image/png;base64,AA"/>
<mask id="mask-0"><use xlink:href="#source-7" transform="matrix(0.380831, 0, 0, 0.380831, 39.459775, 534.478226)"/></mask>
<image id="source-6" x="0" y="0" width="1162" height="323" xlink:href="data:image/png;base64,AA"/>
</defs>
<g mask="url(#mask-0)"><use xlink:href="#source-6" transform="matrix(0.380831, 0, 0, 0.380831, 39.459775, 534.478226)"/></g>
</svg>'''


def test_a_pasted_capture_is_located_not_only_counted() -> None:
    """reMarkable's 2026-09 capture tool pastes a region of the page's background -- typeset
    text included -- as a raster. The gate could say a page carried one; it could not say
    where. Knowing where is what lets the author keep it as a picture instead of having it
    transcribed: the region is the crop, with nothing guessed."""
    regions = rasterize.pasted_regions_from_svg(PASTED_SVG)
    assert len(regions) == 1, "one capture, not one per use of its image"
    r = regions[0]
    assert (round(r["width"]), round(r["height"])) == (443, 123)
    assert (round(r["x"]), round(r["y"])) == (39, 534)


def test_a_page_with_no_capture_has_no_regions() -> None:
    assert rasterize.pasted_regions_from_svg('<svg><path d="M 1 2 L 3 4"/></svg>') == ()


def test_the_finding_says_where_the_capture_is() -> None:
    """"One pasted image on this page" leaves the author hunting for it. The file knows the
    rectangle; saying it is the difference between a warning and an instruction."""
    r = pasted_gate.check(1, regions=({"x": 39.5, "y": 534.5, "width": 442.5, "height": 123.0},))
    detail = r.failures[0].detail
    assert "442x123 pt" in detail and "(40, 534)" in detail


def test_a_count_without_regions_still_reports(monkeypatch) -> None:
    """Locating needs the vector source; counting does not. A page whose regions could not be
    read must still say a capture is there."""
    r = pasted_gate.check(2, regions=())
    assert not r.passed and "2 pasted image" in r.failures[0].detail
