"""The centre-origin correction and the viewport padding scan.

This module holds the package's load-bearing invariant. Scene ``x`` is measured from the
*centre* of the page, so every rendered coordinate is ``x * scale + x_shift`` where
``x_shift`` is half the viewport width in points. Get it wrong and the ink is still drawn,
still looks like handwriting, and sits half a page to one side.

The padding scan is a gated recurrence, not a maximum over extents
------------------------------------------------------------------
Each of the four updates is gated on the *running* pad::

    if px > vw + pad_right:
        pad_right = px - vw + min_pad

so once a pad has grown, a later, larger extent that exceeds the old one by less than
``min_pad`` does not trigger again. The result therefore depends on layer, stroke and point
iteration order, and it is **not** the same number a maximum over stroke extents produces.
Worked counterexample, with ``min_pad = 30`` and two x samples: ``[-100, -110]`` yields
``pad_left = 130.0``, the reversed order yields ``140.0``, and a max-over-extents rewrite
yields ``140.0`` as well.

``PageContent.bounding_box`` is therefore deliberately *not* used here, even though it looks
like the same computation and is the natural domain property to reach for: substituting it
would move the viewBox of real pages while rendering a visually identical picture, which is
the single most likely way to break all thirty oracle hashes. A test pins the counterexample
above so the rewrite fails loudly.

Typed text and underlays widen the same margins, in a second pass that is *not* gated
------------------------------------------------------------------------------------
Neither has a legacy counterpart -- the legacy renderer never read ``Layer.text_blocks`` -- so
neither inherits the order dependence. :func:`_widened_for_box` takes a ``max`` against the pad
it replaces, which makes that pass commutative, and it runs after the ink scan so it cannot
perturb a single oracle byte. What it buys is the anti-vanishing guarantee: a text block at a
large negative ``pos_x`` moves the ``viewBox`` instead of being laid out outside it and clipped.

For the same reason this value is not hoisted into ``rmspec.domain.models``, where
``ports/render.py`` says a layout value belongs: putting an order-dependent quirk of legacy
iteration into shared vocabulary would give the export slice a second reason to reproduce it.
It stays private here until the padding rule is made order-independent, which is a change that
must regenerate the oracle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rmspec.render._units import PRINTABLE_TOLERANCE, points_per_pixel

if TYPE_CHECKING:
    from rmspec.domain.models import Layer, ScreenSpec

__all__ = ["PageLayout", "layout_for"]


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Everything the SVG writer needs to place a point, in points.

    Attributes
    ----------
    scale
        Points per screen unit, ``72 / dpi``.
    x_shift
        Half the viewport width, added to every ``x`` because scene ``x`` is centre-origin.
    viewport_width
        The screen's width in points.
    viewport_height
        The screen's height in points.
    pad_left
        Margin to the left of the page box.
    pad_top
        Margin above the page box.
    pad_right
        Margin to the right of the page box.
    pad_bottom
        Margin below the page box.
    min_padding
        The minimum every pad started at, kept so ``content_overflowed`` can tell "margin"
        from "the handwriting ran off the page".
    """

    scale: float
    x_shift: float
    viewport_width: float
    viewport_height: float
    pad_left: float
    pad_top: float
    pad_right: float
    pad_bottom: float
    min_padding: float

    @property
    def total_width(self) -> float:
        """Width of the whole document, margins included.

        Returns
        -------
        float
            ``pad_left + viewport_width + pad_right``, summed in that order.
        """
        return self.pad_left + self.viewport_width + self.pad_right

    @property
    def total_height(self) -> float:
        """Height of the whole document, margins included.

        Returns
        -------
        float
            ``pad_top + viewport_height + pad_bottom``, summed in that order.
        """
        return self.pad_top + self.viewport_height + self.pad_bottom

    @property
    def origin_x(self) -> float:
        """Left edge of the viewBox.

        Returns
        -------
        float
            ``-pad_left``.
        """
        return -self.pad_left

    @property
    def origin_y(self) -> float:
        """Top edge of the viewBox.

        Returns
        -------
        float
            ``-pad_top``.
        """
        return -self.pad_top

    @property
    def expanded(self) -> bool:
        """Whether the document is larger than the screen it was rendered for.

        Returns
        -------
        bool
            ``True`` whenever any margin was applied at all, including the uniform minimum.
            This is what makes a ``VIEWPORT_EXPANDED`` notice mandatory, because
            ``RenderedPage.size`` then describes a box bigger than ``screen``.
        """
        return self.total_width > self.viewport_width or self.total_height > self.viewport_height

    @property
    def content_overflowed(self) -> bool:
        """Whether content actually fell outside the page box.

        Ink or typed text: both widen the pads, so neither may claim the notice alone.

        Returns
        -------
        bool
            ``True`` when some pad grew past the minimum, which is the case worth telling a
            person about; a page that only has its uniform margin has not overflowed.
        """
        return (
            self.pad_left > self.min_padding
            or self.pad_top > self.min_padding
            or self.pad_right > self.min_padding
            or self.pad_bottom > self.min_padding
        )


