"""``rmspec annotations``: where the ``PdfSourceRef`` comes from, and the two texts kept apart.

The doubles follow ``test_cli_diagram.py``'s reasoning: the command's own ``run`` is replaced so
the real boundary still opens the writer, loads the settings, composes the container and catches
every ``RmspecError``, while the bindings that would cost money or touch hardware are overridden.
Two things are deliberately **not** doubled, because they are what this command's one unobvious
route runs through:

* the real :class:`~rmspec.cli._container.RenderPagesRasterizer`, over a renderer that draws
  nothing, so the ``page_box`` the PDF underlay is fitted to is the real arithmetic; and
* a real :class:`~rmspec.export.PdfSourceRegistry`, held by the test, so the token the command
  minted can be resolved back into the path it names and asserted against the mirror's layout.

``RawBundleSource`` is doubled with the shipped ``InMemoryRawBundleSource``, and that binding is
not optional: ``_source_for``'s no-mirror branch resolves the port, so before it was overridden
this file's own unconfigured-mirror test reached the *attached tablet* over USB and failed with
``DeviceDocumentNotFound`` from real firmware. One catalog instance serves both it and
``DeviceCatalog``, which is how ``bundle.document`` and ``catalog.get_document`` cannot disagree.
"""

from __future__ import annotations

import json as json_module
import os
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from dishka import Provider, Scope, provide

from rmspec.app import (
    PageAnnotations,
    RenderedPageArtifact,
    RenderPagesRequest,
    RenderPagesResult,
)
from rmspec.cli import _annotations
from rmspec.cli._annotations import (
    COMMAND,
    DENSE_COLUMNS,
    HUMAN_CAPTION,
    HUMAN_COLUMN_INDICES,
    HUMAN_COLUMNS,
    PDF_SUFFIX,
    TRUNCATED_KEY,
    XOCHITL_VARIABLE,
    read_annotations,
)
from rmspec.cli._container import RasterTemplate, RenderPagesRasterizer
from rmspec.cli._invoke import PAGE_SPEC_GRAMMAR, Invoked, run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.device.testing import InMemoryDeviceCatalog, InMemoryRawBundleSource
from rmspec.domain.errors import (
    AmbiguousDocument,
    DegradationKind,
    PdfPageOutOfRange,
    UsageError,
    exit_code,
)
from rmspec.domain.models import (
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
    SourceKind,
    Stroke,
)
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DevicePageSource,
    RawBundleSource,
)
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.export import ImageMedia as ExportMedia
from rmspec.domain.ports.export import (
    PdfPageBackground,
    PdfPageReader,
    PdfSourceRef,
    PixelSize,
)
from rmspec.domain.ports.export import (
    PhysicalSize as ExportSize,
)
from rmspec.domain.ports.export import RasterImage as ExportRaster
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT, DocumentRepository
from rmspec.domain.ports.ocr import StopReason, VisionCompletion, VisionLanguageModel
from rmspec.domain.ports.render import PhysicalSize, RenderedPage
from rmspec.export import PdfSourceRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from rmspec.domain.models import Document
    from rmspec.domain.ports.ocr import VisionRequest

DOC = "d3b38661-3333-4333-8333-333333333333"
NAME = "I Love You, Sylvia"
QUERY = "Sylvia"
MODEL_FINGERPRINT = "scripted-vision@1"
RENDER_DIGEST = "render-digest-1"

PRINTED = (
    "Clause 1. The term begins on the first of April.",
    "Clause 2. Either party may terminate with thirty days notice.",
    "Clause 3. Fees are payable monthly in arrears.",
)
READING = "The reader struck out 'thirty days' and wrote 'sixty days' above it."

UNDERLAY = b"%PDF-1.7\n%%EOF\n"
"""The print as the tablet holds it, which is what the no-mirror branch spools with ``for_bytes``.

Short but real: nothing in this file parses it -- :class:`_Pdf` answers from fixed text -- so its
only job is to be a payload a test can recognise again after the command has minted a token over
it, which is how the device branch is told apart from the mirror's ``for_path``.
"""

