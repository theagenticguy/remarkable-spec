"""Persistence ports: the sync mirror, the derived-artifact caches, and the audit log.

The application layer never opens a database. It depends on the four protocols
defined here, and the ``rmspec-cli`` composition root binds each to an adapter in
``rmspec-persistence``. That is the structural fix for the defect where SQL
literals lived in ``sync/migrations.py``, were hand-mirrored into pydantic models
in ``sync/models.py`` with nothing asserting the two agreed, and were driven
straight from CLI command bodies.

Rules this module holds itself to
---------------------------------
No storage vocabulary crosses the boundary. No method takes or returns a row
count, a cursor, a schema version, a transaction handle, or a query language, and
no declared error names a relational concept. Every error a method declares must
be raisable by the in-memory test double as well as by the SQLite adapter, so an
application ``except`` branch is reachable in a unit test. Errors that only a
relational adapter could produce -- schema mismatch, migration failure, integrity
violation, lock contention -- are adapter-internal: an adapter verifies its store
when it is constructed in the ``Scope.APP`` provider and fails container
composition with ``StoreUnavailableError`` naming the store, which is the same
place a missing optional dependency must surface. A per-call schema error would
otherwise force every call site to handle a failure only one adapter has.

Scope
-----
All four ports are ``Scope.APP``. Each call is durable when it returns; there is
no cross-call transaction and no ``UnitOfWork`` port. That preserves shipped
behaviour: the legacy writers committed per statement, so an interrupted
``rmspec sync pull`` keeps the pages it already recorded and a replay is
idempotent. An invocation-long write transaction would instead hold the write
lock for a 500-page pull and lose all 500 on a USB drop.

Errors named in the ``Raises`` sections, all defined in ``rmspec.domain.errors``
-------------------------------------------------------------------------------
``StoreUnavailableError``
    The store cannot be reached or used. Adapters fold I/O failure, lock
    contention after retry, and schema trouble into it.
``StoredRecordUnreadableError``
    A stored payload cannot be reconstructed as a domain model. The in-memory
    double reaches it through its ``seed_unreadable`` seam, so the handling
    branch is testable without SQLite.
``AuditWriteFailedError``
    An audit append did not land. The operation it describes may well have
    succeeded, which is why it is a distinct, single, user-visible degradation.

Domain models these ports depend on, all defined in ``rmspec.domain.models``
---------------------------------------------------------------------------
``SyncedDocument`` and ``SyncedPage`` are the tracked mirror of one tablet
document. ``PageText`` carries a page identity, the extracted text, and the
provenance of the extraction, so ``rmspec search`` can say how the text it
matched was produced. ``OcrCacheKey`` and ``DiagramCacheKey`` each expose a
``digest: str`` computed over every input that affects the output -- source-file
hash, renderer version, render DPI, model id, prompt version -- so a cache entry
of unknown provenance is unconstructible rather than merely unlikely.
``SyncAuditEntry`` is what callers append; ``RecordedSyncAuditEntry`` is what the
log returns, adding the store-assigned ``sequence``.

What deliberately has no port
-----------------------------
Comparison is not retrieval, so hash diffing is a pure domain function --
``diff_page_hashes(stored, current) -> PageDiff`` -- not a store method. Behind a
port, the rule for what counts as changed would live in every adapter, and the
double and SQLite could disagree with only a contract test between them.

Full-text search gets no port and no FTS5 table either. Extracted text is a fact
about a page, so it lives on the page in this store, written by
``record_page_text`` and read by ``page_texts``. A separate index would put
SQLite's ``MATCH`` grammar in the domain -- a substring double and a tokenised
adapter cannot satisfy one contract -- and would manufacture a second copy of the
text with no way to keep the two writes consistent.

Cache enumeration and eviction get no port. ``rmspec cache prune`` and "why did
this miss" are maintenance, not use cases; they live on a persistence-local class
the CLI constructs directly, since the CLI is the only package allowed to import
adapters. Keeping them out of the domain also keeps a wipe-everything default out
of it: an ``evict()`` whose empty filters mean "all" turns a computed-empty
selection into the loss of paid Textract and Bedrock output.

Nothing here is ``runtime_checkable``: the contract suite instantiates each
double and the adapter directly and calls them, so no test needs ``isinstance``.

Legacy surface that does not get a port
---------------------------------------
``get_page``, ``get_all_ocr``, ``find_changed_pages``, ``put_ocr``, ``get_ocr``
and ``migrate_ocr_sidecars`` have zero callers and are deleted, not ported --
about a third of ``sync/db.py``. ``migrate_ocr_sidecars`` also cannot ever have
worked: it derived ``{uuid}.ocr.rm`` from ``{uuid}.ocr.txt`` and so always
returned 0, which is why the only migration worth building imports the on-disk
``.ocr.txt`` text through ``record_page_text``. ``download_rmdoc`` is likewise
gone; the device has no zip route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.models import (
        DiagramArtifact,
        DiagramCacheKey,
        OcrArtifact,
        OcrCacheKey,
        PageText,
        RecordedSyncAuditEntry,
        SyncAuditEntry,
        SyncedDocument,
        SyncedPage,
    )

__all__ = ["DiagramCache", "DocumentSyncStore", "OcrCache", "SyncAuditLog"]


class DocumentSyncStore(Protocol):
    """The tracked mirror of the tablet: documents, their pages, and page text.

    Every method that returns a sequence declares a total order, because an
    undeclared order is a contract the in-memory double passes and the SQLite
    adapter breaks. An adapter that cannot express the order in its store sorts
    before returning.

    Notes
    -----
    Scope.APP. Adapters: ``SqliteDocumentSyncStore`` in ``rmspec-persistence``;
    ``InMemoryDocumentSyncStore`` in ``rmspec-persistence.testing``, a test
    double rather than a second product adapter -- there is one single-user
    ``~/.remarkable-spec/sync.db`` and no second store is planned, so this port
    is justified by testability and by the ban on ``sqlite3`` above the adapter
    layer, not by hypothetical swappability. The double keeps two dicts keyed by
    document uuid and offers ``seed_unreadable`` so
    ``StoredRecordUnreadableError`` is reachable in memory.
    """

    def record_document(
        self,
        document: SyncedDocument,
        pages: Sequence[SyncedPage],
        /,
    ) -> None:
        """Record a document together with its complete page set.

        One call, all or nothing: a document recorded without its pages is not a
        representable state. ``pages`` is required and never defaulted, because a
        default empty sequence cannot be told apart from "leave the pages alone"
        and would silently discard the page set on a metadata-only update.
        Recording is idempotent and replaces the document's page set, so
        replaying an interrupted pull converges.

        Parameters
        ----------
        document
            The document to record, keyed by its uuid.
        pages
            The document's complete page set. Passing an empty sequence records
            a document with no pages.

        Raises
        ------
        StoreUnavailableError
            The store cannot be written.
        """
        ...

    def get_document(self, doc_uuid: str, /) -> SyncedDocument | None:
        """Return the recorded document with this uuid, or ``None`` if untracked.

        Parameters
        ----------
        doc_uuid
            Uuid of the document to look up.

        Returns
        -------
        SyncedDocument | None
            The recorded document, or ``None`` when nothing is recorded for
            ``doc_uuid``.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed as a ``SyncedDocument``.
        """
        ...

    def list_documents(self) -> list[SyncedDocument]:
        """Return every recorded document, case-folded by name then by uuid.

        The order is part of the contract: ``(visible_name.casefold(), uuid)``
        ascending. It is total, so the double and the adapter cannot disagree on
        ties, and it is case-folded because that is what the legacy listing
        produced. There is no name or parent filter: filtering is a domain
        concern, and a store-side filter would inherit the adapter's collation --
        SQL ``LIKE`` folds ASCII case while Python ``in`` does not.

        Returns
        -------
        list[SyncedDocument]
            Every recorded document in contract order.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed as a ``SyncedDocument``.
        """
        ...

    def forget_document(self, doc_uuid: str, /) -> None:
        """Forget a document, its pages, and its page text.

        Forgetting an untracked document is a successful no-op, not an error: the
        only caller reacts to a device-side delete and would have to catch and
        discard a not-found error every time. Nothing is returned -- a count of
        what was deleted is a store-shaped value the caller has no use for.

        Parameters
        ----------
        doc_uuid
            Uuid of the document to forget.

        Raises
        ------
        StoreUnavailableError
            The store cannot be written.
        """
        ...

    def record_page_text(self, page_text: PageText, /) -> None:
        """Record the extracted text of one page, replacing any earlier text.

        Text arrives after the page does -- OCR runs as its own command, long
        after the pull that recorded the page -- so it is written per page rather
        than folded into :meth:`record_document`, which would have to rewrite the
        whole page set to store one page's text. Keyed by the page identity
        carried in ``page_text``, so re-running OCR overwrites rather than
        accumulating.

        Parameters
        ----------
        page_text
            The page identity, its extracted text, and the provenance of the
            extraction.

        Raises
        ------
        StoreUnavailableError
            The store cannot be written.
        """
        ...

    def page_texts(self, doc_uuid: str, /) -> list[PageText]:
        """Return recorded page text for one document, ordered by page index.

        The order is ``page_index`` ascending, tie-broken by page uuid, so it is
        total. Searching is a pure domain function over the returned entries,
        which is what keeps a query language out of this port and what lets
        search read text whose provenance no longer matches any current cache
        key -- a cache is an exact-key lookup and must never double as a browse.

        Parameters
        ----------
        doc_uuid
            Uuid of the document whose page text is wanted.

        Returns
        -------
        list[PageText]
            Recorded page text in contract order; empty when the document is
            untracked or has no text.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed as a ``PageText``.
        """
        ...

    def all_page_texts(self) -> list[PageText]:
        """Return recorded page text for every tracked document.

        A separate method rather than an optional filter on :meth:`page_texts`,
        so "every document" is a distinct call the reader can see rather than a
        default argument that a mistakenly-``None`` uuid could reach. The order
        is ``(doc_uuid, page_index, page_uuid)`` ascending, and it is total.

        Returns
        -------
        list[PageText]
            All recorded page text in contract order.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed as a ``PageText``.
        """
        ...


class OcrCache(Protocol):
    """Memoize OCR output under a key containing every input that affects it.

    Lookup is by ``key.digest`` and nothing else. There is no partial match and
    no fallback, so a changed prompt, model id, renderer version, or render DPI
    yields a different digest and therefore a miss -- never a stale hit that
    looks valid. The legacy tables keyed on the source hash alone while storing
    the model id and DPI as non-key columns, which made exactly that stale hit
    representable.

    Both methods are total: neither raises. A read fault is a miss, a write fault
    is dropped after the adapter logs it, and a cache that cannot be opened at
    all fails when it is constructed in the ``Scope.APP`` provider. That is
    sound because a miss only costs a recomputation, and it means the error
    parity rule -- every declared error must be raisable by the double -- holds
    trivially, with no unreachable ``except`` branch in the application layer.

    A separate Protocol from :class:`DiagramCache` rather than one generic
    ``ArtifactCache[K, V]``: two named ports are separately bindable, separately
    null-able, and legible to a type checker with every rule at error, which is
    worth more than the one deduplicated adapter class a parameterized generic
    alias would buy.

    Notes
    -----
    Scope.APP. Adapters: ``SqliteOcrCache`` over a table of
    ``(digest, key payload, artifact payload)`` with no per-field columns, so
    there is no mirrored column for the model to drift from;
    ``NullOcrCache``, which makes ``--no-cache`` a binding rather than an ``if``
    inside a use case; ``InMemoryOcrCache`` in ``rmspec-persistence.testing``,
    a dict keyed by digest, contract-exact because the port declares no error it
    cannot produce.
    """

    def get(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return the artifact stored under this exact key, or ``None``.

        Parameters
        ----------
        key
            The complete cache key; only ``key.digest`` is matched.

        Returns
        -------
        OcrArtifact | None
            The cached artifact, or ``None`` on a miss or any read fault.
        """
        ...

    def put(self, key: OcrCacheKey, artifact: OcrArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry.

        Total and idempotent: storing the same key twice is not a conflict.

        Parameters
        ----------
        key
            The complete cache key, stored alongside the artifact so an entry
            can never exist without the full provenance that produced it.
        artifact
            The OCR result to cache.
        """
        ...


class DiagramCache(Protocol):
    """Memoize Mermaid diagram extraction under a fully-specified key.

    Same contract as :class:`OcrCache` -- exact ``key.digest`` lookup, no
    fallback, both methods total -- over the diagram artifact type. See that
    class for why the two are separate Protocols rather than one generic, and
    why an unavailable cache fails at container composition instead of at a call
    site.

    Notes
    -----
    Scope.APP. Adapters: ``SqliteDiagramCache`` (the same implementation as the
    OCR cache, bound to a different table), ``NullDiagramCache`` for
    ``--no-cache``, and ``InMemoryDiagramCache`` in
    ``rmspec-persistence.testing``.
    """

    def get(self, key: DiagramCacheKey, /) -> DiagramArtifact | None:
        """Return the artifact stored under this exact key, or ``None``.

        Parameters
        ----------
        key
            The complete cache key; only ``key.digest`` is matched.

        Returns
        -------
        DiagramArtifact | None
            The cached artifact, or ``None`` on a miss or any read fault.
        """
        ...

    def put(self, key: DiagramCacheKey, artifact: DiagramArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry.

        Total and idempotent: storing the same key twice is not a conflict.

        Parameters
        ----------
        key
            The complete cache key, stored alongside the artifact.
        artifact
            The diagram extraction to cache.
        """
        ...


class SyncAuditLog(Protocol):
    """Append-only history of sync, OCR, and push operations, including failures.

    An append survives the failure it describes. The log is durable the moment
    :meth:`append` returns and is not tied to any surrounding unit of work, so a
    pull that dies at document 400 still has the entry recording the death --
    the legacy writer behaved this way, committing immediately from inside the
    pull loop and from the failure paths, and a log that rolled back with the
    operation would delete exactly the records worth keeping.

    Order is assigned by the store, not read off a clock. :meth:`append` returns
    a strictly increasing sequence number and :meth:`recent` returns entries
    carrying it, newest first. Wall-clock ordering was ambiguous three different
    ways: entries written inside one pull share a timestamp, tests freeze the
    clock, and the double, a file, and SQLite each broke the tie differently.
    With a sequence, the contract test is ``recent()`` equals the appends
    reversed, and it needs no clock at all.

    Notes
    -----
    Scope.APP. Adapters: ``SqliteSyncAuditLog``, which holds its own
    autocommit connection so it never contends with, or depends on, the store's
    writes; and ``InMemorySyncAuditLog`` in ``rmspec-persistence.testing``, a
    list whose sequence is its length. The double is testing-only -- dry-run
    paths get a null binding, not an audit log that silently discards history.
    """

    def append(self, entry: SyncAuditEntry, /) -> int:
        """Append one entry and return the sequence number the store assigned.

        Parameters
        ----------
        entry
            The operation to record. Failed and partial operations are recorded
            too; the outcome and its failure detail are fields of the entry.

        Returns
        -------
        int
            The store-assigned sequence number: strictly increasing across
            appends, starting at 1.

        Raises
        ------
        AuditWriteFailedError
            The entry did not land. The operation it describes may still have
            succeeded, so callers report this as a degradation -- "operation
            succeeded, history not recorded" -- and do not retry the operation.
        """
        ...

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the most recently appended entries, newest first.

        Ordered by ``sequence`` descending, which is total. ``limit`` is required
        and keyword-only: how many entries to show is the caller's decision, and
        a default here would be a display concern living in the domain. There is
        no document filter, because no command filters the history yet.

        Unreadable stored entries are skipped by the adapter rather than raised
        past this boundary; the count of skipped entries is reported through the
        adapter's own diagnostics, so one damaged entry cannot make the whole
        history unreadable.

        Parameters
        ----------
        limit
            Maximum number of entries to return. Must be positive.

        Returns
        -------
        list[RecordedSyncAuditEntry]
            Up to ``limit`` entries, newest first, each carrying its sequence.

        Raises
        ------
        StoreUnavailableError
            The log cannot be read.
        """
        ...
