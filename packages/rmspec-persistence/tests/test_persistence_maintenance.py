"""Retention, enumeration, and the two one-way imports.

The destructive methods are the point of this file. ``rmspec cache prune`` deletes
paid Textract and Bedrock output, so every one of them requires an explicit bound
and refuses a computed-empty selection -- asserted by signature inspection as well
as by behaviour, because a keyword default is exactly the kind of change nobody
notices in review.

``import_ocr_sidecars`` is the regression test for a function that could never have
worked: legacy ``migrate_ocr_sidecars`` derived ``{uuid}.ocr.rm`` from
``{uuid}.ocr.txt`` with ``Path.with_suffix`` and therefore always returned 0.
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from conftest import LEGACY_SCHEMA
from persistence_builders import (
    FROZEN_NOW,
    a_diagram_artifact,
    a_diagram_key,
    a_document,
    a_page,
    a_page_text,
    an_audit_entry,
    an_ocr_artifact,
    an_ocr_key,
)

from rmspec.domain.errors import StoreUnavailableError
from rmspec.domain.models import OcrCacheKey
from rmspec.persistence import (
    SqliteDiagramCache,
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    SqliteSyncAuditLog,
    StoreMaintenance,
)
from rmspec.persistence.derived import utc_key
from rmspec.persistence.maintenance import EMPTY_SCENE_DIGEST, SIDECAR_SUFFIX
from rmspec.persistence.testing import InMemoryDocumentSyncStore

if TYPE_CHECKING:
    from pathlib import Path

    from rmspec.persistence import SqliteDatabase


def test_counts_report_what_was_written(tmp_db: SqliteDatabase) -> None:
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(
        a_document("doc-a"),
        [a_page("doc-a", "page-1", 0), a_page("doc-a", "page-2", 1)],
    )
    store.record_page_text(a_page_text("doc-a", "page-1", 0))
    SqliteOcrCache(tmp_db).put(an_ocr_key(), an_ocr_artifact())
    SqliteDiagramCache(tmp_db).put(a_diagram_key(), a_diagram_artifact())
    SqliteSyncAuditLog(tmp_db).append(an_audit_entry())

    counts = StoreMaintenance(tmp_db).counts()

    assert counts.documents == 1
    assert counts.pages == 2
    assert counts.page_texts == 1
    assert counts.ocr_entries == 1
    assert counts.diagram_entries == 1
    assert counts.audit_entries == 1


def test_counts_of_an_empty_store_are_zero(tmp_db: SqliteDatabase) -> None:
    counts = StoreMaintenance(tmp_db).counts()
    assert (counts.documents, counts.pages, counts.ocr_entries) == (0, 0, 0)


@pytest.mark.parametrize("method", ["prune_ocr", "prune_diagram"])
def test_a_prune_bound_is_keyword_only_and_has_no_wipe_everything_default(
    method: str,
) -> None:
    # Signature-level, so a future default cannot quietly turn "no filter" into
    # "delete the lot".
    signature = inspect.signature(getattr(StoreMaintenance, method))
    parameters = {
        name: parameter for name, parameter in signature.parameters.items() if name != "self"
    }
    assert set(parameters) == {"older_than", "page_hashes"}
    for parameter in parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None


@pytest.mark.parametrize("method", ["prune_ocr", "prune_diagram"])
def test_pruning_without_a_bound_is_refused(tmp_db: SqliteDatabase, method: str) -> None:
    with pytest.raises(ValueError, match="needs an explicit bound"):
        getattr(StoreMaintenance(tmp_db), method)()


@pytest.mark.parametrize("method", ["prune_ocr", "prune_diagram"])
def test_pruning_with_an_empty_page_bound_is_refused(
    tmp_db: SqliteDatabase,
    method: str,
) -> None:
    # A computed-empty selection is the dangerous case: it means "nothing
    # matched", never "everything".
    with pytest.raises(ValueError, match="not 'everything'"):
        getattr(StoreMaintenance(tmp_db), method)(page_hashes=[])


@pytest.mark.parametrize("method", ["prune_ocr", "prune_diagram"])
def test_pruning_with_a_naive_cutoff_is_refused(tmp_db: SqliteDatabase, method: str) -> None:
    # The bound's presence was checked; its correctness was not. A naive datetime
    # goes through `astimezone` as the host's local wall clock, so this same call
    # deleted nothing under TZ=UTC and destroyed a 12:00Z entry under
    # TZ=America/Los_Angeles, where the cutoff silently became 19:59Z. A bound that
    # means a different instant on a different machine is not an explicit bound.
    cache = SqliteOcrCache(tmp_db)
    key = an_ocr_key("page-a")
    cache.put(key, an_ocr_artifact(created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))
    cutoff = datetime(2026, 1, 1, 11, 59, tzinfo=UTC)
    maintenance = StoreMaintenance(tmp_db)

    with pytest.raises(ValueError, match="needs an aware older_than"):
        getattr(maintenance, method)(older_than=cutoff.replace(tzinfo=None))

    # The same wall clock, said properly, deletes nothing -- which is what the
    # naive call meant everywhere except west of UTC.
    assert getattr(maintenance, method)(older_than=cutoff) == 0
    assert cache.get(key) is not None


def test_a_naive_moment_cannot_become_a_store_key() -> None:
    # Belt to the prune guard's braces: every future caller of the key function
    # inherits the refusal, not just prune.
    with pytest.raises(ValueError, match="needs an aware datetime"):
        utc_key(FROZEN_NOW.replace(tzinfo=None))
    assert utc_key(FROZEN_NOW).endswith("+00:00")


def test_pruning_by_page_hash_covers_every_page_with_that_content(
    tmp_db: SqliteDatabase,
) -> None:
    # `page_hashes` names content, not a page. Every zero-byte stub in the library
    # shares one hash, so one hash prunes them all -- across documents. The
    # docstring says so because the caller cannot otherwise know.
    cache = SqliteOcrCache(tmp_db)
    stub_of_doc_a = an_ocr_key(EMPTY_SCENE_DIGEST, variant="v1")
    stub_of_doc_b = an_ocr_key(EMPTY_SCENE_DIGEST, variant="v2")
    inked = an_ocr_key("page-hash-inked")
    for key in (stub_of_doc_a, stub_of_doc_b, inked):
        cache.put(key, an_ocr_artifact())

    assert StoreMaintenance(tmp_db).prune_ocr(page_hashes=[EMPTY_SCENE_DIGEST]) == 2
    assert cache.get(inked) is not None


def test_pruning_by_age_removes_only_older_entries(tmp_db: SqliteDatabase) -> None:
    cache = SqliteOcrCache(tmp_db)
    old = an_ocr_key("page-old")
    new = an_ocr_key("page-new")
    cache.put(old, an_ocr_artifact(created_at=FROZEN_NOW - timedelta(days=30)))
    cache.put(new, an_ocr_artifact(created_at=FROZEN_NOW))

    deleted = StoreMaintenance(tmp_db).prune_ocr(older_than=FROZEN_NOW - timedelta(days=1))

    assert deleted == 1
    assert cache.get(old) is None
    assert cache.get(new) is not None


def test_pruning_by_age_compares_across_time_zones(tmp_db: SqliteDatabase) -> None:
    # The reason created_at is normalised to UTC before it is stored: a cutoff
    # written in another offset must still mean the same instant.
    cache = SqliteOcrCache(tmp_db)
    key = an_ocr_key("page-a")
    cache.put(key, an_ocr_artifact(created_at=datetime(2026, 1, 1, 0, 30, tzinfo=UTC)))
    eastern = datetime(2025, 12, 31, 20, 0, tzinfo=UTC).astimezone(
        datetime.now(UTC).astimezone().tzinfo,
    )
    assert StoreMaintenance(tmp_db).prune_ocr(older_than=eastern) == 0
    assert cache.get(key) is not None


def test_pruning_by_page_removes_only_that_page(tmp_db: SqliteDatabase) -> None:
    cache = SqliteDiagramCache(tmp_db)
    doomed = a_diagram_key("page-doomed")
    kept = a_diagram_key("page-kept")
    cache.put(doomed, a_diagram_artifact())
    cache.put(kept, a_diagram_artifact())

    deleted = StoreMaintenance(tmp_db).prune_diagram(page_hashes=["page-doomed"])

    assert deleted == 1
    assert cache.get(doomed) is None
    assert cache.get(kept) is not None


def test_pruning_by_both_bounds_requires_both_to_match(tmp_db: SqliteDatabase) -> None:
    cache = SqliteOcrCache(tmp_db)
    key = an_ocr_key("page-a")
    cache.put(key, an_ocr_artifact(created_at=FROZEN_NOW))
    maintenance = StoreMaintenance(tmp_db)
    assert maintenance.prune_ocr(older_than=FROZEN_NOW, page_hashes=["page-b"]) == 0
    assert maintenance.prune_ocr(older_than=FROZEN_NOW, page_hashes=["page-a"]) == 0
    assert (
        maintenance.prune_ocr(
            older_than=FROZEN_NOW + timedelta(seconds=1),
            page_hashes=["page-a"],
        )
        == 1
    )


def test_enumeration_returns_keys_and_never_artifacts(tmp_db: SqliteDatabase) -> None:
    ocr = SqliteOcrCache(tmp_db)
    first = an_ocr_key("page-a", variant="v1")
    second = an_ocr_key("page-a", variant="v2")
    other = an_ocr_key("page-b")
    for key in (first, second, other):
        ocr.put(key, an_ocr_artifact())
    SqliteDiagramCache(tmp_db).put(a_diagram_key("page-a"), a_diagram_artifact())
    maintenance = StoreMaintenance(tmp_db)

    everything = maintenance.ocr_keys()
    for_page = maintenance.ocr_keys(page_hash="page-a")

    assert len(everything) == 3
    assert all(isinstance(key, OcrCacheKey) for key in everything)
    assert {key.digest for key in for_page} == {first.digest, second.digest}
    assert [key.digest for key in for_page] == sorted(key.digest for key in for_page)
    assert len(maintenance.diagram_keys(page_hash="page-a")) == 1
    assert maintenance.diagram_keys(page_hash="page-absent") == []


def test_trimming_the_audit_log_keeps_the_newest_and_leaves_a_gap(
    tmp_db: SqliteDatabase,
) -> None:
    log = SqliteSyncAuditLog(tmp_db)
    for _ in range(5):
        log.append(an_audit_entry())

    deleted = StoreMaintenance(tmp_db).trim_audit_log(keep=2)

    assert deleted == 3
    assert [recorded.sequence for recorded in log.recent(limit=10)] == [5, 4]


def test_trimming_never_resets_the_sequence_counter(tmp_db: SqliteDatabase) -> None:
    # The exact failure a rowid-allocated sequence produces. The counter row is
    # separate from the entries precisely so retention cannot cause reuse.
    log = SqliteSyncAuditLog(tmp_db)
    for _ in range(4):
        log.append(an_audit_entry())
    StoreMaintenance(tmp_db).trim_audit_log(keep=1)
    assert tmp_db.primary.query("SELECT next_sequence FROM audit_counter") == [(5,)]
    assert log.append(an_audit_entry()).sequence == 5


def test_trimming_to_nothing_is_refused(tmp_db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        StoreMaintenance(tmp_db).trim_audit_log(keep=0)


def test_trimming_more_than_the_history_deletes_nothing(tmp_db: SqliteDatabase) -> None:
    log = SqliteSyncAuditLog(tmp_db)
    log.append(an_audit_entry())
    assert StoreMaintenance(tmp_db).trim_audit_log(keep=10) == 0


def test_vacuum_preserves_the_contents(tmp_db: SqliteDatabase) -> None:
    store = SqliteDocumentSyncStore(tmp_db)
    document = a_document("doc-a")
    store.record_document(document, [a_page("doc-a", "page-1", 0)])
    StoreMaintenance(tmp_db).vacuum()
    assert store.get_document("doc-a") == document


def _sidecar_tree(root: Path) -> None:
    """Write a xochitl-shaped tree with three sidecars.

    Parameters
    ----------
    root
        Directory to build the tree under.
    """
    doc = root / "doc-a"
    doc.mkdir(parents=True)
    (doc / f"page-1{SIDECAR_SUFFIX}").write_text("page one transcription", encoding="utf-8")
    (doc / f"page-2{SIDECAR_SUFFIX}").write_text("   \n  ", encoding="utf-8")
    (doc / f"page-absent{SIDECAR_SUFFIX}").write_text("orphan text", encoding="utf-8")


def test_sidecars_are_imported_for_recorded_pages_only(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
) -> None:
    xochitl = tmp_path / "xochitl"
    _sidecar_tree(xochitl)
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(
        a_document("doc-a"),
        [a_page("doc-a", "page-1", 0), a_page("doc-a", "page-2", 1)],
    )

    imported = StoreMaintenance(tmp_db).import_ocr_sidecars(xochitl, store)

    # One import: the blank sidecar is a page nobody transcribed, and the orphan
    # names a page the mirror does not have.
    assert imported == 1
    texts = store.page_texts("doc-a")
    assert [(text.page_uuid, text.text) for text in texts] == [
        ("page-1", "page one transcription"),
    ]
    # Legacy sidecars name no model, which is the honest provenance and also the
    # only one a blank text could legally carry.
    assert texts[0].provenance.model_fingerprint is None
    assert texts[0].provenance.recognizers == ()
    assert texts[0].page_index == 0


def test_importing_sidecars_is_idempotent(tmp_db: SqliteDatabase, tmp_path: Path) -> None:
    xochitl = tmp_path / "xochitl"
    _sidecar_tree(xochitl)
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
    maintenance = StoreMaintenance(tmp_db)
    assert maintenance.import_ocr_sidecars(xochitl, store) == 1
    assert maintenance.import_ocr_sidecars(xochitl, store) == 1
    assert len(store.page_texts("doc-a")) == 1


def test_importing_sidecars_writes_through_the_port_so_a_double_works(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
) -> None:
    # The importer takes a DocumentSyncStore, not a connection, which is what
    # makes it testable without a file and what validates each import once.
    xochitl = tmp_path / "xochitl"
    _sidecar_tree(xochitl)
    store = InMemoryDocumentSyncStore()
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
    assert StoreMaintenance(tmp_db).import_ocr_sidecars(xochitl, store) == 1


def test_importing_from_a_missing_directory_imports_nothing(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
) -> None:
    store = InMemoryDocumentSyncStore()
    assert StoreMaintenance(tmp_db).import_ocr_sidecars(tmp_path / "absent", store) == 0


def test_an_undecodable_sidecar_is_skipped(tmp_db: SqliteDatabase, tmp_path: Path) -> None:
    xochitl = tmp_path / "xochitl"
    doc = xochitl / "doc-a"
    doc.mkdir(parents=True)
    (doc / f"page-1{SIDECAR_SUFFIX}").write_bytes(b"\xff\xfe not utf-8")
    store = InMemoryDocumentSyncStore()
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
    assert StoreMaintenance(tmp_db).import_ocr_sidecars(xochitl, store) == 0
    assert store.page_texts("doc-a") == []


def test_legacy_ocr_text_is_rescued_before_the_file_is_deleted(
    tmp_db: SqliteDatabase,
    legacy_db: Path,
) -> None:
    # The legacy caches are unmigratable -- a digest folds render, raster and
    # request digests no legacy row recorded -- but the *text* is rescuable, and it
    # is paid output. `pages.rm_hash` joins `ocr_cache.rm_hash` into a valid
    # PageText.
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(
        a_document("doc-a", page_count=3),
        [
            a_page("doc-a", "page-1", 0),
            a_page("doc-a", "page-2", 1),
            a_page("doc-a", "page-3", 2),
        ],
    )

    rescued = StoreMaintenance(tmp_db).rescue_legacy_page_texts(
        legacy_db,
        store,
        extracted_at=FROZEN_NOW,
    )

    # Two: page 3's legacy text is whitespace, and page 1 has two engine rows that
    # resolve to one page.
    assert rescued == 2
    assert [(text.page_uuid, text.text) for text in store.page_texts("doc-a")] == [
        ("page-1", "page one text"),
        ("page-2", "page two text"),
    ]


def test_a_rescue_drops_text_for_pages_the_new_mirror_lacks(
    tmp_db: SqliteDatabase,
    legacy_db: Path,
) -> None:
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-2", 0)])
    StoreMaintenance(tmp_db).rescue_legacy_page_texts(legacy_db, store, extracted_at=FROZEN_NOW)
    assert [text.page_uuid for text in store.page_texts("doc-a")] == ["page-2"]


def test_a_rescue_from_a_missing_file_is_reported(tmp_db: SqliteDatabase, tmp_path: Path) -> None:
    store = InMemoryDocumentSyncStore()
    with pytest.raises(StoreUnavailableError):
        StoreMaintenance(tmp_db).rescue_legacy_page_texts(
            tmp_path / "absent.db",
            store,
            extracted_at=FROZEN_NOW,
        )


def test_a_rescue_skips_a_legacy_row_the_mirror_does_not_recognise(
    tmp_db: SqliteDatabase,
    legacy_db: Path,
) -> None:
    # A legacy row with an empty page uuid could not form a PageText at all
    # (min_length=1), and it is skipped before that can matter because the mirror
    # has no such page -- which is the difference between this and the bare
    # `except Exception: continue` it replaces.
    #
    # The added page gets a hash of its own. Reusing page-1's would make that hash
    # name two pages, and an ambiguous hash is now declined before the mirror is
    # ever consulted, which would test the wrong refusal.
    conn = sqlite3.connect(legacy_db, isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO pages (page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes) "
            "VALUES ('', ?, 9, ?, 512)",
            ("doc-a", "d" * 64),
        )
        conn.execute(
            "INSERT INTO ocr_cache (rm_hash, engine, ocr_text, render_dpi, created_at) "
            "VALUES (?, 'merged', 'text of a page with no uuid', 300, '2025-06-01 09:00:00')",
            ("d" * 64,),
        )
    finally:
        conn.close()
    store = InMemoryDocumentSyncStore()
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
    assert (
        StoreMaintenance(tmp_db).rescue_legacy_page_texts(
            legacy_db,
            store,
            extracted_at=FROZEN_NOW,
        )
        == 1
    )


#: The reference corpus's shape, from one device's backup: 92 ``.rm`` files, 62 of
#: them zero bytes. Split across two documents here because the caches are not
#: document-scoped and the fan-out crossed that boundary.
CORPUS_PAGES = 92
CORPUS_STUBS = 62
DOC_B_STUBS = 31

#: The one legacy cache row for the shared zero-byte digest. Real text, from
#: whichever stub was transcribed last -- legacy keyed the cache by ``hash_file`` of
#: the ``.rm`` while rendering the PDF page behind it, so two stubs of one PDF
#: legitimately have different recognizable content under one identical key.
STUB_TEXT = "CHAPTER 4 - printed text of one particular PDF page"

INKED_TEXT = "handwriting from an annotated page"


def _corpus_shaped_pages() -> list[tuple[str, str, int, str, int]]:
    """Return 92 legacy page rows, 62 of them zero-byte stubs across two documents.

    Returns
    -------
    list[tuple[str, str, int, str, int]]
        ``(page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes)`` rows. Every
        inked page has a hash of its own; every stub carries the shared digest.
    """
    inked = CORPUS_PAGES - CORPUS_STUBS
    rows = [(f"page-inked-{i}", "doc-a", i, f"{i:064x}", 2048) for i in range(inked)]
    rows += [
        (f"page-stub-{i}", "doc-a", inked + i, EMPTY_SCENE_DIGEST, 0)
        for i in range(CORPUS_STUBS - DOC_B_STUBS)
    ]
    rows += [(f"page-stub-b-{i}", "doc-b", i, EMPTY_SCENE_DIGEST, 0) for i in range(DOC_B_STUBS)]
    return rows


def _stub_legacy_db(path: Path) -> None:
    """Write a legacy database shaped like the reference corpus's PDF-backed docs.

    Two documents' unannotated pages are zero-byte ``.rm`` stubs, so legacy's
    ``hash_file`` gave every one of them the same digest, and one ``ocr_cache`` row
    for that digest joins all 62 of them.

    Parameters
    ----------
    path
        The legacy database file to create.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany(
            "INSERT INTO documents (doc_uuid, visible_name, page_count) VALUES (?, ?, ?)",
            [("doc-a", "Annotated PDF", CORPUS_PAGES - DOC_B_STUBS), ("doc-b", "Another", 31)],
        )
        conn.executemany(
            "INSERT INTO pages (page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            _corpus_shaped_pages(),
        )
        conn.executemany(
            "INSERT INTO ocr_cache (rm_hash, engine, ocr_text, render_dpi, created_at) "
            "VALUES (?, 'merged', ?, 300, '2025-06-01 09:00')",
            [(f"{0:064x}", INKED_TEXT), (EMPTY_SCENE_DIGEST, STUB_TEXT)],
        )
    finally:
        conn.close()


