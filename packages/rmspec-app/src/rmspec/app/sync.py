"""Pull the tablet's library into the local mirror, or report what a pull would do.

One use case covers both, because they are one policy read twice:
:attr:`SyncDocumentsRequest.dry_run` is the old ``sync status`` -- enumerate, compare
against the mirror, report -- and a real pull is the same comparison followed by the
transfers it implies. A separate status use case would be a second implementation of the
change signal, and two implementations of a change signal is how a status command comes to
disagree with the pull it is supposed to predict.

The change signal, and why it is the whole device-side projection of the row
---------------------------------------------------------------------------
A pull is expensive and a listing is free, so the gate has to be answerable from the
listing alone. Everything :class:`~rmspec.domain.ports.device.DeviceDocument` carries is
free, so the signal is *all of it*: a document is current when the row the listing alone
would write equals the row the mirror already holds, field for field, excluding the three
fields a listing cannot answer (:data:`_UNSIGNALLED`). Comparing the whole projection
rather than one timestamp is what makes the mirror's denormalised
:attr:`~rmspec.domain.models.SyncedDocument.visible_name` and ``parent_uuid`` self-healing:
a rename is a change, so it is re-pulled, so the mirror stops lying about the name.

Two rules keep it honest. A document the transport reported no ``last_modified`` for is
never current -- absence of a timestamp is not evidence of sameness, and the cost of being
wrong in that direction is one download rather than a page of ink that never reached the
mirror. And a ``page_count`` the transport did not report cannot contribute, so it is taken
from the mirror rather than guessed; :func:`_page_count` is the one place that happens, and
it is also why a stored count is always the count the *device listed* rather than the
number of pages the bundle turned out to hold. Storing the bundle's number instead would
make a device whose metadata disagrees with its own page list re-pull on every run forever,
which is defect 2 arriving by a different road.

The comparison is pinned by a test over :class:`~rmspec.domain.models.SyncedDocument`'s
field set rather than by a comment. ``_UNSIGNALLED`` plus the signalled fields must be
exactly the model's fields, so a field added to the mirror row fails the suite instead of
being silently left out of change detection -- the loud-failure property a neighbouring
project got from asserting that its sync leaves one field constant while the real signal
moves.

Which fields track the device, and which track the human
--------------------------------------------------------
Audited, because a neighbouring project's upsert deliberately omits its ``excluded`` column
so that a routine sync cannot un-exclude a notebook the user excluded on purpose. The result
of the audit: **every field of ``SyncedDocument`` except ``synced_at`` tracks the device, and
none of them tracks the human.** ``synced_at`` is the mirror's own fact about itself.
``metadata_hash`` and ``content_hash`` are device-derived and are written as ``None`` here,
because no port hands this layer the sidecar bytes -- the transport decodes them, by design,
so that no wire format crosses the boundary -- and inventing a digest over something else
would make the columns lie.

The human-owned facts the tablet does carry -- ``pinned``, and the trash flag as a user
action -- are on :class:`~rmspec.domain.models.DocumentMetadata` and deliberately absent
from the mirror row, so this sync has nothing of the user's to clobber. That is a property
of today's model rather than a law, which is why the field-partition test is the one that
matters: adding a human-owned field to ``SyncedDocument`` fails it, and the fix is to add
the field to neither the signal nor the write.

The four defects this module is built against
---------------------------------------------
**1. Every destructive reconcile is guarded on non-empty input.** Twice, because there are
two destructive writes.

:meth:`~rmspec.domain.ports.persistence.DocumentSyncStore.record_document` *replaces* a
document's page set and discards the recorded text of every page the new set omits. So a
bundle that comes back with no pages for a document the mirror holds pages for is refused:
it is indistinguishable from a transient failure, and acting on it costs every one of that
document's pages a paid re-transcription. The document is reported ``FAILED`` with a detail
and the run goes ``PARTIAL``, which is loud every run until a human looks -- deliberately
preferred over a silent wipe, and the reason the guard is on *the recorded set being
non-empty* rather than on the bundle: a genuinely new document with no pages is recorded
with no pages, because there is nothing to lose.

:meth:`~rmspec.domain.ports.persistence.DocumentSyncStore.forget_document` is the
per-notebook prune the neighbouring project forgot to guard, and it is guarded three ways.
It does not run when the enumeration represented no documents at all, because a producer
that yields nothing cannot be told apart from an empty library. It does not run when the
listing reported *any* skipped entry, because the entry it could not represent may be
exactly the document that would be deleted. And it does not run in a dry run. When a guard
trips, the run still reports what it would have forgotten:
:attr:`SyncDocumentsResult.absent` names every stored document the enumeration did not
contain and :attr:`SyncDocumentsResult.forgotten` names the ones actually removed, so the
guard is visible on the result rather than being a silent no-op.

A per-document failure can never delete anything, structurally: the prune is computed
against the *listing*, never against "the documents this run managed to process". That is
the difference between this and the defect, where a swallowed ``stat`` failure made a
document merely absent from the producer's output and the prune read absence as deletion.

**2. The change signal is recorded on inspection, not on work performed.** A rename or a
metadata-only edit moves the signal and changes no page hash. The neighbouring project
wrote its manifest version only after a successful page extraction, so its loop skipped the
write and re-downloaded, re-unzipped and re-rendered that document on every subsequent run,
forever. Here the mirror row is rewritten whenever the document was pulled, whether or not
any page's hash moved, and :attr:`SyncedDocumentOutcome.pages_changed` reports the zero
rather than branching on it. The regression test is two consecutive syncs: the second
performs no transfer at all.

**3. The signal is pinned by a test**, as described above.

**4. A sync never writes a field the human owns**, as audited above.

Whole-transport failure is not per-document failure
---------------------------------------------------
A pull over four hundred documents may not die at the third, so the per-document failures in
:data:`_PER_DOCUMENT_FAILURES` are caught, reported on that document, appended to history,
and the loop continues. ``DeviceUnreachable``, ``DeviceAuthFailed``, ``DeviceProtocolError``
and ``StoreUnavailableError`` are *not* in that tuple and propagate, because folding them in
would reproduce the defect already fixed in ``rmspec-device``, where a dead cable and a
per-path read error were the same error class and a mid-listing disconnect became forty
skipped entries with exit status success.

Two behaviours worth stating before they surprise someone
---------------------------------------------------------
**A trashed document is neither synced nor forgotten.** The trash is not the library, so
there is nothing to pull; but its mirror rows stay, so restoring it on the tablet does not
re-bill every page's transcription. It is reported ``SKIPPED`` with a detail rather than
being silently absent from the result.

Over USB that path is unreachable and its consequence is not: firmware 3.27.3.0's
``GET /documents/`` omits trashed entries at every depth, so a document the user trashes
simply disappears from the enumeration and *is* forgotten, text included. No port can tell
"absent because trashed" from "absent because deleted" over that transport, so this is
recorded rather than defended against -- and it is one more reason the prune is guarded by
everything else it is guarded by.

**Only work is audited.** One entry per document pulled, one per document that failed, one
per document forgotten. A document that was already current is not audited, because it was
only read, and ``SyncOperation``'s own docstring rules reads out: "a history that records
reads is a history nobody reads". Entries are appended as the run proceeds rather than
summarised at the end, so a pull that dies at document four hundred still has the history of
the three hundred and ninety-nine that landed. An
:class:`~rmspec.domain.errors.AuditWriteFailedError` is a
``DegradationKind.AUDIT_NOT_RECORDED`` degradation: a successful operation whose history
could not be recorded is a degradation, not a failure, and it is never retried as one.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

from pydantic import AwareDatetime, BaseModel, Field

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import (
    AuditWriteFailedError,
    Degradation,
    DegradationKind,
    DeviceDocumentNotFound,
    DeviceTransferInterrupted,
    MalformedDeviceMetadata,
    StoredRecordUnreadableError,
)
from rmspec.domain.models import (
    DocumentKind,
    SourceKind,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
)

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

    from rmspec.domain.ports.device import (
        DeviceCatalog,
        DeviceDocument,
        DeviceListing,
        DocumentSourceBundle,
        RawBundleSource,
    )
    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog

__all__ = [
    "SyncDocuments",
    "SyncDocumentsRequest",
    "SyncDocumentsResult",
    "SyncedDocumentOutcome",
]

_UNIDENTIFIED: Final = "<entry with no recoverable identifier>"
"""Subject for a skipped entry whose ``uuid`` the transport could not recover."""

_UNSIGNALLED: Final = frozenset({"content_hash", "metadata_hash", "synced_at"})
"""Fields of :class:`SyncedDocument` that take no part in change detection.

