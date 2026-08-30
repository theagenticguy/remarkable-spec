"""The single-stroke font: metrics, coverage, and the properties the letterforms depend on.

No oracle stands behind any of this -- the legacy renderer only ever read scenes, never wrote
one -- so what these tests pin is the set of facts :mod:`rmspec.render._ink_text` builds on and
cannot check for itself.

The one that matters most is the anti-double-wall property
---------------------------------------------------------
The whole design decision behind this font is that a glyph is a **centre-line**, not the outline
of a filled shape. Those two are indistinguishable in a stroke count and obvious in a picture,
so the difference is asserted numerically instead:
:func:`test_the_lowercase_o_is_one_wall_not_two` measures every sample of the ``o`` against the
bowl's own ellipse. A centre-line gives one radius; a traced outline gives two, separated by the
stem width. Get that wrong and the reply renders as hollow, double-walled letters -- which is
exactly the failure mode option (a) was rejected for.
"""

from __future__ import annotations

import itertools
import math
import string

import pytest

from rmspec.render._ink_font import (
    CAP_UNITS,
    DESCENDER_UNITS,
    DOT_RADIUS_UNITS,
    EM_UNITS,
    GLYPHS,
    MIN_SAMPLES_PER_TRACE,
    SAMPLE_STEP_UNITS,
    SUBSTITUTE,
    X_HEIGHT_UNITS,
    Arc,
    glyph_for,
    resample,
    sampled_traces,
)

#: Every printable ASCII code point, U+0020 to U+007E, derived from the standard library rather
#: than from the table under test -- otherwise a missing glyph would make the expectation shrink
#: to match it.
PRINTABLE_ASCII = frozenset(string.printable) - frozenset(string.whitespace) | {" "}

MIN_TRACE_SAMPLES = 2


def test_the_font_covers_exactly_printable_ascii() -> None:
    assert set(GLYPHS) == PRINTABLE_ASCII
    assert len(GLYPHS) == 95


def test_metrics_are_ordered_and_fit_inside_the_em() -> None:
    """Ink must fit the em, or one line's descenders collide with the next line's caps."""
    assert DESCENDER_UNITS < 0 < X_HEIGHT_UNITS < CAP_UNITS < EM_UNITS
    assert CAP_UNITS - DESCENDER_UNITS < EM_UNITS


@pytest.mark.parametrize("char", sorted(GLYPHS))
def test_every_glyph_has_a_positive_advance(char: str) -> None:
    assert GLYPHS[char].advance > 0


@pytest.mark.parametrize("char", sorted(GLYPHS))
def test_every_glyph_but_the_space_draws_something(char: str) -> None:
    traces = sampled_traces(char)
    assert (len(traces) == 0) == (char == " "), (
        f"{char!r} must draw exactly when it is not the space"
    )


@pytest.mark.parametrize("char", sorted(GLYPHS))
def test_every_trace_samples_to_at_least_two_distinct_points(char: str) -> None:
    """The invariant ``_ink_stroke`` divides by, so it is asserted rather than guarded.

    Normalising position along a trace is ``index / (len(points) - 1)``. A one-point trace would
    make that a ``ZeroDivisionError``, and a guard for a case no glyph can produce is a branch no
    test can reach -- so the guarantee lives here, over the whole font, instead.
    """
    for trace in sampled_traces(char):
        assert len(trace) >= MIN_TRACE_SAMPLES
        assert len(set(trace)) >= MIN_TRACE_SAMPLES


@pytest.mark.parametrize("char", sorted(GLYPHS))
def test_every_trace_is_sampled_at_least_as_finely_as_the_step_promises(char: str) -> None:
    for trace in sampled_traces(char):
        assert len(trace) >= MIN_SAMPLES_PER_TRACE
        for before, after in itertools.pairwise(trace):
            gap = math.hypot(after[0] - before[0], after[1] - before[1])
            assert gap <= SAMPLE_STEP_UNITS + 1e-9


@pytest.mark.parametrize("char", sorted(GLYPHS))
def test_every_glyph_stays_inside_its_advance_and_its_vertical_bounds(char: str) -> None:
    """Bounds, because overrunning either one collides with something.

    A glyph wider than its advance collides with its neighbour; one taller than the em collides
    with the line below.
    """
    slack = 0.75
    for trace in sampled_traces(char):
        for x, y in trace:
            assert -slack <= x <= GLYPHS[char].advance + slack, f"{char!r} overruns its advance"
            assert DESCENDER_UNITS - slack <= y <= CAP_UNITS + 1.5, f"{char!r} overruns the em"


def test_the_lowercase_o_is_one_wall_not_two() -> None:
    """Every sample of the ``o`` sits on one ellipse, which is what "centre-line" means.

    A traced *outline* of the same letter would put half the samples on an inner ellipse and
    half on an outer one, separated by the stem width -- normalised radii spread by something
    like 0.3 rather than the 0.02 asserted here. This is the numeric form of the argument in
    :mod:`rmspec.render._ink_font`'s docstring for choosing a single-stroke font, and it fails
    loudly if anybody swaps the data for glyph contours.
    """
    (bowl,) = GLYPHS["o"].traces
    (arc,) = bowl
    assert isinstance(arc, Arc)
    ((samples,)) = sampled_traces("o")

    radii = [math.hypot((x - arc.cx) / arc.rx, (y - arc.cy) / arc.ry) for x, y in samples]
    assert max(radii) - min(radii) < 0.02
    assert 0.98 <= min(radii) <= max(radii) <= 1.0

    centre_gap = min(math.hypot(x - arc.cx, y - arc.cy) for x, y in samples)
    assert centre_gap > min(arc.rx, arc.ry) * 0.9, "the bowl must be hollow, not filled"

    assert math.isclose(samples[0][0], samples[-1][0], abs_tol=1e-9)
    assert math.isclose(samples[0][1], samples[-1][1], abs_tol=1e-9)


