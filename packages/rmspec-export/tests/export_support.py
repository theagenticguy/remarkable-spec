"""Shared builders, in-memory doubles and port-contract suites for the export suite.

Not a ``test_`` module, so pytest does not collect it, and it carries a workspace-unique stem
so it cannot collide with another package's helpers under pytest's ``prepend`` import mode.
Importable from any test file in this directory by bare stem.

Three things live here:

1. **Builders.** SVG pages shaped like the ones the legacy renderer actually wrote -- a
   *padded* box whose ``width``/``height`` are unitless point figures, so the aspect ratio is
   the padded one (0.7729 for a reMarkable 2 page) and not the screen's 0.75. That shape
   matters: markup whose declared box happens to equal the page size lets a composer with a
   hard-coded unit assumption pass its own tests, and lets a rasterizer that anisotropically
   squashes its output go unnoticed.
2. **In-memory doubles.** A dictionary-backed sink and a recording rasterizer, run through the
   *same* contract functions as the real adapters. A double that cannot satisfy the contract is
   how a vacuous assertion is discovered.
3. **Contract suites.** The assertions the ports state, written once and applied to every
   implementation, so an adapter and a fake cannot be asserted against different rules.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from typing import TYPE_CHECKING, Protocol

from rmspec.domain.errors import ArtifactWriteFailed, ArtifactWriteReason
from rmspec.domain.ports.export import (
    ArtifactMedia,
    ArtifactName,
    ArtifactRef,
    ImageMedia,
    PdfDocument,
    PhysicalSize,
    PixelSize,
    RasterImage,
    SvgPage,
    SvgPageSet,
)
from rmspec.export._geometry import sizes_agree
from rmspec.export._pymupdf import pymupdf

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.ports.export import (
        ArtifactSink,
        PdfComposer,
        PdfPageReader,
        PdfSourceRef,
        SvgRasterizer,
    )

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0

#: The declared box the legacy renderer produced for a reMarkable 2 page, in points. Its
#: aspect ratio is 0.7729, not the screen's 0.75, because the renderer pads every side.
LEGACY_RM2_BOX_PT = (507.29, 656.39)

#: US Letter, for a second page size that is neither the first nor a scaled copy of it.
US_LETTER_BOX_PT = (612.0, 792.0)

#: Paper Pro, as a third distinct size.
PAPER_PRO_BOX_PT = (509.32, 679.09)

#: The exact twelve bytes a complete PNG stream ends with.
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"


def millimetres(points: float) -> float:
    """Convert points to millimetres, independently of the code under test.

    Parameters
    ----------
    points
        A length in points.

    Returns
    -------
    float
        The same length in millimetres.
    """
    return points * MM_PER_INCH / POINTS_PER_INCH


def svg_markup(width_pt: float, height_pt: float, *, ink: bool = True) -> str:
    """Build markup shaped like the legacy renderer's output.

    Parameters
    ----------
    width_pt
        Declared width, written without a unit, as the legacy renderer wrote it.
    height_pt
        Declared height, written without a unit.
    ink
        Whether to include a stroke element. ``False`` produces the ink-free page a zero-byte
        ``.rm`` stub renders to -- a normal page, not an error.

    Returns
    -------
    str
        SVG document text.
    """
    body = (
        '<line x1="12.00" y1="18.00" x2="90.00" y2="140.00" stroke-width="1.698" '
        'stroke="rgb(0,0,0)" />'
        if ink
        else ""
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="-30.00 -30.00 {width_pt:.2f} {height_pt:.2f}" '
        f'width="{width_pt:.2f}" height="{height_pt:.2f}">'
        f"{body}</svg>"
    )


def millimetre_svg_markup(width_mm: float, height_mm: float) -> str:
    """Build markup that declares its size in explicit millimetres.

    The second unit convention. A composer that hard-codes the 96/72 correction for unitless
    markup overshoots this one by a third; a composer that applies no correction undershoots
    the unitless one by a quarter. Both were measured, and both are pinned.

    Parameters
    ----------
    width_mm
        Declared width in millimetres.
    height_mm
        Declared height in millimetres.

    Returns
    -------
    str
        SVG document text.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width_mm:.4f} {height_mm:.4f}" '
        f'width="{width_mm:.4f}mm" height="{height_mm:.4f}mm">'
        '<rect x="1" y="1" width="10" height="10" fill="black" /></svg>'
    )


