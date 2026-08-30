"""The composition root: the eager pass, every binding, the finalizers, and the bridge."""

from __future__ import annotations

import io
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

import pytest
from dishka import Provider, Scope, provide
from pydantic import BaseModel

from rmspec.app import (
    CreateDocument,
    ExtractDiagrams,
    ListDocuments,
    ReadAnnotations,
    RenderedPageArtifact,
    RenderPages,
    RenderPagesRequest,
    RenderPagesResult,
    ReplyOnPage,
    ReportCapabilities,
    ReportCapabilitiesResult,
    ReportDeviceFacts,
    ReportSyncHistory,
    ResolveDocument,
    SearchText,
    SyncDocuments,
    TranscribePages,
)
from rmspec.cli import _container
from rmspec.cli._container import (
    APP_SCOPED_PORTS,
    BOUND_PORTS,
    FEATURE_MODULES,
    REQUEST_SCOPED_PORTS,
    AdjudicatorModel,
    BundleDocumentRepository,
    BundleRepositoryProvider,
    DependencyFailure,
    DeviceProvider,
    Feature,
    ImportProbe,
    Invocation,
    MemoizingBundleSource,
    MirrorRepositoryProvider,
    OptionalModule,
    RasterizedPage,
    RasterTemplate,
    ReaderModel,
    RenderPagesRasterizer,
    TextToInkEngraver,
    WritebackSceneWriter,
    compose,
    composed_transport,
    describe_bindings,
    probe_features,
    require_engines,
    resolve_dependencies,
)
from rmspec.cli._output import CliOutput, ConsolePair, OutputMode, make_console_pair
from rmspec.cli._settings import CliSettings, OcrEngineName, Transport
from rmspec.device import (
    ParamikoShell,
    SshBundleSource,
    SshCatalog,
    SshFacts,
    SshSearchIndexSource,
    SshUploader,
    UsbBundleSource,
    UsbCatalog,
    UsbFacts,
    UsbUploader,
    UsbWebApi,
)
from rmspec.device.addresses import WEB_API_PORT, Endpoint, RemotePath
from rmspec.device.testing import (
    InMemoryDeviceCatalog,
    InMemoryDeviceFactsSource,
    InMemoryDocumentUploader,
    InMemoryRawBundleSource,
)
from rmspec.device.writeback import (
    ABSENT_IDENTITY,
    SNAPSHOT_DEPTH,
    SNAPSHOT_DIR_EXPECTED,
    SNAPSHOT_DIR_GOT,
    SNAPSHOT_GONE,
    SNAPSHOT_ROOT,
    SNAPSHOT_WANTED,
    SshSceneWriter,
)
from rmspec.device.writeback import ScenePrecondition as WritebackPrecondition
from rmspec.device.writeback import SceneRead as WritebackRead
from rmspec.device.writeback import SceneWriteReceipt as WritebackReceipt
from rmspec.domain.errors import (
    CorruptPageData,
    DeviceDocumentNotFound,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceStateMismatchError,
    DeviceTransferInterrupted,
    DocumentNotFound,
    DocumentStoreUnavailable,
    InvalidSettingError,
    MalformedDeviceMetadata,
    MalformedDocument,
    MissingDependencyError,
    PageNotFound,
    RasterizationFailed,
    RmspecError,
    TransportKind,
    UnsupportedPageFormat,
    UsageError,
    exit_code,
)
from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    DocumentId,
    DocumentKind,
    DocumentMetadata,
    DocumentSummary,
    PageContent,
    PageDefectCode,
    PageId,
    PenColor,
    SourceKind,
)
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFactsSource,
    DeviceFileType,
    DevicePageSource,
    DocumentSourceBundle,
    DocumentUploader,
    RawBundleSource,
    ScenePrecondition,
    SceneRead,
    SceneVisibility,
    SceneWriter,
    SceneWriteReceipt,
    SearchIndexSource,
)
from rmspec.domain.ports.export import ImageMedia, RasterImage
from rmspec.domain.ports.formats import (
    ABSENT_ARTIFACT_FINGERPRINT,
    DocumentRepository,
    PageCodec,
    SceneAppender,
)
from rmspec.domain.ports.ocr import HandwrittenTextIndex, TextRecognizer, VisionLanguageModel
from rmspec.domain.ports.render import (
    ImageMedia as UnderlayMedia,
)
from rmspec.domain.ports.render import InkText as DomainInkText
from rmspec.domain.ports.render import (
    InkTextStyle,
    PageUnderlay,
    PhysicalSize,
    RenderedPage,
    RenderStyle,
    TextEngraver,
    TextStyle,
)
from rmspec.formats import AppendOnlySceneWriter, XochitlDocumentRepository, fingerprint_bytes
from rmspec.ocr import OcrEngine
from rmspec.ocr.testing import ScriptedTextRecognizer, ScriptedVisionLanguageModel
from rmspec.persistence import DeviceSearchIndex, SqliteDatabase, StoreMaintenance
from rmspec.render import SVG_RENDERER_REVISION, text_to_ink
from rmspec.render import InkText as EngravedInkText
from rmspec.render import InkTextStyle as EngravedTextStyle

if TYPE_CHECKING:
    from pathlib import Path

_EX_CONFIG = 78

# Written as code points rather than literals: RUF001 rejects the characters themselves, and
# the code point is what the use case names in its refusal anyway.
_EM_DASH = chr(0x2014)
_APOSTROPHE = chr(0x2019)
_ELLIPSIS = chr(0x2026)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"

_DOC = DocumentId(uuid="11111111-1111-1111-1111-111111111111")
_PAGE = PageId(uuid="22222222-2222-2222-2222-222222222222")
_OTHER_PAGE = PageId(uuid="33333333-3333-3333-3333-333333333333")

_STYLE = RenderStyle(
    thickness_scale=1.5,
    min_padding_mm=10.0,
    text=TextStyle(family="Noto Sans, sans-serif", size_px=32.0, line_height=1.25),
    renderer_revision=SVG_RENDERER_REVISION,
)
_TEMPLATE = RasterTemplate(
    screen=PAPER_PRO_SCREEN,
    palette=EXPORT_PALETTE,
    style=_STYLE,
    raster_dpi=300,
)


def _png(width: int, height: int) -> bytes:
    return (
        _PNG_SIGNATURE
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + _IEND
    )


def _summary(*pages: PageId) -> DocumentSummary:
    return DocumentSummary(
        doc_id=_DOC,
        metadata=DocumentMetadata(visible_name="Notes", kind=DocumentKind.DOCUMENT),
        pages=pages,
    )


class _FakePageOrder:
    """The one repository method the bridge uses, plus a call counter for the memo test."""

    def __init__(self, summary: DocumentSummary) -> None:
        self._summary = summary
        self.calls = 0

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        assert doc_id == _DOC
        self.calls += 1
        return self._summary


class _FakeBatchRenderer:
    """Records the request it was handed and returns one artifact."""

    def __init__(self, *, raster: RasterImage | None, digest: str = "render-1") -> None:
        self._raster = raster
        self._digest = digest
        self.requests: list[RenderPagesRequest] = []

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        self.requests.append(request)
        return RenderPagesResult(
            document_uuid=request.document_uuid,
            pages=(
                RenderedPageArtifact(
                    page_ref=_PAGE.uuid,
                    page_index=0,
                    page_hash="hash-1",
                    rendered=RenderedPage(
                        page_ref=_PAGE.uuid,
                        svg='<svg xmlns="http://www.w3.org/2000/svg"/>',
                        size=PhysicalSize(width_mm=180.0, height_mm=240.0),
                        stroke_count=1,
                        text_block_count=0,
                    ),
                    raster=self._raster,
                ),
            ),
            render_digest=self._digest,
            degradations=(),
        )


_SCENE = b"scene-bytes"
_OTHER_SCENE = b"other-scene-bytes"

_SCENE_DIGEST = fingerprint_bytes(_SCENE)
_OTHER_DIGEST = fingerprint_bytes(_OTHER_SCENE)

_PAGE_PATH = RemotePath(value="/home/root/.local/share/remarkable/xochitl/doc-1/page-1.rm")
_SNAPSHOT_PATH = RemotePath(value="/home/root/.cache/rmspec/scene-snapshots/doc-1/page-1/0")


def _identity(digest: str | None, /) -> str:
    """Spell an identity the way `rmspec.device.writeback` spells one in a refusal.

    Returns
    -------
    str
        The digest with its algorithm, or the absent-artifact sentinel.
    """
    return ABSENT_IDENTITY if digest is None else f"sha256 {digest}"


def _moved_identity(expected: str | None, /) -> DeviceProtocolError:
    """Build the refusal SshSceneWriter raises when a page's identity moved.

    Returns
    -------
    DeviceProtocolError
        Naming both identities, which is what the bridge carries into the domain's vocabulary.
    """
    return DeviceProtocolError(
        transport=TransportKind.SSH,
        route=_PAGE_PATH.value,
        expected=_identity(expected),
        got=_identity(_OTHER_DIGEST),
    )


class _ScriptedWriteback:
    """A ``PageSceneWriteback`` over one page in memory: no shell, no tablet, nothing written.

    The whole point of the narrow protocol. Exercising the bridge's field renaming through the
    real writer would need a connected session against the owner's attached tablet, and the page
    it wrote to would be a page of their handwriting.
    """

    def __init__(
        self,
        *,
        scene: bytes | None,
        refuse: DeviceProtocolError | None = None,
    ) -> None:
        self._scene = scene
        self.refuse = refuse
        self.wrote: list[WritebackPrecondition] = []
        self.undone: list[WritebackReceipt] = []

    def read_scene(self, doc_uuid: str, page_id: str, /) -> WritebackRead:
        return WritebackRead(
            doc_uuid=doc_uuid,
            page_id=page_id,
            path=_PAGE_PATH,
            scene=self._scene,
        )

    def write_scene(
        self,
        precondition: WritebackPrecondition,
        scene: bytes,
        /,
    ) -> WritebackReceipt:
        if self.refuse is not None:
            raise self.refuse
        self.wrote.append(precondition)
        return WritebackReceipt(
            doc_uuid=precondition.doc_uuid,
            page_id=precondition.page_id,
            path=_PAGE_PATH,
            byte_count=len(scene),
            digest=fingerprint_bytes(scene),
            snapshot=None if precondition.digest is None else _SNAPSHOT_PATH,
            pruned=(),
        )

    def undo(self, receipt: WritebackReceipt, /) -> WritebackReceipt:
        if self.refuse is not None:
            raise self.refuse
        self.undone.append(receipt)
        return WritebackReceipt(
            doc_uuid=receipt.doc_uuid,
            page_id=receipt.page_id,
            path=receipt.path,
            byte_count=len(_SCENE),
            digest=_SCENE_DIGEST,
            snapshot=_SNAPSHOT_PATH,
            pruned=(),
        )


def _device_document(
    *,
    uuid: str = _DOC.uuid,
    file_type: DeviceFileType = DeviceFileType.NOTEBOOK,
) -> DeviceDocument:
    return DeviceDocument(
        uuid=uuid,
        name="Notes",
        file_type=file_type,
        parent_uuid="folder-1",
        trashed=False,
    )


def _bundle(
    *pages: DevicePageSource, document: DeviceDocument | None = None
) -> DocumentSourceBundle:
    resolved = _device_document() if document is None else document
    base = None if resolved.file_type is DeviceFileType.NOTEBOOK else b"%PDF-1.7\n"
    return DocumentSourceBundle(document=resolved, pages=pages, base=base)


