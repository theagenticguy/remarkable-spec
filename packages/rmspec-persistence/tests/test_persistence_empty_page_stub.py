"""Corpus finding 1, from the storage side: a zero-byte page is data, not a fault.

62 of the 92 real ``.rm`` files in the reference corpus are zero bytes -- empty
stubs for the unannotated pages of a PDF-backed document. The legacy parser raised
a bare ``EOFError`` with an empty message on every one, so two thirds of a real
document crashed a naive page loop. Parsing is a formats concern and this package
never sees that error; what it must guarantee is that the outcome is storable and
that it stays distinguishable from a failure.

Three states, not two
---------------------
``rm_hash=None`` means the page was never read. ``rm_hash`` = SHA-256 of zero bytes
with ``rm_size_bytes=0`` means it was read and holds no ink. A real digest with a
non-zero size means ink. The legacy schema could represent all three and the legacy
code conflated the first two; here all three round-trip byte-identically, through
both implementations.

Every test runs against the SQLite adapter and the in-memory double, because the
distinction has to hold for every later application-layer test that binds the
double.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from persistence_builders import (
    EMPTY_SCENE_DIGEST,
    a_document,
    a_page,
    a_page_text,
    an_empty_stub_page,
    an_ocr_artifact,
    an_ocr_key,
)

from rmspec.domain.models import PageText, TextProvenance
from rmspec.persistence import SqliteDocumentSyncStore, SqliteOcrCache
from rmspec.persistence import maintenance as maintenance_module
from rmspec.persistence.testing import InMemoryDocumentSyncStore, InMemoryOcrCache

if TYPE_CHECKING:
    from rmspec.domain.ports.persistence import DocumentSyncStore, OcrCache
    from rmspec.persistence import SqliteDatabase

#: The corpus's shape: 92 pages, 62 of them empty stubs.
CORPUS_PAGES = 92
CORPUS_STUBS = 62


@pytest.fixture(params=["sqlite", "memory"])
def store(request: pytest.FixtureRequest, tmp_db: SqliteDatabase) -> DocumentSyncStore:
    """Return each implementation in turn.

    Parameters
    ----------
    request
        pytest's parametrization handle.
    tmp_db
        An open database under ``tmp_path``.

    Returns
    -------
    DocumentSyncStore
        The SQLite adapter or the in-memory double.
    """
    if request.param == "sqlite":
        return SqliteDocumentSyncStore(tmp_db)
    return InMemoryDocumentSyncStore()


@pytest.fixture(params=["sqlite", "memory"])
def cache(request: pytest.FixtureRequest, tmp_db: SqliteDatabase) -> OcrCache:
    """Return each OCR cache implementation in turn.

    Parameters
    ----------
    request
        pytest's parametrization handle.
    tmp_db
        An open database under ``tmp_path``.

    Returns
    -------
    OcrCache
        The SQLite adapter or the in-memory double.
    """
    if request.param == "sqlite":
        return SqliteOcrCache(tmp_db)
    return InMemoryOcrCache()


def test_the_three_page_states_are_three_states(store: DocumentSyncStore) -> None:
    never_read = a_page("doc-a", "page-unread", 0, rm_hash=None, rm_size_bytes=None)
    empty_stub = an_empty_stub_page("doc-a", "page-stub", 1)
    inked = a_page("doc-a", "page-inked", 2, rm_hash="f" * 64, rm_size_bytes=4096)
    store.record_document(a_document("doc-a", page_count=3), [never_read, empty_stub, inked])

    stored = store.pages("doc-a")
    assert stored == [never_read, empty_stub, inked]
    assert stored[0].rm_hash is None
    assert stored[1].rm_hash == EMPTY_SCENE_DIGEST
    assert stored[1].rm_size_bytes == 0
    assert stored[2].rm_size_bytes == 4096
    # The pair the legacy code conflated: "never read" and "read, no ink" are not
    # the same record.
    assert stored[0] != stored[1]


def test_a_pdf_backed_document_of_mostly_stubs_records_in_one_call(
    store: DocumentSyncStore,
) -> None:
    pages = [
        an_empty_stub_page("doc-a", f"page-{index:03d}", index)
        if index < CORPUS_STUBS
        else a_page("doc-a", f"page-{index:03d}", index, rm_hash=f"{index:064d}")
        for index in range(CORPUS_PAGES)
    ]
    store.record_document(a_document("doc-a", page_count=CORPUS_PAGES), pages)

    stored = store.pages("doc-a")
    assert len(stored) == CORPUS_PAGES
    assert [page.page_index for page in stored] == list(range(CORPUS_PAGES))
    assert sum(page.rm_size_bytes == 0 for page in stored) == CORPUS_STUBS


def test_a_blank_page_records_text_and_reads_back_as_a_present_entry(
    store: DocumentSyncStore,
) -> None:
    store.record_document(a_document("doc-a"), [an_empty_stub_page("doc-a", "page-stub", 0)])
    blank = PageText(
        doc_uuid="doc-a",
        page_uuid="page-stub",
        page_index=0,
        text="",
        provenance=TextProvenance(extracted_at=a_page_text().provenance.extracted_at),
    )
    store.record_page_text(blank)

    texts = store.page_texts("doc-a")
    # One present entry with empty text, not an empty list. "I read the page and
    # it held nothing" is a value; an absent entry would mean "nobody looked".
    assert len(texts) == 1
    assert texts[0] == blank
    assert texts[0].text == ""


def test_a_blank_page_claiming_a_model_is_rejected_by_the_model_not_the_store() -> None:
    # "No ink" and "the merge failed and was written down as a success" are
    # different things, and the second is unconstructible -- so no store error can
    # ever be confused for it.
    with pytest.raises(ValueError, match="empty text but claims model"):
        PageText(
            doc_uuid="doc-a",
            page_uuid="page-stub",
            page_index=0,
            text="",
            provenance=TextProvenance(
                model_fingerprint="model-v1",
                extracted_at=a_page_text().provenance.extracted_at,
            ),
        )


def test_an_empty_transcription_is_stored_and_returned(cache: OcrCache) -> None:
    key = an_ocr_key(EMPTY_SCENE_DIGEST)
    empty = an_ocr_artifact(text="")
    cache.put(key, empty)
    assert cache.get(key) == empty
    assert cache.get(an_ocr_key("never-stored")) is None


def test_stubs_share_a_page_hash_so_superseded_may_cross_between_them(
    cache: OcrCache,
) -> None:
    # A hazard the corpus creates: all 62 stubs hash to the same page_hash, so a
    # provenance lookup for one stub can return a key belonging to another -- or
    # to a stub in a different document, since the caches are not
    # document-scoped. The port makes superseded diagnostic-only, so the
    # cross-match cannot serve wrong output; this pins that boundary rather than
    # assuming it.
    stored = an_ocr_key(EMPTY_SCENE_DIGEST, variant="v1")
    cache.put(stored, an_ocr_artifact(text=""))
    asked = an_ocr_key(EMPTY_SCENE_DIGEST, variant="v2")

    found = cache.superseded(asked)

    assert found == stored
    assert found is not None
    assert found.page_hash == EMPTY_SCENE_DIGEST
    # It names provenance and nothing else: the caller still misses, and still
    # recomputes.
    assert cache.get(asked) is None


def test_an_inked_page_and_a_stub_do_not_share_a_cache_entry(cache: OcrCache) -> None:
    stub_key = an_ocr_key(EMPTY_SCENE_DIGEST)
    inked_key = an_ocr_key("f" * 64)
    cache.put(stub_key, an_ocr_artifact(text=""))
    assert cache.get(inked_key) is None
    assert cache.superseded(inked_key) is None


def test_the_maintenance_modules_empty_digest_literal_is_the_real_one() -> None:
    # The rescue and the prune docstring both turn on this exact value, and the
    # module writes it out rather than hashing -- this package holds no second
    # SHA-256. Pinned against hashlib here, where hashing a literal is free.
    assert hashlib.sha256(b"").hexdigest() == maintenance_module.EMPTY_SCENE_DIGEST
    assert maintenance_module.EMPTY_SCENE_DIGEST == EMPTY_SCENE_DIGEST
