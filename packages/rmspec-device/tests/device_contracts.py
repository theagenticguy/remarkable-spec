"""The five device port contracts, written once and run against every implementation.

Each class here holds every assertion one port in :mod:`rmspec.domain.ports.device` makes,
and declares its subject through a fixture annotated with the *Protocol* rather than with an
adapter. ``test_device_conformance.py`` binds them to the USB adapters over
``httpx.MockTransport``, to the SSH adapters over an in-memory shell, and to the five
in-memory doubles -- so one assertion set proves that an adapter which quietly narrowed its
behaviour, or a double that quietly widened its own, fails here rather than three packages
away in step 6.

The fixture's Protocol annotation is also a static conformance check. No port is
``runtime_checkable`` and nothing calls ``isinstance``, so returning a concrete adapter from a
fixture annotated with the Protocol is what makes ``ty`` the gate.

The reference library, which every binding materialises for itself
-----------------------------------------------------------------
The contract fixes one library shape and each binding reproduces it behind its own
transport -- synthetically, never captured, because a real ``/documents/`` body carries the
user's document titles and a real ``.rmdoc`` carries their handwriting.

* :data:`NOTEBOOK_UUID` -- a notebook at the library root, whose pages are
  :data:`PAGE_ONE` (carrying ink) then :data:`PAGE_TWO` (carrying none), in that order.
* :data:`FOLDER_UUID` -- a folder at the library root.
* :data:`PDF_UUID` -- a document over a PDF underlay, inside that folder, with one inked
  page :data:`PAGE_THREE` and :data:`UNDERLAY` as its base. Its parent makes the USB
  binding's breadth-first walk load-bearing rather than a formality.
* :data:`UNKNOWN_UUID` -- an identifier nothing holds.

What is asserted about *values* and what is not
----------------------------------------------
No assertion here compares a listed :class:`~rmspec.domain.ports.device.DeviceDocument`
against a literal, because two honest transports disagree about one of its fields:
``page_count`` is ``None`` over the USB web API, which reports no count at all, and the
length of the ``cPages`` list over SSH, which has the sidecar in hand. What the port promises
is *internal* coherence -- that ``get_document`` returns the same value the listing reported
-- and that is what is asserted, against whatever each transport reported.

Skips are produced, never injected
----------------------------------
:meth:`DeviceCatalogContract.catalog_with_skip` takes a
:class:`~rmspec.domain.ports.device.SkipReason` and returns a catalog whose listing reports
one skip carrying it, plus the identifier it carries. Each binding decides how to *cause*
that reason -- a malformed timestamp, an unknown file type, a refused read -- because the
whole point of a skip is that the diagnosis is decided by what the transport found. The
``detail`` is deliberately not asserted: the port documents it as displayed and logged, never
parsed. Note that over the USB web API an ``UNREADABLE`` skip necessarily names a *folder*,
since that is the only entry whose children a routed failure can refuse, so that one
identifier appears in both ``folders`` and ``skipped``; ``get_document`` still raises
``MalformedDeviceMetadata`` for it, because both real catalogs search ``skipped`` before
falling through.

The search index is bytes, and this file never opens one
-------------------------------------------------------
:data:`SEARCH_INDEX_IMAGE` is a synthetic byte string carrying the real format's magic and
nothing else that a database reader would recognise. It is deliberately not a database: this
package may not import ``sqlite3``, the port it belongs to is a transport, and the real image
on the measured device holds the user's handwriting on 90 of its 92 rows. What
:class:`SearchIndexSourceContract` asserts is that the bytes cross unchanged and that absence
is spelled ``None`` -- never that they parse.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, NamedTuple

import pytest
from pydantic import BaseModel, ValidationError

from rmspec.domain.errors import (
    DeviceDocumentNotFound,
    DeviceOperationUnsupported,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.ports.device import (
    DeviceFacts,
    DeviceFileType,
    DeviceListing,
    DeviceResources,
    LibraryRefresh,
    SkipReason,
    UploadMedia,
    UploadRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rmspec.domain.errors import DeviceError
    from rmspec.domain.ports.device import (
        DeviceCatalog,
        DeviceFactsSource,
        DocumentUploader,
        RawBundleSource,
        SearchIndexSource,
    )

NOTEBOOK_UUID = "aaaaaaaa-0000-4000-8000-000000000001"
"""A notebook at the library root. Handwriting only, so its bundle carries no underlay."""

FOLDER_UUID = "bbbbbbbb-0000-4000-8000-000000000002"
"""A folder at the library root, holding :data:`PDF_UUID`."""

PDF_UUID = "cccccccc-0000-4000-8000-000000000003"
"""A document over a PDF underlay, one level down. Its bundle must carry that underlay."""

UNKNOWN_UUID = "99999999-0000-4000-8000-000000000009"
"""An identifier no binding's library holds."""

