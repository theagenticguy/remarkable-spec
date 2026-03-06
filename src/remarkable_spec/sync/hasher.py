"""SHA-256 file hashing for sync change detection.

The ``rm_hash`` (SHA-256 of a ``.rm`` binary file) is the primary cache
invalidation key — when a user edits a page on the device, the ``.rm``
file changes, the hash changes, and all derived caches (OCR, diagrams)
become stale.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents.

    Reads in 64 KB chunks to handle large files without excessive memory use.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def hash_document_files(xochitl_dir: Path, doc_uuid: str) -> dict[str, str | dict[str, str]]:
    """Hash all relevant files for a document.

    Returns a dict with structure::

        {
            "metadata": "abc123...",       # SHA-256 of .metadata
            "content": "def456...",        # SHA-256 of .content
            "pages": {
                "page-uuid-1": "ghi789...",  # SHA-256 of each .rm file
                "page-uuid-2": "jkl012...",
            }
        }

    Missing files are omitted from the result.
    """
    result: dict[str, str | dict[str, str]] = {}

    meta_path = xochitl_dir / f"{doc_uuid}.metadata"
    if meta_path.exists():
        result["metadata"] = hash_file(meta_path)

    content_path = xochitl_dir / f"{doc_uuid}.content"
    if content_path.exists():
        result["content"] = hash_file(content_path)

    doc_dir = xochitl_dir / doc_uuid
    page_hashes: dict[str, str] = {}
    if doc_dir.is_dir():
        for rm_file in sorted(doc_dir.glob("*.rm")):
            page_uuid = rm_file.stem
            page_hashes[page_uuid] = hash_file(rm_file)

    if page_hashes:
        result["pages"] = page_hashes

    return result
