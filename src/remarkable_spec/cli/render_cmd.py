"""``rmspec render`` -- Render a .rm file or document to PNG, SVG, or PDF.

Converts reMarkable stroke data into a raster or vector image. The ``source``
argument accepts either a ``.rm`` file path (renders a single page) or a
document name (looks up the document in a xochitl directory and renders all
pages, or a specific page with ``--page``).

When using a document name, the xochitl directory is resolved from the
``--xochitl`` flag or the ``RMSPEC_XOCHITL`` environment variable.

Supported output formats:
  - ``.svg`` -- Scalable vector graphics (pure Python, no extra deps)
  - ``.png`` -- Raster image (requires ``[render]`` extra)
  - ``.pdf`` -- PDF document (requires ``[render]`` extra)

Examples
--------
Render a single .rm file to SVG::

    rmspec render my-notebook/page.rm output.svg

Render a document by name (all pages)::

    rmspec render "My Notebook" ./output-dir/

Render page 3 of a document::

    rmspec render "My Notebook" page3.svg --page 3

Set xochitl directory via env var::

    export RMSPEC_XOCHITL=~/remarkable-backup/xochitl
    rmspec render "My Notebook" ./output/
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import UUID, uuid4

import cyclopts
from rich.console import Console

from remarkable_spec.cli._util import get_xochitl_dir
from remarkable_spec.models.screen import PAPER_PRO_SCREEN, ScreenSpec

if TYPE_CHECKING:
    from remarkable_spec.cli._resolve import ResolvedDocument
    from remarkable_spec.models.page import Page

app = cyclopts.App(name="render", help=__doc__)
console = Console()


@app.default
def render(
    source: Annotated[
        str,
        cyclopts.Parameter(
            help="Path to a .rm file, or a document name to look up in the xochitl directory"
        ),
    ],
    output: Annotated[
        Path,
        cyclopts.Parameter(
            help="Output file path (.svg/.png/.pdf) or directory (for batch rendering)"
        ),
    ],
    *,
    xochitl: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="Path to the xochitl directory (defaults to RMSPEC_XOCHITL env var)"
        ),
    ] = None,
    page: Annotated[
        int | None,
        cyclopts.Parameter(help="Render a specific page (1-indexed)"),
    ] = None,
    thickness: Annotated[
        float,
        cyclopts.Parameter(help="Stroke-width multiplier"),
    ] = 1.5,
    dpi: Annotated[
        int,
        cyclopts.Parameter(help="DPI for raster output"),
    ] = 226,
    background: Annotated[
        Path | None,
        cyclopts.Parameter(help="Path to a background template SVG file"),
    ] = None,
    no_pdf_bg: Annotated[
        bool,
        cyclopts.Parameter(
            name="--no-pdf-bg",
            help="Disable automatic PDF background compositing for PDF-backed documents",
        ),
    ] = False,
    fmt: Annotated[
        str,
        cyclopts.Parameter(
            name="--format",
            help="Output format for batch rendering to a directory (svg, png, pdf)",
        ),
    ] = "svg",
) -> None:
    """Render a .rm file or document to an image or document format."""
    source_path = Path(source)

    if source.endswith(".rm"):
        if not source_path.exists():
            console.print(f"[red]Error:[/red] File not found: {source}")
            sys.exit(1)
        _render_single_rm(source_path, output, thickness=thickness, dpi=dpi, background=background)
    else:
        _render_document_by_name(
            source,
            output,
            xochitl_flag=xochitl,
            page_number=page,
            thickness=thickness,
            dpi=dpi,
            background=background,
            no_pdf_bg=no_pdf_bg,
            batch_format=fmt,
        )


def _render_single_rm(
    rm_file: Path,
    output: Path,
    *,
    thickness: float,
    dpi: int,
    background: Path | None,
) -> None:
    """Render a single .rm file (original behavior)."""
    suffix = output.suffix.lower()
    if suffix not in (".png", ".svg", ".pdf"):
        console.print(f"[red]Error:[/red] Unsupported format: {suffix}")
        console.print("Supported formats: .png, .svg, .pdf")
        sys.exit(1)

    if background and not background.exists():
        console.print(f"[red]Error:[/red] Background not found: {background}")
        sys.exit(1)

    from remarkable_spec.formats.rm_file import parse_rm_file
    from remarkable_spec.models.page import Page
    from remarkable_spec.models.screen import detect_screen

    layers = parse_rm_file(rm_file)
    total_strokes = sum(len(layer.strokes) for layer in layers)
    console.print(f"Parsed {len(layers)} layer(s), {total_strokes} stroke(s)")

    page = Page(uuid=uuid4(), layers=layers)
    screen = detect_screen(layers)

    template_svg = None
    if background:
        template_svg = background

    _export_page(
        page,
        output,
        suffix,
        thickness=thickness,
        dpi=dpi,
        template_svg=template_svg,
        screen=screen,
    )


def _render_document_by_name(
    name: str,
    output: Path,
    *,
    xochitl_flag: Path | None,
    page_number: int | None,
    thickness: float,
    dpi: int,
    background: Path | None,
    no_pdf_bg: bool = False,
    batch_format: str = "svg",
) -> None:
    """Look up a document by name in the xochitl dir and render its pages."""
    xochitl_dir = get_xochitl_dir(xochitl_flag)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory specified.\n"
            "Provide [bold]--xochitl /path/to/xochitl[/bold] or set the "
            "[bold]RMSPEC_XOCHITL[/bold] environment variable:\n\n"
            "  export RMSPEC_XOCHITL=~/remarkable-backup/xochitl"
        )
        sys.exit(1)

    if not xochitl_dir.is_dir():
        console.print(f"[red]Error:[/red] Not a directory: {xochitl_dir}")
        sys.exit(1)

    from remarkable_spec.cli._resolve import resolve_document_full

    result = resolve_document_full(xochitl_dir, name, console)
    if result is None:
        sys.exit(1)

    rm_files = result.rm_files
    doc_name = result.visible_name

    if not rm_files:
        console.print("[red]Error:[/red] Document has no pages.")
        sys.exit(1)

    if result.pdf_path and not no_pdf_bg:
        console.print("[dim]PDF-backed document → compositing backgrounds[/dim]")

    template_svg = background if background and background.exists() else None

    if page_number is not None:
        # Render a specific page
        if page_number < 1 or page_number > len(rm_files):
            console.print(
                f"[red]Error:[/red] Page {page_number} out of range "
                f"(document has {len(rm_files)} page(s))."
            )
            sys.exit(1)

        rm_path = rm_files[page_number - 1]
        if rm_path is not None:
            page_uuid = UUID(rm_path.stem)
            page_obj, screen = _load_page_from_rm(rm_path, page_uuid)
        else:
            # Unannotated page — empty page with PDF background only
            from remarkable_spec.models.page import Page as PageModel

            page_obj = PageModel(uuid=uuid4(), layers=[])
            screen = PAPER_PRO_SCREEN

        bg_result = _get_pdf_bg(result, page_number - 1, screen) if not no_pdf_bg else None
        bg_b64 = bg_result[0] if bg_result else None
        bg_size = bg_result[1] if bg_result else None

        suffix = output.suffix.lower()
        if suffix not in (".png", ".svg", ".pdf"):
            console.print(f"[red]Error:[/red] Unsupported format: {suffix}")
            console.print("Supported formats: .png, .svg, .pdf")
            sys.exit(1)

        _export_page(
            page_obj,
            output,
            suffix,
            thickness=thickness,
            dpi=dpi,
            template_svg=template_svg,
            background_image_b64=bg_b64,
            background_page_size=bg_size,
            screen=screen,
        )
        console.print(f"Rendered page {page_number} of [bold]{doc_name}[/bold]")
    else:
        # Render all pages into output directory
        output.mkdir(parents=True, exist_ok=True)

        rendered = 0
        for i, rm_path in enumerate(rm_files):
            if rm_path is not None:
                page_uuid = UUID(rm_path.stem)
                page_obj, screen = _load_page_from_rm(rm_path, page_uuid)
            else:
                from remarkable_spec.models.page import Page as PageModel

                page_obj = PageModel(uuid=uuid4(), layers=[])
                screen = PAPER_PRO_SCREEN

            bg_result = _get_pdf_bg(result, i, screen) if not no_pdf_bg else None
            bg_b64 = bg_result[0] if bg_result else None
            bg_size = bg_result[1] if bg_result else None

            suffix = f".{batch_format.lstrip('.')}"
            page_file = output / f"page-{i + 1:02d}{suffix}"
            _export_page(
                page_obj,
                page_file,
                suffix,
                thickness=thickness,
                dpi=dpi,
                template_svg=template_svg,
                background_image_b64=bg_b64,
                background_page_size=bg_size,
                screen=screen,
            )
            rendered += 1

        console.print(
            f"[green]Rendered {rendered} page(s)[/green] of [bold]{doc_name}[/bold] to {output}/"
        )


def _load_page_from_rm(rm_path: Path, page_uuid: UUID) -> tuple[Page, ScreenSpec]:
    """Load a .rm file and return a (Page, detected ScreenSpec) tuple."""
    from remarkable_spec.formats.rm_file import parse_rm_file
    from remarkable_spec.models.page import Page
    from remarkable_spec.models.screen import detect_screen

    layers = parse_rm_file(rm_path)
    screen = detect_screen(layers)
    return Page(uuid=page_uuid, layers=layers), screen


def _get_pdf_bg(
    result: ResolvedDocument,
    page_idx: int,
    screen: ScreenSpec,
) -> tuple[str, tuple[float, float]] | None:
    """Rasterize a PDF page background for a resolved document.

    Returns ``(base64_png, (page_w_pt, page_h_pt))`` if PDF-backed,
    otherwise ``None``.
    """
    if result.pdf_path is None:
        return None
    if page_idx < 0 or page_idx >= len(result.page_indices):
        return None

    from remarkable_spec.render.pdf_bg import rasterize_pdf_page

    pdf_page_idx = result.page_indices[page_idx]
    b64, pw, ph = rasterize_pdf_page(
        result.pdf_path,
        pdf_page_idx,
        screen.page_width_pt,
        screen.page_height_pt,
    )
    return b64, (pw, ph)


def _export_page(
    page: Page,
    output: Path,
    suffix: str,
    *,
    thickness: float,
    dpi: int,
    template_svg: Path | None,
    background_image_b64: str | None = None,
    background_page_size: tuple[float, float] | None = None,
    screen: ScreenSpec | None = None,
) -> None:
    """Export a Page to the given output path based on file suffix."""
    if screen is None:
        screen = PAPER_PRO_SCREEN

    if suffix == ".svg":
        from remarkable_spec.export.svg import export_svg

        export_svg(
            page,
            output,
            screen=screen,
            template_svg=template_svg,
            thickness=thickness,
            background_image_b64=background_image_b64,
            background_page_size=background_page_size,
        )
        console.print(f"[green]Wrote SVG:[/green] {output}")

    elif suffix == ".png":
        try:
            from remarkable_spec.export.png import export_png

            export_png(
                page,
                output,
                dpi=dpi,
                screen=screen,
                template_svg=template_svg,
                background_image_b64=background_image_b64,
                background_page_size=background_page_size,
            )
            console.print(f"[green]Wrote PNG:[/green] {output}")
        except ImportError as e:
            console.print(
                f"[red]Error:[/red] {e}\n"
                "Install with: [bold]uv add 'remarkable-spec[render]'[/bold]"
            )
            sys.exit(1)

    elif suffix == ".pdf":
        try:
            from remarkable_spec.export.pdf import export_pdf

            export_pdf(
                [page],
                output,
                screen=screen,
                template_svg=template_svg,
                background_images_b64=[background_image_b64],
            )
            console.print(f"[green]Wrote PDF:[/green] {output}")
        except ImportError as e:
            console.print(
                f"[red]Error:[/red] {e}\n"
                "Install with: [bold]uv add 'remarkable-spec[render]'[/bold]"
            )
            sys.exit(1)
