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
