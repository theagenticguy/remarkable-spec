"""Byte-level assertions on the emitted document.

``test_an_empty_visible_layer_reproduces_a_real_oracle_hash`` is the important one: it
reproduces one of the thirty differential-manifest SHA-256 values from a hand-built page, with
no ``.rm`` file and no personal corpus, so three of the thirty entries stay guarded on a clean
CI machine where ``~/remarkable`` does not exist. It pins the whole serialisation envelope --
declaration quoting, indentation, unitless two-decimal lengths, ``fill="white"``, the four 30 pt
pads and the self-closed group of an inkless layer -- with zero pen physics involved.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from render_builders import (
    JPEG_BYTES,
    LEGACY_STYLE,
    PNG_BYTES,
    layer,
    page,
    parse_svg,
    point,
    stroke,
    style,
    underlay,
)

from rmspec.domain.errors import BackgroundUnreadable, UnsupportedPenType
from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    Page,
    Palette,
    PenColor,
    PenType,
    ScreenSpec,
)
from rmspec.domain.ports.render import (
    ImageMedia,
    PageBackground,
    RenderedPage,
    RenderNoticeCode,
    RenderStyle,
)
from rmspec.render import SvgPageRenderer, _pens
from rmspec.render._svg import SVG_NAMESPACE

RENDERER = SvgPageRenderer()

#: One of the thirty entries in tests/fixtures/render-differential-manifest.json: a page with a
#: single visible, inkless layer named "Layer 1", on a reMarkable 2 screen, at thickness 1.5.
ORACLE_EMPTY_LAYER_SHA256 = "422de376dd08e237a6aced34caca149e725abc93fe57f6e483c3ecd9da40e284"
ORACLE_EMPTY_LAYER_BYTES = 273

#: The zero-byte-stub document: no layers at all, so no ``<g>`` is emitted. Deliberately a
#: *different* document from the one above, and pinned so a codec that ever synthesises a
#: placeholder layer for a stub cannot start emitting layer groups unnoticed.
STUB_DOCUMENT_SHA256 = "c7dfd1ec30cd54c52a282d2db992120181294e92abd8759a67d2a33d1a83fc80"
STUB_DOCUMENT_BYTES = 232

DECLARATION = "<?xml version='1.0' encoding='utf-8'?>\n"


def render(
    subject: Page,
    *,
    screen: ScreenSpec = RM2_SCREEN,
    palette: Palette = EXPORT_PALETTE,
    render_style: RenderStyle = LEGACY_STYLE,
    background: PageBackground | None = None,
) -> RenderedPage:
    """Render with the manifest's parameters unless a test overrides one."""
    return RENDERER.render(
        subject,
        screen=screen,
        palette=palette,
        style=render_style,
        background=background,
    )


def test_an_empty_visible_layer_reproduces_a_real_oracle_hash() -> None:
    rendered = render(page(layer()))
    raw = rendered.svg.encode()
    assert len(raw) == ORACLE_EMPTY_LAYER_BYTES
    assert hashlib.sha256(raw).hexdigest() == ORACLE_EMPTY_LAYER_SHA256


def test_a_page_with_no_layers_is_a_different_document() -> None:
    rendered = render(page())
    raw = rendered.svg.encode()
    assert len(raw) == STUB_DOCUMENT_BYTES
    assert hashlib.sha256(raw).hexdigest() == STUB_DOCUMENT_SHA256
    assert "<g" not in rendered.svg


def test_the_declaration_is_single_quoted_and_followed_by_a_newline() -> None:
    assert render(page()).svg.startswith(DECLARATION)


def test_there_is_no_trailing_newline() -> None:
    assert render(page()).svg.endswith("</svg>")


def test_the_root_attributes_are_in_the_legacy_order() -> None:
    svg = render(page()).svg
    assert '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-30.00 -30.00 ' in svg
    assert 'viewBox="-30.00 -30.00 507.29 656.39" width="507.29" height="656.39">' in svg


