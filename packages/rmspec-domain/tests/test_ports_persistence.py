"""Contract suite for :mod:`rmspec.domain.ports.persistence` and its in-memory doubles.

The four doubles defined here -- :class:`InMemoryDocumentSyncStore`,
:class:`InMemoryOcrCache`, :class:`InMemoryDiagramCache` and
:class:`InMemorySyncAuditLog` -- are the reusable test doubles every later
app-layer test binds to. They are written against the port docstrings and nothing
else, so a use-case test that passes here is a use-case test the SQLite adapters
must also satisfy.

Three things this module deliberately asserts, because they are the design claims
the ports exist to make and each one is silently losable:

Exact-key cache lookup
    A change to *any* component of a cache key is a miss, never a hit. That is the
    fix for the defect where the legacy tables keyed on the source hash alone and
    kept the model id and the render DPI as ordinary columns, so a prompt change
    served a stale row as valid.
Error parity
    Every error named in a ``Raises`` section is reachable through a double, and
    no port declares an error only a relational adapter could raise. Both
    directions are checked mechanically against the module's own docstrings.
Declared total orders
    Every sequence-returning method has a total order, so the double and the
    adapter cannot disagree on ties. The ordering properties are checked with
    hypothesis over shuffled input rather than one hand-picked list.

The protocols are not ``runtime_checkable`` -- that is itself asserted -- so
conformance is checked two ways: structurally, by comparing
:func:`inspect.signature` of every port method against the double's, and
statically, by binding each double to a port-annotated name that ``ty`` checks.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rmspec.domain import errors as errors_module
from rmspec.domain.errors import (
    AuditWriteFailedError,
    PersistenceError,
    StoredRecordUnreadableError,
    StoreUnavailableError,
)
from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    DocumentKind,
    OcrArtifact,
    OcrCacheKey,
    PageContentKind,
    PageText,
    RecordedSyncAuditEntry,
    SourceKind,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
    TextProvenance,
)
from rmspec.domain.ports import persistence as persistence_module
from rmspec.domain.ports.persistence import (
    DiagramCache,
    DocumentSyncStore,
    OcrCache,
    SyncAuditLog,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# ──────────────────────────── fixed values for the doubles ────────────────────────────

_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
"""One frozen instant. Nothing in these ports orders by a clock."""

_STORE_NAME = "in-memory sync store"
_LOG_NAME = "in-memory sync audit log"

UnreadableKind = Literal["document", "page", "page_text"]
"""One ``seed_unreadable`` kind per reader, so the three are independently poisonable."""


# ──────────────────────────── the reusable in-memory doubles ────────────────────────────


class InMemoryDocumentSyncStore:
    """In-memory :class:`DocumentSyncStore`: three dicts keyed by document uuid.

    Seams, all of them off by default. ``fail_reads`` and ``fail_writes`` raise
    ``StoreUnavailableError`` from every reader or writer, and
    :meth:`seed_unreadable` raises ``StoredRecordUnreadableError`` from one reader
    at a time -- a single seam would make a ``PageText`` failure unreachable
    without also poisoning the ``SyncedDocument`` the same test needs to read.
    """

    def __init__(self, *, fail_reads: bool = False, fail_writes: bool = False) -> None:
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self._documents: dict[str, SyncedDocument] = {}
        self._pages: dict[str, dict[str, SyncedPage]] = {}
        self._texts: dict[str, dict[str, PageText]] = {}
        self._unreadable: dict[UnreadableKind, set[str]] = {
            "document": set(),
            "page": set(),
            "page_text": set(),
        }

    def seed_unreadable(self, record_kind: UnreadableKind, doc_uuid: str) -> None:
        """Make one reader fail to reconstruct this document's records."""
        self._unreadable[record_kind].add(doc_uuid)

    def _guard_read(self) -> None:
        if self.fail_reads:
            raise StoreUnavailableError(store=_STORE_NAME, detail="read seam is faulting")

    def _guard_write(self) -> None:
        if self.fail_writes:
            raise StoreUnavailableError(store=_STORE_NAME, detail="write seam is faulting")

    def _guard_readable(self, record_kind: UnreadableKind, doc_uuid: str) -> None:
        if doc_uuid in self._unreadable[record_kind]:
            raise StoredRecordUnreadableError(
                store=_STORE_NAME,
                table=record_kind,
                key=doc_uuid,
                detail="seeded unreadable",
            )

    def record_document(
        self,
        document: SyncedDocument,
        pages: Sequence[SyncedPage],
        /,
    ) -> None:
        """Record a document together with its complete page set."""
        self._guard_write()
        page_set = {page.page_uuid: page for page in pages}
        self._documents[document.uuid] = document
        self._pages[document.uuid] = page_set
        kept = self._texts.get(document.uuid, {})
        self._texts[document.uuid] = {
            page_uuid: text for page_uuid, text in kept.items() if page_uuid in page_set
        }

    def get_document(self, doc_uuid: str, /) -> SyncedDocument | None:
        """Return the recorded document with this uuid, or ``None`` if untracked."""
        self._guard_read()
        self._guard_readable("document", doc_uuid)
        return self._documents.get(doc_uuid)

    def list_documents(self) -> list[SyncedDocument]:
        """Return every recorded document, case-folded by name then by uuid."""
        self._guard_read()
        for doc_uuid in self._documents:
            self._guard_readable("document", doc_uuid)
        return sorted(
            self._documents.values(),
            key=lambda document: (document.visible_name.casefold(), document.uuid),
        )

    def pages(self, doc_uuid: str, /) -> list[SyncedPage]:
        """Return the recorded pages of one document, ordered by page index."""
        self._guard_read()
        self._guard_readable("page", doc_uuid)
        return sorted(
            self._pages.get(doc_uuid, {}).values(),
            key=lambda page: (page.page_index, page.page_uuid),
        )

    def forget_document(self, doc_uuid: str, /) -> None:
        """Forget a document, its pages, and its page text."""
        self._guard_write()
        self._documents.pop(doc_uuid, None)
        self._pages.pop(doc_uuid, None)
        self._texts.pop(doc_uuid, None)

    def record_page_text(self, page_text: PageText, /) -> None:
        """Record the extracted text of one page, replacing any earlier text."""
        self._guard_write()
        if page_text.page_uuid not in self._pages.get(page_text.doc_uuid, {}):
            return
        self._texts.setdefault(page_text.doc_uuid, {})[page_text.page_uuid] = page_text

    def page_texts(self, doc_uuid: str, /) -> list[PageText]:
        """Return recorded page text for one document, ordered by page index."""
        self._guard_read()
        self._guard_readable("page_text", doc_uuid)
        return sorted(
            self._texts.get(doc_uuid, {}).values(),
            key=lambda text: (text.page_index, text.page_uuid),
        )

    def all_page_texts(self) -> list[PageText]:
        """Return recorded page text for every tracked document."""
        self._guard_read()
        for doc_uuid in self._texts:
            self._guard_readable("page_text", doc_uuid)
        return sorted(
            (text for texts in self._texts.values() for text in texts.values()),
            key=lambda text: (text.doc_uuid, text.page_index, text.page_uuid),
        )


