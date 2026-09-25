"""PDF → page images, and PDF → vector crops.

reMarkable exports are **vector**: no embedded rasters, no fonts, several hundred paths per
page. That has two consequences the design leans on.

First, the PNG we hand a recognizer is a lossy derivative *we* generate, so the DPI is ours to
choose and can be raised for a page that reads badly.

Second — and this is why `crop_vector` exists — a diagram we keep as a drawing is often the
*finished artifact*, not a placeholder awaiting redrawing. 150 DPI is adequate for a model and
poor for a printed figure. Crops therefore come from the source, never resampled from the
recognition raster.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

RASTERIZER = "pdftoppm"
VECTOR_TOOL = "pdftocairo"
DEFAULT_DPI = 150
"""Adequate for recognition. Emission uses `crop_vector`, which has no DPI at all."""


class RasterizeError(RuntimeError):
    """Rasterisation failed. Never returns an empty page list as if it had succeeded."""


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    """1-indexed, matching the PDF's own numbering so a failure can be quoted back."""
    image: Path


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise RasterizeError(f"{tool} not found on PATH (install poppler)")


def page_count(pdf: Path) -> int:
    _require("pdfinfo")
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True,
                         check=False).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise RasterizeError(f"could not read a page count from {pdf}")


def rasterize(pdf: Path, out_dir: Path, *, first: int = 1, last: int | None = None,
              dpi: int = DEFAULT_DPI) -> list[Page]:
    """Render pages to PNG.

    `first`/`last` exist so a run can be triaged before committing to a whole document —
    recognition is the expensive step and a bad prompt should be discovered on five pages,
    not on a hundred.
    """
    _require(RASTERIZER)
    out_dir.mkdir(parents=True, exist_ok=True)
    last = last or page_count(pdf)
    if first < 1 or last < first:
        raise RasterizeError(f"bad page range {first}..{last}")

    pages: list[Page] = []
    for n in range(first, last + 1):
        stem = out_dir / f"p-{n:04d}"
        proc = subprocess.run(
            [RASTERIZER, "-png", "-r", str(dpi), "-f", str(n), "-l", str(n),
             str(pdf), str(stem)],
            capture_output=True, text=True, check=False)
        # pdftoppm appends its own page suffix, whose width varies with the page count.
        produced = sorted(out_dir.glob(f"p-{n:04d}*.png"))
        if proc.returncode != 0 or not produced:
            raise RasterizeError(
                f"{RASTERIZER} produced no image for page {n}: {proc.stderr.strip()[:200]}")
        pages.append(Page(number=n, image=produced[0]))
    return pages


