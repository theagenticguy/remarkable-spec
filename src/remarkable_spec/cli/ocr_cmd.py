"""``rmspec ocr`` -- Handwriting recognition on reMarkable pages.

Uses Apple Vision framework for high-quality handwriting OCR on rendered
reMarkable pages. Supports single pages or entire notebooks.

Requires the ``[ocr]`` and ``[render]`` extras::

    uv add 'remarkable-spec[ocr,render]'

Examples
--------
OCR a single .rm file::

    rmspec ocr page.rm

OCR a notebook by name (last page)::

    rmspec ocr "Scratch Pad"

OCR a specific page of a notebook::

    rmspec ocr "Scratch Pad" --page 13

OCR all pages and save text files::

    rmspec ocr "Meeting Notes" --all --save
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts
from rich.console import Console
from rich.panel import Panel

from remarkable_spec.cli._util import get_xochitl_dir

if TYPE_CHECKING:
    from remarkable_spec.cli._resolve import ResolvedDocument

app = cyclopts.App(name="ocr", help=__doc__)
console = Console()


@app.default
def ocr(
    source: Annotated[
        str,
        cyclopts.Parameter(help="Path to a .rm file, or a document name to look up"),
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
    all_pages: Annotated[
        bool,
        cyclopts.Parameter(name="--all", help="OCR all pages (not just the last)"),
    ] = False,
    save: Annotated[
        bool,
        cyclopts.Parameter(help="Save recognized text as .txt sidecar files"),
    ] = False,
    dpi: Annotated[
        int,
        cyclopts.Parameter(help="Render DPI for OCR"),
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
    """Run handwriting OCR on reMarkable pages."""
    from remarkable_spec.ocr.pipeline import transcribe_rm

    source_path = Path(source)

    if source_path.suffix == ".rm":
        if not source_path.exists():
            console.print(f"[red]Error:[/red] File not found: {source}")
            sys.exit(1)
        # Direct .rm file — full pipeline
        console.print("[dim]Running OCR pipeline (Vision + Textract → Opus)...[/dim]")
        text = transcribe_rm(source_path, dpi=dpi, thickness=thickness)
        if json_output:
            console.print_json(json.dumps({"source": source, "text": text}))
        else:
            console.print(Panel(text, title=source, border_style="blue"))
        return

    # Document name lookup
    xochitl_dir = get_xochitl_dir(xochitl)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl."
        )
        sys.exit(1)

    # Find document by name or UUID
    from remarkable_spec.cli._resolve import resolve_document_full

    result = resolve_document_full(xochitl_dir, source, console)
    if result is None:
        sys.exit(1)
    rm_files = result.rm_files

    if result.pdf_path:
        console.print("[dim]PDF-backed document → compositing backgrounds for OCR[/dim]")

    # Determine which pages to OCR
    if page is not None:
        if page < 1 or page > len(rm_files):
            console.print(f"[red]Error:[/red] Page {page} out of range (1-{len(rm_files)}).")
            sys.exit(1)
        targets = [(page, rm_files[page - 1])]
    elif all_pages:
        targets = [(i + 1, p) for i, p in enumerate(rm_files)]
    else:
        # Default: last page (most likely the newest notes)
        targets = [(len(rm_files), rm_files[-1])]

    all_results: list[tuple[int, str]] = []
    for page_num, rm_path in targets:
        if rm_path is None and result.pdf_path is None:
            console.print(f"[dim]Skipping page {page_num}/{len(rm_files)} (no annotations)[/dim]")
            continue

        if rm_path is None:
            # PDF-backed page with no handwritten annotations — OCR the bare PDF page
            console.print(
                f"[dim]OCR page {page_num}/{len(rm_files)} (PDF only, no annotations)...[/dim]"
            )
            text = _ocr_pdf_page_only(result, page_num - 1, dpi=dpi)
            if text is None:
                continue
        else:
            console.print(
                f"[dim]OCR page {page_num}/{len(rm_files)} (Vision + Textract → Opus)...[/dim]"
            )
            bg_result = _get_pdf_bg_ocr(result, page_num - 1, rm_path)
            bg_b64 = bg_result[0] if bg_result else None
            bg_size = bg_result[1] if bg_result else None
            text = transcribe_rm(
                rm_path,
                dpi=dpi,
                thickness=thickness,
                background_image_b64=bg_b64,
                background_page_size=bg_size,
            )
        all_results.append((page_num, text))

        if save and rm_path is not None:
            txt_path = rm_path.with_suffix(".ocr.txt")
            txt_path.write_text(text)
            console.print(f"  [green]Saved:[/green] {txt_path.name}")
        elif save:
            console.print(f"  [dim]Page {page_num}: no .rm file to save alongside[/dim]")

    if json_output:
        data = [{"page": page_num, "text": text} for page_num, text in all_results]
        console.print_json(json.dumps(data))
    else:
        for page_num, text in all_results:
            console.print(Panel(text, title=f"Page {page_num}", border_style="blue"))


def _get_pdf_bg_ocr(
    result: ResolvedDocument,
    page_idx: int,
    rm_path: Path,
) -> tuple[str, tuple[float, float]] | None:
    """Rasterize a PDF page background for OCR, using auto-detected screen."""
    if result.pdf_path is None:
        return None
    if page_idx < 0 or page_idx >= len(result.page_indices):
        return None

    from remarkable_spec.formats.rm_file import parse_rm_file
    from remarkable_spec.models.screen import detect_screen
    from remarkable_spec.render.pdf_bg import rasterize_pdf_page

    layers = parse_rm_file(rm_path)
    screen = detect_screen(layers)

    pdf_page_idx = result.page_indices[page_idx]
    b64, pw, ph = rasterize_pdf_page(
        result.pdf_path,
        pdf_page_idx,
        screen.page_width_pt,
        screen.page_height_pt,
    )
    return b64, (pw, ph)


def _ocr_pdf_page_only(
    result: ResolvedDocument,
    page_idx: int,
    dpi: int = 300,
) -> str | None:
    """OCR a bare PDF page (no annotation layer) via the full pipeline."""
    if result.pdf_path is None:
        return None
    if page_idx < 0 or page_idx >= len(result.page_indices):
        return None

    import base64
    import tempfile
    from pathlib import Path as _Path

    from remarkable_spec.ocr.postprocess import transcribe_page
    from remarkable_spec.render.pdf_bg import rasterize_pdf_page

    # Use default Paper Pro screen dimensions for the rasterize
    page_width_pt = 1404.0
    page_height_pt = 1872.0

    pdf_page_idx = result.page_indices[page_idx]
    b64, _pw, _ph = rasterize_pdf_page(
        result.pdf_path,
        pdf_page_idx,
        page_width_pt,
        page_height_pt,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        png_path = _Path(tmp_dir) / "page.png"
        png_path.write_bytes(base64.b64decode(b64))
        return transcribe_page(png_path)