class InMemoryOcrCache:
    """In-memory :class:`OcrCache`: one dict keyed by ``key.digest``.

    All four methods are total, so the ``fail_reads`` and ``fail_writes`` seams
    swallow rather than raise. Through this port a swallowed fault and a genuine
    miss are the same ``None``, which is why the double also counts its calls:
    without the counters, totality is unassertable.
    """

    def __init__(self, *, fail_reads: bool = False, fail_writes: bool = False) -> None:
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.get_calls = 0
        self.put_calls = 0
        self.superseded_calls = 0
        self.equivalent_raster_calls = 0
        self._entries: dict[str, tuple[OcrCacheKey, OcrArtifact]] = {}

    def get(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return the artifact stored under this exact key, or ``None``."""
        self.get_calls += 1
        if self.fail_reads:
            return None
        entry = self._entries.get(key.digest)
        return None if entry is None else entry[1]

    def put(self, key: OcrCacheKey, artifact: OcrArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry."""
        self.put_calls += 1
        if self.fail_writes:
            return
        self._entries[key.digest] = (key, artifact)

    def superseded(self, key: OcrCacheKey, /) -> OcrCacheKey | None:
        """Return a stored key for the same page under other inputs, or ``None``."""
        self.superseded_calls += 1
        if self.fail_reads or key.digest in self._entries:
            return None
        same_page = [
            stored
            for digest, (stored, _) in self._entries.items()
            if stored.page_hash == key.page_hash and digest != key.digest
        ]
        if not same_page:
            return None
        return max(same_page, key=lambda stored: stored.digest)

    def equivalent_raster(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return a stored artifact for identical pixels under a different page hash."""
        self.equivalent_raster_calls += 1
        if self.fail_reads:
            return None
        wanted = key.raster_identity
        qualifying = {
            digest: artifact
            for digest, (stored, artifact) in self._entries.items()
            if stored.page_hash != key.page_hash and stored.raster_identity == wanted
        }
        if not qualifying:
            return None
        return qualifying[max(qualifying)]


class InMemoryDiagramCache:
    """In-memory :class:`DiagramCache`: the same seams as its OCR sibling.

    A separate class rather than a generic, mirroring the two separately-bindable
    ports. A truncated extraction is never stored, but that is the caller's
    precondition -- this double cannot check it, because ``DiagramArtifact``
    carries no ``truncated`` field to check.
    """

    def __init__(self, *, fail_reads: bool = False, fail_writes: bool = False) -> None:
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.get_calls = 0
        self.put_calls = 0
        self.superseded_calls = 0
        self._entries: dict[str, tuple[DiagramCacheKey, DiagramArtifact]] = {}

    def get(self, key: DiagramCacheKey, /) -> DiagramArtifact | None:
        """Return the artifact stored under this exact key, or ``None``."""
        self.get_calls += 1
        if self.fail_reads:
            return None
        entry = self._entries.get(key.digest)
        return None if entry is None else entry[1]

    def put(self, key: DiagramCacheKey, artifact: DiagramArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry."""
        self.put_calls += 1
        if self.fail_writes:
            return
        self._entries[key.digest] = (key, artifact)

    def superseded(self, key: DiagramCacheKey, /) -> DiagramCacheKey | None:
        """Return a stored key for the same page under other inputs, or ``None``."""
        self.superseded_calls += 1
        if self.fail_reads or key.digest in self._entries:
            return None
        same_page = [
            stored
            for digest, (stored, _) in self._entries.items()
            if stored.page_hash == key.page_hash and digest != key.digest
        ]
        if not same_page:
            return None
        return max(same_page, key=lambda stored: stored.digest)


class InMemorySyncAuditLog:
    """In-memory :class:`SyncAuditLog`: a list plus a monotonic counter.

    The counter allocates one past the highest sequence it has ever handed out, so
    :meth:`discard_oldest` -- which stands in for a retention pass -- leaves gaps
    without ever reusing a value.
    """

    def __init__(self, *, fail_reads: bool = False, fail_writes: bool = False) -> None:
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self._entries: list[RecordedSyncAuditEntry] = []
        self._next_sequence = 1
        self._unreadable: set[int] = set()

    def seed_unreadable(self, sequence: int) -> None:
        """Make the entry at this sequence fail to reconstruct."""
        self._unreadable.add(sequence)

    def discard_oldest(self, count: int) -> None:
        """Drop the oldest entries without renumbering, as a retention pass would."""
        ordered = sorted(self._entries, key=lambda recorded: recorded.sequence)
        self._entries = ordered[count:]

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Append one entry and return it as the store recorded it."""
        if self.fail_writes:
            raise AuditWriteFailedError(detail="append seam is faulting")
        recorded = RecordedSyncAuditEntry(sequence=self._next_sequence, entry=entry)
        self._next_sequence += 1
        self._entries.append(recorded)
        return recorded

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the most recently appended entries, newest first."""
        if limit < 1:
            msg = f"limit must be at least 1, got {limit}"
            raise ValueError(msg)
        if self.fail_reads:
            raise StoreUnavailableError(store=_LOG_NAME, detail="read seam is faulting")
        newest_first = sorted(self._entries, key=lambda recorded: recorded.sequence, reverse=True)
        window = newest_first[:limit]
        for recorded in window:
            if recorded.sequence in self._unreadable:
                raise StoredRecordUnreadableError(
                    store=_LOG_NAME,
                    table="audit",
                    key=str(recorded.sequence),
                    detail="seeded unreadable",
                )
        return window


# ──────────────────────────── model builders ────────────────────────────


def _document(
    uuid: str = "doc-1",
    *,
    name: str = "Notebook",
    page_count: int = 2,
    parent_uuid: str | None = None,
) -> SyncedDocument:
    return SyncedDocument(
        uuid=uuid,
        visible_name=name,
        kind=DocumentKind.DOCUMENT,
        source=SourceKind.NOTEBOOK,
        parent_uuid=parent_uuid,
        page_count=page_count,
        metadata_hash="meta-1",
        content_hash="content-1",
        device_last_modified=_AT,
        synced_at=_AT,
    )


def _page(
    page_uuid: str = "page-a",
    *,
    doc_uuid: str = "doc-1",
    index: int = 0,
    rm_hash: str | None = "rm-hash-a",
) -> SyncedPage:
    return SyncedPage(
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        page_index=index,
        rm_hash=rm_hash,
        rm_size_bytes=1024,
        synced_at=_AT,
    )


def _page_text(
    page_uuid: str = "page-a",
    *,
    doc_uuid: str = "doc-1",
    index: int = 0,
    text: str = "handwritten words",
    model_fingerprint: str | None = "model-1",
) -> PageText:
    return PageText(
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        page_index=index,
        text=text,
        provenance=TextProvenance(
            recognizers=("vision", "textract"),
            model_fingerprint=model_fingerprint,
            render_dpi=229,
            extracted_at=_AT,
        ),
    )


def _ocr_key(
    *,
    page_hash: str = "page-hash-1",
    render_digest: str = "render-1",
    raster_digest: str = "raster-1",
    recognizers: tuple[str, ...] = ("vision", "textract"),
    model_fingerprint: str = "model-1",
    request_digest: str = "request-1",
) -> OcrCacheKey:
    return OcrCacheKey(
        page_hash=page_hash,
        render_digest=render_digest,
        raster_digest=raster_digest,
        recognizers=recognizers,
        model_fingerprint=model_fingerprint,
        request_digest=request_digest,
    )


def _ocr_artifact(
    *,
    text: str = "recognized text",
    truncated: bool = False,
    mean_confidence: float | None = 0.87,
) -> OcrArtifact:
    return OcrArtifact(
        text=text,
        mean_confidence=mean_confidence,
        truncated=truncated,
        created_at=_AT,
    )


def _diagram_key(
    *,
    page_hash: str = "page-hash-1",
    render_digest: str = "render-1",
    raster_digest: str = "raster-1",
    model_fingerprint: str = "model-1",
    request_digest: str = "request-1",
) -> DiagramCacheKey:
    return DiagramCacheKey(
        page_hash=page_hash,
        render_digest=render_digest,
        raster_digest=raster_digest,
        model_fingerprint=model_fingerprint,
        request_digest=request_digest,
    )


def _diagram_artifact(
    *,
    content_kind: PageContentKind = PageContentKind.DIAGRAM,
    mermaid: str | None = "graph TD;\n  A-->B;",
) -> DiagramArtifact:
    return DiagramArtifact(
        content_kind=content_kind,
        mermaid=mermaid,
        diagram_kind="flowchart",
        created_at=_AT,
    )


def _audit_entry(
    *,
    operation: SyncOperation = SyncOperation.PULL,
    outcome: SyncOutcome = SyncOutcome.SUCCEEDED,
    doc_uuid: str | None = "doc-1",
    detail: str = "",
    pages_affected: int = 2,
) -> SyncAuditEntry:
    return SyncAuditEntry(
        operation=operation,
        outcome=outcome,
        doc_uuid=doc_uuid,
        doc_name="Notebook",
        pages_affected=pages_affected,
        detail=detail,
        occurred_at=_AT,
    )


# ──────────────────────────── conformance ────────────────────────────

_PORTS_AND_DOUBLES: list[tuple[type[object], type[object]]] = [
    (DocumentSyncStore, InMemoryDocumentSyncStore),
    (OcrCache, InMemoryOcrCache),
    (DiagramCache, InMemoryDiagramCache),
    (SyncAuditLog, InMemorySyncAuditLog),
]

_ALL_PORTS: list[type[object]] = [port for port, _ in _PORTS_AND_DOUBLES]


def _public_methods(cls: type[object]) -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(member)
        for name, member in vars(cls).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


def _shape(signature: inspect.Signature) -> list[tuple[str, object, object, object]]:
    return [
        (parameter.name, parameter.kind, parameter.default, parameter.annotation)
        for parameter in signature.parameters.values()
    ]


@pytest.mark.parametrize(("port", "double"), _PORTS_AND_DOUBLES)
def test_double_matches_port_signature_exactly(port: type[object], double: type[object]) -> None:
    """Every port method is implemented with an identical signature, spelling included."""
    port_methods = _public_methods(port)
    assert port_methods, f"{port.__name__} declares no methods"
    double_methods = _public_methods(double)
    for name, expected in port_methods.items():
        assert name in double_methods, f"{double.__name__} is missing {name}"
        actual = double_methods[name]
        assert _shape(actual) == _shape(expected), f"{name} parameters diverge"
        assert actual.return_annotation == expected.return_annotation, (
            f"{name} return annotation diverges"
        )


@pytest.mark.parametrize("port", _ALL_PORTS)
def test_ports_are_not_runtime_checkable(port: type[object]) -> None:
    """The module says nothing here is ``runtime_checkable``; prove ``isinstance`` refuses."""
    with pytest.raises(TypeError, match=r"[Ii]nstance"):
        isinstance(object(), port)


def test_doubles_bind_to_their_ports() -> None:
    """Bind each double to a port-annotated name: ``ty`` checks this statically."""
    store: DocumentSyncStore = InMemoryDocumentSyncStore()
    ocr: OcrCache = InMemoryOcrCache()
    diagram: DiagramCache = InMemoryDiagramCache()
    log: SyncAuditLog = InMemorySyncAuditLog()
    assert store.list_documents() == []
    assert store.all_page_texts() == []
    assert ocr.get(_ocr_key()) is None
    assert diagram.get(_diagram_key()) is None
    assert log.recent(limit=5) == []


def test_module_exports_exactly_the_four_ports() -> None:
    exported = list(persistence_module.__all__)
    assert exported == sorted(exported)
    assert set(exported) == {port.__name__ for port in _ALL_PORTS}


@pytest.mark.parametrize(("port", "double"), _PORTS_AND_DOUBLES)
def test_only_recent_takes_a_keyword_argument(port: type[object], double: type[object]) -> None:
    """Data parameters are positional-only, so no caller can depend on a parameter name."""
    for name, signature in _public_methods(port).items():
        kinds = {
            parameter.kind
            for parameter in signature.parameters.values()
            if parameter.name != "self"
        }
        if name == "recent":
            assert kinds == {inspect.Parameter.KEYWORD_ONLY}
        else:
            assert kinds <= {inspect.Parameter.POSITIONAL_ONLY}, f"{name} accepts a keyword"
    assert _public_methods(double).keys() >= _public_methods(port).keys()


def test_recent_limit_is_required_and_keyword_only() -> None:
    limit = inspect.signature(SyncAuditLog.recent).parameters["limit"]
    assert limit.kind is inspect.Parameter.KEYWORD_ONLY
    assert limit.default is inspect.Parameter.empty


# ──────────────────────────── declared-error parity ────────────────────────────


def _declared_errors(doc: str | None) -> set[str]:
    """Return the names listed in a numpy ``Raises`` section."""
    if doc is None:
        return set()
    names: set[str] = set()
    in_section = False
    section_indent = 0
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped == "Raises":
            in_section = True
            section_indent = len(line) - len(line.lstrip())
            continue
        if not in_section or not stripped or set(stripped) == {"-"}:
            continue
        if len(line) - len(line.lstrip()) == section_indent:
            names.add(stripped)
    return names


def _all_declared_errors() -> set[str]:
    names: set[str] = set()
    for port in _ALL_PORTS:
        for member in vars(port).values():
            if inspect.isfunction(member):
                names |= _declared_errors(member.__doc__)
    return names


def test_ports_declare_only_errors_a_double_can_raise() -> None:
    """No port may name an error only a relational adapter could produce."""
    assert _all_declared_errors() == {
        "AuditWriteFailedError",
        "StoredRecordUnreadableError",
        "StoreUnavailableError",
        "ValueError",
    }


def test_no_port_method_speaks_storage_vocabulary() -> None:
    """No signature takes or returns a row count, a cursor, a schema version, or a query."""
    banned = {
        "connection",
        "cursor",
        "query",
        "row",
        "rowcount",
        "schema_version",
        "sql",
        "table",
        "transaction",
    }
    for port in _ALL_PORTS:
        for name, signature in _public_methods(port).items():
            offending = banned & {parameter.name for parameter in signature.parameters.values()}
            assert not offending, f"{port.__name__}.{name} takes {offending}"
            assert signature.return_annotation not in {"int", "bool"}, (
                f"{port.__name__}.{name} returns a store-shaped scalar"
            )
    assert "StoreSchemaMismatchError" not in _all_declared_errors()


def test_every_declared_persistence_error_lives_in_the_domain() -> None:
    for name in _all_declared_errors() - {"ValueError"}:
        error = getattr(errors_module, name)
        assert issubclass(error, PersistenceError)


def test_every_declared_error_is_raisable_by_a_double() -> None:
    """Error parity: each declared error is reachable without SQLite."""
    with pytest.raises(StoreUnavailableError):
        InMemoryDocumentSyncStore(fail_reads=True).list_documents()

    poisoned = InMemoryDocumentSyncStore()
    poisoned.record_document(_document(), [_page()])
    poisoned.seed_unreadable("document", "doc-1")
    with pytest.raises(StoredRecordUnreadableError):
        poisoned.get_document("doc-1")

    with pytest.raises(AuditWriteFailedError):
        InMemorySyncAuditLog(fail_writes=True).append(_audit_entry())

    with pytest.raises(ValueError, match="at least 1"):
        InMemorySyncAuditLog().recent(limit=0)


def test_cache_ports_declare_no_errors_at_all() -> None:
    """Totality: a cache fault is a miss or a dropped write, never an exception."""
    for port in (OcrCache, DiagramCache):
        for member in vars(port).values():
            if inspect.isfunction(member):
                assert _declared_errors(member.__doc__) == set()


# ──────────────────────────── DocumentSyncStore ────────────────────────────


def test_record_document_round_trips_document_and_pages() -> None:
    store = InMemoryDocumentSyncStore()
    document = _document()
    pages = [_page("page-a", index=0), _page("page-b", index=1, rm_hash="rm-hash-b")]
    store.record_document(document, pages)
    assert store.get_document("doc-1") == document
    assert store.pages("doc-1") == pages


def test_get_document_returns_none_for_an_untracked_uuid() -> None:
    store = InMemoryDocumentSyncStore()
    assert store.get_document("doc-1") is None
    assert store.pages("doc-1") == []
    assert store.page_texts("doc-1") == []


def test_recording_the_same_document_twice_converges() -> None:
    """An interrupted pull is replayed, so recording must be idempotent."""
    store = InMemoryDocumentSyncStore()
    pages = [_page("page-a", index=0), _page("page-b", index=1)]
    store.record_document(_document(), pages)
    store.record_document(_document(), pages)
    assert store.list_documents() == [_document()]
    assert store.pages("doc-1") == pages


def test_recording_replaces_the_page_set_rather_than_merging_it() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a", index=0), _page("page-b", index=1)])
    store.record_document(_document(), [_page("page-c", index=0)])
    assert [page.page_uuid for page in store.pages("doc-1")] == ["page-c"]


def test_replacing_the_page_set_discards_text_for_departed_pages_only() -> None:
    """Text cannot outlive the page it describes; a surviving page keeps its text."""
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a", index=0), _page("page-b", index=1)])
    text_a = _page_text("page-a", index=0, text="page a text")
    text_b = _page_text("page-b", index=1, text="page b text")
    store.record_page_text(text_a)
    store.record_page_text(text_b)

    store.record_document(_document(), [_page("page-a", index=5), _page("page-c", index=6)])

    assert store.page_texts("doc-1") == [text_a]


def test_recording_an_empty_page_set_keeps_the_document_and_drops_all_text() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    store.record_page_text(_page_text("page-a"))
    store.record_document(_document(page_count=0), [])
    assert store.get_document("doc-1") is not None
    assert store.pages("doc-1") == []
    assert store.page_texts("doc-1") == []


def test_list_documents_orders_by_casefolded_name_then_uuid() -> None:
    """Raw ASCII order would put ``Banana`` before ``apple``; the contract order does not."""
    store = InMemoryDocumentSyncStore()
    store.record_document(_document("doc-3", name="Banana"), [])
    store.record_document(_document("doc-2", name="apple"), [])
    store.record_document(_document("doc-1", name="APPLE"), [])
    assert [(d.visible_name, d.uuid) for d in store.list_documents()] == [
        ("APPLE", "doc-1"),
        ("apple", "doc-2"),
        ("Banana", "doc-3"),
    ]


@settings(deadline=None)
@given(
    st.lists(
        st.tuples(st.sampled_from("abcdef"), st.integers(min_value=0, max_value=4)),
        unique_by=lambda spec: spec[0],
        max_size=6,
    )
)
def test_pages_are_totally_ordered_by_index_then_uuid(specs: list[tuple[str, int]]) -> None:
    store = InMemoryDocumentSyncStore()
    pages = [_page(f"page-{name}", index=index) for name, index in specs]
    store.record_document(_document(), pages)
    recorded = store.pages("doc-1")
    assert recorded == sorted(pages, key=lambda page: (page.page_index, page.page_uuid))
    assert set(recorded) == set(pages)


def test_forget_document_removes_document_pages_and_text() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    store.record_page_text(_page_text("page-a"))
    store.forget_document("doc-1")
    assert store.get_document("doc-1") is None
    assert store.pages("doc-1") == []
    assert store.page_texts("doc-1") == []
    assert store.list_documents() == []
    assert store.all_page_texts() == []


def test_forgetting_an_untracked_document_is_a_successful_no_op() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document("doc-1"), [])
    store.forget_document("doc-missing")
    assert [document.uuid for document in store.list_documents()] == ["doc-1"]


def test_page_text_is_keyed_on_doc_and_page_uuid_only() -> None:
    """``page_index`` is payload, so re-running OCR overwrites instead of accumulating."""
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a", index=0)])
    store.record_page_text(_page_text("page-a", index=0, text="first pass"))
    moved = _page_text("page-a", index=7, text="second pass")
    store.record_page_text(moved)
    assert store.page_texts("doc-1") == [moved]


def test_page_text_for_an_unrecorded_page_is_dropped_without_error() -> None:
    """An orphaned text row is not a representable state, and it is not an error either."""
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    store.record_page_text(_page_text("page-ghost"))
    assert store.page_texts("doc-1") == []
    assert store.all_page_texts() == []


def test_page_text_for_an_untracked_document_is_dropped_without_error() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_page_text(_page_text("page-a", doc_uuid="doc-unknown"))
    assert store.page_texts("doc-unknown") == []


def test_page_text_with_blank_text_and_no_model_claim_is_storable() -> None:
    """A blank page is a fact worth recording, as long as it claims no model."""
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    blank = _page_text("page-a", text="", model_fingerprint=None)
    store.record_page_text(blank)
    assert store.page_texts("doc-1") == [blank]


def test_page_texts_is_scoped_to_one_document() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document("doc-1"), [_page("page-a", doc_uuid="doc-1")])
    store.record_document(_document("doc-2"), [_page("page-a", doc_uuid="doc-2")])
    first = _page_text("page-a", doc_uuid="doc-1", text="from one")
    second = _page_text("page-a", doc_uuid="doc-2", text="from two")
    store.record_page_text(first)
    store.record_page_text(second)
    assert store.page_texts("doc-1") == [first]
    assert store.page_texts("doc-2") == [second]


