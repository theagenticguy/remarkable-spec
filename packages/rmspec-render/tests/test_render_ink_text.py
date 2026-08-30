"""Text into ink, checked through this package's own renderer rather than by eye.

Why the assertions here are geometric and not "it produced output"
-----------------------------------------------------------------
A reply that renders as a valid ``<svg>`` full of hairlines, or lands two hundred points off the
page, or draws a hollow double-walled alphabet, passes every "did it return strokes" check there
is. So the tests below measure the picture: where the ink actually lands in millimetres, how wide
the drawn line actually is, and whether the width varies along a stroke at all. The identity in
:func:`test_ink_lands_at_the_millimetre_the_caller_named` is the strongest of them --
``x`` in the rendered markup comes out at exactly ``mm_to_points(requested_mm)``, which is only
true if the millimetre scale, the screen DPI and the centre-origin correction are all right at
once. Get any one of them wrong and it moves.

There is no oracle behind this file: the legacy renderer never wrote a scene, only read one. The
differential manifest is untouched by everything here, because nothing here is reachable from
``PageRenderer.render``.
"""

from __future__ import annotations

import math

import pytest
from render_builders import LEGACY_STYLE, layer, page, parse_svg
from render_contract import assert_page_renderer_contract

from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    PenColor,
    PenType,
    Stroke,
)
from rmspec.render import (
    INK_TEXT_CHARACTERS,
    InkText,
    InkTextStyle,
    SvgPageRenderer,
    text_to_ink,
)
from rmspec.render._ink_font import CAP_UNITS, EM_UNITS, GLYPHS, X_HEIGHT_UNITS
from rmspec.render._ink_text import wrap_ink_text
from rmspec.render._svg import SVG_NAMESPACE
from rmspec.render._units import mm_to_pixels, mm_to_points, pixels_to_mm

RENDERER = SvgPageRenderer()
LINE_TAG = f"{{{SVG_NAMESPACE}}}line"

#: A legible reply size: a 10 mm em is a 7 mm cap height, which makes the arithmetic in the
#: placement tests exact halves of a millimetre and keeps them readable.
BIG = InkTextStyle(em_mm=10.0, line_height=1.5, color=PenColor.BLACK, thickness_scale=2.0)

#: What a real reply would be written at.
REPLY = InkTextStyle(em_mm=4.5, line_height=1.4, color=PenColor.BLUE, thickness_scale=2.0)

NO_WRAP = math.inf


def ink(
    text: str,
    *,
    style: InkTextStyle = BIG,
    left_mm: float = 20.0,
    top_mm: float = 30.0,
    width_mm: float = NO_WRAP,
) -> InkText:
    """Write ``text`` on a Paper Pro, varying only what a test cares about."""
    return text_to_ink(
        text,
        screen=PAPER_PRO_SCREEN,
        style=style,
        left_mm=left_mm,
        top_mm=top_mm,
        width_mm=width_mm,
    )


def drawn_lines(strokes: tuple[Stroke, ...]) -> list[dict[str, str]]:
    """Render ``strokes`` as a page and return every ``<line>`` element's attributes."""
    rendered = assert_page_renderer_contract(
        RENDERER,
        page=page(layer(*strokes)),
        screen=PAPER_PRO_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )
    assert "nan" not in rendered.svg, "markup carrying nan is markup no rasterizer accepts"
    return [element.attrib for element in parse_svg(rendered.svg).iter(LINE_TAG)]


def ink_box(attribs: list[dict[str, str]]) -> tuple[float, float, float, float]:
    """Return ``(min_x, min_y, max_x, max_y)`` of the drawn ink, in points."""
    xs = [float(a[key]) for a in attribs for key in ("x1", "x2")]
    ys = [float(a[key]) for a in attribs for key in ("y1", "y2")]
    return (min(xs), min(ys), max(xs), max(ys))


# ──────────────────────── placement, in real units ────────────────────────


