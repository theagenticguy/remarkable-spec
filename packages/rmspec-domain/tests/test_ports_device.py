"""Contract tests for the device slice ports.

Two kinds of test live here. The value objects in
:mod:`rmspec.domain.ports.device` carry real validators, so they are tested directly:
timestamp normalization, the ``unsupported`` set rules, the gauge-pair rule, and the
bundle's page/underlay coherence rule.

The Protocols carry no code, so each is tested through a reference in-memory fake that is
bound to a variable annotated with the Protocol -- which is what makes the type gate check
structural conformance -- and then exercised against the rules the port docstrings state:
which identifier raises which error, that a receipt's ``byte_count`` equals what was
offered, that a request is never degraded, and that a fixed fact and a volatile gauge
cannot be served from one cached reading. No fake touches a network, a device, a
subprocess or the filesystem.
"""

from __future__ import annotations

import datetime
from typing import get_protocol_members

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from rmspec.domain.errors import (
    DeviceDocumentNotFound,
    DeviceOperationUnsupported,
    DeviceTransferInterrupted,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.ports import device as device_port
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFacts,
    DeviceFactsSource,
    DeviceFileType,
    DeviceFolder,
    DeviceListing,
    DevicePageSource,
    DeviceResources,
    DocumentSourceBundle,
    DocumentUploader,
    LibraryRefresh,
    RawBundleSource,
    SearchIndexSource,
    SkippedEntry,
    SkipReason,
    UploadMedia,
    UploadReceipt,
    UploadRequest,
    _check_unsupported,
    _to_utc_milliseconds,
)

# ───────────────────────────── shared builders ─────────────────────────────

_OFFSETS = st.integers(min_value=-1439, max_value=1439).map(
    lambda minutes: datetime.timezone(datetime.timedelta(minutes=minutes))
)
_AWARE = st.datetimes(
    min_value=datetime.datetime(1971, 1, 1),  # noqa: DTZ001 -- naive bound, tz added below
    max_value=datetime.datetime(2099, 1, 1),  # noqa: DTZ001 -- naive bound, tz added below
    timezones=_OFFSETS,
)


def _notebook(
    uuid: str = "doc-1",
    *,
    parent_uuid: str | None = None,
    last_modified: datetime.datetime | None = None,
    page_count: int | None = None,
    trashed: bool = False,
) -> DeviceDocument:
    """Return a handwriting-only document with only the given facts recorded."""
    return DeviceDocument(
        uuid=uuid,
        name=uuid,
        file_type=DeviceFileType.NOTEBOOK,
        parent_uuid=parent_uuid,
        last_modified=last_modified,
        page_count=page_count,
        trashed=trashed,
    )


def _annotated_pdf(uuid: str = "doc-pdf") -> DeviceDocument:
    """Return a minimal document that has a PDF underlay."""
    return DeviceDocument(uuid=uuid, name=uuid, file_type=DeviceFileType.PDF)


def _bundle_for(document: DeviceDocument, page_ids: tuple[str, ...]) -> DocumentSourceBundle:
    """Return a valid bundle for ``document`` with one empty page per identifier."""
    return DocumentSourceBundle(
        document=document,
        pages=tuple(DevicePageSource(page_id=page_id) for page_id in page_ids),
        base=None if document.file_type is DeviceFileType.NOTEBOOK else b"%PDF-1.7",
    )


# ───────────────────────────── module surface ─────────────────────────────


def test_all_names_resolve_and_cover_every_public_name():
    exported = set(device_port.__all__)
    assert all(hasattr(device_port, name) for name in exported)
    public = {
        name
        for name in vars(device_port)
        if not name.startswith("_") and getattr(vars(device_port)[name], "__module__", None)
    }
    assert exported == {name for name in public if name in exported}
    assert exported >= {
        "DeviceCatalog",
        "RawBundleSource",
        "DocumentUploader",
        "DeviceFactsSource",
        "SearchIndexSource",
    }
    assert device_port.__all__ == sorted(device_port.__all__)


def test_capability_asymmetry_is_not_data_a_caller_branches_on():
    """No port or value object offers ``capabilities``, ``supports`` or ``probe``."""
    for owner in (
        DeviceCatalog,
        RawBundleSource,
        DocumentUploader,
        DeviceFactsSource,
        SearchIndexSource,
        DeviceFacts,
        DeviceResources,
    ):
        assert not any(hasattr(owner, name) for name in ("capabilities", "supports", "probe"))
    assert "capabilities" not in DeviceFacts.model_fields


# ───────────────────────────── enums ─────────────────────────────


def test_skip_reason_is_a_closed_set_of_string_values():
    assert {member.value for member in SkipReason} == {
        "malformed_metadata",
        "validation_failed",
        "unreadable",
    }
    assert SkipReason("unreadable") is SkipReason.UNREADABLE
    assert SkipReason.MALFORMED_METADATA == "malformed_metadata"
    with pytest.raises(ValueError, match="is not a valid SkipReason"):
        SkipReason("skipped")


def test_device_file_type_is_a_closed_set_of_string_values():
    assert {member.value for member in DeviceFileType} == {"notebook", "pdf", "epub"}
    assert DeviceFileType("pdf") is DeviceFileType.PDF
    assert DeviceFileType.EPUB == "epub"
    with pytest.raises(ValueError, match="is not a valid DeviceFileType"):
        DeviceFileType("folder")


