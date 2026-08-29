"""Every driver failure reaches the domain error tree, and nothing else escapes.

The legacy row mappers let a ``pydantic.ValidationError`` and a ``sqlite3.Row``
``IndexError`` escape to the CLI as-is, and the lazily-connecting ``conn`` property
let an ``OperationalError`` out of a property access. This file pins the
replacement: one ``rmspec.domain.errors`` type per failure mode, with its fields
populated, and the exact class asserted wherever a subclass exists.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from persistence_builders import (
    a_document,
    a_page,
    a_page_text,
    an_audit_entry,
    an_ocr_artifact,
    an_ocr_key,
)

from rmspec.domain.errors import (
    AuditWriteFailedError,
    StoredRecordUnreadableError,
    StoreUnavailableError,
)
from rmspec.domain.models import SyncedDocument
from rmspec.persistence import (
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    SqliteSyncAuditLog,
)
from rmspec.persistence import _sqlite as sqlite_module
from rmspec.persistence._sqlite import SqliteDatabase, loads, open_legacy_readonly

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_a_closed_connection_translates_every_connection_method(
    tmp_db: SqliteDatabase,
) -> None:
    conn = tmp_db.primary
    conn.close()
    calls: list[Callable[[], object]] = [
        lambda: conn.query("SELECT 1"),
        lambda: conn.query_one("SELECT 1"),
        lambda: conn.execute("SELECT 1"),
        lambda: conn.execute_many("SELECT ?", [("a",)]),
        conn.probe_write,
    ]
    for call in calls:
        with pytest.raises(StoreUnavailableError) as caught:
            call()
        assert type(caught.value) is StoreUnavailableError
        assert caught.value.store == "sync.db"


def test_a_closed_connection_translates_a_transaction_start(tmp_db: SqliteDatabase) -> None:
    conn = tmp_db.primary
    conn.close()
    opened = conn.transaction()
    with pytest.raises(StoreUnavailableError):
        opened.__enter__()


def _delete_then_fail(database: SqliteDatabase, *, commit_first: bool) -> None:
    """Run a doomed transaction body, optionally committing before it fails.

    Parameters
    ----------
    database
        The open handle.
    commit_first
        Whether the body ends its own transaction before raising, which is the
        case the guarded rollback exists for.

    Raises
    ------
    RuntimeError
        Always, from inside the transaction.
    """
    with database.primary.transaction():
        if commit_first:
            database.primary.execute("COMMIT")
        else:
            database.primary.execute("DELETE FROM document")
        msg = "deliberate"
        raise RuntimeError(msg)


def test_a_failure_inside_a_transaction_rolls_back_and_propagates(
    tmp_db: SqliteDatabase,
) -> None:
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
    with pytest.raises(RuntimeError, match="deliberate"):
        _delete_then_fail(tmp_db, commit_first=False)
    # Rolled back, and no transaction was left holding the write lock.
    assert store.get_document("doc-a") is not None
    assert tmp_db.primary.in_transaction is False


def test_a_corrupt_payload_names_the_store_the_table_and_the_key(
    tmp_db: SqliteDatabase,
) -> None:
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
    tmp_db.primary.execute(
        "UPDATE document SET payload = ? WHERE uuid = ?",
        ("{not json", "doc-a"),
    )
    with pytest.raises(StoredRecordUnreadableError) as caught:
        store.get_document("doc-a")
    assert caught.value.store == "sync.db"
    assert caught.value.table == "document"
    assert caught.value.key == "doc-a"
    assert caught.value.detail


def test_a_payload_that_is_not_text_is_reported_rather_than_raising_a_builtin() -> None:
    # The branch the legacy mappers reached with a bare TypeError. `loads` is the
    # single row-to-model boundary, so this is the only place it can happen.
    with pytest.raises(StoredRecordUnreadableError, match="int, not text"):
        loads(SyncedDocument, 42, store="sync.db", table="document", key="doc-a")


def test_a_dropped_column_is_a_store_fault_not_an_unreadable_row(
    tmp_db: SqliteDatabase,
) -> None:
    # A column that is gone is a schema problem, so the adapter must not dress it
    # up as one bad row -- and it must certainly not surface as the bare
    # IndexError that name-indexing a sqlite3.Row would raise.
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(a_document("doc-a"), [])
    tmp_db.primary.execute("ALTER TABLE document DROP COLUMN payload")
    with pytest.raises(StoreUnavailableError) as caught:
        store.get_document("doc-a")
    assert type(caught.value) is StoreUnavailableError


def test_a_corrupt_page_text_payload_is_left_alone_by_a_re_record(
    tmp_db: SqliteDatabase,
) -> None:
    # record_document declares only StoreUnavailableError, so re-indexing a
    # payload it cannot read must not start raising StoredRecordUnreadableError.
    # The sort column is corrected; the payload is left for its reader to
    # complain about.
    store = SqliteDocumentSyncStore(tmp_db)
    document = a_document("doc-a")
    store.record_document(document, [a_page("doc-a", "page-1", 3)])
    store.record_page_text(a_page_text("doc-a", "page-1", 3))
    tmp_db.primary.execute("UPDATE page_text SET payload = ?", ("{not json",))

    store.record_document(document, [a_page("doc-a", "page-1", 0)])

    assert tmp_db.primary.query("SELECT page_index FROM page_text") == [(0,)]
    with pytest.raises(StoredRecordUnreadableError):
        store.page_texts("doc-a")


def test_an_append_against_a_dead_log_is_an_audit_failure_only(
    tmp_db: SqliteDatabase,
) -> None:
    log = SqliteSyncAuditLog(tmp_db)
    log._conn.close()
    with pytest.raises(AuditWriteFailedError) as caught:
        log.append(an_audit_entry())
    # Not a StoreUnavailableError: the caller degrades on this one rather than
    # failing the operation the entry describes.
    assert not isinstance(caught.value, StoreUnavailableError)
    assert "sync.db" in caught.value.message


def test_a_read_of_a_dead_log_is_a_store_fault(tmp_db: SqliteDatabase) -> None:
    log = SqliteSyncAuditLog(tmp_db)
    log._conn.close()
    with pytest.raises(StoreUnavailableError) as caught:
        log.recent(limit=5)
    assert type(caught.value) is StoreUnavailableError


def test_lock_contention_fails_the_append_while_readers_keep_working(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two connections on one WAL file was unreachable in the legacy store, which
    # had exactly one. The busy timeout is shortened so the test is fast; the
    # pragma is still verified against the patched value, so the connection and
    # the constant cannot disagree.
    monkeypatch.setattr(sqlite_module, "_BUSY_TIMEOUT_MS", 25)
    database = SqliteDatabase.open(tmp_path / "sync.db")
    try:
        store = SqliteDocumentSyncStore(database)
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        log = SqliteSyncAuditLog(database)
        reader = database.connect()
        with database.primary.transaction():
            with pytest.raises(AuditWriteFailedError):
                log.append(an_audit_entry())
            # WAL readers do not block behind a writer, so the mirror stays
            # readable from another connection while the write lock is held.
            assert reader.query_one("SELECT count(*) FROM document") == (1,)
        # The lock went away with the transaction, so the next append lands.
        assert log.append(an_audit_entry()).sequence == 1
    finally:
        database.close()


def test_a_cache_swallows_a_dead_connection_but_the_store_does_not(
    tmp_db: SqliteDatabase,
) -> None:
    # The asymmetry is the point: the cache ports are total because a miss costs
    # a recomputation, and the store's are not because a lost page mirror is not
    # recoverable by retrying.
    cache = SqliteOcrCache(tmp_db)
    store = SqliteDocumentSyncStore(tmp_db)
    tmp_db.primary.close()
    with pytest.raises(StoreUnavailableError):
        store.list_documents()
    assert cache.get(an_ocr_key()) is None
    cache.put(an_ocr_key(), an_ocr_artifact())


def test_a_missing_legacy_file_is_reported_as_an_unavailable_store(tmp_path: Path) -> None:
    with pytest.raises(StoreUnavailableError, match="is not a file"):
        open_legacy_readonly(tmp_path / "absent.db")


def test_a_legacy_file_that_is_a_directory_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    path.mkdir()
    with pytest.raises(StoreUnavailableError):
        open_legacy_readonly(path)


@pytest.mark.parametrize("name", ["legacy.db", "we?ird.db", "hash#tag.db"])
def test_a_legacy_file_is_opened_read_only_whatever_its_path_contains(
    tmp_path: Path,
    name: str,
) -> None:
    # `f"file:{path}?mode=ro"` did not escape the path, so a `?` in it ended the
    # path and turned the rest into a query key SQLite ignores: it opened a
    # *different*, empty file, created it, and did not honour mode=ro -- while the
    # docstring promised nothing may write to the legacy database. Both halves of
    # that promise are asserted here, for a path that needs escaping and one that
    # does not.
    path = tmp_path / name
    seed = sqlite3.connect(path, isolation_level=None)
    try:
        seed.execute("CREATE TABLE pages (page_uuid TEXT)")
        seed.execute("INSERT INTO pages VALUES ('page-1')")
    finally:
        seed.close()
    before = sorted(item.name for item in tmp_path.iterdir())

    conn = open_legacy_readonly(path)
    try:
        assert conn.query("SELECT page_uuid FROM pages") == [("page-1",)]
        with pytest.raises(StoreUnavailableError, match="readonly database"):
            conn.execute("INSERT INTO pages VALUES ('page-2')")
    finally:
        conn.close()

    assert sorted(item.name for item in tmp_path.iterdir()) == before


def test_a_pragma_that_does_not_take_effect_fails_the_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pragmas are read back rather than assumed, so a value SQLite declines to
    # honour is a refusal to open rather than a connection that quietly behaves
    # differently. A negative busy timeout is the cheapest way to make SQLite
    # answer with something other than what was asked for.
    monkeypatch.setattr(sqlite_module, "_BUSY_TIMEOUT_MS", -5)
    path = tmp_path / "sync.db"
    with pytest.raises(StoreUnavailableError, match="pragmas did not take effect"):
        SqliteDatabase.open(path)


def test_a_body_that_committed_itself_is_not_rolled_back_again(
    tmp_db: SqliteDatabase,
) -> None:
    # ROLLBACK with nothing active raises, which would replace the real failure
    # with a bogus one -- so the rollback is guarded on in_transaction.
    with pytest.raises(RuntimeError, match="deliberate"):
        _delete_then_fail(tmp_db, commit_first=True)
    assert tmp_db.primary.in_transaction is False


def test_the_handle_reports_the_path_it_was_opened_on(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "sync.db")
    try:
        assert database.path == tmp_path / "sync.db"
        assert database.store == "sync.db"
    finally:
        database.close()


def test_closing_twice_is_harmless(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "sync.db")
    database.connect()
    database.close()
    database.close()
