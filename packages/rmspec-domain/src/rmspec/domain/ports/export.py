"""Ports for the export slice: rasterize SVG, compose PDF, read PDF, commit bytes.

This module holds four protocols and the frozen value objects they exchange. Nothing
here knows that cairo, cairocffi, cairosvg, Pillow, PyMuPDF, ``fitz`` or a filesystem
exist; the only third-party import is pydantic.

Two technologies are isolated, one adapter each: the SVG rasterizer plus PDF composer
belong to the vector-graphics adapter, and :class:`PdfPageReader` belongs to the PDF
reader adapter. :class:`ArtifactSink` isolates the destination, not a library.

Geometry lives in the domain, not in the adapters
------------------------------------------------
Two DPI conventions were smeared across the legacy code -- ``dpi / 72`` in the PNG path
and ``72 / screen.dpi`` in the PDF path -- so no reader could say what "DPI" meant. Here
there is one statement of it, and it is on :meth:`SvgRasterizer.to_png`: ``dpi`` is
pixels per inch of the produced raster, and an SVG user unit is 1/72 inch, so the
adapter's only conversion is ``scale = dpi / 72``. Page geometry is millimetres
(:class:`PhysicalSize`) and pixels (:class:`PixelSize`); PostScript points appear
nowhere, because points are a PDF/PostScript unit and a domain that speaks them has
already adopted one adapter's coordinate system.

Background fitting is likewise a domain formula, not adapter arithmetic:
:meth:`PixelSize.fit_within` is the single implementation of "fit this PDF page inside
that pixel box, supersampled", and both the real reader and its test double must derive
their pixel dimensions from it. A double that invents ``width_px=8`` -- which every
rejected proposal's fake did -- then fails the shared contract suite instead of making
alignment assertions vacuous.

Values that cannot lie about their bytes
----------------------------------------
:class:`RasterImage` and :class:`PdfPageBackground` validate their declared pixel
dimensions against the PNG ``IHDR`` chunk inside ``data``, and :class:`PdfDocument`
validates that ``data`` opens with ``%PDF-`` and closes with ``%%EOF``. Both checks are
stdlib byte inspection, no decoder. This is what makes the export slice's headline
defects unrepresentable rather than merely reported:

* A rasterizer or reader whose pixel count disagrees with its pixels raises at
  construction, so ``b"png:" + svg`` is not a usable test double any more and a
  supersample or DPI bug cannot pass a green fake.
* A PDF whose buffer was never flushed -- the legacy blank-page/truncated-surface
  failure, where the writer's own page counter still said *N* -- cannot be returned.
* :attr:`PdfDocument.pages` is provenance, not a self-reported count: the composer must
  derive one :class:`PdfPageRef` per page *from the document it emitted*. The contract
  suite therefore asserts ``[r.page_ref for r in doc.pages] == [p.page_ref for p in
  page_set.pages]``, which is exactly the assertion the legacy "write page one, drop
  pages 2..N" bug fails and a ``page_count=len(pages)`` field could never fail.

What this module deliberately does *not* contain
------------------------------------------------
``probe()`` / ``BackendIdentity``
    Dropped. Health-checking the native library is the composition root's job: the
    adapter's provider imports its package and rasterizes a 1x1 document once, so a
    ``libcairo`` that will not ``dlopen`` fails the build of the container. A ``probe``
    method would make every use case depend on it and every double implement it, to run
    a check the provider can run itself, and ``BackendIdentity`` -- distribution name,
    version, shared-library path -- is meaningless for a pure-Rust wheel or a subprocess
    adapter and nothing consumes it.
``RasterFormat`` / ``UnsupportedRasterFormat``
    Dropped. An enum argument that some bound adapter may legally reject is capability
    negotiation: the caller would have to know which adapter is wired to know what is
    callable. The repo has exactly one raster consumer shape, so the method is named
    :meth:`SvgRasterizer.to_png` and PNG is a promise, not a parameter.
``PdfPageBytes`` / ``to_pdf_page`` / ``expected_pages``
    Dropped from the *contract*, kept inside the adapter. Rendering each SVG page to a
    one-page PDF and then stapling is a real and deliberate implementation -- it is what
    preserves today's vector fidelity instead of silently swapping which library
    rasterizes vectors -- but it is one adapter's internal pipeline. Promoting the
    intermediate blob to a port would make the app layer the pipe carrying one library's
    encoded output into another's merge call, unable to inspect order, size or content.
    ``expected_pages`` goes with it: a caller-supplied invariant on the very method whose
    job is to verify page count is self-agreeing, and it moves the defect one frame up.
``PdfPages`` handle / ``AbstractContextManager``
    Dropped. An ``open()`` returning a closable, index-addressable handle is
    ``fitz.Document`` transcribed, it puts ``with`` blocks over port-returned resources
    into use cases, and it maps onto neither ``Scope.APP`` nor ``Scope.REQUEST``.
    :class:`PdfPageReader` is stateless and path-keyed; caching an open document is the
    adapter's optimisation, invisible here.
``exists()`` / ``open_stream()`` / ``destination: Path`` on a writer
    Dropped. Naming a filesystem path or yielding a writable binary handle in the port
    exists only because a native library wants a seekable sink, and it breaks the very
    destinations a sink is for. Overwrite policy, dry-run, ``mkdir``, temp-file plus
    ``os.replace`` and the ``%%EOF`` flush all live inside the REQUEST-scoped adapter.
``DocumentRenderer`` (Markdown/HTML to PDF)
    Not modelled here, and knowingly so. The push path renders Markdown to PDF with a
    third native stack, which does belong behind a port -- but it takes prose and a
    stylesheet, not :class:`SvgPageSet`, and its only caller is the device slice. Naming
    it in this module would be inventing a contract from no evidence; it is a slice-
    boundary decision for whoever owns push, recorded here so it is not lost.

Errors (named, not imported)
----------------------------
The error tree lives in :mod:`rmspec.domain.errors`, authored after this module. Errors
are named in ``Raises`` sections only and nothing is imported from it -- not even under
``TYPE_CHECKING`` -- so the two files cannot deadlock on naming. Five names are enough,
because the CLI takes only two actions (report-and-stop, or skip-this-page):
``RasterizationFailed``, ``PdfCompositionFailed``, ``PdfSourceUnreadable``,
``PdfPageOutOfRange``, ``ArtifactWriteFailed``.

Deleted on purpose: ``EmptyPageSet`` / ``EmptyPageSequence`` (a state
:class:`SvgPageSet` forbids -- an error class for an unrepresentable state is dead weight
in a tree the type checker walks at error level), ``PdfPageCountMismatch`` and
``PageCountMismatch`` (an adapter postcondition; it wraps its own count readback failure
in ``PdfCompositionFailed`` rather than teaching the app a branch it can never act on),
``PdfSourceEncrypted`` (a password prompt mirrors one library's ``needs_pass``; the app
does the same thing for encrypted and corrupt), ``DestinationExists`` /
``DestinationNotWritable`` (``EEXIST`` and ``EACCES`` relabelled -- the sink's own
policy decides, and one failure carries the reason), and ``InvalidResolution`` (pydantic
constraints refuse the bad value at construction; the CLI maps ``ValidationError`` once
at its boundary).

Availability is *not* a port error (defect 4)
---------------------------------------------
No method here raises "backend unavailable" or "dependency missing". A missing optional
package is a composition failure that names the package and the extra providing it,
raised during the container's eager resolution pass, and that error hangs directly off
the root error -- never under an export error, so a use case's ``except`` cannot swallow
a wiring bug. Keeping such an error in a ``Raises`` section is the 27 legacy
function-local ``ImportError`` raises wearing a typed hat.

Notes for whoever writes ``ports/__init__.py``
----------------------------------------------
:class:`RasterImage` and :class:`ImageMedia` are field-for-field twins of the definitions
in :mod:`rmspec.domain.ports.ocr`, duplicated because a ports module may not import a
sibling and because :class:`SvgRasterizer` is the seam the OCR use case must be given --
the legacy OCR code calls the rasterizing library directly, which would put one
technology in two adapters. Hoist one definition into a shared values module rather than
re-exporting two same-named classes, and when hoisting keep *this* copy's validator: the
digest is identical, but only this one refuses pixel counts that disagree with the bytes.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ArtifactMedia",
    "ArtifactRef",
    "ArtifactSink",
    "ImageMedia",
    "PdfComposer",
    "PdfDocument",
    "PdfPageBackground",
    "PdfPageReader",
    "PdfPageRef",
    "PhysicalSize",
    "PixelSize",
    "RasterImage",
    "SvgPage",
    "SvgPageSet",
    "SvgRasterizer",
]

_FIELD_SEPARATOR = b"\x1f"
"""Byte that separates digest components, so concatenation cannot be ambiguous."""

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IHDR_TAG = b"IHDR"
_PNG_TAG_START = 12
_PNG_TAG_END = 16
_PNG_WIDTH_START = 16
_PNG_HEIGHT_START = 20
_PNG_HEADER_LENGTH = 24
_PNG_INT_WIDTH = 4
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_PDF_SIGNATURE = b"%PDF-"
_PDF_TRAILER = b"%%EOF"
_PDF_TRAILER_WINDOW = 1024
_SVG_ROOT_TAG = "<svg"
_MIN_OVERSAMPLE = 1


def _png_pixel_size(data: bytes) -> tuple[int, int] | None:
    """Read the pixel size a PNG's ``IHDR`` chunk declares.

    Parameters
    ----------
    data
        Candidate PNG bytes.

    Returns
    -------
    tuple[int, int] | None
        ``(width, height)`` from the header, or ``None`` when ``data`` is not a PNG whose
        first chunk is ``IHDR`` -- which every PNG's is, by specification.
    """
    if len(data) < _PNG_HEADER_LENGTH or not data.startswith(_PNG_SIGNATURE):
        return None
    if data[_PNG_TAG_START:_PNG_TAG_END] != _PNG_IHDR_TAG:
        return None
    width = int.from_bytes(data[_PNG_WIDTH_START : _PNG_WIDTH_START + _PNG_INT_WIDTH], "big")
    height = int.from_bytes(data[_PNG_HEIGHT_START : _PNG_HEIGHT_START + _PNG_INT_WIDTH], "big")
    return width, height


def _validate_raster_bytes(media: ImageMedia, data: bytes, width: int, height: int) -> None:
    """Check that encoded pixels agree with the dimensions declared alongside them.

    Parameters
    ----------
    media
        Encoding ``data`` claims to be in.
    data
        Encoded image bytes.
    width
        Declared pixel width.
    height
        Declared pixel height.

    Raises
    ------
    ValueError
        If ``data`` is not in ``media``'s encoding, or its header records dimensions other
        than ``width`` by ``height``.
    """
    if media is ImageMedia.JPEG:
        if not data.startswith(_JPEG_SIGNATURE):
            msg = "media is jpeg but data does not start with a JPEG marker"
            raise ValueError(msg)
        return
    header = _png_pixel_size(data)
    if header is None:
        msg = "media is png but data does not start with a PNG signature and IHDR chunk"
        raise ValueError(msg)
    if header != (width, height):
        msg = f"declared size {width}x{height} disagrees with PNG header {header[0]}x{header[1]}"
        raise ValueError(msg)


class ImageMedia(StrEnum):
    """Encoding of a raster image's bytes, as a domain fact rather than a MIME token.

    A domain enum instead of a content-type string because an HTTP content type is a wire
    label that can lie: the tablet's thumbnail route advertises JPEG and returns PNG
    bytes. The export ports only ever produce :attr:`PNG`; :attr:`JPEG` exists so this
    enum stays a field-for-field twin of the OCR slice's copy, which does receive
    device-supplied bytes.
    """

    PNG = "png"
    JPEG = "jpeg"


class ArtifactMedia(StrEnum):
    """What kind of finished artifact a sink is being handed.

    Three members, because the export slice produces three things. This is the single
    source of truth for an artifact's type: it is what the filesystem adapter uses to
    choose a suffix, so a name and its content cannot disagree the way a caller-chosen
    filename and a separately declared encoding could.
    """

    SVG = "svg"
    PNG = "png"
    PDF = "pdf"


class PhysicalSize(BaseModel):
    """A page's real-world size, in millimetres.

    Millimetres, not PostScript points: points are a PDF/PostScript unit, and a domain
    that speaks them has adopted one adapter's coordinate system. Adapters convert on
    their own boundary (``pt = mm * 72 / 25.4``).

    Attributes
    ----------
    width_mm
        Width in millimetres.
    height_mm
        Height in millimetres.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)