def test_upload_media_omits_notebook_because_a_notebook_has_no_underlay():
    assert {member.value for member in UploadMedia} == {"pdf", "epub"}
    assert "notebook" not in {member.value for member in UploadMedia}
    assert {member.value for member in UploadMedia} < {member.value for member in DeviceFileType}


def test_library_refresh_is_a_closed_two_member_outcome():
    assert {member.value for member in LibraryRefresh} == {
        "visibility_forced",
        "already_visible",
    }
    assert LibraryRefresh("visibility_forced") is LibraryRefresh.VISIBILITY_FORCED


def test_transport_kind_covers_both_ssh_and_the_usb_web_api():
    assert {TransportKind.SSH, TransportKind.USB_WEB_API} <= set(TransportKind)
    assert TransportKind.SSH.value == "ssh"
    assert TransportKind.USB_WEB_API.value == "usb_web_api"


def test_an_unrecorded_file_type_is_rejected_not_coerced():
    with pytest.raises(ValidationError, match="file_type"):
        DeviceDocument(uuid="u", name="n", file_type="folder")
    skipped = SkippedEntry(
        uuid="u", reason=SkipReason.VALIDATION_FAILED, detail="file_type=folder"
    )
    assert skipped.reason is SkipReason.VALIDATION_FAILED


# ───────────────────────────── the timestamp validator ─────────────────────────────


def test_a_naive_timestamp_cannot_be_constructed():
    naive = datetime.datetime(2024, 3, 1, 12, 0)  # noqa: DTZ001 -- the rejected shape
    with pytest.raises(ValueError, match="must be timezone-aware"):
        _to_utc_milliseconds(naive)
    with pytest.raises(ValidationError, match="must be timezone-aware"):
        DeviceDocument(uuid="u", name="n", file_type=DeviceFileType.NOTEBOOK, last_modified=naive)
    with pytest.raises(ValidationError, match="must be timezone-aware"):
        DeviceFolder(uuid="u", name="n", last_modified="2024-03-01T12:00:00")


def test_an_offset_timestamp_is_normalized_to_utc_without_moving_the_instant():
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    local = datetime.datetime(2024, 3, 1, 12, 0, 0, 123456, tzinfo=tz)
    folder = DeviceFolder(uuid="f", name="f", last_modified=local)
    assert folder.last_modified is not None
    assert folder.last_modified.tzinfo is datetime.UTC
    assert folder.last_modified == datetime.datetime(
        2024, 3, 1, 6, 30, 0, 123000, tzinfo=datetime.UTC
    )


def test_two_transports_reporting_one_instant_compare_equal():
    tz = datetime.timezone(datetime.timedelta(hours=-8))
    over_ssh = _notebook(last_modified=datetime.datetime(2024, 3, 1, 4, 0, 0, 500999, tzinfo=tz))
    over_usb = _notebook(
        last_modified=datetime.datetime(2024, 3, 1, 12, 0, 0, 500123, tzinfo=datetime.UTC)
    )
    assert over_ssh.last_modified == over_usb.last_modified
    assert over_ssh == over_usb


def test_an_epoch_number_arrives_timezone_aware():
    document = DeviceDocument.model_validate(
        {"uuid": "d", "name": "d", "file_type": "notebook", "last_modified": 1_700_000_000}
    )
    assert document.last_modified is not None
    assert document.last_modified.tzinfo is not None
    assert document.last_modified == datetime.datetime(
        2023, 11, 14, 22, 13, 20, tzinfo=datetime.UTC
    )


@given(value=_AWARE)
def test_normalization_floors_to_whole_milliseconds_and_is_idempotent(value: datetime.datetime):
    once = _to_utc_milliseconds(value)
    assert once.tzinfo is datetime.UTC
    assert once.microsecond % 1000 == 0
    assert once <= value
    assert value - once < datetime.timedelta(milliseconds=1)
    assert _to_utc_milliseconds(once) == once


@given(value=_AWARE)
def test_normalization_through_a_model_matches_the_validator(value: datetime.datetime):
    assert _notebook(last_modified=value).last_modified == _to_utc_milliseconds(value)


# ───────────────────────────── the unsupported-set rule ─────────────────────────────


def test_check_unsupported_accepts_an_empty_set_and_absent_fields():
    _check_unsupported(frozenset(), {"firmware": None})
    _check_unsupported(frozenset({"firmware"}), {"firmware": None, "model": "rmpp"})


def test_check_unsupported_reports_unknown_names_sorted():
    with pytest.raises(ValueError, match=r"do not exist: alpha, zeta"):
        _check_unsupported(frozenset({"zeta", "alpha"}), {"firmware": None})


def test_check_unsupported_reports_answered_names_sorted():
    with pytest.raises(ValueError, match=r"carry a value: firmware, model"):
        _check_unsupported(frozenset({"model", "firmware"}), {"firmware": "3.27", "model": "rmpp"})


def test_check_unsupported_reports_an_unknown_name_before_an_answered_one():
    with pytest.raises(ValueError, match="do not exist"):
        _check_unsupported(frozenset({"nope", "firmware"}), {"firmware": "3.27"})


