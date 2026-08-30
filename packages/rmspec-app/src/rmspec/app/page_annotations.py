"""Read back what a human wrote on a PDF someone pushed to the tablet.

The use case behind this project's headline agent workflow: an agent pushes a document, a
human annotates it with a pen, the agent reads the annotations back. Three facts have to
meet for that to work, and each one is a separate read here -- the printed text of the
source page, the pixels of that page with the reader's ink over them, and the mapping
between the two page numberings. Getting any of them wrong produces an answer that reads
plausibly and describes the wrong page.

The ``redir`` mapping is load-bearing
-------------------------------------
A document's ``.content`` sidecar records, per page, ``cPages.pages[].redir.value``: which
page of the *source PDF* that annotation page belongs to. It is not the identity map --
inserted pages, reordered pages and re-uploads all break it -- so dropping it silently
mis-composites every annotated PDF: the ink of page 7 lands over the print of page 7's
neighbour, the model is asked what changed, and it answers about a page nobody annotated.
The mapping arrives here already decoded, as
:attr:`~rmspec.domain.models.Page.pdf_page_index`, whose own field docstring records that it
was restored to the domain for exactly this reason -- "without it the degradation is the only
reachable path".

The fallback is that degradation, and it is one member of a closed enum:
``DegradationKind.PDF_PAGE_INDEX_FALLBACK``, "no entry in the redirection map, so the page's
position was used". A page whose ``redir`` names PDF page ``0`` is *not* the fallback, and
the difference is a one-character bug: the test for absence is ``is None``, never falsiness.

What this module reads with :class:`~rmspec.domain.ports.export.PdfPageReader`
-----------------------------------------------------------------------------
:meth:`~rmspec.domain.ports.export.PdfPageReader.page_texts` once per run, for the printed
text, and :meth:`~rmspec.domain.ports.export.PdfPageReader.rasterize_page` once per
annotated page, for the pixels the ink is composited over. It never calls
:meth:`~rmspec.domain.ports.export.PdfPageReader.page_count`: ``len(page_texts(...))`` *is*
the page count, as that method's stated postcondition, so taking it from anywhere else would
be a second source of truth that can disagree with the tuple actually being indexed. A
resolved index past the end of that tuple is
:class:`~rmspec.domain.errors.PdfPageOutOfRange` and not a clamp -- that error exists
precisely because the legacy code let misaligned lists give "every page a neighbour's
background".

Two behaviours that are decisions rather than details
----------------------------------------------------
**A page with no annotation artifact is not an error, and is not omitted.** It is an entry
carrying the page's printed text, ``annotations=None``, and a ``PAGE_NOT_ANNOTATED``
degradation. Omitting it would compact the result, and a compacted result renumbers every
page after the gap -- so a caller who says "page 7" and a document that says "page 7" stop
meaning the same thing. This is also the empty-stub case: the legacy ``annotations`` command
is the only one that stats a file for zero length before parsing it, and **62 of 92 real
``.rm`` files in the reference corpus are zero bytes**. Nothing in this layer stats anything;
an empty stub arrives as a page whose ``content`` is ``None``, and a page whose ink was all
erased arrives as content that is blank. Both are "no ink", and neither is worth a model
call.

**PDF-backed documents only, checked before any page is read.** A notebook has no printed
page to compare against, so "what changed" has no referent;
:class:`~rmspec.domain.errors.UsageError` is the answer, raised from the document's own
recorded source. A document whose source the store did not record is refused the same way
rather than assumed: :attr:`~rmspec.domain.models.DocumentMetadata.source` is ``None`` for
"unknown" precisely so that a missing ``.content`` sidecar cannot read as a notebook.

Cost, and the cap that bounds it
--------------------------------
One model call per annotated page, plus one PDF rasterization for each. So
:attr:`~rmspec.app.selection.PageSelection` matters here as much as in transcription, and
its ``max_pages`` is mandatory and checked at the entry boundary, before the first
rasterization -- not inside the loop, where the user has already paid for the pages the
refusal names.

There is no cache for this pass, and that is a fact about the ports rather than an omission:
:mod:`rmspec.domain.ports.persistence` publishes an OCR cache and a diagram cache and
nothing that could key an annotation reading. It is why :data:`_DECODING` fixes temperature
at zero -- with no stored row, nothing downstream could ever notice that two runs answered
differently about identical pixels.

The collaborator this module does not own
----------------------------------------
Decode, render and rasterize belong to one owner -- a sibling use case, ``RenderPages`` --
and this module names the narrowest shape it needs from it as :class:`PageRasterizer`. A
second copy of that pipeline is how two callers come to render different pixels for one
page. Two members are needed rather than one: :attr:`PageRasterizer.page_box`, because a PDF
page has to be rasterized at the scale the ink will be drawn at and the pipeline is what
knows that scale, and :meth:`PageRasterizer.raster_for`, which takes the underlay this
module built and returns the composite. Registration itself is not this module's arithmetic
either: :class:`~rmspec.domain.ports.render.PageUnderlay` carries the *source* page's
real-world size, so the renderer letterboxes a Letter PDF behind a Paper Pro page from a
value rather than from a scale factor computed twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, Self

from pydantic import BaseModel, Field, model_validator

from rmspec.app._degradations import DegradationLog
from rmspec.app.selection import PageSelection
from rmspec.domain.errors import (
    Degradation,
    DegradationKind,
    PdfPageOutOfRange,
    UsageError,
)
from rmspec.domain.models import DocumentId, SourceKind
from rmspec.domain.ports.export import PdfSourceRef
from rmspec.domain.ports.ocr import (
    Decoding,
    RasterImage,
    ReasoningEffort,
    StopReason,
    VisionRequest,
)
from rmspec.domain.ports.render import ImageMedia as UnderlayMedia
from rmspec.domain.ports.render import PageUnderlay
from rmspec.domain.ports.render import PhysicalSize as UnderlaySize

if TYPE_CHECKING:
    from rmspec.domain.models import DocumentSummary, PageContent, PageId
    from rmspec.domain.ports.export import PdfPageReader, PixelSize
    from rmspec.domain.ports.formats import DocumentRepository
    from rmspec.domain.ports.ocr import PageRasterLike, VisionLanguageModel

__all__ = [
    "PageAnnotations",
    "ReadAnnotations",
    "ReadAnnotationsRequest",
    "ReadAnnotationsResult",
]

_SYSTEM: Final = (
    "You compare a printed page against an image of that page after a human wrote on it, "
    "and you report only what the human added. You never restate printed text that carries "
    "no annotation."
)
"""System turn, framed separately from the prompt in the request digest."""

_PROMPT: Final = """\
This image is one page of a PDF with a reader's handwritten annotations drawn over the
printed page.

