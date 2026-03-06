"""``rmspec inspect`` -- Inspect reMarkable file contents.

Auto-detects the file type by extension and prints a rich-formatted summary
of the parsed structure.

Supported file types:
  - ``.rm``       -- Binary stroke data (v6 format)
  - ``.metadata`` -- Document metadata JSON
  - ``.content``  -- Document content/page structure JSON
  - ``.pagedata`` -- Per-page template names

Examples
--------
Inspect a .rm file to see layer/stroke statistics::

    rmspec inspect my-notebook/page1.rm

Inspect metadata to see document name and type::

    rmspec inspect a1b2c3d4-5678-9abc-def0-123456789abc.metadata

Get machine-readable JSON output::

    rmspec inspect page.rm --json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from remarkable_spec.models.color import PenColor
from remarkable_spec.models.pen import PenType

app = cyclopts.App(name="inspect", help=__doc__)
console = Console()


@app.default
def inspect_file(
    path: Annotated[
        Path,
        cyclopts.Parameter(
            help="Path to the file to inspect (.rm, .metadata, .content, or .pagedata)"
        ),
    ],
    *,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(
            name="--json",
            help="Output machine-readable JSON instead of rich formatting",
        ),
    ] = False,
) -> None:
    """Inspect a reMarkable file and display its parsed structure.

    Auto-detects the file type from the extension and shows a formatted
    summary. For .rm files, shows layer count, stroke count, pen types,
    and colors. For metadata/content files, shows document properties.

    Examples
    --------
    Inspect a .rm file::

        rmspec inspect path/to/page.rm

    Inspect metadata::

        rmspec inspect doc-uuid.metadata

    Output as JSON::

        rmspec inspect page.rm --json
    """
    if not path.exists():
        console.print(f"[red]Error:[/red] File not found: {path}")
        sys.exit(1)

    suffix = path.suffix.lower()

    if suffix == ".rm":
        _inspect_rm(path, json_output)
    elif suffix == ".metadata":
        _inspect_metadata(path, json_output)
    elif suffix == ".content":
        _inspect_content(path, json_output)
    elif suffix == ".pagedata":
        _inspect_pagedata(path, json_output)
    else:
        console.print(f"[red]Error:[/red] Unsupported file type: {suffix}")
        console.print("Supported types: .rm, .metadata, .content, .pagedata")
        sys.exit(1)


def _inspect_rm(path: Path, json_output: bool) -> None:
    """Inspect a .rm binary file."""
    from remarkable_spec.formats.rm_file import parse_rm_file

    layers = parse_rm_file(path)

    total_strokes = sum(len(layer.strokes) for layer in layers)
    total_points = sum(sum(len(s.points) for s in layer.strokes) for layer in layers)
    total_text_blocks = sum(len(layer.text_blocks) for layer in layers)

    # Collect pen types and colors used
    pen_types_used: set[PenType] = set()
    colors_used: set[PenColor] = set()
    for layer in layers:
        for stroke in layer.strokes:
            pen_types_used.add(stroke.pen_type)
            colors_used.add(stroke.color)

    if json_output:
        data = {
            "file": str(path),
            "layer_count": len(layers),
            "total_strokes": total_strokes,
            "total_points": total_points,
            "total_text_blocks": total_text_blocks,
            "pen_types": sorted(pt.name for pt in pen_types_used),
            "colors": sorted(c.name for c in colors_used),
            "layers": [
                {
                    "name": layer.name or f"Layer {i + 1}",
                    "visible": layer.visible,
                    "stroke_count": len(layer.strokes),
                    "text_block_count": len(layer.text_blocks),
                }
                for i, layer in enumerate(layers)
            ],
        }
        console.print_json(json.dumps(data))
        return

    console.print(f"\n[bold cyan].rm File:[/bold cyan] {path.name}")
    console.print(f"  Layers: {len(layers)}")
    console.print(f"  Total strokes: {total_strokes}")
    console.print(f"  Total points: {total_points}")
    if total_text_blocks:
        console.print(f"  Text blocks: {total_text_blocks}")

    if pen_types_used:
        names = ", ".join(sorted(pt.name for pt in pen_types_used))
        console.print(f"  Pen types: {names}")

    if colors_used:
        names = ", ".join(sorted(c.name for c in colors_used))
        console.print(f"  Colors: {names}")

    # Per-layer detail
    if len(layers) > 1 or any(layer.name for layer in layers):
        console.print()
        tree = Tree("[bold]Layers[/bold]")
        for i, layer in enumerate(layers):
            label = layer.name or f"Layer {i + 1}"
            visibility = "" if layer.visible else " [dim](hidden)[/dim]"
            branch = tree.add(f"{label}{visibility}")
            branch.add(f"Strokes: {len(layer.strokes)}")
            if layer.text_blocks:
                branch.add(f"Text blocks: {len(layer.text_blocks)}")
        console.print(tree)

    console.print()


def _inspect_metadata(path: Path, json_output: bool) -> None:
    """Inspect a .metadata JSON file."""
    from remarkable_spec.formats.metadata import parse_metadata

    meta = parse_metadata(path)

    if json_output:
        data = {
            "file": str(path),
            "visible_name": meta.visible_name,
            "doc_type": meta.doc_type.value,
            "parent": meta.parent,
            "deleted": meta.deleted,
            "pinned": meta.pinned,
            "last_modified": meta.last_modified,
            "last_opened": meta.last_opened,
            "last_opened_page": meta.last_opened_page,
            "version": meta.version,
            "synced": meta.synced,
        }
        console.print_json(json.dumps(data))
        return

    console.print(f"\n[bold cyan].metadata File:[/bold cyan] {path.name}")
    console.print(f"  Name: [bold]{meta.visible_name}[/bold]")
    console.print(f"  Type: {meta.doc_type.value}")
    if meta.parent:
        parent_label = "trash" if meta.parent == "trash" else meta.parent
        console.print(f"  Parent: {parent_label}")
    else:
        console.print("  Parent: [dim](root)[/dim]")

    if meta.deleted:
        console.print("  [red]Deleted[/red]")
    if meta.pinned:
        console.print("  [yellow]Pinned[/yellow]")

    if meta.last_modified:
        from datetime import UTC, datetime

        dt = datetime.fromtimestamp(meta.last_modified / 1000, tz=UTC)
        console.print(f"  Last modified: {dt.isoformat()}")

    if meta.last_opened:
        from datetime import UTC, datetime

        dt = datetime.fromtimestamp(meta.last_opened / 1000, tz=UTC)
        console.print(f"  Last opened: {dt.isoformat()}")

    console.print(f"  Version: {meta.version}")
    console.print(f"  Synced: {meta.synced}")
    console.print()


def _inspect_content(path: Path, json_output: bool) -> None:
    """Inspect a .content JSON file."""
    from remarkable_spec.formats.content import parse_content

    content = parse_content(path)

    if json_output:
        data = {
            "file": str(path),
            "file_type": content.file_type.value,
            "format_version": content.format_version,
            "orientation": content.orientation,
            "page_count": content.page_count,
            "pages": [
                {
                    "uuid": str(pr.uuid),
                    "template": pr.template,
                    "redirect": pr.redirect,
                }
                for pr in content.page_refs
            ],
            "extra_metadata": {
                "last_tool": content.extra_metadata.last_tool,
                "last_pen": content.extra_metadata.last_pen,
            },
            "margins": content.margins,
            "font_name": content.font_name,
            "text_scale": content.text_scale,
            "zoom_mode": content.zoom_mode,
        }
        console.print_json(json.dumps(data))
        return

    console.print(f"\n[bold cyan].content File:[/bold cyan] {path.name}")
    console.print(f"  File type: {content.file_type.value}")
    console.print(f"  Format version: {content.format_version}")
    console.print(f"  Orientation: {content.orientation}")
    console.print(f"  Page count: {content.page_count}")

    if content.extra_metadata.last_tool:
        console.print(f"  Last tool: {content.extra_metadata.last_tool}")
    if content.extra_metadata.last_pen:
        console.print(f"  Last pen: {content.extra_metadata.last_pen}")

    if content.page_refs:
        console.print()
        table = Table(title="Pages")
        table.add_column("#", style="dim", width=4)
        table.add_column("UUID", style="cyan")
        table.add_column("Template")

        for i, pr in enumerate(content.page_refs):
            table.add_row(
                str(i + 1),
                str(pr.uuid)[:8] + "...",
                pr.template or "[dim]Blank[/dim]",
            )
        console.print(table)

    console.print()


def _inspect_pagedata(path: Path, json_output: bool) -> None:
    """Inspect a .pagedata text file."""
    from remarkable_spec.formats.pagedata import parse_pagedata

    templates = parse_pagedata(path)

    if json_output:
        data = {
            "file": str(path),
            "page_count": len(templates),
            "templates": templates,
        }
        console.print_json(json.dumps(data))
        return

    console.print(f"\n[bold cyan].pagedata File:[/bold cyan] {path.name}")
    console.print(f"  Pages: {len(templates)}")
    if templates:
        console.print()
        table = Table(title="Page Templates")
        table.add_column("#", style="dim", width=4)
        table.add_column("Template")

        for i, tmpl in enumerate(templates):
            table.add_row(str(i + 1), tmpl or "[dim]Blank[/dim]")
        console.print(table)

    console.print()