SCENE = b"scene-bytes"
"""What a page's ink is on the device side of the rig.

Never decoded: the ink reaches the command through the doubled ``DocumentRepository``, and the
bundle source is asked for nothing but ``base``. It exists so the two doubles agree about which
pages carry ink, rather than the device half claiming a document nobody wrote on.
"""

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
WIDTH = 8
HEIGHT = 12
A4 = ExportSize(width_mm=210.0, height_mm=297.0)

Answer = tuple[str, StopReason]

_OVERRIDDEN_PORTS = (
    DependencyProbe,
    DeviceCatalog,
    DocumentRepository,
    PdfPageReader,
    PdfSourceRegistry,
    RawBundleSource,
    VisionLanguageModel,
)
"""Every binding a double is provided for, listed to give each import a runtime use.

dishka reads a provider method's return annotation at container build, and this module has
``from __future__ import annotations``, so a name moved into an ``if TYPE_CHECKING:`` block makes
the container raise instead of resolving. Same discipline as ``_container.BOUND_PORTS``.
"""


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ``RMSPEC_*`` shell out of what these tests measure."""
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


def make_png(width: int, height: int) -> bytes:
    """Return PNG bytes both slices' raster twins accept: signature, matching IHDR, trailer."""
    return (
        PNG_MAGIC
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + PNG_IEND
    )


def page_uuid(index: int) -> str:
    """Return the stable page identifier this file uses for a position."""
    return f"page-{index}"


def ink() -> PageContent:
    """Return content that draws something, so the page is worth a model call."""
    return PageContent(
        layers=(
            Layer(
                strokes=(
                    Stroke(pen=PenType.FINELINER_1, color=PenColor.BLACK, thickness_scale=2.0),
                ),
            ),
        )
    )


def make_page(
    index: int,
    *,
    content: PageContent | None = None,
    pdf_page_index: int | None = None,
) -> Page:
    """Build one annotation page, absent content and absent redirection entry included."""
    defects = (
        ()
        if content is not None
        else (
            PageDefect(
                code=PageDefectCode.ARTIFACT_ABSENT,
                detail="the store holds a zero-byte stub for this page",
            ),
        )
    )
    return Page(
        page_id=PageId(uuid=page_uuid(index)),
        index=index,
        content=content,
        defects=defects,
        pdf_page_index=pdf_page_index,
    )


def document(
    uuid: str,
    /,
    *,
    name: str = NAME,
    page_count: int = 1,
    file_type: DeviceFileType = DeviceFileType.PDF,
) -> DeviceDocument:
    """Build one catalog entry, PDF-backed unless a test asks for the one kind with no print."""
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=file_type,
        page_count=page_count,
    )


def page_source(page: Page, /) -> DevicePageSource:
    """Mirror one prebuilt page on the device side of the rig."""
    return DevicePageSource(
        page_id=page.page_id.uuid,
        scene=None if page.content is None else SCENE,
    )


ONE_DOCUMENT = (document(DOC),)
NOTEBOOK_ONLY = (document(DOC, file_type=DeviceFileType.NOTEBOOK),)
"""The same identifier as a notebook, which is the one document kind carrying no underlay.

``DocumentSourceBundle`` enforces the pairing -- a notebook must not carry a ``base`` and anything
else must -- so this is how a test reaches ``base is None`` without asserting an impossible bundle.
"""
TWO_DOCUMENTS = (
    document(DOC, page_count=3),
    document("d3b38661-4444-4444-8444-444444444444", name=f"{NAME} draft"),
)


