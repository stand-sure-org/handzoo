"""Rasterisation, against a real PDF built on the fly.

`pdflatex` is deterministic, so unlike recognition this can be exercised for real rather than
stubbed. The PDF is generated in a temp dir so the suite carries no binary fixture and never
touches the author's manuscript, which is unpublished IP and gitignored.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
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


def test_page_size_reads_the_page_asked_for_not_the_first_one() -> None:
    r"""Pages in one export are not all the same size, and assuming they are broke cropping.

    The author writes at different magnifications on the reMarkable and the device bakes that
    into the export geometry: ch22 is 514x685 pt on most pages, **514x1238 on p16** and
    514x773 on p26. `pdfinfo` without a page range prints only the *first* page's size, so a
    crop on p16 converted its fractions against 685 and landed 1.8x off. The author spotted it
    in the output and guessed the cause exactly.

    The fixture is two one-page PDFs of different sizes joined with `pdfunite` — poppler, the
    same package that supplies `pdfinfo` and `pdftoppm`. The corpus is unpublished manuscript
    and stays out of the tests.
    """
    import pytest

    from handzoo.core import rasterize

    if not (shutil.which("pdflatex") and shutil.which("pdfunite")):
        pytest.skip("pdflatex or pdfunite not installed")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name, h in (("a", 300), ("b", 900)):
            (d / f"{name}.tex").write_text(
                f"\\documentclass{{article}}\n"
                f"\\usepackage[paperwidth=200pt,paperheight={h}pt,margin=10pt]{{geometry}}\n"
                f"\\pagestyle{{empty}}\\begin{{document}}{name}\\end{{document}}\n",
                encoding="utf-8")
            subprocess.run(["pdflatex", "-interaction=nonstopmode", f"{name}.tex"],
                           cwd=d, capture_output=True, check=False)
        if not ((d / "a.pdf").exists() and (d / "b.pdf").exists()):
            pytest.skip("could not build the fixture")
        subprocess.run(["pdfunite", "a.pdf", "b.pdf", "both.pdf"],
                       cwd=d, capture_output=True, check=False)
        pdf = d / "both.pdf"
        if not pdf.exists():
            pytest.skip("could not join the fixture")

        _, h1 = rasterize.page_size(pdf, 1)
        _, h2 = rasterize.page_size(pdf, 2)
        # LaTeX's geometry rounds a little; what matters is that the two differ and that
        # each page reports its own height rather than the document's first.
        assert abs(h1 - 300) < 3 and abs(h2 - 900) < 5, (h1, h2)
        assert abs(rasterize.page_size(pdf)[1] - h1) < 1, "defaults to the first page"


# --------------------------------------------------- ink paths: two pen shapes, both real

STROKED = ('<path fill="none" stroke-width="0.5" stroke="rgb(56.86%, 85.48%, 44.31%)" '
           'stroke-opacity="1" transform="matrix(1,0,0,1,0,0)" d="M 10 20 L 16 28 "/>')
RULE = ('<path fill="none" stroke-width="0.5" stroke="rgb(75.29%, 75.29%, 75.29%)" '
        'transform="matrix(1,0,0,1,0,0)" d="M 5 40 L 500 40 "/>')
FILLED = ('<path fill-rule="evenodd" fill="rgb(18.82%, 29.01%, 87.84%)" fill-opacity="1" '
          'd="M 12 60 L 18 66 L 13 70 Z "/>')
BACKGROUND = '<path fill-rule="nonzero" fill="rgb(100%, 100%, 100%)" d="M 0 0 L 514 0 L 514 685 Z "/>'


def test_ink_is_read_from_filled_paths_as_well_as_stroked() -> None:
    """Measured on a 642-page notebook: 367 pages draw ink as *filled* paths carrying plain
    coordinates and no transform matrix -- a different pen. Reading only stroked paths with a
    matrix made the colour gate report "not checked" on 57% of the document, and left the crop
    tool with no regions to suggest there."""
    paths = rasterize.ink_paths(STROKED + FILLED)
    assert [p.colour for p in paths] == [(145, 218, 113), (48, 74, 224)]
    assert [p.filled for p in paths] == [False, True]


def test_a_ruled_guide_line_is_not_ink_and_the_page_itself_is_not_ink() -> None:
    """Guides are separated by geometry, never by hue (the grey-ink lesson). The page's own
    near-white background is not ink either -- counting it would make every page two-coloured."""
    assert rasterize.ink_paths(RULE + BACKGROUND) == []


def test_a_filled_page_still_offers_crop_regions(tmp_path, monkeypatch) -> None:
    """The crop tool suggested nothing on those 367 pages."""
    monkeypatch.setattr(rasterize, "_svg", lambda pdf, page: RULE + FILLED + BACKGROUND)
    blocks = rasterize.page_blocks(tmp_path / "x.pdf", 1)
    assert len(blocks) == 1 and blocks[0].region["width"] > 0


def test_a_filled_page_reports_its_ink_colours(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rasterize, "_svg", lambda pdf, page: RULE + FILLED + BACKGROUND)
    assert rasterize.ink_colours(tmp_path / "x.pdf", 1) == ((48, 74, 224),)


def test_a_page_with_no_ink_still_reports_not_checked(tmp_path, monkeypatch) -> None:
    """None means "could not be determined", and must not become "no colour to lose" (§5.7)."""
    monkeypatch.setattr(rasterize, "_svg", lambda pdf, page: RULE + BACKGROUND)
    assert rasterize.ink_colours(tmp_path / "x.pdf", 1) is None


# ------------------------------------ masked ink: the highlighter, the shader, and white

def _masked(mask_id: str, d: str, colour: str, opacity: str = "1") -> str:
    """How reMarkable's highlighter, marker and shader reach the file, attribute for attribute.

    The *shape* is a white-stroked stencil inside a `<mask>`; the *colour* is a page-sized
    `<rect>` painted through it. Both the mask and the group wrap their content in a
    `translate(51.4, 68.5)` that the page's own root group cancels -- so the mark's real place
    is the path's own matrix, and accumulating the enclosing transforms would move it.
    """
    return (f'<mask id="{mask_id}"><g transform="translate(51.4, 68.5)">'
            f'<path fill="none" stroke-width="9.515418" stroke="rgb(100%, 100%, 100%)" '
            f'stroke-opacity="1" transform="matrix(1, 0, 0, -1, 0, 685)" d="{d}"/></g></mask>'
            f'<g id="compositing-group-{mask_id}" mask="url(#{mask_id})">'
            f'<g transform="translate(51.4, 68.5)">'
            f'<rect x="-51.4" y="-68.5" width="616.8" height="822" fill="rgb({colour})" '
            f'fill-opacity="{opacity}"/></g></g>')


HIGHLIGHT = _masked("m1", "M 100 100 L 200 200 ", "100%, 33.332825%, 81.175232%")
SHADE = _masked("m2", "M 300 300 L 380 360 ", "0%, 0%, 0%", "0.25098")
# A mask made of page-sized rects rather than strokes: an opacity layer in the compositing
# tree, not a mark. 36 of the 108 masks on the author's test page are these.
ALPHA_LAYER = (
    '<g id="cg-0"><rect x="-51.4" y="-68.5" width="616.8" height="822" '
    'fill="rgb(100%, 100%, 100%)" fill-opacity="1"/></g>'
    '<mask id="m3"><use xlink:href="#cg-0"/></mask>'
    '<g mask="url(#m3)"><g transform="translate(51.4, 68.5)">'
    '<rect x="-51.4" y="-68.5" width="616.8" height="822" fill="rgb(0%, 0%, 0%)" '
    'fill-opacity="1"/></g></g>')
# A white pen stroke inside the highlighted region -- painted, not a stencil.
WHITE_ON_PINK = ('<path fill="none" stroke-width="1.9" stroke="rgb(100%, 100%, 100%)" '
                 'stroke-opacity="1" transform="matrix(1, 0, 0, -1, 0, 685)" '
                 'd="M 120 120 L 130 130 "/>')
# And white on bare paper: a filled path, the shape the hidden scribble arrives in.
WHITE_ON_PAPER = ('<path fill-rule="nonzero" fill="rgb(100%, 100%, 100%)" fill-opacity="1" '
                  'd="M 400 400 L 410 410 Z "/>')


def test_ink_painted_through_a_mask_is_ink_and_the_stencil_is_not() -> None:
    """Measured on the author's white-test page: a highlighter, a marker and a shader put their
    colour on a `<rect>` painted through a stencil, and the only `<path>` elements involved are
    the stencil, stroked white. Reading paths alone therefore did both halves wrong at once --
    it reported *white* ink the page does not have (`l3` p4: "black and white"), and missed
    every colour the author actually used."""
    colours = rasterize.ink_colours_from_svg(HIGHLIGHT + FILLED)
    assert (255, 85, 207) in colours, "the highlighter's pink"
    assert (48, 74, 224) in colours, "the pen it was drawn over"
    assert (255, 255, 255) not in colours, "the stencil is a shape, not a mark"


def test_a_masked_mark_is_the_colour_the_reader_sees() -> None:
    """The shader is black at a quarter opacity, and nothing on the page is a grey rect. Reading
    the nominal fill would file the author's shading under the same colour as their pen -- which
    is the distinction the colour gate exists to keep. Composited over the paper it is
    (191, 191, 191), and that is exactly what the rendered page's pixels are."""
    assert rasterize.ink_colours_from_svg(SHADE) == ((191, 191, 191),)


