"""Typed text: the behaviour that stops a page of words rendering as a valid-looking blank.

The legacy renderer never read ``Layer.text_blocks``. There is therefore no oracle behind this
file -- and no oracle entry contains a text block either, which is why drawing text cannot move
a single differential hash. What these tests pin is the port's obligation: a visible non-empty
block is either drawn and counted, or reported as omitted, never silently dropped.
"""

from __future__ import annotations

import math

import pytest
from render_builders import (
    DEFAULT_TEXT_STYLE,
    LEGACY_STYLE,
    layer,
    page,
    parse_svg,
    point,
    stroke,
    style,
)

from rmspec.domain.models import EXPORT_PALETTE, RM2_SCREEN, Page, TextBlock
from rmspec.domain.ports.render import RenderedPage, RenderNoticeCode, RenderStyle, TextStyle
from rmspec.render import SvgPageRenderer
from rmspec.render._layout import layout_for
from rmspec.render._svg import SVG_NAMESPACE
from rmspec.render._text import block_extent, wrap_text

RENDERER = SvgPageRenderer()
TEXT_TAG = f"{{{SVG_NAMESPACE}}}text"


def render(subject: Page, *, render_style: RenderStyle = LEGACY_STYLE) -> RenderedPage:
    """Render with the manifest's parameters."""
    return RENDERER.render(
        subject,
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=render_style,
    )


def block(
    text: str,
    *,
    pos_x: float = 100.0,
    pos_y: float = 200.0,
    width: float = 800.0,
) -> TextBlock:
    """Build one typed text block."""
    return TextBlock(pos_x=pos_x, pos_y=pos_y, width=width, text=text)


def test_a_text_only_page_is_not_an_indistinguishable_blank() -> None:
    """The defect this whole module exists for."""
    subject = page(layer(text_blocks=(block("hello handwriting"),)))
    rendered = render(subject)
    assert rendered.stroke_count == 0
    assert rendered.text_block_count == 1
    assert "hello handwriting" in rendered.svg
    assert rendered.notices == () or all(
        notice.code is not RenderNoticeCode.TEXT_OMITTED for notice in rendered.notices
    )


def test_an_empty_block_is_not_counted_and_draws_nothing() -> None:
    subject = page(layer(text_blocks=(block(""),)))
    rendered = render(subject)
    assert rendered.text_block_count == 0
    assert "<text" not in rendered.svg


def test_a_whitespace_only_block_is_not_counted() -> None:
    rendered = render(page(layer(text_blocks=(block("   \n  "),))))
    assert rendered.text_block_count == 0


def test_a_block_on_a_hidden_layer_is_not_drawn() -> None:
    subject = page(layer(visible=False, text_blocks=(block("invisible"),)))
    rendered = render(subject)
    assert rendered.text_block_count == 0
    assert "invisible" not in rendered.svg


def test_text_is_drawn_above_its_layer_s_ink() -> None:
    subject = page(
        layer(
            stroke(point(0.0, 0.0), point(1.0, 1.0)),
            text_blocks=(block("over the top"),),
        )
    )
    svg = render(subject).svg
    assert svg.index("<line") < svg.index("<text")


def test_every_line_is_its_own_childless_text_element() -> None:
    """``ElementTree.indent`` would inject rendered whitespace into a ``<text>`` with children."""
    subject = page(layer(text_blocks=(block("one two three four five six", width=160.0),)))
    root = parse_svg(render(subject).svg)
    elements = list(root.iter(TEXT_TAG))
    assert len(elements) > 1
    for element in elements:
        assert len(element) == 0
        assert element.text is not None
        assert element.text == element.text.strip()


def test_text_attributes_come_from_the_style() -> None:
    chosen = TextStyle(family="Iosevka, monospace", size_px=48.0, line_height=1.5)
    subject = page(layer(text_blocks=(block("styled"),)))
    root = parse_svg(render(subject, render_style=style(text=chosen)).svg)
    element = next(root.iter(TEXT_TAG))
    assert element.get("font-family") == "Iosevka, monospace"
    scale = 72.0 / RM2_SCREEN.dpi
    assert element.get("font-size") == f"{48.0 * scale:.2f}"


