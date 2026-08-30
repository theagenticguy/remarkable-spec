"""All three resolution stages, the tie-break, the trash filter, and an incomplete listing.

How a ``DeviceCatalog`` is bound here, and why
----------------------------------------------
With a local in-memory fake, annotated against the Protocol -- the same pattern
``packages/rmspec-domain/tests/test_ports_device.py`` uses for the same port. It is **not**
bound with ``rmspec.device.testing``'s shipped doubles, and that is a decision rather than
an oversight:

* ``rmspec.app`` may import ``rmspec.domain`` and nothing else, and these tests hold
  themselves to the rule their source obeys. The architecture check only scans ``src/``,
  so importing an adapter here would pass the gate while breaking the property the gate
  exists to protect.
* ``rmspec.device.testing``'s own docstring records the cost: "Importing this subpackage
  does run the parent package's ``__init__``, which binds the real adapters and therefore
  both transport libraries." Binding a doubles module here would make a pure-policy suite
  need ``paramiko`` and ``httpx`` installed to test string matching.
* Conformance is still checked, and by the type gate rather than by convention. Every
  construction below passes ``_InMemoryCatalog`` to ``ResolveDocument(catalog=...)``,
  whose parameter is annotated ``DeviceCatalog``, so ``ty`` verifies structural
  conformance at every call site -- and a fake that drifted from the port fails the type
  gate, not a test three packages away.

The fake implements both catalog methods because the Protocol has two, and carries two
seams and nothing more: a whole-transport ``failure``, because a dead cable degrading to
an empty listing is the failure the port forbids and is indistinguishable from an empty
library unless the transport can be told to die, and a ``calls`` counter, because one
command must be one handshake.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from rmspec.app import ResolveDocument, ResolveDocumentRequest, ResolveDocumentResult
from rmspec.app.resolve import _UNIDENTIFIED
from rmspec.domain.errors import (
    AmbiguousDocument,
    DegradationKind,
    DeviceUnreachable,
    DocumentNotFound,
    DocumentStoreUnavailable,
    RmspecError,
    TransportKind,
    UsageError,
    exit_code,
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

ALPHA = "aaaaaaaa-1111-4111-8111-111111111111"
GAMMA = "aaaaaaaa-3333-4333-8333-333333333333"
BETA = "bbbbbbbb-2222-4222-8222-222222222222"
ABSENT = "cccccccc-0000-4000-8000-000000000000"
NOT_A_UUID = "zzzzzzzz-9999-4999-8999-999999999999"

EARLY = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
LATE = datetime.datetime(2026, 8, 29, tzinfo=datetime.UTC)


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
    page_count: int | None = None,
    last_modified: datetime.datetime | None = None,
    trashed: bool = False,
) -> DeviceDocument:
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=DeviceFileType.NOTEBOOK,
        page_count=page_count,
        last_modified=last_modified,
        trashed=trashed,
    )


def _listing(
    *documents: DeviceDocument,
    skipped: tuple[SkippedEntry, ...] = (),
    folders: tuple[DeviceFolder, ...] = (),
) -> DeviceListing:
    return DeviceListing(documents=documents, folders=folders, skipped=skipped)


def _resolve(listing: DeviceListing, query: str) -> ResolveDocumentResult:
    return ResolveDocument(catalog=_InMemoryCatalog(listing)).resolve(
        ResolveDocumentRequest(query=query)
    )


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so that frozen-ness is a runtime fact, not a type error.

    A direct ``result.chosen = ...`` is rejected by the type gate before the test can
    prove pydantic rejects it at runtime, and this repository allows no ``type: ignore``
    to get past that. A variable field name also keeps ``B010`` quiet without a ``noqa``.
    """
    setattr(target, field, value)


# ───────────────────────────── stage one: the exact uuid ─────────────────────────────


def test_an_exact_uuid_resolves():
    result = _resolve(_listing(_doc(ALPHA, "Notes"), _doc(BETA, "Sketches")), BETA)
    assert result.chosen.uuid == BETA
    assert result.also_matched == ()


def test_an_exact_uuid_outranks_a_name_that_contains_it():
    """Stage order is what makes a pasted uuid unambiguous even inside a document title."""
    result = _resolve(_listing(_doc(BETA, "Sketches"), _doc(ALPHA, f"copy of {BETA}")), BETA)
    assert result.chosen.uuid == BETA
    assert result.also_matched == ()


def test_a_36_character_query_that_is_no_uuid_falls_through_to_the_name():
    result = _resolve(_listing(_doc(ALPHA, f"backup {NOT_A_UUID}")), NOT_A_UUID)
    assert result.chosen.uuid == ALPHA


