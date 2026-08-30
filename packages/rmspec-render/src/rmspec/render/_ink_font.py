"""A single-stroke (engraving) font: centre-lines a pen can actually draw.

Why this file exists, and why it is not an outline font
------------------------------------------------------
Measured live on firmware 3.27.3.0: xochitl **preserves but does not display** a
``RootTextBlock`` inserted by a foreign author. It read back at the exact position it was
written to, with the foreign author id intact, across the tablet's own re-save -- and it was
never drawn. **Strokes are what it renders.** So a reply a human can see on the tablet has to
be ink, which is the entire reason this module and :mod:`rmspec.render._ink_text` exist. Do not
delete either in favour of writing a text block: that is the simpler thing, and it does not
work.

Given that, there are two ways to turn a character into strokes and they are not close to
equivalent.

**(a) Trace an outline font.** Load a TrueType or OpenType face, pull each glyph's contours,
flatten the quadratic and cubic segments, emit the result as strokes. It is rejected here. A
glyph outline is a *closed contour meant to be filled*, and a stroke cannot fill. Tracing one
therefore draws the **edge** of each letter: an 'o' comes out as two concentric rings and an
'l' as a long thin rectangle -- hollow, double-walled letterforms that no pen has ever made.
Faking the fill means many parallel strokes per glyph, which is slow to compute, enormous in a
``.rm`` file (every point is fourteen bytes on the wire) and still looks like hatching rather
than writing. It also costs a third-party dependency -- ``fontTools`` -- and an
``OWNED_THIRD_PARTY`` entry in ``tests/architecture/test_dependency_direction.py``, which is a
reviewed architectural change, in exchange for a worse picture.

**(b) A single-stroke font**, which is what this module is. Characters are defined as
*centre-lines*: the path the nib travels, the way pen plotters and CNC engravers have always
described type. One to four pen-downs per glyph, exactly what a hand draws, no fill to fake, no
dependency at all -- the data is a dict literal and the arithmetic is :mod:`math`.

Where the glyph data came from, and its licence
----------------------------------------------
The *approach* is the Hershey family's -- Dr. A. V. Hershey's centre-line vector glyphs, the
canonical public-domain engraving set, are the reason "single-stroke font" is a solved idea.
The *data below is not Hershey's and contains none of it.* It is original work, drawn for this
repository against the metrics in the next section, and is covered by the repository's own
LICENSE like every other file here. That was a deliberate choice over embedding a Hershey
subset for two reasons. Provenance: the usual Hershey distribution is the Hurt/Cognition
format, whose notice requires two specific acknowledgements to travel with the font data, and
carrying a third party's attribution obligation inside a committed data table is a thing to do
on purpose or not at all. Verifiability: this machine has no network, so the only way to
"embed Hershey" would have been to type coordinates from memory and *claim* a provenance that
could not be checked -- worse than authoring them, because a subtly corrupted glyph would ship
under someone else's name.

Metrics: the grid every coordinate below is on
----------------------------------------------
Font units, with **y up** -- the opposite of screen units, where y grows downward. The
conversion is one subtraction and it lives in :mod:`rmspec.render._ink_text`, not here.

===================  =====  ==============================================================
Landmark             Units  Meaning
===================  =====  ==============================================================
baseline               0    Where a glyph with no descender sits.
``X_HEIGHT_UNITS``     9    Top of ``x``, ``o``, ``n``.
``CAP_UNITS``         14    Top of ``H``, and of the ascender on ``b``, ``d``, ``l``.
``DESCENDER_UNITS``   -4    Bottom of ``g``, ``p``, ``q``, ``y``, ``j``.
``EM_UNITS``          20    The em: what the caller's requested text size means.
===================  =====  ==============================================================

Ink spans 19.2 of the 20 em units once the taller punctuation is counted -- ``{`` reaches 15.2
and ``_`` sits at -2.6 -- so consecutive baselines one em apart just clear each other and any
``line_height`` above 1.0 leaves air. ``Glyph.advance`` is per glyph, in the same units, and
includes both side bearings -- there is no kerning table, because a monoline hand does not kern.

Arcs are data, not polylines
----------------------------
A bowl is one :class:`Arc` record rather than a dozen hand-typed points. That keeps the table
readable, keeps curves *round* at any size instead of visibly polygonal, and puts the
flattening resolution behind one named constant. :func:`sampled_traces` is the only way out of
this module: it flattens, then **resamples at a uniform arc-length step**, because a real
stylus samples on a clock rather than at the corners a draughtsman happened to type. That is
also what makes the per-point pressure and speed envelopes in
:mod:`rmspec.render._ink_text` mean the same thing on a straight stem as on a curve.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

__all__ = [
    "CAP_UNITS",
    "DESCENDER_UNITS",
    "DOT_RADIUS_UNITS",
    "EM_UNITS",
    "FLATTEN_STEP_UNITS",
    "GLYPHS",
    "MIN_SAMPLES_PER_TRACE",
    "SAMPLE_STEP_UNITS",
    "SUBSTITUTE",
    "X_HEIGHT_UNITS",
    "Arc",
    "Glyph",
    "Line",
    "Polyline",
    "Segment",
    "glyph_for",
    "resample",
    "sampled_traces",
]

type Polyline = tuple[tuple[float, float], ...]
"""A run of ``(x, y)`` pairs in font units, y up. What a flattened pen-down is."""

EM_UNITS = 20.0
"""The em box, in font units. One requested text size maps onto this many units."""

CAP_UNITS = 14.0
"""Cap height and ascender height, in font units."""

X_HEIGHT_UNITS = 9.0
"""x-height, in font units."""

DESCENDER_UNITS = -4.0
"""Lowest point a descender reaches, in font units. Negative: below the baseline."""

FLATTEN_STEP_UNITS = 0.4
"""Arc-length step an :class:`Arc` is flattened at, in font units.

