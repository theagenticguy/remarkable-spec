"""In-memory doubles for the four persistence ports, plus the tier-0 index.

Test doubles, not second product adapters. They ship under ``src/`` rather than
in a ``tests/`` helper for two reasons: the port docstrings promise these names at
``rmspec-persistence.testing``, and every later application-layer test binds them
-- the architecture suite scans ``src/`` for import direction, so an application
*test* may depend on this module while application *source* stays domain-only.

Each double mirrors its adapter's rules line for line, and each carries the seams
without which the ports' guarantees are unassertable.

``fail_reads`` / ``fail_writes``
    A store fault on demand. The caches are total, so through the port a
    swallowed fault and a genuine miss are the same ``None``; only a double that
    can be told to fault, plus the call counters below, proves the swallow
    happened rather than the call being skipped.

``seed_unreadable``
    ``StoredRecordUnreadableError`` without a corrupt file. The sync store takes a
    record kind, one per reader, because a single seam would make a
    :class:`~rmspec.domain.models.PageText` failure unreachable without also
    poisoning the :class:`~rmspec.domain.models.SyncedDocument` the same test
    needs to read.

:class:`FakeHandwrittenTextIndex` is named for what it fakes rather than for how it
stores, because "in memory" would say nothing: the real reader is a database image
held in memory too. What it stands in for is the *device*, which no test may touch.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import (
    AuditWriteFailedError,
    StoredRecordUnreadableError,
    StoreUnavailableError,
)
from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    OcrArtifact,
    OcrCacheKey,
    RecordedSyncAuditEntry,
)
from rmspec.domain.ports.ocr import IndexedHandwriting

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.models import PageText, SyncAuditEntry, SyncedDocument, SyncedPage

__all__ = [
    "IN_MEMORY_STORE",
    "FakeHandwrittenTextIndex",
    "InMemoryDiagramCache",
    "InMemoryDocumentSyncStore",
    "InMemoryOcrCache",
    "InMemorySyncAuditLog",
    "SeededRecordKind",
]

#: The ``store`` label the doubles put in every error they raise, so a test can
#: tell a double's failure from an adapter's.
IN_MEMORY_STORE: Final = "in-memory"


class SeededRecordKind(StrEnum):
    """Which reader a seeded unreadable record should break.

    One member per reader on :class:`~rmspec.domain.ports.persistence.DocumentSyncStore`
    that declares ``StoredRecordUnreadableError``.
    """

    DOCUMENT = "document"
    """Break :meth:`InMemoryDocumentSyncStore.get_document` and ``list_documents``."""

    PAGE = "page"
    """Break :meth:`InMemoryDocumentSyncStore.pages`."""

    PAGE_TEXT = "page_text"
    """Break :meth:`InMemoryDocumentSyncStore.page_texts` and ``all_page_texts``."""


class InMemoryDocumentSyncStore:
    """Three dicts standing in for the three mirror tables.

    Applies the same page-set replacement, surviving-text retention, text
    re-indexing, orphan-drop and total-ordering rules as
    :class:`~rmspec.persistence.sync_store.SqliteDocumentSyncStore`, and is meant
    to be read side by side with it.
    """

    def __init__(self) -> None:
        self.fail_reads = False
        """When true, every reader raises ``StoreUnavailableError``."""

        self.fail_writes = False
        """When true, every writer raises ``StoreUnavailableError``."""

        self._documents: dict[str, SyncedDocument] = {}
        self._pages: dict[str, dict[str, SyncedPage]] = {}
        self._texts: dict[str, dict[str, PageText]] = {}
        self._unreadable: set[tuple[SeededRecordKind, str]] = set()

    def seed_unreadable(self, record_kind: SeededRecordKind, doc_uuid: str, /) -> None:
        """Make one reader fail for one document.

        Parameters
        ----------
        record_kind
            Which reader to break.
        doc_uuid
            The document whose record is unreadable.
        """
        self._unreadable.add((record_kind, doc_uuid))

    def _guard_read(self) -> None:
        """Raise when reads are seeded to fail.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        """
        if self.fail_reads:
            raise StoreUnavailableError(store=IN_MEMORY_STORE, detail="reads are seeded to fail")

    def _guard_write(self) -> None:
        """Raise when writes are seeded to fail.

        Raises
        ------
        StoreUnavailableError
            ``fail_writes`` is set.
        """
        if self.fail_writes:
            raise StoreUnavailableError(store=IN_MEMORY_STORE, detail="writes are seeded to fail")

    def _guard_readable(self, record_kind: SeededRecordKind, doc_uuid: str, /) -> None:
        """Raise when this document's record of this kind is seeded unreadable.

        Parameters
        ----------
        record_kind
            The reader being served.
        doc_uuid
            The document being read.

        Raises
        ------
        StoredRecordUnreadableError
            The pair was passed to :meth:`seed_unreadable`.
        """
        if (record_kind, doc_uuid) in self._unreadable:
            raise StoredRecordUnreadableError(
                store=IN_MEMORY_STORE,
                table=record_kind.value,
                key=doc_uuid,
                detail="seeded unreadable",
            )

    def record_document(
        self,
        document: SyncedDocument,
        pages: Sequence[SyncedPage],
        /,
    ) -> None:
        """Record a document together with its complete page set.

        Parameters
        ----------
        document
            The document to record.
        pages
            Its complete page set, every page owned by ``document``. Departed
            pages lose their text; surviving pages keep theirs, re-indexed if the
            page moved.

        Raises
        ------
        ValueError
            A page's ``doc_uuid`` is not ``document.uuid``. Checked here, ahead of
            ``fail_writes``, because it is a fact about the arguments rather than
            about the store -- and because the SQLite binding cannot store such a
            pair at all, so a double that accepted it would make a caller bug
            green in memory and a foreign-key failure in production.
        StoreUnavailableError
            ``fail_writes`` is set.
        """
        stray = sorted({page.page_uuid for page in pages if page.doc_uuid != document.uuid})
        if stray:
            msg = f"pages {stray} do not belong to {document.uuid}"
            raise ValueError(msg)
        self._guard_write()
        incoming = {page.page_uuid: page for page in pages}
        self._documents[document.uuid] = document
        self._pages[document.uuid] = dict(incoming)
        texts = self._texts.get(document.uuid, {})
        surviving = {
            page_uuid: (
                text
                if text.page_index == incoming[page_uuid].page_index
                else text.model_copy(update={"page_index": incoming[page_uuid].page_index})
            )
            for page_uuid, text in texts.items()
            if page_uuid in incoming
        }
        self._texts[document.uuid] = surviving

    def get_document(self, doc_uuid: str, /) -> SyncedDocument | None:
        """Return the recorded document with this uuid, or ``None``.

        Parameters
        ----------
        doc_uuid
            Uuid of the document to look up.

        Returns
        -------
        SyncedDocument | None
            The recorded document, or ``None`` when untracked.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            The document's record was seeded unreadable.
        """
        self._guard_read()
        self._guard_readable(SeededRecordKind.DOCUMENT, doc_uuid)
        return self._documents.get(doc_uuid)

    def list_documents(self) -> list[SyncedDocument]:
        """Return every recorded document, case-folded by name then by uuid.

        Returns
        -------
        list[SyncedDocument]
            Every recorded document in the port's declared order.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            Any document's record was seeded unreadable.
        """
        self._guard_read()
        for uuid in self._documents:
            self._guard_readable(SeededRecordKind.DOCUMENT, uuid)
        return sorted(
            self._documents.values(),
            key=lambda document: (document.visible_name.casefold(), document.uuid),
        )

    def pages(self, doc_uuid: str, /) -> list[SyncedPage]:
        """Return the recorded pages of one document, ordered by page index.

        Parameters
        ----------
        doc_uuid
            Uuid of the document whose pages are wanted.

        Returns
        -------
        list[SyncedPage]
            The recorded pages, ``page_index`` then page uuid ascending.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            A page record was seeded unreadable.
        """
        self._guard_read()
        self._guard_readable(SeededRecordKind.PAGE, doc_uuid)
        return sorted(
            self._pages.get(doc_uuid, {}).values(),
            key=lambda page: (page.page_index, page.page_uuid),
        )

    def forget_document(self, doc_uuid: str, /) -> None:
        """Forget a document, its pages, and its page text.

        Parameters
        ----------
        doc_uuid
            Uuid of the document to forget. Forgetting an untracked document is a
            successful no-op.

        Raises
        ------
        StoreUnavailableError
            ``fail_writes`` is set.
        """
        self._guard_write()
        self._documents.pop(doc_uuid, None)
        self._pages.pop(doc_uuid, None)
        self._texts.pop(doc_uuid, None)

    def record_page_text(self, page_text: PageText, /) -> None:
        """Record the extracted text of one page, replacing any earlier text.

        Parameters
        ----------
        page_text
            The page identity, the text, and the provenance. Text for a page uuid
            that is not in the recorded page set is not stored, and the call still
            succeeds.

        Raises
        ------
        StoreUnavailableError
            ``fail_writes`` is set.
        """
        self._guard_write()
        if page_text.page_uuid not in self._pages.get(page_text.doc_uuid, {}):
            return
        self._texts.setdefault(page_text.doc_uuid, {})[page_text.page_uuid] = page_text

    def page_texts(self, doc_uuid: str, /) -> list[PageText]:
        """Return recorded page text for one document, ordered by page index.

        Parameters
        ----------
        doc_uuid
            Uuid of the document whose text is wanted.

        Returns
        -------
        list[PageText]
            Recorded text, ``page_index`` then page uuid ascending.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            A text record was seeded unreadable.
        """
        self._guard_read()
        self._guard_readable(SeededRecordKind.PAGE_TEXT, doc_uuid)
        return sorted(
            self._texts.get(doc_uuid, {}).values(),
            key=lambda text: (text.page_index, text.page_uuid),
        )

    def all_page_texts(self) -> list[PageText]:
        """Return recorded page text for every tracked document.

        Returns
        -------
        list[PageText]
            All recorded text, ordered by ``(doc_uuid, page_index, page_uuid)``.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            Any text record was seeded unreadable.
        """
        self._guard_read()
        for doc_uuid in self._texts:
            self._guard_readable(SeededRecordKind.PAGE_TEXT, doc_uuid)
        return sorted(
            (text for texts in self._texts.values() for text in texts.values()),
            key=lambda text: (text.doc_uuid, text.page_index, text.page_uuid),
        )


class _InMemoryArtifactCache[
    K: (OcrCacheKey, DiagramCacheKey),
    A: (OcrArtifact, DiagramArtifact),
]:
    """One dict keyed by digest, with fault seams and call counters."""

    def __init__(self) -> None:
        self.fail_reads = False
        """When true, ``get`` and ``superseded`` behave as a faulting store."""

        self.fail_writes = False
        """When true, ``put`` behaves as a faulting store."""

        self.get_calls = 0
        """How many times :meth:`get` was entered, faults included."""

        self.put_calls = 0
        """How many times :meth:`put` was entered, faults included."""

        self.superseded_calls = 0
        """How many times :meth:`superseded` was entered, faults included."""

        self._entries: dict[str, tuple[K, A]] = {}

    def get(self, key: K, /) -> A | None:
        """Return the artifact stored under this exact key, or ``None``.

        Parameters
        ----------
        key
            The complete cache key; only ``key.digest`` is matched.

        Returns
        -------
        A | None
            The cached artifact, ``None`` on a miss, and ``None`` on a seeded
            read fault -- the fault is swallowed exactly as the adapter swallows
            it, which is what makes the counter above the only evidence the call
            happened.
        """
        self.get_calls += 1
        if self.fail_reads:
            return None
        entry = self._entries.get(key.digest)
        return None if entry is None else entry[1]

    def put(self, key: K, artifact: A, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry.

        Parameters
        ----------
        key
            The complete cache key, stored beside the artifact.
        artifact
            The result to cache. Dropped on a seeded write fault.
        """
        self.put_calls += 1
        if self.fail_writes:
            return
        self._entries[key.digest] = (key, artifact)

    def superseded(self, key: K, /) -> K | None:
        """Return a stored key for the same page under other inputs, or ``None``.

        Parameters
        ----------
        key
            The key that missed.

        Returns
        -------
        K | None
            The stored key with the same ``page_hash`` and the greatest differing
            ``digest``; ``None`` when there is none, when ``key`` itself is
            stored, or on a seeded read fault.
        """
        self.superseded_calls += 1
        if self.fail_reads or key.digest in self._entries:
            return None
        candidates = [
            digest
            for digest, (stored, _) in self._entries.items()
            if stored.page_hash == key.page_hash and digest != key.digest
        ]
        if not candidates:
            return None
        return self._entries[max(candidates)][0]


