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
there is one statement of it, and it is a formula rather than a sentence:
:meth:`PixelSize.from_dpi` turns a page's millimetres into the pixel count a raster at
``dpi`` must have, and :meth:`SvgRasterizer.to_png` must return exactly that count.
Prose alone was not enough to discriminate two adapters -- ``cairosvg``'s own ``dpi``
argument defaults to 96 and converts only absolute units, so two backends could both
"obey" a ``scale = dpi / 72`` sentence, disagree on pixel count, and leave the contract
suite with nothing to assert. Page geometry is millimetres (:class:`PhysicalSize`) and
pixels (:class:`PixelSize`); PostScript points appear nowhere, because points are a
PDF/PostScript unit and a domain that speaks them has already adopted one adapter's
coordinate system.

Background fitting has the same shape: :meth:`PixelSize.fit_within` is the single
implementation of "fit this PDF page inside that pixel box, supersampled", and
:meth:`PdfPageReader.rasterize_page` must return exactly what it computes for the request
that produced it. A double that invents ``width_px=8`` -- which every rejected proposal's
fake did -- then fails the shared contract suite instead of making alignment assertions
vacuous. Both formulas round; an adapter whose native call ceils (``fitz``, ``pdfium``)
resizes to the domain figure instead of reporting its own.

Values that cannot lie about their bytes
----------------------------------------
:class:`RasterImage` and :class:`PdfPageBackground` validate their declared pixel
dimensions against the PNG ``IHDR`` chunk inside ``data`` *and* require the terminating
``IEND`` chunk, and :class:`PdfDocument` validates that ``data`` opens with ``%PDF-`` and
closes with ``%%EOF``. Both checks are stdlib byte inspection, no decoder. This is what
makes the export slice's headline defects unrepresentable rather than merely reported:

* A rasterizer or reader whose pixel count disagrees with its pixels raises at
  construction, so ``b"png:" + svg`` is not a usable test double any more and a
  supersample or DPI bug cannot pass a green fake.
* A raster is truncated the same way a PDF is: a stream cut short after ``IHDR`` still
  declares the right dimensions, so the header check alone would pass it. Requiring
  ``IEND`` costs a conforming fake twelve constant bytes and costs a truncating adapter
  its green test.
* A PDF whose buffer was never flushed -- the legacy blank-page/truncated-surface
  failure, where the writer's own page counter still said *N* -- cannot be returned.
* :attr:`PdfDocument.pages` is provenance, not a self-reported count: the composer must
  derive one :class:`PdfPageRef` per page *from the document it emitted*, so
  ``[r.page_ref for r in doc.pages] == [p.page_ref for p in page_set.pages]`` fails the
  legacy "write page one, drop pages 2..N" bug in a way a ``page_count=len(pages)`` field
  never could. Precisely two facts in that comparison are measured -- the page count and
  each page's size, which is why the contract suite composes pages of *different* sizes.
  ``page_ref`` is positional, because PDF pages carry no such field; order is therefore
  guaranteed by how :meth:`PdfComposer.compose` requires the document to be built, not by
  a read-back check that cannot see it.

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
    Dropped from the *contract*, kept inside the adapter -- and, since page order cannot be
    read back out of a PDF, now *required* there by :meth:`PdfComposer.compose`. Rendering
    each SVG page to a one-page PDF and then stapling is a real and deliberate
    implementation -- it is what preserves today's vector fidelity instead of silently
    swapping which library rasterizes vectors -- but it stays one adapter's internal
    pipeline: no intermediate blob crosses a port. Promoting the
    intermediate blob to a port would make the app layer the pipe carrying one library's
    encoded output into another's merge call, unable to inspect order, size or content.
    ``expected_pages`` goes with it: a caller-supplied invariant on the very method whose
    job is to verify page count is self-agreeing, and it moves the defect one frame up.
``PdfPages`` handle / ``AbstractContextManager``
    Dropped. An ``open()`` returning a closable, index-addressable handle is
    ``fitz.Document`` transcribed, it puts ``with`` blocks over port-returned resources
    into use cases, and it maps onto neither ``Scope.APP`` nor ``Scope.REQUEST``.
    :class:`PdfPageReader` is stateless and keyed by :class:`PdfSourceRef`; caching an open
    document is the adapter's optimisation, bounded by the rule stated on that port.
