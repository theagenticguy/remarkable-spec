"""``rmspec search`` -- Search across reMarkable notebooks.

Two search backends:

1. **Device search** (``--device``): Uses the reMarkable's built-in
   handwriting search via the USB web interface (``POST /search/{keyword}``).
   Requires the tablet to be connected and the USB web interface enabled.

2. **Local OCR search** (default): Renders pages to PNG and runs Apple
   Vision handwriting recognition, then greps across the recognized text.
   Requires ``[ocr,render]`` extras.

Examples
--------
Search via device (USB web interface)::

    rmspec search "deadlines" --device

Search locally with Apple Vision OCR::

    rmspec search "deadlines"

Search a specific notebook::

    rmspec search "deadlines" --doc "Scratch Pad"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console
from rich.table import Table

from remarkable_spec.cli._util import get_xochitl_dir, settings

app = cyclopts.App(name="search", help=__doc__)
console = Console()


@app.default
def search(
    query: Annotated[
        str,
        cyclopts.Parameter(help="Text to search for in handwritten notes"),
    ],
    *,
    device: Annotated[
        bool,
        cyclopts.Parameter(help="Search via device USB web interface instead of local OCR"),
    ] = False,
    doc: Annotated[
        str | None,
        cyclopts.Parameter(help="Limit search to a specific document name"),
    ] = None,
    xochitl: Annotated[
        Path | None,
        cyclopts.Parameter(help="Path to xochitl directory (defaults to RMSPEC_XOCHITL)"),
    ] = None,
    json_output: Annotated[
        bool,
        cyclopts.Parameter(name="--json", help="Output as JSON"),
    ] = False,
) -> None:
    """Search for text across reMarkable notebooks."""
    if device:
        _search_device(query, json_output)
    else:
        _search_local(query, doc, xochitl, json_output)


def _search_device(query: str, json_output: bool) -> None:
    """Search via the reMarkable USB web interface."""
    try:
        import httpx
    except ImportError:
        console.print(
            "[red]Error:[/red] Device search requires httpx. "
            "Install with: uv add 'remarkable-spec[device]'"
        )
        sys.exit(1)

    host = settings.device_host
    url = f"http://{host}/search/{query}"

    try:
        resp = httpx.post(url, timeout=10.0)
        resp.raise_for_status()
    except httpx.ConnectError:
        console.print(
            f"[red]Error:[/red] Cannot connect to device at {host}.\n"
            "Make sure USB is connected and web interface is enabled "
            "(Settings → Storage → USB web interface)."
        )
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error:[/red] Device returned {e.response.status_code}")
        sys.exit(1)

    results = resp.json()
    if not results:
        console.print(f"No results for '{query}' on device.")
        return

    if json_output:
        console.print_json(json.dumps(results))
        return

    table = Table(title=f"Device search: '{query}'")
    table.add_column("Document", style="bold")
    table.add_column("Type")
    table.add_column("ID", style="dim")

    for item in results:
        table.add_row(
            item.get("VissibleName", item.get("visibleName", "?")),
            item.get("Type", "?"),
            item.get("ID", "?")[:12] + "...",
        )

    console.print(table)
    console.print(f"\n{len(results)} result(s)")


def _search_local(
    query: str,
    doc_filter: str | None,
    xochitl: Path | None,
    json_output: bool,
) -> None:
    """Search locally using Apple Vision OCR."""
    from remarkable_spec.ocr.vision import ocr_page

    xochitl_dir = get_xochitl_dir(xochitl)
    if xochitl_dir is None:
        console.print(
            "[red]Error:[/red] No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl."
        )
        sys.exit(1)

    import json as _json

    # Collect documents to search
    docs: list[tuple[str, str, list[Path]]] = []
    for meta_path in sorted(xochitl_dir.glob("*.metadata")):
        try:
            meta = _json.loads(meta_path.read_text())
        except Exception:
            continue

        if meta.get("type") != "DocumentType":
            continue

        name = meta.get("visibleName", "")
        if doc_filter and doc_filter.lower() not in name.lower():
            continue

        doc_uuid = meta_path.stem
        content_path = xochitl_dir / f"{doc_uuid}.content"
        if not content_path.exists():
            continue

        content = _json.loads(content_path.read_text())
        page_uuids: list[str] = []
        if "cPages" in content and "pages" in content["cPages"]:
            page_uuids = [p["id"] for p in content["cPages"]["pages"]]
        elif "pages" in content:
            page_uuids = content["pages"]

        doc_dir = xochitl_dir / doc_uuid
        rm_files = [
            doc_dir / f"{pid}.rm" for pid in page_uuids if (doc_dir / f"{pid}.rm").exists()
        ]

        if rm_files:
            docs.append((name, doc_uuid, rm_files))

    if not docs:
        console.print("No documents found to search.")
        return

    total_pages = sum(len(pages) for _, _, pages in docs)
    console.print(f"Searching {len(docs)} document(s), {total_pages} page(s) for '{query}'...")

    # OCR and search
    hits: list[dict] = []
    page_count = 0
    for doc_name, doc_uuid, rm_files in docs:
        for page_num, rm_path in enumerate(rm_files, 1):
            page_count += 1
            # Check for cached OCR
            cache_path = rm_path.with_suffix(".ocr.txt")
            if cache_path.exists():
                text = cache_path.read_text()
            else:
                console.print(
                    f"  [dim]OCR: {doc_name} p{page_num} ({page_count}/{total_pages})[/dim]"
                )
                try:
                    result = ocr_page(rm_path)
                    text = result.text
                    # Cache the result
                    cache_path.write_text(text)
                except Exception as e:
                    console.print(f"  [yellow]Skip: {doc_name} p{page_num}: {e}[/yellow]")
                    continue

            if query.lower() in text.lower():
                hits.append(
                    {
                        "document": doc_name,
                        "page": page_num,
                        "uuid": doc_uuid,
                        "text": text,
                    }
                )

    if json_output:
        console.print_json(json.dumps(hits, default=str))
        return

    if not hits:
        console.print(f"\nNo results for '{query}'.")
        return

    table = Table(title=f"Search results: '{query}'")
    table.add_column("Document", style="bold")
    table.add_column("Page", justify="right")
    table.add_column("Context")

    for hit in hits:
        # Extract context around the match
        text = hit["text"]
        idx = text.lower().find(query.lower())
        start = max(0, idx - 30)
        end = min(len(text), idx + len(query) + 30)
        context = text[start:end].replace("\n", " ")
        if start > 0:
            context = "..." + context
        if end < len(text):
            context = context + "..."

        table.add_row(hit["document"], str(hit["page"]), context)

    console.print(table)
    console.print(f"\n{len(hits)} result(s)")
