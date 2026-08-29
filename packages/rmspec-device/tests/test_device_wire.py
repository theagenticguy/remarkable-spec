"""The USB ``/documents/`` entry decoder.

Every fixture here is **synthesised** from the measured shape rather than captured: a real
listing body carries the user's document titles, and nothing from the device is committed.
Each builder's docstring names the shape it reproduces, which is what keeps it honest as
the spec moves.

Table-driven where the rows are one decision each: the ``SkipReason`` split is the whole
point of this module, so both members it can produce are enumerated against every input
that reaches them.
"""

from __future__ import annotations

import datetime
import json

import pytest

from rmspec.device._wire import (
    COLLECTION_TYPE,
    DOCUMENT_TYPE,
    LISTING_ROUTE,
    DecodedEntries,
    decode_entries,
    entry_id,
    entry_parent,
    is_collection,
)
from rmspec.domain.errors import DeviceProtocolError, TransportKind
from rmspec.domain.ports.device import DeviceFileType, SkipReason

MODIFIED = "2026-08-29T14:52:11.412Z"
"""One ``ModifiedClient`` value in the measured shape ``9999-99-99T99:99:99.999Z``."""


def document_entry(*, without: tuple[str, ...] = (), **overrides: object) -> dict[str, object]:
    """Build a ``DocumentType`` entry: the 9 keys measured on firmware 3.27.3.0.

    ``Bookmarked``, ``CurrentPage``, ``ID``, ``ModifiedClient``, ``Parent``, ``Type``,
    ``VisibleName``, ``VissibleName`` (sic, equal to ``VisibleName`` on all 31 entries
    observed) and ``fileType``. ``CurrentPage`` is the last-opened page index, not a count.
    """
    entry: dict[str, object] = {
        "Bookmarked": False,
        "CurrentPage": 3,
        "ID": "11111111-2222-3333-4444-555555555555",
        "ModifiedClient": MODIFIED,
        "Parent": "",
        "Type": DOCUMENT_TYPE,
        "VisibleName": "Sprint notes",
        "VissibleName": "Sprint notes",
        "fileType": "notebook",
    }
    entry.update(overrides)
    for key in without:
        del entry[key]
    return entry


def folder_entry(*, without: tuple[str, ...] = (), **overrides: object) -> dict[str, object]:
    """Build a ``CollectionType`` entry: the 7 measured keys, the document set minus two.

    ``CurrentPage`` and ``fileType`` are absent, because a folder has neither a
    last-opened page nor an underlay.
    """
    entry = document_entry(without=("CurrentPage", "fileType"), Type=COLLECTION_TYPE)
    entry["ID"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    entry["VisibleName"] = "Projects"
    entry["VissibleName"] = "Projects"
    entry.update(overrides)
    for key in without:
        del entry[key]
    return entry


def payload(*entries: object) -> bytes:
    """Render a ``/documents/`` response body: a bare json array of flat objects."""
    return json.dumps(list(entries)).encode()


def only_skip(*entries: object) -> tuple[str | None, SkipReason, str]:
    """Decode a payload expected to yield exactly one skip, and return it flattened."""
    decoded = decode_entries(payload(*entries))
    assert decoded.documents == ()
    assert decoded.folders == ()
    assert len(decoded.skipped) == 1
    skipped = decoded.skipped[0]
    return skipped.uuid, skipped.reason, skipped.detail


# ─────────────────────────────── the mapped fields ───────────────────────────────


def test_a_document_entry_maps_every_field_the_port_declares():
    decoded = decode_entries(payload(document_entry(fileType="pdf", Parent="parent-uuid")))

    assert decoded.folders == ()
    assert decoded.skipped == ()
    document = decoded.documents[0]
    assert document.uuid == "11111111-2222-3333-4444-555555555555"
    assert document.name == "Sprint notes"
    assert document.file_type is DeviceFileType.PDF
    assert document.parent_uuid == "parent-uuid"
    assert document.last_modified == datetime.datetime(
        2026, 8, 29, 14, 52, 11, 412000, tzinfo=datetime.UTC
    )


def test_page_count_is_none_even_though_the_wire_carries_currentpage():
    """``CurrentPage`` is the last-opened page index; the wire carries no count at all."""
    decoded = decode_entries(payload(document_entry(CurrentPage=417)))

    assert decoded.documents[0].page_count is None


def test_trashed_is_false_because_the_api_filters_trashed_entries_out_entirely():
    """41 of 42 entities are reachable and no listing ever carries ``Parent == "trash"``.

    ``False`` is accurate rather than assumed: the decoder never sees a trashed entry, so
    it needs -- and has -- no sentinel branch.
    """
    decoded = decode_entries(payload(document_entry(), folder_entry()))

    assert decoded.documents[0].trashed is False
    assert decoded.folders[0].trashed is False


def test_an_empty_parent_means_the_library_root():
    decoded = decode_entries(payload(document_entry(Parent=""), folder_entry(Parent="")))

    assert decoded.documents[0].parent_uuid is None
    assert decoded.folders[0].parent_uuid is None


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        pytest.param("notebook", DeviceFileType.NOTEBOOK, id="notebook, measured"),
        pytest.param("pdf", DeviceFileType.PDF, id="pdf, measured"),
        pytest.param("epub", DeviceFileType.EPUB, id="epub, unmeasured but a member"),
    ],
)
def test_every_filetype_the_domain_represents_is_accepted(wire: str, expected: DeviceFileType):
    decoded = decode_entries(payload(document_entry(fileType=wire)))

    assert decoded.documents[0].file_type is expected