def test_all_page_texts_orders_by_doc_then_index_then_page_uuid() -> None:
    store = InMemoryDocumentSyncStore()
    for doc_uuid in ("doc-2", "doc-1"):
        store.record_document(
            _document(doc_uuid),
            [
                _page("page-b", doc_uuid=doc_uuid, index=1),
                _page("page-a", doc_uuid=doc_uuid, index=1),
                _page("page-c", doc_uuid=doc_uuid, index=0),
            ],
        )
        for page_uuid, index in (("page-b", 1), ("page-a", 1), ("page-c", 0)):
            store.record_page_text(_page_text(page_uuid, doc_uuid=doc_uuid, index=index))
    recorded = [(t.doc_uuid, t.page_index, t.page_uuid) for t in store.all_page_texts()]
    assert recorded == [
        ("doc-1", 0, "page-c"),
        ("doc-1", 1, "page-a"),
        ("doc-1", 1, "page-b"),
        ("doc-2", 0, "page-c"),
        ("doc-2", 1, "page-a"),
        ("doc-2", 1, "page-b"),
    ]


def test_returned_sequences_are_snapshots_the_caller_may_mutate() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    borrowed = store.pages("doc-1")
    borrowed.clear()
    assert len(store.pages("doc-1")) == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda store: store.get_document("doc-1"),
        lambda store: store.list_documents(),
        lambda store: store.pages("doc-1"),
        lambda store: store.page_texts("doc-1"),
        lambda store: store.all_page_texts(),
    ],
)
def test_every_reader_raises_store_unavailable(
    call: Callable[[InMemoryDocumentSyncStore], object],
) -> None:
    store = InMemoryDocumentSyncStore(fail_reads=True)
    with pytest.raises(StoreUnavailableError):
        call(store)


