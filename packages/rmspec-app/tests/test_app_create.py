"""Both title semantics, the two refusals, the duplicate policy, and the unguessed uuid.

How the three ports are bound here, and why
-------------------------------------------
With local in-memory fakes annotated against the Protocols, exactly as
``test_app_resolve.py`` binds ``DeviceCatalog`` and for the same three reasons:
``rmspec.app`` may import ``rmspec.domain`` and nothing else and these tests hold
themselves to the rule their source obeys; the architecture check only scans ``src/``, so
an adapter import here would pass the gate while breaking the property the gate exists to
protect; and conformance is still checked, by the type gate rather than by convention,
because every fake below is passed to a Protocol-annotated keyword argument.

The fakes carry three seams and nothing more. A shared ``journal`` list, because the whole
claim of this use case's ordering -- the name check happens *before* the irreversible write
and the catalog is never consulted after it -- is unassertable without recording the order
the collaborators were called in. A ``failure`` on each, because a dead transport, a
transport that cannot honour a destination, and an audit write that did not land are three
outcomes that only exist if a double can be told to produce them. And a settable
``doc_uuid`` on the uploader, because ``None`` over USB and a minted identifier over SSH are
the two halves of :attr:`UploadReceipt.doc_uuid`'s contract.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from rmspec.app.create import CreateDocument, CreateDocumentRequest, CreateDocumentResult
from rmspec.domain.errors import (
    AuditWriteFailedError,
    DegradationKind,
    DeviceOperationUnsupported,
    DeviceUnreachable,
    RmspecError,
    TransportKind,
    UsageError,
)
from rmspec.domain.models import (
    RecordedSyncAuditEntry,
    SyncAuditEntry,
    SyncOperation,
    SyncOutcome,
)
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DeviceFolder,
    DeviceListing,
    DocumentUploader,
    LibraryRefresh,
    SkippedEntry,
    SkipReason,
    UploadMedia,
    UploadReceipt,
    UploadRequest,
)

if TYPE_CHECKING:
    from rmspec.domain.ports.persistence import SyncAuditLog

ALPHA = "aaaaaaaa-1111-4111-8111-111111111111"
BETA = "bbbbbbbb-2222-4222-8222-222222222222"
FOLDER = "ffffffff-4444-4444-8444-444444444444"
MINTED = "cccccccc-5555-4555-8555-555555555555"

AT = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\nendobj\n%%EOF\n"
"""A payload carrying the PDF witness."""

ARCHIVE_BYTES = b"PK\x03\x04\x14\x00\x00\x00\x08\x00metadata"
"""A payload carrying a zip local file header, so the archive has at least one member."""

EMPTY_ARCHIVE_BYTES = b"PK\x05\x06" + bytes(18)
"""A structurally valid zip with no members at all: the empty-archive case, verbatim."""


class _InMemoryCatalog:
    """A :class:`DeviceCatalog` over one listing, recording that it was consulted."""

    def __init__(
        self,
        listing: DeviceListing,
        *,
        journal: list[str] | None = None,
        failure: RmspecError | None = None,
    ) -> None:
        self.calls = 0
        self._listing = listing
        self._journal = journal if journal is not None else []
        self._failure = failure

    def list_documents(self) -> DeviceListing:
        """Return the whole library, or die the way a transport dies."""
        self.calls += 1
        self._journal.append("list")
        if self._failure is not None:
            raise self._failure
        return self._listing

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Look one document up, which this use case never does."""
        for document in self._listing.documents:
            if document.uuid == doc_uuid:
                return document
        raise DeviceUnreachable(
            transport=TransportKind.USB_WEB_API, endpoint="10.11.99.1", detail="no such document"
        )