class _Repository:
    """A :class:`DocumentRepository` over prebuilt pages, with the source kind under test."""

    def __init__(self, summary: DocumentSummary, pages: dict[str, Page]) -> None:
        self.loaded: list[str] = []
        self._summary = summary
        self._pages = pages

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        return (self._summary,)

    def summary(self, _doc_id: DocumentId, /) -> DocumentSummary:
        return self._summary

    def load(self, doc_id: DocumentId, /) -> Document:
        raise NotImplementedError

    def load_page(self, _doc_id: DocumentId, page_id: PageId, /) -> Page:
        self.loaded.append(page_id.uuid)
        return self._pages[page_id.uuid]

    def page_fingerprint(self, _doc_id: DocumentId, page_id: PageId, /) -> str:
        if self._pages[page_id.uuid].content is None:
            return ABSENT_ARTIFACT_FINGERPRINT
        return f"hash-{page_id.uuid}"

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        return {page: self.page_fingerprint(doc_id, page) for page in self._summary.pages}


class _Pdf:
    """A :class:`PdfPageReader` over fixed page text, recording every handle it was given."""

    def __init__(self, texts: tuple[str, ...] = PRINTED) -> None:
        self.sources: list[PdfSourceRef] = []
        self.rasterized: list[int] = []
        self._texts = texts

    def page_count(self, source: PdfSourceRef) -> int:
        self.sources.append(source)
        return len(self._texts)

    def page_texts(self, source: PdfSourceRef) -> tuple[str, ...]:
        self.sources.append(source)
        return self._texts

    def rasterize_page(
        self,
        source: PdfSourceRef,
        *,
        page_index: int,
        box: PixelSize,
        oversample: int,
    ) -> PdfPageBackground:
        self.sources.append(source)
        if page_index >= len(self._texts):
            raise PdfPageOutOfRange(
                source=source.token,
                page_index=page_index,
                page_count=len(self._texts),
            )
        self.rasterized.append(page_index)
        size = PixelSize.fit_within(A4, box, oversample=oversample)
        return PdfPageBackground(
            page_index=page_index,
            data=make_png(size.width_px, size.height_px),
            pixel_size=size,
            page_size=A4,
        )


class _Renderer:
    """A ``PageBatchRenderer`` that renders nothing and stamps the page it was asked for."""

    def __init__(self) -> None:
        self.requests: list[RenderPagesRequest] = []

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        self.requests.append(request)
        selected = request.selection.indices
        assert selected is not None, "the bridge always asks for one explicit page"
        index = selected[0]
        ref = page_uuid(index)
        return RenderPagesResult(
            document_uuid=request.document_uuid,
            pages=(
                RenderedPageArtifact(
                    page_ref=ref,
                    page_index=index,
                    page_hash=f"hash-{ref}",
                    rendered=RenderedPage(
                        page_ref=ref,
                        svg='<svg xmlns="http://www.w3.org/2000/svg"/>',
                        size=PhysicalSize(width_mm=180.0, height_mm=240.0),
                        stroke_count=1,
                        text_block_count=0,
                    ),
                    raster=ExportRaster(
                        page_ref=ref,
                        media=ExportMedia.PNG,
                        data=make_png(WIDTH, HEIGHT),
                        width=WIDTH,
                        height=HEIGHT,
                        render_dpi=229,
                    ),
                ),
            ),
            render_digest=RENDER_DIGEST,
            degradations=(),
        )


class _Model:
    """A :class:`VisionLanguageModel` whose answers are keyed on the page in the request."""

    def __init__(self, answers: dict[str, Answer]) -> None:
        self.calls = 0
        self._answers = answers

    @property
    def fingerprint(self) -> str:
        return MODEL_FINGERPRINT

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        self.calls += 1
        text, stop_reason = self._answers[request.images[0].page_ref]
        return VisionCompletion.answering(
            request,
            fingerprint=MODEL_FINGERPRINT,
            text=text,
            stop_reason=stop_reason,
        )


