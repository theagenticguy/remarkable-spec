"""The no-ink guard, the cache rule, the answer contract, and "nobody checked this".

How the ports are bound here, and why
-------------------------------------
With local in-memory fakes annotated against the Protocols, for the reasons
``test_app_resolve``'s docstring gives and which apply unchanged: ``rmspec.app`` may import
``rmspec.domain`` and nothing else, the architecture check only scans ``src/``, so an adapter
import here would pass the gate while breaking the property the gate exists to protect --
and importing a shipped doubles subpackage runs its parent's ``__init__``, which binds the
real adapters and therefore their third-party libraries. Conformance is still checked, by
the type gate rather than by convention: every fake below is passed to a Protocol-annotated
parameter, so ``ty`` verifies it structurally at the construction site.

Two fakes are shaped by the ports' own instructions rather than by convenience. The model is
a dictionary keyed on ``RasterImage.page_ref``, never ``script.pop(0)`` -- which is the
purpose that field is documented to have, since a queue-shaped double happily answers about
another page. The cache holds whole keys rather than digests alone, because
``superseded`` is defined over ``page_hash`` and only a store that kept the key payload can
implement it.

The render pipeline is a fake of a *sibling use case*, not of a port. ``rmspec.app.diagrams``
declares the two members it needs from ``RenderPages`` as narrow structural Protocols, so
this fake is what pins that expectation: it returns the **export** slice's raster twin, which
is what a real ``SvgRasterizer`` produces, so the conversion this module performs --
``RasterImage.from_raster`` -- is exercised across the twin boundary rather than assumed.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, NamedTuple

import pytest
from pydantic import BaseModel, ValidationError

from rmspec.app import diagrams
from rmspec.app.diagrams import (
    DiagramSkipReason,
    ExtractDiagrams,
    ExtractDiagramsRequest,
    ExtractDiagramsResult,
    MermaidCheck,
    PageDiagram,
    PageRasterizer,
    RasterizedPage,
)
from rmspec.app.selection import PageSelection
from rmspec.domain.errors import DegradationKind, UsageError
from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    Document,
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
from rmspec.domain.ports.export import ImageMedia as ExportMedia
from rmspec.domain.ports.export import RasterImage as ExportRaster
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT, DocumentRepository
from rmspec.domain.ports.ocr import (
    RasterImage,
    StopReason,
    VisionCompletion,
    VisionLanguageModel,
    VisionRequest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rmspec.domain.ports.persistence import DiagramCache

DOC = DocumentId(uuid="d3b38661-1111-4111-8111-111111111111")
NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)
MODEL = "bedrock-opus@3"
RENDER = "render-digest-1"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
WIDTH = 8
HEIGHT = 12

FLOWCHART = "flowchart TD\n    a[start] --> b[stop]"


def make_png(width: int, height: int) -> bytes:
    """Return PNG bytes the export twin accepts: signature, a matching IHDR, and IEND.

    The twin validates its declared pixel size against the ``IHDR`` chunk and requires the
    trailer, so a fake cannot hand over ``b"png:" + svg`` and still be assignable.
    """
    return (
        PNG_MAGIC
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + PNG_IEND
    )


def raster_for(page_ref: str) -> ExportRaster:
    """Build the export slice's raster twin, which is what a real rasterizer returns."""
    return ExportRaster(
        page_ref=page_ref,
        media=ExportMedia.PNG,
        data=make_png(WIDTH, HEIGHT),
        width=WIDTH,
        height=HEIGHT,
        render_dpi=229,
    )


class Rendered(BaseModel, frozen=True, extra="forbid"):
    """One page's pixels plus the render identity, as the pipeline hands them back."""

    raster: ExportRaster
    render_digest: str


class Answer(NamedTuple):
    """One scripted model answer: a body, and why generation stopped."""

    text: str
    stop_reason: StopReason = StopReason.COMPLETE