def test_facts_default_to_all_unknown_and_nothing_unsupported():
    facts = DeviceFacts()
    assert (facts.firmware, facts.model, facts.serial) == (None, None, None)
    assert facts.unsupported == frozenset()


def test_a_transport_that_cannot_ask_names_the_field_instead_of_raising():
    facts = DeviceFacts(firmware="3.27.3.0", model="rmpp", unsupported=["serial"])
    assert facts.serial is None
    assert facts.unsupported == frozenset({"serial"})


def test_unsupported_cannot_name_a_field_that_carries_a_value():
    with pytest.raises(ValidationError, match="carry a value: firmware"):
        DeviceFacts(firmware="3.27.3.0", unsupported={"firmware"})


def test_unsupported_cannot_name_a_non_field_including_itself():
    with pytest.raises(ValidationError, match="do not exist: unsupported"):
        DeviceFacts(unsupported={"unsupported"})
    with pytest.raises(ValidationError, match="do not exist: battery"):
        DeviceFacts(unsupported={"battery"})


@given(
    firmware=st.none() | st.text(min_size=1, max_size=6),
    model=st.none() | st.text(min_size=1, max_size=6),
    serial=st.none() | st.text(min_size=1, max_size=6),
)
def test_unsupported_may_name_exactly_the_unanswered_facts(
    firmware: str | None, model: str | None, serial: str | None
):
    answered = {"firmware": firmware, "model": model, "serial": serial}
    absent = frozenset(name for name, value in answered.items() if value is None)
    facts = DeviceFacts(firmware=firmware, model=model, serial=serial, unsupported=absent)
    assert facts.unsupported == absent
    present = frozenset(answered) - absent
    if present:
        with pytest.raises(ValidationError, match="carry a value"):
            DeviceFacts(
                firmware=firmware, model=model, serial=serial, unsupported=absent | present
            )


# ───────────────────────────── resources: the gauge-pair rule ─────────────────────────────


def test_a_reading_may_report_a_full_partition():
    reading = DeviceResources(
        total_memory_bytes=8,
        available_memory_bytes=8,
        total_storage_bytes=128,
        available_storage_bytes=0,
    )
    assert reading.available_memory_bytes == reading.total_memory_bytes
    assert reading.available_storage_bytes == 0


def test_free_memory_above_its_total_is_the_mis_read_column_signature():
    with pytest.raises(ValidationError, match="available_memory_bytes exceeds total_memory_bytes"):
        DeviceResources(total_memory_bytes=8, available_memory_bytes=9)


def test_free_storage_above_its_total_is_rejected():
    with pytest.raises(
        ValidationError, match="available_storage_bytes exceeds total_storage_bytes"
    ):
        DeviceResources(total_storage_bytes=128, available_storage_bytes=129)


def test_one_unread_half_of_a_pair_does_not_trip_the_gauge_rule():
    assert DeviceResources(available_memory_bytes=9).total_memory_bytes is None
    assert DeviceResources(total_storage_bytes=1).available_storage_bytes is None


def test_a_negative_gauge_is_rejected():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        DeviceResources(available_storage_bytes=-1)


def test_a_zero_reading_counts_as_answered_for_the_unsupported_rule():
    with pytest.raises(ValidationError, match="carry a value: total_storage_bytes"):
        DeviceResources(total_storage_bytes=0, unsupported={"total_storage_bytes"})


def test_a_transport_may_declare_a_whole_gauge_pair_unsupported():
    reading = DeviceResources(
        total_storage_bytes=128,
        available_storage_bytes=64,
        unsupported=["total_memory_bytes", "available_memory_bytes"],
    )
    assert reading.unsupported == frozenset({"total_memory_bytes", "available_memory_bytes"})


def test_resources_unsupported_cannot_name_a_fact_field():
    with pytest.raises(ValidationError, match="do not exist: firmware"):
        DeviceResources(unsupported={"firmware"})


@given(
    total=st.integers(min_value=0, max_value=1_000),
    free=st.integers(min_value=0, max_value=1_000),
)
def test_a_reading_validates_exactly_when_free_does_not_exceed_total(total: int, free: int):
    if free <= total:
        reading = DeviceResources(total_memory_bytes=total, available_memory_bytes=free)
        assert reading.available_memory_bytes == free
    else:
        with pytest.raises(ValidationError, match="exceeds total_memory_bytes"):
            DeviceResources(total_memory_bytes=total, available_memory_bytes=free)