def test_a_uuid_shaped_query_matching_nothing_falls_through_both_later_stages():
    with pytest.raises(DocumentNotFound):
        _resolve(_listing(_doc(ALPHA, "Notes")), ABSENT)


# ───────────────────────── stage two: the uuid prefix, at 8 ─────────────────────────


def test_a_prefix_of_exactly_eight_characters_matches_by_identifier():
    result = _resolve(_listing(_doc(ALPHA, "Notes", page_count=1)), "aaaaaaaa")
    assert result.chosen.uuid == ALPHA


def test_a_prefix_of_seven_characters_is_not_treated_as_an_identifier():
    """Eight is the boundary: one character shorter falls through to the substring stage."""
    with pytest.raises(DocumentNotFound):
        _resolve(_listing(_doc(ALPHA, "Notes")), "aaaaaaa")


def test_a_seven_character_hex_query_still_matches_a_name_by_substring():
    """Which is what proves the previous test fell through rather than simply failed."""
    result = _resolve(_listing(_doc(ALPHA, "cafe12 plans")), "cafe12")
    assert result.chosen.uuid == ALPHA


def test_a_prefix_longer_than_eight_characters_may_contain_the_hyphen():
    result = _resolve(_listing(_doc(ALPHA, "Notes"), _doc(GAMMA, "Sketches")), "aaaaaaaa-1111")
    assert result.chosen.uuid == ALPHA


def test_a_prefix_is_matched_case_insensitively():
    result = _resolve(_listing(_doc(ALPHA, "Notes")), "AAAAAAAA-1111")
    assert result.chosen.uuid == ALPHA


def test_a_prefix_matching_several_documents_reports_all_of_them():
    result = _resolve(
        _listing(_doc(ALPHA, "Notes", page_count=1), _doc(GAMMA, "Sketches", page_count=9)),
        "aaaaaaaa",
    )
    assert result.chosen.uuid == GAMMA
    assert [candidate.uuid for candidate in result.also_matched] == [ALPHA]


def test_a_prefix_shaped_query_matching_no_identifier_falls_through_to_the_name():
    result = _resolve(_listing(_doc(ALPHA, "the 12345678 report")), "12345678")
    assert result.chosen.uuid == ALPHA


# ───────────────────────── stage three: the name substring ─────────────────────────


def test_a_name_substring_matches_case_insensitively():
    result = _resolve(_listing(_doc(ALPHA, "Meeting Notes")), "meeting")
    assert result.chosen.uuid == ALPHA


def test_a_name_substring_matches_mid_word():
    result = _resolve(_listing(_doc(ALPHA, "Q3 Planning")), "lanni")
    assert result.chosen.uuid == ALPHA


def test_nothing_matching_a_complete_listing_is_document_not_found():
    with pytest.raises(DocumentNotFound) as caught:
        _resolve(_listing(_doc(ALPHA, "Notes")), "invoices")
    assert caught.value.query == "invoices"


def test_surrounding_whitespace_is_not_part_of_a_search_term():
    result = _resolve(_listing(_doc(ALPHA, "Notes")), "  Notes  ")
    assert result.chosen.uuid == ALPHA


# ───────────────────────────── the tie-break ordering ─────────────────────────────


def test_the_higher_page_count_wins():
    result = _resolve(
        _listing(_doc(ALPHA, "notes a", page_count=3), _doc(BETA, "notes b", page_count=9)),
        "notes",
    )
    assert result.chosen.uuid == BETA


def test_the_later_modification_breaks_a_page_count_tie():
    result = _resolve(
        _listing(
            _doc(ALPHA, "notes a", page_count=3, last_modified=EARLY),
            _doc(BETA, "notes b", page_count=3, last_modified=LATE),
        ),
        "notes",
    )
    assert result.chosen.uuid == BETA


def test_an_unreported_page_count_loses_to_a_reported_one_of_zero():
    """``page_count`` is ``Field(ge=0)``, so the unknown floor cannot collide with a real count."""
    result = _resolve(
        _listing(_doc(ALPHA, "notes a"), _doc(BETA, "notes b", page_count=0)),
        "notes",
    )
    assert result.chosen.uuid == BETA


def test_an_unreported_modification_time_loses_to_a_reported_one():
    result = _resolve(
        _listing(_doc(ALPHA, "notes a"), _doc(BETA, "notes b", last_modified=EARLY)),
        "notes",
    )
    assert result.chosen.uuid == BETA


