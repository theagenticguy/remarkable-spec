"""The ``redir`` mapping, its fallback, one-to-one indices, and a page nobody wrote on.

How the ports are bound here, and why
-------------------------------------
With local in-memory fakes annotated against the Protocols, for the reasons
``test_app_resolve``'s docstring gives and which apply unchanged: ``rmspec.app`` may import
``rmspec.domain`` and nothing else, the architecture check only scans ``src/``, so an adapter
import here would pass the gate while breaking the property the gate exists to protect --
and importing a shipped doubles subpackage runs its parent's ``__init__``, which binds the
real adapters and therefore their third-party libraries. Conformance is checked by the type
gate rather than by convention: every fake is passed to a Protocol-annotated parameter.

The PDF reader fake honours the postcondition its port states rather than inventing a size:
``rasterize_page`` returns pixels whose ``pixel_size`` really is
``PixelSize.fit_within(page_size, box, oversample=oversample)``, and PNG bytes whose ``IHDR``
really declares that size. That is what makes the assertions about the underlay meaningful --
a double that answered ``width_px=8`` would let a wrong box, a wrong oversample and a wrong
page all pass unnoticed, which is exactly what the export ports say about the rejected
proposals' fakes.

The render pipeline is a fake of a *sibling use case*, not of a port: ``RenderPages`` owns
decode-render-rasterize, and ``rmspec.app.page_annotations`` declares the two members it needs
from it. The fake returns the **export** slice's raster twin, so the conversion the use case
performs -- ``RasterImage.from_raster`` -- is exercised across the twin boundary.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, NamedTuple

import pytest
from pydantic import BaseModel, ValidationError

from rmspec.app.page_annotations import (
    PageAnnotations,
    PageRasterizer,
    RasterizedPage,
    ReadAnnotations,
    ReadAnnotationsRequest,
    ReadAnnotationsResult,
)
from rmspec.app.selection import PageSelection
from rmspec.domain.errors import DegradationKind, PdfPageOutOfRange, UsageError
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
    SourceKind,
    Stroke,
)
from rmspec.domain.ports.export import (
    ImageMedia as ExportMedia,
)
from rmspec.domain.ports.export import (
    PdfPageBackground,
    PdfPageReader,
    PdfSourceRef,
    PhysicalSize,
    PixelSize,
)
from rmspec.domain.ports.export import (
    RasterImage as ExportRaster,
)
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT, DocumentRepository
from rmspec.domain.ports.ocr import (
    StopReason,
    VisionCompletion,
    VisionLanguageModel,
    VisionRequest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rmspec.domain.ports.render import PageUnderlay

DOC = DocumentId(uuid="d3b38661-2222-4222-8222-222222222222")
SOURCE_HANDLE = "pulled://d3b38661/contract.pdf"
SOURCE = PdfSourceRef(token=SOURCE_HANDLE)
MODEL = "bedrock-opus@3"
RENDER = "render-digest-1"
BOX = PixelSize(width_px=1620, height_px=2160)
A4 = PhysicalSize(width_mm=210.0, height_mm=297.0)
OVERSAMPLE = 2

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
INK_WIDTH = 8
INK_HEIGHT = 12

PRINTED = (
    "Clause 1. The term begins on the first of April.",
    "Clause 2. Either party may terminate with thirty days notice.",
    "Clause 3. Fees are payable monthly in arrears.",
    "Clause 4. This agreement is governed by the law of England.",
    "Clause 5. Signed by both parties below.",
)
READING = "The reader struck out 'thirty days' and wrote 'sixty days' above it."


def make_png(width: int, height: int) -> bytes:
    """Return PNG bytes both slices' rasters accept: signature, a matching IHDR, and IEND."""
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
        data=make_png(INK_WIDTH, INK_HEIGHT),
        width=INK_WIDTH,
        height=INK_HEIGHT,
        render_dpi=229,
    )


class Rendered(BaseModel, frozen=True, extra="forbid"):
    """One page's composited pixels plus the render identity, as the pipeline returns them."""

    raster: ExportRaster
    render_digest: str


