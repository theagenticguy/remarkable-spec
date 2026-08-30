"""Conformance tests for the formats ports: the repository, the page codec, the appender.

The module under test is Protocols plus one value object and one sentinel, so the behaviour
under test is the *contract*: what a conforming implementation must return, what it must
raise, and what it must never let escape. Fakes stand in for adapters -- one holding prebuilt
aggregates, one composing :class:`PageCodec` the way a real repository adapter does, one
appending a marker where an adapter would encode blocks -- and the tests are written against
the Protocol, so binding a different adapter later re-uses them unchanged.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from rmspec.domain.errors import (
    CorruptPageData,
    DocumentNotFound,
    DocumentStoreUnavailable,
    FormatError,
    MalformedDocument,
    PageNotFound,
    RmspecError,
    SceneRewriteUnsafe,
    UnsupportedPageFormat,
    UsageError,
)
from rmspec.domain.models import (
    Document,
    DocumentId,
    DocumentKind,
    DocumentMetadata,
    DocumentSummary,
    Layer,
    Page,
    PageContent,
    PageDefect,
    PageDefectCode,
    PageId,
    PenColor,
    PenType,
    Point,
    Stroke,
)
from rmspec.domain.ports import formats as formats_module
from rmspec.domain.ports.formats import (
    ABSENT_ARTIFACT_FINGERPRINT,
    DocumentRepository,
    PageCodec,
    SceneAppender,
    SceneEdit,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from typing import Literal

# --------------------------------------------------------------------------------------
# fixtures: prebuilt domain models, so no test needs a byte fixture or a file on disk
# --------------------------------------------------------------------------------------

_STORE = "fake-xochitl"

_DOC = DocumentId(uuid="11111111-1111-4111-8111-111111111111")
_OTHER_DOC = DocumentId(uuid="22222222-2222-4222-8222-222222222222")
_MALFORMED_DOC = DocumentId(uuid="33333333-3333-4333-8333-333333333333")
_UNKNOWN_DOC = DocumentId(uuid="99999999-9999-4999-8999-999999999999")

_INK = PageId(uuid="page-with-ink")
_ABSENT = PageId(uuid="page-never-drawn-on")
_UNDECODABLE = PageId(uuid="page-that-will-not-decode")
_DEGRADED = PageId(uuid="page-decoded-with-substitutions")
_UNCLAIMED = PageId(uuid="page-this-document-does-not-claim")

_METADATA = DocumentMetadata(visible_name="Design notes", kind=DocumentKind.DOCUMENT)


def _content(*, stroke_count: int = 1, defects: tuple[PageDefect, ...] = ()) -> PageContent:
    """Build page content with the requested number of one-point strokes."""
    strokes = tuple(
        Stroke(
            pen=PenType.FINELINER_1,
            color=PenColor.BLACK,
            thickness_scale=2.0,
            points=(Point(x=float(index), y=float(index)),),
        )
        for index in range(stroke_count)
    )
    return PageContent(layers=(Layer(name="Layer 1", strokes=strokes),), defects=defects)


def _ink_page(index: int = 0) -> Page:
    """Return a page whose artifact was present and decoded."""
    return Page(page_id=_INK, index=index, template_name="Blank", content=_content())


def _absent_page(index: int = 1) -> Page:
    """Return a page the document claims and stores no scene artifact for."""
    return Page(
        page_id=_ABSENT,
        index=index,
        template_name="Blank",
        pdf_page_index=index,
        defects=(PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail="no scene artifact"),),
    )


def _undecodable_page(index: int = 2) -> Page:
    """Return a page whose artifact was present and would not decode."""
    return Page(
        page_id=_UNDECODABLE,
        index=index,
        defects=(PageDefect(code=PageDefectCode.CONTENT_UNDECODABLE, detail="truncated"),),
    )


def _degraded_page(index: int = 3) -> Page:
    """Return a page that decoded, with substitutions recorded as values on the content."""
    return Page(
        page_id=_DEGRADED,
        index=index,
        content=_content(
            defects=(
                PageDefect(code=PageDefectCode.UNKNOWN_PEN_SUBSTITUTED, detail="pen 42 -> pencil"),
            )
        ),
    )


def _document() -> Document:
    """Build the four-page document every repository test is built over."""
    return Document(
        doc_id=_DOC,
        metadata=_METADATA,
        pages=(_ink_page(), _absent_page(), _undecodable_page(), _degraded_page()),
    )


def _other_document() -> Document:
    """Build a second, single-page document, so listings have more than one entry."""
    return Document(
        doc_id=_OTHER_DOC,
        metadata=DocumentMetadata(visible_name="Scratch", kind=DocumentKind.DOCUMENT),
        pages=(_ink_page(),),
    )


# --------------------------------------------------------------------------------------
# fake 1: prebuilt aggregates in, domain models out
# --------------------------------------------------------------------------------------


class _FakeDocumentRepository:
    """A ``DocumentRepository`` that serves prebuilt aggregates.

    Carries the three knobs the port's own notes call for: contentless pages come from the
    aggregates themselves, fingerprints are held independently of page content, and an
    ``unavailable`` switch makes ``DocumentStoreUnavailable`` reachable without deleting
    fixtures mid-test. Documents listed in ``malformed`` exist on the store but their
    document-level metadata will not decode.
    """

    def __init__(
        self,
        documents: Iterable[Document] = (),
        *,
        fingerprints: Mapping[PageId, str] | None = None,
        malformed: Iterable[DocumentId] = (),
        unavailable: bool = False,
    ) -> None:
        """Register the aggregates, fingerprint overrides and failure switches."""
        self._documents = {document.doc_id: document for document in documents}
        self._fingerprints = dict(fingerprints or {})
        self._malformed = frozenset(malformed)
        self.unavailable = unavailable

    def _require(self, doc_id: DocumentId) -> Document:
        """Resolve a document identity, raising the port's documented failures in order."""
        if self.unavailable:
            raise DocumentStoreUnavailable(store=_STORE, detail="switched off for this test")
        if doc_id in self._malformed:
            raise MalformedDocument(
                document_uuid=doc_id.uuid, artifact=".content", detail="not json"
            )
        document = self._documents.get(doc_id)
        if document is None:
            raise DocumentNotFound(query=doc_id.uuid, store=_STORE)
        return document

    @staticmethod
    def _claimed(document: Document, page_id: PageId) -> Page:
        """Return the claimed page, or raise ``PageNotFound`` if the document claims none."""
        for page in document.pages:
            if page.page_id == page_id:
                return page
        raise PageNotFound(
            document_uuid=document.doc_id.uuid, page=page_id.uuid, page_count=len(document.pages)
        )

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """List every document whose metadata decodes, as a materialised tuple."""
        if self.unavailable:
            raise DocumentStoreUnavailable(store=_STORE, detail="switched off for this test")
        return tuple(
            document.summary
            for doc_id, document in self._documents.items()
            if doc_id not in self._malformed
        )

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Summarise one document without decoding pages."""
        return self._require(doc_id).summary

    def load(self, doc_id: DocumentId, /) -> Document:
        """Load one whole document."""
        return self._require(doc_id)

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Load exactly the page ``load`` places at this identity."""
        return self._claimed(self._require(doc_id), page_id)

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Fingerprint one page's stored bytes."""
        page = self._claimed(self._require(doc_id), page_id)
        return self._fingerprint_of(page)

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Fingerprint every claimed page in document order."""
        document = self._require(doc_id)
        return {page.page_id: self._fingerprint_of(page) for page in document.pages}

    def _fingerprint_of(self, page: Page) -> str:
        """Sentinel for a page with no artifact, an override if given, else a content digest."""
        if any(defect.code is PageDefectCode.ARTIFACT_ABSENT for defect in page.defects):
            return ABSENT_ARTIFACT_FINGERPRINT
        override = self._fingerprints.get(page.page_id)
        if override is not None:
            return override
        return hashlib.sha256(page.ref.encode()).hexdigest()


# --------------------------------------------------------------------------------------
# fake 2: bytes plus a codec, the way a real repository adapter composes the two ports
# --------------------------------------------------------------------------------------


class _FakePageCodec:
    """A ``PageCodec`` deriving content from the bytes, or failing on demand.

    The canned content is a function of ``raw`` rather than a constant, so "the same bytes
    decode the same way" and "the ref cannot change what is decoded" are not vacuous.
    """

    def __init__(
        self,
        *,
        fails_with: Literal["corrupt", "unsupported"] | None = None,
        observed_version: str = "7.1",
    ) -> None:
        """Choose which documented failure, if any, this codec raises."""
        self._fails_with = fails_with
        self._observed_version = observed_version
        self.decoded_refs: list[str] = []

    def decode_page(self, raw: bytes, page_ref: str, /) -> PageContent:
        """Decode scene bytes, or raise the configured domain error against ``page_ref``."""
        self.decoded_refs.append(page_ref)
        if self._fails_with == "unsupported":
            raise UnsupportedPageFormat(
                page_uuid=page_ref,
                observed_version=self._observed_version,
                supported_versions=("6",),
            )
        if self._fails_with == "corrupt":
            raise CorruptPageData(page_uuid=page_ref, detail="truncated", offset=len(raw))
        defects = (
            (PageDefect(code=PageDefectCode.UNKNOWN_COLOR_SUBSTITUTED, detail="colour 31"),)
            if b"?" in raw
            else ()
        )
        return _content(stroke_count=len(raw), defects=defects)


class _FakeSceneAppender:
    """A ``SceneAppender`` that concatenates a marker rather than encoding anything.

    Enough to exercise the contract without a parser: it holds the port's three refusals
    behind explicit switches, and its bytes make the input a strict prefix of the result, so
    the clause an implementation is judged on is observable in a fake as well as in an
    adapter.
    """

    def __init__(
        self,
        *,
        fails_with: Literal["corrupt", "unsafe"] | None = None,
        author_id: int = 2,
        layer_index: int = 0,
    ) -> None:
        """Choose which documented failure, if any, this appender raises."""
        self._fails_with = fails_with
        self._author_id = author_id
        self._layer_index = layer_index
        self.appended_refs: list[str] = []

    def append_strokes(
        self, raw: bytes, page_ref: str, /, *, strokes: tuple[Stroke, ...]
    ) -> SceneEdit:
        """Append a marker per stroke, or raise the configured domain error."""
        self.appended_refs.append(page_ref)
        if not strokes:
            raise UsageError(subject=f"no strokes for {page_ref}", requirement="at least one")
        if self._fails_with == "corrupt":
            raise CorruptPageData(page_uuid=page_ref, detail="truncated", offset=len(raw))
        if self._fails_with == "unsafe":
            raise SceneRewriteUnsafe(page_uuid=page_ref, detail="this build cannot write it")
        return SceneEdit(
            scene=raw + b"|" * len(strokes),
            author_id=self._author_id,
            layer_index=self._layer_index,
        )


class _CodecBackedRepository:
    """A ``DocumentRepository`` that decodes stored bytes through a :class:`PageCodec`.

    Exists to pin the translation the port documents: ``CorruptPageData`` and
    ``UnsupportedPageFormat`` become a ``CONTENT_UNDECODABLE`` defect on a contentless page,
    and neither error leaves ``load`` or ``load_page``. Artifacts mapped to ``None`` are
    pages the document claims and stores nothing for.
    """

    def __init__(self, artifacts: Mapping[PageId, bytes | None], codec: PageCodec) -> None:
        """Register the stored artifacts in document order and the codec to decode them."""
        self._artifacts = dict(artifacts)
        self._codec = codec

    def _page(self, page_id: PageId, index: int) -> Page:
        """Assemble one page from its stored bytes, defecting rather than raising."""
        raw = self._artifacts[page_id]
        if raw is None:
            return Page(
                page_id=page_id,
                index=index,
                template_name="Blank",
                defects=(
                    PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail="no scene artifact"),
                ),
            )
        try:
            content = self._codec.decode_page(raw, page_id.uuid)
        except (CorruptPageData, UnsupportedPageFormat) as exc:
            return Page(
                page_id=page_id,
                index=index,
                defects=(PageDefect(code=PageDefectCode.CONTENT_UNDECODABLE, detail=exc.message),),
            )
        return Page(page_id=page_id, index=index, content=content)

    def _pages(self) -> tuple[Page, ...]:
        """Assemble every claimed page in document order."""
        return tuple(self._page(page_id, index) for index, page_id in enumerate(self._artifacts))

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """List the single document this fake holds."""
        return (self.summary(_DOC),)

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Summarise the document without decoding any page."""
        if doc_id != _DOC:
            raise DocumentNotFound(query=doc_id.uuid, store=_STORE)
        return DocumentSummary(doc_id=_DOC, metadata=_METADATA, pages=tuple(self._artifacts))

    def load(self, doc_id: DocumentId, /) -> Document:
        """Load the document with every page decoded."""
        if doc_id != _DOC:
            raise DocumentNotFound(query=doc_id.uuid, store=_STORE)
        return Document(doc_id=_DOC, metadata=_METADATA, pages=self._pages())

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Load one page, decoding only its own bytes."""
        if doc_id != _DOC:
            raise DocumentNotFound(query=doc_id.uuid, store=_STORE)
        for index, claimed in enumerate(self._artifacts):
            if claimed == page_id:
                return self._page(page_id, index)
        raise PageNotFound(
            document_uuid=doc_id.uuid, page=page_id.uuid, page_count=len(self._artifacts)
        )

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Digest the stored bytes as read, before any decoding."""
        if doc_id != _DOC:
            raise DocumentNotFound(query=doc_id.uuid, store=_STORE)
        if page_id not in self._artifacts:
            raise PageNotFound(
                document_uuid=doc_id.uuid, page=page_id.uuid, page_count=len(self._artifacts)
            )
        raw = self._artifacts[page_id]
        if raw is None:
            return ABSENT_ARTIFACT_FINGERPRINT
        return hashlib.sha256(raw).hexdigest()

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Digest every claimed page in one pass."""
        return {page_id: self.page_fingerprint(doc_id, page_id) for page_id in self._artifacts}


def _as_repository(candidate: DocumentRepository, /) -> DocumentRepository:
    """Accept anything structurally satisfying :class:`DocumentRepository`."""
    return candidate


def _as_codec(candidate: PageCodec, /) -> PageCodec:
    """Accept anything structurally satisfying :class:`PageCodec`."""
    return candidate


def _as_appender(candidate: SceneAppender, /) -> SceneAppender:
    """Accept anything structurally satisfying :class:`SceneAppender`."""
    return candidate


def _stroke() -> Stroke:
    """Return one stroke, the unit of ink the appender takes."""
    return Stroke(
        pen=PenType.FINELINER_1,
        color=PenColor.BLACK,
        thickness_scale=2.0,
        points=(Point(x=-10.0, y=20.0), Point(x=10.0, y=20.0)),
    )


def _repository(
    *,
    fingerprints: Mapping[PageId, str] | None = None,
    unavailable: bool = False,
) -> DocumentRepository:
    """Build a repository over the two fixture documents, typed as the port."""
    return _as_repository(
        _FakeDocumentRepository(
            (_document(), _other_document()),
            fingerprints=fingerprints,
            malformed=(_MALFORMED_DOC,),
            unavailable=unavailable,
        )
    )


def _received(value: object) -> object:
    """Return a value at ``object`` altitude, so a stub's ``None`` is assertable."""
    return value