@pytest.mark.parametrize(
    "call",
    [
        lambda store: store.record_document(_document(), []),
        lambda store: store.forget_document("doc-1"),
        lambda store: store.record_page_text(_page_text()),
    ],
)
def test_every_writer_raises_store_unavailable(
    call: Callable[[InMemoryDocumentSyncStore], object],
) -> None:
    store = InMemoryDocumentSyncStore(fail_writes=True)
    with pytest.raises(StoreUnavailableError):
        call(store)


def test_unreadable_document_records_do_not_poison_the_page_readers() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    store.record_page_text(_page_text("page-a"))
    store.seed_unreadable("document", "doc-1")
    with pytest.raises(StoredRecordUnreadableError):
        store.get_document("doc-1")
    with pytest.raises(StoredRecordUnreadableError):
        store.list_documents()
    assert len(store.pages("doc-1")) == 1
    assert len(store.page_texts("doc-1")) == 1


def test_unreadable_page_records_do_not_poison_the_text_readers() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    store.record_page_text(_page_text("page-a"))
    store.seed_unreadable("page", "doc-1")
    with pytest.raises(StoredRecordUnreadableError):
        store.pages("doc-1")
    assert store.get_document("doc-1") is not None
    assert len(store.page_texts("doc-1")) == 1


def test_unreadable_text_records_do_not_poison_the_document_readers() -> None:
    store = InMemoryDocumentSyncStore()
    store.record_document(_document(), [_page("page-a")])
    store.record_page_text(_page_text("page-a"))
    store.seed_unreadable("page_text", "doc-1")
    with pytest.raises(StoredRecordUnreadableError):
        store.page_texts("doc-1")
    with pytest.raises(StoredRecordUnreadableError):
        store.all_page_texts()
    assert store.get_document("doc-1") is not None
    assert len(store.pages("doc-1")) == 1


