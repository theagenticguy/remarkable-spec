"""The shipped doubles, and the seams the conformance contract cannot reach through a port.

``test_device_conformance.py`` proves the doubles satisfy the same contract as the adapters.
This file covers what is left, and everything in it is here for one of three reasons.

**A seam only a double has.** ``refresh=ALREADY_VISIBLE`` is unreachable in every shipped
adapter -- the SSH uploader restarts the tablet UI unconditionally and there is no USB
uploader -- so the second :class:`~rmspec.domain.ports.device.LibraryRefresh` member is
tested here or nowhere. Same for ``reject_with`` and ``honours_parent``.

**An ordering only a double can establish.** The port says
``DeviceOperationUnsupported`` is "raised before anything is written". Proving that needs a
double that is *both* unable to honour a destination *and* seeded to fail, so that which
error comes out says which check ran first.

**A counter.** Through a total port, a memoised listing and a re-fetched one return equal
values, and a swallowed fault and a genuine miss are the same ``None``. The counters are the
only evidence, so they are asserted directly rather than trusted.

The doubles ship under ``src/`` and are measured by the coverage gate, so every branch above
is also a line this file is responsible for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from device_contracts import an_upload

from rmspec.device._shell import PathUnreadableError, RemoteShell
from rmspec.device.addresses import RemoteCommand, RemotePath
from rmspec.device.testing import (
    IN_MEMORY_ENDPOINT,
    IN_MEMORY_TRANSPORT,
    UPLOAD_OPERATION,
    FakeRemoteShell,
    InMemoryDeviceCatalog,
    InMemoryDeviceFactsSource,
    InMemoryDocumentUploader,
    InMemoryRawBundleSource,
)
from rmspec.domain.errors import (
    DeviceDocumentNotFound,
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    DeviceUploadRejected,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.ports.device import (
    DeviceDocument,
    DeviceFacts,
    DeviceFileType,
    DeviceFolder,
    DevicePageSource,
    DeviceResources,
    LibraryRefresh,
    SkippedEntry,
    SkipReason,
    UploadMedia,
)

if TYPE_CHECKING:
    from collections.abc import Callable

DOC = "aaaaaaaa-0000-4000-8000-000000000001"
PDF_DOC = "cccccccc-0000-4000-8000-000000000003"
FOLDER = "bbbbbbbb-0000-4000-8000-000000000002"
UNKNOWN = "99999999-0000-4000-8000-000000000009"
PAGE_A = "11111111-0000-4000-8000-00000000000a"
PAGE_B = "22222222-0000-4000-8000-00000000000b"

INK = b"v6-scene-bytes"
UNDERLAY = b"%PDF-1.7 synthetic underlay"

A_NOTEBOOK = DeviceDocument(uuid=DOC, name="Sprint notes", file_type=DeviceFileType.NOTEBOOK)
A_PDF = DeviceDocument(uuid=PDF_DOC, name="Spec", file_type=DeviceFileType.PDF)
A_FOLDER = DeviceFolder(uuid=FOLDER, name="Work")

DEAD = DeviceUnreachable(
    transport=TransportKind.SSH,
    endpoint="10.11.99.1:22",
    detail="the test seeded a dead transport",
)
"""One whole-transport failure, reused wherever a fault has to describe the session."""


def a_catalog(
    *,
    skipped: tuple[SkippedEntry, ...] = (),
    fail_with: DeviceError | None = None,
) -> InMemoryDeviceCatalog:
    """Build a catalog holding one notebook, one annotated pdf and one folder."""
    return InMemoryDeviceCatalog(
        documents=(A_NOTEBOOK, A_PDF),
        folders=(A_FOLDER,),
        skipped=skipped,
        fail_with=fail_with,
    )


def a_source(
    *,
    truncate_at: int | None = None,
    fail_with: DeviceError | None = None,
) -> InMemoryRawBundleSource:
    """Build a bundle source over that catalog, with the notebook's two pages seeded."""
    return InMemoryRawBundleSource(
        catalog=a_catalog(),
        pages={
            DOC: (
                DevicePageSource(page_id=PAGE_A, scene=INK),
                DevicePageSource(page_id=PAGE_B, scene=None),
            )
        },
        bases={PDF_DOC: UNDERLAY},
        truncate_at=truncate_at,
        fail_with=fail_with,
    )


# ─────────────────────────── InMemoryDeviceCatalog ───────────────────────────


def _catalog_clashing_on_a_folder() -> InMemoryDeviceCatalog:
    return InMemoryDeviceCatalog(
        documents=(A_NOTEBOOK,),
        folders=(DeviceFolder(uuid=DOC, name="Clash"),),
    )