Report only what the reader added: every mark, in reading order, and what each one does to
the printed text it sits on -- struck out, underlined, circled, boxed, replaced, questioned,
answered. Quote the printed words each mark touches, so that a reader of your answer can
find the place. Do not restate printed text that carries no mark, and do not summarise the
page.

Answer with nothing at all if the image carries no handwriting.
"""
"""User turn. Its bytes are hashed into the request digest, so editing it changes identity."""

_PRINTED_HEADING: Final = "The printed text of this page, as the PDF itself carries it:"
"""Label for the extracted text appended to the prompt."""

_NO_PRINTED_TEXT: Final = (
    "This PDF page carries no extractable text -- it is a scan, or a page of pure "
    "graphics -- so the image is the only evidence of what was printed."
)
"""What to say instead when the PDF page has no text layer, which is a real and common state."""

_DECODING: Final = Decoding(
    max_output_tokens=2048,
    temperature=0.0,
    reasoning=ReasoningEffort.NONE,
)
"""Room for a page of marks, at temperature zero because no cache row could catch drift.

Larger than the diagram pass's budget, because a densely annotated page is a list rather
than one code block, and smaller than a transcription merge's, because printed text is not
being transcribed. No latent reasoning: the task is to read marks, not to solve anything,
and the effort is a per-call value so a future pass may raise it without rebinding a model.
"""

_UNDERLAY_OVERSAMPLE: Final = 2
"""Supersampling applied to the PDF page behind the ink.