def test_change_detection_is_a_subtraction_the_caller_performs() -> None:
    """The port hands back stored fingerprints; comparing them is the use case's job."""
    store = InMemoryDocumentSyncStore()
    store.record_document(
        _document(),
        [_page("page-a", rm_hash="old"), _page("page-b", index=1, rm_hash="same")],
    )
    recorded = {page.page_uuid: page.rm_hash for page in store.pages("doc-1")}
    current = {"page-a": "new", "page-b": "same", "page-c": "fresh"}
    changed = {uuid for uuid, digest in current.items() if recorded.get(uuid) != digest}
    assert changed == {"page-a", "page-c"}


# ──────────────────────────── OcrCache ────────────────────────────


def test_ocr_cache_round_trips_an_artifact_under_its_digest() -> None:
    cache = InMemoryOcrCache()
    key, artifact = _ocr_key(), _ocr_artifact()
    assert cache.get(key) is None
    cache.put(key, artifact)
    assert cache.get(key) == artifact
    assert cache.get(_ocr_key()) == artifact


@pytest.mark.parametrize(
    "component",
    ["page_hash", "render_digest", "raster_digest", "model_fingerprint", "request_digest"],
)
def test_ocr_cache_misses_when_any_key_component_changes(component: str) -> None:
    """Defect 3: a changed prompt, model or DPI must miss, never serve a stale row."""
    cache = InMemoryOcrCache()
    stored = _ocr_key()
    cache.put(stored, _ocr_artifact())
    changed = stored.model_copy(update={component: "changed"})
    assert changed.digest != stored.digest
    assert cache.get(changed) is None


