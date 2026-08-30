"""The tier-0 reader, against real SQLite images built here and never committed.

Every fixture in this file is a genuine database: the measured FTS5 schema is
created in memory, filled, and handed to ``Connection.serialize()``. So the reader is
exercised against bytes with real page headers, a real inverted index and real
shadow tables, with no device attached and no binary fixture in the tree. Truncating
those bytes produces exactly the torn image the reader exists to refuse.

One property of the builder is load-bearing and was measured rather than assumed:
the connection must be in autocommit -- ``isolation_level=None``, which is also the
house rule in ``_sqlite.py`` -- before ``serialize()``. Python's default
``isolation_level=""`` opens an implicit transaction on the first ``INSERT``, and an
image serialized inside it deserializes into a database whose ``PRAGMA quick_check``
reports ``malformed inverted index for FTS5 table main.search``. That is the builder
lying about its own fixture, not the reader failing, and it would have made every
happy-path assertion here unreachable.
"""

from __future__ import annotations

import gc
import sqlite3
from typing import TYPE_CHECKING, Final

import pytest
from persistence_contracts import INDEXED_ROWS, HandwrittenTextIndexContract

from rmspec.domain.errors import (
    DeviceError,
    DeviceUnreachable,
    StoredRecordUnreadableError,
    StoreSchemaMismatchError,
    StoreUnavailableError,
    TransportKind,
)
from rmspec.persistence import DeviceSearchIndex
from rmspec.persistence.testing import FakeHandwrittenTextIndex

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.ports.device import SearchIndexSource
    from rmspec.domain.ports.ocr import HandwrittenTextIndex

#: The store label the reader puts in every error it raises.
STORE: Final = "rm-search-index"

#: The ``version`` value and the ``generation`` counter read off the attached tablet
#: on firmware 3.27.3.0.
MEASURED_SCHEMA_VERSION: Final = 9
MEASURED_GENERATION: Final = 1788031309223100658

#: The device's own ``search`` table definition, verbatim. Triple-quoted because it
#: contains both quote characters, and copied rather than paraphrased because the
#: ``tokenize`` option and the ``UNINDEXED`` columns are part of what the reader has
#: to survive.
MEASURED_SEARCH_DDL: Final = """CREATE VIRTUAL TABLE search USING fts5(
    entryId, pageId, handwrittenText, authorMap UNINDEXED, wordStrokes UNINDEXED,
    digitalText, title, tags, type,
    tokenize="unicode61 categories 'L* M* N* Cf Co P* S*'")"""

#: A ``search`` table a later firmware might plausibly ship: the document and page
#: identity are still there, the handwriting column is not.
REDUCED_SEARCH_DDL: Final = "CREATE VIRTUAL TABLE search USING fts5(entryId, pageId, digitalText)"

_INSERT_ROW: Final = (
    "INSERT INTO search"
    " (entryId, pageId, handwrittenText, authorMap, wordStrokes, digitalText, title, tags, type)"
    " VALUES (?, ?, ?, '', '', '', NULL, '', 1)"
)

type _Row = tuple[str | None, str | None, str | None]


