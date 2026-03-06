"""``rmspec sync`` -- Two-way sync between reMarkable and local filesystem.

Provides incremental sync with change detection via a local SQLite database.
Only transfers documents whose content has changed since the last sync.

Requires the ``[device]`` extra::

    uv add 'remarkable-spec[device]'

Examples
--------
Show what's changed since last sync::

    rmspec sync status

Pull only changed documents::

    rmspec sync pull

Push a file to the device::

    rmspec sync push report.pdf

View sync history::

    rmspec sync log
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console
from rich.table import Table

from remarkable_spec.cli._util import get_sync_db, get_xochitl_dir, settings

app = cyclopts.App(name="sync", help=__doc__)
console = Console()

_KEY_PATH = Path("~/.ssh/id_ed25519_remarkable").expanduser()


@app.default
def _default(
    *,
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
    xochitl: Annotated[
        Path | None,
        cyclopts.Parameter(help="Path to local xochitl directory"),
    ] = None,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output as JSON"),
    ] = False,
) -> None:
    """Show sync status (default when no subcommand given)."""
    status(
        host=host,
        user=user,
        password=password,
        xochitl=xochitl,
        json_output=json_output,
    )


def _get_connection(host: str, user: str, password: str | None):
    """Create a DeviceConnection with sensible defaults."""
    try:
        from remarkable_spec.device.connection import DeviceConnection
    except ImportError:
        console.print(
            "[red]Error:[/red] Device dependencies not installed.\n"
            "Install with: [bold]uv add 'remarkable-spec[device]'[/bold]"
        )
        sys.exit(1)

    pw = password or settings.device_password
    key = _KEY_PATH if _KEY_PATH.exists() else None
    return DeviceConnection(host=host, user=user, password=pw, key_path=key)


@app.command
def status(
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
    xochitl: Annotated[
        Path | None,
        cyclopts.Parameter(help="Path to local xochitl directory"),
    ] = None,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output as JSON"),
    ] = False,
) -> None:
    """Show what's changed on the device since last sync."""
    from remarkable_spec.device.sync import SyncManager

    xochitl_dir = get_xochitl_dir(xochitl)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl."
        )
        sys.exit(1)

    db = get_sync_db()
    sync = SyncManager()

    try:
        with _get_connection(host, user, password) as conn:
            changes = sync.sync_status(conn, db, xochitl_dir)
    except ConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if json_output:
        import json

        data = [{"uuid": uid, "name": name, "change": change} for uid, name, change in changes]
        console.print_json(json.dumps(data))
        return

    if not changes:
        console.print("[green]Everything is in sync.[/green]")
        return

    table = Table(title="Sync Status")
    table.add_column("Document", style="bold")
    table.add_column("Change")
    table.add_column("UUID", style="dim")

    style_map = {
        "new_on_device": "[green]New[/green]",
        "modified_on_device": "[yellow]Modified[/yellow]",
        "deleted_on_device": "[red]Deleted[/red]",
    }

    for uid, name, change in changes:
        table.add_row(name, style_map.get(change, change), uid[:12] + "...")

    console.print(table)
    console.print(f"\n{len(changes)} change(s)")


@app.command
def pull(
    *,
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
    xochitl: Annotated[
        Path | None,
        cyclopts.Parameter(help="Path to local xochitl directory"),
    ] = None,
) -> None:
    """Pull only changed documents from the device (incremental sync)."""
    from remarkable_spec.device.sync import SyncManager

    xochitl_dir = get_xochitl_dir(xochitl)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl."
        )
        sys.exit(1)

    db = get_sync_db()
    sync = SyncManager()

    try:
        with _get_connection(host, user, password) as conn:
            console.print("[dim]Checking for changes...[/dim]")
            pulled, skipped = sync.sync_pull(xochitl_dir, conn, db)
    except ConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if not pulled and not skipped:
        console.print("[green]Already up to date.[/green]")
        return

    for _uid, name, pages in pulled:
        console.print(f"  [green]Pulled:[/green] {name} ({pages} pages)")

    for _uid, name, err in skipped:
        console.print(f"  [yellow]Skipped:[/yellow] {name}: {err}")

    summary = f"\n{len(pulled)} document(s) synced."
    if skipped:
        summary += f" {len(skipped)} skipped due to errors."
    console.print(summary)


