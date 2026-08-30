"""Typed-text layout: the one thing here with no legacy counterpart.

The legacy renderer never read ``Layer.text_blocks``, so a page whose only content was a typed
block rendered as a perfectly valid, blank-looking ``<svg>`` -- ``stroke_count`` zero,
``notices`` empty -- and the words were simply gone from the PNG, the PDF and both OCR paths.
``ports/render.py`` closes that with three members, and this module is the half that draws.

Nothing here knows which of a page's two text sources a block came from. ``parent`` is whatever
element the caller wants the lines under -- a layer's group for ``Layer.text_blocks``, the root
``<svg>`` for ``PageContent.text_blocks``, which is drawn last and belongs to no layer. Keeping
that distinction entirely in :mod:`rmspec.render._renderer` is why there is one drawing path and
not two that could drift.

Wrapping is an estimate, and says so
------------------------------------
``TextBlock`` carries a corner, a wrap width and a string; SVG has no auto-wrap and a pure
package has no font metrics, so lines are broken greedily on whitespace at an average advance
of half the em size. That is an honest estimate rather than a measurement, and it is stated
here so nobody reads the output as typeset. Every input to it -- family, size and line height
-- comes from ``RenderStyle.text``, so it is digest-covered: changing the font misses the OCR
cache instead of serving a stale row.

Measuring is half the job, and it happens first
-----------------------------------------------
Drawing a block correctly is not enough: laid out at a large negative ``pos_x``, the words land
outside the ``viewBox`` and every rasterizer clips them, so the counters say "drawn" while the
PNG, the PDF and both OCR paths show nothing. :func:`block_extent` therefore reports the box a
block *will* occupy, in screen units, before any layout exists -- ``layout_for`` folds those
boxes into the same margins ink widens. The estimate has to be an over-estimate rather than an
under-estimate for that reason, which is why the right edge takes the wider of the declared wrap
width and the longest laid-out line.

One ``<text>`` element per line, not ``<tspan>`` children
--------------------------------------------------------
``ElementTree.indent`` rewrites whitespace-only text and tails, so an element *with children*
gets a newline plus indentation injected as its own text content. Inside an SVG ``<text>`` that
whitespace is content: it collapses to a rendered space and silently adds leading padding to
every laid-out line. Emitting one childless ``<text>`` per line sidesteps it entirely --
``indent`` leaves the non-whitespace text of a childless element alone -- and it is also what
lets each line carry its own absolute ``x``, so no baseline depends on the previous one.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rmspec.domain.models import TextBlock
    from rmspec.domain.ports.render import TextStyle
    from rmspec.render._layout import PageLayout

__all__ = ["append_text_block", "block_extent", "wrap_text"]

_AVERAGE_ADVANCE_EM = 0.5
"""Assumed mean glyph advance as a fraction of the em size.

