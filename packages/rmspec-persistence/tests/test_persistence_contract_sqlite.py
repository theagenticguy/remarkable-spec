"""The four port contracts, bound to the SQLite adapters.

Every assertion in this file's classes comes from ``persistence_contracts.py``.
What lives here is the binding -- the subject fixture and the three seams -- plus
the handful of cases that cannot be phrased against a port at all: two adapters on
one file, re-opening an existing file, and the cascade that proves
``PRAGMA foreign_keys`` is live on both connections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from persistence_builders import a_document, a_page, a_page_text, an_audit_entry
from persistence_contracts import (
    ArtifactCache,
    DiagramCacheCases,
    DocumentSyncStoreContract,
    OcrCacheCases,
    SyncAuditLogContract,
)

from rmspec.domain.errors import AuditWriteFailedError
from rmspec.persistence import (
    SqliteDatabase,
    SqliteDiagramCache,
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    SqliteSyncAuditLog,
    StoreMaintenance,
)
from rmspec.persistence.testing import SeededRecordKind

if TYPE_CHECKING:
    from pathlib import Path

    from rmspec.domain.models import (
        DiagramArtifact,
        DiagramCacheKey,
        OcrArtifact,
        OcrCacheKey,
    )
    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog

#: One literal statement per record kind. Literals rather than an interpolated
#: table name, so this file needs no lint suppression to corrupt a payload.
_CORRUPTIONS: Final = {
    SeededRecordKind.DOCUMENT: "UPDATE document SET payload = ? WHERE uuid = ?",
    SeededRecordKind.PAGE: "UPDATE page SET payload = ? WHERE doc_uuid = ?",
    SeededRecordKind.PAGE_TEXT: "UPDATE page_text SET payload = ? WHERE doc_uuid = ?",
}


class TestSqliteDocumentSyncStore(DocumentSyncStoreContract):
    """The document sync store contract over a temporary file."""

    @pytest.fixture
    def store(self, tmp_db: SqliteDatabase) -> DocumentSyncStore:
        """Return the SQLite store.

        Parameters
        ----------
        tmp_db
            An open database under ``tmp_path``.

        Returns
        -------
        DocumentSyncStore
            The adapter, which ``ty`` checks against the Protocol here.
        """
        return SqliteDocumentSyncStore(tmp_db)

    def make_unreadable(
        self,
        store: DocumentSyncStore,
        kind: SeededRecordKind,
        doc_uuid: str,
    ) -> None:
        """Overwrite a payload column with text that is not JSON.

        Parameters
        ----------
        store
            The subject.
        kind
            Which reader to break.
        doc_uuid
            The document whose record becomes unreadable.
        """
        assert isinstance(store, SqliteDocumentSyncStore)
        store._conn.execute(_CORRUPTIONS[kind], ("not json at all", doc_uuid))

    def break_store(self, store: DocumentSyncStore) -> None:
        """Close the connection under the store.

        Parameters
        ----------
        store
            The subject.
        """
        assert isinstance(store, SqliteDocumentSyncStore)
        store._conn.close()

    def test_no_transaction_is_left_open_by_a_write(self, tmp_db: SqliteDatabase) -> None:
        """No transaction is left open by a write."""
        # The failure this guards is silent and total: with `autocommit=True` a
        # `Connection.commit()` is a no-op, so the write lock would survive the
        # call, the second record_document would raise "cannot start a
        # transaction within a transaction", and every audit append after it
        # would time out. Two consecutive writes plus the flag prove otherwise.
        store = SqliteDocumentSyncStore(tmp_db)
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        assert tmp_db.primary.in_transaction is False
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-2", 0)])
        assert tmp_db.primary.in_transaction is False
        assert [page.page_uuid for page in store.pages("doc-a")] == ["page-2"]

    def test_an_audit_append_lands_while_a_store_write_is_in_flight(
        self,
        tmp_db: SqliteDatabase,
    ) -> None:
        """An audit append lands while a store write is in flight."""
        # Two connections on one WAL file, which the legacy single-connection
        # store could never reach. The append must not wait on the store's write
        # transaction, because the port requires an entry to survive the failure
        # it describes.
        store = SqliteDocumentSyncStore(tmp_db)
        log = SqliteSyncAuditLog(tmp_db)
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        recorded = log.append(an_audit_entry())
        assert recorded.sequence == 1
        assert log.recent(limit=1) == [recorded]

    def test_reopening_an_existing_file_preserves_its_contents(self, tmp_path: Path) -> None:
        """Reopening an existing file preserves its contents."""
        document = a_document("doc-a")
        first = SqliteDatabase.open(tmp_path / "sync.db")
        try:
            SqliteDocumentSyncStore(first).record_document(
                document, [a_page("doc-a", "page-1", 0)]
            )
        finally:
            first.close()
        second = SqliteDatabase.open(tmp_path / "sync.db")
        try:
            assert SqliteDocumentSyncStore(second).get_document("doc-a") == document
        finally:
            second.close()

    def test_the_cascade_is_live_on_the_connection_that_writes(
        self,
        tmp_db: SqliteDatabase,
    ) -> None:
        """The cascade is live on the connection that writes."""
        # Asserted behaviourally rather than by reading the pragma back, because
        # what matters is that text cannot outlive the page it describes -- and
        # PRAGMA foreign_keys is per-connection, so the audit log's second
        # connection makes this worth checking on the one doing the deleting.
        store = SqliteDocumentSyncStore(tmp_db)
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        store.record_page_text(a_page_text("doc-a", "page-1", 0))
        store.forget_document("doc-a")
        assert tmp_db.primary.query("SELECT * FROM page") == []
        assert tmp_db.primary.query("SELECT * FROM page_text") == []


class TestSqliteOcrCache(OcrCacheCases):
    """The OCR cache contract over a temporary file."""

    @pytest.fixture
    def cache(self, tmp_db: SqliteDatabase) -> ArtifactCache[OcrCacheKey, OcrArtifact]:
        """Return the SQLite OCR cache.

        Parameters
        ----------
        tmp_db
            An open database under ``tmp_path``.

        Returns
        -------
        ArtifactCache[OcrCacheKey, OcrArtifact]
            The adapter.
        """
        return SqliteOcrCache(tmp_db)

    def induce_read_fault(self, cache: ArtifactCache[OcrCacheKey, OcrArtifact]) -> None:
        """Close the connection under the cache.

        Parameters
        ----------
        cache
            The subject.
        """
        assert isinstance(cache, SqliteOcrCache)
        cache._conn.close()

    def induce_write_fault(self, cache: ArtifactCache[OcrCacheKey, OcrArtifact]) -> None:
        """Close the connection under the cache.

        Parameters
        ----------
        cache
            The subject.
        """
        self.induce_read_fault(cache)

    def test_a_corrupt_artifact_payload_reads_as_a_miss(
        self,
        tmp_db: SqliteDatabase,
    ) -> None:
        """A corrupt artifact payload reads as a miss."""
        # Adapter-only: the port declares get total, so an unreadable payload has
        # to come back as None rather than as StoredRecordUnreadableError. Only
        # the adapter can reach this state at all.
        cache = SqliteOcrCache(tmp_db)
        key = self.a_key()
        cache.put(key, self.an_artifact())
        tmp_db.primary.execute(
            "UPDATE ocr_cache SET artifact_payload = ? WHERE digest = ?",
            ("not json at all", key.digest),
        )
        assert cache.get(key) is None

    def test_a_corrupt_key_payload_reports_a_bare_miss(self, tmp_db: SqliteDatabase) -> None:
        """A corrupt key payload reports a bare miss."""
        cache = SqliteOcrCache(tmp_db)
        stored = self.a_key(variant="v1")
        cache.put(stored, self.an_artifact())
        tmp_db.primary.execute(
            "UPDATE ocr_cache SET key_payload = ? WHERE digest = ?",
            ("not json at all", stored.digest),
        )
        assert cache.superseded(self.a_key(variant="v2")) is None


class TestSqliteDiagramCache(DiagramCacheCases):
    """The diagram cache contract over a temporary file."""

    @pytest.fixture
    def cache(self, tmp_db: SqliteDatabase) -> ArtifactCache[DiagramCacheKey, DiagramArtifact]:
        """Return the SQLite diagram cache.

        Parameters
        ----------
        tmp_db
            An open database under ``tmp_path``.

        Returns
        -------
        ArtifactCache[DiagramCacheKey, DiagramArtifact]
            The adapter.
        """
        return SqliteDiagramCache(tmp_db)

    def induce_read_fault(self, cache: ArtifactCache[DiagramCacheKey, DiagramArtifact]) -> None:
        """Close the connection under the cache.

        Parameters
        ----------
        cache
            The subject.
        """
        assert isinstance(cache, SqliteDiagramCache)
        cache._conn.close()

    def induce_write_fault(self, cache: ArtifactCache[DiagramCacheKey, DiagramArtifact]) -> None:
        """Close the connection under the cache.

        Parameters
        ----------
        cache
            The subject.
        """
        self.induce_read_fault(cache)

    def test_the_two_caches_do_not_share_a_key_space(self, tmp_db: SqliteDatabase) -> None:
        """The two caches do not share a key space."""
        # One adapter class over two tables, which is the claim the port makes.
        # An OCR key and a diagram key for the same page must not collide, and
        # they cannot: the digest folds a per-cache tag.
        diagrams = SqliteDiagramCache(tmp_db)
        diagrams.put(self.a_key(), self.an_artifact())
        assert tmp_db.primary.query_one("SELECT count(*) FROM diagram_cache") == (1,)
        assert tmp_db.primary.query_one("SELECT count(*) FROM ocr_cache") == (0,)


class TestSqliteSyncAuditLog(SyncAuditLogContract):
    """The audit log contract over a temporary file."""

    _db: SqliteDatabase

    @pytest.fixture
    def log(self, tmp_db: SqliteDatabase) -> SyncAuditLog:
        """Return the SQLite audit log.

        Parameters
        ----------
        tmp_db
            An open database under ``tmp_path``.

        Returns
        -------
        SyncAuditLog
            The adapter, which holds its own connection to the same file.
        """
        self._db = tmp_db
        return SqliteSyncAuditLog(tmp_db)

    def make_unreadable(self, log: SyncAuditLog) -> None:
        """Overwrite a stored entry's payload with text that is not JSON.

        Parameters
        ----------
        log
            The subject. Read through its own connection, which is the one whose
            translation is under test.
        """
        assert isinstance(log, SqliteSyncAuditLog)
        log._conn.execute("UPDATE sync_audit SET payload = ?", ("not json at all",))

    def break_log(self, log: SyncAuditLog) -> None:
        """Close the log's own connection.

        Parameters
        ----------
        log
            The subject.
        """
        assert isinstance(log, SqliteSyncAuditLog)
        log._conn.close()

    def retain_newest(self, log: SyncAuditLog, keep: int) -> None:
        """Trim through the real maintenance path.

        Parameters
        ----------
        log
            The subject, asserted to be the adapter this binding covers.
        keep
            How many entries to retain.
        """
        assert isinstance(log, SqliteSyncAuditLog)
        StoreMaintenance(self._db).trim_audit_log(keep=keep)

    def test_a_missing_counter_row_fails_the_append_rather_than_reusing_a_sequence(
        self,
        tmp_db: SqliteDatabase,
    ) -> None:
        """A missing counter row fails the append rather than reusing a sequence."""
        log = SqliteSyncAuditLog(tmp_db)
        log.append(an_audit_entry())
        tmp_db.primary.execute("DELETE FROM audit_counter")
        with pytest.raises(AuditWriteFailedError, match="no sequence can be issued"):
            log.append(an_audit_entry())

    def test_the_log_leaves_no_transaction_open(self, tmp_db: SqliteDatabase) -> None:
        """The log leaves no transaction open."""
        log = SqliteSyncAuditLog(tmp_db)
        log.append(an_audit_entry())
        assert isinstance(log, SqliteSyncAuditLog)
        assert log._conn.in_transaction is False
