"""The stub page, the page-order arithmetic, the render identity, and the raster hop.

How the four ports are bound here, and why
------------------------------------------
With local in-memory fakes, annotated against the Protocols -- the pattern
``test_app_resolve.py`` establishes and whose reasoning applies here unchanged:

* ``rmspec.app`` may import ``rmspec.domain`` and nothing else, and these tests hold
  themselves to the rule their source obeys. ``tests/architecture`` only scans ``src/``, so
  importing ``rmspec.render``, ``rmspec.export``, ``rmspec.formats`` or ``rmspec.device``
  here would pass the gate while breaking the property the gate exists to protect.
* Those four packages own ``cairocffi``, ``cairosvg``, ``rmscene``, ``httpx`` and
  ``paramiko`` between them. Binding one shipped double would make a pure-policy suite --
  which asserts page indices, digests and one branch about zero bytes -- need a native
  graphics stack installed to run.
* Conformance is checked by the type gate, not by convention. Every construction below
  passes these fakes to ``RenderPages(bundles=..., codec=..., renderer=..., rasterizer=...)``,
  whose parameters are annotated with the four Protocols, so ``ty`` verifies structural
  conformance at every call site.

The fakes carry the seams the contract needs and nothing more. Each has a ``failure`` it can
be told to raise, because "a dead transport is never degraded into an empty render" and "a
corrupt page is never rendered as a plausible blank one" are only assertable if the
collaborator can be made to die. The renderer and the codec record what they were handed,
because the whole point of two of these tests is that the codec is *not* called for a stub
page and that the rasterizer receives the rendered page's own size rather than a transposed
copy of it.

The renderer derives its counts from the page it is given rather than returning constants.
A double that reports ``stroke_count=0`` unconditionally would make "a stub renders as a page
with no ink" pass for a page that had ink and was silently dropped, which is the fakes-lie
hole ``ports/render.py`` deleted ``viewport_for`` over.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from rmspec.app import PageSelection
from rmspec.app.render import (
    RenderedPageArtifact,
    RenderPages,
    RenderPagesRequest,
    RenderPagesResult,
    _as_svg_page,
)
from rmspec.domain.errors import (
    CorruptPageData,
    DeviceUnreachable,
    InvalidSettingError,
    MalformedDocument,
    PageNotFound,
    RasterizationFailed,
    RmspecError,
    TransportKind,
    UnsupportedPenType,
    UsageError,
    exit_code,
)
from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    Layer,
    Page,
    PageContent,
    PageDefectCode,
    PageId,
    Palette,
    PenColor,
    PenType,
    ScreenSpec,
    Stroke,
    TextBlock,
)
from rmspec.domain.ports.device import (
    DeviceDocument,
    DeviceFileType,
    DevicePageSource,
    DocumentSourceBundle,
    RawBundleSource,
)
from rmspec.domain.ports.export import ImageMedia, PixelSize, RasterImage, SvgPage, SvgRasterizer
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT, PageCodec
from rmspec.domain.ports.render import (
    PageBackground,
    PageRenderer,
    RenderedPage,
    RenderNotice,
    RenderNoticeCode,
    RenderStyle,
    TextStyle,
)
from rmspec.domain.ports.render import PhysicalSize as RenderPhysicalSize

DOC = "d3b38661-1111-4111-8111-111111111111"
PAGE_A = "aaaaaaaa-1111-4111-8111-111111111111"
PAGE_B = "bbbbbbbb-2222-4222-8222-222222222222"
PAGE_C = "cccccccc-3333-4333-8333-333333333333"

INK = b"reMarkable .lines file, version=6          \x00\x01\x02"

STYLE = RenderStyle(
    thickness_scale=1.5,
    min_padding_mm=10.6,
    text=TextStyle(family="Noto Sans, sans-serif", size_px=32.0, line_height=1.25),
    renderer_revision="render-r1",
)

TEMPLATE = PageBackground(template_svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>')

#: The notice a real page emits every time: measured on firmware 3.27.3.0, a uniform
#: 10.6 mm margin kept around the page box. It is why no notice becomes a degradation.
VIEWPORT = RenderNotice(
    code=RenderNoticeCode.VIEWPORT_EXPANDED,
    detail="a uniform 10.6 mm margin was kept around the page box",
)

OMITTED = RenderNotice(
    code=RenderNoticeCode.TEXT_OMITTED,
    detail="1 typed block was left out: this writer has no font metrics",
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"


def _png(width: int, height: int) -> bytes:
    """Build the smallest byte stream ``RasterImage``'s validators accept."""
    return (
        _PNG_SIGNATURE
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + _IEND
    )