class _Probe:
    """A :class:`DependencyProbe` answering from one set of absent modules."""

    def __init__(self, *, absent: frozenset[str] = frozenset()) -> None:
        self.asked: list[str] = []
        self._absent = absent

    def is_installed(self, module_name: str, /) -> bool:
        self.asked.append(module_name)
        return module_name not in self._absent

    def load_error(self, module_name: str, /) -> str | None:
        if module_name in self._absent:
            return f"No module named {module_name!r}"
        return None


class _AppDoubles(Provider):
    """Every app-scoped binding whose real half would cost money, disk or a tablet."""

    scope = Scope.APP

    def __init__(
        self,
        *,
        repository: _Repository,
        pdf: _Pdf,
        model: _Model,
        probe: _Probe,
        registry: PdfSourceRegistry,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._pdf = pdf
        self._model = model
        self._probe = probe
        self._registry = registry

    @provide(override=True)
    def repository(self) -> DocumentRepository:
        return self._repository

    @provide(override=True)
    def pdf(self) -> PdfPageReader:
        return self._pdf

    @provide(override=True)
    def model(self) -> VisionLanguageModel:
        return self._model

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        return self._probe

    @provide(override=True)
    def registry(self) -> PdfSourceRegistry:
        return self._registry


class _RequestDoubles(Provider):
    """The catalog and the bundle source, plus the real bridge over a renderer drawing nothing."""

    scope = Scope.REQUEST

    def __init__(
        self,
        *,
        catalog: InMemoryDeviceCatalog,
        bundles: InMemoryRawBundleSource,
        renderer: _Renderer,
    ) -> None:
        super().__init__()
        self._catalog = catalog
        self._bundles = bundles
        self._renderer = renderer

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        return self._catalog

    @provide(override=True)
    def bundles(self) -> RawBundleSource:
        return self._bundles

    @provide(override=True)
    def page_rasterizer(
        self,
        repository: DocumentRepository,
        template: RasterTemplate,
    ) -> RenderPagesRasterizer:
        return RenderPagesRasterizer(
            pages=self._renderer,
            repository=repository,
            template=template,
        )


class Wiring(NamedTuple):
    """Every double behind one patched invocation, so a test may assert on any of them."""

    repository: _Repository
    pdf: _Pdf
    renderer: _Renderer
    model: _Model
    probe: _Probe
    registry: PdfSourceRegistry
    bundles: InMemoryRawBundleSource


def wire(
    monkeypatch: pytest.MonkeyPatch,
    *pages: Page,
    answers: dict[str, Answer] | None = None,
    absent: frozenset[str] = frozenset(),
    source: SourceKind | None = SourceKind.PDF,
    texts: tuple[str, ...] = PRINTED,
    documents: tuple[DeviceDocument, ...] = ONE_DOCUMENT,
) -> Wiring:
    """Patch the command's ``run`` so that its container is composed with doubles."""
    summary = DocumentSummary(
        doc_id=DocumentId(uuid=DOC),
        metadata=DocumentMetadata(
            visible_name=NAME,
            kind=DocumentKind.DOCUMENT,
            source=source,
        ),
        pages=tuple(page.page_id for page in pages),
    )
    catalog = InMemoryDeviceCatalog(documents=documents)
    wiring = Wiring(
        repository=_Repository(summary, {page.page_id.uuid: page for page in pages}),
        pdf=_Pdf(texts),
        renderer=_Renderer(),
        model=_Model(answers or {}),
        probe=_Probe(absent=absent),
        registry=PdfSourceRegistry(),
        bundles=InMemoryRawBundleSource(
            catalog=catalog,
            pages={DOC: tuple(page_source(page) for page in pages)},
            bases={
                entry.uuid: UNDERLAY
                for entry in documents
                if entry.file_type is not DeviceFileType.NOTEBOOK
            },
        ),
    )
    providers = (
        _AppDoubles(
            repository=wiring.repository,
            pdf=wiring.pdf,
            model=wiring.model,
            probe=wiring.probe,
            registry=wiring.registry,
        ),
        _RequestDoubles(catalog=catalog, bundles=wiring.bundles, renderer=wiring.renderer),
    )

    def patched(
        body: Callable[[Invoked], int],
        /,
        *,
        json: bool = False,
        dense: bool = False,
    ) -> int:
        return run(body, json=json, dense=dense, providers=providers)

    monkeypatch.setattr(_annotations, "run", patched)
    return wiring


def mirror(monkeypatch: pytest.MonkeyPatch, root: Path, /) -> None:
    """Point the settings at a mirror root, which is where the source PDF is looked for."""
    monkeypatch.setenv(XOCHITL_VARIABLE, str(root))


def answering(text: str, stop_reason: StopReason = StopReason.COMPLETE) -> dict[str, Answer]:
    """Script one answer for page zero."""
    return {page_uuid(0): (text, stop_reason)}


def envelope(captured: str, /) -> dict[str, Any]:
    """Parse one JSON envelope off stdout."""
    return json_module.loads(captured)


def records(captured: str, /) -> list[list[str]]:
    """Split a dense stream into its records."""
    return [line.split("\t") for line in captured.splitlines()]


def kinds(document_: dict[str, Any], /) -> set[str]:
    """Read every degradation kind out of one envelope."""
    return {entry["kind"] for entry in document_["degradations"]}


# ───────────────────────────── the frozen contract ─────────────────────────────


def test_the_discriminator_is_read_from_the_manifest_table():
    assert RESPONSE_TYPES[COMMAND] == "annotations"


def test_the_pages_help_pastes_the_one_shared_grammar_sentence():
    assert read_annotations.__doc__ is not None
    assert PAGE_SPEC_GRAMMAR in " ".join(read_annotations.__doc__.split())


def test_the_alias_is_the_command_itself_so_cyclopts_still_names_it_annotations():
    # `from __future__ import annotations` binds the name in every importing module, so a caller
    # that imports the function under that name cannot call it without ty refusing the union.
    assert read_annotations is _annotations.annotations
    assert read_annotations.__name__ == COMMAND


def test_a_row_model_really_does_omit_truncated_from_its_dump():
    # The measurement behind _payload: truncated is a @property, and pydantic dumps fields.
    row = PageAnnotations(
        page_index=0,
        page_ref=page_uuid(0),
        pdf_page_index=0,
        printed_text=PRINTED[0],
        annotations=READING,
        stop_reason=StopReason.OUTPUT_LIMIT,
    )

    assert row.truncated is True
    assert TRUNCATED_KEY not in row.model_dump(mode="json")


# ───────────────────────────── where the handle comes from ─────────────────────────────


def test_the_minted_handle_names_the_mirrors_own_copy_of_the_pdf(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(monkeypatch, make_page(0, content=ink()), answers=answering(READING))

    assert read_annotations(QUERY, json=True) == 0

    capsys.readouterr()
    minted = wiring.pdf.sources[0]
    assert wiring.registry.resolve(minted) == tmp_path / f"{DOC}{PDF_SUFFIX}"
    assert set(wiring.pdf.sources) == {minted}


def test_no_mirror_now_reads_the_underlay_off_the_device_instead_of_refusing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # This pinned XochitlDirNotConfigured, and that refusal made `rmspec annotations` dead in the
    # default configuration: exit 78 naming RMSPEC_XOCHITL, a xochitl tree `rmspec sync` does not
    # create -- sync writes RMSPEC_SYNC_DB, a different artifact -- so the remediation named
    # something no command produces. Measured over USB against the attached tablet on `I Love You,
    # Sylvia`, the same run with no mirror configured now exits 0 with both texts.
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING),
    )

    assert read_annotations(QUERY, json=True) == 0

    page = envelope(capsys.readouterr().out)["data"]["pages"][0]
    assert page["printed_text"] == PRINTED[0]
    assert page["annotations"] == READING
    # One call, and it is the mint. `DocumentRepository` is doubled here, so this counts nothing
    # else; a real no-mirror run also pays `BundleDocumentRepository`'s own pull, whose one-slot
    # memo does not see the call `_source_for` makes straight to the port.
    assert wiring.bundles.load_calls == 1
    # The token is over the payload the device handed back, not over a path -- which is the whole
    # difference between `for_bytes` and the mirror's `for_path`, and the reason the spooled
    # temporary is the registry's to remove rather than something this command left behind.
    minted = wiring.pdf.sources[0]
    assert set(wiring.pdf.sources) == {minted}
    spooled = wiring.registry.resolve(minted)
    assert spooled.read_bytes() == UNDERLAY
    wiring.registry.close()
    assert not spooled.exists()