The one constant available without font metrics. It is deliberately named rather than inlined,
because it is the number to change when somebody wires real metrics in.
"""

_TEXT_FILL = "black"
"""Typed text is drawn in black; ``TextBlock`` carries no colour of its own."""


def _line_limit(width: float, /, *, style: TextStyle) -> float:
    """Return the estimated character count one line of ``width`` holds.

    ``TextBlock.width`` is ``Field(gt=0)``, and pydantic admits ``inf`` under that constraint
    because ``inf > 0`` -- it only rejects ``nan``, for which the comparison is false. A bare
    ``int(width / ...)`` therefore raises ``OverflowError`` on a value the domain accepts,
    which is a third exception type escaping a method whose docstring declares exactly two. An
    unbounded wrap width means "do not wrap" instead, which is also the only reading of
    infinity that draws the words the caller stored.

    Parameters
    ----------
    width
        The block's wrap width in screen units, finite or not.
    style
        Family, size and line height, from ``RenderStyle.text``.

    Returns
    -------
    float
        A character count of at least one, or ``math.inf`` when ``width`` is not finite.
    """
    if not math.isfinite(width):
        return math.inf
    return max(1, int(width / (style.size_px * _AVERAGE_ADVANCE_EM)))


def wrap_text(text: str, /, *, width: float, style: TextStyle) -> tuple[str, ...]:
    """Break ``text`` into rendered lines.

    Explicit newlines are honoured as paragraph breaks first, then each paragraph is wrapped
    greedily on whitespace. A single word longer than the estimated line length is kept whole
    rather than split mid-word, because a hyphenation rule without metrics would be a second
    guess stacked on the first.

    Parameters
    ----------
    text
        The block's flattened text.
    width
        The block's wrap width in screen units.
    style
        Family, size and line height, from ``RenderStyle.text``.

    Returns
    -------
    tuple[str, ...]
        The lines to draw, top first. Empty when ``text`` holds nothing but whitespace.
    """
    limit = _line_limit(width, style=style)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= limit:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return tuple(lines)


def block_extent(block: TextBlock, /, *, style: TextStyle) -> tuple[float, float, float, float]:
    """Estimate the box one laid-out block occupies, in **screen units**.

    Screen units, not points, so this can be computed *before* a :class:`PageLayout` exists:
    the padding scan needs every extent up front, and returning points would need the scale
    and the centre-origin shift that only the layout owns. Everything here is in the same
    units ``TextBlock`` and ``TextStyle.size_px`` already speak, so the transform stays in one
    place.

    The right edge is the wider of the declared wrap width and the longest laid-out line at
    the estimated average advance, because :func:`wrap_text` keeps a single over-long word
    whole rather than splitting it. The bottom edge is the last baseline; a descender hangs a
    fraction of an em below it, which the minimum margin absorbs.

    Parameters
    ----------
    block
        The block that would be drawn.
    style
        Family, size and line height.

    Returns
    -------
    tuple[float, float, float, float]
        ``(min_x, min_y, max_x, max_y)`` in screen units. Degenerate -- all four equal to the
        block's corner -- for a block that draws nothing, and for one whose corner is not a
        finite number, which is unplaceable rather than merely off-page.
    """
    lines = wrap_text(block.text, width=block.width, style=style)
    if not lines or not (math.isfinite(block.pos_x) and math.isfinite(block.pos_y)):
        return (block.pos_x, block.pos_y, block.pos_x, block.pos_y)

    longest = max(len(line) for line in lines) * style.size_px * _AVERAGE_ADVANCE_EM
    declared = block.width if math.isfinite(block.width) else 0.0
    height = style.size_px + (len(lines) - 1) * style.size_px * style.line_height
    return (
        block.pos_x,
        block.pos_y,
        block.pos_x + max(declared, longest),
        block.pos_y + height,
    )


def append_text_block(
    parent: ET.Element,
    block: TextBlock,
    /,
    *,
    style: TextStyle,
    layout: PageLayout,
) -> bool:
    """Draw one typed text block over a layer's ink.

    Coordinates go through the same ``x * scale + x_shift`` centre-origin transform the ink
    does, because ``TextBlock`` positions are screen units like every stroke point.

    Parameters
    ----------
    parent
        The layer group to append to.
    block
        The block to draw.
    style
        Family, size and line height.
    layout
        The geometry.

    Returns
    -------
    bool
        Whether any markup was committed. ``False`` for a block whose text is empty or all
        whitespace, which is a real thing to store and nothing to draw, and ``False`` for one
        whose corner is not a finite number, which would serialise as ``x="nan"`` -- markup no
        rasterizer accepts. The caller counts the difference and reports it as
        ``TEXT_OMITTED``, because dropping words in silence is the failure this module exists
        to end.
    """
    lines = wrap_text(block.text, width=block.width, style=style)
    if not lines:
        return False

    x = block.pos_x * layout.scale + layout.x_shift
    font_size = style.size_px * layout.scale
    advance = style.size_px * style.line_height * layout.scale
    first_baseline = block.pos_y * layout.scale + font_size
    if not (math.isfinite(x) and math.isfinite(first_baseline) and math.isfinite(advance)):
        return False

    for index, line in enumerate(lines):
        element = ET.SubElement(parent, "text")
        element.set("x", f"{x:.2f}")
        element.set("y", f"{first_baseline + index * advance:.2f}")
        element.set("font-family", style.family)
        element.set("font-size", f"{font_size:.2f}")
        element.set("fill", _TEXT_FILL)
        element.text = line
    return True