def test_ocr_cache_misses_when_the_recognizer_set_changes() -> None:
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(), _ocr_artifact())
    assert cache.get(_ocr_key(recognizers=("vision",))) is None
    assert cache.get(_ocr_key(recognizers=("textract", "vision"))) is None


def test_ocr_put_is_idempotent_and_overwrites() -> None:
    cache = InMemoryOcrCache()
    key = _ocr_key()
    cache.put(key, _ocr_artifact(text="first"))
    cache.put(key, _ocr_artifact(text="second"))
    hit = cache.get(key)
    assert hit is not None
    assert hit.text == "second"


def test_ocr_cache_preserves_the_truncated_flag_across_the_round_trip() -> None:
    """A partial page is a hit that says so; dropping it would re-pay the recognizers."""
    cache = InMemoryOcrCache()
    key = _ocr_key()
    cache.put(key, _ocr_artifact(text="half a p", truncated=True))
    hit = cache.get(key)
    assert hit is not None
    assert hit.truncated is True
    assert hit.text == "half a p"


def test_ocr_cache_reads_are_total_and_swallow_faults() -> None:
    cache = InMemoryOcrCache()
    key = _ocr_key()
    cache.put(key, _ocr_artifact())
    cache.fail_reads = True
    assert cache.get(key) is None
    assert cache.superseded(_ocr_key(request_digest="other")) is None
    assert cache.get_calls == 1
    assert cache.superseded_calls == 1


def test_ocr_cache_writes_are_total_and_swallow_faults() -> None:
    cache = InMemoryOcrCache(fail_writes=True)
    key = _ocr_key()
    cache.put(key, _ocr_artifact())
    assert cache.put_calls == 1
    assert cache.get(key) is None


def test_ocr_superseded_names_the_page_whose_inputs_changed() -> None:
    cache = InMemoryOcrCache()
    stored = _ocr_key(request_digest="prompt-v1")
    cache.put(stored, _ocr_artifact())
    result = cache.superseded(_ocr_key(request_digest="prompt-v2"))
    assert result == stored
    assert isinstance(result, OcrCacheKey)


def test_ocr_superseded_returns_none_when_the_key_itself_is_stored() -> None:
    cache = InMemoryOcrCache()
    key = _ocr_key()
    cache.put(key, _ocr_artifact())
    cache.put(_ocr_key(request_digest="other"), _ocr_artifact())
    assert cache.superseded(key) is None


def test_ocr_superseded_ignores_other_pages() -> None:
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(page_hash="page-hash-2"), _ocr_artifact())
    assert cache.superseded(_ocr_key(page_hash="page-hash-1")) is None
    assert cache.superseded(_ocr_key()) is None


@settings(deadline=None)
@given(
    st.sets(
        st.text(alphabet="0123456789abcdef", min_size=2, max_size=4),
        min_size=2,
        max_size=5,
    )
)
def test_ocr_superseded_returns_the_greatest_stored_digest(request_digests: set[str]) -> None:
    """The tie-break is arbitrary but declared, so double and adapter cannot diverge."""
    cache = InMemoryOcrCache()
    stored = [_ocr_key(request_digest=digest) for digest in sorted(request_digests)]
    for key in stored:
        cache.put(key, _ocr_artifact())
    result = cache.superseded(_ocr_key(request_digest="probe-not-stored"))
    assert result is not None
    assert result.digest == max(key.digest for key in stored)


