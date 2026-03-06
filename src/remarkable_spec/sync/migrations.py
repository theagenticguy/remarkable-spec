"""Database schema creation and data migration.

Called automatically on first access to the sync database. Creates all
tables if they don't exist and migrates legacy ``.ocr.txt`` sidecar
files into the ``ocr_cache`` table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_SQL = """\
-- Sync state: tracked documents
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

CREATE INDEX IF NOT EXISTS idx_documents_parent ON documents(parent);
CREATE INDEX IF NOT EXISTS idx_documents_name ON documents(visible_name);

-- Sync state: tracked pages
CREATE TABLE IF NOT EXISTS pages (
    page_uuid       TEXT NOT NULL,
    doc_uuid        TEXT NOT NULL REFERENCES documents(doc_uuid) ON DELETE CASCADE,
    page_index      INTEGER NOT NULL,
    rm_hash         TEXT,
    rm_size_bytes   INTEGER,
    last_synced_at  TEXT,
    PRIMARY KEY (page_uuid, doc_uuid)
);

CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(doc_uuid);
CREATE INDEX IF NOT EXISTS idx_pages_hash ON pages(rm_hash);

-- Cache: OCR results (one row per engine per rm_hash)
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

CREATE INDEX IF NOT EXISTS idx_ocr_hash ON ocr_cache(rm_hash);

-- Cache: diagram extraction results (one row per rm_hash)
CREATE TABLE IF NOT EXISTS diagram_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rm_hash         TEXT NOT NULL UNIQUE,
    content_type    TEXT NOT NULL,
    mermaid_code    TEXT,
    diagram_type    TEXT,
    model_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_diagram_hash ON diagram_cache(rm_hash);

-- Audit log: sync operations
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

CREATE INDEX IF NOT EXISTS idx_synclog_doc ON sync_log(doc_uuid);
CREATE INDEX IF NOT EXISTS idx_synclog_time ON sync_log(timestamp);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist.

    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
    """
    conn.executescript(_SCHEMA_SQL)

    # Record schema version if not present
    cur = conn.execute("SELECT COUNT(*) FROM schema_version")
    if cur.fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()


def migrate_ocr_sidecars(conn: sqlite3.Connection, xochitl_dir: Path) -> int:
    """Import existing ``.ocr.txt`` sidecar files into the ocr_cache table.

    Scans the xochitl directory for ``{page_uuid}.ocr.txt`` files,
    computes the rm_hash of the corresponding ``.rm`` file, and inserts
    the cached text into the database.

    Returns the number of entries migrated.
    """
    from remarkable_spec.sync.hasher import hash_file

    count = 0
    if not xochitl_dir.exists():
        return count

    for ocr_txt in xochitl_dir.rglob("*.ocr.txt"):
        rm_path = ocr_txt.with_suffix(".rm")
        if not rm_path.exists():
            continue

        rm_hash = hash_file(rm_path)
        ocr_text = ocr_txt.read_text(encoding="utf-8")

        if not ocr_text.strip():
            continue

        try:
            conn.execute(
                """INSERT OR IGNORE INTO ocr_cache
                   (rm_hash, engine, ocr_text, confidence, model_id, render_dpi, created_at)
                   VALUES (?, 'vision', ?, NULL, NULL, 300, datetime('now'))""",
                (rm_hash, ocr_text),
            )
            count += 1
        except Exception:
            continue

    conn.commit()
    return count