# --------------------------------------------------------------------------------------
# the absent-artifact sentinel
# --------------------------------------------------------------------------------------


def test_absent_artifact_fingerprint_is_a_non_empty_token():
    assert isinstance(ABSENT_ARTIFACT_FINGERPRINT, str)
    assert ABSENT_ARTIFACT_FINGERPRINT.strip() == ABSENT_ARTIFACT_FINGERPRINT
    assert ABSENT_ARTIFACT_FINGERPRINT


def test_absent_artifact_fingerprint_is_not_shaped_like_a_hex_digest():
    # "Deliberately not a hex digest": a caller may tell "nothing was drawn" from
    # "these bytes hashed to this", which only holds if the two shapes cannot coincide.
    assert not all(character in "0123456789abcdef" for character in ABSENT_ARTIFACT_FINGERPRINT)


@given(raw=st.binary(max_size=64))
def test_no_content_digest_can_collide_with_the_absent_sentinel(raw: bytes):
    assert hashlib.sha256(raw).hexdigest() != ABSENT_ARTIFACT_FINGERPRINT


def test_module_publishes_exactly_the_three_ports_the_receipt_and_the_sentinel():
    assert formats_module.__all__ == [
        "ABSENT_ARTIFACT_FINGERPRINT",
        "DocumentRepository",
        "PageCodec",
        "SceneAppender",
        "SceneEdit",
    ]
    assert sorted(formats_module.__all__) == list(formats_module.__all__)
    for name in formats_module.__all__:
        assert getattr(formats_module, name, None) is not None