def test_documents_that_tie_completely_keep_listing_order():
    """The sort is stable, so resolution is deterministic even with nothing to rank on."""
    result = _resolve(_listing(_doc(ALPHA, "notes a"), _doc(BETA, "notes b")), "notes")
    assert result.chosen.uuid == ALPHA
    assert [candidate.uuid for candidate in result.also_matched] == [BETA]


def test_also_matched_is_in_ranked_order_rather_than_listing_order():
    result = _resolve(
        _listing(
            _doc(ALPHA, "notes a", page_count=1),
            _doc(BETA, "notes b", page_count=9),
            _doc(GAMMA, "notes c", page_count=5),
        ),
        "notes",
    )
    assert result.chosen.uuid == BETA
    assert [candidate.uuid for candidate in result.also_matched] == [GAMMA, ALPHA]


# ───────────────────────────── reporting the ambiguity ─────────────────────────────


def test_an_unambiguous_resolution_reports_nothing():
    result = _resolve(_listing(_doc(ALPHA, "Notes")), "Notes")
    assert result.also_matched == ()
    assert result.degradations == ()


def test_ambiguity_is_reported_as_candidates_and_as_a_degradation():
    result = _resolve(
        _listing(_doc(ALPHA, "notes a", page_count=1), _doc(BETA, "notes b", page_count=9)),
        "notes",
    )
    assert [candidate.uuid for candidate in result.also_matched] == [ALPHA]
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.AMBIGUOUS_AUTO_RESOLVED
    assert degradation.subject == "notes"
    assert degradation.substituted == BETA
    assert "2 documents matched" in degradation.detail


def test_a_candidate_carries_only_what_an_ambiguity_message_needs():
    result = _resolve(_listing(_doc(ALPHA, "notes a"), _doc(BETA, "notes b")), "notes")
    (candidate,) = result.also_matched
    assert candidate.uuid == BETA
    assert candidate.name == "notes b"


def test_the_candidates_are_the_shape_a_strict_boundary_raises_with():
    """``AmbiguousDocument`` already carries this element type, so the CLI just passes it on."""
    result = _resolve(_listing(_doc(ALPHA, "notes a"), _doc(BETA, "notes b")), "notes")
    error = AmbiguousDocument(query="notes", candidates=result.also_matched)
    assert error.candidates == result.also_matched
    assert exit_code(error) == 2


def test_resolution_has_no_strict_mode_of_its_own():
    assert "strict" not in ResolveDocumentRequest.model_fields


# ───────────────────────────── the trash filter ─────────────────────────────


def test_a_trashed_document_does_not_shadow_a_live_one_of_the_same_name():
    """The legacy bug, and the one ``remarkable-mcp`` still ships."""
    result = _resolve(
        _listing(
            _doc(ALPHA, "Notes", page_count=9, trashed=True),
            _doc(BETA, "Notes", page_count=1),
        ),
        "Notes",
    )
    assert result.chosen.uuid == BETA
    assert result.also_matched == ()


def test_a_query_matching_only_a_deleted_document_is_not_found():
    """The trash is not the library, so this is the answer rather than a degradation."""
    with pytest.raises(DocumentNotFound):
        _resolve(_listing(_doc(ALPHA, "Notes", trashed=True)), "Notes")


def test_a_trashed_document_is_excluded_before_the_ranking_rather_than_after():
    with pytest.raises(DocumentNotFound):
        _resolve(_listing(_doc(ALPHA, ALPHA[:8], trashed=True)), ALPHA)


def test_a_trashed_document_never_appears_among_the_candidates():
    result = _resolve(
        _listing(
            _doc(ALPHA, "notes a", page_count=1),
            _doc(GAMMA, "notes c", page_count=5, trashed=True),
            _doc(BETA, "notes b", page_count=9),
        ),
        "notes",
    )
    assert [candidate.uuid for candidate in result.also_matched] == [ALPHA]


# ─────────────────── a listing that omitted entries is not complete ───────────────────


def test_each_skipped_entry_becomes_one_degradation():
    skipped = (
        SkippedEntry(uuid="abc", reason=SkipReason.UNREADABLE, detail="permission denied"),
        SkippedEntry(uuid="def", reason=SkipReason.VALIDATION_FAILED, detail="no visibleName"),
    )
    result = _resolve(_listing(_doc(ALPHA, "Notes"), skipped=skipped), "Notes")
    assert [entry.kind for entry in result.degradations] == [
        DegradationKind.CATALOG_ENTRY_SKIPPED,
        DegradationKind.CATALOG_ENTRY_SKIPPED,
    ]
    assert [entry.subject for entry in result.degradations] == ["abc", "def"]
    assert result.degradations[0].detail == "unreadable: permission denied"