def _padding_from_ink(
    layers: tuple[Layer, ...],
    /,
    *,
    scale: float,
    x_shift: float,
    viewport_width: float,
    viewport_height: float,
    min_padding_pt: float,
) -> tuple[float, float, float, float]:
    """Widen the four margins until every visible sample is inside the document.

    The gated recurrence, relocated verbatim: four independent comparisons per sample, each
    against the *running* pad, in the legacy order. Strokes with fewer than two samples are
    visited even though they draw nothing, because the legacy scan visited them too.

    The one addition is the finiteness guard. ``Point.x`` and ``Point.y`` are unconstrained
    floats, so a malformed scene file can carry a NaN or an infinity through the codec and the
    domain untouched; letting one into these comparisons yields ``pad_right = inf``, a
    ``viewBox`` of ``"-30.00 -30.00 inf inf"`` and a ``PhysicalSize`` of infinity that the
    export slice would size a PDF page from. A sample that cannot be placed is skipped whole
    -- not per axis -- so the scan and :func:`rmspec.render._svg.append_stroke`, which skips
    the same segments, agree on which samples exist. The real fix belongs on the model; this
    keeps the adapter's output printable until it lands.

    Parameters
    ----------
    layers
        The page's layers in render order.
    scale
        Points per screen unit.
    x_shift
        The centre-origin correction, in points.
    viewport_width
        The screen's width in points.
    viewport_height
        The screen's height in points.
    min_padding_pt
        Minimum margin, and the amount added beyond an overflowing sample.

    Returns
    -------
    tuple[float, float, float, float]
        ``(left, top, right, bottom)`` in points.
    """
    pad_left = min_padding_pt
    pad_top = min_padding_pt
    pad_right = min_padding_pt
    pad_bottom = min_padding_pt

    for layer in layers:
        if not layer.visible:
            continue
        for stroke in layer.strokes:
            for point in stroke.points:
                px = point.x * scale + x_shift
                py = point.y * scale
                if not (math.isfinite(px) and math.isfinite(py)):
                    continue
                if px < -pad_left:
                    pad_left = -px + min_padding_pt
                if px > viewport_width + pad_right:
                    pad_right = px - viewport_width + min_padding_pt
                if py < -pad_top:
                    pad_top = -py + min_padding_pt
                if py > viewport_height + pad_bottom:
                    pad_bottom = py - viewport_height + min_padding_pt

    return (pad_left, pad_top, pad_right, pad_bottom)


def _widened_for_box(
    pads: tuple[float, float, float, float],
    /,
    *,
    box: tuple[float, float, float, float],
    viewport_width: float,
    viewport_height: float,
    min_padding_pt: float,
) -> tuple[float, float, float, float]:
    """Grow the four margins until ``box`` fits inside the document.

    Deliberately **not** the gated recurrence above: every update is a ``max`` against the pad
    it replaces, so the result is independent of the order the boxes arrive in. The ink scan
    has to stay order-dependent because thirty oracle hashes encode that quirk; nothing
    reproduced here has a legacy counterpart, so nothing here inherits it.

    Parameters
    ----------
    pads
        ``(left, top, right, bottom)`` so far, in points.
    box
        ``(min_x, min_y, max_x, max_y)`` of the content to fit, in points, already through the
        centre-origin transform.
    viewport_width
        The screen's width in points.
    viewport_height
        The screen's height in points.
    min_padding_pt
        The amount kept beyond an overflowing edge.

    Returns
    -------
    tuple[float, float, float, float]
        The widened ``(left, top, right, bottom)``.
    """
    pad_left, pad_top, pad_right, pad_bottom = pads
    min_x, min_y, max_x, max_y = box
    if min_x < -pad_left:
        pad_left = max(pad_left, -min_x + min_padding_pt)
    if min_y < -pad_top:
        pad_top = max(pad_top, -min_y + min_padding_pt)
    if max_x - (viewport_width + pad_right) > PRINTABLE_TOLERANCE:
        pad_right = max(pad_right, max_x - viewport_width + min_padding_pt)
    if max_y - (viewport_height + pad_bottom) > PRINTABLE_TOLERANCE:
        pad_bottom = max(pad_bottom, max_y - viewport_height + min_padding_pt)
    return (pad_left, pad_top, pad_right, pad_bottom)


