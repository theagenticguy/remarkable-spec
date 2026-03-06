"""Parse reMarkable ``.metadata`` JSON files.

Each document on the reMarkable tablet has a ``{UUID}.metadata`` file containing
display name, type (document vs. folder), parent folder, pinned status, sync
state, and timestamps.

Example ``.metadata`` file::

    {
        "visibleName": "My Notebook",
        "type": "DocumentType",
        "parent": "",
        "deleted": false,
        "pinned": false,
        "lastModified": "1700000000000",
        "lastOpened": "1700000000000",
        "lastOpenedPage": 0,
        "version": 3,
        "synced": true
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from remarkable_spec.models.document import DocumentMetadata

__all__ = [
    "parse_metadata",
    "parse_metadata_json",
]


def parse_metadata(path: Path) -> DocumentMetadata:
    """Parse a ``.metadata`` JSON file from disk.

    Parameters
    ----------
    path:
        Path to a ``{UUID}.metadata`` file.

    Returns
    -------
    DocumentMetadata
        Parsed metadata object with document name, type, parent, etc.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    json.JSONDecodeError
        If the file is not valid JSON.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_metadata_json(data)


def parse_metadata_json(data: dict) -> DocumentMetadata:
    """Parse a metadata dict (already loaded from JSON) into a DocumentMetadata.

    Parameters
    ----------
    data:
        Dictionary parsed from the ``.metadata`` JSON file.

    Returns
    -------
    DocumentMetadata
        Parsed metadata object.
    """
    return DocumentMetadata.from_json(data)