def test_the_baseline_advance_comes_from_the_line_height() -> None:
    chosen = TextStyle(family="sans-serif", size_px=40.0, line_height=2.0)
    subject = page(layer(text_blocks=(block("aaa bbb ccc ddd eee fff", width=120.0),)))
    root = parse_svg(render(subject, render_style=style(text=chosen)).svg)
    baselines = [float(element.get("y", "0")) for element in root.iter(TEXT_TAG)]
    scale = 72.0 / RM2_SCREEN.dpi
    assert len(baselines) > 1
    assert baselines[1] - baselines[0] == pytest.approx(40.0 * 2.0 * scale, abs=0.011)


def test_text_uses_the_same_centre_origin_transform_as_ink() -> None:
    subject = page(layer(text_blocks=(block("aligned", pos_x=-300.0),)))
    root = parse_svg(render(subject).svg)
    element = next(root.iter(TEXT_TAG))
    layout = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)
    assert element.get("x") == f"{-300.0 * layout.scale + layout.x_shift:.2f}"


def test_wrapping_is_deterministic() -> None:
    first = wrap_text("the quick brown fox jumps", width=200.0, style=DEFAULT_TEXT_STYLE)
    second = wrap_text("the quick brown fox jumps", width=200.0, style=DEFAULT_TEXT_STYLE)
    assert first == second


def test_wrapping_produces_no_more_lines_as_the_box_widens() -> None:
    text = "the quick brown fox jumps over the lazy dog again and again"
    narrow = wrap_text(text, width=200.0, style=DEFAULT_TEXT_STYLE)
    wide = wrap_text(text, width=2000.0, style=DEFAULT_TEXT_STYLE)
    assert len(wide) <= len(narrow)
    assert " ".join(narrow) == " ".join(wide) == text


def test_explicit_newlines_are_paragraph_breaks() -> None:
    lines = wrap_text("first\nsecond", width=4000.0, style=DEFAULT_TEXT_STYLE)
    assert lines == ("first", "second")


def test_blank_paragraphs_are_dropped() -> None:
    lines = wrap_text("first\n\n\nsecond", width=4000.0, style=DEFAULT_TEXT_STYLE)
    assert lines == ("first", "second")


def test_whitespace_only_text_wraps_to_nothing() -> None:
    assert wrap_text("  \n \t ", width=4000.0, style=DEFAULT_TEXT_STYLE) == ()


def test_a_word_longer_than_the_line_is_kept_whole() -> None:
    """Hyphenating without font metrics would be a second guess stacked on the first."""
    lines = wrap_text("unsplittablesupercalifragilistic", width=1.0, style=DEFAULT_TEXT_STYLE)
    assert lines == ("unsplittablesupercalifragilistic",)


def test_a_narrow_box_still_wraps_to_at_least_one_character_per_line() -> None:
    lines = wrap_text("a b c", width=0.001, style=DEFAULT_TEXT_STYLE)
    assert lines == ("a", "b", "c")


def test_markup_special_characters_are_escaped() -> None:
    subject = page(layer(text_blocks=(block("a < b & c"),)))
    svg = render(subject).svg
    assert "&lt;" in svg
    assert "&amp;" in svg
    root = parse_svg(svg)
    assert next(root.iter(TEXT_TAG)).text == "a < b & c"


def test_blocks_across_two_layers_are_both_counted() -> None:
    subject = page(
        layer(text_blocks=(block("first"),)),
        layer(text_blocks=(block("second"),), name="Layer 2"),
    )
    assert render(subject).text_block_count == 2