def test_a_dot_is_a_closed_ring_rather_than_a_degenerate_stroke() -> None:
    """A zero-length stroke might render in SVG and vanish on the device; a ring cannot."""
    stem, dot = sampled_traces("i")
    assert len({round(x, 6) for x, _ in stem}) == 1, "the stem of an i is vertical"
    spread = max(x for x, _ in dot) - min(x for x, _ in dot)
    assert math.isclose(spread, 2 * DOT_RADIUS_UNITS, rel_tol=0.05)


def _flattening_shortfall(arc: Arc) -> float:
    """Return the furthest a flattened chord's midpoint falls inside ``arc``, in font units."""
    worst = 0.0
    for (x0, y0), (x1, y1) in itertools.pairwise(arc.points):
        mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2
        normalised = math.hypot((mid_x - arc.cx) / arc.rx, (mid_y - arc.cy) / arc.ry)
        radial = math.hypot(mid_x - arc.cx, mid_y - arc.cy)
        worst = max(worst, radial / normalised - radial)
    return worst


def test_no_arc_in_the_table_flattens_visibly_off_its_ellipse() -> None:
    """The prose claim in ``FLATTEN_STEP_UNITS``, measured, because it drifts with the data.

    Flattening error grows as the radius shrinks, so adding one small bowl is what would break
    this -- not raising the step. The bound to beat is the width of the drawn line, which at a
    4 mm em is about 1.7 font units.
    """
    arcs = [
        segment
        for glyph in [*GLYPHS.values(), SUBSTITUTE]
        for trace in glyph.traces
        for segment in trace
        if isinstance(segment, Arc)
    ]
    assert len(arcs) == 32
    letter_sized = [arc for arc in arcs if arc.rx != DOT_RADIUS_UNITS]
    assert len(letter_sized) == 24
    assert max(_flattening_shortfall(arc) for arc in letter_sized) < 0.01
    assert max(_flattening_shortfall(arc) for arc in arcs) < 0.03


def test_arcs_flatten_to_a_polygon_that_hugs_the_ellipse() -> None:
    arc = Arc(cx=0.0, cy=0.0, rx=4.0, ry=4.0, start_deg=0.0, end_deg=360.0)
    points = arc.points
    assert points[0] == pytest.approx((4.0, 0.0))
    assert points[-1] == pytest.approx((4.0, 0.0), abs=1e-9)
    for x, y in points:
        assert math.isclose(math.hypot(x, y), 4.0, rel_tol=1e-12)


def test_a_clockwise_sweep_is_expressed_by_an_end_below_the_start() -> None:
    arc = Arc(cx=0.0, cy=0.0, rx=1.0, ry=1.0, start_deg=90.0, end_deg=-90.0)
    middle = arc.points[len(arc.points) // 2]
    assert middle[0] > 0, "sweeping 90 to -90 passes through the positive x axis"


def test_an_unknown_character_gets_the_substitute_glyph() -> None:
    assert glyph_for("é") is SUBSTITUTE
    assert glyph_for("a") is GLYPHS["a"]
    assert len(sampled_traces("—")) == len(SUBSTITUTE.traces)


def test_the_substitute_is_a_visible_box_with_a_diagonal_through_it() -> None:
    """It has to be unmistakably not-a-letter, or a reader reads past the hole in the sentence."""
    outline, diagonal = sampled_traces("\U0001f600")
    assert math.isclose(outline[0][0], outline[-1][0], abs_tol=1e-9), "the box closes"
    assert math.isclose(outline[0][1], outline[-1][1], abs_tol=1e-9)
    xs = [x for x, _ in outline]
    ys = [y for _, y in outline]
    assert max(xs) - min(xs) > 5, "the box is wide enough to see"
    assert max(ys) - min(ys) > 10
    assert diagonal[0] != diagonal[-1]
    assert (diagonal[-1][0] - diagonal[0][0]) * (diagonal[-1][1] - diagonal[0][1]) != 0


def test_resample_returns_nothing_for_nothing() -> None:
    assert resample((), step=1.0) == ()


def test_resample_keeps_both_endpoints_and_spaces_the_rest_evenly() -> None:
    walked = resample(((0.0, 0.0), (10.0, 0.0)), step=2.5)
    assert walked == (
        (0.0, 0.0),
        (2.5, 0.0),
        (5.0, 0.0),
        (7.5, 0.0),
        (10.0, 0.0),
    )


def test_resample_appends_the_final_point_when_the_step_does_not_divide_evenly() -> None:
    walked = resample(((0.0, 0.0), (2.5, 0.0)), step=1.0)
    assert walked == ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.5, 0.0))


def test_resample_carries_leftover_distance_across_a_corner() -> None:
    """Spacing is measured along the path, not restarted at every typed corner."""
    walked = resample(((0.0, 0.0), (1.5, 0.0), (1.5, 1.5)), step=1.0)
    assert walked == ((0.0, 0.0), (1.0, 0.0), (1.5, 0.5), (1.5, 1.5))


def test_resample_skips_a_zero_length_step_instead_of_dividing_by_it() -> None:
    walked = resample(((0.0, 0.0), (0.0, 0.0), (2.0, 0.0)), step=1.0)
    assert walked == ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0))