# --------------------------------------------------------------------------------------
# the shape of the contract
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("port", [DocumentRepository, PageCodec, SceneAppender])
def test_ports_are_structural_contracts_not_runtime_checkable_classes(port: type):
    # A caller must not gate on nominal typing: these are static contracts, so isinstance
    # and issubclass are refused rather than silently answering "no" for a conforming fake.
    with pytest.raises(TypeError):
        isinstance(_FakePageCodec(), port)
    with pytest.raises(TypeError):
        issubclass(_FakePageCodec, port)


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (DocumentRepository.list_documents, ()),
        (DocumentRepository.summary, ("doc_id",)),
        (DocumentRepository.load, ("doc_id",)),
        (DocumentRepository.load_page, ("doc_id", "page_id")),
        (DocumentRepository.page_fingerprint, ("doc_id", "page_id")),
        (DocumentRepository.page_fingerprints, ("doc_id",)),
        (PageCodec.decode_page, ("raw", "page_ref")),
    ],
)
def test_every_port_argument_is_positional_only_and_has_no_default(
    method: Callable[..., object], expected: tuple[str, ...]
):
    # Nothing in these ports may be invented by an implementation: page_ref in particular
    # is documented as required and without a default, since both codec errors demand it.
    parameters = list(inspect.signature(method).parameters.values())
    assert parameters[0].name == "self"
    assert tuple(parameter.name for parameter in parameters[1:]) == expected
    for parameter in parameters[1:]:
        assert parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_the_appender_takes_its_bytes_positionally_and_its_ink_by_keyword():
    # `raw` and `page_ref` mirror `PageCodec.decode_page`, so the two halves of the same seam
    # are called the same way; `strokes` is keyword-only because at a call site three bare
    # positionals would not say which is the label and which is the ink. Nothing has a
    # default: an implementation must not be able to invent either the label or the ink.
    parameters = inspect.signature(SceneAppender.append_strokes).parameters
    assert list(parameters) == ["self", "raw", "page_ref", "strokes"]
    for name in ("raw", "page_ref"):
        assert parameters[name].kind is inspect.Parameter.POSITIONAL_ONLY
    assert parameters["strokes"].kind is inspect.Parameter.KEYWORD_ONLY
    for name in ("raw", "page_ref", "strokes"):
        assert parameters[name].default is inspect.Parameter.empty