def _catalog_clashing_on_a_skip() -> InMemoryDeviceCatalog:
    return InMemoryDeviceCatalog(
        documents=(A_NOTEBOOK,),
        skipped=(SkippedEntry(uuid=DOC, reason=SkipReason.UNREADABLE, detail="clash"),),
    )


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_catalog_clashing_on_a_folder, id="folder"),
        pytest.param(_catalog_clashing_on_a_skip, id="skipped"),
    ],
)
def test_an_identifier_reported_as_a_document_and_as_something_else_is_refused(
    build: Callable[[], InMemoryDeviceCatalog],
):
    """A clashing identifier is refused at construction.

    Neither real adapter can produce it, so a double that accepted it would answer for a
    state no device can be in: the USB walk keeps a ``visited`` set and the SSH walk yields
    exactly one value per store entry.
    """
    with pytest.raises(ValueError, match="both as documents"):
        build()


def test_a_folder_may_also_be_reported_as_skipped():
    """A folder may also be reported as skipped.

    Which is exactly what the USB catalog produces for a folder whose children the device
    refused -- so the double must permit it, and ``skipped`` must win in ``get_document``.
    """
    entry = SkippedEntry(uuid=FOLDER, reason=SkipReason.UNREADABLE, detail="children refused")
    catalog = InMemoryDeviceCatalog(documents=(), folders=(A_FOLDER,), skipped=(entry,))

    with pytest.raises(MalformedDeviceMetadata) as caught:
        catalog.get_document(FOLDER)

    assert caught.value.detail == "children refused"


def test_the_skip_list_is_searched_past_entries_that_do_not_match():
    first = SkippedEntry(uuid=UNKNOWN, reason=SkipReason.MALFORMED_METADATA, detail="first")
    wanted = SkippedEntry(uuid=DOC, reason=SkipReason.VALIDATION_FAILED, detail="second")
    catalog = InMemoryDeviceCatalog(documents=(), folders=(), skipped=(first, wanted))

    with pytest.raises(MalformedDeviceMetadata) as caught:
        catalog.get_document(DOC)

    assert caught.value.detail == "second"


def test_a_skip_carrying_no_identifier_is_permitted_and_matches_nothing():
    """``SkippedEntry.uuid`` is optional because a decoder may recover no id at all."""
    anonymous = SkippedEntry(uuid=None, reason=SkipReason.MALFORMED_METADATA, detail="no id")
    catalog = InMemoryDeviceCatalog(documents=(), folders=(), skipped=(anonymous,))

    with pytest.raises(DeviceDocumentNotFound):
        catalog.get_document(DOC)


def test_the_listing_is_built_once_and_returned_as_the_same_object():
    catalog = a_catalog()

    first = catalog.list_documents()
    second = catalog.list_documents()
    catalog.get_document(DOC)

    assert first is second
    assert catalog.builds == 1
    assert catalog.list_calls == 3
    assert catalog.get_calls == 1


def test_a_faulting_catalog_still_counts_the_call_it_refused():
    catalog = a_catalog(fail_with=DEAD)

    with pytest.raises(DeviceUnreachable):
        catalog.list_documents()

    assert catalog.list_calls == 1
    assert catalog.builds == 0


def test_a_doubles_error_names_the_one_transport_no_adapter_here_binds():
    # LOCAL_MIRROR is the member D9 defers to step 6 and puts in rmspec-formats, so an error
    # raised by a double is distinguishable from one raised by either real transport here.
    assert IN_MEMORY_TRANSPORT is TransportKind.LOCAL_MIRROR

    with pytest.raises(DeviceDocumentNotFound) as caught:
        a_catalog().get_document(UNKNOWN)

    assert caught.value.transport is IN_MEMORY_TRANSPORT


# ─────────────────────────── InMemoryRawBundleSource ───────────────────────────


def test_a_repeated_page_identifier_is_dropped_after_the_first():
    # Mirrors decode_page_order, which both real adapters build their page tuple through --
    # so DocumentSourceBundle's duplicate arm is unreachable from either, and a double that
    # surfaced a ValidationError instead would disagree with both.
    source = InMemoryRawBundleSource(
        catalog=a_catalog(),
        pages={
            DOC: (
                DevicePageSource(page_id=PAGE_A, scene=INK),
                DevicePageSource(page_id=PAGE_A, scene=b"a later layer"),
                DevicePageSource(page_id=PAGE_B, scene=None),
            )
        },
        bases={PDF_DOC: UNDERLAY},
    )

    pages = source.load_bundle(DOC).pages

    assert tuple(page.page_id for page in pages) == (PAGE_A, PAGE_B)
    assert pages[0].scene == INK