def make_page(page_ref: str, box_pt: tuple[float, float], *, ink: bool = True) -> SvgPage:
    """Build one :class:`SvgPage` whose ``size`` comes from the markup's own declared box.

    Parameters
    ----------
    page_ref
        Stable page identity.
    box_pt
        Declared box in points.
    ink
        Whether the page carries a stroke.

    Returns
    -------
    SvgPage
        The page.
    """
    width_pt, height_pt = box_pt
    return SvgPage(
        page_ref=page_ref,
        svg=svg_markup(width_pt, height_pt, ink=ink),
        size=PhysicalSize(width_mm=millimetres(width_pt), height_mm=millimetres(height_pt)),
    )


def build_pdf(
    boxes_pt: Sequence[tuple[float, float]],
    *,
    texts: Sequence[str] | None = None,
    rotations: Sequence[int] | None = None,
) -> bytes:
    """Build a PDF in memory with the requested page boxes, text and rotations.

    Uses the guarded ``pymupdf`` handle from :mod:`rmspec.export._pymupdf` rather than a second
    unguarded import -- see that module for why an unguarded one takes down the session.

    Parameters
    ----------
    boxes_pt
        One ``(width, height)`` in points per page.
    texts
        Text to draw on each page, or ``None`` for text-free pages. An empty string leaves a
        page text-free, which is what proves ``page_texts`` returns a placeholder rather than
        raising or skipping.
    rotations
        Page rotation in degrees per page, or ``None`` for none.

    Returns
    -------
    bytes
        The PDF.
    """
    with pymupdf.open() as document:
        for index, (width_pt, height_pt) in enumerate(boxes_pt):
            new_page = document.new_page(width=width_pt, height=height_pt)
            if texts is not None and texts[index]:
                new_page.insert_text((40, 60), texts[index], fontsize=11)
            if rotations is not None and rotations[index]:
                new_page.set_rotation(rotations[index])
        return bytes(document.tobytes())


def artifact_name(value: str) -> ArtifactName:
    """Build an :class:`ArtifactName` without repeating the keyword at every call site.

    Parameters
    ----------
    value
        The stem.

    Returns
    -------
    ArtifactName
        The validated name.
    """
    return ArtifactName(value=value)


def sha256(data: bytes) -> str:
    """Hash bytes, for the byte-verbatim assertions.

    Parameters
    ----------
    data
        Bytes to hash.

    Returns
    -------
    str
        Lowercase hex digest.
    """
    return hashlib.sha256(data).hexdigest()


def png_header_size(data: bytes) -> PixelSize:
    """Read a PNG's ``IHDR`` size without a decoder, independently of the domain's helper.

    Parameters
    ----------
    data
        PNG bytes.

    Returns
    -------
    PixelSize
        Declared pixel size.
    """
    return PixelSize(
        width_px=int.from_bytes(data[16:20], "big"),
        height_px=int.from_bytes(data[20:24], "big"),
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """Frame one PNG chunk: length, type, payload, CRC-32 over type and payload.

    Parameters
    ----------
    kind
        Four-byte chunk type.
    payload
        Chunk data.

    Returns
    -------
    bytes
        The framed chunk.
    """
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def solid_png(size: PixelSize) -> bytes:
    """Encode a real, decodable PNG of exactly ``size`` using nothing but the standard library.

    By hand rather than through MuPDF, and the reason is a dependency rather than taste. A
    double built by a backend can only live where that backend may be imported, and
    :class:`MemoryRasterizer` below is the workspace's only in-memory rasterizer double: the
    app and CLI packages will want it, and ``tests/architecture`` maps ``pymupdf`` to
    ``rmspec-export`` alone. Four chunks of standard library owe nothing to either backend.

    Still a real PNG, because the validators require one: :class:`RasterImage` compares its
    declared size against the ``IHDR`` chunk and requires the ``IEND`` trailer, and
    ``test_export_pillow.py`` resamples these bytes through a real decoder.

    Parameters
    ----------
    size
        The pixel size to encode.

    Returns
    -------
    bytes
        Opaque mid-grey PNG bytes whose header declares ``size``.
    """
    header = struct.pack(">IIBBBBB", size.width_px, size.height_px, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x80\x80\x80" * size.width_px
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(row * size.height_px, 6))
        + _png_chunk(b"IEND", b"")
    )


