"""Attribution, corroboration, a torn device index, and the two kinds of finding nothing.

How the three ports are bound here, and why
-------------------------------------------
With local in-memory fakes annotated against the Protocols, for the reasons
``test_app_resolve.py`` sets out: ``rmspec.app`` may import ``rmspec.domain`` and nothing
else, these tests hold themselves to the rule their source obeys, and the architecture check
only scans ``src/`` -- so binding ``rmspec-persistence``'s or ``rmspec-ocr``'s shipped doubles
here would pass the gate while breaking the property the gate exists to protect, and would
make a pure-policy suite need those packages' third-party dependencies installed to test
substring matching.

Conformance is checked by the type gate rather than by convention: every construction below
passes the fakes to ``SearchText(store=..., index=..., audit=...)``, whose parameters are
annotated with the Protocols, so ``ty`` verifies structural conformance at every call site and
a fake that drifted from a port fails the type gate rather than a test three packages away.

The store fake implements all eight of its port's methods because the Protocol has eight, and
the three write methods are real rather than stubs so that ``writes`` can prove a search does
not use them. Beyond that the fakes carry only seams the behaviour needs: a ``failure`` each
read can be told to raise, so a torn index is reachable and a dead mirror can be shown *not*
to degrade; and ``reads``/``lookups`` counters, because "the index is not consulted again after
it faults" and "the audit log is not read unless the question arises" are both claims about
calls not made.
"""

from __future__ import annotations

import datetime
import inspect
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from rmspec.app.search import (
    MatchSource,
    SearchOutcome,
    SearchText,
    SearchTextRequest,
    SearchTextResult,
    TextMatch,
)
from rmspec.domain.errors import (
    DegradationKind,
    DocumentNotFound,
    RmspecError,
    StoredRecordUnreadableError,
    StoreSchemaMismatchError,
    StoreUnavailableError,
    UsageError,
)
from rmspec.domain.models import (
    DocumentKind,
    PageText,
    RecordedSyncAuditEntry,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
    TextProvenance,
)
from rmspec.domain.ports.ocr import IndexedHandwriting

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.ports.ocr import HandwrittenTextIndex
    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog

WHEN = datetime.datetime(2026, 8, 29, 12, tzinfo=datetime.UTC)
PROVENANCE = TextProvenance(
    recognizers=("apple-vision@1",),
    model_fingerprint="claude-opus-4-6@1",
    render_dpi=226,
    extracted_at=WHEN,
)
INDEX_ID = "device-index@1"
GENERATION = 7

NOTES = "aaaaaaaa-1111-4111-8111-111111111111"
SKETCHES = "bbbbbbbb-2222-4222-8222-222222222222"


def _page_uuid(doc_uuid: str, index: int, /) -> str:
    return f"{doc_uuid}-page-{index}"