PAGE_ONE = "11111111-0000-4000-8000-00000000000a"
"""The notebook's first page, carrying ink."""

PAGE_TWO = "22222222-0000-4000-8000-00000000000b"
"""The notebook's second page, carrying none. 86 of 194 real artifacts are zero bytes."""

PAGE_THREE = "33333333-0000-4000-8000-00000000000c"
"""The annotated PDF's only page, carrying ink."""

NOTEBOOK_PAGES = (PAGE_ONE, PAGE_TWO)
"""The notebook's page order, which a bundle must reproduce exactly."""

MODIFIED = datetime.datetime(2026, 8, 29, 14, 52, 11, 412000, tzinfo=datetime.UTC)
"""One modification instant, already UTC at millisecond precision so no validator rewrites
it and the two transports' decoded values are comparable."""

INK = b"v6-scene-bytes"
"""Stand-in for a page's v6 scene payload, which no adapter in this package interprets."""

UNDERLAY = b"%PDF-1.7 synthetic underlay"
"""Stand-in for the original PDF an annotated document sits over."""

UPLOAD_PAYLOAD = b"%PDF-1.7 synthetic upload"
"""The bytes every upload assertion offers. Not empty, so ``byte_count`` is falsifiable."""

SQLITE_MAGIC = b"SQLite format 3\x00"
"""The 16 bytes every SQLite database image begins with, NUL terminator included.

Spelled once for the whole suite: ``test_device_hardware.py`` checks the real
``rm-search-index.db`` against it, and :data:`SEARCH_INDEX_IMAGE` is prefixed with it so the
synthetic value is recognisably an image rather than arbitrary text."""

SEARCH_INDEX_IMAGE = SQLITE_MAGIC + b"synthetic index image, not a database"
"""Stand-in for the device's search-index image, which nothing in this package decodes.

Not a real database, and not captured from the device: the measured file is 503,808 bytes and
carries the user's own handwriting. Not empty either, so "the whole image crossed" is
falsifiable against the ``None`` that spells absence."""

FACT_FIELDS = frozenset(DeviceFacts.model_fields) - {"unsupported"}
"""Every field :class:`~rmspec.domain.ports.device.DeviceFacts` can answer. Derived from the
port rather than listed, so a field added there is covered without editing this file."""

RESOURCE_FIELDS = frozenset(DeviceResources.model_fields) - {"unsupported"}
"""Every field :class:`~rmspec.domain.ports.device.DeviceResources` can answer."""


class BoundCatalog(NamedTuple):
    """A catalog plus a way to count what its transport actually did.

    A :class:`~typing.NamedTuple` and not a pydantic model: one member is a ``Protocol``
    instance and the other a closure over a binding's own recorder, neither of which a
    validator can say anything useful about. It is frozen by construction, which is the
    property that matters here.
    """

    catalog: DeviceCatalog
    """The subject under test."""

    fetches: Callable[[], int]
    """How many transport round trips this catalog has performed so far."""


class SkipCase(NamedTuple):
    """A catalog whose listing reports one skip, and the identifier that skip carries."""

    catalog: DeviceCatalog
    """The subject under test."""

    doc_uuid: str
    """The identifier the skipped entry carries."""


class BoundUploader(NamedTuple):
    """An uploader plus a count of how many documents its transport actually placed."""

    uploader: DocumentUploader
    """The subject under test."""

    placed: Callable[[], int]
    """How many documents have landed. Zero after a refusal, which is what makes "raised
    before anything is written" assertable rather than assumed."""


class Reading(BaseModel, frozen=True, extra="forbid"):
    """One transport's answer to both facts methods, gathered so the rules read as one.

    A model rather than a tuple because the two halves are asserted against the same three
    rules and naming them at the call site is what keeps the assertions readable.
    """

    facts: DeviceFacts
    """What :meth:`~rmspec.domain.ports.device.DeviceFactsSource.read_facts` returned."""

    resources: DeviceResources
    """What ``read_resources`` returned."""