class InMemoryOcrCache(_InMemoryArtifactCache[OcrCacheKey, OcrArtifact]):
    """In-memory :class:`~rmspec.domain.ports.persistence.OcrCache`."""


class InMemoryDiagramCache(_InMemoryArtifactCache[DiagramCacheKey, DiagramArtifact]):
    """In-memory :class:`~rmspec.domain.ports.persistence.DiagramCache`."""


class InMemorySyncAuditLog:
    """A list plus a high-water counter that only ever increases.

    The counter is separate from the list precisely so
    :meth:`retain_newest` cannot cause a sequence to be reused -- the failure a
    rowid-allocated sequence produces, and the reason the SQLite adapter keeps a
    counter row of its own.
    """

    def __init__(self) -> None:
        self.fail_writes = False
        """When true, :meth:`append` raises ``AuditWriteFailedError``."""

        self.fail_reads = False
        """When true, :meth:`recent` raises ``StoreUnavailableError``."""

        self._entries: list[RecordedSyncAuditEntry] = []
        self._next_sequence = 1
        self._unreadable = False

    def seed_unreadable(self) -> None:
        """Make :meth:`recent` raise ``StoredRecordUnreadableError``."""
        self._unreadable = True

    def retain_newest(self, keep: int, /) -> int:
        """Drop all but the newest ``keep`` entries, leaving the counter alone.

        Parameters
        ----------
        keep
            How many entries to retain.

        Returns
        -------
        int
            How many entries were dropped.

        Raises
        ------
        ValueError
            ``keep`` is less than 1, matching
            :meth:`~rmspec.persistence.maintenance.StoreMaintenance.trim_audit_log`.
        """
        if keep < 1:
            msg = f"keep must be at least 1, got {keep}"
            raise ValueError(msg)
        dropped = max(0, len(self._entries) - keep)
        self._entries = self._entries[dropped:]
        return dropped

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Append one entry and return it as the log recorded it.

        Parameters
        ----------
        entry
            The operation to record.

        Returns
        -------
        RecordedSyncAuditEntry
            The appended entry with its sequence.

        Raises
        ------
        AuditWriteFailedError
            ``fail_writes`` is set.
        """
        if self.fail_writes:
            raise AuditWriteFailedError(detail="writes are seeded to fail")
        recorded = RecordedSyncAuditEntry(sequence=self._next_sequence, entry=entry)
        self._next_sequence += 1
        self._entries.append(recorded)
        return recorded

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the most recently appended entries, newest first.

        Parameters
        ----------
        limit
            Maximum number of entries to return. Must be at least 1.

        Returns
        -------
        list[RecordedSyncAuditEntry]
            Up to ``limit`` entries, ``sequence`` descending.

        Raises
        ------
        ValueError
            ``limit`` is less than 1. Checked first, before either fault seam, so
            a test can prove the check precedes any store access.
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            :meth:`seed_unreadable` was called.
        """
        if limit < 1:
            msg = f"limit must be at least 1, got {limit}"
            raise ValueError(msg)
        if self.fail_reads:
            raise StoreUnavailableError(store=IN_MEMORY_STORE, detail="reads are seeded to fail")
        if self._unreadable:
            raise StoredRecordUnreadableError(
                store=IN_MEMORY_STORE,
                table="sync_audit",
                key="seeded",
                detail="seeded unreadable",
            )
        newest_first = sorted(self._entries, key=lambda recorded: recorded.sequence, reverse=True)
        return newest_first[:limit]