def test_a_non_notebook_with_no_seeded_underlay_is_a_protocol_error():
    """A non-notebook with no seeded underlay is a protocol error.

    The transport contradicting its own answer, which is what the USB archive reader
    reports for an archive missing the member its own file type requires.
    """
    source = InMemoryRawBundleSource(catalog=a_catalog(), pages={}, bases={})

    with pytest.raises(DeviceProtocolError) as caught:
        source.load_bundle(PDF_DOC)

    assert caught.value.route == IN_MEMORY_ENDPOINT
    assert "pdf underlay" in caught.value.expected


def test_a_notebook_with_no_seeded_pages_is_an_empty_bundle_and_not_a_failure():
    """An empty ``pages`` tuple is unambiguous: a document with no pages at all."""
    source = InMemoryRawBundleSource(catalog=a_catalog(), pages={}, bases={PDF_DOC: UNDERLAY})

    bundle = source.load_bundle(DOC)

    assert bundle.pages == ()
    assert bundle.base is None


def test_a_truncation_names_both_byte_counts_from_the_seeded_payload():
    source = a_source(truncate_at=4)

    with pytest.raises(DeviceTransferInterrupted) as caught:
        source.load_bundle(DOC)

    assert caught.value.subject == DOC
    assert caught.value.bytes_transferred == 4
    # Only PAGE_A carries ink, and a notebook has no underlay, so that is the whole payload.
    assert caught.value.bytes_expected == len(INK)


def test_a_truncation_counts_the_pdfs_underlay_in_what_was_expected():
    source = a_source(truncate_at=0)

    with pytest.raises(DeviceTransferInterrupted) as caught:
        source.load_bundle(PDF_DOC)

    assert caught.value.bytes_expected == len(UNDERLAY)


def test_the_catalog_diagnoses_a_missing_identifier_before_a_transfer_is_attempted():
    # Which is what both real adapters do, and why the not-found error is not replaced by a
    # transfer failure for an identifier the device never held.
    source = a_source(truncate_at=1)

    with pytest.raises(DeviceDocumentNotFound):
        source.load_bundle(UNKNOWN)


def test_a_whole_transport_failure_precedes_even_the_catalog():
    source = a_source(fail_with=DEAD)

    with pytest.raises(DeviceUnreachable):
        source.load_bundle(UNKNOWN)

    assert source.load_calls == 1


# ─────────────────────────── InMemoryDocumentUploader ───────────────────────────


@pytest.mark.parametrize("refresh", list(LibraryRefresh))
def test_both_library_refresh_members_are_reportable(refresh: LibraryRefresh):
    """Both library refresh members are reportable.

    The SSH adapter only ever produces ``VISIBILITY_FORCED`` and there is no USB uploader,
    so ``ALREADY_VISIBLE`` is reachable here and nowhere else in the workspace.
    """
    uploader = InMemoryDocumentUploader(refresh=refresh)

    assert uploader.upload(an_upload()).library_refresh is refresh


def test_a_refusal_carries_the_devices_own_message_and_places_nothing():
    uploader = InMemoryDocumentUploader(reject_with="No file sent")

    with pytest.raises(DeviceUploadRejected) as caught:
        uploader.upload(an_upload(name="Design review"))

    assert caught.value.device_message == "No file sent"
    assert caught.value.name == "Design review"
    assert uploader.uploaded == []
    assert uploader.upload_calls == 1


def test_a_receipt_may_report_no_identifier_at_all():
    """A receipt may report no identifier at all.

    ``UploadReceipt.doc_uuid`` is optional because the identifier is a transport fact: an
    SSH adapter mints it, and a transport that reports none leaves it ``None`` rather than
    guessing by name, which races on-device indexing.
    """
    assert InMemoryDocumentUploader().upload(an_upload()).doc_uuid is None
    assert InMemoryDocumentUploader(doc_uuid=DOC).upload(an_upload()).doc_uuid == DOC


def test_an_unhonourable_destination_is_refused_before_the_transport_is_even_consulted():
    # Both seams are armed, so which error comes out says which check ran first. The port
    # documents DeviceOperationUnsupported as "raised before anything is written", and a
    # transport failure reaching the caller first would mean the write had been attempted.
    uploader = InMemoryDocumentUploader(honours_parent=False, fail_with=DEAD)

    with pytest.raises(DeviceOperationUnsupported) as caught:
        uploader.upload(an_upload(parent_uuid=FOLDER))

    assert caught.value.operation == UPLOAD_OPERATION
    assert caught.value.supported_by == (TransportKind.SSH,)
    assert uploader.uploaded == []