class _InMemoryStore:
    """A :class:`DocumentSyncStore` over dicts, with reads that can be told to die."""

    def __init__(self, *, failure: RmspecError | None = None) -> None:
        self.reads = 0
        self.writes = 0
        self.whole_mirror_reads = 0
        self._documents: list[SyncedDocument] = []
        self._pages: dict[str, list[SyncedPage]] = {}
        self._texts: dict[str, list[PageText]] = {}
        self._failure = failure

    def track(self, doc_uuid: str, name: str, /, *, pages: int) -> None:
        """Record a document with ``pages`` recorded pages and no text, as a pull would."""
        self.record_document(
            SyncedDocument(
                uuid=doc_uuid,
                visible_name=name,
                kind=DocumentKind.DOCUMENT,
                page_count=pages,
                synced_at=WHEN,
            ),
            [
                SyncedPage(
                    doc_uuid=doc_uuid,
                    page_uuid=_page_uuid(doc_uuid, index),
                    page_index=index,
                    synced_at=WHEN,
                )
                for index in range(pages)
            ],
        )

    def transcribe(self, doc_uuid: str, index: int, text: str, /) -> None:
        """Record one page's text, as an OCR run would."""
        self.record_page_text(
            PageText(
                doc_uuid=doc_uuid,
                page_uuid=_page_uuid(doc_uuid, index),
                page_index=index,
                text=text,
                provenance=PROVENANCE if text.strip() else TextProvenance(extracted_at=WHEN),
            )
        )

    def record_document(self, document: SyncedDocument, pages: Sequence[SyncedPage], /) -> None:
        """Replace a document's row and its whole page set."""
        self.writes += 1
        self._documents = [row for row in self._documents if row.uuid != document.uuid]
        self._documents.append(document)
        self._pages[document.uuid] = list(pages)
        self._texts.setdefault(document.uuid, [])

    def get_document(self, doc_uuid: str, /) -> SyncedDocument | None:
        """Look one row up, or answer ``None`` when the mirror does not track it."""
        self.reads += 1
        self._die()
        return next((row for row in self._documents if row.uuid == doc_uuid), None)

    def list_documents(self) -> list[SyncedDocument]:
        """Return every row in the port's contract order: case-folded name, then uuid."""
        self.reads += 1
        self._die()
        return sorted(self._documents, key=lambda row: (row.visible_name.casefold(), row.uuid))

    def pages(self, doc_uuid: str, /) -> list[SyncedPage]:
        """Return one document's recorded pages, by index then uuid."""
        self.reads += 1
        self._die()
        return sorted(
            self._pages.get(doc_uuid, []), key=lambda row: (row.page_index, row.page_uuid)
        )

    def forget_document(self, doc_uuid: str, /) -> None:
        """Drop a document, its pages and its text."""
        self.writes += 1
        self._documents = [row for row in self._documents if row.uuid != doc_uuid]
        self._pages.pop(doc_uuid, None)
        self._texts.pop(doc_uuid, None)

    def record_page_text(self, page_text: PageText, /) -> None:
        """Replace the text of one page, keyed by ``(doc_uuid, page_uuid)``."""
        self.writes += 1
        rows = self._texts.setdefault(page_text.doc_uuid, [])
        self._texts[page_text.doc_uuid] = [
            row for row in rows if row.page_uuid != page_text.page_uuid
        ]
        self._texts[page_text.doc_uuid].append(page_text)

    def page_texts(self, doc_uuid: str, /) -> list[PageText]:
        """Return one document's recorded text, by index then uuid."""
        self.reads += 1
        self._die()
        return sorted(
            self._texts.get(doc_uuid, []), key=lambda row: (row.page_index, row.page_uuid)
        )

    def all_page_texts(self) -> list[PageText]:
        """Return every recorded text row, which this use case never asks for."""
        self.whole_mirror_reads += 1
        self._die()
        rows = [row for texts in self._texts.values() for row in texts]
        return sorted(rows, key=lambda row: (row.doc_uuid, row.page_index, row.page_uuid))

    def _die(self) -> None:
        if self._failure is not None:
            raise self._failure


class _InMemoryIndex:
    """A :class:`HandwrittenTextIndex` over a dict, with a lookup that can be told to die."""

    def __init__(self, *, failure: RmspecError | None = None) -> None:
        self.lookups = 0
        self._rows: dict[str, IndexedHandwriting] = {}
        self._failure = failure

    @property
    def provider_id(self) -> str:
        """Return this index's stable identity slug."""
        return INDEX_ID

    def lookup(self, page_ref: str, /) -> IndexedHandwriting | None:
        """Return the index's row, or ``None`` -- which is its dominant state."""
        self.lookups += 1
        if self._failure is not None:
            raise self._failure
        return self._rows.get(page_ref)

    def index(self, doc_uuid: str, page: int, text: str, /) -> None:
        """Give the index a row for one page, as a tablet-side index build would."""
        page_ref = _page_uuid(doc_uuid, page)
        self._rows[page_ref] = IndexedHandwriting(
            page_ref=page_ref, entry_ref=doc_uuid, text=text, generation=GENERATION
        )


