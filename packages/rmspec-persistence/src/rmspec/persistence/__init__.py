"""SQLite repository adapters. The only package permitted to import sqlite3.

What this package binds
-----------------------
Four adapters, one per persistence port, plus the two null cache bindings that
make ``--no-cache`` a wiring decision instead of an ``if`` inside a use case, plus
a maintenance class the CLI constructs directly because retention and "why did
this miss" are not use cases.

Plus one reader that is not a persistence port at all:
:class:`~rmspec.persistence.search_index.DeviceSearchIndex` binds
``rmspec.domain.ports.ocr.HandwrittenTextIndex``. It lives here because the
tablet's own search index is a SQLite image and ``sqlite3`` may be imported
nowhere else; the transport that fetches those bytes is ``rmspec-device``'s half
of the same capability, and neither package may hold both.

Nothing here appears in an application-layer signature. The composition root in
``rmspec-cli`` is the only other package allowed to name these classes; everything
above depends on the Protocols in ``rmspec.domain.ports.persistence``.

What it fixed
-------------
Three defects, each named in the design review and each closed structurally
rather than by convention.

The SQL lived in string literals hand-mirrored into pydantic models with nothing
testing that the two agreed. Now a row is one JSON payload produced by the model
itself, plus only the columns a declared ordering or an indexed lookup needs. Each
of those derived columns is written by exactly one named function in
:mod:`rmspec.persistence.derived`, and the schema-agreement test re-derives every
one of them from its own row's payload.

``init_schema`` read ``schema_version`` and never compared it to
``SCHEMA_VERSION``, so the only migration was deleting the file. Now
``PRAGMA user_version`` is compared on every open, a newer file raises
``StoreSchemaMismatchError`` carrying both numbers, and
:mod:`rmspec.persistence.payload_schema` extends the same gate to the JSON payload,
which ``user_version`` alone cannot see.

Three tables carried an ``AUTOINCREMENT`` id no model could represent. There is
none anywhere now: the caches are keyed by the digest that already identifies
them, and the audit log's sequence is a modelled field allocated from an explicit
counter row -- ``sqlite_sequence`` is resettable, so ``AUTOINCREMENT`` cannot
underwrite the port's promise that a sequence is never reused.

Not exported
------------
The in-memory doubles. They live at ``rmspec.persistence.testing`` and are
imported explicitly by tests, never by the composition root: a double bound in
production would silently discard history.
"""

from __future__ import annotations

from rmspec.persistence._sqlite import MINIMUM_SQLITE_VERSION, SqliteDatabase
from rmspec.persistence.audit_log import SqliteSyncAuditLog
from rmspec.persistence.caches import (
    NullDiagramCache,
    NullOcrCache,
    SqliteDiagramCache,
    SqliteOcrCache,
)
from rmspec.persistence.maintenance import StoreCounts, StoreMaintenance
from rmspec.persistence.migrations import SCHEMA_VERSION
from rmspec.persistence.paths import default_database_path
from rmspec.persistence.search_index import DeviceSearchIndex
from rmspec.persistence.sync_store import SqliteDocumentSyncStore

__all__ = [
    "MINIMUM_SQLITE_VERSION",
    "SCHEMA_VERSION",
    "DeviceSearchIndex",
    "NullDiagramCache",
    "NullOcrCache",
    "SqliteDatabase",
    "SqliteDiagramCache",
    "SqliteDocumentSyncStore",
    "SqliteOcrCache",
    "SqliteSyncAuditLog",
    "StoreCounts",
    "StoreMaintenance",
    "default_database_path",
]
