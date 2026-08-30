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

One builtin is also declared, and it is not a store error: ``ValueError`` for an
argument the caller got wrong before any store was touched -- a non-positive
``limit``, and a page set that does not belong to the document it is recorded
with. Both are spelled out because an unspecified argument is where two adapters
provably diverge: a bare list slice and a store-side row limit do not agree about
a negative number, and a dict keyed by the document and a table keyed by the page
do not agree about a page whose ``doc_uuid`` names somewhere else. The caller
cannot be asked to know which one it is talking to.

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
Change detection gets no port method. Comparison is not retrieval:
:meth:`DocumentSyncStore.pages` hands back the recorded page mirror and the use
case compares those fingerprints against the ones it just computed from the
device. Behind a port, the rule for what counts as changed would live in every
adapter, and the double and SQLite could disagree with only a contract test
between them. No pure helper is named here either -- whichever function subtracts
the two sequences lives with its caller -- because this module must not promise a
domain symbol that does not exist, and because what the port genuinely owes the
caller is the stored side of that comparison, not a claim about who subtracts.

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
``get_pages`` *is* ported, as :meth:`DocumentSyncStore.pages`. Without it the page
mirror would be write-only: a pull could then detect a changed page only by
re-reading and re-hashing every page's scene bytes, or by opening the database
above the adapter layer, which is the defect this module exists to close.

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
    layer, not by hypothetical swappability. The double keeps three dicts keyed by
    document uuid -- documents, pages, page text -- and offers
    ``seed_unreadable(record_kind, doc_uuid)`` with one kind per reader, so
    ``StoredRecordUnreadableError`` is reachable in memory for
    :meth:`get_document`, :meth:`pages` and :meth:`page_texts` independently. A
    single seam would make a ``PageText`` failure unreachable without also
    poisoning the ``SyncedDocument`` the same test needs to read.
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

        Replacing the page set also discards recorded text for every page uuid the
        new set omits. Text is a fact about a page, so it cannot outlive the page
        it describes; stating it here is what stops a cascading relational schema
        and a double holding two independent dicts from both passing while
        disagreeing about whether :meth:`page_texts` still returns the departed
        page. Text for a page uuid that survives the replacement is kept, even if
        its ``page_index`` moved.

        Every page must name this document. ``SyncedPage.doc_uuid`` and
        ``document.uuid`` are two spellings of one fact, and a pair that disagrees
        is a caller bug with no reading that is safe to guess: filing the page
        under the document strands a row whose payload names someone else, and
        filing it under the page writes outside the document the call named. So it
        is refused, before any store is touched, and identically by every binding
        -- an implementation left to choose would make a re-parented page set green
        against a double and destructive against a database.

        Parameters
        ----------
        document
            The document to record, keyed by its uuid.
        pages
            The document's complete page set, every page owned by ``document``.
            Passing an empty sequence records a document with no pages and
            discards all of its recorded text.

        Raises
        ------
        ValueError
            A page's ``doc_uuid`` is not ``document.uuid``. Raised before anything
            is recorded, so the call changes nothing.
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
        ties. It is *not* what the legacy listing produced: legacy ``ORDER BY
        visible_name`` took SQLite's BINARY collation, which puts every uppercase
        letter before every lowercase one -- "Banana" before "apple" -- so a
        mixed-case library lists in a visibly different order than it used to.
        That change is deliberate and user-visible, and saying so here is what
        stops the next reader concluding ``rmspec ls`` output was unaffected.
        There is no name or parent filter: filtering is a domain concern, and a
        store-side filter would inherit the adapter's collation -- SQL ``LIKE``
        folds ASCII case while Python ``in`` does not.

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

    def pages(self, doc_uuid: str, /) -> list[SyncedPage]:
        """Return the recorded pages of one document, ordered by page index.

        The read half of :meth:`record_document`. It exists so that
        ``SyncedPage.rm_hash`` -- the fingerprint whose comparison is the whole
        point of the mirror -- can actually be compared: the use case reads the
        recorded pages here, computes the current fingerprints from the device, and
        subtracts the two itself. Without this method the stored fingerprints are
        write-only, and page-level change detection has to re-hash every page's
        scene bytes on every pull or reach past the adapter layer into the
        database.

        The order is ``page_index`` ascending, tie-broken by page uuid, so it is
        total and matches :meth:`page_texts`.

        Parameters
        ----------
        doc_uuid
            Uuid of the document whose recorded pages are wanted.

        Returns
        -------
        list[SyncedPage]
            The recorded pages in contract order; empty when the document is
            untracked or was recorded with no pages. The empty list does not
            distinguish those two cases, and it does not need to --
            :meth:`get_document` answers that, and a caller that needs both facts
            wants both calls rather than a sentinel from one.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed as a ``SyncedPage``.
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
        whole page set to store one page's text.

        The key is ``(page_text.doc_uuid, page_text.page_uuid)`` and nothing else;
        ``page_index`` is payload. Re-running OCR therefore overwrites rather than
        accumulating, and a page that moved within its document keeps the one text
        row it has. Spelling the key out is what stops a double keying on the pair
        and an adapter keying on the triple from both passing the same suite while
        one of them grows a second row per page.

        Text whose ``page_uuid`` is not in the document's recorded page set is not
        stored, and the call still succeeds. An orphaned text row is not a
        representable state -- see :meth:`record_document` -- and making the write
        a no-op rather than an error is what keeps this reachable in the double: an
        adapter enforcing the relationship in its schema could only report the
        violation as ``StoreUnavailableError``, which is a lie about a store that
        is working fine. The cost is explicit: text extracted for a page the mirror
        no longer has is discarded, so a caller that cares reads :meth:`pages`
        first.

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

        Every returned entry names a page in the document's current recorded page
        set: text is dropped when its page departs, so there is nothing orphaned to
        filter and no need for the caller to intersect this result with
        :meth:`pages`.

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

    Lookup is by ``key.digest`` with one sanctioned exception, and it is spelled
    out as its own method rather than hidden in :meth:`get`:
    :meth:`equivalent_raster` allows ``page_hash`` -- and only ``page_hash`` -- to
    differ, because that component fingerprints the bytes the page is stored as
    while every reader below tier 0 reads the pixels. A caller that serves such a
    row has to say so, which is what
    :attr:`~rmspec.domain.errors.DegradationKind.CACHE_HIT_RASTER_EQUIVALENT` is
    for. Keeping it out of :meth:`get` is the point: a fallback nobody asked for
    is how a stale hit becomes invisible, and a second named method is a fallback
    a caller opts into and reports.

    All four methods are total: none raises. A read fault is a miss, a write
    fault is dropped after the adapter logs it, and a cache that cannot be opened
    at all fails when it is constructed in the ``Scope.APP`` provider. That is
    sound because a miss only costs a recomputation, and it means the error
    parity rule -- every declared error must be raisable by the double -- holds
    trivially, with no unreachable ``except`` branch in the application layer.

    A hit can be a partial page, and says so. An ``OcrArtifact`` carries
    ``truncated``, so a completion the model cut short is stored with that fact
    attached and :meth:`get` returns it unchanged rather than dropping it: the
    caller re-decides from the flag exactly as it decided on the fresh path, and
    the paid output is still there for a run that accepts it. Dropping truncated
    artifacts at :meth:`put` instead would make ``truncated`` unrepresentable in
    the store and re-pay the recognizers on every run of a page that will truncate
    again. What the port does guarantee is that the flag survives the round trip;
    a caller that ignores it gets a half page, and no key can protect it from
    that.

    Lookup happens after rendering. ``key.raster_digest`` is an input, so the page
    must already be rendered and rasterized before a key exists: this cache saves
    the recognizer and model calls, never the render.

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
    cannot produce. The double takes ``fail_reads`` and ``fail_writes`` flags and
    counts every one of the four methods' calls, because totality is otherwise
    unassertable: through this port a swallowed fault and a genuine miss are the
    same ``None``, so only a double that can be told to fault proves the swallow
    happened at all.
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
            The cached artifact, or ``None`` on a miss or any read fault. A
            returned artifact may have ``truncated`` set; that is a hit, not a
            fault, and the caller decides whether to use it or recompute.
        """
        ...

    def put(self, key: OcrCacheKey, artifact: OcrArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry.

        Total and idempotent: storing the same key twice is not a conflict, and a
        write fault is swallowed after the adapter logs it rather than raised, so a
        full disk costs the next run a recomputation instead of failing the command
        that just did the work. Spelling the swallow out here is what keeps a
        relational adapter and the double answering the same way.

        Parameters
        ----------
        key
            The complete cache key, stored alongside the artifact so an entry
            can never exist without the full provenance that produced it.
        artifact
            The OCR result to cache. A truncated result is stored, with its
            ``truncated`` flag, rather than silently discarded.
        """
        ...

    def superseded(self, key: OcrCacheKey, /) -> OcrCacheKey | None:
        """Return a stored key for the same page under other inputs, or ``None``.

        A miss says "recompute"; this says why. The returned key has the same
        ``page_hash`` as ``key`` and a different ``digest``, which means the page
        itself did not change and something upstream of it did -- prompt, model,
        renderer, DPI, recognizer set. It is the only way a caller can report
        ``DegradationKind.CACHE_MISS_KEY_CHANGED`` rather than a bare miss, and
        without it the key payload stored beside every artifact is write-only.

        When several stored keys qualify, the one whose ``digest`` is greatest in
        lexicographic order is returned. The order is arbitrary but declared, so
        the double and the adapter cannot disagree. The result is diagnostic only:
        it names provenance, never an artifact, so it can never become a fallback
        that serves output produced under inputs the caller did not ask for.

        Parameters
        ----------
        key
            The key that missed. Only ``key.page_hash`` and ``key.digest`` are
            used.

        Returns
        -------
        OcrCacheKey | None
            A stored key for the same page with a different digest; ``None`` when
            there is none, when ``key`` itself is stored, or on any read fault.
        """
        ...

    def equivalent_raster(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return a stored artifact for identical pixels under a different page hash.

        The one fallback this port sanctions, and it is sound because the single
        component it lets differ is the only one that does not describe the work.
        ``page_hash`` fingerprints the *bytes* the page is stored as; every tier
        below tier 0 reads the *pixels*. A stored row whose ``render_digest``,
        ``raster_digest``, ``recognizers``, ``model_fingerprint`` and
        ``request_digest`` all equal this key's was produced from the same pixels
        by the same engines under the same prompt by the same model, so serving it
        is not a stale hit: it is the same answer, reached from bytes that were
        rewritten underneath it. The matched set is exactly
        :attr:`~rmspec.domain.models.OcrCacheKey.raster_identity`, which exists so
        that no two implementations can disagree about what "the same work" means.

        Measured, not hypothetical: the tablet rewrote one page from 18,813 to
        24,534 bytes with the ink unchanged, so ``page_hash`` moved,
        ``raster_digest`` did not, and :meth:`get` missed on a page nobody had
        edited. :meth:`superseded` cannot cover it, twice over -- it matches on
        *equal* ``page_hash``, which is precisely the component that moved, and it
        returns a key rather than an artifact by design.

        A caller that serves this row must report
        :attr:`~rmspec.domain.errors.DegradationKind.CACHE_HIT_RASTER_EQUIVALENT`.
        The row it used is not the row its key names, and that is a fact about the
        run rather than an implementation detail.

        Declared on this port and not on :class:`DiagramCache`, unlike
        :meth:`superseded`: the matched set names ``recognizers``, which
        :class:`~rmspec.domain.models.DiagramCacheKey` does not have, so this
        method is not expressible over the diagram key at all and cannot be lifted
        into the one adapter class the two ports share. That class still
        implements the three methods they do share; this is the fourth, and it is
        OCR's alone.

        When several stored artifacts qualify, the one whose stored key's
        ``digest`` is greatest in lexicographic order is returned -- the same
        arbitrary-but-declared rule :meth:`superseded` follows, so that the double
        and the adapter cannot disagree.

        Parameters
        ----------
        key
            The key that missed. Every component except ``page_hash`` is matched,
            and ``page_hash`` must differ: a stored row for this very page is
            :meth:`get`'s business or :meth:`superseded`'s, never this method's.

        Returns
        -------
        OcrArtifact | None
            A stored artifact produced from identical pixels under a different
            ``page_hash``; ``None`` when there is none and ``None`` on any read
            fault, on the same totality rule as the other three methods. A
            returned artifact may have ``truncated`` set, exactly as :meth:`get`'s
            may, and the caller re-decides from the flag the same way.
        """
        ...


class DiagramCache(Protocol):
    """Memoize Mermaid diagram extraction under a fully-specified key.

    Same contract as :class:`OcrCache` -- exact ``key.digest`` lookup, no
    fallback, its three methods total -- over the diagram artifact type. Three,
    not four: ``OcrCache`` carries an ``equivalent_raster`` fallback whose matched
    set names ``recognizers``, a component :class:`~rmspec.domain.models.DiagramCacheKey`
    does not have, so the method is not expressible here. See that
    class for why the two are separate Protocols rather than one generic, why an
    unavailable cache fails at container composition instead of at a call site,
    and why ``key.raster_digest`` means a lookup only ever saves the model call and
    not the render.

    One difference, and it is a precondition on the caller rather than a
    difference in the methods: a ``DiagramArtifact`` has no ``truncated`` field,
    so nothing on the hit path can tell a Mermaid body the model cut off at its
    token limit from a complete one -- the non-empty check its validator runs
    passes either way. The truncation is only knowable while the caller still
    holds the ``VisionCompletion`` and its ``stop_reason``. So a truncated
    extraction must not be stored: :meth:`put` is called only for a completion
    that finished, and a partial diagram is reported and recomputed instead.
    :class:`OcrCache` does not need this rule because its artifact carries the
    flag and its reader can decide; here the alternative is a broken ``.mmd``
    written from a hit, with no warning, for the life of the cache.

    Notes
    -----
    Scope.APP. Adapters: ``SqliteDiagramCache`` (the same implementation as the
    OCR cache, bound to a different table), ``NullDiagramCache`` for
    ``--no-cache``, and ``InMemoryDiagramCache`` in
    ``rmspec-persistence.testing``, which takes the same ``fail_reads`` and
    ``fail_writes`` seams as its OCR sibling so the swallowed-fault clauses below
    are assertable rather than merely stated.
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
            The cached artifact, or ``None`` on a miss or any read fault. A hit is
            a complete extraction, because a truncated one is never stored.
        """
        ...

    def put(self, key: DiagramCacheKey, artifact: DiagramArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry.

        Total and idempotent: storing the same key twice is not a conflict, and a
        write fault is swallowed after the adapter logs it rather than raised, on
        the same reasoning as :meth:`OcrCache.put`.

        Parameters
        ----------
        key
            The complete cache key, stored alongside the artifact.
        artifact
            The diagram extraction to cache. It must come from a completion that
            finished; see the class docstring for why a truncated extraction has
            to be recomputed instead.
        """
        ...

    def superseded(self, key: DiagramCacheKey, /) -> DiagramCacheKey | None:
        """Return a stored key for the same page under other inputs, or ``None``.

        Identical in meaning, ordering, and totality to
        :meth:`OcrCache.superseded`: same ``page_hash``, different ``digest``,
        greatest digest wins, diagnostic only. It is declared on both caches
        because ``DegradationKind.CACHE_MISS_KEY_CHANGED`` is one member serving
        both passes, and because the three methods the two ports share are
        deliberately implemented by one adapter class over two tables -- a *shared*
        method on only one of them would make that claim false.
        :meth:`OcrCache.equivalent_raster` is the declared exception and is not
        shared: its matched set names a component this key does not have.

        Parameters
        ----------
        key
            The key that missed. Only ``key.page_hash`` and ``key.digest`` are
            used.

        Returns
        -------
        DiagramCacheKey | None
            A stored key for the same page with a different digest; ``None`` when
            there is none, when ``key`` itself is stored, or on any read fault.
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
    the entry as it landed, carrying its sequence, and :meth:`recent` returns
    those same values, newest first. Wall-clock ordering was ambiguous three
    different ways: entries written inside one pull share a timestamp, tests
    freeze the clock, and the double, a file, and SQLite each broke the tie
    differently. With a sequence, the contract test is that ``recent()`` equals
    what the appends returned, reversed, and it needs no clock at all.

    What the sequence promises: it starts at 1, it strictly increases across
    appends, and a value is never reused for the life of the store. What it does
    not promise: contiguity. Gaps are allowed, so a retention pass may drop old
    entries without renumbering, and no reader may treat ``sequence`` as a count
    of what was ever written.

    Nothing here prunes. An unbounded log is trimmed by the same persistence-local
    maintenance that prunes the caches, constructed directly by the CLI, since
    retention is not a use case; it must keep the no-reuse rule, which is why the
    SQLite adapter allocates from a monotonic counter rather than from whatever
    row identifier happens to be free.

    Notes
    -----
    Scope.APP. Adapters: ``SqliteSyncAuditLog``, which holds its own
    autocommit connection so it never contends with, or depends on, the store's
    writes; and ``InMemorySyncAuditLog`` in ``rmspec-persistence.testing``, a
    list that allocates one past the highest sequence it has ever handed out, and
    offers ``seed_unreadable`` so ``StoredRecordUnreadableError`` is reachable in
    memory. The double is testing-only -- dry-run paths get a null binding, not an
    audit log that silently discards history.
    """

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Append one entry and return it as the store recorded it.

        Returning the recorded entry rather than a bare sequence number means the
        caller can echo exactly what landed, and the contract test compares
        :meth:`recent` against these values instead of rebuilding what it expects
        the log to have made of them.

        Parameters
        ----------
        entry
            The operation to record. Failed and partial operations are recorded
            too; the outcome and its failure detail are fields of the entry.

        Returns
        -------
        RecordedSyncAuditEntry
            The appended entry with the store-assigned sequence: at least 1,
            strictly greater than every sequence handed out before it.

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

        An unreadable stored entry raises ``StoredRecordUnreadableError``, exactly
        as it does for every other reader in this module. It is not skipped: the one
        question a reader of an append-only log has to be able to answer is whether
        the history it is holding is complete, and a silently short list cannot
        answer it. Reporting the skip count through the adapter's own logger would
        put the answer in the one place the application layer cannot read, which is
        the guess-and-log that ``Degradation`` exists to replace -- and a
        ``--strict`` run would then exit 0 over a partly destroyed log. Raising
        keeps the damaged log loud and the readable prefix recoverable through a
        smaller ``limit``.

        Parameters
        ----------
        limit
            Maximum number of entries to return. Must be at least 1.

        Returns
        -------
        list[RecordedSyncAuditEntry]
            Up to ``limit`` entries, newest first, each carrying its sequence.

        Raises
        ------
        ValueError
            ``limit`` is less than 1. Checked before the store is touched, because
            a bare list slice and a store-side row limit disagree about a
            non-positive bound: one drops the newest entry, another returns the
            whole history.
        StoreUnavailableError
            The log cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed as a
            ``RecordedSyncAuditEntry``.
        """
        ...