class _RecordingUploader:
    """A :class:`DocumentUploader` that answers with a receipt, or refuses the request."""

    def __init__(
        self,
        *,
        doc_uuid: str | None = None,
        refresh: LibraryRefresh = LibraryRefresh.ALREADY_VISIBLE,
        journal: list[str] | None = None,
        failure: RmspecError | None = None,
    ) -> None:
        self.requests: list[UploadRequest] = []
        self._doc_uuid = doc_uuid
        self._refresh = refresh
        self._journal = journal if journal is not None else []
        self._failure = failure

    def upload(self, request: UploadRequest, /) -> UploadReceipt:
        """Accept the document, or raise the way an adapter that cannot honour it does."""
        self._journal.append("upload")
        if self._failure is not None:
            raise self._failure
        self.requests.append(request)
        return UploadReceipt(
            doc_uuid=self._doc_uuid,
            name=request.name,
            media=request.media,
            byte_count=len(request.data),
            library_refresh=self._refresh,
        )


class _RecordingAuditLog:
    """A :class:`SyncAuditLog` that either records or reports that it could not."""

    def __init__(
        self,
        *,
        journal: list[str] | None = None,
        failure: AuditWriteFailedError | None = None,
    ) -> None:
        self.entries: list[RecordedSyncAuditEntry] = []
        self._journal = journal if journal is not None else []
        self._failure = failure

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Append one entry, or report that it did not land."""
        self._journal.append("audit")
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
    uuid: str,
    name: str,
    *,
    parent_uuid: str | None = None,
    trashed: bool = False,
) -> DeviceDocument:
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=DeviceFileType.NOTEBOOK,
        parent_uuid=parent_uuid,
        trashed=trashed,
    )


def _listing(
    *documents: DeviceDocument,
    skipped: tuple[SkippedEntry, ...] = (),
    folders: tuple[DeviceFolder, ...] = (),
) -> DeviceListing:
    return DeviceListing(documents=documents, folders=folders, skipped=skipped)


def _request(
    *,
    name: str = "Q3 plan.pdf",
    media: UploadMedia = UploadMedia.PDF,
    data: bytes = PDF_BYTES,
    page_count: int = 3,
    parent_uuid: str | None = None,
    allow_duplicate_name: bool = False,
) -> CreateDocumentRequest:
    return CreateDocumentRequest(
        name=name,
        media=media,
        data=data,
        page_count=page_count,
        occurred_at=AT,
        parent_uuid=parent_uuid,
        allow_duplicate_name=allow_duplicate_name,
    )


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error.

    A direct ``result.doc_uuid = ...`` is rejected by the type gate before the test can
    prove pydantic rejects it at runtime, and this repository allows no ``type: ignore`` to
    get past that. A variable field name also keeps ``B010`` quiet without a ``noqa``.
    """
    setattr(target, field, value)


# ───────────────────────────── the supported write path ─────────────────────────────


def test_a_pdf_is_created_and_the_receipt_is_reported_verbatim():
    uploader = _RecordingUploader(refresh=LibraryRefresh.ALREADY_VISIBLE)
    creator = CreateDocument(
        uploader=uploader, catalog=_InMemoryCatalog(_listing()), audit=_RecordingAuditLog()
    )
    result = creator.create(_request())
    assert result.media is UploadMedia.PDF
    assert result.byte_count == len(PDF_BYTES)
    assert result.library_refresh is LibraryRefresh.ALREADY_VISIBLE
    assert result.also_named == ()
    assert result.degradations == ()


def test_the_payload_and_the_name_reach_the_uploader_unchanged():
    uploader = _RecordingUploader()
    creator = CreateDocument(
        uploader=uploader, catalog=_InMemoryCatalog(_listing()), audit=_RecordingAuditLog()
    )
    creator.create(_request(name="  Q3 plan.pdf  "))
    (sent,) = uploader.requests
    assert sent.name == "Q3 plan.pdf"
    assert sent.data == PDF_BYTES
    assert sent.media is UploadMedia.PDF