def _content(*, blocks: int = 1, strokes: int = 1) -> PageContent:
    """Content a codec would return: countable, so the renderer's counts are falsifiable."""
    return PageContent(
        layers=(
            Layer(
                name="Layer 1",
                strokes=tuple(
                    Stroke(pen=PenType.FINELINER_2, color=PenColor.BLACK, thickness_scale=2.0)
                    for _ in range(strokes)
                ),
                text_blocks=tuple(
                    TextBlock(pos_x=10.0, pos_y=20.0, width=400.0, text="hello")
                    for _ in range(blocks)
                ),
            ),
        )
    )


class _InMemoryBundles:
    """A :class:`RawBundleSource` over one bundle, with a transport that can be told to die."""

    def __init__(self, bundle: DocumentSourceBundle, failure: RmspecError | None = None) -> None:
        self.requested: list[str] = []
        self._bundle = bundle
        self._failure = failure

    @property
    def calls(self) -> int:
        """How many handshakes this source was asked for. One command is one handshake."""
        return len(self.requested)

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Return the whole bundle, or die the way a half-pulled transfer does."""
        self.requested.append(doc_uuid)
        if self._failure is not None:
            raise self._failure
        return self._bundle


class _RecordingCodec:
    """A :class:`PageCodec` that records every call, so "never called" is assertable."""

    def __init__(self, failure: RmspecError | None = None) -> None:
        self.decoded: list[tuple[bytes, str]] = []
        self._failure = failure

    def decode_page(self, raw: bytes, page_ref: str, /) -> PageContent:
        """Return countable content, or refuse the bytes the way a real codec does."""
        self.decoded.append((raw, page_ref))
        if self._failure is not None:
            raise self._failure
        return _content()


class _CountingRenderer:
    """A :class:`PageRenderer` whose counts come from the page, not from a constant."""

    def __init__(
        self,
        *,
        notices: tuple[RenderNotice, ...] = (),
        failure: RmspecError | None = None,
    ) -> None:
        self.pages: list[Page] = []
        self.backgrounds: list[PageBackground | None] = []
        self._notices = notices
        self._failure = failure

    def render(
        self,
        page: Page,
        /,
        *,
        screen: ScreenSpec,
        palette: Palette,
        style: RenderStyle,
        background: PageBackground | None = None,
    ) -> RenderedPage:
        """Render to a marker document whose counts a test can compare against the page."""
        self.pages.append(page)
        self.backgrounds.append(background)
        if self._failure is not None:
            raise self._failure
        content = page.content
        blocks = (
            0
            if content is None
            else sum(len(layer.text_blocks) for layer in content.visible_layers)
        )
        return RenderedPage(
            page_ref=page.ref,
            svg=(
                f'<svg xmlns="http://www.w3.org/2000/svg" data-ink="{palette.name}" '
                f'data-weight="{style.thickness_scale}"></svg>'
            ),
            size=RenderPhysicalSize(width_mm=screen.width_mm, height_mm=screen.height_mm),
            stroke_count=page.stroke_count,
            text_block_count=blocks,
            notices=self._notices,
        )


class _CountingRasterizer:
    """An :class:`SvgRasterizer` that records what it was handed and honours ``from_dpi``."""

    def __init__(self, failure: RmspecError | None = None) -> None:
        self.requested: list[tuple[SvgPage, int]] = []
        self._failure = failure

    def to_png(self, page: SvgPage, *, dpi: int) -> RasterImage:
        """Return pixels whose declared size is the domain formula, as the port requires."""
        self.requested.append((page, dpi))
        if self._failure is not None:
            raise self._failure
        size = PixelSize.from_dpi(page.size, dpi)
        return RasterImage(
            page_ref=page.page_ref,
            media=ImageMedia.PNG,
            data=_png(size.width_px, size.height_px),
            width=size.width_px,
            height=size.height_px,
            render_dpi=dpi,
        )


def _bundle(
    *pages: DevicePageSource,
    file_type: DeviceFileType = DeviceFileType.NOTEBOOK,
) -> DocumentSourceBundle:
    return DocumentSourceBundle(
        document=DeviceDocument(uuid=DOC, name="TestNb", file_type=file_type),
        pages=pages,
        base=None if file_type is DeviceFileType.NOTEBOOK else b"%PDF-1.7\n%%EOF",
    )


def _request(**overrides: object) -> RenderPagesRequest:
    fields: dict[str, object] = {
        "document_uuid": DOC,
        "selection": PageSelection.all(),
        "max_pages": 50,
        "screen": PAPER_PRO_SCREEN,
        "palette": EXPORT_PALETTE,
        "style": STYLE,
    }
    fields.update(overrides)
    return RenderPagesRequest.model_validate(fields)


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error."""
    setattr(target, field, value)