def an_upload(
    *,
    name: str = "Design review",
    media: UploadMedia = UploadMedia.PDF,
    parent_uuid: str | None = None,
) -> UploadRequest:
    """Build one upload request.

    Parameters
    ----------
    name
        The name the document should show in the tablet UI.
    media
        What the payload holds.
    parent_uuid
        Destination folder, or ``None`` for the library root.

    Returns
    -------
    UploadRequest
        The request, carrying :data:`UPLOAD_PAYLOAD`.
    """
    return UploadRequest(
        name=name,
        media=media,
        data=UPLOAD_PAYLOAD,
        parent_uuid=parent_uuid,
    )


def a_dead_transport() -> DeviceUnreachable:
    """Build the whole-transport failure every "this must raise" assertion seeds.

    Returns
    -------
    DeviceUnreachable
        A dead cable. The class matters: ``ports/device.py`` names it as the thing that must
        never degrade to an empty listing, and a binding that caught it per entry would turn
        an unplugged tablet into a library of unreadable folders.
    """
    return DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="the contract seeded a dead transport",
    )


class DeviceCatalogContract:
    """Every assertion ``DeviceCatalog`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def bound(self) -> BoundCatalog:
        """Return a catalog over the reference library, and its round-trip counter.

        Returns
        -------
        BoundCatalog
            The subject and its recorder.
        """
        raise NotImplementedError

    def catalog_with_skip(self, reason: SkipReason) -> SkipCase:
        """Return a catalog whose listing reports one skip carrying *reason*.

        The reference library's notebook must still be reported, so that a per-entry problem
        is visibly per-entry rather than costing the rest of the walk.

        Parameters
        ----------
        reason
            Which diagnosis the binding must cause.

        Returns
        -------
        SkipCase
            The subject and the identifier the skip carries.
        """
        raise NotImplementedError

    def failing_catalog(self, failure: DeviceError) -> DeviceCatalog:
        """Return a catalog whose whole transport fails with *failure*.

        Parameters
        ----------
        failure
            What the transport raises.

        Returns
        -------
        DeviceCatalog
            The subject.
        """
        raise NotImplementedError

    # ── the listing states everything ───────────────────────────────────────

    def test_a_listing_states_all_three_tuples(self, bound: BoundCatalog) -> None:
        """A listing states all three tuples."""
        listing = bound.catalog.list_documents()
        assert isinstance(listing.documents, tuple)
        assert isinstance(listing.folders, tuple)
        assert isinstance(listing.skipped, tuple)
        assert {document.uuid for document in listing.documents} == {NOTEBOOK_UUID, PDF_UUID}
        assert {folder.uuid for folder in listing.folders} == {FOLDER_UUID}
        assert listing.skipped == ()

    def test_a_listing_cannot_be_built_without_stating_what_it_could_not_read(self) -> None:
        """A listing cannot be built without stating what it could not read."""
        # Neither `folders` nor `skipped` has a default, and that is what makes the
        # assertion above non-vacuous: without this, an adapter could keep passing by
        # omitting the half it had nothing to say about.
        with pytest.raises(ValidationError):
            DeviceListing.model_validate({"documents": (), "folders": ()})
        with pytest.raises(ValidationError):
            DeviceListing.model_validate({"documents": (), "skipped": ()})

    def test_the_folder_tree_is_reconstructable_from_the_documents_it_holds(
        self,
        bound: BoundCatalog,
    ) -> None:
        """The folder tree is reconstructable from the documents it holds."""
        listing = bound.catalog.list_documents()
        by_uuid = {document.uuid: document for document in listing.documents}
        assert by_uuid[NOTEBOOK_UUID].parent_uuid is None
        assert by_uuid[PDF_UUID].parent_uuid == FOLDER_UUID
        assert listing.folders[0].parent_uuid is None

    # ── the four resolution branches ────────────────────────────────────────

    @pytest.mark.parametrize("doc_uuid", [NOTEBOOK_UUID, PDF_UUID])
    def test_a_listed_identifier_resolves_to_the_value_the_listing_reported(
        self,
        bound: BoundCatalog,
        doc_uuid: str,
    ) -> None:
        """A listed identifier resolves to the value the listing reported."""
        listing = bound.catalog.list_documents()
        listed = next(entry for entry in listing.documents if entry.uuid == doc_uuid)
        assert bound.catalog.get_document(doc_uuid) == listed

    def test_a_folder_identifier_is_not_a_document(self, bound: BoundCatalog) -> None:
        """A folder identifier is not a document."""
        with pytest.raises(DeviceDocumentNotFound) as caught:
            bound.catalog.get_document(FOLDER_UUID)
        assert caught.value.document_uuid == FOLDER_UUID

    def test_an_identifier_nothing_holds_is_not_found(self, bound: BoundCatalog) -> None:
        """An identifier nothing holds is not found."""
        with pytest.raises(DeviceDocumentNotFound) as caught:
            bound.catalog.get_document(UNKNOWN_UUID)
        assert caught.value.document_uuid == UNKNOWN_UUID

    @pytest.mark.parametrize("reason", list(SkipReason))
    def test_a_skipped_identifier_raises_malformed_whichever_reason_it_carried(
        self,
        reason: SkipReason,
    ) -> None:
        """A skipped identifier raises malformed whichever reason it carried."""
        # The members differ in diagnosis, not in outcome. An implementation that reported
        # UNREADABLE from the listing and a different error class from get_document would be
        # incoherent, and only running all three members catches it.
        case = self.catalog_with_skip(reason)
        with pytest.raises(MalformedDeviceMetadata) as caught:
            case.catalog.get_document(case.doc_uuid)
        assert caught.value.document_uuid == case.doc_uuid

    # ── per-entry failure is data; whole-transport failure raises ───────────

    @pytest.mark.parametrize("reason", list(SkipReason))
    def test_a_per_entry_problem_is_reported_rather_than_costing_the_walk(
        self,
        reason: SkipReason,
    ) -> None:
        """A per-entry problem is reported rather than costing the walk."""
        case = self.catalog_with_skip(reason)
        listing = case.catalog.list_documents()
        reported = [entry for entry in listing.skipped if entry.uuid == case.doc_uuid]
        assert [entry.reason for entry in reported] == [reason]
        assert NOTEBOOK_UUID in {document.uuid for document in listing.documents}

    def test_a_whole_transport_failure_raises_rather_than_reporting_an_empty_library(
        self,
    ) -> None:
        """A whole transport failure raises rather than reporting an empty library."""
        # The rule ports/device.py states and the one a per-entry `except` swallows: an
        # unplugged tablet must not read as a library with nothing in it.
        catalog = self.failing_catalog(a_dead_transport())
        with pytest.raises(DeviceUnreachable):
            catalog.list_documents()

    def test_a_whole_transport_failure_also_raises_from_get_document(self) -> None:
        """A whole transport failure also raises from get document."""
        catalog = self.failing_catalog(a_dead_transport())
        with pytest.raises(DeviceUnreachable):
            catalog.get_document(NOTEBOOK_UUID)

    # ── one command is one handshake ────────────────────────────────────────

    def test_the_library_is_enumerated_once_per_instance(self, bound: BoundCatalog) -> None:
        """The library is enumerated once per instance."""
        # Every port is one view over a single Scope.REQUEST transport resource, so an
        # instance's lifetime is one command. Through a total port a memoised listing and a
        # re-fetched one return equal values, so the counter is the only evidence which
        # happened -- and a second enumeration would also be a second chance to observe a
        # changed store, which is a different bug wearing the same clothes.
        bound.catalog.list_documents()
        after_one_listing = bound.fetches()
        assert after_one_listing > 0

        bound.catalog.list_documents()
        bound.catalog.get_document(NOTEBOOK_UUID)
        assert bound.fetches() == after_one_listing


class RawBundleSourceContract:
    """Every assertion ``RawBundleSource`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def source(self) -> RawBundleSource:
        """Return a bundle source over the reference library.

        Returns
        -------
        RawBundleSource
            The subject.
        """
        raise NotImplementedError

    def truncated_source(self) -> RawBundleSource:
        """Return a bundle source whose transfer of the notebook ends early.

        Returns
        -------
        RawBundleSource
            The subject.
        """
        raise NotImplementedError

    def failing_source(self, failure: DeviceError) -> RawBundleSource:
        """Return a bundle source whose whole transport fails with *failure*.

        Parameters
        ----------
        failure
            What the transport raises.

        Returns
        -------
        RawBundleSource
            The subject.
        """
        raise NotImplementedError

    # ── the bundle is the document that was asked for ───────────────────────

    @pytest.mark.parametrize("doc_uuid", [NOTEBOOK_UUID, PDF_UUID])
    def test_a_bundle_names_the_document_it_was_asked_for(
        self,
        source: RawBundleSource,
        doc_uuid: str,
    ) -> None:
        """A bundle names the document it was asked for."""
        assert source.load_bundle(doc_uuid).document.uuid == doc_uuid

    def test_the_notebooks_pages_are_in_the_recorded_order(self, source: RawBundleSource) -> None:
        """The notebook's pages are in the recorded order."""
        bundle = source.load_bundle(NOTEBOOK_UUID)
        assert tuple(page.page_id for page in bundle.pages) == NOTEBOOK_PAGES

    @pytest.mark.parametrize("doc_uuid", [NOTEBOOK_UUID, PDF_UUID])
    def test_page_identifiers_are_unique(self, source: RawBundleSource, doc_uuid: str) -> None:
        """Page identifiers are unique."""
        pages = source.load_bundle(doc_uuid).pages
        assert len({page.page_id for page in pages}) == len(pages)

    def test_a_page_carrying_no_ink_is_none_and_never_empty_bytes(
        self,
        source: RawBundleSource,
    ) -> None:
        """A page carrying no ink is none and never empty bytes."""
        by_id = {page.page_id: page for page in source.load_bundle(NOTEBOOK_UUID).pages}
        assert by_id[PAGE_ONE].scene == INK
        assert by_id[PAGE_TWO].scene is None

    # ── base contradicts nothing ────────────────────────────────────────────

    def test_a_notebook_carries_no_underlay(self, source: RawBundleSource) -> None:
        """A notebook carries no underlay."""
        assert source.load_bundle(NOTEBOOK_UUID).base is None

    def test_a_document_over_a_pdf_carries_its_underlay(self, source: RawBundleSource) -> None:
        """A document over a pdf carries its underlay."""
        bundle = source.load_bundle(PDF_UUID)
        assert bundle.document.file_type is DeviceFileType.PDF
        assert bundle.base == UNDERLAY

    # ── fetching is all-or-nothing ──────────────────────────────────────────

    def test_a_truncated_transfer_raises_and_returns_no_partial_bundle(self) -> None:
        """A truncated transfer raises and returns no partial bundle."""
        # There is nothing to inspect for holes, and that is the assertion: the only two
        # outcomes are a complete bundle and an exception, so a half-pulled document can
        # never be hashed and recorded as complete.
        source = self.truncated_source()
        with pytest.raises(DeviceTransferInterrupted) as caught:
            source.load_bundle(NOTEBOOK_UUID)
        assert caught.value.bytes_transferred >= 0

    # ── the catalog's coherence rules reach here too ─────────────────────────

    def test_an_identifier_nothing_holds_never_reaches_a_transfer(
        self,
        source: RawBundleSource,
    ) -> None:
        """An identifier nothing holds never reaches a transfer."""
        with pytest.raises(DeviceDocumentNotFound):
            source.load_bundle(UNKNOWN_UUID)

    def test_a_folder_identifier_never_reaches_a_bundle(self, source: RawBundleSource) -> None:
        """A folder identifier never reaches a bundle."""
        with pytest.raises(DeviceDocumentNotFound):
            source.load_bundle(FOLDER_UUID)

    def test_a_whole_transport_failure_raises(self) -> None:
        """A whole transport failure raises."""
        source = self.failing_source(a_dead_transport())
        with pytest.raises(DeviceUnreachable):
            source.load_bundle(NOTEBOOK_UUID)


class DeviceFactsSourceContract:
    """Every assertion ``DeviceFactsSource`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def source(self) -> DeviceFactsSource:
        """Return a facts source over an attached, healthy transport.

        Returns
        -------
        DeviceFactsSource
            The subject.
        """
        raise NotImplementedError

    def failing_source(self, failure: DeviceError) -> DeviceFactsSource:
        """Return a facts source whose whole transport fails with *failure*.

        Parameters
        ----------
        failure
            What the transport raises.

        Returns
        -------
        DeviceFactsSource
            The subject.
        """
        raise NotImplementedError

    @staticmethod
    def _read(source: DeviceFactsSource) -> Reading:
        """Take both readings from one source.

        Parameters
        ----------
        source
            The subject.

        Returns
        -------
        Reading
            Both answers, gathered.
        """
        return Reading(facts=source.read_facts(), resources=source.read_resources())

    # ── unsupported names only fields, and only unanswered ones ─────────────

    def test_unsupported_never_names_a_field_that_does_not_exist(
        self,
        source: DeviceFactsSource,
    ) -> None:
        """Unsupported never names a field that does not exist."""
        reading = self._read(source)
        assert reading.facts.unsupported <= FACT_FIELDS
        assert reading.resources.unsupported <= RESOURCE_FIELDS

    def test_unsupported_never_names_a_field_that_carries_a_value(
        self,
        source: DeviceFactsSource,
    ) -> None:
        """Unsupported never names a field that carries a value."""
        # Asserted against the adapter's own output rather than trusted to the port's
        # validator: a transport that learned to answer a field and forgot to stop naming it
        # is the drift this catches, and the validator only fires if the pair is constructed.
        reading = self._read(source)
        for name in reading.facts.unsupported:
            assert getattr(reading.facts, name) is None
        for name in reading.resources.unsupported:
            assert getattr(reading.resources, name) is None

    def test_the_port_refuses_the_pair_the_adapter_avoids(self) -> None:
        """The port refuses the pair the adapter avoids."""
        # Which is what makes the two assertions above non-vacuous.
        with pytest.raises(ValidationError):
            DeviceFacts.model_validate({"firmware": "3.27.3.0", "unsupported": {"firmware"}})
        with pytest.raises(ValidationError):
            DeviceFacts.model_validate({"unsupported": {"not_a_field"}})

    def test_the_two_causes_of_none_are_distinguishable(self) -> None:
        """The two causes of none are distinguishable."""
        # A field this transport structurally cannot ask, and a field it asked for and got
        # nothing intelligible back from. Both are None; only one is named.
        named = DeviceFacts(unsupported=frozenset({"serial"}))
        unanswered = DeviceFacts()
        assert named.serial is None
        assert unanswered.serial is None
        assert named != unanswered

    # ── a gauge pair is internally consistent ───────────────────────────────

    def test_a_free_reading_never_exceeds_its_total(self, source: DeviceFactsSource) -> None:
        """A free reading never exceeds its total."""
        resources = self._read(source).resources
        for total, free in (
            (resources.total_memory_bytes, resources.available_memory_bytes),
            (resources.total_storage_bytes, resources.available_storage_bytes),
        ):
            if total is not None and free is not None:
                assert free <= total

    def test_the_port_refuses_an_impossible_gauge_pair(self) -> None:
        """The port refuses an impossible gauge pair."""
        with pytest.raises(ValidationError):
            DeviceResources.model_validate(
                {"total_memory_bytes": 1024, "available_memory_bytes": 2048}
            )

    # ── both methods are total, and both raise on a dead transport ──────────

    def test_both_methods_answer_without_arguments(self, source: DeviceFactsSource) -> None:
        """Both methods answer without arguments."""
        reading = self._read(source)
        assert isinstance(reading.facts, DeviceFacts)
        assert isinstance(reading.resources, DeviceResources)

    def test_a_dead_transport_raises_from_read_facts(self) -> None:
        """A dead transport raises from read facts."""
        # Not "everything unsupported": a facts source that never touched the wire would
        # report a detached tablet as a transport that structurally cannot ask.
        source = self.failing_source(a_dead_transport())
        with pytest.raises(DeviceUnreachable):
            source.read_facts()

    def test_a_dead_transport_raises_from_read_resources(self) -> None:
        """A dead transport raises from read resources."""
        source = self.failing_source(a_dead_transport())
        with pytest.raises(DeviceUnreachable):
            source.read_resources()