def test_an_rmdoc_archive_is_created_as_a_notebook():
    """The measured refutation of "a notebook cannot be uploaded", as a use case."""
    uploader = _RecordingUploader(refresh=LibraryRefresh.VISIBILITY_FORCED)
    creator = CreateDocument(
        uploader=uploader, catalog=_InMemoryCatalog(_listing()), audit=_RecordingAuditLog()
    )
    result = creator.create(_request(media=UploadMedia.RMDOC, data=ARCHIVE_BYTES, name="TestNb"))
    assert result.media is UploadMedia.RMDOC
    assert result.library_refresh is LibraryRefresh.VISIBILITY_FORCED


def test_visibility_is_taken_from_the_receipt_rather_than_assumed():
    for refresh in LibraryRefresh:
        creator = CreateDocument(
            uploader=_RecordingUploader(refresh=refresh),
            catalog=_InMemoryCatalog(_listing()),
            audit=_RecordingAuditLog(),
        )
        assert creator.create(_request()).library_refresh is refresh


# ───────────────────────── the two container title semantics ─────────────────────────


def test_a_pdf_filename_becomes_the_visible_name_verbatim_extension_included():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request(name="Q3 plan.pdf"))
    assert result.requested_name == "Q3 plan.pdf"
    assert result.visible_name == "Q3 plan.pdf"


def test_an_epub_filename_becomes_the_visible_name_too():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(
        _request(name="novel.epub", media=UploadMedia.EPUB, data=ARCHIVE_BYTES)
    )
    assert result.visible_name == "novel.epub"


def test_an_rmdoc_carries_its_own_title_so_this_layer_reports_that_it_cannot_know():
    """``visible_name is None`` is the report; echoing the request back would be a lie."""
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request(name="TestNb", media=UploadMedia.RMDOC, data=ARCHIVE_BYTES))
    assert result.requested_name == "TestNb"
    assert result.visible_name is None


def test_the_two_media_disagree_about_the_title_and_the_result_says_so():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    as_pdf = creator.create(_request(name="Notes", media=UploadMedia.PDF, data=PDF_BYTES))
    as_archive = creator.create(
        _request(name="Notes", media=UploadMedia.RMDOC, data=ARCHIVE_BYTES)
    )
    assert as_pdf.visible_name == as_pdf.requested_name
    assert as_archive.visible_name is None
    assert as_archive.requested_name == as_pdf.requested_name


# ───────────────────────────── refusing to deliver nothing ─────────────────────────────


def test_a_payload_with_no_pdf_witness_is_refused_before_the_wire():
    uploader = _RecordingUploader()
    creator = CreateDocument(
        uploader=uploader, catalog=_InMemoryCatalog(_listing()), audit=_RecordingAuditLog()
    )
    with pytest.raises(UsageError):
        creator.create(_request(data=b"this is not a pdf"))
    assert uploader.requests == []


def test_an_empty_payload_is_refused():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(UsageError):
        creator.create(_request(data=b""))


def test_a_structurally_valid_empty_archive_is_refused():
    """The exact shape a neighbouring project ships: a valid container holding nothing."""
    uploader = _RecordingUploader()
    creator = CreateDocument(
        uploader=uploader, catalog=_InMemoryCatalog(_listing()), audit=_RecordingAuditLog()
    )
    with pytest.raises(UsageError) as caught:
        creator.create(_request(media=UploadMedia.RMDOC, data=EMPTY_ARCHIVE_BYTES))
    assert "rmdoc" in caught.value.subject
    assert uploader.requests == []


def test_an_empty_epub_archive_is_refused_by_the_same_witness():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(UsageError):
        creator.create(_request(media=UploadMedia.EPUB, data=EMPTY_ARCHIVE_BYTES))


def test_a_pdf_header_after_a_prefix_is_still_a_pdf():
    """Refusing a real document is a cost this check may not impose, so the window is not 0."""
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request(data=b"junk-prefix\n" + PDF_BYTES))
    assert result.byte_count == len(b"junk-prefix\n" + PDF_BYTES)


def test_a_witness_beyond_the_window_does_not_count():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(UsageError):
        creator.create(_request(data=bytes(2048) + PDF_BYTES))