def test_a_block_far_off_the_left_edge_moves_the_viewbox_instead_of_vanishing() -> None:
    """The hole the port's three text members left open, closed by geometry.

    A block at a large negative ``pos_x`` used to be laid out hundreds of points outside the
    ``viewBox``: counted as drawn, no ``TEXT_OMITTED`` notice, and clipped by every rasterizer
    -- so the words were gone from the PNG, the PDF and both OCR paths while every counter said
    they were there. The block now widens the margin the way ink does.
    """
    subject = page(layer(text_blocks=(block("vanishing words", pos_x=-2000.0, pos_y=100.0),)))
    rendered = render(subject)
    root = parse_svg(rendered.svg)
    origin_x, origin_y, width, height = (float(part) for part in root.get("viewBox", "").split())
    element = next(root.iter(TEXT_TAG))
    text_x = float(element.get("x", "0"))
    text_y = float(element.get("y", "0"))

    assert rendered.text_block_count == 1
    assert origin_x <= text_x, "the first glyph must not sit left of the viewBox"
    assert text_x <= origin_x + width
    assert origin_y <= text_y <= origin_y + height


def test_the_estimated_right_edge_of_a_block_lands_inside_the_viewbox() -> None:
    """The wrap width, not just the corner, has to fit: a block is a box, not a point."""
    subject = page(layer(text_blocks=(block("far right", pos_x=1300.0, width=900.0),)))
    root = parse_svg(render(subject).svg)
    origin_x, _, width, _ = (float(part) for part in root.get("viewBox", "").split())
    extent = block_extent(block("far right", pos_x=1300.0, width=900.0), style=DEFAULT_TEXT_STYLE)
    layout = layout_for((), screen=RM2_SCREEN, min_padding_pt=30.0)

    right_edge = extent[2] * layout.scale + layout.x_shift
    assert right_edge <= origin_x + width


def test_a_block_inside_the_page_box_leaves_the_viewbox_where_ink_alone_would() -> None:
    """The widening is conditional, so an ordinary page keeps the legacy geometry."""
    inked = page(layer(stroke(point(0.0, 0.0), point(10.0, 10.0))))
    with_text = page(
        layer(
            stroke(point(0.0, 0.0), point(10.0, 10.0)),
            text_blocks=(block("ordinary", pos_x=100.0, pos_y=200.0, width=400.0),),
        )
    )
    assert parse_svg(render(with_text).svg).get("viewBox") == parse_svg(render(inked).svg).get(
        "viewBox"
    )


def test_an_unbounded_wrap_width_does_not_wrap_and_does_not_raise() -> None:
    """``TextBlock.width`` is ``gt=0``, and pydantic admits ``inf`` under that constraint.

    ``int(inf)`` raises ``OverflowError`` -- a third exception type out of a method whose
    docstring declares two -- so an unbounded width means "do not wrap" instead.
    """
    lines = wrap_text("one two three", width=float("inf"), style=DEFAULT_TEXT_STYLE)
    assert lines == ("one two three",)

    subject = page(layer(text_blocks=(block("one two three", width=float("inf")),)))
    rendered = render(subject)
    assert rendered.text_block_count == 1
    assert len(list(parse_svg(rendered.svg).iter(TEXT_TAG))) == 1


def test_an_unbounded_width_contributes_no_infinite_extent() -> None:
    """An infinite wrap width must not become an infinite margin."""
    extent = block_extent(block("one two", width=float("inf")), style=DEFAULT_TEXT_STYLE)
    assert all(math.isfinite(edge) for edge in extent)

    rendered = render(page(layer(text_blocks=(block("one two", width=float("inf")),))))
    assert math.isfinite(rendered.size.width_mm)
    assert math.isfinite(rendered.size.height_mm)


def test_a_block_that_draws_nothing_has_a_degenerate_extent() -> None:
    """Whitespace is a real thing to store, and it must not move the viewport."""
    extent = block_extent(block("   \n\t", pos_x=5.0, pos_y=7.0), style=DEFAULT_TEXT_STYLE)
    assert extent == (5.0, 7.0, 5.0, 7.0)


def test_an_over_long_word_widens_the_extent_past_the_declared_width() -> None:
    """``wrap_text`` keeps such a word whole, so the box has to admit it."""
    subject = block("unsplittablesupercalifragilistic", pos_x=0.0, width=1.0)
    _, _, max_x, _ = block_extent(subject, style=DEFAULT_TEXT_STYLE)
    assert max_x > subject.width
    assert max_x == len(subject.text) * DEFAULT_TEXT_STYLE.size_px * 0.5
