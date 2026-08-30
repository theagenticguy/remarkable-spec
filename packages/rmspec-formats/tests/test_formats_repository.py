"""The ``DocumentRepository`` adapter over a real xochitl directory on tmp_path.

Everything here is about what the *adapter* owns: the on-disk layout, which artifacts
are optional, and the translation of every stdlib and pydantic failure into the domain's
error tree. The shared port contract -- the part a fake must satisfy too -- lives in
``test_formats_port_conformance.py``.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import formats_fixtures as ff
import pytest

from rmspec.domain.errors import (
    DocumentNotFound,
    DocumentStoreUnavailable,
    MalformedDocument,
    PageNotFound,
)
from rmspec.domain.models import DocumentId, DocumentKind, PageDefectCode, PageId, SourceKind
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT
from rmspec.formats import SceneCodec, XochitlDocumentRepository, fingerprint_bytes

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

NOTEBOOK = ff.DocumentSpec(
    uuid="notebook-1",
    visible_name="Field notes",
    pages=(
        ff.PageSpec(uuid="page-a", template="Lined"),
        ff.PageSpec(uuid="page-b", state=ff.PageState.STUB, pdf_page_index=3),
        ff.PageSpec(uuid="page-c", state=ff.PageState.ABSENT),
    ),
)
FOLDER = ff.DocumentSpec(
    uuid="folder-1",
    visible_name="Projects",
    kind=DocumentKind.COLLECTION,
    source=None,
    with_content=False,
)


def recording_repository(
    root: Path, *specs: ff.DocumentSpec
) -> tuple[XochitlDocumentRepository, ff.FakePageCodec]:
    """Write a store and bind it to a recording codec instead of the real parser."""
    codec = ff.FakePageCodec()
    return XochitlDocumentRepository(root=ff.write_store(root, *specs), codec=codec), codec


# ─────────────────────────── the catalog ───────────────────────────


def test_the_listing_covers_every_document_and_includes_folders(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK, FOLDER)

    summaries = repository.list_documents()

    assert [summary.doc_id.uuid for summary in summaries] == ["folder-1", "notebook-1"]
    folder = summaries[0]
    assert folder.metadata.kind is DocumentKind.COLLECTION
    assert folder.pages == (), "a folder has no .content, so it claims no pages"


def test_an_empty_store_lists_nothing(tmp_path: Path):
    repository = XochitlDocumentRepository(root=tmp_path, codec=SceneCodec())

    assert repository.list_documents() == ()


def test_the_listing_omits_an_entry_whose_metadata_will_not_decode(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    (tmp_path / "broken.metadata").write_bytes(b"{not json")

    assert [summary.doc_id.uuid for summary in repository.list_documents()] == ["notebook-1"]


@pytest.mark.parametrize(
    "redir",
    [
        pytest.param("3", id="a bare integer"),
        pytest.param('{"value": "abc"}', id="an enveloped non-numeric string"),
        pytest.param('{"value": true}', id="an enveloped boolean"),
        pytest.param("null", id="an explicit null"),
    ],
)
def test_a_junk_redirection_index_does_not_cost_the_document(tmp_path: Path, redir: str):
    """The confirmed defect, at the altitude where it hurt.

    ``_page_offset`` raised, ``_page_list`` translated that to ``MalformedDocument``, and
    ``list_documents`` swallows ``MalformedDocument`` and continues -- so one junk
    optional field made the document vanish from the CLI's view entirely, and ``load``,
    ``summary``, ``load_page`` and both fingerprint methods all refused it. ``redir``
    says nothing about page order, so it cannot be a document-level refusal.
    """
    (tmp_path / "pdf-doc.metadata").write_bytes(ff.metadata_json(ff.DocumentSpec(uuid="pdf-doc")))
    (tmp_path / "pdf-doc.content").write_text(
        f'{{"fileType": "pdf", "cPages": {{"pages": [{{"id": "pg-1", "redir": {redir}}}]}}}}'
    )
    repository = XochitlDocumentRepository(root=tmp_path, codec=SceneCodec())
    doc_id = DocumentId(uuid="pdf-doc")

    document = repository.load(doc_id)

    assert [summary.doc_id for summary in repository.list_documents()] == [doc_id]
    assert len(document.pages) == 1
    assert document.pages[0].pdf_page_index in (3, None), "read when readable, None when not"
    assert document.pages[0].page_id == PageId(uuid="pg-1"), "identity and order are untouched"


def test_the_listing_omits_a_stem_that_cannot_be_an_identity(tmp_path: Path):
    """The legacy loader ended with ``UUID(doc_uuid)`` and crashed on the whole store."""
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    (tmp_path / f"{'x' * 80}.metadata").write_bytes(ff.metadata_json(NOTEBOOK))
    (tmp_path / "..metadata").write_bytes(ff.metadata_json(NOTEBOOK))

    assert [summary.doc_id.uuid for summary in repository.list_documents()] == ["notebook-1"]


def test_the_listing_omits_an_entry_whose_metadata_is_a_directory(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    (tmp_path / "odd.metadata").mkdir()

    assert [summary.doc_id.uuid for summary in repository.list_documents()] == ["notebook-1"]


def test_a_store_that_does_not_exist_is_unavailable_rather_than_empty(tmp_path: Path):
    repository = XochitlDocumentRepository(root=tmp_path / "gone", codec=SceneCodec())

    with pytest.raises(DocumentStoreUnavailable) as caught:
        repository.list_documents()

    assert caught.value.store.endswith("gone")


# ─────────────────────────── one document ───────────────────────────


def test_a_summary_carries_the_metadata_both_sidecars_hold(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)

    summary = repository.summary(NOTEBOOK.doc_id)

    assert summary.metadata.visible_name == "Field notes"
    assert summary.metadata.source is SourceKind.NOTEBOOK
    assert summary.metadata.last_modified == ff.LAST_MODIFIED
    assert summary.metadata.layout is not None
    assert [page_id.uuid for page_id in summary.pages] == ["page-a", "page-b", "page-c"]


def test_a_document_with_no_content_sidecar_claims_no_pages_and_no_source(tmp_path: Path):
    """The legacy loader raised ``FileNotFoundError`` here, which is why no listing had folders."""
    repository = ff.build_xochitl(tmp_path, FOLDER)

    summary = repository.summary(FOLDER.doc_id)

    assert summary.pages == ()
    assert summary.metadata.source is None
    assert summary.metadata.layout is None


def test_every_page_carries_its_position_template_and_source_pdf_page(tmp_path: Path):
    spec = ff.DocumentSpec(
        uuid="pdf-1",
        source=SourceKind.PDF,
        pages=(
            ff.PageSpec(uuid="p0", template="Dots", pdf_page_index=0),
            ff.PageSpec(uuid="p1", template="Grid", pdf_page_index=7),
            ff.PageSpec(uuid="p2", state=ff.PageState.ABSENT),
        ),
        templates=("Lined", "", "Blank"),
    )
    repository = ff.build_xochitl(tmp_path, spec)

    pages = repository.load(spec.doc_id).pages

    assert [page.index for page in pages] == [0, 1, 2]
    assert [page.template_name for page in pages] == ["Dots", "Grid", "Blank"], (
        "the entry's own template wins; a pagedata line only fills a page that names none"
    )
    assert [page.pdf_page_index for page in pages] == [0, 7, None]
    assert pages[2].content is None, "a page with no artifact still carries its sidecar facts"
    assert pages[2].template_name == "Blank", "and Blank from .pagedata is a real name"


def test_an_unknown_document_is_not_found(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)

    with pytest.raises(DocumentNotFound) as caught:
        repository.load(DocumentId(uuid="nobody"))

    assert caught.value.query == "nobody"


def test_a_page_the_document_does_not_claim_is_not_found(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)

    with pytest.raises(PageNotFound) as caught:
        repository.load_page(NOTEBOOK.doc_id, PageId(uuid="never-heard-of-it"))

    assert caught.value.page_count == 3


# ─────────────────────────── malformed sidecars ───────────────────────────


@pytest.mark.parametrize(
    ("artifact", "payload", "expected"),
    [
        pytest.param(".metadata", b"{not json", ".metadata", id="metadata is not json"),
        pytest.param(".metadata", b"[]", ".metadata", id="metadata is not an object"),
        pytest.param(
            ".metadata",
            b'{"visibleName": "x", "type": "SomethingType"}',
            ".metadata",
            id="a document type this domain does not know",
        ),
        pytest.param(".content", b"{not json", ".content", id="content is not json"),
        pytest.param(
            ".content",
            b'{"cPages": {"pages": [{"id": 7}]}}',
            ".content",
            id="a numeric page id",
        ),
        pytest.param(
            ".content",
            b'{"fileType": "docx", "cPages": {"pages": []}}',
            ".content",
            id="a file type this domain does not know",
        ),
        pytest.param(
            ".content",
            b'{"cPages": {"pages": [{"id": "a/b"}]}}',
            ".content",
            id="a page id that is not a leaf name",
        ),
        pytest.param(
            ".content",
            b'{"cPages": {"pages": [{"id": "dup"}, {"id": "dup"}]}}',
            ".content",
            id="the same page claimed twice",
        ),
        pytest.param(".pagedata", b"\xff\xfe", ".pagedata", id="pagedata is not utf-8"),
    ],
)
def test_an_undecodable_sidecar_is_a_malformed_document_naming_that_artifact(
    tmp_path: Path, artifact: str, payload: bytes, expected: str
):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    (tmp_path / f"{NOTEBOOK.uuid}{artifact}").write_bytes(payload)

    with pytest.raises(MalformedDocument) as caught:
        repository.summary(NOTEBOOK.doc_id)

    assert caught.value.artifact == expected
    assert caught.value.document_uuid == NOTEBOOK.uuid
    assert caught.value.detail, "a malformed sidecar must say something about why"


def test_a_metadata_failure_is_blamed_on_the_metadata_even_when_content_is_present(
    tmp_path: Path,
):
    """``DocumentMetadata.decode`` reads both, so the blame is decided by re-decoding one."""
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    (tmp_path / f"{NOTEBOOK.uuid}.metadata").write_bytes(b'{"lastModified": "not a number"}')

    with pytest.raises(MalformedDocument) as caught:
        repository.summary(NOTEBOOK.doc_id)

    assert caught.value.artifact == ".metadata"


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda repo, spec: repo.summary(spec.doc_id), id="summary"),
        pytest.param(lambda repo, spec: repo.load(spec.doc_id), id="load"),
        pytest.param(
            lambda repo, spec: repo.load_page(spec.doc_id, PageId(uuid="page-a")), id="load_page"
        ),
        pytest.param(
            lambda repo, spec: repo.page_fingerprint(spec.doc_id, PageId(uuid="page-a")),
            id="page_fingerprint",
        ),
        pytest.param(
            lambda repo, spec: repo.page_fingerprints(spec.doc_id), id="page_fingerprints"
        ),
    ],
)
def test_every_identity_addressed_method_reports_a_malformed_document(
    tmp_path: Path, call: Callable[[XochitlDocumentRepository, ff.DocumentSpec], object]
):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    (tmp_path / f"{NOTEBOOK.uuid}.content").write_bytes(b"{not json")

    with pytest.raises(MalformedDocument):
        call(repository, NOTEBOOK)


# ─────────────────────────── unreadable stores ───────────────────────────


def test_an_artifact_that_cannot_be_read_makes_the_store_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A permission failure is not "no such document": the legacy loader let it through raw."""
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)

    def refuse(_self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)

    with pytest.raises(DocumentStoreUnavailable) as caught:
        repository.summary(NOTEBOOK.doc_id)

    assert "Permission denied" in caught.value.detail