``source: Path`` on the PDF reader
    Dropped, for exactly the reason ``destination: Path`` is dropped from the sink below.
    No use case can obtain a path: the reading call sites hold a document identity or
    role-addressed bytes, no port anywhere returns a path, and the sync call site holds
    only bytes fetched over SSH. A ``Path`` parameter therefore made the use case rebuild
    the store layout -- the legacy ``cli/_resolve.py`` scan -- from a root no domain type
    exposes, or spool a temporary file inside a use case that must not perform I/O. The
    three methods take :class:`PdfSourceRef`, an opaque token minted by whichever adapter
    already knows where that document's bytes live.
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

Unfinished, and blocking: the :class:`RasterImage` twin
------------------------------------------------------
:class:`RasterImage`, :class:`ImageMedia` and :class:`PhysicalSize` are field-for-field
twins of definitions in :mod:`rmspec.domain.ports.ocr`, duplicated only because a ports
module may not import a sibling. That duplication is a defect, not a tolerated cost, and
it is not repairable from inside this module:

* pydantic models are nominal, so ``to_png``'s return value is *not* the type
  ``TextRecognizer.recognize`` or ``Message.images`` accept. Under ``ty`` with every rule
  at error the OCR use case cannot pass it, and the field-by-field rebuild it is forced
  into drops :meth:`RasterImage._check_declared_size` -- so pixels reaching OCR carry a
  ``width``, ``height`` and ``render_dpi`` nothing has checked against the PNG header,
  while :meth:`RasterImage.digest` hashes exactly those three into the OCR cache key.
  That is defect 3 reopened by a type-identity accident.
* The fix is one edit spanning two modules, which is why it is recorded rather than done:
  hoist :class:`PhysicalSize`, :class:`ImageMedia` and :class:`RasterImage` (this copy,
  with its validator and its digest) into a pydantic-only ``rmspec.domain.values`` -- not
  a ports sibling, so both slices may import it -- and have this module and
  :mod:`rmspec.domain.ports.ocr` import from there. No signature in this file changes;
  only the types' address does. Re-exporting two same-named classes from
  ``ports/__init__.py`` is not a substitute: it leaves two classes.

Until that lands, the "three consumers" claim on :class:`SvgRasterizer` describes the
intended wiring, not a wiring that type-checks.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import ClassVar, Final, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "PAGE_SIZE_TOLERANCE_MM",
    "ArtifactMedia",
    "ArtifactName",
    "ArtifactRef",
    "ArtifactSink",
    "ImageMedia",
    "PdfComposer",
    "PdfDocument",
    "PdfPageBackground",
    "PdfPageReader",
    "PdfPageRef",
    "PdfSourceRef",
    "PhysicalSize",
    "PixelSize",
    "RasterImage",
    "SvgPage",
    "SvgPageSet",
    "SvgRasterizer",
]

PAGE_SIZE_TOLERANCE_MM: Final = 0.1
"""Millimetres of slack allowed when comparing a requested page size to a read-back one.

PDF page boxes are stored in points, so a millimetre size cannot survive a write-then-read
round trip exactly. One shared figure, imported by the contract suite, is what lets
:meth:`PdfComposer.compose`'s size clause be an assertion instead of a judgement call; a
per-adapter epsilon would let each adapter pick the tolerance that makes it pass.
"""

_FIELD_SEPARATOR = b"\x1f"
"""Byte that separates digest components, so concatenation cannot be ambiguous."""

