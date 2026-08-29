"""One policy, asserted end to end: a coordinate that is not a finite number is unplaceable.

``Point.x``, ``Point.y`` and ``TextBlock.pos_x``/``pos_y`` are unconstrained floats, and
``TextBlock.width`` and both ``PhysicalSize`` fields are ``Field(gt=0)`` -- a constraint pydantic
satisfies with ``inf``, because ``inf > 0`` is true and only ``nan`` fails it. A malformed scene
file can therefore carry an IEEE NaN or infinity through the codec and the domain into this
package untouched, and all four consequences below were reproduced before this file existed:

- ``x1="nan"`` in the markup, which ``RenderedPage`` accepts (its validator greps for ``<svg``)
  and which cairo then rejects one package along, in ``rmspec-export``.
- ``viewBox="-30.00 -30.00 inf inf"`` and ``PhysicalSize(width_mm=inf)``, which the export slice
  sizes a PDF page from.
- ``<image x="-inf" width="inf">`` from an underlay whose native page size is not usable.
- ``OverflowError`` out of :func:`rmspec.render._text.wrap_text`, a third exception type from a
  method whose docstring declares two.

The right fix is ``allow_inf_nan=False`` on those fields in ``rmspec.domain.models``, which would
fix every consumer at once and make everything asserted here defensive rather than live. Until
that lands, this adapter's contract is that its output is always parseable and always finite, and
that anything it could not place is *reported* rather than dropped in silence.
"""

from __future__ import annotations

import math

import pytest
from render_builders import LEGACY_STYLE, layer, page, parse_svg, point, stroke, underlay
from render_contract import assert_page_renderer_contract

from rmspec.domain.models import EXPORT_PALETTE, RM2_SCREEN, Page, TextBlock
from rmspec.domain.ports.render import PageBackground, RenderedPage, RenderNoticeCode
from rmspec.render import SvgPageRenderer
from rmspec.render._svg import SVG_NAMESPACE

RENDERER = SvgPageRenderer()
LINE_TAG = f"{{{SVG_NAMESPACE}}}line"
TEXT_TAG = f"{{{SVG_NAMESPACE}}}text"

NON_FINITE = [float("inf"), float("-inf"), float("nan")]
NON_FINITE_IDS = ["inf", "-inf", "nan"]


def render(subject: Page) -> RenderedPage:
    """Render through the shared contract, so no fix here may break the port's rules."""
    return assert_page_renderer_contract(
        RENDERER,
        page=subject,
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )


@pytest.mark.parametrize("poison", NON_FINITE, ids=NON_FINITE_IDS)
def test_no_coordinate_in_the_markup_is_ever_non_finite(poison: float) -> None:
    rendered = render(page(layer(stroke(point(poison, 0.0), point(10.0, 10.0)))))
    root = parse_svg(rendered.svg)
    values = [
        value
        for element in root.iter()
        for key, value in element.items()
        if key in {"x", "y", "x1", "y1", "x2", "y2", "width", "height", "viewBox"}
    ]
    assert values, "nothing was measured, so this assertion proves nothing"
    for value in values:
        for part in value.split():
            assert math.isfinite(float(part)), f"{value!r} is not printable"