def test_a_document_declaring_no_pages_is_refused_however_valid_its_bytes():
    """A parser that returned nothing is knowable at the boundary that built the payload."""
    uploader = _RecordingUploader()
    catalog = _InMemoryCatalog(_listing())
    creator = CreateDocument(uploader=uploader, catalog=catalog, audit=_RecordingAuditLog())
    with pytest.raises(UsageError) as caught:
        creator.create(_request(page_count=0))
    assert "0 pages" in caught.value.subject
    assert uploader.requests == []
    assert catalog.calls == 0


def test_a_negative_page_count_is_unconstructible_rather_than_a_named_error():
    with pytest.raises(ValidationError):
        _request(page_count=-1)


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_a_blank_name_is_a_usage_error(name: str):
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(UsageError):
        creator.create(_request(name=name))


def test_a_refusal_costs_no_device_round_trip():
    uploader = _RecordingUploader()
    catalog = _InMemoryCatalog(_listing())
    creator = CreateDocument(uploader=uploader, catalog=catalog, audit=_RecordingAuditLog())
    with pytest.raises(UsageError):
        creator.create(_request(name=" "))
    assert catalog.calls == 0
    assert uploader.requests == []


# ─────────────────── a destination this route cannot honour reaches the caller ───────────


def test_a_parent_uuid_is_handed_to_the_uploader_rather_than_dropped():
    uploader = _RecordingUploader()
    creator = CreateDocument(
        uploader=uploader, catalog=_InMemoryCatalog(_listing()), audit=_RecordingAuditLog()
    )
    creator.create(_request(parent_uuid=FOLDER))
    (sent,) = uploader.requests
    assert sent.parent_uuid == FOLDER


def test_a_destination_the_transport_cannot_express_is_the_callers_news():
    """Never a silent placement at the root, and never swallowed on the way back out."""
    refusal = DeviceOperationUnsupported(
        transport=TransportKind.USB_WEB_API,
        operation="upload into a folder",
        supported_by=(TransportKind.SSH,),
    )
    audit = _RecordingAuditLog()
    creator = CreateDocument(
        uploader=_RecordingUploader(failure=refusal),
        catalog=_InMemoryCatalog(_listing()),
        audit=audit,
    )
    with pytest.raises(DeviceOperationUnsupported):
        creator.create(_request(parent_uuid=FOLDER))
    assert audit.entries == []


def test_a_dead_transport_is_never_reported_as_a_created_document():
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API, endpoint="10.11.99.1", detail="connection refused"
    )
    creator = CreateDocument(
        uploader=_RecordingUploader(failure=failure),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(DeviceUnreachable):
        creator.create(_request())


def test_an_unreachable_catalog_is_never_degraded_into_a_free_name():
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API, endpoint="10.11.99.1", detail="connection refused"
    )
    uploader = _RecordingUploader()
    creator = CreateDocument(
        uploader=uploader,
        catalog=_InMemoryCatalog(_listing(), failure=failure),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(DeviceUnreachable):
        creator.create(_request())
    assert uploader.requests == []


# ───────────────────── the identifier the transport does not report ─────────────────────


def test_an_absent_identifier_stays_absent_and_is_not_guessed():
    """``POST /upload`` reports no id, and re-listing to find "the new one" races."""
    journal: list[str] = []
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Older")), journal=journal)
    creator = CreateDocument(
        uploader=_RecordingUploader(doc_uuid=None, journal=journal),
        catalog=catalog,
        audit=_RecordingAuditLog(journal=journal),
    )
    result = creator.create(_request())
    assert result.doc_uuid is None
    assert catalog.calls == 1
    assert journal == ["list", "upload", "audit"]


def test_an_identifier_the_transport_did_mint_is_reported():
    creator = CreateDocument(
        uploader=_RecordingUploader(doc_uuid=MINTED),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    assert creator.create(_request()).doc_uuid == MINTED


def test_the_name_check_happens_before_the_irreversible_write():
    """Ordering is the whole claim: this route cannot delete, so the check cannot follow."""
    journal: list[str] = []
    creator = CreateDocument(
        uploader=_RecordingUploader(journal=journal),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Q3 plan.pdf")), journal=journal),
        audit=_RecordingAuditLog(journal=journal),
    )
    with pytest.raises(UsageError):
        creator.create(_request())
    assert journal == ["list"]


def test_an_rmdoc_costs_no_listing_at_all():
    """Its visible name is decided by the archive, so there is no name to check."""
    journal: list[str] = []
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "TestNb")), journal=journal)
    creator = CreateDocument(
        uploader=_RecordingUploader(journal=journal),
        catalog=catalog,
        audit=_RecordingAuditLog(journal=journal),
    )
    result = creator.create(_request(name="TestNb", media=UploadMedia.RMDOC, data=ARCHIVE_BYTES))
    assert catalog.calls == 0
    assert journal == ["upload", "audit"]
    assert result.also_named == ()


