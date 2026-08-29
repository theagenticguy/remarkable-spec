"""Properties of the two pure derivations, and of the sequence allocator.

Three claims in this package are universal rather than exemplary, so they are
tested over generated input instead of over a handful of cases.

``ORDER BY name_fold, uuid`` *is* ``sorted(key=(casefold, uuid))``. The port
declares a case-folded order and the adapter delegates it to SQLite, which
compares UTF-8 bytes under its BINARY collation. That equals Python's code-point
comparison, but only because UTF-8 byte order is code-point order -- a claim worth
generating adversarial names against rather than asserting once with three ASCII
strings.

``utc_key`` orders text the way the instants order. Pydantic serialises an aware
datetime with whatever offset it carries, and comparing those strings across mixed
offsets is not chronological, which is why the column is normalised first. The
indexed ``created_at_utc < ?`` that ``prune`` runs is only correct if this holds
for every pair.

A sequence is never reused. Whatever the history of appends and trims, the next
sequence exceeds every sequence ever handed out.

The two ``DocumentSyncStore`` bindings agree. ``record_document`` implements four
coupled write rules in each binding independently, so the one place a future edit
to one side would go unnoticed is exactly there -- and it had already drifted on a
fifth rule neither stated. A random program of records, texts, forgets and reads
runs against both in lockstep, compared after every step, results and raised errors
alike.

No test here uses a function-scoped fixture: hypothesis rejects one outright, and a
database opened inside the example body is also the only way to keep each example
independent while still closing every connection.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from hypothesis import given, settings
from hypothesis import strategies as st
from persistence_builders import (
    FROZEN_NOW,
    a_document,
    a_page,
    a_page_text,
    an_audit_entry,
    an_ocr_artifact,
    an_ocr_key,
)

from rmspec.domain.errors import PersistenceError
from rmspec.persistence import (
    SqliteDatabase,
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    StoreMaintenance,
)
from rmspec.persistence.derived import utc_key
from rmspec.persistence.testing import InMemoryDocumentSyncStore, InMemorySyncAuditLog

if TYPE_CHECKING:
    from rmspec.domain.ports.persistence import DocumentSyncStore

#: Text without surrogates or control characters. Casefold and UTF-8 encoding are
#: both total over this, and it still reaches the interesting cases -- German
#: sharp s, Turkish dotted capitals, Greek final sigma.
TEXT = st.text(
    alphabet=st.characters(exclude_categories=("Cs", "Cc")),
    max_size=12,
)

#: Fixed offsets rather than named zones, so no test depends on a tz database.
OFFSETS = st.sampled_from([timezone(timedelta(hours=hours)) for hours in (-11, -5, 0, 2, 9, 13)])

#: Bounds are parsed rather than constructed: ``st.datetimes`` requires naive
#: bounds, and a bare ``datetime(...)`` with no tzinfo is banned in this workspace.
MOMENTS = st.datetimes(
    min_value=datetime.fromisoformat("1970-01-01T00:00:00"),
    max_value=datetime.fromisoformat("2200-01-01T00:00:00"),
    timezones=OFFSETS,
)


@given(
    named=st.lists(
        st.tuples(
            st.text(
                alphabet=st.characters(exclude_categories=("Cs", "Cc")), min_size=1, max_size=8
            ),
            TEXT,
        ),
        min_size=1,
        max_size=8,
        unique_by=lambda pair: pair[0],
    ),
)
@settings(deadline=None, max_examples=40)
def test_sql_document_order_equals_python_sorted(named: list[tuple[str, str]]) -> None:
    with tempfile.TemporaryDirectory() as raw:
        database = SqliteDatabase.open(Path(raw) / "sync.db")
        try:
            store = SqliteDocumentSyncStore(database)
            for uuid, name in named:
                store.record_document(a_document(uuid, name=name), [])
            listed = [document.uuid for document in store.list_documents()]
        finally:
            database.close()
    expected = [uuid for uuid, _ in sorted(named, key=lambda pair: (pair[1].casefold(), pair[0]))]
    assert listed == expected


@given(first=MOMENTS, second=MOMENTS)
@settings(max_examples=200)
def test_utc_key_orders_text_the_way_the_instants_order(
    first: datetime,
    second: datetime,
) -> None:
    assert (utc_key(first) < utc_key(second)) == (first < second)
    assert (utc_key(first) == utc_key(second)) == (first == second)


@given(moment=MOMENTS)
@settings(max_examples=50)
def test_utc_key_is_always_the_same_instant_in_utc(moment: datetime) -> None:
    assert datetime.fromisoformat(utc_key(moment)) == moment
    assert utc_key(moment).endswith("+00:00")


@given(
    ages=st.lists(
        st.integers(min_value=-500, max_value=500), min_size=1, max_size=10, unique=True
    ),
    cutoff_age=st.integers(min_value=-500, max_value=500),
)
@settings(deadline=None, max_examples=25)
def test_pruning_by_age_deletes_exactly_the_older_entries(
    ages: list[int],
    cutoff_age: int,
) -> None:
    cutoff = FROZEN_NOW + timedelta(days=cutoff_age)
    with tempfile.TemporaryDirectory() as raw:
        database = SqliteDatabase.open(Path(raw) / "sync.db")
        try:
            cache = SqliteOcrCache(database)
            keys = {age: an_ocr_key(f"page-{age}") for age in ages}
            for age, key in keys.items():
                cache.put(key, an_ocr_artifact(created_at=FROZEN_NOW + timedelta(days=age)))

            deleted = StoreMaintenance(database).prune_ocr(older_than=cutoff)

            expected = [age for age in ages if age < cutoff_age]
            assert deleted == len(expected)
            for age, key in keys.items():
                assert (cache.get(key) is None) == (age in expected)
        finally:
            database.close()


@given(
    appends=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=6),
    keep=st.integers(min_value=1, max_value=3),
)
@settings(max_examples=100)
def test_a_sequence_is_never_reused_whatever_the_retention_history(
    appends: list[int],
    keep: int,
) -> None:
    log = InMemorySyncAuditLog()
    handed_out: list[int] = []
    for batch in appends:
        handed_out.extend(log.append(an_audit_entry()).sequence for _ in range(batch))
        log.retain_newest(keep)
    assert handed_out == sorted(set(handed_out))
    # Everything still readable was handed out at some point: a trim removes
    # entries, it never renumbers the survivors.
    assert all(
        recorded.sequence in handed_out for recorded in log.recent(limit=len(handed_out) + 1)
    )
    assert log.append(an_audit_entry()).sequence > max(handed_out)


#: The universe the lockstep program draws from. Small on purpose: three page uuids
#: over two documents is what makes departures, re-indexings, re-parentings and
#: uuid collisions actually collide within a twelve-step program.
DOC_UUIDS = st.sampled_from(["doc-a", "doc-b"])
PAGE_UUIDS = st.sampled_from(["page-1", "page-2", "page-3"])
INDEXES = st.integers(min_value=0, max_value=3)

#: A page set for ``record_document``, where each page names its own owner --
#: including, deliberately, one that is not the document being recorded.
PAGE_SPECS = st.lists(st.tuples(DOC_UUIDS, PAGE_UUIDS, INDEXES), max_size=3)

OPERATIONS = st.one_of(
    st.tuples(st.just("record"), DOC_UUIDS, PAGE_SPECS),
    st.tuples(st.just("text"), DOC_UUIDS, PAGE_UUIDS, INDEXES),
    st.tuples(st.just("forget"), DOC_UUIDS),
    st.tuples(st.just("pages"), DOC_UUIDS),
    st.tuples(st.just("texts"), DOC_UUIDS),
    st.tuples(st.just("all_texts")),
    st.tuples(st.just("documents")),
)

#: One generated operation. Spelled out as a discriminated union rather than
#: ``tuple[object, ...]`` so the match arms below are typed rather than cast.
type Operation = (
    tuple[Literal["record"], str, list[tuple[str, str, int]]]
    | tuple[Literal["text"], str, str, int]
    | tuple[Literal["forget"], str]
    | tuple[Literal["pages"], str]
    | tuple[Literal["texts"], str]
    | tuple[Literal["all_texts"]]
    | tuple[Literal["documents"]]
)


def _apply(store: DocumentSyncStore, operation: Operation) -> object:
    """Run one operation and return whatever a caller could observe from it.

    Parameters
    ----------
    store
        The binding under test.
    operation
        A generated operation tuple.

    Returns
    -------
    object
        The operation's result, reduced to plain tuples so two bindings can be
        compared without depending on model identity.
    """
    observed: object = None
    match operation:
        case ("record", doc_uuid, specs):
            pages = [a_page(owner, page_uuid, index) for owner, page_uuid, index in specs]
            store.record_document(a_document(doc_uuid), pages)
        case ("text", doc_uuid, page_uuid, index):
            store.record_page_text(a_page_text(doc_uuid, page_uuid, index))
        case ("forget", doc_uuid):
            store.forget_document(doc_uuid)
        case ("pages", doc_uuid):
            observed = [
                (page.doc_uuid, page.page_uuid, page.page_index) for page in store.pages(doc_uuid)
            ]
        case ("texts", doc_uuid):
            observed = [
                (text.page_uuid, text.page_index, text.text) for text in store.page_texts(doc_uuid)
            ]
        case ("all_texts",):
            observed = [
                (text.doc_uuid, text.page_uuid, text.page_index) for text in store.all_page_texts()
            ]
        case _:
            observed = [document.uuid for document in store.list_documents()]
    return observed


def _outcome(store: DocumentSyncStore, operation: Operation) -> object:
    """Return an operation's result, or the error it raised, as a comparable value.

    Parameters
    ----------
    store
        The binding under test.
    operation
        A generated operation tuple.

    Returns
    -------
    object
        The result, or ``(type name, message)`` when the call raised. The message
        is part of the comparison on purpose: two bindings that refuse the same
        call for different stated reasons still disagree.
    """
    try:
        return _apply(store, operation)
    except (ValueError, PersistenceError) as exc:
        return (type(exc).__name__, str(exc))


def _snapshot(store: DocumentSyncStore) -> object:
    """Return everything readable from a store, reduced to plain values.

    Parameters
    ----------
    store
        The binding to read.

    Returns
    -------
    object
        Documents, each document's pages, and all page text.
    """
    return (
        [document.uuid for document in store.list_documents()],
        [
            (doc_uuid, [(page.page_uuid, page.page_index) for page in store.pages(doc_uuid)])
            for doc_uuid in ("doc-a", "doc-b")
        ],
        [
            (text.doc_uuid, text.page_uuid, text.page_index, text.text)
            for text in store.all_page_texts()
        ],
    )


@given(program=st.lists(OPERATIONS, min_size=1, max_size=12))
@settings(deadline=None, max_examples=60)
def test_the_two_bindings_answer_a_random_program_identically(
    program: list[Operation],
) -> None:
    # The differential the example-based contract cannot replace. `record_document`
    # implements four coupled write rules -- replace the page set, drop the departed
    # pages' text, keep the survivors', re-index the survivors' payloads -- twice,
    # once in each binding, and a fifth rule neither used to state: which document a
    # page belongs to. That fifth one had already drifted, and every contract input
    # was consistent, so nothing could see it. Here the two run in lockstep and are
    # compared after every step, including the error they raise and its message.
    with tempfile.TemporaryDirectory() as raw:
        database = SqliteDatabase.open(Path(raw) / "sync.db")
        try:
            adapter: DocumentSyncStore = SqliteDocumentSyncStore(database)
            double: DocumentSyncStore = InMemoryDocumentSyncStore()
            for step, operation in enumerate(program):
                assert _outcome(adapter, operation) == _outcome(double, operation), (
                    f"step {step}: {operation!r}"
                )
                assert _snapshot(adapter) == _snapshot(double), f"after step {step}: {operation!r}"
        finally:
            database.close()


@given(moment=MOMENTS)
@settings(max_examples=50)
def test_an_artifact_timestamp_survives_the_payload_round_trip(moment: datetime) -> None:
    # Pydantic's JSON form is not ``datetime.isoformat()`` -- it writes ``Z`` for
    # UTC where isoformat writes ``+00:00`` -- so the round trip is asserted on
    # the model rather than on the text.
    artifact = an_ocr_artifact(created_at=moment)
    restored = type(artifact).model_validate_json(artifact.model_dump_json())
    assert restored == artifact
    assert restored.created_at == moment
    assert restored.created_at.utcoffset() == moment.utcoffset()
