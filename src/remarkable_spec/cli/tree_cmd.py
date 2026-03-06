"""``rmspec tree`` -- Show folder hierarchy of reMarkable documents.

Displays a tree view of all documents organized by folder structure,
similar to the "My Files" view on the device.

The xochitl directory is resolved from the positional argument or the
``RMSPEC_XOCHITL`` environment variable.

Examples
--------
Show document tree::

    rmspec tree /path/to/xochitl/

Using the RMSPEC_XOCHITL env var::

    export RMSPEC_XOCHITL=~/remarkable-backup/xochitl
    rmspec tree

Machine-readable JSON output::

    rmspec tree --json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console
from rich.tree import Tree

from remarkable_spec.cli._util import get_xochitl_dir
from remarkable_spec.formats.content import parse_content
from remarkable_spec.formats.metadata import parse_metadata
from remarkable_spec.models.document import DocumentType

app = cyclopts.App(name="tree", help=__doc__)
console = Console()


@dataclass
class _TreeEntry:
    """Internal representation of a document or folder for tree display."""

    uuid: str
    name: str
    doc_type: str
    file_type: str
    parent: str
    page_count: int
    children: list[_TreeEntry] = field(default_factory=list)


@app.default
def tree(
    xochitl_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="Path to the xochitl directory (defaults to RMSPEC_XOCHITL env var)"
        ),
    ] = None,
    *,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(
            name="--json",
            help="Output machine-readable JSON instead of rich formatting",
        ),
    ] = False,
) -> None:
    """Show the folder hierarchy of documents in a xochitl directory.

    Scans ``.metadata`` and ``.content`` files to build a tree view of
    all documents organized by their folder structure.

    Examples
    --------
    Show document tree::

        rmspec tree ~/remarkable-backup/xochitl/

    Using env var::

        export RMSPEC_XOCHITL=~/remarkable-backup/xochitl
        rmspec tree

    JSON output::

        rmspec tree --json
    """
    resolved_dir = get_xochitl_dir(xochitl_dir)
    if resolved_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory specified.\n"
            "Provide a path argument or set the "
            "[bold]RMSPEC_XOCHITL[/bold] environment variable:\n\n"
            "  export RMSPEC_XOCHITL=~/remarkable-backup/xochitl"
        )
        sys.exit(1)

    if not resolved_dir.is_dir():
        console.print(f"[red]Error:[/red] Not a directory: {resolved_dir}")
        sys.exit(1)

    entries = _scan_entries(resolved_dir)

    if json_output:
        _output_json(entries)
    else:
        _output_tree(entries)


def _scan_entries(xochitl_dir: Path) -> list[_TreeEntry]:
    """Scan the xochitl directory and build a list of tree entries."""
    entries: list[_TreeEntry] = []

    for metadata_path in sorted(xochitl_dir.glob("*.metadata")):
        doc_uuid = metadata_path.stem

        try:
            meta = parse_metadata(metadata_path)
        except Exception:
            continue

        # Skip deleted and trashed documents
        if meta.deleted or meta.parent == "trash":
            continue

        # Determine file type and page count
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
            _TreeEntry(
                uuid=doc_uuid,
                name=meta.visible_name,
                doc_type=meta.doc_type.value,
                file_type=file_type,
                parent=meta.parent,
                page_count=page_count,
            )
        )

    return entries


def _output_tree(entries: list[_TreeEntry]) -> None:
    """Display entries as a rich folder hierarchy tree."""
    if not entries:
        console.print("[dim]No documents found.[/dim]")
        return

    # Build a lookup by UUID
    by_uuid: dict[str, _TreeEntry] = {e.uuid: e for e in entries}

    # Build children lists
    root_entries: list[_TreeEntry] = []
    for entry in entries:
        if entry.parent and entry.parent in by_uuid:
            by_uuid[entry.parent].children.append(entry)
        elif not entry.parent or entry.parent not in by_uuid:
            root_entries.append(entry)

    rich_tree = Tree("[bold]My Files/[/bold]")
    _add_tree_children(rich_tree, root_entries, by_uuid)
    console.print(rich_tree)


def _add_tree_children(
    tree_node: Tree,
    entries: list[_TreeEntry],
    by_uuid: dict[str, _TreeEntry],
) -> None:
    """Recursively add children to a rich Tree node."""
    # Sort: folders first, then by name
    sorted_entries = sorted(entries, key=lambda e: (e.file_type != "folder", e.name.lower()))

    for entry in sorted_entries:
        if entry.file_type == "folder":
            # Count direct document children
            doc_count = sum(1 for c in entry.children if c.file_type != "folder")
            suffix = f" ({doc_count} doc{'s' if doc_count != 1 else ''})" if doc_count else ""
            label = f"[bold]{entry.name}/[/bold]{suffix}"
            branch = tree_node.add(label)
            _add_tree_children(branch, entry.children, by_uuid)
        else:
            page_str = f"{entry.page_count} page{'s' if entry.page_count != 1 else ''}"
            label = f"{entry.name} [dim]({page_str}, {entry.file_type})[/dim]"
            tree_node.add(label)


def _output_json(entries: list[_TreeEntry]) -> None:
    """Output the tree structure as JSON."""
    # Build a lookup by UUID
    by_uuid: dict[str, _TreeEntry] = {e.uuid: e for e in entries}

    # Build children lists
    root_entries: list[_TreeEntry] = []
    for entry in entries:
        if entry.parent and entry.parent in by_uuid:
            by_uuid[entry.parent].children.append(entry)
        elif not entry.parent or entry.parent not in by_uuid:
            root_entries.append(entry)

    def _to_dict(entry: _TreeEntry) -> dict:
        result: dict = {
            "uuid": entry.uuid,
            "name": entry.name,
            "type": entry.file_type,
        }
        if entry.file_type != "folder":
            result["page_count"] = entry.page_count
        if entry.children:
            result["children"] = [_to_dict(c) for c in entry.children]
        return result

    data = [_to_dict(e) for e in root_entries]
    console.print_json(json.dumps(data))