Small enough that no curve reads as faceted. The polygon's shortfall from the true ellipse is
``r * (1 - cos(step / 2r))``, so it grows as the radius *shrinks*: measured over the 32 arcs in
the table it is at most 0.0087 units on the 24 that are letter-sized, and 0.027 on the eight
dot rings, whose radius is 0.65. Both are far below the width of the drawn line -- a reply at a
4 mm em draws a line about 1.7 units wide -- and a test pins them rather than trusting this
paragraph.
"""

SAMPLE_STEP_UNITS = 1.0
"""Longest arc-length spacing between emitted stylus samples, in font units.

This is a *stylus* property, not a curve-fidelity one: it decides how many samples a stroke
carries and therefore how finely the pressure and speed envelopes can vary along it. One unit
is a twentieth of an em, so a 4 mm em on a 229 DPI screen samples every 0.2 mm -- finer than
the drawn line is wide. It is also the knob that trades file size for smoothness: every sample
is fourteen bytes in a ``.rm`` scene, and a full line of text is roughly a thousand of them.
"""

MIN_SAMPLES_PER_TRACE = 6
"""Fewest samples any pen-down is emitted with, however short it is.

Without a floor, :data:`SAMPLE_STEP_UNITS` alone turns the smallest features in the font into
polygons: a dot's ring is barely four units around, so a flat one-unit step draws it as a
diamond. It is invisible at any size a reply is written at -- a dot is narrower than the line
that draws it -- but it is visible on a heading, and the fix costs one division.
"""

DOT_RADIUS_UNITS = 0.65
"""Radius of the tiny closed ring that draws a dot, in font units.