def test_facts_and_resources_are_separate_types_so_a_cache_cannot_mix_them():
    assert set(DeviceFacts.model_fields) & set(DeviceResources.model_fields) == {"unsupported"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeviceFacts.model_validate({"available_storage_bytes": 1})


# ───────────────────────────── documents and folders ─────────────────────────────


def test_a_folder_cannot_carry_document_only_facts():
    assert "page_count" not in DeviceFolder.model_fields
    assert "file_type" not in DeviceFolder.model_fields
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeviceFolder.model_validate({"uuid": "f", "name": "f", "page_count": 3})


def test_a_folder_defaults_to_a_live_root_entry_with_no_known_time():
    folder = DeviceFolder(uuid="f", name="Books")
    assert (folder.parent_uuid, folder.last_modified, folder.trashed) == (None, None, False)


def test_an_empty_identifier_is_rejected_everywhere_one_is_required():
    with pytest.raises(ValidationError, match="at least 1 character"):
        DeviceFolder(uuid="", name="f")
    with pytest.raises(ValidationError, match="at least 1 character"):
        DeviceDocument(uuid="", name="d", file_type=DeviceFileType.NOTEBOOK)
    with pytest.raises(ValidationError, match="at least 1 character"):
        DevicePageSource(page_id="")
    with pytest.raises(ValidationError, match="at least 1 character"):
        UploadRequest(name="", media=UploadMedia.PDF, data=b"x")


def test_a_document_requires_its_file_type_and_defaults_the_rest():
    with pytest.raises(ValidationError, match="file_type"):
        DeviceDocument.model_validate({"uuid": "d", "name": "d"})
    document = _notebook()
    assert (document.parent_uuid, document.page_count, document.trashed) == (None, None, False)


def test_page_count_is_device_reported_and_never_negative():
    assert _notebook(page_count=0).page_count == 0
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _notebook(page_count=-1)


def test_a_trashed_document_still_names_its_real_parent():
    document = _notebook(parent_uuid="folder-7", trashed=True)
    assert document.trashed is True
    assert document.parent_uuid == "folder-7"


def test_documents_are_frozen_hashable_values():
    document = _notebook()
    with pytest.raises(ValidationError, match="frozen"):
        document.name = "renamed"  # ty: ignore[invalid-assignment]
    assert {document, _notebook()} == {document}
    assert document == _notebook()
    assert document != _notebook(page_count=1)


def test_a_document_round_trips_through_a_plain_dict():
    document = _notebook(
        parent_uuid="f",
        page_count=4,
        trashed=True,
        last_modified=datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
    )
    assert DeviceDocument.model_validate(document.model_dump()) == document


# ───────────────────────────── listings ─────────────────────────────


def test_a_listing_cannot_be_built_without_stating_folders_and_skips():
    with pytest.raises(ValidationError) as excinfo:
        DeviceListing.model_validate({"documents": []})
    missing = {error["loc"][0] for error in excinfo.value.errors()}
    assert missing == {"folders", "skipped"}


def test_an_empty_library_is_representable():
    listing = DeviceListing(documents=(), folders=(), skipped=())
    assert listing.documents == ()
    assert listing.skipped == ()


def test_a_listing_freezes_the_sequences_it_was_given():
    documents = [_notebook("a"), _notebook("b")]
    listing = DeviceListing(documents=documents, folders=[], skipped=[])
    assert isinstance(listing.documents, tuple)
    documents.append(_notebook("c"))
    assert len(listing.documents) == 2
    with pytest.raises(ValidationError, match="frozen"):
        listing.documents = ()  # ty: ignore[invalid-assignment]


def test_documents_and_folders_stay_in_separate_halves():
    with pytest.raises(ValidationError, match="valid dictionary or instance of DeviceDocument"):
        DeviceListing.model_validate(
            {"documents": (DeviceFolder(uuid="f", name="f"),), "folders": (), "skipped": ()}
        )
    with pytest.raises(ValidationError, match="valid dictionary or instance of DeviceFolder"):
        DeviceListing.model_validate({"documents": (), "folders": (_notebook(),), "skipped": ()})


def test_a_skipped_entry_needs_an_explicit_identifier_even_when_unknown():
    with pytest.raises(ValidationError) as excinfo:
        SkippedEntry.model_validate({"reason": SkipReason.UNREADABLE, "detail": "denied"})
    assert {error["loc"][0] for error in excinfo.value.errors()} == {"uuid"}
    anonymous = SkippedEntry(uuid=None, reason=SkipReason.UNREADABLE, detail="permission denied")
    assert anonymous.uuid is None


def test_a_skipped_entry_carries_a_diagnostic_and_a_closed_set_reason():
    with pytest.raises(ValidationError, match="detail"):
        SkippedEntry.model_validate({"uuid": "u", "reason": SkipReason.MALFORMED_METADATA})
    with pytest.raises(ValidationError, match="reason"):
        SkippedEntry(uuid="u", reason="whoops", detail="d")


def test_a_partially_readable_library_reports_the_failures_as_data():
    listing = DeviceListing(
        documents=(_notebook("good"),),
        folders=(DeviceFolder(uuid="folder", name="Books"),),
        skipped=(
            SkippedEntry(uuid="bad", reason=SkipReason.MALFORMED_METADATA, detail="not json"),
        ),
    )
    assert [entry.uuid for entry in listing.skipped] == ["bad"]
    assert [document.uuid for document in listing.documents] == ["good"]


# ───────────────────────────── bundles ─────────────────────────────


def test_a_notebook_bundle_carries_no_underlay():
    bundle = _bundle_for(_notebook(), ("p1", "p2"))
    assert bundle.base is None
    assert [page.page_id for page in bundle.pages] == ["p1", "p2"]


def test_a_notebook_with_an_underlay_is_unrepresentable():
    with pytest.raises(ValidationError, match="a notebook has no underlay"):
        DocumentSourceBundle(document=_notebook(), pages=(), base=b"%PDF-1.7")


@pytest.mark.parametrize("file_type", [DeviceFileType.PDF, DeviceFileType.EPUB])
def test_an_annotated_document_must_carry_its_underlay(file_type: DeviceFileType):
    document = DeviceDocument(uuid="d", name="d", file_type=file_type)
    with pytest.raises(ValidationError, match="must carry its underlay in base"):
        DocumentSourceBundle(document=document, pages=())
    assert DocumentSourceBundle(document=document, pages=(), base=b"payload").base == b"payload"


def test_a_repeated_page_identifier_is_rejected_and_named():
    with pytest.raises(ValidationError, match=r"repeats the page identifier 'p1'"):
        DocumentSourceBundle(
            document=_notebook(),
            pages=(DevicePageSource(page_id="p1"), DevicePageSource(page_id="p1")),
        )


def test_an_unannotated_pdf_keeps_its_full_page_order_with_no_scenes():
    bundle = _bundle_for(_annotated_pdf(), ("p1", "p2", "p3"))
    assert all(page.scene is None for page in bundle.pages)
    assert len(bundle.pages) == 3


def test_an_empty_page_tuple_means_a_document_with_no_pages():
    bundle = DocumentSourceBundle(document=_notebook(), pages=())
    assert bundle.pages == ()


def test_a_page_carries_its_own_template_rather_than_a_parallel_list():
    page = DevicePageSource(page_id="p1", scene=b"v6-scene", template_name="Grid")
    bundle = DocumentSourceBundle(document=_notebook(), pages=(page,))
    assert bundle.pages[0].template_name == "Grid"
    assert bundle.pages[0].scene == b"v6-scene"
    assert DevicePageSource(page_id="p2").template_name is None


def test_no_wire_format_crosses_the_bundle_boundary():
    assert set(DocumentSourceBundle.model_fields) == {"document", "pages", "base"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DocumentSourceBundle.model_validate(
            {"document": _notebook(), "pages": (), "content": b"{}"}
        )


def test_a_bundle_round_trips_through_a_plain_dict_with_raw_bytes():
    bundle = DocumentSourceBundle(
        document=_annotated_pdf(),
        pages=(DevicePageSource(page_id="p1", scene=b"\x80\x01v6"),),
        base=b"%PDF-1.7\x00",
    )
    assert DocumentSourceBundle.model_validate(bundle.model_dump()) == bundle


@given(page_ids=st.lists(st.text(min_size=1, max_size=4), min_size=0, max_size=6))
def test_a_bundle_validates_exactly_when_its_page_identifiers_are_unique(page_ids: list[str]):
    document = _notebook()
    if len(set(page_ids)) == len(page_ids):
        bundle = _bundle_for(document, tuple(page_ids))
        assert [page.page_id for page in bundle.pages] == page_ids
    else:
        with pytest.raises(ValidationError, match="repeats the page identifier"):
            _bundle_for(document, tuple(page_ids))


# ───────────────────────────── upload values ─────────────────────────────


def test_an_upload_request_is_bytes_with_a_media_and_an_optional_destination():
    request = UploadRequest(name="spec.pdf", media=UploadMedia.PDF, data=b"%PDF-1.7")
    assert request.parent_uuid is None
    assert request.data == b"%PDF-1.7"
    assert "path" not in UploadRequest.model_fields
    assert "content_type" not in UploadRequest.model_fields


def test_an_upload_request_requires_a_media_from_the_closed_set():
    with pytest.raises(ValidationError, match="media"):
        UploadRequest.model_validate({"name": "n", "data": b"x"})
    with pytest.raises(ValidationError, match="media"):
        UploadRequest(name="n", media="notebook", data=b"x")


def test_an_empty_payload_is_the_uploader_s_problem_not_the_model_s():
    assert UploadRequest(name="n", media=UploadMedia.EPUB, data=b"").data == b""


def test_a_receipt_states_an_absent_identifier_explicitly():
    with pytest.raises(ValidationError) as excinfo:
        UploadReceipt.model_validate(
            {
                "name": "n",
                "media": UploadMedia.PDF,
                "byte_count": 1,
                "library_refresh": LibraryRefresh.ALREADY_VISIBLE,
            }
        )
    assert {error["loc"][0] for error in excinfo.value.errors()} == {"doc_uuid"}
    receipt = UploadReceipt(
        doc_uuid=None,
        name="n",
        media=UploadMedia.PDF,
        byte_count=1,
        library_refresh=LibraryRefresh.ALREADY_VISIBLE,
    )
    assert receipt.doc_uuid is None


def test_a_receipt_cannot_report_a_negative_byte_count_or_omit_visibility():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        UploadReceipt(
            doc_uuid=None,
            name="n",
            media=UploadMedia.PDF,
            byte_count=-1,
            library_refresh=LibraryRefresh.ALREADY_VISIBLE,
        )
    with pytest.raises(ValidationError, match="library_refresh"):
        UploadReceipt.model_validate(
            {"doc_uuid": None, "name": "n", "media": UploadMedia.PDF, "byte_count": 0}
        )


# ───────────────────────────── reference fakes ─────────────────────────────


class _InMemoryCatalog:
    """A :class:`DeviceCatalog` over one listing, obeying the port's coherence rules."""

    def __init__(self, listing: DeviceListing, transport: TransportKind) -> None:
        self._listing = listing
        self._transport = transport

    def list_documents(self) -> DeviceListing:
        """Return the whole library, trashed entries included."""
        return self._listing

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Return one listed document, or raise what the port documents for it."""
        for document in self._listing.documents:
            if document.uuid == doc_uuid:
                return document
        for entry in self._listing.skipped:
            if entry.uuid == doc_uuid:
                raise MalformedDeviceMetadata(
                    transport=self._transport, detail=entry.detail, document_uuid=doc_uuid
                )
        raise DeviceDocumentNotFound(transport=self._transport, document_uuid=doc_uuid)


class _InMemoryBundleSource:
    """A :class:`RawBundleSource` over pre-decoded bundles, with one truncating identifier."""

    def __init__(self, bundles: dict[str, DocumentSourceBundle], truncated: str) -> None:
        self._bundles = bundles
        self._truncated = truncated

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Return one document's whole source, or raise; never a partial bundle."""
        if doc_uuid == self._truncated:
            raise DeviceTransferInterrupted(
                transport=TransportKind.SSH,
                subject=doc_uuid,
                bytes_transferred=7,
                bytes_expected=64,
            )
        try:
            return self._bundles[doc_uuid]
        except KeyError as exc:
            raise DeviceDocumentNotFound(
                transport=TransportKind.SSH, document_uuid=doc_uuid
            ) from exc


class _StubUploader:
    """A :class:`DocumentUploader` whose wire limits are declared, never negotiated."""

    def __init__(
        self,
        *,
        transport: TransportKind,
        accepts_destination: bool,
        media: frozenset[UploadMedia],
        refresh: LibraryRefresh,
    ) -> None:
        self.placed: list[UploadRequest] = []
        self._transport = transport
        self._accepts_destination = accepts_destination
        self._media = media
        self._refresh = refresh

    def upload(self, request: UploadRequest, /) -> UploadReceipt:
        """Place one document, or refuse the request before writing anything."""
        if request.parent_uuid is not None and not self._accepts_destination:
            raise DeviceOperationUnsupported(
                transport=self._transport,
                operation="upload into a folder",
                supported_by=(TransportKind.SSH,),
            )
        if request.media not in self._media:
            raise DeviceOperationUnsupported(
                transport=self._transport,
                operation=f"upload {request.media.value}",
                supported_by=(TransportKind.SSH,),
            )
        self.placed.append(request)
        return UploadReceipt(
            doc_uuid=f"minted-{len(self.placed)}" if self._accepts_destination else None,
            name=request.name,
            media=request.media,
            byte_count=len(request.data),
            library_refresh=self._refresh,
        )


class _StubIndexSource:
    """A :class:`SearchIndexSource` over one in-memory image, or none at all.

    The image is bytes rather than a path or a cursor because there is no ``sqlite3`` binary on
    the device and no BusyBox applet for one, so an on-device query is not an available shape.
    """

    def __init__(self, image: bytes | None) -> None:
        self.reads = 0
        self._image = image

    def read_index(self) -> bytes | None:
        """Return the whole database image, or ``None`` when the device has no index."""
        self.reads += 1
        return self._image


class _StubFactsSource:
    """A :class:`DeviceFactsSource` with fixed facts and a draining storage gauge."""

    def __init__(self) -> None:
        self.reads = 0

    def read_facts(self) -> DeviceFacts:
        """Return the fixed facts, naming what this transport cannot ask."""
        return DeviceFacts(firmware="3.27.3.0", model="rmpp", unsupported=frozenset({"serial"}))

    def read_resources(self) -> DeviceResources:
        """Return a fresh gauge reading, lower on every call."""
        self.reads += 1
        return DeviceResources(
            total_storage_bytes=1_000,
            available_storage_bytes=1_000 - self.reads,
            unsupported=frozenset({"total_memory_bytes", "available_memory_bytes"}),
        )


_LISTING = DeviceListing(
    documents=(_notebook("live"), _notebook("gone", trashed=True), _annotated_pdf()),
    folders=(DeviceFolder(uuid="folder", name="Books"),),
    skipped=tuple(
        SkippedEntry(
            uuid=f"bad-{reason.value}", reason=reason, detail=f"detail for {reason.value}"
        )
        for reason in SkipReason
    ),
)


# ───────────────────────────── catalog contract ─────────────────────────────


def test_the_catalog_returns_both_halves_and_every_failure():
    catalog: DeviceCatalog = _InMemoryCatalog(_LISTING, TransportKind.SSH)
    listing = catalog.list_documents()
    assert [document.uuid for document in listing.documents] == ["live", "gone", "doc-pdf"]
    assert [folder.uuid for folder in listing.folders] == ["folder"]
    assert len(listing.skipped) == len(SkipReason)


def test_the_catalog_reports_a_deleted_document_as_trashed_rather_than_hiding_it():
    catalog: DeviceCatalog = _InMemoryCatalog(_LISTING, TransportKind.LOCAL_MIRROR)
    listing = catalog.list_documents()
    live = [document.uuid for document in listing.documents if not document.trashed]
    assert live == ["live", "doc-pdf"]
    assert catalog.get_document("gone").trashed is True


def test_a_listed_identifier_resolves_to_the_listed_document():
    catalog: DeviceCatalog = _InMemoryCatalog(_LISTING, TransportKind.SSH)
    listing = catalog.list_documents()
    for document in listing.documents:
        assert catalog.get_document(document.uuid) == document


@pytest.mark.parametrize("reason", list(SkipReason))
def test_every_skip_reason_becomes_malformed_metadata_when_asked_by_identifier(
    reason: SkipReason,
):
    catalog: DeviceCatalog = _InMemoryCatalog(_LISTING, TransportKind.SSH)
    with pytest.raises(MalformedDeviceMetadata) as excinfo:
        catalog.get_document(f"bad-{reason.value}")
    assert excinfo.value.document_uuid == f"bad-{reason.value}"
    assert not isinstance(excinfo.value, DeviceDocumentNotFound)


def test_a_folder_identifier_is_not_a_document():
    catalog: DeviceCatalog = _InMemoryCatalog(_LISTING, TransportKind.SSH)
    with pytest.raises(DeviceDocumentNotFound) as excinfo:
        catalog.get_document("folder")
    assert excinfo.value.document_uuid == "folder"
    assert excinfo.value.transport is TransportKind.SSH


def test_an_unknown_identifier_raises_rather_than_returning_nothing():
    catalog: DeviceCatalog = _InMemoryCatalog(_LISTING, TransportKind.USB_WEB_API)
    with pytest.raises(DeviceDocumentNotFound):
        catalog.get_document("no-such-uuid")


# ───────────────────────────── bundle source contract ─────────────────────────────


def test_a_loaded_bundle_belongs_to_the_requested_document():
    bundles = {"live": _bundle_for(_notebook("live"), ("p1", "p2"))}
    source: RawBundleSource = _InMemoryBundleSource(bundles, truncated="half")
    bundle = source.load_bundle("live")
    assert bundle.document.uuid == "live"
    assert [page.page_id for page in bundle.pages] == ["p1", "p2"]


def test_a_truncated_transfer_raises_instead_of_yielding_a_bundle_with_holes():
    source: RawBundleSource = _InMemoryBundleSource({}, truncated="half")
    with pytest.raises(DeviceTransferInterrupted) as excinfo:
        source.load_bundle("half")
    assert excinfo.value.bytes_transferred == 7
    assert excinfo.value.subject == "half"


def test_an_unknown_document_has_no_bundle():
    source: RawBundleSource = _InMemoryBundleSource({}, truncated="half")
    with pytest.raises(DeviceDocumentNotFound):
        source.load_bundle("missing")


# ───────────────────────────── uploader contract ─────────────────────────────


def _ssh_uploader() -> _StubUploader:
    """Return an uploader whose wire has a destination parameter."""
    return _StubUploader(
        transport=TransportKind.SSH,
        accepts_destination=True,
        media=frozenset(UploadMedia),
        refresh=LibraryRefresh.VISIBILITY_FORCED,
    )


def _usb_uploader() -> _StubUploader:
    """Return an uploader modelled on the firmware's single-route POST /upload."""
    return _StubUploader(
        transport=TransportKind.USB_WEB_API,
        accepts_destination=False,
        media=frozenset({UploadMedia.PDF}),
        refresh=LibraryRefresh.ALREADY_VISIBLE,
    )


def test_a_receipt_reports_exactly_the_bytes_that_were_offered():
    uploader: DocumentUploader = _ssh_uploader()
    request = UploadRequest(name="spec.pdf", media=UploadMedia.PDF, data=b"%PDF-1.7 body")
    receipt = uploader.upload(request)
    assert receipt.byte_count == len(request.data)
    assert (receipt.name, receipt.media) == (request.name, request.media)
    assert receipt.library_refresh is LibraryRefresh.VISIBILITY_FORCED
    assert receipt.doc_uuid == "minted-1"


def test_visibility_is_per_upload_so_n_documents_give_n_receipts():
    stub = _ssh_uploader()
    uploader: DocumentUploader = stub
    receipts = [
        uploader.upload(UploadRequest(name=f"d{index}.pdf", media=UploadMedia.PDF, data=b"x"))
        for index in range(3)
    ]
    assert [receipt.doc_uuid for receipt in receipts] == ["minted-1", "minted-2", "minted-3"]
    assert all(receipt.library_refresh is LibraryRefresh.VISIBILITY_FORCED for receipt in receipts)
    assert len(stub.placed) == 3


def test_a_destination_a_wire_cannot_express_is_refused_not_dropped():
    stub = _usb_uploader()
    uploader: DocumentUploader = stub
    request = UploadRequest(
        name="spec.pdf", media=UploadMedia.PDF, data=b"%PDF", parent_uuid="folder"
    )
    with pytest.raises(DeviceOperationUnsupported) as excinfo:
        uploader.upload(request)
    assert excinfo.value.operation == "upload into a folder"
    assert excinfo.value.transport is TransportKind.USB_WEB_API
    assert TransportKind.SSH in excinfo.value.supported_by
    assert "retry with ssh" in str(excinfo.value.remediation)
    assert stub.placed == []


def test_a_media_a_wire_cannot_place_is_refused_before_anything_is_written():
    stub = _usb_uploader()
    uploader: DocumentUploader = stub
    with pytest.raises(DeviceOperationUnsupported) as excinfo:
        uploader.upload(UploadRequest(name="book.epub", media=UploadMedia.EPUB, data=b"PK"))
    assert excinfo.value.operation == "upload epub"
    assert stub.placed == []


def test_the_same_request_succeeds_over_the_transport_that_can_honor_it():
    stub = _ssh_uploader()
    uploader: DocumentUploader = stub
    request = UploadRequest(
        name="book.epub", media=UploadMedia.EPUB, data=b"PK\x03\x04", parent_uuid="folder"
    )
    receipt = uploader.upload(request)
    assert receipt.media is UploadMedia.EPUB
    assert stub.placed == [request]


def test_an_upload_at_the_library_root_needs_no_destination_support():
    stub = _usb_uploader()
    uploader: DocumentUploader = stub
    receipt = uploader.upload(UploadRequest(name="spec.pdf", media=UploadMedia.PDF, data=b"%PDF"))
    assert receipt.doc_uuid is None
    assert receipt.library_refresh is LibraryRefresh.ALREADY_VISIBLE
    assert len(stub.placed) == 1


# ───────────────────────────── facts source contract ─────────────────────────────


def test_a_facts_source_answers_with_unsupported_instead_of_raising():
    source: DeviceFactsSource = _StubFactsSource()
    facts = source.read_facts()
    assert facts.serial is None
    assert facts.unsupported == frozenset({"serial"})
    assert facts.firmware == "3.27.3.0"


def test_fixed_facts_are_cacheable_while_each_gauge_reading_is_fresh():
    stub = _StubFactsSource()
    source: DeviceFactsSource = stub
    assert source.read_facts() == source.read_facts()
    first = source.read_resources()
    second = source.read_resources()
    assert first != second
    assert first.available_storage_bytes is not None
    assert second.available_storage_bytes is not None
    assert second.available_storage_bytes < first.available_storage_bytes
    assert stub.reads == 2


def test_a_gauge_reading_is_internally_consistent_with_its_total():
    source: DeviceFactsSource = _StubFactsSource()
    reading = source.read_resources()
    assert reading.total_storage_bytes is not None
    assert reading.available_storage_bytes is not None
    assert reading.available_storage_bytes <= reading.total_storage_bytes
    assert "total_memory_bytes" in reading.unsupported


def test_neither_facts_method_takes_a_shell_command():
    source: DeviceFactsSource = _StubFactsSource()
    assert source.read_facts() is not None
    assert source.read_resources() is not None
    assert DeviceFactsSource.read_facts.__code__.co_varnames[:1] == ("self",)
    assert DeviceFactsSource.read_facts.__code__.co_argcount == 1
    assert DeviceFactsSource.read_resources.__code__.co_argcount == 1


# ───────────────────────────── search index source contract ─────────────────────────────


def test_the_index_source_hands_over_the_whole_image_as_bytes():
    # 503,808 bytes on the measured device, with no -wal and no -shm sidecar, so the file is a
    # self-contained image and one read per command is cheap. Bytes rather than a path because no
    # port here touches a filesystem, and rather than a query interface because the device has no
    # sqlite3 binary and no BusyBox applet for one.
    image = b"SQLite format 3\x00" + bytes(48)
    source: SearchIndexSource = _StubIndexSource(image)

    assert source.read_index() == image


def test_a_device_with_no_index_answers_none_and_never_empty_bytes():
    # `None` is the honest state of a device that has not built an index yet, and it is what a
    # caller distinguishes from an index that exists and holds no row for a page. `b""` would
    # collapse the two -- and would then reach the reader, where an empty image raises
    # `MemoryError` rather than a database error.
    source: SearchIndexSource = _StubIndexSource(None)
    answer = source.read_index()

    assert answer is None
    assert answer != b""


def test_the_index_read_takes_no_arguments_so_it_cannot_become_a_per_page_read():
    # One image per command, read once and reused for every page. A parameter here would invite
    # a per-page transport read of a half-megabyte file.
    assert SearchIndexSource.read_index.__code__.co_varnames[:1] == ("self",)
    assert SearchIndexSource.read_index.__code__.co_argcount == 1


def test_the_index_source_publishes_transport_and_nothing_about_reading():
    # The split the sqlite3 ban forces: this half moves bytes, and `HandwrittenTextIndex` in the
    # OCR slice opens them. A `lookup` or a `provider_id` here would put both in one package.
    assert get_protocol_members(SearchIndexSource) == {"read_index"}


@pytest.mark.parametrize(
    "port",
    [DeviceCatalog, RawBundleSource, DocumentUploader, DeviceFactsSource, SearchIndexSource],
)
def test_the_device_ports_are_not_runtime_checkable(port: type):
    # Nothing needs `isinstance` against them, and a structural check at runtime would only
    # verify member names -- so the type gate, not the interpreter, is what checks conformance.
    with pytest.raises(TypeError, match="runtime_checkable"):
        isinstance(_StubIndexSource(None), port)
