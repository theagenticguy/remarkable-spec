"""``rmspec device`` -- Access a reMarkable tablet over SSH/USB.

Provides subcommands for interacting with a reMarkable device directly:

- ``rmspec device info``  -- Show device firmware version, model, storage
- ``rmspec device ls``    -- List documents on the device
- ``rmspec device pull``  -- Download a document from the device
- ``rmspec device push``  -- Upload a PDF or EPUB to the device

All device commands connect via SSH (USB cable or Wi-Fi). The default host
is ``10.11.99.1`` (USB connection). You can override with ``--host``.

This command requires the ``[device]`` extra to be installed::

    uv add 'remarkable-spec[device]'

Examples
--------
Show device info via USB::

    rmspec device info

List documents on the device::

    rmspec device ls

Download a notebook::

    rmspec device pull "My Notebook" ./backup/

Upload a PDF::

    rmspec device push report.pdf --name "Status Report"
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console

from remarkable_spec.cli._util import settings

app = cyclopts.App(name="device", help=__doc__)
console = Console()


def _check_device_deps() -> bool:
    """Check if device dependencies are installed."""
    try:
        import paramiko  # noqa: F401

        return True
    except ImportError:
        console.print(
            "[red]Error:[/red] Device dependencies not installed.\n"
            "Install with: [bold]uv add 'remarkable-spec[device]'[/bold]"
        )
        return False


@app.command
def info(
    *,
    host: Annotated[
        str,
        cyclopts.Parameter(help="Device hostname or IP (default: 10.11.99.1 for USB)"),
    ] = settings.device_host,
    user: Annotated[
        str,
        cyclopts.Parameter(help="SSH username (default: root)"),
    ] = settings.device_user,
    password: Annotated[
        str | None,
        cyclopts.Parameter(help="SSH password (if not using key auth)"),
    ] = None,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output machine-readable JSON"),
    ] = False,
) -> None:
    """Show device information (model, firmware, storage).

    Connects to the reMarkable via SSH and reads device info from system
    files and the xochitl configuration.

    Examples
    --------
    Via USB (default)::

        rmspec device info

    Via Wi-Fi with a specific IP::

        rmspec device info --host 192.168.1.42

    With password authentication::

        rmspec device info --password mypassword
    """
    if not _check_device_deps():
        sys.exit(1)

    import json as json_mod

    from remarkable_spec.device.connection import DeviceConnection

    key_path = Path("~/.ssh/id_ed25519_remarkable").expanduser()
    pw = password or settings.device_password

    try:
        with DeviceConnection(
            host=host,
            user=user,
            password=pw,
            key_path=key_path if key_path.exists() else None,
        ) as conn:
            firmware = conn.execute("cat /etc/version").strip()
            model = conn.execute(
                "cat /sys/devices/soc0/machine 2>/dev/null || echo 'Unknown'"
            ).strip()
            serial = conn.execute(
                "cat /sys/devices/soc0/serial_number 2>/dev/null || echo 'Unknown'"
            ).strip()
            mem = conn.execute("free -h | head -2").strip()
            disk = conn.execute("df -h /home | tail -1").strip()
    except ConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if json_output:
        data = {
            "host": host,
            "model": model,
            "serial": serial,
            "firmware": firmware,
            "memory": mem,
            "disk": disk,
        }
        console.print(json_mod.dumps(data, indent=2))
        return

    from rich.panel import Panel
    from rich.table import Table

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")
    table.add_row("Model", model)
    table.add_row("Serial", serial)
    table.add_row("Firmware", firmware)
    table.add_row("Memory", mem)
    table.add_row("Disk (/home)", disk)

    console.print(Panel(table, title=f"[bold]reMarkable @ {host}[/bold]", expand=False))


@app.command
def ls(
    *,
    host: Annotated[
        str,
        cyclopts.Parameter(help="Device hostname or IP (default: 10.11.99.1 for USB)"),
    ] = settings.device_host,
    user: Annotated[
        str,
        cyclopts.Parameter(help="SSH username (default: root)"),
    ] = settings.device_user,
    password: Annotated[
        str | None,
        cyclopts.Parameter(help="SSH password (if not using key auth)"),
    ] = None,
    tree: Annotated[
        bool,
        cyclopts.Parameter(help="Display documents in a folder hierarchy tree"),
    ] = False,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output machine-readable JSON"),
    ] = False,
) -> None:
    """List all documents on the device.

    Connects via SSH, reads the xochitl directory, and lists all documents
    with their name, type, and page count.

    Examples
    --------
    List all documents on device::

        rmspec device ls

    Show folder tree::

        rmspec device ls --tree

    JSON output for scripting::

        rmspec device ls --json
    """
    if not _check_device_deps():
        sys.exit(1)

    import json as json_mod

    from remarkable_spec.device.web_api import WebAPI

    try:
        api = WebAPI(base_url=f"http://{host}")
        docs = api.list_all_documents()
    except Exception as exc:
        console.print(f"[red]Error:[/red] Failed to connect to device web API: {exc}")
        console.print(
            "Ensure the device is connected via USB and the web interface is enabled "
            "(Settings > Storage > USB web interface).\n"
            "[dim]Tip: SSH-based commands work without the web interface:[/dim]\n"
            "[dim]  rmspec sync pull   (incremental sync via SSH)[/dim]"
        )
        sys.exit(1)

    if not docs:
        console.print("[yellow]No documents found on device.[/yellow]")
        return

    def _get_name(d: dict) -> str:
        return d.get("VissibleName", d.get("VisibleName", d.get("VisssibleName", "Untitled")))

    if json_output:
        console.print(json_mod.dumps(docs, indent=2))
        return

    if tree:
        from rich.tree import Tree

        # Build folder hierarchy
        folders: dict[str, dict] = {}
        documents: list[dict] = []
        for d in docs:
            if d.get("Type") == "CollectionType":
                folders[d["ID"]] = d
            else:
                documents.append(d)

        root_tree = Tree("[bold]reMarkable Documents[/bold]")
        folder_trees: dict[str, Tree] = {}

        # Create folder nodes
        for fid, f in folders.items():
            folder_trees[fid] = Tree(f"[bold blue]{_get_name(f)}[/bold blue]/")

        # Attach folders to parents
        for fid, f in folders.items():
            parent_id = f.get("Parent", "")
            if parent_id and parent_id in folder_trees:
                folder_trees[parent_id].add(folder_trees[fid])
            else:
                root_tree.add(folder_trees[fid])

        # Attach documents to folders
        for d in documents:
            parent_id = d.get("Parent", "")
            doc_label = f"{_get_name(d)}  [dim]({d.get('ID', '?')[:8]}...)[/dim]"
            if parent_id and parent_id in folder_trees:
                folder_trees[parent_id].add(doc_label)
            else:
                root_tree.add(doc_label)

        console.print(root_tree)
        return

    # Default: table view
    from rich.table import Table

    table = Table(title=f"Documents on {host}")
    table.add_column("Name", style="bold")
    table.add_column("Type", style="cyan")
    table.add_column("ID", style="dim")

    for d in sorted(docs, key=lambda x: _get_name(x).lower()):
        doc_type = d.get("Type", "Unknown")
        type_label = "Folder" if doc_type == "CollectionType" else "Document"
        doc_id = d.get("ID", "?")
        truncated_id = f"{doc_id[:8]}..." if len(doc_id) > 8 else doc_id
        table.add_row(_get_name(d), type_label, truncated_id)

    console.print(table)


@app.command
def pull(
    doc_name: Annotated[
        str,
        cyclopts.Parameter(help="Name of the document to download (or UUID)"),
    ],
    dest: Annotated[
        Path,
        cyclopts.Parameter(help="Local destination directory for the downloaded files"),
    ],
    *,
    host: Annotated[
        str,
        cyclopts.Parameter(help="Device hostname or IP (default: 10.11.99.1 for USB)"),
    ] = settings.device_host,
    user: Annotated[
        str,
        cyclopts.Parameter(help="SSH username (default: root)"),
    ] = settings.device_user,
    password: Annotated[
        str | None,
        cyclopts.Parameter(help="SSH password (if not using key auth)"),
    ] = None,
) -> None:
    """Download a document from the device.

    Copies all files for the specified document (metadata, content, pages,
    thumbnails) to the local destination directory.

    Examples
    --------
    Download by name::

        rmspec device pull "My Notebook" ./backup/

    Download by UUID::

        rmspec device pull a1b2c3d4-5678-9abc-def0-123456789abc ./backup/
    """
    if not _check_device_deps():
        sys.exit(1)

    from remarkable_spec.device.connection import DeviceConnection
    from remarkable_spec.device.sync import SyncManager
    from remarkable_spec.device.web_api import WebAPI
    from remarkable_spec.sync.models import SyncDocument, SyncLogEntry

    # 1. Resolve document name to UUID via WebAPI
    try:
        api = WebAPI(base_url=f"http://{host}")
        docs = api.list_all_documents()
    except Exception as exc:
        console.print(f"[red]Error:[/red] Failed to query device web API: {exc}")
        sys.exit(1)

    def _get_name(d: dict) -> str:
        return d.get("VissibleName", d.get("VisibleName", d.get("VisssibleName", "")))

    # Check if doc_name looks like a UUID (contains dashes and hex chars)
    import re

    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
    )
    if uuid_pattern.match(doc_name):
        matches = [d for d in docs if d.get("ID") == doc_name]
    else:
        matches = [d for d in docs if doc_name.lower() in _get_name(d).lower()]

    if not matches:
        console.print(f"[red]Error:[/red] No document matching '{doc_name}' found on device.")
        sys.exit(1)

    if len(matches) > 1:
        console.print(f"[red]Error:[/red] Multiple documents match '{doc_name}':")
        for m in matches:
            console.print(f"  - {_get_name(m)} ({m.get('ID', '?')})")
        console.print("Use the full UUID to specify which document to pull.")
        sys.exit(1)

    doc = matches[0]
    doc_uuid = doc["ID"]
    visible_name = _get_name(doc)

    # 2. Pull via SSH using SyncManager
    key_path = Path("~/.ssh/id_ed25519_remarkable").expanduser()
    pw = password or settings.device_password

    try:
        with DeviceConnection(
            host=host,
            user=user,
            password=pw,
            key_path=key_path if key_path.exists() else None,
        ) as conn:
            sync = SyncManager()
            console.print(f"Pulling [bold]{visible_name}[/bold] ({doc_uuid[:8]}...) to {dest} ...")
            sync.pull_document(doc_uuid, dest, conn)
    except ConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        # Log failure to SyncDB
        try:
            from remarkable_spec.cli._util import get_sync_db

            db = get_sync_db()
            db.log_sync(
                SyncLogEntry(
                    direction="pull",
                    doc_uuid=doc_uuid,
                    doc_name=visible_name,
                    status="error",
                    details=str(exc),
                    device_host=host,
                )
            )
        except Exception:
            pass
        console.print(f"[red]Error:[/red] Failed to pull document: {exc}")
        sys.exit(1)

    # 3. Record in SyncDB
    try:
        from remarkable_spec.cli._util import get_sync_db

        db = get_sync_db()
        db.upsert_document(
            SyncDocument(
                doc_uuid=doc_uuid,
                visible_name=visible_name,
                doc_type=doc.get("Type", "DocumentType"),
                local_path=str(dest),
            )
        )
        db.log_sync(
            SyncLogEntry(
                direction="pull",
                doc_uuid=doc_uuid,
                doc_name=visible_name,
                status="ok",
                device_host=host,
            )
        )
    except Exception:
        pass  # SyncDB logging is best-effort

    console.print(f"[green]Successfully pulled[/green] [bold]{visible_name}[/bold] to {dest}")


@app.command
def push(
    file: Annotated[
        Path,
        cyclopts.Parameter(help="File to upload (PDF, EPUB, .md, .mmd, .txt)"),
    ],
    *,
    name: Annotated[
        str | None,
        cyclopts.Parameter(help="Display name on the device (default: filename stem)"),
    ] = None,
    folder: Annotated[
        str | None,
        cyclopts.Parameter(help="Target folder name on device (resolves to UUID)"),
    ] = None,
    parent: Annotated[
        str,
        cyclopts.Parameter(help="Parent folder UUID (use --folder for name lookup)"),
    ] = "",
    host: Annotated[
        str,
        cyclopts.Parameter(help="Device hostname or IP"),
    ] = settings.device_host,
    user: Annotated[
        str,
        cyclopts.Parameter(help="SSH username"),
    ] = settings.device_user,
    password: Annotated[
        str | None,
        cyclopts.Parameter(help="SSH password"),
    ] = None,
) -> None:
    """Upload a file to the device.

    Supports PDF, EPUB, Markdown (.md), Mermaid (.mmd), and plain text
    (.txt). Non-native types are rendered to PDF before uploading.

    Examples
    --------
    Upload a PDF::

        rmspec device push report.pdf

    Upload to a folder by name::

        rmspec device push notes.md --folder "Projects"

    Upload with a custom display name::

        rmspec device push document.pdf --name "Monthly Report"
    """
    # Delegate to sync push — single implementation for all push logic
    from remarkable_spec.cli.sync_cmd import push as sync_push

    sync_push(
        file=file,
        name=name,
        folder=folder,
        parent=parent,
        host=host,
        user=user,
        password=password,
    )
