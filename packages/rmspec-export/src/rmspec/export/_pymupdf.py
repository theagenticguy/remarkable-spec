"""The only module in this workspace that imports ``pymupdf``. Paths in, values out.

Why the import is wrapped in ``catch_warnings``
----------------------------------------------
``pymupdf`` 1.27.1's SWIG bindings emit ``DeprecationWarning: builtin type SwigPyObject has
no __module__ attribute`` while their C types are initialised. Under warnings-as-errors that
escalation happens inside SWIG's type construction and the interpreter *segfaults* rather
than raising -- measured on this machine, ``python -W error -c "import pymupdf"`` exits 139,
while the same interpreter with the ``catch_warnings`` block below exits 0. The workspace
runs pytest with ``filterwarnings = ["error"]``, so an unguarded module-scope import would
not fail one test: it would kill collection, and a collection error is not a test failure --
it is a bogus coverage number instead of a red build, which is the exact failure mode
``tests/architecture/test_coverage_config.py`` exists to catch. The guard is therefore
load-bearing, and it is why every other module in this package reaches ``pymupdf`` only
through here.

Why the C core's own message device is turned off
------------------------------------------------
MuPDF's C core writes its diagnostics straight to a file descriptor, below Python: opening a
truncated PDF printed ``MuPDF error: format error: object is not a stream`` twice while *both*
calls succeeded, measured on this machine. Nothing in Python can capture that -- not
``capsys``, not a ``rich`` console -- so a CLI export of a slightly damaged PDF interleaves C
writes with formatted output and no ``except`` clause ever sees the text. Turning the device
off does not throw the diagnosis away: MuPDF keeps accumulating it in a store, and
:func:`_drain_diagnostics` empties that store into the ``detail`` of every
:class:`PdfBackendError` this module raises. The information therefore moves from an
uncapturable stream onto the typed error that the port already carries to the caller.

What is *not* surfaced, and why that is a domain-shaped gap
---------------------------------------------------------
``Document.is_repaired`` is ``True`` for a PDF MuPDF had to rebuild, and this module reads it
nowhere on purpose. Acting on it would over-reject: a document truncated to 90 % is also
repaired and still yields its text perfectly, measured, while one truncated to 50 % yields
``('',)`` -- indistinguishable from a genuinely text-free scanned page, which the port
explicitly permits. Reporting it needs a field on the reader's result or a degradation
channel, which is :mod:`rmspec.domain`'s decision and not this adapter's. It matters for the
SSH/USB pull path, where a half-transferred PDF is real and the OCR cache would key an empty
answer under a digest, so it is recorded here rather than silently decided.

Why every function takes a path and opens with ``with``
------------------------------------------------------
MuPDF reads lazily from its backing file, so a ``Document`` that outlives the function that
opened it is both a descriptor leak -- a 92-page corpus times three reader methods against
macOS's 256 descriptor soft limit -- and a use-after-unlink once the spooled temporary
backing it is removed. Every function here opens, works and closes within one ``with``
block, and nothing returns a ``Document``. Paths rather than ``bytes`` because the port's
own docstring refuses whole-file bytes: an annotated PDF is large and would be re-parsed
once per method.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from rmspec.domain.ports.export import PixelSize
from rmspec.export._geometry import physical_size_from_points, scale_matrix_for
from rmspec.export._pillow import resize_png_exact

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import pymupdf

_DISPLAY_C_LEVEL_ERRORS = False
"""Whether MuPDF's C core may write its diagnostics to a file descriptor. It may not; see the
module docstring. Bound to a name because the backend's setter is positional-only and a bare
literal there reads as an unexplained flag."""

pymupdf.TOOLS.mupdf_display_errors(_DISPLAY_C_LEVEL_ERRORS)

if TYPE_CHECKING:
    from pathlib import Path

    from rmspec.domain.ports.export import PhysicalSize

__all__ = [
    "BACKEND",
    "PdfBackendError",
    "blob_page_sizes",
    "merge",
    "page_count",
    "page_sizes",
    "page_texts",
    "probe_backend",
    "rasterize",
]

BACKEND = "pymupdf"
"""Backend name carried by every error this module's failures become."""

_TEXT_MODE = "text"
"""Extraction mode relocated verbatim from the legacy annotations command, so annotation
output is unchanged by the move behind the port."""