def test_no_mirror_and_no_underlay_is_a_usage_error_before_the_first_page_is_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # `DocumentSourceBundle.base` is None for a notebook by the model's own rule, so the device
    # branch has the whole answer in hand: there is no print for a mark to sit on. Exit 2, not a
    # transport failure -- nothing went wrong on the wire.
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink()),
        source=SourceKind.NOTEBOOK,
        documents=NOTEBOOK_ONLY,
    )

    status = read_annotations(QUERY, json=True)

    error = envelope(capsys.readouterr().out)["error"]
    assert status == exit_code(UsageError(subject="x", requirement="y"))
    assert error["type"] == "UsageError"
    assert "pdf-backed" in error["message"].lower()
    assert wiring.bundles.load_calls == 1
    assert wiring.repository.loaded == []
    assert wiring.pdf.sources == []
    assert wiring.model.calls == 0


def test_a_notebook_is_refused_at_two_different_moments_under_one_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    # The asymmetry is deliberate, and a reader who was not told would call it a bug. The mirror
    # path mints a token for a path it never opens, so a notebook survives `_source_for` and only
    # the use case refuses it; the device path holds the bytes and so learns immediately that there
    # are none. What must not differ is the answer: "this is not a PDF-backed document" is one
    # fact, and two spellings of it would be two things for a caller to branch on.
    over_device = wire(
        monkeypatch,
        make_page(0, content=ink()),
        source=SourceKind.NOTEBOOK,
        documents=NOTEBOOK_ONLY,
    )
    device_status = read_annotations(QUERY, json=True)
    from_device = envelope(capsys.readouterr().out)["error"]

    mirror(monkeypatch, tmp_path)
    over_mirror = wire(
        monkeypatch,
        make_page(0, content=ink()),
        source=SourceKind.NOTEBOOK,
        documents=NOTEBOOK_ONLY,
    )
    mirror_status = read_annotations(QUERY, json=True)
    from_mirror = envelope(capsys.readouterr().out)["error"]

    assert device_status == mirror_status == exit_code(UsageError(subject="x", requirement="y"))
    assert from_device["type"] == from_mirror["type"] == "UsageError"
    for message in (from_device["message"], from_mirror["message"]):
        assert DOC in message
        assert "pdf-backed" in message.lower()
    # Two moments, one error: only the device branch went and looked.
    assert over_device.bundles.load_calls == 1
    assert over_mirror.bundles.load_calls == 0