def test_the_background_rect_attributes_are_in_the_legacy_order() -> None:
    assert (
        '<rect x="-30.00" y="-30.00" width="507.29" height="656.39" fill="white" />'
        in render(page()).svg
    )


def test_lengths_carry_no_unit_suffix() -> None:
    """``width="507.29"`` and not ``width="507.29pt"``: four bytes per page, all thirty of them."""
    svg = render(page()).svg
    assert 'width="507.29"' in svg
    assert 'pt"' not in svg


def test_elements_are_indented_with_two_spaces() -> None:
    assert "\n  <rect " in render(page()).svg


def test_a_line_is_self_closed_with_a_space_before_the_slash() -> None:
    svg = render(page(layer(stroke(point(0.0, 0.0), point(10.0, 10.0))))).svg
    assert " />" in svg
    assert "/>" in svg
    assert "></line>" not in svg


def test_line_attributes_are_in_the_legacy_order() -> None:
    drawn = page(layer(stroke(point(0.0, 0.0), point(10.0, 20.0), pen=PenType.FINELINER_1)))
    svg = render(drawn).svg
    assert '<line x1="223.65" y1="0.00" x2="226.83" y2="6.37" ' in svg
    assert 'stroke-width="1.720" stroke="rgb(0,0,0)" />' in svg


def test_rgb_has_no_spaces() -> None:
    """``Rgb.as_css`` inserts them; reusing it would change every stroke in the oracle."""
    blue = page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0), color=PenColor.BLUE)))
    assert 'stroke="rgb(78,105,201)"' in render(blue).svg


def test_opacity_is_absent_when_a_segment_is_fully_opaque() -> None:
    full = page(
        layer(
            stroke(
                point(0.0, 0.0),
                point(1.0, 1.0, pressure=255),
                pen=PenType.PENCIL_1,
            )
        )
    )
    assert "opacity=" not in render(full).svg


def test_opacity_is_written_at_three_decimals_when_a_segment_is_translucent() -> None:
    partial = page(
        layer(
            stroke(
                point(0.0, 0.0),
                point(1.0, 1.0, pressure=200),
                pen=PenType.PENCIL_1,
            )
        )
    )
    assert 'opacity="0.935"' in render(partial).svg


def test_a_highlighter_writes_its_constant_opacity() -> None:
    marked = page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0), pen=PenType.HIGHLIGHTER_1)))
    svg = render(marked).svg
    assert 'stroke-linecap="square"' in svg
    assert 'opacity="0.300"' in svg


def test_a_shader_writes_its_constant_opacity() -> None:
    shaded = page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0), pen=PenType.SHADER)))
    assert 'opacity="0.100"' in render(shaded).svg


def test_an_eraser_draws_white_whatever_the_stroke_colour_says() -> None:
    erased = page(
        layer(
            stroke(
                point(0.0, 0.0),
                point(1.0, 1.0),
                pen=PenType.ERASER,
                color=PenColor.RED,
            )
        )
    )
    assert 'stroke="rgb(255,255,255)"' in render(erased).svg


def test_the_stroke_group_attributes_are_in_the_legacy_order() -> None:
    svg = render(page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0))))).svg
    assert '<g stroke-linecap="round" fill="none">' in svg


def test_a_tap_draws_no_group_and_no_line() -> None:
    tapped = page(layer(stroke(point(5.0, 5.0))))
    rendered = render(tapped)
    assert "<line" not in rendered.svg
    assert "stroke-linecap" not in rendered.svg
    assert rendered.stroke_count == 1, "the page has a stroke even though it drew nothing"


def test_layer_ids_number_positions_in_the_full_layer_list() -> None:
    """A hidden layer leaves a gap, exactly as ``enumerate(page.layers)`` produced."""
    mixed = page(
        layer(name="hidden", visible=False),
        layer(name="second"),
        layer(name="third"),
    )
    svg = render(mixed).svg
    assert 'id="layer-0"' not in svg
    assert 'id="layer-1" data-name="second"' in svg
    assert 'id="layer-2" data-name="third"' in svg