def test_a_rescue_refuses_to_fan_one_stub_text_across_every_stub_page(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
) -> None:
    # The corpus finding turned destructive. Legacy hashed the .rm file, 62 of 92
    # are zero bytes, so one cache row joins every stub page in the library and the
    # (doc_uuid, page_uuid) dedup cannot see it -- each match is a different page.
    # Unrescued, one row became 62 confident, durable, wrong PageText records
    # spanning documents, and a PageText cannot be evicted the way a cache entry can.
    legacy = tmp_path / "legacy.db"
    _stub_legacy_db(legacy)
    legacy_pages = _corpus_shaped_pages()
    store = SqliteDocumentSyncStore(tmp_db)
    for doc_uuid in ("doc-a", "doc-b"):
        store.record_document(
            a_document(doc_uuid),
            [
                a_page(doc_uuid, page_uuid, page_index, rm_hash=rm_hash, rm_size_bytes=size)
                for page_uuid, owner, page_index, rm_hash, size in legacy_pages
                if owner == doc_uuid
            ],
        )

    rescued = StoreMaintenance(tmp_db).rescue_legacy_page_texts(
        legacy,
        store,
        extracted_at=FROZEN_NOW,
    )

    # One: the inked page whose hash names exactly one page. Not 62.
    assert rescued == 1
    assert [(text.page_uuid, text.text) for text in store.page_texts("doc-a")] == [
        ("page-inked-0", INKED_TEXT),
    ]
    # The other document was never transcribed and must not acquire doc-a's text.
    assert store.page_texts("doc-b") == []
    assert STUB_TEXT not in {text.text for text in store.all_page_texts()}