``synced_at`` is a fact about the mirror rather than about the document, and the two hashes
are device-derived facts no port hands this layer -- so all three would either always
differ or always be ``None``, and neither is a signal. Everything else the model carries is
compared. The partition is asserted by the test suite, so a new field cannot quietly join
this set by being forgotten.
"""

_PER_DOCUMENT_FAILURES: Final = (
    DeviceDocumentNotFound,
    DeviceTransferInterrupted,
    MalformedDeviceMetadata,
    StoredRecordUnreadableError,
)
"""Failures that concern one document and must not end the run.

Every one of them names a document. A dead transport, refused credentials, an
uninterpretable answer and an unavailable store are deliberately absent: they say nothing
about a document and everything about the run, so they propagate.
"""


class SyncDocumentsRequest(BaseModel, frozen=True, extra="forbid"):
    """One pull of the whole library, or one report about what a pull would do."""

    synced_at: AwareDatetime
    """The instant every row and every history entry this run writes is stamped with.

    Supplied by the caller rather than read from a clock, so this package holds no clock and
    no test has to freeze one. One instant for the whole run on purpose: every row a single
    pull wrote is then identifiable as that pull's work, which is also why the audit log
    orders by its own sequence rather than by this value.
    """

    dry_run: bool = False
    """Report what would happen and write nothing.

    Nothing at all: no bundle is fetched, so a status is a listing plus one store read and
    costs no transfer; no row is written; no document is forgotten; and no history is
    appended, because nothing happened.
    """


class SyncedDocumentOutcome(BaseModel, frozen=True, extra="forbid"):
    """What this run did about one document, and what it found.

    Carried on the result rather than summarised into counters, because "which document
    failed" is the first question a partial pull raises and a count cannot answer it.
    """

    uuid: str = Field(min_length=1)
    """The document's identifier on the tablet."""

    visible_name: str
    """The document's name as the listing reported it. Empty is legal on the tablet."""

    outcome: SyncOutcome
    """What happened to this document.

    ``SUCCEEDED`` when the mirror was updated, ``SKIPPED`` when nothing needed doing or the
    run was a dry one, ``FAILED`` when this document could not be mirrored -- including the
    refusal to replace a recorded page set with an empty one.
    """

    changed: bool
    """Whether the change signal differs from the mirror's row.

    The field a dry run exists to report: ``changed`` documents are the ones a pull would
    fetch. ``True`` for a document the mirror has never seen.
    """

    pages_recorded: int = Field(ge=0)
    """How many pages were written to the mirror. Zero in a dry run and on a failure."""

    pages_changed: int = Field(ge=0)
    """How many recorded pages' fingerprints moved, including pages the mirror had none for.

    Zero alongside a ``SUCCEEDED`` outcome is the metadata-only edit: the document was
    re-pulled because its signal moved, no page's ink changed, and the row was rewritten
    anyway so the next run skips it.
    """

    detail: str
    """Why, for a human reading the report. Empty when the outcome speaks for itself."""


