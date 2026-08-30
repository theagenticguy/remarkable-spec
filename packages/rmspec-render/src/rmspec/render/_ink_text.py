"""Text into ink: the strokes a reply has to be made of, because a text block is not read.

The measurement that forces this module to exist
------------------------------------------------
Measured live on firmware 3.27.3.0: xochitl **preserves but does not display** a
``RootTextBlock`` inserted by a foreign author. It read back at the exact position it was
written to, with the foreign author id intact, across the tablet's own re-save -- and it was
never drawn. **Strokes are what it renders.**

So a reply a human can actually see on the tablet has to be ink. That is the whole reason this
component exists, and it is why "just write a text block" is not an option: the device keeps the
block and shows nobody. Do not delete this module in favour of the simpler thing that does not
work. :mod:`rmspec.render._text` draws typed text into the *preview* for the opposite reason --
showing a caller what a file contains, including what the device hides -- and the two must not
be confused: one reports, this one is meant to be read off glass.

Single-stroke font, not a traced outline
---------------------------------------
:mod:`rmspec.render._ink_font` holds the letterforms and the whole argument for centre-lines
over outline tracing. The short version: an outline is a closed contour meant to be *filled*,
a stroke cannot fill, and tracing one draws hollow double-walled letters no pen ever made.

What this module owns instead is everything between a string and a tuple of
:class:`~rmspec.domain.models.Stroke`: line breaking at a real measured width, baselines, the
millimetre-to-screen-unit scale, the centre-origin correction, and the per-sample pressure and
speed envelopes that make the result look written rather than printed.

The envelopes, and why they are the point
-----------------------------------------
A polyline with every sample at the same pressure renders through
:class:`~rmspec.render._pens.BallpointModel` as a constant-width slab -- geometrically correct
and obviously machine-made. Two profiles fix that, and both are physical rather than decorative:

- **Pressure** follows a raised-cosine plateau: it ramps up over the first
  :data:`_TAPER_FRACTION` of the trace, holds, and ramps back down. That is a nib landing,
  bearing down, and lifting.
- **Speed** follows a sine bell -- zero at both ends, peak in the middle -- which is the
  velocity profile of a hand movement, and which the ballpoint's ``- speed / 400`` term turns
  into a *thinning* of the fastest part of the stroke.

Together they give roughly a 1.4:1 width ratio between the middle of a stroke and its ends,
which is what reads as ink. Every constant below is named, and
``packages/rmspec-render/tests/test_render_ink_text.py`` pins the ratio rather than trusting it.

The pen is fixed, and that is a choice
--------------------------------------
Every stroke comes out as :attr:`~rmspec.domain.models.PenType.BALLPOINT_1`, because it is the
one modelled pen whose width formula reads all three channels this module synthesises. A
fineliner ignores pressure entirely and would render the same text as a uniform hairline; a
highlighter is a constant 15 units at 0.3 opacity; an eraser draws white. The caller chooses
the colour and the tablet's thickness slider, not the tool. A caller who genuinely wants
another pen has plain frozen models to work with and can
``stroke.model_copy(update={"pen": ...})`` -- but the envelopes were calibrated against this
one, and nothing else here promises to look like handwriting.

Two consumers, one of which this package cannot see
--------------------------------------------------
The SVG preview and the tablet both derive stroke width from these same per-sample channels,
which is why ``rmspec-render`` can preview a reply at all. They do not agree on everything:
:attr:`~rmspec.domain.models.Stroke.thickness_scale` is inert for a ballpoint here -- the
model's formula never reads its base width -- while the device's own engine does use the
slider. So the preview is a preview: it proves the letterforms, the layout and the taper, and
it does not promise the tablet's exact line weight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rmspec.domain.models import PenType, Point, Stroke
from rmspec.render._ink_font import (
    EM_UNITS,
    GLYPHS,
    glyph_for,
    sampled_traces,
)
from rmspec.render._units import mm_to_pixels, pixels_to_mm

if TYPE_CHECKING:
    from rmspec.domain.models import PenColor, ScreenSpec

__all__ = [
    "INK_TEXT_CHARACTERS",
    "InkText",
    "InkTextStyle",
    "text_to_ink",
    "wrap_ink_text",
]

INK_TEXT_CHARACTERS: frozenset[str] = frozenset(GLYPHS)
"""Exactly the characters :func:`text_to_ink` can draw: printable ASCII, U+0020 to U+007E.