def test_a_transport_failure_is_raised_once_the_destination_check_has_passed():
    uploader = InMemoryDocumentUploader(honours_parent=False, fail_with=DEAD)

    with pytest.raises(DeviceUnreachable):
        uploader.upload(an_upload())

    assert uploader.uploaded == []
    assert uploader.upload_calls == 1


@pytest.mark.parametrize("media", list(UploadMedia))
def test_a_placed_request_is_recorded_verbatim(media: UploadMedia):
    uploader = InMemoryDocumentUploader()
    request = an_upload(media=media, parent_uuid=FOLDER)

    receipt = uploader.upload(request)

    assert uploader.uploaded == [request]
    assert receipt.media is media
    assert receipt.byte_count == len(request.data)


# ─────────────────────────── InMemoryDeviceFactsSource ───────────────────────────


def test_the_default_reading_is_every_field_asked_for_and_unanswered():
    """The default reading is every field asked for and unanswered.

    Neither transport's shape: the port's neutral value, where nothing is structurally
    unaskable and nothing came back intelligibly. A legal reading, and documented as the
    default so a test that wants a transport's shape states it.
    """
    source = InMemoryDeviceFactsSource()

    assert source.read_facts() == DeviceFacts()
    assert source.read_resources() == DeviceResources()
    assert source.read_facts().unsupported == frozenset()


def test_the_seeded_readings_are_returned_unchanged_and_counted():
    facts = DeviceFacts(firmware="3.27.3.0", unsupported=frozenset({"serial"}))
    resources = DeviceResources(total_storage_bytes=1024, available_storage_bytes=512)
    source = InMemoryDeviceFactsSource(facts=facts, resources=resources)

    assert source.read_facts() is facts
    assert source.read_resources() is resources
    assert source.read_resources() is resources
    assert (source.facts_calls, source.resources_calls) == (1, 2)


def test_a_fault_raises_from_both_methods_and_is_still_counted():
    # Not "everything unsupported": a facts source that reported a detached tablet as a
    # transport which structurally cannot ask would be lying about a temporary condition.
    source = InMemoryDeviceFactsSource(fail_with=DEAD)

    with pytest.raises(DeviceUnreachable):
        source.read_facts()
    with pytest.raises(DeviceUnreachable):
        source.read_resources()

    assert (source.facts_calls, source.resources_calls) == (1, 1)


def test_the_seeded_failure_is_the_object_that_arrives():
    """The seeded failure is the object that arrives.

    Raised rather than rebuilt, so a test asserts on the exact value it seeded -- including
    a class the doubles would never construct themselves.
    """
    source = InMemoryDeviceFactsSource(fail_with=DEAD)

    with pytest.raises(DeviceUnreachable) as caught:
        source.read_facts()

    assert caught.value is DEAD


# ─────────────────────────── FakeRemoteShell ───────────────────────────

A_PATH = RemotePath.root().child("sidecar.metadata")
A_COMMAND = RemoteCommand.of("cat {}", A_PATH)


def test_the_fake_shell_satisfies_the_remote_shell_protocol():
    shell: RemoteShell = FakeRemoteShell()

    for name in ("run", "read_file", "list_dir", "write_file"):
        assert callable(getattr(shell, name))


def test_a_scripted_command_answers_verbatim():
    shell = FakeRemoteShell(outputs={A_COMMAND.text: "3.27.3.0\n"})

    assert shell.run(A_COMMAND) == "3.27.3.0\n"
    assert shell.commands == [A_COMMAND.text]


def _shell_refusing_the_command() -> FakeRemoteShell:
    return FakeRemoteShell(refuse_commands=[A_COMMAND.text])


@pytest.mark.parametrize(
    ("build", "got"),
    [
        pytest.param(_shell_refusing_the_command, "exit non-zero", id="refused"),
        pytest.param(FakeRemoteShell, "no output was scripted", id="unscripted"),
    ],
)
def test_a_refused_or_unscripted_command_is_a_protocol_error(
    build: Callable[[], FakeRemoteShell],
    got: str,
):
    """A refused or unscripted command is a protocol error.

    An unscripted command raises rather than returning ``""``, so a test that misspells the
    command under test fails loudly instead of asserting against an empty string.
    """
    shell = build()

    with pytest.raises(DeviceProtocolError) as caught:
        shell.run(A_COMMAND)

    assert caught.value.route == A_COMMAND.text
    assert caught.value.expected == "exit status 0"
    assert got in caught.value.got