A dot is a ring rather than a zero-length stroke on purpose. SVG renders a zero-length subpath
with a round linecap as a disc, but ``rmspec.render._svg.append_stroke`` needs two *distinct*
samples before it emits anything and the tablet's own engine makes no such promise, so a
degenerate stroke is the one shape that might render on the preview and vanish on the device --
the exact failure mode this whole component exists to avoid. A ring of this radius, drawn with
a line wider than its own diameter, fills in as a solid dot.
"""


@dataclass(frozen=True, slots=True)
class Line:
    """A straight run through two or more points, in font units.

    Attributes
    ----------
    points
        The corners, in the order the nib visits them.
    """

    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Arc:
    """A piece of an axis-aligned ellipse, in font units.

    Angles are the usual mathematical convention in *font* space, so they run
    counter-clockwise from the positive x axis and a point at angle ``t`` is
    ``(cx + rx*cos t, cy + ry*sin t)``. ``end_deg`` below ``start_deg`` sweeps clockwise, and a
    360 degree sweep closes the ellipse.

    Attributes
    ----------
    cx
        Centre, x.
    cy
        Centre, y.
    rx
        Semi-axis along x.
    ry
        Semi-axis along y.
    start_deg
        Angle the nib touches down at, in degrees.
    end_deg
        Angle the nib lifts at, in degrees.
    """

    cx: float
    cy: float
    rx: float
    ry: float
    start_deg: float
    end_deg: float

    @property
    def points(self) -> Polyline:
        """Flatten this arc to a polyline at :data:`FLATTEN_STEP_UNITS`.

        Named ``points`` so that it and :attr:`Line.points` are one attribute, which is what
        lets :func:`sampled_traces` walk a trace with no ``isinstance`` dispatch and therefore
        with no branch that a new segment kind could quietly fall through.

        Returns
        -------
        Polyline
            At least two points, first and last exactly on ``start_deg`` and ``end_deg``.
        """
        sweep = math.radians(self.end_deg - self.start_deg)
        span = abs(sweep) * (self.rx + self.ry) / 2
        steps = max(1, math.ceil(span / FLATTEN_STEP_UNITS))
        start = math.radians(self.start_deg)
        return tuple(
            (
                self.cx + self.rx * math.cos(start + sweep * index / steps),
                self.cy + self.ry * math.sin(start + sweep * index / steps),
            )
            for index in range(steps + 1)
        )


type Segment = Line | Arc
"""One piece of one pen-down. A trace is a tuple of these, drawn without lifting."""


@dataclass(frozen=True, slots=True)
class Glyph:
    """One character's advance width and the pen-downs that draw it.

    Attributes
    ----------
    advance
        How far the pen moves right before the next glyph, in font units. Includes both side
        bearings, so glyphs abut without a kerning table.
    traces
        One tuple of segments per pen-down, in writing order. Empty for the space, which
        advances and draws nothing.
    """

    advance: float
    traces: tuple[tuple[Segment, ...], ...]


def _line(*points: tuple[float, float]) -> Line:
    """Build a straight run, terser than the constructor at 95 call sites.

    Parameters
    ----------
    *points
        The corners, in nib order.

    Returns
    -------
    Line
        The segment.
    """
    return Line(points=points)


def _arc(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    /,
    *,
    start_deg: float,
    end_deg: float,
) -> Arc:
    """Build an elliptical arc, geometry positional and sweep named.

    The split is not stylistic: an arc reads as ``(centre, radii)`` followed by two angles, and
    two adjacent bare floats whose swap silently mirrors a glyph are worth naming.

    Parameters
    ----------
    cx
        Centre, x.
    cy
        Centre, y.
    rx
        Semi-axis along x.
    ry
        Semi-axis along y.
    start_deg
        Touch-down angle in degrees.
    end_deg
        Lift angle in degrees.

    Returns
    -------
    Arc
        The segment.
    """
    return Arc(cx=cx, cy=cy, rx=rx, ry=ry, start_deg=start_deg, end_deg=end_deg)


def _ring(cx: float, cy: float, rx: float, ry: float) -> Arc:
    """Build a closed ellipse, which is what every bowl and every dot is.

    Parameters
    ----------
    cx
        Centre, x.
    cy
        Centre, y.
    rx
        Semi-axis along x.
    ry
        Semi-axis along y.

    Returns
    -------
    Arc
        A full 360 degree sweep.
    """
    return _arc(cx, cy, rx, ry, start_deg=0.0, end_deg=360.0)


def _dot(x: float, y: float) -> tuple[Segment, ...]:
    """Build the one-segment trace that draws a dot.

    Parameters
    ----------
    x
        Centre, x, in font units.
    y
        Centre, y, in font units.

    Returns
    -------
    tuple[Segment, ...]
        A single tiny ring; see :data:`DOT_RADIUS_UNITS` for why it is not a point.
    """
    return (_ring(x, y, DOT_RADIUS_UNITS, DOT_RADIUS_UNITS),)


def _glyph(advance: float, *traces: tuple[Segment, ...]) -> Glyph:
    """Build a glyph, so the table below is one line per character where it can be.

    Parameters
    ----------
    advance
        Advance width in font units.
    *traces
        One tuple of segments per pen-down.

    Returns
    -------
    Glyph
        The glyph.
    """
    return Glyph(advance=advance, traces=traces)


#: Every character this font draws: the 95 printable ASCII code points, U+0020 to U+007E.
#: Anything else is :data:`SUBSTITUTE`. The set is exported as
#: ``rmspec.render.INK_TEXT_CHARACTERS`` so a caller can fold its text down to it *before*
#: writing to a device rather than discovering the gap afterwards.
GLYPHS: dict[str, Glyph] = {
    " ": _glyph(7.0),
    "!": _glyph(5.0, (_line((2.5, 14.0), (2.5, 4.0)),), _dot(2.5, 1.1)),
    '"': _glyph(7.0, (_line((2.0, 14.0), (2.0, 10.4)),), (_line((5.0, 14.0), (5.0, 10.4)),)),
    "#": _glyph(
        11.0,
        (_line((4.2, 14.0), (2.6, 0.0)),),
        (_line((8.8, 14.0), (7.2, 0.0)),),
        (_line((1.6, 9.4), (9.8, 9.4)),),
        (_line((1.0, 4.6), (9.2, 4.6)),),
    ),
    "$": _glyph(
        11.0,
        (_line((5.5, 15.2), (5.5, -1.2)),),
        (
            _line(
                (9.0, 11.6),
                (7.4, 13.2),
                (4.0, 13.2),
                (2.2, 11.6),
                (2.2, 9.6),
                (3.6, 8.2),
                (7.4, 6.4),
                (8.9, 4.8),
                (8.9, 2.6),
                (7.2, 1.0),
                (3.8, 1.0),
                (2.0, 2.6),
            ),
        ),
    ),
    "%": _glyph(
        13.0,
        (_ring(3.2, 11.4, 2.2, 2.4),),
        (_ring(9.8, 2.6, 2.2, 2.4),),
        (_line((11.2, 14.0), (1.8, 0.0)),),
    ),
    "&": _glyph(
        13.0,
        (
            _line(
                (11.4, 4.6),
                (6.8, 0.5),
                (3.8, 0.5),
                (2.0, 2.2),
                (2.0, 4.4),
                (8.0, 8.4),
                (8.0, 11.0),
                (6.6, 12.6),
                (4.8, 12.6),
                (3.4, 11.0),
                (3.4, 9.0),
                (11.0, 0.2),
            ),
        ),
    ),
    "'": _glyph(4.0, (_line((2.0, 14.0), (2.0, 10.4)),)),
    "(": _glyph(5.0, (_arc(6.0, 7.0, 4.5, 8.0, start_deg=128.0, end_deg=232.0),)),
    ")": _glyph(5.0, (_arc(-1.0, 7.0, 4.5, 8.0, start_deg=52.0, end_deg=-52.0),)),
    "*": _glyph(
        9.0,
        (_line((4.6, 13.4), (4.6, 6.6)),),
        (_line((1.7, 11.7), (7.5, 8.3)),),
        (_line((7.5, 11.7), (1.7, 8.3)),),
    ),
    "+": _glyph(12.0, (_line((6.0, 11.0), (6.0, 3.0)),), (_line((2.0, 7.0), (10.0, 7.0)),)),
    ",": _glyph(5.0, (_line((2.9, 1.4), (2.4, -0.4), (1.2, -2.0)),)),
    "-": _glyph(9.0, (_line((1.4, 7.0), (7.6, 7.0)),)),
    ".": _glyph(5.0, _dot(2.5, 0.9)),
    "/": _glyph(9.0, (_line((0.6, -1.2), (8.4, 15.2)),)),
    "0": _glyph(11.0, (_ring(5.5, 7.0, 3.9, 7.0),)),
    "1": _glyph(
        10.0,
        (_line((1.8, 11.4), (4.6, 14.0), (4.6, 0.0)),),
        (_line((1.8, 0.0), (7.6, 0.0)),),
    ),
    "2": _glyph(
        11.0,
        (
            _line(
                (2.0, 11.0),
                (2.0, 12.4),
                (3.6, 14.0),
                (6.6, 14.0),
                (8.8, 12.2),
                (8.8, 10.0),
                (2.0, 2.0),
                (2.0, 0.0),
                (9.2, 0.0),
            ),
        ),
    ),
    "3": _glyph(
        11.0,
        (
            _line(
                (2.0, 14.0),
                (8.8, 14.0),
                (5.0, 8.6),
                (7.2, 8.6),
                (9.2, 6.8),
                (9.2, 2.6),
                (7.2, 0.4),
                (3.6, 0.4),
                (1.8, 1.8),
            ),
        ),
    ),
    "4": _glyph(11.0, (_line((7.0, 0.0), (7.0, 14.0), (1.0, 4.2), (9.6, 4.2)),)),
    "5": _glyph(
        11.0,
        (
            _line(
                (9.0, 14.0),
                (2.6, 14.0),
                (2.1, 7.8),
                (3.8, 8.8),
                (6.6, 8.8),
                (9.0, 7.0),
                (9.0, 3.2),
                (7.0, 0.5),
                (3.6, 0.4),
                (1.8, 1.8),
            ),
        ),
    ),
    "6": _glyph(
        11.0,
        (_line((8.6, 12.6), (6.0, 14.0), (3.6, 12.4), (2.0, 8.4), (2.0, 4.4)),),
        (_ring(5.6, 4.2, 3.6, 4.2),),
    ),
    "7": _glyph(11.0, (_line((1.6, 14.0), (9.6, 14.0), (3.8, 0.0)),)),
    "8": _glyph(11.0, (_ring(5.5, 10.4, 3.3, 3.6),), (_ring(5.5, 3.6, 3.9, 3.6),)),
    "9": _glyph(
        11.0,
        (_ring(5.5, 9.8, 3.6, 4.2),),
        (_line((9.1, 9.8), (9.1, 5.0), (7.6, 1.4), (5.0, 0.2), (2.6, 1.0)),),
    ),
    ":": _glyph(5.0, _dot(2.5, 8.6), _dot(2.5, 0.9)),
    ";": _glyph(5.0, _dot(2.5, 8.6), (_line((2.9, 1.4), (2.4, -0.4), (1.2, -2.0)),)),
    "<": _glyph(11.0, (_line((9.2, 12.0), (2.0, 7.0), (9.2, 2.0)),)),
    "=": _glyph(12.0, (_line((2.0, 9.2), (10.0, 9.2)),), (_line((2.0, 4.8), (10.0, 4.8)),)),
    ">": _glyph(11.0, (_line((2.0, 12.0), (9.2, 7.0), (2.0, 2.0)),)),
    "?": _glyph(
        10.0,
        (
            _line(
                (2.0, 11.0),
                (2.0, 12.4),
                (3.6, 14.0),
                (6.6, 14.0),
                (8.2, 12.4),
                (8.2, 10.4),
                (5.1, 7.6),
                (5.1, 4.4),
            ),
        ),
        _dot(5.1, 1.2),
    ),
    "@": _glyph(
        14.0,
        (
            _arc(6.8, 7.0, 5.6, 7.0, start_deg=-30.0, end_deg=300.0),
            _line((9.6, 0.94), (11.4, 1.3), (12.4, 2.6)),
        ),
        (_ring(6.5, 6.0, 2.4, 3.0),),
    ),
    "A": _glyph(
        12.0,
        (_line((0.8, 0.0), (6.0, 14.0), (11.2, 0.0)),),
        (_line((2.9, 5.0), (9.1, 5.0)),),
    ),
    "B": _glyph(
        12.0,
        (_line((2.0, 14.0), (2.0, 0.0)),),
        (_line((2.0, 14.0), (7.5, 14.0), (10.0, 12.2), (10.0, 9.4), (7.5, 7.4), (2.0, 7.4)),),
        (_line((7.5, 7.4), (10.4, 5.4), (10.4, 2.0), (7.8, 0.0), (2.0, 0.0)),),
    ),
    "C": _glyph(12.0, (_arc(6.3, 7.0, 4.6, 7.0, start_deg=45.0, end_deg=315.0),)),
    "D": _glyph(
        12.0,
        (_line((2.0, 14.0), (2.0, 0.0)),),
        (
            _line(
                (2.0, 14.0),
                (6.4, 14.0),
                (9.4, 12.0),
                (10.4, 8.6),
                (10.4, 5.4),
                (9.4, 2.0),
                (6.4, 0.0),
                (2.0, 0.0),
            ),
        ),
    ),
    "E": _glyph(
        11.0,
        (_line((9.8, 14.0), (2.0, 14.0), (2.0, 0.0), (9.8, 0.0)),),
        (_line((2.0, 7.4), (8.4, 7.4)),),
    ),
    "F": _glyph(
        10.0,
        (_line((9.4, 14.0), (2.0, 14.0), (2.0, 0.0)),),
        (_line((2.0, 7.4), (8.0, 7.4)),),
    ),
    "G": _glyph(
        13.0,
        (_arc(6.6, 7.0, 4.8, 7.0, start_deg=45.0, end_deg=320.0),),
        (_line((10.3, 2.5), (11.4, 4.2), (11.4, 6.0), (7.6, 6.0)),),
    ),
    "H": _glyph(
        12.0,
        (_line((2.0, 14.0), (2.0, 0.0)),),
        (_line((10.0, 14.0), (10.0, 0.0)),),
        (_line((2.0, 7.4), (10.0, 7.4)),),
    ),
    "I": _glyph(5.0, (_line((2.5, 14.0), (2.5, 0.0)),)),
    "J": _glyph(10.0, (_line((7.6, 14.0), (7.6, 3.4), (6.2, 0.6), (3.6, 0.2), (1.6, 1.6)),)),
    "K": _glyph(
        12.0,
        (_line((2.0, 14.0), (2.0, 0.0)),),
        (_line((10.2, 14.0), (2.0, 6.0), (10.6, 0.0)),),
    ),
    "L": _glyph(10.0, (_line((2.0, 14.0), (2.0, 0.0), (9.4, 0.0)),)),
    "M": _glyph(15.0, (_line((2.0, 0.0), (2.0, 14.0), (7.5, 3.0), (13.0, 14.0), (13.0, 0.0)),)),
    "N": _glyph(13.0, (_line((2.0, 0.0), (2.0, 14.0), (11.0, 0.0), (11.0, 14.0)),)),
    "O": _glyph(13.0, (_ring(6.5, 7.0, 4.9, 7.0),)),
    "P": _glyph(
        11.0,
        (_line((2.0, 0.0), (2.0, 14.0)),),
        (_line((2.0, 14.0), (7.4, 14.0), (9.9, 12.2), (9.9, 9.2), (7.4, 7.2), (2.0, 7.2)),),
    ),
    "Q": _glyph(13.0, (_ring(6.5, 7.0, 4.9, 7.0),), (_line((8.2, 3.2), (12.2, -1.4)),)),
    "R": _glyph(
        12.0,
        (_line((2.0, 0.0), (2.0, 14.0)),),
        (_line((2.0, 14.0), (7.4, 14.0), (9.9, 12.2), (9.9, 9.4), (7.4, 7.4), (2.0, 7.4)),),
        (_line((6.8, 7.4), (10.8, 0.0)),),
    ),
    "S": _glyph(
        11.0,
        (
            _line(
                (9.4, 11.9),
                (7.6, 13.9),
                (4.0, 13.9),
                (2.0, 12.0),
                (2.0, 9.6),
                (3.6, 8.0),
                (7.5, 6.4),
                (9.2, 4.6),
                (9.2, 2.0),
                (7.2, 0.2),
                (3.6, 0.2),
                (1.8, 1.9),
            ),
        ),
    ),
    "T": _glyph(11.0, (_line((1.0, 14.0), (10.0, 14.0)),), (_line((5.5, 14.0), (5.5, 0.0)),)),
    "U": _glyph(
        12.0,
        (
            _line(
                (2.0, 14.0),
                (2.0, 4.0),
                (3.6, 1.0),
                (6.0, 0.2),
                (8.4, 1.0),
                (10.0, 4.0),
                (10.0, 14.0),
            ),
        ),
    ),
    "V": _glyph(12.0, (_line((1.0, 14.0), (6.0, 0.0), (11.0, 14.0)),)),
    "W": _glyph(
        16.0,
        (_line((1.0, 14.0), (4.2, 0.0), (8.0, 11.0), (11.8, 0.0), (15.0, 14.0)),),
    ),
    "X": _glyph(
        12.0,
        (_line((1.4, 14.0), (10.6, 0.0)),),
        (_line((10.6, 14.0), (1.4, 0.0)),),
    ),
    "Y": _glyph(
        12.0,
        (_line((1.4, 14.0), (6.0, 7.0), (10.6, 14.0)),),
        (_line((6.0, 7.0), (6.0, 0.0)),),
    ),
    "Z": _glyph(11.0, (_line((1.6, 14.0), (9.4, 14.0), (1.6, 0.0), (9.4, 0.0)),)),
    "[": _glyph(5.0, (_line((4.6, 15.2), (2.0, 15.2), (2.0, -1.6), (4.6, -1.6)),)),
    "\\": _glyph(9.0, (_line((0.6, 15.2), (8.4, -1.2)),)),
    "]": _glyph(5.0, (_line((0.8, 15.2), (3.4, 15.2), (3.4, -1.6), (0.8, -1.6)),)),
    "^": _glyph(10.0, (_line((1.4, 9.2), (5.0, 13.8), (8.6, 9.2)),)),
    "_": _glyph(10.0, (_line((0.0, -2.6), (10.0, -2.6)),)),
    "`": _glyph(5.0, (_line((1.6, 14.2), (4.0, 11.4)),)),
    "a": _glyph(10.0, (_ring(4.7, 4.5, 3.5, 4.5),), (_line((8.2, 9.0), (8.2, 0.0)),)),
    "b": _glyph(10.0, (_line((2.0, 14.0), (2.0, 0.0)),), (_ring(5.6, 4.5, 3.6, 4.5),)),
    "c": _glyph(9.0, (_arc(5.0, 4.5, 3.5, 4.5, start_deg=50.0, end_deg=310.0),)),
    "d": _glyph(10.0, (_line((8.0, 14.0), (8.0, 0.0)),), (_ring(4.4, 4.5, 3.6, 4.5),)),
    "e": _glyph(
        9.0,
        (_line((1.5, 5.0), (8.5, 5.0)), _arc(5.0, 4.5, 3.5, 4.5, start_deg=6.4, end_deg=335.0)),
    ),
    "f": _glyph(
        7.0,
        (_line((7.0, 13.2), (5.4, 14.2), (4.0, 13.0), (4.0, 0.0)),),
        (_line((1.4, 9.0), (6.8, 9.0)),),
    ),
    "g": _glyph(
        10.0,
        (_ring(4.7, 4.5, 3.5, 4.5),),
        (_line((8.2, 9.0), (8.2, -1.6), (6.4, -3.6), (3.4, -3.8), (1.8, -3.0)),),
    ),
    "h": _glyph(
        10.0,
        (_line((2.0, 14.0), (2.0, 0.0)),),
        (_line((2.0, 6.8), (4.0, 9.0), (6.4, 9.0), (8.2, 7.2), (8.2, 0.0)),),
    ),
    "i": _glyph(5.0, (_line((2.5, 9.0), (2.5, 0.0)),), _dot(2.5, 12.0)),
    "j": _glyph(
        6.0,
        (_line((3.6, 9.0), (3.6, -1.6), (2.2, -3.6), (0.2, -3.8)),),
        _dot(3.6, 12.0),
    ),
    "k": _glyph(
        9.0,
        (_line((2.0, 14.0), (2.0, 0.0)),),
        (_line((7.8, 9.0), (2.0, 3.8), (8.2, 0.0)),),
    ),
    "l": _glyph(5.0, (_line((2.5, 14.0), (2.5, 0.0)),)),
    "m": _glyph(
        15.0,
        (_line((2.0, 9.0), (2.0, 0.0)),),
        (_line((2.0, 6.8), (3.6, 9.0), (5.6, 9.0), (7.0, 7.2), (7.0, 0.0)),),
        (_line((7.0, 6.8), (8.6, 9.0), (10.6, 9.0), (12.0, 7.2), (12.0, 0.0)),),
    ),
    "n": _glyph(
        10.0,
        (_line((2.0, 9.0), (2.0, 0.0)),),
        (_line((2.0, 6.8), (4.0, 9.0), (6.4, 9.0), (8.2, 7.2), (8.2, 0.0)),),
    ),
    "o": _glyph(10.0, (_ring(5.0, 4.5, 3.6, 4.5),)),
    "p": _glyph(10.0, (_line((2.0, 9.0), (2.0, -4.0)),), (_ring(5.6, 4.5, 3.6, 4.5),)),
    "q": _glyph(10.0, (_line((8.0, 9.0), (8.0, -4.0)),), (_ring(4.4, 4.5, 3.6, 4.5),)),
    "r": _glyph(
        7.0,
        (_line((2.0, 9.0), (2.0, 0.0)),),
        (_line((2.0, 6.4), (3.6, 8.6), (5.6, 9.0), (6.6, 8.6)),),
    ),
    "s": _glyph(
        8.0,
        (
            _line(
                (6.9, 7.9),
                (5.4, 9.0),
                (3.0, 9.0),
                (1.5, 7.7),
                (2.1, 6.2),
                (5.6, 4.6),
                (6.7, 3.2),
                (6.2, 1.2),
                (4.0, 0.0),
                (1.4, 0.9),
            ),
        ),
    ),
    "t": _glyph(
        7.0,
        (_line((3.4, 13.0), (3.4, 2.0), (4.6, 0.2), (6.4, 0.8)),),
        (_line((1.2, 9.0), (6.0, 9.0)),),
    ),
    "u": _glyph(
        10.0,
        (_line((2.0, 9.0), (2.0, 2.2), (3.6, 0.2), (6.0, 0.0), (8.2, 1.6)),),
        (_line((8.2, 9.0), (8.2, 0.0)),),
    ),
    "v": _glyph(9.0, (_line((1.2, 9.0), (4.5, 0.0), (7.8, 9.0)),)),
    "w": _glyph(13.0, (_line((1.0, 9.0), (3.4, 0.0), (6.5, 7.0), (9.6, 0.0), (12.0, 9.0)),)),
    "x": _glyph(9.0, (_line((1.4, 9.0), (7.6, 0.0)),), (_line((7.6, 9.0), (1.4, 0.0)),)),
    "y": _glyph(9.0, (_line((1.2, 9.0), (4.7, 0.6)),), (_line((7.8, 9.0), (2.4, -3.8)),)),
    "z": _glyph(8.0, (_line((1.4, 9.0), (6.6, 9.0), (1.4, 0.0), (6.6, 0.0)),)),
    "{": _glyph(
        7.0,
        (
            _line(
                (5.4, 15.2),
                (3.4, 13.4),
                (3.4, 8.6),
                (1.4, 7.0),
                (3.4, 5.4),
                (3.4, 0.6),
                (5.4, -1.2),
            ),
        ),
    ),
    "|": _glyph(5.0, (_line((2.6, 15.2), (2.6, -2.0)),)),
    "}": _glyph(
        7.0,
        (
            _line(
                (1.4, 15.2),
                (3.4, 13.4),
                (3.4, 8.6),
                (5.4, 7.0),
                (3.4, 5.4),
                (3.4, 0.6),
                (1.4, -1.2),
            ),
        ),
    ),
    "~": _glyph(
        12.0,
        (_line((1.4, 6.6), (3.2, 8.6), (5.0, 8.6), (7.0, 5.8), (8.8, 5.8), (10.6, 7.8)),),
    ),
}

SUBSTITUTE = _glyph(
    11.0,
    (_line((2.0, 0.0), (2.0, 12.0), (9.0, 12.0), (9.0, 0.0), (2.0, 0.0)),),
    (_line((2.0, 0.0), (9.0, 12.0)),),
)
"""What a character outside :data:`GLYPHS` is drawn as: a box with a diagonal through it.

