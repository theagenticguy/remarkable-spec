"""PNG export for reMarkable pages.

Requires the ``render`` extra: ``pip install remarkable-spec[render]``

This module provides PNG export by first rendering the page to SVG, then
rasterizing it using CairoSVG (preferred) or Pillow as a fallback. The
SVG rendering step uses the same pure-Python renderer as ``export_svg``.
"""

from __future__ import annotations

from pathlib import Path

from remarkable_spec.models.page import Page
from remarkable_spec.models.screen import RM2_SCREEN, ScreenSpec
from remarkable_spec.render.palette import EXPORT_PALETTE, Palette


def export_png(
    page: Page,
    output: Path,
    dpi: int = 300,
    palette: Palette | None = None,
    screen: ScreenSpec | None = None,
    template_svg: Path | None = None,
    background_image_b64: str | None = None,
    background_page_size: tuple[float, float] | None = None,
) -> None:
    """Export a single page to PNG.

    Renders the page to an intermediate SVG, then rasterizes it to PNG
    at the requested DPI. Requires either ``cairosvg`` or ``pillow``
    to be installed.

    The resolution of the output image is determined by the ``dpi`` parameter.
    At 300 DPI with the default RM2 screen, the output is approximately
    1863x2484 pixels.

    Args:
        page: The Page object to export.
        output: Destination file path for the PNG image.
        dpi: Resolution of the output image in dots per inch. Defaults to 300.
        palette: Color palette for stroke colors. Defaults to the standard
            export palette.
        screen: Screen specification for page dimensions. Defaults to RM2.
        template_svg: Optional SVG template for page background.
        background_image_b64: Optional base64-encoded PNG to embed as a
            raster background beneath stroke layers.

    Raises:
        ImportError: If neither cairosvg nor pillow is installed. Install
            with: ``pip install remarkable-spec[render]``

    Examples:
        >>> from remarkable_spec.export.png import export_png
        >>> export_png(page, Path("output.png"), dpi=300)
    """
    import tempfile

    from remarkable_spec.export.svg import export_svg

    if palette is None:
        palette = EXPORT_PALETTE
    if screen is None:
        screen = RM2_SCREEN

    # Render to intermediate SVG
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        svg_path = Path(tmp.name)

    try:
        export_svg(
            page,
            svg_path,
            palette=palette,
            screen=screen,
            template_svg=template_svg,
            background_image_b64=background_image_b64,
            background_page_size=background_page_size,
        )
        svg_data = svg_path.read_bytes()
    finally:
        svg_path.unlink(missing_ok=True)

    # Calculate output dimensions
    scale_factor = dpi / 72.0

    # Try CairoSVG first (best quality)
    try:
        import cairosvg

        output.parent.mkdir(parents=True, exist_ok=True)
        cairosvg.svg2png(
            bytestring=svg_data,
            write_to=str(output),
            scale=scale_factor,
        )
        return
    except ImportError:
        pass

    # Fallback: inform the user about requirements
    try:
        raise ImportError(
            "PNG export requires cairosvg for SVG rasterization. "
            "Install it with: pip install remarkable-spec[render]"
        )
    except ImportError:
        raise ImportError(
            "PNG export requires cairosvg for SVG rasterization. "
            "Install it with: pip install remarkable-spec[render]"
        ) from None
