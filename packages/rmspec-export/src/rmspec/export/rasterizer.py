"""``cairosvg``-backed :class:`~rmspec.domain.ports.export.SvgRasterizer`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rmspec.domain.errors import RasterizationFailed
from rmspec.domain.ports.export import ImageMedia, PixelSize, RasterImage
from rmspec.export import _cairo

if TYPE_CHECKING:
    from rmspec.domain.ports.export import SvgPage

__all__ = ["CairoSvgRasterizer"]


class CairoSvgRasterizer:
    """Rasterize one finished SVG page to PNG at an explicit resolution.

    Satisfies :class:`~rmspec.domain.ports.export.SvgRasterizer`. Stateless, so ``APP``
    scope; the native handle is loaded once per process when :mod:`rmspec.export._cairo` is
    imported.

    What was dropped from the legacy exporter rather than relocated
    --------------------------------------------------------------
    The legacy ``export_png`` rendered the page to a temporary SVG file, read it back,
    rasterized it, and wrote the PNG to a path -- with a ``NamedTemporaryFile`` that leaked
    on any exception between create and unlink, a ``mkdir`` of its own, and a docstring
    promising a Pillow fallback that did not exist. None of that relocates. Bytes come in and
    bytes go out, the file write belongs to
    :class:`~rmspec.export.sink.FilesystemArtifactSink`, and there is no temporary file on
    this path at all.

    The pixel count is the domain's, not the backend's
    -------------------------------------------------
    Legacy passed ``scale = dpi / 72`` and reported whatever surface size ``cairosvg``
    produced. This adapter computes :meth:`PixelSize.from_dpi` first and forces it with
    ``output_width``/``output_height``, because the port states its output dimensions as an
    equality against that formula. The two agree on real page geometry -- measured identical
    at 72, 150 and 300 dpi on a legacy-shaped page -- but the equality is what is asserted,
    so the adapter converts to the domain figure instead of trusting the agreement.
    """

    def to_png(self, page: SvgPage, *, dpi: int) -> RasterImage:
        """Rasterize ``page`` to PNG at ``dpi``.

        Parameters
        ----------
        page
            The SVG document to rasterize, with the physical size it fills.
        dpi
            Pixels per inch of the produced raster. Must be positive.

        Returns
        -------
        RasterImage
            PNG bytes carrying ``page.page_ref`` and ``dpi``, whose ``width`` and ``height``
            equal ``PixelSize.from_dpi(page.size, dpi)``.

        Raises
        ------
        RasterizationFailed
            The markup could not be rasterized, the library failed mid-render, the call
            returned zero bytes, or the produced PNG disagreed with the size it was asked
            for. ``dpi`` below 1 arrives here as the ``ValueError`` the domain formula
            raises, which the CLI maps where it maps ``ValidationError``.
        """
        target = PixelSize.from_dpi(page.size, dpi)
        try:
            data = _cairo.render_png(
                page.svg,
                width_px=target.width_px,
                height_px=target.height_px,
            )
        except _cairo.CairoError as exc:
            raise RasterizationFailed(
                backend=_cairo.BACKEND,
                detail=exc.detail,
                page_ref=page.page_ref,
            ) from exc
        try:
            return RasterImage(
                page_ref=page.page_ref,
                media=ImageMedia.PNG,
                data=data,
                width=target.width_px,
                height=target.height_px,
                render_dpi=dpi,
            )
        except ValueError as exc:
            # The model compares its declared size against the PNG header and requires the
            # terminating IEND chunk, so this is where a backend that rounded its own way or
            # truncated its stream is caught. Reported as a rasterization failure rather than
            # leaked as a validation error, because the caller cannot tell the difference and
            # the port names only one error for it.
            raise RasterizationFailed(
                backend=_cairo.BACKEND,
                detail=f"produced PNG did not match the requested raster: {exc}",
                page_ref=page.page_ref,
            ) from exc
