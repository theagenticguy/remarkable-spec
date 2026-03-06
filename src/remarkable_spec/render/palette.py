"""Palette management for reMarkable color rendering.

Provides two palette modes:
  - **Export palette** (``EXPORT_PALETTE``): Bright, saturated RGB values used
    when rendering to PNG/SVG/PDF for viewing on LCD/OLED screens.
  - **Physical palette** (``PHYSICAL_PALETTE``): Muted colors that approximate
    what the Paper Pro e-ink display actually shows.

The export palette is the default for all rendering operations. The physical
palette is useful for creating faithful reproductions of the on-device appearance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from remarkable_spec.models.color import (
    PAPER_PRO_PHYSICAL,
    RGB,
    RM_PALETTE,
    PenColor,
)


@dataclass(frozen=True)
class Palette:
    """A named mapping from PenColor IDs to RGB tuples.

    Palettes control how stroke colors are rendered in exported files.
    The ``get_rgb`` method looks up the RGB value for a given PenColor,
    falling back to black if the color is not defined in the palette.

    Attributes:
        name: Human-readable palette name.
        colors: Mapping from PenColor enum values to RGB objects.
    """

    name: str
    colors: dict[PenColor, RGB] = field(default_factory=dict)

    def get_rgb(self, color: PenColor) -> tuple[int, int, int]:
        """Look up the RGB tuple for a pen color.

        Args:
            color: The PenColor to look up.

        Returns:
            An (r, g, b) tuple with values in 0-255 range.
            Falls back to black (0, 0, 0) if the color is not in this palette.

        Examples:
            >>> EXPORT_PALETTE.get_rgb(PenColor.BLACK)
            (0, 0, 0)
            >>> EXPORT_PALETTE.get_rgb(PenColor.BLUE)
            (78, 105, 201)
        """
        rgb = self.colors.get(color)
        if rgb is not None:
            return rgb.as_tuple()
        return (0, 0, 0)

    def get_hex(self, color: PenColor) -> str:
        """Look up the hex color string for a pen color.

        Args:
            color: The PenColor to look up.

        Returns:
            A hex color string like ``#4e69c9``. Falls back to ``#000000``.
        """
        rgb = self.colors.get(color)
        if rgb is not None:
            return rgb.as_hex()
        return "#000000"

    def get_css(self, color: PenColor) -> str:
        """Look up the CSS rgb() string for a pen color.

        Args:
            color: The PenColor to look up.

        Returns:
            A CSS color string like ``rgb(78, 105, 201)``. Falls back to ``rgb(0, 0, 0)``.
        """
        rgb = self.colors.get(color)
        if rgb is not None:
            return rgb.as_css()
        return "rgb(0, 0, 0)"


# Default export palette -- bright, saturated colors for screen viewing.
EXPORT_PALETTE = Palette(name="export", colors=RM_PALETTE)

# Physical Paper Pro palette -- muted colors matching e-ink display appearance.
PHYSICAL_PALETTE = Palette(name="paper_pro_physical", colors=PAPER_PRO_PHYSICAL)