class _ScriptedBundleSource:
    """A :class:`RawBundleSource` over one canned bundle per uuid, with a pull counter."""

    def __init__(
        self,
        bundles: dict[str, DocumentSourceBundle] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._bundles = bundles or {}
        self._raises = raises
        self.pulls: list[str] = []

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        self.pulls.append(doc_uuid)
        if self._raises is not None:
            raise self._raises
        return self._bundles[doc_uuid]


class _ScriptedCodec:
    """A :class:`PageCodec` that returns one content, or raises what it was handed."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.refs: list[str] = []

    def decode_page(self, raw: bytes, page_ref: str, /) -> PageContent:
        assert raw in {_SCENE, _OTHER_SCENE}
        self.refs.append(page_ref)
        if self._raises is not None:
            raise self._raises
        return PageContent()


def _repository(
    source: RawBundleSource,
    *,
    codec: _ScriptedCodec | None = None,
    transport: TransportKind = TransportKind.USB_WEB_API,
) -> BundleDocumentRepository:
    # Typed as the port, not as the scripted double, so a repository can be built over the
    # memoizing seam as well as straight over the double -- which is the arrangement the
    # composed container actually has.
    return BundleDocumentRepository(
        bundles=source,
        codec=_ScriptedCodec() if codec is None else codec,
        transport=transport,
    )


class _ScriptedProbe:
    """A :class:`DependencyProbe` over two dictionaries, holding the port's own invariant."""

    def __init__(
        self,
        *,
        installed: dict[str, bool],
        errors: dict[str, str | None] | None = None,
    ) -> None:
        self._installed = installed
        self._errors = errors or {}
        self.load_calls: list[str] = []

    def is_installed(self, module_name: str, /) -> bool:
        return self._installed.get(module_name, True)

    def load_error(self, module_name: str, /) -> str | None:
        self.load_calls.append(module_name)
        if not self.is_installed(module_name):
            return f"No module named {module_name!r}"
        return self._errors.get(module_name)


class _Doubles(Provider):
    """Bind the shipped in-memory device doubles over the real device providers.

    Proof that a caller can compose this container without a tablet: ``override=True`` on each
    port replaces the SSH binding, and nothing in the resulting graph opens a session, a
    ``bedrock-runtime`` client or a ``textract`` client.
    """

    scope = Scope.REQUEST

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        return InMemoryDeviceCatalog()

    @provide(override=True)
    def bundles(self) -> RawBundleSource:
        return InMemoryRawBundleSource(catalog=InMemoryDeviceCatalog())

    @provide(override=True)
    def uploader(self) -> DocumentUploader:
        return InMemoryDocumentUploader()

    @provide(override=True)
    def facts(self) -> DeviceFactsSource:
        return InMemoryDeviceFactsSource()


class _ModelDoubles(Provider):
    """Bind the shipped scripted recogniser and vision model over the real OCR providers.

    Narrower than :class:`_Doubles` on purpose: it replaces only the three bindings that would
    otherwise construct a ``textract`` or ``bedrock-runtime`` client, so every device binding in
    the same container is still the real one and still asserted.
    """

    scope = Scope.APP

    @provide(override=True)
    def recognizers(self) -> Sequence[TextRecognizer]:
        return (ScriptedTextRecognizer(),)

    @provide(override=True)
    def read_model(self) -> VisionLanguageModel:
        return ScriptedVisionLanguageModel(model_id="scripted-reader")

    @provide(override=True)
    def adjudicator(self) -> AdjudicatorModel:
        return ScriptedVisionLanguageModel(model_id="scripted-judge")


_BDA_PROJECT_ARN = "arn:aws:bedrock:us-west-2:123456789012:data-automation-project/abc123"
"""A syntactically real SYNC-project ARN, for the cases that need the binding to succeed."""


class _RefusingBda:
    """Stands in for the BDA factory when the project ARN cannot be named.

    Raises the ``ValueError`` the real ``profile_arn_for`` raises, rather than being the real
    one: the assertion under test is the container's translation, and driving it through a
    double keeps this file from depending on the adapter's parse.
    """

    def for_project(self, project_arn: str, /, **_kwargs: object) -> TextRecognizer:
        msg = f"not an ARN: {project_arn!r}"
        raise ValueError(msg)


class _RecordingOcr:
    """Stands in for the three adapter factories and records what they were asked for."""

    def __init__(self) -> None:
        self.regions: list[str] = []
        self.projects: list[tuple[str, str, str]] = []
        self.model_ids: list[str] = []
        self.vision_calls = 0

    def in_region(self, region: str, /) -> TextRecognizer:
        self.regions.append(region)
        return ScriptedTextRecognizer(provider="scripted-textract")

    def for_project(
        self, project_arn: str, /, *, region_name: str, profile: str
    ) -> TextRecognizer:
        self.projects.append((project_arn, region_name, profile))
        return ScriptedTextRecognizer(provider="scripted-bda")

    def on_this_machine(self) -> TextRecognizer:
        self.vision_calls += 1
        return ScriptedTextRecognizer(provider="scripted-vision")

    def build_model(self, model_id: str, /) -> VisionLanguageModel:
        self.model_ids.append(model_id)
        return ScriptedVisionLanguageModel(model_id=model_id)


@pytest.fixture
def recording_ocr(monkeypatch: pytest.MonkeyPatch) -> _RecordingOcr:
    """Replace the OCR adapter factories so no test constructs a real AWS client.

    Returns
    -------
    _RecordingOcr
        The recorder the container's factories now call.
    """
    recorder = _RecordingOcr()
    monkeypatch.setattr(_container, "TextractRecognizer", recorder)
    monkeypatch.setattr(_container, "BdaRecognizer", recorder)
    monkeypatch.setattr(_container, "AppleVisionRecognizer", recorder)
    monkeypatch.setattr(_container, "build_client", lambda *, region: region)
    monkeypatch.setattr(
        _container,
        "BedrockOpenAiVisionModel",
        lambda client, *, model_id, region: recorder.build_model(f"{model_id}@{client}@{region}"),
    )
    return recorder


@pytest.fixture
def settings(tmp_path: Path) -> CliSettings:
    return CliSettings(xochitl=tmp_path / "xochitl", sync_db=tmp_path / "sync.db")


# --------------------------------------------------------------------------- probe


def test_an_installed_module_is_reported_installed():
    assert ImportProbe().is_installed("json") is True


def test_an_absent_module_is_reported_absent():
    assert ImportProbe().is_installed("no_such_module_anywhere") is False


def test_an_unresolvable_name_is_answered_rather_than_raised():
    # find_spec raises ImportError for a missing parent package. A failure of the probe must
    # never be mistaken for a failure of the thing being probed.
    assert ImportProbe().is_installed("no_such_parent.child") is False


def test_installation_is_memoized():
    probe = ImportProbe()

    assert probe.is_installed("json") is True
    assert probe.is_installed("json") is True
    assert probe._installed == {"json": True}


def test_a_loadable_module_has_no_load_error():
    assert ImportProbe().load_error("json") is None


def test_an_absent_module_reports_its_loader_message():
    detail = ImportProbe().load_error("no_such_module_anywhere")

    assert detail is not None
    assert "no_such_module_anywhere" in detail


def test_load_errors_are_memoized():
    probe = ImportProbe()

    assert probe.load_error("json") is None
    assert probe.load_error("json") is None
    assert probe._errors == {"json": None}


def test_a_module_that_raises_on_import_is_described_not_propagated(tmp_path: Path):
    # The third dependency state: installed, and it does not load. cairocffi's real failure
    # is an OSError from dlopen; this reproduces the shape without needing a broken host.
    module = tmp_path / "explodes_on_import.py"
    module.write_text('raise OSError("no library called \\"cairo-2\\" was found")\n')

    sys.path.insert(0, str(tmp_path))
    try:
        detail = ImportProbe().load_error("explodes_on_import")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("explodes_on_import", None)

    assert detail == 'no library called "cairo-2" was found'


def test_an_exception_with_no_message_is_reported_as_its_class(tmp_path: Path):
    module = tmp_path / "silent_failure.py"
    module.write_text("raise RuntimeError\n")

    sys.path.insert(0, str(tmp_path))
    try:
        detail = ImportProbe().load_error("silent_failure")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("silent_failure", None)

    assert detail == "RuntimeError"


# ------------------------------------------------------------------- the eager pass


def test_no_selected_feature_probes_nothing():
    probe = _ScriptedProbe(installed={})

    assert resolve_dependencies(probe, ()) == ()
    assert probe.load_calls == []


def test_a_usable_composition_reports_no_failure():
    probe = _ScriptedProbe(installed={})

    assert resolve_dependencies(probe, set(Feature)) == ()


def test_an_absent_module_is_reported_with_its_extra():
    probe = _ScriptedProbe(installed={"cairocffi": False})

    failures = resolve_dependencies(probe, (Feature.RASTER,))

    assert [(f.package, f.extra, f.detail) for f in failures] == [("cairocffi", "render", None)]


def test_a_missing_module_is_not_then_asked_whether_it_loads():
    probe = _ScriptedProbe(installed={"cairocffi": False})

    resolve_dependencies(probe, (Feature.RASTER,))

    assert "cairocffi" not in probe.load_calls


def test_a_non_native_module_is_never_asked_whether_it_loads():
    # load_error executes module code, which is the cost is_installed exists to avoid. Only
    # the modules that link against a native library earn that call.
    probe = _ScriptedProbe(installed={})

    resolve_dependencies(probe, (Feature.SCENE_DECODE, Feature.OCR_TEXTRACT))

    assert probe.load_calls == []


def test_an_installed_module_that_will_not_load_is_reported_with_the_reason():
    probe = _ScriptedProbe(installed={}, errors={"cairocffi": 'no library called "cairo-2"'})

    failures = resolve_dependencies(probe, (Feature.RASTER,))

    assert [(f.package, f.detail) for f in failures] == [
        ("cairocffi", 'no library called "cairo-2"')
    ]


def test_two_missing_extras_are_both_learned_from_one_run():
    # The requirement the whole return-instead-of-raise split exists for.
    probe = _ScriptedProbe(installed={"cairocffi": False, "Quartz": False})

    failures = resolve_dependencies(probe, (Feature.RASTER, Feature.OCR_APPLE_VISION))

    assert sorted({f.extra for f in failures}) == ["ocr", "render"]


def test_a_module_two_features_share_is_reported_once():
    probe = _ScriptedProbe(installed={"boto3": False})

    failures = resolve_dependencies(probe, (Feature.OCR_TEXTRACT, Feature.MODEL_BEDROCK))

    assert [f.package for f in failures] == ["boto3"]


def test_failures_are_ordered_by_package_so_the_report_is_stable():
    probe = _ScriptedProbe(installed=dict.fromkeys(("cairocffi", "PIL", "cairosvg"), False))

    failures = resolve_dependencies(probe, (Feature.RASTER,))

    assert [f.package for f in failures] == ["PIL", "cairocffi", "cairosvg"]


def test_the_recogniser_backend_check_runs_only_for_engines_whose_modules_are_present(
    monkeypatch: pytest.MonkeyPatch,
):
    # A Textract-only composition must not be told to install pyobjc.
    seen: list[set[OcrEngine]] = []
    monkeypatch.setattr(_container, "require_backends", lambda engines: seen.append(set(engines)))
    probe = _ScriptedProbe(installed={"Quartz": False})

    probe_features(probe, (Feature.OCR_APPLE_VISION, Feature.OCR_TEXTRACT))

    assert seen == [{OcrEngine.AWS_TEXTRACT}]


def test_a_backend_failure_joins_the_same_failure_list(monkeypatch: pytest.MonkeyPatch):
    def _refuse(_engines: object) -> None:
        raise MissingDependencyError(
            package="Quartz",
            extra="ocr",
            feature="recognising handwriting on device",
        )

    monkeypatch.setattr(_container, "require_backends", _refuse)

    failures = probe_features(_ScriptedProbe(installed={}), (Feature.OCR_APPLE_VISION,))

    assert [f.package for f in failures] == ["Quartz"]
    assert failures[0].detail is not None


def test_require_engines_asks_about_nothing_when_no_recogniser_is_selected(
    monkeypatch: pytest.MonkeyPatch,
):
    seen: list[set[OcrEngine]] = []
    monkeypatch.setattr(_container, "require_backends", lambda engines: seen.append(set(engines)))

    require_engines((Feature.RASTER, Feature.PDF_READ))

    assert seen == [set()]


def test_every_feature_names_at_least_one_module():
    assert set(FEATURE_MODULES) == set(Feature)
    assert all(FEATURE_MODULES[feature] for feature in Feature)


def test_a_failure_becomes_the_domains_configuration_error():
    failure = DependencyFailure(
        package="cairocffi", extra="render", feature="rasterizing a page", detail=None
    )

    err = failure.as_error()

    assert err.extra == "render"
    assert err.remediation == "uv sync --extra render"
    assert exit_code(err) == _EX_CONFIG


def test_a_module_declares_whether_it_loads_native_code():
    # No default: deciding per module whether load_error is warranted is the whole reason the
    # port has two methods, so a table entry cannot omit the answer.
    assert OptionalModule.model_fields["native"].is_required()


# ---------------------------------------------------------------------- the container


def test_every_declared_port_resolves(settings: CliSettings):
    # _ModelDoubles is the narrowest override that keeps a textract and a bedrock-runtime client
    # out of this suite; every device binding resolved below is still the real one.
    container = compose(settings=settings, consoles=_consoles(), providers=(_ModelDoubles(),))
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            bound = {port: request.get(port) for port in BOUND_PORTS}
    finally:
        container.close()

    assert len(bound) == len(BOUND_PORTS)
    assert all(value is not None for value in bound.values())


def test_the_port_manifest_is_split_by_the_scope_each_port_is_bound_at(settings: CliSettings):
    # dishka 1.10.1 has no eager resolution, so warming the graph up is a manual loop -- and a
    # request-scoped port cannot be resolved before a request scope is entered.
    container = compose(settings=settings, consoles=_consoles(), providers=(_ModelDoubles(),))
    try:
        app_scoped = {port: container.get(port) for port in APP_SCOPED_PORTS}
    finally:
        container.close()

    assert BOUND_PORTS == APP_SCOPED_PORTS + REQUEST_SCOPED_PORTS
    assert set(APP_SCOPED_PORTS).isdisjoint(REQUEST_SCOPED_PORTS)
    assert all(value is not None for value in app_scoped.values())


def test_the_store_is_opened_once_and_closed_when_the_container_closes(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles())
    database = container.get(SqliteDatabase)

    maintenance = StoreMaintenance(database)

    assert container.get(SqliteDatabase) is database
    assert maintenance.counts().documents == 0
    container.close()

    with pytest.raises(RmspecError):
        maintenance.counts()


def test_one_command_is_one_shell_and_the_finalizer_closes_it(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            first = request.get(ParamikoShell)
            assert request.get(ParamikoShell) is first
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            assert request.get(ParamikoShell) is not first
    finally:
        container.close()


def test_the_ssh_key_path_reaches_the_shell(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            shell = request.get(ParamikoShell)
    finally:
        container.close()

    assert shell._key_path == str(settings.ssh_key)
    assert shell._user == "root"


def test_the_thickness_and_ocr_dpi_settings_are_now_read(tmp_path: Path):
    settings = CliSettings(xochitl=tmp_path, sync_db=tmp_path / "s.db", thickness=2.5, ocr_dpi=400)
    container = compose(settings=settings, consoles=_consoles())
    try:
        style = container.get(RenderStyle)
        template = container.get(RasterTemplate)
    finally:
        container.close()

    assert style.thickness_scale == 2.5
    assert template.raster_dpi == 400


def test_a_configured_mirror_is_the_repository_and_needs_no_device(settings: CliSettings):
    # Precedence: RMSPEC_XOCHITL set wins. It needs no handshake, it is what an offline run wants,
    # and it answers list_documents and pdf_page_index, which a bundle source cannot.
    container = compose(settings=settings, consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            repository = request.get(DocumentRepository)
    finally:
        container.close()

    assert isinstance(repository, XochitlDocumentRepository)


def test_an_unconfigured_mirror_now_reads_the_tablet_instead_of_refusing(tmp_path: Path):
    # This pinned XochitlDirNotConfigured, whose remediation named a directory `rmspec sync` does
    # not create -- so `diagram` and `annotations` were dead in the default configuration while
    # `render`, which asks for RawBundleSource, worked over the same cable.
    container = compose(
        settings=CliSettings(sync_db=tmp_path / "s.db"),
        consoles=_consoles(),
        providers=(_Doubles(),),
    )
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            repository = request.get(DocumentRepository)
    finally:
        container.close()

    assert isinstance(repository, BundleDocumentRepository)


def test_the_repository_provider_is_chosen_by_the_setting_not_by_a_provider_body(tmp_path: Path):
    # dishka resolves a factory's parameters before calling it, so one provider taking both the
    # settings and RawBundleSource would connect ParamikoShell on every mirror-backed run.
    mirrored = _container._repository_provider(CliSettings(xochitl=tmp_path))
    bare = _container._repository_provider(CliSettings())

    assert isinstance(mirrored, MirrorRepositoryProvider)
    assert isinstance(bare, BundleRepositoryProvider)


def test_the_repository_is_bound_at_request_scope_whichever_store_serves_it():
    # A port whose scope moved with an environment variable would make REQUEST_SCOPED_PORTS -- the
    # manifest saying which half a caller may resolve before entering a request -- unanswerable.
    assert DocumentRepository in REQUEST_SCOPED_PORTS
    assert DocumentRepository not in APP_SCOPED_PORTS
    assert MirrorRepositoryProvider.scope is Scope.REQUEST
    assert BundleRepositoryProvider.scope is Scope.REQUEST


def test_the_render_use_case_is_wired_from_the_bound_ports(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            assert isinstance(request.get(RenderPages), RenderPages)
            assert isinstance(request.get(RenderPagesRasterizer), RenderPagesRasterizer)
    finally:
        container.close()


@pytest.mark.parametrize(
    "use_case",
    [
        ListDocuments,
        ResolveDocument,
        CreateDocument,
        SyncDocuments,
        SearchText,
        ReportSyncHistory,
        ReportDeviceFacts,
        ExtractDiagrams,
        ReadAnnotations,
        TranscribePages,
        ReplyOnPage,
    ],
)
def test_every_use_case_is_wired_from_the_bound_ports(settings: CliSettings, use_case: type):
    container = compose(settings=settings, consoles=_consoles(), providers=(_ModelDoubles(),))
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            resolved = request.get(use_case)
    finally:
        container.close()

    assert isinstance(resolved, use_case)


def test_one_rasterizer_instance_serves_both_use_cases_that_declare_the_protocol(
    settings: CliSettings,
):
    # app/diagrams declares the narrow protocol and app/page_annotations a superset adding
    # page_box and a keyword-only underlay. RenderPagesRasterizer satisfies both, so binding it
    # twice would be two renderers memoising the same document summaries separately.
    container = compose(settings=settings, consoles=_consoles(), providers=(_ModelDoubles(),))
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            bridge = request.get(RenderPagesRasterizer)
            diagrams = request.get(ExtractDiagrams)
            annotations = request.get(ReadAnnotations)
    finally:
        container.close()

    assert diagrams._rasterizer is bridge
    assert annotations._rasterizer is bridge


def test_the_transcription_pipeline_is_render_pages_itself(settings: CliSettings):
    # PageRenderPipeline in app/transcribe is satisfied structurally by RenderPages, so the
    # same object is bound rather than a second adapter written.
    container = compose(settings=settings, consoles=_consoles(), providers=(_ModelDoubles(),))
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            pages = request.get(RenderPages)
            transcribe = request.get(TranscribePages)
    finally:
        container.close()

    assert transcribe._pipeline is pages


def test_the_shipped_doubles_can_replace_the_device_bindings(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles(), providers=(_Doubles(),))
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            catalog = request.get(DeviceCatalog)
            facts = request.get(DeviceFactsSource)
            uploader = request.get(DocumentUploader)
            bundles = request.get(RawBundleSource)
    finally:
        container.close()

    assert isinstance(catalog, InMemoryDeviceCatalog)
    assert isinstance(facts, InMemoryDeviceFactsSource)
    assert isinstance(uploader, InMemoryDocumentUploader)
    assert isinstance(bundles, InMemoryRawBundleSource)


def test_the_output_mode_comes_from_the_invocation_not_the_process(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.JSON)}) as request:
            assert request.get(CliOutput).machine_readable is True
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            assert request.get(CliOutput).machine_readable is False
    finally:
        container.close()


def test_the_uploader_is_handed_a_working_clock_and_uuid_factory():
    # SshUploader stamps metadata with both. A frozen clock or a constant uuid would make
    # every created document collide, so these are asserted rather than assumed.
    first = _container._now_ms()
    minted = _container._new_uuid()

    assert first > 1_700_000_000_000
    assert len(minted) == 36
    assert minted != _container._new_uuid()


def test_the_device_provider_binds_every_device_port_at_request_scope():
    assert DeviceProvider.scope is Scope.REQUEST


# -------------------------------------------------------------------------- writeback


def test_the_two_ink_text_types_are_two_types_and_not_one():
    # The whole reason TextToInkEngraver exists. rmspec.render declares InkTextStyle and InkText
    # under the same names the domain port does, and neither pair is the same object -- so a
    # provider that bound text_to_ink's result straight through would be handing the app layer a
    # frozen dataclass where a pydantic model is declared.
    assert InkTextStyle is not EngravedTextStyle
    assert DomainInkText is not EngravedInkText
    assert issubclass(InkTextStyle, BaseModel)
    assert not issubclass(EngravedTextStyle, BaseModel)


def test_the_engraver_reports_the_extent_from_the_corner_the_caller_asked_for():
    # rmspec.render reports (left, top, right, bottom) absolute; the port declares a PhysicalSize.
    # Measuring from the requested corner rather than from the ink's own bounds is what makes
    # ReplyOnPage's `top_mm + height_mm` land on the reply's real bottom edge -- the ink itself
    # starts a whole ascender lower, because the first baseline is one em below top_mm.
    engraver = TextToInkEngraver()

    ink = engraver.engrave(
        "reply",
        screen=PAPER_PRO_SCREEN,
        style=InkTextStyle(
            em_mm=5.0,
            line_height=1.4,
            color=PenColor.BLUE,
            thickness_scale=1.5,
        ),
        left_mm=15.0,
        top_mm=15.0,
        width_mm=150.0,
    )
    raw = text_to_ink(
        "reply",
        screen=PAPER_PRO_SCREEN,
        style=EngravedTextStyle(
            em_mm=5.0,
            line_height=1.4,
            color=PenColor.BLUE,
            thickness_scale=1.5,
        ),
        left_mm=15.0,
        top_mm=15.0,
        width_mm=150.0,
    )
    _, top, right, bottom = raw.extent_mm

    assert isinstance(ink, DomainInkText)
    assert isinstance(ink.extent_mm, PhysicalSize)
    assert ink.extent_mm.width_mm == pytest.approx(right - 15.0)
    assert ink.extent_mm.height_mm == pytest.approx(bottom - 15.0)
    # The naive conversions, both wrong, both pinned so nobody reintroduces one.
    assert ink.extent_mm.width_mm != pytest.approx(right)
    assert ink.extent_mm.height_mm != pytest.approx(bottom - top)
    assert 15.0 + ink.extent_mm.height_mm == pytest.approx(bottom)
    assert top > 15.0


def test_the_engraver_passes_the_style_through_field_by_field_and_keeps_the_strokes():
    # Every field of the domain style has to reach the render style, and the strokes are already
    # rmspec.domain.models.Stroke on both sides, so they are carried rather than rebuilt.
    engraver = TextToInkEngraver()
    style = InkTextStyle(em_mm=8.0, line_height=1.1, color=PenColor.RED, thickness_scale=2.5)

    ink = engraver.engrave(
        "ab cd",
        screen=PAPER_PRO_SCREEN,
        style=style,
        left_mm=10.0,
        top_mm=20.0,
        width_mm=40.0,
    )
    expected = text_to_ink(
        "ab cd",
        screen=PAPER_PRO_SCREEN,
        style=EngravedTextStyle(
            em_mm=8.0,
            line_height=1.1,
            color=PenColor.RED,
            thickness_scale=2.5,
        ),
        left_mm=10.0,
        top_mm=20.0,
        width_mm=40.0,
    )

    assert ink.strokes == expected.strokes
    assert ink.lines == expected.lines
    assert ink.substituted == expected.substituted


def test_the_engraver_reports_undrawable_characters_rather_than_folding_them():
    # The three characters model prose is full of, written as escapes because RUF001 rejects the
    # literals. Nothing folds anywhere in this pipeline: each one is a struck box and is named.
    engraver = TextToInkEngraver()

    ink = engraver.engrave(
        f"not just x {_EM_DASH} it{_APOSTROPHE}s y{_ELLIPSIS}",
        screen=PAPER_PRO_SCREEN,
        style=InkTextStyle(
            em_mm=5.0,
            line_height=1.4,
            color=PenColor.BLUE,
            thickness_scale=1.5,
        ),
        left_mm=15.0,
        top_mm=15.0,
        width_mm=150.0,
    )

    assert ink.substituted == (_EM_DASH, _APOSTROPHE, _ELLIPSIS)
    assert ink.strokes


def test_the_engraver_is_bound_at_app_scope_because_it_is_stateless():
    assert TextEngraver in APP_SCOPED_PORTS
    assert TextEngraver not in REQUEST_SCOPED_PORTS
    assert SceneAppender in APP_SCOPED_PORTS


def test_the_appender_is_its_own_class_and_not_the_codec(settings: CliSettings):
    # Two directions, two classes: only AppendOnlySceneWriter has append_strokes.
    container = compose(settings=settings, consoles=_consoles())
    try:
        appender = container.get(SceneAppender)
        codec = container.get(PageCodec)
    finally:
        container.close()

    assert isinstance(appender, AppendOnlySceneWriter)
    assert appender is not codec
    assert not hasattr(codec, "append_strokes")


def test_the_two_scene_read_types_are_two_types_and_not_one():
    # The measurement that makes WritebackSceneWriter mandatory rather than tidy. Three names
    # declared on both sides of the seam, none of them one class, and the shapes differ too --
    # so `ty` refused the raw writer and pydantic would have refused it again at run time.
    assert WritebackRead is not SceneRead
    assert WritebackPrecondition is not ScenePrecondition
    assert WritebackReceipt is not SceneWriteReceipt
    assert "path" in WritebackRead.model_fields
    assert "location" in SceneRead.model_fields
    assert "digest" in WritebackPrecondition.model_fields
    assert "fingerprint" in ScenePrecondition.model_fields
    assert set(WritebackReceipt.model_fields) - set(SceneWriteReceipt.model_fields) == {
        "path",
        "digest",
        "pruned",
    }
    assert set(SceneWriteReceipt.model_fields) - set(WritebackReceipt.model_fields) == {
        "location",
        "fingerprint",
        "replaced",
        "visibility",
    }


def test_the_two_slices_do_not_fingerprint_a_scene_to_the_same_number():
    # The defect that would have refused every reply. Both are 64 lowercase hex, so each passes
    # the other's constraints and nothing rejects the swap -- verify would just have compared two
    # unrelated values and reported "somebody drew on your page" every single time.
    transport = WritebackRead(
        doc_uuid="doc-1",
        page_id="page-1",
        path=_PAGE_PATH,
        scene=_SCENE,
    ).precondition
    domain = SceneRead(
        doc_uuid="doc-1",
        page_id="page-1",
        location=_PAGE_PATH.value,
        scene=_SCENE,
    ).precondition

    assert transport.digest == _SCENE_DIGEST
    assert domain.fingerprint != _SCENE_DIGEST
    assert domain.fingerprint is not None
    assert len(domain.fingerprint) == len(_SCENE_DIGEST)
    assert set(domain.fingerprint) <= set("0123456789abcdef")


def test_the_writeback_bridge_flattens_the_remote_path_into_the_ports_display_string():
    inner = _ScriptedWriteback(scene=_SCENE)

    read = WritebackSceneWriter(inner).read_scene("doc-1", "page-1")

    assert isinstance(read, SceneRead)
    assert read.location == _PAGE_PATH.value
    assert read.scene == _SCENE
    assert read.precondition.doc_uuid == "doc-1"
    assert read.precondition.page_id == "page-1"


def test_the_writeback_bridge_carries_an_absent_scene_through_as_absent():
    # `scene is None` is half the precondition, so collapsing it to b"" would erase the one
    # distinction ReplyOnPage raises SceneRewriteUnsafe on.
    read = WritebackSceneWriter(_ScriptedWriteback(scene=None)).read_scene("doc-1", "page-1")

    assert read.scene is None
    assert read.precondition.fingerprint is None


def test_a_write_uses_the_transport_identity_from_its_own_read_and_not_the_domains():
    # The whole reason the bridge remembers a read. Passing precondition.fingerprint through as
    # the transport's digest is what would have made every reply look like a stolen page.
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    bridge.write_scene(read.precondition, _OTHER_SCENE)

    assert inner.wrote[0].digest == _SCENE_DIGEST
    assert inner.wrote[0].digest != read.precondition.fingerprint


def test_a_write_under_a_precondition_this_writer_never_produced_is_refused():
    # A guessed translation would write under a number that means something else, so there is
    # nothing to guess with: the read that produced both spellings is the only source.
    bridge = WritebackSceneWriter(_ScriptedWriteback(scene=_SCENE))

    with pytest.raises(UsageError) as refused:
        bridge.write_scene(
            ScenePrecondition(doc_uuid="doc-1", page_id="page-1", fingerprint=_SCENE_DIGEST),
            _OTHER_SCENE,
        )

    assert "read_scene did not produce" in str(refused.value)


def test_a_write_of_zero_bytes_is_refused_before_the_transport_sees_it():
    # The port's own obligation: a zero-byte page is a page whose ink has been deleted.
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    with pytest.raises(UsageError):
        bridge.write_scene(read.precondition, b"")

    assert inner.wrote == []


def test_the_receipts_fingerprint_is_what_reading_the_page_back_would_report():
    # The port requires exactly this equality, so a caller can tell "still mine" from "the human
    # has drawn since" without holding the bytes it wrote. Derived through the domain's own
    # property rather than by re-spelling a digest whose tag is private.
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    receipt = bridge.write_scene(read.precondition, _OTHER_SCENE)
    reread = SceneRead(
        doc_uuid="doc-1",
        page_id="page-1",
        location=_PAGE_PATH.value,
        scene=_OTHER_SCENE,
    )

    assert receipt.fingerprint == reread.precondition.fingerprint
    assert receipt.fingerprint != _OTHER_DIGEST


def test_the_writeback_bridge_takes_replaced_from_the_precondition_the_write_proved():
    # The domain receipt refuses `replaced` and `snapshot` disagreeing about existing, and the
    # transport receipt has no `replaced`. The precondition is the honest source: write_scene
    # succeeds only after verifying the page is what the precondition described.
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    receipt = bridge.write_scene(read.precondition, _OTHER_SCENE)

    assert isinstance(receipt, SceneWriteReceipt)
    assert receipt.replaced == read.precondition.fingerprint
    assert receipt.location == _PAGE_PATH.value
    assert receipt.snapshot == _SNAPSHOT_PATH.value
    assert receipt.byte_count == len(_OTHER_SCENE)
    assert receipt.visibility is SceneVisibility.REOPEN_REQUIRED


def test_the_writeback_bridge_reports_a_created_page_as_superseding_nothing():
    inner = _ScriptedWriteback(scene=None)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    receipt = bridge.write_scene(read.precondition, _SCENE)

    assert receipt.replaced is None
    assert receipt.snapshot is None


def test_the_writeback_bridge_never_claims_the_human_can_already_see_the_write():
    # SceneVisibility has exactly one member and this conversion states it rather than defaulting
    # it, because the enum having no "already visible" is the fact the whole command reports.
    assert list(SceneVisibility) == [SceneVisibility.REOPEN_REQUIRED]

    bridge = WritebackSceneWriter(_ScriptedWriteback(scene=_SCENE))
    read = bridge.read_scene("doc-1", "page-1")

    assert bridge.write_scene(read.precondition, _OTHER_SCENE).visibility is (
        SceneVisibility.REOPEN_REQUIRED
    )


def test_a_page_the_human_drew_on_becomes_a_state_mismatch_and_not_a_protocol_error():
    # The refusal this whole command exists for. SshSceneWriter says DeviceProtocolError, which
    # means "the firmware broke its own contract"; a human picking up a stylus broke the
    # *caller's* precondition, carries `retryable=True`, and gets a re-read remediation.
    inner = _ScriptedWriteback(scene=_SCENE, refuse=_moved_identity(_SCENE_DIGEST))
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    with pytest.raises(DeviceStateMismatchError) as refused:
        bridge.write_scene(read.precondition, _OTHER_SCENE)

    assert refused.value.retryable is True
    assert refused.value.subject == "page page-1 of document doc-1"
    assert refused.value.expected == _identity(_SCENE_DIGEST)
    assert refused.value.observed == _identity(_OTHER_DIGEST)
    assert refused.value.remediation == (
        "re-read page page-1 of document doc-1 and repeat the operation"
    )
    assert isinstance(refused.value.__cause__, DeviceProtocolError)


def test_a_page_that_was_expected_absent_and_is_not_is_also_a_state_mismatch():
    inner = _ScriptedWriteback(scene=None, refuse=_moved_identity(None))
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    with pytest.raises(DeviceStateMismatchError) as refused:
        bridge.write_scene(read.precondition, _SCENE)

    assert refused.value.expected == ABSENT_IDENTITY


def test_a_snapshot_fault_stays_a_protocol_error_rather_than_blaming_the_human():
    # Discriminated positively, so a refusal this class grows later is re-raised untouched
    # instead of being reported as "somebody drew on your page".
    inner = _ScriptedWriteback(
        scene=_SCENE,
        refuse=DeviceProtocolError(
            transport=TransportKind.SSH,
            route=_SNAPSHOT_PATH.value,
            expected=SNAPSHOT_DIR_EXPECTED,
            got=SNAPSHOT_DIR_GOT,
        ),
    )
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    with pytest.raises(DeviceProtocolError) as refused:
        bridge.write_scene(read.precondition, _OTHER_SCENE)

    assert not isinstance(refused.value, DeviceStateMismatchError)
    assert refused.value.expected == SNAPSHOT_DIR_EXPECTED


def test_an_undo_writes_under_the_transport_identity_the_write_left_behind():
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")
    written = bridge.write_scene(read.precondition, _OTHER_SCENE)

    restored = bridge.undo(written)

    assert inner.undone[0].doc_uuid == "doc-1"
    assert inner.undone[0].page_id == "page-1"
    assert inner.undone[0].digest == _OTHER_DIGEST
    assert inner.undone[0].path == _PAGE_PATH
    assert inner.undone[0].snapshot == _SNAPSHOT_PATH
    # The pair inverts: the undo restores exactly what the write superseded.
    assert restored.fingerprint == written.replaced
    assert restored.replaced == written.fingerprint
    assert restored.visibility is SceneVisibility.REOPEN_REQUIRED


def test_an_undo_is_itself_undoable_through_the_same_writer():
    # SshSceneWriter.undo goes through write_scene, which takes its own snapshot. The bridge has
    # to record the restoring receipt too or that property would be unreachable from the port.
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")

    restored = bridge.undo(bridge.write_scene(read.precondition, _OTHER_SCENE))

    assert bridge.undo(restored) is not None
    assert len(inner.undone) == 2


def test_an_undo_of_a_write_that_superseded_nothing_is_refused_rather_than_deleting_a_file():
    inner = _ScriptedWriteback(scene=None)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")
    created = bridge.write_scene(read.precondition, _SCENE)

    with pytest.raises(DeviceOperationUnsupported) as refused:
        bridge.undo(created)

    assert refused.value.supported_by == ()
    assert inner.undone == []


def test_an_undo_of_a_receipt_from_an_earlier_invocation_says_so_rather_than_guessing():
    # The one capability the fingerprint split costs. A stored receipt cannot yield the transport
    # identity an undo writes under, and inventing one would restore a snapshot chosen by a
    # number that means something else.
    bridge = WritebackSceneWriter(_ScriptedWriteback(scene=_SCENE))

    with pytest.raises(UsageError) as refused:
        bridge.undo(
            SceneWriteReceipt(
                doc_uuid="doc-1",
                page_id="page-1",
                location=_PAGE_PATH.value,
                byte_count=len(_OTHER_SCENE),
                fingerprint=_OTHER_DIGEST,
                replaced=_SCENE_DIGEST,
                snapshot=_SNAPSHOT_PATH.value,
                visibility=SceneVisibility.REOPEN_REQUIRED,
            )
        )

    assert "an earlier invocation" in str(refused.value)


def test_an_undo_over_a_page_that_moved_since_the_write_is_refused_in_the_same_vocabulary():
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")
    written = bridge.write_scene(read.precondition, _OTHER_SCENE)
    inner.refuse = _moved_identity(_OTHER_DIGEST)

    with pytest.raises(DeviceStateMismatchError) as refused:
        bridge.undo(written)

    assert refused.value.retryable is True


def test_an_undo_that_hits_a_missing_snapshot_stays_a_protocol_error():
    inner = _ScriptedWriteback(scene=_SCENE)
    bridge = WritebackSceneWriter(inner)
    read = bridge.read_scene("doc-1", "page-1")
    written = bridge.write_scene(read.precondition, _OTHER_SCENE)
    inner.refuse = DeviceProtocolError(
        transport=TransportKind.SSH,
        route=_SNAPSHOT_PATH.value,
        expected=SNAPSHOT_WANTED,
        got=SNAPSHOT_GONE,
    )

    with pytest.raises(DeviceProtocolError) as refused:
        bridge.undo(written)

    assert not isinstance(refused.value, DeviceStateMismatchError)


def test_the_bridge_does_not_republish_the_two_methods_the_port_withholds():
    # A standalone verify would make check-then-act the obvious sequence, and no policy decides
    # anything from a list of backups. Both are the port's stated intent, not an oversight.
    bridge = WritebackSceneWriter(_ScriptedWriteback(scene=_SCENE))

    assert not hasattr(bridge, "verify")
    assert not hasattr(bridge, "snapshots")
    assert hasattr(SshSceneWriter, "verify")
    assert hasattr(SshSceneWriter, "snapshots")


@pytest.mark.parametrize("transport", list(Transport))
def test_the_scene_writer_is_served_over_ssh_on_every_transport(
    transport: Transport,
    tmp_path: Path,
):
    # The write-side twin of the search index: no HTTP route replaces a file in the xochitl tree,
    # so a `usb` run that replies opens a shell too. Constructed only -- nothing is written.
    container = compose(
        settings=CliSettings(transport=transport, xochitl=tmp_path, sync_db=tmp_path / "s.db"),
        consoles=_consoles(),
    )
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            writer = request.get(SceneWriter)
            shell = request.get(ParamikoShell)
    finally:
        container.close()

    assert isinstance(writer, WritebackSceneWriter)
    assert isinstance(writer._writer, SshSceneWriter)
    assert writer._writer._shell is shell
    assert writer._writer._snapshot_root == SNAPSHOT_ROOT
    assert writer._writer._depth == SNAPSHOT_DEPTH


def test_the_scene_writer_holds_one_handshake_per_command():
    assert SceneWriter in REQUEST_SCOPED_PORTS
    assert SceneWriter not in APP_SCOPED_PORTS


def test_the_reply_use_case_is_wired_from_the_three_ports_it_declares(settings: CliSettings):
    container = compose(settings=settings, consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            replier = request.get(ReplyOnPage)
            engraver = container.get(TextEngraver)
            appender = container.get(SceneAppender)
            writer = request.get(SceneWriter)
    finally:
        container.close()

    assert replier._engraver is engraver
    assert replier._appender is appender
    assert replier._writer is writer


@pytest.mark.parametrize("transport", list(Transport))
def test_the_writeback_report_never_claims_the_reply_is_already_visible(transport: Transport):
    # SceneVisibility has one member and this is the report that agrees with it. `served` would
    # claim the human sees the reply; `unavailable` would claim a usb run cannot reply at all.
    result = ReportCapabilities().report(describe_bindings(_settings_for(transport)))
    rows = [row for row in result.restricted if row.port == "SceneWriter"]

    assert "SceneWriter" not in result.served
    assert "SceneWriter" not in {row.port for row in result.unavailable}
    assert all(row.supported_by == () for row in rows[-2:])
    assert "closed and reopened" in rows[-2].detail
    assert "undo in the same invocation" in rows[-1].detail


def test_only_a_usb_or_mirror_writeback_pays_for_a_second_connection():
    over_usb = ReportCapabilities().report(describe_bindings())
    over_ssh = ReportCapabilities().report(describe_bindings(_settings_for(Transport.SSH)))

    def operations(report: ReportCapabilitiesResult, /) -> list[str]:
        return [row.operation for row in report.restricted if row.port == "SceneWriter"]

    assert operations(over_usb) == [
        "write onto an existing page without opening an SSH session",
        "have the tablet redraw a page that was written underneath it",
        "reverse a reply from a later invocation, using a stored receipt",
    ]
    assert operations(over_ssh) == [
        "have the tablet redraw a page that was written underneath it",
        "reverse a reply from a later invocation, using a stored receipt",
    ]


# ----------------------------------------------------------------------------- ocr


def test_only_the_selected_recognisers_are_built(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
):
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        aws_region="eu-west-1",
        ocr_engines=frozenset({OcrEngineName.TEXTRACT}),
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        recognizers = container.get(Sequence[TextRecognizer])
    finally:
        container.close()

    assert len(recognizers) == 1
    assert recording_ocr.regions == ["eu-west-1"]
    assert recording_ocr.vision_calls == 0


def test_the_recogniser_order_is_the_enums_not_the_frozensets(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
    monkeypatch: pytest.MonkeyPatch,
):
    # ocr_engines is a frozenset, so iterating it directly would make the tier ordering depend
    # on the hash seed and two runs of the same command disagree about which engine ran first.
    monkeypatch.setattr(_container, "require_backends", lambda _engines: None)
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        ocr_engines=frozenset(OcrEngineName),
        bda_project_arn=_BDA_PROJECT_ARN,
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        recognizers = container.get(Sequence[TextRecognizer])
    finally:
        container.close()

    assert [r.provider_id for r in recognizers] == [
        "scripted-bda@1",
        "scripted-textract@1",
        "scripted-vision@1",
    ]
    assert recording_ocr.vision_calls == 1


def test_the_bda_binding_is_told_the_project_the_region_and_the_profile(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(_container, "require_backends", lambda _engines: None)
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        ocr_engines=frozenset({OcrEngineName.BDA}),
        bda_project_arn=_BDA_PROJECT_ARN,
        bda_profile="eu.data-automation-v1",
        aws_region="eu-west-1",
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        container.get(Sequence[TextRecognizer])
    finally:
        container.close()

    assert recording_ocr.projects == [(_BDA_PROJECT_ARN, "eu-west-1", "eu.data-automation-v1")]
    assert recording_ocr.regions == [], "the Textract factory must not be reached"


def test_selecting_bda_with_no_project_arn_names_the_setting_rather_than_failing_on_a_page(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
    monkeypatch: pytest.MonkeyPatch,
):
    # The service refuses an absent project with "At least one of project or inline blueprints
    # must be provided", which would arrive as a RecognitionFailed naming no setting, after a
    # page had already been rendered and rasterised. This is the same argument require_backends
    # makes for a missing package, for the one engine whose obstacle is configuration.
    monkeypatch.setattr(_container, "require_backends", lambda _engines: None)
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        ocr_engines=frozenset({OcrEngineName.BDA}),
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        with pytest.raises(InvalidSettingError) as caught:
            container.get(Sequence[TextRecognizer])
    finally:
        container.close()

    assert caught.value.setting == "RMSPEC_BDA_PROJECT_ARN"
    assert "SYNC-type" in caught.value.requirement
    assert exit_code(caught.value) == _EX_CONFIG
    assert recording_ocr.projects == [], "no client is built for a project that cannot be named"


def test_a_bda_project_arn_that_is_not_a_project_arn_is_reported_against_that_setting(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
    monkeypatch: pytest.MonkeyPatch,
):
    # `profile_arn_for` raises ValueError because the name of the environment variable is the
    # composition root's knowledge, not the adapter's. This is where it becomes a named setting.
    monkeypatch.setattr(_container, "require_backends", lambda _engines: None)
    monkeypatch.setattr(_container, "BdaRecognizer", _RefusingBda())
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        ocr_engines=frozenset({OcrEngineName.BDA}),
        bda_project_arn="not-an-arn",
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        with pytest.raises(InvalidSettingError) as caught:
            container.get(Sequence[TextRecognizer])
    finally:
        container.close()

    assert (caught.value.setting, caught.value.value) == ("RMSPEC_BDA_PROJECT_ARN", "not-an-arn")
    assert recording_ocr.projects == [], "the refusing double stood in for the real factory"


def test_a_selected_recogniser_with_no_backend_fails_while_the_binding_is_built(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
    monkeypatch: pytest.MonkeyPatch,
):
    def _refuse(engines: object) -> None:
        assert engines == {OcrEngine.APPLE_VISION}
        raise MissingDependencyError(
            package="Quartz",
            extra="ocr",
            feature="recognising handwriting on device",
        )

    monkeypatch.setattr(_container, "require_backends", _refuse)
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        ocr_engines=frozenset({OcrEngineName.APPLE_VISION}),
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        with pytest.raises(MissingDependencyError):
            container.get(Sequence[TextRecognizer])
    finally:
        container.close()

    assert recording_ocr.vision_calls == 0


def test_the_two_transcription_roles_are_two_models_and_the_bare_port_is_the_reader(
    tmp_path: Path,
    recording_ocr: _RecordingOcr,
):
    # dishka cannot resolve one type to two values, and ExtractDiagrams and ReadAnnotations ask
    # for the bare port because they do not care which model they got. So three keys, two
    # objects: the bare port and the reader role are the same instance.
    settings = CliSettings(
        xochitl=tmp_path,
        sync_db=tmp_path / "s.db",
        aws_region="eu-west-1",
        read_model="reader-id",
        merge_model="judge-id",
    )
    container = compose(settings=settings, consoles=_consoles())
    try:
        bare = container.get(VisionLanguageModel)
        reader = container.get(ReaderModel)
        judge = container.get(AdjudicatorModel)
    finally:
        container.close()

    assert reader is bare
    assert judge is not bare
    assert recording_ocr.model_ids == [
        "reader-id@eu-west-1@eu-west-1",
        "judge-id@eu-west-1@eu-west-1",
    ]


# ------------------------------------------------------------------------- transport


_USB_PORTS: tuple[tuple[type, type], ...] = (
    (DeviceCatalog, UsbCatalog),
    (RawBundleSource, UsbBundleSource),
    (DocumentUploader, UsbUploader),
    (DeviceFactsSource, UsbFacts),
)

_SSH_PORTS: tuple[tuple[type, type], ...] = (
    (DeviceCatalog, SshCatalog),
    (RawBundleSource, SshBundleSource),
    (DocumentUploader, SshUploader),
    (DeviceFactsSource, SshFacts),
)


def _transport_adapter(bound: object, /) -> object:
    """Look through the per-request memoizing seam to the thing that names a transport.

    ``RawBundleSource`` is bound as a ``MemoizingBundleSource`` wrapping the transport's own
    adapter, so "USB is the default read path" is a claim about what it wraps. Every other port
    is bound directly and comes back unchanged, which is why this is one helper rather than a
    branch in each test.
    """
    return bound.source if isinstance(bound, MemoizingBundleSource) else bound


@pytest.mark.parametrize(("port", "expected"), _USB_PORTS)
def test_usb_is_the_default_transport_for_every_port_it_can_serve(
    tmp_path: Path,
    port: type,
    expected: type,
):
    container = compose(settings=_device_settings(tmp_path), consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            bound = request.get(port)
    finally:
        container.close()

    assert isinstance(_transport_adapter(bound), expected)


@pytest.mark.parametrize(("port", "expected"), _SSH_PORTS)
def test_ssh_still_serves_every_port_when_it_is_asked_for(
    tmp_path: Path,
    port: type,
    expected: type,
):
    container = compose(
        settings=_device_settings(tmp_path, transport=Transport.SSH),
        consoles=_consoles(),
    )
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            bound = request.get(port)
    finally:
        container.close()

    assert isinstance(_transport_adapter(bound), expected)


def test_the_bundle_source_is_bound_behind_the_memoizing_seam(tmp_path: Path):
    """Without this, the seam's own tests would prove the behaviour of a class nobody holds.

    Two claims in one: ``RawBundleSource`` resolves to the seam, and the seam wraps the
    transport's adapter rather than replacing it. The sharing the seam buys is a property of the
    *port*, so it only exists if this is what the port resolves to.
    """
    container = compose(settings=_device_settings(tmp_path), consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            bound = request.get(RawBundleSource)
    finally:
        container.close()

    assert isinstance(bound, MemoizingBundleSource)
    assert isinstance(bound.source, UsbBundleSource)


def test_one_command_is_one_seam_and_the_next_command_gets_a_fresh_one(tmp_path: Path):
    """``Scope.REQUEST`` is the whole safety argument, so it is asserted rather than commented.

    Within one command every caller must get the same object, or nothing is shared. Across two
    commands they must not, or the memo becomes a cache with no expiry and a second invocation
    would be served a document the tablet had since changed.
    """
    container = compose(settings=_device_settings(tmp_path), consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            first = request.get(RawBundleSource)
            assert request.get(RawBundleSource) is first
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            assert request.get(RawBundleSource) is not first
    finally:
        container.close()


@pytest.mark.parametrize("port", [port for port, _ in _USB_PORTS])
def test_the_mirror_transport_refuses_rather_than_falling_back_to_ssh(
    tmp_path: Path,
    port: type,
):
    # A silent fallback would make RMSPEC_TRANSPORT=mirror look like it worked while every
    # read still went to the tablet.
    container = compose(
        settings=_device_settings(tmp_path, transport=Transport.MIRROR),
        consoles=_consoles(),
    )
    try:
        with (
            container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request,
            pytest.raises(DeviceOperationUnsupported) as caught,
        ):
            request.get(port)
    finally:
        container.close()

    assert caught.value.transport is TransportKind.LOCAL_MIRROR
    assert caught.value.supported_by == (TransportKind.USB_WEB_API, TransportKind.SSH)


@pytest.mark.parametrize(
    "transport",
    [Transport.USB, Transport.SSH, Transport.MIRROR],
)
def test_the_search_index_is_served_over_ssh_on_every_transport(
    tmp_path: Path,
    transport: Transport,
):
    # The firmware's route table is closed at six families and none serves a file from the
    # xochitl tree, so there is no USB binding to choose between -- a usb run opens a session.
    container = compose(
        settings=_device_settings(tmp_path, transport=transport),
        consoles=_consoles(),
    )
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            source = request.get(SearchIndexSource)
            index = request.get(HandwrittenTextIndex)
    finally:
        container.close()

    assert isinstance(source, SshSearchIndexSource)
    assert isinstance(index, DeviceSearchIndex)


def test_the_usb_web_api_is_pointed_at_the_tablets_http_port(tmp_path: Path):
    container = compose(settings=_device_settings(tmp_path), consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            api = request.get(UsbWebApi)
            endpoint = api._endpoint
    finally:
        container.close()

    assert endpoint == Endpoint(host="10.11.99.1", port=WEB_API_PORT)
    assert WEB_API_PORT == 80


def test_one_command_is_one_web_api_and_the_finalizer_closes_it(tmp_path: Path):
    container = compose(settings=_device_settings(tmp_path), consoles=_consoles())
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            first = request.get(UsbWebApi)
            assert request.get(UsbWebApi) is first
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            assert request.get(UsbWebApi) is not first
    finally:
        container.close()

    # close() is idempotent, so the finalizer having run is asserted by calling it again.
    first.close()


# ------------------------------------------------------------------------- bindings


def test_the_capability_report_describes_the_transport_actually_composed():
    # This pinned COMPOSED_TRANSPORT is TransportKind.SSH, on the premise that UsbWebApi could
    # not be built without importing httpx here. UsbWebApi.over_usb refuted that premise, so
    # USB is the default and the report is a function of the settings rather than a constant.
    assert describe_bindings().transport is TransportKind.USB_WEB_API
    assert describe_bindings(_settings_for(Transport.SSH)).transport is TransportKind.SSH
    assert (
        describe_bindings(_settings_for(Transport.MIRROR)).transport is TransportKind.LOCAL_MIRROR
    )


@pytest.mark.parametrize(
    ("transport", "kind"),
    [
        (Transport.USB, TransportKind.USB_WEB_API),
        (Transport.SSH, TransportKind.SSH),
        (Transport.MIRROR, TransportKind.LOCAL_MIRROR),
    ],
)
def test_every_transport_a_user_can_type_maps_to_one_the_domain_names(
    transport: Transport,
    kind: TransportKind,
):
    assert composed_transport(_settings_for(transport)) is kind


def test_the_default_composition_is_described_without_reading_the_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    # describe_bindings() is called by `doctor` with no argument, and model_construct supplies
    # the declared defaults -- so an exported RMSPEC_TRANSPORT cannot change the report and a
    # bad one cannot raise while the report is being assembled.
    monkeypatch.setenv("RMSPEC_TRANSPORT", "nonsense")

    assert describe_bindings().transport is TransportKind.USB_WEB_API


@pytest.mark.parametrize("transport", list(Transport))
def test_every_described_binding_is_bound_and_every_absence_would_be_explained(
    transport: Transport,
):
    # PortBinding refuses bound=False with no limits, so this holds by construction -- but it
    # holds for every transport, which is the claim worth pinning.
    for binding in describe_bindings(_settings_for(transport)).bindings:
        assert binding.bound or binding.limits


def test_the_report_splits_served_from_limited():
    # This pinned served == ("SearchIndexSource", "HandwrittenTextIndex") while SSH was composed.
    # USB inverts it exactly: the read path is served outright because .rmdoc is a snapshot
    # xochitl assembled, and it is the index and the write side that carry the limits.
    result = ReportCapabilities().report(describe_bindings())

    assert result.transport is TransportKind.USB_WEB_API
    assert result.served == ("DeviceCatalog", "RawBundleSource", "DocumentRepository")
    assert {row.port for row in result.restricted} == {
        "SearchIndexSource",
        "HandwrittenTextIndex",
        "DeviceFactsSource",
        "DocumentUploader",
        "SceneWriter",
    }
    assert result.unavailable == ()


def test_the_ssh_report_still_says_what_it_said_before_usb_became_the_default():
    result = ReportCapabilities().report(describe_bindings(_settings_for(Transport.SSH)))

    assert result.served == ("SearchIndexSource", "HandwrittenTextIndex")
    assert {row.port for row in result.restricted} == {
        "DeviceCatalog",
        "RawBundleSource",
        "DocumentRepository",
        "DeviceFactsSource",
        "DocumentUploader",
        # SceneWriter joined `restricted` when `reply` landed, and it is here even though SSH is
        # the transport that serves it: the reopen limit is a property of the firmware, so no
        # transport writes a page the human can immediately see.
        "SceneWriter",
    }
    assert result.unavailable == ()


def test_the_repository_inherits_the_read_side_limit_of_whatever_serves_it(tmp_path: Path):
    # It *is* the bundle source plus a codec when no mirror is configured, so SSH's torn read is
    # its limit too -- and a configured mirror carries the limit that started this whole change.
    over_ssh = ReportCapabilities().report(describe_bindings(_settings_for(Transport.SSH)))
    mirrored = ReportCapabilities().report(describe_bindings(CliSettings(xochitl=tmp_path)))

    assert [row.operation for row in over_ssh.restricted if row.port == "DocumentRepository"] == [
        "read a document while xochitl is running"
    ]
    assert [row.operation for row in mirrored.restricted if row.port == "DocumentRepository"] == [
        "read a document the mirror at RMSPEC_XOCHITL does not hold"
    ]


def test_a_configured_mirror_keeps_the_repository_bound_even_on_the_mirror_transport(
    tmp_path: Path,
):
    # Either setting alone binds the port, so only the pair being absent is an absence.
    result = ReportCapabilities().report(
        describe_bindings(CliSettings(transport=Transport.MIRROR, xochitl=tmp_path))
    )

    assert "DocumentRepository" not in {row.port for row in result.unavailable}


def test_the_mirror_report_names_the_five_ports_nothing_implements_yet():
    result = ReportCapabilities().report(describe_bindings(_settings_for(Transport.MIRROR)))

    assert result.served == ()
    assert {row.port for row in result.unavailable} == {
        "DeviceCatalog",
        "RawBundleSource",
        "DocumentRepository",
        "DeviceFactsSource",
        "DocumentUploader",
    }
    assert {row.port for row in result.restricted} == {
        "SearchIndexSource",
        "HandwrittenTextIndex",
        # Bound on the mirror transport for the same reason the index is: both resolve the shell
        # directly rather than through the transport seam, so both work and both cost a session.
        "SceneWriter",
    }
    assert all(
        row.supported_by == (TransportKind.USB_WEB_API, TransportKind.SSH)
        for row in result.unavailable
    )
    repository = next(row for row in result.unavailable if row.port == "DocumentRepository")
    assert "RMSPEC_XOCHITL unset as well" in repository.detail


def test_the_search_index_is_a_limit_on_usb_rather_than_an_absence():
    # It is bound and it works; it just costs a second connection. That distinction is what
    # DeviceFacts.unsupported: frozenset[str] structurally cannot express.
    result = ReportCapabilities().report(describe_bindings())
    rows = [row for row in result.restricted if row.port == "SearchIndexSource"]

    assert len(rows) == 1
    assert rows[0].supported_by == (TransportKind.SSH,)
    assert "closed at six families" in rows[0].detail


def test_the_upload_route_reports_all_three_of_its_limits():
    result = ReportCapabilities().report(describe_bindings())
    operations = [row.operation for row in result.restricted if row.port == "DocumentUploader"]

    assert operations == [
        "create a document inside a folder",
        "update a document already on the tablet",
        "delete a document the host just created",
    ]


def test_only_the_folder_limit_names_ssh_as_the_way_round_it():
    result = ReportCapabilities().report(describe_bindings())
    rows = {
        row.operation: row.supported_by
        for row in result.restricted
        if row.port == "DocumentUploader"
    }

    assert rows["create a document inside a folder"] == (TransportKind.SSH,)
    assert rows["update a document already on the tablet"] == ()
    assert rows["delete a document the host just created"] == ()


def test_usb_facts_name_ssh_as_the_transport_that_knows_more():
    result = ReportCapabilities().report(describe_bindings())
    rows = {
        row.operation: row.supported_by
        for row in result.restricted
        if row.port == "DeviceFactsSource"
    }

    assert rows["read the firmware version, the board model and the free space"] == (
        TransportKind.SSH,
    )


@pytest.mark.parametrize("transport", [Transport.USB, Transport.SSH])
def test_the_unreadable_serial_names_no_alternative_transport(transport: Transport):
    result = ReportCapabilities().report(describe_bindings(_settings_for(transport)))
    serial = next(
        row for row in result.restricted if row.operation == "read the serial the tablet UI shows"
    )

    assert serial.port == "DeviceFactsSource"
    assert serial.supported_by == ()
    assert "needs no transport" in serial.refusal


# --------------------------------------------------------------------------- bridge


def test_the_template_builds_a_one_page_request():
    request = _TEMPLATE.request_for(_DOC, 3, background=None)

    assert request.document_uuid == _DOC.uuid
    assert request.selection.indices == (3,)
    assert request.max_pages == 1
    assert request.raster_dpi == 300


def test_the_bridge_pairs_each_page_with_the_results_render_digest():
    raster = _raster()
    bridge = RenderPagesRasterizer(
        pages=_FakeBatchRenderer(raster=raster, digest="digest-7"),
        repository=_FakePageOrder(_summary(_OTHER_PAGE, _PAGE)),
        template=_TEMPLATE,
    )

    rendered = bridge.raster_for(_DOC, _PAGE)

    assert rendered == RasterizedPage(raster=raster, render_digest="digest-7")


def test_the_bridge_turns_a_page_uuid_into_the_index_render_pages_wants():
    renderer = _FakeBatchRenderer(raster=_raster())
    bridge = RenderPagesRasterizer(
        pages=renderer,
        repository=_FakePageOrder(_summary(_OTHER_PAGE, _PAGE)),
        template=_TEMPLATE,
    )

    bridge.raster_for(_DOC, _PAGE)

    assert renderer.requests[0].selection.indices == (1,)


def test_the_diagrams_protocols_two_positional_call_still_binds():
    # ExtractDiagrams calls raster_for(doc_id, page_id) with no keyword at all.
    bridge = RenderPagesRasterizer(
        pages=_FakeBatchRenderer(raster=_raster()),
        repository=_FakePageOrder(_summary(_PAGE)),
        template=_TEMPLATE,
    )

    assert bridge.raster_for(_DOC, _PAGE).render_digest == "render-1"


def test_the_annotations_variant_passes_its_underlay_through_as_a_background():
    renderer = _FakeBatchRenderer(raster=_raster())
    bridge = RenderPagesRasterizer(
        pages=renderer,
        repository=_FakePageOrder(_summary(_PAGE)),
        template=_TEMPLATE,
    )
    underlay = PageUnderlay(
        media=UnderlayMedia.PNG,
        data=_png(1, 1),
        source_size=PhysicalSize(width_mm=210.0, height_mm=297.0),
    )

    bridge.raster_for(_DOC, _PAGE, underlay=underlay)

    background = renderer.requests[0].background
    assert background is not None
    assert background.underlay == underlay


def test_the_page_box_is_the_screen_at_the_templates_raster_density():
    bridge = RenderPagesRasterizer(
        pages=_FakeBatchRenderer(raster=_raster()),
        repository=_FakePageOrder(_summary(_PAGE)),
        template=_TEMPLATE,
    )

    box = bridge.page_box

    assert box.width_px == round(PAPER_PRO_SCREEN.width_mm / 25.4 * 300)


def test_the_summary_is_read_once_per_document():
    order = _FakePageOrder(_summary(_PAGE, _OTHER_PAGE))
    bridge = RenderPagesRasterizer(
        pages=_FakeBatchRenderer(raster=_raster()),
        repository=order,
        template=_TEMPLATE,
    )

    bridge.raster_for(_DOC, _PAGE)
    bridge.raster_for(_DOC, _PAGE)

    assert order.calls == 1


def test_an_unknown_page_is_refused_before_any_rendering_is_paid_for():
    renderer = _FakeBatchRenderer(raster=_raster())
    bridge = RenderPagesRasterizer(
        pages=renderer,
        repository=_FakePageOrder(_summary(_OTHER_PAGE)),
        template=_TEMPLATE,
    )

    with pytest.raises(PageNotFound) as caught:
        bridge.raster_for(_DOC, _PAGE)

    assert caught.value.page == _PAGE.uuid
    assert renderer.requests == []


def test_an_svg_only_render_is_narrowed_into_a_domain_error():
    # RenderPages leaves raster optional because SVG-only render is a real mode, so the
    # narrowing belongs to the bridge rather than to the use case.
    bridge = RenderPagesRasterizer(
        pages=_FakeBatchRenderer(raster=None),
        repository=_FakePageOrder(_summary(_PAGE)),
        template=_TEMPLATE,
    )

    with pytest.raises(RasterizationFailed) as caught:
        bridge.raster_for(_DOC, _PAGE)

    assert caught.value.page_ref == _PAGE.uuid


# ------------------------------------------------------- the bundle-backed repository


def test_the_summary_comes_off_the_bundle_the_device_already_ordered():
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE, template_name="Blank"),
                DevicePageSource(page_id=_OTHER_PAGE.uuid, scene=_OTHER_SCENE),
            )
        }
    )

    summary = _repository(source).summary(_DOC)

    assert summary.doc_id == _DOC
    assert summary.pages == (_PAGE, _OTHER_PAGE)
    assert summary.metadata.visible_name == "Notes"
    assert summary.metadata.kind is DocumentKind.DOCUMENT
    assert summary.metadata.parent_uuid == "folder-1"


@pytest.mark.parametrize(
    ("file_type", "source_kind"),
    [
        (DeviceFileType.NOTEBOOK, SourceKind.NOTEBOOK),
        (DeviceFileType.PDF, SourceKind.PDF),
        (DeviceFileType.EPUB, SourceKind.EPUB),
    ],
)
def test_the_device_file_type_becomes_the_source_kind_the_metadata_publishes(
    file_type: DeviceFileType,
    source_kind: SourceKind,
):
    # ReadAnnotations refuses anything but SourceKind.PDF, so this mapping is the whole reason a
    # PDF-backed document is readable through a bundle at all.
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE),
                document=_device_document(file_type=file_type),
            )
        }
    )

    assert _repository(source).summary(_DOC).metadata.source is source_kind


def test_every_device_file_type_has_a_source_kind():
    # A dict lookup over a closed enum is total until someone widens one of the two enums.
    assert set(_container._SOURCE_KINDS) == set(DeviceFileType)


def test_a_whole_document_carries_ink_absence_and_undecodable_pages_side_by_side():
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE, template_name="Grid"),
                DevicePageSource(page_id=_OTHER_PAGE.uuid, template_name="Blank"),
            )
        }
    )

    document = _repository(source).load(_DOC)

    assert [page.index for page in document.pages] == [0, 1]
    assert document.pages[0].is_readable
    assert document.pages[0].template_name == "Grid"
    assert document.pages[1].content is None
    assert [defect.code for defect in document.pages[1].defects] == [
        PageDefectCode.ARTIFACT_ABSENT
    ]