def crop_vector(pdf: Path, page: int, out: Path, *, x: int, y: int, width: int,
                height: int) -> Path:
    """Extract a region of a page as **vector** PDF, for `\\includegraphics`.

    Coordinates are in points (`-r 72`), matching the PDF's own coordinate space rather than
    whatever DPI recognition happened to use. Measured: a cropped diagram retained 133 vector
    paths at 14 KB and dropped straight into LaTeX.
    """
    _require(VECTOR_TOOL)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Unlike pdftoppm, pdftocairo -pdf takes the output FILENAME, not a stem.
    proc = subprocess.run(
        [VECTOR_TOOL, "-pdf", "-f", str(page), "-l", str(page),
         "-x", str(x), "-y", str(y), "-W", str(width), "-H", str(height), "-r", "72",
         str(pdf), str(out)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.exists():
        raise RasterizeError(
            f"{VECTOR_TOOL} produced no crop for page {page}: {proc.stderr.strip()[:200]}")
    _tighten(out)
    return out


TIGHTEN_TOOL = "pdfcrop"
TIGHTEN_MARGIN = 4
"""A little air around the ink. A box drawn exactly on the bounding path clips its own stroke."""


def _tighten(pdf: Path) -> None:
    """Shrink the page box to the ink, in place.

    `pdftocairo -pdf -x -y -W -H` clips the *content* and leaves the page box at full size.
    Measured: asking for 240x190pt of a 514x685 page produced a 514x685 PDF with the diagram in
    one corner, so `\\includegraphics` would import a mostly-blank page -- a crop that is
    technically correct and visually useless.

    Best-effort. `pdfcrop` ships with the same TeX distribution as the hardcoded `pdflatex`, so
    it is normally there; where it is not, the untightened crop is still a valid PDF and still
    contains the right ink. Degrading beats failing, but the caller should know the difference,
    which is why the page size is checkable via `page_size`.
    """
    if shutil.which(TIGHTEN_TOOL) is None:
        return
    tmp = pdf.with_suffix(".tight.pdf")
    proc = subprocess.run([TIGHTEN_TOOL, "--margins", str(TIGHTEN_MARGIN), str(pdf), str(tmp)],
                          capture_output=True, text=True, check=False)
    if proc.returncode == 0 and tmp.exists():
        tmp.replace(pdf)
    elif tmp.exists():
        tmp.unlink()


def embedded_images(pdf: Path, page: int) -> int:
    """How many raster images are pasted onto this page.

    **The one thing nothing else on a page can see.** Measured on Leinster 1.1: pages 14, 15,
    17 and 18 carry screenshots of the book's printed exercises above the author's handwritten
    answers, and p14 passed every gate while emitting Leinster's text verbatim.

    Each existing mechanism is blind to it for a different reason:

    - `ink_colours` reads *stroke* colour, and a raster has none — so the black-print-against-
      coloured-pen discriminator that worked on the previous corpus reports one colour here;
    - `page_blocks` groups vector paths, so no band is offered over the pasted region and the
      crop tool cannot reach it;
    - there is no text layer, so nothing downstream knows the pixels are words.

    Raises rather than returning 0 when it cannot look. "No pasted image" and "could not
    check" are different answers and must not share one (DESIGN 5.7).
    """
    _require("pdfimages")
    proc = subprocess.run(["pdfimages", "-list", "-f", str(page), "-l", str(page), str(pdf)],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RasterizeError(
            f"pdfimages could not read page {page} of {pdf}: {proc.stderr.strip()[:200]}")
    # Header is two lines; a `smask` row is the transparency channel of the row above it, not
    # a second pasted picture, so counting it would double every figure.
    return sum(1 for line in proc.stdout.splitlines()[2:]
               if line.split()[2:3] == ["image"])


def page_size(pdf: Path, page: int = 1) -> tuple[float, float]:
    """Dimensions of one page, in points, from the PDF itself.

    **Pages in one export are not all the same size.** The author writes at different
    magnifications on the reMarkable and the device bakes that into the export geometry:
    ch22 is 514x685 pt on most pages, 514x1238 on p16 and 514x773 on p26.

    `pdfinfo` without a page range reports only the *first* page, and an earlier version of
    this took no page argument at all — so a crop on p16 converted its fractions against 685
    and landed 1.8x off. The author noticed it in the output and guessed the cause exactly.
    """
    _require(RASTERIZER)
    proc = subprocess.run(["pdfinfo", "-f", str(page), "-l", str(page), str(pdf)],
                          capture_output=True, text=True, check=False)
    # `-f/-l` prints "Page N size: W x H pts"; the plain "Page size:" line is the document
    # default and is what made this wrong in the first place, so it is only the fallback.
    m = (re.search(rf"Page\s+{page}\s+size:\s+([0-9.]+) x ([0-9.]+)", proc.stdout)
         or re.search(r"Page size:\s+([0-9.]+) x ([0-9.]+)", proc.stdout))
    if not m:
        raise RasterizeError(f"could not read a page size from {pdf}")
    return float(m.group(1)), float(m.group(2))


@dataclass(frozen=True, slots=True)
class InkProfile:
    """Where the ink is on a page, measured from the vector source.

    This is the only signal in the pipeline that no vision model produced. Everything else —
    the transcription, the inventory, any confidence a model reports — comes from the same
    family of system and can be wrong in correlated ways. Ink cannot.
    """

    points: int
    """Total path coordinates. A proxy for "how much is on this page"."""
    bands: tuple[float, ...]
    """Fraction of ink in each equal vertical band, top to bottom."""

    @property
    def is_blank(self) -> bool:
        return self.points < 20

    def occupied_bands(self, threshold: float = 0.01) -> tuple[int, ...]:
        return tuple(i for i, f in enumerate(self.bands) if f >= threshold)


_SVG_POINT = re.compile(r"[ML]\s*[-\d.]+\s+([-\d.]+)")


def ink_profile(pdf: Path, page: int, *, bands: int = 10) -> InkProfile:
    """Measure ink distribution down a page, without rendering or reading a model.

    reMarkable exports carry no embedded rasters and no fonts, so every path coordinate in the
    SVG is ink the author made.
    """
    _require(VECTOR_TOOL)
    proc = subprocess.run(
        [VECTOR_TOOL, "-svg", "-f", str(page), "-l", str(page), str(pdf), "/dev/stdout"],
        capture_output=True, text=True, check=False)
    ys = [float(m) for m in _SVG_POINT.findall(proc.stdout)]
    if not ys:
        return InkProfile(points=0, bands=tuple([0.0] * bands))

    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1.0
    counts = [0] * bands
    for y in ys:
        counts[min(bands - 1, int((y - lo) / span * bands))] += 1
    total = len(ys)
    return InkProfile(points=total, bands=tuple(c / total for c in counts))


_SVG_NUM = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")

RULE_MIN_WIDTH = 300.0
RULE_MAX_HEIGHT = 1.0
"""A ruled guide line runs the width of the page and is flat.

Guides are separated from ink **by geometry, never by hue.** The first version of this test
keyed off "grey and uniform", which held on the pages it was written against and fails on
`Cheng 217-220` p3, where 17 paths of deliberate grey ink — the base diagram's own arrows,
drawn against green for the cone's legs — would have been discarded as furniture. Measured
there: the rules are 685.1pt wide and 0.0 tall; the grey ink is 6.5 x 8.2.
"""


BACKGROUND_MIN = 231
"""A fill this pale is the page, not ink. Counting it would make every page two-coloured."""


@dataclass(frozen=True, slots=True)
class InkPath:
    """One mark the author made: its colour, and the box it occupies in page points."""

    colour: tuple[int, int, int] | None
    box: tuple[float, float, float, float]
    filled: bool


def _svg(pdf: Path, page: int) -> str:
    _require(VECTOR_TOOL)
    proc = subprocess.run(
        [VECTOR_TOOL, "-svg", "-f", str(page), "-l", str(page), str(pdf), "/dev/stdout"],
        capture_output=True, text=True, check=False)
    return proc.stdout


_SVG_RGB = re.compile(r"rgb\(([^)]*)\)")


def _rgb(value: str) -> tuple[int, int, int] | None:
    parts = [v.strip().rstrip("%") for v in value.split(",")]
    if len(parts) != 3:
        return None
    return tuple(round(float(v) * 255 / 100) for v in parts)  # type: ignore[return-value]


_SVG_IMAGE_DEF = re.compile(r'<image\b([^>]*)>')
_SVG_USE = re.compile(r'<use\b([^>]*)>')


def pasted_regions_from_svg(svg: str) -> tuple[dict[str, float], ...]:
    """Where each pasted raster sits on the page, in points.

    reMarkable's capture tool (2026-09) pastes a region of the page's *background* -- typeset
    text included -- as a raster. `embedded_images` counts them; this says where they are, which
    is what lets the author keep a capture as a picture rather than have it transcribed: the
    region is the crop, with nothing guessed (DESIGN 7.2's rule, applied to a region the file
    already knows).

    The same image is referenced twice, once as its own transparency mask, so identical
    placements collapse to one region.
    """
    sizes: dict[str, tuple[float, float]] = {}
    for attrs in _SVG_IMAGE_DEF.findall(svg):
        ident = re.search(r'id="([^"]*)"', attrs)
        w = re.search(r'width="([0-9.]+)"', attrs)
        h = re.search(r'height="([0-9.]+)"', attrs)
        if ident and w and h:
            sizes[ident.group(1)] = (float(w.group(1)), float(h.group(1)))
    seen: dict[tuple, dict[str, float]] = {}
    for attrs in _SVG_USE.findall(svg):
        href = re.search(r'xlink:href="#([^"]*)"', attrs)
        matrix = _SVG_MATRIX.search(attrs)
        if not (href and matrix and href.group(1) in sizes):
            continue
        v = [float(n) for n in _SVG_NUM.findall(matrix.group(1))]
        if len(v) < 6:
            continue
        a, _, _, d, e, f = v[:6]
        w, h = sizes[href.group(1)]
        key = (round(e, 3), round(f, 3), round(w * a, 3), round(h * d, 3))
        seen.setdefault(key, {"x": e, "y": f, "width": w * a, "height": h * d})
    return tuple(seen.values())


def pasted_regions(pdf: Path, page: int) -> tuple[dict[str, float], ...]:
    """Where each pasted raster sits on `page`, in points. See `pasted_regions_from_svg`."""
    return pasted_regions_from_svg(_svg(pdf, page))


def _parse(svg: str) -> ET.Element | None:
    """The SVG as a tree, or None when it cannot be read.

    Tests and callers hand this fragments as often as whole documents, so a fragment is wrapped
    before it is given up on. `None` is "could not look" and never "found nothing" (5.7).
    """
    wrapped = ('<svg xmlns="http://www.w3.org/2000/svg" '
               f'xmlns:xlink="http://www.w3.org/1999/xlink">{svg}</svg>')
    for text in (svg, wrapped):
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            continue
    return None


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


HREF = "{http://www.w3.org/1999/xlink}href"
_MATRIX = re.compile(r"matrix\(([^)]*)\)")


def _transform(attrs: dict) -> tuple[float, float, float, float, float, float] | None:
    """The path's **own** matrix, and deliberately not the transforms wrapped around it.

    Measured on the author's white-test page: a stencil sits inside `<g translate(51.4, 68.5)>`
    and the root group that draws it carries `translate(-51.4, -68.5)`, so the two cancel.
    Accumulating ancestors would move every masked mark 51pt across the page and 68pt down --
    and a block in the wrong place is worse than no block, because the crop tool acts on it.
    """
    matrix = _MATRIX.search(attrs.get("transform", ""))
    if not matrix:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    v = [float(n) for n in _SVG_NUM.findall(matrix.group(1))]
    return tuple(v[:6]) if len(v) >= 6 else None  # type: ignore[return-value]


def _box(data: str, attrs: dict) -> tuple[float, float, float, float] | None:
    m = _transform(attrs)
    if m is None:
        return None
    a, b, c, d, e, f = m
    co = [float(n) for n in _SVG_NUM.findall(data)]
    pts = [(a * x + c * y + e, b * x + d * y + f) for x, y in zip(co[0::2], co[1::2])]
    if len(pts) < 2:
        return None
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    if x1 - x0 > RULE_MIN_WIDTH and y1 - y0 < RULE_MAX_HEIGHT:
        return None                                    # ruled guide line, not ink
    return (x0, y0, x1, y1)


def _over_paper(colour: tuple[int, int, int], opacity: float) -> tuple[int, int, int]:
    """What the reader sees: the fill composited over the white page.

    The shader is black at `fill-opacity="0.25098"` and nothing on the page is a grey rect.
    Reporting the nominal black would file the author's shading under the same colour as their
    pen, which is the distinction the colour gate exists to keep. Verified against the rendered
    pixels: 0.251 black gives (191, 191, 191) and 0.251 of (30, 26, 26) gives (199, 198, 198),
    which are the two greys the raster actually contains.
    """
    return tuple(round(c * opacity + 255 * (1 - opacity)) for c in colour)  # type: ignore


def _painted(element: ET.Element) -> InkPath | None:
    """A mark drawn directly: colour from its own stroke or fill, box from its own coordinates."""
    attrs = element.attrib
    data = attrs.get("d")
    if not data:
        return None
    stroke = _SVG_RGB.match(attrs.get("stroke", ""))
    fill = _SVG_RGB.match(attrs.get("fill", ""))
    colour = _rgb(stroke.group(1)) if stroke else (_rgb(fill.group(1)) if fill else None)
    if colour is None:
        return None
    if fill and not stroke and min(colour) >= BACKGROUND_MIN:
        return None                                    # the page, not a mark on it
    box = _box(data, attrs)
    return None if box is None else InkPath(colour=colour, box=box,
                                            filled=bool(fill and not stroke))


def _stencil(mask: ET.Element, index: dict[str, ET.Element], depth: int = 0) -> list[ET.Element]:
    """The paths that give a mask its shape, following `<use>` into the rest of the file."""
    found: list[ET.Element] = []
    if depth > 4:
        return found
    for element in mask.iter():
        if _tag(element) == "path" and element.get("d"):
            found.append(element)
        elif _tag(element) == "use":
            target = index.get((element.get(HREF) or "").lstrip("#"))
            if target is not None:
                found += _stencil(target, index, depth + 1)
    return found


def _through_mask(group: ET.Element, index: dict[str, ET.Element]) -> list[InkPath]:
    """The marks a masked compositing group paints: its colour, in its stencil's shape.

    A mask whose shape is page-sized rects rather than strokes is an opacity layer in the
    compositing tree -- a third of the masks on the author's test page are -- and has no marks
    in it. Its geometry never comes from the rect, which is always the whole page.
    """
    name = re.search(r"url\(#([^)]*)\)", group.get("mask") or "")
    mask = index.get(name.group(1)) if name else None
    if mask is None:
        return []
    stencil = _stencil(mask, index)
    if not stencil:
        return []
    colour = None
    for element in group.iter():
        if _tag(element) not in ("rect", "path"):
            continue
        fill = _SVG_RGB.match(element.get("fill", ""))
        opacity = float(element.get("fill-opacity", "1") or 1)
        if fill and opacity > 0 and (rgb := _rgb(fill.group(1))) is not None:
            colour = _over_paper(rgb, opacity)
            break
    if colour is None or min(colour) >= BACKGROUND_MIN:
        return []
    boxes = [_box(p.get("d", ""), p.attrib) for p in stencil]
    return [InkPath(colour=colour, box=box, filled=True) for box in boxes if box is not None]


def ink_paths(svg: str) -> list[InkPath] | None:
    """Every mark in a `pdftocairo` SVG, with its colour and its box. None if it cannot be read.

    **Three shapes of mark, all ink** -- measured across one 642-page notebook and the author's
    white-test page:

    - *stroked*, carrying a `transform="matrix(...)"`, the colour in `stroke=`;
    - *filled*, carrying plain coordinates and **no matrix at all**, the colour in `fill=`; and
    - *masked*, where the shape is a white-stroked stencil inside a `<mask>` and the colour is a
      page-sized `<rect>` painted through it. This is what the highlighter, the marker and the
      shader all export as.

    Reading only the first made two readers blind on 367 of those 642 pages. Reading only paths
    got the third wrong in both directions at once: the stencil's white was reported as ink the
    page does not have (`l3` p4: "black and white"), and the colour the author actually used was
    never seen at all.

    Guide lines are separated from ink **by geometry, never by hue** (`RULE_MIN_WIDTH`), and the
    page's own near-white background is not ink.
    """
    root = _parse(svg)
    if root is None:
        return None
    index = {e.get("id"): e for e in root.iter() if e.get("id")}
    stencils = {id(p) for e in root.iter() if _tag(e) == "mask" for p in e.iter()}
    out: list[InkPath] = []
    for element in root.iter():
        if _tag(element) == "path" and id(element) not in stencils:
            if (mark := _painted(element)) is not None:
                out.append(mark)
        elif _tag(element) == "g" and element.get("mask"):
            out += _through_mask(element, index)
    return out


def ink_colours(pdf: Path, page: int) -> tuple[tuple[int, int, int], ...] | None:
    """Distinct ink colours on a page, read from the vector source.

    This is *file ground truth*, not a model report — which is what makes it usable as gate
    evidence without touching the §3 trust boundary. Naming a colour is a description and
    descriptions are untrustworthy; reading one off a path is not.

    Returns:
        Distinct RGB triples, most-used first. **`None` when it cannot be determined** — a
        raster source (a scan) has no vector paths, and neither does a blank page. Returning
        an empty tuple for those would read as "no colour to lose", which on a scan is exactly
        the wrong answer: it is where colour is hardest to recover, not where there is none.
    """
    return ink_colours_from_svg(_svg(pdf, page))


def ink_colours_from_svg(svg: str) -> tuple[tuple[int, int, int], ...] | None:
    """Distinct ink colours in one SVG, most-used first. See `ink_colours`."""
    paths = ink_paths(svg)
    if paths is None:
        return None
    counts: dict[tuple[int, int, int], int] = {}
    for path in paths:
        counts[path.colour] = counts.get(path.colour, 0) + 1
    if not counts:
        return None
    return tuple(sorted(counts, key=lambda k: -counts[k]))


@dataclass(frozen=True, slots=True)
class Block:
    """A candidate region: ink separated from its neighbours by whitespace.

    Coordinates are in **points, top-left origin** — the same space `crop_vector` takes, so a
    block can be handed straight to it. Getting that wrong is silent: page 3's blocks landed at
    y=694..1307 on a 685pt page when only the transform's scale factor was applied instead of
    the full affine matrix, and every proposal would have cropped the wrong thing.
    """

    x: float
    y: float
    width: float
    height: float
    paths: int
    """How many ink strokes fell in this block. A rough sense of how much is there."""

    @property
    def region(self) -> dict[str, int]:
        """Rounded out to whole points, for `crop_vector(**block.region)`."""
        return {"x": int(self.x), "y": int(self.y),
                "width": int(self.width + 0.5), "height": int(self.height + 0.5)}


BLOCK_GAP = 10.0
"""Vertical whitespace, in points, that separates one block from the next."""

_SVG_MATRIX = re.compile(r'transform="matrix\(([^)]*)\)"')


def page_blocks(pdf: Path, page: int, *, gap: float = BLOCK_GAP) -> tuple[Block, ...]:
    """Ink grouped into horizontal bands, as candidate crop regions.

    This is an **assist, not evidence**, and the distinction matters. An empty result says "no
    suggestions" — the human can still type coordinates — where `ink_colours` returning `None`
    is a claim deliberately withheld from a gate. Nothing downstream may treat an empty block
    list as a statement about the page.

    Bands rather than boxes because handwriting runs in lines: a diagram sitting between two
    paragraphs is separated vertically, and column detection would be guessing.
    """
    return blocks_from_svg(_svg(pdf, page), gap=gap)


def blocks_from_svg(svg: str, *, gap: float = BLOCK_GAP) -> tuple[Block, ...]:
    """Ink in one SVG, grouped into bands. See `page_blocks`."""
    paths = ink_paths(svg)
    found = [p.box for p in (paths or ())]

    if not found:
        return ()

    rows = sorted(found, key=lambda r: r[1])
    groups: list[list[tuple[float, float, float, float]]] = [[rows[0]]]
    for r in rows[1:]:
        if r[1] - max(g[3] for g in groups[-1]) > gap:
            groups.append([r])
        else:
            groups[-1].append(r)

    return tuple(
        Block(x=min(g[0] for g in grp), y=min(g[1] for g in grp),
              width=max(g[2] for g in grp) - min(g[0] for g in grp),
              height=max(g[3] for g in grp) - min(g[1] for g in grp),
              paths=len(grp))
        for grp in groups)