def test_a_document_that_is_not_pdf_backed_is_refused_before_any_page_is_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(monkeypatch, make_page(0, content=ink()), source=SourceKind.NOTEBOOK)

    status = read_annotations(QUERY, json=True)

    assert status == exit_code(UsageError(subject="x", requirement="y"))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "UsageError"
    assert wiring.pdf.sources == []
    assert wiring.model.calls == 0


# ───────────────────────────── json ─────────────────────────────


def test_json_keeps_the_printed_text_and_the_ink_apart(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=1),
        answers=answering(READING),
    )

    assert read_annotations(QUERY, json=True) == 0

    document_ = envelope(capsys.readouterr().out)
    assert document_["type"] == "annotations"
    assert document_["degradations"] == []
    page = document_["data"]["pages"][0]
    assert page["page_index"] == 0
    assert page["pdf_page_index"] == 1
    assert page["printed_text"] == PRINTED[1]
    assert page["annotations"] == READING
    assert page[TRUNCATED_KEY] is False
    assert wiring.model.calls == 1
    assert wiring.pdf.rasterized == [1]


def test_the_computed_truncated_is_put_back_into_the_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING, StopReason.OUTPUT_LIMIT),
    )

    assert read_annotations(QUERY, json=True) == 0

    page = envelope(capsys.readouterr().out)["data"]["pages"][0]
    assert page["stop_reason"] == StopReason.OUTPUT_LIMIT.value
    assert page[TRUNCATED_KEY] is True