@pytest.mark.parametrize(
    "failure",
    [
        CorruptPageData(page_uuid=_PAGE.uuid, detail="truncated"),
        UnsupportedPageFormat(
            page_uuid=_PAGE.uuid,
            observed_version="7.0",
            supported_versions=("6.0",),
        ),
    ],
)
def test_a_scene_that_will_not_decode_is_a_defect_rather_than_an_escaping_error(
    failure: Exception,
):
    # The port requires that neither error leaves load_page: one bad page is a defect, not a
    # failed document.
    source = _ScriptedBundleSource(
        {_DOC.uuid: _bundle(DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE))}
    )

    page = _repository(source, codec=_ScriptedCodec(raises=failure)).load_page(_DOC, _PAGE)

    assert page.content is None
    assert [defect.code for defect in page.defects] == [PageDefectCode.CONTENT_UNDECODABLE]


def test_the_codec_is_handed_the_page_uuid_it_needs_to_name_a_failure():
    codec = _ScriptedCodec()
    source = _ScriptedBundleSource(
        {_DOC.uuid: _bundle(DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE))}
    )

    _repository(source, codec=codec).load_page(_DOC, _PAGE)

    assert codec.refs == [_PAGE.uuid]


def test_a_page_the_document_does_not_claim_is_refused_with_the_count_it_does_claim():
    source = _ScriptedBundleSource(
        {_DOC.uuid: _bundle(DevicePageSource(page_id=_OTHER_PAGE.uuid, scene=_SCENE))}
    )

    with pytest.raises(PageNotFound) as caught:
        _repository(source).load_page(_DOC, _PAGE)

    assert caught.value.page == _PAGE.uuid
    assert caught.value.page_count == 1