def test_protocol_bodies_are_placeholders_and_never_usable_defaults():
    # Called through the Protocol, every method returns None rather than a plausible value:
    # an incomplete implementation is a composition-time defect, not a runtime null object.
    repo = _FakeDocumentRepository()
    codec = _FakePageCodec()
    appender = _FakeSceneAppender()
    stubs = (
        DocumentRepository.list_documents(repo),
        DocumentRepository.summary(repo, _DOC),
        DocumentRepository.load(repo, _DOC),
        DocumentRepository.load_page(repo, _DOC, _INK),
        DocumentRepository.page_fingerprint(repo, _DOC, _INK),
        DocumentRepository.page_fingerprints(repo, _DOC),
        PageCodec.decode_page(codec, b"", "ref"),
        SceneAppender.append_strokes(appender, b"", "ref", strokes=(_stroke(),)),
    )
    assert [_received(stub) for stub in stubs] == [None] * 8
    assert codec.decoded_refs == []
    assert appender.appended_refs == []


# --------------------------------------------------------------------------------------
# the repository: listing documents and summarising one
# --------------------------------------------------------------------------------------


def test_list_documents_returns_a_materialised_tuple_of_summaries():
    listed = _repository().list_documents()
    assert isinstance(listed, tuple)
    assert [summary.doc_id for summary in listed] == [_DOC, _OTHER_DOC]
    assert [summary.page_count for summary in listed] == [4, 1]