def test_a_collection_entry_becomes_a_folder_with_the_same_four_fields():
    decoded = decode_entries(payload(folder_entry(Parent="grandparent")))

    assert decoded.documents == ()
    folder = decoded.folders[0]
    assert folder.uuid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert folder.name == "Projects"
    assert folder.parent_uuid == "grandparent"
    assert folder.last_modified is not None


def test_modifiedclient_is_normalised_to_utc_at_millisecond_precision():
    """An offset is honoured and sub-millisecond precision is discarded by the port."""
    decoded = decode_entries(payload(document_entry(ModifiedClient="2026-08-29T16:00:00+02:00")))

    assert decoded.documents[0].last_modified == datetime.datetime(
        2026, 8, 29, 14, 0, tzinfo=datetime.UTC
    )


def test_payload_order_is_preserved_within_each_kind():
    decoded = decode_entries(
        payload(
            document_entry(ID="doc-b"),
            folder_entry(ID="folder-b"),
            document_entry(ID="doc-a"),
            folder_entry(ID="folder-a"),
        )
    )

    assert [d.uuid for d in decoded.documents] == ["doc-b", "doc-a"]
    assert [f.uuid for f in decoded.folders] == ["folder-b", "folder-a"]


def test_an_empty_listing_decodes_to_three_empty_tuples():
    assert decode_entries(b"[]") == DecodedEntries(documents=(), folders=(), skipped=())


# ─────────────────────────────── the two name keys ───────────────────────────────


def test_visiblename_wins_over_the_misspelled_duplicate():
    """Legacy preferred ``VissibleName``; ``VisibleName`` is the canonical spelling."""
    decoded = decode_entries(
        payload(document_entry(VisibleName="canonical", VissibleName="legacy"))
    )

    assert decoded.documents[0].name == "canonical"


def test_the_misspelled_duplicate_is_read_only_when_visiblename_is_absent():
    decoded = decode_entries(
        payload(document_entry(without=("VisibleName",), VissibleName="legacy only"))
    )

    assert decoded.documents[0].name == "legacy only"


def test_an_empty_name_is_kept_rather_than_replaced_with_untitled():
    """Legacy defaulted to ``"Untitled"``, a name the device never recorded."""
    decoded = decode_entries(payload(document_entry(VisibleName="", VissibleName="")))

    assert decoded.documents[0].name == ""


def test_an_ill_typed_visiblename_is_not_repaired_by_the_misspelled_duplicate():
    uuid, reason, detail = only_skip(document_entry(VisibleName=42, VissibleName="usable"))

    assert uuid == "11111111-2222-3333-4444-555555555555"
    assert reason is SkipReason.MALFORMED_METADATA
    assert "VisibleName" in detail


def test_an_entry_naming_itself_with_neither_key_is_malformed():
    uuid, reason, detail = only_skip(
        document_entry(without=("VisibleName", "VissibleName"), ID="nameless")
    )

    assert uuid == "nameless"
    assert reason is SkipReason.MALFORMED_METADATA
    assert "VisibleName" in detail
    assert "VissibleName" in detail