# ───────────────────── the zero-byte stub: 62 of 92 real files ─────────────────────


@pytest.mark.parametrize("scene", [None, b""])
def test_a_page_with_no_scene_bytes_renders_as_a_page_with_no_ink(scene: bytes | None):
    """Legacy raised a bare ``EOFError`` on every one of these and crashed three commands."""
    codec = _RecordingCodec()
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=scene))),
        codec=codec,
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    (artifact,) = result.pages
    assert artifact.rendered.stroke_count == 0
    assert artifact.rendered.text_block_count == 0
    assert codec.decoded == []


@pytest.mark.parametrize("scene", [None, b""])
def test_a_stub_page_is_contentless_and_says_why(scene: bytes | None):
    """The same value ``DocumentRepository.load_page`` returns for the identical state."""
    renderer = _CountingRenderer()
    RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=scene))),
        codec=_RecordingCodec(),
        renderer=renderer,
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    (page,) = renderer.pages
    assert page.content is None
    assert [defect.code for defect in page.defects] == [PageDefectCode.ARTIFACT_ABSENT]
    assert page.is_readable is False


def test_both_spellings_of_no_ink_produce_one_page_hash():
    """A transport reporting ``b""`` and one reporting ``None`` must not key two cache rows."""
    hashes = {
        RenderPages(
            bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=scene))),
            codec=_RecordingCodec(),
            renderer=_CountingRenderer(),
            rasterizer=_CountingRasterizer(),
        )
        .render(_request())
        .pages[0]
        .page_hash
        for scene in (None, b"")
    }
    assert hashes == {ABSENT_ARTIFACT_FINGERPRINT}


def test_a_stub_page_still_carries_its_template():
    """The template is a fact only the store holds, so a blank page must not lose it."""
    renderer = _CountingRenderer()
    RenderPages(
        bundles=_InMemoryBundles(
            _bundle(DevicePageSource(page_id=PAGE_A, scene=None, template_name="Grid"))
        ),
        codec=_RecordingCodec(),
        renderer=renderer,
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert renderer.pages[0].template_name == "Grid"


def test_the_unannotated_pdf_is_rendered_page_by_page_rather_than_refused():
    """The measured corpus case: a PDF-backed document whose pages are mostly stubs."""
    result = RenderPages(
        bundles=_InMemoryBundles(
            _bundle(
                DevicePageSource(page_id=PAGE_A, scene=None),
                DevicePageSource(page_id=PAGE_B, scene=INK),
                DevicePageSource(page_id=PAGE_C, scene=b""),
                file_type=DeviceFileType.PDF,
            )
        ),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert [artifact.page_index for artifact in result.pages] == [0, 1, 2]
    assert [artifact.rendered.stroke_count for artifact in result.pages] == [0, 1, 0]


# ───────────────────────────── decoding a real page ─────────────────────────────


def test_a_page_with_scene_bytes_goes_through_the_codec_with_its_own_ref():
    codec = _RecordingCodec()
    RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=codec,
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert codec.decoded == [(INK, PAGE_A)]


def test_a_decoded_page_is_hashed_over_the_bytes_the_device_held():
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert result.pages[0].page_hash == hashlib.sha256(INK).hexdigest()
    assert result.pages[0].page_hash != ABSENT_ARTIFACT_FINGERPRINT


def test_an_undecodable_page_is_not_rendered_as_a_plausible_blank_one():
    """A repository would degrade this to a defect; a command writing an artifact must not."""
    failure = CorruptPageData(page_uuid=PAGE_A, detail="truncated after the header", offset=43)
    with pytest.raises(CorruptPageData):
        RenderPages(
            bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
            codec=_RecordingCodec(failure),
            renderer=_CountingRenderer(),
            rasterizer=_CountingRasterizer(),
        ).render(_request())


def test_a_page_identifier_that_cannot_be_an_identity_is_a_malformed_document():
    """``PageId`` refuses separators so a store may join it into a path unsanitised."""
    with pytest.raises(MalformedDocument) as caught:
        RenderPages(
            bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id="../../etc", scene=INK))),
            codec=_RecordingCodec(),
            renderer=_CountingRenderer(),
            rasterizer=_CountingRasterizer(),
        ).render(_request())
    assert caught.value.document_uuid == DOC
    assert "'../../etc'" in caught.value.detail
    assert exit_code(caught.value) == 65