class PixelSize(BaseModel):
    """A raster's size in pixels, plus the one formula that derives it.

    Attributes
    ----------
    width_px
        Width in pixels.
    height_px
        Height in pixels.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)

    @classmethod
    def fit_within(cls, page: PhysicalSize, box: PixelSize, *, oversample: int = 2) -> Self:
        """Scale ``page`` to the largest size fitting inside ``box``, then supersample.

        The legacy background path computed this inline in the PDF adapter, which is why
        no test could reach it: a background rendered at the wrong scale registers
        slightly off under the stroke overlay and only a human eye notices. Both the real
        :class:`PdfPageReader` and its test double must derive
        :attr:`PdfPageBackground.pixel_size` from this method, so a wrong scale is a
        failing equality in the shared contract suite instead of a rendering opinion.

        Parameters
        ----------
        page
            The source page's real-world size.
        box
            The pixel box the rasterized page has to fit inside -- normally the rendered
            note page it will sit behind.
        oversample
            Integer supersampling factor applied after fitting, so downscaling into the
            final composite stays smooth. Defaults to 2, which is what the legacy
            background path used.

        Returns
        -------
        PixelSize
            Fitted, supersampled pixel dimensions, aspect-preserving and at least 1x1.

        Raises
        ------
        ValueError
            If ``oversample`` is below 1.
        """
        if oversample < _MIN_OVERSAMPLE:
            msg = "oversample must be at least 1"
            raise ValueError(msg)
        scale = min(box.width_px / page.width_mm, box.height_px / page.height_mm) * oversample
        return cls(
            width_px=max(1, round(page.width_mm * scale)),
            height_px=max(1, round(page.height_mm * scale)),
        )


class RasterImage(BaseModel):
    """An already-rendered page raster, carried as bytes.

    Bytes, never a path: no port below this line takes a filesystem location for pixels,
    which is what lets the app and adapter packages hold zero image fixtures.
    ``render_dpi`` travels with the pixels it describes rather than being remembered
    separately, because a scale that can drift away from its bytes is half of the
    stale-cache defect -- and :meth:`digest` folds it in, so a DPI change mechanically
    invalidates a cached row instead of leaving it valid-looking and wrong.

    A field-for-field twin of the OCR slice's value object, deliberately: this is the
    type :class:`SvgRasterizer` hands the OCR use case, which is how one rasterizing
    technology stays inside one adapter.

    Attributes
    ----------
    page_ref
        Stable identity of the page these pixels depict, opaque to the ports. Its purpose
        is to make a test double a dictionary lookup instead of a FIFO queue: calls are
        neither ordered nor one-to-one, so a queue-shaped fake would happily return
        another page's pixels.
    media
        Encoding of ``data``.
    data
        Encoded image bytes.
    width
        Pixel width, which must equal what ``data``'s header records.
    height
        Pixel height, which must equal what ``data``'s header records.
    render_dpi
        Dots per inch the raster was rendered at.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_ref: str = Field(min_length=1)
    media: ImageMedia
    data: bytes = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    render_dpi: int = Field(gt=0)

    @model_validator(mode="after")
    def _check_declared_size(self) -> Self:
        """Reject pixel counts that disagree with the encoded bytes.

        Returns
        -------
        RasterImage
            The validated model.

        Raises
        ------
        ValueError
            If ``data`` is not in ``media``'s encoding or its header disagrees with
            ``width`` and ``height``.
        """
        _validate_raster_bytes(self.media, self.data, self.width, self.height)
        return self

    def digest(self) -> str:
        """Return a stable content digest of these pixels and their scale.

        Deliberately excludes ``page_ref``: identical pixels rendered for a different page
        slot are the same input and should share a cache row.

        Returns
        -------
        str
            Lowercase hex SHA-256 over encoding, dimensions, DPI and bytes.
        """
        hasher = hashlib.sha256()
        hasher.update(b"rmspec.raster.v1")
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(self.media.value.encode())
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(f"{self.width}x{self.height}@{self.render_dpi}".encode())
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(self.data)
        return hasher.hexdigest()


