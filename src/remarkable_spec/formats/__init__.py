"""Format parsers for reMarkable tablet file types.

This package provides parsers for all file types in the reMarkable xochitl
filesystem:

- ``.rm`` binary files (v6 format) -- parsed via :mod:`rmscene`
- ``.metadata`` JSON files -- document display name, type, parent
- ``.content`` JSON files -- page list, file type, tool settings
- ``.pagedata`` text files -- per-page template names
- Full document loading -- combines all of the above into a :class:`Document`
"""

from __future__ import annotations

from remarkable_spec.formats.content import parse_content, parse_content_json
from remarkable_spec.formats.document_loader import load_document
from remarkable_spec.formats.metadata import parse_metadata, parse_metadata_json
from remarkable_spec.formats.pagedata import parse_pagedata
from remarkable_spec.formats.rm_file import parse_rm_bytes, parse_rm_file

__all__ = [
    # .rm binary
    "parse_rm_file",
    "parse_rm_bytes",
    # .metadata JSON
    "parse_metadata",
    "parse_metadata_json",
    # .content JSON
    "parse_content",
    "parse_content_json",
    # .pagedata text
    "parse_pagedata",
    # Full document
    "load_document",
]