class Answer(NamedTuple):
    """One scripted model answer: a body, and why generation stopped."""

    text: str
    stop_reason: StopReason = StopReason.COMPLETE


class InMemoryRepository:
    """A :class:`DocumentRepository` over prebuilt pages, with fingerprints held apart."""

    def __init__(self, summary: DocumentSummary, pages: dict[str, Page]) -> None:
        self.loaded: list[str] = []
        self._summary = summary
        self._pages = pages

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
        """Fingerprint one page's stored bytes, which this use case never asks for."""
        assert doc_id == self._summary.doc_id
        if self._pages[page_id.uuid].content is None:
            return ABSENT_ARTIFACT_FINGERPRINT
        return f"hash-{page_id.uuid}"

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Fingerprint every claimed page, which this use case never asks for either."""
        return {page: self.page_fingerprint(doc_id, page) for page in self._summary.pages}


class FakePdfReader:
    """A :class:`PdfPageReader` over fixed page text, honouring the fit-to-box postcondition."""

    def __init__(self, texts: tuple[str, ...] = PRINTED) -> None:
        self.text_calls = 0
        self.count_calls = 0
        self.rasterized: list[tuple[int, PixelSize, int]] = []
        self._texts = texts

    def page_count(self, source: PdfSourceRef) -> int:
        """Count the pages, which this use case deliberately never asks for."""
        assert source == SOURCE
        self.count_calls += 1
        return len(self._texts)

    def page_texts(self, source: PdfSourceRef) -> tuple[str, ...]:
        """Extract every page's text in document order, one entry per page."""
        assert source == SOURCE
        self.text_calls += 1
        return self._texts

    def rasterize_page(
        self,
        source: PdfSourceRef,
        *,
        page_index: int,
        box: PixelSize,
        oversample: int,
    ) -> PdfPageBackground:
        """Rasterize one page, fitted to ``box`` and supersampled exactly as the port says."""
        assert source == SOURCE
        if page_index >= len(self._texts):
            raise PdfPageOutOfRange(
                source=source.token,
                page_index=page_index,
                page_count=len(self._texts),
            )
        self.rasterized.append((page_index, box, oversample))
        size = PixelSize.fit_within(A4, box, oversample=oversample)
        return PdfPageBackground(
            page_index=page_index,
            data=make_png(size.width_px, size.height_px),
            pixel_size=size,
            page_size=A4,
        )


class FakeRasterizer:
    """A :class:`PageRasterizer` that renders nothing and records what it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, PageUnderlay | None]] = []

    @property
    def page_box(self) -> PixelSize:
        """The pixel box a page rasterizes into, which the underlay is read at."""
        return BOX

    def raster_for(
        self,
        doc_id: DocumentId,
        page_id: PageId,
        /,
        *,
        underlay: PageUnderlay | None = None,
    ) -> Rendered:
        """Return fixed pixels stamped with the page they were asked for."""
        assert doc_id == DOC
        self.calls.append((page_id.uuid, underlay))
        return Rendered(raster=raster_for(page_id.uuid), render_digest=RENDER)


class ScriptedModel:
    """A :class:`VisionLanguageModel` whose answers are keyed on the page in the request."""

    def __init__(self, answers: dict[str, Answer]) -> None:
        self.requests: list[VisionRequest] = []
        self._answers = answers

    @property
    def fingerprint(self) -> str:
        """Identity of this binding."""
        return MODEL

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Answer about whichever page the attached raster names."""
        self.requests.append(request)
        answer = self._answers[request.images[0].page_ref]
        return VisionCompletion.answering(
            request,
            fingerprint=MODEL,
            text=answer.text,
            stop_reason=answer.stop_reason,
        )


class Wiring(NamedTuple):
    """One wired use case and every fake behind it, so a test may assert on any of them."""

    reader: ReadAnnotations
    repository: InMemoryRepository
    pdf: FakePdfReader
    rasterizer: FakeRasterizer
    model: ScriptedModel


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