def test_list_documents_of_an_empty_store_is_empty_not_an_error():
    assert _as_repository(_FakeDocumentRepository()).list_documents() == ()


def test_list_documents_omits_a_document_whose_metadata_will_not_decode():
    # list_documents raises only DocumentStoreUnavailable, so an undecodable entry cannot
    # take the whole listing down with it.
    listed = _repository().list_documents()
    assert _MALFORMED_DOC not in [summary.doc_id for summary in listed]


def test_summary_carries_page_identities_in_document_order():
    summary = _repository().summary(_DOC)
    assert summary.pages == (_INK, _ABSENT, _UNDECODABLE, _DEGRADED)
    assert summary.page_count == 4


def test_summary_is_the_same_summary_the_listing_carries():
    repository = _repository()
    from_listing = next(
        summary for summary in repository.list_documents() if summary.doc_id == _DOC
    )
    assert repository.summary(_DOC) == from_listing


def test_load_returns_pages_in_document_order_agreeing_with_the_summary():
    repository = _repository()
    document = repository.load(_DOC)
    assert document.summary == repository.summary(_DOC)
    assert tuple(page.index for page in document.pages) == (0, 1, 2, 3)


# --------------------------------------------------------------------------------------
# the repository: the three page states, and one-page loading agreeing with the whole
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("page_id", [_INK, _ABSENT, _UNDECODABLE, _DEGRADED])
def test_load_page_returns_exactly_the_page_load_places_at_that_identity(page_id: PageId):
    repository = _repository()
    from_aggregate = next(page for page in repository.load(_DOC).pages if page.page_id == page_id)
    assert repository.load_page(_DOC, page_id) == from_aggregate


def test_a_claimed_page_with_no_stored_artifact_is_contentless_not_empty():
    page = _repository().load_page(_DOC, _ABSENT)
    assert page.content is None
    assert page.is_readable is False
    assert page.stroke_count == 0
    assert [defect.code for defect in page.all_defects] == [PageDefectCode.ARTIFACT_ABSENT]
    # The template is a fact only the store holds, so it survives the missing artifact.
    assert page.template_name == "Blank"


def test_a_present_but_undecodable_artifact_is_contentless_with_its_own_defect():
    page = _repository().load_page(_DOC, _UNDECODABLE)
    assert page.content is None
    assert [defect.code for defect in page.all_defects] == [PageDefectCode.CONTENT_UNDECODABLE]


def test_the_two_contentless_states_are_distinguishable_from_each_other():
    repository = _repository()
    absent = repository.load_page(_DOC, _ABSENT)
    undecodable = repository.load_page(_DOC, _UNDECODABLE)
    assert absent.is_readable is undecodable.is_readable is False
    assert {defect.code for defect in absent.defects} != {
        defect.code for defect in undecodable.defects
    }


def test_decode_degradations_arrive_as_values_on_a_readable_page():
    page = _repository().load_page(_DOC, _DEGRADED)
    assert page.is_readable is True
    assert page.defects == ()
    assert [defect.code for defect in page.all_defects] == [PageDefectCode.UNKNOWN_PEN_SUBSTITUTED]