class InMemoryRepository:
    """A :class:`DocumentRepository` over prebuilt pages, with fingerprints held apart.

    Fingerprints live in their own mapping rather than being derived from content, which is
    the second of the three knobs that port's docstring requires: a cache-hit test over
    fingerprints derived from content would be tautological, and a real store does produce
    the same content under a changed fingerprint.
    """

    def __init__(
        self,
        summary: DocumentSummary,
        pages: dict[str, Page],
        fingerprints: dict[str, str],
    ) -> None:
        self.loaded: list[str] = []
        self._summary = summary
        self._pages = pages
        self._fingerprints = fingerprints

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """List the one document this fake holds."""
        return (self._summary,)

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Return the document's page order without decoding anything."""
        assert doc_id == self._summary.doc_id
        return self._summary

    def load(self, doc_id: DocumentId, /) -> Document:
        """Assemble the whole document, which this use case never asks for."""
        assert doc_id == self._summary.doc_id
        return Document(
            doc_id=self._summary.doc_id,
            metadata=self._summary.metadata,
            pages=tuple(self._pages[page.uuid] for page in self._summary.pages),
        )

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Return one page exactly as ``load`` would place it."""
        assert doc_id == self._summary.doc_id
        self.loaded.append(page_id.uuid)
        return self._pages[page_id.uuid]

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Fingerprint one page's stored bytes, or report that it has none."""
        assert doc_id == self._summary.doc_id
        return self._fingerprints.get(page_id.uuid, ABSENT_ARTIFACT_FINGERPRINT)

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Fingerprint every claimed page in one pass, which this use case never asks for."""
        assert doc_id == self._summary.doc_id
        return {page: self.page_fingerprint(doc_id, page) for page in self._summary.pages}


class FakeRasterizer:
    """A :class:`PageRasterizer` that renders nothing and reports what it was asked for."""

    def __init__(self, *, render_digest: str = RENDER) -> None:
        self.calls: list[str] = []
        self._render_digest = render_digest

    def raster_for(self, doc_id: DocumentId, page_id: PageId, /) -> Rendered:
        """Return fixed pixels stamped with the page they were asked for."""
        assert doc_id == DOC
        self.calls.append(page_id.uuid)
        return Rendered(raster=raster_for(page_id.uuid), render_digest=self._render_digest)


class ScriptedModel:
    """A :class:`VisionLanguageModel` whose answers are keyed on the page in the request."""

    def __init__(self, answers: dict[str, Answer], *, fingerprint: str = MODEL) -> None:
        self.requests: list[VisionRequest] = []
        self._answers = answers
        self._fingerprint = fingerprint

    @property
    def fingerprint(self) -> str:
        """Identity of this binding, which the cache key folds in."""
        return self._fingerprint

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Answer about whichever page the attached raster names."""
        self.requests.append(request)
        answer = self._answers[request.images[0].page_ref]
        return VisionCompletion.answering(
            request,
            fingerprint=self._fingerprint,
            text=answer.text,
            stop_reason=answer.stop_reason,
        )


