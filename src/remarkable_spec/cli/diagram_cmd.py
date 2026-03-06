"""``rmspec diagram`` -- Extract Mermaid diagrams from handwritten pages.

Uses Claude Opus 4.6 via Bedrock to analyze rendered reMarkable pages,
detect handwritten diagrams, and convert them to valid Mermaid syntax.

Requires the ``[ocr]`` and ``[render]`` extras::

    uv add 'remarkable-spec[ocr,render]'

Examples
--------
Extract Mermaid from a .rm file::

    rmspec diagram page.rm

Extract from a notebook page by name::

    rmspec diagram "Scratch Pad" --page 3

Extract and render to PNG::

    rmspec diagram "Scratch Pad" --page 3 --render output.png

Extract all pages with diagrams::

    rmspec diagram "System Diagrams" --all
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from remarkable_spec.cli._util import get_xochitl_dir

if TYPE_CHECKING:
    from remarkable_spec.cli._resolve import ResolvedDocument
    from remarkable_spec.ocr.diagram import MermaidResult

app = cyclopts.App(name="diagram", help=__doc__)
console = Console()


@app.default
def diagram(
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
        cyclopts.Parameter(name="--all", help="Extract diagrams from all pages"),
    ] = False,
    render: Annotated[
        Path | None,
        cyclopts.Parameter(help="Render extracted Mermaid to PNG via mmdc"),
    ] = None,
    validate: Annotated[
        bool,
        cyclopts.Parameter(help="Validate extracted Mermaid syntax"),
    ] = False,
    save: Annotated[
        bool,
        cyclopts.Parameter(help="Save .mmd sidecar files next to .rm files"),
    ] = False,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output as JSON"),
    ] = False,
    dpi: Annotated[
        int,
        cyclopts.Parameter(help="Render DPI for extraction"),
    ] = 300,
    thickness: Annotated[
        float,
        cyclopts.Parameter(help="Stroke thickness multiplier"),
    ] = 1.5,
) -> None:
    """Extract Mermaid diagrams from handwritten reMarkable pages."""

    source_path = Path(source)

    if source_path.suffix == ".rm":
        if not source_path.exists():
            console.print(f"[red]Error:[/red] File not found: {source}")
            sys.exit(1)
        # Direct .rm file
        console.print("[dim]Extracting diagram (rendering + Opus analysis)...[/dim]")
        result = _extract_with_cache(source_path, dpi=dpi, thickness=thickness)
        _handle_result(
            result,
            rm_path=source_path,
            page_num=1,
            total_pages=1,
            render_path=render,
            validate_flag=validate,
            save_flag=save,
            json_flag=json_output,
            source_name=source,
        )
        return

    # Document name lookup
    xochitl_dir = get_xochitl_dir(xochitl)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl."
        )
        sys.exit(1)

    from remarkable_spec.cli._resolve import resolve_document_full

    doc_result = resolve_document_full(xochitl_dir, source, console)
    if doc_result is None:
        sys.exit(1)
    rm_files = doc_result.rm_files

    if doc_result.pdf_path:
        console.print("[dim]PDF-backed document → compositing backgrounds[/dim]")

    # Determine which pages to extract
    if page is not None:
        if page < 1 or page > len(rm_files):
            console.print(f"[red]Error:[/red] Page {page} out of range (1-{len(rm_files)}).")
            sys.exit(1)
        targets = [(page, rm_files[page - 1])]
    elif all_pages:
        targets = [(i + 1, p) for i, p in enumerate(rm_files)]
    else:
        # Default: last page
        targets = [(len(rm_files), rm_files[-1])]

    all_results: list[tuple[int, MermaidResult, Path]] = []
    for page_num, rm_path in targets:
        console.print(
            f"[dim]Extracting page {page_num}/{len(rm_files)} (rendering + Opus analysis)...[/dim]"
        )
        bg_result = _get_pdf_bg_diagram(doc_result, page_num - 1, rm_path)
        bg_b64 = bg_result[0] if bg_result else None
        result = _extract_with_cache(
            rm_path, dpi=dpi, thickness=thickness, background_image_b64=bg_b64
        )
        all_results.append((page_num, result, rm_path))

        if save and result.mermaid_code:
            mmd_path = rm_path.with_suffix(".mmd")
            mmd_path.write_text(result.mermaid_code)
            console.print(f"  [green]Saved:[/green] {mmd_path.name}")

    if json_output:
        data = [
            {
                "page": page_num,
                "content_type": result.content_type.value,
                "diagram_type": result.diagram_type,
                "mermaid_code": result.mermaid_code,
            }
            for page_num, result, _ in all_results
        ]
        console.print_json(json.dumps(data))
    else:
        for page_num, result, _rm_path in all_results:
            _display_result(result, page_num)

    # Render the last diagram if --render was specified (only for single-page targets)
    if render and all_results:
        _, last_result, _ = all_results[-1]
        if last_result.mermaid_code:
            _render_mermaid(last_result.mermaid_code, render)

    # Validate all results if --validate
    if validate:
        from remarkable_spec.ocr.diagram import validate_mermaid

        for page_num, result, _ in all_results:
            if result.mermaid_code:
                is_valid, err = validate_mermaid(result.mermaid_code)
                if is_valid:
                    console.print(f"  Page {page_num}: [green]valid[/green]")
                else:
                    console.print(f"  Page {page_num}: [red]invalid[/red] - {err}")


def _extract_with_cache(
    rm_path: Path,
    dpi: int = 300,
    thickness: float = 1.5,
    background_image_b64: str | None = None,
) -> MermaidResult:
    """Extract Mermaid from a .rm file, using the sync DB cache when available."""
    from remarkable_spec.ocr.diagram import (
        DEFAULT_MODEL,
        MermaidResult,
        PageContentType,
        extract_mermaid_from_rm,
    )

    try:
        from remarkable_spec.cli._util import get_sync_db
        from remarkable_spec.sync.hasher import hash_file
        from remarkable_spec.sync.models import DiagramCacheEntry

        rm_hash = hash_file(rm_path)
        db = get_sync_db()
        cached = db.get_diagram(rm_hash)
        if cached and (cached.mermaid_code or cached.content_type == "TEXT"):
            console.print("  [dim](cached)[/dim]")
            return MermaidResult(
                content_type=PageContentType(cached.content_type),
                diagram_type=cached.diagram_type,
                mermaid_code=cached.mermaid_code,
                raw_response="(cached)",
            )
    except Exception:
        # Cache is optional — proceed without it
        rm_hash = None
        db = None

    result = extract_mermaid_from_rm(
        rm_path, dpi=dpi, thickness=thickness, background_image_b64=background_image_b64
    )

    # Store in cache
    if db is not None and rm_hash is not None:
        try:
            from remarkable_spec.sync.models import DiagramCacheEntry

            db.put_diagram(
                DiagramCacheEntry(
                    rm_hash=rm_hash,
                    content_type=result.content_type.value,
                    mermaid_code=result.mermaid_code,
                    diagram_type=result.diagram_type,
                    model_id=DEFAULT_MODEL,
                )
            )
        except Exception:
            pass  # Cache write failure is not fatal

    return result


def _display_result(result: MermaidResult, page_num: int) -> None:
    """Display a MermaidResult to the console with syntax highlighting."""

    title = f"Page {page_num} [{result.content_type.value}]"

    if result.mermaid_code:
        if result.diagram_type:
            title += f" ({result.diagram_type})"
        syntax = Syntax(result.mermaid_code, "text", theme="monokai")
        console.print(Panel(syntax, title=title, border_style="green"))
    else:
        console.print(
            Panel(
                "[dim]No diagram detected on this page.[/dim]",
                title=title,
                border_style="yellow",
            )
        )


def _render_mermaid(code: str, output_path: Path) -> None:
    """Render Mermaid code to PNG via mmdc."""
    with tempfile.NamedTemporaryFile(suffix=".mmd", mode="w", delete=False) as f:
        f.write(code)
        mmd_path = f.name

    try:
        result = subprocess.run(
            ["mmdc", "--input", mmd_path, "--output", str(output_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            console.print(f"[green]Rendered:[/green] {output_path}")
        else:
            console.print(f"[red]Render failed:[/red] {result.stderr.strip()}")
    except FileNotFoundError:
        console.print(
            "[red]Error:[/red] mmdc not found. Install mermaid-cli: "
            "npm install -g @mermaid-js/mermaid-cli"
        )
    except subprocess.TimeoutExpired:
        console.print("[red]Error:[/red] Mermaid rendering timed out.")
    finally:
        Path(mmd_path).unlink(missing_ok=True)


def _handle_result(
    result: MermaidResult,
    *,
    rm_path: Path,
    page_num: int,
    total_pages: int,
    render_path: Path | None,
    validate_flag: bool,
    save_flag: bool,
    json_flag: bool,
    source_name: str,
) -> None:
    """Handle output for a single .rm file target."""
    from remarkable_spec.ocr.diagram import validate_mermaid as _validate

    if save_flag and result.mermaid_code:
        mmd_path = rm_path.with_suffix(".mmd")
        mmd_path.write_text(result.mermaid_code)
        console.print(f"  [green]Saved:[/green] {mmd_path.name}")

    if json_flag:
        data = {
            "source": source_name,
            "content_type": result.content_type.value,
            "diagram_type": result.diagram_type,
            "mermaid_code": result.mermaid_code,
        }
        console.print_json(json.dumps(data))
    else:
        _display_result(result, page_num)

    if render_path and result.mermaid_code:
        _render_mermaid(result.mermaid_code, render_path)

    if validate_flag and result.mermaid_code:
        is_valid, err = _validate(result.mermaid_code)
        if is_valid:
            console.print("  [green]Mermaid syntax: valid[/green]")
        else:
            console.print(f"  [red]Mermaid syntax: invalid[/red] - {err}")


def _get_pdf_bg_diagram(
    result: ResolvedDocument,
    page_idx: int,
    rm_path: Path,
) -> tuple[str, tuple[float, float]] | None:
    """Rasterize a PDF page background for diagram extraction."""
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