_MM_PER_INCH = 25.4
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_TRAILER = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
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
_NAME_SEPARATORS = ("/", "\\")
_RESERVED_NAMES = (".", "..")


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
        If ``data`` is not in ``media``'s encoding, its header records dimensions other
        than ``width`` by ``height``, or the PNG stream is truncated before its ``IEND``
        chunk.
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
    if not data.endswith(_PNG_TRAILER):
        msg = "png data does not end with an IEND chunk; the stream was truncated"
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
    """A raster's size in pixels, plus the two formulas that derive it.

    :meth:`from_dpi` is the resolution convention and :meth:`fit_within` is the
    fit-to-box convention. Both live here rather than in an adapter, so a port can state
    its output dimensions as an equality against a formula the contract suite can call.

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

    @classmethod
    def from_dpi(cls, page: PhysicalSize, dpi: int) -> Self:
        """Return the pixel size a raster of ``page`` at ``dpi`` must have.

        The domain's whole definition of "DPI": millimetres to inches to pixels, rounded,
        never below 1x1. :meth:`SvgRasterizer.to_png` is required to return this exact
        size, which is what makes the resolution contract assertable -- a rasterizing
        library's own ``dpi`` parameter may mean something else entirely (``cairosvg``
        defaults to 96 and scales only absolute units), so an adapter converts to this
        figure rather than reporting whatever its backend produced.

        Parameters
        ----------
        page
            The page's real-world size.
        dpi
            Pixels per inch of the produced raster.

        Returns
        -------
        PixelSize
            Pixel dimensions for that page at that resolution.

        Raises
        ------
        ValueError
            If ``dpi`` is not positive.
        """
        if dpi <= 0:
            msg = "dpi must be positive"
            raise ValueError(msg)
        return cls(
            width_px=max(1, round(page.width_mm / _MM_PER_INCH * dpi)),
            height_px=max(1, round(page.height_mm / _MM_PER_INCH * dpi)),
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

    Half read back, half positional, and the difference matters. ``size`` is measured from
    the emitted document, so it can contradict the request. ``page_ref`` cannot be measured
    -- PDF pages carry no such field -- so it is the input reference at the same ordinal,
    and it is trustworthy only because :meth:`PdfComposer.compose` requires order to be
    established by construction rather than checked afterwards.

    Attributes
    ----------
    page_ref
        The :attr:`SvgPage.page_ref` at this page's ordinal in the requested set.
    size
        The real-world size of the emitted page, read back from the document, equal to the
        requested :attr:`SvgPage.size` within :data:`PAGE_SIZE_TOLERANCE_MM`.
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


class PdfSourceRef(BaseModel):
    """Opaque handle to an existing PDF a reader may open but a use case cannot locate.

    A token, not a path. The three :class:`PdfPageReader` methods need to name a document,
    and a filesystem path is a value no use case can honestly produce: the reading call
    sites hold a document identity or bytes pulled over SSH, and nothing in the domain maps
    either to a location. Handing them a ``Path`` parameter meant rebuilding the store's
    naming convention inside a use case, or spooling a temporary file there -- the same
    leak this module refuses for a sink's destination.

    So the direction is inverted: whichever adapter already knows where a document's bytes
    live mints the ref, and the PDF reader adapter is the only component permitted to
    resolve it back into a file, a stream or a spooled temporary. Everything above treats
    it as an opaque equality-and-hash key, which is also what makes a test double a
    dictionary from ref to fixture bytes.

    Attributes
    ----------
    token
        Adapter-assigned identity of one PDF. Its content is meaningless to the domain: a
        use case may pass it and compare it, and must not parse, split or build one.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(min_length=1)


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
        Encoding of ``data``, pinned to PNG. :meth:`PdfPageReader.rasterize_page` promises
        PNG, so a field admitting JPEG would let an adapter satisfy the type while breaking
        the method's contract, and would make the alpha channel a background compositor
        needs optional.
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
    media: Literal[ImageMedia.PNG] = ImageMedia.PNG
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


