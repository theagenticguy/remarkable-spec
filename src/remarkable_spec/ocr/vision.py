"""Apple Vision framework handwriting recognition.

Uses macOS Vision framework (VNRecognizeTextRequest) for high-quality
handwriting OCR. Renders a reMarkable page to a temporary PNG, then
feeds it to Vision for recognition.

Requires the [ocr] extra: uv add 'remarkable-spec[ocr]'
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class OCRResult:
    """Result of OCR on a single page."""

    text: str
    confidence: float
    lines: list[OCRLine] = field(default_factory=list)


@dataclass
class OCRLine:
    """A single recognized line of text with position and confidence."""

    text: str
    confidence: float
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


def _import_vision():
    """Lazily import Vision framework."""
    try:
        import Vision

        return Vision
    except ImportError:
        raise ImportError(
            "OCR requires Apple Vision framework. Install with: uv add 'remarkable-spec[ocr]'"
        ) from None


def _import_quartz():
    """Lazily import Quartz framework."""
    try:
        import Quartz

        return Quartz
    except ImportError:
        raise ImportError(
            "OCR requires Quartz framework. Install with: uv add 'remarkable-spec[ocr]'"
        ) from None


def ocr_image(image_path: Path) -> OCRResult:
    """Run handwriting OCR on a PNG/JPEG image file.

    Uses Apple Vision's VNRecognizeTextRequest with accurate recognition
    level for best handwriting results.

    Args:
        image_path: Path to the image file (PNG, JPEG, TIFF).

    Returns:
        OCRResult with recognized text, per-line results, and confidence.
    """
    Vision = _import_vision()
    Quartz = _import_quartz()

    # Load image
    image_url = Quartz.NSURL.fileURLWithPath_(str(image_path.resolve()))
    image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
    if image_source is None:
        raise FileNotFoundError(f"Could not load image: {image_path}")

    cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
    if cg_image is None:
        raise ValueError(f"Could not create image from: {image_path}")

    # Create text recognition request
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    # Process
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    success = handler.performRequests_error_([request], None)
    if not success[0]:
        raise RuntimeError(f"Vision request failed: {success[1]}")

    # Extract results
    observations = request.results() or []
    lines: list[OCRLine] = []
    all_text_parts: list[str] = []
    total_confidence = 0.0

    for obs in observations:
        top_candidate = obs.topCandidates_(1)
        if not top_candidate:
            continue

        candidate = top_candidate[0]
        text = candidate.string()
        confidence = candidate.confidence()

        # Get bounding box (normalized 0-1, origin bottom-left)
        bbox = obs.boundingBox()

        lines.append(
            OCRLine(
                text=text,
                confidence=confidence,
                x=bbox.origin.x,
                y=bbox.origin.y,
                width=bbox.size.width,
                height=bbox.size.height,
            )
        )
        all_text_parts.append(text)
        total_confidence += confidence

    full_text = "\n".join(all_text_parts)
    avg_confidence = total_confidence / len(lines) if lines else 0.0

    return OCRResult(
        text=full_text,
        confidence=avg_confidence,
        lines=lines,
    )


def ocr_page(
    rm_path: Path,
    dpi: int = 300,
    thickness: float = 1.5,
) -> OCRResult:
    """Run handwriting OCR on a .rm file.

    Renders the page to a temporary high-DPI PNG, then runs Apple Vision
    handwriting recognition on it.

    Args:
        rm_path: Path to the .rm binary file.
        dpi: Render DPI (higher = better OCR, slower). Default 300.
        thickness: Stroke thickness multiplier. Default 1.5.

    Returns:
        OCRResult with recognized text and per-line details.
    """
    from remarkable_spec.export.svg import export_svg
    from remarkable_spec.formats.rm_file import parse_rm_file
    from remarkable_spec.models.page import Page
    from remarkable_spec.models.screen import RM2_SCREEN

    # Parse .rm file
    layers = parse_rm_file(rm_path)
    page = Page(uuid=uuid4(), layers=layers)

    # Render to temporary SVG, then rasterize to PNG
    with tempfile.TemporaryDirectory() as tmp_dir:
        svg_path = Path(tmp_dir) / "page.svg"
        png_path = Path(tmp_dir) / "page.png"

        export_svg(page, svg_path, screen=RM2_SCREEN, thickness=thickness)

        # Rasterize SVG to PNG using cairosvg
        try:
            import cairosvg
        except ImportError:
            raise ImportError(
                "OCR requires cairosvg for PNG rendering. "
                "Install with: uv add 'remarkable-spec[render,ocr]'"
            ) from None

        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=int(RM2_SCREEN.width * dpi / RM2_SCREEN.dpi),
            output_height=int(RM2_SCREEN.height * dpi / RM2_SCREEN.dpi),
        )

        return ocr_image(png_path)