def test_page_not_found_is_raised_only_for_a_page_the_document_does_not_claim():
    repository = _repository()
    with pytest.raises(PageNotFound) as caught:
        repository.load_page(_DOC, _UNCLAIMED)
    assert caught.value.document_uuid == _DOC.uuid
    assert caught.value.page == _UNCLAIMED.uuid
    # The surviving meaning of page_count: how many pages the document actually claims.
    assert caught.value.page_count == repository.summary(_DOC).page_count


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda repo: repo.summary(_UNKNOWN_DOC), DocumentNotFound),
        (lambda repo: repo.load(_UNKNOWN_DOC), DocumentNotFound),
        (lambda repo: repo.load_page(_UNKNOWN_DOC, _INK), DocumentNotFound),
        (lambda repo: repo.page_fingerprint(_UNKNOWN_DOC, _INK), DocumentNotFound),
        (lambda repo: repo.page_fingerprints(_UNKNOWN_DOC), DocumentNotFound),
        (lambda repo: repo.summary(_MALFORMED_DOC), MalformedDocument),
        (lambda repo: repo.load(_MALFORMED_DOC), MalformedDocument),
        (lambda repo: repo.load_page(_MALFORMED_DOC, _INK), MalformedDocument),
        (lambda repo: repo.page_fingerprint(_MALFORMED_DOC, _INK), MalformedDocument),
        (lambda repo: repo.page_fingerprints(_MALFORMED_DOC), MalformedDocument),
    ],
)
def test_every_identity_addressed_method_reports_the_same_document_failures(
    call: Callable[[DocumentRepository], object], expected: type[RmspecError]
):
    with pytest.raises(expected):
        call(_repository())


@pytest.mark.parametrize(
    "call",
    [
        lambda repo: repo.list_documents(),
        lambda repo: repo.summary(_DOC),
        lambda repo: repo.load(_DOC),
        lambda repo: repo.load_page(_DOC, _INK),
        lambda repo: repo.page_fingerprint(_DOC, _INK),
        lambda repo: repo.page_fingerprints(_DOC),
    ],
)
def test_an_unreachable_store_fails_every_method_including_the_listing(
    call: Callable[[DocumentRepository], object],
):
    with pytest.raises(DocumentStoreUnavailable) as caught:
        call(_repository(unavailable=True))
    assert caught.value.store == _STORE


# --------------------------------------------------------------------------------------
# the repository: fingerprinting pages for cache invalidation
# --------------------------------------------------------------------------------------


def test_a_page_with_no_artifact_still_has_a_cache_key():
    assert _repository().page_fingerprint(_DOC, _ABSENT) == ABSENT_ARTIFACT_FINGERPRINT


@pytest.mark.parametrize("page_id", [_INK, _UNDECODABLE, _DEGRADED])
def test_a_page_with_an_artifact_fingerprints_to_a_stable_non_empty_token(page_id: PageId):
    repository = _repository()
    first = repository.page_fingerprint(_DOC, page_id)
    assert first
    assert first != ABSENT_ARTIFACT_FINGERPRINT
    assert repository.page_fingerprint(_DOC, page_id) == first


def test_fingerprint_page_not_found_is_the_unclaimed_page_never_the_absent_artifact():
    repository = _repository()
    assert repository.page_fingerprint(_DOC, _ABSENT) == ABSENT_ARTIFACT_FINGERPRINT
    with pytest.raises(PageNotFound):
        repository.page_fingerprint(_DOC, _UNCLAIMED)


def test_page_fingerprints_covers_every_claimed_page_in_document_order():
    repository = _repository()
    fingerprints = repository.page_fingerprints(_DOC)
    assert tuple(fingerprints) == repository.summary(_DOC).pages
    assert fingerprints[_ABSENT] == ABSENT_ARTIFACT_FINGERPRINT


def test_page_fingerprints_agrees_with_page_fingerprint_for_every_page():
    repository = _repository()
    batched = repository.page_fingerprints(_DOC)
    assert batched == {page_id: repository.page_fingerprint(_DOC, page_id) for page_id in batched}


@given(
    stored=st.text(min_size=1, max_size=32).filter(lambda t: t != ABSENT_ARTIFACT_FINGERPRINT),
    rewritten=st.text(min_size=1, max_size=32).filter(lambda t: t != ABSENT_ARTIFACT_FINGERPRINT),
)
def test_a_fingerprint_tracks_the_stored_bytes_and_not_the_decoded_content(
    stored: str, rewritten: str
):
    # Held independently of content on purpose: the same content with a changed fingerprint
    # and changed content with the same fingerprint are both states a real store produces,
    # so a cache test built on this fake is not tautological.
    before = _repository(fingerprints={_INK: stored})
    after = _repository(fingerprints={_INK: rewritten})
    assert before.load_page(_DOC, _INK) == after.load_page(_DOC, _INK)
    assert (before.page_fingerprint(_DOC, _INK) == after.page_fingerprint(_DOC, _INK)) is (
        stored == rewritten
    )


