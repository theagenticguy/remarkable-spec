"""The centre-origin correction and the padding recurrence.

Two things here are worth more than the rest of the file. ``test_padding_is_a_gated_recurrence``
pins the exact counterexample that distinguishes the relocated rule from a maximum over stroke
extents, and ``test_x_shift_agrees_with_the_domain_screen_property`` pins the two spellings of
``x_shift`` together so a third screen cannot break one of them quietly.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from render_builders import layer, point, stroke

from rmspec.domain.models import PAPER_PRO_SCREEN, RM2_SCREEN, ScreenSpec
from rmspec.render import LEGACY_MIN_PADDING_MM
from rmspec.render._layout import PageLayout, _padding_from_ink, layout_for
from rmspec.render._units import mm_to_points, points_per_pixel, points_to_mm

LEGACY_MIN_PADDING_PT = 30.0
SCREENS = [RM2_SCREEN, PAPER_PRO_SCREEN]


def test_legacy_min_padding_round_trips_to_exactly_thirty_points() -> None:
    """The whole geometry hangs off this one conversion, so it is asserted exactly."""
    assert mm_to_points(LEGACY_MIN_PADDING_MM) == LEGACY_MIN_PADDING_PT


def test_the_other_operation_order_does_not_round_trip() -> None:
    """Why ``mm * 72 / 25.4`` is written in that order and nowhere else."""
    assert LEGACY_MIN_PADDING_MM / 25.4 * 72.0 != LEGACY_MIN_PADDING_PT
    assert LEGACY_MIN_PADDING_MM * (72.0 / 25.4) != LEGACY_MIN_PADDING_PT


@pytest.mark.parametrize("screen", SCREENS)
def test_x_shift_is_half_the_viewport_width(screen: ScreenSpec) -> None:
    layout = layout_for((), screen=screen, min_padding_pt=0.0)
    assert layout.x_shift == layout.viewport_width / 2


@pytest.mark.parametrize("screen", SCREENS)
def test_x_shift_agrees_with_the_domain_screen_property(screen: ScreenSpec) -> None:
    """Points-from-viewport and screen-units-times-scale are the same float here.

    Halving is exact for every finite float, so ``(w * s) / 2 == (w / 2) * s`` for any DPI.
    Asserted anyway, because the two units are what a reader gets wrong and the failure would
    otherwise be a page drawn half a margin off centre.
    """
    layout = layout_for((), screen=screen, min_padding_pt=0.0)
    assert layout.x_shift == screen.x_shift * points_per_pixel(screen)


@pytest.mark.parametrize("screen", SCREENS)
def test_scene_origin_lands_at_the_centre_of_the_page(screen: ScreenSpec) -> None:
    layout = layout_for((), screen=screen, min_padding_pt=0.0)
    assert 0.0 * layout.scale + layout.x_shift == layout.viewport_width / 2


@pytest.mark.parametrize("screen", SCREENS)
def test_left_edge_of_the_scene_lands_at_zero(screen: ScreenSpec) -> None:
    layout = layout_for((), screen=screen, min_padding_pt=0.0)
    left_edge = -(screen.width / 2)
    assert left_edge * layout.scale + layout.x_shift == 0.0


def test_a_page_with_no_ink_gets_exactly_the_minimum_margin() -> None:
    layout = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)
    assert (layout.pad_left, layout.pad_top, layout.pad_right, layout.pad_bottom) == (
        30.0,
        30.0,
        30.0,
        30.0,
    )
    assert layout.content_overflowed is False
    assert layout.expanded is True


def test_zero_padding_makes_the_document_exactly_the_screen() -> None:
    layout = layout_for((), screen=RM2_SCREEN, min_padding_pt=0.0)
    assert layout.total_width == layout.viewport_width
    assert layout.total_height == layout.viewport_height
    assert layout.expanded is False


def test_padding_is_a_gated_recurrence_not_a_max_over_extents() -> None:
    """The single most likely way to break all thirty oracle hashes.

    Each update is gated on the *running* pad, so a later, larger extent that exceeds the old
    one by less than ``min_pad`` does not retrigger. The three numbers below differ, which is
    why ``PageContent.bounding_box`` must not be substituted here and why point order matters.
    """
    near_then_far = _padding_from_ink(
        (layer(stroke(point(-100.0, 0.0), point(-110.0, 0.0))),),
        scale=1.0,
        x_shift=0.0,
        viewport_width=400.0,
        viewport_height=400.0,
        min_padding_pt=30.0,
    )
    far_then_near = _padding_from_ink(
        (layer(stroke(point(-110.0, 0.0), point(-100.0, 0.0))),),
        scale=1.0,
        x_shift=0.0,
        viewport_width=400.0,
        viewport_height=400.0,
        min_padding_pt=30.0,
    )
    max_over_extents = 110.0 + 30.0

    assert near_then_far[0] == 130.0
    assert far_then_near[0] == 140.0
    assert max_over_extents == 140.0
    assert near_then_far[0] != max_over_extents


@pytest.mark.parametrize(
    ("x", "y", "side", "expected"),
    [
        (-100.0, 0.0, "pad_left", 130.0),
        (500.0, 0.0, "pad_right", 130.0),
        (0.0, -100.0, "pad_top", 130.0),
        (0.0, 500.0, "pad_bottom", 130.0),
    ],
)
def test_one_overflow_per_side(x: float, y: float, side: str, expected: float) -> None:
    pads = _padding_from_ink(
        (layer(stroke(point(x, y), point(x, y))),),
        scale=1.0,
        x_shift=0.0,
        viewport_width=400.0,
        viewport_height=400.0,
        min_padding_pt=30.0,
    )
    named = dict(zip(("pad_left", "pad_top", "pad_right", "pad_bottom"), pads, strict=True))
    assert named[side] == expected
    assert [value for key, value in named.items() if key != side] == [30.0, 30.0, 30.0]


def test_hidden_layers_widen_nothing() -> None:
    hidden = layer(stroke(point(-9999.0, -9999.0), point(9999.0, 9999.0)), visible=False)
    layout = layout_for((hidden,), screen=RM2_SCREEN, min_padding_pt=30.0)
    assert layout.pad_left == 30.0
    assert layout.content_overflowed is False


def test_a_tap_widens_nothing_because_it_has_no_samples() -> None:
    tap = layer(stroke())
    layout = layout_for((tap,), screen=RM2_SCREEN, min_padding_pt=30.0)
    assert layout.content_overflowed is False


def test_a_single_sample_stroke_still_widens_the_padding() -> None:
    """It draws no line, but the legacy scan visited it, so the viewBox moves."""
    single = layer(stroke(point(-9999.0, 0.0)))
    layout = layout_for((single,), screen=RM2_SCREEN, min_padding_pt=30.0)
    assert layout.pad_left > 30.0
    assert layout.content_overflowed is True


def test_an_underlay_wider_than_the_screen_widens_the_viewport() -> None:
    bare = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)
    wide = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=30.0,
        underlay_size_pt=(bare.viewport_width + 100.0, bare.viewport_height),
    )
    assert wide.pad_right == bare.viewport_width + 100.0 - bare.viewport_width + 30.0
    assert wide.pad_bottom == 30.0


def test_an_underlay_taller_than_the_screen_widens_the_viewport() -> None:
    bare = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)
    tall = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=30.0,
        underlay_size_pt=(bare.viewport_width, bare.viewport_height + 250.0),
    )
    assert tall.pad_bottom == bare.viewport_height + 250.0 - bare.viewport_height + 30.0
    assert tall.pad_right == 30.0


def test_an_underlay_inside_the_page_box_changes_nothing() -> None:
    bare = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)
    fitted = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=30.0,
        underlay_size_pt=(bare.viewport_width - 10.0, bare.viewport_height - 10.0),
    )
    assert (fitted.pad_right, fitted.pad_bottom) == (bare.pad_right, bare.pad_bottom)


def test_totals_and_origins_are_summed_in_the_legacy_order() -> None:
    layout = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)
    assert layout.total_width == layout.pad_left + layout.viewport_width + layout.pad_right
    assert layout.total_height == layout.pad_top + layout.viewport_height + layout.pad_bottom
    assert layout.origin_x == -layout.pad_left
    assert layout.origin_y == -layout.pad_top


@pytest.mark.parametrize(
    ("left", "top", "right", "bottom"),
    [
        (40.0, 30.0, 30.0, 30.0),
        (30.0, 40.0, 30.0, 30.0),
        (30.0, 30.0, 40.0, 30.0),
        (30.0, 30.0, 30.0, 40.0),
    ],
)
def test_content_overflowed_reports_any_grown_side(
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> None:
    layout = PageLayout(
        scale=1.0,
        x_shift=50.0,
        viewport_width=100.0,
        viewport_height=100.0,
        pad_left=left,
        pad_top=top,
        pad_right=right,
        pad_bottom=bottom,
        min_padding=30.0,
    )
    assert layout.content_overflowed is True


@given(
    x=st.floats(min_value=-5000.0, max_value=5000.0, allow_nan=False),
    y=st.floats(min_value=-5000.0, max_value=5000.0, allow_nan=False),
)
def test_every_sample_lands_inside_the_document(x: float, y: float) -> None:
    """The property the padding scan exists for: no ink is ever clipped."""
    layout = layout_for(
        (layer(stroke(point(x, y), point(x, y))),),
        screen=RM2_SCREEN,
        min_padding_pt=30.0,
    )
    px = x * layout.scale + layout.x_shift
    py = y * layout.scale
    assert layout.origin_x <= px <= layout.origin_x + layout.total_width
    assert layout.origin_y <= py <= layout.origin_y + layout.total_height


def test_points_to_mm_inverts_mm_to_points() -> None:
    assert points_to_mm(mm_to_points(LEGACY_MIN_PADDING_MM)) == LEGACY_MIN_PADDING_MM


def test_text_extents_widen_the_margins_the_way_ink_does() -> None:
    """A block off the left and above the top grows both of those pads."""
    bare = layout_for((), screen=RM2_SCREEN, min_padding_pt=LEGACY_MIN_PADDING_PT)
    widened = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
        text_extents=((-2000.0, -400.0, -1000.0, -200.0),),
    )
    assert bare.pad_left == LEGACY_MIN_PADDING_PT
    assert bare.pad_top == LEGACY_MIN_PADDING_PT
    assert widened.pad_left == -(-2000.0 * bare.scale + bare.x_shift) + LEGACY_MIN_PADDING_PT
    assert widened.pad_top == -(-400.0 * bare.scale) + LEGACY_MIN_PADDING_PT
    assert widened.pad_right == LEGACY_MIN_PADDING_PT
    assert widened.pad_bottom == LEGACY_MIN_PADDING_PT


def test_text_extents_widen_the_far_margins_too() -> None:
    widened = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
        text_extents=((0.0, 0.0, 4000.0, 5000.0),),
    )
    assert widened.pad_right > LEGACY_MIN_PADDING_PT
    assert widened.pad_bottom > LEGACY_MIN_PADDING_PT
    assert widened.pad_left == LEGACY_MIN_PADDING_PT
    assert widened.pad_top == LEGACY_MIN_PADDING_PT


def test_the_text_pass_is_order_independent_where_the_ink_scan_is_not() -> None:
    """Deliberately *not* the gated recurrence: no oracle hash encodes this pass.

    The counterexample ``test_padding_is_a_gated_recurrence`` pins for ink -- ``[-100, -110]``
    giving 130.0 and the reverse giving 140.0 -- must not reproduce here, so two extents in
    either order have to give the same margins.
    """
    first = (-3000.0, 0.0, 0.0, 0.0)
    second = (-3100.0, 0.0, 0.0, 0.0)
    forward = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
        text_extents=(first, second),
    )
    backward = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
        text_extents=(second, first),
    )
    assert forward.pad_left == backward.pad_left
    assert forward.pad_left == -(-3100.0 * forward.scale + forward.x_shift) + (
        LEGACY_MIN_PADDING_PT
    )


def test_an_unprintably_wider_underlay_does_not_grow_the_pad() -> None:
    """The mm round trip is inexact, so a sub-printable excess must not add a whole margin.

    ``mm_to_points(points_to_mm(x)) != x`` for about a fifth of the point values in 1..3000, one
    ulp out. Comparing an underlay's converted width against the page box with a bare ``>``
    therefore lets a rounding artifact expand the viewport by the whole minimum margin.
    """
    bare = layout_for((), screen=RM2_SCREEN, min_padding_pt=LEGACY_MIN_PADDING_PT)
    barely_wider = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
        underlay_size_pt=(
            bare.viewport_width + bare.pad_right + 0.004,
            bare.viewport_height + bare.pad_bottom + 0.004,
        ),
    )
    assert barely_wider.pad_right == bare.pad_right
    assert barely_wider.pad_bottom == bare.pad_bottom


def test_a_printably_wider_underlay_still_grows_the_pad() -> None:
    """The tolerance is half a printed digit, not a licence to clip a real underlay."""
    bare = layout_for((), screen=RM2_SCREEN, min_padding_pt=LEGACY_MIN_PADDING_PT)
    wider = layout_for(
        (),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
        underlay_size_pt=(bare.viewport_width + 100.0, bare.viewport_height + 100.0),
    )
    assert wider.pad_right == pytest.approx(100.0 + LEGACY_MIN_PADDING_PT)
    assert wider.pad_bottom == pytest.approx(100.0 + LEGACY_MIN_PADDING_PT)


def test_a_non_finite_sample_is_skipped_rather_than_making_the_pads_infinite() -> None:
    """``Point.x`` is an unconstrained float, and a malformed scene file can carry a NaN.

    Letting one into the comparisons yields ``pad_right = inf``, a ``viewBox`` of
    ``"-30.00 -30.00 inf inf"`` and a ``PhysicalSize`` of infinity for the export slice to size
    a PDF page from.
    """
    clean = layout_for(
        (layer(stroke(point(10.0, 20.0), point(30.0, 40.0))),),
        screen=RM2_SCREEN,
        min_padding_pt=LEGACY_MIN_PADDING_PT,
    )
    for poison in (float("inf"), float("-inf"), float("nan")):
        polluted = layout_for(
            (
                layer(
                    stroke(point(10.0, 20.0), point(30.0, 40.0)),
                    stroke(point(poison, 0.0), point(0.0, poison)),
                ),
            ),
            screen=RM2_SCREEN,
            min_padding_pt=LEGACY_MIN_PADDING_PT,
        )
        assert polluted == clean, f"{poison} moved the layout"