def make_page(
    index: int,
    *,
    content: PageContent | None = None,
    pdf_page_index: int | None = None,
) -> Page:
    """Build one annotation page, with the defect the domain requires for absent content."""
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
        pdf_page_index=pdf_page_index,
        content=content,
        defects=defects,
    )


def build(
    *pages: Page,
    answers: dict[str, Answer] | None = None,
    source: SourceKind | None = SourceKind.PDF,
    texts: tuple[str, ...] = PRINTED,
) -> Wiring:
    """Wire the use case over fakes, and hand every fake back for assertions."""
    summary = DocumentSummary(
        doc_id=DOC,
        metadata=DocumentMetadata(
            visible_name="Contract",
            kind=DocumentKind.DOCUMENT,
            source=source,
        ),
        pages=tuple(page.page_id for page in pages),
    )
    repository = InMemoryRepository(summary, {page.page_id.uuid: page for page in pages})
    pdf = FakePdfReader(texts)
    rasterizer = FakeRasterizer()
    model = ScriptedModel(answers or {})
    reader = ReadAnnotations(
        repository=repository,
        pdf=pdf,
        rasterizer=rasterizer,
        model=model,
    )
    return Wiring(reader, repository, pdf, rasterizer, model)


def request_for(
    pages: PageSelection | None = None, *, max_pages: int = 10
) -> ReadAnnotationsRequest:
    """Build a request over the whole document unless a narrower selection is given."""
    return ReadAnnotationsRequest(
        doc_id=DOC,
        source=SOURCE,
        pages=pages if pages is not None else PageSelection.all(),
        max_pages=max_pages,
    )


def read(*pages: Page, answers: dict[str, Answer] | None = None) -> ReadAnnotationsResult:
    """Run the use case over the given pages and return only the result."""
    return build(*pages, answers=answers).reader.read(request_for())


def answering(text: str, stop_reason: StopReason = StopReason.COMPLETE) -> dict[str, Answer]:
    """Script one answer for page zero."""
    return {page_uuid(0): Answer(text=text, stop_reason=stop_reason)}


def assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error."""
    setattr(target, field, value)


# ───────────────────────────── the fakes are the ports ─────────────────────────────


def test_the_fakes_are_the_ports_the_use_case_declares():
    wiring = build(make_page(0, content=ink()))
    repository: DocumentRepository = wiring.repository
    pdf: PdfPageReader = wiring.pdf
    rasterizer: PageRasterizer = wiring.rasterizer
    model: VisionLanguageModel = wiring.model
    rendered: RasterizedPage = Rendered(raster=raster_for(page_uuid(0)), render_digest=RENDER)
    assert repository.list_documents()[0].page_count == 1
    assert pdf.page_count(SOURCE) == len(PRINTED)
    assert rasterizer.page_box == BOX
    assert model.fingerprint == MODEL
    assert rendered.render_digest == RENDER


def test_the_re_exported_surface_is_models_and_use_cases_only():
    """Why the Protocol placeholders stay out of ``__all__``.

    The surface sweep treats every non-model exported class as a use case and asserts
    keyword-only collaborators over its ``__init__``, which a Protocol cannot satisfy.

    The module is reached through ``importlib`` rather than ``from rmspec.app import
    annotations``, and that is not a style choice: ``rmspec/app/__init__.py`` contains
    ``from __future__ import annotations``, so the package namespace already binds the name
    ``annotations`` to a ``__future__._Feature``. The attribute form resolves to that feature
    unless the submodule happens to have been imported first, which makes it order-dependent
    at run time and simply wrong to a type checker. ``import_module`` reads ``sys.modules``
    and cannot be shadowed.
    """
    module = importlib.import_module("rmspec.app.page_annotations")
    for name in module.__all__:
        exported = getattr(module, name)
        assert isinstance(exported, type)
        assert issubclass(exported, BaseModel) or exported is ReadAnnotations, name


# ──────────────────────── pdf-backed documents only, checked first ────────────────────────


@pytest.mark.parametrize("source", [SourceKind.NOTEBOOK, SourceKind.EPUB, None])
def test_a_document_that_is_not_pdf_backed_is_refused_before_anything_is_read(
    source: SourceKind | None,
):
    wiring = build(make_page(0, content=ink()), source=source)
    with pytest.raises(UsageError, match="pdf-backed document"):
        wiring.reader.read(request_for())
    assert wiring.repository.loaded == []
    assert wiring.pdf.text_calls == 0
    assert wiring.model.requests == []


def test_an_unrecorded_source_says_so_rather_than_naming_a_kind():
    wiring = build(make_page(0, content=ink()), source=None)
    with pytest.raises(UsageError, match="unrecorded"):
        wiring.reader.read(request_for())


def test_a_notebook_names_its_own_kind_in_the_refusal():
    wiring = build(make_page(0, content=ink()), source=SourceKind.NOTEBOOK)
    with pytest.raises(UsageError, match="notebook"):
        wiring.reader.read(request_for())


# ─────────────────────────── the redir mapping is load-bearing ───────────────────────────


def test_the_redir_entry_decides_which_pdf_page_the_ink_is_composited_over():
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=3),
        answers=answering(READING),
    )
    result = wiring.reader.read(request_for())
    (page,) = result.pages
    assert page.pdf_page_index == 3
    assert page.printed_text == PRINTED[3]
    assert [call[0] for call in wiring.pdf.rasterized] == [3]
    assert result.degradations == ()


def test_a_missing_redir_entry_falls_back_to_the_position_and_says_so():
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=None),
        make_page(1, content=ink(), pdf_page_index=None),
        answers={page_uuid(0): Answer(READING), page_uuid(1): Answer(READING)},
    )
    result = wiring.reader.read(request_for())
    assert [page.pdf_page_index for page in result.pages] == [0, 1]
    assert [call[0] for call in wiring.pdf.rasterized] == [0, 1]
    assert [degradation.kind for degradation in result.degradations] == [
        DegradationKind.PDF_PAGE_INDEX_FALLBACK,
        DegradationKind.PDF_PAGE_INDEX_FALLBACK,
    ]
    assert [degradation.substituted for degradation in result.degradations] == ["0", "1"]
    assert result.degradations[0].subject == page_uuid(0)
    assert "redirection map" in result.degradations[0].detail


def test_a_redir_entry_naming_pdf_page_zero_is_not_the_fallback():
    """``is None``, never falsiness: page zero is a real answer and a fallback is not."""
    wiring = build(make_page(0, content=ink(), pdf_page_index=0), answers=answering(READING))
    result = wiring.reader.read(request_for())
    assert result.pages[0].pdf_page_index == 0
    assert result.degradations == ()


def test_a_redir_entry_past_the_end_of_the_pdf_is_refused_rather_than_clamped():
    wiring = build(make_page(0, content=ink(), pdf_page_index=99), answers=answering(READING))
    with pytest.raises(PdfPageOutOfRange) as caught:
        wiring.reader.read(request_for())
    assert caught.value.page_index == 99
    assert caught.value.page_count == len(PRINTED)
    assert caught.value.source == SOURCE.token
    assert wiring.model.requests == []


def test_a_positional_fallback_past_the_end_of_the_pdf_is_refused_too():
    pages = tuple(make_page(index, content=ink()) for index in range(len(PRINTED) + 1))
    wiring = build(*pages, answers={page_uuid(i): Answer(READING) for i in range(len(pages))})
    with pytest.raises(PdfPageOutOfRange) as caught:
        wiring.reader.read(request_for(max_pages=99))
    assert caught.value.page_index == len(PRINTED)


def test_the_pdf_text_is_extracted_once_for_the_whole_run():
    """``len`` of that one call is the page count, so the reader never asks for it twice."""
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, content=ink(), pdf_page_index=1),
        answers={page_uuid(0): Answer(READING), page_uuid(1): Answer(READING)},
    )
    wiring.reader.read(request_for())
    assert wiring.pdf.text_calls == 1
    assert wiring.pdf.count_calls == 0


# ──────────────────────── the annotation layer over the printed page ────────────────────────


def test_the_underlay_is_the_pdf_page_read_at_the_pipelines_own_scale():
    wiring = build(make_page(0, content=ink(), pdf_page_index=2), answers=answering(READING))
    wiring.reader.read(request_for())
    (page_index, box, oversample) = wiring.pdf.rasterized[0]
    assert page_index == 2
    assert box == BOX
    assert oversample == OVERSAMPLE
    (page_ref, underlay) = wiring.rasterizer.calls[0]
    assert page_ref == page_uuid(0)
    assert underlay is not None
    assert underlay.source_size.width_mm == A4.width_mm
    assert underlay.source_size.height_mm == A4.height_mm
    assert underlay.media.value == "png"
    assert underlay.data.startswith(PNG_MAGIC)


def test_the_composite_is_the_one_image_the_model_is_given():
    wiring = build(make_page(0, content=ink(), pdf_page_index=1), answers=answering(READING))
    wiring.reader.read(request_for())
    (request,) = wiring.model.requests
    (image,) = request.images
    assert image.page_ref == page_uuid(0)
    assert image.width == INK_WIDTH
    assert request.decoding.temperature == 0.0
    assert request.decoding.max_output_tokens == 2048
    assert request.system is not None


def test_the_prompt_carries_the_printed_text_of_the_page_it_asks_about():
    wiring = build(make_page(0, content=ink(), pdf_page_index=1), answers=answering(READING))
    wiring.reader.read(request_for())
    prompt = wiring.model.requests[0].prompt
    assert PRINTED[1] in prompt
    assert PRINTED[0] not in prompt


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_a_pdf_page_with_no_text_layer_says_so_instead_of_pretending(text: str):
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING),
        texts=(text,),
    )
    result = wiring.reader.read(request_for())
    assert "no extractable text" in wiring.model.requests[0].prompt
    assert result.pages[0].printed_text == text


# ──────────────────── a page nobody wrote on is an entry, not an error ────────────────────


def test_an_unannotated_page_is_reported_rather_than_failed_or_omitted():
    wiring = build(make_page(0, content=None, pdf_page_index=0))
    result = wiring.reader.read(request_for())
    (page,) = result.pages
    assert page.annotations is None
    assert page.stop_reason is None
    assert page.truncated is False
    assert page.printed_text == PRINTED[0]
    assert wiring.model.requests == []
    assert wiring.rasterizer.calls == []
    assert wiring.pdf.rasterized == []


def test_a_page_with_no_ink_is_reported_as_a_degradation_naming_it():
    result = read(make_page(0, content=None, pdf_page_index=0))
    (degradation,) = result.degradations
    assert degradation.kind is DegradationKind.PAGE_NOT_ANNOTATED
    assert degradation.subject == page_uuid(0)


def test_a_zero_byte_stub_and_an_erased_page_are_both_no_ink_and_say_which():
    absent = read(make_page(0, content=None, pdf_page_index=0))
    erased = read(make_page(0, content=PageContent(), pdf_page_index=0))
    assert absent.pages[0].annotations is None
    assert erased.pages[0].annotations is None
    assert "no scene artifact" in absent.degradations[0].detail
    assert "no visible ink" in erased.degradations[0].detail


def test_an_empty_reading_is_not_the_same_answer_as_an_unread_page():
    result = read(make_page(0, content=ink(), pdf_page_index=0), answers=answering(""))
    (page,) = result.pages
    assert page.annotations == ""
    assert page.stop_reason is StopReason.COMPLETE


# ─────────────────────────── indices stay one-to-one with the pdf ───────────────────────────


def test_pages_nobody_wrote_on_keep_their_slot_so_page_seven_means_page_seven():
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, content=None, pdf_page_index=1),
        make_page(2, content=PageContent(), pdf_page_index=2),
        make_page(3, content=ink(), pdf_page_index=3),
        answers={page_uuid(0): Answer(READING), page_uuid(3): Answer(READING)},
    )
    result = wiring.reader.read(request_for())
    assert [page.page_index for page in result.pages] == [0, 1, 2, 3]
    assert [page.pdf_page_index for page in result.pages] == [0, 1, 2, 3]
    assert [page.annotations is None for page in result.pages] == [False, True, True, False]
    assert [page.page_ref for page in result.pages] == [page_uuid(i) for i in range(4)]


def test_a_reordered_document_keeps_its_own_positions_and_the_pdfs():
    """The mapping is not the identity, which is the whole reason ``redir`` exists."""
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=4),
        make_page(1, content=ink(), pdf_page_index=2),
        answers={page_uuid(0): Answer(READING), page_uuid(1): Answer(READING)},
    )
    result = wiring.reader.read(request_for())
    assert [page.page_index for page in result.pages] == [0, 1]
    assert [page.pdf_page_index for page in result.pages] == [4, 2]
    assert [page.printed_text for page in result.pages] == [PRINTED[4], PRINTED[2]]


def test_a_narrower_selection_reads_only_the_pages_it_names():
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, content=ink(), pdf_page_index=1),
        make_page(2, content=ink(), pdf_page_index=2),
        answers={page_uuid(1): Answer(READING)},
    )
    result = wiring.reader.read(request_for(PageSelection.of(1)))
    assert [page.page_index for page in result.pages] == [1]
    assert wiring.repository.loaded == [page_uuid(1)]


def test_a_document_with_no_pages_yields_no_entries():
    wiring = build()
    result = wiring.reader.read(request_for())
    assert result.pages == ()
    assert result.degradations == ()


# ─────────────────────────── the cap, and truncation as data ───────────────────────────


def test_a_selection_larger_than_the_cap_is_refused_before_the_pdf_is_opened():
    wiring = build(
        make_page(0, content=ink(), pdf_page_index=0),
        make_page(1, content=ink(), pdf_page_index=1),
        make_page(2, content=ink(), pdf_page_index=2),
    )
    with pytest.raises(UsageError, match="at most 2 pages"):
        wiring.reader.read(request_for(max_pages=2))
    assert wiring.pdf.text_calls == 0
    assert wiring.pdf.rasterized == []
    assert wiring.repository.loaded == []
    assert wiring.model.requests == []


def test_a_truncated_reading_is_kept_and_labelled_rather_than_raised():
    result = read(
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering(READING, StopReason.OUTPUT_LIMIT),
    )
    (page,) = result.pages
    assert page.annotations == READING
    assert page.stop_reason is StopReason.OUTPUT_LIMIT
    assert page.truncated is True


def test_a_refusal_is_carried_verbatim_rather_than_folded_into_truncation():
    result = read(
        make_page(0, content=ink(), pdf_page_index=0),
        answers=answering("I cannot help with that.", StopReason.REFUSAL),
    )
    (page,) = result.pages
    assert page.stop_reason is StopReason.REFUSAL
    assert page.truncated is False


# ────────────────────────────── the values refuse to lie ──────────────────────────────


def test_an_unread_page_cannot_claim_a_stop_reason():
    with pytest.raises(ValidationError, match="was not read"):
        PageAnnotations(
            page_index=0,
            page_ref=page_uuid(0),
            pdf_page_index=0,
            printed_text=PRINTED[0],
            annotations=None,
            stop_reason=StopReason.COMPLETE,
        )


def test_a_read_page_may_carry_any_stop_reason():
    page = PageAnnotations(
        page_index=0,
        page_ref=page_uuid(0),
        pdf_page_index=0,
        printed_text=PRINTED[0],
        annotations=READING,
        stop_reason=StopReason.STOP_SEQUENCE,
    )
    assert page.truncated is False


def test_a_result_is_frozen():
    result = read(make_page(0, content=None, pdf_page_index=0))
    with pytest.raises(ValidationError, match="frozen"):
        assign(result, "pages", ())


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ReadAnnotationsResult.model_validate({"pages": (), "degradations": (), "read": 0})
