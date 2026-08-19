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
    return out


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