# ─────────────────────────── MALFORMED_METADATA ───────────────────────────


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param("just a string", id="a json string"),
        pytest.param(7, id="a json number"),
        pytest.param(["nested"], id="a json array"),
        pytest.param(None, id="json null"),
        pytest.param(True, id="a json bool"),
    ],
)
def test_an_entry_that_is_not_a_json_object_is_malformed_with_no_recovered_uuid(entry: object):
    uuid, reason, detail = only_skip(entry)

    assert uuid is None
    assert reason is SkipReason.MALFORMED_METADATA
    assert "expected a json object per entry" in detail


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("Type", id="Type"),
        pytest.param("ID", id="ID"),
        pytest.param("Parent", id="Parent"),
        pytest.param("ModifiedClient", id="ModifiedClient"),
        pytest.param("fileType", id="fileType, documents only"),
    ],
)
def test_an_absent_required_key_is_malformed(key: str):
    _, reason, detail = only_skip(document_entry(without=(key,)))

    assert reason is SkipReason.MALFORMED_METADATA
    assert f"required key {key!r} is absent" in detail


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("Type", id="Type"),
        pytest.param("ID", id="ID"),
        pytest.param("Parent", id="Parent"),
        pytest.param("ModifiedClient", id="ModifiedClient"),
        pytest.param("fileType", id="fileType"),
    ],
)
def test_a_required_key_of_the_wrong_json_type_is_malformed(key: str):
    entry = document_entry()
    entry[key] = 42

    _, reason, detail = only_skip(entry)

    assert reason is SkipReason.MALFORMED_METADATA
    assert f"key {key!r} is a json int, not a string" in detail


def test_a_required_key_set_to_json_null_reads_as_absent():
    """The two are the same diagnosis, so distinguishing them would buy nothing."""
    _, reason, detail = only_skip(document_entry(Parent=None))

    assert reason is SkipReason.MALFORMED_METADATA
    assert "required key 'Parent' is absent" in detail


def test_the_keys_this_decoder_never_reads_are_not_required():
    """``Bookmarked`` and ``CurrentPage`` map to nothing, so their absence costs nothing."""
    decoded = decode_entries(payload(document_entry(without=("Bookmarked", "CurrentPage"))))

    assert len(decoded.documents) == 1
    assert decoded.skipped == ()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("not a date at all", id="not ISO-8601"),
        pytest.param("", id="the empty string"),
        pytest.param("2026-13-45T99:99:99.999Z", id="ISO-shaped but impossible"),
    ],
)
def test_an_unparseable_modifiedclient_is_malformed(value: str):
    _, reason, detail = only_skip(document_entry(ModifiedClient=value))

    assert reason is SkipReason.MALFORMED_METADATA
    assert "is not an ISO-8601 instant" in detail


def test_an_offset_less_modifiedclient_is_malformed_rather_than_a_pydantic_failure():
    """The port's validator rejects a naive datetime; this reports it as the wire's fault."""
    _, reason, detail = only_skip(document_entry(ModifiedClient="2026-08-29T14:52:11.412"))

    assert reason is SkipReason.MALFORMED_METADATA
    assert "carries no timezone offset" in detail


# ─────────────────────────── VALIDATION_FAILED ───────────────────────────


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("TemplateType", id="a type this firmware does not write"),
        pytest.param("", id="the empty string"),
        pytest.param("documenttype", id="the right word, wrong case"),
    ],
)
def test_an_unknown_type_fails_validation_rather_than_being_coerced(kind: str):
    uuid, reason, detail = only_skip(document_entry(Type=kind))

    assert uuid == "11111111-2222-3333-4444-555555555555"
    assert reason is SkipReason.VALIDATION_FAILED
    assert f"Type {kind!r} is neither" in detail


@pytest.mark.parametrize(
    "wire",
    [
        pytest.param("docx", id="a format the device cannot hold"),
        pytest.param("Notebook", id="the right word, wrong case"),
        pytest.param("", id="the empty string"),
    ],
)
def test_an_unknown_filetype_fails_validation_rather_than_being_coerced(wire: str):
    """A document silently reported as a notebook would be pulled without its underlay."""
    _, reason, detail = only_skip(document_entry(fileType=wire))

    assert reason is SkipReason.VALIDATION_FAILED
    assert f"fileType {wire!r} is not a kind" in detail


