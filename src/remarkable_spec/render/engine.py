"""Rendering engines for converting reMarkable page data to visual output.

Provides an abstract ``RenderEngine`` interface and a pure-Python SVG
implementation that requires no external dependencies.

The SVG renderer produces standards-compliant SVG 1.1 files that can be
opened in any modern browser or vector graphics editor.

Screen coordinate system:
    The reMarkable 2 uses a 1404x1872 pixel canvas at 226 DPI.
    The Paper Pro uses a 1620x2160 pixel canvas at 229 DPI.
    All coordinates in .rm files are in screen pixels.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from pathlib import Path

from remarkable_spec.models.page import Page
from remarkable_spec.models.pen import Pen
from remarkable_spec.models.screen import RM2_SCREEN, ScreenSpec
from remarkable_spec.models.stroke import Stroke
from remarkable_spec.render.palette import EXPORT_PALETTE, Palette
from remarkable_spec.render.pens import get_pen_renderer

# Default rendering constants (reMarkable 2)
SCREEN_WIDTH = 1404
SCREEN_HEIGHT = 1872
SCREEN_DPI = 226
SCALE = 72.0 / SCREEN_DPI


class RenderEngine(ABC):
    """Abstract base class for rendering engines.

    A render engine converts a Page model (layers, strokes, points) into
    a visual output file. Different implementations may produce SVG, PNG,
    PDF, or other formats.
    """

    @abstractmethod
    def render_page(
        self,
        page: Page,
        output: Path,
        palette: Palette | None = None,
        screen: ScreenSpec | None = None,
        template_svg: Path | None = None,
        thickness: float = 1.5,
        background_image_b64: str | None = None,
        background_page_size: tuple[float, float] | None = None,
    ) -> None:
        """Render a single page to an output file.

        Args:
            page: The Page object containing layers and strokes to render.
            output: Path where the output file will be written.
            palette: Color palette for stroke colors. Defaults to EXPORT_PALETTE.
            screen: Screen specification for dimensions. Defaults to RM2_SCREEN.
            template_svg: Optional path to an SVG template file to use as
                the page background.
            thickness: Global stroke-width multiplier. Defaults to 1.5 to
                match on-device visual weight.
            background_image_b64: Optional base64-encoded PNG to embed as a
                raster background (e.g. a PDF page) beneath stroke layers.
            background_page_size: Native (width_pt, height_pt) of the PDF page.
                When provided, the background image is placed at these dimensions
                so strokes align with the PDF coordinate space.
        """
        ...


class SVGRenderer(RenderEngine):
    """Pure-Python SVG renderer with no external dependencies.

    Renders each stroke as a series of SVG ``<line>`` elements with
    per-segment width, color, and opacity computed from the pen rendering
    formulas. The output is a self-contained SVG file with proper viewBox
    dimensions matching the reMarkable screen.

    Usage:
        >>> from remarkable_spec.render.engine import SVGRenderer
        >>> renderer = SVGRenderer()
        >>> renderer.render_page(page, Path("output.svg"))
    """

    SVG_NS = "http://www.w3.org/2000/svg"

    def render_page(
        self,
        page: Page,
        output: Path,
        palette: Palette | None = None,
        screen: ScreenSpec | None = None,
        template_svg: Path | None = None,
        thickness: float = 1.5,
        background_image_b64: str | None = None,
        background_page_size: tuple[float, float] | None = None,
    ) -> None:
        """Render a page to an SVG file.

        Creates an SVG document with:
        - A viewBox matching the screen dimensions
        - Optional raster background (embedded PNG, e.g. from a PDF page)
        - Optional template background (embedded SVG)
        - One ``<g>`` group per visible layer
        - Per-segment ``<line>`` elements with computed stroke attributes

        Args:
            page: The Page object to render.
            output: Destination file path (will be created or overwritten).
            palette: Color palette. Defaults to EXPORT_PALETTE.
            screen: Screen spec for dimensions. Defaults to RM2_SCREEN.
            template_svg: Optional SVG file to embed as background.
            thickness: Global stroke-width multiplier. Defaults to 1.5 to
                match on-device visual weight.
            background_image_b64: Optional base64-encoded PNG to embed as a
                raster background beneath stroke layers.
            background_page_size: Native (width_pt, height_pt) of the PDF page.
        """
        if palette is None:
            palette = EXPORT_PALETTE
        if screen is None:
            screen = RM2_SCREEN

        scale = 72.0 / screen.dpi
        vw = screen.width * scale
        vh = screen.height * scale

        # v6 .rm files use X origin at center of page (not top-left).
        # Shift all X coordinates right by half the page width.
        x_shift = vw / 2

        # Compute padding by scanning stroke extents — ensures all
        # handwriting is visible even if it extends past the page edge
        # (common on PDF-backed docs where annotations overflow).
        min_pad = 30.0  # minimum padding in points (~10.6mm)
        pad_left = min_pad
        pad_top = min_pad
        pad_right = min_pad
        pad_bottom = min_pad

        for layer in page.layers:
            if not layer.visible:
                continue
            for stroke in layer.strokes:
                for pt in stroke.points:
                    px = pt.x * scale + x_shift
                    py = pt.y * scale
                    if px < -pad_left:
                        pad_left = -px + min_pad
                    if px > vw + pad_right:
                        pad_right = px - vw + min_pad
                    if py < -pad_top:
                        pad_top = -py + min_pad
                    if py > vh + pad_bottom:
                        pad_bottom = py - vh + min_pad

        # Expand viewport to fit PDF background if larger than screen
        if background_page_size is not None:
            bg_w, bg_h = background_page_size
            if bg_w > vw + pad_right:
                pad_right = max(pad_right, bg_w - vw + min_pad)
            if bg_h > vh + pad_bottom:
                pad_bottom = max(pad_bottom, bg_h - vh + min_pad)

        # Build SVG root — viewBox includes per-side padding
        svg = ET.Element("svg")
        svg.set("xmlns", self.SVG_NS)
        total_w = pad_left + vw + pad_right
        total_h = pad_top + vh + pad_bottom
        svg.set(
            "viewBox",
            f"{-pad_left:.2f} {-pad_top:.2f} {total_w:.2f} {total_h:.2f}",
        )
        svg.set("width", f"{total_w:.2f}")
        svg.set("height", f"{total_h:.2f}")

        # White background covering the full viewBox
        bg = ET.SubElement(svg, "rect")
        bg.set("x", f"{-pad_left:.2f}")
        bg.set("y", f"{-pad_top:.2f}")
        bg.set("width", f"{total_w:.2f}")
        bg.set("height", f"{total_h:.2f}")
        bg.set("fill", "white")

        # Optional raster background (e.g. PDF page).  The .rm coordinate
        # system maps approximately 1:1 to PDF points after the DPI scale
        # (72/dpi), so the PDF must be placed at its native dimensions for
        # correct vertical alignment.  The horizontal position is shifted so
        # the PDF center aligns with x_shift (the stroke coordinate origin).
        if background_image_b64 is not None:
            if background_page_size is not None:
                bg_w, bg_h = background_page_size
            else:
                bg_w, bg_h = vw, vh
            bg_x = x_shift - bg_w / 2
            self._embed_raster_background(svg, background_image_b64, bg_w, bg_h, bg_x)

        # Optional template background
        if template_svg is not None:
            self._embed_template(svg, template_svg, vw, vh)

        # Render each visible layer
        for layer_idx, layer in enumerate(page.layers):
            if not layer.visible:
                continue

            layer_g = ET.SubElement(svg, "g")
            layer_g.set("id", f"layer-{layer_idx}")
            if layer.name:
                layer_g.set("data-name", layer.name)

            for stroke in layer.strokes:
                self._render_stroke(
                    layer_g,
                    stroke,
                    palette,
                    scale,
                    x_shift,
                    thickness,
                )

        # Write output
        tree = ET.ElementTree(svg)
        ET.indent(tree, space="  ")
        output.parent.mkdir(parents=True, exist_ok=True)
        tree.write(str(output), encoding="unicode", xml_declaration=True)

    def _render_stroke(
        self,
        parent: ET.Element,
        stroke: Stroke,
        palette: Palette,
        scale: float,
        x_shift: float = 0.0,
        thickness: float = 1.5,
    ) -> None:
        """Render a single stroke as a group of line segments.

        Each pair of consecutive points becomes a ``<line>`` element with
        per-segment rendering attributes computed from the pen formulas.

        Args:
            parent: The SVG parent element to append line elements to.
            stroke: The Stroke object to render.
            palette: Color palette for resolving stroke color.
            scale: Coordinate scale factor (points-per-pixel).
            x_shift: X offset in points to apply (v6 format uses
                center-origin X coordinates).
            thickness: Global stroke-width multiplier applied after
                per-segment width computation.
        """

        points = stroke.points
        if len(points) < 2:
            return

        pen = Pen.from_stroke(stroke.pen_type, stroke.thickness_scale)
        renderer = get_pen_renderer(stroke.pen_type, pen.base_width)
        base_rgb = palette.get_rgb(stroke.color)

        stroke_g = ET.SubElement(parent, "g")
        stroke_g.set("stroke-linecap", pen.stroke_linecap)
        stroke_g.set("fill", "none")

        last_width = pen.base_width

        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]

            seg_width = renderer.segment_width(
                speed=p2.speed,
                direction=p2.direction,
                width=p2.width,
                pressure=p2.pressure,
                last_width=last_width,
            )
            seg_color = renderer.segment_color(
                speed=p2.speed,
                direction=p2.direction,
                width=p2.width,
                pressure=p2.pressure,
                last_width=last_width,
                base_color=base_rgb,
            )
            seg_opacity = renderer.segment_opacity(
                speed=p2.speed,
                direction=p2.direction,
                width=p2.width,
                pressure=p2.pressure,
                last_width=last_width,
            )

            # Clamp width to positive value
            seg_width = max(0.1, seg_width)
            seg_width *= thickness

            line = ET.SubElement(stroke_g, "line")
            line.set("x1", f"{p1.x * scale + x_shift:.2f}")
            line.set("y1", f"{p1.y * scale:.2f}")
            line.set("x2", f"{p2.x * scale + x_shift:.2f}")
            line.set("y2", f"{p2.y * scale:.2f}")
            line.set("stroke-width", f"{seg_width * scale:.3f}")
            line.set(
                "stroke",
                f"rgb({seg_color[0]},{seg_color[1]},{seg_color[2]})",
            )
            if seg_opacity < 1.0:
                line.set("opacity", f"{seg_opacity:.3f}")

            last_width = seg_width

    def _embed_template(
        self,
        svg: ET.Element,
        template_path: Path,
        vw: float,
        vh: float,
    ) -> None:
        """Embed an SVG template file as a background group.

        The template SVG is parsed and its child elements are wrapped in a
        ``<g>`` element with id ``template``. The template is scaled to fit
        the page dimensions.

        Args:
            svg: The root SVG element to append the template to.
            template_path: Path to the SVG template file.
            vw: Target viewport width in points.
            vh: Target viewport height in points.
        """
        if not template_path.exists():
            return

        try:
            template_tree = ET.parse(str(template_path))
            template_root = template_tree.getroot()
        except ET.ParseError:
            return

        template_g = ET.SubElement(svg, "g")
        template_g.set("id", "template")
        template_g.set("opacity", "0.5")

        for child in template_root:
            template_g.append(child)

    def _embed_raster_background(
        self,
        svg: ET.Element,
        image_b64: str,
        vw: float,
        vh: float,
        x: float = 0.0,
    ) -> None:
        """Embed a base64-encoded PNG as a raster background.

        Inserts an ``<image>`` element sized to the given dimensions,
        placed after the white background rect but before any stroke layers.

        Args:
            svg: The root SVG element.
            image_b64: Base64-encoded PNG data.
            vw: Image width in points.
            vh: Image height in points.
            x: Horizontal offset in points.  Used to center PDF backgrounds
                on the stroke coordinate origin (x_shift).
        """
        img = ET.SubElement(svg, "image")
        img.set("x", f"{x:.2f}")
        img.set("y", "0")
        img.set("width", f"{vw:.2f}")
        img.set("height", f"{vh:.2f}")
        img.set("preserveAspectRatio", "xMidYMin meet")
        img.set("href", f"data:image/png;base64,{image_b64}")