def test_an_unnamed_layer_gets_no_data_name_attribute() -> None:
    assert "data-name" not in render(page(layer(name=""))).svg


def test_a_hidden_layer_draws_nothing() -> None:
    hidden = page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0)), visible=False))
    rendered = render(hidden)
    assert "<line" not in rendered.svg
    assert rendered.stroke_count == 0, "a hidden layer's strokes are not page strokes"


def test_the_thickness_multiplier_compounds_through_the_smoothing_loop() -> None:
    """The regression test for anyone who "cleans up" the clamp/multiply ordering.

    ``last_width`` is fed forward *after* the export multiplier, so a marker's second segment
    does not scale linearly with thickness. If it does, the loop was reordered.
    """
    marked = page(
        layer(
            stroke(
                point(0.0, 0.0, width=200, direction=50),
                point(1.0, 1.0, width=200, direction=50),
                point(2.0, 2.0, width=200, direction=50),
                pen=PenType.MARKER_1,
            )
        )
    )
    at_one = _stroke_widths(render(marked, render_style=style(thickness_scale=1.0)).svg)
    at_one_point_five = _stroke_widths(render(marked, render_style=style(thickness_scale=1.5)).svg)

    assert at_one_point_five[0] == pytest.approx(at_one[0] * 1.5, rel=1e-3)
    assert at_one_point_five[1] != pytest.approx(at_one[1] * 1.5, rel=1e-6)


def _stroke_widths(svg: str) -> list[float]:
    root = parse_svg(svg)
    return [float(line.get("stroke-width", "0")) for line in root.iter(f"{{{SVG_NAMESPACE}}}line")]


def test_a_valid_template_lands_inside_a_half_opacity_group() -> None:
    background = PageBackground(
        template_svg='<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" /></svg>'
    )
    svg = render(page(), background=background).svg
    assert '<g id="template" opacity="0.5">' in svg
    assert "rect" in svg


def test_a_template_serialises_with_the_hoisted_ns0_prefix() -> None:
    """A quirk relocated unchanged, pinned so global namespace state cannot shift it.

    ``ElementTree._namespace_map`` is process-global: one ``register_namespace("", SVG_NS)``
    anywhere in the interpreter would change every emitted prefix and therefore every hash.
    """
    background = PageBackground(
        template_svg='<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" /></svg>'
    )
    svg = render(page(), background=background).svg
    assert '<svg xmlns:ns0="http://www.w3.org/2000/svg"' in svg
    assert "<ns0:rect " in svg


@pytest.mark.parametrize(
    "markup",
    ["<svg", "not xml at all", "<svg></rect></svg>", " "],
)
def test_unparsable_template_markup_raises_rather_than_vanishing(markup: str) -> None:
    with pytest.raises(BackgroundUnreadable) as caught:
        render(page(), background=PageBackground(template_svg=markup))
    assert caught.value.page_ref == page().ref


def test_template_markup_with_a_non_svg_root_raises() -> None:
    background = PageBackground(template_svg="<html><body>this parses<svg /></body></html>")
    with pytest.raises(BackgroundUnreadable) as caught:
        render(page(), background=background)
    assert "not <svg>" in caught.value.detail


def test_a_template_without_a_namespace_is_still_accepted() -> None:
    svg = render(page(), background=PageBackground(template_svg="<svg><rect /></svg>")).svg
    assert '<g id="template" opacity="0.5">' in svg


def test_a_png_underlay_is_embedded_centred_on_the_ink_origin() -> None:
    rendered = render(page(), background=PageBackground(underlay=underlay()))
    svg = rendered.svg
    assert '<image x="' in svg
    assert ' y="0" ' in svg, "the underlay's y is the literal string 0, not 0.00"
    assert 'preserveAspectRatio="xMidYMin meet"' in svg
    assert 'href="data:image/png;base64,' in svg


