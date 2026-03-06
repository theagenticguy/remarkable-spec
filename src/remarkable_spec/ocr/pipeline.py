"""Full OCR pipeline: render → parallel OCR → LLM merge with image.

This is the recommended entry point for handwriting transcription.

Pipeline:
1. Render .rm file to high-DPI PNG
2. Apple Vision + AWS Textract run in parallel on the PNG
3. Both OCR results + the original PNG go to Claude Opus 4.6 via Bedrock
4. Returns the LLM's corrected transcription

Usage::

    from remarkable_spec.ocr.pipeline import transcribe_rm

    text = transcribe_rm(Path("page.rm"))
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4


def render_rm_to_png(
    rm_path: Path,
    output_path: Path | None = None,
    dpi: int = 300,
    thickness: float = 1.5,
    background_image_b64: str | None = None,
    background_page_size: tuple[float, float] | None = None,
) -> Path:
    """Render a .rm file to a high-DPI PNG.

    Args:
        rm_path: Path to the .rm binary file.
        output_path: Where to write the PNG. If None, writes to a temp file.
        dpi: Target DPI (300 recommended for OCR).
        thickness: Stroke thickness multiplier.
        background_image_b64: Optional base64-encoded PNG to embed as a
            raster background (e.g. a PDF page) beneath stroke layers.

    Returns:
        Path to the rendered PNG file.
    """
    from remarkable_spec.export.svg import export_svg
    from remarkable_spec.formats.rm_file import parse_rm_file
    from remarkable_spec.models.page import Page
    from remarkable_spec.models.screen import detect_screen

    try:
        import cairosvg
    except ImportError:
        raise ImportError(
            "PNG rendering requires cairosvg. Install with: uv add 'remarkable-spec[render]'"
        ) from None

    layers = parse_rm_file(rm_path)
    page = Page(uuid=uuid4(), layers=layers)
    screen = detect_screen(layers)

    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix=".png"))

    svg_path = output_path.with_suffix(".svg")
    export_svg(
        page,
        svg_path,
        screen=screen,
        thickness=thickness,
        background_image_b64=background_image_b64,
        background_page_size=background_page_size,
    )

    cairosvg.svg2png(
        url=str(svg_path),
        write_to=str(output_path),
        output_width=int(screen.width * dpi / screen.dpi),
        output_height=int(screen.height * dpi / screen.dpi),
    )

    svg_path.unlink(missing_ok=True)
    return output_path


def transcribe_rm(
    rm_path: Path,
    dpi: int = 300,
    thickness: float = 1.5,
    model_id: str = "global.anthropic.claude-opus-4-6-v1",
    region: str = "us-east-1",
    background_image_b64: str | None = None,
    background_page_size: tuple[float, float] | None = None,
) -> str:
    """Full pipeline: .rm → PNG → parallel OCR → LLM merge with image.

    This is the recommended function for transcribing reMarkable handwriting.

    Args:
        rm_path: Path to the .rm binary file.
        dpi: Render DPI for the intermediate PNG.
        thickness: Stroke thickness multiplier.
        model_id: Bedrock model ID for the LLM merge step.
        region: AWS region for Textract and Bedrock.
        background_image_b64: Optional base64-encoded PNG to embed as a
            raster background (e.g. a PDF page) beneath stroke layers.

    Returns:
        Clean transcription text.
    """
    from remarkable_spec.ocr.postprocess import transcribe_page

    with tempfile.TemporaryDirectory() as tmp_dir:
        png_path = Path(tmp_dir) / "page.png"
        render_rm_to_png(
            rm_path,
            png_path,
            dpi=dpi,
            thickness=thickness,
            background_image_b64=background_image_b64,
            background_page_size=background_page_size,
        )
        return transcribe_page(
            png_path,
            model_id=model_id,
            region=region,
        )