class MemoryArtifactSink:
    """A dictionary-backed :class:`ArtifactSink`, run through the same contract as the real one.

    Deliberately about ten lines of behaviour: if a fake this small cannot satisfy
    :func:`check_sink`, the contract is testing the adapter rather than the port.

    It carries an ``overwrite`` policy for one reason: without it,
    :func:`check_sink_refuses_a_second_write` could only be applied to the filesystem adapter,
    so :meth:`ArtifactSink.write`'s ``ALREADY_PRESENT`` clause would be adapter-tested rather
    than port-tested -- and an app-layer fake sink would be free to overwrite silently where
    production raises.
    """

    def __init__(self, *, committed: bool = True, overwrite: bool = True) -> None:
        self.written: dict[str, tuple[ArtifactMedia, bytes]] = {}
        self._committed = committed
        self._overwrite = overwrite

    def write(self, name: ArtifactName, payload: bytes, *, media: ArtifactMedia) -> ArtifactRef:
        """Record ``payload`` and return a receipt of the same shape the filesystem sink does.

        Parameters
        ----------
        name
            Artifact stem.
        payload
            Bytes.
        media
            Artifact kind.

        Returns
        -------
        ArtifactRef
            The receipt.

        Raises
        ------
        ArtifactWriteFailed
            ``name`` was already written and this sink was built with overwriting off.
        """
        if name.value in self.written and not self._overwrite:
            raise ArtifactWriteFailed(
                name=name.value,
                reason=ArtifactWriteReason.ALREADY_PRESENT,
                detail=f"{name.value} was already written to this in-memory sink",
            )
        if self._committed:
            self.written[name.value] = (media, payload)
        return ArtifactRef(
            name=name,
            uri=f"memory:{name.value}.{media.value}",
            byte_count=len(payload),
            media=media,
            committed=self._committed,
        )


def check_rasterizer(rasterizer: SvgRasterizer, *, dpi_values: Sequence[int]) -> None:
    """Assert the :class:`SvgRasterizer` contract for one implementation.

    Parameters
    ----------
    rasterizer
        The implementation under test.
    dpi_values
        Resolutions to exercise.
    """
    page = make_page("contract-page", LEGACY_RM2_BOX_PT)
    digests: set[str] = set()
    for dpi in dpi_values:
        image = rasterizer.to_png(page, dpi=dpi)
        expected = PixelSize.from_dpi(page.size, dpi)
        assert (image.width, image.height) == (expected.width_px, expected.height_px)
        assert png_header_size(image.data) == expected
        assert image.data.endswith(PNG_IEND)
        assert image.render_dpi == dpi
        assert image.page_ref == page.page_ref
        assert image.media is ImageMedia.PNG
        digests.add(image.digest())
    assert len(digests) == len(set(dpi_values)), "a DPI change must change the cache digest"


def check_composer(composer: PdfComposer, *, page_set: SvgPageSet) -> PdfDocument:
    """Assert the :class:`PdfComposer` contract for one implementation.

    Parameters
    ----------
    composer
        The implementation under test.
    page_set
        Pages to compose. Must have distinct sizes for the size half of the read-back
        comparison to mean anything.

    Returns
    -------
    PdfDocument
        The composed document, so a caller can make further assertions on it.
    """
    document = composer.compose(page_set)
    assert [ref.page_ref for ref in document.pages] == [p.page_ref for p in page_set.pages]
    for ref, requested in zip(document.pages, page_set.pages, strict=True):
        assert sizes_agree(ref.size, requested.size), (
            f"page {ref.page_ref} came back at {ref.size} for a request of {requested.size}"
        )
    assert document.data.startswith(b"%PDF-")
    return document


