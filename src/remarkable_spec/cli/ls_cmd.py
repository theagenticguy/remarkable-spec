"""``rmspec ls`` -- List documents in a reMarkable xochitl directory.

Scans the xochitl directory for ``.metadata`` files and displays all documents
with their name, type, page count, and last-modified timestamp.

The xochitl directory is the root data directory on the reMarkable tablet,
typically at ``/home/root/.local/share/remarkable/xochitl/``.  You can also
point this at a local copy or backup of that directory.

The xochitl directory can be provided as a positional argument or via the
``RMSPEC_XOCHITL`` environment variable.

Examples
--------
List all documents::

    rmspec ls /path/to/xochitl/

Using the RMSPEC_XOCHITL env var::

    export RMSPEC_XOCHITL=~/remarkable-backup/xochitl
    rmspec ls

Show folder hierarchy as a tree::

    rmspec ls --tree

Machine-readable JSON output::

    rmspec ls --json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from remarkable_spec.cli._util import get_xochitl_dir
from remarkable_spec.formats.content import parse_content
from remarkable_spec.formats.metadata import parse_metadata
from remarkable_spec.models.document import DocumentType

app = cyclopts.App(name="ls", help=__doc__)
console = Console()


@dataclass
class _DocEntry:
    """Internal representation of a discovered document."""

    uuid: str
    name: str
    doc_type: str  # "DocumentType" or "CollectionType"
    file_type: str  # "notebook", "pdf", "epub", or "folder"
    parent: str
    page_count: int
    last_modified: int
    deleted: bool
    pinned: bool
    children: list[_DocEntry] = field(default_factory=list)


@app.default
def ls_documents(
    xochitl_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="Path to the xochitl directory (defaults to RMSPEC_XOCHITL env var)"
        ),
    ] = None,
    *,
    tree: Annotated[
        bool,
        cyclopts.Parameter(help="Display documents in a folder hierarchy tree"),
    ] = False,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(
            name="--json",
            help="Output machine-readable JSON instead of rich formatting",
        ),
    ] = False,
    show_deleted: Annotated[
        bool,
        cyclopts.Parameter(
            name="--deleted",
            help="Include deleted/trashed documents in output",
        ),
    ] = False,
) -> None:
    """List all documents in a reMarkable xochitl directory.

    Scans for .metadata files and shows each document's name, type
    (notebook/pdf/epub/folder), page count, and last-modified date.

    The xochitl directory can be provided as a positional argument or
    via the ``RMSPEC_XOCHITL`` environment variable.

    Examples
    --------
    Basic listing::

        rmspec ls ~/remarkable-backup/xochitl/

    Using env var::

        export RMSPEC_XOCHITL=~/remarkable-backup/xochitl
        rmspec ls

    Folder tree view::

        rmspec ls --tree

    Include trashed documents::

        rmspec ls --deleted

    JSON output for scripting::

        rmspec ls --json
    """
    xochitl_dir = get_xochitl_dir(xochitl_dir)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory specified.\n"
            "Provide a path argument or set the "
            "[bold]RMSPEC_XOCHITL[/bold] environment variable:\n\n"
            "  export RMSPEC_XOCHITL=~/remarkable-backup/xochitl"
        )
        sys.exit(1)

    if not xochitl_dir.is_dir():
        console.print(f"[red]Error:[/red] Not a directory: {xochitl_dir}")
        sys.exit(1)

    entries = _scan_documents(xochitl_dir)

    if not show_deleted:
        entries = [e for e in entries if not e.deleted]

    if json_output:
        _output_json(entries)
    elif tree:
        _output_tree(entries)
    else:
        _output_table(entries)


def _scan_documents(xochitl_dir: Path) -> list[_DocEntry]:
    """Scan the xochitl directory and build a list of document entries."""
    entries: list[_DocEntry] = []

    for metadata_path in sorted(xochitl_dir.glob("*.metadata")):
        doc_uuid = metadata_path.stem

        try:
            meta = parse_metadata(metadata_path)
        except Exception:
            continue

        # Determine file type
        file_type = "folder"
        page_count = 0
        if meta.doc_type == DocumentType.DOCUMENT:
            content_path = xochitl_dir / f"{doc_uuid}.content"
            if content_path.exists():
                try:
                    content = parse_content(content_path)
                    file_type = content.file_type.value
                    page_count = content.page_count or len(content.page_refs)
                except Exception:
                    file_type = "unknown"
            else:
                file_type = "unknown"

        entries.append(
            _DocEntry(
                uuid=doc_uuid,
                name=meta.visible_name,
                doc_type=meta.doc_type.value,
                file_type=file_type,
                parent=meta.parent,
                page_count=page_count,
                last_modified=meta.last_modified,
                deleted=meta.deleted or meta.parent == "trash",
                pinned=meta.pinned,
            )
        )

    return entries


def _output_table(entries: list[_DocEntry]) -> None:
    """Display entries as a rich table."""
    if not entries:
        console.print("[dim]No documents found.[/dim]")
        return

    table = Table(title="Documents")
    table.add_column("Name", style="bold")
    table.add_column("Type", style="cyan")
    table.add_column("Pages", justify="right")
    table.add_column("Last Modified")
    table.add_column("UUID", style="dim")

    # Sort: folders first, then by name
    sorted_entries = sorted(entries, key=lambda e: (e.file_type != "folder", e.name.lower()))

    for entry in sorted_entries:
        # Format type with icon
        type_str = _format_type(entry.file_type)

        # Format page count
        pages_str = str(entry.page_count) if entry.page_count > 0 else ""

        # Format date
        date_str = ""
        if entry.last_modified:
            dt = datetime.fromtimestamp(entry.last_modified / 1000, tz=UTC)
            date_str = dt.strftime("%Y-%m-%d %H:%M")

        # Pinned indicator
        name_str = entry.name
        if entry.pinned:
            name_str = f"* {name_str}"

        table.add_row(name_str, type_str, pages_str, date_str, entry.uuid[:8] + "...")

    console.print(table)
    console.print(f"\n[dim]{len(entries)} document(s)[/dim]")


def _output_tree(entries: list[_DocEntry]) -> None:
    """Display entries as a folder hierarchy tree."""
    if not entries:
        console.print("[dim]No documents found.[/dim]")
        return

    # Build a lookup by UUID
    by_uuid: dict[str, _DocEntry] = {e.uuid: e for e in entries}

    # Build children lists
    root_entries: list[_DocEntry] = []
    for entry in entries:
        if entry.parent and entry.parent in by_uuid:
            by_uuid[entry.parent].children.append(entry)
        elif not entry.parent or entry.parent not in by_uuid:
            root_entries.append(entry)

    tree = Tree("[bold]xochitl[/bold]")
    _add_tree_children(tree, root_entries)
    console.print(tree)
    console.print(f"\n[dim]{len(entries)} document(s)[/dim]")


def _add_tree_children(tree_node: Tree, entries: list[_DocEntry]) -> None:
    """Recursively add children to a rich Tree node."""
    # Sort: folders first, then by name
    sorted_entries = sorted(entries, key=lambda e: (e.file_type != "folder", e.name.lower()))

    for entry in sorted_entries:
        type_str = _format_type(entry.file_type)
        label = f"{entry.name} [dim]({type_str})[/dim]"
        if entry.page_count:
            label += f" [dim]{entry.page_count}p[/dim]"

        if entry.children:
            branch = tree_node.add(label)
            _add_tree_children(branch, entry.children)
        else:
            tree_node.add(label)


def _output_json(entries: list[_DocEntry]) -> None:
    """Output entries as JSON."""
    data = [
        {
            "uuid": e.uuid,
            "name": e.name,
            "doc_type": e.doc_type,
            "file_type": e.file_type,
            "parent": e.parent,
            "page_count": e.page_count,
            "last_modified": e.last_modified,
            "deleted": e.deleted,
            "pinned": e.pinned,
        }
        for e in entries
    ]
    console.print_json(json.dumps(data))


def _format_type(file_type: str) -> str:
    """Format a file type string for display."""
    return {
        "folder": "folder",
        "notebook": "notebook",
        "pdf": "pdf",
        "epub": "epub",
    }.get(file_type, file_type)