# ───────────────────────── the selection and the work cap ─────────────────────────


def _three_pages() -> DocumentSourceBundle:
    return _bundle(
        DevicePageSource(page_id=PAGE_A, scene=INK),
        DevicePageSource(page_id=PAGE_B, scene=INK),
        DevicePageSource(page_id=PAGE_C, scene=INK),
    )


def _render(request: RenderPagesRequest) -> RenderPagesResult:
    return RenderPages(
        bundles=_InMemoryBundles(_three_pages()),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(request)


def test_every_page_is_rendered_in_document_order():
    result = _render(_request())
    assert [artifact.page_ref for artifact in result.pages] == [PAGE_A, PAGE_B, PAGE_C]


def test_an_explicit_selection_keeps_the_document_index_rather_than_its_own():
    """``page_index`` names a position in the document; nothing here renumbers."""
    result = _render(_request(selection=PageSelection.of(2, 0)))
    assert [artifact.page_index for artifact in result.pages] == [0, 2]
    assert [artifact.page_ref for artifact in result.pages] == [PAGE_A, PAGE_C]


def test_a_leading_limit_is_a_bound_rather_than_an_assertion():
    result = _render(_request(selection=PageSelection.first(9)))
    assert len(result.pages) == 3


def test_a_page_the_document_does_not_have_costs_no_decode_and_no_render():
    codec = _RecordingCodec()
    renderer = _CountingRenderer()
    with pytest.raises(PageNotFound):
        RenderPages(
            bundles=_InMemoryBundles(_three_pages()),
            codec=codec,
            renderer=renderer,
            rasterizer=_CountingRasterizer(),
        ).render(_request(selection=PageSelection.of(7)))
    assert codec.decoded == []
    assert renderer.pages == []


def test_a_selection_over_the_cap_is_refused_before_anything_is_decoded():
    """One 432-page document must not silently become 432 rasterizations."""
    codec = _RecordingCodec()
    with pytest.raises(UsageError) as caught:
        RenderPages(
            bundles=_InMemoryBundles(_three_pages()),
            codec=codec,
            renderer=_CountingRenderer(),
            rasterizer=_CountingRasterizer(),
        ).render(_request(max_pages=2))
    assert "at most 2 pages" in caught.value.requirement
    assert codec.decoded == []


def test_a_cap_of_zero_blames_the_wiring_rather_than_the_command_line():
    with pytest.raises(InvalidSettingError) as caught:
        _render(_request(max_pages=0))
    assert caught.value.setting == "max_pages"


def test_a_document_with_no_pages_renders_nothing_rather_than_failing():
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle()),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert result.pages == ()
    assert result.degradations == ()


def test_one_render_is_one_device_handshake():
    bundles = _InMemoryBundles(_three_pages())
    RenderPages(
        bundles=bundles,
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert bundles.calls == 1


def test_a_dead_transport_is_never_degraded_into_an_empty_render():
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connection refused",
    )
    with pytest.raises(DeviceUnreachable):
        RenderPages(
            bundles=_InMemoryBundles(_three_pages(), failure=failure),
            codec=_RecordingCodec(),
            renderer=_CountingRenderer(),
            rasterizer=_CountingRasterizer(),
        ).render(_request())


