"""The ten pen-physics models, and the width/linecap table that seeds them.

Relocated from the legacy ``render/pens.py`` (ported there from rmc, MIT) plus the two fields
of the legacy ``models/pen.py`` ``Pen.from_stroke`` the SVG renderer actually read. The
arithmetic is character-for-character identical; only the plumbing changed, and the
differential oracle is what proves it.

What changed, and why it changes no byte
----------------------------------------
- The five raw channels arrive as one :class:`SegmentInput` instead of five keyword arguments.
  Every expression and its association is preserved, so the floats are identical.
- ``get_pen_renderer``'s ``case _: return FinelineRenderer(base_width)`` fallback is gone.
  :func:`model_for` returns ``None`` and the adapter raises ``UnsupportedPenType``, because a
  page drawn with fineliner physics instead of the real pen is indistinguishable from a
  correct one. The fallback was already dead for every enum member: legacy
  ``get_pen_renderer`` and ``Pen.from_stroke`` both folded through ``PenType.canonical``
  first, and all eleven canonical members are covered here.
- The ``runtime_checkable`` ``PenRenderer`` Protocol is dropped. Nothing did ``isinstance``
  against it, and a structural check that passes on three method names while the formulas
  differ is worse than no check.

Two different numbers are both called "thickness"
-------------------------------------------------
``Stroke.thickness_scale`` is the tablet's per-stroke slider value and is what
:func:`profile_for` turns into a base width. ``RenderStyle.thickness_scale`` is the 1.5
export multiplier applied to every finished segment width, and it never reaches this module.
The parameter here is therefore named ``stroke_thickness``, so passing the wrong one is a
misspelling rather than a silently thinner page.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from rmspec.domain.models import PenType

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "BallpointModel",
    "CalligraphyModel",
    "EraserModel",
    "FinelinerModel",
    "HighlighterModel",
    "MarkerModel",
    "MechanicalPencilModel",
    "PaintbrushModel",
    "PenModel",
    "PenProfile",
    "PencilModel",
    "SegmentInput",
    "ShaderModel",
    "direction_to_tilt",
    "model_for",
    "profile_for",
]

_FULL_TURN = math.pi * 2
"""One full stylus rotation in radians."""

_UINT8_MAX = 255
"""Full scale of the ``direction`` and ``pressure`` channels."""

_HIGHLIGHTER_WIDTH = 15.0
"""Constant highlighter width, from the legacy ``HighlighterRenderer`` default."""

_HIGHLIGHTER_OPACITY = 0.3
"""Constant highlighter opacity, from the legacy ``HighlighterRenderer`` default."""

_SHADER_WIDTH = 12.0
"""Constant shader width, from the legacy ``ShaderRenderer`` default."""

_SHADER_OPACITY = 0.1
"""Constant shader opacity, from the legacy ``ShaderRenderer`` default."""

_ERASER_RGB = (255, 255, 255)
"""The eraser draws hard white, whatever colour the stroke claims."""

_FINELINER_WIDTH_FACTOR = 1.8
"""Legacy ``Pen.from_stroke`` fineliner multiplier."""

_ERASER_WIDTH_FACTOR = 2
"""Legacy ``Pen.from_stroke`` point-eraser multiplier."""


def direction_to_tilt(direction: int, /) -> float:
    """Convert the stylus ``direction`` byte to a tilt angle in radians.

    Relocated verbatim: ``direction * (math.pi * 2) / 255``. ``Point.direction_radians``
    computes the same float with the same three operations from the same two constants, so
    the two spellings cannot drift; this one is kept because the ten formulas below are
    pinned against the legacy source and take the raw byte.

    Parameters
    ----------
    direction
        Raw stylus angle, ``0`` to ``255``.

    Returns
    -------
    float
        Tilt in radians, ``0`` to just under ``2 * pi``.
    """
    return direction * _FULL_TURN / _UINT8_MAX


@dataclass(frozen=True, slots=True)
class SegmentInput:
    """The five numbers a per-segment formula reads.

    Raw wire scales, deliberately not the domain's normalised properties: normalising here
    would change every formula's arithmetic and therefore every stroke width in the oracle.

    ``last_width`` is the previous segment's *finished* width -- post-clamp and
    post-thickness-multiplier, exactly as the legacy loop fed it forward. Marker, Pencil and
    Calligraphy read it, so the whole page after the first segment depends on that ordering.

    Attributes
    ----------
    speed
        Raw stylus speed, uint16.
    direction
        Raw stylus angle, uint8.
    width
        Raw input width, uint16.
    pressure
        Raw nib pressure, uint8.
    last_width
        The finished width of the previous segment of the same stroke.
    """

    speed: int
    direction: int
    width: int
    pressure: int
    last_width: float


@dataclass(frozen=True, slots=True)
class PenProfile:
    """The two per-stroke facts the SVG writer needs before any segment is computed.

    The surviving half of the legacy ``Pen`` model. ``base_opacity``, ``segment_length`` and
    the three sensitivity booleans are gone: the SVG renderer never read them, the
    highlighter's ``0.3`` and the shader's ``0.1`` come from their own models, and the
    mechanical pencil's ``0.7`` was overridden by its opacity formula on every segment.

    Attributes
    ----------
    base_width
        Seed for the ``last_width`` feedback term, and the whole width for a constant pen.
    stroke_linecap
        SVG ``stroke-linecap`` for this stroke's group.
    """

    base_width: float
    stroke_linecap: Literal["round", "square"]


class PenModel(ABC):
    """One pen's per-segment width, colour and opacity rules.

    Subclasses override only what varies. ``segment_color`` returning the base colour and
    ``segment_opacity`` returning ``1.0`` are the legacy ``BasePenRenderer`` defaults.

    Every method takes its argument positionally, so an implementation that ignores the
    sample may name the parameter ``_sample`` without breaking a caller.
    """

    def __init__(self, base_width: float) -> None:
        self._base_width = base_width

    @abstractmethod
    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return this segment's width, before the 0.1 clamp and the export multiplier.

        Parameters
        ----------
        sample
            The segment's raw channels and the previous segment's finished width.

        Returns
        -------
        float
            Width in screen units. May be zero or negative; the caller clamps.
        """

    def segment_color(
        self,
        sample: SegmentInput,
        base_color: tuple[int, int, int],
        /,
    ) -> tuple[int, int, int]:
        """Return this segment's ink, defaulting to the stroke's own.

        Parameters
        ----------
        sample
            The segment's raw channels.
        base_color
            The ink the palette resolved this stroke's colour to.

        Returns
        -------
        tuple[int, int, int]
            Channels in ``0``-``255``.
        """
        del sample
        return base_color

    def segment_opacity(self, sample: SegmentInput, /) -> float:
        """Return this segment's opacity, defaulting to fully opaque.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.0`` to ``1.0``. Only a value strictly below ``1.0`` is written to the markup.
        """
        del sample
        return 1.0


