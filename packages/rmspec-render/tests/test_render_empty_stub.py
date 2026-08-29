"""The zero-byte scene stub: a page with no ink, not a parse failure.

Sixty-two of the ninety-two ``.rm`` files in the reference corpus are zero bytes -- the stubs the
device writes for unannotated pages of a PDF-backed document. The legacy ``parse_rm_file`` raised
a bare ``EOFError`` with an empty message on every one, so two thirds of a real corpus took the
whole page loop down and the exception said nothing about why.

This package never opens a file, so producing the typed outcome is ``rmspec-formats``' job. What
is asserted here is the other half: that the four states are four distinct *values*, that only
one of them is readable, that the fourth is unconstructable, and that rendering the stub
succeeds and returns a blank document rather than raising anything at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from render_builders import LEGACY_STYLE, ZERO_PAGE_UUID, layer, page, unreadable_page

from rmspec.domain.models import (
    EXPORT_PALETTE,
    RM2_SCREEN,
    Page,
    PageDefectCode,
    PageId,
)
from rmspec.render import SvgPageRenderer

if TYPE_CHECKING:
    from rmspec.domain.ports.render import RenderedPage

RENDERER = SvgPageRenderer()


def render(subject: Page) -> RenderedPage:
    """Render with the manifest's parameters."""
    return RENDERER.render(
        subject,
        screen=RM2_SCREEN,
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )


def test_a_stub_a_corrupt_file_and_an_absent_file_are_three_distinct_values() -> None:
    stub = page()
    undecodable = unreadable_page(PageDefectCode.CONTENT_UNDECODABLE)
    absent = unreadable_page(PageDefectCode.ARTIFACT_ABSENT)
    assert len({stub, undecodable, absent}) == 3


def test_only_the_stub_is_readable() -> None:
    assert page().is_readable is True
    assert unreadable_page(PageDefectCode.CONTENT_UNDECODABLE).is_readable is False
    assert unreadable_page(PageDefectCode.ARTIFACT_ABSENT).is_readable is False


def test_the_stub_carries_no_defect_at_all() -> None:
    """An empty page and a damaged one must not be the same value."""
    stub = page()
    assert stub.all_defects == ()
    assert stub.stroke_count == 0


def test_an_unreadable_page_names_its_reason() -> None:
    undecodable = unreadable_page(PageDefectCode.CONTENT_UNDECODABLE)
    assert [defect.code for defect in undecodable.all_defects] == [
        PageDefectCode.CONTENT_UNDECODABLE
    ]


def test_empty_for_no_stated_reason_is_unconstructable() -> None:
    """The domain closes the fourth state, so nothing can spell "gone, no reason given"."""
    with pytest.raises(ValidationError):
        Page(page_id=PageId(uuid=ZERO_PAGE_UUID), index=0, content=None, defects=())


def test_rendering_a_stub_succeeds_where_the_legacy_parser_raised() -> None:
    rendered = render(page())
    assert "<svg" in rendered.svg
    assert rendered.stroke_count == 0
    assert rendered.text_block_count == 0


def test_rendering_a_stub_with_an_empty_layer_also_succeeds() -> None:
    rendered = render(page(layer()))
    assert rendered.stroke_count == 0
    assert 'id="layer-0"' in rendered.svg


@pytest.mark.parametrize(
    "code",
    [PageDefectCode.CONTENT_UNDECODABLE, PageDefectCode.ARTIFACT_ABSENT],
)
def test_rendering_an_unreadable_page_returns_an_empty_document_rather_than_raising(
    code: PageDefectCode,
) -> None:
    """Strictness is the use case's decision over ``page.all_defects``, not the renderer's.

    ``PageRenderer.render`` declares exactly two exceptions, and inventing a third for a page
    the domain already describes would break the contract every adapter is held to.
    """
    rendered = render(unreadable_page(code))
    assert "<svg" in rendered.svg
    assert rendered.stroke_count == 0


def test_a_stub_and_an_unreadable_page_render_to_the_same_markup() -> None:
    """The difference between them is legible on the page value, not in the picture.

    Asserted so nobody later "improves" the renderer into raising on one of the two: the
    markup is deliberately identical, and ``is_readable`` is where the distinction lives.
    """
    stub = render(page())
    undecodable = render(unreadable_page(PageDefectCode.CONTENT_UNDECODABLE))
    assert stub.svg == undecodable.svg
    assert page().is_readable != unreadable_page(PageDefectCode.CONTENT_UNDECODABLE).is_readable