def check_reader(
    reader: PdfPageReader,
    *,
    source: PdfSourceRef,
    expected_pages: int,
    box: PixelSize,
    oversample_values: Sequence[int],
) -> None:
    """Assert the :class:`PdfPageReader` contract for one implementation.

    Parameters
    ----------
    reader
        The implementation under test.
    source
        A ref the reader can resolve.
    expected_pages
        How many pages the document holds.
    box
        Pixel box to fit backgrounds into.
    oversample_values
        Supersampling factors to exercise. Include a value above 1: reading the contract as
        scale-to-box instead of fit-then-supersample produces a half-size background that
        registers plausibly and wrongly.
    """
    assert reader.page_count(source) == expected_pages
    texts = reader.page_texts(source)
    assert len(texts) == expected_pages, "one text entry per page, empty for a text-free page"
    for oversample in oversample_values:
        for index in (0, expected_pages - 1):
            background = reader.rasterize_page(
                source,
                page_index=index,
                box=box,
                oversample=oversample,
            )
            expected = PixelSize.fit_within(
                background.page_size,
                box,
                oversample=oversample,
            )
            assert background.pixel_size == expected
            assert png_header_size(background.data) == expected
            assert background.data.endswith(PNG_IEND)
            assert background.page_index == index


def check_sink(sink: ArtifactSink, *, payloads: Sequence[bytes]) -> None:
    """Assert the :class:`ArtifactSink` contract for one implementation.

    Parameters
    ----------
    sink
        The implementation under test.
    payloads
        Byte strings to commit. Each is written under its own name.
    """
    for index, payload in enumerate(payloads):
        name = artifact_name(f"artifact-{index:03d}")
        receipt = sink.write(name, payload, media=ArtifactMedia.SVG)
        assert receipt.name == name, "the receipt echoes the caller's stem verbatim"
        assert receipt.byte_count == len(payload)
        assert receipt.media is ArtifactMedia.SVG
        assert receipt.uri


def check_sink_refuses_a_second_write(sink: ArtifactSink) -> None:
    """Assert that a sink with overwriting disabled refuses an existing name.

    Parameters
    ----------
    sink
        The implementation under test, constructed with overwriting off.
    """
    name = artifact_name("collision")
    sink.write(name, b"first", media=ArtifactMedia.SVG)
    try:
        sink.write(name, b"second", media=ArtifactMedia.SVG)
    except ArtifactWriteFailed:
        return
    msg = "a sink with overwriting disabled must refuse an existing name"
    raise AssertionError(msg)


class RasterizerLike(Protocol):
    """The single method :func:`check_rasterizer` needs, for a double that is not an adapter."""

    def to_png(self, page: SvgPage, *, dpi: int) -> RasterImage:
        """Rasterize a page.

        Parameters
        ----------
        page
            The page.
        dpi
            Resolution.

        Returns
        -------
        RasterImage
            The raster.
        """
        ...


class MemoryRasterizer:
    """A rasterizer double that derives its pixel count from the domain formula.

    It exists to prove :func:`check_rasterizer` is a contract and not an adapter test: the only
    way to pass is to call :meth:`PixelSize.from_dpi` and encode a PNG of exactly that size,
    which is what the port requires of the real one.
    """

    def to_png(self, page: SvgPage, *, dpi: int) -> RasterImage:
        """Encode a blank raster of the size the domain formula asks for.

        Parameters
        ----------
        page
            The page whose size decides the raster size.
        dpi
            Resolution.

        Returns
        -------
        RasterImage
            The raster.
        """
        target = PixelSize.from_dpi(page.size, dpi)
        return RasterImage(
            page_ref=page.page_ref,
            media=ImageMedia.PNG,
            data=solid_png(target),
            width=target.width_px,
            height=target.height_px,
            render_dpi=dpi,
        )