def test_a_fingerprint_is_the_same_digest_the_mirror_repository_would_produce():
    # Identical scene bytes must fingerprint identically whichever store served them, or a
    # diagram cached from the mirror is re-billed from the tablet.
    source = _ScriptedBundleSource(
        {_DOC.uuid: _bundle(DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE))}
    )

    assert _repository(source).page_fingerprint(_DOC, _PAGE) == _SCENE_DIGEST


def test_a_page_with_no_scene_bytes_is_keyed_by_the_absent_sentinel():
    source = _ScriptedBundleSource({_DOC.uuid: _bundle(DevicePageSource(page_id=_PAGE.uuid))})

    assert _repository(source).page_fingerprint(_DOC, _PAGE) == ABSENT_ARTIFACT_FINGERPRINT


def test_every_page_is_fingerprinted_from_the_one_pull():
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE),
                DevicePageSource(page_id=_OTHER_PAGE.uuid),
            )
        }
    )

    fingerprints = _repository(source).page_fingerprints(_DOC)

    assert fingerprints == {
        _PAGE: _SCENE_DIGEST,
        _OTHER_PAGE: ABSENT_ARTIFACT_FINGERPRINT,
    }
    assert source.pulls == [_DOC.uuid]


def test_the_three_calls_the_use_cases_make_cost_one_pull():
    # ExtractDiagrams asks for the summary, the page and the fingerprint of the same document.
    source = _ScriptedBundleSource(
        {_DOC.uuid: _bundle(DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE))}
    )
    repository = _repository(source)

    repository.summary(_DOC)
    repository.load_page(_DOC, _PAGE)
    repository.page_fingerprint(_DOC, _PAGE)

    assert source.pulls == [_DOC.uuid]