def test_ink_lands_at_the_millimetre_the_caller_named() -> None:
    """The one identity that proves scale, DPI and the centre-origin shift are all right.

    Screen units are ``mm * dpi / 25.4`` and the renderer's own scale is ``72 / dpi``, so a
    coordinate that survives both comes out at ``mm * 72 / 25.4`` -- the millimetre position in
    points, with the DPI cancelled and ``x_shift`` exactly undone. A sign error on the
    centre-origin correction moves this by half a page; a DPI mistake moves it by a few percent.
    """
    written = ink("H", left_mm=20.0, top_mm=30.0)
    unit_mm = BIG.em_mm / EM_UNITS
    baseline_mm = 30.0 + BIG.em_mm
    left, top, right, bottom = ink_box(drawn_lines(written.strokes))

    assert left == pytest.approx(mm_to_points(20.0 + 2.0 * unit_mm), abs=0.01)
    assert right == pytest.approx(mm_to_points(20.0 + 10.0 * unit_mm), abs=0.01)
    assert top == pytest.approx(mm_to_points(baseline_mm - CAP_UNITS * unit_mm), abs=0.01)
    assert bottom == pytest.approx(mm_to_points(baseline_mm), abs=0.01)


def test_the_reported_extent_is_the_same_box_the_markup_draws() -> None:
    """``extent_mm`` is what a caller checks the reply fits by, so it has to be the real box."""
    written = ink("Hxp")
    drawn = ink_box(drawn_lines(written.strokes))
    in_mm = tuple(value * 25.4 / 72 for value in drawn)
    for reported, measured in zip(written.extent_mm, in_mm, strict=True):
        assert reported == pytest.approx(measured, abs=0.01)


def test_scene_x_is_centre_origin_so_a_left_hand_placement_is_negative() -> None:
    """The package's load-bearing invariant, from the writing side."""
    written = ink("l", left_mm=10.0)
    xs = [point.x for stroke in written.strokes for point in stroke.points]
    assert max(xs) < 0, "10 mm from the left edge is well left of the page centre"
    assert min(xs) == pytest.approx(
        mm_to_pixels(10.0, screen=PAPER_PRO_SCREEN)
        + 2.5 * (BIG.em_mm / EM_UNITS) * PAPER_PRO_SCREEN.dpi / 25.4
        - PAPER_PRO_SCREEN.x_shift,
        abs=0.01,
    )


def test_the_extent_reaches_below_the_baseline_for_a_descender() -> None:
    upright = ink("n")
    hanging = ink("p")
    assert hanging.extent_mm[3] > upright.extent_mm[3]
    unit_mm = BIG.em_mm / EM_UNITS
    assert upright.extent_mm[3] == pytest.approx(30.0 + BIG.em_mm, abs=0.02)
    assert hanging.extent_mm[3] == pytest.approx(30.0 + BIG.em_mm + 4.0 * unit_mm, abs=0.02)
    assert upright.extent_mm[1] == pytest.approx(
        30.0 + BIG.em_mm - X_HEIGHT_UNITS * unit_mm, abs=0.02
    )


def test_baselines_step_by_the_em_times_the_line_height() -> None:
    written = ink("l\nl\nl")
    baselines = sorted(max(p.y for p in stroke.points) for stroke in written.strokes)
    step = mm_to_pixels(BIG.em_mm * BIG.line_height, screen=PAPER_PRO_SCREEN)
    assert baselines[1] - baselines[0] == pytest.approx(step, abs=0.01)
    assert baselines[2] - baselines[1] == pytest.approx(step, abs=0.01)


def test_the_first_baseline_sits_one_em_below_the_declared_top() -> None:
    """The same convention ``append_text_block`` uses, so "put the box here" means one thing."""
    written = ink("l", top_mm=30.0)
    baseline_px = max(point.y for stroke in written.strokes for point in stroke.points)
    assert baseline_px == pytest.approx(
        mm_to_pixels(30.0 + BIG.em_mm, screen=PAPER_PRO_SCREEN), abs=0.01
    )


def test_the_same_text_scales_with_the_em() -> None:
    small = ink("Hello", style=InkTextStyle(4.0, 1.4, PenColor.BLACK, 2.0))
    large = ink("Hello", style=InkTextStyle(8.0, 1.4, PenColor.BLACK, 2.0))
    small_width = small.extent_mm[2] - small.extent_mm[0]
    large_width = large.extent_mm[2] - large.extent_mm[0]
    assert large_width == pytest.approx(small_width * 2, rel=1e-9)


