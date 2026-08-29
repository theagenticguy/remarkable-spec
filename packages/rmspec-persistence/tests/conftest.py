"""Fixtures for the persistence suite.

Two rules this file exists to enforce.

Every database is under ``tmp_path``. Nothing in this suite reads ``HOME``, opens a
socket, constructs a cloud client, or touches the tablet;
``test_persistence_hygiene.py`` asserts that rather than trusting it.

Every connection is closed deterministically. An unclosed ``sqlite3.Connection``
raises ``ResourceWarning`` when it is collected, this workspace turns warnings
into errors, and ``pytest-randomly`` means the resulting failure lands on whichever
unrelated test happens to be running at collection time. So the handle fixture
closes in a ``finally``, and so does every test that opens a second handle.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from rmspec.persistence import SqliteDatabase

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

#: The legacy schema, verbatim from ``sync/migrations.py``'s ``_SCHEMA_SQL`` minus
#: its indexes, which nothing here reads. Used to build a pre-rewrite database so
#: the migration's refusal and the text rescue are tested against the real shape
#: rather than against a guess at it.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_uuid            TEXT PRIMARY KEY,
    visible_name        TEXT NOT NULL,
    doc_type            TEXT NOT NULL DEFAULT 'DocumentType',
    file_type           TEXT NOT NULL DEFAULT 'notebook',
    parent              TEXT NOT NULL DEFAULT '',
    page_count          INTEGER NOT NULL DEFAULT 0,
    metadata_hash       TEXT,
    content_hash        TEXT,
    device_last_modified INTEGER NOT NULL DEFAULT 0,
    last_synced_at      TEXT,
    local_path          TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    page_uuid       TEXT NOT NULL,
    doc_uuid        TEXT NOT NULL REFERENCES documents(doc_uuid) ON DELETE CASCADE,
    page_index      INTEGER NOT NULL,
    rm_hash         TEXT,
    rm_size_bytes   INTEGER,
    last_synced_at  TEXT,
    PRIMARY KEY (page_uuid, doc_uuid)
);
CREATE TABLE IF NOT EXISTS ocr_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rm_hash     TEXT NOT NULL,
    engine      TEXT NOT NULL,
    ocr_text    TEXT NOT NULL,
    confidence  REAL,
    model_id    TEXT,
    render_dpi  INTEGER NOT NULL DEFAULT 300,
    created_at  TEXT NOT NULL,
    UNIQUE (rm_hash, engine)
);
CREATE TABLE IF NOT EXISTS diagram_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rm_hash         TEXT NOT NULL UNIQUE,
    content_type    TEXT NOT NULL,
    mermaid_code    TEXT,
    diagram_type    TEXT,
    model_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    direction           TEXT NOT NULL,
    doc_uuid            TEXT NOT NULL,
    doc_name            TEXT NOT NULL,
    pages_transferred   INTEGER DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ok',
    details             TEXT DEFAULT '',
    device_host         TEXT,
    timestamp           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[SqliteDatabase]:
    """Yield an open, migrated database under ``tmp_path``.

    Parameters
    ----------
    tmp_path
        pytest's per-test directory.

    Yields
    ------
    SqliteDatabase
        The open handle, closed on teardown.
    """
    database = SqliteDatabase.open(tmp_path / "sync.db")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """Return the path of a pre-rewrite database holding one document's OCR text.

    Built with the driver directly, which the per-package lint ignores allow in
    tests, because the point is to exercise the real legacy shape.

    Parameters
    ----------
    tmp_path
        pytest's per-test directory.

    Returns
    -------
    Path
        The legacy database file.
    """
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute(
            "INSERT INTO documents (doc_uuid, visible_name, page_count) VALUES (?, ?, ?)",
            ("doc-a", "Notes", 2),
        )
        conn.executemany(
            "INSERT INTO pages (page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("page-1", "doc-a", 0, "a" * 64, 2048),
                ("page-2", "doc-a", 1, "b" * 64, 4096),
                ("page-3", "doc-a", 2, "c" * 64, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO ocr_cache (rm_hash, engine, ocr_text, render_dpi, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("a" * 64, "merged", "page one text", 300, "2025-06-01 09:00:00"),
                ("a" * 64, "vision", "page one, vision only", 300, "2025-06-01 09:00:00"),
                ("b" * 64, "merged", "page two text", 300, "2025-06-01 09:00:00"),
                ("c" * 64, "merged", "   ", 300, "2025-06-01 09:00:00"),
            ],
        )
    finally:
        conn.close()
    return path