def _shell_refusing_the_read() -> FakeRemoteShell:
    return FakeRemoteShell(refuse_reads=[A_PATH.value], files={A_PATH.value: INK})


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_shell_refusing_the_read, id="refused"),
        pytest.param(FakeRemoteShell, id="absent"),
    ],
)
def test_a_refused_read_and_an_absent_path_are_both_per_path_failures(
    build: Callable[[], FakeRemoteShell],
):
    # PathUnreadableError is not a DeviceError, and that is the whole point: a caller with a
    # per-entry answer may give one for this, and must not for a dead session.
    shell = build()

    with pytest.raises(PathUnreadableError) as caught:
        shell.read_file(A_PATH)

    assert not isinstance(caught.value, DeviceError)
    assert caught.value.path == A_PATH.value
    assert shell.reads == [A_PATH.value]


def test_a_zero_length_file_reads_as_empty_bytes_and_not_none():
    """Deciding what that means is the caller's business, exactly as with the real shell."""
    shell = FakeRemoteShell(files={A_PATH.value: b""})

    assert shell.read_file(A_PATH) == b""


def test_an_unlistable_directory_is_a_per_path_failure():
    """A non-zero ``ls`` proves the session is alive, so it is a per-path signal."""
    root = RemotePath.root()
    shell = FakeRemoteShell()

    with pytest.raises(PathUnreadableError) as caught:
        shell.list_dir(root)

    assert caught.value.path == root.value
    assert IN_MEMORY_ENDPOINT in caught.value.detail
    assert shell.listings == [root.value]


def test_a_listed_directory_answers_in_the_order_it_was_given():
    root = RemotePath.root()
    shell = FakeRemoteShell(dirs={root.value: ["b.metadata", "a.metadata"]})

    assert shell.list_dir(root) == ("b.metadata", "a.metadata")


def test_a_write_lands_in_the_store_and_is_readable_back():
    shell = FakeRemoteShell()

    shell.write_file(A_PATH, INK)

    assert shell.written(A_PATH) == INK
    assert shell.writes == [(A_PATH.value, INK)]
    assert shell.read_file(A_PATH) == INK


def test_a_short_write_names_both_byte_counts_and_stores_nothing():
    """A short write names both byte counts and stores nothing.

    A domain error and not a per-path one: an uploader has no per-entry answer to give, and
    ``UploadReceipt.byte_count`` promises the payload's full length whenever a receipt
    exists.
    """
    shell = FakeRemoteShell(short_writes=[A_PATH.value])

    with pytest.raises(DeviceTransferInterrupted) as caught:
        shell.write_file(A_PATH, INK)

    assert caught.value.bytes_transferred == 0
    assert caught.value.bytes_expected == len(INK)
    assert shell.writes == []
    assert shell.log == [f"short {A_PATH.value}"]


def test_the_log_orders_every_operation_across_all_four_methods():
    # One ordered log rather than four counters, because "the .metadata sidecar is written
    # last" is an ordering property no per-method counter can express.
    root = RemotePath.root()
    shell = FakeRemoteShell(outputs={A_COMMAND.text: ""}, dirs={root.value: ()})

    shell.run(A_COMMAND)
    shell.list_dir(root)
    shell.write_file(A_PATH, INK)
    shell.read_file(A_PATH)

    assert shell.log == [
        f"run {A_COMMAND.text}",
        f"list {root.value}",
        f"write {A_PATH.value}",
        f"read {A_PATH.value}",
    ]


def test_a_whole_transport_failure_precedes_every_per_path_answer():
    shell = FakeRemoteShell(fail_with=DEAD, files={A_PATH.value: INK})

    for call in (
        lambda: shell.run(A_COMMAND),
        lambda: shell.read_file(A_PATH),
        lambda: shell.list_dir(RemotePath.root()),
        lambda: shell.write_file(A_PATH, INK),
    ):
        with pytest.raises(DeviceUnreachable):
            call()

    # Nothing was even recorded: a session that is gone did not see any of these.
    assert shell.log == []


def test_reading_a_path_nothing_wrote_is_a_key_error_from_the_assertion_helper():
    """Reading a path nothing wrote is a plain key error.

    ``written`` is an assertion helper, so a miss is a defect in the test rather than a
    device failure the adapter under test should ever see.
    """
    shell = FakeRemoteShell()

    with pytest.raises(KeyError):
        shell.written(A_PATH)