def test_the_screen_decides_the_scene_unit() -> None:
    """Millimetres are physical; screen units are not, so the same request differs per screen."""
    on_pro = text_to_ink(
        "l", screen=PAPER_PRO_SCREEN, style=BIG, left_mm=20.0, top_mm=30.0, width_mm=NO_WRAP
    )
    on_rm2 = text_to_ink(
        "l", screen=RM2_SCREEN, style=BIG, left_mm=20.0, top_mm=30.0, width_mm=NO_WRAP
    )
    pro_height = max(p.y for p in on_pro.strokes[0].points) - min(
        p.y for p in on_pro.strokes[0].points
    )
    rm2_height = max(p.y for p in on_rm2.strokes[0].points) - min(
        p.y for p in on_rm2.strokes[0].points
    )
    assert pro_height > rm2_height
    assert pro_height / rm2_height == pytest.approx(
        PAPER_PRO_SCREEN.dpi / RM2_SCREEN.dpi, rel=1e-9
    )


# ──────────────────────── it looks like a pen made it ────────────────────────


def test_the_drawn_line_is_a_pen_weight_and_not_a_hairline_or_a_slab() -> None:
    """A reply the tablet renders as a 0.02 mm scratch is as useless as one it does not render."""
    widths = [float(a["stroke-width"]) for a in drawn_lines(ink("Reply noted.").strokes)]
    in_mm = [width * 25.4 / 72 for width in widths]
    assert min(in_mm) > 0.2, "a hairline is not ink"
    assert max(in_mm) < 0.45, "a ballpoint does not lay down half a millimetre"


def test_the_width_tapers_along_a_stroke_instead_of_being_constant() -> None:
    """The whole difference between "written" and "printed", measured.

    A fineliner would put every segment of this stem at one width. The ballpoint reads the
    pressure and speed envelopes, so the middle of the stroke is meaningfully fatter than its
    ends -- and the ratio is pinned so that flattening either envelope fails here.
    """
    stem = ink("l").strokes[0]
    widths = [float(a["stroke-width"]) for a in drawn_lines((stem,))]
    assert len(set(widths)) > 3, "a constant width is a printed letter, not a written one"
    assert 1.25 < max(widths) / min(widths) < 1.7


