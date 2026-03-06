"""Sync state database and change tracking for reMarkable documents.

Provides a local SQLite database for tracking sync state, caching OCR
and diagram extraction results, and enabling incremental two-way sync
between the device and local filesystem.

The database is stored at ``~/.remarkable-spec/sync.db`` by default
(configurable via ``RMSPEC_SYNC_DB`` environment variable).
"""

from __future__ import annotations

from remarkable_spec.sync.db import SyncDB
from remarkable_spec.sync.models import (
    DiagramCacheEntry,
    OCRCacheEntry,
    SyncDocument,
    SyncLogEntry,
    SyncPage,
)

__all__ = [
    "DiagramCacheEntry",
    "OCRCacheEntry",
    "SyncDB",
    "SyncDocument",
    "SyncLogEntry",
    "SyncPage",
]
