"""Model builders for the persistence suite.

Every stored model is frozen with ``extra="forbid"`` and every timestamp field is
an ``AwareDatetime``, so a test that spells a model out by hand writes six
required fields to assert one. These builders default the noise and freeze the
clock, which is what lets the audit-log contract assert ``recent() == appends
reversed`` with no clock involved at all.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Final

from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    DocumentKind,
    OcrArtifact,
    OcrCacheKey,
    PageContentKind,
    PageText,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
    TextProvenance,
)

#: One fixed instant for the whole suite. No test reads a clock.
FROZEN_NOW: Final = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: SHA-256 of zero bytes: the fingerprint of one of the 62 empty ``.rm`` stubs in
#: the reference corpus. Computed here rather than exported from the package,
#: because the one definition of a page fingerprint is
#: ``DocumentRepository.page_fingerprint`` in ``rmspec-formats`` and persistence
#: must not grow a second one.
EMPTY_SCENE_DIGEST: Final = hashlib.sha256(b"").hexdigest()


def a_document(
    uuid: str = "doc-a",
    *,
    name: str = "Notes",
    page_count: int = 1,
    kind: DocumentKind = DocumentKind.DOCUMENT,
) -> SyncedDocument:
    """Return a recorded document.

    Parameters
    ----------
    uuid
        Document uuid.
    name
        Visible name, which drives ``list_documents``' declared order.
    page_count
        Page count the document reported.
    kind
        Document or folder.

    Returns
    -------
    SyncedDocument
        A document ready to record.
    """
    return SyncedDocument(
        uuid=uuid,
        visible_name=name,
        kind=kind,
        page_count=page_count,
        synced_at=FROZEN_NOW,
    )


def a_page(
    doc_uuid: str = "doc-a",
    page_uuid: str = "page-a",
    page_index: int = 0,
    *,
    rm_hash: str | None = "0" * 64,
    rm_size_bytes: int | None = 1024,
) -> SyncedPage:
    """Return a recorded page.

    Parameters
    ----------
    doc_uuid
        Owning document uuid.
    page_uuid
        The page's own uuid.
    page_index
        Zero-based position.
    rm_hash
        Scene-bytes fingerprint, or ``None`` for a page that was never read.
    rm_size_bytes
        Scene-bytes size, or ``None``.

    Returns
    -------
    SyncedPage
        A page ready to record.
    """
    return SyncedPage(
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        page_index=page_index,
        rm_hash=rm_hash,
        rm_size_bytes=rm_size_bytes,
        synced_at=FROZEN_NOW,
    )


def an_empty_stub_page(
    doc_uuid: str = "doc-a",
    page_uuid: str = "page-stub",
    page_index: int = 0,
) -> SyncedPage:
    """Return a page whose ``.rm`` file exists and is zero bytes.

    The middle of the three page states the corpus contains: read, and holding no
    ink. Distinct from ``rm_hash=None``, which means never read.

    Parameters
    ----------
    doc_uuid
        Owning document uuid.
    page_uuid
        The page's own uuid.
    page_index
        Zero-based position.

    Returns
    -------
    SyncedPage
        A page with a zero-byte scene.
    """
    return a_page(
        doc_uuid,
        page_uuid,
        page_index,
        rm_hash=EMPTY_SCENE_DIGEST,
        rm_size_bytes=0,
    )


def a_page_text(
    doc_uuid: str = "doc-a",
    page_uuid: str = "page-a",
    page_index: int = 0,
    *,
    text: str = "the quick brown fox",
    model_fingerprint: str | None = "model-v1",
) -> PageText:
    """Return recorded page text.

    Parameters
    ----------
    doc_uuid
        Owning document uuid.
    page_uuid
        The page's own uuid.
    page_index
        Zero-based position, part of the declared sort order.
    text
        The extracted text. Empty is legal, and means the page held nothing.
    model_fingerprint
        The merging model, or ``None``. Must be ``None`` when ``text`` is blank --
        a validator on the model says so.

    Returns
    -------
    PageText
        Text ready to record.
    """
    return PageText(
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        page_index=page_index,
        text=text,
        provenance=TextProvenance(
            recognizers=("vision",),
            model_fingerprint=model_fingerprint,
            render_dpi=300,
            extracted_at=FROZEN_NOW,
        ),
    )


def an_ocr_key(page_hash: str = "page-hash-a", *, variant: str = "v1") -> OcrCacheKey:
    """Return a fully-specified OCR cache key.

    Parameters
    ----------
    page_hash
        The page's scene-bytes fingerprint.
    variant
        Folded into every other component, so two keys for one page differ in
        their digest without the caller spelling six strings out.

    Returns
    -------
    OcrCacheKey
        A key whose ``digest`` covers every input.
    """
    return OcrCacheKey(
        page_hash=page_hash,
        render_digest=f"render-{variant}",
        raster_digest=f"raster-{variant}",
        recognizers=("vision", "textract"),
        model_fingerprint=f"model-{variant}",
        request_digest=f"request-{variant}",
    )


def an_ocr_artifact(
    *,
    text: str = "the quick brown fox",
    truncated: bool = False,
    created_at: datetime = FROZEN_NOW,
) -> OcrArtifact:
    """Return a cached transcription.

    Parameters
    ----------
    text
        The transcription. Empty is legal.
    truncated
        Whether generation stopped at a limit.
    created_at
        When it was produced, which is what ``prune`` compares against.

    Returns
    -------
    OcrArtifact
        A cacheable transcription.
    """
    return OcrArtifact(
        text=text,
        mean_confidence=0.94,
        truncated=truncated,
        created_at=created_at,
    )


def a_diagram_key(page_hash: str = "page-hash-a", *, variant: str = "v1") -> DiagramCacheKey:
    """Return a fully-specified diagram cache key.

    Parameters
    ----------
    page_hash
        The page's scene-bytes fingerprint.
    variant
        Folded into every other component.

    Returns
    -------
    DiagramCacheKey
        A key whose ``digest`` covers every input.
    """
    return DiagramCacheKey(
        page_hash=page_hash,
        render_digest=f"render-{variant}",
        raster_digest=f"raster-{variant}",
        model_fingerprint=f"model-{variant}",
        request_digest=f"request-{variant}",
    )


def a_diagram_artifact(*, created_at: datetime = FROZEN_NOW) -> DiagramArtifact:
    """Return a cached diagram extraction.

    Parameters
    ----------
    created_at
        When the extraction ran.

    Returns
    -------
    DiagramArtifact
        A cacheable extraction.
    """
    return DiagramArtifact(
        content_kind=PageContentKind.DIAGRAM,
        mermaid="flowchart TD\n  a --> b",
        diagram_kind="flowchart",
        created_at=created_at,
    )


def an_audit_entry(
    *,
    operation: SyncOperation = SyncOperation.PULL,
    outcome: SyncOutcome = SyncOutcome.SUCCEEDED,
    doc_uuid: str | None = "doc-a",
    pages_affected: int = 3,
    detail: str = "",
) -> SyncAuditEntry:
    """Return one history entry.

    Parameters
    ----------
    operation
        What kind of work it was.
    outcome
        How it ended. A failed or partial outcome needs a detail.
    doc_uuid
        The document worked on, or ``None`` for a library-wide operation.
    pages_affected
        How many pages were touched.
    detail
        Why, for a human reading the history.

    Returns
    -------
    SyncAuditEntry
        An entry ready to append.
    """
    return SyncAuditEntry(
        operation=operation,
        outcome=outcome,
        doc_uuid=doc_uuid,
        doc_name="Notes",
        pages_affected=pages_affected,
        detail=detail,
        occurred_at=FROZEN_NOW,
    )