@pytest.mark.parametrize("poison", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_page_size_is_always_finite(poison: float) -> None:
    """The export slice sizes a PDF page from this value."""
    rendered = render(page(layer(stroke(point(poison, poison), point(10.0, 10.0)))))
    assert math.isfinite(rendered.size.width_mm)
    assert math.isfinite(rendered.size.height_mm)


@pytest.mark.parametrize("poison", NON_FINITE, ids=NON_FINITE_IDS)
def test_only_the_unplaceable_segment_is_dropped(poison: float) -> None:
    """A three-sample stroke with one bad middle sample keeps neither neighbour segment.

    Both segments touch the bad sample, so both go; the *other* stroke on the layer is
    untouched, which is what makes this a skip rather than a bail-out.
    """
    rendered = render(
        page(
            layer(
                stroke(point(0.0, 0.0), point(poison, 1.0), point(2.0, 2.0)),
                stroke(point(3.0, 3.0), point(4.0, 4.0)),
            )
        )
    )
    assert len(list(parse_svg(rendered.svg).iter(LINE_TAG))) == 1


@pytest.mark.parametrize("poison", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_stroke_with_no_placeable_segment_draws_an_empty_group(poison: float) -> None:
    rendered = render(page(layer(stroke(point(poison, 0.0), point(poison, 1.0)))))
    assert "<line" not in rendered.svg
    assert 'stroke-linecap="round"' in rendered.svg, "the group is opened before the scan"


@pytest.mark.parametrize("poison", NON_FINITE, ids=NON_FINITE_IDS)
def test_an_unplaceable_text_block_is_reported_rather_than_drawn(poison: float) -> None:
    """The one path that emits ``TEXT_OMITTED``: a block with no coordinate to draw at.

    ``x="nan"`` is the failure being avoided, and the contract in ``render`` is what forbids
    swapping the silence for a lower count with no notice.
    """
    subject = page(
        layer(text_blocks=(TextBlock(pos_x=poison, pos_y=10.0, width=800.0, text="lost words"),))
    )
    rendered = render(subject)
    codes = [notice.code for notice in rendered.notices]

    assert rendered.text_block_count == 0
    assert RenderNoticeCode.TEXT_OMITTED in codes
    assert "<text" not in rendered.svg
    detail = next(n.detail for n in rendered.notices if n.code is RenderNoticeCode.TEXT_OMITTED)
    assert "1 typed text block" in detail


def test_a_placeable_block_beside_an_unplaceable_one_is_still_drawn() -> None:
    """The notice counts what was lost, not what was attempted."""
    subject = page(
        layer(
            text_blocks=(
                TextBlock(pos_x=float("nan"), pos_y=10.0, width=800.0, text="lost"),
                TextBlock(pos_x=100.0, pos_y=10.0, width=800.0, text="kept"),
            )
        )
    )
    rendered = render(subject)
    assert rendered.text_block_count == 1
    assert next(parse_svg(rendered.svg).iter(TEXT_TAG)).text == "kept"
    assert RenderNoticeCode.TEXT_OMITTED in [notice.code for notice in rendered.notices]


def test_an_unbounded_wrap_width_neither_raises_nor_omits() -> None:
    """``int(inf)`` used to raise ``OverflowError`` here, which the port does not declare."""
    subject = page(
        layer(text_blocks=(TextBlock(pos_x=0.0, pos_y=0.0, width=float("inf"), text="hello"),))
    )
    rendered = render(subject)
    assert rendered.text_block_count == 1
    assert RenderNoticeCode.TEXT_OMITTED not in [notice.code for notice in rendered.notices]


@pytest.mark.parametrize("poison", NON_FINITE[:2], ids=NON_FINITE_IDS[:2])
def test_an_underlay_with_an_unusable_native_size_falls_back_to_the_page_box(
    poison: float,
) -> None:
    """``PhysicalSize`` is ``gt=0``, which ``inf`` satisfies, so this arrives from a caller.

    Drawing it at its own size emitted ``width="inf"`` and ``x="-inf"`` and pushed
    ``PhysicalSize(width_mm=inf)`` into the export slice. The pixels are kept -- the user's PDF
    page still shows -- at the page box, and the substitution is reported.
    """
    background = PageBackground(underlay=underlay(width_mm=abs(poison), height_mm=297.0))
    rendered = assert_page_renderer_contract(
        RENDERER,
        page=page(layer()),
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
        background=background,
    )
    root = parse_svg(rendered.svg)
    image = next(root.iter(f"{{{SVG_NAMESPACE}}}image"))

    assert RenderNoticeCode.UNDERLAY_RESCALED in [notice.code for notice in rendered.notices]
    assert float(image.get("width", "0")) == pytest.approx(447.29, abs=0.01)
    assert math.isfinite(float(image.get("x", "0")))
    assert math.isfinite(rendered.size.width_mm)


def test_a_usable_underlay_is_still_drawn_at_its_own_size() -> None:
    """The fallback must not swallow the ordinary case it exists beside."""
    background = PageBackground(underlay=underlay(width_mm=210.0, height_mm=297.0))
    rendered = RENDERER.render(
        page(layer()),
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
        background=background,
    )
    image = next(parse_svg(rendered.svg).iter(f"{{{SVG_NAMESPACE}}}image"))
    assert float(image.get("width", "0")) == pytest.approx(210.0 * 72.0 / 25.4, abs=0.01)
