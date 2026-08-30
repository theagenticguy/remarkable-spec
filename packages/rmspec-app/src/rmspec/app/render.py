"""Turn a document's pages into SVG, and optionally into pixels, exactly once.

One pipeline, owned here, because three commands need it
-------------------------------------------------------
``rmspec render``, ``rmspec ocr`` and ``rmspec diagram`` all need the same four
steps -- fetch the bundle, decode the scene bytes, render to SVG, rasterize -- and
the two paid commands additionally need the *identity* of what was rendered, since
:class:`~rmspec.domain.models.OcrCacheKey` folds in both the render digest and the
raster digest. A second copy of the pipeline is how two commands come to render
different pixels for the same page and then disagree about a cache key, so
:class:`TranscribePages` and the diagram pass take a :class:`RenderPages` and call
it rather than re-deriving any of it. That makes this class the single place a
``page_hash``, a ``render_digest`` and a ``RasterImage`` are produced.

Why ``RawBundleSource`` rather than ``DocumentRepository``
---------------------------------------------------------
:class:`~rmspec.domain.ports.device.RawBundleSource` says so itself: "This is the
live retrieval path: it is what renders, OCR, and diagram extraction consume."
The north star is a tablet that is plugged in and powered on, so the source of
truth for a render is the device, not an already-pulled mirror -- and the mirror is
bindable *behind* this same port, which is what makes one use case serve both.
:class:`~rmspec.domain.ports.formats.DocumentRepository` is the other altitude: it
would hand back an assembled :class:`~rmspec.domain.models.Page` and a
``page_fingerprint``, which is strictly less work here, but it would also make
:class:`~rmspec.domain.ports.formats.PageCodec` an unreachable collaborator, since
a repository calls the codec internally. Binding both would give this use case two
ways to obtain one page and two chances for them to disagree.

The cost is explicit and it is paid here: assembling a
:class:`~rmspec.domain.models.Page` from a
:class:`~rmspec.domain.ports.device.DevicePageSource` -- the page identity, its
document-order index, its template, and the defect that explains a missing scene --
is work a repository adapter would otherwise do. It is a fixed six lines over an
*already decoded and already ordered* bundle, and no xochitl filename, JSON key or
``cPages`` offset appears in it, because the transport adapter decoded all of that
before the bundle crossed the boundary.

An empty scene is a page with no ink, not a parse failure
--------------------------------------------------------
62 of the 92 real ``.rm`` files in the reference corpus are zero bytes: they are the
stub pages of a PDF-backed document nobody has annotated. Legacy fed every one of
them to the parser and raised a bare ``EOFError``, and that crash reaches ``render``,
``ocr`` and ``diagram`` today. Here a page whose ``scene`` is ``None`` **or** empty is
never handed to the codec at all: it becomes a contentless
:class:`~rmspec.domain.models.Page` carrying
:attr:`~rmspec.domain.models.PageDefectCode.ARTIFACT_ABSENT`, which is the same value
:meth:`~rmspec.domain.ports.formats.DocumentRepository.load_page` returns for the
identical state, and it renders as a blank page.

Both spellings of "no ink" also produce one ``page_hash``, and it is
:data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` rather than
``sha256(b"")``. That constant exists for exactly this: it is "deliberately not a hex
digest ... a caller may compare against it to tell 'nothing was ever drawn here' from
'these bytes hashed to this'". Hashing the empty string instead would give a
transport that reports ``b""`` and one that reports ``None`` two different cache keys
for one unannotated page.

That is a fact about the page, not a substitution, so it is **not** a
:class:`~rmspec.domain.errors.Degradation`. Nothing was guessed and nothing was put in
place of anything: the page has no ink and a blank page was produced.
``DegradationKind.PAGE_NOT_ANNOTATED`` is the member a reader reaches for, and its own
docstring rules it out -- it means the page "was skipped rather than pushed through the
pipeline as an empty page", and this use case deliberately does not skip it.

Why nothing here reports a degradation
--------------------------------------
:attr:`RenderPagesResult.degradations` is always empty, and the field is present
because convention 3 requires it of every result and because the field must exist
before a member can be added. That is not an oversight; it is the closed enum doing
its job. The render slice has two vocabularies for "something changed and we
survived" -- :class:`~rmspec.domain.ports.render.RenderNoticeCode` on the markup and
:class:`~rmspec.domain.models.PageDefectCode` on the decode -- and
:class:`~rmspec.domain.errors.DegradationKind` has no member corresponding to any of
their six values. Mapping one onto an unrelated member would be worse than reporting
none, for the reason :mod:`rmspec.app.resolve` gives about the trash filter: adding a
member is a reviewed change to the domain, not something this module may decide.

So both vocabularies travel back verbatim instead.
:attr:`RenderedPageArtifact.rendered` carries
:attr:`~rmspec.domain.ports.render.RenderedPage.notices`, and the decoded
:attr:`~rmspec.domain.models.PageContent.defects` are reachable through the same
value, so a ``--strict`` boundary has everything it needs and loses nothing.

Two of the three notices argue for staying out of the degradation summary on their
own merits. ``VIEWPORT_EXPANDED`` fires on a real page every time -- measured, with a
uniform 10.6 mm margin kept around the page box -- so promoting it would put one
degradation on every page of every correct render and make a degradation count
useless as a signal. ``UNDERLAY_RESCALED`` cannot occur here at all, because this use
case never composes an underlay (see below). ``TEXT_OMITTED`` is the one that
genuinely changes what the artifact means: a page of typed words came back with the
words missing, and a caller that only summarises degradations would not learn it.
That is the argument for a ``DegradationKind`` member, and it is recorded here as the
domain change it would be rather than smuggled in under a member that means something
else.

What this use case deliberately does not do
-------------------------------------------
**It does not compose a PDF underlay.** Putting the annotated page of a PDF behind the
ink needs :class:`~rmspec.domain.ports.export.PdfPageReader`, which is an export-slice
port and not a collaborator here, plus the redirection-map policy that
``DegradationKind.PDF_PAGE_INDEX_FALLBACK`` exists for. So a PDF-backed document
renders its ink and not its pages. What the request *does* carry is
:attr:`RenderPagesRequest.background`, already-read template markup and/or
already-rasterized pixels, because that keeps the filesystem out of this layer while
still making :meth:`~rmspec.domain.ports.render.RenderStyle.digest` total -- a render
identity that omitted the background is the stale-row defect one component along.

**It does not re-check the renderer's page attribution.**
:meth:`~rmspec.domain.ports.render.PageRenderer.render` promises its ``page_ref``
identifies the page it was given, and the shared adapter contract suite pins that
against the input page for every adapter and every double. Re-asserting it here would
duplicate a gate one layer up, and there is no honest error in the tree to raise if it
failed. So every value that describes the markup -- ``page_ref``, ``svg``, ``size`` --
is taken from the :class:`~rmspec.domain.ports.render.RenderedPage`, and every value
that describes the source -- the index and the hash -- is taken from the bundle.

**It does not swallow an undecodable page.** A codec's ``CorruptPageData`` or
``UnsupportedPageFormat`` propagates. A repository would turn them into a
``CONTENT_UNDECODABLE`` defect and hand back a contentless page, and the port that
does so says strictness is then "an explicit policy on the use case reading
``defects``". This is that policy, and for a command whose whole output is an artifact
a human will look at once and trust, the answer is to fail: a corrupt page rendered as
a plausible blank one is indistinguishable from a page that really was blank.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field, ValidationError

from rmspec.app.selection import PageSelection
from rmspec.domain.errors import Degradation, MalformedDocument
from rmspec.domain.models import Page, PageDefect, PageDefectCode, PageId, Palette, ScreenSpec
from rmspec.domain.ports.export import PhysicalSize as ExportPhysicalSize
from rmspec.domain.ports.export import RasterImage, SvgPage
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT
from rmspec.domain.ports.render import PageBackground, RenderedPage, RenderStyle

if TYPE_CHECKING:
    from rmspec.domain.ports.device import DevicePageSource, RawBundleSource
    from rmspec.domain.ports.export import SvgRasterizer
    from rmspec.domain.ports.formats import PageCodec
    from rmspec.domain.ports.render import PageRenderer

__all__ = ["RenderPages", "RenderPagesRequest", "RenderPagesResult", "RenderedPageArtifact"]

_NO_SCENE: Final = "the device holds no scene bytes for this page, so it has no ink"
"""Detail of the defect a stub page carries, in place of the legacy ``EOFError``."""

_PAGE_ORDER: Final = "page order"
"""Which artifact :class:`~rmspec.domain.errors.MalformedDocument` names for a bad page id."""


def _page_hash(scene: bytes | None, /) -> str:
    """Fingerprint one page's stored scene bytes for a cache key.

    Parameters
    ----------
    scene
        The page's scene bytes as the transport reported them. ``None`` and ``b""``
        are the two spellings of "no artifact" and must not produce two keys.

    Returns
    -------
    str
        Lowercase hex SHA-256 of the bytes, which is what
        :attr:`~rmspec.domain.models.OcrCacheKey.page_hash` documents itself as, or
        :data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` when there
        were no bytes at all.
    """
    if not scene:
        return ABSENT_ARTIFACT_FINGERPRINT
    return hashlib.sha256(scene).hexdigest()


def _page_id(page_ref: str, /, *, document_uuid: str) -> PageId:
    """Adopt a transport's page identifier as a domain identity.

    :class:`~rmspec.domain.models.PageId` constrains its charset and length so a
    store may join it into a path without sanitising it first, which means a
    transport that reports something else produces a ``pydantic.ValidationError``.
    This layer raises only from :mod:`rmspec.domain.errors`, so that is translated
    rather than allowed out.

    Parameters
    ----------
    page_ref
        The identifier the transport reported for this page.
    document_uuid
        The document being rendered, which the raised error names.

    Returns
    -------
    PageId
        The page's domain identity.

    Raises
    ------
    MalformedDocument
        The identifier cannot be a page identity, so the document's page order
        decoded to something no aggregate can be built from. A ``FormatError``
        rather than a device error, because
        :class:`~rmspec.domain.errors.MalformedDeviceMetadata` requires a
        ``TransportKind`` this layer must not learn -- ports are addresses, not
        transports.
    """
    try:
        return PageId(uuid=page_ref)
    except ValidationError as error:
        raise MalformedDocument(
            document_uuid=document_uuid,
            artifact=_PAGE_ORDER,
            detail=f"{page_ref!r} is not a usable page identifier",
        ) from error


def _as_svg_page(rendered: RenderedPage, /) -> SvgPage:
    """Re-wrap a rendered page as the export slice's equivalent value.

    The one hop in this package that :mod:`rmspec.domain.ports.render` predicted:
    "every export and OCR use case re-copies ``RenderedPage.size`` field by field
    into an ``SvgPage``, and a transposed ``width_mm``/``height_mm`` in that re-wrap
    type checks clean and silently rotates the page". The two
    :class:`PhysicalSize` classes are field-for-field twins in two ports modules,
    pydantic models are nominal, so the cross-assignment is refused at runtime --
    ``Input should be a valid dictionary or instance of PhysicalSize`` -- and there
    is no structural adopter on the export side to route around it the way
    :meth:`~rmspec.domain.ports.ocr.RasterImage.from_raster` does one hop later.

    So the splat is forced, and the mitigation is that it exists exactly once, takes
    exactly one argument, and derives every field of the result from that argument.
    A caller cannot pass a ``page_ref`` from somewhere else, which is the shape that
    attributes one page's markup to another while type-checking clean.

    Parameters
    ----------
    rendered
        The finished page, and the sole source of all three fields below.

    Returns
    -------
    SvgPage
        The same page, addressed in the export slice's vocabulary.
    """
    return SvgPage(
        page_ref=rendered.page_ref,
        svg=rendered.svg,
        size=ExportPhysicalSize(
            width_mm=rendered.size.width_mm,
            height_mm=rendered.size.height_mm,
        ),
    )


class RenderPagesRequest(BaseModel, frozen=True, extra="forbid"):
    """One document, which of its pages, and every input that changes a pixel.

    Every field that reaches
    :meth:`~rmspec.domain.ports.render.RenderStyle.digest` is required, so a render
    identity that omits a component is unconstructible here as well as there.
    """

    document_uuid: str = Field(min_length=1)
    """The document to render, already resolved to one identifier.

    Resolution is :class:`~rmspec.app.resolve.ResolveDocument`'s job, so this use
    case never sees a name substring and cannot resolve one ambiguously.
    """

    selection: PageSelection
    """Which pages to render, 0-based, unresolved against any page count."""

    max_pages: int
    """The most pages this run may render, checked before any page is decoded.

    Unconstrained by pydantic on purpose:
    :meth:`~rmspec.app.selection.PageSelection.resolve_against` raises
    :class:`~rmspec.domain.errors.InvalidSettingError` for a non-positive cap, and
    the domain naming a condition means this package raises the domain's error for
    it rather than adding a second vocabulary the CLI must render.
    """

    screen: ScreenSpec
    """Screen geometry to render for. No default, because a wrong screen produces a
    wrong-sized page with correctly placed ink."""

    palette: Palette
    """Palette resolving every pen colour to ink, total by its own validator."""

    style: RenderStyle
    """Thickness, padding, text policy and renderer revision."""

    background: PageBackground | None = None
    """Already-read template markup and/or already-rasterized pixels, or ``None``.

    Markup rather than a path, so no use case opens a file: whichever edge accepted
    ``--background`` did the read. It is a request field rather than a constant
    because :meth:`~rmspec.domain.ports.render.RenderStyle.digest` folds it in, and a
    background that could be omitted from the identity is a cached OCR row that looks
    valid and was produced under a different picture.
    """

    raster_dpi: int | None = Field(default=None, gt=0)
    """Rasterize each page at this resolution, or ``None`` to render SVG only.

    Constrained rather than validated in the body: the export slice deleted its
    ``InvalidResolution`` error precisely because "pydantic constraints refuse the
    bad value at construction; the CLI maps ``ValidationError`` once at its
    boundary". ``None`` is the whole of "do not rasterize", which is what keeps
    ``rmspec render`` to one backend call per page instead of two.
    """


class RenderedPageArtifact(BaseModel, frozen=True, extra="forbid"):
    """One rendered page: its markup, its pixels when asked for, and its identity.

    The identity fields are the reason this type exists rather than a bare
    :class:`~rmspec.domain.ports.render.RenderedPage`. A caller that has to key a
    cache needs ``page_hash`` and the pixels' own digest, and deriving either of them
    a second time somewhere else is how two commands come to disagree about whether a
    page is cached.
    """

    page_ref: str = Field(min_length=1)
    """Identity of the page this markup depicts, taken from the rendered page.

    Taken from :attr:`~rmspec.domain.ports.render.RenderedPage.page_ref` rather than
    from the bundle, so this value, the markup and the raster share one attribution.
    The renderer's obligation to echo the page it was given is pinned by the shared
    adapter contract suite; see this module's docstring.
    """

    page_index: int = Field(ge=0)
    """Zero-based position in the document, taken from the bundle's page order.

    0-based like everything else in :mod:`rmspec.app`. The CLI is 1-based and
    converts at its own boundary; nothing here adds or subtracts one.
    """

    page_hash: str = Field(min_length=1)
    """Lowercase hex SHA-256 of the page's scene bytes, or the absent-artifact token.

    The ``page_hash`` component of an
    :class:`~rmspec.domain.models.OcrCacheKey`. It is
    :data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` exactly when the
    device held no scene bytes, which is also the one way a caller can tell an
    unannotated stub from a page whose bytes happen to decode to nothing.
    """

    rendered: RenderedPage
    """The markup, the page's physical size, both content counts, and the notices.

    Carried whole rather than unpacked, so a caller reading ``notices`` reads the
    renderer's own values and not a copy this layer made of them.
    """

    raster: RasterImage | None
    """The page's pixels, or ``None`` when :attr:`RenderPagesRequest.raster_dpi` was.

    No default. A caller cannot construct this without saying whether it rasterized,
    which is the reason :class:`~rmspec.domain.ports.device.DeviceListing` gives for
    the same choice about ``skipped``. The pixels carry their own ``render_dpi`` and
    their own :meth:`~rmspec.domain.ports.export.RasterImage.digest`, so the raster
    component of a cache key is read off the bytes rather than remembered separately.
    """


class RenderPagesResult(BaseModel, frozen=True, extra="forbid"):
    """Every page that was rendered, plus the render identity they share.

    No field has a default, so a construction site cannot forget the identity a
    cache key needs.
    """

    document_uuid: str = Field(min_length=1)
    """The document that was rendered."""

    pages: tuple[RenderedPageArtifact, ...]
    """The rendered pages, in ascending document order.

    Empty only when the document has no pages, which is what
    :meth:`~rmspec.app.selection.PageSelection.resolve_against` returns for one:
    naming no page is not an error, and a caller that wants it to be checks the
    length. A selection naming a page the document does not have raised
    :class:`~rmspec.domain.errors.PageNotFound` long before here.
    """

    render_digest: str = Field(min_length=1)
    """:meth:`~rmspec.domain.ports.render.RenderStyle.digest` for this whole call.

    One value rather than one per page, because it is one value: the style, the
    screen, the palette and the background are request fields, so every page of one
    call shares the render identity. Two spellings of one fact is how the two come to
    disagree, so there is only this one.
    """

    degradations: tuple[Degradation, ...]
    """Always empty here, and required anyway. See this module's docstring for why:
    the render slice's own substitution vocabularies have no
    :class:`~rmspec.domain.errors.DegradationKind` member, so they travel back on
    :attr:`RenderedPageArtifact.rendered` instead of being mapped onto a member that
    means something else."""


class RenderPages:
    """Decode, render, and optionally rasterize the selected pages of one document.

    Four collaborators, all Protocols, all keyword-only. It reads the bundle once
    per call -- one command is one device handshake -- and holds no state between
    calls.

    Notes
    -----
    This is the pipeline the two paid commands are built on, so it is called rather
    than copied::

        artifacts = RenderPages(
            bundles=bundles, codec=codec, renderer=renderer, rasterizer=rasterizer
        ).render(
            RenderPagesRequest(
                document_uuid=doc,
                selection=PageSelection.all(),
                max_pages=50,
                screen=PAPER_PRO_SCREEN,
                palette=EXPORT_PALETTE,
                style=style,
                raster_dpi=300,
            )
        )
    """

    def __init__(
        self,
        *,
        bundles: RawBundleSource,
        codec: PageCodec,
        renderer: PageRenderer,
        rasterizer: SvgRasterizer,
    ) -> None:
        self._bundles = bundles
        self._codec = codec
        self._renderer = renderer
        self._rasterizer = rasterizer

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        """Render the requested pages of the requested document.

        Parameters
        ----------
        request
            The document, the page selection, the work cap, and every input that
            changes a pixel.

        Returns
        -------
        RenderPagesResult
            One artifact per selected page in ascending document order, and the
            render digest they share.

        Raises
        ------
        InvalidSettingError
            ``request.max_pages`` is not positive, so no selection could satisfy it.
        PageNotFound
            The selection names a page index the document does not have. Raised
            before any page is decoded or rendered.
        UsageError
            The resolved selection is larger than ``request.max_pages``. Raised
            before any page is decoded, so a 432-page document cannot silently
            become 432 rasterizations.
        MalformedDocument
            A page identifier the transport reported cannot be a page identity.
        DeviceDocumentNotFound
            Raised by the bundle source.
        MalformedDeviceMetadata
            Raised by the bundle source.
        DeviceTransferInterrupted
            Raised by the bundle source. Never degraded here: a half-pulled document
            must not be rendered as though it were whole.
        DeviceUnreachable
            Raised by the bundle source.
        DeviceAuthFailed
            Raised by the bundle source.
        DeviceProtocolError
            Raised by the bundle source.
        CorruptPageData
            The page's scene bytes will not decode. Not degraded into a blank page;
            see this module's docstring.
        UnsupportedPageFormat
            The page's scene bytes are a scene version this codec does not decode.
        UnsupportedPenType
            Raised by the renderer.
        BackgroundUnreadable
            Raised by the renderer.
        RasterizationFailed
            Raised by the rasterizer.
        """
        bundle = self._bundles.load_bundle(request.document_uuid)
        indices = request.selection.resolve_against(
            len(bundle.pages),
            document_uuid=request.document_uuid,
            max_pages=request.max_pages,
        )
        return RenderPagesResult(
            document_uuid=request.document_uuid,
            pages=tuple(
                self._one_page(bundle.pages[index], index=index, request=request)
                for index in indices
            ),
            render_digest=request.style.digest(
                screen=request.screen,
                palette=request.palette,
                background=request.background,
            ),
            degradations=(),
        )

    def _one_page(
        self,
        source: DevicePageSource,
        /,
        *,
        index: int,
        request: RenderPagesRequest,
    ) -> RenderedPageArtifact:
        """Decode, render and optionally rasterize one page of a bundle.

        Parameters
        ----------
        source
            The page as the device holds it: identity, scene bytes, template.
        index
            The page's zero-based position in the document, which is its position in
            the bundle's ordered page tuple.
        request
            The whole request, for the render inputs and the raster resolution.

        Returns
        -------
        RenderedPageArtifact
            The markup, the pixels when asked for, and the page's identity.
        """
        page = self._decode(source, index=index, document_uuid=request.document_uuid)
        rendered = self._renderer.render(
            page,
            screen=request.screen,
            palette=request.palette,
            style=request.style,
            background=request.background,
        )
        raster = (
            None
            if request.raster_dpi is None
            else self._rasterizer.to_png(_as_svg_page(rendered), dpi=request.raster_dpi)
        )
        return RenderedPageArtifact(
            page_ref=rendered.page_ref,
            page_index=index,
            page_hash=_page_hash(source.scene),
            rendered=rendered,
            raster=raster,
        )

    def _decode(
        self,
        source: DevicePageSource,
        /,
        *,
        index: int,
        document_uuid: str,
    ) -> Page:
        """Assemble one bundle page into the domain page a renderer accepts.

        The only branch is the one legacy did not have: a page the document lists
        with no scene bytes is never handed to the codec, so a zero-byte stub is a
        page with no ink instead of an ``EOFError``.

        Parameters
        ----------
        source
            The page as the device holds it.
        index
            The page's zero-based position in the document.
        document_uuid
            The owning document, named by
            :class:`~rmspec.domain.errors.MalformedDocument` if the page identifier
            is unusable.

        Returns
        -------
        Page
            A page with decoded content, or a contentless page carrying
            :attr:`~rmspec.domain.models.PageDefectCode.ARTIFACT_ABSENT`.
        """
        page_id = _page_id(source.page_id, document_uuid=document_uuid)
        if not source.scene:
            return Page(
                page_id=page_id,
                index=index,
                template_name=source.template_name,
                content=None,
                defects=(PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail=_NO_SCENE),),
            )
        return Page(
            page_id=page_id,
            index=index,
            template_name=source.template_name,
            content=self._codec.decode_page(source.scene, source.page_id),
        )
