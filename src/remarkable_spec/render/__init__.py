"""Rendering subsystem for reMarkable page data.

This module provides:
  - **Pen renderers** (``pens``) — per-segment width/color/opacity formulas
    for each pen type, ported from the rmc project (MIT license).
  - **Palettes** (``palette``) — color lookup tables mapping PenColor IDs
    to export or physical RGB values.
  - **Render engines** (``engine``) — abstract rendering interface with a
    pure-Python SVG implementation.

The SVG renderer has no external dependencies. For PNG/PDF export, install
the ``render`` extra: ``pip install remarkable-spec[render]``.
"""

from __future__ import annotations

from remarkable_spec.render.engine import (
    SCALE,
    SCREEN_DPI,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    RenderEngine,
    SVGRenderer,
)
from remarkable_spec.render.palette import (
    EXPORT_PALETTE,
    PHYSICAL_PALETTE,
    Palette,
)
from remarkable_spec.render.pens import (
    BasePenRenderer,
    PenRenderer,
    direction_to_tilt,
    get_pen_renderer,
)

__all__ = [
    # Engine
    "RenderEngine",
    "SVGRenderer",
    "SCREEN_WIDTH",
    "SCREEN_HEIGHT",
    "SCREEN_DPI",
    "SCALE",
    # Palette
    "Palette",
    "EXPORT_PALETTE",
    "PHYSICAL_PALETTE",
    # Pens
    "PenRenderer",
    "BasePenRenderer",
    "get_pen_renderer",
    "direction_to_tilt",
]