def test_ocr_superseded_never_returns_an_artifact() -> None:
    """Diagnostic only: it names provenance, so it can never become a fallback."""
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(request_digest="v1"), _ocr_artifact(text="paid output"))
    result = cache.superseded(_ocr_key(request_digest="v2"))
    assert not isinstance(result, OcrArtifact)


# ─────────────── OcrCache.equivalent_raster: the one sanctioned fallback ───────────────


def test_equivalent_raster_serves_a_row_stored_under_other_page_bytes() -> None:
    """The measured case: the bytes were rewritten and the pixels were not."""
    cache = InMemoryOcrCache()
    paid = _ocr_artifact(text="paid output")
    cache.put(_ocr_key(page_hash="page-hash-before"), paid)
    assert cache.get(_ocr_key(page_hash="page-hash-after")) is None
    assert cache.equivalent_raster(_ocr_key(page_hash="page-hash-after")) == paid


@pytest.mark.parametrize(
    "component",
    ["render_digest", "raster_digest", "model_fingerprint", "request_digest"],
)
def test_equivalent_raster_still_misses_when_any_other_component_moves(component: str) -> None:
    """Only ``page_hash`` may differ; this is a fallback, not a blanket one."""
    cache = InMemoryOcrCache()
    stored = _ocr_key(page_hash="page-hash-before")
    cache.put(stored, _ocr_artifact())
    probe = stored.model_copy(update={"page_hash": "page-hash-after", component: "moved"})
    assert cache.equivalent_raster(probe) is None


def test_equivalent_raster_misses_when_the_recognizer_set_changes() -> None:
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(page_hash="page-hash-before", recognizers=("vision",)), _ocr_artifact())
    probe = _ocr_key(page_hash="page-hash-after", recognizers=("vision", "textract"))
    assert cache.equivalent_raster(probe) is None


def test_equivalent_raster_ignores_a_row_for_the_very_same_page() -> None:
    """``page_hash`` must differ: this page's own rows are ``get``'s or ``superseded``'s."""
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(page_hash="page-hash-1"), _ocr_artifact())
    assert cache.equivalent_raster(_ocr_key(page_hash="page-hash-1")) is None


def test_equivalent_raster_is_none_on_a_cold_cache() -> None:
    assert InMemoryOcrCache().equivalent_raster(_ocr_key()) is None


@given(
    st.sets(
        st.text(alphabet="0123456789abcdef", min_size=2, max_size=4),
        min_size=2,
        max_size=5,
    )
)
def test_equivalent_raster_returns_the_greatest_stored_digest(page_hashes: set[str]) -> None:
    """The same arbitrary-but-declared tie-break ``superseded`` follows."""
    cache = InMemoryOcrCache()
    stored = [_ocr_key(page_hash=f"stored-{page_hash}") for page_hash in sorted(page_hashes)]
    for index, key in enumerate(stored):
        cache.put(key, _ocr_artifact(text=f"reading {index}"))
    winner = max(stored, key=lambda key: key.digest)
    found = cache.equivalent_raster(_ocr_key(page_hash="probe-not-stored"))
    assert found is not None
    assert found.text == f"reading {stored.index(winner)}"


def test_equivalent_raster_is_total_and_swallows_faults() -> None:
    """A read fault is a miss here too, and only the counter proves the call happened."""
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(page_hash="page-hash-before"), _ocr_artifact())
    cache.fail_reads = True
    assert cache.equivalent_raster(_ocr_key(page_hash="page-hash-after")) is None
    assert cache.equivalent_raster_calls == 1


def test_equivalent_raster_returns_a_truncated_row_and_leaves_the_decision_to_the_caller() -> None:
    """Same rule as ``get``: the flag survives the round trip and the caller re-decides."""
    cache = InMemoryOcrCache()
    cache.put(_ocr_key(page_hash="page-hash-before"), _ocr_artifact(truncated=True))
    found = cache.equivalent_raster(_ocr_key(page_hash="page-hash-after"))
    assert found is not None
    assert found.truncated is True


def test_the_diagram_cache_declares_no_equivalent_raster() -> None:
    """Its matched set names ``recognizers``, which a ``DiagramCacheKey`` does not have."""
    assert hasattr(OcrCache, "equivalent_raster")
    assert not hasattr(DiagramCache, "equivalent_raster")


# ──────────────────────────── DiagramCache ────────────────────────────


def test_diagram_cache_round_trips_an_artifact_under_its_digest() -> None:
    cache = InMemoryDiagramCache()
    key, artifact = _diagram_key(), _diagram_artifact()
    assert cache.get(key) is None
    cache.put(key, artifact)
    assert cache.get(key) == artifact
    assert cache.get(_diagram_key()) == artifact


@pytest.mark.parametrize(
    "component",
    ["page_hash", "render_digest", "raster_digest", "model_fingerprint", "request_digest"],
)
def test_diagram_cache_misses_when_any_key_component_changes(component: str) -> None:
    cache = InMemoryDiagramCache()
    stored = _diagram_key()
    cache.put(stored, _diagram_artifact())
    changed = stored.model_copy(update={component: "changed"})
    assert changed.digest != stored.digest
    assert cache.get(changed) is None


def test_diagram_put_is_idempotent_and_overwrites() -> None:
    cache = InMemoryDiagramCache()
    key = _diagram_key()
    cache.put(key, _diagram_artifact(mermaid="graph TD;\n  A-->B;"))
    cache.put(key, _diagram_artifact(mermaid="graph TD;\n  B-->C;"))
    hit = cache.get(key)
    assert hit is not None
    assert hit.mermaid == "graph TD;\n  B-->C;"


def test_diagram_cache_stores_a_text_only_page_with_no_mermaid() -> None:
    """A page the model judged to be prose is a cacheable answer, not an absence."""
    cache = InMemoryDiagramCache()
    key = _diagram_key()
    cache.put(key, DiagramArtifact(content_kind=PageContentKind.TEXT, created_at=_AT))
    hit = cache.get(key)
    assert hit is not None
    assert hit.content_kind is PageContentKind.TEXT
    assert hit.mermaid is None


