"""Diagram detection and Mermaid extraction from handwritten reMarkable pages.

Uses Claude Opus 4.6 via Bedrock to analyze rendered page images, classify
content as text/diagram/mixed, and extract Mermaid syntax from diagrams.

Pipeline:
1. Render .rm file to high-DPI PNG (reuse existing render_rm_to_png)
2. Send PNG to Opus with diagram-specific prompt
3. Parse response for diagram type + Mermaid code
4. Optionally validate Mermaid syntax via mmdc

Usage::

    from remarkable_spec.ocr.diagram import extract_mermaid_from_rm
    result = extract_mermaid_from_rm(Path("page.rm"))
    print(result.mermaid_code)
"""

from __future__ import annotations

import base64
import enum
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class PageContentType(enum.Enum):
    """Classification of a reMarkable page's visual content."""

    TEXT = "TEXT"
    DIAGRAM = "DIAGRAM"
    MIXED = "MIXED"


@dataclass
class MermaidResult:
    """Result of diagram extraction from a reMarkable page.

    Attributes:
        content_type: Whether the page is text, diagram, or mixed.
        diagram_type: The Mermaid diagram type (e.g. "flowchart", "sequenceDiagram"),
            or ``None`` if the page is text-only.
        mermaid_code: The generated Mermaid syntax, or ``None`` if no diagram found.
        raw_response: The raw LLM response for debugging.
    """

    content_type: PageContentType
    diagram_type: str | None
    mermaid_code: str | None
    raw_response: str


DEFAULT_MODEL = "global.anthropic.claude-opus-4-6-v1"
DEFAULT_REGION = "us-east-1"


CLASSIFY_PROMPT = """\
You are analyzing a page from an e-ink tablet that may contain typed/printed \
text, handwritten annotations, or both.

Classify the page content into exactly one category:
- TEXT: Only text (typed or handwritten — notes, lists, paragraphs)
- DIAGRAM: Contains a visual diagram (flowchart, sequence diagram, mind map, etc.)
- MIXED: Contains both text and diagrams

Output ONLY one word: TEXT, DIAGRAM, or MIXED"""


EXTRACTION_PROMPT = """\
You are analyzing a page from an e-ink tablet that may contain typed/printed \
text, handwritten annotations, or both.

First, classify the page content:
- TEXT: Only handwritten text (notes, lists, paragraphs)
- DIAGRAM: Contains a visual diagram (flowchart, sequence diagram, mind map, etc.)
- MIXED: Contains both text and diagrams

If the page contains a DIAGRAM or is MIXED, convert the diagram to Mermaid syntax.

Instructions for diagram extraction:
1. Identify all nodes/boxes and their text labels
2. Identify all connections/arrows between nodes
3. Identify the direction of arrows (one-way, two-way)
4. Identify any labels on connections
5. Determine the best Mermaid diagram type:
   - flowchart TD/LR for box-and-arrow diagrams
   - sequenceDiagram for timeline/interaction diagrams
   - mindmap for tree/radial diagrams
   - classDiagram for class/entity relationships
   - stateDiagram-v2 for state machines
   - erDiagram for entity-relationship diagrams

Rules:
- Use the EXACT text visible in handwritten labels (don't paraphrase)
- Preserve arrow directions faithfully
- Use appropriate node shapes: rectangles for processes, diamonds for decisions, \
circles for start/end
- For flowcharts, detect if the layout is top-down (TD) or left-right (LR)
- Output valid Mermaid syntax that will render correctly

Output format (follow EXACTLY):
CONTENT_TYPE: <TEXT|DIAGRAM|MIXED>
DIAGRAM_TYPE: <flowchart|sequenceDiagram|mindmap|classDiagram|stateDiagram-v2|erDiagram|none>
```mermaid
<valid mermaid code here>
```

If CONTENT_TYPE is TEXT, set DIAGRAM_TYPE to "none" and omit the mermaid code block."""


def classify_page(
    image_path: Path,
    model_id: str = DEFAULT_MODEL,
    region: str = DEFAULT_REGION,
) -> PageContentType:
    """Send PNG to Opus and classify as TEXT, DIAGRAM, or MIXED.

    Args:
        image_path: Path to a rendered PNG image of the page.
        model_id: Bedrock model ID. Defaults to Claude Opus 4.6.
        region: AWS region for Bedrock.

    Returns:
        The page content classification.
    """
    img_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    response = _invoke_bedrock_vision(
        prompt=CLASSIFY_PROMPT,
        img_b64=img_b64,
        media_type="image/png",
        model_id=model_id,
        region=region,
    )
    response_upper = response.strip().upper()
    for ct in PageContentType:
        if ct.value in response_upper:
            return ct
    return PageContentType.TEXT


