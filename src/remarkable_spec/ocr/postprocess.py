"""LLM-powered handwriting transcription pipeline.

The pipeline:
1. Apple Vision + AWS Textract run in parallel on a rendered PNG
2. Both OCR results + the original image go to Claude Opus 4.6 via Bedrock
3. The LLM cross-references what it sees in the image with both OCR attempts
   to produce the most accurate transcription possible

This is the recommended approach for reMarkable handwriting — it combines
the strengths of traditional OCR (word boundary detection, character shapes)
with the LLM's linguistic reasoning and visual understanding.
"""

from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from remarkable_spec.ocr.vision import OCRResult

DEFAULT_MODEL = "global.anthropic.claude-opus-4-6-v1"
DEFAULT_REGION = "us-east-1"


SYSTEM_PROMPT = """\
You are an expert at reading sloppy handwriting on e-ink tablets. You excel at \
distinguishing handwritten annotations from typed/printed text, reading proper \
nouns (people, companies, products) in cursive, and interpreting spatial \
relationships like arrows and strikethroughs."""

PIPELINE_PROMPT = """\
You are reading a page from an e-ink tablet that may contain BOTH typed/printed \
text AND handwritten annotations. Produce an accurate transcription of everything \
on the page — typed text verbatim, plus all handwritten additions, edits, and \
annotations.

I'm providing:
1. The original rendered image of the page
2. Two OCR engine outputs that attempted to read both typed and handwritten text

Both OCR engines made errors on the handwriting. Use the image as your primary
source of truth, and use the OCR outputs as hints to help disambiguate difficult
characters. The OCR outputs are generally accurate for typed/printed text.

Context: These are notes and annotated documents from meetings or brainstorming
sessions. The writer uses a calligraphy pen on an e-ink tablet. Pages often
contain typed/printed content (tables, lists, paragraphs) with handwritten
annotations overlaid — arrows, marginal notes, strikethroughs, circled items.

Annotation patterns to recognize (in priority order):

1. STRIKETHROUGHS / CROSS-OUTS (most important to detect):
   Look carefully for hand-drawn lines passing through typed/printed text.
   These are rough pen strokes — not clean CSS lines — so they may be wavy,
   diagonal, or only partially cover the text. ANY pen stroke that crosses
   through typed text is a deletion. Common patterns:
   - A single horizontal line through a word, phrase, or entire row
   - A scribble or zig-zag over typed text
   - A diagonal slash through a word or name
   ALWAYS mark these as [strikethrough: original typed text here].
   If new handwritten text appears nearby, it REPLACES the crossed-out text.
   Example: typed "David" with a line through it + handwritten "Maria" nearby
   → output: [strikethrough: David] Maria

2. Arrows from handwritten text to typed items = assignment or attribution
   (e.g., handwritten "Goes to [name]" with arrow pointing to a list item)

3. Marginal handwritten notes = additions or comments on adjacent typed content

4. Circled or underlined typed text = emphasis

Handwriting disambiguation:
- NAMES ARE COMMON. These notes frequently reference people, companies, and
  products. When handwriting is ambiguous between a common word and a proper
  noun, strongly prefer the proper noun — especially when:
  (a) the text appears near other names in typed content
  (b) the first letter appears capitalized
  (c) the common-word reading doesn't fit the context
- Use typed/printed text on the page as context. If the typed text lists team
  members, a handwritten annotation nearby is almost certainly a name, role,
  or action — not an unrelated word.
- Sloppy cursive merges letters: "rph" can look like "ltipl", "ey" like "y",
  "th" like "ll". Prefer readings that form coherent words or names over
  letter-by-letter decoding of ambiguous strokes.

Rules:
- The image is the ground truth — if you can read something in the image
  that both OCR engines missed, include it
- If the OCR engines disagree, look at the image to determine which is correct
- Fix obvious OCR errors using linguistic context AND visual confirmation
- Preserve the original structure: bullet points, arrows (→), indentation,
  line breaks, underlines
- Mark strikethroughs as [strikethrough: ...] so edits are visible
- Preserve abbreviations as-is (don't expand them)
- Do NOT add content that isn't visible in the image
- Do NOT summarize, interpret, or add commentary
- Output ONLY the corrected transcription

=== Apple Vision OCR ===
{vision_text}

=== AWS Textract OCR ===
{textract_text}

=== Corrected transcription ==="""


def transcribe_page(
    image_path: Path,
    model_id: str = DEFAULT_MODEL,
    region: str = DEFAULT_REGION,
) -> str:
    """Full OCR pipeline: Vision + Textract in parallel, then LLM merge with image.

    This is the recommended entry point for handwriting transcription.

    Args:
        image_path: Path to the rendered PNG image.
        model_id: Bedrock model ID. Defaults to Claude Opus 4.6.
        region: AWS region for Bedrock and Textract.

    Returns:
        Clean transcription text.
    """
    from remarkable_spec.ocr.textract import ocr_image_textract
    from remarkable_spec.ocr.vision import ocr_image

    # Step 1: Run Apple Vision + Textract in parallel
    with ThreadPoolExecutor(max_workers=2) as pool:
        vision_future = pool.submit(ocr_image, image_path)
        textract_future = pool.submit(ocr_image_textract, image_path, region)

        vision_result = vision_future.result()
        textract_result = textract_future.result()

    # Step 2: Send OCR results + original image to LLM
    return merge_with_image(
        image_path=image_path,
        vision_result=vision_result,
        textract_result=textract_result,
        model_id=model_id,
        region=region,
    )


def merge_with_image(
    image_path: Path,
    vision_result: OCRResult,
    textract_result: OCRResult,
    model_id: str = DEFAULT_MODEL,
    region: str = DEFAULT_REGION,
) -> str:
    """Send OCR results + original image to an LLM for final transcription.

    The LLM sees the actual handwriting image alongside both OCR attempts,
    letting it cross-reference visual evidence with textual hints.

    Args:
        image_path: Path to the PNG image.
        vision_result: Apple Vision OCR result.
        textract_result: AWS Textract OCR result.
        model_id: Bedrock model ID (must support vision).
        region: AWS region for Bedrock.

    Returns:
        Corrected transcription text.
    """
    img_bytes = image_path.read_bytes()
    img_b64 = base64.standard_b64encode(img_bytes).decode("ascii")

    prompt_text = PIPELINE_PROMPT.format(
        vision_text=vision_result.text,
        textract_text=textract_result.text,
    )

    return _invoke_bedrock_vision(
        prompt=prompt_text,
        img_b64=img_b64,
        media_type="image/png",
        model_id=model_id,
        region=region,
    )


def _invoke_bedrock_vision(
    prompt: str,
    img_b64: str,
    media_type: str,
    model_id: str,
    region: str,
) -> str:
    """Call Bedrock invoke_model with image + text."""
    try:
        import boto3
    except ImportError:
        raise ImportError("LLM pipeline requires boto3. Install with: uv add boto3") from None

    client = boto3.client("bedrock-runtime", region_name=region)

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "temperature": 1,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
    )

    response = client.invoke_model(modelId=model_id, body=body)
    result = json.loads(response["body"].read())
    # With extended thinking enabled, response has thinking + text blocks.
    # Extract the last text block (the actual transcription).
    for block in reversed(result["content"]):
        if block["type"] == "text":
            return block["text"].strip()
    return result["content"][0]["text"].strip()
