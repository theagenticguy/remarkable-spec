"""SQLite database for sync state and OCR/diagram caching.

The database is created lazily on first access at
``~/.remarkable-spec/sync.db`` (configurable via ``RMSPEC_SYNC_DB``).

Uses Python's built-in ``sqlite3`` module — zero additional dependencies.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from remarkable_spec.sync.models import (
    DiagramCacheEntry,
    OCRCacheEntry,
    SyncDocument,
    SyncLogEntry,
    SyncPage,
)

DEFAULT_DB_PATH = Path.home() / ".remarkable-spec" / "sync.db"


class SyncDB:
    """Local SQLite database for sync state, OCR cache, and diagram cache.

    The database file and parent directories are created automatically on
    first access. Schema migrations run on connection.

    Args:
        db_path: Path to the SQLite database file. If ``None``, uses
            ``~/.remarkable-spec/sync.db``.

    Usage::

        db = SyncDB()
        db.upsert_document(SyncDocument(doc_uuid="abc", visible_name="Notes"))
        doc = db.get_document("abc")
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazily connect and initialize the database."""
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")

            from remarkable_spec.sync.migrations import init_schema

            init_schema(self._conn)
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SyncDB:
        _ = self.conn  # ensure connected
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── Document CRUD ──────────────────────────────────────────────

    def upsert_document(self, doc: SyncDocument) -> None:
        """Insert or update a document in the sync database."""
        self.conn.execute(
            """INSERT INTO documents
               (doc_uuid, visible_name, doc_type, file_type, parent, page_count,
                metadata_hash, content_hash, device_last_modified, last_synced_at, local_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(doc_uuid) DO UPDATE SET
                 visible_name=excluded.visible_name,
                 doc_type=excluded.doc_type,
                 file_type=excluded.file_type,
                 parent=excluded.parent,
                 page_count=excluded.page_count,
                 metadata_hash=excluded.metadata_hash,
                 content_hash=excluded.content_hash,
                 device_last_modified=excluded.device_last_modified,
                 last_synced_at=excluded.last_synced_at,
                 local_path=excluded.local_path""",
            (
                doc.doc_uuid,
                doc.visible_name,
                doc.doc_type,
                doc.file_type,
                doc.parent,
                doc.page_count,
                doc.metadata_hash,
                doc.content_hash,
                doc.device_last_modified,
                doc.last_synced_at.isoformat(),
                doc.local_path,
            ),
        )
        self.conn.commit()

    def get_document(self, doc_uuid: str) -> SyncDocument | None:
        """Retrieve a document by UUID, or ``None`` if not tracked."""
        row = self.conn.execute(
            "SELECT * FROM documents WHERE doc_uuid = ?", (doc_uuid,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_sync_document(row)

    def list_documents(self) -> list[SyncDocument]:
        """List all tracked documents."""
        rows = self.conn.execute("SELECT * FROM documents ORDER BY visible_name").fetchall()
        return [_row_to_sync_document(r) for r in rows]

    def delete_document(self, doc_uuid: str) -> None:
        """Remove a document and its pages from the database."""
        self.conn.execute("DELETE FROM documents WHERE doc_uuid = ?", (doc_uuid,))
        self.conn.commit()

    # ── Page CRUD ──────────────────────────────────────────────────

    def upsert_page(self, page: SyncPage) -> None:
        """Insert or update a page in the sync database."""
        self.conn.execute(
            """INSERT INTO pages
               (page_uuid, doc_uuid, page_index, rm_hash, rm_size_bytes, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_uuid, doc_uuid) DO UPDATE SET
                 page_index=excluded.page_index,
                 rm_hash=excluded.rm_hash,
                 rm_size_bytes=excluded.rm_size_bytes,
                 last_synced_at=excluded.last_synced_at""",
            (
                page.page_uuid,
                page.doc_uuid,
                page.page_index,
                page.rm_hash,
                page.rm_size_bytes,
                page.last_synced_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_pages(self, doc_uuid: str) -> list[SyncPage]:
        """List all tracked pages for a document, ordered by page index."""
        rows = self.conn.execute(
            "SELECT * FROM pages WHERE doc_uuid = ? ORDER BY page_index",
            (doc_uuid,),
        ).fetchall()
        return [_row_to_sync_page(r) for r in rows]

    def get_page(self, doc_uuid: str, page_uuid: str) -> SyncPage | None:
        """Retrieve a specific page, or ``None`` if not tracked."""
        row = self.conn.execute(
            "SELECT * FROM pages WHERE doc_uuid = ? AND page_uuid = ?",
            (doc_uuid, page_uuid),
        ).fetchone()
        if row is None:
            return None
        return _row_to_sync_page(row)

    # ── OCR Cache ──────────────────────────────────────────────────

    def get_ocr(self, rm_hash: str, engine: str = "merged") -> OCRCacheEntry | None:
        """Look up a cached OCR result by rm_hash and engine."""
        row = self.conn.execute(
            "SELECT * FROM ocr_cache WHERE rm_hash = ? AND engine = ?",
            (rm_hash, engine),
        ).fetchone()
        if row is None:
            return None
        return OCRCacheEntry(
            rm_hash=row["rm_hash"],
            engine=row["engine"],
            ocr_text=row["ocr_text"],
            confidence=row["confidence"],
            model_id=row["model_id"],
            render_dpi=row["render_dpi"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def put_ocr(self, entry: OCRCacheEntry) -> None:
        """Store an OCR result in the cache (upsert by rm_hash + engine)."""
        self.conn.execute(
            """INSERT INTO ocr_cache
               (rm_hash, engine, ocr_text, confidence, model_id, render_dpi, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rm_hash, engine) DO UPDATE SET
                 ocr_text=excluded.ocr_text,
                 confidence=excluded.confidence,
                 model_id=excluded.model_id,
                 render_dpi=excluded.render_dpi,
                 created_at=excluded.created_at""",
            (
                entry.rm_hash,
                entry.engine,
                entry.ocr_text,
                entry.confidence,
                entry.model_id,
                entry.render_dpi,
                entry.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_all_ocr(self, rm_hash: str) -> list[OCRCacheEntry]:
        """Get all cached OCR results for a given rm_hash (all engines)."""
        rows = self.conn.execute(
            "SELECT * FROM ocr_cache WHERE rm_hash = ? ORDER BY engine",
            (rm_hash,),
        ).fetchall()
        return [
            OCRCacheEntry(
                rm_hash=r["rm_hash"],
                engine=r["engine"],
                ocr_text=r["ocr_text"],
                confidence=r["confidence"],
                model_id=r["model_id"],
                render_dpi=r["render_dpi"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ── Diagram Cache ──────────────────────────────────────────────

    def get_diagram(self, rm_hash: str) -> DiagramCacheEntry | None:
        """Look up a cached diagram extraction result by rm_hash."""
        row = self.conn.execute(
            "SELECT * FROM diagram_cache WHERE rm_hash = ?", (rm_hash,)
        ).fetchone()
        if row is None:
            return None
        return DiagramCacheEntry(
            rm_hash=row["rm_hash"],
            content_type=row["content_type"],
            mermaid_code=row["mermaid_code"],
            diagram_type=row["diagram_type"],
            model_id=row["model_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def put_diagram(self, entry: DiagramCacheEntry) -> None:
        """Store a diagram extraction result in the cache."""
        self.conn.execute(
            """INSERT INTO diagram_cache
               (rm_hash, content_type, mermaid_code, diagram_type, model_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(rm_hash) DO UPDATE SET
                 content_type=excluded.content_type,
                 mermaid_code=excluded.mermaid_code,
                 diagram_type=excluded.diagram_type,
                 model_id=excluded.model_id,
                 created_at=excluded.created_at""",
            (
                entry.rm_hash,
                entry.content_type,
                entry.mermaid_code,
                entry.diagram_type,
                entry.model_id,
                entry.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    # ── Sync Log ───────────────────────────────────────────────────

    def log_sync(self, entry: SyncLogEntry) -> None:
        """Record a sync operation in the audit log."""
        self.conn.execute(
            """INSERT INTO sync_log
               (direction, doc_uuid, doc_name, pages_transferred, status,
                details, device_host, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.direction,
                entry.doc_uuid,
                entry.doc_name,
                entry.pages_transferred,
                entry.status,
                entry.details,
                entry.device_host,
                entry.timestamp.isoformat(),
            ),
        )
        self.conn.commit()

    def get_sync_log(self, limit: int = 50) -> list[SyncLogEntry]:
        """Retrieve recent sync log entries, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM sync_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            SyncLogEntry(
                direction=r["direction"],
                doc_uuid=r["doc_uuid"],
                doc_name=r["doc_name"],
                pages_transferred=r["pages_transferred"],
                status=r["status"],
                details=r["details"],
                device_host=r["device_host"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    # ── Change Detection ───────────────────────────────────────────

    def find_changed_pages(self, doc_uuid: str, current_hashes: dict[str, str]) -> list[str]:
        """Find pages whose rm_hash differs from what's in the database.

        Args:
            doc_uuid: Document UUID.
            current_hashes: Mapping of page_uuid → current SHA-256 of .rm file.

        Returns:
            List of page UUIDs that are new or have changed content.
        """
        tracked = {p.page_uuid: p.rm_hash for p in self.get_pages(doc_uuid)}
        changed = []
        for page_uuid, current_hash in current_hashes.items():
            if page_uuid not in tracked or tracked[page_uuid] != current_hash:
                changed.append(page_uuid)
        return changed


def _row_to_sync_document(row: sqlite3.Row) -> SyncDocument:
    """Convert a sqlite3.Row to a SyncDocument model."""
    synced = row["last_synced_at"]
    return SyncDocument(
        doc_uuid=row["doc_uuid"],
        visible_name=row["visible_name"],
        doc_type=row["doc_type"],
        file_type=row["file_type"],
        parent=row["parent"],
        page_count=row["page_count"],
        metadata_hash=row["metadata_hash"],
        content_hash=row["content_hash"],
        device_last_modified=row["device_last_modified"],
        last_synced_at=(datetime.fromisoformat(synced) if synced else datetime.now(UTC)),
        local_path=row["local_path"],
    )


def _row_to_sync_page(row: sqlite3.Row) -> SyncPage:
    """Convert a sqlite3.Row to a SyncPage model."""
    synced = row["last_synced_at"]
    return SyncPage(
        page_uuid=row["page_uuid"],
        doc_uuid=row["doc_uuid"],
        page_index=row["page_index"],
        rm_hash=row["rm_hash"],
        rm_size_bytes=row["rm_size_bytes"],
        last_synced_at=(datetime.fromisoformat(synced) if synced else datetime.now(UTC)),
    )