def test_a_rescue_declines_an_ambiguous_hash_and_says_so(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Cardinality, not ink, is the rule: two *inked* pages that happen to share a
    # hash are equally unattributable. Silence would be the whole defect -- the user
    # deletes the legacy file believing the rescue took everything.
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy, isolation_level=None)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO documents (doc_uuid, visible_name, page_count) VALUES ('doc-a', 'N', 2)",
        )
        conn.executemany(
            "INSERT INTO pages (page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes) "
            "VALUES (?, 'doc-a', ?, ?, 1024)",
            [("page-1", 0, "e" * 64), ("page-2", 1, "e" * 64)],
        )
        conn.execute(
            "INSERT INTO ocr_cache (rm_hash, engine, ocr_text, render_dpi, created_at) "
            "VALUES (?, 'merged', 'which page is this?', 300, '2025-06-01 09:00')",
            ("e" * 64,),
        )
    finally:
        conn.close()
    store = InMemoryDocumentSyncStore()
    store.record_document(
        a_document("doc-a", page_count=2),
        [a_page("doc-a", "page-1", 0), a_page("doc-a", "page-2", 1)],
    )

    with caplog.at_level(logging.WARNING, logger="rmspec.persistence.maintenance"):
        rescued = StoreMaintenance(tmp_db).rescue_legacy_page_texts(
            legacy,
            store,
            extracted_at=FROZEN_NOW,
        )

    assert rescued == 0
    assert store.page_texts("doc-a") == []
    assert f"hash {'e' * 64} was left behind -- 2 pages share that hash" in caplog.text