Required by :meth:`~rmspec.domain.ports.export.PdfPageReader.rasterize_page` rather than
defaulted there, so that the figure has exactly one owner per call site; ``2`` is what the
legacy background path used and what :meth:`~rmspec.domain.ports.export.PixelSize.fit_within`
carries as its own default.
"""


class RasterizedPage(Protocol):
    """One page's pixels and the render identity that produced them, as read-only members.

    Read-only properties, hence covariant: a concrete result model whose ``raster`` field is
    either slice's raster twin satisfies this, exactly as
    :class:`~rmspec.domain.ports.ocr.PageRasterLike` is satisfied by both twins today.
    """

    @property
    def raster(self) -> PageRasterLike:
        """The composited pixels, in whichever raster twin the producer holds."""
        ...

    @property
    def render_digest(self) -> str:
        """``RenderStyle.digest`` for the render that produced :attr:`raster`.

        Not consumed by this use case -- there is no annotation cache to key -- and required
        anyway, so that one binding satisfies this expectation and
        :class:`rmspec.app.diagrams.PageRasterizer`'s, which does key a cache with it.
        """
        ...


class PageRasterizer(Protocol):
    """The decode-render-rasterize pipeline, at the width this use case needs it.

    A sibling use case (``RenderPages``) owns the pipeline; this is the narrowest statement
    of what reading annotations needs from it, so that no second copy exists here.
    Structural, so the one binding satisfies both this and
    :class:`rmspec.app.diagrams.PageRasterizer`.
    """

    @property
    def page_box(self) -> PixelSize:
        """The pixel box this pipeline rasterizes a page into.

        Needed because a PDF page is rasterized to sit behind the ink, so its resolution has
        to be chosen against the ink's, and the pipeline is the one component that knows
        the screen geometry and the render DPI. Asking for it is what keeps a second copy of
        that arithmetic -- and therefore a second answer -- out of this module. It is a
        resolution choice and not a registration one: the renderer fits the underlay from
        :attr:`~rmspec.domain.ports.render.PageUnderlay.source_size`.
        """
        ...

    def raster_for(
        self,
        doc_id: DocumentId,
        page_id: PageId,
        /,
        *,
        underlay: PageUnderlay | None = None,
    ) -> RasterizedPage:
        """Render one page's ink over an optional underlay, and rasterize the result.

        Parameters
        ----------
        doc_id
            The document the page belongs to.
        page_id
            The page to render.
        underlay
            Already-rasterized pixels to draw beneath the ink, or ``None`` for ink alone.

        Returns
        -------
        RasterizedPage
            The composited pixels and the render digest that produced them.
        """
        ...


class PageAnnotations(BaseModel, frozen=True, extra="forbid"):
    """What one page of an annotated PDF holds, whether or not anyone wrote on it.

    Entries are one-to-one with the pages the selection resolved, in ascending order, so
    :attr:`page_index` is a position and never an offset into a compacted list.
    """

    page_index: int = Field(ge=0)
    """Zero-based position of the annotation page in the document."""

    page_ref: str = Field(min_length=1)
    """The page's own identity, for a message or a filename. Never parsed."""

    pdf_page_index: int = Field(ge=0)
    """Zero-based page of the source PDF this page annotates, after the ``redir`` mapping.

    Equal to :attr:`page_index` when the mapping was absent and the position was used --
    which is reported as a ``PDF_PAGE_INDEX_FALLBACK`` degradation, so the two cases are
    distinguishable even though the numbers coincide.
    """

    printed_text: str
    """The PDF page's own text, as extracted. Empty for a scanned or graphics-only page."""

    annotations: str | None
    """What the model read off the ink, or ``None`` when the page carries no ink at all.

    ``None`` and ``""`` are different answers: ``None`` means no model was asked, and ``""``
    means one was asked and reported nothing added. Both are legal, and conflating them is
    how a page whose ink failed to render reads as a page nobody wrote on.
    """

    stop_reason: StopReason | None
    """Why the model stopped, verbatim, or ``None`` when no model was called."""

    @property
    def truncated(self) -> bool:
        """Whether generation stopped at the output limit rather than on its own terms.

        Returns
        -------
        bool
            ``True`` only for :attr:`~rmspec.domain.ports.ocr.StopReason.OUTPUT_LIMIT`. A
            densely annotated page is the realistic way to reach it, and it is data rather
            than an error: half a list of marks is worth reporting *as* half a list.
        """
        return self.stop_reason is StopReason.OUTPUT_LIMIT

    @model_validator(mode="after")
    def _check_an_unread_page_claims_no_completion(self) -> Self:
        """Reject a page nothing was read for that nonetheless says why the model stopped.

        Returns
        -------
        PageAnnotations
            The validated model.

        Raises
        ------
        ValueError
            If :attr:`annotations` is ``None`` while :attr:`stop_reason` is set, which would
            be a page reported as never examined and as examined at once.
        """
        if self.annotations is None and self.stop_reason is not None:
            msg = (
                f"page {self.page_ref} was not read, so it cannot report stop reason "
                f"{self.stop_reason.value!r}"
            )
            raise ValueError(msg)
        return self


