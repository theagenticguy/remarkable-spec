"""``rmspec diagram``: a skipped page is a row, the Mermaid verdicts, and the three modes.

How the container is kept off the wire, and why the command's own ``run`` is patched
-----------------------------------------------------------------------------------
A command module calls :func:`rmspec.cli._invoke.run` with no ``providers=``, because a real
invocation must never take any. So a test binds its doubles by replacing the module-level name
the command reaches ``run`` through: the real boundary still opens the writer, loads the
settings, composes the container, enters the request scope and catches every ``RmspecError``,
and only the provider list differs. Four bindings are overridden, each one because leaving it
real would cost money or touch hardware -- the vision model would build a ``bedrock-runtime``
client, the repository would read a xochitl tree off disk, the cache would open SQLite and run
its migrations, and the dependency probe would answer about this interpreter rather than about
the case under test.

The rasterizer is the exception that proves the seam: the **real**
:class:`~rmspec.cli._container.RenderPagesRasterizer` is bound, over a fake ``PageBatchRenderer``
that draws nothing. That bridge's uuid-to-index mapping and optional-raster narrowing are what
this command's page identities travel through, so replacing it wholesale would leave the object
the command really runs against untested while asserting on a shape nothing produces.
"""

from __future__ import annotations

import datetime
import json as json_module
import os
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from dishka import Provider, Scope, provide

from rmspec.app import (
    PageDiagram,
    RenderedPageArtifact,
    RenderPagesRequest,
    RenderPagesResult,
)
from rmspec.app.diagrams import MermaidCheck
from rmspec.cli import _diagram
from rmspec.cli._container import RasterTemplate, RenderPagesRasterizer
from rmspec.cli._diagram import (
    COMMAND,
    DENSE_COLUMNS,
    HUMAN_CAPTION,
    HUMAN_COLUMN_INDICES,
    HUMAN_COLUMNS,
    TRUNCATED_KEY,
    diagram,
)
from rmspec.cli._invoke import PAGE_SPEC_GRAMMAR, Invoked, run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import AmbiguousDocument, DegradationKind, UsageError, exit_code
from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    DocumentId,
    DocumentKind,
    DocumentMetadata,
    DocumentSummary,
    Layer,
    Page,
    PageContent,
    PageContentKind,
    PageDefect,
    PageDefectCode,
    PageId,
    PenColor,
    PenType,
    SourceKind,
    Stroke,
)
from rmspec.domain.ports.device import DeviceCatalog, DeviceDocument, DeviceFileType
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.export import ImageMedia as ExportMedia
from rmspec.domain.ports.export import RasterImage as ExportRaster
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT, DocumentRepository
from rmspec.domain.ports.ocr import StopReason, VisionCompletion, VisionLanguageModel
from rmspec.domain.ports.persistence import DiagramCache
from rmspec.domain.ports.render import PhysicalSize, RenderedPage

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rmspec.domain.models import Document
    from rmspec.domain.ports.ocr import VisionRequest

DOC = "d3b38661-1111-4111-8111-111111111111"
OTHER = "d3b38661-2222-4222-8222-222222222222"
NAME = "Frontier workstream"
QUERY = "Frontier"
MODEL_FINGERPRINT = "scripted-vision@1"
RENDER_DIGEST = "render-digest-1"