def test_a_page_directory_that_cannot_be_listed_makes_the_store_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    real_iterdir = pathlib.Path.iterdir

    def refuse(self: Path) -> object:
        if self.name == NOTEBOOK.uuid:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", refuse)

    with pytest.raises(DocumentStoreUnavailable):
        repository.page_fingerprints(NOTEBOOK.doc_id)


def test_a_document_with_no_page_directory_still_fingerprints_every_page(tmp_path: Path):
    spec = ff.DocumentSpec(
        uuid="never-drawn", pages=(ff.PageSpec(uuid="p0", state=ff.PageState.ABSENT),)
    )
    repository = ff.build_xochitl(tmp_path, spec)

    assert not (tmp_path / spec.uuid).exists()
    assert list(repository.page_fingerprints(spec.doc_id).values()) == ["absent"]


# ─────────────────────────── this adapter's fingerprints ───────────────────────────
#
# Claiming SHA-256 is legitimate here and nowhere else: only one implementation is in
# scope, so the digest is a fact about it rather than a cross-implementation comparison
# the port tells callers not to make. The shared contract table asserts the port's own
# invariants instead -- opaque, non-empty, stable, sentinel iff no artifact.


def test_this_adapter_fingerprints_a_page_with_the_unsalted_sha256_of_its_bytes(tmp_path: Path):
    """The compatibility constraint: cached OCR and diagram rows on disk key on this."""
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    inked = next(page for page in NOTEBOOK.pages if page.state is ff.PageState.INKED)

    value = repository.page_fingerprint(NOTEBOOK.doc_id, PageId(uuid=inked.uuid))

    assert value == fingerprint_bytes(ff.inked_scene())
    assert len(value) == 64
    assert value == value.lower()