Deliberately loud. The alternative -- skipping the character -- leaves the reader a sentence
with a hole in it and no way to tell that anything was lost, which in a *written reply* is the
worst outcome available: the human trusts what the page says. A struck box is unmistakably not
a letter, occupies real width so the line still scans, and the offending characters also come
back in ``InkText.substituted`` so the caller can say which ones they were.
"""


def glyph_for(char: str, /) -> Glyph:
    """Return the glyph that draws ``char``.

    Parameters
    ----------
    char
        One character.

    Returns
    -------
    Glyph
        Its glyph, or :data:`SUBSTITUTE` when this font has none. Total by construction: there
        is no "unknown character" path for a caller to forget to handle.
    """
    return GLYPHS.get(char, SUBSTITUTE)


def resample(points: Polyline, /, *, step: float) -> Polyline:
    """Re-space a polyline at a uniform ``step`` of arc length, keeping both endpoints.

    A stylus samples on a clock, so its points are evenly spaced along the path rather than
    clustered at the corners a draughtsman happened to type. Without this, a straight stem
    would arrive as two samples and a bowl as sixty -- and every per-point envelope in
    :mod:`rmspec.render._ink_text` would mean something different on one than on the other,
    which is exactly how a stroke ends up tapering on its curves and not on its stems.

    Zero-length steps are skipped, so a trace whose segments join at coincident points does not
    emit a duplicate sample and does not divide by zero.

    Parameters
    ----------
    points
        The polyline, in font units.
    step
        Spacing to emit at, in font units. Must be positive.

    Returns
    -------
    Polyline
        The resampled polyline. Empty only for empty input; otherwise it starts at
        ``points[0]`` and ends exactly at ``points[-1]``, so a closed ring stays closed.
    """
    if not points:
        return ()
    walked: list[tuple[float, float]] = [points[0]]
    carried = 0.0
    for index in range(len(points) - 1):
        x0, y0 = points[index]
        x1, y1 = points[index + 1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0.0:
            continue
        along = step - carried
        while along <= length:
            fraction = along / length
            walked.append((x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction))
            along += step
        carried = length - (along - step)
    if walked[-1] != points[-1]:
        walked.append(points[-1])
    return tuple(walked)


def _trace_step(points: Polyline, /) -> float:
    """Return the sample spacing to walk one flattened pen-down at.

    The trace's length divided into a whole number of equal steps, each no longer than
    :data:`SAMPLE_STEP_UNITS` and never fewer than :data:`MIN_SAMPLES_PER_TRACE` of them.
    Dividing evenly rather than walking a fixed step also puts the final sample on the trace's
    end to within float error, instead of up to a whole step short of it and followed by a runt
    segment of whatever was left over.

    Parameters
    ----------
    points
        The flattened pen-down, in font units.

    Returns
    -------
    float
        The spacing, in font units. Positive for any trace with two distinct points, which
        every glyph trace has.
    """
    length = math.fsum(
        math.hypot(after[0] - before[0], after[1] - before[1])
        for before, after in itertools.pairwise(points)
    )
    return length / max(MIN_SAMPLES_PER_TRACE, math.ceil(length / SAMPLE_STEP_UNITS))


def sampled_traces(char: str, /) -> tuple[Polyline, ...]:
    """Return ``char``'s pen-downs as uniformly sampled polylines, in font units.

    The only way out of this module. Flattening resolution and sample spacing are both fixed
    here rather than exposed, because they are properties of *this font* and of a stylus, not
    of the caller's page: a caller who scales the text gets the same letterform sampled the
    same way, and the numbers stay in one place.

    Parameters
    ----------
    char
        One character. Anything outside :data:`GLYPHS` yields the substitute's traces.

    Returns
    -------
    tuple[Polyline, ...]
        One polyline per pen-down, in writing order, each with at least two distinct points --
        an invariant the font-wide test asserts over every glyph, and what lets the stroke
        builder normalise position along a trace without a guard for a trace of one point.
    """
    return tuple(
        resample(flat, step=_trace_step(flat))
        for flat in (
            tuple(point for segment in trace for point in segment.points)
            for trace in glyph_for(char).traces
        )
    )