def test_a_fingerprint_alone_is_not_a_cache_key():
    # The defect this port exists to prevent: a prompt or DPI change must not serve a stale
    # row as valid, so the caller combines the fingerprint with everything else that moves.
    def key(repository: DocumentRepository, *, model: str, dpi: int) -> tuple[str, str, str, int]:
        return (_STORE, repository.page_fingerprint(_DOC, _INK), model, dpi)

    repository = _repository(fingerprints={_INK: "rev-1"})
    baseline = key(repository, model="opus", dpi=229)
    assert key(repository, model="opus", dpi=229) == baseline
    assert key(repository, model="opus", dpi=300) != baseline
    assert key(repository, model="haiku", dpi=229) != baseline
    assert key(_repository(fingerprints={_INK: "rev-2"}), model="opus", dpi=229) != baseline


def test_the_absent_sentinel_invalidates_the_moment_an_artifact_appears():
    absent = _repository().page_fingerprint(_DOC, _ABSENT)
    now_drawn_on = Document(
        doc_id=_DOC,
        metadata=_METADATA,
        pages=(Page(page_id=_ABSENT, index=0, content=_content()),),
    )
    after = _as_repository(_FakeDocumentRepository((now_drawn_on,)))
    assert absent == ABSENT_ARTIFACT_FINGERPRINT
    assert after.page_fingerprint(_DOC, _ABSENT) != absent


# --------------------------------------------------------------------------------------
# the codec: bytes in, page content or a domain error out
# --------------------------------------------------------------------------------------


def test_decode_page_returns_content_with_degradations_as_values():
    content = _as_codec(_FakePageCodec()).decode_page(b"ab?", "page-1")
    assert content.stroke_count == 3
    assert [defect.code for defect in content.defects] == [
        PageDefectCode.UNKNOWN_COLOR_SUBSTITUTED
    ]


@given(raw=st.binary(max_size=8), first=st.text(max_size=8), second=st.text(max_size=8))
def test_the_page_ref_is_a_label_and_never_changes_what_is_decoded(
    raw: bytes, first: str, second: str
):
    codec = _as_codec(_FakePageCodec())
    assert codec.decode_page(raw, first) == codec.decode_page(raw, second)


def test_corrupt_bytes_are_reported_against_the_caller_supplied_ref():
    with pytest.raises(CorruptPageData) as caught:
        _as_codec(_FakePageCodec(fails_with="corrupt")).decode_page(b"1234", "notes/typed.rm")
    assert caught.value.page_uuid == "notes/typed.rm"
    assert caught.value.offset == 4


def test_an_unsupported_version_is_carried_on_the_error_not_returned_for_comparison():
    codec = _as_codec(_FakePageCodec(fails_with="unsupported", observed_version="8"))
    with pytest.raises(UnsupportedPageFormat) as caught:
        codec.decode_page(b"scene", _INK.uuid)
    assert caught.value.page_uuid == _INK.uuid
    assert caught.value.observed_version == "8"
    assert not hasattr(codec, "probe_version")


@pytest.mark.parametrize("failure", [CorruptPageData, UnsupportedPageFormat])
def test_codec_failures_are_domain_errors_so_no_parser_type_escapes(failure: type[RmspecError]):
    assert issubclass(failure, FormatError)
    assert issubclass(failure, RmspecError)


# --------------------------------------------------------------------------------------
# the two ports composed, as a repository adapter composes them
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("failure", ["corrupt", "unsupported"])
def test_a_repository_translates_codec_failures_into_a_page_defect(
    failure: Literal["corrupt", "unsupported"],
):
    repository = _as_repository(
        _CodecBackedRepository({_INK: b"bytes"}, _FakePageCodec(fails_with=failure))
    )
    page = repository.load_page(_DOC, _INK)
    assert page.content is None
    assert [defect.code for defect in page.defects] == [PageDefectCode.CONTENT_UNDECODABLE]
    assert repository.load(_DOC).pages == (page,)


def test_a_codec_backed_repository_keeps_load_and_load_page_in_agreement():
    repository = _as_repository(
        _CodecBackedRepository({_INK: b"ab", _ABSENT: None}, _FakePageCodec())
    )
    document = repository.load(_DOC)
    assert document.summary == repository.summary(_DOC)
    assert repository.list_documents() == (document.summary,)
    for page in document.pages:
        assert repository.load_page(_DOC, page.page_id) == page


def test_a_codec_backed_repository_hands_the_codec_the_page_uuid_it_already_holds():
    codec = _FakePageCodec()
    _as_repository(_CodecBackedRepository({_INK: b"a"}, codec)).load_page(_DOC, _INK)
    assert codec.decoded_refs == [_INK.uuid]


