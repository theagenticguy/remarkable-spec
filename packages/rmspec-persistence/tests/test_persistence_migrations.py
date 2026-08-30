"""Versioned migrations, and the version comparison the legacy code never made.

The legacy ``init_schema`` read ``schema_version`` and never compared it to
``SCHEMA_VERSION``, so the assertion at the centre of this file -- a newer file on
disk is refused, loudly, with both numbers -- is one the legacy code could not
have made about itself.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from rmspec.domain.errors import StoreSchemaMismatchError, StoreUnavailableError
from rmspec.persistence import SCHEMA_VERSION, SqliteDatabase
from rmspec.persistence import _sqlite as sqlite_module
from rmspec.persistence import migrations as migrations_module
from rmspec.persistence.migrations import (
    LEGACY_REMEDIATION_TEMPLATE,
    LEGACY_TABLES,
    MIGRATIONS,
    NEWER_SCHEMA_REMEDIATION,
    Migration,
)

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_TABLES = frozenset(
    {
        "document",
        "page",
        "page_text",
        "ocr_cache",
        "diagram_cache",
        "sync_audit",
        "audit_counter",
    },
)

EXPECTED_INDEXES = frozenset(
    {
        "document_name_order",
        "page_order",
        "page_text_order",
        "ocr_cache_page",
        "ocr_cache_age",
        "diagram_cache_page",
        "diagram_cache_age",
    },
)


def _user_version(path: Path) -> int:
    """Return a database file's ``user_version`` without going through the package.

    Parameters
    ----------
    path
        The database file.

    Returns
    -------
    int
        The header's version integer.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _objects(path: Path, kind: str) -> frozenset[str]:
    """Return the names of one kind of schema object in a database file.

    Parameters
    ----------
    path
        The database file.
    kind
        ``"table"`` or ``"index"``.

    Returns
    -------
    frozenset[str]
        Object names, excluding SQLite's own internal ones.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (kind,),
        ).fetchall()
    finally:
        conn.close()
    return frozenset(str(row[0]) for row in rows)


def test_migration_versions_are_contiguous_and_end_at_the_schema_version() -> None:
    versions = [step.version for step in MIGRATIONS]
    assert versions == list(range(1, SCHEMA_VERSION + 1))
    assert len(set(versions)) == len(versions)


def test_every_migration_carries_at_least_one_statement_and_a_description() -> None:
    for step in MIGRATIONS:
        assert step.statements
        assert step.description.strip()


def test_no_migration_uses_conditional_ddl() -> None:
    # `CREATE TABLE IF NOT EXISTS` is how the legacy schema left a stale shape in
    # place: the version number must be what decides whether a step runs.
    for step in MIGRATIONS:
        for statement in step.statements:
            assert "IF NOT EXISTS" not in statement.upper()


def test_a_fresh_file_lands_at_the_current_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "sync.db"
    database = SqliteDatabase.open(path)
    database.close()
    assert _user_version(path) == SCHEMA_VERSION


def test_a_fresh_file_has_exactly_the_expected_tables_and_indexes(tmp_path: Path) -> None:
    path = tmp_path / "sync.db"
    database = SqliteDatabase.open(path)
    database.close()
    assert _objects(path, "table") == EXPECTED_TABLES
    assert _objects(path, "index") == EXPECTED_INDEXES


def test_the_audit_counter_is_seeded_once(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "sync.db")
    try:
        assert database.primary.query("SELECT id, next_sequence FROM audit_counter") == [(1, 1)]
    finally:
        database.close()


def test_reopening_applies_nothing(tmp_path: Path) -> None:
    path = tmp_path / "sync.db"
    first = SqliteDatabase.open(path)
    first.close()
    second = SqliteDatabase.open(path)
    try:
        assert migrations_module.apply_migrations(second.primary) == 0
    finally:
        second.close()


def test_a_newer_file_is_refused_with_both_versions(tmp_path: Path) -> None:
    path = tmp_path / "sync.db"
    database = SqliteDatabase.open(path)
    database.close()
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    finally:
        conn.close()

    with pytest.raises(StoreSchemaMismatchError) as caught:
        SqliteDatabase.open(path)
    assert caught.value.found == SCHEMA_VERSION + 1
    assert caught.value.expected == SCHEMA_VERSION
    assert caught.value.store == "sync.db"
    # A subclass of StoreUnavailableError, and the CLI maps the two to different
    # exit codes -- 78 "fix your configuration" versus 74 "I/O failed" -- so the
    # exact type matters and a `pytest.raises(StoreUnavailableError)` here would
    # pass either way.
    assert isinstance(caught.value, StoreUnavailableError)


def test_a_legacy_file_is_refused_rather_than_dropped(legacy_db: Path) -> None:
    with pytest.raises(StoreSchemaMismatchError) as caught:
        SqliteDatabase.open(legacy_db)
    assert caught.value.found == 0
    assert caught.value.expected == SCHEMA_VERSION

    # Every legacy table and its rows survive, because ocr_cache.ocr_text is paid
    # output and StoreMaintenance.rescue_legacy_page_texts still has to read it.
    surviving = _objects(legacy_db, "table")
    assert surviving >= LEGACY_TABLES
    conn = sqlite3.connect(legacy_db, isolation_level=None)
    try:
        assert conn.execute("SELECT count(*) FROM ocr_cache").fetchone() == (4,)
        assert conn.execute("SELECT count(*) FROM pages").fetchone() == (3,)
    finally:
        conn.close()
    # Nothing from the new schema was created. Note that `ocr_cache` and
    # `diagram_cache` are named in both schemas with different shapes, which is a
    # second reason the refusal is right: today's DDL cannot run over yesterday's
    # tables even if someone wanted it to.
    new_only = EXPECTED_TABLES - {"ocr_cache", "diagram_cache"}
    assert not new_only & surviving


def test_both_refusals_tell_the_user_what_to_do(tmp_path: Path, legacy_db: Path) -> None:
    """Refusing is correct; refusing with nothing to do next is the upgrade wall.

    Every user of the legacy CLI meets the second of these on their first run of this
    build, and it exits 78 -- the environment is wrong, not the request -- so there is
    always an action and the error has to name it. Both sentences name
    ``RMSPEC_SYNC_DB``, which is the only handle on the file that survives this layer:
    ``store`` is the file's *name*, not the path the user set.
    """
    newer = tmp_path / "sync.db"
    database = SqliteDatabase.open(newer)
    database.close()
    conn = sqlite3.connect(newer, isolation_level=None)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    finally:
        conn.close()

    with pytest.raises(StoreSchemaMismatchError) as too_new:
        SqliteDatabase.open(newer)
    with pytest.raises(StoreSchemaMismatchError) as too_old:
        SqliteDatabase.open(legacy_db)

    assert too_new.value.remediation == NEWER_SCHEMA_REMEDIATION
    assert too_old.value.remediation == LEGACY_REMEDIATION_TEMPLATE.format(
        store=legacy_db.name,
    )
    # Two situations, two sentences: nothing is gained by upgrading past a file this
    # build already understands, and nothing is gained by moving aside a file a newer
    # build wrote.
    assert too_new.value.remediation != too_old.value.remediation
    for advice in (too_new.value.remediation, too_old.value.remediation):
        assert advice is not None
        assert "RMSPEC_SYNC_DB" in advice
        assert "{" not in advice


def test_an_empty_file_is_not_mistaken_for_a_legacy_one(tmp_path: Path) -> None:
    path = tmp_path / "sync.db"
    path.touch()
    database = SqliteDatabase.open(path)
    database.close()
    assert _user_version(path) == SCHEMA_VERSION


@pytest.mark.parametrize("size", [1, 2, 15, 16, 100, 4096])
def test_a_file_that_is_not_a_database_is_refused_and_left_alone(
    tmp_path: Path,
    size: int,
) -> None:
    # The driver refuses 16 bytes or more on its own, but *adopts* anything
    # shorter: it treats the file as empty, writes a header over it and creates the
    # schema, so the user's byte is gone. The header check makes every non-empty
    # size fail the same way, with the file untouched.
    path = tmp_path / "sync.db"
    payload = b"notes.md: buy milk"[:size].ljust(size, b"?")
    path.write_bytes(payload)

    with pytest.raises(StoreUnavailableError, match="not a SQLite database") as caught:
        SqliteDatabase.open(path)

    assert type(caught.value) is StoreUnavailableError
    assert caught.value.store == "sync.db"
    assert path.read_bytes() == payload
    assert sorted(item.name for item in tmp_path.iterdir()) == ["sync.db"]


def test_a_failed_migration_leaves_the_previous_version_and_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = Migration(
        version=SCHEMA_VERSION + 1,
        description="a step whose second statement cannot run",
        statements=(
            "CREATE TABLE arrived (a TEXT) STRICT",
            "CREATE TABLE this is not sql",
        ),
    )
    monkeypatch.setattr(migrations_module, "MIGRATIONS", (*MIGRATIONS, broken))
    path = tmp_path / "sync.db"

    with pytest.raises(StoreUnavailableError) as caught:
        SqliteDatabase.open(path)
    assert type(caught.value) is StoreUnavailableError

    # The baseline committed; the broken step rolled back whole, so neither its
    # table nor its version number survived.
    assert _user_version(path) == SCHEMA_VERSION
    tables = _objects(path, "table")
    assert "arrived" not in tables
    assert tables >= EXPECTED_TABLES


def test_an_unwritable_parent_directory_is_reported_as_an_unavailable_store(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(StoreUnavailableError) as caught:
        SqliteDatabase.open(blocker / "sync.db")
    assert type(caught.value) is StoreUnavailableError
    assert caught.value.store == "sync.db"


def test_too_old_a_sqlite_library_fails_at_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unguarded feature dependency would surface as an OperationalError from
    # the middle of a pull on a CI image with an older libsqlite3.
    monkeypatch.setattr(sqlite_module, "MINIMUM_SQLITE_VERSION", (99, 0, 0))
    with pytest.raises(StoreUnavailableError, match=r"older than 99\.0\.0"):
        SqliteDatabase.open(tmp_path / "sync.db")
    assert not (tmp_path / "sync.db").exists()


def test_a_directory_where_the_database_should_be_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "sync.db"
    path.mkdir()
    with pytest.raises(StoreUnavailableError) as caught:
        SqliteDatabase.open(path)
    assert type(caught.value) is StoreUnavailableError