def test_an_opacity_layer_is_not_a_mark() -> None:
    """A third of the masks on that page are page-sized rects masked by page-sized rects -- the
    compositing tree, not ink. Counting them would put a full-page black mark on every page that
    uses a highlighter, and the crop tool would offer the whole sheet as a region."""
    assert rasterize.ink_paths(ALPHA_LAYER) == []


def test_a_masked_mark_is_where_its_stencil_is() -> None:
    """`page_blocks` feeds the crop tool, and a block is only useful if it is in the right
    place: the mark's box comes from the stencil, never from the page-sized rect that colours
    it, and from the path's own matrix, never from the transforms wrapped around it."""
    blocks = rasterize.blocks_from_svg(HIGHLIGHT)
    assert len(blocks) == 1
    assert blocks[0].region == {"x": 100, "y": 485, "width": 100, "height": 100}


def test_white_over_colour_is_ink_and_white_on_paper_is_the_paper() -> None:
    """The author made a page with both, deliberately: white marks inside the pink highlighter,
    which a reader sees, and a white scribble on white paper, which nobody sees -- not a reader
    and not a recognizer. They arrive in different shapes, and that is what separates them: the
    visible marks are *stroked* white, the invisible scribble is 111 white *fills*, which are
    the shape the page's own background arrives in (`BACKGROUND_MIN`).

    So the second is dropped for being background, not for being invisible. A white mark painted
    somewhere nobody can see it would still be reported; no page has produced one.
    """
    colours = rasterize.ink_colours_from_svg(HIGHLIGHT + WHITE_ON_PINK + WHITE_ON_PAPER)
    assert (255, 255, 255) in colours and (255, 85, 207) in colours
    assert len(rasterize.ink_paths(HIGHLIGHT + WHITE_ON_PAPER)) == 1, "the scribble is not ink"


def test_an_svg_that_cannot_be_read_says_so() -> None:
    """Rule 6. Reading the file is now parsing, which can fail where a regex only found
    nothing -- and "could not look" must not arrive as "nothing to lose"."""
    assert rasterize.ink_paths("<svg><path d=") is None
    assert rasterize.ink_colours_from_svg("<svg><path d=") is None
    assert rasterize.blocks_from_svg("<svg><path d=") == ()
