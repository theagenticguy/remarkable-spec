"""In-memory doubles for the persistence ports, shipped rather than vendored.

Every later application-layer test binds these, and the port docstrings promise
them at this import path. They live under ``src/`` on purpose: the architecture
suite checks import direction over ``src/`` only, so an application *test* may
depend on ``rmspec.persistence.testing`` while application *source* stays
domain-only. They ship in the wheel and are held to the same coverage gate as the
adapters, which is what keeps the double honest about the contract it claims to
satisfy.

Nothing here opens a file, imports ``sqlite3``, or reaches a network.
"""

from __future__ import annotations

from rmspec.persistence.testing.doubles import (
    IN_MEMORY_STORE,
    InMemoryDiagramCache,
    InMemoryDocumentSyncStore,
    InMemoryOcrCache,
    InMemorySyncAuditLog,
    SeededRecordKind,
)

__all__ = [
    "IN_MEMORY_STORE",
    "InMemoryDiagramCache",
    "InMemoryDocumentSyncStore",
    "InMemoryOcrCache",
    "InMemorySyncAuditLog",
    "SeededRecordKind",
]
