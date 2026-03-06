"""PDF page rasterizer for composite backgrounds.

Rasterizes a single PDF page to a base64-encoded PNG suitable for embedding
as an SVG ``<image>`` background beneath .rm stroke overlays.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf


def rasterize_pdf_page(
    pdf_path: Path,
    page_index: int,
    width_pt: float,
    height_pt: float,
) -> tuple[str, float, float]:
    """Rasterize one page of a PDF to a base64-encoded PNG.

    Opens the PDF with PyMuPDF, renders the target page at 2x scale for
    crisp output, and returns the PNG data plus the PDF page's native
    dimensions in points.

    Args:
        pdf_path: Path to the PDF file.
        page_index: Zero-based page index to rasterize.
        width_pt: Target width in PDF points (used for scale calculation).
        height_pt: Target height in PDF points (used for scale calculation).

    Returns:
        Tuple of ``(base64_png, page_width_pt, page_height_pt)``.

    Raises:
        IndexError: If page_index is out of range.
    """
    doc = pymupdf.open(str(pdf_path))
    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError(f"Page index {page_index} out of range (PDF has {len(doc)} pages)")

        page = doc[page_index]
        pdf_rect = page.rect
        page_w_pt = pdf_rect.width
        page_h_pt = pdf_rect.height

        # Render at 2x for crispness
        scale_x = width_pt / page_w_pt * 2.0
        scale_y = height_pt / page_h_pt * 2.0
        scale = min(scale_x, scale_y)

        mat = pymupdf.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_data = pix.tobytes("png")
    finally:
        doc.close()

    b64 = base64.standard_b64encode(png_data).decode("ascii")
    return b64, page_w_pt, page_h_pt