Exported so a caller can fold its text down to this set *before* spending a write on a device.
That matters for a reply whose text came out of a language model, because model prose is full of
en and em dashes, curly quotes and ellipsis characters, none of which are in here. This module
deliberately does not guess what such a character meant -- guessing is a silent edit to
somebody's words -- so it draws the substitute box and reports the character, and normalising is
the caller's decision to make explicitly.
"""

_INK_PEN = PenType.BALLPOINT_1
"""The one pen these envelopes are calibrated for. See the module docstring."""

_ENTRY_PRESSURE = 60
"""Nib pressure where a trace touches down and lifts, on the wire's 0-255 scale."""

_PEAK_PRESSURE = 255
"""Nib pressure across the middle of a trace."""

_PEAK_SPEED = 100
"""Stylus speed at the middle of a trace, on the wire's uint16 scale.

Chosen for its effect rather than its realism as a raw reading: the ballpoint subtracts
``speed / 400`` screen units of width, so this is a quarter of a unit of thinning at the fastest
part of a stroke -- visible, and far short of the clamp at 0.1.
"""

_NIB_INPUT_WIDTH = 3
"""The stylus ``width`` channel, constant along every trace.

Constant because a nib is: a human writing larger does not get a proportionally fatter line, and
the ballpoint's ``width / 4`` term is in screen units, so leaving this fixed is what makes a
reply at any text size come out in the same weight of ink.
"""

_STYLUS_DIRECTION = 25
"""The stylus azimuth channel, constant along every trace.

A right-handed grip holds the pen at a roughly fixed angle, so a constant is more honest than a
per-segment heading. The ballpoint ignores this channel outright; it is populated because the
field is part of a stylus sample and a zero would be a claim about the hand, not an absence.
"""

_TAPER_FRACTION = 0.22
"""Fraction of a trace, at each end, over which pressure ramps between entry and peak."""

_UINT8_MAX = 255
"""Full scale of the ``pressure`` channel, for the clamp."""

_UINT16_MAX = 65535
"""Full scale of the ``speed`` channel, for the clamp."""

_BAD_EM = "InkTextStyle.em_mm must be a finite, positive number of millimetres"
_BAD_LINE_HEIGHT = "InkTextStyle.line_height must be a finite, positive multiple of the em"
_BAD_THICKNESS = "InkTextStyle.thickness_scale must be a finite, non-negative slider value"
_BAD_PLACEMENT = "text_to_ink needs finite left_mm and top_mm; a reply must land somewhere"


@dataclass(frozen=True, slots=True)
class InkTextStyle:
    """How a reply is written: how big, how far apart, in what colour, at what nib setting.

    Field for field the ink counterpart of ``RenderStyle.text``'s
    :class:`~rmspec.domain.ports.render.TextStyle`, and named to match it: ``em_mm`` is that
    type's ``size_px`` in the units a caller placing ink on a page actually has, and
    ``line_height`` is the same multiple-of-the-em it is there. There is no ``family``, because
    this package ships exactly one face.

    Validated in ``__post_init__`` rather than at the point of use. A non-finite size does not
    fail until it has become a ``nan`` coordinate inside a stroke, at which point the SVG writer
    silently skips the segment and the ``.rm`` encoder writes ``nan`` to a file -- so it is
    refused where the caller wrote it.

    Attributes
    ----------
    em_mm
        The em box in millimetres: the text size. Cap height is 0.7 of it and x-height 0.45,
        per the metrics table in :mod:`rmspec.render._ink_font`.
    line_height
        Baseline-to-baseline distance as a multiple of ``em_mm``. Ink spans about 0.95 em, so
        1.0 just touches and anything above leaves air.
    color
        The pen colour index the strokes carry.
    thickness_scale
        The tablet's thickness-slider value, written into every stroke. Inert in this package's
        own preview for a ballpoint -- see the module docstring -- and not inert on the device.

    Raises
    ------
    ValueError
        If ``em_mm`` or ``line_height`` is not finite and positive, or ``thickness_scale`` is
        not finite and non-negative.
    """

    em_mm: float
    line_height: float
    color: PenColor
    thickness_scale: float

    def __post_init__(self) -> None:
        """Refuse a style that cannot produce placeable ink.

        Raises
        ------
        ValueError
            If any field is outside its stated range.
        """
        if not (math.isfinite(self.em_mm) and self.em_mm > 0):
            raise ValueError(_BAD_EM)
        if not (math.isfinite(self.line_height) and self.line_height > 0):
            raise ValueError(_BAD_LINE_HEIGHT)
        if not (math.isfinite(self.thickness_scale) and self.thickness_scale >= 0):
            raise ValueError(_BAD_THICKNESS)


