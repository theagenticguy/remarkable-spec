"""The shared contract, run over the real adapter and over a deliberately minimal double.

The double exists to prove the contract is satisfiable by something other than the adapter --
which is what stops the assertions from silently encoding one implementation's arithmetic -- and
to demonstrate the legitimate second shape ``ports/render.py`` blesses: a writer with no font
metrics that reports ``TEXT_OMITTED`` instead of dropping words in silence.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from render_builders import (
    LEGACY_STYLE,
    layer,
    page,
    point,
    stroke,
    style,
    underlay,
    unreadable_page,
)
from render_contract import (
    PageRendererLike,
    assert_page_renderer_contract,
    drawable_text_blocks,
)

from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_PHYSICAL_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    Page,
    PageDefectCode,
    Palette,
    PenType,
    ScreenSpec,
    TextBlock,
)
from rmspec.domain.ports.render import (
    PageBackground,
    PhysicalSize,
    RenderedPage,
    RenderNotice,
    RenderNoticeCode,
    RenderStyle,
)
from rmspec.render import SvgPageRenderer


@dataclass(frozen=True, slots=True)
class InkOnlyFakeRenderer:
    """A double that draws nothing and says so, in fifteen lines.

    It reports the screen's own size, so it never claims a viewport expansion, and it reports
    ``TEXT_OMITTED`` whenever the page carries text -- the two halves of the contract a lying
    fake would get wrong.
    """

    def render(
        self,
        page: Page,
        /,
        *,
        screen: ScreenSpec,
        palette: Palette,
        style: RenderStyle,
        background: PageBackground | None = None,
    ) -> RenderedPage:
        """Return a blank document sized to the screen."""
        del palette, style, background
        dropped = drawable_text_blocks(page)
        notices = (
            (
                RenderNotice(
                    code=RenderNoticeCode.TEXT_OMITTED,
                    detail=f"{dropped} blocks left out: this writer has no font metrics",
                ),
            )
            if dropped
            else ()
        )
        return RenderedPage(
            page_ref=page.ref,
            svg='<svg xmlns="http://www.w3.org/2000/svg" />',
            size=PhysicalSize(width_mm=screen.width_mm, height_mm=screen.height_mm),
            stroke_count=page.stroke_count,
            text_block_count=0,
            notices=notices,
        )


RENDERERS = [SvgPageRenderer(), InkOnlyFakeRenderer()]
RENDERER_IDS = ["SvgPageRenderer", "InkOnlyFakeRenderer"]

TEXT = TextBlock(pos_x=10.0, pos_y=20.0, width=600.0, text="typed words")

#: A second block, so a page carrying text from both sources has two distinguishable ones.
PAGE_TEXT = TextBlock(pos_x=30.0, pos_y=40.0, width=600.0, text="page scoped words")

PAGES = {
    "blank stub": page(),
    "one empty layer": page(layer()),
    "one stroke": page(layer(stroke(point(0.0, 0.0), point(10.0, 10.0)))),
    "a tap only": page(layer(stroke(point(3.0, 4.0)))),
    "ink far off page": page(layer(stroke(point(-4000.0, -4000.0), point(4000.0, 6000.0)))),
    "hidden ink": page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0)), visible=False)),
    "typed text only": page(layer(text_blocks=(TEXT,))),
    "text and ink": page(layer(stroke(point(0.0, 0.0), point(1.0, 1.0)), text_blocks=(TEXT,))),
    "page-level text only": page(text_blocks=(TEXT,)),
    "page-level text over ink": page(
        layer(stroke(point(0.0, 0.0), point(1.0, 1.0))),
        text_blocks=(TEXT,),
    ),
    "page-level text over hidden layers": page(
        layer(stroke(point(0.0, 0.0), point(1.0, 1.0)), visible=False),
        text_blocks=(TEXT,),
    ),
    "text from both sources": page(layer(text_blocks=(TEXT,)), text_blocks=(PAGE_TEXT,)),
    "unreadable": unreadable_page(PageDefectCode.CONTENT_UNDECODABLE),
    "every pen": page(
        layer(
            *[
                stroke(point(0.0, 0.0), point(float(index), 1.0), pen=pen)
                for index, pen in enumerate(PenType)
            ]
        )
    ),
}


@pytest.mark.parametrize("renderer", RENDERERS, ids=RENDERER_IDS)
@pytest.mark.parametrize("subject", PAGES.values(), ids=list(PAGES))
@pytest.mark.parametrize("screen", [RM2_SCREEN, PAPER_PRO_SCREEN], ids=["rm2", "paper-pro"])
def test_the_contract_holds_for_every_page_and_screen(
    renderer: PageRendererLike,
    subject: Page,
    screen: ScreenSpec,
) -> None:
    assert_page_renderer_contract(
        renderer,
        page=subject,
        screen=screen,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )


@pytest.mark.parametrize("renderer", RENDERERS, ids=RENDERER_IDS)
@pytest.mark.parametrize(
    "palette",
    [EXPORT_PALETTE, PAPER_PRO_PHYSICAL_PALETTE],
    ids=["export", "physical"],
)
def test_the_contract_holds_for_either_palette(
    renderer: PageRendererLike,
    palette: Palette,
) -> None:
    assert_page_renderer_contract(
        renderer,
        page=PAGES["one stroke"],
        screen=RM2_SCREEN,
        palette=palette,
        style=LEGACY_STYLE,
    )


@pytest.mark.parametrize("renderer", RENDERERS, ids=RENDERER_IDS)
def test_the_contract_holds_with_a_background(renderer: PageRendererLike) -> None:
    assert_page_renderer_contract(
        renderer,
        page=PAGES["one stroke"],
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
        background=PageBackground(
            template_svg='<svg xmlns="http://www.w3.org/2000/svg"><rect /></svg>',
            underlay=underlay(),
        ),
    )


def test_a_zero_margin_render_reports_exactly_the_screen_size() -> None:
    """The clean case: no padding, so ``size`` equals the screen and no notice is due."""
    rendered = assert_page_renderer_contract(
        SvgPageRenderer(),
        page=PAGES["one stroke"],
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=style(min_padding_mm=0.0),
    )
    assert rendered.notices == ()
    assert rendered.size.width_mm == RM2_SCREEN.width_mm


def test_the_legacy_margin_always_reports_a_viewport_expansion() -> None:
    """A margin on all four sides makes the document bigger than the screen, on every page.

    Reported honestly rather than papered over: ``rmspec-export`` sizes PNG and PDF pages from
    ``size``, and claiming the screen's 157.8x210.4 mm for a 178.9x231.6 mm document would put
    a 13% scaling error downstream where nothing checks it.
    """
    rendered = SvgPageRenderer().render(
        PAGES["one stroke"],
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )
    codes = [notice.code for notice in rendered.notices]
    assert codes == [RenderNoticeCode.VIEWPORT_EXPANDED]
    assert "margin" in rendered.notices[0].detail
    assert rendered.size.width_mm > RM2_SCREEN.width_mm


def test_overflowing_ink_says_so_rather_than_naming_the_margin() -> None:
    rendered = SvgPageRenderer().render(
        PAGES["ink far off page"],
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )
    detail = rendered.notices[0].detail
    assert "outside the page box" in detail
    assert "mm" in detail


def test_the_adapter_is_stateless_and_deterministic() -> None:
    subject = PAGES["every pen"]
    once = SvgPageRenderer().render(
        subject, screen=RM2_SCREEN, palette=EXPORT_PALETTE, style=LEGACY_STYLE
    )
    twice = SvgPageRenderer().render(
        subject, screen=RM2_SCREEN, palette=EXPORT_PALETTE, style=LEGACY_STYLE
    )
    assert once == twice


def test_two_pens_of_the_same_tool_row_render_identically() -> None:
    row_one = page(layer(stroke(point(0.0, 0.0), point(5.0, 5.0), pen=PenType.FINELINER_1)))
    row_two = page(layer(stroke(point(0.0, 0.0), point(5.0, 5.0), pen=PenType.FINELINER_2)))
    renderer = SvgPageRenderer()
    first = renderer.render(row_one, screen=RM2_SCREEN, palette=EXPORT_PALETTE, style=LEGACY_STYLE)
    second = renderer.render(
        row_two, screen=RM2_SCREEN, palette=EXPORT_PALETTE, style=LEGACY_STYLE
    )
    assert first.svg == second.svg


def test_a_stroke_thickness_change_is_not_a_style_thickness_change() -> None:
    """The two numbers both called "thickness" must not be interchangeable.

    ``Stroke.thickness_scale`` seeds the pen's base width; ``RenderStyle.thickness_scale`` is
    the export multiplier. A swap type-checks, so it is pinned here -- with a mechanical pencil,
    whose base width is the slider value *squared*, because for a linear pen with one segment
    the two numbers really are interchangeable and the swap would go unnoticed.
    """
    renderer = SvgPageRenderer()
    pen = PenType.MECHANICAL_PENCIL_1
    thick_stroke = page(
        layer(stroke(point(0.0, 0.0), point(5.0, 5.0), pen=pen, thickness_scale=4.0)),
    )
    thin_stroke = page(
        layer(stroke(point(0.0, 0.0), point(5.0, 5.0), pen=pen, thickness_scale=1.0)),
    )
    swapped = renderer.render(
        thin_stroke, screen=RM2_SCREEN, palette=EXPORT_PALETTE, style=style(thickness_scale=4.0)
    )
    correct = renderer.render(
        thick_stroke, screen=RM2_SCREEN, palette=EXPORT_PALETTE, style=style(thickness_scale=1.0)
    )
    assert swapped.svg != correct.svg