def layout_for(
    layers: tuple[Layer, ...],
    /,
    *,
    screen: ScreenSpec,
    min_padding_pt: float,
    text_extents: tuple[tuple[float, float, float, float], ...] = (),
    underlay_size_pt: tuple[float, float] | None = None,
) -> PageLayout:
    """Compute the viewport, the centre-origin shift and the four margins.

    Relocated line for line from the first half of the legacy ``SVGRenderer.render_page``:
    the same ``72.0 / dpi``, the same ``x_shift = vw / 2``, the same nested scan that skips
    invisible layers and visits every point of every stroke -- including strokes with fewer
    than two samples, which draw nothing but did widen the legacy padding -- and the same
    four gated comparisons in the same order.

    ``x_shift`` is computed as ``viewport_width / 2`` in *points* rather than as
    ``screen.x_shift * scale`` in screen units. The two are bit-identical for every DPI,
    because halving a float is exact, and a test asserts that for both real screens so a
    third one cannot break it quietly. The legacy spelling is kept because it is the one the
    oracle bytes came from.

    Parameters
    ----------
    layers
        The page's layers in render order. Pass an empty tuple for a page with no content.
    screen
        Screen geometry to lay out for.
    min_padding_pt
        Minimum margin on all four sides, in points. The legacy constant was ``30.0``.
    text_extents
        ``(min_x, min_y, max_x, max_y)`` boxes, in **screen units**, of the typed text blocks
        that will be drawn -- :func:`rmspec.render._text.block_extent` produces them. They
        widen the margins exactly as ink does, which is what stops a block at a large negative
        ``pos_x`` from being laid out hundreds of points outside the ``viewBox`` and clipped by
        every rasterizer: a silent vanishing act with a valid-looking ``<svg>`` around it, and
        the same failure ``TEXT_OMITTED`` exists to make loud. Legacy drew no text at all, so
        there is no legacy geometry to contradict, and no oracle entry carries a visible block.
    underlay_size_pt
        Native ``(width, height)`` of an underlay in points, when there is one. A larger
        underlay widens the viewport so it is not clipped.

    Returns
    -------
    PageLayout
        The geometry every coordinate in the document is computed from.
    """
    scale = points_per_pixel(screen)
    viewport_width = screen.width * scale
    viewport_height = screen.height * scale

    # v6 .rm files put the X origin at the centre of the page, not the top-left corner.
    x_shift = viewport_width / 2

    pad_left, pad_top, pad_right, pad_bottom = _padding_from_ink(
        layers,
        scale=scale,
        x_shift=x_shift,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        min_padding_pt=min_padding_pt,
    )

    pads = (pad_left, pad_top, pad_right, pad_bottom)
    for min_x, min_y, max_x, max_y in text_extents:
        box = (min_x * scale + x_shift, min_y * scale, max_x * scale + x_shift, max_y * scale)
        if not all(math.isfinite(edge) for edge in box):
            continue
        pads = _widened_for_box(
            pads,
            box=box,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            min_padding_pt=min_padding_pt,
        )

    if underlay_size_pt is not None and all(math.isfinite(edge) for edge in underlay_size_pt):
        # A native size that is not finite is drawn at the page box instead -- see
        # rmspec.render._svg.underlay_box -- and the page box needs no widening.
        underlay_width, underlay_height = underlay_size_pt
        pads = _widened_for_box(
            pads,
            box=(0.0, 0.0, underlay_width, underlay_height),
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            min_padding_pt=min_padding_pt,
        )
    pad_left, pad_top, pad_right, pad_bottom = pads

    return PageLayout(
        scale=scale,
        x_shift=x_shift,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        pad_left=pad_left,
        pad_top=pad_top,
        pad_right=pad_right,
        pad_bottom=pad_bottom,
        min_padding=min_padding_pt,
    )