def test_a_skipped_entry_with_no_recoverable_identifier_is_still_reported():
    skipped = (SkippedEntry(uuid=None, reason=SkipReason.MALFORMED_METADATA, detail="not json"),)
    result = _resolve(_listing(_doc(ALPHA, "Notes"), skipped=skipped), "Notes")
    (degradation,) = result.degradations
    assert degradation.subject == _UNIDENTIFIED


def test_a_skipped_entry_and_an_ambiguity_are_both_reported():
    skipped = (SkippedEntry(uuid="abc", reason=SkipReason.UNREADABLE, detail="denied"),)
    result = _resolve(
        _listing(_doc(ALPHA, "notes a"), _doc(BETA, "notes b"), skipped=skipped),
        "notes",
    )
    assert [entry.kind for entry in result.degradations] == [
        DegradationKind.CATALOG_ENTRY_SKIPPED,
        DegradationKind.AMBIGUOUS_AUTO_RESOLVED,
    ]


def test_nothing_matching_an_incomplete_listing_is_not_reported_as_absence():
    """``DocumentNotFound`` would assert something the enumeration does not license."""
    skipped = (
        SkippedEntry(uuid="abc", reason=SkipReason.UNREADABLE, detail="denied"),
        SkippedEntry(uuid=None, reason=SkipReason.MALFORMED_METADATA, detail="not json"),
    )
    with pytest.raises(DocumentStoreUnavailable) as caught:
        _resolve(_listing(_doc(ALPHA, "Notes"), skipped=skipped), "invoices")
    assert "2 of 3 entries" in caught.value.detail
    assert "malformed_metadata, unreadable" in caught.value.detail
    assert "'invoices'" in caught.value.detail


def test_the_two_kinds_of_nothing_matched_exit_differently():
    """Which is what makes the distinction actionable rather than cosmetic."""
    complete = DocumentNotFound(query="invoices", store="device catalog")
    incomplete = DocumentStoreUnavailable(store="device catalog", detail="1 of 2 entries")
    assert exit_code(complete) == 66
    assert exit_code(incomplete) == 69


def test_a_match_found_despite_skipped_entries_still_resolves():
    """The omission only escalates when it could have changed the answer."""
    skipped = (SkippedEntry(uuid="abc", reason=SkipReason.UNREADABLE, detail="denied"),)
    result = _resolve(_listing(_doc(ALPHA, "Notes"), skipped=skipped), "Notes")
    assert result.chosen.uuid == ALPHA


# ───────────────────────────── the boundary conditions ─────────────────────────────


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_a_blank_query_is_a_usage_error(query: str):
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")))
    with pytest.raises(UsageError):
        ResolveDocument(catalog=catalog).resolve(ResolveDocumentRequest(query=query))


def test_a_blank_query_costs_no_device_round_trip():
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")))
    with pytest.raises(UsageError):
        ResolveDocument(catalog=catalog).resolve(ResolveDocumentRequest(query=" "))
    assert catalog.calls == 0


def test_one_resolution_is_one_handshake():
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")))
    ResolveDocument(catalog=catalog).resolve(ResolveDocumentRequest(query="Notes"))
    assert catalog.calls == 1


def test_an_empty_library_is_document_not_found_rather_than_a_crash():
    with pytest.raises(DocumentNotFound):
        _resolve(_listing(), "Notes")


def test_a_dead_transport_is_never_degraded_into_an_empty_library():
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connection refused",
    )
    catalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")), failure=failure)
    with pytest.raises(DeviceUnreachable):
        ResolveDocument(catalog=catalog).resolve(ResolveDocumentRequest(query="Notes"))


def test_a_folder_cannot_reach_this_policy_at_all():
    """Folders live in their own tuple, so legacy's CollectionType skip has no analogue here."""
    folders = (DeviceFolder(uuid=BETA, name="Notes"),)
    with pytest.raises(DocumentNotFound):
        _resolve(_listing(folders=folders), "Notes")


def test_the_fake_is_the_port_the_use_case_declares():
    catalog: DeviceCatalog = _InMemoryCatalog(_listing(_doc(ALPHA, "Notes")))
    assert catalog.get_document(ALPHA).name == "Notes"


def test_a_result_is_frozen():
    result = _resolve(_listing(_doc(ALPHA, "Notes")), "Notes")
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "chosen", _doc(BETA, "Sketches"))


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ResolveDocumentResult.model_validate(
            {
                "chosen": _doc(ALPHA, "Notes"),
                "also_matched": (),
                "degradations": (),
                "resolved_by": "stage three",
            }
        )
