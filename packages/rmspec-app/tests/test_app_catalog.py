"""Depth-2 nesting, a cycle that would hang a naive walker, the trash flag, and skipped entries.

How a ``DeviceCatalog`` is bound here, and why
---------------------------------------------
With a local in-memory fake annotated against the Protocol, for the reasons
``test_app_resolve.py`` sets out and this file does not repeat: ``rmspec.app`` may import
``rmspec.domain`` and nothing else, the architecture check only scans ``src/`` so an adapter
import in a test would pass the gate while breaking the property the gate protects, and
``rmspec.device.testing``'s shipped doubles run the parent package's ``__init__`` and would
make a pure-policy suite need ``paramiko`` and ``httpx`` installed to test tree building.
Conformance is checked by the type gate: every construction below passes
``_InMemoryCatalog`` to ``ListDocuments(catalog=...)``, whose parameter is annotated
``DeviceCatalog``.

One test in here would **hang** rather than fail if the ancestor walk lost its visited set,
which is the honest shape for that property: ``test_a_parent_cycle_is_reported_rather_than_looped``
builds two folders naming each other, and a walker without a visited set never returns.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rmspec.app.catalog import (
    _UNIDENTIFIED,
    CatalogFolder,
    ListDocuments,
    ListDocumentsRequest,
    ListDocumentsResult,
)
from rmspec.app.resolve import _UNIDENTIFIED as _RESOLVE_UNIDENTIFIED
from rmspec.domain.errors import (
    DegradationKind,
    DeviceUnreachable,
    DocumentNotFound,
    RmspecError,
    TransportKind,
)
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DeviceFolder,
    DeviceListing,
    SkippedEntry,
    SkipReason,
)

ROOT_FOLDER = "f0000000-0000-4000-8000-000000000000"
DEPTH_1 = "f1111111-1111-4111-8111-111111111111"
DEPTH_2 = "f2222222-2222-4222-8222-222222222222"
ORPHAN_FOLDER = "f3333333-3333-4333-8333-333333333333"
CYCLE_A = "faaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CYCLE_B = "fbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ABSENT_FOLDER = "f9999999-9999-4999-8999-999999999999"

ALPHA = "aaaaaaaa-1111-4111-8111-111111111111"
BETA = "bbbbbbbb-2222-4222-8222-222222222222"
GAMMA = "cccccccc-3333-4333-8333-333333333333"
DELTA = "dddddddd-4444-4444-8444-444444444444"


class _InMemoryCatalog:
    """A :class:`DeviceCatalog` over one listing, with a transport that can be told to die."""

    def __init__(self, listing: DeviceListing, failure: RmspecError | None = None) -> None:
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
        """Look one document up by identifier, which this use case never does."""
        for document in self._listing.documents:
            if document.uuid == doc_uuid:
                return document
        raise DocumentNotFound(query=doc_uuid, store="fake")


def _doc(
    uuid: str,
    name: str,
    *,
    parent_uuid: str | None = None,
    page_count: int | None = None,
    trashed: bool = False,
) -> DeviceDocument:
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=DeviceFileType.NOTEBOOK,
        parent_uuid=parent_uuid,
        page_count=page_count,
        trashed=trashed,
    )


def _folder(
    uuid: str,
    name: str,
    *,
    parent_uuid: str | None = None,
    trashed: bool = False,
) -> DeviceFolder:
    return DeviceFolder(uuid=uuid, name=name, parent_uuid=parent_uuid, trashed=trashed)


def _listing(
    *documents: DeviceDocument,
    folders: tuple[DeviceFolder, ...] = (),
    skipped: tuple[SkippedEntry, ...] = (),
) -> DeviceListing:
    return DeviceListing(documents=documents, folders=folders, skipped=skipped)


def _list(listing: DeviceListing, *, include_trashed: bool = False) -> ListDocumentsResult:
    return ListDocuments(catalog=_InMemoryCatalog(listing)).list_documents(
        ListDocumentsRequest(include_trashed=include_trashed)
    )


def _names(nodes: tuple[CatalogFolder, ...]) -> list[str]:
    return [node.folder.name for node in nodes]


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error.

    Same reason ``test_app_resolve.py`` gives: the type gate rejects a direct attribute
    assignment before pydantic can be shown rejecting it, and no ``type: ignore`` is allowed
    to get past that.
    """
    setattr(target, field, value)


# ───────────────────────── the hierarchy, to the measured depth ─────────────────────────