class _InMemoryAuditLog:
    """A :class:`SyncAuditLog` over a list, allocating sequences that are never reused."""

    def __init__(self, *, failure: RmspecError | None = None) -> None:
        self.calls = 0
        self._recorded: list[RecordedSyncAuditEntry] = []
        self._next = 1
        self._failure = failure

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Allocate the next sequence and keep the entry."""
        recorded = RecordedSyncAuditEntry(sequence=self._next, entry=entry)
        self._next += 1
        self._recorded.append(recorded)
        return recorded

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the newest entries first, or die as an unreadable store does."""
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        return list(reversed(self._recorded))[:limit]

    def record(self, operation: SyncOperation, /) -> None:
        """Append one successful operation of the given kind."""
        self.append(
            SyncAuditEntry(
                operation=operation,
                outcome=SyncOutcome.SUCCEEDED,
                doc_uuid=NOTES,
                doc_name="Notes",
                pages_affected=1,
                occurred_at=WHEN,
            )
        )


def _search(
    store: _InMemoryStore,
    index: _InMemoryIndex | None = None,
    audit: _InMemoryAuditLog | None = None,
    /,
    **kwargs: str,
) -> SearchTextResult:
    return SearchText(
        store=store,
        index=index if index is not None else _InMemoryIndex(),
        audit=audit if audit is not None else _InMemoryAuditLog(),
    ).search(SearchTextRequest(**kwargs))


