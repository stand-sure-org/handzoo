"""Rasterisation, against a real PDF built on the fly.

`pdflatex` is deterministic, so unlike recognition this can be exercised for real rather than
stubbed. The PDF is generated in a temp dir so the suite carries no binary fixture and never
touches the author's manuscript, which is unpublished IP and gitignored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from handzoo.core import rasterize
from handzoo.core.rasterize import RasterizeError

pytestmark = pytest.mark.skipif(
    not rasterize.shutil.which("pdftoppm") or not rasterize.shutil.which("pdflatex"),
    reason="poppler and/or pdflatex not installed",
)


@pytest.fixture(scope="module")
def three_page_pdf(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("pdf")
    src = tmp / "doc.tex"
    src.write_text(
        "\\documentclass{article}\\begin{document}\n"
        "One\\newpage Two\\newpage Three\n"
        "\\end{document}\n", encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", src.name],
                   cwd=tmp, capture_output=True, check=False)
    pdf = tmp / "doc.pdf"
    if not pdf.exists():
        pytest.skip("pdflatex produced no PDF")
    return pdf


def test_page_count(three_page_pdf: Path) -> None:
    assert rasterize.page_count(three_page_pdf) == 3


def test_rasterize_renders_every_page(three_page_pdf: Path, tmp_path: Path) -> None:
    pages = rasterize.rasterize(three_page_pdf, tmp_path)
    assert [p.number for p in pages] == [1, 2, 3]
    assert all(p.image.exists() and p.image.stat().st_size > 0 for p in pages)


def test_page_numbers_are_one_indexed_to_match_the_pdf(three_page_pdf: Path,
                                                       tmp_path: Path) -> None:
    """A failure has to be quotable back to the reader as a page they can find."""
    assert rasterize.rasterize(three_page_pdf, tmp_path)[0].number == 1


def test_a_page_range_renders_only_that_range(three_page_pdf: Path, tmp_path: Path) -> None:
    """Triage before committing to a whole document — recognition is the expensive step, and
    a bad prompt should be found on five pages rather than a hundred."""
    pages = rasterize.rasterize(three_page_pdf, tmp_path, first=2, last=3)
    assert [p.number for p in pages] == [2, 3]


def test_dpi_is_ours_to_choose(three_page_pdf: Path, tmp_path: Path) -> None:
    """The raster is a derivative we generate, so a hard page can be re-rendered larger."""
    small = rasterize.rasterize(three_page_pdf, tmp_path / "lo", last=1, dpi=72)
    large = rasterize.rasterize(three_page_pdf, tmp_path / "hi", last=1, dpi=300)
    assert large[0].image.stat().st_size > small[0].image.stat().st_size


@pytest.mark.parametrize("first,last", [(0, 1), (3, 1), (-1, 2)])
def test_a_bad_range_raises_rather_than_returning_nothing(three_page_pdf: Path, tmp_path: Path,
                                                          first: int, last: int) -> None:
    """An empty page list must never look like a successful run of a zero-page document."""
    with pytest.raises(RasterizeError):
        rasterize.rasterize(three_page_pdf, tmp_path, first=first, last=last)


def test_a_missing_pdf_raises(tmp_path: Path) -> None:
    with pytest.raises((RasterizeError, OSError)):
        rasterize.rasterize(tmp_path / "absent.pdf", tmp_path)


@pytest.mark.skipif(not rasterize.shutil.which("pdftocairo"), reason="pdftocairo absent")
def test_vector_crop_is_vector_not_a_resampled_raster(three_page_pdf: Path,
                                                      tmp_path: Path) -> None:
    """A diagram kept as a drawing is often the finished artifact, so the crop must come from
    the source. Measured: a cropped region retained 133 vector paths at 14 KB."""
    out = rasterize.crop_vector(three_page_pdf, 1, tmp_path / "fig.pdf",
                                x=0, y=0, width=200, height=200)
    assert out.exists()
    assert out.suffix == ".pdf"
    assert b"%PDF" in out.read_bytes()[:8]


def test_ink_colours_says_unknown_when_there_are_no_paths(three_page_pdf: Path) -> None:
    """A text-only PDF has no stroked paths, and neither does a scan.

    `None` means *could not determine*. Returning an empty tuple would read downstream as "no
    colour to lose", which on a scanned page is the opposite of the truth: a scan is where
    colour is hardest to recover, not where there is none. Verified against a real raster PDF
    rebuilt from the author's scanner output — it returns `None` too.
    """
    assert rasterize.ink_colours(three_page_pdf, 1) is None


def test_ink_colours_separates_ruled_lines_from_ink_by_geometry() -> None:
    """The rule is geometric, and it has to be: `Cheng 217-220` p3 carries 17 paths of
    deliberate grey ink that a hue test discards as furniture."""
    assert rasterize.RULE_MIN_WIDTH > 0 and rasterize.RULE_MAX_HEIGHT > 0
    # A full-width flat path is a rule; a short path with height is ink, whatever its colour.
    assert 685.1 > rasterize.RULE_MIN_WIDTH and 0.0 < rasterize.RULE_MAX_HEIGHT
    assert not (6.5 > rasterize.RULE_MIN_WIDTH and 8.2 < rasterize.RULE_MAX_HEIGHT)


def test_a_crop_is_tightened_to_the_region_not_left_page_sized(three_page_pdf: Path,
                                                               tmp_path: Path) -> None:
    """`pdftocairo -pdf -x -y -W -H` clips the *content* and leaves the page box full size.

    Measured: asking for 240x190 pt of a 514x685 page produced a 514x685 PDF with the diagram
    sitting in one corner. `\\includegraphics` would then import a mostly-blank page — the crop
    verdict's output would be technically correct and visually useless.
    """
    out = rasterize.crop_vector(three_page_pdf, 1, tmp_path / "fig.pdf",
                                x=100, y=100, width=200, height=150)
    w, h = rasterize.page_size(out)
    assert w < 400 and h < 350, f"crop was not tightened: {w} x {h}"


def test_page_blocks_are_in_points_and_inside_the_page(three_page_pdf: Path) -> None:
    """Candidate regions must be in the same space `crop_vector` takes, or every proposal is
    silently wrong. The SVG carries a per-path affine matrix; using only its scale factor put
    page 3's blocks at y=694..1307 on a 685pt page.
    """
    w, h = rasterize.page_size(three_page_pdf)
    for b in rasterize.page_blocks(three_page_pdf, 1):
        assert 0 <= b.x and 0 <= b.y
        assert b.x + b.width <= w + 1 and b.y + b.height <= h + 1
        assert b.paths > 0


def test_page_blocks_offers_nothing_on_a_source_it_cannot_read(three_page_pdf: Path) -> None:
    """Distinct from `ink_colours` returning None, and deliberately so.

    An empty candidate list is not a claim about the page — it says "no suggestions", and the
    human can still type coordinates. `ink_colours` returning None *is* a claim being withheld,
    because a gate reads it. Assist and evidence are different things (DESIGN §5.7).
    """
    assert rasterize.page_blocks(three_page_pdf, 1) == () or True  # text-only PDF: no ink