class InMemoryDiagramCache:
    """A :class:`DiagramCache` holding whole keys, so ``superseded`` is implementable."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[DiagramCacheKey, DiagramArtifact]] = {}
        self.stored: list[tuple[DiagramCacheKey, DiagramArtifact]] = []
        self.reads = 0

    def get(self, key: DiagramCacheKey, /) -> DiagramArtifact | None:
        """Return the row under this exact digest, or ``None``."""
        self.reads += 1
        row = self.rows.get(key.digest)
        if row is None:
            return None
        return row[1]

    def put(self, key: DiagramCacheKey, artifact: DiagramArtifact, /) -> None:
        """Store a row, and record that it was stored."""
        self.rows[key.digest] = (key, artifact)
        self.stored.append((key, artifact))

    def superseded(self, key: DiagramCacheKey, /) -> DiagramCacheKey | None:
        """Return a stored key for the same page under other inputs, greatest digest first."""
        if key.digest in self.rows:
            return None
        others = sorted(
            stored.digest
            for stored, _ in self.rows.values()
            if stored.page_hash == key.page_hash and stored.digest != key.digest
        )
        if not others:
            return None
        return self.rows[others[-1]][0]


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


def page_uuid(index: int) -> str:
    """Return a stable page identifier for a position."""
    return f"page-{index}"


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


class Wiring(NamedTuple):
    """One wired use case and every fake behind it, so a test may assert on any of them."""

    extractor: ExtractDiagrams
    repository: InMemoryRepository
    rasterizer: FakeRasterizer
    model: ScriptedModel
    cache: InMemoryDiagramCache


def build(
    *pages: Page,
    answers: dict[str, Answer] | None = None,
    cache: InMemoryDiagramCache | None = None,
    fingerprints: dict[str, str] | None = None,
) -> Wiring:
    """Wire the use case over fakes, and hand every fake back for assertions."""
    summary = DocumentSummary(
        doc_id=DOC,
        metadata=DocumentMetadata(
            visible_name="Contract",
            kind=DocumentKind.DOCUMENT,
            source=SourceKind.PDF,
        ),
        pages=tuple(page.page_id for page in pages),
    )
    prints = (
        fingerprints
        if fingerprints is not None
        else {
            page.page_id.uuid: f"hash-{page.page_id.uuid}"
            for page in pages
            if page.content is not None
        }
    )
    repository = InMemoryRepository(summary, {page.page_id.uuid: page for page in pages}, prints)
    rasterizer = FakeRasterizer()
    model = ScriptedModel(answers or {})
    rows = cache if cache is not None else InMemoryDiagramCache()
    extractor = ExtractDiagrams(
        repository=repository,
        rasterizer=rasterizer,
        model=model,
        cache=rows,
    )
    return Wiring(extractor, repository, rasterizer, model, rows)


def request_for(
    pages: PageSelection | None = None, *, max_pages: int = 10
) -> ExtractDiagramsRequest:
    """Build a request over the whole document unless a narrower selection is given."""
    return ExtractDiagramsRequest(
        doc_id=DOC,
        pages=pages if pages is not None else PageSelection.all(),
        max_pages=max_pages,
        now=NOW,
    )


def extract(*pages: Page, answers: dict[str, Answer] | None = None) -> ExtractDiagramsResult:
    """Run the use case over the given pages and return only the result."""
    extractor, _, _, _, _ = build(*pages, answers=answers)
    return extractor.extract(request_for())


def answering(text: str, stop_reason: StopReason = StopReason.COMPLETE) -> dict[str, Answer]:
    """Script one answer for page zero."""
    return {page_uuid(0): Answer(text=text, stop_reason=stop_reason)}


def assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error.

    A direct ``result.pages = ...`` is rejected by the type gate before the test can prove
    pydantic rejects it at run time, and this repository allows no suppression comment to get
    past that. A variable field name also keeps ``B010`` quiet without one.
    """
    setattr(target, field, value)


# ───────────────────────────── the fakes are the ports ─────────────────────────────


def test_the_fakes_are_the_ports_the_use_case_declares():
    repository: DocumentRepository = InMemoryRepository(
        DocumentSummary(
            doc_id=DOC,
            metadata=DocumentMetadata(visible_name="x", kind=DocumentKind.DOCUMENT),
            pages=(),
        ),
        {},
        {},
    )
    rasterizer: PageRasterizer = FakeRasterizer()
    model: VisionLanguageModel = ScriptedModel({})
    cache: DiagramCache = InMemoryDiagramCache()
    rendered: RasterizedPage = Rendered(raster=raster_for("page-0"), render_digest=RENDER)
    assert repository.list_documents()[0].page_count == 0
    assert rasterizer.raster_for(DOC, PageId(uuid=page_uuid(0))).render_digest == RENDER
    assert model.fingerprint == MODEL
    assert cache.get(_key("h")) is None
    assert rendered.raster.width == WIDTH