class FinelinerModel(PenModel):
    """Constant width, no pressure, tilt or speed sensitivity."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return the base width, whatever the stylus did.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            The base width.
        """
        del sample
        return self._base_width


class BallpointModel(PenModel):
    """Wider under pressure, thinner at speed, and slightly darker under pressure."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return width from pressure, raw width and speed.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``(0.5 + pressure / 255) + (width / 4) - 0.5 * (speed / 4 / 50)``.
        """
        return (
            (0.5 + sample.pressure / _UINT8_MAX)
            + (sample.width / 4)
            - 0.5 * (sample.speed / 4 / 50)
        )

    def segment_color(
        self,
        sample: SegmentInput,
        base_color: tuple[int, int, int],
        /,
    ) -> tuple[int, int, int]:
        """Darken the ink slightly at higher pressure, for an ink-saturation effect.

        ``int()`` truncates rather than rounds, and the clamp is kept even though
        ``intensity`` cannot exceed ``1.0``: both are legacy behaviour and both are visible
        in the oracle bytes.

        Parameters
        ----------
        sample
            The segment's raw channels.
        base_color
            The stroke's ink.

        Returns
        -------
        tuple[int, int, int]
            The darkened ink.
        """
        intensity = 0.2 * (sample.pressure / _UINT8_MAX) + 0.8
        return (
            max(0, min(255, int(base_color[0] * intensity))),
            max(0, min(255, int(base_color[1] * intensity))),
            max(0, min(255, int(base_color[2] * intensity))),
        )


class MarkerModel(PenModel):
    """Wider when tilted, smoothed against the previous segment."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return width from tilt and the previous segment's width.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.9 * ((width / 4) - 0.4 * tilt) + 0.1 * last_width``.
        """
        tilt = direction_to_tilt(sample.direction)
        return 0.9 * ((sample.width / 4) - 0.4 * tilt) + 0.1 * sample.last_width