class SyncDocumentsResult(BaseModel, frozen=True, extra="forbid"):
    """Everything one sync did, per document, plus what it refused to do.

    No field has a default, so a construction site cannot omit the prune it did not perform.
    """

    outcome: SyncOutcome
    """The run's own outcome.

    ``SKIPPED`` for a dry run and for a run where nothing needed doing, ``SUCCEEDED`` when
    every document that needed work got it, ``FAILED`` when every document failed, and
    ``PARTIAL`` when some did.
    """

    dry_run: bool
    """Whether this run wrote anything at all. Echoed so a report cannot be misread."""

    documents: tuple[SyncedDocumentOutcome, ...]
    """One entry per document the listing represented, in listing order.

    Trashed documents included, as ``SKIPPED``: a document that was deliberately not
    synced must be visible as such rather than merely missing from this tuple.
    """

    absent: tuple[str, ...]
    """Uuids of mirrored documents the enumeration did not contain, in store order.

    What a prune *would* remove. Folder rows are never here: this use case does not create
    them and must not delete them.
    """

    forgotten: tuple[str, ...]
    """Uuids actually removed from the mirror. A subset of :attr:`absent`.

    Empty in a dry run, and empty whenever a prune guard tripped -- which is how a refused
    prune is visible instead of silent. ``absent`` longer than ``forgotten`` means the
    enumeration was not trusted enough to delete from.
    """

    degradations: tuple[Degradation, ...]
    """Every substitution this run made instead of failing.

    ``CATALOG_ENTRY_SKIPPED`` once per entry the listing could not represent and once per
    document whose recorded pages this run refused to replace with an empty set;
    ``AUDIT_NOT_RECORDED`` once per history entry that did not land.
    """