# ─────────────────────────── the render identity ───────────────────────────


def test_the_render_digest_is_the_styles_own_digest():
    result = _render(_request())
    assert result.render_digest == STYLE.digest(
        screen=PAPER_PRO_SCREEN, palette=EXPORT_PALETTE, background=None
    )


def test_the_background_is_folded_into_the_render_identity():
    """A background that could be omitted from the identity is a stale row one component along."""
    bare = _render(_request()).render_digest
    with_template = _render(_request(background=TEMPLATE)).render_digest
    assert bare != with_template


def test_the_palette_is_folded_into_the_render_identity():
    other = Palette(name="paper-pro-physical", inks=dict(EXPORT_PALETTE.inks))
    assert _render(_request(palette=other)).render_digest != _render(_request()).render_digest


def test_the_background_reaches_the_renderer_rather_than_only_the_digest():
    renderer = _CountingRenderer()
    RenderPages(
        bundles=_InMemoryBundles(_three_pages()),
        codec=_RecordingCodec(),
        renderer=renderer,
        rasterizer=_CountingRasterizer(),
    ).render(_request(background=TEMPLATE))
    assert renderer.backgrounds == [TEMPLATE, TEMPLATE, TEMPLATE]


def test_no_background_reaches_the_renderer_as_none():
    renderer = _CountingRenderer()
    RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=_RecordingCodec(),
        renderer=renderer,
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert renderer.backgrounds == [None]


def test_every_page_of_one_call_shares_one_render_identity():
    """It is one value, so it is stored once: two spellings is how two come to disagree."""
    assert "render_digest" not in RenderedPageArtifact.model_fields


def test_a_renderer_failure_is_not_degraded():
    failure = UnsupportedPenType(pen="42", page_ref=PAGE_A)
    with pytest.raises(UnsupportedPenType):
        RenderPages(
            bundles=_InMemoryBundles(_three_pages()),
            codec=_RecordingCodec(),
            renderer=_CountingRenderer(failure=failure),
            rasterizer=_CountingRasterizer(),
        ).render(_request())


# ─────────────────────── notices, and the line drawn at them ───────────────────────


def test_notices_travel_back_verbatim_on_the_rendered_page():
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(notices=(VIEWPORT,)),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert result.pages[0].rendered.notices == (VIEWPORT,)


@pytest.mark.parametrize("notice", [VIEWPORT, OMITTED])
def test_no_render_notice_becomes_a_degradation(notice: RenderNotice):
    """``DegradationKind`` is closed and has no member for any of the three notice codes.

    ``VIEWPORT_EXPANDED`` additionally fires on every real page, so promoting it would put
    one degradation on every page of every correct render.
    """
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(notices=(notice,)),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert result.degradations == ()
    assert result.pages[0].rendered.notices == (notice,)


def test_a_stub_page_is_not_reported_as_a_degradation_either():
    """Nothing was substituted: the page has no ink and a blank page was produced."""
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=None))),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request())
    assert result.degradations == ()


# ─────────────────────────── the rasterize hop ───────────────────────────


def test_no_raster_dpi_means_no_rasterizer_call_at_all():
    rasterizer = _CountingRasterizer()
    result = RenderPages(
        bundles=_InMemoryBundles(_three_pages()),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=rasterizer,
    ).render(_request())
    assert rasterizer.requested == []
    assert [artifact.raster for artifact in result.pages] == [None, None, None]


def test_a_raster_dpi_rasterizes_every_selected_page_once():
    rasterizer = _CountingRasterizer()
    result = RenderPages(
        bundles=_InMemoryBundles(_three_pages()),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=rasterizer,
    ).render(_request(raster_dpi=300))
    assert [dpi for _, dpi in rasterizer.requested] == [300, 300, 300]
    assert [artifact.raster is None for artifact in result.pages] == [False, False, False]


def test_the_raster_carries_the_dpi_it_was_rendered_at():
    result = RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=_CountingRasterizer(),
    ).render(_request(raster_dpi=300))
    raster = result.pages[0].raster
    assert raster is not None
    assert raster.render_dpi == 300
    assert raster.digest() == raster.digest()


