"""``pymupdf``-backed :class:`~rmspec.domain.ports.export.PdfPageReader`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rmspec.domain.errors import (
    PdfPageOutOfRange,
    PdfSourceUnreadable,
    RasterizationFailed,
)
from rmspec.domain.ports.export import PdfPageBackground, PhysicalSize, PixelSize
from rmspec.export import _pillow, _pymupdf
from rmspec.export.sources import SourceMissingError

if TYPE_CHECKING:
    from pathlib import Path

    from rmspec.domain.ports.export import PdfSourceRef
    from rmspec.export.sources import SourceResolver

__all__ = ["PyMuPdfPageReader"]

_PRECONDITION_PROBE_PAGE = PhysicalSize(width_mm=1.0, height_mm=1.0)
"""A 1x1 mm page used for nothing but reaching :meth:`PixelSize.fit_within`'s ``oversample``
precondition before any document is opened. The lower bound is defined once, in the domain
formula; restating it here as ``if oversample < 1: raise ValueError`` would be a second copy
of a constant that can drift from the formula it guards."""


class PyMuPdfPageReader:
    """Read an existing PDF: how many pages, what they say, what they look like.

    Satisfies :class:`~rmspec.domain.ports.export.PdfPageReader`. Stateless and keyed by
    :class:`~rmspec.domain.ports.export.PdfSourceRef`, so ``APP`` scope with a
    ``REQUEST``-scoped registry injected -- bound as
    :class:`~rmspec.export.sources.SourceResolver`, the one-method view of that registry, so
    minting a ref stays a capability of the composition root and not of this reader.

    Three legacy call sites, one adapter
    -----------------------------------
    :meth:`rasterize_page` relocates the background rasterizer that lived in the *caller*,
    which is why all three legacy exporters took ``background_images_b64: list[str | None]``
    and ``background_page_size: tuple[float, float]``: a list of base64 PNG strings aligned to
    the page list only by position is the fingerprint of a caller that had already opened the
    PDF, rasterized it, encoded it and passed the page size separately. That positional
    alignment with ``None`` holes is exactly the misalignment
    :class:`~rmspec.domain.errors.PdfPageOutOfRange` was written for. :meth:`page_count`
    replaces the legacy ``/Type /Page`` regular-expression scan, which over- and under-counted
    real documents; :meth:`page_texts` relocates the annotation command's
    ``get_text("text")`` verbatim.

    No open-document memo
    --------------------
    The port permits caching an open document against a ref and this adapter declines it.
    MuPDF reads lazily from its backing file, so a memoised ``Document`` outliving the
    registry that spooled its temporary is a use-after-unlink, and nothing in this workspace
    measures a win that would justify the coupling. Each method opens, works and closes inside
    one ``with`` block.
    """

    def __init__(self, *, registry: SourceResolver) -> None:
        self._registry = registry

    def page_count(self, source: PdfSourceRef) -> int:
        """Count the pages in ``source``.

        Parameters
        ----------
        source
            Opaque reference to the PDF.

        Returns
        -------
        int
            Number of pages.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is unknown, missing, is not a PDF, is corrupt, or needs a password.
        """
        path = self._resolve(source)
        try:
            return _pymupdf.page_count(path)
        except _pymupdf.PdfBackendError as exc:
            raise PdfSourceUnreadable(source=source.token, detail=exc.detail) from exc

    def page_texts(self, source: PdfSourceRef) -> tuple[str, ...]:
        """Extract the text of every page in ``source``.

        Parameters
        ----------
        source
            Opaque reference to the PDF.

        Returns
        -------
        tuple[str, ...]
            Page text in document order, empty string for a page with no extractable text.
            Its length equals :meth:`page_count` by construction, because it is built by
            iterating the same document.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is unknown, missing, is not a PDF, is corrupt, or needs a password.
        """
        path = self._resolve(source)
        try:
            return _pymupdf.page_texts(path)
        except _pymupdf.PdfBackendError as exc:
            raise PdfSourceUnreadable(source=source.token, detail=exc.detail) from exc

    def rasterize_page(
        self,
        source: PdfSourceRef,
        *,
        page_index: int,
        box: PixelSize,
        oversample: int,
    ) -> PdfPageBackground:
        """Rasterize one page of ``source``, fitted to ``box`` and supersampled.

        The returned ``pixel_size`` equals ``PixelSize.fit_within(result.page_size, box,
        oversample=oversample)`` exactly. It is not bounded by ``box``: at ``oversample=2``
        it is twice ``box`` in each dimension, and reading the contract as scale-to-box
        produces a half-size background that registers plausibly and wrongly.

        Parameters
        ----------
        source
            Opaque reference to the PDF.
        page_index
            Zero-based page index.
        box
            Pixel box the page is fitted to before supersampling.
        oversample
            Integer supersampling factor applied after fitting.

        Returns
        -------
        PdfPageBackground
            PNG bytes, their pixel size, and the source page's native real-world size with
            rotation applied -- all from one open.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is unknown, missing, is not a PDF, is corrupt, or needs a password.
        PdfPageOutOfRange
            ``page_index`` is not a page of this document.
        RasterizationFailed
            The page was found but could not be rasterized, or the produced PNG disagreed
            with the size the domain formula asked for.
        ValueError
            ``oversample`` is below 1. Raised by :meth:`PixelSize.fit_within` before the
            registry is consulted, which is asserted rather than assumed: a ref pointing at a
            file that does not exist still raises ``ValueError`` here rather than
            ``PdfSourceUnreadable``, so a caller bug cannot be reported as a broken document.
        """
        if oversample < 1:
            # Delegated to the domain formula rather than restated, so there is exactly one
            # definition of the lower bound, and reached before the registry is consulted so
            # a caller bug cannot be masked by an unreadable source.
            PixelSize.fit_within(_PRECONDITION_PROBE_PAGE, box, oversample=oversample)
        path = self._resolve(source)
        try:
            sizes = _pymupdf.page_sizes(path)
        except _pymupdf.PdfBackendError as exc:
            raise PdfSourceUnreadable(source=source.token, detail=exc.detail) from exc
        if not 0 <= page_index < len(sizes):
            raise PdfPageOutOfRange(
                source=source.token,
                page_index=page_index,
                page_count=len(sizes),
            )
        page_size = sizes[page_index]
        target = PixelSize.fit_within(page_size, box, oversample=oversample)
        try:
            data = _pymupdf.rasterize(path, page_index=page_index, target=target)
        except (_pymupdf.PdfBackendError, _pillow.PillowError) as exc:
            raise RasterizationFailed(
                backend=_pymupdf.BACKEND,
                detail=exc.detail,
                page_ref=f"{source.token}#{page_index}",
            ) from exc
        try:
            return PdfPageBackground(
                page_index=page_index,
                data=data,
                pixel_size=target,
                page_size=page_size,
            )
        except ValueError as exc:
            # The model compares its declared pixel size against the PNG header and requires
            # the terminating IEND chunk, so a backend that sized its own way or truncated
            # its stream is caught here rather than registering slightly off under the strokes.
            raise RasterizationFailed(
                backend=_pymupdf.BACKEND,
                detail=f"produced PNG did not match the requested raster: {exc}",
                page_ref=f"{source.token}#{page_index}",
            ) from exc

    def _resolve(self, source: PdfSourceRef) -> Path:
        """Turn a ref into a location, translating an unknown token.

        Parameters
        ----------
        source
            The ref to resolve.

        Returns
        -------
        pathlib.Path
            Where the document's bytes are.

        Raises
        ------
        PdfSourceUnreadable
            The token is unknown to the registry, or its invocation has ended.
        """
        try:
            return self._registry.resolve(source)
        except SourceMissingError as exc:
            raise PdfSourceUnreadable(source=source.token, detail=exc.detail) from exc