def an_index_image(
    rows: Sequence[_Row] = INDEXED_ROWS,
    *,
    search_ddl: str = MEASURED_SEARCH_DDL,
    version: int | None = MEASURED_SCHEMA_VERSION,
    generation: int | None = MEASURED_GENERATION,
    with_version_table: bool = True,
    with_generation_table: bool = True,
) -> bytes:
    """Build a real database image with the measured schema and serialize it.

    Parameters
    ----------
    rows
        ``(page_ref, entry_ref, text)`` per row, inserted verbatim -- so ``None`` is a
        NULL column and two rows may share a ``pageId``.
    search_ddl
        The ``search`` table definition, so a missing column can be expressed as the
        firmware change it would be.
    version
        The ``version`` table's single value, or ``None`` to leave the table empty.
    generation
        The ``generation`` table's single value, or ``None`` to leave it empty.
    with_version_table
        Whether to create the ``version`` table at all.
    with_generation_table
        Whether to create the ``generation`` table at all.

    Returns
    -------
    bytes
        The whole image, exactly as the device's file would arrive.
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute(search_ddl)
        if with_version_table:
            conn.execute("CREATE TABLE version (id INTEGER PRIMARY KEY, value INTEGER)")
        if with_generation_table:
            conn.execute("CREATE TABLE generation (id INTEGER PRIMARY KEY, value INTEGER)")
        if len(rows) > 0:
            conn.executemany(
                _INSERT_ROW,
                [(entry_ref, page_ref, text) for page_ref, entry_ref, text in rows],
            )
        if with_version_table and version is not None:
            conn.execute("INSERT INTO version (id, value) VALUES (42, ?)", (version,))
        if with_generation_table and generation is not None:
            conn.execute("INSERT INTO generation (id, value) VALUES (42, ?)", (generation,))
        return conn.serialize()
    finally:
        conn.close()


def a_corpus_sized_image() -> bytes:
    """Return an image with the measured row count, so truncation lands predictably.

    Returns
    -------
    bytes
        92 rows over 23 documents, the shape of the attached tablet's index, with
        :data:`INDEXED_ROWS` among them so the shared assertions still apply.
    """
    filler: list[_Row] = [
        (f"page-{index:02d}", f"doc-{index // 4}", f"a page of ink, number {index}")
        for index in range(89)
    ]
    return an_index_image([*INDEXED_ROWS, *filler])


class _CountingSource:
    """A :class:`SearchIndexSource` that counts reads and can be broken.

    The counter is the only way to assert the memoization: through the port, an image
    read once and an image read ninety-two times answer identically.
    """

    def __init__(self, image: bytes | None) -> None:
        self.image = image
        self.reads = 0
        self.failure: DeviceError | None = None

    def read_index(self) -> bytes | None:
        """Return the image, or raise the seeded transport failure.

        Returns
        -------
        bytes | None
            Whatever this source currently holds.

        Raises
        ------
        DeviceError
            A failure was seeded on :attr:`failure`.
        """
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return self.image


def handwritten_text_indexes(
    source: SearchIndexSource,
) -> tuple[HandwrittenTextIndex, HandwrittenTextIndex]:
    """Return every ``HandwrittenTextIndex`` binding.

    Parameters
    ----------
    source
        The transport half of the capability.

    Returns
    -------
    tuple[HandwrittenTextIndex, HandwrittenTextIndex]
        The reader and the double. The annotation is the check: no port here is
        ``runtime_checkable``, so returning both through this signature is what makes
        ``ty`` notice a renamed method or a parameter that stopped being positional.
    """
    return (DeviceSearchIndex(source), FakeHandwrittenTextIndex())


def test_every_binding_is_constructible_and_typed_as_its_port() -> None:
    assert len(handwritten_text_indexes(_CountingSource(None))) == 2


# ── the shared contract, bound twice ─────────────────────────────────────────


class TestDeviceSearchIndex(HandwrittenTextIndexContract):
    """The tier-0 contract over a real serialized database image."""

    @pytest.fixture
    def index(self) -> HandwrittenTextIndex:
        """Return the reader over a corpus-sized image holding the shared rows.

        Corpus-sized rather than three rows so :meth:`break_index` tears a byte off a
        database with real interior pages, which is what a hot copy of the device's
        own file is.

        Returns
        -------
        HandwrittenTextIndex
            The reader, which ``ty`` checks against the Protocol here.
        """
        return DeviceSearchIndex(_CountingSource(a_corpus_sized_image()))

    def break_index(self, index: HandwrittenTextIndex) -> None:
        """Tear the last byte off the image the source will hand over.

        Parameters
        ----------
        index
            The subject, whose source has not been read yet.
        """
        assert isinstance(index, DeviceSearchIndex)
        source = index._source
        assert isinstance(source, _CountingSource)
        assert source.image is not None
        source.image = source.image[:-1]


class TestFakeHandwrittenTextIndex(HandwrittenTextIndexContract):
    """The tier-0 contract over the shipped double."""

    @pytest.fixture
    def index(self) -> HandwrittenTextIndex:
        """Return the double, seeded with the shared corpus.

        Returns
        -------
        HandwrittenTextIndex
            The double.
        """
        double = FakeHandwrittenTextIndex(generation=MEASURED_GENERATION)
        for page_ref, entry_ref, text in INDEXED_ROWS:
            double.seed(page_ref, entry_ref=entry_ref, text=text)
        return double

    def break_index(self, index: HandwrittenTextIndex) -> None:
        """Set the double's fault flag.

        Parameters
        ----------
        index
            The subject.
        """
        assert isinstance(index, FakeHandwrittenTextIndex)
        index.fail_reads = True


# ── identity ─────────────────────────────────────────────────────────────────


def test_the_provider_id_names_the_default_revision() -> None:
    assert DeviceSearchIndex(_CountingSource(None)).provider_id == "device-index@1"


def test_a_bumped_revision_is_a_different_provider_id() -> None:
    # The app folds this slug into the cache key, so this is the whole mechanism by
    # which a change to what this reader reads invalidates rows written before it.
    source = _CountingSource(None)
    assert DeviceSearchIndex(source, revision="2").provider_id == "device-index@2"
    assert (
        DeviceSearchIndex(source, revision="2").provider_id
        != DeviceSearchIndex(source).provider_id
    )


# ── memoization ──────────────────────────────────────────────────────────────


def test_the_image_is_read_once_however_many_pages_are_looked_up() -> None:
    # 92 pages against a 503 KB file over SSH is the shape this prevents.
    source = _CountingSource(an_index_image())
    index = DeviceSearchIndex(source)
    for page_ref in ("page-ink", "page-more-ink", "page-blank", "page-unindexed", "page-ink"):
        index.lookup(page_ref)
    assert source.reads == 1


def test_a_device_with_no_index_is_asked_once_and_answers_none_every_time() -> None:
    # The memoized `None` specifically: without it, "this device has no index" costs
    # one round trip per page for a question already answered.
    source = _CountingSource(None)
    index = DeviceSearchIndex(source)
    assert [index.lookup(f"page-{index_number}") for index_number in range(5)] == [None] * 5
    assert source.reads == 1


def test_a_dead_transport_is_a_store_fault_reported_from_a_single_read() -> None:
    # A DeviceError crossing this port would put a transport this package cannot even
    # import into an OCR caller's `except` clause, so it is translated -- and the
    # failure is memoized, or a dead cable costs 92 timeouts instead of one.
    source = _CountingSource(None)
    source.failure = DeviceUnreachable(
        transport=TransportKind.SSH,
        endpoint="rm-host",
        detail="cable unplugged",
    )
    index = DeviceSearchIndex(source)
    for _ in range(3):
        with pytest.raises(StoreUnavailableError) as caught:
            index.lookup("page-ink")
        assert type(caught.value) is StoreUnavailableError
        assert caught.value.store == STORE
        assert "cable unplugged" in caught.value.detail
    assert source.reads == 1


def test_many_lookups_leak_no_connection() -> None:
    # Each lookup opens its own in-memory database because the port declares no
    # `close`. An unclosed sqlite3.Connection is a ResourceWarning at collection
    # time, and this workspace turns that into a failure on whichever test happens to
    # be running -- so the closing is asserted, not assumed.
    index = DeviceSearchIndex(_CountingSource(an_index_image()))
    for _ in range(20):
        index.lookup("page-ink")
    gc.collect()


# ── unusable images ──────────────────────────────────────────────────────────


def test_an_empty_image_is_a_store_fault_rather_than_a_crash() -> None:
    # `deserialize(b"")` raises MemoryError, not sqlite3.DatabaseError. A reader that
    # caught only the driver's own error type dies here, taking the command with it.
    with pytest.raises(StoreUnavailableError) as caught:
        DeviceSearchIndex(_CountingSource(b"")).lookup("page-ink")
    assert type(caught.value) is StoreUnavailableError
    assert caught.value.store == STORE
    assert "0-byte" in caught.value.detail


def test_garbage_bytes_are_a_store_fault() -> None:
    # `deserialize` accepts these happily; it is the first statement that objects.
    with pytest.raises(StoreUnavailableError) as caught:
        DeviceSearchIndex(_CountingSource(b"this is not a database" * 64)).lookup("page-ink")
    assert type(caught.value) is StoreUnavailableError
    assert "not a database" in caught.value.detail


def test_an_image_truncated_by_a_whole_page_is_a_store_fault() -> None:
    image = a_corpus_sized_image()
    with pytest.raises(StoreUnavailableError) as caught:
        DeviceSearchIndex(_CountingSource(image[: len(image) - 4096])).lookup("page-ink")
    assert type(caught.value) is StoreUnavailableError


def test_an_image_truncated_by_one_byte_is_refused_although_it_still_answers() -> None:
    # The reason `PRAGMA quick_check` is mandatory rather than defensive. The hazard
    # is demonstrated here, not quoted: a torn copy of the index xochitl is writing
    # opens, answers a bound query with a row, and reports nothing wrong.
    torn = a_corpus_sized_image()[:-1]
    raw = sqlite3.connect(":memory:", isolation_level=None)
    try:
        raw.deserialize(torn)
        answered = raw.execute(
            "SELECT entryId FROM search WHERE pageId = ?",
            ("page-ink",),
        ).fetchall()
        verdict = raw.execute("PRAGMA quick_check").fetchall()
    finally:
        raw.close()
    assert answered == [("doc-a",)]
    assert verdict != [("ok",)]

    with pytest.raises(StoreUnavailableError) as caught:
        DeviceSearchIndex(_CountingSource(torn)).lookup("page-ink")
    assert type(caught.value) is StoreUnavailableError
    assert "integrity check" in caught.value.detail


# ── schema gating ────────────────────────────────────────────────────────────


def test_a_missing_handwriting_column_is_a_schema_mismatch_carrying_both_numbers() -> None:
    image = an_index_image((), search_ddl=REDUCED_SEARCH_DDL, version=11)
    with pytest.raises(StoreSchemaMismatchError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-ink")
    assert caught.value.store == STORE
    assert caught.value.found == 11
    assert caught.value.expected == MEASURED_SCHEMA_VERSION


def test_a_renumbered_schema_that_keeps_the_columns_still_reads() -> None:
    # The gate is the columns, not the integer. A firmware bump that renumbers the
    # schema and keeps entryId, pageId and handwrittenText must keep working, or tier
    # 0 goes dark on the next update for no reason.
    found = DeviceSearchIndex(_CountingSource(an_index_image(version=10_000))).lookup("page-ink")
    assert found is not None
    assert found.text == "the first page of ink"


def test_a_missing_version_table_still_reports_a_mismatch() -> None:
    # The mismatch is about the columns, so composing its message must not be able to
    # fail on an image that is missing the version table too.
    image = an_index_image((), search_ddl=REDUCED_SEARCH_DDL, with_version_table=False)
    with pytest.raises(StoreSchemaMismatchError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-ink")
    assert caught.value.found == 0


def test_an_empty_version_table_still_reports_a_mismatch() -> None:
    image = an_index_image((), search_ddl=REDUCED_SEARCH_DDL, version=None)
    with pytest.raises(StoreSchemaMismatchError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-ink")
    assert caught.value.found == 0


def test_a_missing_generation_table_is_a_schema_mismatch() -> None:
    # IndexedHandwriting requires a generation and there is no honest value to invent
    # for one, so the counter's column is gated exactly like the handwriting column.
    image = an_index_image(with_generation_table=False)
    with pytest.raises(StoreSchemaMismatchError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-ink")
    assert caught.value.found == MEASURED_SCHEMA_VERSION


# ── rows ─────────────────────────────────────────────────────────────────────


def test_the_generation_counter_arrives_as_the_int64_the_device_holds() -> None:
    found = DeviceSearchIndex(_CountingSource(an_index_image())).lookup("page-ink")
    assert found is not None
    assert found.generation == MEASURED_GENERATION


def test_a_null_reading_is_the_empty_string() -> None:
    # Never observed on the measured device, representable in FTS5 all the same.
    # Crashing on it would be a worse answer than the one the genuinely blank rows
    # already carry.
    image = an_index_image([("page-null", "doc-a", None)])
    found = DeviceSearchIndex(_CountingSource(image)).lookup("page-null")
    assert found is not None
    assert found.text == ""
    assert found.entry_ref == "doc-a"


def test_a_null_entry_id_makes_the_record_unreadable() -> None:
    # There is no empty document to attribute a page to, so this is a row that cannot
    # be read rather than a row with a default.
    image = an_index_image([("page-orphan", None, "ink with no document")])
    with pytest.raises(StoredRecordUnreadableError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-orphan")
    assert caught.value.store == STORE
    assert caught.value.table == "search"
    assert caught.value.key == "page-orphan"


def test_a_generation_table_with_no_row_makes_the_record_unreadable() -> None:
    image = an_index_image(generation=None)
    with pytest.raises(StoredRecordUnreadableError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-ink")
    assert caught.value.key == "page-ink"


def test_two_rows_for_one_page_are_refused_rather_than_arbitrated() -> None:
    # Picking a winner is how one page's handwriting becomes another's: a duplicated
    # page uuid means this reader's identity assumption is wrong, and the reading it
    # would return is exactly as likely to be the other page's.
    rows: list[_Row] = [*INDEXED_ROWS, ("page-ink", "doc-z", "a contradictory reading")]
    image = an_index_image(rows)
    with pytest.raises(StoredRecordUnreadableError) as caught:
        DeviceSearchIndex(_CountingSource(image)).lookup("page-ink")
    assert caught.value.key == "page-ink"
    assert "2 rows" in caught.value.detail


def test_a_duplicated_page_does_not_poison_the_other_pages() -> None:
    rows: list[_Row] = [*INDEXED_ROWS, ("page-ink", "doc-z", "a contradictory reading")]
    found = DeviceSearchIndex(_CountingSource(an_index_image(rows))).lookup("page-blank")
    assert found is not None
    assert found.text == ""


def test_the_page_ref_is_bound_never_interpolated() -> None:
    # A pageId is a string this package received and did not parse. Interpolated into
    # the statement instead of bound, this one would either match every row or fail
    # to compile; bound, it simply matches nothing.
    index = DeviceSearchIndex(_CountingSource(an_index_image()))
    assert index.lookup("page-ink' OR '1'='1") is None
    assert index.lookup("page-ink") is not None


# ── the double's own seams ───────────────────────────────────────────────────


def test_the_double_counts_its_lookups() -> None:
    double = FakeHandwrittenTextIndex()
    double.seed("page-ink", entry_ref="doc-a", text="ink")
    double.lookup("page-ink")
    double.lookup("page-unindexed")
    double.fail_reads = True
    with pytest.raises(StoreUnavailableError) as caught:
        double.lookup("page-ink")
    assert caught.value.detail.endswith("integrity check")
    assert double.lookup_calls == 3


def test_the_double_refuses_a_duplicated_page_the_way_the_reader_does() -> None:
    double = FakeHandwrittenTextIndex()
    double.seed("page-ink", entry_ref="doc-a", text="ink")
    double.seed_duplicated("page-ink")
    with pytest.raises(StoredRecordUnreadableError) as caught:
        double.lookup("page-ink")
    assert caught.value.table == "search"
    assert caught.value.key == "page-ink"


def test_the_double_reports_the_revision_and_generation_it_was_given() -> None:
    double = FakeHandwrittenTextIndex(revision="7", generation=MEASURED_GENERATION)
    double.seed("page-ink", entry_ref="doc-a", text="ink")
    found = double.lookup("page-ink")
    assert double.provider_id == "device-index@7"
    assert found is not None
    assert found.generation == MEASURED_GENERATION