def _page_count(document: DeviceDocument, /, *, fallback: int) -> int:
    """Return the page count to record for, and compare against, this document.

    Parameters
    ----------
    document
        The document as the listing reported it.
    fallback
        The count to use when the transport reported none -- the mirror's own count when
        comparing, and the bundle's page count when recording a document for the first time.

    Returns
    -------
    int
        The device's recorded count when it reported one, else ``fallback``. An unreported
        count therefore never contributes to change detection instead of being guessed, and
        a stored count is always the count the device *listed*, so a device whose metadata
        disagrees with its own page list cannot re-pull forever.
    """
    return fallback if document.page_count is None else document.page_count


def _row(document: DeviceDocument, /, *, page_count: int, at: datetime.datetime) -> SyncedDocument:
    """Build the mirror row this document's listing entry describes.

    One builder for both jobs -- the row to write and the row to compare against -- so the
    comparison cannot drift from the write. ``metadata_hash`` and ``content_hash`` are left
    ``None`` because no port hands this layer the sidecar bytes to hash.

    Parameters
    ----------
    document
        The document as the listing reported it.
    page_count
        The count to record; see :func:`_page_count`.
    at
        The run's instant.

    Returns
    -------
    SyncedDocument
        The row, with ``source`` mapped from the device's file type -- the two enums share
        their wire spellings, so the mapping is total and needs no fallback.
    """
    return SyncedDocument(
        uuid=document.uuid,
        visible_name=document.name,
        kind=DocumentKind.DOCUMENT,
        source=SourceKind(document.file_type.value),
        parent_uuid=document.parent_uuid,
        page_count=page_count,
        device_last_modified=document.last_modified,
        synced_at=at,
    )


def _is_current(document: DeviceDocument, stored: SyncedDocument | None, /) -> bool:
    """Report whether the mirror already holds this document as the device now describes it.

    Parameters
    ----------
    document
        The document as the listing reported it.
    stored
        The mirror's row, or ``None`` when the document is untracked.

    Returns
    -------
    bool
        ``True`` only when a row exists, the transport reported a modification time, and
        every signalled field of the projected row equals the stored one. An untracked
        document and a document with no reported timestamp are both "not current", because
        neither licenses the claim that a download would find nothing new.
    """
    if stored is None or document.last_modified is None:
        return False
    projected = _row(
        document,
        page_count=_page_count(document, fallback=stored.page_count),
        at=stored.synced_at,
    )
    unsignalled = set(_UNSIGNALLED)
    return projected.model_dump(exclude=unsignalled) == stored.model_dump(exclude=unsignalled)


def _page_rows(
    bundle: DocumentSourceBundle, /, *, at: datetime.datetime
) -> tuple[SyncedPage, ...]:
    """Fingerprint every page of a fetched bundle, in the order the device recorded.

    Parameters
    ----------
    bundle
        The fetched document.
    at
        The run's instant.

    Returns
    -------
    tuple[SyncedPage, ...]
        One row per page. ``rm_hash`` is the plain lowercase hex SHA-256 of the page's scene
        bytes -- plain, and not the domain's framed digest, because this value must equal
        what ``DocumentRepository.page_fingerprint`` produces for the same bytes and what
        ``OcrCacheKey.page_hash`` is compared against. A page with no scene artifact carries
        no hash and no size, which is the honest reading of an unannotated PDF page.
    """
    return tuple(
        SyncedPage(
            doc_uuid=bundle.document.uuid,
            page_uuid=page.page_id,
            page_index=index,
            rm_hash=None if page.scene is None else hashlib.sha256(page.scene).hexdigest(),
            rm_size_bytes=None if page.scene is None else len(page.scene),
            synced_at=at,
        )
        for index, page in enumerate(bundle.pages)
    )