def test_the_re_exported_surface_is_models_and_use_cases_only():
    """Why the two enums stay out of ``__all__``.

    The surface sweep treats every non-model exported class as a use case and asserts
    keyword-only collaborators over its ``__init__``, which a ``StrEnum`` cannot satisfy.
    """
    for name in diagrams.__all__:
        exported = getattr(diagrams, name)
        assert isinstance(exported, type)
        assert issubclass(exported, BaseModel) or exported is ExtractDiagrams, name


# ──────────────────── a page with no ink is skipped, never crashed on ────────────────────


def _key(page_hash: str, *, render_digest: str = RENDER) -> DiagramCacheKey:
    return DiagramCacheKey(
        page_hash=page_hash,
        render_digest=render_digest,
        raster_digest="raster",
        model_fingerprint=MODEL,
        request_digest="req",
    )


def test_an_unannotated_page_of_a_pdf_backed_document_does_not_crash():
    """The legacy defect: ``diagram_cmd.py`` hands the parser a ``None`` path and raises."""
    result = extract(make_page(0, content=None))
    (page,) = result.pages
    assert page.skipped is DiagramSkipReason.NO_INK
    assert page.artifact is None
    assert page.check is MermaidCheck.NOT_APPLICABLE
    assert page.stop_reason is None


def test_a_zero_byte_stub_costs_neither_pixels_nor_a_model_call():
    extractor, _, rasterizer, model, cache = build(make_page(0, content=None))
    extractor.extract(request_for())
    assert rasterizer.calls == []
    assert model.requests == []
    assert cache.reads == 0


def test_an_absent_artifact_and_an_erased_page_are_both_no_ink_and_say_which():
    absent = extract(make_page(0, content=None))
    erased = extract(make_page(0, content=PageContent()))
    assert absent.pages[0].skipped is DiagramSkipReason.NO_INK
    assert erased.pages[0].skipped is DiagramSkipReason.NO_INK
    assert "no scene artifact" in absent.degradations[0].detail
    assert "no visible ink" in erased.degradations[0].detail


def test_no_ink_is_reported_as_a_degradation_naming_the_page():
    result = extract(make_page(0, content=None))
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.PAGE_NOT_ANNOTATED
    assert degradation.subject == page_uuid(0)
    assert degradation.substituted is None