def test_the_memo_holds_one_document_so_a_library_walk_cannot_accumulate_one():
    other = DocumentId(uuid="44444444-4444-4444-4444-444444444444")
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _bundle(DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE)),
            other.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE),
                document=_device_document(uuid=other.uuid),
            ),
        }
    )
    repository = _repository(source)

    repository.summary(_DOC)
    repository.summary(other)
    repository.summary(_DOC)

    assert source.pulls == [_DOC.uuid, other.uuid, _DOC.uuid]


# --------------------------------------------------------------- the memoizing seam


def _pdf_bundle() -> DocumentSourceBundle:
    """One PDF-backed document, so ``base`` carries the underlay bytes the print is read from."""
    return _bundle(
        DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE),
        document=_device_document(file_type=DeviceFileType.PDF),
    )


def test_the_ink_and_the_print_of_one_document_cost_one_pull():
    """The defect: ``rmspec annotations`` pulled the same document twice on a no-mirror run.

    The command reads the ink through ``DocumentRepository`` -- ``BundleDocumentRepository``,
    which memoizes -- and the underlay bytes by resolving ``RawBundleSource`` and calling
    ``load_bundle`` on the port directly. The repository's memo lives inside the repository and
    structurally cannot see that second call, so both callers pulled the whole ``.rmdoc``:
    measured at 13.6 MB where 6.8 MB was needed for one command on ``Quick sheets``, and 5,481
    bytes twice on a small document, which is why it went unnoticed. The seam closes it by being
    the object *both* callers hold.

    What this test does **not** promise: that the wire carried one transfer. ``pulls`` counts
    entries into a Python method on an in-memory double and measures no bytes at all -- the
    13.6 MB is a property of ``UsbBundleSource.load_bundle`` over HTTP, which nothing here
    exercises. What is proven is the seam: two independent callers holding one
    ``RawBundleSource`` reach ``load_bundle`` once between them, so whatever that single call
    costs is paid once instead of twice.
    """
    source = _ScriptedBundleSource({_DOC.uuid: _pdf_bundle()})
    seam = MemoizingBundleSource(source)
    repository = _repository(seam)

    repository.load_page(_DOC, _PAGE)
    underlay = seam.load_bundle(_DOC.uuid).base

    assert source.pulls == [_DOC.uuid]
    assert underlay == b"%PDF-1.7\n"


