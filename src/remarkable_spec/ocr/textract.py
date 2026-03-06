"""AWS Textract handwriting recognition.

Uses AWS Textract's DetectDocumentText API for handwriting OCR.
Requires boto3 and valid AWS credentials.
"""

from __future__ import annotations

from pathlib import Path

from remarkable_spec.ocr.vision import OCRLine, OCRResult


def _import_boto3():
    try:
        import boto3

        return boto3
    except ImportError:
        raise ImportError("Textract OCR requires boto3. Install with: uv add boto3") from None


def ocr_image_textract(
    image_path: Path,
    region: str = "us-east-1",
) -> OCRResult:
    """Run handwriting OCR on a PNG/JPEG image via AWS Textract.

    Args:
        image_path: Path to the image file.
        region: AWS region for the Textract API.

    Returns:
        OCRResult with recognized text and per-line details.
    """
    boto3 = _import_boto3()
    client = boto3.client("textract", region_name=region)

    img_bytes = image_path.read_bytes()
    response = client.detect_document_text(Document={"Bytes": img_bytes})

    lines: list[OCRLine] = []
    all_text: list[str] = []
    total_conf = 0.0

    for block in response.get("Blocks", []):
        if block["BlockType"] != "LINE":
            continue

        text = block["Text"]
        confidence = block["Confidence"] / 100.0
        bbox = block.get("Geometry", {}).get("BoundingBox", {})

        lines.append(
            OCRLine(
                text=text,
                confidence=confidence,
                x=bbox.get("Left", 0),
                y=bbox.get("Top", 0),
                width=bbox.get("Width", 0),
                height=bbox.get("Height", 0),
            )
        )
        all_text.append(text)
        total_conf += confidence

    return OCRResult(
        text="\n".join(all_text),
        confidence=total_conf / len(lines) if lines else 0.0,
        lines=lines,
    )