class ReadAnnotationsRequest(BaseModel, frozen=True, extra="forbid"):
    """Which pages of which annotated PDF to read, and how many are affordable."""

    doc_id: DocumentId
    """The document to read. Already resolved -- see :mod:`rmspec.app.resolve`."""

    source: PdfSourceRef
    """Opaque handle to the document's source PDF.

    A field rather than something this use case derives, because
    :class:`~rmspec.domain.ports.export.PdfSourceRef` is "an opaque handle to an existing
    PDF a reader may open but a use case cannot locate" -- whichever adapter already knows
    where the bytes live mints it, and this layer may only pass it and compare it.
    """

    pages: PageSelection
    """Which annotation pages, 0-based. The CLI converts from the numbers a human types."""

    max_pages: int
    """The most pages this run may read. Unconstrained here on purpose: a non-positive cap is
    an :class:`~rmspec.domain.errors.InvalidSettingError` raised by
    :meth:`~rmspec.app.selection.PageSelection.resolve_against`, and a pydantic constraint
    here would answer a condition the domain has already named."""


class ReadAnnotationsResult(BaseModel, frozen=True, extra="forbid"):
    """One entry per selected page, and everything this pass substituted instead of failing."""

    pages: tuple[PageAnnotations, ...]
    """One entry per selected page, ascending, pages nobody wrote on included."""

    degradations: tuple[Degradation, ...]
    """``PDF_PAGE_INDEX_FALLBACK`` per page whose ``redir`` entry was missing, and
    ``PAGE_NOT_ANNOTATED`` per page with no ink."""


def _prompt_for(printed: str, /) -> str:
    """Build the user turn for one page, printed text included.

    Parameters
    ----------
    printed
        The PDF page's extracted text, which may be empty.

    Returns
    -------
    str
        The instructions followed by the printed text, or by a statement that there is none.
        Appending it as prose is safe against boundary confusion because
        :meth:`~rmspec.domain.ports.ocr.VisionRequest.digest` length-frames every component,
        so text embedded here -- separator bytes included -- cannot shift the boundary
        between this prompt and another request's key.
    """
    body = printed.strip()
    if not body:
        return f"{_PROMPT}\n{_NO_PRINTED_TEXT}\n"
    return f"{_PROMPT}\n{_PRINTED_HEADING}\n\n{body}\n"