FLOWCHART = "flowchart TD\n    a[start] --> b[stop]"
DIAGRAM_ANSWER = f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```\n"
TEXT_ANSWER = "TEXT\n"
UNREADABLE_ANSWER = "I am afraid I cannot make this page out at all.\n"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
WIDTH = 8
HEIGHT = 12
NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)

Answer = tuple[str, StopReason]

_OVERRIDDEN_PORTS = (
    DependencyProbe,
    DeviceCatalog,
    DiagramCache,
    DocumentRepository,
    VisionLanguageModel,
)
"""Every port a double is bound over, listed to give each import a runtime use.

dishka reads a provider method's return annotation at container build, and this module has
``from __future__ import annotations``, so a name moved into an ``if TYPE_CHECKING:`` block
makes the container raise instead of resolving. Same discipline, and same reason, as
``_container.BOUND_PORTS``: the repo allows neither a ``noqa`` nor a ``type: ignore``.
"""


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ``RMSPEC_*`` shell out of what these tests measure."""
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


def make_png(width: int, height: int) -> bytes:
    """Return PNG bytes the export raster twin accepts: signature, matching IHDR, trailer."""
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


def make_page(index: int, *, content: PageContent | None = None) -> Page:
    """Build one page, absent content included, with the defect the domain requires for it."""
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
    )


def document(uuid: str, /, *, name: str = NAME, page_count: int = 1) -> DeviceDocument:
    """Build one catalog entry."""
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=DeviceFileType.NOTEBOOK,
        page_count=page_count,
    )


ONE_DOCUMENT = (document(DOC),)
TWO_DOCUMENTS = (document(DOC, page_count=3), document(OTHER, name=f"{NAME} draft"))


class _Repository:
    """A :class:`DocumentRepository` over prebuilt pages, tolerant of which uuid was chosen."""

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


class _Cache:
    """A :class:`DiagramCache` that either always hits with one artifact or always misses."""

    def __init__(self, *, hit: DiagramArtifact | None = None) -> None:
        self.stored: list[DiagramArtifact] = []
        self._hit = hit

    def get(self, _key: DiagramCacheKey, /) -> DiagramArtifact | None:
        return self._hit

    def put(self, _key: DiagramCacheKey, artifact: DiagramArtifact, /) -> None:
        self.stored.append(artifact)

    def superseded(self, _key: DiagramCacheKey, /) -> DiagramCacheKey | None:
        return None


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
        model: _Model,
        cache: _Cache,
        probe: _Probe,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._model = model
        self._cache = cache
        self._probe = probe

    @provide(override=True)
    def repository(self) -> DocumentRepository:
        return self._repository

    @provide(override=True)
    def model(self) -> VisionLanguageModel:
        return self._model

    @provide(override=True)
    def cache(self) -> DiagramCache:
        return self._cache

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        return self._probe


class _RequestDoubles(Provider):
    """The catalog, and the real bridge over a renderer that draws nothing."""

    scope = Scope.REQUEST

    def __init__(self, *, documents: tuple[DeviceDocument, ...], renderer: _Renderer) -> None:
        super().__init__()
        self._documents = documents
        self._renderer = renderer

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        return InMemoryDeviceCatalog(documents=self._documents)

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
    renderer: _Renderer
    model: _Model
    cache: _Cache
    probe: _Probe


def wire(
    monkeypatch: pytest.MonkeyPatch,
    *pages: Page,
    answers: dict[str, Answer] | None = None,
    hit: DiagramArtifact | None = None,
    absent: frozenset[str] = frozenset(),
    documents: tuple[DeviceDocument, ...] = ONE_DOCUMENT,
) -> Wiring:
    """Patch the command's ``run`` so that its container is composed with doubles."""
    summary = DocumentSummary(
        doc_id=DocumentId(uuid=DOC),
        metadata=DocumentMetadata(
            visible_name=NAME,
            kind=DocumentKind.DOCUMENT,
            source=SourceKind.PDF,
        ),
        pages=tuple(page.page_id for page in pages),
    )
    wiring = Wiring(
        repository=_Repository(summary, {page.page_id.uuid: page for page in pages}),
        renderer=_Renderer(),
        model=_Model(answers or {}),
        cache=_Cache(hit=hit),
        probe=_Probe(absent=absent),
    )
    providers = (
        _AppDoubles(
            repository=wiring.repository,
            model=wiring.model,
            cache=wiring.cache,
            probe=wiring.probe,
        ),
        _RequestDoubles(documents=documents, renderer=wiring.renderer),
    )

    def patched(
        body: Callable[[Invoked], int],
        /,
        *,
        json: bool = False,
        dense: bool = False,
    ) -> int:
        return run(body, json=json, dense=dense, providers=providers)

    monkeypatch.setattr(_diagram, "run", patched)
    return wiring


def answering(text: str, stop_reason: StopReason = StopReason.COMPLETE) -> dict[str, Answer]:
    """Script one answer for page zero."""
    return {page_uuid(0): (text, stop_reason)}


def envelope(captured: str, /) -> dict[str, Any]:
    """Parse one JSON envelope off stdout."""
    return json_module.loads(captured)


def records(captured: str, /) -> list[list[str]]:
    """Split a dense stream into its records."""
    return [line.split("\t") for line in captured.splitlines()]


def indices(document_: dict[str, Any], /) -> list[int]:
    """Read every page index out of one envelope."""
    return [page["page_index"] for page in document_["data"]["pages"]]


def kinds(document_: dict[str, Any], /) -> set[str]:
    """Read every degradation kind out of one envelope."""
    return {entry["kind"] for entry in document_["degradations"]}


# ───────────────────────────── the frozen contract ─────────────────────────────


def test_the_discriminator_is_read_from_the_manifest_table():
    assert RESPONSE_TYPES[COMMAND] == "diagrams"


def test_the_pages_help_pastes_the_one_shared_grammar_sentence():
    assert diagram.__doc__ is not None
    assert PAGE_SPEC_GRAMMAR in " ".join(diagram.__doc__.split())


def test_a_row_model_really_does_omit_truncated_from_its_dump():
    # The measurement behind _payload: truncated is a @property, and pydantic dumps fields.
    row = PageDiagram(
        page_index=0,
        page_ref=page_uuid(0),
        artifact=DiagramArtifact(
            content_kind=PageContentKind.DIAGRAM,
            mermaid=FLOWCHART,
            diagram_kind="flowchart",
            created_at=NOW,
        ),
        skipped=None,
        check=MermaidCheck.UNCHECKED,
        stop_reason=StopReason.OUTPUT_LIMIT,
        served_from_cache=False,
    )

    assert row.truncated is True
    assert TRUNCATED_KEY not in row.model_dump(mode="json")


# ───────────────────────────── json ─────────────────────────────


def test_json_carries_the_mermaid_the_verdict_and_what_it_cost(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wiring = wire(monkeypatch, make_page(0, content=ink()), answers=answering(DIAGRAM_ANSWER))

    assert diagram(QUERY, json=True) == 0

    document_ = envelope(capsys.readouterr().out)
    assert document_["type"] == "diagrams"
    assert document_["degradations"] == []
    page = document_["data"]["pages"][0]
    assert page["page_index"] == 0
    assert page["artifact"]["mermaid"] == FLOWCHART
    assert page["artifact"]["diagram_kind"] == "flowchart"
    assert page["check"] == MermaidCheck.UNCHECKED.value
    assert page["skipped"] is None
    assert page["served_from_cache"] is False
    assert page[TRUNCATED_KEY] is False
    assert wiring.model.calls == 1
    assert wiring.cache.stored[0].mermaid == FLOWCHART


def test_the_computed_truncated_is_put_back_into_the_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wire(
        monkeypatch,
        make_page(0, content=ink()),
        answers=answering(DIAGRAM_ANSWER, StopReason.OUTPUT_LIMIT),
    )

    assert diagram(QUERY, json=True) == 0

    page = envelope(capsys.readouterr().out)["data"]["pages"][0]
    assert page["stop_reason"] == StopReason.OUTPUT_LIMIT.value
    assert page[TRUNCATED_KEY] is True


def test_an_unannotated_page_is_a_row_and_a_degradation_rather_than_a_crash(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # The named legacy defect: diagram_cmd.py handed a None path to the parser and raised
    # TypeError, having already disabled its own cache one line earlier.
    wiring = wire(monkeypatch, make_page(0))

    assert diagram(QUERY, json=True) == 0

    document_ = envelope(capsys.readouterr().out)
    page = document_["data"]["pages"][0]
    assert page["artifact"] is None
    assert page["skipped"] == "no_ink"
    assert page["check"] == MermaidCheck.NOT_APPLICABLE.value
    assert page[TRUNCATED_KEY] is False
    assert kinds(document_) == {DegradationKind.PAGE_NOT_ANNOTATED.value}
    assert wiring.model.calls == 0
    assert wiring.renderer.requests == []


def test_a_verdict_nobody_can_read_is_data_and_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wire(monkeypatch, make_page(0, content=ink()), answers=answering(UNREADABLE_ANSWER))

    assert diagram(QUERY, json=True) == 0

    page = envelope(capsys.readouterr().out)["data"]["pages"][0]
    assert page["skipped"] == "unreadable_verdict"
    assert page["artifact"] is None


def test_a_cache_hit_says_it_did_not_pay(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    stored = DiagramArtifact(
        content_kind=PageContentKind.MIXED,
        mermaid=FLOWCHART,
        diagram_kind="flowchart",
        created_at=NOW,
    )
    wiring = wire(monkeypatch, make_page(0, content=ink()), hit=stored)

    assert diagram(QUERY, json=True) == 0

    page = envelope(capsys.readouterr().out)["data"]["pages"][0]
    assert page["served_from_cache"] is True
    assert page["check"] == MermaidCheck.UNCHECKED.value
    assert wiring.model.calls == 0


# ───────────────────────────── dense and human ─────────────────────────────


def test_dense_projects_the_identity_and_what_the_command_is_for(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wire(
        monkeypatch,
        make_page(0, content=ink()),
        make_page(1, content=ink()),
        make_page(2),
        answers={
            page_uuid(0): (DIAGRAM_ANSWER, StopReason.OUTPUT_LIMIT),
            page_uuid(1): (TEXT_ANSWER, StopReason.COMPLETE),
        },
    )

    assert diagram(QUERY, dense=True) == 0

    written = records(capsys.readouterr().out)
    assert written[0] == list(DENSE_COLUMNS)
    assert written[1] == [
        "0",
        "diagram",
        "unchecked",
        "",
        "false",
        "true",
        FLOWCHART.replace("\n", " "),
    ]
    assert written[2] == ["1", "text", "not_applicable", "", "false", "false", ""]
    assert written[3] == ["2", "", "not_applicable", "no_ink", "false", "false", ""]


def test_dense_keeps_its_record_stream_clean_and_still_warns_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wire(monkeypatch, make_page(0))

    assert diagram(QUERY, dense=True) == 0

    captured = capsys.readouterr()
    assert "no_ink" in captured.out
    assert "not annotated" in captured.err.replace("_", " ")


def test_the_human_projection_is_a_narrowing_of_the_dense_one_and_names_its_choice():
    # Taken by index out of the same record, so the two renderings cannot drift: a rename in
    # DENSE_COLUMNS moves the human header with it, and the indices are the one thing a reorder
    # has to correct.
    assert HUMAN_COLUMNS == ("page_index", "content_kind", "skipped", "truncated")
    assert tuple(DENSE_COLUMNS[index] for index in HUMAN_COLUMN_INDICES) == HUMAN_COLUMNS
    assert set(HUMAN_COLUMNS) < set(DENSE_COLUMNS)


def test_the_default_mode_writes_nothing_to_stdout_and_puts_its_table_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # The rule this test exists for: stdout carries the machine-consumable payload and nothing
    # else. A default invocation that wrote tab-separated records there made `2>/dev/null` a lie
    # and `| jq` a parse error, and no test caught it.
    wire(
        monkeypatch,
        make_page(0, content=ink()),
        make_page(1),
        answers=answering(DIAGRAM_ANSWER),
    )

    assert diagram(QUERY) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "\t" not in captured.err
    shown = " ".join(captured.err.split())
    for header in HUMAN_COLUMNS:
        assert header in shown
    assert "diagram" in shown
    assert "no_ink" in shown
    assert "cached" not in shown
    assert HUMAN_CAPTION in shown


# ───────────────────────────── the shared policies ─────────────────────────────


def test_the_probe_runs_before_the_command_touches_a_use_case(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink()),
        answers=answering(DIAGRAM_ANSWER),
        absent=frozenset({"rmscene"}),
    )

    assert diagram(QUERY, json=True) == 78

    assert envelope(capsys.readouterr().out)["error"]["type"] == "MissingDependencyError"
    assert wiring.repository.loaded == []
    assert wiring.model.calls == 0


def test_the_probe_names_the_scene_the_raster_and_the_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wiring = wire(monkeypatch, make_page(0, content=ink()), answers=answering(DIAGRAM_ANSWER))

    assert diagram(QUERY, json=True) == 0

    capsys.readouterr()
    assert {"rmscene", "boto3"} <= set(wiring.probe.asked)


def test_an_ambiguous_selector_is_accepted_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wire(
        monkeypatch,
        make_page(0, content=ink()),
        answers=answering(DIAGRAM_ANSWER),
        documents=TWO_DOCUMENTS,
    )

    assert diagram(QUERY, json=True) == 0

    reported = kinds(envelope(capsys.readouterr().out))

    assert DegradationKind.AMBIGUOUS_AUTO_RESOLVED.value in reported


def test_strict_refuses_the_same_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wiring = wire(monkeypatch, make_page(0, content=ink()), documents=TWO_DOCUMENTS)

    status = diagram(QUERY, strict=True, json=True)

    assert status == exit_code(AmbiguousDocument(query=QUERY, candidates=()))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "AmbiguousDocument"
    assert wiring.model.calls == 0


def test_the_cap_comes_from_the_setting_and_the_flag_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "1")
    answers = {
        page_uuid(0): (DIAGRAM_ANSWER, StopReason.COMPLETE),
        page_uuid(1): (DIAGRAM_ANSWER, StopReason.COMPLETE),
    }
    both = (make_page(0, content=ink()), make_page(1, content=ink()))
    wire(monkeypatch, *both, answers=answers)

    refused = diagram(QUERY, json=True)

    assert refused == exit_code(UsageError(subject="x", requirement="y"))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "UsageError"

    wire(monkeypatch, *both, answers=answers)
    assert diagram(QUERY, max_pages=2, json=True) == 0
    assert indices(envelope(capsys.readouterr().out)) == [0, 1]


def test_pages_selects_by_zero_based_index(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wiring = wire(
        monkeypatch,
        make_page(0, content=ink()),
        make_page(1, content=ink()),
        answers={page_uuid(1): (DIAGRAM_ANSWER, StopReason.COMPLETE)},
    )

    assert diagram(QUERY, pages="1", json=True) == 0

    assert indices(envelope(capsys.readouterr().out)) == [1]
    assert wiring.repository.loaded == [page_uuid(1)]


def test_limit_takes_the_leading_pages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    wire(
        monkeypatch,
        make_page(0, content=ink()),
        make_page(1, content=ink()),
        answers=answering(DIAGRAM_ANSWER),
    )

    assert diagram(QUERY, limit=1, json=True) == 0

    assert indices(envelope(capsys.readouterr().out)) == [0]


def test_the_two_output_flags_together_are_refused(capsys: pytest.CaptureFixture[str]):
    status = diagram(QUERY, json=True, dense=True)

    assert status == exit_code(UsageError(subject="x", requirement="y"))
    assert envelope(capsys.readouterr().out)["error"]["type"] == "UsageError"