def test_the_seam_serves_the_same_bundle_object_to_every_caller():
    # Every model a bundle carries is frozen, so sharing one object is free -- and it is what
    # makes "one pull" and "one answer" the same statement.
    source = _ScriptedBundleSource({_DOC.uuid: _pdf_bundle()})
    seam = MemoizingBundleSource(source)

    assert seam.load_bundle(_DOC.uuid) is seam.load_bundle(_DOC.uuid)
    assert source.pulls == [_DOC.uuid]


def test_the_seam_holds_one_document_so_a_library_walk_cannot_accumulate_one():
    # SyncDocuments resolves this port and walks the whole library -- 41 documents measured -- so
    # a dictionary here would hold every .rmdoc in the account before the command ended.
    other = DocumentId(uuid="44444444-4444-4444-4444-444444444444")
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _pdf_bundle(),
            other.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE),
                document=_device_document(uuid=other.uuid),
            ),
        }
    )
    seam = MemoizingBundleSource(source)

    seam.load_bundle(_DOC.uuid)
    seam.load_bundle(other.uuid)
    seam.load_bundle(_DOC.uuid)

    assert source.pulls == [_DOC.uuid, other.uuid, _DOC.uuid]


def test_a_failed_pull_is_not_memoized_and_reaches_every_caller():
    # A cached failure would hand the second caller an error the device never raised for it, and
    # would make one flaky transfer look like a broken document for the rest of the command.
    source = _ScriptedBundleSource(
        raises=DeviceDocumentNotFound(
            transport=TransportKind.USB_WEB_API,
            document_uuid=_DOC.uuid,
        )
    )
    seam = MemoizingBundleSource(source)

    for _ in range(2):
        with pytest.raises(DeviceDocumentNotFound):
            seam.load_bundle(_DOC.uuid)

    assert source.pulls == [_DOC.uuid, _DOC.uuid]


