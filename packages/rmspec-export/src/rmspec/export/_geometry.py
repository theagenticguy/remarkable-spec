"""Millimetre/point arithmetic for the export slice's boundary. No third-party imports.

The domain speaks millimetres and pixels and refuses PostScript points, because points are
a PDF/PostScript unit and a domain that speaks them has adopted one adapter's coordinate
system. Points nonetheless exist: a PDF page box is stored in them, and ``fitz`` reports
``page.rect`` in them. This module is the single place the two units meet, so the
conversion factor appears once instead of once per adapter -- which is how the legacy code
ended up with ``dpi / 72`` in the PNG path and ``72 / screen.dpi`` in the PDF path,
disagreeing about what "DPI" meant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rmspec.domain.ports.export import PAGE_SIZE_TOLERANCE_MM, PhysicalSize

if TYPE_CHECKING:
    from rmspec.domain.ports.export import PixelSize

__all__ = [
    "MM_PER_INCH",
    "POINTS_PER_INCH",
    "millimetres_from_points",
    "physical_size_from_points",
    "points_from_millimetres",
    "scale_matrix_for",
    "sizes_agree",
]

MM_PER_INCH = 25.4
"""Millimetres in one inch. The definition, not an approximation."""

POINTS_PER_INCH = 72.0
"""PostScript points in one inch."""


def points_from_millimetres(millimetres: float) -> float:
    """Convert millimetres to PostScript points.

    Parameters
    ----------
    millimetres
        A length in millimetres.

    Returns
    -------
    float
        The same length in points.
    """
    return millimetres * POINTS_PER_INCH / MM_PER_INCH


def millimetres_from_points(points: float) -> float:
    """Convert PostScript points to millimetres.

    Parameters
    ----------
    points
        A length in points.

    Returns
    -------
    float
        The same length in millimetres.
    """
    return points * MM_PER_INCH / POINTS_PER_INCH


def physical_size_from_points(width_pt: float, height_pt: float) -> PhysicalSize:
    """Build a :class:`PhysicalSize` from a PDF page box measured in points.

    Parameters
    ----------
    width_pt
        Page box width in points.
    height_pt
        Page box height in points.

    Returns
    -------
    PhysicalSize
        The same page, in millimetres.

    Raises
    ------
    ValueError
        If either dimension is not positive, refused by :class:`PhysicalSize`.
    """
    return PhysicalSize(
        width_mm=millimetres_from_points(width_pt),
        height_mm=millimetres_from_points(height_pt),
    )


def sizes_agree(
    left: PhysicalSize,
    right: PhysicalSize,
    *,
    tolerance_mm: float = PAGE_SIZE_TOLERANCE_MM,
) -> bool:
    """Report whether two page sizes match within the domain's shared tolerance.

    Parameters
    ----------
    left
        One page size.
    right
        The other page size.
    tolerance_mm
        Millimetres of slack. Defaults to :data:`PAGE_SIZE_TOLERANCE_MM`; a per-adapter
        epsilon would let each adapter pick the tolerance that makes it pass.

    Returns
    -------
    bool
        True when both dimensions agree to within ``tolerance_mm``.
    """
    return (
        abs(left.width_mm - right.width_mm) <= tolerance_mm
        and abs(left.height_mm - right.height_mm) <= tolerance_mm
    )


def scale_matrix_for(target: PixelSize, width_pt: float, height_pt: float) -> tuple[float, float]:
    """Return the per-axis zoom that renders a points-sized page at exactly ``target``.

    Deliberately anisotropic. A single isotropic zoom derived from the same fit scale
    disagrees with :meth:`PixelSize.fit_within` wherever the two roundings straddle a half
    pixel -- measured on this machine, 4 of 16 page/box/oversample combinations, including
    US Letter into a 1620x2160 box at ``oversample=1`` (the domain says 1620x2096, an
    isotropic ``fitz`` zoom produces 1620x2097). Deriving each axis from the target the
    domain already computed removes the disagreement instead of resampling it away, and it
    hit the domain figure in 16 of 16 combinations tested.

    Parameters
    ----------
    target
        The pixel size the raster must have, from :meth:`PixelSize.fit_within`.
    width_pt
        Source page width in points.
    height_pt
        Source page height in points.

    Returns
    -------
    tuple[float, float]
        ``(zoom_x, zoom_y)`` in pixels per point.

    Raises
    ------
    ValueError
        If either source dimension is not positive, which no PDF page box may be.
    """
    if width_pt <= 0 or height_pt <= 0:
        msg = f"page box must be positive in both dimensions, got {width_pt}x{height_pt}"
        raise ValueError(msg)
    return target.width_px / width_pt, target.height_px / height_pt