def _one_page_store() -> _InMemoryStore:
    store = _InMemoryStore()
    store.track(NOTES, "Notes", pages=1)
    return store


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so that frozen-ness is a runtime fact, not a type error.

    The device ``test_app_resolve.py`` uses, and for the same reason: a direct assignment is
    rejected by the type gate before the test can prove pydantic rejects it at runtime, and
    this repository allows no ``type: ignore`` to get past that.
    """
    setattr(target, field, value)


# ───────────────────────── a hit from each source, attributed ─────────────────────────


def test_a_mirror_hit_carries_our_provenance():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "the retention pass drops old entries")
    (match,) = _search(store, query="retention").matches
    assert match.source is MatchSource.MIRROR
    assert match.provenance == PROVENANCE
    assert match.index_generation is None


def test_a_mirror_hit_names_the_document_and_the_page():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    (match,) = _search(store, query="retention").matches
    assert (match.doc_uuid, match.doc_name) == (NOTES, "Notes")
    assert (match.page_uuid, match.page_index) == (_page_uuid(NOTES, 0), 0)


def test_a_device_index_hit_needs_no_transcription_of_ours():
    """The free prior earning its keep: a page we have never paid to read."""
    index = _InMemoryIndex()
    index.index(NOTES, 0, "the retention pass, as the tablet read it")
    (match,) = _search(_one_page_store(), index, query="retention").matches
    assert match.source is MatchSource.DEVICE_INDEX
    assert match.text == "the retention pass, as the tablet read it"


def test_a_device_index_hit_claims_no_provenance_and_names_its_snapshot():
    index = _InMemoryIndex()
    index.index(NOTES, 0, "retention")
    (match,) = _search(_one_page_store(), index, query="retention").matches
    assert match.provenance is None
    assert match.index_generation == GENERATION


def test_a_single_source_hit_is_not_corroborated():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    (match,) = _search(store, query="retention").matches
    assert match.corroborated is False


# ───────────────────────── corroboration, reported not collapsed ─────────────────────────


def test_both_sources_matching_one_page_yields_both_readings():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "the retention pass")
    index = _InMemoryIndex()
    index.index(NOTES, 0, "the retentlon pass")
    matches = _search(store, index, query="retent").matches
    assert [match.source for match in matches] == [
        MatchSource.MIRROR,
        MatchSource.DEVICE_INDEX,
    ]
    assert [match.text for match in matches] == ["the retention pass", "the retentlon pass"]


def test_corroboration_is_flagged_on_both_rows():
    """Either row alone has to say so, or a filtered view loses the fact."""
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    index = _InMemoryIndex()
    index.index(NOTES, 0, "retention")
    matches = _search(store, index, query="retention").matches
    assert [match.corroborated for match in matches] == [True, True]


def test_one_source_missing_the_term_is_not_corroboration():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    index = _InMemoryIndex()
    index.index(NOTES, 0, "something else entirely")
    (match,) = _search(store, index, query="retention").matches
    assert match.source is MatchSource.MIRROR
    assert match.corroborated is False


# ───────────────────────────── how the term is matched ─────────────────────────────


def test_the_term_matches_case_insensitively():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "Retention Pass")
    assert _search(store, query="retention").outcome is SearchOutcome.MATCHED


def test_the_term_matches_mid_word():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    assert _search(store, query="tenti").outcome is SearchOutcome.MATCHED


def test_surrounding_whitespace_is_not_part_of_a_term():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    result = _search(store, query="  retention  ")
    assert result.outcome is SearchOutcome.MATCHED
    assert result.query == "retention"


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_a_blank_term_is_a_usage_error(query: str):
    with pytest.raises(UsageError):
        _search(_one_page_store(), query=query)


def test_a_blank_term_costs_no_read_of_either_source():
    store = _one_page_store()
    index = _InMemoryIndex()
    with pytest.raises(UsageError):
        _search(store, index, query=" ")
    assert (store.reads, index.lookups) == (0, 0)


# ───────────────────── the two kinds of nothing, told apart ─────────────────────


def test_text_that_does_not_contain_the_term_is_a_no_match():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "something else entirely")
    result = _search(store, query="retention")
    assert result.outcome is SearchOutcome.NO_MATCH
    assert result.pages_searched == 1


def test_a_page_with_no_text_from_either_source_is_not_a_no_match():
    """The distinction: a user who has never run ``ocr`` is not told their words are absent."""
    result = _search(_one_page_store(), query="retention")
    assert result.outcome is SearchOutcome.NOTHING_TRANSCRIBED
    assert result.pages_searched == 1


def test_nothing_recorded_at_all_is_reported_as_nothing_synced():
    result = _search(_InMemoryStore(), query="retention")
    assert result.outcome is SearchOutcome.NOTHING_SYNCED
    assert result.pages_searched == 0


def test_an_empty_transcription_still_counts_as_transcribed():
    """``PageText`` with empty text means the page was read and held nothing."""
    store = _one_page_store()
    store.transcribe(NOTES, 0, "")
    assert _search(store, query="retention").outcome is SearchOutcome.NO_MATCH


def test_an_empty_device_index_reading_also_counts_as_transcribed():
    """The port is explicit: an empty reading is positive, an unindexed page has no row."""
    index = _InMemoryIndex()
    index.index(NOTES, 0, "")
    assert _search(_one_page_store(), index, query="retention").outcome is SearchOutcome.NO_MATCH


def test_the_audit_log_says_whether_a_transcription_was_ever_attempted():
    audit = _InMemoryAuditLog()
    audit.record(SyncOperation.OCR)
    result = _search(_one_page_store(), None, audit, query="retention")
    assert result.outcome is SearchOutcome.NOTHING_TRANSCRIBED
    assert result.recent_ocr_attempt is True


def test_a_history_of_pulls_alone_is_no_evidence_of_a_transcription():
    audit = _InMemoryAuditLog()
    audit.record(SyncOperation.PULL)
    result = _search(_one_page_store(), None, audit, query="retention")
    assert result.recent_ocr_attempt is False


def test_the_audit_log_is_not_read_when_the_question_does_not_arise():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    audit = _InMemoryAuditLog()
    result = _search(store, None, audit, query="retention")
    assert result.recent_ocr_attempt is None
    assert audit.calls == 0


def test_the_audit_probe_is_evidence_rather_than_a_third_outcome():
    """It refines ``NOTHING_TRANSCRIBED`` and never replaces it, because the log is bounded."""
    audit = _InMemoryAuditLog()
    audit.record(SyncOperation.OCR)
    assert (
        _search(_one_page_store(), None, audit, query="retention").outcome
        is SearchOutcome.NOTHING_TRANSCRIBED
    )


# ───────────────────── a torn device index degrades, and only it ─────────────────────


def test_an_unreadable_device_index_degrades_rather_than_failing():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    failure = StoreUnavailableError(store="xochitl index", detail="disk image is malformed")
    result = _search(store, _InMemoryIndex(failure=failure), query="retention")
    assert [match.source for match in result.matches] == [MatchSource.MIRROR]
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.DEVICE_INDEX_UNAVAILABLE
    assert degradation.subject == INDEX_ID
    assert "disk image is malformed" in degradation.detail


def test_a_schema_mismatch_degrades_by_the_same_clause():
    """``StoreSchemaMismatchError`` subclasses ``StoreUnavailableError``, so one clause does."""
    failure = StoreSchemaMismatchError(store="xochitl index", found=3, expected=4)
    result = _search(_one_page_store(), _InMemoryIndex(failure=failure), query="retention")
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.DEVICE_INDEX_UNAVAILABLE


def test_a_torn_index_is_consulted_once_and_reported_once():
    """Four hundred identical degradations would bury the ones that mean something."""
    store = _InMemoryStore()
    store.track(NOTES, "Notes", pages=4)
    failure = StoreUnavailableError(store="xochitl index", detail="torn read")
    index = _InMemoryIndex(failure=failure)
    result = _search(store, index, query="retention")
    assert index.lookups == 1
    assert len(result.degradations) == 1
    assert result.pages_searched == 4


def test_a_torn_index_does_not_make_untranscribed_pages_look_searched():
    failure = StoreUnavailableError(store="xochitl index", detail="torn read")
    result = _search(_one_page_store(), _InMemoryIndex(failure=failure), query="retention")
    assert result.outcome is SearchOutcome.NOTHING_TRANSCRIBED


def test_an_unreadable_mirror_is_never_degraded_into_an_empty_result():
    """The mirror is the answer, not a bonus, so losing it is a failure rather than a footnote."""
    failure = StoreUnavailableError(store="sync.db", detail="database is locked")
    with pytest.raises(StoreUnavailableError):
        _search(_InMemoryStore(failure=failure), query="retention")


def test_an_unreadable_stored_row_propagates():
    failure = StoredRecordUnreadableError(
        store="sync.db", table="page_text", key=NOTES, detail="not json"
    )
    with pytest.raises(StoredRecordUnreadableError):
        _search(_InMemoryStore(failure=failure), query="retention")


def test_an_unreadable_audit_log_propagates():
    failure = StoreUnavailableError(store="sync.db", detail="database is locked")
    with pytest.raises(StoreUnavailableError):
        _search(_one_page_store(), None, _InMemoryAuditLog(failure=failure), query="retention")


# ───────────────────────────── scope, order, and cost ─────────────────────────────


def test_a_scope_restricts_the_search_to_one_document():
    store = _InMemoryStore()
    store.track(NOTES, "Notes", pages=1)
    store.track(SKETCHES, "Sketches", pages=1)
    store.transcribe(NOTES, 0, "retention")
    store.transcribe(SKETCHES, 0, "retention")
    result = _search(store, query="retention", doc_uuid=SKETCHES)
    assert [match.doc_uuid for match in result.matches] == [SKETCHES]
    assert result.pages_searched == 1


def test_a_scope_naming_an_untracked_document_is_not_an_empty_result():
    with pytest.raises(DocumentNotFound) as caught:
        _search(_one_page_store(), query="retention", doc_uuid=SKETCHES)
    assert caught.value.query == SKETCHES


def test_matches_are_ordered_by_document_name_then_page_index():
    store = _InMemoryStore()
    store.track(SKETCHES, "apple notes", pages=2)
    store.track(NOTES, "Banana notes", pages=1)
    store.transcribe(SKETCHES, 1, "retention")
    store.transcribe(SKETCHES, 0, "retention")
    store.transcribe(NOTES, 0, "retention")
    result = _search(store, query="retention")
    assert [(match.doc_name, match.page_index) for match in result.matches] == [
        ("apple notes", 0),
        ("apple notes", 1),
        ("Banana notes", 0),
    ]


def test_a_page_consulted_in_both_sources_is_counted_once():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    index = _InMemoryIndex()
    index.index(NOTES, 0, "retention")
    result = _search(store, index, query="retention")
    assert len(result.matches) == 2
    assert result.pages_searched == 1


def test_the_cost_is_the_scope_plus_two_reads_per_document():
    store = _InMemoryStore()
    store.track(NOTES, "Notes", pages=1)
    store.track(SKETCHES, "Sketches", pages=1)
    _search(store, query="retention")
    assert store.reads == 1 + 2 * 2


def test_a_search_writes_nothing():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    writes = store.writes
    _search(store, query="retention")
    assert store.writes == writes


# ───────────────────── what it refuses to be, and what it refuses to take ─────────────────────


def test_the_use_case_takes_no_cache_collaborator():
    """The OCR cache is keyed by digest so it cannot be browsed; not taking it is the proof."""
    parameters = inspect.signature(SearchText.__init__).parameters
    assert set(parameters) == {"self", "store", "index", "audit"}


def test_the_request_offers_a_term_and_a_scope_and_nothing_else():
    """No ranking option, no field selector, no regex flag: this layer invents no index."""
    assert set(SearchTextRequest.model_fields) == {"query", "doc_uuid"}


def test_the_whole_mirror_text_read_is_not_used():
    """``all_page_texts`` cannot pair a row with the page ref the device index is keyed by."""
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    _search(store, query="retention")
    assert store.whole_mirror_reads == 0


# ───────────────────────────── the attribution is unforgeable ─────────────────────────────


def _match_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "doc_uuid": NOTES,
        "doc_name": "Notes",
        "page_uuid": _page_uuid(NOTES, 0),
        "page_index": 0,
        "source": MatchSource.MIRROR,
        "text": "retention",
        "provenance": PROVENANCE,
        "index_generation": None,
        "corroborated": False,
    }
    return fields | overrides


def test_a_mirror_match_without_provenance_is_unconstructible():
    with pytest.raises(ValidationError, match="provenance"):
        TextMatch.model_validate(_match_fields(provenance=None))


def test_a_device_match_claiming_provenance_is_unconstructible():
    with pytest.raises(ValidationError, match="provenance"):
        TextMatch.model_validate(
            _match_fields(source=MatchSource.DEVICE_INDEX, index_generation=GENERATION)
        )


def test_a_mirror_match_claiming_an_index_generation_is_unconstructible():
    with pytest.raises(ValidationError, match="generation"):
        TextMatch.model_validate(_match_fields(index_generation=GENERATION))


def test_a_device_match_without_a_generation_is_unconstructible():
    with pytest.raises(ValidationError, match="generation"):
        TextMatch.model_validate(_match_fields(source=MatchSource.DEVICE_INDEX, provenance=None))


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_the_fakes_are_the_ports_the_use_case_declares():
    store: DocumentSyncStore = _one_page_store()
    index: HandwrittenTextIndex = _InMemoryIndex()
    audit: SyncAuditLog = _InMemoryAuditLog()
    assert store.list_documents()[0].uuid == NOTES
    assert index.lookup(_page_uuid(NOTES, 0)) is None
    assert audit.recent(limit=1) == []


def test_a_result_is_frozen():
    result = _search(_one_page_store(), query="retention")
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "outcome", SearchOutcome.MATCHED)


def test_a_match_is_frozen():
    store = _one_page_store()
    store.transcribe(NOTES, 0, "retention")
    (match,) = _search(store, query="retention").matches
    with pytest.raises(ValidationError, match="frozen"):
        _assign(match, "source", MatchSource.DEVICE_INDEX)


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        SearchTextResult.model_validate(
            {
                "outcome": SearchOutcome.NO_MATCH,
                "query": "retention",
                "matches": (),
                "pages_searched": 0,
                "recent_ocr_attempt": None,
                "degradations": (),
                "ranked": False,
            }
        )