def test_a_page_nobody_wrote_on_keeps_its_printed_text_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(monkeypatch, make_page(0, pdf_page_index=0))

    assert read_annotations(QUERY, json=True) == 0

    document_ = envelope(capsys.readouterr().out)
    page = document_["data"]["pages"][0]
    assert page["printed_text"] == PRINTED[0]
    assert page["annotations"] is None
    assert page[TRUNCATED_KEY] is False
    assert kinds(document_) == {DegradationKind.PAGE_NOT_ANNOTATED.value}
    assert wiring.model.calls == 0


def test_a_missing_redirection_entry_is_reported_rather_than_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    # The fallback is how one page's ink comes to sit over another page's print, so a run that
    # hid it would produce an answer that reads perfectly and describes the wrong page.
    mirror(monkeypatch, tmp_path)
    wire(monkeypatch, make_page(0, content=ink()), answers=answering(READING))

    assert read_annotations(QUERY, json=True) == 0

    document_ = envelope(capsys.readouterr().out)
    assert kinds(document_) == {DegradationKind.PDF_PAGE_INDEX_FALLBACK.value}
    assert document_["data"]["pages"][0]["pdf_page_index"] == 0


# ───────────────────────────── dense and human ─────────────────────────────


def test_dense_carries_both_numberings_and_both_texts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=1),
        make_page(1, content=ink(), pdf_page_index=2),
        make_page(2, pdf_page_index=0),
        answers={
            page_uuid(0): (READING, StopReason.OUTPUT_LIMIT),
            page_uuid(1): ("", StopReason.COMPLETE),
        },
    )

    assert read_annotations(QUERY, dense=True) == 0

    written = records(capsys.readouterr().out)
    assert written[0] == list(DENSE_COLUMNS)
    assert written[1] == ["0", "1", "true", "true", PRINTED[1], READING]
    assert written[2] == ["1", "2", "true", "false", PRINTED[2], ""]
    assert written[3] == ["2", "0", "false", "false", PRINTED[0], ""]


def test_dense_keeps_its_record_stream_clean_and_still_warns_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wire(monkeypatch, make_page(0, pdf_page_index=0))

    assert read_annotations(QUERY, dense=True) == 0

    captured = capsys.readouterr()
    assert PRINTED[0] in captured.out
    assert "not annotated" in captured.err.replace("_", " ")


def test_the_human_projection_keeps_the_ink_and_drops_the_print():
    # Taken by index out of the same record, so the two renderings cannot drift. One long text,
    # never two: the printed clause is already in the PDF, and the correction over it is not.
    assert HUMAN_COLUMNS == (
        "page_index",
        "pdf_page_index",
        "annotated",
        "truncated",
        "annotations",
    )
    assert tuple(DENSE_COLUMNS[index] for index in HUMAN_COLUMN_INDICES) == HUMAN_COLUMNS
    assert "printed_text" in DENSE_COLUMNS
    assert "printed_text" not in HUMAN_COLUMNS