def test_a_flat_library_is_all_root_documents():
    result = _list(_listing(_doc(ALPHA, "Notes"), _doc(BETA, "Sketches")))
    assert [doc.uuid for doc in result.root_documents] == [ALPHA, BETA]
    assert result.root_folders == ()
    assert result.documents == result.root_documents


def test_nesting_is_reported_to_depth_two():
    """9 at the root, 30 at depth 1, 2 at depth 2 on the measured device."""
    result = _list(
        _listing(
            _doc(ALPHA, "at root"),
            _doc(BETA, "at depth one", parent_uuid=ROOT_FOLDER),
            _doc(GAMMA, "at depth two", parent_uuid=DEPTH_1),
            _doc(DELTA, "at depth three", parent_uuid=DEPTH_2),
            folders=(
                _folder(ROOT_FOLDER, "Books"),
                _folder(DEPTH_1, "Work", parent_uuid=ROOT_FOLDER),
                _folder(DEPTH_2, "Q3", parent_uuid=DEPTH_1),
            ),
        )
    )
    (books,) = result.root_folders
    (work,) = books.folders
    (quarter,) = work.folders
    assert _names(result.root_folders) == ["Books"]
    assert [doc.uuid for doc in books.documents] == [BETA]
    assert [doc.uuid for doc in work.documents] == [GAMMA]
    assert [doc.uuid for doc in quarter.documents] == [DELTA]
    assert quarter.folders == ()


def test_a_folder_holding_nothing_is_still_reported():
    """An empty folder that vanished from a listing is a folder the user cannot find."""
    result = _list(_listing(folders=(_folder(ROOT_FOLDER, "Empty"),)))
    (node,) = result.root_folders
    assert node.documents == ()
    assert node.folders == ()


def test_several_folders_and_documents_keep_transport_order():
    """Sorting is a rendering decision, and a use case that sorted would force a re-sort."""
    result = _list(
        _listing(
            _doc(BETA, "b", parent_uuid=ROOT_FOLDER),
            _doc(ALPHA, "a", parent_uuid=ROOT_FOLDER),
            folders=(_folder(DEPTH_1, "zeta"), _folder(ROOT_FOLDER, "alpha")),
        )
    )
    assert _names(result.root_folders) == ["zeta", "alpha"]
    assert [doc.uuid for doc in result.root_folders[1].documents] == [BETA, ALPHA]


def test_the_flat_view_holds_every_document_wherever_it_sits():
    result = _list(
        _listing(
            _doc(ALPHA, "at root"),
            _doc(BETA, "nested", parent_uuid=ROOT_FOLDER),
            _doc(GAMMA, "orphan", parent_uuid=ABSENT_FOLDER),
            folders=(_folder(ROOT_FOLDER, "Books"),),
        )
    )
    assert [doc.uuid for doc in result.documents] == [ALPHA, BETA, GAMMA]


def test_the_two_views_partition_the_same_documents():
    """The flat tuple and the hierarchy are one enumeration, not two queries."""
    result = _list(
        _listing(
            _doc(ALPHA, "at root"),
            _doc(BETA, "nested", parent_uuid=ROOT_FOLDER),
            _doc(GAMMA, "deeper", parent_uuid=DEPTH_1),
            _doc(DELTA, "orphan", parent_uuid=ABSENT_FOLDER),
            folders=(
                _folder(ROOT_FOLDER, "Books"),
                _folder(DEPTH_1, "Work", parent_uuid=ROOT_FOLDER),
            ),
        )
    )

    def walk(nodes: tuple[CatalogFolder, ...]) -> list[str]:
        found: list[str] = []
        for node in nodes:
            found.extend(doc.uuid for doc in node.documents)
            found.extend(walk(node.folders))
        return found

    placed = [
        *(doc.uuid for doc in result.root_documents),
        *walk(result.root_folders),
        *(doc.uuid for doc in result.unrooted_documents),
    ]
    assert sorted(placed) == sorted(doc.uuid for doc in result.documents)
    assert len(placed) == len(set(placed))


def test_folders_and_documents_are_never_flattened_into_one_shape():
    """A folder carries no ``page_count`` and no ``file_type``, because it has neither."""
    result = _list(
        _listing(_doc(ALPHA, "Notes", page_count=3), folders=(_folder(ROOT_FOLDER, "Books"),))
    )
    (node,) = result.root_folders
    assert isinstance(node.folder, DeviceFolder)
    assert not hasattr(node.folder, "page_count")
    assert not hasattr(node.folder, "file_type")
    assert result.documents[0].page_count == 3