def test_the_seam_names_the_adapter_it_wraps():
    # The transport claim is about what is underneath, so the seam must not hide it.
    source = _ScriptedBundleSource()

    assert MemoizingBundleSource(source).source is source


class _SeamedBundles(Provider):
    """Bind one memoizing seam over a counting double, in the real bundle source's place.

    The double goes *under* the seam rather than in place of it, which is the arrangement the
    composed container has -- so the pull count this yields is the count a real command pays.
    """

    scope = Scope.REQUEST

    def __init__(self, source: RawBundleSource, /) -> None:
        super().__init__()
        self._source = source

    @provide(override=True)
    def bundles(self) -> RawBundleSource:
        return MemoizingBundleSource(self._source)


def test_the_repository_and_a_direct_port_caller_get_the_same_seam(tmp_path: Path):
    """The crux: dishka must hand *one* seam to both, or the sharing does not happen.

    This is the whole arrangement ``rmspec annotations`` has with no mirror configured --
    ``DocumentRepository`` bound to :class:`BundleDocumentRepository` over the bundle source, and
    the command also resolving that same port itself for the underlay bytes. If the seam were
    bound at any scope where two resolutions could return two objects, both would pull.

    It does not promise anything about the wire either: the count below is calls into an
    in-memory double. What it adds over the unit test above is that the container, not the test,
    is what makes both callers hold the same object.
    """
    source = _ScriptedBundleSource({_DOC.uuid: _pdf_bundle()})
    container = compose(
        settings=CliSettings(sync_db=tmp_path / "s.db"),
        consoles=_consoles(),
        providers=(_SeamedBundles(source),),
    )
    try:
        with container(context={Invocation: Invocation(mode=OutputMode.HUMAN)}) as request:
            repository = request.get(DocumentRepository)
            bundles = request.get(RawBundleSource)
            repository.summary(_DOC)
            underlay = bundles.load_bundle(_DOC.uuid).base
    finally:
        container.close()

    assert isinstance(repository, BundleDocumentRepository)
    assert source.pulls == [_DOC.uuid]
    assert underlay == b"%PDF-1.7\n"