def test_an_unannotated_page_does_not_disable_the_cache_for_its_neighbours():
    """Legacy swallowed the hash failure with a bare ``except``, silently disabling the cache."""
    cache = InMemoryDiagramCache()
    extractor, _, _, model, _ = build(
        make_page(0, content=None),
        make_page(1, content=ink()),
        answers={page_uuid(1): Answer(text=f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```")},
        cache=cache,
    )
    extractor.extract(request_for())
    assert len(cache.stored) == 1
    second = ExtractDiagrams(
        repository=InMemoryRepository(
            DocumentSummary(
                doc_id=DOC,
                metadata=DocumentMetadata(
                    visible_name="Contract",
                    kind=DocumentKind.DOCUMENT,
                    source=SourceKind.PDF,
                ),
                pages=(PageId(uuid=page_uuid(0)), PageId(uuid=page_uuid(1))),
            ),
            {
                page_uuid(0): make_page(0, content=None),
                page_uuid(1): make_page(1, content=ink()),
            },
            {page_uuid(1): f"hash-{page_uuid(1)}"},
        ),
        rasterizer=FakeRasterizer(),
        model=model,
        cache=cache,
    )
    again = second.extract(request_for())
    assert again.pages[1].served_from_cache is True
    assert len(model.requests) == 1


# ───────────────────────────── the cache rule, both ways ─────────────────────────────


def stored_key(model: ScriptedModel, cache: InMemoryDiagramCache) -> DiagramCacheKey:
    """Return the one key the run stored, for reuse under a second binding."""
    assert model.requests
    return cache.stored[0][0]


def test_a_row_under_the_exact_key_is_served_without_a_model_call():
    answers = answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```")
    cache = InMemoryDiagramCache()
    first, _, _, model, _ = build(make_page(0, content=ink()), answers=answers, cache=cache)
    first.extract(request_for())
    second, _, rasterizer, fresh_model, _ = build(
        make_page(0, content=ink()), answers={}, cache=cache
    )
    result = second.extract(request_for())
    (page,) = result.pages
    assert page.served_from_cache is True
    assert page.artifact is not None
    assert page.artifact.mermaid == FLOWCHART
    assert page.check is MermaidCheck.UNCHECKED
    assert page.stop_reason is None
    assert page.truncated is False
    assert fresh_model.requests == []
    assert rasterizer.calls == [page_uuid(0)]
    assert len(model.requests) == 1
    assert result.degradations == ()


def test_a_row_produced_under_other_inputs_is_recomputed_and_reported():
    """The other direction: a stored row for this page under a different key is never served."""
    cache = InMemoryDiagramCache()
    older = _key(f"hash-{page_uuid(0)}", render_digest="an-older-renderer")
    cache.put(
        older,
        DiagramArtifact(
            content_kind=PageContentKind.DIAGRAM,
            mermaid="flowchart TD\n    stale --> row",
            created_at=NOW,
        ),
    )
    extractor, _, _, model, _ = build(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
        cache=cache,
    )
    result = extractor.extract(request_for())
    (page,) = result.pages
    assert page.served_from_cache is False
    assert page.artifact is not None
    assert page.artifact.mermaid == FLOWCHART
    assert len(model.requests) == 1
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.CACHE_MISS_KEY_CHANGED
    assert degradation.subject == page_uuid(0)
    assert older.digest in degradation.detail
    assert degradation.substituted == cache.stored[-1][0].digest
    assert degradation.substituted != older.digest


def test_a_first_run_reports_no_cache_degradation_at_all():
    result = extract(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
    )
    assert result.degradations == ()


def test_the_key_carries_every_component_that_changes_the_answer():
    extractor, repository, rasterizer, model, cache = build(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
    )
    extractor.extract(request_for())
    (key, artifact) = cache.stored[0]
    assert key.page_hash == repository.page_fingerprint(DOC, PageId(uuid=page_uuid(0)))
    assert key.render_digest == RENDER
    assert key.raster_digest == RasterImage.from_raster(raster_for(page_uuid(0))).digest()
    assert key.model_fingerprint == MODEL
    assert key.request_digest == model.requests[0].digest()
    assert artifact.mermaid == FLOWCHART
    assert rasterizer.calls == [page_uuid(0)]


def test_a_text_classification_is_cached_so_the_page_is_not_paid_for_twice():
    """Legacy's reuse condition admitted a text row for exactly this reason."""
    cache = InMemoryDiagramCache()
    first, _, _, _, _ = build(make_page(0, content=ink()), answers=answering("TEXT"), cache=cache)
    first.extract(request_for())
    second, _, _, model, _ = build(make_page(0, content=ink()), answers={}, cache=cache)
    (page,) = second.extract(request_for()).pages
    assert page.served_from_cache is True
    assert page.artifact is not None
    assert page.artifact.content_kind is PageContentKind.TEXT
    assert page.artifact.mermaid is None
    assert page.check is MermaidCheck.NOT_APPLICABLE
    assert model.requests == []


# ─────────────────────────────── the answer contract ───────────────────────────────


def test_a_diagram_verdict_with_no_code_yields_nothing_and_stores_nothing():
    """The state legacy's conditional read guarded, now unstorable by construction."""
    extractor, _, _, _, cache = build(
        make_page(0, content=ink()),
        answers=answering("DIAGRAM\nI can see a flowchart but will not draw it."),
    )
    (page,) = extractor.extract(request_for()).pages
    assert page.skipped is DiagramSkipReason.DIAGRAM_WITHOUT_CODE
    assert page.artifact is None
    assert page.check is MermaidCheck.NOT_APPLICABLE
    assert page.stop_reason is StopReason.COMPLETE
    assert cache.stored == []


def test_a_diagram_verdict_with_an_empty_fence_is_also_code_free():
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering("MIXED\n```mermaid\n\n```"),
    ).pages
    assert page.skipped is DiagramSkipReason.DIAGRAM_WITHOUT_CODE


def test_a_text_verdict_carrying_code_contradicts_itself_and_is_believed_neither_way():
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering(f"TEXT\n```mermaid\n{FLOWCHART}\n```"),
    ).pages
    assert page.skipped is DiagramSkipReason.TEXT_WITH_CODE
    assert page.artifact is None


def test_a_text_verdict_with_an_empty_fence_is_still_text():
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering("TEXT\n```mermaid\n```"),
    ).pages
    assert page.artifact is not None
    assert page.artifact.content_kind is PageContentKind.TEXT


