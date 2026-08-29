"""One contract table, run against the real adapter and against an in-memory fake.

There is no differential oracle for this package, so this is what stands in for one: every
assertion below is a clause of ``ports/formats.py`` and must hold for *both*
implementations. The real one derives each page's state from bytes on disk; the fake is
told the state by the same spec. An assertion only one of them can satisfy is an assertion
about an implementation rather than about the port, and it fails here.

Deliberately mirrors the names in ``packages/rmspec-domain/tests/test_ports_formats.py``
where the clause is the same one, so drift between the domain's account of the contract
and this package's is visible in a diff. That suite's directory is not importable, by
design, so the table is restated rather than shared.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import formats_fixtures as ff
import pytest

from rmspec.domain.errors import (
    DocumentNotFound,
    DocumentStoreUnavailable,
    MalformedDocument,
    PageNotFound,
)
from rmspec.domain.models import DocumentId, DocumentKind, PageDefectCode, PageId
from rmspec.domain.ports.formats import (
    ABSENT_ARTIFACT_FINGERPRINT,
    DocumentRepository,
    PageCodec,
)
from rmspec.formats import SceneCodec, XochitlDocumentRepository

if TYPE_CHECKING:
    from collections.abc import Callable

NOTEBOOK = ff.DocumentSpec(
    uuid="notebook-1",
    pages=(
        ff.PageSpec(uuid="n-inked", template="Lined"),
        ff.PageSpec(uuid="n-stub", state=ff.PageState.STUB),
    ),
)
PDF = ff.DocumentSpec(
    uuid="pdf-1",
    pages=(
        ff.PageSpec(uuid="p-absent", state=ff.PageState.ABSENT, pdf_page_index=0),
        ff.PageSpec(uuid="p-broken", state=ff.PageState.UNDECODABLE),
        ff.PageSpec(uuid="p-inked", state=ff.PageState.INKED, pdf_page_index=2),
    ),
)
FOLDER = ff.DocumentSpec(uuid="folder-1", kind=DocumentKind.COLLECTION, with_content=False)
TEMPLATED = ff.DocumentSpec(
    uuid="templated-1",
    pages=(
        ff.PageSpec(uuid="t-first", template="Lined"),
        ff.PageSpec(uuid="t-second", state=ff.PageState.STUB, template="Dots"),
    ),
    templates=("", "Grid"),
)
"""A ``.pagedata`` whose first line is blank, and pages that also name their own template.

