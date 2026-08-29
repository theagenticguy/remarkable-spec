"""The shared ``PageRenderer`` contract, as one function every implementation is run through.

``ports/render.py`` states four pins on :class:`RenderedPage` that no validator on the model can
enforce, because each relates the result to the *input page* the caller already has. They are
collected here so the real adapter and its double are asserted against the same rules rather
than each against its own.

This module lives inside ``rmspec-render``'s test tree because that is the only directory this
change may write to. It belongs in a workspace-level ``tests/contract/`` package once
``rmspec-export`` and ``rmspec-app`` grow doubles of their own -- the import is by bare module
name, so hoisting it is a move plus an ``__init__.py``, with no edit to the assertions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from rmspec.domain.ports.render import RenderNoticeCode

if TYPE_CHECKING:
    from rmspec.domain.models import Page, Palette, ScreenSpec
    from rmspec.domain.ports.render import (
        PageBackground,
        RenderedPage,
        RenderStyle,
    )


class PageRendererLike(Protocol):
    """Structural stand-in for the port, so this helper needs no adapter import."""

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
        """Render one page."""
        ...


def visible_text_blocks(page: Page, /) -> int:
    """Count the page's visible, non-empty typed text blocks.

    Computed here from the page rather than read off a production helper, so the expectation
    is independent of the code under test.
    """
    if page.content is None:
        return 0
    return sum(
        1
        for layer in page.content.visible_layers
        for block in layer.text_blocks
        if block.text.strip()
    )


def assert_page_renderer_contract(
    renderer: PageRendererLike,
    /,
    *,
    page: Page,
    screen: ScreenSpec,
    palette: Palette,
    style: RenderStyle,
    background: PageBackground | None = None,
) -> RenderedPage:
    """Assert every rule ``ports/render.py`` puts on a rendered page.

    Returns the result so a caller can add adapter-specific assertions on top.
    """
    rendered = renderer.render(
        page,
        screen=screen,
        palette=palette,
        style=style,
        background=background,
    )
    codes = {notice.code for notice in rendered.notices}

    assert rendered.page_ref == page.ref, "a rendered page must identify the page it came from"
    assert "<svg" in rendered.svg

    assert rendered.stroke_count <= page.stroke_count
    assert (rendered.stroke_count == 0) == (page.stroke_count == 0), (
        "stroke_count is zero if and only if the page has no strokes in any visible layer"
    )

    if RenderNoticeCode.VIEWPORT_EXPANDED in codes:
        assert (
            rendered.size.width_mm > screen.width_mm or rendered.size.height_mm > screen.height_mm
        ), "a viewport-expanded page must be strictly larger than the screen somewhere"
    else:
        assert rendered.size.width_mm == screen.width_mm
        assert rendered.size.height_mm == screen.height_mm

    expected_blocks = visible_text_blocks(page)
    if RenderNoticeCode.TEXT_OMITTED in codes:
        assert rendered.text_block_count < expected_blocks, (
            "a TEXT_OMITTED notice claims blocks were dropped, so the count must be lower"
        )
    else:
        assert rendered.text_block_count == expected_blocks, (
            "every visible non-empty block must be drawn or reported as omitted"
        )

    return rendered