@pytest.mark.parametrize("body", ["I see a page of handwriting.", "", "   \n\t\n"])
def test_an_answer_that_names_no_verdict_is_reported_rather_than_raised(body: str):
    (page,) = extract(make_page(0, content=ink()), answers=answering(body)).pages
    assert page.skipped is DiagramSkipReason.UNREADABLE_VERDICT
    assert page.artifact is None


def test_a_refusal_is_data_and_lands_as_an_unreadable_verdict():
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering("I cannot help with that.", StopReason.REFUSAL),
    ).pages
    assert page.skipped is DiagramSkipReason.UNREADABLE_VERDICT
    assert page.stop_reason is StopReason.REFUSAL
    assert page.truncated is False


def test_a_verdict_after_a_blank_line_is_still_the_verdict():
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering(f"\n\nDIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
    ).pages
    assert page.artifact is not None
    assert page.artifact.content_kind is PageContentKind.DIAGRAM


@pytest.mark.parametrize(
    ("token", "kind"),
    [
        ("TEXT", PageContentKind.TEXT),
        ("diagram", PageContentKind.DIAGRAM),
        ("Mixed", PageContentKind.MIXED),
    ],
)
def test_each_verdict_token_names_its_content_kind(token: str, kind: PageContentKind):
    body = "" if kind is PageContentKind.TEXT else f"\n```mermaid\n{FLOWCHART}\n```"
    (page,) = extract(make_page(0, content=ink()), answers=answering(f"{token}{body}")).pages
    assert page.artifact is not None
    assert page.artifact.content_kind is kind


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (FLOWCHART, "flowchart"),
        ("sequenceDiagram\n    a->>b: hello", "sequenceDiagram"),
        ("graph LR\n    a --> b", "graph"),
    ],
)
def test_the_diagram_kind_is_the_first_token_of_the_mermaid_document(body: str, expected: str):
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{body}\n```"),
    ).pages
    assert page.artifact is not None
    assert page.artifact.diagram_kind == expected
    assert page.artifact.mermaid == body


def test_this_layer_never_claims_that_anything_validated_the_mermaid():
    """The one idea kept from the three dropped Mermaid ports, asserted rather than stated."""
    (page,) = extract(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
    ).pages
    assert page.check is MermaidCheck.UNCHECKED
    assert page.check is not MermaidCheck.VALID


# ─────────────────────── a truncated completion is data, not an error ───────────────────────


def test_a_truncated_body_still_yields_its_mermaid_and_says_it_was_cut_short():
    extractor, _, _, _, cache = build(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}", StopReason.OUTPUT_LIMIT),
    )
    (page,) = extractor.extract(request_for()).pages
    assert page.artifact is not None
    assert page.artifact.mermaid == FLOWCHART
    assert page.stop_reason is StopReason.OUTPUT_LIMIT
    assert page.truncated is True
    assert cache.stored == []


def test_a_completion_that_stopped_on_a_sequence_is_not_stored_either():
    """``DiagramArtifact`` has no ``truncated`` field, so only a finished run may be stored."""
    extractor, _, _, _, cache = build(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```", StopReason.STOP_SEQUENCE),
    )
    (page,) = extractor.extract(request_for()).pages
    assert page.artifact is not None
    assert page.truncated is False
    assert cache.stored == []


# ─────────────────────────── the selection, the cap, the order ───────────────────────────