class ArtifactName(BaseModel):
    """A validated artifact name: one segment, no directory component, no traversal.

    A type rather than a rule in prose. "Without a directory component" was documented on
    :meth:`ArtifactSink.write` and enforced nowhere, so a plain ``str`` parameter pushed
    the traversal check into every sink -- and a name like ``../../x`` is exactly what a
    caller derived from a document title can produce. Refusing it at construction means the
    check exists once, in the domain, before any adapter is chosen, and a sink that
    forgets it cannot be the reason bytes land outside the destination.

    A stem, not a filename: the suffix comes from :class:`ArtifactMedia`, so a trailing dot
    is refused too rather than producing ``page..svg``.

    Attributes
    ----------
    value
        The name, as one path-free segment.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    value: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_single_segment(self) -> Self:
        """Reject names that address anything but one artifact in the destination.

        Returns
        -------
        ArtifactName
            The validated model.

        Raises
        ------
        ValueError
            If ``value`` contains a path separator or a NUL, is a relative-path segment
            such as ``.`` or ``..``, ends in a dot, or is padded with whitespace.
        """
        if self.value != self.value.strip():
            msg = "artifact name must not have leading or trailing whitespace"
            raise ValueError(msg)
        if any(separator in self.value for separator in _NAME_SEPARATORS):
            msg = "artifact name must not contain a path separator"
            raise ValueError(msg)
        if "\x00" in self.value:
            msg = "artifact name must not contain a NUL character"
            raise ValueError(msg)
        if self.value in _RESERVED_NAMES:
            msg = f"artifact name must not be the relative-path segment {self.value!r}"
            raise ValueError(msg)
        if self.value.endswith("."):
            msg = "artifact name must not end in a dot; the sink appends the suffix"
            raise ValueError(msg)
        return self


class ArtifactRef(BaseModel):
    """Receipt for one artifact a sink wrote, or would have written.

    Addressed by name, never by path. A name plus an opaque ``uri`` is the only shape
    every destination can honour: a filesystem sink returns a ``file:`` URI, and any other
    destination returns its own scheme without inventing a path it does not have. It is
    also what keeps path construction, and therefore the filesystem, out of use cases.

    ``committed`` is here because the sink owns the invocation's dry-run flag. Without it a
    dry-run sink has to return a receipt that claims a commit, and the use case reporting
    "wrote 200 pages" would be reading the CLI's own flag back to itself to phrase "would
    write" -- two sources of truth for what landed, one of them not the writer.

    Attributes
    ----------
    name
        The name the caller supplied, verbatim. It is not the stored filename: the sink
        derives a suffix from ``media`` and may resolve, prefix or encode further, and all
        of that shows up in ``uri`` only. Callers never parse ``uri``.
    uri
        Where it landed, in the sink's own address space.
    byte_count
        Length of the payload -- committed when ``committed`` is true, and what would have
        been committed when it is false.
    media
        What kind of artifact it is.
    committed
        True when the bytes were durably written, false when the sink simulated the write.
        A report phrases itself from this field, not from a flag somewhere above.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    name: ArtifactName
    uri: str = Field(min_length=1)
    byte_count: int = Field(ge=0)
    media: ArtifactMedia
    committed: bool