def test_a_rescue_skips_blank_legacy_text_on_an_otherwise_rescuable_page(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
) -> None:
    # An inked page whose hash names only itself, and whose legacy transcription is
    # whitespace. Nothing to rescue, and nothing to complain about either: the
    # legacy sidecar importer wrote a row for a page it found no text on.
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy, isolation_level=None)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO documents (doc_uuid, visible_name, page_count) VALUES ('doc-a', 'N', 1)",
        )
        conn.execute(
            "INSERT INTO pages (page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes) "
            "VALUES ('page-1', 'doc-a', 0, ?, 2048)",
            ("f" * 64,),
        )
        conn.execute(
            "INSERT INTO ocr_cache (rm_hash, engine, ocr_text, render_dpi, created_at) "
            "VALUES (?, 'merged', '   \n ', 300, '2025-06-01 09:00')",
            ("f" * 64,),
        )
    finally:
        conn.close()
    store = InMemoryDocumentSyncStore()
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])

    rescued = StoreMaintenance(tmp_db).rescue_legacy_page_texts(
        legacy,
        store,
        extracted_at=FROZEN_NOW,
    )

    assert rescued == 0
    assert store.page_texts("doc-a") == []


def test_a_rescue_reads_a_legacy_path_containing_a_question_mark(
    tmp_db: SqliteDatabase,
    tmp_path: Path,
) -> None:
    # `f"file:{path}?mode=ro"` ended the path at the `?`: SQLite opened `.../we`,
    # created it, ignored mode=ro, and the rescue reported "no such table: pages" --
    # telling the user their legacy file was not a database, just before they delete
    # the real one. Path.as_uri percent-encodes, so the path survives intact.
    legacy = tmp_path / "we?ird.db"
    _stub_legacy_db(legacy)
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-inked-0", 0)])

    rescued = StoreMaintenance(tmp_db).rescue_legacy_page_texts(
        legacy,
        store,
        extracted_at=FROZEN_NOW,
    )

    assert rescued == 1
    assert [text.text for text in store.page_texts("doc-a")] == [INKED_TEXT]
    # And nothing was created beside it: the truncated path used to appear on disk.
    assert not (tmp_path / "we").exists()