class PencilModel(PenModel):
    """Graphite: pressure, tilt and smoothing together, with pressure-varying opacity."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return width from base width, pressure, raw width, tilt and smoothing.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.7 * (((0.8 * base) + (0.5 * pressure / 255)) * (width / 4)
            - 0.5 * sqrt(tilt) + 0.5 * last_width)``.
        """
        tilt = direction_to_tilt(sample.direction)
        return 0.7 * (
            ((0.8 * self._base_width) + (0.5 * sample.pressure / _UINT8_MAX)) * (sample.width / 4)
            - 0.5 * math.sqrt(tilt)
            + 0.5 * sample.last_width
        )

    def segment_opacity(self, sample: SegmentInput, /) -> float:
        """Return opacity rising with pressure.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.7 + 0.3 * (pressure / 255)``, so full pressure is exactly ``1.0`` and writes
            no ``opacity`` attribute at all.
        """
        return 0.7 + 0.3 * (sample.pressure / _UINT8_MAX)


class MechanicalPencilModel(PenModel):
    """Near-constant thin line with pressure-varying opacity."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return the base width, which for this pen is the squared slider value.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            The base width.
        """
        del sample
        return self._base_width

    def segment_opacity(self, sample: SegmentInput, /) -> float:
        """Return opacity rising with pressure.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.6 + 0.4 * (pressure / 255)``.
        """
        return 0.6 + 0.4 * (sample.pressure / _UINT8_MAX)


class PaintbrushModel(PenModel):
    """Expressive brush: pressure widens, tilt and speed narrow."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return width from pressure, raw width, tilt and speed.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.7 * (((1 + 1.4 * pressure / 255) * (width / 4)) - 0.5 * tilt
            - speed / 4 / 50)``.
        """
        tilt = direction_to_tilt(sample.direction)
        return 0.7 * (
            ((1 + 1.4 * sample.pressure / _UINT8_MAX) * (sample.width / 4))
            - 0.5 * tilt
            - sample.speed / 4 / 50
        )


class CalligraphyModel(PenModel):
    """Flat-nib pen: width swings with tilt, pressure and smoothing."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return width from pressure, raw width, tilt and smoothing.

        Parameters
        ----------
        sample
            The segment's raw channels.

        Returns
        -------
        float
            ``0.5 * ((0.5 + pressure / 255) * (width / 4) - 0.5 * tilt
            + 0.5 * last_width)``.
        """
        tilt = direction_to_tilt(sample.direction)
        return 0.5 * (
            (0.5 + sample.pressure / _UINT8_MAX) * (sample.width / 4)
            - 0.5 * tilt
            + 0.5 * sample.last_width
        )


class HighlighterModel(PenModel):
    """Wide, semi-transparent, indifferent to the stylus."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return the constant highlighter width.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            ``15.0``, the legacy default, not the profile's base width.
        """
        del sample
        return _HIGHLIGHTER_WIDTH

    def segment_opacity(self, sample: SegmentInput, /) -> float:
        """Return the constant highlighter opacity.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            ``0.3``.
        """
        del sample
        return _HIGHLIGHTER_OPACITY


class ShaderModel(PenModel):
    """Like the highlighter, but softer: wide and very transparent."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return the constant shader width.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            ``12.0``, the legacy default.
        """
        del sample
        return _SHADER_WIDTH

    def segment_opacity(self, sample: SegmentInput, /) -> float:
        """Return the constant shader opacity.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            ``0.1``.
        """
        del sample
        return _SHADER_OPACITY


class EraserModel(PenModel):
    """Constant-width hard white, which covers rather than removes."""

    def segment_width(self, sample: SegmentInput, /) -> float:
        """Return the constant eraser width.

        Parameters
        ----------
        sample
            Ignored.

        Returns
        -------
        float
            The base width.
        """
        del sample
        return self._base_width

    def segment_color(
        self,
        sample: SegmentInput,
        base_color: tuple[int, int, int],
        /,
    ) -> tuple[int, int, int]:
        """Return white, whatever the stroke's colour index said.

        Parameters
        ----------
        sample
            Ignored.
        base_color
            Ignored.

        Returns
        -------
        tuple[int, int, int]
            ``(255, 255, 255)``.
        """
        del sample, base_color
        return _ERASER_RGB


def _fineliner_width(stroke_thickness: float) -> float:
    """Return the fineliner base width.

    Parameters
    ----------
    stroke_thickness
        ``Stroke.thickness_scale``.

    Returns
    -------
    float
        ``stroke_thickness * 1.8``.
    """
    return stroke_thickness * _FINELINER_WIDTH_FACTOR


def _slider_width(stroke_thickness: float) -> float:
    """Return the slider value unchanged, which is what six of the eleven pens use.

    Parameters
    ----------
    stroke_thickness
        ``Stroke.thickness_scale``.

    Returns
    -------
    float
        ``stroke_thickness``.
    """
    return stroke_thickness


