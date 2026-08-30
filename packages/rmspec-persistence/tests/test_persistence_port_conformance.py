"""Every binding satisfies its *named* port, checked by the type gate.

None of the four ports is ``runtime_checkable`` and nothing in the suite calls
``isinstance`` on one, which is the right choice -- the contract tests instantiate
each implementation and call it. But it leaves a hole: nothing would notice a
renamed method or a positional parameter that stopped being positional. The
functions below close it by returning each binding through a signature annotated
with the Protocol, so ``ty`` verifies assignability. They are called at runtime too,
which is also where the two null bindings get exercised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persistence_builders import a_diagram_artifact, a_diagram_key, an_ocr_artifact, an_ocr_key

from rmspec.persistence import (
    NullDiagramCache,
    NullOcrCache,
    SqliteDiagramCache,
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    SqliteSyncAuditLog,
)
from rmspec.persistence.testing import (
    InMemoryDiagramCache,
    InMemoryDocumentSyncStore,
    InMemoryOcrCache,
    InMemorySyncAuditLog,
)

if TYPE_CHECKING:
    from rmspec.domain.ports.persistence import (
        DiagramCache,
        DocumentSyncStore,
        OcrCache,
        SyncAuditLog,
    )
    from rmspec.persistence import SqliteDatabase


def document_sync_stores(database: SqliteDatabase) -> tuple[DocumentSyncStore, DocumentSyncStore]:
    """Return every ``DocumentSyncStore`` binding.

    Parameters
    ----------
    database
        An open database handle.

    Returns
    -------
    tuple[DocumentSyncStore, DocumentSyncStore]
        The SQLite adapter and the in-memory double. The annotation is the check.
    """
    return (SqliteDocumentSyncStore(database), InMemoryDocumentSyncStore())


def ocr_caches(database: SqliteDatabase) -> tuple[OcrCache, OcrCache, OcrCache]:
    """Return every ``OcrCache`` binding.

    Parameters
    ----------
    database
        An open database handle.

    Returns
    -------
    tuple[OcrCache, OcrCache, OcrCache]
        The SQLite adapter, the null binding and the in-memory double.
    """
    return (SqliteOcrCache(database), NullOcrCache(), InMemoryOcrCache())


def diagram_caches(database: SqliteDatabase) -> tuple[DiagramCache, DiagramCache, DiagramCache]:
    """Return every ``DiagramCache`` binding.

    Parameters
    ----------
    database
        An open database handle.

    Returns
    -------
    tuple[DiagramCache, DiagramCache, DiagramCache]
        The SQLite adapter, the null binding and the in-memory double.
    """
    return (SqliteDiagramCache(database), NullDiagramCache(), InMemoryDiagramCache())


def sync_audit_logs(database: SqliteDatabase) -> tuple[SyncAuditLog, SyncAuditLog]:
    """Return every ``SyncAuditLog`` binding.

    Parameters
    ----------
    database
        An open database handle.

    Returns
    -------
    tuple[SyncAuditLog, SyncAuditLog]
        The SQLite adapter and the in-memory double.
    """
    return (SqliteSyncAuditLog(database), InMemorySyncAuditLog())


def test_every_binding_is_constructible_and_typed_as_its_port(tmp_db: SqliteDatabase) -> None:
    assert len(document_sync_stores(tmp_db)) == 2
    assert len(ocr_caches(tmp_db)) == 3
    assert len(diagram_caches(tmp_db)) == 3
    assert len(sync_audit_logs(tmp_db)) == 2


def test_the_null_ocr_cache_misses_everything_and_stores_nothing() -> None:
    # What `--no-cache` binds. A wiring decision rather than an `if` inside a use
    # case, which is why it has to satisfy the same port.
    cache = NullOcrCache()
    key = an_ocr_key()
    cache.put(key, an_ocr_artifact())
    assert cache.get(key) is None
    assert cache.superseded(key) is None
    # The fourth method too: a fallback that fired under `--no-cache` would make the
    # flag mean something other than "this run pays".
    assert cache.equivalent_raster(key) is None


def test_the_null_diagram_cache_misses_everything_and_stores_nothing() -> None:
    cache = NullDiagramCache()
    key = a_diagram_key()
    cache.put(key, a_diagram_artifact())
    assert cache.get(key) is None
    assert cache.superseded(key) is None