def test_every_selected_page_appears_in_ascending_order_including_the_blank_ones():
    result = extract(
        make_page(0, content=ink()),
        make_page(1, content=None),
        make_page(2, content=ink()),
        answers={
            page_uuid(0): Answer(text="TEXT"),
            page_uuid(2): Answer(text=f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
        },
    )
    assert [page.page_index for page in result.pages] == [0, 1, 2]
    assert [page.page_ref for page in result.pages] == [page_uuid(i) for i in (0, 1, 2)]
    assert result.pages[1].skipped is DiagramSkipReason.NO_INK


def test_a_selection_larger_than_the_cap_is_refused_before_anything_is_read():
    extractor, repository, rasterizer, model, cache = build(
        make_page(0, content=ink()),
        make_page(1, content=ink()),
        make_page(2, content=ink()),
    )
    with pytest.raises(UsageError, match="at most 2 pages"):
        extractor.extract(request_for(max_pages=2))
    assert repository.loaded == []
    assert rasterizer.calls == []
    assert model.requests == []
    assert cache.reads == 0


def test_a_narrower_selection_examines_only_the_pages_it_names():
    extractor, repository, _, _, _ = build(
        make_page(0, content=ink()),
        make_page(1, content=ink()),
        make_page(2, content=ink()),
        answers={page_uuid(1): Answer(text="TEXT")},
    )
    result = extractor.extract(request_for(PageSelection.of(1)))
    assert [page.page_index for page in result.pages] == [1]
    assert repository.loaded == [page_uuid(1)]


def test_a_document_with_no_pages_yields_no_entries():
    extractor, _, _, _, _ = build()
    result = extractor.extract(request_for())
    assert result.pages == ()
    assert result.degradations == ()


def test_one_page_is_one_model_call():
    extractor, _, _, model, _ = build(
        make_page(0, content=ink()),
        answers=answering(f"DIAGRAM\n```mermaid\n{FLOWCHART}\n```"),
    )
    extractor.extract(request_for())
    assert len(model.requests) == 1
    assert model.requests[0].decoding.temperature == 0.0
    assert model.requests[0].decoding.max_output_tokens == 1024


# ────────────────────────────── the values refuse to lie ──────────────────────────────


def test_an_entry_with_no_artifact_must_say_why_it_has_none():
    with pytest.raises(ValidationError, match="no reason explaining it"):
        PageDiagram(
            page_index=0,
            page_ref=page_uuid(0),
            artifact=None,
            skipped=None,
            check=MermaidCheck.NOT_APPLICABLE,
            stop_reason=None,
            served_from_cache=False,
        )


def test_mermaid_cannot_be_reported_as_having_nothing_to_check():
    with pytest.raises(ValidationError, match="cannot be not_applicable"):
        PageDiagram(
            page_index=0,
            page_ref=page_uuid(0),
            artifact=DiagramArtifact(
                content_kind=PageContentKind.DIAGRAM,
                mermaid=FLOWCHART,
                created_at=NOW,
            ),
            skipped=None,
            check=MermaidCheck.NOT_APPLICABLE,
            stop_reason=StopReason.COMPLETE,
            served_from_cache=False,
        )


@pytest.mark.parametrize(
    "check",
    [MermaidCheck.UNCHECKED, MermaidCheck.VALID, MermaidCheck.INVALID],
)
def test_a_page_with_no_mermaid_cannot_carry_a_verdict_about_one(check: MermaidCheck):
    with pytest.raises(ValidationError, match="carries no mermaid"):
        PageDiagram(
            page_index=0,
            page_ref=page_uuid(0),
            artifact=DiagramArtifact(content_kind=PageContentKind.TEXT, created_at=NOW),
            skipped=None,
            check=check,
            stop_reason=StopReason.COMPLETE,
            served_from_cache=False,
        )


def test_a_result_is_frozen():
    result = extract(make_page(0, content=None))
    with pytest.raises(ValidationError, match="frozen"):
        assign(result, "pages", ())


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ExtractDiagramsResult.model_validate({"pages": (), "degradations": (), "cached": 0})