def test_the_underlay_x_is_half_its_width_left_of_the_ink_origin() -> None:
    rendered = render(page(), background=PageBackground(underlay=underlay()))
    root = parse_svg(rendered.svg)
    image = next(root.iter(f"{{{SVG_NAMESPACE}}}image"))
    width = float(image.get("width", "0"))
    # x_shift for a reMarkable 2 is half of 1404 * 72 / 226.
    x_shift = 1404 * (72.0 / 226) / 2
    assert float(image.get("x", "0")) == pytest.approx(x_shift - width / 2, abs=0.01)


def test_a_jpeg_underlay_gets_a_jpeg_data_url() -> None:
    background = PageBackground(underlay=underlay(media=ImageMedia.JPEG))
    assert "data:image/jpeg;base64," in render(page(), background=background).svg


def test_the_underlay_bytes_are_standard_base64() -> None:
    rendered = render(page(), background=PageBackground(underlay=underlay()))
    assert base64.standard_b64encode(PNG_BYTES).decode("ascii") in rendered.svg


def test_a_jpeg_underlay_embeds_its_own_bytes() -> None:
    background = PageBackground(underlay=underlay(media=ImageMedia.JPEG))
    rendered = render(page(), background=background)
    assert base64.standard_b64encode(JPEG_BYTES).decode("ascii") in rendered.svg


def test_an_underlay_of_a_different_size_reports_underlay_rescaled() -> None:
    rendered = render(page(), background=PageBackground(underlay=underlay()))
    codes = [notice.code for notice in rendered.notices]
    assert RenderNoticeCode.UNDERLAY_RESCALED in codes


def test_an_underlay_the_size_of_the_page_reports_no_rescale() -> None:
    """A4 is not the reMarkable 2's page box, so the matching size is computed from it."""
    scale = 72.0 / 226
    fitted = underlay(
        width_mm=1404 * scale * 25.4 / 72,
        height_mm=1872 * scale * 25.4 / 72,
    )
    rendered = render(page(), background=PageBackground(underlay=fitted))
    codes = [notice.code for notice in rendered.notices]
    assert RenderNoticeCode.UNDERLAY_RESCALED not in codes


def test_both_background_halves_render_in_document_order() -> None:
    background = PageBackground(
        template_svg='<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" /></svg>',
        underlay=underlay(),
    )
    svg = render(page(layer()), background=background).svg
    assert svg.index("<rect") < svg.index("<image") < svg.index('id="template"')
    assert svg.index('id="template"') < svg.index('id="layer-0"')


def test_an_unmodelled_pen_raises_instead_of_drawing_as_a_fineliner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loud-failure path. Unreachable for a real page, which is why it is forced here."""
    monkeypatch.setattr(_pens, "_MODELS", {})
    drawn = page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0), pen=PenType.CALLIGRAPHY)))
    with pytest.raises(UnsupportedPenType) as caught:
        render(drawn)
    assert caught.value.pen == "CALLIGRAPHY"
    assert caught.value.page_ref == drawn.ref


def test_an_unpriced_pen_raises_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pens, "_PROFILES", {})
    drawn = page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0))))
    with pytest.raises(UnsupportedPenType):
        render(drawn)


def test_the_paper_pro_screen_produces_a_bigger_document_than_the_rm2() -> None:
    on_rm2 = render(page(), screen=RM2_SCREEN)
    on_pro = render(page(), screen=PAPER_PRO_SCREEN)
    assert on_pro.size.width_mm > on_rm2.size.width_mm
    assert on_pro.size.height_mm > on_rm2.size.height_mm


def test_the_page_ref_is_echoed_from_the_page() -> None:
    subject = page()
    assert render(subject).page_ref == subject.ref
