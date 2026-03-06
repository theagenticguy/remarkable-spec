"""Load a complete reMarkable document from the xochitl filesystem.

The xochitl directory (typically ``/home/root/.local/share/remarkable/xochitl/``
on the device) contains all document files.  A single document ``DOC_UUID``
consists of:

- ``DOC_UUID.metadata``  -- JSON with display name, type, parent
- ``DOC_UUID.content``   -- JSON with page list, file type, tool settings
- ``DOC_UUID.pagedata``  -- Text file with template names per page
- ``DOC_UUID/``          -- Directory containing per-page ``.rm`` files
- ``DOC_UUID.thumbnails/`` -- Directory with per-page JPEG thumbnails

This module combines all of these into a single :class:`Document` object.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from remarkable_spec.formats.content import parse_content
from remarkable_spec.formats.metadata import parse_metadata
from remarkable_spec.formats.pagedata import parse_pagedata
from remarkable_spec.formats.rm_file import parse_rm_file
from remarkable_spec.models.document import Document
from remarkable_spec.models.page import Page

logger = logging.getLogger(__name__)

__all__ = [
    "load_document",
]


def load_document(xochitl_dir: Path, doc_uuid: str) -> Document:
    """Load a complete document from the xochitl directory.

    Parses the metadata, content, pagedata, and all per-page ``.rm`` files
    for the given document UUID and assembles them into a :class:`Document`.

    Parameters
    ----------
    xochitl_dir:
        Root xochitl directory path (e.g. ``/home/root/.local/share/remarkable/xochitl``).
    doc_uuid:
        UUID string of the document to load (e.g. ``"a1b2c3d4-..."-``).

    Returns
    -------
    Document
        Fully populated document with metadata, content info, and parsed pages.

    Raises
    ------
    FileNotFoundError
        If the metadata or content file does not exist.
    """
    base = xochitl_dir / doc_uuid

    # --- Metadata (required) ---
    metadata_path = base.with_suffix(".metadata")
    metadata = parse_metadata(metadata_path)

    # --- Content (required) ---
    content_path = base.with_suffix(".content")
    content = parse_content(content_path)

    # --- Pagedata (optional) ---
    pagedata_path = base.with_suffix(".pagedata")
    templates: list[str] = []
    if pagedata_path.exists():
        templates = parse_pagedata(pagedata_path)

    # --- Pages ---
    pages: list[Page] = []
    page_dir = Path(base)  # directory named {UUID}/ containing .rm files

    for idx, page_ref in enumerate(content.page_refs):
        page_uuid = page_ref.uuid
        rm_path = page_dir / f"{page_uuid}.rm"

        # Determine template name for this page
        template_name = ""
        if idx < len(templates):
            template_name = templates[idx]
        elif page_ref.template:
            template_name = page_ref.template

        # Parse the .rm file if it exists
        layers = []
        if rm_path.exists():
            try:
                layers = parse_rm_file(rm_path)
            except Exception:
                logger.warning(
                    "Failed to parse .rm file for page %s of document %s",
                    page_uuid,
                    doc_uuid,
                    exc_info=True,
                )

        pages.append(
            Page(
                uuid=page_uuid,
                layers=layers,
                template_name=template_name,
            )
        )

    return Document(
        uuid=UUID(doc_uuid),
        metadata=metadata,
        content=content,
        pages=pages,
        templates=templates,
    )
