"""The only module in this workspace that imports ``cairosvg``. Bytes in, bytes out.

Two functions, one private exception, and no domain type: the public adapters translate
:class:`CairoError` into :class:`~rmspec.domain.errors.RasterizationFailed` or
:class:`~rmspec.domain.errors.PdfCompositionFailed`, so no ``cairo`` object and no ``cairo``
exception ever reaches a port. ``cairocffi`` is not imported here at all -- ``cairosvg``
imports it internally, which is why it stays a declared install dependency of this package
and is mapped to this package in the architecture suite's ownership table.

``libcairo`` is located before the import, not after
---------------------------------------------------
``cairocffi`` ``dlopen``s at import time and fails with ``OSError``, not ``ImportError``, so
a call-time ``try/except ImportError`` cannot catch it. :mod:`rmspec.export._dyld` runs
first and seeds ``DYLD_FALLBACK_LIBRARY_PATH`` when it is unset on macOS; see that module
for the measurement showing why an in-process assignment is sufficient here.

Unit conventions are *not* decided in this module
------------------------------------------------
:func:`render_pdf` passes no size and no scale unless the caller supplies one, and
:func:`render_png` passes an explicit pixel size and never a ``scale`` or ``dpi``. That is
deliberate: ``cairosvg`` reads a unitless SVG ``width``/``height`` as CSS pixels at 96 per
inch, so identical markup declared in user units and in millimetres produces PDF pages
0.75x apart -- measured, 507.29 unitless user units becomes a 380.47 pt page box while
``507.29mm``-equivalent markup becomes 507.29 pt. Encoding either assumption here would put
a guess about markup this package does not own into the one function that cannot see it.
:class:`~rmspec.export.composer.CairoSvgPdfComposer` measures the first conversion and
re-converts, which is exact under both conventions.
"""

from __future__ import annotations

import sys

if sys.platform == "darwin":
    # Ordering, not style: this has to run before ``cairocffi`` performs its ``dlopen``,
    # which happens while ``cairosvg`` below is being imported. Guarded on the platform
    # because it is the only one whose loader needs the help -- ``ld.so`` finds
    # ``libcairo.so.2`` unaided -- and because a guarded block is what lets the import
    # below stay a plain module-level import instead of a suppressed lint.
    from rmspec.export._dyld import ensure_native_library_path

    ensure_native_library_path()

import cairosvg

__all__ = ["BACKEND", "CairoError", "render_pdf", "render_png"]

BACKEND = "cairosvg"
"""Backend name carried by every error this module's failures become."""


class CairoError(Exception):
    """A ``cairosvg`` call failed, or returned nothing usable.

    Private to this package. Adapters catch it and raise the domain error their port
    documents, which is what keeps a third-party exception type off every port.

    Attributes
    ----------
    detail
        Human-readable cause, already stringified so no ``cairo`` object is retained.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"{BACKEND}: {detail}")
        self.detail = detail


def render_png(svg: str, *, width_px: int, height_px: int) -> bytes:
    """Rasterize SVG markup to PNG at exactly ``width_px`` by ``height_px``.

    ``output_width``/``output_height`` rather than ``scale``: the port requires the returned
    pixel count to equal :meth:`PixelSize.from_dpi` exactly, and ``cairosvg``'s own ``dpi``
    argument defaults to 96 and converts only absolute units. Forcing the surface size is
    the only way to make that equality hold for both unitless and unit-bearing markup --
    measured identical at 72, 150 and 300 dpi for both conventions.

    Parameters
    ----------
    svg
        The SVG document text.
    width_px
        Required output width in pixels.
    height_px
        Required output height in pixels.

    Returns
    -------
    bytes
        PNG bytes.

    Raises
    ------
    CairoError
        The markup could not be parsed, the library failed mid-render, or the call returned
        nothing. Zero-length output is a failure, never a successful empty file.
    """
    try:
        data = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=width_px,
            output_height=height_px,
        )
    except Exception as exc:
        msg = f"svg2png failed: {exc}"
        raise CairoError(msg) from exc
    if not isinstance(data, bytes) or not data:
        msg = "svg2png returned no bytes"
        raise CairoError(msg)
    return data


def render_pdf(svg: str, *, scale: float | None = None) -> bytes:
    """Convert SVG markup to a one-page PDF.

    Parameters
    ----------
    svg
        The SVG document text.
    scale
        Uniform scale factor applied to the emitted page box, or ``None`` to let the
        markup's own declared size decide. The composer calls this twice: once without a
        scale to measure what the markup produces, then once with the ratio that lands the
        page on the size the domain asked for.

    Returns
    -------
    bytes
        PDF bytes holding exactly one page.

    Raises
    ------
    CairoError
        The markup could not be parsed, the library failed mid-convert, or the call returned
        nothing.
    """
    try:
        data = (
            cairosvg.svg2pdf(bytestring=svg.encode("utf-8"))
            if scale is None
            else cairosvg.svg2pdf(bytestring=svg.encode("utf-8"), scale=scale)
        )
    except Exception as exc:
        msg = f"svg2pdf failed: {exc}"
        raise CairoError(msg) from exc
    if not isinstance(data, bytes) or not data:
        msg = "svg2pdf returned no bytes"
        raise CairoError(msg)
    return data
