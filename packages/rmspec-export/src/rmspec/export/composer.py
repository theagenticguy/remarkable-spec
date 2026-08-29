"""``cairosvg`` + ``pymupdf``-backed :class:`~rmspec.domain.ports.export.PdfComposer`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rmspec.domain.errors import PdfCompositionFailed
from rmspec.domain.ports.export import PdfDocument, PdfPageRef
from rmspec.export import _cairo, _pymupdf
from rmspec.export._geometry import points_from_millimetres, sizes_agree

if TYPE_CHECKING:
    from rmspec.domain.ports.export import PhysicalSize, SvgPage, SvgPageSet

__all__ = ["CairoSvgPdfComposer"]

_ONE_PAGE = 1


def _aspect(size: PhysicalSize) -> float:
    """Width over height, for the one failure message that cannot be diagnosed without it.

    Parameters
    ----------
    size
        A page size. Both dimensions are ``gt=0`` by construction, so this cannot divide by
        zero.

    Returns
    -------
    float
        The aspect ratio.
    """
    return size.width_mm / size.height_mm


class CairoSvgPdfComposer:
    """Compose an ordered set of SVG pages into exactly that many PDF pages.

    Satisfies :class:`~rmspec.domain.ports.export.PdfComposer`. Stateless, so ``APP`` scope.

    The legacy defect this class exists to make impossible
    -----------------------------------------------------
    Legacy ``export_pdf`` had a one-page fast path that worked and a multi-page path that did
    not: the multi-page branch converted every page, painted an unrelated blank white
    ``cairocffi`` surface over the output, then ended with
    ``output.write_bytes(page_pdfs[0])`` under a comment claiming it appended the rest. An
    N-page request produced page one and reported success. There is no page-count branch
    here at all -- one page and two hundred run the identical convert, verify and merge
    pipeline, and a test asserts the per-page converter is called once per page for both, so
    a reintroduced fast path fails.

    Why the page size is measured rather than assumed
    ------------------------------------------------
    ``cairosvg`` reads a unitless SVG ``width``/``height`` as CSS pixels at 96 per inch. On
    the legacy renderer's markup, whose declared box is a *points* figure written without a
    unit, that makes every emitted page 0.75x the intended size -- measured, a 507.29-unit
    page box becomes 380.47 pt. A composer that hard-codes the reciprocal 96/72 correction
    fixes today's markup and breaks the moment the renderer declares ``mm``, where no
    correction is needed and 96/72 overshoots by a third. Both were measured. So this
    composer does not encode an assumption about markup it does not own: it converts once,
    measures the page box, and re-converts at ``target / measured``. That converged to
    within 0.000003 mm of the requested size under both conventions, well inside
    :data:`PAGE_SIZE_TOLERANCE_MM`, and it needs no update if the renderer's unit convention
    changes.
    """

    def compose(self, pages: SvgPageSet) -> PdfDocument:
        """Compose ``pages`` into one PDF, one emitted page per input page, in order.

        Parameters
        ----------
        pages
            The pages to emit, in order. Non-empty and distinctly referenced by
            construction.

        Returns
        -------
        PdfDocument
            The composed document, whose ``pages`` has one entry per page found in the
            emitted bytes -- count and sizes measured from the document, ``page_ref`` taken
            from the input at the same ordinal.

        Raises
        ------
        PdfCompositionFailed
            A page could not be converted, an intermediate held other than one page, the
            pages could not be merged, or read-back found a page count or a page size other
            than the one requested.
        """
        expected = len(pages.pages)
        parts = tuple(self._to_single_page_pdf(page, expected=expected) for page in pages.pages)
        try:
            data = _pymupdf.merge(parts)
        except _pymupdf.PdfBackendError as exc:
            raise PdfCompositionFailed(expected_pages=expected, detail=exc.detail) from exc
        measured = self._read_back(data, expected=expected)
        if len(measured) != expected:
            msg = "the merged document does not hold one page per requested page"
            raise PdfCompositionFailed(
                expected_pages=expected,
                actual_pages=len(measured),
                detail=msg,
            )
        refs = tuple(
            PdfPageRef(page_ref=page.page_ref, size=size)
            for page, size in zip(pages.pages, measured, strict=True)
        )
        for page, size in zip(pages.pages, measured, strict=True):
            if not sizes_agree(size, page.size):
                msg = (
                    f"page {page.page_ref} was emitted at "
                    f"{size.width_mm:.4f}x{size.height_mm:.4f} mm, not the requested "
                    f"{page.size.width_mm:.4f}x{page.size.height_mm:.4f} mm"
                )
                raise PdfCompositionFailed(
                    expected_pages=expected,
                    actual_pages=len(measured),
                    detail=msg,
                )
        try:
            return PdfDocument(data=data, pages=refs)
        except ValueError as exc:
            # ``%PDF-`` header and ``%%EOF`` trailer, checked by the model: this is where a
            # surface whose buffer was never flushed is refused, which is the legacy
            # truncated-output failure the model exists to make unrepresentable.
            raise PdfCompositionFailed(
                expected_pages=expected,
                actual_pages=len(measured),
                detail=f"the composed bytes are not a complete PDF: {exc}",
            ) from exc

    def _to_single_page_pdf(self, page: SvgPage, *, expected: int) -> bytes:
        """Convert one page to a one-page PDF whose box is the size the domain asked for.

        Parameters
        ----------
        page
            The page to convert.
        expected
            Page count of the whole request, carried only so a failure here can report it.

        Returns
        -------
        bytes
            A PDF holding exactly one page, sized within :data:`PAGE_SIZE_TOLERANCE_MM` of
            ``page.size``.

        Raises
        ------
        PdfCompositionFailed
            The conversion failed, produced other than one page, or could not be brought
            onto the requested size. The correction scale comes from width alone and is
            applied to both axes, so a page whose markup aspect ratio differs from
            ``page.size``'s can never converge and always fails here after two conversions.
            That is contract-conformant -- the port requires both dimensions to agree -- and
            unreachable from today's render output, which is aspect-consistent; the message
            therefore names all three aspect ratios so the failure reads as the mismatch it
            is rather than as a backend fault.
        """
        first = self._convert(page, scale=None, expected=expected)
        measured = self._one_page_size(first, page=page, expected=expected)
        if sizes_agree(measured, page.size):
            return first
        scale = points_from_millimetres(page.size.width_mm) / points_from_millimetres(
            measured.width_mm
        )
        second = self._convert(page, scale=scale, expected=expected)
        rescaled = self._one_page_size(second, page=page, expected=expected)
        if not sizes_agree(rescaled, page.size):
            msg = (
                f"page {page.page_ref} could not be brought onto "
                f"{page.size.width_mm:.4f}x{page.size.height_mm:.4f} mm "
                f"(aspect {_aspect(page.size):.6f}); two passes reached "
                f"{measured.width_mm:.4f}x{measured.height_mm:.4f} "
                f"(aspect {_aspect(measured):.6f}) then "
                f"{rescaled.width_mm:.4f}x{rescaled.height_mm:.4f} "
                f"(aspect {_aspect(rescaled):.6f}). The correction is derived from width and "
                f"applied to both axes, so two differing aspect ratios never converge: that is "
                f"the markup's declared box disagreeing with the requested size, not a backend "
                f"fault"
            )
            raise PdfCompositionFailed(expected_pages=expected, detail=msg)
        return second

    def _convert(self, page: SvgPage, *, scale: float | None, expected: int) -> bytes:
        """Run one SVG-to-PDF conversion, translating backend failure.

        Parameters
        ----------
        page
            The page to convert.
        scale
            Uniform scale to apply, or ``None`` for the markup's own size.
        expected
            Page count of the whole request, for the error message.

        Returns
        -------
        bytes
            PDF bytes.

        Raises
        ------
        PdfCompositionFailed
            The conversion failed.
        """
        try:
            return _cairo.render_pdf(page.svg, scale=scale)
        except _cairo.CairoError as exc:
            raise PdfCompositionFailed(
                expected_pages=expected,
                detail=f"page {page.page_ref}: {exc.detail}",
            ) from exc

    def _one_page_size(self, data: bytes, *, page: SvgPage, expected: int) -> PhysicalSize:
        """Measure an intermediate and insist it holds exactly one page.

        Parameters
        ----------
        data
            The intermediate PDF bytes.
        page
            The page it was converted from, for the error message.
        expected
            Page count of the whole request, for the error message.

        Returns
        -------
        PhysicalSize
            The single page's size.

        Raises
        ------
        PdfCompositionFailed
            The intermediate could not be read, or held other than one page.
        """
        try:
            sizes = _pymupdf.blob_page_sizes(data)
        except _pymupdf.PdfBackendError as exc:
            raise PdfCompositionFailed(
                expected_pages=expected,
                detail=f"page {page.page_ref}: {exc.detail}",
            ) from exc
        if len(sizes) != _ONE_PAGE:
            msg = (
                f"the intermediate for page {page.page_ref} holds {len(sizes)} pages, "
                f"so merging it in order would not preserve one page per input page"
            )
            raise PdfCompositionFailed(expected_pages=expected, detail=msg)
        return sizes[0]

    def _read_back(self, data: bytes, *, expected: int) -> tuple[PhysicalSize, ...]:
        """Reopen the composed bytes and measure every page in them.

        Parameters
        ----------
        data
            The merged PDF bytes.
        expected
            Page count of the whole request, for the error message.

        Returns
        -------
        tuple[PhysicalSize, ...]
            One size per page found in the emitted document.

        Raises
        ------
        PdfCompositionFailed
            The composed bytes could not be reopened.
        """
        try:
            return _pymupdf.blob_page_sizes(data)
        except _pymupdf.PdfBackendError as exc:
            raise PdfCompositionFailed(
                expected_pages=expected,
                detail=f"the composed document could not be read back: {exc.detail}",
            ) from exc