@dataclass(frozen=True, slots=True)
class InkText:
    """One laid-out reply: the ink, what it says, what it could not say, and where it landed.

    Attributes
    ----------
    strokes
        The strokes in writing order, one per pen-down, in **screen units with x measured from
        the centre of the page** -- the coordinate space
        :class:`~rmspec.domain.models.Point` documents and the ``.rm`` encoder expects.
    lines
        The text as it was actually broken, top line first. Whitespace is normalised to single
        spaces, so this is what the page says rather than what was handed in.
    substituted
        The distinct characters this font has no glyph for, in first-appearance order. Each was
        drawn as a struck box; none was dropped. Empty for text that is entirely printable
        ASCII.
    extent_mm
        ``(left, top, right, bottom)`` of the ink, in millimetres from the page's top-left
        corner -- the same frame ``left_mm`` and ``top_mm`` are given in, so a caller can check
        the reply fits before writing it. Degenerate, all four equal to the requested corner,
        for text that draws nothing.
    """

    strokes: tuple[Stroke, ...]
    lines: tuple[str, ...]
    substituted: tuple[str, ...]
    extent_mm: tuple[float, float, float, float]


def _advance_units(text: str, /) -> float:
    """Return the width ``text`` occupies, in font units.

    A real measurement over the font's own advance table, which is the one thing
    :func:`rmspec.render._text.wrap_text` cannot do: it has no metrics, so it estimates every
    glyph at half an em and says so. Here the metrics are in the package.

    Parameters
    ----------
    text
        Any string. Unsupported characters measure as the substitute glyph, which is what they
        will be drawn as.

    Returns
    -------
    float
        The summed advance, in font units.
    """
    return math.fsum(glyph_for(char).advance for char in text)


def wrap_ink_text(text: str, /, *, width_units: float) -> tuple[str, ...]:
    r"""Break ``text`` into the lines that will be written.

    The same rules as :func:`rmspec.render._text.wrap_text`, so the two halves of this package
    break a paragraph the same way: explicit newlines are honoured as paragraph breaks first,
    each paragraph is then wrapped greedily on whitespace, and a single word wider than the
    limit is kept whole rather than hyphenated -- a hyphenation rule is a second set of
    judgements and neither module makes it.

    Two differences, both deliberate. The limit is compared against a *measured* advance rather
    than a character count, because this module has the metrics. And whitespace is normalised by
    ``str.split()``, which is what lets ``\r\n`` line endings, tabs and runs of spaces arrive
    from anywhere and lay out sanely instead of each becoming a substitute box.

    Parameters
    ----------
    text
        The reply.
    width_units
        Wrap width in font units. Non-finite means "do not wrap", matching the reading
        :func:`rmspec.render._text._line_limit` gives an infinite ``TextBlock.width``.

    Returns
    -------
    tuple[str, ...]
        The lines, top first. Empty for text that is nothing but whitespace.
    """
    limit = math.inf if not math.isfinite(width_units) else width_units
    space = glyph_for(" ").advance
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        current_units = _advance_units(current)
        for word in words[1:]:
            word_units = _advance_units(word)
            if current_units + space + word_units <= limit:
                current = f"{current} {word}"
                current_units += space + word_units
            else:
                lines.append(current)
                current = word
                current_units = word_units
        lines.append(current)
    return tuple(lines)


