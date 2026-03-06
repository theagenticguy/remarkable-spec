"""Shared document name/UUID resolution for CLI commands.

All commands that accept a document name (render, ocr, diagram, search)
use this module to resolve the name to a document UUID and its .rm file
paths. Supports substring name matching and UUID/UUID-prefix matching.

When multiple documents share the same name, prefers the one with more
pages (then most recently modified) to avoid picking a stub.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HEX_PREFIX_RE = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)


def resolve_document(
    xochitl_dir: Path,
    name: str,
    console: Console,
) -> tuple[str, str, list[Path | None]] | None:
    """Resolve a document name or UUID prefix to its .rm file paths.

    Resolution order:

    1. Full UUID match (36-char hex-with-dashes)
    2. UUID prefix match (8+ hex chars against doc UUIDs)
    3. Substring match on visibleName (case-insensitive)

    On multiple matches, prefers the document with the most pages,
    then the most recently modified.

    Args:
        xochitl_dir: Path to the local xochitl data directory.
        name: Document name, UUID, or UUID prefix.
        console: Rich console for output.

    Returns:
        ``(doc_uuid, visible_name, rm_files)`` or ``None`` if not found.
        ``rm_files`` is ordered by page index.
    """
    candidates: list[_DocInfo] = []

    for meta_path in xochitl_dir.glob("*.metadata"):
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue

        if meta.get("type") == "CollectionType":
            continue

        doc_uuid = meta_path.stem
        visible_name = meta.get("visibleName", "")
        last_mod_str = meta.get("lastModified", "0")
        try:
            last_modified = int(last_mod_str) if last_mod_str else 0
        except (ValueError, TypeError):
            last_modified = 0

        page_uuids = _get_page_uuids(xochitl_dir, doc_uuid)

        candidates.append(
            _DocInfo(
                doc_uuid=doc_uuid,
                visible_name=visible_name,
                last_modified=last_modified,
                page_uuids=page_uuids,
            )
        )

    # 1. Full UUID match
    if _UUID_RE.match(name):
        matches = [c for c in candidates if c.doc_uuid == name]
        if matches:
            return _pick_best(matches, name, xochitl_dir, console)

    # 2. UUID prefix match (8+ hex chars)
    if _HEX_PREFIX_RE.match(name):
        prefix = name.lower()
        matches = [c for c in candidates if c.doc_uuid.lower().startswith(prefix)]
        if matches:
            return _pick_best(matches, name, xochitl_dir, console)

    # 3. Substring match on visibleName
    matches = [c for c in candidates if name.lower() in c.visible_name.lower()]

    if not matches:
        console.print(f"[red]Error:[/red] No document matching '{name}'.")
        return None

    return _pick_best(matches, name, xochitl_dir, console)


class _DocInfo:
    __slots__ = ("doc_uuid", "last_modified", "page_uuids", "visible_name")

    def __init__(
        self,
        doc_uuid: str,
        visible_name: str,
        last_modified: int,
        page_uuids: list[str],
    ) -> None:
        self.doc_uuid = doc_uuid
        self.visible_name = visible_name
        self.last_modified = last_modified
        self.page_uuids = page_uuids


def _pick_best(
    matches: list[_DocInfo],
    name: str,
    xochitl_dir: Path,
    console: Console,
) -> tuple[str, str, list[Path | None]] | None:
    """Pick the best match from a list and return (uuid, name, rm_files).

    For PDF-backed documents, ``rm_files`` includes ``None`` entries for
    pages without annotations so that page indices map 1:1 to PDF pages.
    """
    if len(matches) > 1:
        # Sort: most pages first, then most recently modified
        matches.sort(key=lambda c: (len(c.page_uuids), c.last_modified), reverse=True)
        console.print(f"[yellow]Multiple matches for '{name}':[/yellow]")
        for c in matches:
            console.print(f"  {c.visible_name} ({c.doc_uuid[:8]}...) — {len(c.page_uuids)} pages")
        console.print("Using best match (most pages).")

    best = matches[0]
    console.print(f"Found: [bold]{best.visible_name}[/bold]")

    doc_dir = xochitl_dir / best.doc_uuid
    rm_files: list[Path | None] = []
    annotated = 0
    for page_uuid in best.page_uuids:
        rm = doc_dir / f"{page_uuid}.rm"
        if rm.exists():
            rm_files.append(rm)
            annotated += 1
        else:
            rm_files.append(None)

    console.print(f"  {len(rm_files)} page(s)")
    return (best.doc_uuid, best.visible_name, rm_files)


def _get_page_uuids(xochitl_dir: Path, doc_uuid: str) -> list[str]:
    """Read .content to get ordered page UUIDs."""
    content_path = xochitl_dir / f"{doc_uuid}.content"
    if not content_path.exists():
        return []

    try:
        content = json.loads(content_path.read_text())
    except Exception:
        return []

    if "cPages" in content and "pages" in content["cPages"]:
        return [p["id"] for p in content["cPages"]["pages"]]
    if "pages" in content:
        return content["pages"]
    return []


def _get_redir_map(xochitl_dir: Path, doc_uuid: str) -> dict[str, int]:
    """Map page UUID to its PDF page index from the ``redir`` field in ``.content``.

    The CRDT-format ``.content`` file stores a ``redir`` value for each page
    that maps it to the corresponding page index in the backing PDF.  Without
    this mapping, stroke overlays are composited onto the wrong PDF page.
    """
    content = _parse_content_file(xochitl_dir, doc_uuid)
    if "cPages" not in content or "pages" not in content["cPages"]:
        return {}
    result: dict[str, int] = {}
    for i, page in enumerate(content["cPages"]["pages"]):
        page_id = page.get("id", "")
        redir = page.get("redir", {})
        pdf_idx = redir.get("value") if isinstance(redir, dict) else redir
        if pdf_idx is not None and isinstance(pdf_idx, int):
            result[page_id] = pdf_idx
        else:
            # Fallback: page order in .content matches PDF order
            result[page_id] = i
    return result


def _parse_content_file(xochitl_dir: Path, doc_uuid: str) -> dict:
    """Parse a document's .content JSON file."""
    content_path = xochitl_dir / f"{doc_uuid}.content"
    if not content_path.exists():
        return {}
    try:
        return json.loads(content_path.read_text())
    except Exception:
        return {}


