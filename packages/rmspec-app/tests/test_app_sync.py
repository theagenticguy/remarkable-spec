"""The reconcile guards, the pinned change signal, and the transport-versus-document split.

How the four ports are bound here, and why
------------------------------------------
With local in-memory fakes annotated against the Protocols, exactly as
``test_app_resolve.py`` binds ``DeviceCatalog``: ``rmspec.app`` may import ``rmspec.domain``
and nothing else and these tests hold themselves to the rule their source obeys; the
architecture check only scans ``src/``, so an adapter import here would pass the gate while
breaking the property the gate exists to protect; and conformance is checked by the type
gate, because every fake is passed to a Protocol-annotated keyword argument.

``_InMemoryStore`` is the one fake with real behaviour rather than canned answers, and it has
to be: the whole subject of this module is a *destructive* write, so a double that merely
recorded calls could not show that recorded pages and their recorded text survive a refused
replacement. It therefore implements the three parts of
:meth:`DocumentSyncStore.record_document`'s contract that matter here -- the page set is
replaced, text for a departed page is discarded, and a page naming another document is
refused -- plus an ``unreadable`` seam so ``StoredRecordUnreadableError`` is reachable
without deleting fixtures mid-test.

Every "would this defect reproduce" test is written so that it fails if the guard it names
is removed: the wipe tests assert the surviving rows, and the re-download test asserts the
transfer count rather than the outcome.
"""

from __future__ import annotations

import datetime
import hashlib
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from rmspec.app.sync import _UNSIGNALLED as UNSIGNALLED
from rmspec.app.sync import (
    SyncDocuments,
    SyncDocumentsRequest,
    SyncDocumentsResult,
    SyncedDocumentOutcome,
)
from rmspec.domain.errors import (
    AuditWriteFailedError,
    DegradationKind,
    DeviceAuthFailed,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    MalformedDeviceMetadata,
    RmspecError,
    StoredRecordUnreadableError,
    StoreUnavailableError,
    TransportKind,
)
from rmspec.domain.models import (
    DocumentKind,
    PageText,
    RecordedSyncAuditEntry,
    SourceKind,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
    TextProvenance,
)
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DeviceFolder,
    DeviceListing,
    DevicePageSource,
    DocumentSourceBundle,
    RawBundleSource,
    SkippedEntry,
    SkipReason,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog

ALPHA = "aaaaaaaa-1111-4111-8111-111111111111"
BETA = "bbbbbbbb-2222-4222-8222-222222222222"
GAMMA = "cccccccc-3333-4333-8333-333333333333"
FOLDER = "ffffffff-4444-4444-8444-444444444444"

EARLY = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
LATE = datetime.datetime(2026, 8, 29, tzinfo=datetime.UTC)

RUN_ONE = datetime.datetime(2026, 8, 29, 9, 0, tzinfo=datetime.UTC)
RUN_TWO = datetime.datetime(2026, 8, 29, 10, 0, tzinfo=datetime.UTC)

INK = b"v6 scene bytes for one page"
MORE_INK = b"v6 scene bytes after the user wrote another line"

SIGNALLED = frozenset(
    {
        "uuid",
        "visible_name",
        "kind",
        "source",
        "parent_uuid",
        "page_count",
        "device_last_modified",
    }
)
"""Every field of ``SyncedDocument`` the change signal compares, spelled out here.

The partition test below asserts that this set and ``_UNSIGNALLED`` together are exactly the
model's fields, which is what makes a field added to the mirror row fail loudly rather than
be silently left out of change detection.
"""


class _InMemoryCatalog:
    """A :class:`DeviceCatalog` over one listing, with a transport that can be told to die."""

    def __init__(self, listing: DeviceListing, *, failure: RmspecError | None = None) -> None:
        self.calls = 0
        self._listing = listing
        self._failure = failure

    def list_documents(self) -> DeviceListing:
        """Return the whole library, trashed entries included, or die as a transport does."""
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        return self._listing

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Look one document up, which this use case never does."""
        for document in self._listing.documents:
            if document.uuid == doc_uuid:
                return document
        raise MalformedDeviceMetadata(
            transport=TransportKind.SSH, document_uuid=doc_uuid, detail="not in this listing"
        )


class _InMemoryBundles:
    """A :class:`RawBundleSource` over prebuilt bundles, counting every transfer."""

    def __init__(
        self,
        bundles: dict[str, DocumentSourceBundle],
        *,
        failures: dict[str, RmspecError] | None = None,
    ) -> None:
        self.loads: list[str] = []
        self._bundles = bundles
        self._failures = failures or {}

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Hand over one document's source, or fail the way one document fails."""
        self.loads.append(doc_uuid)
        failure = self._failures.get(doc_uuid)
        if failure is not None:
            raise failure
        return self._bundles[doc_uuid]