def _squared_width(stroke_thickness: float) -> float:
    """Return the mechanical pencil base width.

    Parameters
    ----------
    stroke_thickness
        ``Stroke.thickness_scale``.

    Returns
    -------
    float
        ``stroke_thickness ** 2``.
    """
    return stroke_thickness**2


def _doubled_width(stroke_thickness: float) -> float:
    """Return the point-eraser base width.

    Parameters
    ----------
    stroke_thickness
        ``Stroke.thickness_scale``.

    Returns
    -------
    float
        ``stroke_thickness * 2``.
    """
    return stroke_thickness * _ERASER_WIDTH_FACTOR


def _highlighter_width(stroke_thickness: float) -> float:
    """Return the constant highlighter base width.

    Parameters
    ----------
    stroke_thickness
        Ignored: the legacy table hard-coded this pen's width.

    Returns
    -------
    float
        ``15.0``.
    """
    del stroke_thickness
    return _HIGHLIGHTER_WIDTH


def _shader_width(stroke_thickness: float) -> float:
    """Return the constant shader base width.

    Parameters
    ----------
    stroke_thickness
        Ignored: the legacy table hard-coded this pen's width.

    Returns
    -------
    float
        ``12.0``.
    """
    del stroke_thickness
    return _SHADER_WIDTH


#: Canonical pen -> (base-width rule, linecap), relocating the legacy ``Pen.from_stroke``
#: match arm for arm. A dict rather than a ``match`` so a missing pen is one reachable
#: ``None`` instead of a silent substitution, and so the exhaustiveness test can enumerate it.
_PROFILES: dict[PenType, tuple[Callable[[float], float], Literal["round", "square"]]] = {
    PenType.FINELINER_1: (_fineliner_width, "round"),
    PenType.BALLPOINT_1: (_slider_width, "round"),
    PenType.MARKER_1: (_slider_width, "round"),
    PenType.PENCIL_1: (_slider_width, "round"),
    PenType.MECHANICAL_PENCIL_1: (_squared_width, "round"),
    PenType.PAINTBRUSH_1: (_slider_width, "round"),
    PenType.CALLIGRAPHY: (_slider_width, "round"),
    PenType.HIGHLIGHTER_1: (_highlighter_width, "square"),
    PenType.SHADER: (_shader_width, "round"),
    PenType.ERASER: (_doubled_width, "square"),
    PenType.ERASER_AREA: (_slider_width, "square"),
}

#: Canonical pen -> the physics class that draws it.
_MODELS: dict[PenType, type[PenModel]] = {
    PenType.FINELINER_1: FinelinerModel,
    PenType.BALLPOINT_1: BallpointModel,
    PenType.MARKER_1: MarkerModel,
    PenType.PENCIL_1: PencilModel,
    PenType.MECHANICAL_PENCIL_1: MechanicalPencilModel,
    PenType.PAINTBRUSH_1: PaintbrushModel,
    PenType.CALLIGRAPHY: CalligraphyModel,
    PenType.HIGHLIGHTER_1: HighlighterModel,
    PenType.SHADER: ShaderModel,
    PenType.ERASER: EraserModel,
    PenType.ERASER_AREA: EraserModel,
}


def profile_for(pen: PenType, /, *, stroke_thickness: float) -> PenProfile | None:
    """Return the base width and linecap for one stroke's pen.

    Parameters
    ----------
    pen
        The stroke's pen, folded to its canonical member here.
    stroke_thickness
        ``Stroke.thickness_scale`` -- the tablet's slider value, never
        ``RenderStyle.thickness_scale``.

    Returns
    -------
    PenProfile | None
        The profile, or ``None`` when no rule covers this pen. ``None`` is unreachable for
        every current :class:`PenType` member and exists so that adding one without a rule
        fails loudly instead of drawing with the wrong physics.
    """
    entry = _PROFILES.get(pen.canonical)
    if entry is None:
        return None
    width_of, linecap = entry
    return PenProfile(base_width=width_of(stroke_thickness), stroke_linecap=linecap)


def model_for(pen: PenType, /, *, base_width: float) -> PenModel | None:
    """Return the physics model for one stroke's pen.

    Parameters
    ----------
    pen
        The stroke's pen, folded to its canonical member here.
    base_width
        :attr:`PenProfile.base_width`, which four of the ten models read.

    Returns
    -------
    PenModel | None
        The model, or ``None`` when no model covers this pen -- the case the adapter turns
        into ``UnsupportedPenType``.
    """
    model = _MODELS.get(pen.canonical)
    if model is None:
        return None
    return model(base_width)
