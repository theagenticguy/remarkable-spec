"""Pen types and their rendering characteristics.

Each pen type on the reMarkable has unique rendering behavior controlled by
formulas that take speed, direction, width, pressure, and tilt as inputs.

The _1 and _2 variants produce identical rendering -- the duplication reflects
internal UI state (toolbar row 1 vs row 2).
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field


class PenType(enum.IntEnum):
    """Pen tool ID stored per stroke in .rm binary files.

    Maps to rmscene.scene_items.Pen values. Each pen type has distinct
    rendering behavior: some respond to pressure, tilt, and/or speed,
    while others produce uniform strokes regardless of stylus input.

    The _1 and _2 suffixed variants are functionally identical -- the
    duplication corresponds to the two toolbar rows in the reMarkable UI.
    Use canonical() to normalize to the _1 variant.
    """

    PAINTBRUSH_1 = 0
    PENCIL_1 = 1
    BALLPOINT_1 = 2
    MARKER_1 = 3
    FINELINER_1 = 4
    HIGHLIGHTER_1 = 5
    ERASER = 6
    MECHANICAL_PENCIL_1 = 7
    ERASER_AREA = 8
    PAINTBRUSH_2 = 12
    MECHANICAL_PENCIL_2 = 13
    PENCIL_2 = 14
    BALLPOINT_2 = 15
    MARKER_2 = 16
    FINELINER_2 = 17
    HIGHLIGHTER_2 = 18
    CALLIGRAPHY = 21
    SHADER = 23

    @classmethod
    def is_highlighter(cls, value: int) -> bool:
        """Check whether a pen type ID is a highlighter variant."""
        return value in (cls.HIGHLIGHTER_1, cls.HIGHLIGHTER_2)

    @classmethod
    def is_eraser(cls, value: int) -> bool:
        """Check whether a pen type ID is an eraser variant."""
        return value in (cls.ERASER, cls.ERASER_AREA)

    @classmethod
    def canonical(cls, value: int) -> PenType:
        """Return the canonical (_1) variant for any pen type.

        Maps _2 toolbar-row variants back to their _1 equivalents so that
        rendering logic only needs to handle one variant per pen type.
        """
        _aliases: dict[int, PenType] = {
            cls.PAINTBRUSH_2: cls.PAINTBRUSH_1,
            cls.MECHANICAL_PENCIL_2: cls.MECHANICAL_PENCIL_1,
            cls.PENCIL_2: cls.PENCIL_1,
            cls.BALLPOINT_2: cls.BALLPOINT_1,
            cls.MARKER_2: cls.MARKER_1,
            cls.FINELINER_2: cls.FINELINER_1,
            cls.HIGHLIGHTER_2: cls.HIGHLIGHTER_1,
        }
        pen = cls(value)
        return _aliases.get(pen, pen)


class Pen(BaseModel):
    """A pen configuration with rendering parameters.

    Encapsulates all the information needed to render a stroke for a given
    pen type: the base width and opacity, line cap style, segment length
    for interpolation, and which stylus input channels (pressure, tilt,
    speed) affect the rendered output.

    The base_width and base_opacity can be overridden by per-pen-type
    rendering formulas in the render module.
    """

    model_config = ConfigDict(frozen=True)

    pen_type: PenType = Field(
        description="The pen tool ID from the .rm file. Determines which rendering "
        "formula is applied to transform raw stylus input into visual output.",
    )
    base_width: float = Field(
        description="Base stroke width in screen units before any pressure/speed "
        "modulation. Derived from the thickness_scale value in the .rm file.",
    )
    base_opacity: float = Field(
        default=1.0,
        description="Base stroke opacity (0.0 = transparent, 1.0 = fully opaque). "
        "Some pens like mechanical pencil and highlighter use reduced opacity.",
    )
    stroke_linecap: str = Field(
        default="round",
        description="SVG/Cairo line cap style: 'round', 'square', or 'butt'. "
        "Highlighters and erasers use 'square' for flat-edged strokes.",
    )
    segment_length: int = Field(
        default=1000,
        description="Number of points per rendered segment for interpolation. "
        "Lower values create smoother curves at the cost of more draw calls.",
    )
    pressure_sensitive: bool = Field(
        default=False,
        description="Whether the pen responds to stylus pressure. When True, "
        "harder pressure produces wider/darker strokes.",
    )
    tilt_sensitive: bool = Field(
        default=False,
        description="Whether the pen responds to stylus tilt angle. When True, "
        "tilting the pen changes stroke width or shading.",
    )
    speed_sensitive: bool = Field(
        default=False,
        description="Whether the pen responds to drawing speed. When True, "
        "faster strokes may produce thinner/lighter lines.",
    )

    @classmethod
    def from_stroke(cls, pen_type: PenType, thickness_scale: float) -> Pen:
        """Create a Pen from stroke data read from a .rm file.

        The thickness_scale from the .rm file is used as the base width,
        potentially transformed by pen-specific formulas (e.g. squared for
        mechanical pencil).

        Args:
            pen_type: The pen tool ID from the stroke header.
            thickness_scale: The raw thickness scale value from the stroke header.

        Returns:
            A fully configured Pen instance with the correct rendering parameters.
        """
        match PenType.canonical(pen_type):
            case PenType.FINELINER_1:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale * 1.8,
                    segment_length=1000,
                )
            case PenType.BALLPOINT_1:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale,
                    segment_length=5,
                    pressure_sensitive=True,
                    speed_sensitive=True,
                )
            case PenType.MARKER_1:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale,
                    segment_length=3,
                    tilt_sensitive=True,
                )
            case PenType.PENCIL_1:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale,
                    segment_length=2,
                    pressure_sensitive=True,
                    tilt_sensitive=True,
                    speed_sensitive=True,
                )
            case PenType.MECHANICAL_PENCIL_1:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale**2,
                    base_opacity=0.7,
                )
            case PenType.PAINTBRUSH_1:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale,
                    segment_length=2,
                    pressure_sensitive=True,
                    tilt_sensitive=True,
                    speed_sensitive=True,
                )
            case PenType.CALLIGRAPHY:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale,
                    segment_length=2,
                    pressure_sensitive=True,
                    tilt_sensitive=True,
                )
            case PenType.HIGHLIGHTER_1:
                return cls(
                    pen_type=pen_type,
                    base_width=15.0,
                    base_opacity=0.3,
                    stroke_linecap="square",
                )
            case PenType.SHADER:
                return cls(
                    pen_type=pen_type,
                    base_width=12.0,
                    base_opacity=0.1,
                )
            case PenType.ERASER:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale * 2,
                    stroke_linecap="square",
                )
            case PenType.ERASER_AREA:
                return cls(
                    pen_type=pen_type,
                    base_width=thickness_scale,
                    stroke_linecap="square",
                )
            case _:
                return cls(pen_type=pen_type, base_width=thickness_scale)