def test_rewriting_an_artifact_changes_its_fingerprint_and_nothing_else_does(tmp_path: Path):
    """The invariant the port actually states, and the one nothing was asserting.

    "It changes whenever those bytes change and never changes while they do not." Two
    reads with no write between must agree; a write must move it.
    """
    repository = ff.build_xochitl(tmp_path, NOTEBOOK)
    inked = next(page for page in NOTEBOOK.pages if page.state is ff.PageState.INKED)
    page_id = PageId(uuid=inked.uuid)
    artifact = tmp_path / NOTEBOOK.uuid / f"{inked.uuid}.rm"

    before = repository.page_fingerprint(NOTEBOOK.doc_id, page_id)
    assert repository.page_fingerprint(NOTEBOOK.doc_id, page_id) == before

    artifact.write_bytes(ff.rootless_scene(ff.stroke_item()))
    after = repository.page_fingerprint(NOTEBOOK.doc_id, page_id)

    assert after != before
    assert after == fingerprint_bytes(artifact.read_bytes())


def test_the_batch_and_single_fingerprint_methods_agree_on_a_name_that_only_differs_in_case(
    tmp_path: Path,
):
    """The disagreement the two methods used to have, on the filesystem this runs on.

    ``page_fingerprints`` decided presence from a directory listing while
    ``page_fingerprint`` and ``load`` decided it by opening the file. On a
    case-insensitive filesystem -- APFS by default, i.e. here -- a page id whose case
    differs from the on-disk name got the absent sentinel from the batch method and a
    real digest from the single one, and ``load`` returned content for a page the batch
    method called absent. Every path now asks the same listing, so the three agree
    whichever way the filesystem folds case.
    """
    spec = ff.DocumentSpec(uuid="cased", pages=(ff.PageSpec(uuid="PageOne"),))
    repository = ff.build_xochitl(tmp_path, spec)
    (tmp_path / spec.uuid / "PageOne.rm").rename(tmp_path / spec.uuid / "pageone.rm")
    page_id = PageId(uuid="PageOne")

    batch = repository.page_fingerprints(spec.doc_id)[page_id]
    single = repository.page_fingerprint(spec.doc_id, page_id)
    loaded = repository.load_page(spec.doc_id, page_id)

    assert batch == single, "the port requires each batch value to be what the single one returns"
    assert (single == ABSENT_ARTIFACT_FINGERPRINT) is (loaded.content is None)


