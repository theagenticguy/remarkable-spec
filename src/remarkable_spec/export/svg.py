"""SVG export for reMarkable pages.

Pure-Python SVG export with no external dependencies. Uses the
``SVGRenderer`` from the render module to produce self-contained SVG
files from Page objects.
"""

from __future__ import annotations

from pathlib import Path

from remarkable_spec.models.page import Page
from remarkable_spec.models.screen import RM2_SCREEN, ScreenSpec
from remarkable_spec.render.engine import SVGRenderer
from remarkable_spec.render.palette import EXPORT_PALETTE, Palette


def export_svg(
    page: Page,
    output: Path,
    palette: Palette | None = None,
    screen: ScreenSpec | None = None,
    template_svg: Path | None = None,
    thickness: float = 1.5,
    background_image_b64: str | None = None,
    background_page_size: tuple[float, float] | None = None,
) -> None:
    """Export a single page to an SVG file.

    This is a convenience function that creates an ``SVGRenderer`` and
    calls ``render_page``. No external dependencies are required.

    The output SVG uses a viewBox matching the device screen dimensions
    (scaled to PDF points at 72 DPI). Each stroke is rendered as a series
    of ``<line>`` elements with per-segment width, color, and opacity
    computed from the pen rendering formulas.

    Args:
        page: The Page object to export.
        output: Destination file path. Parent directories are created
            automatically.
        palette: Color palette for stroke colors. Defaults to the standard
            export palette (bright, saturated colors).
        screen: Screen specification for page dimensions. Defaults to
            reMarkable 2 (1404x1872 at 226 DPI).
        template_svg: Optional path to an SVG template file to embed as
            the page background.
        thickness: Global stroke-width multiplier. Defaults to 1.5 to
            match on-device visual weight.
        background_image_b64: Optional base64-encoded PNG to embed as a
            raster background (e.g. a PDF page) beneath stroke layers.

    Examples:
        >>> from remarkable_spec.export.svg import export_svg
        >>> export_svg(page, Path("output.svg"))
        >>> export_svg(page, Path("output.svg"), palette=PHYSICAL_PALETTE)
    """
    renderer = SVGRenderer()
    renderer.render_page(
        page=page,
        output=output,
        palette=palette or EXPORT_PALETTE,
        screen=screen or RM2_SCREEN,
        template_svg=template_svg,
        thickness=thickness,
        background_image_b64=background_image_b64,
        background_page_size=background_page_size,
    )
