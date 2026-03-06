"""Pydantic v2 models for sync state tracking.

These models represent rows in the local SQLite sync database. They are
the typed API surface used by ``SyncDB`` methods and the CLI commands.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SyncDocument(BaseModel):
    """A tracked document in the sync database."""

    doc_uuid: str = Field(description="reMarkable document UUID")
    visible_name: str = Field(description="Display name from .metadata")
    doc_type: str = Field(
        default="DocumentType",
        description="'DocumentType' for notebooks/PDFs, 'CollectionType' for folders",
    )
    file_type: str = Field(
        default="notebook",
        description="File type: 'notebook', 'pdf', or 'epub'",
    )
    parent: str = Field(
        default="",
        description="Parent folder UUID (empty = root, 'trash' = trashed)",
    )
    page_count: int = Field(default=0, description="Number of pages in the document")
    metadata_hash: str | None = Field(default=None, description="SHA-256 of the .metadata file")
    content_hash: str | None = Field(default=None, description="SHA-256 of the .content file")
    device_last_modified: int = Field(
        default=0, description="last_modified from .metadata (epoch milliseconds)"
    )
    last_synced_at: datetime = Field(
        default_factory=_utcnow, description="Timestamp of last successful sync"
    )
    local_path: str | None = Field(
        default=None, description="Local filesystem path where document was pulled to"
    )


class SyncPage(BaseModel):
    """A tracked page within a document."""

    page_uuid: str = Field(description="reMarkable page UUID")
    doc_uuid: str = Field(description="Parent document UUID")
    page_index: int = Field(description="Zero-based page position in document")
    rm_hash: str | None = Field(
        default=None,
        description="SHA-256 of the .rm file — the cache invalidation key",
    )
    rm_size_bytes: int | None = Field(default=None, description="Size of the .rm file in bytes")
    last_synced_at: datetime = Field(
        default_factory=_utcnow, description="Timestamp of last sync for this page"
    )


class OCRCacheEntry(BaseModel):
    """Cached OCR result keyed by rm_hash.

    Multiple entries can exist per page — one per engine per version
    of the page content.
    """

    rm_hash: str = Field(description="SHA-256 of the .rm file when OCR was run")
    engine: str = Field(description="OCR engine: 'vision', 'textract', or 'merged'")
    ocr_text: str = Field(description="Recognized text content")
    confidence: float | None = Field(
        default=None, description="Average confidence score (0.0-1.0)"
    )
    model_id: str | None = Field(
        default=None,
        description="LLM model ID used for the 'merged' engine",
    )
    render_dpi: int = Field(default=300, description="DPI used when rendering for OCR")
    created_at: datetime = Field(
        default_factory=_utcnow, description="When this cache entry was created"
    )


class DiagramCacheEntry(BaseModel):
    """Cached diagram extraction result keyed by rm_hash."""

    rm_hash: str = Field(description="SHA-256 of the .rm file when extraction ran")
    content_type: str = Field(
        description="Page content classification: 'TEXT', 'DIAGRAM', or 'MIXED'"
    )
    mermaid_code: str | None = Field(
        default=None, description="Generated Mermaid syntax (None if page is TEXT only)"
    )
    diagram_type: str | None = Field(
        default=None,
        description="Mermaid diagram type: 'flowchart', 'sequence', 'mindmap', etc.",
    )
    model_id: str = Field(description="LLM model ID used for extraction")
    created_at: datetime = Field(
        default_factory=_utcnow, description="When this cache entry was created"
    )


class SyncLogEntry(BaseModel):
    """An entry in the sync history log."""

    direction: str = Field(description="'pull' or 'push'")
    doc_uuid: str = Field(description="Document UUID")
    doc_name: str = Field(description="Document name at time of sync (denormalized)")
    pages_transferred: int = Field(default=0, description="Number of pages transferred")
    status: str = Field(
        default="ok", description="Sync status: 'ok', 'conflict', 'error', 'skipped'"
    )
    details: str = Field(default="", description="Additional details or error message")
    device_host: str | None = Field(default=None, description="Device IP used for this sync")
    timestamp: datetime = Field(default_factory=_utcnow, description="When the sync occurred")