def extract_mermaid(
    image_path: Path,
    model_id: str = DEFAULT_MODEL,
    region: str = DEFAULT_REGION,
) -> MermaidResult:
    """Send PNG to Opus with diagram extraction prompt.

    Analyzes the rendered page image, classifies its content, and extracts
    Mermaid syntax if a diagram is detected.

    Args:
        image_path: Path to a rendered PNG image of the page.
        model_id: Bedrock model ID. Defaults to Claude Opus 4.6.
        region: AWS region for Bedrock.

    Returns:
        A :class:`MermaidResult` with classification and optional Mermaid code.
    """
    img_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    response = _invoke_bedrock_vision(
        prompt=EXTRACTION_PROMPT,
        img_b64=img_b64,
        media_type="image/png",
        model_id=model_id,
        region=region,
    )
    return _parse_mermaid_response(response)


def extract_mermaid_from_rm(
    rm_path: Path,
    dpi: int = 300,
    thickness: float = 1.5,
    model_id: str = DEFAULT_MODEL,
    region: str = DEFAULT_REGION,
    background_image_b64: str | None = None,
) -> MermaidResult:
    """Full pipeline: .rm -> PNG -> extract Mermaid.

    Renders the reMarkable binary file to a high-DPI PNG, then sends it
    to the LLM for diagram detection and Mermaid extraction.

    Args:
        rm_path: Path to the .rm binary file.
        dpi: Render DPI for the intermediate PNG.
        thickness: Stroke thickness multiplier.
        model_id: Bedrock model ID for extraction.
        region: AWS region for Bedrock.
        background_image_b64: Optional base64-encoded PNG to embed as a
            raster background beneath stroke layers.

    Returns:
        A :class:`MermaidResult` with classification and optional Mermaid code.
    """
    from remarkable_spec.ocr.pipeline import render_rm_to_png

    with tempfile.TemporaryDirectory() as tmp_dir:
        png_path = Path(tmp_dir) / "page.png"
        render_rm_to_png(
            rm_path,
            png_path,
            dpi=dpi,
            thickness=thickness,
            background_image_b64=background_image_b64,
        )
        return extract_mermaid(png_path, model_id=model_id, region=region)


def validate_mermaid(code: str) -> tuple[bool, str]:
    """Validate Mermaid syntax.

    Attempts validation via ``mmdc --input - --output /dev/null`` first.
    If ``mmdc`` is not installed, falls back to checking whether the code
    starts with a recognized Mermaid diagram type keyword.

    Args:
        code: Mermaid diagram source code to validate.

    Returns:
        A tuple of ``(is_valid, error_message)``. When valid, error_message
        is an empty string.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            tmp_out = tmp.name
        try:
            result = subprocess.run(
                ["mmdc", "--input", "-", "--output", tmp_out],
                input=code,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return (result.returncode == 0, result.stderr.strip())
        finally:
            Path(tmp_out).unlink(missing_ok=True)
    except FileNotFoundError:
        # mmdc not installed — basic syntax check
        valid_starts = (
            "flowchart",
            "sequenceDiagram",
            "mindmap",
            "classDiagram",
            "stateDiagram",
            "erDiagram",
            "gantt",
            "pie",
            "graph",
        )
        if any(code.strip().startswith(s) for s in valid_starts):
            return (True, "")
        return (False, "Mermaid code does not start with a recognized diagram type")
    except subprocess.TimeoutExpired:
        return (False, "Mermaid validation timed out")


def _parse_mermaid_response(response: str) -> MermaidResult:
    """Parse the structured LLM response into a MermaidResult.

    Expects the response to contain ``CONTENT_TYPE: ...``, ``DIAGRAM_TYPE: ...``,
    and optionally a fenced ``mermaid`` code block.
    """
    content_match = re.search(r"CONTENT_TYPE:\s*(\w+)", response)
    content_type = (
        PageContentType(content_match.group(1)) if content_match else PageContentType.TEXT
    )

    type_match = re.search(r"DIAGRAM_TYPE:\s*(\S+)", response)
    diagram_type = type_match.group(1) if type_match and type_match.group(1) != "none" else None

    code_match = re.search(r"```mermaid\n(.*?)```", response, re.DOTALL)
    mermaid_code = code_match.group(1).strip() if code_match else None

    return MermaidResult(
        content_type=content_type,
        diagram_type=diagram_type,
        mermaid_code=mermaid_code,
        raw_response=response,
    )


def _invoke_bedrock_vision(
    prompt: str,
    img_b64: str,
    media_type: str,
    model_id: str,
    region: str,
) -> str:
    """Call Bedrock invoke_model with image + text.

    Uses the same pattern as :func:`remarkable_spec.ocr.postprocess._invoke_bedrock_vision`.
    """
    try:
        import boto3
    except ImportError:
        raise ImportError(
            "Diagram extraction requires boto3. Install with: uv add boto3"
        ) from None

    client = boto3.client("bedrock-runtime", region_name=region)

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": 0.0,
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
    return result["content"][0]["text"].strip()