class _InMemoryStore:
    """A :class:`DocumentSyncStore` that really replaces page sets, so a wipe is observable."""

    def __init__(self, *, unreadable: frozenset[str] = frozenset()) -> None:
        self.recorded: list[str] = []
        self.forgotten: list[str] = []
        self._documents: dict[str, SyncedDocument] = {}
        self._pages: dict[str, list[SyncedPage]] = {}
        self._texts: dict[str, dict[str, PageText]] = {}
        self._unreadable = unreadable

    def record_document(self, document: SyncedDocument, pages: Sequence[SyncedPage], /) -> None:
        """Replace the document and its whole page set, discarding departed pages' text."""
        misfiled = [page.page_uuid for page in pages if page.doc_uuid != document.uuid]
        if misfiled:
            msg = f"pages {misfiled} do not belong to {document.uuid}"
            raise ValueError(msg)
        self.recorded.append(document.uuid)
        self._documents[document.uuid] = document
        self._pages[document.uuid] = list(pages)
        surviving = {page.page_uuid for page in pages}
        kept = self._texts.get(document.uuid, {})
        self._texts[document.uuid] = {
            page_uuid: text for page_uuid, text in kept.items() if page_uuid in surviving
        }

    def get_document(self, doc_uuid: str, /) -> SyncedDocument | None:
        """Return the recorded document, or ``None`` when it is untracked."""
        return self._documents.get(doc_uuid)

    def list_documents(self) -> list[SyncedDocument]:
        """Return every recorded document in the port's declared order."""
        return sorted(
            self._documents.values(), key=lambda row: (row.visible_name.casefold(), row.uuid)
        )

    def pages(self, doc_uuid: str, /) -> list[SyncedPage]:
        """Return the recorded pages in page-index order, or report an unreadable payload."""
        if doc_uuid in self._unreadable:
            raise StoredRecordUnreadableError(
                store="mirror", table="pages", key=doc_uuid, detail="page_index is null"
            )
        return sorted(
            self._pages.get(doc_uuid, []), key=lambda page: (page.page_index, page.page_uuid)
        )

    def forget_document(self, doc_uuid: str, /) -> None:
        """Forget a document, its pages and its text. A no-op when untracked."""
        self.forgotten.append(doc_uuid)
        self._documents.pop(doc_uuid, None)
        self._pages.pop(doc_uuid, None)
        self._texts.pop(doc_uuid, None)

    def record_page_text(self, page_text: PageText, /) -> None:
        """Record text for a page the document still has, and drop it otherwise."""
        known = {page.page_uuid for page in self._pages.get(page_text.doc_uuid, [])}
        if page_text.page_uuid in known:
            self._texts.setdefault(page_text.doc_uuid, {})[page_text.page_uuid] = page_text

    def page_texts(self, doc_uuid: str, /) -> list[PageText]:
        """Return recorded text for one document in page-index order."""
        found = self._texts.get(doc_uuid, {}).values()
        return sorted(found, key=lambda text: (text.page_index, text.page_uuid))

    def all_page_texts(self) -> list[PageText]:
        """Return recorded text for every tracked document in the port's declared order."""
        return sorted(
            (text for texts in self._texts.values() for text in texts.values()),
            key=lambda text: (text.doc_uuid, text.page_index, text.page_uuid),
        )


class _RecordingAuditLog:
    """A :class:`SyncAuditLog` that either records or reports that it could not."""

    def __init__(self, *, failure: AuditWriteFailedError | None = None) -> None:
        self.entries: list[RecordedSyncAuditEntry] = []
        self._failure = failure

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Append one entry, or report that it did not land."""
        if self._failure is not None:
            raise self._failure
        recorded = RecordedSyncAuditEntry(sequence=len(self.entries) + 1, entry=entry)
        self.entries.append(recorded)
        return recorded

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the newest entries first, which this use case never reads."""
        if limit < 1:
            msg = "limit must be at least 1"
            raise ValueError(msg)
        return list(reversed(self.entries))[:limit]


def _doc(
    uuid: str = ALPHA,
    name: str = "Notes",
    *,
    file_type: DeviceFileType = DeviceFileType.NOTEBOOK,
    parent_uuid: str | None = None,
    last_modified: datetime.datetime | None = EARLY,
    page_count: int | None = 1,
    trashed: bool = False,
) -> DeviceDocument:
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=file_type,
        parent_uuid=parent_uuid,
        last_modified=last_modified,
        page_count=page_count,
        trashed=trashed,
    )


def _listing(
    *documents: DeviceDocument,
    skipped: tuple[SkippedEntry, ...] = (),
    folders: tuple[DeviceFolder, ...] = (),
) -> DeviceListing:
    return DeviceListing(documents=documents, folders=folders, skipped=skipped)


def _bundle(
    document: DeviceDocument,
    *pages: tuple[str, bytes | None],
    base: bytes | None = None,
) -> DocumentSourceBundle:
    return DocumentSourceBundle(
        document=document,
        pages=tuple(DevicePageSource(page_id=page_id, scene=scene) for page_id, scene in pages),
        base=base,
    )