def test_the_page_handed_to_the_rasterizer_keeps_the_rendered_size_unrotated():
    """The Paper Pro page is not square, so a transposed re-wrap is visible here.

    ``ports/render.py`` predicts this exact failure: "a transposed ``width_mm``/``height_mm``
    in that re-wrap type checks clean and silently rotates the page."
    """
    rasterizer = _CountingRasterizer()
    RenderPages(
        bundles=_InMemoryBundles(_bundle(DevicePageSource(page_id=PAGE_A, scene=INK))),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=rasterizer,
    ).render(_request(raster_dpi=226))
    (svg_page, _) = rasterizer.requested[0]
    assert svg_page.size.width_mm == pytest.approx(PAPER_PRO_SCREEN.width_mm)
    assert svg_page.size.height_mm == pytest.approx(PAPER_PRO_SCREEN.height_mm)
    assert svg_page.size.width_mm < svg_page.size.height_mm


def test_the_page_handed_to_the_rasterizer_carries_its_own_markup_and_ref():
    """``SvgPage(page_ref=other, svg=rendered.svg)`` type-checks and misattributes markup."""
    rasterizer = _CountingRasterizer()
    result = RenderPages(
        bundles=_InMemoryBundles(_three_pages()),
        codec=_RecordingCodec(),
        renderer=_CountingRenderer(),
        rasterizer=rasterizer,
    ).render(_request(raster_dpi=300))
    for artifact, (svg_page, _) in zip(result.pages, rasterizer.requested, strict=True):
        assert svg_page.page_ref == artifact.page_ref
        assert svg_page.svg == artifact.rendered.svg
        assert artifact.raster is not None
        assert artifact.raster.page_ref == artifact.page_ref


def test_a_rasterizer_failure_is_not_degraded():
    failure = RasterizationFailed(backend="cairo", detail="surface exhausted", page_ref=PAGE_A)
    with pytest.raises(RasterizationFailed):
        RenderPages(
            bundles=_InMemoryBundles(_three_pages()),
            codec=_RecordingCodec(),
            renderer=_CountingRenderer(),
            rasterizer=_CountingRasterizer(failure),
        ).render(_request(raster_dpi=300))


@pytest.mark.parametrize("dpi", [0, -300])
def test_a_non_positive_raster_dpi_is_unconstructible_rather_than_an_error_class(dpi: int):
    """The export slice deleted ``InvalidResolution`` for exactly this reason."""
    with pytest.raises(ValidationError):
        _request(raster_dpi=dpi)


# ───────────────────────────── the model contracts ─────────────────────────────


def test_a_result_is_frozen():
    result = _render(_request())
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "render_digest", "0" * 64)


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        RenderPagesResult.model_validate(
            {
                "document_uuid": DOC,
                "pages": (),
                "render_digest": "0" * 64,
                "degradations": (),
                "rendered_at": "2026-08-29",
            }
        )


def test_an_artifact_cannot_be_built_without_saying_whether_it_rasterized():
    assert RenderedPageArtifact.model_fields["raster"].is_required()


def test_a_request_refuses_a_selection_that_names_two_selections_at_once():
    with pytest.raises(ValidationError):
        _request(selection={"indices": (0,), "limit": 2})


def test_the_fakes_are_the_ports_the_use_case_declares():
    """The annotations are the assertion: ``ty`` checks conformance at every binding site."""
    bundles: RawBundleSource = _InMemoryBundles(_three_pages())
    codec: PageCodec = _RecordingCodec()
    renderer: PageRenderer = _CountingRenderer()
    rasterizer: SvgRasterizer = _CountingRasterizer()
    assert bundles.load_bundle(DOC).document.uuid == DOC
    assert codec.decode_page(INK, PAGE_A).stroke_count == 1
    rendered = renderer.render(
        Page(page_id=PageId(uuid=PAGE_A), index=0, content=_content()),
        screen=PAPER_PRO_SCREEN,
        palette=EXPORT_PALETTE,
        style=STYLE,
    )
    assert rendered.page_ref == PAGE_A
    assert rasterizer.to_png(_as_svg_page(rendered), dpi=226).render_dpi == 226
