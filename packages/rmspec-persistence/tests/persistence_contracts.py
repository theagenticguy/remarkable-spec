"""The four port contracts, written once and run against every implementation.

Each class here holds every assertion the port makes, and declares its subject
through a fixture annotated with the *Protocol* rather than with an adapter. Two
files bind them -- ``test_persistence_contract_sqlite.py`` and
``test_persistence_contract_in_memory.py`` -- so one assertion set proves the
SQLite adapter and the in-memory double satisfy the same contract. That is what
makes the doubles usable by every later application-layer test: a double that
drifts from the adapter fails here, not three packages away.

The fixture's Protocol annotation is also a static conformance check. No port is
``runtime_checkable`` and nothing calls ``isinstance``, so returning a concrete
adapter from a function annotated with the Protocol is what makes ``ty`` the gate.
The two cache contracts are stated against :class:`ArtifactCache`, a local
structural view of the identical method set the two cache ports declare, because
one contract body cannot be typed against a union of two ports without lying about
which key type it holds; conformance to the *named* ports is asserted separately in
``test_persistence_port_conformance.py``.

Three seams are declared abstract because they are the same *behaviour* reached by
different means: a corrupt payload is raw SQL in one implementation and a seeded
flag in the other, and a dead store is a closed connection in one and a flag in
the other. Everything else is shared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

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

from rmspec.domain.errors import (
    AuditWriteFailedError,
    StoredRecordUnreadableError,
    StoreUnavailableError,
)
from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    OcrArtifact,
    OcrCacheKey,
    SyncOutcome,
)
from rmspec.persistence.testing import SeededRecordKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog


class ArtifactCache[K, A](Protocol):
    """Structural view of the method set ``OcrCache`` and ``DiagramCache`` share.

    Declared here rather than in the domain on purpose: the domain keeps two named
    Protocols so the two caches stay separately bindable and separately
    null-able. This is a test-side generic so one contract body can be typed
    against whichever pair it was bound to.
    """

    def get(self, key: K, /) -> A | None:
        """Return the artifact stored under this exact key, or ``None``.

        Parameters
        ----------
        key
            The complete cache key.

        Returns
        -------
        A | None
            The cached artifact, or ``None``.
        """
        ...

    def put(self, key: K, artifact: A, /) -> None:
        """Store an artifact under this key.

        Parameters
        ----------
        key
            The complete cache key.
        artifact
            The artifact to cache.
        """
        ...

    def superseded(self, key: K, /) -> K | None:
        """Return a stored key for the same page under other inputs, or ``None``.

        Parameters
        ----------
        key
            The key that missed.

        Returns
        -------
        K | None
            A stored key for the same page with a different digest, or ``None``.
        """
        ...


class DocumentSyncStoreContract:
    """Every assertion ``DocumentSyncStore`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def store(self) -> DocumentSyncStore:
        """Return the subject under test.

        Returns
        -------
        DocumentSyncStore
            An empty store.
        """
        raise NotImplementedError

    def make_unreadable(
        self,
        store: DocumentSyncStore,
        kind: SeededRecordKind,
        doc_uuid: str,
    ) -> None:
        """Make one reader's record for one document unreadable.

        Parameters
        ----------
        store
            The subject.
        kind
            Which reader to break.
        doc_uuid
            The document whose record becomes unreadable.
        """
        raise NotImplementedError

    def break_store(self, store: DocumentSyncStore) -> None:
        """Make every method on ``store`` fail as an unavailable store.

        Parameters
        ----------
        store
            The subject.
        """
        raise NotImplementedError

    # ── recording ───────────────────────────────────────────────────────────

    def test_record_document_then_read_it_back(self, store: DocumentSyncStore) -> None:
        """Record document then read it back."""
        document = a_document("doc-a", name="Notes", page_count=2)
        pages = [a_page("doc-a", "page-1", 0), a_page("doc-a", "page-2", 1)]
        store.record_document(document, pages)
        assert store.get_document("doc-a") == document
        assert store.pages("doc-a") == pages

    def test_replaying_a_record_converges(self, store: DocumentSyncStore) -> None:
        """Replaying a record converges."""
        document = a_document("doc-a")
        pages = [a_page("doc-a", "page-1", 0)]
        store.record_document(document, pages)
        store.record_document(document, pages)
        assert store.list_documents() == [document]
        assert store.pages("doc-a") == pages

    def test_a_repeated_page_uuid_keeps_the_last_occurrence(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """A repeated page uuid keeps the last occurrence."""
        first = a_page("doc-a", "page-1", 0, rm_hash="a" * 64)
        second = a_page("doc-a", "page-1", 3, rm_hash="b" * 64)
        store.record_document(a_document("doc-a"), [first, second])
        assert store.pages("doc-a") == [second]

    def test_the_page_set_is_replaced_not_merged(self, store: DocumentSyncStore) -> None:
        """The page set is replaced not merged."""
        document = a_document("doc-a")
        store.record_document(
            document,
            [a_page("doc-a", "page-1", 0), a_page("doc-a", "page-2", 1)],
        )
        store.record_document(document, [a_page("doc-a", "page-2", 0)])
        assert [page.page_uuid for page in store.pages("doc-a")] == ["page-2"]

    def test_a_departed_page_loses_its_text_and_a_survivor_keeps_it_reindexed(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """A departed page loses its text and a survivor keeps it reindexed."""
        document = a_document("doc-a")
        store.record_document(
            document,
            [a_page("doc-a", "page-1", 0), a_page("doc-a", "page-2", 1)],
        )
        store.record_page_text(a_page_text("doc-a", "page-1", 0, text="departing"))
        store.record_page_text(a_page_text("doc-a", "page-2", 1, text="surviving"))

        store.record_document(document, [a_page("doc-a", "page-2", 0)])

        remaining = store.page_texts("doc-a")
        assert [text.page_uuid for text in remaining] == ["page-2"]
        assert remaining[0].text == "surviving"
        # The survivor moved from index 1 to index 0, so its recorded text moves
        # with it. Left stale, page_texts and pages would disagree about order --
        # and both implementations would be stale identically, so this assertion
        # is the only thing standing between the contract and a divergence it
        # cannot otherwise see.
        assert remaining[0].page_index == 0

    def test_a_survivor_that_did_not_move_keeps_its_text_untouched(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """A survivor that did not move keeps its text untouched."""
        document = a_document("doc-a")
        pages = [a_page("doc-a", "page-1", 0)]
        store.record_document(document, pages)
        text = a_page_text("doc-a", "page-1", 0)
        store.record_page_text(text)
        store.record_document(document, pages)
        assert store.page_texts("doc-a") == [text]

    def test_recording_an_empty_page_set_discards_all_text(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Recording an empty page set discards all text."""
        document = a_document("doc-a")
        store.record_document(document, [a_page("doc-a", "page-1", 0)])
        store.record_page_text(a_page_text("doc-a", "page-1", 0))
        store.record_document(document, [])
        assert store.pages("doc-a") == []
        assert store.page_texts("doc-a") == []
        assert store.get_document("doc-a") == document

    def test_a_page_belonging_to_another_document_is_refused(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """A page belonging to another document is refused, by every binding."""
        # The one state the port used to leave undefined, and the two bindings
        # answered it differently: the double filed the page under the document,
        # the adapter under the page -- so this call used to succeed in memory and
        # raise a foreign-key failure, dressed up as StoreUnavailableError, against
        # SQLite. It is a caller bug in both, so it is a ValueError in both.
        with pytest.raises(ValueError, match="do not belong to doc-a"):
            store.record_document(a_document("doc-a"), [a_page("doc-b", "page-1", 0)])
        assert store.get_document("doc-a") is None
        assert store.pages("doc-a") == []

    def test_a_stray_page_is_refused_before_anything_is_recorded(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """A stray page is refused before anything is recorded, leaving both documents intact."""
        # The destructive shape: recording doc-a with a page of doc-b used to write
        # zero pages for doc-a and overwrite doc-b's page row, leaving doc-b's
        # pages and page_texts disagreeing about index.
        store.record_document(a_document("doc-b", name="Other"), [a_page("doc-b", "page-1", 7)])
        store.record_page_text(a_page_text("doc-b", "page-1", 7, text="doc-b text"))
        with pytest.raises(ValueError, match="page-1"):
            store.record_document(a_document("doc-a"), [a_page("doc-b", "page-1", 0)])
        assert [(page.page_uuid, page.page_index) for page in store.pages("doc-b")] == [
            ("page-1", 7),
        ]
        assert [text.page_index for text in store.page_texts("doc-b")] == [7]
        assert store.pages("doc-a") == []

    # ── reading and ordering ────────────────────────────────────────────────

    def test_get_document_returns_none_for_an_untracked_uuid(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Get document returns none for an untracked uuid."""
        assert store.get_document("never-seen") is None

    def test_pages_is_empty_for_an_untracked_uuid(self, store: DocumentSyncStore) -> None:
        """Pages is empty for an untracked uuid."""
        assert store.pages("never-seen") == []

    def test_page_texts_is_empty_for_an_untracked_uuid(self, store: DocumentSyncStore) -> None:
        """Page texts is empty for an untracked uuid."""
        assert store.page_texts("never-seen") == []

    def test_list_documents_is_case_folded_by_name_then_by_uuid(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """List documents is case folded by name then by uuid."""
        # Deliberately adversarial: mixed case, which the legacy BINARY ordering
        # got wrong; non-ASCII whose casefold is not an ASCII lowering; and a name
        # tie only the uuid can break.
        names = {
            "doc-4": "apple",
            "doc-2": "Banana",
            "doc-1": "ÄPFEL",
            "doc-3": "apple",
            "doc-5": "STRASSE",
            "doc-6": "straße",
        }
        for uuid, name in names.items():
            store.record_document(a_document(uuid, name=name), [])
        listed = store.list_documents()
        assert [document.uuid for document in listed] == sorted(
            names,
            key=lambda uuid: (names[uuid].casefold(), uuid),
        )

    def test_pages_break_an_index_tie_by_page_uuid(self, store: DocumentSyncStore) -> None:
        """Pages break an index tie by page uuid."""
        store.record_document(
            a_document("doc-a"),
            [
                a_page("doc-a", "page-c", 1),
                a_page("doc-a", "page-b", 0),
                a_page("doc-a", "page-a", 1),
            ],
        )
        assert [page.page_uuid for page in store.pages("doc-a")] == [
            "page-b",
            "page-a",
            "page-c",
        ]

    def test_page_texts_use_the_same_order_as_pages(self, store: DocumentSyncStore) -> None:
        """Page texts use the same order as pages."""
        pages = [
            a_page("doc-a", "page-c", 1),
            a_page("doc-a", "page-b", 0),
            a_page("doc-a", "page-a", 1),
        ]
        store.record_document(a_document("doc-a"), pages)
        for page in pages:
            store.record_page_text(a_page_text("doc-a", page.page_uuid, page.page_index))
        assert [text.page_uuid for text in store.page_texts("doc-a")] == [
            page.page_uuid for page in store.pages("doc-a")
        ]

    def test_all_page_texts_orders_by_document_then_index_then_uuid(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """All page texts orders by document then index then uuid."""
        for doc_uuid in ("doc-b", "doc-a"):
            store.record_document(
                a_document(doc_uuid),
                [a_page(doc_uuid, "page-2", 1), a_page(doc_uuid, "page-1", 0)],
            )
            for page_uuid, index in (("page-2", 1), ("page-1", 0)):
                store.record_page_text(a_page_text(doc_uuid, page_uuid, index))
        assert [(text.doc_uuid, text.page_uuid) for text in store.all_page_texts()] == [
            ("doc-a", "page-1"),
            ("doc-a", "page-2"),
            ("doc-b", "page-1"),
            ("doc-b", "page-2"),
        ]

    # ── text writes ─────────────────────────────────────────────────────────

    def test_text_for_an_unrecorded_page_is_a_silent_no_op(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Text for an unrecorded page is a silent no op."""
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        store.record_page_text(a_page_text("doc-a", "page-absent", 0))
        assert store.page_texts("doc-a") == []

    def test_text_for_an_untracked_document_is_a_silent_no_op(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Text for an untracked document is a silent no op."""
        store.record_page_text(a_page_text("doc-absent", "page-1", 0))
        assert store.all_page_texts() == []

    def test_re_recording_text_replaces_rather_than_accumulates(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Re recording text replaces rather than accumulates."""
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        store.record_page_text(a_page_text("doc-a", "page-1", 0, text="first pass"))
        store.record_page_text(a_page_text("doc-a", "page-1", 0, text="second pass"))
        texts = store.page_texts("doc-a")
        assert len(texts) == 1
        assert texts[0].text == "second pass"

    # ── forgetting ──────────────────────────────────────────────────────────

    def test_forgetting_an_untracked_document_is_a_no_op(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Forgetting an untracked document is a no op."""
        store.forget_document("never-seen")
        assert store.list_documents() == []

    def test_forgetting_removes_the_document_its_pages_and_its_text(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """Forgetting removes the document its pages and its text."""
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        store.record_page_text(a_page_text("doc-a", "page-1", 0))
        store.record_document(a_document("doc-b"), [a_page("doc-b", "page-1", 0)])
        store.record_page_text(a_page_text("doc-b", "page-1", 0))

        store.forget_document("doc-a")

        assert store.get_document("doc-a") is None
        assert store.pages("doc-a") == []
        assert store.page_texts("doc-a") == []
        assert [text.doc_uuid for text in store.all_page_texts()] == ["doc-b"]

    # ── failures ────────────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        ("kind", "reader"),
        [
            (SeededRecordKind.DOCUMENT, "get_document"),
            (SeededRecordKind.DOCUMENT, "list_documents"),
            (SeededRecordKind.PAGE, "pages"),
            (SeededRecordKind.PAGE_TEXT, "page_texts"),
            (SeededRecordKind.PAGE_TEXT, "all_page_texts"),
        ],
    )
    def test_an_unreadable_record_raises_for_its_own_reader(
        self,
        store: DocumentSyncStore,
        kind: SeededRecordKind,
        reader: str,
    ) -> None:
        """An unreadable record raises for its own reader."""
        store.record_document(a_document("doc-a"), [a_page("doc-a", "page-1", 0)])
        store.record_page_text(a_page_text("doc-a", "page-1", 0))
        self.make_unreadable(store, kind, "doc-a")
        readers: dict[str, Callable[[], object]] = {
            "get_document": lambda: store.get_document("doc-a"),
            "list_documents": store.list_documents,
            "pages": lambda: store.pages("doc-a"),
            "page_texts": lambda: store.page_texts("doc-a"),
            "all_page_texts": store.all_page_texts,
        }
        with pytest.raises(StoredRecordUnreadableError):
            readers[reader]()

    def test_an_unreadable_page_does_not_break_the_document_reader(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """An unreadable page does not break the document reader."""
        document = a_document("doc-a")
        store.record_document(document, [a_page("doc-a", "page-1", 0)])
        self.make_unreadable(store, SeededRecordKind.PAGE, "doc-a")
        assert store.get_document("doc-a") == document

    def test_a_dead_store_raises_store_unavailable_from_every_method(
        self,
        store: DocumentSyncStore,
    ) -> None:
        """A dead store raises store unavailable from every method."""
        document = a_document("doc-a")
        page = a_page("doc-a", "page-1", 0)
        self.break_store(store)
        calls: list[Callable[[], object]] = [
            lambda: store.record_document(document, [page]),
            lambda: store.get_document("doc-a"),
            store.list_documents,
            lambda: store.pages("doc-a"),
            lambda: store.forget_document("doc-a"),
            lambda: store.record_page_text(a_page_text("doc-a", "page-1", 0)),
            lambda: store.page_texts("doc-a"),
            store.all_page_texts,
        ]
        for call in calls:
            with pytest.raises(StoreUnavailableError) as caught:
                call()
            # Exactly the base class: StoreSchemaMismatchError is a subclass and
            # the CLI maps the two to different exit codes, so a test that
            # accepted either would not notice them swapping.
            assert type(caught.value) is StoreUnavailableError


class ArtifactCacheContract[K, A]:
    """Every assertion ``OcrCache`` and ``DiagramCache`` share.

    Both ports declare the identical three methods, all total. A subclass supplies
    the key and artifact builders for its pair, and the two binding files supply
    the subject and the fault seams.
    """

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def cache(self) -> ArtifactCache[K, A]:
        """Return the subject under test.

        Returns
        -------
        ArtifactCache[K, A]
            An empty cache.
        """
        raise NotImplementedError

    def a_key(self, page_hash: str = "page-hash-a", *, variant: str = "v1") -> K:
        """Return a key of the type this cache stores.

        Parameters
        ----------
        page_hash
            The page's scene-bytes fingerprint.
        variant
            Folded into every non-page component, so the digest changes with it.

        Returns
        -------
        K
            A fully-specified key.
        """
        raise NotImplementedError

    def an_artifact(self) -> A:
        """Return an artifact of the type this cache stores.

        Returns
        -------
        A
            A cacheable artifact.
        """
        raise NotImplementedError

    def another_artifact(self) -> A:
        """Return a second, distinguishable artifact.

        Returns
        -------
        A
            An artifact that compares unequal to :meth:`an_artifact`.
        """
        raise NotImplementedError

    def digest_of(self, key: K) -> str:
        """Return ``key.digest``.

        A method rather than an attribute access, because the contract is generic
        over two key types and their shared surface is not declared anywhere in
        the domain.

        Parameters
        ----------
        key
            The key to inspect.

        Returns
        -------
        str
            The key's digest.
        """
        raise NotImplementedError

    def induce_read_fault(self, cache: ArtifactCache[K, A]) -> None:
        """Make reads on ``cache`` fail at the store level.

        Parameters
        ----------
        cache
            The subject.
        """
        raise NotImplementedError

    def induce_write_fault(self, cache: ArtifactCache[K, A]) -> None:
        """Make writes on ``cache`` fail at the store level.

        Parameters
        ----------
        cache
            The subject.
        """
        raise NotImplementedError

    # ── exact-key lookup ────────────────────────────────────────────────────

    def test_a_cold_cache_misses(self, cache: ArtifactCache[K, A]) -> None:
        """A cold cache misses."""
        assert cache.get(self.a_key()) is None

    def test_put_then_get_round_trips(self, cache: ArtifactCache[K, A]) -> None:
        """Put then get round trips."""
        key = self.a_key()
        artifact = self.an_artifact()
        cache.put(key, artifact)
        assert cache.get(key) == artifact

    def test_put_is_idempotent(self, cache: ArtifactCache[K, A]) -> None:
        """Put is idempotent."""
        key = self.a_key()
        artifact = self.an_artifact()
        cache.put(key, artifact)
        cache.put(key, artifact)
        assert cache.get(key) == artifact

    def test_put_overwrites_an_earlier_entry(self, cache: ArtifactCache[K, A]) -> None:
        """Put overwrites an earlier entry."""
        key = self.a_key()
        cache.put(key, self.an_artifact())
        replacement = self.another_artifact()
        cache.put(key, replacement)
        assert cache.get(key) == replacement

    def test_a_changed_input_is_a_miss(self, cache: ArtifactCache[K, A]) -> None:
        """A changed input is a miss."""
        cache.put(self.a_key(variant="v1"), self.an_artifact())
        assert cache.get(self.a_key(variant="v2")) is None

    def test_a_changed_page_is_a_miss(self, cache: ArtifactCache[K, A]) -> None:
        """A changed page is a miss."""
        cache.put(self.a_key("page-hash-a"), self.an_artifact())
        assert cache.get(self.a_key("page-hash-b")) is None

    # ── provenance diagnosis ────────────────────────────────────────────────

    def test_superseded_names_a_stored_key_for_the_same_page(
        self,
        cache: ArtifactCache[K, A],
    ) -> None:
        """Superseded names a stored key for the same page."""
        stored = self.a_key("page-hash-a", variant="v1")
        cache.put(stored, self.an_artifact())
        asked = self.a_key("page-hash-a", variant="v2")
        found = cache.superseded(asked)
        assert found == stored
        assert found is not None
        assert self.digest_of(found) != self.digest_of(asked)

    def test_superseded_is_none_when_the_key_itself_is_stored(
        self,
        cache: ArtifactCache[K, A],
    ) -> None:
        """Superseded is none when the key itself is stored."""
        key = self.a_key("page-hash-a", variant="v1")
        cache.put(key, self.an_artifact())
        cache.put(self.a_key("page-hash-a", variant="v2"), self.an_artifact())
        assert cache.superseded(key) is None

    def test_superseded_is_none_for_an_unknown_page(self, cache: ArtifactCache[K, A]) -> None:
        """Superseded is none for an unknown page."""
        cache.put(self.a_key("page-hash-a"), self.an_artifact())
        assert cache.superseded(self.a_key("page-hash-b")) is None

    def test_superseded_is_none_on_a_cold_cache(self, cache: ArtifactCache[K, A]) -> None:
        """Superseded is none on a cold cache."""
        assert cache.superseded(self.a_key()) is None

    def test_superseded_prefers_the_greatest_digest(self, cache: ArtifactCache[K, A]) -> None:
        """Superseded prefers the greatest digest."""
        stored = [self.a_key("page-hash-a", variant=f"v{index}") for index in (1, 2, 3)]
        for key in stored:
            cache.put(key, self.an_artifact())
        found = cache.superseded(self.a_key("page-hash-a", variant="v9"))
        assert found is not None
        assert self.digest_of(found) == max(self.digest_of(key) for key in stored)

    # ── totality ────────────────────────────────────────────────────────────

    def test_get_is_total_under_a_read_fault(self, cache: ArtifactCache[K, A]) -> None:
        """Get is total under a read fault."""
        key = self.a_key()
        cache.put(key, self.an_artifact())
        self.induce_read_fault(cache)
        assert cache.get(key) is None

    def test_superseded_is_total_under_a_read_fault(self, cache: ArtifactCache[K, A]) -> None:
        """Superseded is total under a read fault."""
        cache.put(self.a_key(variant="v1"), self.an_artifact())
        self.induce_read_fault(cache)
        assert cache.superseded(self.a_key(variant="v2")) is None

    def test_put_is_total_under_a_write_fault(self, cache: ArtifactCache[K, A]) -> None:
        """Put is total under a write fault."""
        self.induce_write_fault(cache)
        cache.put(self.a_key(), self.an_artifact())


class OcrCacheCases(ArtifactCacheContract[OcrCacheKey, OcrArtifact]):
    """The shared cache contract, bound to the OCR key and artifact types."""

    def a_key(self, page_hash: str = "page-hash-a", *, variant: str = "v1") -> OcrCacheKey:
        """Return an OCR cache key.

        Parameters
        ----------
        page_hash
            The page's scene-bytes fingerprint.
        variant
            Folded into every non-page component.

        Returns
        -------
        OcrCacheKey
            A fully-specified key.
        """
        return an_ocr_key(page_hash, variant=variant)

    def an_artifact(self) -> OcrArtifact:
        """Return a cached transcription.

        Returns
        -------
        OcrArtifact
            A cacheable transcription.
        """
        return an_ocr_artifact()

    def another_artifact(self) -> OcrArtifact:
        """Return a different cached transcription.

        Returns
        -------
        OcrArtifact
            A transcription unequal to :meth:`an_artifact`.
        """
        return an_ocr_artifact(text="a second reading")

    def digest_of(self, key: OcrCacheKey) -> str:
        """Return the key's digest.

        Parameters
        ----------
        key
            The key to inspect.

        Returns
        -------
        str
            ``key.digest``.
        """
        return key.digest

    def test_a_truncated_artifact_round_trips_with_its_flag(
        self,
        cache: ArtifactCache[OcrCacheKey, OcrArtifact],
    ) -> None:
        """A truncated artifact round trips with its flag."""
        key = self.a_key()
        artifact = an_ocr_artifact(text="half a pa", truncated=True)
        cache.put(key, artifact)
        stored = cache.get(key)
        assert stored == artifact
        assert stored is not None
        assert stored.truncated is True

    def test_an_empty_transcription_is_a_hit_not_a_miss(
        self,
        cache: ArtifactCache[OcrCacheKey, OcrArtifact],
    ) -> None:
        """An empty transcription is a hit not a miss."""
        # The storage-layer statement of "no ink is a value, not an absence". A
        # page that was read and held nothing caches an empty string, and `get`
        # must tell that from a genuine miss -- which is why nothing in this
        # package tests a payload for truthiness.
        key = self.a_key("page-hash-empty")
        artifact = an_ocr_artifact(text="")
        cache.put(key, artifact)
        assert cache.get(key) == artifact
        assert cache.get(self.a_key("page-hash-never-stored")) is None


class DiagramCacheCases(ArtifactCacheContract[DiagramCacheKey, DiagramArtifact]):
    """The shared cache contract, bound to the diagram key and artifact types."""

    def a_key(self, page_hash: str = "page-hash-a", *, variant: str = "v1") -> DiagramCacheKey:
        """Return a diagram cache key.

        Parameters
        ----------
        page_hash
            The page's scene-bytes fingerprint.
        variant
            Folded into every non-page component.

        Returns
        -------
        DiagramCacheKey
            A fully-specified key.
        """
        return a_diagram_key(page_hash, variant=variant)

    def an_artifact(self) -> DiagramArtifact:
        """Return a cached diagram extraction.

        Returns
        -------
        DiagramArtifact
            A cacheable extraction.
        """
        return a_diagram_artifact()

    def another_artifact(self) -> DiagramArtifact:
        """Return a different cached diagram extraction.

        Returns
        -------
        DiagramArtifact
            An extraction unequal to :meth:`an_artifact`.
        """
        return a_diagram_artifact(created_at=a_diagram_artifact().created_at.replace(hour=13))

    def digest_of(self, key: DiagramCacheKey) -> str:
        """Return the key's digest.

        Parameters
        ----------
        key
            The key to inspect.

        Returns
        -------
        str
            ``key.digest``.
        """
        return key.digest

    def test_the_mermaid_body_survives_the_round_trip(
        self,
        cache: ArtifactCache[DiagramCacheKey, DiagramArtifact],
    ) -> None:
        """The mermaid body survives the round trip."""
        key = self.a_key()
        artifact = a_diagram_artifact()
        cache.put(key, artifact)
        stored = cache.get(key)
        assert stored is not None
        assert stored.mermaid == artifact.mermaid


class SyncAuditLogContract:
    """Every assertion ``SyncAuditLog`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def log(self) -> SyncAuditLog:
        """Return the subject under test.

        Returns
        -------
        SyncAuditLog
            An empty log.
        """
        raise NotImplementedError

    def make_unreadable(self, log: SyncAuditLog) -> None:
        """Make a stored entry impossible to reconstruct.

        Parameters
        ----------
        log
            The subject.
        """
        raise NotImplementedError

    def break_log(self, log: SyncAuditLog) -> None:
        """Make appends and reads on ``log`` fail at the store level.

        Parameters
        ----------
        log
            The subject.
        """
        raise NotImplementedError

    def retain_newest(self, log: SyncAuditLog, keep: int) -> None:
        """Drop all but the newest ``keep`` entries.

        Parameters
        ----------
        log
            The subject.
        keep
            How many entries to retain.
        """
        raise NotImplementedError

    # ── sequencing ──────────────────────────────────────────────────────────

    def test_the_first_sequence_is_one(self, log: SyncAuditLog) -> None:
        """The first sequence is one."""
        assert log.append(an_audit_entry()).sequence == 1

    def test_sequences_strictly_increase(self, log: SyncAuditLog) -> None:
        """Sequences strictly increase."""
        sequences = [log.append(an_audit_entry()).sequence for _ in range(5)]
        assert sequences == [1, 2, 3, 4, 5]

    def test_append_returns_the_entry_it_was_given(self, log: SyncAuditLog) -> None:
        """Append returns the entry it was given."""
        entry = an_audit_entry(outcome=SyncOutcome.PARTIAL, detail="380 of 400 documents")
        assert log.append(entry).entry == entry

    def test_recent_is_the_appends_reversed(self, log: SyncAuditLog) -> None:
        """Recent is the appends reversed."""
        # No clock anywhere in this assertion: every entry shares one frozen
        # occurred_at, so only the store-assigned sequence can order them.
        appended = [log.append(an_audit_entry(pages_affected=index)) for index in range(4)]
        assert log.recent(limit=10) == list(reversed(appended))

    def test_recent_returns_everything_when_the_limit_exceeds_the_history(
        self,
        log: SyncAuditLog,
    ) -> None:
        """Recent returns everything when the limit exceeds the history."""
        appended = [log.append(an_audit_entry()) for _ in range(3)]
        assert len(log.recent(limit=99)) == len(appended)

    def test_recent_truncates_to_the_limit(self, log: SyncAuditLog) -> None:
        """Recent truncates to the limit."""
        appended = [log.append(an_audit_entry(pages_affected=index)) for index in range(4)]
        assert log.recent(limit=2) == [appended[3], appended[2]]

    def test_recent_of_an_empty_log_is_empty(self, log: SyncAuditLog) -> None:
        """Recent of an empty log is empty."""
        assert log.recent(limit=5) == []

    def test_a_sequence_is_never_reused_after_a_trim(self, log: SyncAuditLog) -> None:
        """A sequence is never reused after a trim."""
        # The exact failure a rowid-allocated sequence produces: delete the
        # highest rows and the next append hands out a retired number.
        handed_out = [log.append(an_audit_entry()).sequence for _ in range(5)]
        self.retain_newest(log, 2)
        assert log.append(an_audit_entry()).sequence > max(handed_out)

    def test_a_trim_leaves_a_gap_rather_than_renumbering(self, log: SyncAuditLog) -> None:
        """A trim leaves a gap rather than renumbering."""
        for _ in range(5):
            log.append(an_audit_entry())
        self.retain_newest(log, 2)
        assert [recorded.sequence for recorded in log.recent(limit=10)] == [5, 4]

    # ── failures ────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("limit", [0, -1, -50])
    def test_a_non_positive_limit_raises_before_the_store_is_touched(
        self,
        log: SyncAuditLog,
        limit: int,
    ) -> None:
        """A non positive limit raises before the store is touched."""
        # Asserted on a log whose store is already dead, which is what proves the
        # check runs first: a store-side LIMIT and a list slice disagree about a
        # negative bound, so the caller must not have to know which it holds.
        self.break_log(log)
        with pytest.raises(ValueError, match="at least 1"):
            log.recent(limit=limit)

    def test_a_failed_append_raises_audit_write_failed(self, log: SyncAuditLog) -> None:
        """A failed append raises audit write failed."""
        self.break_log(log)
        with pytest.raises(AuditWriteFailedError) as caught:
            log.append(an_audit_entry())
        # Specifically not StoreUnavailableError: the operation the entry
        # describes may well have succeeded, so the caller degrades rather than
        # failing the work it just did.
        assert not isinstance(caught.value, StoreUnavailableError)

    def test_an_unreadable_entry_raises_rather_than_shortening_the_list(
        self,
        log: SyncAuditLog,
    ) -> None:
        """An unreadable entry raises rather than shortening the list."""
        log.append(an_audit_entry())
        self.make_unreadable(log)
        with pytest.raises(StoredRecordUnreadableError):
            log.recent(limit=10)