def _syncer(
    listing: DeviceListing,
    bundles: dict[str, DocumentSourceBundle],
    *,
    store: _InMemoryStore | None = None,
    audit: _RecordingAuditLog | None = None,
    catalog: _InMemoryCatalog | None = None,
    source: _InMemoryBundles | None = None,
) -> tuple[SyncDocuments, _InMemoryCatalog, _InMemoryBundles, _InMemoryStore, _RecordingAuditLog]:
    """Wire one use case over the four fakes and hand every one of them back."""
    catalog = catalog or _InMemoryCatalog(listing)
    source = source or _InMemoryBundles(bundles)
    store = store or _InMemoryStore()
    audit = audit or _RecordingAuditLog()
    return (
        SyncDocuments(catalog=catalog, bundles=source, store=store, audit=audit),
        catalog,
        source,
        store,
        audit,
    )


def _hash_of(scene: bytes) -> str:
    """Return the fingerprint the use case will compute for these scene bytes."""
    return hashlib.sha256(scene).hexdigest()


def _text(doc_uuid: str, page_uuid: str, body: str = "handwritten words") -> PageText:
    return PageText(
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        page_index=0,
        text=body,
        provenance=TextProvenance(recognizers=("apple-vision@1",), extracted_at=EARLY),
    )


def _mirror_row(
    uuid: str = ALPHA,
    name: str = "Notes",
    *,
    kind: DocumentKind = DocumentKind.DOCUMENT,
    source: SourceKind = SourceKind.NOTEBOOK,
    parent_uuid: str | None = None,
    page_count: int = 1,
    device_last_modified: datetime.datetime | None = EARLY,
) -> SyncedDocument:
    return SyncedDocument(
        uuid=uuid,
        visible_name=name,
        kind=kind,
        source=source,
        parent_uuid=parent_uuid,
        page_count=page_count,
        device_last_modified=device_last_modified,
        synced_at=RUN_ONE,
    )


def _mirror_page(
    doc_uuid: str = ALPHA,
    page_uuid: str = "page-a",
    *,
    index: int = 0,
    rm_hash: str | None = "0" * 64,
) -> SyncedPage:
    return SyncedPage(
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        page_index=index,
        rm_hash=rm_hash,
        rm_size_bytes=None if rm_hash is None else 10,
        synced_at=RUN_ONE,
    )


def _pull(at: datetime.datetime = RUN_ONE) -> SyncDocumentsRequest:
    return SyncDocumentsRequest(synced_at=at)