class DocumentUploaderContract:
    """Every assertion ``DocumentUploader`` makes about its implementations.

    Two per-request asymmetries run through this port and every binding refuses one of them,
    which is why the seams below are shaped as "produce an uploader with *this* behaviour, or
    say you cannot". A refusal a binding cannot produce is skipped rather than faked, and a
    refusal it *does* produce is asserted -- so the skip list is a statement about the wire
    rather than about the test's ambition.
    """

    # ── seams a binding must provide ────────────────────────────────────────

    def uploader_for(self, *, honours_parent: bool) -> BoundUploader | None:
        """Return an uploader with that destination behaviour, or ``None``.

        Parameters
        ----------
        honours_parent
            Whether the uploader must be able to place a document in a named folder.

        Returns
        -------
        BoundUploader | None
            The subject and its placement counter, or ``None`` when this implementation
            cannot be made to behave that way at all -- in which case the assertions that
            need it skip rather than pretending to cover it.
        """
        raise NotImplementedError

    def unplaceable_media(self) -> frozenset[UploadMedia]:
        """Return the media this implementation's wire structurally cannot place.

        Empty for most bindings. The SSH adapter overrides it with
        :attr:`~rmspec.domain.ports.device.UploadMedia.RMDOC`, because placing an archive
        there means unpacking it and writing the sidecars by hand rather than converting a
        media -- and stating it here is what makes the *refusal* assertable rather than merely
        absent from the happy path.

        Returns
        -------
        frozenset[UploadMedia]
            The refused members.
        """
        return frozenset()

    def _require(self, *, honours_parent: bool) -> BoundUploader:
        """Return the uploader, or skip when this implementation cannot produce it.

        Parameters
        ----------
        honours_parent
            Whether the uploader must honour a destination folder.

        Returns
        -------
        BoundUploader
            The subject.
        """
        bound = self.uploader_for(honours_parent=honours_parent)
        if bound is None:
            behaviour = "honours" if honours_parent else "cannot honour"
            pytest.skip(f"this implementation has no uploader that {behaviour} a destination")
        return bound

    def _any(self) -> BoundUploader:
        """Return this implementation's uploader, whatever it does with a destination.

        Every assertion about a *receipt* runs through here rather than through
        :meth:`_require`, because a receipt's rules hold for both destination behaviours and
        gating them on one would have skipped them entirely for the binding that cannot
        express it.

        Returns
        -------
        BoundUploader
            The subject.
        """
        for honours in (True, False):
            bound = self.uploader_for(honours_parent=honours)
            if bound is not None:
                return bound
        pytest.fail("this implementation provides no uploader at all")

    def _placing(self, media: UploadMedia) -> BoundUploader:
        """Return an uploader that can place *media*, or skip.

        Parameters
        ----------
        media
            The media the caller wants placed.

        Returns
        -------
        BoundUploader
            The subject.
        """
        if media in self.unplaceable_media():
            pytest.skip(f"this implementation cannot place {media.value}")
        return self._any()

    def _refusing(self, media: UploadMedia) -> BoundUploader:
        """Return an uploader that cannot place *media*, or skip.

        Parameters
        ----------
        media
            The media the caller wants refused.

        Returns
        -------
        BoundUploader
            The subject.
        """
        if media not in self.unplaceable_media():
            pytest.skip(f"this implementation places {media.value}")
        return self._any()

    # ── a receipt restates the request ──────────────────────────────────────

    @pytest.mark.parametrize("media", list(UploadMedia))
    def test_a_receipt_restates_the_request_it_was_given(self, media: UploadMedia) -> None:
        """A receipt restates the request it was given."""
        bound = self._placing(media)
        request = an_upload(name="Design review", media=media)
        receipt = bound.uploader.upload(request)
        assert receipt.name == request.name
        assert receipt.media is request.media
        assert receipt.byte_count == len(request.data)
        assert bound.placed() == 1

    def test_every_receipt_names_a_library_refresh(self) -> None:
        """Every receipt names a library refresh."""
        # Reported as a post-condition of upload rather than through a separate refresh
        # port, so "uploaded but never made visible" is unrepresentable.
        assert self._any().uploader.upload(an_upload()).library_refresh in set(LibraryRefresh)

    # ── degrading a request is forbidden ────────────────────────────────────

    def test_a_destination_that_can_be_honoured_is_honoured(self) -> None:
        """A destination that can be honoured is honoured."""
        bound = self._require(honours_parent=True)
        receipt = bound.uploader.upload(an_upload(parent_uuid=FOLDER_UUID))
        assert receipt.byte_count == len(UPLOAD_PAYLOAD)
        assert bound.placed() == 1

    def test_a_destination_that_cannot_be_honoured_raises_before_anything_is_written(
        self,
    ) -> None:
        """A destination that cannot be honoured raises before anything is written."""
        # Never a silent placement at the library root: the caller asks for a folder and
        # gets a receipt reporting success for something it did not ask for.
        bound = self._require(honours_parent=False)
        with pytest.raises(DeviceOperationUnsupported) as caught:
            bound.uploader.upload(an_upload(parent_uuid=FOLDER_UUID))
        assert caught.value.operation == "upload"
        assert caught.value.supported_by != ()
        assert bound.placed() == 0

    def test_an_uploader_that_cannot_honour_a_destination_still_places_at_the_root(
        self,
    ) -> None:
        """An uploader that cannot honour a destination still places at the root."""
        # The asymmetry is per-request data, so a request that names no folder is not
        # refused by the same adapter that refuses one which does.
        bound = self._require(honours_parent=False)
        assert bound.uploader.upload(an_upload()).byte_count == len(UPLOAD_PAYLOAD)

    @pytest.mark.parametrize("media", list(UploadMedia))
    def test_a_media_this_wire_cannot_place_is_refused_before_anything_is_written(
        self,
        media: UploadMedia,
    ) -> None:
        """A media this wire cannot place is refused before anything is written."""
        # The other half of "degrading a request is forbidden": an adapter may not substitute a
        # media it does prefer any more than it may drop a destination. The refusal names the
        # media, so a shell can say what to retry and where.
        bound = self._refusing(media)
        with pytest.raises(DeviceOperationUnsupported) as caught:
            bound.uploader.upload(an_upload(media=media))
        assert media.value in caught.value.operation
        assert caught.value.supported_by != ()
        assert bound.placed() == 0


