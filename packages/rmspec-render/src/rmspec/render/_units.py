"""Unit arithmetic, with exactly one expression per conversion.

Three units meet in this package and the differential oracle is sensitive to all three:

- **screen units** -- what a ``.rm`` scene file stores, and what ``TextBlock`` positions and
  ``TextStyle.size_px`` are given in.
- **PostScript points** -- what the SVG user space is, because ``scale`` is ``72 / dpi``.
- **millimetres** -- what the domain speaks, because ``PhysicalSize`` and
  ``RenderStyle.min_padding_mm`` are physical facts rather than one adapter's coordinate
  system.

Operation order is load-bearing
-------------------------------
``mm * 72.0 / 25.4`` round-trips the legacy 30.0 pt margin back to exactly ``30.0``, while
``mm / 25.4 * 72.0`` yields ``30.000000000000007`` and ``mm * (72.0 / 25.4)`` yields
``30.000000000000004``. The raw float feeds the ``px < -pad_left`` comparisons in
:mod:`rmspec.render._layout` and the ``:.2f`` viewBox, so a one-ulp difference can flip a
printed digit at a rounding boundary. Both conversions are therefore written once, here, and
:func:`mm_to_points` is asserted to round-trip the legacy constant exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rmspec.domain.models import ScreenSpec

__all__ = [
    "MM_PER_INCH",
    "POINTS_PER_INCH",
    "PRINTABLE_TOLERANCE",
    "mm_to_pixels",
    "mm_to_points",
    "pixels_to_mm",
    "points_per_pixel",
    "points_to_mm",
]

POINTS_PER_INCH = 72.0
"""PostScript points in one inch, which is what makes ``scale`` ``72 / dpi``."""

MM_PER_INCH = 25.4
"""Millimetres in one inch."""

PRINTABLE_TOLERANCE = 0.005
"""Half of the smallest difference a two-decimal length can express, in points.

Two lengths closer than this print identically, so comparing them for strict inequality
decides on bits nobody can see. It matters because an underlay's size arrives in millimetres
and comes back through :func:`mm_to_points`, and that round trip is inexact for 39,889 of
200,000 sampled point values in 1..3000 -- one ulp, which is enough to make ``>`` fire. Both
places that compare a converted length against the page box use this: :mod:`rmspec.render._svg`
for the ``UNDERLAY_RESCALED`` notice and :mod:`rmspec.render._layout` for the pad it grows.
It lives here rather than in either of them because those two modules must agree, and because
neither may import the other at run time.
"""


def points_per_pixel(screen: ScreenSpec, /) -> float:
    """Return the scale that turns one screen unit into points.

    The legacy renderer's ``scale``, spelled ``72.0 / screen.dpi`` in that order because every
    coordinate in the oracle SVGs was produced by multiplying against this exact float.

    Parameters
    ----------
    screen
        The screen the page was drawn on.

    Returns
    -------
    float
        Points per screen unit.
    """
    return POINTS_PER_INCH / screen.dpi


def mm_to_points(value: float, /) -> float:
    """Convert millimetres to PostScript points.

    Parameters
    ----------
    value
        A length in millimetres.

    Returns
    -------
    float
        The same length in points. ``30.0 * 25.4 / 72.0`` converts back to exactly ``30.0``.
    """
    return value * POINTS_PER_INCH / MM_PER_INCH


def points_to_mm(value: float, /) -> float:
    """Convert PostScript points to millimetres.

    Parameters
    ----------
    value
        A length in points.

    Returns
    -------
    float
        The same length in millimetres.
    """
    return value * MM_PER_INCH / POINTS_PER_INCH


def mm_to_pixels(value: float, /, *, screen: ScreenSpec) -> float:
    """Convert millimetres to **screen units**, the coordinate space a ``.rm`` scene stores.

    The third edge of the triangle, and the one no oracle constrains: nothing the legacy
    renderer wrote ever went from millimetres *into* screen units, because it only ever read a
    scene and only ever emitted points. Text-to-ink goes the other way -- a caller places a
    reply on a page it measures in millimetres and the strokes have to come out in the units
    :class:`~rmspec.domain.models.Point` speaks -- so this is where that direction lives, next
    to the other two rather than spelled out at whichever call site needed it first.

    Written ``value * dpi / MM_PER_INCH`` in the same operation order as :func:`mm_to_points`,
    for the same reason: one spelling per conversion, so two call sites cannot disagree by an
    ulp.

    Parameters
    ----------
    value
        A length in millimetres.
    screen
        The screen whose pixel density defines the scene's unit.

    Returns
    -------
    float
        The same length in screen units.
    """
    return value * screen.dpi / MM_PER_INCH


def pixels_to_mm(value: float, /, *, screen: ScreenSpec) -> float:
    """Convert **screen units** to millimetres.

    The inverse of :func:`mm_to_pixels`, and what turns a synthesised stroke's extent back
    into a number the caller can compare against the page it is placing ink on.

    Parameters
    ----------
    value
        A length in screen units.
    screen
        The screen whose pixel density defines the scene's unit.

    Returns
    -------
    float
        The same length in millimetres.
    """
    return value * MM_PER_INCH / screen.dpi