@dataclass
class ResolvedDocument:
    """Full resolution result for a document, including PDF metadata.

    Attributes:
        doc_uuid: The document's UUID.
        visible_name: Human-readable document name.
        rm_files: Ordered list of ``.rm`` file paths (one per page).  Entries
            are ``None`` for pages that have no annotation data on disk.
        file_type: Document type — ``"notebook"``, ``"pdf"``, or ``"epub"``.
        pdf_path: Path to the backing PDF file, or ``None`` for notebooks.
        page_indices: PDF page index (0-based) corresponding to each entry in
            ``rm_files``.  Built from the ``redir`` field in ``.content`` so
            that stroke overlays are composited onto the correct PDF page.
    """

    doc_uuid: str
    visible_name: str
    rm_files: list[Path | None] = field(default_factory=list)
    file_type: str = "notebook"
    pdf_path: Path | None = None
    page_indices: list[int] = field(default_factory=list)


def resolve_document_full(
    xochitl_dir: Path,
    name: str,
    console: Console,
) -> ResolvedDocument | None:
    """Resolve a document name/UUID to full metadata including PDF path.

    Extends :func:`resolve_document` with ``file_type``, ``pdf_path``,
    and ``page_indices`` parsed from the ``.content`` file.

    Args:
        xochitl_dir: Path to the local xochitl data directory.
        name: Document name, UUID, or UUID prefix.
        console: Rich console for output.

    Returns:
        A :class:`ResolvedDocument` or ``None`` if not found.
    """
    result = resolve_document(xochitl_dir, name, console)
    if result is None:
        return None

    doc_uuid, visible_name, rm_files = result

    content = _parse_content_file(xochitl_dir, doc_uuid)
    file_type = content.get("fileType", "notebook")

    # Determine PDF path
    pdf_path: Path | None = None
    if file_type == "pdf":
        candidate = xochitl_dir / f"{doc_uuid}.pdf"
        if candidate.exists():
            pdf_path = candidate

    # Build page indices from the redir mapping in .content so that
    # stroke overlays land on the correct PDF page.
    redir_map = _get_redir_map(xochitl_dir, doc_uuid)
    page_indices: list[int] = []
    for i, rm_path in enumerate(rm_files):
        if rm_path is not None:
            page_indices.append(redir_map.get(rm_path.stem, i))
        else:
            # Unannotated page — use redir from .content page list
            page_uuids = _get_page_uuids(xochitl_dir, doc_uuid)
            if i < len(page_uuids):
                page_indices.append(redir_map.get(page_uuids[i], i))
            else:
                page_indices.append(i)

    return ResolvedDocument(
        doc_uuid=doc_uuid,
        visible_name=visible_name,
        rm_files=rm_files,
        file_type=file_type,
        pdf_path=pdf_path,
        page_indices=page_indices,
    )