class FakeHandwrittenTextIndex:
    """A dict of rows standing in for the tablet's own search index.

    The :class:`~rmspec.domain.ports.ocr.HandwrittenTextIndex` double. It answers the
    same three states :class:`~rmspec.persistence.search_index.DeviceSearchIndex`
    answers -- a reading, an indexed page that held nothing, and no row at all -- and
    is meant to be read side by side with it.

    Both failure seams exist because the reader's dangerous states are unreachable
    otherwise. ``fail_reads`` is a torn image: the tablet was writing the index while
    it was copied, so the integrity check refuses it and the caller must fall through
    to a paid read rather than treat the command as failed. :meth:`seed_duplicated`
    is one page uuid on two rows, which the reader refuses to arbitrate.
    """

    def __init__(self, *, revision: str = "1", generation: int = 1) -> None:
        self.fail_reads = False
        """When true, :meth:`lookup` raises ``StoreUnavailableError``."""

        self.lookup_calls = 0
        """How many times :meth:`lookup` was entered, faults included."""

        self._provider_id = f"device-index@{revision}"
        self._generation = generation
        self._rows: dict[str, tuple[str, str]] = {}
        self._duplicated: set[str] = set()

    def seed(self, page_ref: str, /, *, entry_ref: str, text: str) -> None:
        """Record one indexed page.

        Parameters
        ----------
        page_ref
            The page uuid, as the index's ``pageId``.
        entry_ref
            The document uuid the page belongs to.
        text
            The device's own reading. ``""`` is a legitimate seeding: it means the
            device indexed the page and found nothing, which is not the same as
            leaving the page unseeded.
        """
        self._rows[page_ref] = (entry_ref, text)

    def seed_duplicated(self, page_ref: str, /) -> None:
        """Make one page uuid look like two rows.

        Parameters
        ----------
        page_ref
            The page uuid that stops identifying a single row.
        """
        self._duplicated.add(page_ref)

    @property
    def provider_id(self) -> str:
        """Return this index's stable identity slug.

        Returns
        -------
        str
            ``"device-index@<revision>"``, in the adapter's own format so a test
            that folds the slug into a cache key exercises the real shape.
        """
        return self._provider_id

    def lookup(self, page_ref: str, /) -> IndexedHandwriting | None:
        """Return the seeded row for one page, or ``None`` when there is none.

        Parameters
        ----------
        page_ref
            The page uuid to look up.

        Returns
        -------
        IndexedHandwriting | None
            The seeded reading, or ``None`` when the page was never seeded.

        Raises
        ------
        StoreUnavailableError
            ``fail_reads`` is set.
        StoredRecordUnreadableError
            The page was passed to :meth:`seed_duplicated`.
        """
        self.lookup_calls += 1
        if self.fail_reads:
            raise StoreUnavailableError(
                store=IN_MEMORY_STORE,
                detail="the image is seeded to fail its integrity check",
            )
        if page_ref in self._duplicated:
            raise StoredRecordUnreadableError(
                store=IN_MEMORY_STORE,
                table="search",
                key=page_ref,
                detail="2 rows share this pageId, so no row identifies this page",
            )
        row = self._rows.get(page_ref)
        if row is None:
            return None
        return IndexedHandwriting(
            page_ref=page_ref,
            entry_ref=row[0],
            text=row[1],
            generation=self._generation,
        )