class SvgRasterizer(Protocol):
    """Turn one finished SVG page into PNG pixels at an explicit resolution.

    Scope: ``APP``. Stateless, and its native library handle is loaded once per process.

    Notes
    -----
    This port is intended to have three consumers, not one: PNG export, and the two OCR
    paths that rasterize a page for recognition and for the multimodal model. The legacy
    OCR code imported the rasterizing library itself, which would have put one technology
    in two adapters and left the OCR render DPI -- a cache-key component -- outside the seam
    that owns it.

    That third and fourth consumer are blocked, and not on anything in this file: the OCR
    port module declares its own nominal :class:`RasterImage`, so this method's return value
    is not the type its recognition and multimodal methods accept. The prerequisite is the
    hoist into ``rmspec.domain.values`` described at the top of this module. Do not work
    around it by rebuilding the OCR twin field by field in a use case -- that copy has no
    bytes-versus-declared-size validator, and it is the copy whose ``digest`` keys the OCR
    cache.

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
            Pixels per inch of the produced raster. An SVG user unit is 1/72 inch, so an
            adapter's internal conversion is ``scale = dpi / 72`` -- but that sentence is
            not the contract, because a backend's own ``dpi`` argument may mean something
            else and two adapters obeying it can still disagree on pixel count. The
            contract is the returned size, below. Must be positive --
            :meth:`PixelSize.from_dpi` and :class:`RasterImage` both refuse otherwise.

        Returns
        -------
        RasterImage
            PNG bytes carrying ``page.page_ref`` and ``dpi`` as ``render_dpi``, whose
            ``width`` and ``height`` equal ``PixelSize.from_dpi(page.size, dpi)``. Not
            "about right": the equality is what the contract suite asserts, so an adapter
            that lets its library round differently resizes to the domain figure or fails.

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

    Order is *constructed*, not verified, and the method contract says so. A composed PDF
    records nothing that identifies which :class:`SvgPage` produced which page, so a
    read-back "is the order right?" check is not implementable -- an adapter claiming to
    perform one is claiming something no test can distinguish from a stub. The required
    pipeline (one one-page PDF per input page, each asserted to hold exactly one page, then
    merged in list order) makes order a property of construction, and is the only place
    this module dictates adapter internals.
    """

    def compose(self, pages: SvgPageSet) -> PdfDocument:
        """Compose ``pages`` into one PDF, one emitted page per input page, in order.

        The adapter must convert each :class:`SvgPage` to a single-page PDF, confirm that
        intermediate holds exactly one page, and merge the intermediates in list order. It
        must then reopen its own output and, for each page, measure the size, comparing it
        to that page's :attr:`SvgPage.size` within :data:`PAGE_SIZE_TOLERANCE_MM`. The size
        clause is not decoration: a converter that scales markup to the requested size and
        one that honours the markup's own viewport are both defensible readings of "compose
        these pages", and they emit different documents.

        Parameters
        ----------
        pages
            The pages to emit, in order. Non-empty and distinctly referenced by
            construction, so this method has no precondition to check.

        Returns
        -------
        PdfDocument
            The composed document. Its ``pages`` has one entry per page found in the
            emitted bytes -- count and sizes measured from the document, ``page_ref`` taken
            from the input at the same ordinal.

        Raises
        ------
        PdfCompositionFailed
            A page could not be converted, an intermediate held other than one page, the
            pages could not be merged, or read-back found a page count or a page size other
            than the one requested. Order is not listed: it is established by the required
            construction, because it cannot be measured.
        """
        ...