def _plateau(position: float, /) -> float:
    """Return the pressure envelope at ``position`` along a trace.

    A raised cosine ramped in over the first :data:`_TAPER_FRACTION` and out over the last,
    flat in between. Raised cosine rather than linear because a linear ramp puts a visible
    corner in the drawn width where the ramp meets the plateau.

    Parameters
    ----------
    position
        Normalised distance along the trace, ``0.0`` to ``1.0``.

    Returns
    -------
    float
        ``0.0`` at either end, ``1.0`` across the middle.
    """
    ramp = min(1.0, position / _TAPER_FRACTION, (1.0 - position) / _TAPER_FRACTION)
    return 0.5 - 0.5 * math.cos(math.pi * ramp)


def _bell(position: float, /) -> float:
    """Return the speed envelope at ``position`` along a trace.

    Parameters
    ----------
    position
        Normalised distance along the trace, ``0.0`` to ``1.0``.

    Returns
    -------
    float
        ``0.0`` at either end, ``1.0`` at the middle: a hand accelerating and decelerating
        across one pen-down.
    """
    return math.sin(math.pi * position)


def _sample(x: float, y: float, /, *, position: float) -> Point:
    """Build one stylus sample at ``position`` along its trace.

    Parameters
    ----------
    x
        Screen-unit x, already centre-origin.
    y
        Screen-unit y, from the top edge.
    position
        Normalised distance along the trace, ``0.0`` to ``1.0``.

    Returns
    -------
    Point
        The sample, with every channel populated and clamped to the wire's range so no value
        this module computes can raise out of ``Point``'s own validators.
    """
    pressure = _ENTRY_PRESSURE + (_PEAK_PRESSURE - _ENTRY_PRESSURE) * _plateau(position)
    return Point(
        x=x,
        y=y,
        speed=min(_UINT16_MAX, max(0, round(_PEAK_SPEED * _bell(position)))),
        direction=_STYLUS_DIRECTION,
        width=_NIB_INPUT_WIDTH,
        pressure=min(_UINT8_MAX, max(0, round(pressure))),
    )


def _ink_stroke(
    placed: tuple[tuple[float, float], ...],
    /,
    *,
    style: InkTextStyle,
) -> Stroke:
    """Turn one placed pen-down into a stroke, envelopes and all.

    Parameters
    ----------
    placed
        The trace's samples, already in screen units and already centre-origin. At least two
        points: every glyph trace resamples to two or more, which the font-wide test asserts,
        so there is no one-point case to guard and no dead branch pretending there is.
    style
        Colour and slider value.

    Returns
    -------
    Stroke
        One ballpoint stroke.
    """
    last = len(placed) - 1
    return Stroke(
        pen=_INK_PEN,
        color=style.color,
        thickness_scale=style.thickness_scale,
        points=tuple(_sample(x, y, position=index / last) for index, (x, y) in enumerate(placed)),
    )


