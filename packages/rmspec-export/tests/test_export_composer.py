"""The PDF composer, and the legacy page-one truncation it makes structurally impossible."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from export_support import (
    LEGACY_RM2_BOX_PT,
    PAPER_PRO_BOX_PT,
    US_LETTER_BOX_PT,
    build_pdf,
    check_composer,
    millimetre_svg_markup,
    millimetres,
)

from rmspec.domain.errors import PdfCompositionFailed
from rmspec.domain.ports.export import PhysicalSize, SvgPage, SvgPageSet
from rmspec.export import _cairo, _pymupdf
from rmspec.export._geometry import sizes_agree
from rmspec.export.composer import CairoSvgPdfComposer

if TYPE_CHECKING:
    from conftest import PageFactory


def test_three_pages_of_three_sizes_compose_to_three_pages(
    three_distinct_pages: SvgPageSet,
) -> None:
    # The direct regression for `output.write_bytes(page_pdfs[0])`: an N-page request answered
    # with page one fails both halves of this, the count and every size after the first.
    document = check_composer(CairoSvgPdfComposer(), page_set=three_distinct_pages)
    assert len(document.pages) == 3


def test_a_single_page_takes_the_identical_pipeline(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The divergence between the one-page and multi-page branches *was* the legacy bug, so the
    # absence of a fast path is asserted by counting conversions rather than described.
    calls: list[float | None] = []
    original = _cairo.render_pdf

    def counting(svg: str, *, scale: float | None = None) -> bytes:
        calls.append(scale)
        return original(svg, scale=scale)

    monkeypatch.setattr(_cairo, "render_pdf", counting)
    one = SvgPageSet(pages=(page("only", LEGACY_RM2_BOX_PT),))
    CairoSvgPdfComposer().compose(one)
    single_page_conversions = len(calls)
    calls.clear()
    three = SvgPageSet(
        pages=(
            page("a", LEGACY_RM2_BOX_PT),
            page("b", US_LETTER_BOX_PT),
            page("c", PAPER_PRO_BOX_PT),
        )
    )
    CairoSvgPdfComposer().compose(three)
    assert len(calls) == 3 * single_page_conversions


def test_unitless_point_markup_reads_back_at_its_millimetre_size(page: PageFactory) -> None:
    # cairosvg reads a unitless width as CSS pixels at 96/inch, so without a correction this
    # page comes back 0.75x too small and the port's read-back rejects it.
    document = CairoSvgPdfComposer().compose(SvgPageSet(pages=(page("pt", LEGACY_RM2_BOX_PT),)))
    assert sizes_agree(document.pages[0].size, page("pt", LEGACY_RM2_BOX_PT).size)


def test_millimetre_declared_markup_reads_back_at_its_millimetre_size() -> None:
    # The other convention. A composer that hard-codes 96/72 overshoots this one by a third,
    # which is why the correction is measured rather than assumed.
    width_mm = millimetres(LEGACY_RM2_BOX_PT[0])
    height_mm = millimetres(LEGACY_RM2_BOX_PT[1])
    subject = SvgPage(
        page_ref="mm",
        svg=millimetre_svg_markup(width_mm, height_mm),
        size=PhysicalSize(width_mm=width_mm, height_mm=height_mm),
    )
    document = CairoSvgPdfComposer().compose(SvgPageSet(pages=(subject,)))
    assert sizes_agree(document.pages[0].size, subject.size)


def test_pages_from_zero_byte_stubs_still_become_pages(page: PageFactory) -> None:
    # 62 of 92 .rm files in the reference corpus are empty stubs. Their pages carry no ink and
    # must still appear in the output; an export that skipped them would look exactly like the
    # page-one truncation bug.
    ink_free = SvgPageSet(
        pages=(
            page("stub-0", LEGACY_RM2_BOX_PT, ink=False),
            page("inked", US_LETTER_BOX_PT, ink=True),
            page("stub-1", PAPER_PRO_BOX_PT, ink=False),
        )
    )
    document = check_composer(CairoSvgPdfComposer(), page_set=ink_free)
    assert [ref.page_ref for ref in document.pages] == ["stub-0", "inked", "stub-1"]


def test_a_conversion_failure_becomes_a_composition_failure(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> bytes:
        msg = "surface died"
        raise _cairo.CairoError(msg)

    monkeypatch.setattr(_cairo, "render_pdf", explode)
    with pytest.raises(PdfCompositionFailed) as caught:
        CairoSvgPdfComposer().compose(SvgPageSet(pages=(page("a"), page("b"))))
    assert caught.value.expected_pages == 2
    assert "surface died" in caught.value.detail


def test_a_multi_page_intermediate_is_refused(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Merging a two-page intermediate in list order would silently produce more pages than
    # inputs, so the count would still "match" nothing and page_ref alignment would be wrong.
    two_pages = build_pdf([(100.0, 200.0), (100.0, 200.0)])
    monkeypatch.setattr(_cairo, "render_pdf", lambda *_a, **_k: two_pages)
    with pytest.raises(PdfCompositionFailed, match="holds 2 pages"):
        CairoSvgPdfComposer().compose(SvgPageSet(pages=(page("a"),)))


def test_a_merge_that_drops_a_page_is_caught_with_both_counts(
    three_distinct_pages: SvgPageSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _pymupdf.merge

    def dropping(parts: tuple[bytes, ...]) -> bytes:
        return original(parts[:-1])

    monkeypatch.setattr(_pymupdf, "merge", dropping)
    with pytest.raises(PdfCompositionFailed) as caught:
        CairoSvgPdfComposer().compose(three_distinct_pages)
    assert caught.value.expected_pages == 3
    assert caught.value.actual_pages == 2


def test_a_merge_that_returns_the_first_page_only_is_caught(
    three_distinct_pages: SvgPageSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is the legacy behaviour reproduced deliberately: page one, reported as success.
    monkeypatch.setattr(_pymupdf, "merge", lambda parts: parts[0])
    with pytest.raises(PdfCompositionFailed) as caught:
        CairoSvgPdfComposer().compose(three_distinct_pages)
    assert (caught.value.expected_pages, caught.value.actual_pages) == (3, 1)


def test_a_merge_failure_becomes_a_composition_failure(
    three_distinct_pages: SvgPageSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_parts: tuple[bytes, ...]) -> bytes:
        msg = "merge died"
        raise _pymupdf.PdfBackendError(msg)

    monkeypatch.setattr(_pymupdf, "merge", explode)
    with pytest.raises(PdfCompositionFailed, match="merge died"):
        CairoSvgPdfComposer().compose(three_distinct_pages)


def test_an_unreadable_merge_result_becomes_a_composition_failure(
    three_distinct_pages: SvgPageSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pymupdf, "merge", lambda _parts: b"not a pdf")
    with pytest.raises(PdfCompositionFailed, match="could not be read back"):
        CairoSvgPdfComposer().compose(three_distinct_pages)


def test_an_unflushed_output_is_refused_by_the_document_validator(
    three_distinct_pages: SvgPageSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Three real pages, no %%EOF: the legacy truncated-surface failure, where the writer's own
    # page counter still said N. The count and every size read back correctly and it is still
    # refused, which is the whole point of validating the bytes.
    original = _pymupdf.merge
    monkeypatch.setattr(
        _pymupdf,
        "merge",
        lambda parts: original(parts).replace(b"%%EOF", b"%%eof"),
    )
    with pytest.raises(PdfCompositionFailed, match="not a complete PDF"):
        CairoSvgPdfComposer().compose(three_distinct_pages)


def test_a_page_emitted_at_the_wrong_size_is_refused(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A converter that honours the markup's viewport and one that scales to the request are both
    # defensible readings of "compose these pages", and they emit different documents. The size
    # clause is what makes the difference detectable.
    wrong = build_pdf([(100.0, 200.0)])
    monkeypatch.setattr(_cairo, "render_pdf", lambda *_a, **_k: wrong)
    with pytest.raises(PdfCompositionFailed, match="could not be brought onto") as caught:
        CairoSvgPdfComposer().compose(SvgPageSet(pages=(page("a", LEGACY_RM2_BOX_PT),)))
    # The forced page is 0.5 wide-over-high against a request of 0.7729, and a scale taken from
    # width and applied to both axes cannot close that. The message has to say so: without the
    # aspect ratios it reads as a backend that failed twice.
    assert "aspect 0.772848" in caught.value.detail
    assert "aspect 0.500000" in caught.value.detail
    assert "not a backend fault" in caught.value.detail


def test_a_page_already_at_the_requested_size_is_converted_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The second pass is skipped when the first already lands inside the tolerance, so
    # mm-declared markup costs one conversion and unitless markup costs two.
    width_mm = millimetres(US_LETTER_BOX_PT[0])
    height_mm = millimetres(US_LETTER_BOX_PT[1])
    subject = SvgPage(
        page_ref="mm",
        svg=millimetre_svg_markup(width_mm, height_mm),
        size=PhysicalSize(width_mm=width_mm, height_mm=height_mm),
    )
    calls: list[float | None] = []
    original = _cairo.render_pdf

    def counting(svg: str, *, scale: float | None = None) -> bytes:
        calls.append(scale)
        return original(svg, scale=scale)

    monkeypatch.setattr(_cairo, "render_pdf", counting)
    CairoSvgPdfComposer().compose(SvgPageSet(pages=(subject,)))
    assert calls == [None]


def test_a_page_measured_wrong_after_a_correct_merge_is_refused(
    three_distinct_pages: SvgPageSet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The read-back is two facts, count and size, and they fail independently. Here the count is
    # right and the middle page's size is not, which is the shape a converter that scaled one
    # page to the wrong box would produce.
    original = _pymupdf.blob_page_sizes
    seen: list[int] = []

    def bend(data: bytes) -> tuple[PhysicalSize, ...]:
        sizes = original(data)
        seen.append(len(sizes))
        if len(sizes) != 3:
            return sizes
        wrong = PhysicalSize(
            width_mm=sizes[1].width_mm + 5.0,
            height_mm=sizes[1].height_mm,
        )
        return (sizes[0], wrong, sizes[2])

    monkeypatch.setattr(_pymupdf, "blob_page_sizes", bend)
    with pytest.raises(PdfCompositionFailed) as caught:
        CairoSvgPdfComposer().compose(three_distinct_pages)
    assert caught.value.actual_pages == 3
    assert "not the requested" in caught.value.detail


def test_an_unreadable_intermediate_becomes_a_composition_failure(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_data: bytes) -> tuple[PhysicalSize, ...]:
        msg = "intermediate died"
        raise _pymupdf.PdfBackendError(msg)

    monkeypatch.setattr(_pymupdf, "blob_page_sizes", explode)
    with pytest.raises(PdfCompositionFailed) as caught:
        CairoSvgPdfComposer().compose(SvgPageSet(pages=(page("solo"),)))
    assert "solo" in caught.value.detail
    assert "intermediate died" in caught.value.detail