def test_listing_every_document_refuses_rather_than_pulling_the_whole_library():
    # DocumentSummary carries page identities and a DeviceCatalog reports a page count, so the
    # only way to answer is one load_bundle per document -- which contradicts the method's own
    # contract, and pages=() would report every document as empty.
    with pytest.raises(DeviceOperationUnsupported) as caught:
        _repository(_ScriptedBundleSource()).list_documents()

    assert caught.value.transport is TransportKind.USB_WEB_API
    assert caught.value.supported_by == (TransportKind.LOCAL_MIRROR,)


def test_a_document_the_device_does_not_hold_is_the_stores_own_absence_error():
    source = _ScriptedBundleSource(
        raises=DeviceDocumentNotFound(
            transport=TransportKind.USB_WEB_API,
            document_uuid=_DOC.uuid,
        )
    )

    with pytest.raises(DocumentNotFound) as caught:
        _repository(source).summary(_DOC)

    assert caught.value.query == _DOC.uuid
    assert caught.value.store == "the tablet over usb_web_api"
    assert isinstance(caught.value.__cause__, DeviceDocumentNotFound)


def test_undecodable_device_metadata_is_a_malformed_document():
    source = _ScriptedBundleSource(
        raises=MalformedDeviceMetadata(
            transport=TransportKind.USB_WEB_API,
            detail="no cPages",
            document_uuid=_DOC.uuid,
        )
    )

    with pytest.raises(MalformedDocument) as caught:
        _repository(source).summary(_DOC)

    assert caught.value.artifact == "device metadata"
    assert "no cPages" in caught.value.detail


def test_every_other_device_failure_is_a_whole_store_failure():
    # A use case catching DocumentSourceError means "I could not get the document", and an
    # unreachable tablet has to be inside that.
    source = _ScriptedBundleSource(
        raises=DeviceTransferInterrupted(
            transport=TransportKind.SSH,
            subject=f"{_DOC.uuid}.rmdoc",
            bytes_transferred=12,
        )
    )

    with pytest.raises(DocumentStoreUnavailable) as caught:
        _repository(source, transport=TransportKind.SSH).summary(_DOC)

    assert caught.value.store == "the tablet over ssh"
    assert isinstance(caught.value.__cause__, DeviceTransferInterrupted)


def test_a_page_identifier_the_domain_refuses_is_reported_as_an_undecodable_page_order():
    # PageId's charset and dot-segment rules exist so an identifier cannot escape its store, so a
    # device that names one is untrusted input rather than a formality.
    source = _ScriptedBundleSource(
        {_DOC.uuid: _bundle(DevicePageSource(page_id="../escape", scene=_SCENE))}
    )

    with pytest.raises(MalformedDocument) as caught:
        _repository(source).summary(_DOC)

    assert caught.value.artifact == "page order"
    assert "../escape" in caught.value.detail


def test_the_redirection_map_does_not_survive_the_bundle_boundary():
    # DevicePageSource carries a page id and a template and nothing else, so pdf_page_index is
    # always None and a reordered PDF falls back to the page's position.
    source = _ScriptedBundleSource(
        {
            _DOC.uuid: _bundle(
                DevicePageSource(page_id=_PAGE.uuid, scene=_SCENE),
                document=_device_document(file_type=DeviceFileType.PDF),
            )
        }
    )

    assert _repository(source).load_page(_DOC, _PAGE).pdf_page_index is None


def _raster() -> RasterImage:
    return RasterImage(
        page_ref=_PAGE.uuid,
        media=ImageMedia.PNG,
        data=_png(8, 8),
        width=8,
        height=8,
        render_dpi=300,
    )


def _consoles() -> ConsolePair:
    return make_console_pair(stdout=io.StringIO(), stderr=io.StringIO())


def _settings_for(transport: Transport, /) -> CliSettings:
    return CliSettings(transport=transport)


def _device_settings(tmp_path: Path, /, *, transport: Transport = Transport.USB) -> CliSettings:
    return CliSettings(
        xochitl=tmp_path / "xochitl",
        sync_db=tmp_path / "sync.db",
        transport=transport,
    )