# ──────────────── what the legacy walkers did instead, and why it hung ────────────────


def test_a_document_whose_folder_is_missing_is_not_moved_to_the_root():
    """The silent root fallback is what made the legacy walk non-terminating."""
    result = _list(_listing(_doc(ALPHA, "Notes", parent_uuid=ABSENT_FOLDER)))
    assert [doc.uuid for doc in result.unrooted_documents] == [ALPHA]
    assert result.root_documents == ()


def test_a_folder_whose_parent_is_missing_is_reported_rather_than_rooted():
    result = _list(_listing(folders=(_folder(ORPHAN_FOLDER, "Lost", parent_uuid=ABSENT_FOLDER),)))
    assert [folder.name for folder in result.unrooted_folders] == ["Lost"]
    assert result.root_folders == ()


def test_a_parent_cycle_is_reported_rather_than_looped():
    """Without the visited set in the ancestor walk this does not fail: it hangs."""
    result = _list(
        _listing(
            _doc(ALPHA, "inside the cycle", parent_uuid=CYCLE_A),
            folders=(
                _folder(CYCLE_A, "A", parent_uuid=CYCLE_B),
                _folder(CYCLE_B, "B", parent_uuid=CYCLE_A),
            ),
        )
    )
    assert sorted(folder.name for folder in result.unrooted_folders) == ["A", "B"]
    assert result.root_folders == ()
    assert [doc.uuid for doc in result.unrooted_documents] == [ALPHA]


def test_a_folder_that_is_its_own_parent_is_reported_rather_than_looped():
    result = _list(_listing(folders=(_folder(CYCLE_A, "Escher", parent_uuid=CYCLE_A),)))
    assert [folder.name for folder in result.unrooted_folders] == ["Escher"]


def test_a_cycle_below_a_rooted_folder_does_not_unroot_the_folder_above_it():
    result = _list(
        _listing(
            folders=(
                _folder(ROOT_FOLDER, "Books"),
                _folder(CYCLE_A, "A", parent_uuid=CYCLE_B),
                _folder(CYCLE_B, "B", parent_uuid=CYCLE_A),
            )
        )
    )
    assert _names(result.root_folders) == ["Books"]
    assert sorted(folder.name for folder in result.unrooted_folders) == ["A", "B"]


def test_a_chain_hanging_off_a_missing_folder_is_unrooted_all_the_way_down():
    result = _list(
        _listing(
            folders=(
                _folder(ORPHAN_FOLDER, "Lost", parent_uuid=ABSENT_FOLDER),
                _folder(DEPTH_1, "Below the lost one", parent_uuid=ORPHAN_FOLDER),
            )
        )
    )
    assert sorted(folder.name for folder in result.unrooted_folders) == [
        "Below the lost one",
        "Lost",
    ]
    assert result.root_folders == ()


# ───────────────────────────── the trash, on both semantics ─────────────────────────────


def test_the_trash_is_excluded_by_default():
    result = _list(_listing(_doc(ALPHA, "Notes"), _doc(BETA, "Deleted", trashed=True)))
    assert [doc.uuid for doc in result.documents] == [ALPHA]


def test_including_the_trash_reports_it_at_the_root():
    """The firmware overwrites ``parent`` with ``"trash"``, so the original place is gone."""
    result = _list(
        _listing(_doc(ALPHA, "Notes"), _doc(BETA, "Deleted", trashed=True)),
        include_trashed=True,
    )
    assert [doc.uuid for doc in result.documents] == [ALPHA, BETA]
    assert [doc.uuid for doc in result.root_documents] == [ALPHA, BETA]


def test_a_trashed_folder_is_excluded_by_default_and_reported_when_asked_for():
    listing = _listing(folders=(_folder(ROOT_FOLDER, "Deleted folder", trashed=True),))
    assert _list(listing).root_folders == ()
    assert _names(_list(listing, include_trashed=True).root_folders) == ["Deleted folder"]


def test_a_live_document_under_a_trashed_folder_is_unrooted_rather_than_promoted():
    """Excluding the trash may orphan a document; it is reported, never moved."""
    result = _list(
        _listing(
            _doc(ALPHA, "Notes", parent_uuid=ROOT_FOLDER),
            folders=(_folder(ROOT_FOLDER, "Deleted folder", trashed=True),),
        )
    )
    assert [doc.uuid for doc in result.unrooted_documents] == [ALPHA]
    assert result.root_documents == ()