def test_pressure_rises_off_the_entry_and_falls_back_at_the_lift() -> None:
    pressures = [point.pressure for point in ink("l").strokes[0].points]
    assert pressures[0] == pressures[-1] < max(pressures)
    assert max(pressures) == 255
    assert pressures[len(pressures) // 2] == 255
    assert pressures == sorted(pressures[: len(pressures) // 2]) + sorted(
        pressures[len(pressures) // 2 :], reverse=True
    )


def test_speed_peaks_in_the_middle_of_a_stroke_and_is_zero_at_both_ends() -> None:
    speeds = [point.speed for point in ink("l").strokes[0].points]
    assert speeds[0] == speeds[-1] == 0
    assert max(speeds) == 100
    assert speeds[len(speeds) // 2] > speeds[1]


def test_every_stroke_is_a_ballpoint_carrying_the_requested_colour_and_slider() -> None:
    written = ink("Ok!", style=REPLY)
    assert written.strokes
    for stroke in written.strokes:
        assert stroke.pen is PenType.BALLPOINT_1
        assert stroke.color is PenColor.BLUE
        assert stroke.thickness_scale == REPLY.thickness_scale
        assert stroke.color_override is None


def test_one_stroke_per_pen_down_rather_than_one_per_glyph() -> None:
    """An ``i`` is a stem and a dot; welding them into one stroke would draw a line between."""
    assert len(ink("i").strokes) == 2
    assert len(ink("l").strokes) == 1
    assert len(ink("H").strokes) == 3


# ──────────────────────── layout ────────────────────────


def test_a_space_advances_without_drawing() -> None:
    assert ink(" ").strokes == ()
    assert len(ink("l l").strokes) == 2
    spread = ink("l l")
    xs = sorted(point.x for stroke in spread.strokes for point in stroke.points)
    gap_units = (xs[-1] - xs[0]) / mm_to_pixels(BIG.em_mm / EM_UNITS, screen=PAPER_PRO_SCREEN)
    assert gap_units == pytest.approx(GLYPHS["l"].advance + GLYPHS[" "].advance, abs=0.01)


def test_lines_break_at_the_requested_width() -> None:
    body = "one two three four five six seven eight nine ten"
    narrow = ink(body, style=REPLY, left_mm=20.0, width_mm=40.0)
    assert len(narrow.lines) > 1
    assert " ".join(narrow.lines) == body, "wrapping must not lose or reorder a word"
    assert narrow.extent_mm[2] - narrow.extent_mm[0] <= 40.0
    assert narrow.extent_mm[0] >= 20.0
    wide = ink(body, style=REPLY, left_mm=20.0, width_mm=200.0)
    assert wide.lines == (body,)


def test_a_word_wider_than_the_width_is_kept_whole_rather_than_hyphenated() -> None:
    written = ink("antidisestablishmentarianism", style=REPLY, width_mm=5.0)
    assert written.lines == ("antidisestablishmentarianism",)
    assert written.extent_mm[2] - written.extent_mm[0] > 5.0


def test_a_non_finite_width_means_do_not_wrap() -> None:
    body = "a much longer line than any sane page could hold at this size"
    assert ink(body, width_mm=math.inf).lines == (body,)
    assert ink(body, width_mm=math.nan).lines == (body,)


def test_newlines_are_paragraph_breaks_and_blank_lines_draw_nothing() -> None:
    assert ink("one\ntwo").lines == ("one", "two")
    assert ink("\n\none\n\n\ntwo\n\n").lines == ("one", "two")


def test_whitespace_normalises_so_carriage_returns_and_tabs_are_not_boxes() -> None:
    written = ink("first\r\nsecond\tthird   fourth")
    assert written.lines == ("first", "second third fourth")
    assert written.substituted == ()


def test_text_with_nothing_to_draw_yields_no_ink_and_a_degenerate_extent() -> None:
    for blank in ("", "   ", "\n", " \t\n "):
        written = ink(blank, left_mm=12.0, top_mm=34.0)
        assert written.strokes == ()
        assert written.lines == ()
        assert written.extent_mm == (12.0, 34.0, 12.0, 34.0)


def test_wrap_measures_real_advances_rather_than_counting_characters() -> None:
    """The improvement over ``_text.wrap_text``, which has no metrics and estimates at half an em.

    ``iiii`` and ``mmmm`` are the same four characters and nowhere near the same width, so a
    width that holds one and not the other can only be honoured by a measurement.
    """
    limit = GLYPHS["i"].advance * 4 + GLYPHS[" "].advance + 1.0
    assert wrap_ink_text("iiii iiii", width_units=limit * 2 + 10) == ("iiii iiii",)
    assert wrap_ink_text("mmmm mmmm", width_units=limit) == ("mmmm", "mmmm")


# ──────────────────────── characters this font does not have ────────────────────────


def test_the_supported_set_is_printable_ascii_and_is_exported() -> None:
    assert frozenset(chr(code) for code in range(0x20, 0x7F)) == INK_TEXT_CHARACTERS
    assert len(INK_TEXT_CHARACTERS) == 95


def test_an_unsupported_character_is_drawn_as_a_box_and_reported_never_dropped() -> None:
    """The failure this behaviour exists to prevent is a sentence with an invisible hole in it."""
    written = ink("cost — £5")
    assert written.substituted == ("—", "£")
    assert written.lines == ("cost — £5",)

    box_traces = 2
    plain = ink("cost - 5")
    assert len(written.strokes) > len(plain.strokes)

    boxes_only = ink("——")
    assert len(boxes_only.strokes) == 2 * box_traces


def test_substituted_characters_are_distinct_and_in_first_appearance_order() -> None:
    assert ink("é—é—ü").substituted == ("é", "—", "ü")


def test_a_substituted_character_still_advances_the_pen() -> None:
    """A zero-width substitute would overlap its neighbours and destroy the rest of the line."""
    with_box = ink("aéa")
    without = ink("aa")
    assert with_box.extent_mm[2] - with_box.extent_mm[0] > (
        without.extent_mm[2] - without.extent_mm[0]
    )


# ──────────────────────── the arguments a reply cannot land without ────────────────────────


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_a_size_that_cannot_produce_placeable_ink_is_refused_at_construction(bad: float) -> None:
    with pytest.raises(ValueError, match="em_mm"):
        InkTextStyle(em_mm=bad, line_height=1.4, color=PenColor.BLACK, thickness_scale=2.0)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_a_line_height_that_cannot_step_a_baseline_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="line_height"):
        InkTextStyle(em_mm=4.0, line_height=bad, color=PenColor.BLACK, thickness_scale=2.0)


@pytest.mark.parametrize("bad", [-1.0, math.inf, math.nan])
def test_a_slider_value_the_domain_would_reject_is_refused_here_first(bad: float) -> None:
    with pytest.raises(ValueError, match="thickness_scale"):
        InkTextStyle(em_mm=4.0, line_height=1.4, color=PenColor.BLACK, thickness_scale=bad)


def test_a_zero_slider_value_is_accepted_because_the_domain_accepts_it() -> None:
    assert InkTextStyle(4.0, 1.4, PenColor.BLACK, 0.0).thickness_scale == 0.0


@pytest.mark.parametrize(
    ("left_mm", "top_mm"),
    [(math.nan, 10.0), (10.0, math.nan), (math.inf, 10.0), (10.0, -math.inf)],
)
def test_a_placement_that_is_not_a_finite_number_is_refused(left_mm: float, top_mm: float) -> None:
    """Better a ``ValueError`` at the call site than ``nan`` coordinates inside a scene file."""
    with pytest.raises(ValueError, match="land somewhere"):
        ink("hello", left_mm=left_mm, top_mm=top_mm)


def test_a_negative_placement_is_allowed_because_the_viewport_grows_for_it() -> None:
    written = ink("l", left_mm=-40.0, top_mm=-40.0)
    assert written.strokes
    assert written.extent_mm[0] < 0


# ──────────────────────── the millimetre-to-scene-unit conversions ────────────────────────


def test_mm_to_pixels_round_trips_through_pixels_to_mm() -> None:
    for value in (0.0, 1.0, 30.0, 210.0):
        there = mm_to_pixels(value, screen=PAPER_PRO_SCREEN)
        assert pixels_to_mm(there, screen=PAPER_PRO_SCREEN) == pytest.approx(value, abs=1e-12)


def test_a_millimetre_in_scene_units_times_the_render_scale_is_a_millimetre_in_points() -> None:
    """Why ink placed in millimetres lands at the millimetre in the markup: the DPI cancels."""
    for screen in (PAPER_PRO_SCREEN, RM2_SCREEN):
        scale = 72.0 / screen.dpi
        assert mm_to_pixels(37.0, screen=screen) * scale == pytest.approx(
            mm_to_points(37.0), abs=1e-9
        )


# ──────────────────────── the whole reply, previewed ────────────────────────


def test_a_multi_line_reply_previews_through_the_page_renderer_contract() -> None:
    """The only way to see a reply without spending a write on somebody's tablet."""
    written = ink(
        "Checked page 3 against the sync log:\nthe 09:41 run is the one that stuck.",
        style=REPLY,
        left_mm=15.0,
        top_mm=200.0,
        width_mm=120.0,
    )
    assert len(written.lines) == 2
    subject = page(layer(*written.strokes))
    rendered = assert_page_renderer_contract(
        RENDERER,
        page=subject,
        screen=PAPER_PRO_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )
    assert rendered.stroke_count == len(written.strokes)
    drawn = [element.attrib for element in parse_svg(rendered.svg).iter(LINE_TAG)]
    assert len(drawn) == sum(len(stroke.points) - 1 for stroke in written.strokes)

    inks = {attrib["stroke"] for attrib in drawn}
    full_pressure = EXPORT_PALETTE.rgb(PenColor.BLUE)
    assert f"rgb({full_pressure.r},{full_pressure.g},{full_pressure.b})" in inks
    assert len(inks) > 1, (
        "the ballpoint darkens its ink under pressure, so a reply drawn at one flat "
        "colour means the pressure envelope never reached the markup"
    )