def test_an_empty_id_fails_validation_and_recovers_no_uuid():
    """``DeviceDocument.uuid`` is ``min_length=1``; an empty string is not an identity."""
    uuid, reason, detail = only_skip(document_entry(ID=""))

    assert uuid is None
    assert reason is SkipReason.VALIDATION_FAILED
    assert "uuid" in detail


def test_a_folder_with_an_empty_id_fails_validation_too():
    uuid, reason, reason_detail = only_skip(folder_entry(ID=""))

    assert uuid is None
    assert reason is SkipReason.VALIDATION_FAILED
    assert "uuid" in reason_detail


# ─────────────────────── the one whole-payload failure ───────────────────────


@pytest.mark.parametrize(
    ("body", "expected_got"),
    [
        pytest.param(b"not json at all", "bytes that are not json", id="not json"),
        pytest.param(b"", "bytes that are not json", id="an empty body"),
        pytest.param(b'{"documents": []}', "a json dict", id="a json object"),
        pytest.param(b"null", "a json NoneType", id="json null"),
        pytest.param(b"3", "a json int", id="a json number"),
        pytest.param(b'"a string"', "a json str", id="a json string"),
    ],
)
def test_a_payload_that_is_not_a_json_array_raises(body: bytes, expected_got: str):
    with pytest.raises(DeviceProtocolError) as raised:
        decode_entries(body)

    assert raised.value.route == LISTING_ROUTE
    assert raised.value.transport is TransportKind.USB_WEB_API
    assert raised.value.expected == "a json array of library entries"
    assert expected_got in raised.value.got


def test_no_per_entry_problem_ever_raises():
    """Per-entry failure is data; whole-transport failure raises. Both halves at once."""
    decoded = decode_entries(
        payload(
            document_entry(ID="good"),
            "not an object",
            document_entry(fileType="docx", ID="bad-type"),
            folder_entry(ID="good-folder"),
            document_entry(without=("ModifiedClient",), ID="undated"),
        )
    )

    assert [d.uuid for d in decoded.documents] == ["good"]
    assert [f.uuid for f in decoded.folders] == ["good-folder"]
    assert [s.uuid for s in decoded.skipped] == [None, "bad-type", "undated"]
    assert [s.reason for s in decoded.skipped] == [
        SkipReason.MALFORMED_METADATA,
        SkipReason.VALIDATION_FAILED,
        SkipReason.MALFORMED_METADATA,
    ]


def test_unreadable_is_never_produced_here():
    """It means a folder fetch was refused, which only the catalog's walk can observe."""
    decoded = decode_entries(
        payload("not an object", document_entry(Type="Nope"), document_entry(ID=""))
    )

    assert SkipReason.UNREADABLE not in {skipped.reason for skipped in decoded.skipped}


# ──────────────────────── the three raw-entry predicates ────────────────────────


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param(document_entry(Parent="folder-1"), "folder-1", id="a real folder"),
        pytest.param(document_entry(Parent=""), None, id="the library root"),
        pytest.param(document_entry(without=("Parent",)), None, id="no Parent key"),
        pytest.param(document_entry(Parent=42), None, id="an ill-typed Parent"),
        pytest.param("not an object", None, id="not a json object"),
    ],
)
def test_entry_parent_is_total_and_spells_the_root_none(entry: object, expected: str | None):
    """The walk compares this against the folder it asked for, so it cannot refuse."""
    assert entry_parent(entry) == expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param(document_entry(ID="doc-1"), "doc-1", id="a real id"),
        pytest.param(document_entry(ID=""), None, id="an empty id is no identity"),
        pytest.param(document_entry(without=("ID",)), None, id="no ID key"),
        pytest.param(document_entry(ID=42), None, id="an ill-typed ID"),
        pytest.param(None, None, id="not a json object"),
    ],
)
def test_entry_id_is_total(entry: object, expected: str | None):
    assert entry_id(entry) == expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param(folder_entry(), True, id="a folder"),
        pytest.param(document_entry(), False, id="a document"),
        pytest.param(document_entry(Type="Nope"), False, id="an unknown type"),
        pytest.param(document_entry(without=("Type",)), False, id="no Type key"),
        pytest.param(["not an object"], False, id="not a json object"),
    ],
)
def test_is_collection_only_says_yes_to_something_it_could_list(entry: object, *, expected: bool):
    assert is_collection(entry) is expected