class PdfPageReader(Protocol):
    """Read an existing PDF: how many pages, what they say, what they look like.

    Scope: ``APP``. Stateless and keyed by :class:`PdfSourceRef`; any reuse of an open
    document is the adapter's own memoisation, not a lifetime the app can see or must close.

    Notes
    -----
    One port, three methods, because there is one technology behind it and three live
    call sites -- background rasterization, per-page text, and a bare page count during
    sync. Splitting text into another slice would put the same PDF library in two
    adapters, the defect this architecture exists to prevent; splitting it into three
    ports would give one library three seams to keep consistent.

    A ref, neither a path nor whole-file bytes. Not a path, because no use case can obtain
    one and the alternatives are rebuilding the store layout or writing a temporary file
    inside a use case that may not perform I/O; see the module docstring. Not ``bytes``,
    because that forces a large annotated PDF into memory and re-parses it once per method.
    The ref is the one value both callers can hold: the adapter that already knows where the
    document lives mints it, whether that is a file in the store or a payload just pulled
    over SSH.

    Memoisation is bounded, because this port is ``APP``-scoped and its inputs are not. An
    adapter may cache an open document or a page count against a ref only for as long as the
    ref's minter vouches for it -- in practice, the invocation that produced it -- and must
    drop the entry afterwards. A memo that outlives its ref would serve a page count from a
    document that has since been re-pulled, which is the stale-key defect with a different
    key.
    """

    def page_count(self, source: PdfSourceRef) -> int:
        """Count the pages in ``source``.

        Parameters
        ----------
        source
            Opaque reference to the PDF.

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

    def page_texts(self, source: PdfSourceRef) -> tuple[str, ...]:
        """Extract the text of every page in ``source``.

        One entry per page, in document order, empty for a page with no extractable text.
        There is no per-page not-found error and no optional element: the caller wants a
        placeholder for a text-free page, not an exception.

        Parameters
        ----------
        source
            Opaque reference to the PDF.

        Returns
        -------
        tuple[str, ...]
            Page text in document order. ``len(result) == self.page_count(source)`` is a
            postcondition, not an observation -- the contract suite asserts it, so an
            adapter that silently skips a page it cannot decode fails instead of shifting
            every later page's text one slot up.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is missing, is not a PDF, is corrupt, or needs a password.
        """
        ...

    def rasterize_page(
        self,
        source: PdfSourceRef,
        *,
        page_index: int,
        box: PixelSize,
        oversample: int,
    ) -> PdfPageBackground:
        """Rasterize one page of ``source``, fitted to ``box`` and supersampled.

        Fit-to-box, not a DPI: a background exists to sit under a rendered note page, so
        the target is that page's pixel box and the contract is registration, not
        resolution. Note the raster is *not* bounded by ``box`` -- at ``oversample=2`` it is
        twice ``box`` in each dimension, and an adapter that reads "fits inside ``box``" as
        scale-to-box produces a half-size background that registers plausibly and wrongly.

        Parameters
        ----------
        source
            Opaque reference to the PDF.
        page_index
            Zero-based page index.
        box
            Pixel box the page is fitted to before supersampling -- normally the rendered
            note page this background will sit behind.
        oversample
            Integer supersampling factor applied after fitting. Required, not defaulted:
            :meth:`PixelSize.fit_within` already carries the default, and a second copy here
            is a constant that can drift from the formula it feeds.

        Returns
        -------
        PdfPageBackground
            PNG bytes, their pixel size, and the source page's native real-world size --
            all from one open, so no caller needs a second call to align strokes. The
            returned ``pixel_size`` must equal ``PixelSize.fit_within(result.page_size, box,
            oversample=oversample)`` exactly; ``fit_within`` rounds, while ``fitz`` and
            ``pdfium`` size by a ceiling, so an adapter resizes to the domain figure rather
            than reporting its backend's.

        Raises
        ------
        PdfSourceUnreadable
            ``source`` is missing, is not a PDF, is corrupt, or needs a password.
        PdfPageOutOfRange
            ``page_index`` is not a page of this document.
        RasterizationFailed
            The page was found but could not be rasterized.
        ValueError
            ``oversample`` is below 1, refused by :meth:`PixelSize.fit_within` before any
            document is opened. Not an export error: it is a caller bug in the same class as
            a non-positive DPI, and the CLI maps it where it maps ``ValidationError``.
        """
        ...


class ArtifactSink(Protocol):
    """Commit finished export bytes to this invocation's destination, and name what landed.

    Scope: ``REQUEST``. It carries the invocation's output root, overwrite policy and
    dry-run flag, which is why none of those appear in the signature. The dry-run flag does
    appear in the *receipt*, as :attr:`ArtifactRef.committed`: a sink that simulates a write
    must say so in the value it returns, because the use case that reports the result has no
    other honest way to know.

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

    def write(self, name: ArtifactName, payload: bytes, *, media: ArtifactMedia) -> ArtifactRef:
        """Commit ``payload`` under ``name``.

        Parameters
        ----------
        name
            Caller-chosen artifact name: a *stem*, never a filename and never a path.
            :class:`ArtifactName` has already refused separators and traversal, so no sink
            re-implements that check. The adapter resolves the stem against the destination
            it was constructed with and appends the suffix for ``media``; a filesystem sink
            writing ``page-001`` as PNG therefore lands ``page-001.png``.
        payload
            The complete artifact.
        media
            What kind of artifact this is, and the sole source of the suffix -- so a name
            and its content cannot disagree.

        Returns
        -------
        ArtifactRef
            Receipt echoing ``name`` verbatim, carrying the sink's own ``uri`` for where the
            bytes landed, and ``committed`` false when the sink only simulated the write.
            ``name`` stays the caller's stem in every sink so that a fake and the filesystem
            adapter return comparable receipts; suffixing, prefixing and escaping show up in
            ``uri``, which callers pass through and never parse.

        Raises
        ------
        ArtifactWriteFailed
            The artifact could not be committed. Carries the sink's reason -- already
            present, not writable, out of space, interrupted mid-commit -- as a typed
            reason rather than one error class per errno.
        """
        ...
