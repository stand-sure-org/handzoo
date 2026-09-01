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


_SVG_PATH_EL = re.compile(r"<path\s+([^>]*?)/>", re.S)
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
    _require(VECTOR_TOOL)
    proc = subprocess.run(
        [VECTOR_TOOL, "-svg", "-f", str(page), "-l", str(page), str(pdf), "/dev/stdout"],
        capture_output=True, text=True, check=False)

    counts: dict[tuple[int, int, int], int] = {}
    for attrs in _SVG_PATH_EL.findall(proc.stdout):
        colour = re.search(r'stroke="rgb\(([^)]*)\)"', attrs)
        scale = re.search(r'transform="matrix\(([0-9.eE-]+)', attrs)
        data = re.search(r'\sd="([^"]*)"', attrs)
        if not (colour and scale and data):
            continue
        coords = [float(x) for x in _SVG_NUM.findall(data.group(1))]
        xs, ys = coords[0::2], coords[1::2]
        if len(xs) < 2:
            continue
        s = float(scale.group(1))
        if (max(xs) - min(xs)) * s > RULE_MIN_WIDTH and (max(ys) - min(ys)) * s < RULE_MAX_HEIGHT:
            continue  # ruled guide line, not ink
        rgb = tuple(round(float(v.strip().rstrip("%")) * 255 / 100)
                    for v in colour.group(1).split(","))
        if len(rgb) == 3:
            counts[rgb] = counts.get(rgb, 0) + 1

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
    _require(VECTOR_TOOL)
    proc = subprocess.run(
        [VECTOR_TOOL, "-svg", "-f", str(page), "-l", str(page), str(pdf), "/dev/stdout"],
        capture_output=True, text=True, check=False)

    found: list[tuple[float, float, float, float]] = []
    for attrs in _SVG_PATH_EL.findall(proc.stdout):
        data = re.search(r'\sd="([^"]*)"', attrs)
        matrix = _SVG_MATRIX.search(attrs)
        if not (data and matrix):
            continue
        v = [float(n) for n in _SVG_NUM.findall(matrix.group(1))]
        if len(v) < 6:
            continue
        a, b, c, d, e, f = v[:6]
        co = [float(n) for n in _SVG_NUM.findall(data.group(1))]
        pts = [(a * x + c * y + e, b * x + d * y + f) for x, y in zip(co[0::2], co[1::2])]
        if len(pts) < 2:
            continue
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        if x1 - x0 > RULE_MIN_WIDTH and y1 - y0 < RULE_MAX_HEIGHT:
            continue  # ruled guide line, not ink
        found.append((x0, y0, x1, y1))

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