class SvgPage(BaseModel):
    """One finished SVG document plus the physical page it describes.

    Not a bare ``str``: handing an adapter raw wire text loses the page's identity and its
    real-world size, and both are load-bearing. ``page_ref`` is what lets the composer
    return provenance the caller can compare against its input, and ``size`` is what lets
    the PDF adapter emit a page of the right dimensions without a second argument that
    could contradict the markup.

    Attributes
    ----------
    page_ref
        Stable identity of the page this markup was rendered from, opaque to the ports.
    svg
        The SVG document text.
    size
        The real-world size of the page this markup fills.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_ref: str = Field(min_length=1)
    svg: str = Field(min_length=1)
    size: PhysicalSize

    @model_validator(mode="after")
    def _check_svg_root(self) -> Self:
        """Reject text that is not an SVG document.

        A one-substring check, but it is what stops a test double from passing ``"x"`` as
        a page: the rasterizer's contract suite then has to build markup a real
        rasterizer would accept, which is the difference between testing the contract and
        testing the double.

        Returns
        -------
        SvgPage
            The validated model.

        Raises
        ------
        ValueError
            If ``svg`` contains no ``<svg`` root element.
        """
        if _SVG_ROOT_TAG not in self.svg:
            msg = "svg must contain an <svg> root element"
            raise ValueError(msg)
        return self


class SvgPageSet(BaseModel):
    """An ordered, non-empty set of SVG pages with distinct page references.

    The two constraints replace two error classes. ``min_length=1`` makes an empty export
    unrepresentable, so no adapter and no double has to re-implement an emptiness guard,
    and distinct ``page_ref`` values make :attr:`PdfDocument.pages` comparable to this
    input element by element -- the assertion that catches a composer which drops pages.

    Attributes
    ----------
    pages
        The pages, in output order.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    pages: tuple[SvgPage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_distinct_refs(self) -> Self:
        """Reject repeated page references.

        Returns
        -------
        SvgPageSet
            The validated model.

        Raises
        ------
        ValueError
            If two pages share a ``page_ref``.
        """
        refs = [page.page_ref for page in self.pages]
        if len(set(refs)) != len(refs):
            msg = "page_ref values must be distinct within a page set"
            raise ValueError(msg)
        return self


class PdfPageRef(BaseModel):
    """Provenance for one page of a composed PDF.

    Attributes
    ----------
    page_ref
        The :attr:`SvgPage.page_ref` this emitted page came from.
    size
        The real-world size of the emitted page, as read back from the document.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_ref: str = Field(min_length=1)
    size: PhysicalSize


class PdfDocument(BaseModel):
    """A composed multi-page PDF: its bytes, and one reference per page it contains.

    ``pages`` is provenance, not bookkeeping. The composer must derive it by reading back
    the document it produced, so the count cannot be a restatement of the input length --
    which is precisely why the legacy truncating writer could report *N* pages while
    holding one. The bytes are validated to open with ``%PDF-`` and close with ``%%EOF``,
    so a surface whose buffer was never flushed cannot be handed on as a document.

    Attributes
    ----------
    data
        The whole PDF, ready for a sink. No destination path: committing bytes is
        :class:`ArtifactSink`'s single responsibility, and the largest, slowest,
        most-partially-writable artifact is the one that most needs its atomicity.
    pages
        One reference per emitted page, in document order.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    data: bytes = Field(min_length=1)
    pages: tuple[PdfPageRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_pdf_bytes(self) -> Self:
        """Reject bytes that are not a complete PDF.

        Returns
        -------
        PdfDocument
            The validated model.

        Raises
        ------
        ValueError
            If ``data`` lacks a ``%PDF-`` header or a trailing ``%%EOF`` marker.
        """
        if not self.data.startswith(_PDF_SIGNATURE):
            msg = "data does not start with a %PDF- header"
            raise ValueError(msg)
        if _PDF_TRAILER not in self.data[-_PDF_TRAILER_WINDOW:]:
            msg = "data has no %%EOF marker; the document was never finished"
            raise ValueError(msg)
        return self


class PdfPageBackground(BaseModel):
    """One page of an existing PDF, rasterized to sit behind handwritten strokes.

    Not a :class:`RasterImage`: a fitted background has no requested DPI, and rounding an
    effective one into an integer field that feeds a cache digest would be a lie with
    consequences. It carries instead the two facts the callers actually need together --
    the pixels, and the source page's native real-world size, which is what aligns
    strokes over the background. The legacy code needed a second call and a second parse
    to learn the second fact; the rejected ports dropped it entirely.

    Attributes
    ----------
    page_index
        Zero-based index of the source page within its document.
    media
        Encoding of ``data``.
    data
        Encoded image bytes.
    pixel_size
        Size of ``data``'s pixels, which must equal both what its header records and
        :meth:`PixelSize.fit_within` for the request that produced it.
    page_size
        The source PDF page's native real-world size, with any page rotation already
        applied, so two conforming adapters cannot disagree about orientation.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_index: int = Field(ge=0)
    media: ImageMedia
    data: bytes = Field(min_length=1)
    pixel_size: PixelSize
    page_size: PhysicalSize

    @model_validator(mode="after")
    def _check_declared_size(self) -> Self:
        """Reject pixel counts that disagree with the encoded bytes.

        Returns
        -------
        PdfPageBackground
            The validated model.

        Raises
        ------
        ValueError
            If ``data`` is not in ``media``'s encoding or its header disagrees with
            ``pixel_size``.
        """
        _validate_raster_bytes(
            self.media,
            self.data,
            self.pixel_size.width_px,
            self.pixel_size.height_px,
        )
        return self


class ArtifactRef(BaseModel):
    """Receipt for one artifact a sink committed.

    Addressed by name, never by path. A name plus an opaque ``uri`` is the only shape
    every destination can honour: a filesystem sink returns a ``file:`` URI, and any other
    destination returns its own scheme without inventing a path it does not have. It is
    also what keeps path construction, and therefore the filesystem, out of use cases.

    Attributes
    ----------
    name
        The name the artifact was written under, as the caller supplied it.
    uri
        Where it landed, in the sink's own address space.
    byte_count
        Bytes committed.
    media
        What kind of artifact it is.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    byte_count: int = Field(ge=0)
    media: ArtifactMedia


class SvgRasterizer(Protocol):
    """Turn one finished SVG page into PNG pixels at an explicit resolution.

    Scope: ``APP``. Stateless, and its native library handle is loaded once per process.

    Notes
    -----
    This port has three consumers, not one: PNG export, and the two OCR paths that
    rasterize a page for recognition and for the multimodal model. The legacy OCR code
    imported the rasterizing library itself, which would have put one technology in two
    adapters and left the OCR render DPI -- a cache-key component -- outside the seam that
    owns it. The OCR use case is given this port instead.

    The three rejected variants of this port were refuted for surface this one does not
    have: ``probe()``, a raster-format enum, a PDF-page method, a package-missing error,
    and a raw ``str`` input. What survives is one method, bytes in and bytes out, with no
    temporary file anywhere.
    """

    def to_png(self, page: SvgPage, *, dpi: int) -> RasterImage:
        """Rasterize ``page`` to PNG at ``dpi``.

        Parameters
        ----------
        page
            The SVG document to rasterize, with the physical size it fills.
        dpi
            Pixels per inch of the produced raster. An SVG user unit is 1/72 inch, so the
            adapter's only conversion is ``scale = dpi / 72``; this is the single
            statement of that convention in the codebase. Must be positive -- the CLI
            validates its own flag, and :class:`RasterImage` refuses a non-positive
            ``render_dpi`` on the way out.

        Returns
        -------
        RasterImage
            PNG bytes, their true pixel dimensions, ``page.page_ref``, and ``dpi``
            recorded as ``render_dpi``.

        Raises
        ------
        RasterizationFailed
            The markup could not be rasterized, or the library failed mid-render.
        """
        ...


class PdfComposer(Protocol):
    """Compose an ordered set of SVG pages into exactly that many PDF pages.

    Scope: ``APP``. Stateless; every call receives the full page set it needs.

    Notes
    -----
    One method and one composer, with no size-conditional branch. The legacy exporter had
    a one-page fast path and a multi-page path, and the divergence between them *was* the
    bug: the multi-page path wrote a blank surface and then overwrote the output with page
    one. A composer that keeps a "when there is a single page" branch reopens it.

    Inside the adapter, SVG-to-PDF conversion and page merging may well be two libraries;
    that split stays internal, so the intermediate encoding never becomes shared
    vocabulary and the app never orchestrates the handoff. The page count is verified by
    the adapter reopening its own output, and a mismatch is wrapped in
    ``PdfCompositionFailed`` -- there is no caller-supplied expected count to disagree
    with, and no page size argument to contradict the pages' own sizes.
    """

    def compose(self, pages: SvgPageSet) -> PdfDocument:
        """Compose ``pages`` into one PDF.

        Parameters
        ----------
        pages
            The pages to emit, in order. Non-empty and distinctly referenced by
            construction, so this method has no precondition to check.

        Returns
        -------
        PdfDocument
            The composed document, whose ``pages`` the adapter derives by reading back
            the bytes it produced -- one reference per emitted page, in emitted order,
            carrying that page's ``page_ref`` and size.

        Raises
        ------
        PdfCompositionFailed
            A page could not be converted, the pages could not be merged, or read-back
            found a page count or order other than the one requested.
        """
        ...


class PdfPageReader(Protocol):
    """Read an existing PDF: how many pages, what they say, what they look like.

    Scope: ``APP``. Stateless and path-keyed; any reuse of an open document is the
    adapter's own memoisation, not a lifetime the app can see or must close.

    Notes
    -----
    One port, three methods, because there is one technology behind it and three live
    call sites -- background rasterization, per-page text, and a bare page count during
    sync. Splitting text into another slice would put the same PDF library in two
    adapters, the defect this architecture exists to prevent; splitting it into three
    ports would give one library three seams to keep consistent.

    A path, not whole-file bytes: every caller holds a path, and a ``bytes`` parameter
    would force a large annotated PDF into memory and be re-parsed once per method.
    """

    def page_count(self, source: Path) -> int:
        """Count the pages in ``source``.

        Parameters
        ----------
        source
            Path to the PDF.

        Returns
        -------
        int
            Number of pages. Replaces a legacy regular-expression scan for page objects,
            which over- and under-counted on real documents.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is missing, is not a PDF, is corrupt, or needs a password.
        """
        ...

    def page_texts(self, source: Path) -> tuple[str, ...]:
        """Extract the text of every page in ``source``.

        One entry per page, in document order, empty for a page with no extractable text.
        There is no per-page not-found error and no optional element: the caller wants a
        placeholder for a text-free page, not an exception, and a positional result whose
        length equals :meth:`page_count` is checkable in the contract suite.

        Parameters
        ----------
        source
            Path to the PDF.

        Returns
        -------
        tuple[str, ...]
            Page text in document order.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is missing, is not a PDF, is corrupt, or needs a password.
        """
        ...

    def rasterize_page(
        self,
        source: Path,
        *,
        page_index: int,
        box: PixelSize,
        oversample: int = 2,
    ) -> PdfPageBackground:
        """Rasterize one page of ``source`` to fit inside ``box``.

        Fit-to-box, not a DPI: a background exists to sit under a rendered note page, so
        the target is that page's pixel box and the contract is registration, not
        resolution. The adapter must size its output with :meth:`PixelSize.fit_within`,
        which is also what a test double must use, so the scale is checkable rather than
        eyeballed.

        Parameters
        ----------
        source
            Path to the PDF.
        page_index
            Zero-based page index.
        box
            Pixel box the rasterized page must fit inside.
        oversample
            Integer supersampling factor, defaulting to the legacy factor of 2.

        Returns
        -------
        PdfPageBackground
            PNG bytes, their pixel size, and the source page's native real-world size --
            all from one open, so no caller needs a second call to align strokes.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is missing, is not a PDF, is corrupt, or needs a password.
        PdfPageOutOfRange
            ``page_index`` is not a page of this document.
        RasterizationFailed
            The page was found but could not be rasterized.
        """
        ...


class ArtifactSink(Protocol):
    """Commit finished export bytes to this invocation's destination, and name what landed.

    Scope: ``REQUEST``. It carries the invocation's output root, overwrite policy and
    dry-run flag, which is why none of those appear in the signature.

    Notes
    -----
    Kept at one method, deliberately narrowed from the three rejected variants. Gone:
    ``exists()`` (meaningless for a destination that cannot be read back, and it
    re-exposes the overwrite policy the adapter owns), ``open_stream()`` (a writable
    handle is what a native library wants, not what a destination can always give, and it
    split receipt reporting across two paths no double could keep consistent), and
    ``destination: Path`` (a filesystem location in the contract, which is exactly the
    knowledge this port exists to keep out of use cases).

    It is conceded that one production destination exists today. The port is not here for
    a second one; it is here because a use case that may import only the domain must not
    perform I/O, and because atomicity has to be one adapter's enforced contract:
    temporary file beside the target, flush, then rename, so a failed 200-page export
    leaves no truncated artifact. That guarantee is why nothing else in this module takes
    a destination -- two owners of "commit bytes" means the largest artifact bypasses it.
    """

    def write(self, name: str, payload: bytes, *, media: ArtifactMedia) -> ArtifactRef:
        """Commit ``payload`` under ``name``.

        Parameters
        ----------
        name
            Caller-chosen artifact name, without a directory component. The adapter
            resolves it against the destination it was constructed with.
        payload
            The complete artifact.
        media
            What kind of artifact this is; the adapter derives any suffix from it, so name
            and content cannot disagree.

        Returns
        -------
        ArtifactRef
            Receipt naming what was committed and where.

        Raises
        ------
        ArtifactWriteFailed
            The artifact could not be committed. Carries the sink's reason -- already
            present, not writable, out of space, interrupted mid-commit -- as a typed
            reason rather than one error class per errno.
        """
        ...