# ───────────────────────── a document of this name already exists ─────────────────────────


def test_an_existing_root_name_is_refused_by_default():
    uploader = _RecordingUploader()
    creator = CreateDocument(
        uploader=uploader,
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Q3 plan.pdf"))),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(UsageError) as caught:
        creator.create(_request(name="Q3 plan.pdf"))
    assert ALPHA in caught.value.subject
    assert "manual delete" in caught.value.requirement
    assert uploader.requests == []


def test_the_collision_is_case_insensitive():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "q3 PLAN.pdf"))),
        audit=_RecordingAuditLog(),
    )
    with pytest.raises(UsageError):
        creator.create(_request(name="Q3 plan.pdf"))


def test_a_caller_may_opt_in_and_is_told_what_it_now_owes_a_manual_delete_for():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Q3 plan.pdf"))),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request(name="Q3 plan.pdf", allow_duplicate_name=True))
    (candidate,) = result.also_named
    assert candidate.uuid == ALPHA
    assert candidate.name == "Q3 plan.pdf"


def test_a_trashed_document_of_the_same_name_is_not_a_collision():
    """The trash is not the library, exactly as document resolution reads it."""
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Q3 plan.pdf", trashed=True))),
        audit=_RecordingAuditLog(),
    )
    assert creator.create(_request(name="Q3 plan.pdf")).also_named == ()


def test_a_document_of_the_same_name_inside_a_folder_is_not_a_collision():
    """An upload lands at the root, so a folder's contents are not what the user sees."""
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Q3 plan.pdf", parent_uuid=FOLDER))),
        audit=_RecordingAuditLog(),
    )
    assert creator.create(_request(name="Q3 plan.pdf")).also_named == ()


def test_a_different_name_is_not_a_collision():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Q2 plan.pdf"))),
        audit=_RecordingAuditLog(),
    )
    assert creator.create(_request(name="Q3 plan.pdf")).also_named == ()


def test_several_collisions_are_all_reported():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Notes"), _doc(BETA, "notes"))),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request(name="Notes", allow_duplicate_name=True))
    assert [candidate.uuid for candidate in result.also_named] == [ALPHA, BETA]


# ─────────────────── a listing that omitted entries cannot clear a name ───────────────────


def test_each_skipped_entry_becomes_one_degradation_and_the_upload_proceeds():
    skipped = (
        SkippedEntry(uuid="abc", reason=SkipReason.UNREADABLE, detail="permission denied"),
        SkippedEntry(uuid=None, reason=SkipReason.MALFORMED_METADATA, detail="not json"),
    )
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing(_doc(ALPHA, "Older"), skipped=skipped)),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request())
    assert [entry.kind for entry in result.degradations] == [
        DegradationKind.CATALOG_ENTRY_SKIPPED,
        DegradationKind.CATALOG_ENTRY_SKIPPED,
    ]
    assert result.degradations[0].subject == "abc"
    assert "unreadable: permission denied" in result.degradations[0].detail
    assert result.degradations[1].subject.startswith("<entry with no")
    assert result.byte_count == len(PDF_BYTES)


