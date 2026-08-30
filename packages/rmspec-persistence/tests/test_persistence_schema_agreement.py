"""Nothing may hand-mirror a model, and nothing may drift from one unnoticed.

This is the test the legacy code did not have. There, ``_SCHEMA_SQL`` spelled out
a column per model field and ``sync/models.py`` spelled out the fields again, with
nothing comparing the two: adding a field to one was a silent divergence. Here a
row is one JSON payload plus a small set of derived columns, and every derived
column is re-derived from its own row's payload and compared.

Two further gates live here. ``PRAGMA user_version`` covers the DDL but not the
payload, so the pinned fingerprints in
:mod:`rmspec.persistence.payload_schema` fail the build when a stored model's field
shape changes -- otherwise a new required field would turn every stored row into
``StoredRecordUnreadableError`` while the version still matched. And no table may
grow an ``AUTOINCREMENT`` column, ever, because the legacy surrogate ids were
unqueryable and ``sqlite_sequence`` is resettable, so one cannot underwrite the
audit log's no-reuse promise.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest
from persistence_builders import (
    a_diagram_artifact,
    a_diagram_key,
    a_document,
    a_page,
    a_page_text,
    an_audit_entry,
    an_ocr_artifact,
    an_ocr_key,
)
from pydantic import BaseModel

from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    OcrArtifact,
    OcrCacheKey,
)
from rmspec.persistence import (
    SqliteDiagramCache,
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    SqliteSyncAuditLog,
)
from rmspec.persistence.derived import utc_key
from rmspec.persistence.payload_schema import (
    PAYLOAD_FINGERPRINTS,
    STORED_MODELS,
    payload_fingerprint,
)

if TYPE_CHECKING:
    from rmspec.persistence import SqliteDatabase

#: Every column of every table, in declaration order. A payload column plus only
#: the columns a key, a declared ordering or an indexed lookup needs.
DECLARED_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "document": ("uuid", "name_fold", "payload"),
    "page": ("doc_uuid", "page_uuid", "page_index", "payload"),
    "page_text": ("doc_uuid", "page_uuid", "page_index", "payload"),
    "ocr_cache": ("digest", "page_hash", "created_at_utc", "key_payload", "artifact_payload"),
    "diagram_cache": ("digest", "page_hash", "created_at_utc", "key_payload", "artifact_payload"),
    "sync_audit": ("sequence", "payload"),
    "audit_counter": ("id", "next_sequence"),
}

#: One literal SELECT per payload column. Literals rather than an interpolated
#: table name, so this file needs no lint suppression to read a payload back.
PAYLOAD_QUERIES: Final[dict[str, str]] = {
    "document.payload": "SELECT payload FROM document",
    "page.payload": "SELECT payload FROM page",
    "page_text.payload": "SELECT payload FROM page_text",
    "ocr_cache.key_payload": "SELECT key_payload FROM ocr_cache",
    "ocr_cache.artifact_payload": "SELECT artifact_payload FROM ocr_cache",
    "diagram_cache.key_payload": "SELECT key_payload FROM diagram_cache",
    "diagram_cache.artifact_payload": "SELECT artifact_payload FROM diagram_cache",
    "sync_audit.payload": "SELECT payload FROM sync_audit",
}

#: One literal SELECT per table carrying a derived ``page_index`` column.
PAGE_INDEX_QUERIES: Final[dict[str, str]] = {
    "page": "SELECT page_index, payload FROM page",
    "page_text": "SELECT page_index, payload FROM page_text",
}

#: One literal SELECT per cache table.
CACHE_ROW_QUERIES: Final[dict[str, str]] = {
    "ocr_cache": (
        "SELECT digest, page_hash, created_at_utc, key_payload, artifact_payload FROM ocr_cache"
    ),
    "diagram_cache": (
        "SELECT digest, page_hash, created_at_utc, key_payload, artifact_payload "
        "FROM diagram_cache"
    ),
}


@pytest.fixture
def seeded(tmp_db: SqliteDatabase) -> SqliteDatabase:
    """Return a database holding one row in every table.

    Parameters
    ----------
    tmp_db
        An open database under ``tmp_path``.

    Returns
    -------
    SqliteDatabase
        The same handle, with one row per table.
    """
    store = SqliteDocumentSyncStore(tmp_db)
    store.record_document(
        a_document("doc-a", name="MiXeD Case Ünicode"),
        [a_page("doc-a", "page-1", 7)],
    )
    store.record_page_text(a_page_text("doc-a", "page-1", 7))
    SqliteOcrCache(tmp_db).put(an_ocr_key(), an_ocr_artifact())
    SqliteDiagramCache(tmp_db).put(a_diagram_key(), a_diagram_artifact())
    SqliteSyncAuditLog(tmp_db).append(an_audit_entry())
    return tmp_db


def _columns(database: SqliteDatabase, table: str) -> tuple[str, ...]:
    """Return one table's column names in declaration order.

    Parameters
    ----------
    database
        The open handle.
    table
        Table name, always a literal from :data:`DECLARED_COLUMNS`.

    Returns
    -------
    tuple[str, ...]
        Column names.
    """
    rows = database.primary.query("SELECT name FROM pragma_table_info(?)", (table,))
    return tuple(str(row[0]) for row in rows)


@pytest.mark.parametrize("table", sorted(DECLARED_COLUMNS))
def test_a_table_has_exactly_its_declared_columns(seeded: SqliteDatabase, table: str) -> None:
    # Exact equality in both directions: a model cannot silently require a new
    # column, and a column cannot survive that no named function produces.
    assert _columns(seeded, table) == DECLARED_COLUMNS[table]


def test_no_table_uses_autoincrement(seeded: SqliteDatabase) -> None:
    # Asserted against sqlite_master rather than against the migration text, so a
    # future migration that reintroduces one is caught too.
    statements = seeded.primary.query(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL",
    )
    offenders = [str(name) for name, sql in statements if "AUTOINCREMENT" in str(sql).upper()]
    assert offenders == []


def test_only_the_counter_table_has_a_column_named_id(seeded: SqliteDatabase) -> None:
    with_id = [table for table in DECLARED_COLUMNS if "id" in _columns(seeded, table)]
    assert with_id == ["audit_counter"]


def test_every_payload_column_is_declared_with_a_model() -> None:
    for column in STORED_MODELS:
        table, name = column.split(".")
        assert table in DECLARED_COLUMNS
        assert name in DECLARED_COLUMNS[table]


@pytest.mark.parametrize("column", sorted(STORED_MODELS))
def test_a_stored_model_matches_its_pinned_fingerprint(column: str) -> None:
    # The payload's version gate. When this fails, a stored model's field shape
    # changed. Decide which kind it is, the way `payload_schema`'s docstring sets out:
    # a change stored rows cannot satisfy needs a SCHEMA_VERSION bump and the migration
    # that rewrites the affected payloads before re-pinning; a new optional field with a
    # default needs only the re-pin, and a docstring saying why that default is what an
    # older row means. Either way, do not just update the number.
    assert payload_fingerprint(STORED_MODELS[column]) == PAYLOAD_FINGERPRINTS[column]


def test_the_fingerprint_table_covers_exactly_the_stored_models() -> None:
    assert set(PAYLOAD_FINGERPRINTS) == set(STORED_MODELS)


def test_a_fingerprint_notices_a_changed_field_shape() -> None:
    # Guard the guard: a fingerprint that never moves would pass the gate above
    # for ever. Two different models must not share one.
    fingerprints = {payload_fingerprint(model) for model in STORED_MODELS.values()}
    assert len(fingerprints) == len(set(STORED_MODELS.values()))


@pytest.mark.parametrize("column", sorted(STORED_MODELS))
def test_every_stored_payload_round_trips_its_model(seeded: SqliteDatabase, column: str) -> None:
    model_type = STORED_MODELS[column]
    rows = seeded.primary.query(PAYLOAD_QUERIES[column])
    assert rows
    for (payload,) in rows:
        restored = model_type.model_validate_json(str(payload))
        assert restored.model_dump_json() == payload


def test_the_document_name_fold_is_re_derivable_from_its_payload(
    seeded: SqliteDatabase,
) -> None:
    for name_fold, payload in seeded.primary.query("SELECT name_fold, payload FROM document"):
        recorded = json.loads(str(payload))
        assert name_fold == recorded["visible_name"].casefold()


@pytest.mark.parametrize("table", ["page", "page_text"])
def test_the_page_index_column_is_re_derivable_from_its_payload(
    seeded: SqliteDatabase,
    table: str,
) -> None:
    rows = seeded.primary.query(PAGE_INDEX_QUERIES[table])
    assert rows
    for page_index, payload in rows:
        assert page_index == json.loads(str(payload))["page_index"]


@pytest.mark.parametrize(
    ("table", "key_type", "artifact_type"),
    [
        ("ocr_cache", OcrCacheKey, OcrArtifact),
        ("diagram_cache", DiagramCacheKey, DiagramArtifact),
    ],
)
def test_a_cache_row_agrees_with_the_key_it_stores(
    seeded: SqliteDatabase,
    table: str,
    key_type: type[OcrCacheKey | DiagramCacheKey],
    artifact_type: type[OcrArtifact | DiagramArtifact],
) -> None:
    # The two types are named here rather than pulled from STORED_MODELS so the
    # digest and timestamp below are checked on the real models -- and the mapping
    # is asserted to agree, which is what keeps the two in step.
    assert STORED_MODELS[f"{table}.key_payload"] is key_type
    assert STORED_MODELS[f"{table}.artifact_payload"] is artifact_type
    rows = seeded.primary.query(CACHE_ROW_QUERIES[table])
    assert rows
    for digest, page_hash, created_at, key_payload, artifact_payload in rows:
        key = key_type.model_validate_json(str(key_payload))
        # The duplicated digest and page_hash columns are checked rather than
        # trusted: this is what makes it safe to index them.
        assert digest == key.digest
        assert page_hash == json.loads(str(key_payload))["page_hash"]
        artifact = artifact_type.model_validate_json(str(artifact_payload))
        assert created_at == utc_key(artifact.created_at)


def test_the_audit_sequence_column_is_the_row_key_not_a_payload_field(
    seeded: SqliteDatabase,
) -> None:
    for sequence, payload in seeded.primary.query("SELECT sequence, payload FROM sync_audit"):
        assert isinstance(sequence, int)
        assert "sequence" not in json.loads(str(payload))


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("PRAGMA journal_mode", "wal"),
        ("PRAGMA foreign_keys", 1),
        ("PRAGMA busy_timeout", 5000),
    ],
)
def test_both_connections_carry_the_same_verified_pragmas(
    tmp_db: SqliteDatabase,
    statement: str,
    expected: object,
) -> None:
    # Read back rather than assumed. Both are per-connection and both are silent
    # no-ops inside a transaction, and the audit log deliberately opens a second
    # connection -- a connection that quietly lost foreign_keys would stop
    # cascading with no error at all.
    second = tmp_db.connect()
    for connection in (tmp_db.primary, second):
        row = connection.query_one(statement)
        assert row is not None
        assert row[0] == expected


def test_the_fingerprint_of_a_self_referential_model_terminates() -> None:
    # Guard the recursion guard: the fingerprint walks nested models, so a model
    # that reaches itself must not walk for ever. None of today's stored models
    # does, which is exactly why this needs its own subject.
    class Node(BaseModel):
        """A model that contains itself."""

        child: Node | None = None
        label: str = ""

    assert payload_fingerprint(Node)