The shape no spec in this table used to have, and the one that made the two
implementations disagree: ``decode_pagedata`` strips the whole text before splitting, so
a leading blank line is *deleted* and every later line moves up one. The real adapter
therefore templates page 0 from ``"Grid"`` and page 1 from its own ``"Dots"``, while a
fake that indexed the spec's tuple positionally said ``None`` then ``"Grid"``. The strip
is relocated legacy behaviour and load-bearing, so the fix is one shared decoder rather
than a second model of it -- and this spec is what keeps that honest.
"""

STORE = (FOLDER, NOTEBOOK, PDF, TEMPLATED)
"""The one store spec both implementations are built from."""

UNKNOWN = DocumentId(uuid="no-such-document")

# ── Static conformance. ty resolves these assignments, so a signature that drifts from
# ── the Protocol fails the type gate rather than waiting for a runtime call.
_A_REAL_REPOSITORY: DocumentRepository = XochitlDocumentRepository(
    root=Path("/nowhere"), codec=SceneCodec()
)
_A_FAKE_REPOSITORY: DocumentRepository = ff.FakeDocumentRepository()
_A_REAL_CODEC: PageCodec = SceneCodec()
_A_FAKE_CODEC: PageCodec = ff.FakePageCodec()


@pytest.fixture(params=["xochitl", "fake"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> DocumentRepository:
    """Build the same store twice: as the real adapter, and as an in-memory double."""
    if request.param == "xochitl":
        return ff.build_xochitl(tmp_path, *STORE)
    return ff.FakeDocumentRepository(*STORE)


@pytest.fixture(params=["xochitl", "fake"])
def unavailable(request: pytest.FixtureRequest, tmp_path: Path) -> DocumentRepository:
    """Build a store that cannot be reached at all."""
    if request.param == "xochitl":
        return XochitlDocumentRepository(root=tmp_path / "gone", codec=SceneCodec())
    return ff.FakeDocumentRepository(*STORE, unavailable=True)


@pytest.fixture(params=["xochitl", "fake"])
def malformed(request: pytest.FixtureRequest, tmp_path: Path) -> DocumentRepository:
    """Build a store whose ``notebook-1`` will not decode."""
    if request.param == "xochitl":
        built = ff.build_xochitl(tmp_path, *STORE)
        (tmp_path / f"{NOTEBOOK.uuid}.content").write_bytes(b"{not json")
        return built
    return ff.FakeDocumentRepository(*STORE, malformed=NOTEBOOK.uuid)


ALL_PAGES = [
    pytest.param(spec, page, id=f"{spec.uuid}/{page.uuid}")
    for spec in STORE
    for page in spec.pages
]
"""Every claimed page of every document, for the per-page clauses."""


# ─────────────────────────── the listing ───────────────────────────


def test_list_documents_returns_a_materialised_tuple_of_summaries(
    repository: DocumentRepository,
):
    summaries = repository.list_documents()

    assert isinstance(summaries, tuple)
    assert {summary.doc_id.uuid for summary in summaries} == {spec.uuid for spec in STORE}


def test_summary_is_the_same_summary_the_listing_carries(repository: DocumentRepository):
    listed = {summary.doc_id: summary for summary in repository.list_documents()}

    for spec in STORE:
        assert repository.summary(spec.doc_id) == listed[spec.doc_id]


def test_list_documents_omits_a_document_whose_metadata_will_not_decode(
    malformed: DocumentRepository,
):
    listed = {summary.doc_id.uuid for summary in malformed.list_documents()}

    assert NOTEBOOK.uuid not in listed
    assert PDF.uuid in listed, "one bad document does not cost the listing"


def test_summary_carries_page_identities_in_document_order(repository: DocumentRepository):
    for spec in STORE:
        assert repository.summary(spec.doc_id).pages == spec.page_ids


def test_a_folder_is_listed_and_claims_no_pages(repository: DocumentRepository):
    summary = repository.summary(FOLDER.doc_id)

    assert summary.metadata.kind is DocumentKind.COLLECTION
    assert summary.pages == ()
    assert summary.page_count == 0


# ─────────────────────────── loading ───────────────────────────


def test_load_returns_pages_in_document_order_agreeing_with_the_summary(
    repository: DocumentRepository,
):
    for spec in STORE:
        document = repository.load(spec.doc_id)

        assert document.summary == repository.summary(spec.doc_id)
        assert [page.index for page in document.pages] == list(range(len(spec.pages)))


@pytest.mark.parametrize(("spec", "page"), ALL_PAGES)
def test_load_page_returns_exactly_the_page_load_places_at_that_identity(
    repository: DocumentRepository, spec: ff.DocumentSpec, page: ff.PageSpec
):
    page_id = PageId(uuid=page.uuid)
    position = spec.page_ids.index(page_id)

    assert (
        repository.load_page(spec.doc_id, page_id) == repository.load(spec.doc_id).pages[position]
    )


@pytest.mark.parametrize(("spec", "page"), ALL_PAGES)
def test_each_page_reports_the_state_the_store_was_built_with(
    repository: DocumentRepository, spec: ff.DocumentSpec, page: ff.PageSpec
):
    loaded = repository.load_page(spec.doc_id, PageId(uuid=page.uuid))
    codes = {defect.code for defect in loaded.all_defects}

    if page.state is ff.PageState.ABSENT:
        assert loaded.content is None
        assert codes == {PageDefectCode.ARTIFACT_ABSENT}
    elif page.state is ff.PageState.UNDECODABLE:
        assert loaded.content is None
        assert codes == {PageDefectCode.CONTENT_UNDECODABLE}
    elif page.state is ff.PageState.STUB:
        assert loaded.content is not None
        assert loaded.content.is_blank
        assert codes == set()
    else:
        assert loaded.content is not None
        assert loaded.stroke_count == 1
        assert codes == set()


def test_the_two_contentless_states_are_distinguishable_from_each_other(
    repository: DocumentRepository,
):
    absent = repository.load_page(PDF.doc_id, PageId(uuid="p-absent"))
    broken = repository.load_page(PDF.doc_id, PageId(uuid="p-broken"))

    assert absent.all_defects != broken.all_defects
    assert not absent.is_readable
    assert not broken.is_readable


def test_a_contentless_page_still_carries_its_sidecar_facts(repository: DocumentRepository):
    absent = repository.load_page(PDF.doc_id, PageId(uuid="p-absent"))

    assert absent.pdf_page_index == 0, "the template and pdf page are facts only the store has"


def test_a_leading_blank_pagedata_line_renumbers_the_whole_template_column(
    repository: DocumentRepository,
):
    """The divergence the fake used to have, asserted rather than left to a comment."""
    document = repository.load(TEMPLATED.doc_id)

    assert [page.template_name for page in document.pages] == ["Grid", "Dots"]


@pytest.mark.parametrize(("spec", "page"), ALL_PAGES)
def test_every_page_reports_the_template_the_shared_precedence_rule_gives_it(
    repository: DocumentRepository, spec: ff.DocumentSpec, page: ff.PageSpec
):
    loaded = repository.load_page(spec.doc_id, PageId(uuid=page.uuid))

    assert loaded.template_name == ff.effective_template(spec, spec.pages.index(page))


# ─────────────────────────── fingerprints ───────────────────────────


def test_page_fingerprints_covers_every_claimed_page_in_document_order(
    repository: DocumentRepository,
):
    for spec in STORE:
        fingerprints = repository.page_fingerprints(spec.doc_id)

        assert list(fingerprints) == list(spec.page_ids)


@pytest.mark.parametrize(("spec", "page"), ALL_PAGES)
def test_page_fingerprints_agrees_with_page_fingerprint_for_every_page(
    repository: DocumentRepository, spec: ff.DocumentSpec, page: ff.PageSpec
):
    page_id = PageId(uuid=page.uuid)

    assert repository.page_fingerprints(spec.doc_id)[page_id] == repository.page_fingerprint(
        spec.doc_id, page_id
    )


@pytest.mark.parametrize(("spec", "page"), ALL_PAGES)
def test_a_fingerprint_is_opaque_non_empty_and_stable(
    repository: DocumentRepository, spec: ff.DocumentSpec, page: ff.PageSpec
):
    """The port's words, not an implementation's.

    ``ports/formats.py`` calls the value "an opaque, non-empty token" that is "not
    comparable across implementations". Asserting a *particular* digest here would make
    this table assert the one thing the port tells callers not to rely on -- and it is
    what forced 354 bytes of ``rmscene``-serialised v6 data into the in-memory fake,
    which could not otherwise answer the method at all. The exact-SHA-256 claims live in
    ``test_formats_repository.py``, where only one implementation is in scope and the
    claim is therefore true.
    """
    page_id = PageId(uuid=page.uuid)

    value = repository.page_fingerprint(spec.doc_id, page_id)

    assert value
    assert value == repository.page_fingerprint(spec.doc_id, page_id), "stable across calls"


@pytest.mark.parametrize(("spec", "page"), ALL_PAGES)
def test_the_absent_sentinel_is_returned_for_exactly_the_pages_with_no_artifact(
    repository: DocumentRepository, spec: ff.DocumentSpec, page: ff.PageSpec
):
    value = repository.page_fingerprint(spec.doc_id, PageId(uuid=page.uuid))

    assert (value == ABSENT_ARTIFACT_FINGERPRINT) is (page.state is ff.PageState.ABSENT)


def test_pages_holding_different_artifacts_have_different_cache_keys(
    repository: DocumentRepository,
):
    """A stub, a broken artifact and an inked one are three distinct keys.

    Distinctness is asserted over pages whose *declared states* differ, never over two
    pages in the same state: the real adapter hashes bytes, so two inked pages built from
    the same fixture legitimately share a key, while a fake keyed on identity cannot.
    """
    keys = {
        repository.page_fingerprint(PDF.doc_id, PageId(uuid=uuid))
        for uuid in ("p-absent", "p-broken", "p-inked")
    }

    assert len(keys) == 3
    stub = repository.page_fingerprint(NOTEBOOK.doc_id, PageId(uuid="n-stub"))
    assert stub != ABSENT_ARTIFACT_FINGERPRINT, "a stub has an artifact: it stays on that path"


# ─────────────────────────── failures ───────────────────────────

METHODS: dict[str, Callable[[DocumentRepository, DocumentId], object]] = {
    "summary": lambda repo, doc: repo.summary(doc),
    "load": lambda repo, doc: repo.load(doc),
    "load_page": lambda repo, doc: repo.load_page(doc, PageId(uuid="n-inked")),
    "page_fingerprint": lambda repo, doc: repo.page_fingerprint(doc, PageId(uuid="n-inked")),
    "page_fingerprints": lambda repo, doc: repo.page_fingerprints(doc),
}
"""Every identity-addressed method, for the clauses that must hold across all of them."""


@pytest.mark.parametrize("call", METHODS.values(), ids=list(METHODS))
def test_every_identity_addressed_method_reports_an_unknown_document(
    repository: DocumentRepository,
    call: Callable[[DocumentRepository, DocumentId], object],
):
    with pytest.raises(DocumentNotFound) as caught:
        call(repository, UNKNOWN)

    assert caught.value.query == UNKNOWN.uuid, "the method must report the identity it was given"


@pytest.mark.parametrize("call", METHODS.values(), ids=list(METHODS))
def test_every_identity_addressed_method_reports_a_malformed_document(
    malformed: DocumentRepository,
    call: Callable[[DocumentRepository, DocumentId], object],
):
    with pytest.raises(MalformedDocument) as caught:
        call(malformed, NOTEBOOK.doc_id)

    assert caught.value.document_uuid == NOTEBOOK.uuid, "the error must name the document"


@pytest.mark.parametrize("call", METHODS.values(), ids=list(METHODS))
def test_an_unreachable_store_fails_every_method_including_the_listing(
    unavailable: DocumentRepository,
    call: Callable[[DocumentRepository, DocumentId], object],
):
    with pytest.raises(DocumentStoreUnavailable):
        call(unavailable, NOTEBOOK.doc_id)
    with pytest.raises(DocumentStoreUnavailable):
        unavailable.list_documents()


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda repo, doc, page: repo.load_page(doc, page), id="load_page"),
        pytest.param(
            lambda repo, doc, page: repo.page_fingerprint(doc, page), id="page_fingerprint"
        ),
    ],
)
def test_page_not_found_is_raised_only_for_a_page_the_document_does_not_claim(
    repository: DocumentRepository,
    call: Callable[[DocumentRepository, DocumentId, PageId], object],
):
    with pytest.raises(PageNotFound) as caught:
        call(repository, PDF.doc_id, PageId(uuid="not-a-page-of-this-document"))

    assert caught.value.page_count == len(PDF.pages)
    # A page of *another* document is just as unclaimed as one of no document.
    with pytest.raises(PageNotFound):
        call(repository, PDF.doc_id, PageId(uuid="n-inked"))


def test_a_claimed_page_with_no_artifact_is_never_page_not_found(
    repository: DocumentRepository,
):
    """The narrowing ``ports/formats.py`` makes over the error's own docstring."""
    assert repository.load_page(PDF.doc_id, PageId(uuid="p-absent")).content is None
