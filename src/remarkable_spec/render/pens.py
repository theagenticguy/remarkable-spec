"""Pen rendering formulas for reMarkable stroke segments.

Each pen type on the reMarkable has unique per-segment rendering characteristics
that depend on stylus input: speed, direction/tilt, raw width, and pressure.

The formulas here are ported from the rmc project (MIT license) and replicate
how the tablet firmware renders strokes. Each PenRenderer implementation
computes the visual width, color, and opacity for a single segment of a stroke.

Segment = the line between two consecutive Points in a Stroke.

References:
    https://github.com/ricklupton/rmc (MIT license)
    https://plasma.ninja/blog/devices/remarkable/binary/format/2017/12/26/reMarkable-lines-file-format.html
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from remarkable_spec.models.pen import PenType


def direction_to_tilt(direction: int) -> float:
    """Convert direction byte (0-255) to tilt angle in radians.

    The reMarkable stylus encodes direction as an unsigned byte where
    0 maps to 0 radians and 255 maps to approximately 2*pi radians.

    Args:
        direction: Raw direction value from the stylus (0-255).

    Returns:
        Tilt angle in radians (0 to ~2*pi).
    """
    return direction * (math.pi * 2) / 255


@runtime_checkable
class PenRenderer(Protocol):
    """Protocol defining the per-segment rendering interface for a pen type.

    Each pen type implements this protocol to compute the visual properties
    of a single stroke segment based on the raw stylus input values.

    Args for all methods:
        speed: Stylus movement speed (raw uint16).
        direction: Stylus direction / tilt angle (raw uint8, 0-255).
        width: Raw input width (uint16).
        pressure: Pen pressure (raw uint8, 0-255).
        last_width: The computed width of the previous segment (for smoothing).
        base_color: The base RGB color assigned to the stroke.
    """

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute the rendered width for this segment."""
        ...

    def segment_color(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
        base_color: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """Compute the rendered RGB color for this segment."""
        ...

    def segment_opacity(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute the rendered opacity (0.0-1.0) for this segment."""
        ...


class BasePenRenderer(ABC):
    """Abstract base class for pen renderers with shared defaults.

    Subclasses must implement ``segment_width``. The default implementations
    of ``segment_color`` and ``segment_opacity`` return the base color unchanged
    and full opacity respectively.
    """

    @abstractmethod
    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute the rendered width for this segment."""
        ...

    def segment_color(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
        base_color: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """Return the base color unchanged (most pens do not modify color per segment)."""
        return base_color

    def segment_opacity(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return full opacity (most pens are fully opaque per segment)."""
        return 1.0


class FinelineRenderer(BasePenRenderer):
    """Fineliner pen renderer.

    The fineliner produces a constant-width line with no pressure/tilt sensitivity.
    Width is determined solely by the base width set at stroke creation time.
    """

    def __init__(self, base_width: float) -> None:
        self._base_width = base_width

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return constant base width regardless of stylus input."""
        return self._base_width


class BallpointRenderer(BasePenRenderer):
    """Ballpoint pen renderer.

    The ballpoint varies width based on pressure and speed. Pressing harder
    produces a wider line; moving faster produces a thinner line.

    Formula: width = (0.5 + pressure/255) + (raw_width/4) - 0.5 * (speed/4/50)
    """

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute ballpoint width from pressure and speed."""
        return (0.5 + pressure / 255) + (width / 4) - 0.5 * (speed / 4 / 50)

    def segment_color(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
        base_color: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """Slightly darken color at higher pressures for ink saturation effect."""
        intensity = 0.2 * (pressure / 255) + 0.8
        return (
            max(0, min(255, int(base_color[0] * intensity))),
            max(0, min(255, int(base_color[1] * intensity))),
            max(0, min(255, int(base_color[2] * intensity))),
        )


class MarkerRenderer(BasePenRenderer):
    """Marker pen renderer.

    The marker varies width based on tilt angle and smooths against the
    previous segment width. Tilting the pen produces a wider stroke.

    Formula: width = 0.9 * ((raw_width/4) - 0.4 * tilt) + 0.1 * last_width
    """

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute marker width from tilt and previous width."""
        tilt = direction_to_tilt(direction)
        return 0.9 * ((width / 4) - 0.4 * tilt) + 0.1 * last_width


class PencilRenderer(BasePenRenderer):
    """Pencil (standard) renderer.

    The pencil combines pressure, tilt, and previous width for a natural
    graphite-like feel. The formula blends all inputs with smoothing.

    Formula: width = 0.7 * (((0.8 * base_width) + (0.5 * pressure/255))
                     * (raw_width/4) - 0.5 * sqrt(tilt) + 0.5 * last_width)
    """

    def __init__(self, base_width: float) -> None:
        self._base_width = base_width

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute pencil width from pressure, tilt, and smoothing."""
        tilt = direction_to_tilt(direction)
        return 0.7 * (
            ((0.8 * self._base_width) + (0.5 * pressure / 255)) * (width / 4)
            - 0.5 * math.sqrt(tilt)
            + 0.5 * last_width
        )

    def segment_opacity(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Pencil opacity varies slightly with pressure for a graphite effect."""
        return 0.7 + 0.3 * (pressure / 255)


class MechanicalPencilRenderer(BasePenRenderer):
    """Mechanical pencil renderer.

    Produces a nearly constant thin line with slight opacity variation.
    """

    def __init__(self, base_width: float) -> None:
        self._base_width = base_width

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return near-constant width for mechanical pencil."""
        return self._base_width

    def segment_opacity(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Slight opacity variation with pressure (default 0.7 base)."""
        return 0.6 + 0.4 * (pressure / 255)


class PaintbrushRenderer(BasePenRenderer):
    """Paintbrush renderer.

    The paintbrush combines pressure, tilt, and speed for an expressive
    brush-like stroke. Pressing harder widens; moving faster narrows.

    Formula: width = 0.7 * (((1 + 1.4 * pressure/255) * (raw_width/4))
                     - 0.5 * tilt - speed/4/50)
    """

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute paintbrush width from pressure, tilt, and speed."""
        tilt = direction_to_tilt(direction)
        return 0.7 * (((1 + 1.4 * pressure / 255) * (width / 4)) - 0.5 * tilt - speed / 4 / 50)


class CalligraphyRenderer(BasePenRenderer):
    """Calligraphy pen renderer.

    Produces a stroke that varies width dramatically with tilt/direction,
    simulating a flat-nib calligraphy pen. Pressure also influences width.
    """

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Compute calligraphy width from pressure and tilt."""
        tilt = direction_to_tilt(direction)
        return 0.5 * ((0.5 + pressure / 255) * (width / 4) - 0.5 * tilt + 0.5 * last_width)


class HighlighterRenderer(BasePenRenderer):
    """Highlighter renderer.

    Produces a wide, semi-transparent stroke with constant width. The
    highlighter ignores pressure and tilt; its visual effect comes from
    low opacity layering.
    """

    def __init__(self, base_width: float = 15.0, base_opacity: float = 0.3) -> None:
        self._base_width = base_width
        self._base_opacity = base_opacity

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return constant highlighter width."""
        return self._base_width

    def segment_opacity(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return constant semi-transparent opacity."""
        return self._base_opacity


class ShaderRenderer(BasePenRenderer):
    """Shader tool renderer.

    The shader is similar to the highlighter but with even lower opacity
    for soft shading effects. It uses a wide, very transparent stroke.
    """

    def __init__(self, base_width: float = 12.0, base_opacity: float = 0.1) -> None:
        self._base_width = base_width
        self._base_opacity = base_opacity

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return constant shader width."""
        return self._base_width

    def segment_opacity(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return low opacity for shading."""
        return self._base_opacity


class EraserRenderer(BasePenRenderer):
    """Eraser renderer.

    The eraser renders as a white stroke that visually covers underlying content.
    Width is constant based on the eraser size setting.
    """

    def __init__(self, base_width: float) -> None:
        self._base_width = base_width

    def segment_width(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
    ) -> float:
        """Return constant eraser width."""
        return self._base_width

    def segment_color(
        self,
        speed: int,
        direction: int,
        width: int,
        pressure: int,
        last_width: float,
        base_color: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """Eraser always renders as white regardless of stroke color."""
        return (255, 255, 255)


def get_pen_renderer(pen_type: PenType, base_width: float) -> BasePenRenderer:
    """Return the appropriate PenRenderer for a given pen type.

    This factory function maps each PenType to its concrete renderer class,
    passing through any required construction parameters.

    Args:
        pen_type: The pen type from the stroke data.
        base_width: The base width computed from the stroke's thickness_scale.

    Returns:
        A concrete PenRenderer instance for the given pen type.

    Examples:
        >>> renderer = get_pen_renderer(PenType.BALLPOINT_1, 2.0)
        >>> w = renderer.segment_width(
        ...     speed=100, direction=50, width=200, pressure=128, last_width=1.5,
        ... )
    """
    canonical = PenType.canonical(pen_type)
    match canonical:
        case PenType.FINELINER_1:
            return FinelineRenderer(base_width)
        case PenType.BALLPOINT_1:
            return BallpointRenderer()
        case PenType.MARKER_1:
            return MarkerRenderer()
        case PenType.PENCIL_1:
            return PencilRenderer(base_width)
        case PenType.MECHANICAL_PENCIL_1:
            return MechanicalPencilRenderer(base_width)
        case PenType.PAINTBRUSH_1:
            return PaintbrushRenderer()
        case PenType.CALLIGRAPHY:
            return CalligraphyRenderer()
        case PenType.HIGHLIGHTER_1:
            return HighlighterRenderer()
        case PenType.SHADER:
            return ShaderRenderer()
        case PenType.ERASER | PenType.ERASER_AREA:
            return EraserRenderer(base_width)
        case _:
            # Fallback to fineliner for unknown pen types
            return FinelineRenderer(base_width)