def _status(at: datetime.datetime = RUN_ONE) -> SyncDocumentsRequest:
    return SyncDocumentsRequest(synced_at=at, dry_run=True)


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error."""
    setattr(target, field, value)


# ───────────────────────────── the pull itself ─────────────────────────────


def test_an_untracked_document_is_pulled_and_recorded():
    document = _doc(page_count=2)
    syncer, _, source, store, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK), ("page-b", None))}
    )
    result = syncer.sync(_pull())
    assert result.outcome is SyncOutcome.SUCCEEDED
    assert source.loads == [ALPHA]
    (entry,) = result.documents
    assert entry.outcome is SyncOutcome.SUCCEEDED
    assert entry.changed is True
    assert entry.pages_recorded == 2
    assert entry.pages_changed == 2
    assert store.get_document(ALPHA) is not None
    assert [page.page_uuid for page in store.pages(ALPHA)] == ["page-a", "page-b"]


def test_a_page_is_fingerprinted_with_the_plain_sha256_of_its_scene_bytes():
    """The value must equal what ``page_fingerprint`` produces, so it is not a framed digest."""
    document = _doc()
    syncer, _, _, store, _ = _syncer(_listing(document), {ALPHA: _bundle(document, ("p", INK))})
    syncer.sync(_pull())
    (page,) = store.pages(ALPHA)
    assert page.rm_hash == hashlib.sha256(INK).hexdigest()
    assert page.rm_size_bytes == len(INK)


def test_a_page_with_no_scene_artifact_carries_no_fingerprint_and_no_size():
    document = _doc(file_type=DeviceFileType.PDF)
    syncer, _, _, store, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("p", None), base=b"%PDF-1.7")}
    )
    syncer.sync(_pull())
    (page,) = store.pages(ALPHA)
    assert page.rm_hash is None
    assert page.rm_size_bytes is None


def test_the_recorded_row_projects_the_device_facts():
    document = _doc(
        name="Q3 plan",
        file_type=DeviceFileType.PDF,
        parent_uuid=FOLDER,
        last_modified=LATE,
        page_count=1,
    )
    syncer, _, _, store, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("p", INK), base=b"%PDF-1.7")}
    )
    syncer.sync(_pull())
    row = store.get_document(ALPHA)
    assert row is not None
    assert row.visible_name == "Q3 plan"
    assert row.kind is DocumentKind.DOCUMENT
    assert row.source is SourceKind.PDF
    assert row.parent_uuid == FOLDER
    assert row.device_last_modified == LATE
    assert row.synced_at == RUN_ONE


def test_the_two_hash_columns_stay_none_because_no_port_hands_this_layer_the_sidecars():
    document = _doc()
    syncer, _, _, store, _ = _syncer(_listing(document), {ALPHA: _bundle(document, ("p", INK))})
    syncer.sync(_pull())
    row = store.get_document(ALPHA)
    assert row is not None
    assert row.metadata_hash is None
    assert row.content_hash is None


def test_an_empty_library_syncs_nothing_and_reports_skipped():
    syncer, _, source, store, audit = _syncer(_listing(), {})
    result = syncer.sync(_pull())
    assert result.outcome is SyncOutcome.SKIPPED
    assert result.documents == ()
    assert source.loads == []
    assert store.recorded == []
    assert audit.entries == []


# ───────────────────── defect 3: the change signal, pinned ─────────────────────


def test_the_change_signal_and_the_unsignalled_fields_partition_the_mirror_row():
    """A field added to ``SyncedDocument`` fails here rather than being silently ignored."""
    assert set(SyncedDocument.model_fields) == SIGNALLED | UNSIGNALLED
    assert not SIGNALLED & UNSIGNALLED


def test_the_unsignalled_fields_are_exactly_the_three_a_listing_cannot_answer():
    assert {"synced_at", "metadata_hash", "content_hash"} == UNSIGNALLED


def test_a_moved_modification_time_is_a_change():
    document = _doc(last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(_mirror_row(device_last_modified=EARLY), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert source.loads == [ALPHA]
    assert result.documents[0].changed is True


def test_an_identical_projection_is_not_a_change_and_costs_no_transfer():
    document = _doc(last_modified=EARLY, page_count=1)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, source, _, audit = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert source.loads == []
    assert result.outcome is SyncOutcome.SKIPPED
    assert result.documents[0].outcome is SyncOutcome.SKIPPED
    assert result.documents[0].changed is False
    assert audit.entries == []


def test_a_rename_is_a_change_even_when_the_timestamp_did_not_move():
    """Otherwise the mirror's denormalised name lies for as long as the timestamp holds."""
    document = _doc(name="Q4 plan", last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(name="Q3 plan"), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    syncer.sync(_pull(RUN_TWO))
    assert source.loads == [ALPHA]
    row = store.get_document(ALPHA)
    assert row is not None
    assert row.visible_name == "Q4 plan"


def test_a_move_between_folders_is_a_change():
    document = _doc(parent_uuid=FOLDER, last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(parent_uuid=None), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    syncer.sync(_pull(RUN_TWO))
    assert source.loads == [ALPHA]


def test_a_changed_page_count_is_a_change():
    document = _doc(page_count=2, last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(page_count=1), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document),
        {ALPHA: _bundle(document, ("page-a", INK), ("page-b", INK))},
        store=store,
    )
    syncer.sync(_pull(RUN_TWO))
    assert source.loads == [ALPHA]


def test_a_document_with_no_reported_modification_time_is_never_current():
    """Absence of a timestamp is not evidence of sameness; the cost of re-pulling is one pull."""
    document = _doc(last_modified=None)
    store = _InMemoryStore()
    store.record_document(_mirror_row(device_last_modified=None), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert source.loads == [ALPHA]
    assert result.documents[0].changed is True


def test_an_unreported_page_count_cannot_contribute_to_the_signal():
    """It is taken from the mirror rather than guessed, so it never forces a pull by itself."""
    document = _doc(page_count=None, last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(page_count=7), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert source.loads == []
    assert result.documents[0].changed is False


def test_an_unreported_page_count_is_recorded_from_the_pages_that_arrived():
    document = _doc(page_count=None, last_modified=EARLY)
    syncer, _, _, store, _ = _syncer(
        _listing(document),
        {ALPHA: _bundle(document, ("page-a", INK), ("page-b", INK))},
    )
    syncer.sync(_pull())
    row = store.get_document(ALPHA)
    assert row is not None
    assert row.page_count == 2


def test_a_reported_page_count_is_recorded_as_the_device_listed_it():
    """Recording the bundle's number instead would re-pull forever if the two disagreed."""
    document = _doc(page_count=5, last_modified=EARLY)
    syncer, _, _, store, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}
    )
    syncer.sync(_pull())
    row = store.get_document(ALPHA)
    assert row is not None
    assert row.page_count == 5


# ───────── defect 2: the signal is recorded on inspection, not on work performed ─────────


def test_a_metadata_only_change_is_pulled_once_and_never_again():
    """The defect: the signal moves, no page changes, the write is skipped, forever after."""
    renamed = _doc(name="Q4 plan", last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(
        _mirror_row(name="Q3 plan", device_last_modified=EARLY),
        [_mirror_page(rm_hash=_hash_of(INK))],
    )
    syncer, _, source, _, _ = _syncer(
        _listing(renamed), {ALPHA: _bundle(renamed, ("page-a", INK))}, store=store
    )
    first = syncer.sync(_pull(RUN_ONE))
    assert source.loads == [ALPHA]
    assert first.documents[0].pages_changed == 0
    assert first.documents[0].outcome is SyncOutcome.SUCCEEDED
    second = syncer.sync(_pull(RUN_TWO))
    assert source.loads == [ALPHA]
    assert second.documents[0].outcome is SyncOutcome.SKIPPED
    assert second.documents[0].changed is False


def test_an_edited_page_is_counted_as_changed():
    document = _doc(last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page(rm_hash=_hash_of(INK))])
    syncer, _, _, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", MORE_INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.documents[0].pages_changed == 1


def test_a_brand_new_page_with_no_ink_counts_as_changed_rather_than_matching_a_missing_row():
    document = _doc(page_count=2, last_modified=LATE, file_type=DeviceFileType.PDF)
    store = _InMemoryStore()
    store.record_document(_mirror_row(page_count=1), [_mirror_page(rm_hash=None)])
    syncer, _, _, _, _ = _syncer(
        _listing(document),
        {ALPHA: _bundle(document, ("page-a", None), ("page-b", None), base=b"%PDF-1.7")},
        store=store,
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.documents[0].pages_changed == 1


# ─────────── defect 1a: a recorded page set is never replaced with an empty one ───────────


def test_a_device_reporting_no_pages_never_wipes_a_recorded_page_set():
    """Remove the guard and this test loses both the pages and the paid transcription."""
    document = _doc(page_count=0, last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(
        _mirror_row(page_count=2), [_mirror_page(), _mirror_page(ALPHA, "page-b", index=1)]
    )
    store.record_page_text(_text(ALPHA, "page-a"))
    syncer, _, _, _, _ = _syncer(_listing(document), {ALPHA: _bundle(document)}, store=store)
    result = syncer.sync(_pull(RUN_TWO))
    assert [page.page_uuid for page in store.pages(ALPHA)] == ["page-a", "page-b"]
    assert [text.page_uuid for text in store.page_texts(ALPHA)] == ["page-a"]
    assert result.documents[0].outcome is SyncOutcome.FAILED
    assert result.outcome is SyncOutcome.FAILED


def test_the_refused_replacement_is_reported_rather_than_swallowed():
    document = _doc(page_count=0, last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, audit = _syncer(_listing(document), {ALPHA: _bundle(document)}, store=store)
    result = syncer.sync(_pull(RUN_TWO))
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.CATALOG_ENTRY_SKIPPED
    assert degradation.subject == ALPHA
    assert "discard every page's recorded text" in degradation.detail
    (recorded,) = audit.entries
    assert recorded.entry.outcome is SyncOutcome.FAILED
    assert recorded.entry.pages_affected == 0


def test_a_genuinely_new_document_with_no_pages_is_recorded_because_there_is_nothing_to_lose():
    document = _doc(page_count=0)
    syncer, _, _, store, _ = _syncer(_listing(document), {ALPHA: _bundle(document)})
    result = syncer.sync(_pull())
    assert store.recorded == [ALPHA]
    assert store.pages(ALPHA) == []
    assert result.documents[0].outcome is SyncOutcome.SUCCEEDED
    assert result.documents[0].pages_recorded == 0


def test_a_departed_page_does_lose_its_text_when_the_set_is_genuinely_replaced():
    """The guard is about an *empty* set; a shrinking one is a real edit and text follows it."""
    document = _doc(page_count=1, last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(
        _mirror_row(page_count=2), [_mirror_page(), _mirror_page(ALPHA, "page-b", index=1)]
    )
    store.record_page_text(_text(ALPHA, "page-b"))
    syncer, _, _, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    syncer.sync(_pull(RUN_TWO))
    assert [page.page_uuid for page in store.pages(ALPHA)] == ["page-a"]
    assert store.page_texts(ALPHA) == []


# ─────────── defect 1b: the per-notebook prune, guarded three ways ───────────


def test_a_document_the_device_no_longer_has_is_forgotten():
    store = _InMemoryStore()
    store.record_document(_mirror_row(BETA, "Gone"), [_mirror_page(BETA, "page-z")])
    document = _doc(last_modified=EARLY)
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, audit = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.absent == (BETA,)
    assert result.forgotten == (BETA,)
    assert store.forgotten == [BETA]
    assert store.get_document(BETA) is None
    (recorded,) = audit.entries
    assert recorded.entry.doc_uuid == BETA
    assert recorded.entry.doc_name == "Gone"
    assert "forgotten" in recorded.entry.detail


def test_an_enumeration_that_represented_nothing_never_prunes():
    """A producer that yields nothing cannot be told apart from an empty library."""
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    store.record_page_text(_text(ALPHA, "page-a"))
    syncer, _, _, _, _ = _syncer(_listing(), {}, store=store)
    result = syncer.sync(_pull(RUN_TWO))
    assert result.absent == (ALPHA,)
    assert result.forgotten == ()
    assert store.forgotten == []
    assert store.get_document(ALPHA) is not None
    assert store.page_texts(ALPHA) != []


def test_an_incomplete_enumeration_never_prunes():
    """The entry it could not represent may be the very document a prune would delete."""
    document = _doc(last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    store.record_document(_mirror_row(BETA, "Maybe gone"), [_mirror_page(BETA, "page-z")])
    skipped = (SkippedEntry(uuid=BETA, reason=SkipReason.UNREADABLE, detail="permission denied"),)
    syncer, _, _, _, _ = _syncer(
        _listing(document, skipped=skipped),
        {ALPHA: _bundle(document, ("page-a", INK))},
        store=store,
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.absent == (BETA,)
    assert result.forgotten == ()
    assert store.get_document(BETA) is not None
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.CATALOG_ENTRY_SKIPPED
    assert "no document is pruned this run" in degradation.detail


def test_a_skipped_entry_with_no_recoverable_identifier_is_still_reported():
    skipped = (SkippedEntry(uuid=None, reason=SkipReason.MALFORMED_METADATA, detail="not json"),)
    syncer, _, _, _, _ = _syncer(_listing(skipped=skipped), {})
    result = syncer.sync(_pull())
    (degradation,) = result.degradations
    assert degradation.subject.startswith("<entry with no")


def test_a_dry_run_never_prunes_but_still_says_what_a_pull_would_forget():
    store = _InMemoryStore()
    store.record_document(_mirror_row(BETA, "Gone"), [_mirror_page(BETA, "page-z")])
    document = _doc(last_modified=EARLY)
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_status(RUN_TWO))
    assert result.absent == (BETA,)
    assert result.forgotten == ()
    assert store.forgotten == []


def test_a_document_that_failed_this_run_is_never_forgotten():
    """The prune is computed against the listing, never against what the run managed to do."""
    document = _doc(last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    store.record_page_text(_text(ALPHA, "page-a"))
    failure = DeviceTransferInterrupted(
        transport=TransportKind.SSH, subject=ALPHA, bytes_transferred=12, bytes_expected=900
    )
    syncer, _, _, _, _ = _syncer(
        _listing(document),
        {},
        store=store,
        source=_InMemoryBundles({}, failures={ALPHA: failure}),
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.absent == ()
    assert result.forgotten == ()
    assert store.get_document(ALPHA) is not None
    assert store.page_texts(ALPHA) != []


def test_a_folder_row_is_never_forgotten_by_a_document_sync():
    """A document sync does not create folder rows, so it must not delete them either."""
    store = _InMemoryStore()
    store.record_document(_mirror_row(FOLDER, "Books", kind=DocumentKind.COLLECTION), [])
    document = _doc(last_modified=EARLY)
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, _ = _syncer(
        _listing(document, folders=(DeviceFolder(uuid=FOLDER, name="Books"),)),
        {ALPHA: _bundle(document, ("page-a", INK))},
        store=store,
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.absent == ()
    assert store.get_document(FOLDER) is not None


# ───────────── whole-transport failure is not per-document failure ─────────────


def test_a_malformed_document_fails_alone_and_the_run_goes_partial():
    good = _doc(BETA, "Good")
    bad = _doc(ALPHA, "Bad")
    failure = MalformedDeviceMetadata(
        transport=TransportKind.SSH, document_uuid=ALPHA, detail="content is not json"
    )
    syncer, _, source, store, audit = _syncer(
        _listing(bad, good),
        {BETA: _bundle(good, ("page-b", INK))},
        source=_InMemoryBundles({BETA: _bundle(good, ("page-b", INK))}, failures={ALPHA: failure}),
    )
    result = syncer.sync(_pull())
    assert result.outcome is SyncOutcome.PARTIAL
    assert [entry.outcome for entry in result.documents] == [
        SyncOutcome.FAILED,
        SyncOutcome.SUCCEEDED,
    ]
    assert "MalformedDeviceMetadata" in result.documents[0].detail
    assert source.loads == [ALPHA, BETA]
    assert store.recorded == [BETA]
    assert [entry.entry.outcome for entry in audit.entries] == [
        SyncOutcome.FAILED,
        SyncOutcome.SUCCEEDED,
    ]


def test_a_truncated_transfer_is_a_per_document_failure():
    document = _doc()
    failure = DeviceTransferInterrupted(
        transport=TransportKind.SSH, subject=ALPHA, bytes_transferred=1, bytes_expected=99
    )
    syncer, _, _, store, _ = _syncer(
        _listing(document), {}, source=_InMemoryBundles({}, failures={ALPHA: failure})
    )
    result = syncer.sync(_pull())
    assert result.outcome is SyncOutcome.FAILED
    assert store.recorded == []


def test_an_unreadable_mirror_row_fails_that_document_alone():
    document = _doc(last_modified=LATE)
    store = _InMemoryStore(unreadable=frozenset({ALPHA}))
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, source, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}, store=store
    )
    result = syncer.sync(_pull(RUN_TWO))
    assert result.documents[0].outcome is SyncOutcome.FAILED
    assert "StoredRecordUnreadableError" in result.documents[0].detail
    assert source.loads == []


def test_a_dead_transport_mid_pull_propagates_rather_than_becoming_many_skipped_documents():
    """The split ``rmspec-device`` already fixed: a dead cable is not forty per-path errors."""
    document = _doc()
    failure = DeviceUnreachable(
        transport=TransportKind.SSH, endpoint="10.11.99.1", detail="connection reset"
    )
    syncer, _, _, _, _ = _syncer(
        _listing(document), {}, source=_InMemoryBundles({}, failures={ALPHA: failure})
    )
    with pytest.raises(DeviceUnreachable):
        syncer.sync(_pull())


def test_refused_credentials_propagate():
    document = _doc()
    failure = DeviceAuthFailed(transport=TransportKind.SSH, user="root", detail="denied")
    syncer, _, _, _, _ = _syncer(
        _listing(document), {}, source=_InMemoryBundles({}, failures={ALPHA: failure})
    )
    with pytest.raises(DeviceAuthFailed):
        syncer.sync(_pull())


def test_an_unavailable_store_propagates():
    document = _doc()
    failure = StoreUnavailableError(store="mirror", detail="disk is full")
    syncer, _, _, _, _ = _syncer(
        _listing(document), {}, source=_InMemoryBundles({}, failures={ALPHA: failure})
    )
    with pytest.raises(StoreUnavailableError):
        syncer.sync(_pull())


def test_an_unreachable_catalog_is_never_degraded_into_an_empty_library():
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API, endpoint="10.11.99.1", detail="refused"
    )
    syncer, _, _, _, _ = _syncer(
        _listing(), {}, store=store, catalog=_InMemoryCatalog(_listing(), failure=failure)
    )
    with pytest.raises(DeviceUnreachable):
        syncer.sync(_pull())
    assert store.forgotten == []


# ───────────────────────────── the dry run ─────────────────────────────


def test_a_dry_run_writes_nothing_and_fetches_nothing():
    document = _doc()
    syncer, catalog, source, store, audit = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}
    )
    result = syncer.sync(_status())
    assert result.dry_run is True
    assert result.outcome is SyncOutcome.SKIPPED
    assert source.loads == []
    assert store.recorded == []
    assert audit.entries == []
    assert catalog.calls == 1


def test_a_dry_run_reports_which_documents_a_pull_would_fetch():
    stale = _doc(ALPHA, "Stale", last_modified=LATE)
    current = _doc(BETA, "Current", last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(BETA, "Current"), [_mirror_page(BETA, "page-z")])
    syncer, _, _, _, _ = _syncer(_listing(stale, current), {}, store=store)
    result = syncer.sync(_status(RUN_TWO))
    assert [(entry.uuid, entry.changed) for entry in result.documents] == [
        (ALPHA, True),
        (BETA, False),
    ]
    assert result.documents[0].detail == "a pull would fetch this document"
    assert result.documents[0].pages_recorded == 0


def test_a_dry_run_over_a_current_library_still_reports_skipped():
    document = _doc(last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, _ = _syncer(_listing(document), {}, store=store)
    assert syncer.sync(_status(RUN_TWO)).outcome is SyncOutcome.SKIPPED


# ───────────────────────────── the trash ─────────────────────────────


def test_a_trashed_document_is_neither_synced_nor_forgotten():
    document = _doc(trashed=True, last_modified=LATE)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    store.record_page_text(_text(ALPHA, "page-a"))
    syncer, _, source, _, _ = _syncer(_listing(document), {}, store=store)
    result = syncer.sync(_pull(RUN_TWO))
    assert source.loads == []
    assert store.forgotten == []
    assert store.page_texts(ALPHA) != []
    (entry,) = result.documents
    assert entry.outcome is SyncOutcome.SKIPPED
    assert entry.changed is False
    assert "trash" in entry.detail
    assert result.absent == ()


# ───────────────────────────── history is best effort ─────────────────────────────


def test_a_pulled_document_is_recorded_as_a_pull():
    document = _doc(page_count=2)
    syncer, _, _, _, audit = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK), ("page-b", INK))}
    )
    syncer.sync(_pull())
    (recorded,) = audit.entries
    assert recorded.entry.operation is SyncOperation.PULL
    assert recorded.entry.outcome is SyncOutcome.SUCCEEDED
    assert recorded.entry.doc_uuid == ALPHA
    assert recorded.entry.doc_name == "Notes"
    assert recorded.entry.pages_affected == 2
    assert recorded.entry.occurred_at == RUN_ONE


def test_a_document_that_needed_nothing_is_not_audited():
    """A history that records reads is a history nobody reads."""
    document = _doc(last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, audit = _syncer(_listing(document), {}, store=store)
    syncer.sync(_pull(RUN_TWO))
    assert audit.entries == []


def test_an_audit_write_that_did_not_land_degrades_rather_than_fails():
    document = _doc()
    syncer, _, _, store, _ = _syncer(
        _listing(document),
        {ALPHA: _bundle(document, ("page-a", INK))},
        audit=_RecordingAuditLog(failure=AuditWriteFailedError(detail="database is locked")),
    )
    result = syncer.sync(_pull())
    assert result.outcome is SyncOutcome.SUCCEEDED
    assert store.recorded == [ALPHA]
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.AUDIT_NOT_RECORDED
    assert degradation.subject == ALPHA
    assert "database is locked" in degradation.detail


# ───────────────────────────── the run's own outcome ─────────────────────────────


def test_every_document_failing_is_a_failed_run():
    document = _doc()
    failure = MalformedDeviceMetadata(
        transport=TransportKind.SSH, document_uuid=ALPHA, detail="not json"
    )
    syncer, _, _, _, _ = _syncer(
        _listing(document), {}, source=_InMemoryBundles({}, failures={ALPHA: failure})
    )
    assert syncer.sync(_pull()).outcome is SyncOutcome.FAILED


def test_one_document_failing_beside_a_skip_is_a_partial_run():
    failing = _doc(ALPHA, "Bad")
    current = _doc(BETA, "Current", last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(BETA, "Current"), [_mirror_page(BETA, "page-z")])
    failure = MalformedDeviceMetadata(
        transport=TransportKind.SSH, document_uuid=ALPHA, detail="not json"
    )
    syncer, _, _, _, _ = _syncer(
        _listing(failing, current),
        {},
        store=store,
        source=_InMemoryBundles({}, failures={ALPHA: failure}),
    )
    assert syncer.sync(_pull(RUN_TWO)).outcome is SyncOutcome.PARTIAL


def test_work_landing_with_nothing_failing_is_a_successful_run():
    document = _doc()
    syncer, _, _, _, _ = _syncer(_listing(document), {ALPHA: _bundle(document, ("page-a", INK))})
    assert syncer.sync(_pull()).outcome is SyncOutcome.SUCCEEDED


def test_nothing_needing_doing_is_a_skipped_run():
    document = _doc(last_modified=EARLY)
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    syncer, _, _, _, _ = _syncer(_listing(document), {}, store=store)
    assert syncer.sync(_pull(RUN_TWO)).outcome is SyncOutcome.SKIPPED


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_one_sync_is_one_enumeration():
    document = _doc()
    syncer, catalog, _, _, _ = _syncer(
        _listing(document), {ALPHA: _bundle(document, ("page-a", INK))}
    )
    syncer.sync(_pull())
    assert catalog.calls == 1


def test_every_listed_document_appears_in_the_report_in_listing_order():
    first = _doc(GAMMA, "Zebra")
    second = _doc(ALPHA, "Apple")
    syncer, _, _, _, _ = _syncer(
        _listing(first, second),
        {
            GAMMA: _bundle(first, ("page-z", INK)),
            ALPHA: _bundle(second, ("page-a", INK)),
        },
    )
    result = syncer.sync(_pull())
    assert [entry.uuid for entry in result.documents] == [GAMMA, ALPHA]


def test_the_fakes_are_the_ports_the_use_case_declares():
    document = _doc()
    catalog: DeviceCatalog = _InMemoryCatalog(_listing(document))
    bundles: RawBundleSource = _InMemoryBundles({ALPHA: _bundle(document, ("page-a", INK))})
    store: DocumentSyncStore = _InMemoryStore()
    audit: SyncAuditLog = _RecordingAuditLog()
    assert catalog.get_document(ALPHA).name == "Notes"
    assert bundles.load_bundle(ALPHA).document.uuid == ALPHA
    assert store.list_documents() == []
    assert store.all_page_texts() == []
    assert audit.recent(limit=1) == []


def test_the_store_double_refuses_a_page_that_names_another_document():
    """The port raises ``ValueError`` for it, so the double must too or the suite lies."""
    store = _InMemoryStore()
    with pytest.raises(ValueError, match="do not belong"):
        store.record_document(_mirror_row(), [_mirror_page(BETA, "page-z")])


def test_the_store_double_reports_an_unreadable_payload():
    store = _InMemoryStore(unreadable=frozenset({ALPHA}))
    with pytest.raises(StoredRecordUnreadableError):
        store.pages(ALPHA)


def test_the_catalog_double_reports_an_unknown_identifier():
    catalog = _InMemoryCatalog(_listing())
    with pytest.raises(MalformedDeviceMetadata):
        catalog.get_document(ALPHA)


def test_the_audit_double_refuses_a_non_positive_limit_like_the_port_says():
    with pytest.raises(ValueError, match="at least 1"):
        _RecordingAuditLog().recent(limit=0)


def test_forgetting_an_untracked_document_is_a_no_op_in_the_double():
    store = _InMemoryStore()
    store.forget_document(ALPHA)
    assert store.forgotten == [ALPHA]


def test_text_for_a_page_the_mirror_does_not_have_is_dropped_by_the_double():
    store = _InMemoryStore()
    store.record_document(_mirror_row(), [_mirror_page()])
    store.record_page_text(_text(ALPHA, "page-elsewhere"))
    assert store.page_texts(ALPHA) == []


def test_a_result_is_frozen():
    syncer, _, _, _, _ = _syncer(_listing(), {})
    result = syncer.sync(_pull())
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "outcome", SyncOutcome.FAILED)


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        SyncDocumentsResult.model_validate(
            {
                "outcome": SyncOutcome.SKIPPED,
                "dry_run": False,
                "documents": (),
                "absent": (),
                "forgotten": (),
                "degradations": (),
                "pruned_anyway": True,
            }
        )


def test_a_per_document_outcome_is_frozen_and_forbids_unknown_fields():
    entry = SyncedDocumentOutcome(
        uuid=ALPHA,
        visible_name="Notes",
        outcome=SyncOutcome.SKIPPED,
        changed=False,
        pages_recorded=0,
        pages_changed=0,
        detail="",
    )
    with pytest.raises(ValidationError, match="frozen"):
        _assign(entry, "outcome", SyncOutcome.FAILED)
    with pytest.raises(ValidationError):
        SyncedDocumentOutcome.model_validate({**entry.model_dump(), "wiped": True})


def test_a_request_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        SyncDocumentsRequest.model_validate({"synced_at": RUN_ONE, "force": True})