def test_the_flag_makes_no_difference_to_a_usb_listing():
    """Over USB every entry is live and that is accurate, so both spellings agree."""
    usb = _listing(
        _doc(ALPHA, "Notes"),
        _doc(BETA, "Nested", parent_uuid=ROOT_FOLDER),
        folders=(_folder(ROOT_FOLDER, "Books"),),
    )
    assert _list(usb) == _list(usb, include_trashed=True)


def test_the_flag_makes_a_difference_to_a_listing_that_sees_the_trash():
    """Which is what keeps the flag from being dead code rather than merely unexercised."""
    mirror = _listing(_doc(ALPHA, "Notes"), _doc(BETA, "Deleted", trashed=True))
    assert _list(mirror) != _list(mirror, include_trashed=True)


# ─────────────── a listing that omitted entries says so, once per entry ───────────────


def test_each_skipped_entry_becomes_one_degradation():
    skipped = (
        SkippedEntry(uuid="abc", reason=SkipReason.UNREADABLE, detail="permission denied"),
        SkippedEntry(uuid="def", reason=SkipReason.VALIDATION_FAILED, detail="no visibleName"),
    )
    result = _list(_listing(_doc(ALPHA, "Notes"), skipped=skipped))
    assert [entry.kind for entry in result.degradations] == [
        DegradationKind.CATALOG_ENTRY_SKIPPED,
        DegradationKind.CATALOG_ENTRY_SKIPPED,
    ]
    assert [entry.subject for entry in result.degradations] == ["abc", "def"]
    assert result.degradations[0].detail == "unreadable: permission denied"


def test_a_skipped_entry_with_no_recoverable_identifier_is_still_reported():
    skipped = (SkippedEntry(uuid=None, reason=SkipReason.MALFORMED_METADATA, detail="not json"),)
    result = _list(_listing(_doc(ALPHA, "Notes"), skipped=skipped))
    (degradation,) = result.degradations
    assert degradation.subject == _UNIDENTIFIED


def test_the_unidentified_subject_matches_the_other_use_case_that_reports_it():
    """Two use cases naming one condition two ways is how a user learns they disagree."""
    assert _UNIDENTIFIED == _RESOLVE_UNIDENTIFIED


def test_a_complete_listing_reports_no_degradation():
    result = _list(_listing(_doc(ALPHA, "Notes")))
    assert result.degradations == ()


def test_an_unrooted_entry_is_not_reported_as_a_skipped_one():
    """It was not omitted, and ``DegradationKind`` has no member for "cannot be placed"."""
    result = _list(_listing(_doc(ALPHA, "Notes", parent_uuid=ABSENT_FOLDER)))
    assert result.degradations == ()
    assert result.unrooted_documents


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_an_empty_library_is_an_empty_result_rather_than_a_crash():
    result = _list(_listing())
    assert result.documents == ()
    assert result.root_folders == ()
    assert result.unrooted_folders == ()


def test_one_listing_is_one_handshake():
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")))
    ListDocuments(catalog=catalog).list_documents(ListDocumentsRequest())
    assert catalog.calls == 1


def test_a_dead_transport_is_never_degraded_into_an_empty_library():
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connection refused",
    )
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")), failure=failure)
    with pytest.raises(DeviceUnreachable):
        ListDocuments(catalog=catalog).list_documents(ListDocumentsRequest())


def test_the_fake_is_the_port_the_use_case_declares():
    catalog: DeviceCatalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")))
    assert catalog.get_document(ALPHA).name == "Notes"


def test_a_node_is_frozen():
    result = _list(_listing(folders=(_folder(ROOT_FOLDER, "Books"),)))
    (node,) = result.root_folders
    with pytest.raises(ValidationError, match="frozen"):
        _assign(node, "documents", ())


def test_a_result_is_frozen():
    result = _list(_listing(_doc(ALPHA, "Notes")))
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "documents", ())


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ListDocumentsResult.model_validate(
            {
                "documents": (),
                "root_documents": (),
                "root_folders": (),
                "unrooted_folders": (),
                "unrooted_documents": (),
                "degradations": (),
                "tree": (),
            }
        )


def test_a_request_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ListDocumentsRequest.model_validate({"include_trashed": True, "tree": True})
