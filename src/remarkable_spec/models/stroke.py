"""Stroke and point data structures.

Each stroke (line) in a .rm file consists of metadata (pen type, color,
thickness) and a list of points with per-sample input data from the stylus.

Point attributes in v6 format (14 bytes per point):
  x, y:       float32 -- screen coordinates (1404x1872 for RM2)
  speed:      uint16  -- stylus movement speed
  width:      uint16  -- raw input width
  direction:  uint8   -- angle (0-255 maps to 0-360 degrees)
  pressure:   uint8   -- pen pressure (0-255)
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, computed_field

from remarkable_spec.models.color import PenColor
from remarkable_spec.models.pen import PenType


class Point(BaseModel):
    """A single sampled point from the stylus.

    All values are in the device's native coordinate system:
    x/y in screen units (e.g. 0-1404 / 0-1872 on rM2), and raw sensor
    values for speed, direction, width, and pressure. Each point is
    14 bytes in the v6 binary format.

    The stylus samples at approximately 21,000 points per second on rM2,
    though the actual rate varies with pen speed and firmware version.
    """

    model_config = ConfigDict(frozen=True)

    x: float = Field(description="Horizontal position in screen units (0 = left edge).")
    y: float = Field(description="Vertical position in screen units (0 = top edge).")
    speed: int = Field(
        default=0,
        description="Stylus movement speed as a raw uint16 sensor value. "
        "Higher values indicate faster pen movement.",
    )
    direction: int = Field(
        default=0,
        description="Stylus angle as uint8 (0-255 maps to 0-360 degrees). "
        "Used by tilt-sensitive pens like marker and pencil.",
    )
    width: int = Field(
        default=0,
        description="Raw input width as uint16. Varies by pen type and is "
        "combined with pressure in rendering formulas.",
    )
    pressure: int = Field(
        default=0,
        description="Pen pressure as uint8 (0-255). 0 = no pressure, 255 = maximum. "
        "Used by pressure-sensitive pens like ballpoint and pencil.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pressure_normalized(self) -> float:
        """Pressure as a 0.0-1.0 float, normalized from the raw 0-255 range."""
        return self.pressure / 255.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def direction_radians(self) -> float:
        """Direction converted from uint8 (0-255) to radians (0 to 2*pi)."""
        return self.direction * (math.pi * 2) / 255

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tilt(self) -> float:
        """Tilt derived from direction (used in pen rendering formulas).

        Alias for direction_radians -- named 'tilt' because pen rendering
        formulas use the direction angle as a tilt input for shading effects.
        """
        return self.direction_radians


class Stroke(BaseModel):
    """A single stroke (line) drawn by the stylus.

    Corresponds to a SceneLineItemBlock in the v6 .rm format. A stroke
    captures everything needed to replay one continuous pen-down to pen-up
    movement: the tool used, its color, the thickness scale from the UI
    slider, and the ordered sequence of sampled points.

    Strokes are the fundamental drawing primitive in the reMarkable system.
    All handwritten content is composed of strokes organized into layers.
    """

    pen_type: PenType = Field(
        description="The pen tool used for this stroke. Determines rendering "
        "behavior (line width formula, opacity, sensitivity to pressure/tilt/speed).",
    )
    color: PenColor = Field(
        description="The color index for this stroke. On monochrome devices "
        "(rM1/rM2) only BLACK, GRAY, WHITE are available. Paper Pro adds colors.",
    )
    thickness_scale: float = Field(
        description="Raw thickness scale from the UI thickness slider. This value "
        "is transformed by pen-specific formulas into the actual rendered width.",
    )
    points: list[Point] = Field(
        default_factory=list,
        description="Ordered sequence of stylus sample points from pen-down to "
        "pen-up. Empty strokes are valid (e.g. single-tap dots).",
    )
    starting_length: float = Field(
        default=0.0,
        description="Cumulative length offset for multi-segment rendering. "
        "Used when a stroke is split across multiple rendering passes.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_eraser(self) -> bool:
        """Whether this stroke is an eraser (point or area)."""
        return PenType.is_eraser(self.pen_type)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_highlighter(self) -> bool:
        """Whether this stroke is a highlighter variant."""
        return PenType.is_highlighter(self.pen_type)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """Return (x_min, y_min, x_max, y_max) bounding box of all points.

        Returns (0, 0, 0, 0) for strokes with no points.
        """
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))