def test_a_codec_backed_repository_fingerprints_bytes_before_decoding_them():
    repository = _as_repository(
        _CodecBackedRepository(
            {_INK: b"scene", _ABSENT: None}, _FakePageCodec(fails_with="corrupt")
        )
    )
    fingerprints = repository.page_fingerprints(_DOC)
    assert fingerprints[_INK] == hashlib.sha256(b"scene").hexdigest()
    assert fingerprints[_ABSENT] == ABSENT_ARTIFACT_FINGERPRINT
    with pytest.raises(PageNotFound):
        repository.page_fingerprint(_DOC, _UNCLAIMED)
    with pytest.raises(DocumentNotFound):
        repository.page_fingerprint(_UNKNOWN_DOC, _INK)


# --------------------------------------------------------------------------------------
# the appender: ink onto a page that already exists
# --------------------------------------------------------------------------------------


def test_an_append_returns_the_whole_page_and_not_a_patch():
    appender = _as_appender(_FakeSceneAppender())

    edit = appender.append_strokes(b"scene", "ref", strokes=(_stroke(), _stroke()))

    assert edit.scene.startswith(b"scene"), "an appending implementation may make this true"
    assert len(edit.scene) > len(b"scene"), "and something must actually have been added"


def test_the_receipt_reports_the_two_decisions_the_caller_did_not_make():
    appender = _as_appender(_FakeSceneAppender(author_id=4, layer_index=2))

    edit = appender.append_strokes(b"scene", "ref", strokes=(_stroke(),))

    assert (edit.author_id, edit.layer_index) == (4, 2)


def test_the_page_ref_is_a_label_and_never_changes_what_is_written():
    appender = _FakeSceneAppender()

    first = _as_appender(appender).append_strokes(b"scene", "by-uuid", strokes=(_stroke(),))
    second = _as_appender(appender).append_strokes(b"scene", "/a/path.rm", strokes=(_stroke(),))

    assert first.scene == second.scene
    assert appender.appended_refs == ["by-uuid", "/a/path.rm"]


def test_appending_nothing_is_the_callers_mistake_and_not_a_format_failure():
    with pytest.raises(UsageError) as caught:
        _as_appender(_FakeSceneAppender()).append_strokes(b"scene", "ref", strokes=())

    assert not isinstance(caught.value, FormatError)


@pytest.mark.parametrize(
    ("switch", "expected"),
    [
        pytest.param("corrupt", CorruptPageData, id="bytes that do not decode"),
        pytest.param("unsafe", SceneRewriteUnsafe, id="bytes this build will not write"),
    ],
)
def test_appender_failures_are_domain_errors_reported_against_the_supplied_ref(
    switch: Literal["corrupt", "unsafe"], expected: type[CorruptPageData | SceneRewriteUnsafe]
):
    with pytest.raises(expected) as caught:
        _as_appender(_FakeSceneAppender(fails_with=switch)).append_strokes(
            b"scene", "ref", strokes=(_stroke(),)
        )

    assert caught.value.page_uuid == "ref"
    assert isinstance(caught.value, FormatError)


def test_the_two_write_refusals_are_distinguishable_from_each_other():
    # "your file is damaged" and "this build will not write your undamaged file" send a
    # caller to two different places, so neither may be a subclass of the other.
    assert not issubclass(SceneRewriteUnsafe, CorruptPageData)
    assert not issubclass(CorruptPageData, SceneRewriteUnsafe)


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param({"scene": b"", "author_id": 1, "layer_index": 0}, id="empty bytes"),
        pytest.param({"scene": b"x", "author_id": 0, "layer_index": 0}, id="author id zero"),
        pytest.param({"scene": b"x", "author_id": -1, "layer_index": 0}, id="negative author"),
        pytest.param({"scene": b"x", "author_id": 1, "layer_index": -1}, id="negative layer"),
        pytest.param(
            {"scene": b"x", "author_id": 1, "layer_index": 0, "author_uuid": "extra"},
            id="a field the receipt does not declare",
        ),
    ],
)
def test_a_receipt_refuses_a_state_a_caller_would_have_to_check_for(invalid: Mapping[str, object]):
    # Constrained rather than merely annotated, so nothing has to validate a receipt before
    # writing it: empty bytes would truncate a page, and author id 0 is the component every
    # artifact measured already uses.
    with pytest.raises(ValidationError):
        SceneEdit.model_validate(invalid)


def test_a_receipt_is_frozen_so_the_bytes_cannot_be_edited_after_they_are_reported():
    # Asserted through the model's own configuration rather than by assigning to a field: the
    # assignment is a static error as well as a runtime one, and only the runtime half is the
    # point here.
    assert SceneEdit.model_config.get("frozen") is True
    assert SceneEdit.model_config.get("extra") == "forbid"