@app.command
def push(
    file: Annotated[
        Path,
        cyclopts.Parameter(help="Path to the file to upload (PDF, EPUB, .md, .mmd, .txt)"),
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
    """Push a file to the device.

    Supports PDF, EPUB directly. Also supports Markdown (.md), Mermaid
    (.mmd), and plain text (.txt) by rendering to PDF first.

    Examples
    --------
    Push a PDF::

        rmspec sync push report.pdf

    Push with a custom name::

        rmspec sync push document.pdf --name "Monthly Report"

    Push Markdown (rendered to PDF)::

        rmspec sync push notes.md
    """
    from remarkable_spec.device.sync import SyncManager

    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        sys.exit(1)

    # Resolve --folder name to UUID
    target_parent = parent
    if folder:
        try:
            from remarkable_spec.device.web_api import WebAPI

            api = WebAPI(base_url=f"http://{host}")
            docs = api.list_all_documents()
            folders = [
                d
                for d in docs
                if d.get("Type") == "CollectionType"
                and folder.lower() in d.get("VissibleName", d.get("VisibleName", "")).lower()
            ]
            if not folders:
                console.print(f"[red]Error:[/red] No folder matching '{folder}' on device.")
                sys.exit(1)
            if len(folders) > 1:
                console.print(f"[yellow]Multiple folders match '{folder}':[/yellow]")
                for f in folders:
                    fname = f.get("VissibleName", f.get("VisibleName", "?"))
                    console.print(f"  {fname} ({f['ID'][:8]}...)")
                console.print("Using first match.")
            target_parent = folders[0]["ID"]
            fname = folders[0].get("VissibleName", folders[0].get("VisibleName", "?"))
            console.print(f"[dim]Target folder: {fname}[/dim]")
        except ImportError:
            console.print(
                "[red]Error:[/red] Folder lookup requires httpx.\n"
                "Install with: [bold]uv add 'remarkable-spec[device]'[/bold]"
            )
            sys.exit(1)

    suffix = file.suffix.lower()
    native_types = {".pdf", ".epub"}
    renderable_types = {".md", ".mmd", ".txt"}

    if suffix not in native_types | renderable_types:
        console.print(f"[red]Error:[/red] Unsupported file type: {suffix}")
        console.print("Supported: .pdf, .epub, .md, .mmd, .txt")
        sys.exit(1)

    # Render non-native types to PDF first
    push_path = file
    if suffix in renderable_types:
        try:
            from remarkable_spec.device.push import render_to_pdf
        except ImportError:
            console.print(
                "[red]Error:[/red] Content rendering requires the [push] extra.\n"
                "Install with: [bold]uv add 'remarkable-spec[push]'[/bold]"
            )
            sys.exit(1)

        console.print(f"[dim]Rendering {file.name} to PDF...[/dim]")
        push_path = render_to_pdf(file)

    db = get_sync_db()
    sync = SyncManager()

    try:
        with _get_connection(host, user, password) as conn:
            console.print(f"[dim]Uploading {push_path.name} to device...[/dim]")
            doc_uuid = sync.sync_push_file(
                push_path, conn, db, name=name or file.stem, parent=target_parent
            )
    except ConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]Pushed:[/green] {name or file.stem} ({doc_uuid[:8]}...)")

    # Cache rendered PDF for later use (e.g. compositing annotations)
    if push_path != file:
        import shutil

        cache_dir = Path("~/.remarkable-spec/cache").expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{name or file.stem}.pdf"
        shutil.move(str(push_path), str(cached))
        console.print(f"[dim]Cached PDF: {cached}[/dim]")


@app.command
def log(
    *,
    limit: Annotated[
        int,
        cyclopts.Parameter(help="Maximum number of log entries to show"),
    ] = 20,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output as JSON"),
    ] = False,
) -> None:
    """Show sync history."""
    db = get_sync_db()
    entries = db.get_sync_log(limit=limit)

    if json_output:
        import json

        data = [
            {
                "direction": e.direction,
                "document": e.doc_name,
                "uuid": e.doc_uuid,
                "pages": e.pages_transferred,
                "status": e.status,
                "details": e.details,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in entries
        ]
        console.print_json(json.dumps(data))
        return

    if not entries:
        console.print("No sync history yet.")
        return

    table = Table(title="Sync Log")
    table.add_column("Time", style="dim")
    table.add_column("Dir")
    table.add_column("Document", style="bold")
    table.add_column("Pages", justify="right")
    table.add_column("Status")

    for entry in entries:
        direction_icon = "[green]↓[/green]" if entry.direction == "pull" else "[blue]↑[/blue]"
        status_style = (
            "[green]ok[/green]" if entry.status == "ok" else f"[red]{entry.status}[/red]"
        )
        table.add_row(
            entry.timestamp.strftime("%Y-%m-%d %H:%M"),
            direction_icon,
            entry.doc_name,
            str(entry.pages_transferred),
            status_style,
        )

    console.print(table)