class ReadAnnotations:
    """Read the handwritten annotations on selected pages of one PDF-backed document.

    Four collaborators, every one of them a Protocol, and one of them --
    :class:`PageRasterizer` -- a sibling use case rather than a port, because the pipeline it
    names must have exactly one implementation.

    Notes
    -----
    The three reads happen in a fixed order, and the order is what keeps the money at the
    end: the document's source is checked, the cap is applied, the PDF's text is extracted
    once, and only then is a page rasterized or a model asked::

        result = reader.read(
            ReadAnnotationsRequest(
                doc_id=doc, source=ref, pages=PageSelection.first(5), max_pages=20
            )
        )
        wrote_on = [p for p in result.pages if p.annotations is not None]
    """

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        pdf: PdfPageReader,
        rasterizer: PageRasterizer,
        model: VisionLanguageModel,
    ) -> None:
        self._repository = repository
        self._pdf = pdf
        self._rasterizer = rasterizer
        self._model = model

    def read(self, request: ReadAnnotationsRequest, /) -> ReadAnnotationsResult:
        """Read every selected page's annotations, and report the pages that had none.

        Parameters
        ----------
        request
            The document, its source PDF, the pages, and the cap.

        Returns
        -------
        ReadAnnotationsResult
            One entry per selected page in ascending order -- pages nobody wrote on
            included, so indices stay one-to-one with the document's pages -- and every
            substitution made.

        Raises
        ------
        UsageError
            The document is not PDF-backed, or the selection is larger than ``max_pages``.
            Both are raised before any page is rasterized.
        InvalidSettingError
            ``max_pages`` is not positive, raised by
            :meth:`~rmspec.app.selection.PageSelection.resolve_against`.
        PageNotFound
            An explicitly selected index is not a page of this document.
        PdfPageOutOfRange
            A page's ``redir`` entry, or the position used in its absence, names a page the
            source PDF does not have. Never clamped to a neighbour.
        PdfSourceUnreadable
            Raised by the PDF reader: the source is missing, is not a PDF, is corrupt, or
            needs a password.
        DocumentNotFound
            Raised by the repository.
        MalformedDocument
            Raised by the repository.
        DocumentStoreUnavailable
            Raised by the repository.
        RasterizationFailed
            Raised by the PDF reader or the render pipeline.
        UnsupportedPenType
            Raised by the render pipeline for a stroke whose pen it does not implement.
        BackgroundUnreadable
            Raised by the render pipeline.
        ModelUnavailable
            Raised by the model binding, and never degraded here: a page that could not be
            examined must not be reported as a page nobody wrote on.
        ModelAccessDenied
            Raised by the model binding.
        ModelThrottled
            Raised by the model binding.
        ModelRejectedRequest
            Raised by the model binding.
        ModelResponseMalformed
            Raised by the model binding.
        """
        summary = self._repository.summary(request.doc_id)
        if summary.metadata.source is not SourceKind.PDF:
            observed = (
                "unrecorded" if summary.metadata.source is None else summary.metadata.source.value
            )
            raise UsageError(
                subject=f"document {request.doc_id.uuid}, whose source is {observed}",
                requirement="a pdf-backed document, the only kind with annotations to read",
            )
        indices = request.pages.resolve_against(
            summary.page_count,
            document_uuid=request.doc_id.uuid,
            max_pages=request.max_pages,
        )
        printed = self._pdf.page_texts(request.source)
        log = DegradationLog()
        pages = tuple(
            self._read_page(request, summary, index, printed, log=log) for index in indices
        )
        return ReadAnnotationsResult(pages=pages, degradations=log.frozen())

    def _read_page(
        self,
        request: ReadAnnotationsRequest,
        summary: DocumentSummary,
        index: int,
        printed: tuple[str, ...],
        *,
        log: DegradationLog,
    ) -> PageAnnotations:
        """Read one page: resolve its PDF page, then either skip it or pay for it.

        Parameters
        ----------
        request
            The whole request, for the document identity and the source handle.
        summary
            The document's summary, which is where the page identity at ``index`` comes
            from.
        index
            The 0-based page position to read.
        printed
            Every PDF page's text, in document order. Its length is the PDF's page count.
        log
            The run's degradation log.

        Returns
        -------
        PageAnnotations
            This page's entry, with ``annotations=None`` when it carries no ink.

        Raises
        ------
        PdfPageOutOfRange
            The resolved PDF page index is not a page of the source PDF.
        """
        page_id = summary.pages[index]
        page = self._repository.load_page(request.doc_id, page_id)
        pdf_page_index = page.pdf_page_index
        if pdf_page_index is None:
            pdf_page_index = index
            log.record(
                Degradation(
                    kind=DegradationKind.PDF_PAGE_INDEX_FALLBACK,
                    subject=page.ref,
                    detail=(
                        "the document's redirection map has no entry for this page, so its "
                        "own position was used as the source pdf page"
                    ),
                    substituted=str(index),
                )
            )
        if pdf_page_index >= len(printed):
            raise PdfPageOutOfRange(
                source=request.source.token,
                page_index=pdf_page_index,
                page_count=len(printed),
            )
        content = page.content
        if content is None or content.is_blank:
            log.record(
                Degradation(
                    kind=DegradationKind.PAGE_NOT_ANNOTATED,
                    subject=page.ref,
                    detail=_no_ink_detail(content),
                )
            )
            return PageAnnotations(
                page_index=index,
                page_ref=page.ref,
                pdf_page_index=pdf_page_index,
                printed_text=printed[pdf_page_index],
                annotations=None,
                stop_reason=None,
            )
        rendered = self._rasterizer.raster_for(
            request.doc_id,
            page_id,
            underlay=self._underlay(request.source, page_index=pdf_page_index),
        )
        completion = self._model.complete(
            VisionRequest(
                prompt=_prompt_for(printed[pdf_page_index]),
                decoding=_DECODING,
                system=_SYSTEM,
                images=(RasterImage.from_raster(rendered.raster),),
            )
        )
        return PageAnnotations(
            page_index=index,
            page_ref=page.ref,
            pdf_page_index=pdf_page_index,
            printed_text=printed[pdf_page_index],
            annotations=completion.text,
            stop_reason=completion.stop_reason,
        )

    def _underlay(self, source: PdfSourceRef, /, *, page_index: int) -> PageUnderlay:
        """Rasterize one PDF page and adopt it as the render slice's underlay value.

        The two slices' :class:`~rmspec.domain.ports.export.ImageMedia` and
        :class:`~rmspec.domain.ports.export.PhysicalSize` are field-for-field twins that
        pydantic keeps nominally distinct, so this conversion is explicit rather than a
        ``model_dump`` splat -- which would copy the fields and drop the destination's
        validators. It converts by value, so an encoding the render slice does not know
        fails here rather than downstream.

        Parameters
        ----------
        source
            Opaque handle to the source PDF.
        page_index
            Zero-based page of that PDF, already resolved through the ``redir`` mapping.

        Returns
        -------
        PageUnderlay
            The page's pixels plus its native real-world size, which is what lets the
            renderer letterbox a differently shaped source page under the ink.

        Raises
        ------
        PdfPageOutOfRange
            Raised by the PDF reader when the index is not a page of this document.
        PdfSourceUnreadable
            Raised by the PDF reader.
        RasterizationFailed
            Raised by the PDF reader.
        """
        background = self._pdf.rasterize_page(
            source,
            page_index=page_index,
            box=self._rasterizer.page_box,
            oversample=_UNDERLAY_OVERSAMPLE,
        )
        return PageUnderlay(
            media=UnderlayMedia(background.media.value),
            data=background.data,
            source_size=UnderlaySize(
                width_mm=background.page_size.width_mm,
                height_mm=background.page_size.height_mm,
            ),
        )


def _no_ink_detail(content: PageContent | None, /) -> str:
    """Say what "no ink" meant for one page, without naming a file.

    Parameters
    ----------
    content
        The page's decoded content, or ``None`` when the store held no scene artifact at all.

    Returns
    -------
    str
        The distinction a reader of a warning wants: an unannotated page of a pushed PDF --
        the zero-byte stub, and the majority of the reference corpus -- against a page whose
        ink was erased.
    """
    if content is None:
        return "the store holds no scene artifact for this page, so nobody has written on it"
    return "the page's scene artifact decodes to no visible ink"
