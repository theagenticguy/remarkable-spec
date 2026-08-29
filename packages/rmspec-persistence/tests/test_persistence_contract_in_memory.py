"""The four port contracts, bound to the in-memory doubles.

This file is the proof that the doubles every later application-layer test will
bind satisfy the same contract as the real adapters -- the assertions are
literally the same objects, imported from ``persistence_contracts.py``. Nothing
here reaches into an adapter or opens a file.

What is specific to the doubles: the fault and unreadable seams themselves, and
the call counters. Through a total port a swallowed fault and a genuine miss are
the same ``None``, so a counter is the only evidence that the call was attempted
rather than short-circuited by the double.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persistence_builders import an_ocr_artifact, an_ocr_key
from persistence_contracts import (
    ArtifactCache,
    DiagramCacheCases,
    DocumentSyncStoreContract,
    OcrCacheCases,
    SyncAuditLogContract,
)

from rmspec.domain.errors import StoredRecordUnreadableError, StoreUnavailableError
from rmspec.persistence.testing import (
    IN_MEMORY_STORE,
    InMemoryDiagramCache,
    InMemoryDocumentSyncStore,
    InMemoryOcrCache,
    InMemorySyncAuditLog,
    SeededRecordKind,
)

if TYPE_CHECKING:
    from rmspec.domain.models import (
        DiagramArtifact,
        DiagramCacheKey,
        OcrArtifact,
        OcrCacheKey,
    )
    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog


class TestInMemoryDocumentSyncStore(DocumentSyncStoreContract):
    """The document sync store contract over three dicts."""

    @pytest.fixture
    def store(self) -> DocumentSyncStore:
        """Return the double.

        Returns
        -------
        DocumentSyncStore
            The double, which ``ty`` checks against the Protocol here.
        """
        return InMemoryDocumentSyncStore()

    def make_unreadable(
        self,
        store: DocumentSyncStore,
        kind: SeededRecordKind,
        doc_uuid: str,
    ) -> None:
        """Seed the double's unreadable seam for one reader.

        Parameters
        ----------
        store
            The subject.
        kind
            Which reader to break.
        doc_uuid
            The document whose record becomes unreadable.
        """
        assert isinstance(store, InMemoryDocumentSyncStore)
        store.seed_unreadable(kind, doc_uuid)

    def break_store(self, store: DocumentSyncStore) -> None:
        """Set both fault flags.

        Parameters
        ----------
        store
            The subject.
        """
        assert isinstance(store, InMemoryDocumentSyncStore)
        store.fail_reads = True
        store.fail_writes = True

    def test_the_unreadable_seam_names_the_double_as_the_store(self) -> None:
        """The unreadable seam names the double as the store."""
        store = InMemoryDocumentSyncStore()
        store.seed_unreadable(SeededRecordKind.DOCUMENT, "doc-a")
        with pytest.raises(StoredRecordUnreadableError) as caught:
            store.get_document("doc-a")
        assert caught.value.store == IN_MEMORY_STORE
        assert caught.value.table == SeededRecordKind.DOCUMENT.value


class TestInMemoryOcrCache(OcrCacheCases):
    """The OCR cache contract over a dict."""

    @pytest.fixture
    def cache(self) -> ArtifactCache[OcrCacheKey, OcrArtifact]:
        """Return the double.

        Returns
        -------
        ArtifactCache[OcrCacheKey, OcrArtifact]
            The double.
        """
        return InMemoryOcrCache()

    def induce_read_fault(self, cache: ArtifactCache[OcrCacheKey, OcrArtifact]) -> None:
        """Set the read-fault flag.

        Parameters
        ----------
        cache
            The subject.
        """
        assert isinstance(cache, InMemoryOcrCache)
        cache.fail_reads = True

    def induce_write_fault(self, cache: ArtifactCache[OcrCacheKey, OcrArtifact]) -> None:
        """Set the write-fault flag.

        Parameters
        ----------
        cache
            The subject.
        """
        assert isinstance(cache, InMemoryOcrCache)
        cache.fail_writes = True

    def test_the_counters_prove_a_faulting_call_was_attempted(self) -> None:
        """The counters prove a faulting call was attempted."""
        # Without this, a double that returned None from an early `if` would pass
        # every totality assertion in the contract while never touching its
        # storage -- and so would an adapter whose swallow had drifted inward.
        cache = InMemoryOcrCache()
        cache.fail_reads = True
        cache.fail_writes = True
        key = an_ocr_key()
        cache.put(key, an_ocr_artifact())
        assert cache.get(key) is None
        assert cache.superseded(key) is None
        assert (cache.get_calls, cache.put_calls, cache.superseded_calls) == (1, 1, 1)

    def test_a_dropped_write_leaves_the_cache_cold(self) -> None:
        """A dropped write leaves the cache cold."""
        cache = InMemoryOcrCache()
        cache.fail_writes = True
        key = an_ocr_key()
        cache.put(key, an_ocr_artifact())
        cache.fail_writes = False
        assert cache.get(key) is None


class TestInMemoryDiagramCache(DiagramCacheCases):
    """The diagram cache contract over a dict."""

    @pytest.fixture
    def cache(self) -> ArtifactCache[DiagramCacheKey, DiagramArtifact]:
        """Return the double.

        Returns
        -------
        ArtifactCache[DiagramCacheKey, DiagramArtifact]
            The double.
        """
        return InMemoryDiagramCache()

    def induce_read_fault(self, cache: ArtifactCache[DiagramCacheKey, DiagramArtifact]) -> None:
        """Set the read-fault flag.

        Parameters
        ----------
        cache
            The subject.
        """
        assert isinstance(cache, InMemoryDiagramCache)
        cache.fail_reads = True

    def induce_write_fault(self, cache: ArtifactCache[DiagramCacheKey, DiagramArtifact]) -> None:
        """Set the write-fault flag.

        Parameters
        ----------
        cache
            The subject.
        """
        assert isinstance(cache, InMemoryDiagramCache)
        cache.fail_writes = True


class TestInMemorySyncAuditLog(SyncAuditLogContract):
    """The audit log contract over a list and a counter."""

    @pytest.fixture
    def log(self) -> SyncAuditLog:
        """Return the double.

        Returns
        -------
        SyncAuditLog
            The double.
        """
        return InMemorySyncAuditLog()

    def make_unreadable(self, log: SyncAuditLog) -> None:
        """Seed the double's unreadable seam.

        Parameters
        ----------
        log
            The subject.
        """
        assert isinstance(log, InMemorySyncAuditLog)
        log.seed_unreadable()

    def break_log(self, log: SyncAuditLog) -> None:
        """Set both fault flags.

        Parameters
        ----------
        log
            The subject.
        """
        assert isinstance(log, InMemorySyncAuditLog)
        log.fail_reads = True
        log.fail_writes = True

    def retain_newest(self, log: SyncAuditLog, keep: int) -> None:
        """Drop all but the newest entries.

        Parameters
        ----------
        log
            The subject.
        keep
            How many entries to retain.
        """
        assert isinstance(log, InMemorySyncAuditLog)
        log.retain_newest(keep)

    def test_retaining_fewer_than_one_entry_is_rejected(self) -> None:
        """Retaining fewer than one entry is rejected."""
        log = InMemorySyncAuditLog()
        with pytest.raises(ValueError, match="at least 1"):
            log.retain_newest(0)

    def test_retaining_more_than_the_history_drops_nothing(self) -> None:
        """Retaining more than the history drops nothing."""
        log = InMemorySyncAuditLog()
        assert log.retain_newest(5) == 0

    def test_a_read_fault_is_not_swallowed_by_the_log(self) -> None:
        """A read fault is not swallowed by the log."""
        # The caches are total; the log is not. A read fault here must surface,
        # because a silently short history cannot answer whether it is complete.
        log = InMemorySyncAuditLog()
        log.fail_reads = True
        with pytest.raises(StoreUnavailableError, match="seeded to fail") as caught:
            log.recent(limit=1)
        assert caught.value.store == IN_MEMORY_STORE