def _changed_pages(recorded: Sequence[SyncedPage], current: Sequence[SyncedPage], /) -> int:
    """Count the pages whose fingerprint the mirror does not already hold.

    Parameters
    ----------
    recorded
        The mirror's pages for this document, before the write.
    current
        The pages just fingerprinted from the device.

    Returns
    -------
    int
        How many current pages are new to the mirror or carry a different fingerprint.
        Membership is checked separately from equality, so a brand-new page whose scene is
        absent -- fingerprint ``None`` -- counts as changed rather than matching the ``None``
        a missing row would otherwise produce.
    """
    was = {page.page_uuid: page.rm_hash for page in recorded}
    return sum(
        1 for page in current if page.page_uuid not in was or was[page.page_uuid] != page.rm_hash
    )


def _run_outcome(documents: Sequence[SyncedDocumentOutcome], /, *, dry_run: bool) -> SyncOutcome:
    """Fold the per-document outcomes into the run's own.

    Parameters
    ----------
    documents
        One outcome per document the listing represented.
    dry_run
        Whether this run was forbidden to write.

    Returns
    -------
    SyncOutcome
        ``SKIPPED`` for a dry run, which by construction landed nothing. Otherwise
        ``FAILED`` only when every document failed, ``PARTIAL`` when some did, ``SUCCEEDED``
        when work landed and nothing failed, and ``SKIPPED`` when nothing needed doing.
    """
    if dry_run:
        return SyncOutcome.SKIPPED
    failed = sum(1 for entry in documents if entry.outcome is SyncOutcome.FAILED)
    landed = sum(1 for entry in documents if entry.outcome is SyncOutcome.SUCCEEDED)
    if not failed:
        return SyncOutcome.SUCCEEDED if landed else SyncOutcome.SKIPPED
    return SyncOutcome.FAILED if failed == len(documents) else SyncOutcome.PARTIAL


def _prunable(listing: DeviceListing, /, *, dry_run: bool) -> bool:
    """Report whether this enumeration may be used to delete from the mirror.

    Parameters
    ----------
    listing
        The enumeration this run compared against.
    dry_run
        Whether this run was forbidden to write.

    Returns
    -------
    bool
        ``False`` for a dry run, for an enumeration that represented no documents at all,
        and for one that reported any skipped entry. An empty producer is
        indistinguishable from an empty library, and an entry that could not be represented
        may be the very document a prune would delete.
    """
    return not dry_run and bool(listing.documents) and not listing.skipped