class PdfBackendError(Exception):
    """A ``pymupdf`` call failed on a document this package handed it.

    Private to this package. Adapters catch it and raise the domain error their port
    documents, which is what keeps ``fitz``'s exception hierarchy off every port.

    Attributes
    ----------
    detail
        Human-readable cause, already stringified so no MuPDF object is retained. Carries
        whatever the C core recorded in its message store, appended by
        :func:`_drain_diagnostics`, because that text has nowhere else to go once the core's
        own descriptor writes are off.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"{BACKEND}: {detail}")
        self.detail = detail


def _drain_diagnostics() -> str:
    """Empty MuPDF's message store, as one parenthesised clause or nothing at all.

    Draining rather than reading: the store is process-global and accumulates across calls, so
    leaving it full would attach one document's repair notes to the next document's failure.
    Every message-producing path here drains it, on success as well as on failure, for that
    reason.

    Returns
    -------
    str
        ``" (mupdf said: ...)"`` with newlines flattened, or ``""`` when the core said nothing.
    """
    recorded = str(pymupdf.TOOLS.mupdf_warnings()).strip()
    if not recorded:
        return ""
    return f" (mupdf said: {'; '.join(line.strip() for line in recorded.splitlines())})"


def probe_backend() -> int:
    """Build a one-page PDF in memory, to prove the MuPDF core is loaded and working.

    Used only by :func:`rmspec.export.availability.require_backends`. It exercises document
    creation, page insertion and serialisation, which is the smallest work that fails when the
    bindings imported but their native core did not.

    Returns
    -------
    int
        Page count of the probe document, which is always 1.

    Raises
    ------
    PdfBackendError
        The core could not create, populate or serialise a document.
    """
    _drain_diagnostics()
    try:
        with pymupdf.open() as document:
            document.new_page(width=1.0, height=1.0)
            serialised = bytes(document.tobytes())
        return len(blob_page_sizes(serialised))
    except Exception as exc:
        msg = f"backend probe failed: {exc}{_drain_diagnostics()}"
        raise PdfBackendError(msg) from exc


def _open(path: Path) -> pymupdf.Document:
    """Open ``path`` as a PDF, translating every failure into :class:`PdfBackendError`.

    Parameters
    ----------
    path
        Filesystem location of the PDF.

    Returns
    -------
    pymupdf.Document
        The open document. Callers must use it inside a ``with`` block.

    Raises
    ------
    PdfBackendError
        The file is missing, is not a PDF, is corrupt, or needs a password. One error for
        all four, because the port collapses them into one domain error for the same reason:
        the caller does the same thing about each.
    """
    # Entry drain, so this call's error carries this call's diagnostics and not the previous
    # document's. Whatever MuPDF says while opening stays in the store deliberately: a document
    # that needed repair to open is exactly the context a later per-page failure wants.
    _drain_diagnostics()
    try:
        document = pymupdf.open(path, filetype="pdf")
    except Exception as exc:
        msg = f"could not open document: {exc}{_drain_diagnostics()}"
        raise PdfBackendError(msg) from exc
    if document.needs_pass:
        document.close()
        msg = "document is encrypted and no password is available"
        raise PdfBackendError(msg)
    return document


def page_count(path: Path) -> int:
    """Count the pages of the PDF at ``path``.

    Parameters
    ----------
    path
        Filesystem location of the PDF.

    Returns
    -------
    int
        Number of pages.

    Raises
    ------
    PdfBackendError
        The document could not be opened or read.
    """
    with _open(path) as document:
        return document.page_count


def page_sizes(path: Path) -> tuple[PhysicalSize, ...]:
    """Measure every page of the PDF at ``path``, with rotation already applied.

    ``page.rect`` rather than ``page.mediabox``: the rect is the rotated, visible box, and
    the port requires orientation-resolved sizes so two conforming adapters cannot disagree
    about which way up a page is.

    Parameters
    ----------
    path
        Filesystem location of the PDF.

    Returns
    -------
    tuple[PhysicalSize, ...]
        One size per page, in document order, in millimetres.

    Raises
    ------
    PdfBackendError
        The document could not be opened, or a page box was degenerate.
    """
    with _open(path) as document:
        try:
            return tuple(
                physical_size_from_points(page.rect.width, page.rect.height) for page in document
            )
        except Exception as exc:
            msg = f"could not measure pages: {exc}{_drain_diagnostics()}"
            raise PdfBackendError(msg) from exc


def page_texts(path: Path) -> tuple[str, ...]:
    """Extract the text of every page of the PDF at ``path``.

    One entry per page, in document order, empty for a page with no extractable text. The
    tuple is built by iterating the document, so its length equals the page count by
    construction -- an adapter cannot silently skip a page and shift every later page's text
    one slot up.

    Parameters
    ----------
    path
        Filesystem location of the PDF.

    Returns
    -------
    tuple[str, ...]
        Page text in document order.

    Raises
    ------
    PdfBackendError
        The document could not be opened or a page could not be decoded.
    """
    with _open(path) as document:
        try:
            return tuple(page.get_text(_TEXT_MODE) for page in document)
        except Exception as exc:
            msg = f"could not extract text: {exc}{_drain_diagnostics()}"
            raise PdfBackendError(msg) from exc


def blob_page_sizes(data: bytes) -> tuple[PhysicalSize, ...]:
    """Measure every page of an in-memory PDF, with rotation already applied.

    The bytes variant exists for the composer's own intermediates and its own output --
    blobs this package just produced and has not committed anywhere, so there is no file to
    open and nothing is gained by spooling one. Source documents still arrive as paths; see
    the module docstring.

    Parameters
    ----------
    data
        PDF bytes.

    Returns
    -------
    tuple[PhysicalSize, ...]
        One size per page, in document order, in millimetres.

    Raises
    ------
    PdfBackendError
        The bytes are not a readable PDF, or a page box was degenerate.
    """
    _drain_diagnostics()
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            return tuple(
                physical_size_from_points(page.rect.width, page.rect.height) for page in document
            )
    except Exception as exc:
        msg = f"could not measure a {len(data)}-byte blob: {exc}{_drain_diagnostics()}"
        raise PdfBackendError(msg) from exc


def merge(parts: tuple[bytes, ...]) -> bytes:
    """Staple one-page PDFs into a single document, preserving list order.

    This is the merge the legacy exporter's comment claimed and its code did not perform:
    it built a blank all-white surface, discarded it, and wrote page one.

    Parameters
    ----------
    parts
        PDF blobs, in output order.

    Returns
    -------
    bytes
        One PDF containing every page of every part, in order.

    Raises
    ------
    PdfBackendError
        A part could not be opened, or the merged document could not be serialised.
    """
    _drain_diagnostics()
    try:
        with pymupdf.open() as output:
            for part in parts:
                with pymupdf.open(stream=part, filetype="pdf") as piece:
                    output.insert_pdf(piece)
            return bytes(output.tobytes())
    except Exception as exc:
        msg = f"could not merge {len(parts)} parts: {exc}{_drain_diagnostics()}"
        raise PdfBackendError(msg) from exc


def rasterize(path: Path, *, page_index: int, target: PixelSize) -> bytes:
    """Render one page of the PDF at ``path`` to PNG at exactly ``target`` pixels.

    The zoom is derived per axis from ``target`` rather than from a re-computed fit scale.
    MuPDF sizes a pixmap by its own ceiling of ``rect * zoom`` while
    :meth:`PixelSize.fit_within` rounds, so an isotropic zoom recomputed beside the domain
    formula disagrees with it wherever the two roundings straddle a half pixel -- 4 of 16
    combinations measured on this machine. Deriving both axes from the figure the domain
    already produced agreed in 16 of 16. The Pillow resample below is the residual guard for
    a case that measurement has not reached, not the primary path.

    Parameters
    ----------
    path
        Filesystem location of the PDF.
    page_index
        Zero-based page index. Assumed in range: the caller bounds-checks against
        :func:`page_count` so it can raise the domain's out-of-range error with the real
        count.
    target
        Exact pixel size the PNG must have.

    Returns
    -------
    bytes
        PNG bytes whose ``IHDR`` declares exactly ``target``.

    Raises
    ------
    PdfBackendError
        The document could not be opened, or the page could not be rendered.
    """
    with _open(path) as document:
        try:
            page = document[page_index]
            zoom_x, zoom_y = scale_matrix_for(target, page.rect.width, page.rect.height)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom_x, zoom_y), alpha=False)
            produced = PixelSize(width_px=pixmap.width, height_px=pixmap.height)
            data = bytes(pixmap.tobytes("png"))
        except Exception as exc:
            msg = f"could not rasterize page {page_index}: {exc}{_drain_diagnostics()}"
            raise PdfBackendError(msg) from exc
    if produced != target:
        return resize_png_exact(data, target=target)
    return data
