"""``rmspec annotations`` -- Read PDF annotations as structured text.

For PDF-backed documents with handwritten annotations, this command
identifies what was added, crossed out, or marked up on each page by
comparing the handwritten strokes against the original PDF text.

Pipeline:
1. Render each annotated page with PDF background to PNG
2. Extract original text from the PDF page (PyMuPDF)
3. Send the composite image + original text to Claude Opus 4.6
4. Report structured annotation findings per page

Requires ``[render]``, ``[ocr]``, and ``boto3``::

    uv add 'remarkable-spec[render,ocr]' boto3

Examples
--------
Read all annotations::

    rmspec annotations "Review Draft"

Read annotations on a specific page::

    rmspec annotations "Review Draft" --page 7

JSON output for scripting::

    rmspec annotations "Review Draft" --json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console

from remarkable_spec.cli._util import get_xochitl_dir

app = cyclopts.App(name="annotations", help=__doc__)
console = Console()


ANNOTATIONS_PROMPT = """\
You are analyzing handwritten annotations on a PDF document from a reMarkable \
Paper Pro tablet.

I'm providing:
1. The composite image showing the original PDF content with handwritten \
annotations overlaid
2. The original text from the PDF page (extracted digitally, 100% accurate)

Your task: identify every handwritten annotation and describe what the user did.

Categories of annotations:
- **Crossed out**: A line drawn through existing text (strikethrough)
- **Handwritten note**: New text written by hand near existing content
- **Underline/circle/mark**: Visual emphasis on existing content
- **Correction**: Existing text crossed out with replacement text written nearby

Rules:
- Compare the image against the original text to distinguish printed text \
from handwriting
- For cross-outs, identify the EXACT text that was crossed out
- For handwritten notes, transcribe them and describe WHERE they appear \
(which row, column, or section)
- Be precise about locations — reference specific rows, names, or sections \
from the original text
- Output a numbered list of annotations, one per line
- If no annotations are visible on this page, say "No annotations"

=== Original PDF Text ===
{pdf_text}

