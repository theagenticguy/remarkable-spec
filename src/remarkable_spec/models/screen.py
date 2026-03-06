"""Screen specifications for reMarkable devices.

The coordinate system in .rm files uses screen units. Different devices
have different physical resolutions and DPIs. These specs are essential
for converting between screen coordinates and physical measurements
(inches, PDF points) when exporting or rendering content.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ScreenSpec(BaseModel):
    """Physical screen specification for a reMarkable device.

    Defines the pixel dimensions and DPI of a device's e-ink display.
    All coordinates in .rm files are in screen units (pixels), so this
    specification is needed to convert to physical units for export to
    PDF, SVG, or PNG at correct scale.

    The reMarkable 2 and Paper Pro have slightly different screen sizes
    and DPIs, which affects page layout when rendering.
    """

    model_config = ConfigDict(frozen=True)

    width: int = Field(
        description="Screen width in pixels (portrait orientation). "
        "1404 for rM1/rM2, 1620 for Paper Pro.",
    )
    height: int = Field(
        description="Screen height in pixels (portrait orientation). "
        "1872 for rM1/rM2, 2160 for Paper Pro.",
    )
    dpi: int = Field(
        description="Display dots per inch. 226 for rM1/rM2, 229 for Paper Pro. "
        "Used to convert between screen units and physical measurements.",
    )
    name: str = Field(
        description="Human-readable device name, e.g. 'reMarkable 2' or 'Paper Pro'.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def points_per_pixel(self) -> float:
        """Convert screen units to PDF points (1/72 inch).

        This ratio is used when rendering .rm content into PDF pages
        to ensure strokes appear at the correct physical size.
        """
        return 72.0 / self.dpi

    @computed_field  # type: ignore[prop-decorator]
    @property
    def page_width_pt(self) -> float:
        """Page width in PDF points (1/72 inch)."""
        return self.width * self.points_per_pixel

    @computed_field  # type: ignore[prop-decorator]
    @property
    def page_height_pt(self) -> float:
        """Page height in PDF points (1/72 inch)."""
        return self.height * self.points_per_pixel

    @computed_field  # type: ignore[prop-decorator]
    @property
    def page_width_inches(self) -> float:
        """Page width in inches."""
        return self.width / self.dpi

    @computed_field  # type: ignore[prop-decorator]
    @property
    def page_height_inches(self) -> float:
        """Page height in inches."""
        return self.height / self.dpi


# reMarkable 2 (and reMarkable 1) -- 1404x1872 @ 226 DPI
RM2_SCREEN = ScreenSpec(width=1404, height=1872, dpi=226, name="reMarkable 2")

# reMarkable Paper Pro (portrait orientation) -- 1620x2160 @ 229 DPI
PAPER_PRO_SCREEN = ScreenSpec(width=1620, height=2160, dpi=229, name="Paper Pro")


def detect_screen(layers: list) -> ScreenSpec:
    """Auto-detect device screen from stroke coordinate extents.

    If any stroke point exceeds reMarkable 2 screen bounds, assumes
    Paper Pro. Falls back to RM2 if all points fit within RM2 bounds.

    Args:
        layers: List of Layer objects from a parsed .rm file.

    Returns:
        The detected :class:`ScreenSpec`.
    """
    for layer in layers:
        for stroke in layer.strokes:
            for pt in stroke.points:
                # v6 X is center-origin, so range is [-width/2, width/2]
                if abs(pt.x) > RM2_SCREEN.width / 2 or pt.y > RM2_SCREEN.height:
                    return PAPER_PRO_SCREEN
    return RM2_SCREEN