# ───────────────────────────── history is best effort ─────────────────────────────


def test_a_created_document_is_recorded_as_a_push():
    audit = _RecordingAuditLog()
    creator = CreateDocument(
        uploader=_RecordingUploader(doc_uuid=MINTED),
        catalog=_InMemoryCatalog(_listing()),
        audit=audit,
    )
    creator.create(_request(name="Q3 plan.pdf", page_count=3))
    (recorded,) = audit.entries
    assert recorded.sequence == 1
    assert recorded.entry.operation is SyncOperation.PUSH
    assert recorded.entry.outcome is SyncOutcome.SUCCEEDED
    assert recorded.entry.doc_uuid == MINTED
    assert recorded.entry.doc_name == "Q3 plan.pdf"
    assert recorded.entry.pages_affected == 3
    assert recorded.entry.occurred_at == AT


def test_history_records_the_absent_identifier_as_absent():
    audit = _RecordingAuditLog()
    creator = CreateDocument(
        uploader=_RecordingUploader(doc_uuid=None),
        catalog=_InMemoryCatalog(_listing()),
        audit=audit,
    )
    creator.create(_request())
    (recorded,) = audit.entries
    assert recorded.entry.doc_uuid is None


def test_an_audit_write_that_did_not_land_degrades_rather_than_fails():
    """The document exists on a device with no delete route; that is not a failure."""
    creator = CreateDocument(
        uploader=_RecordingUploader(doc_uuid=MINTED),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(failure=AuditWriteFailedError(detail="database is locked")),
    )
    result = creator.create(_request())
    assert result.doc_uuid == MINTED
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.AUDIT_NOT_RECORDED
    assert degradation.subject == MINTED
    assert "database is locked" in degradation.detail


def test_an_unrecorded_history_names_the_document_by_name_when_there_is_no_identifier():
    creator = CreateDocument(
        uploader=_RecordingUploader(doc_uuid=None),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(failure=AuditWriteFailedError(detail="disk full")),
    )
    result = creator.create(_request(name="Q3 plan.pdf"))
    (degradation,) = result.degradations
    assert degradation.subject == "Q3 plan.pdf"


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_the_fakes_are_the_ports_the_use_case_declares():
    journal: list[str] = []
    uploader: DocumentUploader = _RecordingUploader(journal=journal)
    catalog: DeviceCatalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Older")), journal=journal)
    audit: SyncAuditLog = _RecordingAuditLog(journal=journal)
    assert catalog.get_document(ALPHA).name == "Older"
    assert (
        uploader.upload(UploadRequest(name="x.pdf", media=UploadMedia.PDF, data=PDF_BYTES)).name
        == "x.pdf"
    )
    assert audit.recent(limit=1) == []


def test_the_audit_double_refuses_a_non_positive_limit_like_the_port_says():
    audit = _RecordingAuditLog()
    with pytest.raises(ValueError, match="at least 1"):
        audit.recent(limit=0)


def test_a_result_is_frozen():
    creator = CreateDocument(
        uploader=_RecordingUploader(),
        catalog=_InMemoryCatalog(_listing()),
        audit=_RecordingAuditLog(),
    )
    result = creator.create(_request())
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "doc_uuid", MINTED)


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        CreateDocumentResult.model_validate(
            {
                "doc_uuid": None,
                "requested_name": "Q3 plan.pdf",
                "visible_name": "Q3 plan.pdf",
                "media": UploadMedia.PDF,
                "byte_count": 4,
                "library_refresh": LibraryRefresh.ALREADY_VISIBLE,
                "also_named": (),
                "degradations": (),
                "deleted_on_failure": True,
            }
        )


def test_a_request_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        CreateDocumentRequest.model_validate(
            {
                "name": "Q3 plan.pdf",
                "media": UploadMedia.PDF,
                "data": PDF_BYTES,
                "page_count": 1,
                "occurred_at": AT,
                "overwrite": True,
            }
        )