=== Annotation Analysis ==="""


@app.default
def annotations(
    source: Annotated[
        str,
        cyclopts.Parameter(help="Document name or UUID to analyze"),
    ],
    *,
    xochitl: Annotated[
        Path | None,
        cyclopts.Parameter(help="Path to xochitl directory (defaults to RMSPEC_XOCHITL)"),
    ] = None,
    page: Annotated[
        int | None,
        cyclopts.Parameter(help="Specific page number (1-indexed)"),
    ] = None,
    dpi: Annotated[
        int,
        cyclopts.Parameter(help="Render DPI for analysis"),
    ] = 300,
    thickness: Annotated[
        float,
        cyclopts.Parameter(help="Stroke thickness multiplier"),
    ] = 1.5,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output as JSON"),
    ] = False,
) -> None:
    """Analyze handwritten annotations on PDF documents."""
    xochitl_dir = get_xochitl_dir(xochitl)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl."
        )
        sys.exit(1)

    from remarkable_spec.cli._resolve import resolve_document_full

    result = resolve_document_full(xochitl_dir, source, console)
    if result is None:
        sys.exit(1)

    if result.pdf_path is None:
        console.print("[red]Error:[/red] This command only works with PDF-backed documents.")
        console.print("For notebooks, use [bold]rmspec ocr[/bold] instead.")
        sys.exit(1)

    try:
        import pymupdf
    except ImportError:
        console.print(
            "[red]Error:[/red] pymupdf is required for annotation analysis.\n"
            "Install with: [bold]uv add pymupdf[/bold]"
        )
        sys.exit(1)

    # Determine which pages to analyze
    rm_files = result.rm_files
    if page is not None:
        if page < 1 or page > len(rm_files):
            console.print(f"[red]Error:[/red] Page {page} out of range (1-{len(rm_files)}).")
            sys.exit(1)
        targets = [(page, rm_files[page - 1])]
    else:
        # All annotated pages
        targets = [
            (i + 1, rm_path)
            for i, rm_path in enumerate(rm_files)
            if rm_path is not None and rm_path.exists() and rm_path.stat().st_size > 0
        ]

    if not targets:
        console.print("[yellow]No annotated pages found.[/yellow]")
        return

    console.print(
        f"[dim]Analyzing {len(targets)} annotated page(s) of "
        f"[bold]{result.visible_name}[/bold]...[/dim]"
    )

    all_results: list[dict] = []
    pdf_doc = pymupdf.open(str(result.pdf_path))

    try:
        for page_num, rm_path in targets:
            if rm_path is None or not rm_path.exists() or rm_path.stat().st_size == 0:
                continue

            console.print(f"[dim]  Page {page_num}: rendering + analyzing...[/dim]")

            # Get PDF page index from redir mapping
            pdf_page_idx = result.page_indices[page_num - 1]

            # Extract original PDF text
            if pdf_page_idx < len(pdf_doc):
                pdf_text = pdf_doc[pdf_page_idx].get_text("text")
            else:
                pdf_text = "(PDF page not available)"

            # Render composite (strokes + PDF background) to PNG
            analysis = _analyze_page(
                rm_path=rm_path,
                pdf_page_idx=pdf_page_idx,
                pdf_path=result.pdf_path,
                pdf_text=pdf_text,
                result=result,
                page_idx=page_num - 1,
                dpi=dpi,
                thickness=thickness,
            )

            all_results.append({"page": page_num, "annotations": analysis})

            if not json_output:
                from rich.panel import Panel

                console.print(Panel(analysis, title=f"Page {page_num}", border_style="green"))
    finally:
        pdf_doc.close()

    if json_output:
        console.print_json(json.dumps(all_results))

    if not json_output and not all_results:
        console.print("[yellow]No annotations found.[/yellow]")


def _analyze_page(
    rm_path: Path,
    pdf_page_idx: int,
    pdf_path: Path,
    pdf_text: str,
    result,
    page_idx: int,
    dpi: int,
    thickness: float,
) -> str:
    """Render a page with PDF background and analyze annotations via Opus."""
    import tempfile

    from remarkable_spec.formats.rm_file import parse_rm_file
    from remarkable_spec.models.screen import detect_screen
    from remarkable_spec.ocr.pipeline import render_rm_to_png
    from remarkable_spec.render.pdf_bg import rasterize_pdf_page

    layers = parse_rm_file(rm_path)
    screen = detect_screen(layers)

    # Rasterize PDF background
    bg_b64, pw, ph = rasterize_pdf_page(
        pdf_path, pdf_page_idx, screen.page_width_pt, screen.page_height_pt
    )

    # Render composite to PNG
    with tempfile.TemporaryDirectory() as tmp_dir:
        png_path = Path(tmp_dir) / "composite.png"
        render_rm_to_png(
            rm_path,
            png_path,
            dpi=dpi,
            thickness=thickness,
            background_image_b64=bg_b64,
            background_page_size=(pw, ph),
        )

        # Send to Opus for analysis
        return _invoke_annotation_analysis(png_path, pdf_text)


def _invoke_annotation_analysis(
    image_path: Path,
    pdf_text: str,
    model_id: str = "global.anthropic.claude-opus-4-6-v1",
    region: str = "us-east-1",
) -> str:
    """Send composite image + PDF text to Opus for annotation analysis."""
    import base64

    try:
        import boto3
    except ImportError:
        raise ImportError(
            "Annotation analysis requires boto3. Install with: uv add boto3"
        ) from None

    img_bytes = image_path.read_bytes()
    img_b64 = base64.standard_b64encode(img_bytes).decode("ascii")

    prompt_text = ANNOTATIONS_PROMPT.format(pdf_text=pdf_text)

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
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        }
    )

    response = client.invoke_model(modelId=model_id, body=body)
    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()