class SyncDocuments:
    """Bring the local mirror up to date with the attached tablet, or say what that needs.

    Four collaborators, all Protocols: the catalog that enumerates, the bundle source that
    fetches, the mirror that is written, and the audit log that records the writes.

    Notes
    -----
    A status is the same call with one flag, and it costs no transfer::

        report = syncer.sync(SyncDocumentsRequest(synced_at=now, dry_run=True))
        stale = [entry.uuid for entry in report.documents if entry.changed]
    """

    def __init__(
        self,
        *,
        catalog: DeviceCatalog,
        bundles: RawBundleSource,
        store: DocumentSyncStore,
        audit: SyncAuditLog,
    ) -> None:
        self._catalog = catalog
        self._bundles = bundles
        self._store = store
        self._audit = audit

    def sync(self, request: SyncDocumentsRequest, /) -> SyncDocumentsResult:
        """Mirror every document the tablet holds, or report what mirroring would do.

        Parameters
        ----------
        request
            The run's instant, and whether it may write.

        Returns
        -------
        SyncDocumentsResult
            One outcome per listed document, what a prune would remove, what it did remove,
            and every substitution made.

        Raises
        ------
        DeviceUnreachable
            The tablet stopped answering. Never degraded into an empty library, and never
            folded into a per-document failure -- see this module's docstring.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something the transport cannot interpret.
        StoreUnavailableError
            The mirror could not be read or written.
        """
        listing = self._catalog.list_documents()
        log = DegradationLog()
        for entry in listing.skipped:
            log.record(
                Degradation(
                    kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
                    subject=entry.uuid or _UNIDENTIFIED,
                    detail=(
                        f"{entry.reason.value}: {entry.detail}; it is neither synced nor "
                        f"eligible to be forgotten, and no document is pruned this run"
                    ),
                )
            )
        mirrored = {row.uuid: row for row in self._store.list_documents()}
        documents = tuple(
            self._inspect(document, mirrored.get(document.uuid), request, log)
            for document in listing.documents
        )
        known = frozenset(document.uuid for document in listing.documents)
        absent = tuple(
            row
            for row in mirrored.values()
            if row.kind is DocumentKind.DOCUMENT and row.uuid not in known
        )
        forgotten = self._forget(absent, listing, request, log)
        return SyncDocumentsResult(
            outcome=_run_outcome(documents, dry_run=request.dry_run),
            dry_run=request.dry_run,
            documents=documents,
            absent=tuple(row.uuid for row in absent),
            forgotten=forgotten,
            degradations=log.frozen(),
        )

    def _inspect(
        self,
        document: DeviceDocument,
        stored: SyncedDocument | None,
        request: SyncDocumentsRequest,
        log: DegradationLog,
        /,
    ) -> SyncedDocumentOutcome:
        """Decide what one document needs, and do it unless this run may not write.

        Parameters
        ----------
        document
            The document as the listing reported it.
        stored
            The mirror's row, or ``None`` when the document is untracked.
        request
            The run's instant and its dry-run flag.
        log
            The accumulator every substitution is reported through.

        Returns
        -------
        SyncedDocumentOutcome
            What happened, and what the comparison found. A failure that concerns only this
            document is caught here and reported; anything that concerns the run propagates.
        """
        if document.trashed:
            return _outcome(
                document,
                SyncOutcome.SKIPPED,
                changed=False,
                detail=(
                    "in the tablet's trash, so it is not synced; its recorded rows are kept "
                    "so restoring it does not re-bill every page"
                ),
            )
        if _is_current(document, stored):
            return _outcome(document, SyncOutcome.SKIPPED, changed=False, detail="already current")
        if request.dry_run:
            return _outcome(
                document,
                SyncOutcome.SKIPPED,
                changed=True,
                detail="a pull would fetch this document",
            )
        try:
            return self._pull(document, request, log)
        except _PER_DOCUMENT_FAILURES as failure:
            self._record(
                SyncOutcome.FAILED,
                document,
                request,
                log,
                pages=0,
                detail=f"{failure.code}: {failure.message}",
            )
            return _outcome(
                document,
                SyncOutcome.FAILED,
                changed=True,
                detail=f"{failure.code}: {failure.message}",
            )

    def _pull(
        self,
        document: DeviceDocument,
        request: SyncDocumentsRequest,
        log: DegradationLog,
        /,
    ) -> SyncedDocumentOutcome:
        """Fetch one document and record it, unless recording it would destroy more than it saves.

        Parameters
        ----------
        document
            The document as the listing reported it.
        request
            The run's instant.
        log
            The accumulator the refused replacement is reported through.

        Returns
        -------
        SyncedDocumentOutcome
            ``SUCCEEDED`` with the page counts, or ``FAILED`` when the fetched bundle held
            no pages for a document the mirror holds pages for.
        """
        recorded = self._store.pages(document.uuid)
        bundle = self._bundles.load_bundle(document.uuid)
        pages = _page_rows(bundle, at=request.synced_at)
        if not pages and recorded:
            detail = (
                f"the device reported no pages while the mirror holds {len(recorded)}; "
                f"recording that would replace the page set and discard every page's "
                f"recorded text, so nothing was written"
            )
            log.record(
                Degradation(
                    kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
                    subject=document.uuid,
                    detail=detail,
                )
            )
            self._record(SyncOutcome.FAILED, document, request, log, pages=0, detail=detail)
            return _outcome(document, SyncOutcome.FAILED, changed=True, detail=detail)
        self._store.record_document(
            _row(
                document,
                page_count=_page_count(document, fallback=len(pages)),
                at=request.synced_at,
            ),
            pages,
        )
        changed = _changed_pages(recorded, pages)
        self._record(
            SyncOutcome.SUCCEEDED,
            document,
            request,
            log,
            pages=len(pages),
            detail="",
        )
        return _outcome(
            document,
            SyncOutcome.SUCCEEDED,
            changed=True,
            pages_recorded=len(pages),
            pages_changed=changed,
        )

    def _forget(
        self,
        absent: tuple[SyncedDocument, ...],
        listing: DeviceListing,
        request: SyncDocumentsRequest,
        log: DegradationLog,
        /,
    ) -> tuple[str, ...]:
        """Remove the mirrored documents the tablet no longer has, when that is safe.

        Parameters
        ----------
        absent
            The mirror's rows the enumeration did not contain.
        listing
            The enumeration, whose completeness decides whether it may be pruned against.
        request
            The run's instant and its dry-run flag.
        log
            The accumulator an unrecorded history is reported through.

        Returns
        -------
        tuple[str, ...]
            The uuids actually forgotten, which is empty whenever :func:`_prunable` refused.
            The caller reports it alongside ``absent`` so the refusal is visible.
        """
        if not _prunable(listing, dry_run=request.dry_run):
            return ()
        for row in absent:
            self._store.forget_document(row.uuid)
            self._append(
                SyncAuditEntry(
                    operation=SyncOperation.PULL,
                    outcome=SyncOutcome.SUCCEEDED,
                    doc_uuid=row.uuid,
                    doc_name=row.visible_name,
                    detail="forgotten: absent from a complete device listing",
                    occurred_at=request.synced_at,
                ),
                log,
            )
        return tuple(row.uuid for row in absent)

    def _record(
        self,
        outcome: SyncOutcome,
        document: DeviceDocument,
        request: SyncDocumentsRequest,
        log: DegradationLog,
        /,
        *,
        pages: int,
        detail: str,
    ) -> None:
        """Append the history entry for work performed on one document.

        Parameters
        ----------
        outcome
            How the document ended.
        document
            The document as the listing reported it.
        request
            The run's instant.
        log
            The accumulator an unrecorded history is reported through.
        pages
            How many pages were written.
        detail
            Why, which the domain requires for an unhappy outcome.
        """
        self._append(
            SyncAuditEntry(
                operation=SyncOperation.PULL,
                outcome=outcome,
                doc_uuid=document.uuid,
                doc_name=document.name,
                pages_affected=pages,
                detail=detail,
                occurred_at=request.synced_at,
            ),
            log,
        )

    def _append(self, entry: SyncAuditEntry, log: DegradationLog, /) -> None:
        """Append one history entry, degrading rather than failing when it does not land.

        Parameters
        ----------
        entry
            The entry to append.
        log
            The accumulator the failure is reported through.
        """
        try:
            self._audit.append(entry)
        except AuditWriteFailedError as failure:
            log.record(
                Degradation(
                    kind=DegradationKind.AUDIT_NOT_RECORDED,
                    subject=entry.doc_uuid or entry.doc_name,
                    detail=(
                        f"the mirror was updated and this history entry did not land: "
                        f"{failure.detail}"
                    ),
                )
            )


def _outcome(
    document: DeviceDocument,
    outcome: SyncOutcome,
    /,
    *,
    changed: bool,
    pages_recorded: int = 0,
    pages_changed: int = 0,
    detail: str = "",
) -> SyncedDocumentOutcome:
    """Build one per-document report.

    A free function rather than a method: it reads the listing entry and nothing else, and
    keeping it out of the class is what stops a reader looking for state it does not have.

    Parameters
    ----------
    document
        The document as the listing reported it.
    outcome
        What happened to it.
    changed
        Whether the change signal differed from the mirror's row.
    pages_recorded
        How many pages were written to the mirror.
    pages_changed
        How many fingerprints moved.
    detail
        Why, for a human reading the report.

    Returns
    -------
    SyncedDocumentOutcome
        The report, carrying the document's identity and name as the listing gave them.
    """
    return SyncedDocumentOutcome(
        uuid=document.uuid,
        visible_name=document.name,
        outcome=outcome,
        changed=changed,
        pages_recorded=pages_recorded,
        pages_changed=pages_changed,
        detail=detail,
    )