def test_the_default_mode_writes_nothing_to_stdout_and_puts_its_table_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    # The rule this test exists for: stdout carries the machine-consumable payload and nothing
    # else. A default invocation that wrote tab-separated records there made `2>/dev/null` a lie
    # and `| jq` a parse error, and no test caught it.
    mirror(monkeypatch, tmp_path)
    wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, pdf_page_index=1),
        answers=answering(READING),
    )

    assert read_annotations(QUERY) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "\t" not in captured.err
    shown = " ".join(captured.err.split())
    for header in HUMAN_COLUMNS:
        assert header in shown
    # A wrapped cell is broken up by the table's own rules, so these are single words: one that
    # only PRINTED[0] carries, and one that only the ink does.
    assert "April" not in shown
    assert "reader" in shown
    assert HUMAN_CAPTION in shown


# ───────────────────────────── the shared policies ─────────────────────────────


def test_the_probe_runs_before_the_command_touches_a_use_case(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING),
        absent=frozenset({"pymupdf"}),
    )

    assert read_annotations(QUERY, json=True) == 78

    document_ = envelope(capsys.readouterr().out)
    assert document_["error"]["type"] == "MissingDependencyError"
    assert "pymupdf" in document_["error"]["message"]
    assert wiring.repository.loaded == []
    assert wiring.pdf.sources == []
    assert wiring.model.calls == 0


def test_the_probe_names_the_pdf_the_scene_the_raster_and_the_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING),
    )

    assert read_annotations(QUERY, json=True) == 0

    capsys.readouterr()
    assert {"pymupdf", "rmscene", "boto3"} <= set(wiring.probe.asked)


def test_the_cap_comes_from_the_setting_and_the_flag_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "1")
    answers = {
        page_uuid(0): (READING, StopReason.COMPLETE),
        page_uuid(1): (READING, StopReason.COMPLETE),
    }
    both = (
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, content=ink(), pdf_page_index=1),
    )
    wire(monkeypatch, *both, answers=answers)

    refused = read_annotations(QUERY, json=True)

    assert refused == exit_code(UsageError(subject="x", requirement="y"))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "UsageError"

    wire(monkeypatch, *both, answers=answers)
    assert read_annotations(QUERY, max_pages=2, json=True) == 0
    assert len(envelope(capsys.readouterr().out)["data"]["pages"]) == 2


def test_pages_and_limit_select_the_same_page_two_ways(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    both = (
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, content=ink(), pdf_page_index=1),
    )
    answers = {page_uuid(index): (READING, StopReason.COMPLETE) for index in (0, 1)}

    wire(monkeypatch, *both, answers=answers)
    assert read_annotations(QUERY, pages="1", json=True) == 0
    selected = envelope(capsys.readouterr().out)["data"]["pages"]

    wire(monkeypatch, *both, answers=answers)
    assert read_annotations(QUERY, limit=1, json=True) == 0
    leading = envelope(capsys.readouterr().out)["data"]["pages"]

    assert [page["page_index"] for page in selected] == [1]
    assert [page["page_index"] for page in leading] == [0]


def test_strict_refuses_an_ambiguous_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        documents=TWO_DOCUMENTS,
    )

    status = read_annotations(QUERY, strict=True, json=True)

    assert status == exit_code(AmbiguousDocument(query=QUERY, candidates=()))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "AmbiguousDocument"
    assert wiring.pdf.sources == []


def test_an_ambiguous_selector_is_otherwise_accepted_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    mirror(monkeypatch, tmp_path)
    wire(
        monkeypatch,
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING),
        documents=TWO_DOCUMENTS,
    )

    assert read_annotations(QUERY, json=True) == 0

    reported = kinds(envelope(capsys.readouterr().out))

    assert DegradationKind.AMBIGUOUS_AUTO_RESOLVED.value in reported


def test_the_two_output_flags_together_are_refused(capsys: pytest.CaptureFixture[str]):
    status = read_annotations(QUERY, json=True, dense=True)

    assert status == exit_code(UsageError(subject="x", requirement="y"))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "UsageError"