def text_to_ink(
    text: str,
    /,
    *,
    screen: ScreenSpec,
    style: InkTextStyle,
    left_mm: float,
    top_mm: float,
    width_mm: float,
) -> InkText:
    r"""Write ``text`` as ink: strokes a tablet will draw and a human will read.

    Placement is in millimetres from the page's **top-left corner**, which is the frame a caller
    holding a page has, and the centre-origin correction that scene ``x`` needs is applied here
    from :attr:`~rmspec.domain.models.ScreenSpec.x_shift` -- the domain's own spelling of it, so
    this is not a second place that could compute half a page width differently.

    The first baseline sits one em below ``top_mm``, and each line after it one
    ``em_mm * line_height`` below the last. That is deliberately the same convention
    :func:`rmspec.render._text.append_text_block` uses for a typed block, so "put the text box
    here" means the same thing whether the words end up as ``<text>`` or as ink.

    Characters, and what happens to one this font has no glyph for
    -------------------------------------------------------------
    Supported: the 95 printable ASCII code points, U+0020 to U+007E, listed in
    :data:`INK_TEXT_CHARACTERS`. ``\n`` is a paragraph break, and every other whitespace
    character is a word separator that normalises to a single space.

    Anything else -- an em dash, a curly quote, an accented letter, an emoji -- is drawn as a
    **struck box** and reported in :attr:`InkText.substituted`. It is never dropped: a reader
    handed a sentence with a hole in it has no way to know anything is missing, and a reply is
    the last place to be quietly wrong. A caller that would rather not show a box should fold
    its text against :data:`INK_TEXT_CHARACTERS` first, which is an explicit edit to somebody's
    words and belongs to whoever owns them.

    Parameters
    ----------
    text
        The reply. Empty or all-whitespace text yields no strokes, which is not an error.
    screen
        The screen the page will be read on. Supplies the DPI that turns millimetres into scene
        units and the half-width that makes ``x`` centre-origin.
    style
        Size, line spacing, colour and slider value.
    left_mm
        Left edge of the text box, in millimetres from the page's left edge.
    top_mm
        Top edge of the text box, in millimetres from the page's top edge. The first baseline
        is one em below it.
    width_mm
        Wrap width in millimetres. Non-finite means "do not wrap", matching how
        :mod:`rmspec.render._text` reads an infinite ``TextBlock.width``; a width too narrow
        for a word puts that word on a line of its own rather than splitting it.

    Returns
    -------
    InkText
        The strokes, the lines as broken, the substituted characters, and the extent.

    Raises
    ------
    ValueError
        If ``left_mm`` or ``top_mm`` is not a finite number. Such a reply cannot land anywhere,
        and the failure is worth having at the call site rather than as ``nan`` coordinates
        inside a stroke that the SVG writer skips and a ``.rm`` encoder does not.
    """
    if not (math.isfinite(left_mm) and math.isfinite(top_mm)):
        raise ValueError(_BAD_PLACEMENT)

    em_px = mm_to_pixels(style.em_mm, screen=screen)
    unit_px = em_px / EM_UNITS
    left_px = mm_to_pixels(left_mm, screen=screen) - screen.x_shift
    top_px = mm_to_pixels(top_mm, screen=screen)
    width_units = (
        math.inf
        if not math.isfinite(width_mm)
        else mm_to_pixels(width_mm, screen=screen) / unit_px
    )

    lines = wrap_ink_text(text, width_units=width_units)
    strokes: list[Stroke] = []
    placed_points: list[tuple[float, float]] = []
    for line_index, line in enumerate(lines):
        baseline_px = top_px + em_px + line_index * em_px * style.line_height
        pen_units = 0.0
        for char in line:
            for trace in sampled_traces(char):
                placed = tuple(
                    (left_px + (pen_units + gx) * unit_px, baseline_px - gy * unit_px)
                    for gx, gy in trace
                )
                placed_points.extend(placed)
                strokes.append(_ink_stroke(placed, style=style))
            pen_units += glyph_for(char).advance

    return InkText(
        strokes=tuple(strokes),
        lines=lines,
        substituted=tuple(
            dict.fromkeys(char for line in lines for char in line if char not in GLYPHS)
        ),
        extent_mm=_extent_mm(
            placed_points,
            screen=screen,
            left_mm=left_mm,
            top_mm=top_mm,
        ),
    )


def _extent_mm(
    placed_points: list[tuple[float, float]],
    /,
    *,
    screen: ScreenSpec,
    left_mm: float,
    top_mm: float,
) -> tuple[float, float, float, float]:
    """Return the box the ink occupies, in millimetres from the page's top-left corner.

    Measured over the samples actually emitted rather than over the advance widths, so it
    reflects side bearings, ascenders and descenders instead of the nominal line box.

    Parameters
    ----------
    placed_points
        Every sample of every stroke, in centre-origin screen units.
    screen
        The screen, for the DPI and the half-width that undoes the centre-origin shift.
    left_mm
        The requested left edge, returned in all four slots when there is no ink.
    top_mm
        The requested top edge, returned in all four slots when there is no ink.

    Returns
    -------
    tuple[float, float, float, float]
        ``(left, top, right, bottom)`` in millimetres. Degenerate for text that drew nothing,
        the same convention :func:`rmspec.render._text.block_extent` uses for a block that
        draws nothing.
    """
    if not placed_points:
        return (left_mm, top_mm, left_mm, top_mm)
    xs = [x + screen.x_shift for x, _ in placed_points]
    ys = [y for _, y in placed_points]
    return (
        pixels_to_mm(min(xs), screen=screen),
        pixels_to_mm(min(ys), screen=screen),
        pixels_to_mm(max(xs), screen=screen),
        pixels_to_mm(max(ys), screen=screen),
    )
