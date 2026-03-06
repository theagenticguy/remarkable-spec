"""Parse reMarkable ``.content`` JSON files.

Each document has a ``{UUID}.content`` file that describes page structure,
file type (notebook / PDF / EPUB), orientation, extra metadata (last-used tool
settings), and layout parameters for reflowable documents.

Example ``.content`` file (notebook)::

    {
        "fileType": "notebook",
        "formatVersion": 2,
        "orientation": "portrait",
        "pageCount": 3,
        "cPages": {
            "pages": [
                {"id": "abc-123", "template": {"value": "Lined"}},
                {"id": "def-456", "template": {"value": "Blank"}}
            ]
        },
        "extraMetadata": {
            "LastTool": "Fineliner",
            "LastPen": "Finelinerv2"
        }
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from remarkable_spec.models.document import ContentInfo

__all__ = [
    "parse_content",
    "parse_content_json",
]


def parse_content(path: Path) -> ContentInfo:
    """Parse a ``.content`` JSON file from disk.

    Parameters
    ----------
    path:
        Path to a ``{UUID}.content`` file.

    Returns
    -------
    ContentInfo
        Parsed content info with file type, page references, extra metadata, etc.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    json.JSONDecodeError
        If the file is not valid JSON.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_content_json(data)


def parse_content_json(data: dict) -> ContentInfo:
    """Parse a content dict (already loaded from JSON) into a ContentInfo.

    Parameters
    ----------
    data:
        Dictionary parsed from the ``.content`` JSON file.

    Returns
    -------
    ContentInfo
        Parsed content info object.
    """
    return ContentInfo.from_json(data)
