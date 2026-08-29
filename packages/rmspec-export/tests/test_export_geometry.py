"""Millimetre/point arithmetic and the anisotropic zoom, as tables plus properties."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rmspec.domain.ports.export import PAGE_SIZE_TOLERANCE_MM, PhysicalSize, PixelSize
from rmspec.export._geometry import (
    MM_PER_INCH,
    POINTS_PER_INCH,
    millimetres_from_points,
    physical_size_from_points,
    points_from_millimetres,
    scale_matrix_for,
    sizes_agree,
)

FINITE_MM = st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False)


def test_the_two_constants_are_the_definitions() -> None:
    assert MM_PER_INCH == 25.4
    assert POINTS_PER_INCH == 72.0


@pytest.mark.parametrize(
    ("millimetres_value", "points_value"),
    [
        (25.4, 72.0),
        (0.0, 0.0),
        (210.0, 595.2755905511812),
        (157.79475982532753, 447.29223257573156),
    ],
)
def test_conversion_table(millimetres_value: float, points_value: float) -> None:
    assert points_from_millimetres(millimetres_value) == pytest.approx(points_value)
    assert millimetres_from_points(points_value) == pytest.approx(millimetres_value)


@given(value=FINITE_MM)
def test_conversion_round_trips(value: float) -> None:
    assert millimetres_from_points(points_from_millimetres(value)) == pytest.approx(value)


@given(value=FINITE_MM)
def test_a_round_trip_never_moves_a_page_outside_the_shared_tolerance(value: float) -> None:
    # The tolerance exists because a millimetre size cannot survive a points round trip
    # exactly. This pins that the error is orders of magnitude smaller than the slack, so the
    # tolerance is absorbing float representation and not hiding a scaling bug.
    assert abs(millimetres_from_points(points_from_millimetres(value)) - value) < (
        PAGE_SIZE_TOLERANCE_MM / 100
    )


def test_physical_size_from_points_converts_both_axes() -> None:
    size = physical_size_from_points(507.29, 656.39)
    assert size.width_mm == pytest.approx(178.96063888888887)
    assert size.height_mm == pytest.approx(231.55980555555556)


@pytest.mark.parametrize(("width_pt", "height_pt"), [(0.0, 100.0), (100.0, 0.0), (-1.0, 1.0)])
def test_physical_size_from_points_refuses_a_degenerate_box(
    width_pt: float, height_pt: float
) -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        physical_size_from_points(width_pt, height_pt)


@pytest.mark.parametrize(
    ("left_mm", "right_mm", "agree"),
    [
        ((100.0, 200.0), (100.0, 200.0), True),
        ((100.0, 200.0), (100.05, 200.05), True),
        ((100.0, 200.0), (100.1, 200.1), True),
        ((100.0, 200.0), (100.2, 200.0), False),
        ((100.0, 200.0), (100.0, 200.2), False),
    ],
)
def test_sizes_agree_table(
    left_mm: tuple[float, float],
    right_mm: tuple[float, float],
    *,
    agree: bool,
) -> None:
    left = PhysicalSize(width_mm=left_mm[0], height_mm=left_mm[1])
    right = PhysicalSize(width_mm=right_mm[0], height_mm=right_mm[1])
    assert sizes_agree(left, right) is agree
    assert sizes_agree(right, left) is agree


@given(width=FINITE_MM, height=FINITE_MM)
def test_sizes_agree_is_reflexive(width: float, height: float) -> None:
    size = PhysicalSize(width_mm=width, height_mm=height)
    assert sizes_agree(size, size)


def test_a_narrower_tolerance_can_be_requested() -> None:
    left = PhysicalSize(width_mm=100.0, height_mm=200.0)
    right = PhysicalSize(width_mm=100.05, height_mm=200.0)
    assert sizes_agree(left, right)
    assert not sizes_agree(left, right, tolerance_mm=0.01)


@pytest.mark.parametrize(
    ("target", "box_pt"),
    [
        ((1620, 2096), (612.0, 792.0)),
        ((3240, 4193), (612.0, 792.0)),
        ((1, 1), (1.0, 1.0)),
        ((2808, 3634), (509.32, 679.09)),
    ],
)
def test_scale_matrix_reproduces_the_target_exactly(
    target: tuple[int, int],
    box_pt: tuple[float, float],
) -> None:
    size = PixelSize(width_px=target[0], height_px=target[1])
    zoom_x, zoom_y = scale_matrix_for(size, box_pt[0], box_pt[1])
    # This is what MuPDF computes the pixmap size from, so if the product does not land on the
    # integer target, the raster comes back a pixel off and the domain validator refuses it.
    assert math.isclose(box_pt[0] * zoom_x, target[0])
    assert math.isclose(box_pt[1] * zoom_y, target[1])


def test_scale_matrix_is_anisotropic_when_the_aspect_ratios_differ() -> None:
    # An isotropic zoom is what disagreed with fit_within in 4 of 16 measured combinations.
    zoom_x, zoom_y = scale_matrix_for(PixelSize(width_px=1620, height_px=2096), 612.0, 792.0)
    assert zoom_x != zoom_y


@pytest.mark.parametrize(("width_pt", "height_pt"), [(0.0, 10.0), (10.0, 0.0), (-5.0, 10.0)])
def test_scale_matrix_refuses_a_degenerate_page_box(width_pt: float, height_pt: float) -> None:
    with pytest.raises(ValueError, match="positive in both dimensions"):
        scale_matrix_for(PixelSize(width_px=10, height_px=10), width_pt, height_pt)