class SearchIndexSourceContract:
    """Every assertion ``SearchIndexSource`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def source(self) -> SearchIndexSource:
        """Return a source over a device that holds :data:`SEARCH_INDEX_IMAGE`.

        Returns
        -------
        SearchIndexSource
            The subject.
        """
        raise NotImplementedError

    def absent_source(self) -> SearchIndexSource:
        """Return a source over a device that has no index at all.

        Returns
        -------
        SearchIndexSource
            The subject. Not an exceptional state: the index is built by the tablet on its
            own schedule, so a device that has never built one is a device in a normal
            condition.
        """
        raise NotImplementedError

    def failing_source(self, failure: DeviceError) -> SearchIndexSource:
        """Return a source whose whole transport fails with *failure*.

        Parameters
        ----------
        failure
            What the transport raises.

        Returns
        -------
        SearchIndexSource
            The subject.
        """
        raise NotImplementedError

    # ── the image crosses whole, and nothing here reads it ──────────────────

    def test_the_whole_image_crosses_as_bytes(self, source: SearchIndexSource) -> None:
        """The whole image crosses as bytes."""
        # Bytes and not a path, and not a query interface: there is no sqlite3 binary on the
        # device and no BusyBox applet for one, so transport-the-image is the only shape
        # available. Equality against the seeded value is what makes "whole" falsifiable --
        # a truncating adapter would still return `bytes` of a plausible length.
        assert source.read_index() == SEARCH_INDEX_IMAGE

    def test_a_second_read_answers_the_same_image(self, source: SearchIndexSource) -> None:
        """A second read answers the same image."""
        # The port is Scope.REQUEST -- one image per command, read once and reused for every
        # page -- so an implementation that memoises and one that re-reads must be
        # indistinguishable through the port. Whether a caller memoises is asserted on the
        # double's own counter, which is the only place the difference is visible.
        assert source.read_index() == source.read_index()

    # ── absence is None, and never empty bytes ──────────────────────────────

    def test_a_device_with_no_index_answers_none_and_never_empty_bytes(self) -> None:
        """A device with no index answers none and never empty bytes."""
        # b"" would be an image the reader then fails to open, which is a different report
        # from "this device has not built one yet" -- and collapsing the two would let a
        # fresh device look like a corrupt store.
        assert self.absent_source().read_index() is None

    # ── a dead transport raises rather than reporting no index ──────────────

    def test_a_whole_transport_failure_raises_rather_than_reporting_no_index(self) -> None:
        """A whole transport failure raises rather than reporting no index."""
        # The distinction the port draws in its own Raises clause: a per-path read failure is
        # None, and everything that describes the session propagates. An unplugged tablet
        # reported as "no index" would silently disable a free prior and look like a cache
        # miss forever.
        source = self.failing_source(a_dead_transport())
        with pytest.raises(DeviceUnreachable):
            source.read_index()