# ─────────────────────────── the injected codec ───────────────────────────


def test_the_codec_is_handed_the_bytes_and_the_page_uuid_the_adapter_already_holds(
    tmp_path: Path,
):
    repository, codec = recording_repository(tmp_path, NOTEBOOK)

    repository.load_page(NOTEBOOK.doc_id, PageId(uuid="page-a"))

    assert codec.calls == [(ff.inked_scene(), "page-a")]


def test_a_stub_is_still_handed_to_the_codec_rather_than_special_cased_here(tmp_path: Path):
    """The zero-byte decision belongs to the codec, so exactly one module makes it."""
    repository, codec = recording_repository(tmp_path, NOTEBOOK)

    repository.load_page(NOTEBOOK.doc_id, PageId(uuid="page-b"))

    assert codec.calls == [(b"", "page-b")]


@pytest.mark.parametrize(
    "codec",
    [
        pytest.param(ff.FakePageCodec(corrupt=True), id="corrupt page data"),
        pytest.param(ff.FakePageCodec(unsupported=True), id="unsupported version"),
    ],
)
def test_both_codec_failures_become_one_undecodable_page(tmp_path: Path, codec: ff.FakePageCodec):
    repository = XochitlDocumentRepository(root=ff.write_store(tmp_path, NOTEBOOK), codec=codec)

    page = repository.load_page(NOTEBOOK.doc_id, PageId(uuid="page-a"))

    assert page.content is None
    assert [defect.code for defect in page.defects] == [PageDefectCode.CONTENT_UNDECODABLE]
    assert "page-a" in page.defects[0].detail, "the error's own message is kept, not discarded"


def test_a_decode_degradation_arrives_as_a_value_on_a_readable_page(tmp_path: Path):
    degraded = ff.CANNED_CONTENT.model_copy(update={"defects": (ff.CANNED_DEFECT,)})
    codec = ff.FakePageCodec(content=degraded)
    repository = XochitlDocumentRepository(root=ff.write_store(tmp_path, NOTEBOOK), codec=codec)

    page = repository.load_page(NOTEBOOK.doc_id, PageId(uuid="page-a"))

    assert page.is_readable
    assert page.defects == (), "a decode degradation is not a page-level defect"
    assert page.all_defects == (ff.CANNED_DEFECT,)