def test_diagram_cache_reads_are_total_and_swallow_faults() -> None:
    cache = InMemoryDiagramCache()
    key = _diagram_key()
    cache.put(key, _diagram_artifact())
    cache.fail_reads = True
    assert cache.get(key) is None
    assert cache.superseded(_diagram_key(request_digest="other")) is None
    assert cache.get_calls == 1
    assert cache.superseded_calls == 1


def test_diagram_cache_writes_are_total_and_swallow_faults() -> None:
    cache = InMemoryDiagramCache(fail_writes=True)
    key = _diagram_key()
    cache.put(key, _diagram_artifact())
    assert cache.put_calls == 1
    assert cache.get(key) is None


def test_diagram_superseded_matches_the_ocr_contract() -> None:
    cache = InMemoryDiagramCache()
    older = _diagram_key(model_fingerprint="model-aaa")
    newer = _diagram_key(model_fingerprint="model-zzz")
    cache.put(older, _diagram_artifact())
    cache.put(newer, _diagram_artifact())
    probe = _diagram_key(model_fingerprint="model-next")
    result = cache.superseded(probe)
    assert result in {older, newer}
    assert result is not None
    assert result.digest == max(older.digest, newer.digest)
    assert cache.superseded(older) is None
    assert cache.superseded(_diagram_key(page_hash="another-page")) is None


# ──────────────────────────── SyncAuditLog ────────────────────────────


def test_append_returns_the_recorded_entry_with_a_sequence_starting_at_one() -> None:
    log = InMemorySyncAuditLog()
    recorded = log.append(_audit_entry())
    assert recorded.sequence == 1
    assert recorded.entry == _audit_entry()


def test_sequences_strictly_increase_across_appends() -> None:
    log = InMemorySyncAuditLog()
    sequences = [log.append(_audit_entry(doc_uuid=f"doc-{n}")).sequence for n in range(5)]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    assert sequences[0] == 1


def test_recent_returns_the_appends_reversed() -> None:
    log = InMemorySyncAuditLog()
    recorded = [log.append(_audit_entry(doc_uuid=f"doc-{n}")) for n in range(4)]
    assert log.recent(limit=10) == list(reversed(recorded))


def test_recent_on_an_empty_log_is_empty() -> None:
    assert InMemorySyncAuditLog().recent(limit=1) == []


@settings(deadline=None)
@given(st.integers(min_value=1, max_value=8), st.integers(min_value=1, max_value=12))
def test_recent_is_the_newest_limit_entries(appends: int, limit: int) -> None:
    log = InMemorySyncAuditLog()
    recorded = [log.append(_audit_entry(doc_uuid=f"doc-{n}")) for n in range(appends)]
    assert log.recent(limit=limit) == list(reversed(recorded))[:limit]


@pytest.mark.parametrize("limit", [0, -1, -1000])
def test_recent_rejects_a_non_positive_limit(limit: int) -> None:
    """The bound is checked in the domain because a slice and a SQL LIMIT disagree here."""
    log = InMemorySyncAuditLog()
    log.append(_audit_entry())
    with pytest.raises(ValueError, match="at least 1"):
        log.recent(limit=limit)


def test_limit_is_validated_before_the_store_is_touched() -> None:
    log = InMemorySyncAuditLog(fail_reads=True)
    with pytest.raises(ValueError, match="at least 1"):
        log.recent(limit=0)


def test_failed_and_partial_operations_are_entries_not_exceptions() -> None:
    log = InMemorySyncAuditLog()
    failure = _audit_entry(outcome=SyncOutcome.FAILED, detail="device dropped at page 400")
    partial = _audit_entry(
        operation=SyncOperation.OCR,
        outcome=SyncOutcome.PARTIAL,
        detail="2 of 5 pages recognized",
    )
    log.append(failure)
    log.append(partial)
    assert [recorded.entry.outcome for recorded in log.recent(limit=2)] == [
        SyncOutcome.PARTIAL,
        SyncOutcome.FAILED,
    ]


def test_append_failure_raises_and_lands_nothing() -> None:
    log = InMemorySyncAuditLog()
    log.append(_audit_entry())
    log.fail_writes = True
    with pytest.raises(AuditWriteFailedError):
        log.append(_audit_entry(doc_uuid="doc-2"))
    assert [recorded.sequence for recorded in log.recent(limit=10)] == [1]


def test_sequences_are_never_reused_after_a_retention_pass() -> None:
    """Gaps are allowed; reuse is not, so no reader may treat sequence as a count."""
    log = InMemorySyncAuditLog()
    handed_out = [log.append(_audit_entry(doc_uuid=f"doc-{n}")).sequence for n in range(3)]
    log.discard_oldest(2)
    handed_out.append(log.append(_audit_entry(doc_uuid="doc-late")).sequence)
    assert handed_out == [1, 2, 3, 4]
    assert len(set(handed_out)) == 4
    assert [recorded.sequence for recorded in log.recent(limit=10)] == [4, 3]


def test_recent_raises_on_an_unreadable_entry_inside_the_window() -> None:
    """A silently short append-only log cannot answer the one question its reader has."""
    log = InMemorySyncAuditLog()
    for n in range(3):
        log.append(_audit_entry(doc_uuid=f"doc-{n}"))
    log.seed_unreadable(1)
    with pytest.raises(StoredRecordUnreadableError):
        log.recent(limit=3)


def test_a_smaller_limit_still_recovers_the_readable_prefix() -> None:
    log = InMemorySyncAuditLog()
    for n in range(3):
        log.append(_audit_entry(doc_uuid=f"doc-{n}"))
    log.seed_unreadable(1)
    assert [recorded.sequence for recorded in log.recent(limit=2)] == [3, 2]


def test_recent_raises_store_unavailable_when_the_log_cannot_be_read() -> None:
    log = InMemorySyncAuditLog(fail_reads=True)
    with pytest.raises(StoreUnavailableError):
        log.recent(limit=1)


def test_the_log_does_not_order_by_the_clock() -> None:
    """Entries written inside one pull share a timestamp; the sequence still orders them."""
    log = InMemorySyncAuditLog()
    first = log.append(_audit_entry(doc_uuid="doc-1"))
    second = log.append(_audit_entry(doc_uuid="doc-2"))
    assert first.entry.occurred_at == second.entry.occurred_at
    assert [recorded.sequence for recorded in log.recent(limit=2)] == [2, 1]
