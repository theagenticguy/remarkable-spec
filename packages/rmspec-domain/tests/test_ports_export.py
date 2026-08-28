"""Behaviour tests for the export slice's value objects, validators and geometry."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from rmspec.domain.ports import export as export_module
from rmspec.domain.ports.export import (
    PAGE_SIZE_TOLERANCE_MM,
    ArtifactMedia,
    ArtifactName,
    ArtifactRef,
    ImageMedia,
    PdfDocument,
    PdfPageBackground,
    PdfPageRef,
    PdfSourceRef,
    PhysicalSize,
    PixelSize,
    RasterImage,
    SvgPage,
    SvgPageSet,
    _png_pixel_size,
)

# --------------------------------------------------------------------------------------
# byte fixtures: the smallest streams the validators accept, built by hand so no test
# needs a real PNG or PDF on disk
# --------------------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"


def _assert_frozen(model: BaseModel, field: str, value: object) -> None:
    """Assert that a frozen model refuses field assignment (setattr keeps ty honest)."""
    with pytest.raises(ValidationError):
        setattr(model, field, value)


def _png(width: int, height: int, *, filler: bytes = b"", trailer: bytes = _IEND) -> bytes:
    return (
        _PNG_SIGNATURE
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + filler
        + trailer
    )


def _jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 8 + b"\xff\xd9"


def _pdf(*, pad: int = 0) -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF" + b"\x00" * pad


def _raster(
    *,
    width: int = 4,
    height: int = 6,
    dpi: int = 229,
    page_ref: str = "page-1",
    filler: bytes = b"",
) -> RasterImage:
    return RasterImage(
        page_ref=page_ref,
        media=ImageMedia.PNG,
        data=_png(width, height, filler=filler),
        width=width,
        height=height,
        render_dpi=dpi,
    )


def _svg_page(page_ref: str = "page-1", *, width_mm: float = 100.0) -> SvgPage:
    return SvgPage(
        page_ref=page_ref,
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm"></svg>',
        size=PhysicalSize(width_mm=width_mm, height_mm=200.0),
    )


_MM = st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False)
_PX = st.integers(min_value=1, max_value=10_000)
_SAFE_NAME = (
    st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126, exclude_characters="/\\"),
        min_size=1,
        max_size=40,
    )
    .filter(lambda value: value not in {".", ".."})
    .filter(lambda value: not value.endswith("."))
)


# --------------------------------------------------------------------------------------
# PixelSize.from_dpi -- the module's single definition of "DPI"
# --------------------------------------------------------------------------------------


def test_from_dpi_turns_a4_millimetres_into_the_expected_pixel_count():
    a4 = PhysicalSize(width_mm=210.0, height_mm=297.0)

    assert PixelSize.from_dpi(a4, 72) == PixelSize(width_px=595, height_px=842)


def test_from_dpi_doubles_the_pixel_count_when_the_resolution_doubles():
    page = PhysicalSize(width_mm=254.0, height_mm=127.0)

    assert PixelSize.from_dpi(page, 100) == PixelSize(width_px=1000, height_px=500)
    assert PixelSize.from_dpi(page, 200) == PixelSize(width_px=2000, height_px=1000)


@pytest.mark.parametrize("dpi", [0, -1, -229])
def test_from_dpi_refuses_a_non_positive_resolution(dpi: int):
    page = PhysicalSize(width_mm=10.0, height_mm=10.0)

    with pytest.raises(ValueError, match="dpi must be positive"):
        PixelSize.from_dpi(page, dpi)


def test_from_dpi_clamps_a_sub_pixel_page_to_one_pixel():
    speck = PhysicalSize(width_mm=0.01, height_mm=0.01)

    assert PixelSize.from_dpi(speck, 1) == PixelSize(width_px=1, height_px=1)


@given(width_mm=_MM, height_mm=_MM, low=_PX, high=_PX)
@settings(deadline=None)
def test_from_dpi_is_monotonic_in_resolution(
    width_mm: float,
    height_mm: float,
    low: int,
    high: int,
):
    page = PhysicalSize(width_mm=width_mm, height_mm=height_mm)
    smaller, larger = sorted((low, high))

    coarse = PixelSize.from_dpi(page, smaller)
    fine = PixelSize.from_dpi(page, larger)

    assert coarse.width_px <= fine.width_px
    assert coarse.height_px <= fine.height_px


@given(width_mm=_MM, height_mm=_MM, dpi=_PX)
@settings(deadline=None)
def test_from_dpi_preserves_which_axis_is_longer(width_mm: float, height_mm: float, dpi: int):
    page = PhysicalSize(width_mm=width_mm, height_mm=height_mm)

    size = PixelSize.from_dpi(page, dpi)

    if width_mm >= height_mm:
        assert size.width_px >= size.height_px
    else:
        assert size.width_px <= size.height_px


# --------------------------------------------------------------------------------------
# PixelSize.fit_within -- the module's single definition of "fit this page in that box"
# --------------------------------------------------------------------------------------


def test_fit_within_supersamples_by_two_by_default():
    page = PhysicalSize(width_mm=100.0, height_mm=200.0)
    box = PixelSize(width_px=100, height_px=200)

    assert PixelSize.fit_within(page, box) == PixelSize(width_px=200, height_px=400)


def test_fit_within_is_not_bounded_by_the_box_it_fits_to():
    page = PhysicalSize(width_mm=50.0, height_mm=50.0)
    box = PixelSize(width_px=100, height_px=100)

    fitted = PixelSize.fit_within(page, box, oversample=3)

    assert fitted.width_px == 300
    assert fitted.width_px > box.width_px


def test_fit_within_letterboxes_on_the_constrained_axis():
    page = PhysicalSize(width_mm=100.0, height_mm=50.0)
    box = PixelSize(width_px=100, height_px=100)

    assert PixelSize.fit_within(page, box, oversample=1) == PixelSize(width_px=100, height_px=50)


@pytest.mark.parametrize("oversample", [0, -1, -8])
def test_fit_within_refuses_an_oversample_below_one(oversample: int):
    page = PhysicalSize(width_mm=10.0, height_mm=10.0)
    box = PixelSize(width_px=10, height_px=10)

    with pytest.raises(ValueError, match="oversample must be at least 1"):
        PixelSize.fit_within(page, box, oversample=oversample)


def test_fit_within_clamps_an_extreme_aspect_ratio_to_one_pixel():
    sliver = PhysicalSize(width_mm=1000.0, height_mm=1.0)
    box = PixelSize(width_px=1, height_px=1)

    assert PixelSize.fit_within(sliver, box, oversample=1) == PixelSize(width_px=1, height_px=1)


@given(
    width_mm=_MM,
    height_mm=_MM,
    box_width=_PX,
    box_height=_PX,
    oversample=st.integers(min_value=1, max_value=8),
)
@settings(deadline=None)
def test_fit_within_never_exceeds_the_supersampled_box(
    width_mm: float,
    height_mm: float,
    box_width: int,
    box_height: int,
    oversample: int,
):
    page = PhysicalSize(width_mm=width_mm, height_mm=height_mm)
    box = PixelSize(width_px=box_width, height_px=box_height)

    fitted = PixelSize.fit_within(page, box, oversample=oversample)

    assert fitted.width_px <= box_width * oversample
    assert fitted.height_px <= box_height * oversample
    assert fitted.width_px >= 1
    assert fitted.height_px >= 1


@given(width_mm=_MM, height_mm=_MM, box_width=_PX, box_height=_PX)
@settings(deadline=None)
def test_fit_within_saturates_at_least_one_axis_of_the_box(
    width_mm: float,
    height_mm: float,
    box_width: int,
    box_height: int,
):
    page = PhysicalSize(width_mm=width_mm, height_mm=height_mm)
    box = PixelSize(width_px=box_width, height_px=box_height)

    fitted = PixelSize.fit_within(page, box, oversample=1)

    touches_width = fitted.width_px == box_width
    touches_height = fitted.height_px == box_height
    clamped = fitted.width_px == 1 or fitted.height_px == 1
    assert touches_width or touches_height or clamped


# --------------------------------------------------------------------------------------
# geometry value objects
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width_mm", "height_mm"),
    [(0.0, 10.0), (10.0, 0.0), (-1.0, 10.0), (10.0, -1.0)],
)
def test_physical_size_refuses_a_non_positive_dimension(width_mm: float, height_mm: float):
    with pytest.raises(ValidationError):
        PhysicalSize(width_mm=width_mm, height_mm=height_mm)


@pytest.mark.parametrize(("width_px", "height_px"), [(0, 10), (10, 0), (-1, 10), (10, -1)])
def test_pixel_size_refuses_a_non_positive_dimension(width_px: int, height_px: int):
    with pytest.raises(ValidationError):
        PixelSize(width_px=width_px, height_px=height_px)


def test_pixel_size_refuses_a_fractional_pixel_count():
    with pytest.raises(ValidationError):
        PixelSize(width_px=10.5, height_px=10)


def test_geometry_values_are_frozen_hashable_and_closed_to_extra_fields():
    size = PixelSize(width_px=10, height_px=20)

    assert {size, PixelSize(width_px=10, height_px=20)} == {size}
    _assert_frozen(size, "width_px", 11)
    with pytest.raises(ValidationError):
        PixelSize(width_px=10, height_px=20, dpi=300)  # ty: ignore[unknown-argument]
    with pytest.raises(ValidationError):
        PhysicalSize(width_mm=1.0, height_mm=1.0, unit="mm")  # ty: ignore[unknown-argument]


@given(width_mm=_MM, height_mm=_MM)
@settings(deadline=None)
def test_physical_size_round_trips_through_a_dump(width_mm: float, height_mm: float):
    size = PhysicalSize(width_mm=width_mm, height_mm=height_mm)

    assert PhysicalSize.model_validate(size.model_dump()) == size
    assert PhysicalSize.model_validate_json(size.model_dump_json()) == size


def test_page_size_tolerance_absorbs_conversion_noise_but_not_a_wrong_page_size():
    requested = PhysicalSize(width_mm=179.7, height_mm=239.5)
    round_tripped = requested.width_mm * 72 / 25.4 * 25.4 / 72

    assert abs(round_tripped - requested.width_mm) < PAGE_SIZE_TOLERANCE_MM
    assert PAGE_SIZE_TOLERANCE_MM < 1.0
    assert abs(210.0 - requested.width_mm) > PAGE_SIZE_TOLERANCE_MM


# --------------------------------------------------------------------------------------
# PNG header inspection
# --------------------------------------------------------------------------------------


def test_png_pixel_size_reads_the_dimensions_out_of_the_ihdr_chunk():
    assert _png_pixel_size(_png(1620, 2160)) == (1620, 2160)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"x",
        _PNG_SIGNATURE,
        b"\x00" * 64,
        b"png:<svg/>" + b"\x00" * 32,
        _PNG_SIGNATURE + (13).to_bytes(4, "big") + b"iCCP" + b"\x00" * 8,
    ],
)
def test_png_pixel_size_returns_none_for_anything_but_a_png_opening_with_ihdr(data: bytes):
    assert _png_pixel_size(data) is None


# --------------------------------------------------------------------------------------
# RasterImage -- values that cannot lie about their bytes
# --------------------------------------------------------------------------------------


def test_raster_image_accepts_pixels_that_agree_with_their_header():
    raster = _raster(width=8, height=12)

    assert raster.width == 8
    assert raster.height == 12
    assert _png_pixel_size(raster.data) == (8, 12)


def test_raster_image_refuses_a_declared_size_the_header_contradicts():
    with pytest.raises(ValidationError, match="disagrees with PNG header"):
        RasterImage(
            page_ref="page-1",
            media=ImageMedia.PNG,
            data=_png(11, 12),
            width=10,
            height=12,
            render_dpi=229,
        )


def test_raster_image_refuses_a_stream_truncated_after_its_header():
    with pytest.raises(ValidationError, match="truncated"):
        RasterImage(
            page_ref="page-1",
            media=ImageMedia.PNG,
            data=_png(4, 6, trailer=b""),
            width=4,
            height=6,
            render_dpi=229,
        )


def test_raster_image_refuses_the_bytes_a_lazy_fake_would_return():
    with pytest.raises(ValidationError, match="PNG signature and IHDR chunk"):
        RasterImage(
            page_ref="page-1",
            media=ImageMedia.PNG,
            data=b"png:<svg/>",
            width=4,
            height=6,
            render_dpi=229,
        )


def test_raster_image_refuses_png_bytes_declared_as_jpeg():
    with pytest.raises(ValidationError, match="JPEG marker"):
        RasterImage(
            page_ref="page-1",
            media=ImageMedia.JPEG,
            data=_png(4, 6),
            width=4,
            height=6,
            render_dpi=229,
        )


def test_raster_image_does_not_check_dimensions_of_jpeg_bytes():
    raster = RasterImage(
        page_ref="page-1",
        media=ImageMedia.JPEG,
        data=_jpeg(),
        width=999,
        height=1,
        render_dpi=96,
    )

    assert raster.media is ImageMedia.JPEG
    assert raster.width == 999


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_ref", ""),
        ("data", b""),
        ("width", 0),
        ("height", 0),
        ("width", -4),
        ("render_dpi", 0),
        ("render_dpi", -229),
    ],
)
def test_raster_image_refuses_empty_or_non_positive_fields(field: str, value: object):
    payload: dict[str, object] = {
        "page_ref": "page-1",
        "media": ImageMedia.PNG,
        "data": _png(4, 6),
        "width": 4,
        "height": 6,
        "render_dpi": 229,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        RasterImage.model_validate(payload)


def test_raster_image_is_frozen_and_closed_to_extra_fields():
    raster = _raster()

    _assert_frozen(raster, "render_dpi", 300)
    with pytest.raises(ValidationError):
        RasterImage.model_validate({**raster.model_dump(), "scale": 2})


def test_raster_image_round_trips_through_a_dump():
    raster = _raster(width=3, height=5, dpi=300, page_ref="doc/page-9")

    assert RasterImage.model_validate(raster.model_dump()) == raster


def test_digest_is_a_lowercase_hex_sha256():
    digest = _raster().digest()

    assert len(digest) == len(hashlib.sha256(b"").hexdigest())
    assert digest == digest.lower()
    assert int(digest, 16) >= 0


def test_digest_is_stable_across_identically_built_rasters():
    assert _raster().digest() == _raster().digest()


def test_digest_ignores_the_page_slot_the_pixels_were_rendered_for():
    left = _raster(page_ref="page-1")
    right = _raster(page_ref="page-77")

    assert left != right
    assert left.digest() == right.digest()


def test_digest_changes_when_the_render_dpi_changes():
    at_150 = _raster(dpi=150)
    at_300 = _raster(dpi=300)

    assert at_150.data == at_300.data
    assert at_150.digest() != at_300.digest()


def test_digest_changes_when_the_pixels_change_at_the_same_declared_size():
    plain = _raster(filler=b"")
    embellished = _raster(filler=b"\x00\x00\x00\x01tEXt")

    assert plain.width == embellished.width
    assert plain.height == embellished.height
    assert plain.digest() != embellished.digest()


def test_digest_changes_when_the_pixel_dimensions_change():
    assert _raster(width=4, height=6).digest() != _raster(width=6, height=4).digest()


def test_digest_cannot_be_forged_by_shifting_a_dimension_boundary():
    tall = _raster(width=1, height=11, dpi=2)
    wide = _raster(width=1, height=1, dpi=12)

    assert tall.digest() != wide.digest()


# --------------------------------------------------------------------------------------
# SvgPage and SvgPageSet
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("markup", ["x", "<html></html>", "svg", "<SVG></SVG>"])
def test_svg_page_refuses_text_without_an_svg_root(markup: str):
    with pytest.raises(ValidationError, match="<svg> root element"):
        SvgPage(
            page_ref="page-1",
            svg=markup,
            size=PhysicalSize(width_mm=100.0, height_mm=200.0),
        )


@pytest.mark.parametrize(
    "markup",
    [
        "<svg/>",
        '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>',
        '<svg width="100mm" height="200mm"><path d="M0 0 L1 1"/></svg>',
    ],
)
def test_svg_page_accepts_markup_carrying_an_svg_root(markup: str):
    page = SvgPage(
        page_ref="page-1",
        svg=markup,
        size=PhysicalSize(width_mm=100.0, height_mm=200.0),
    )

    assert page.svg == markup


@pytest.mark.parametrize(("page_ref", "svg"), [("", "<svg/>"), ("page-1", "")])
def test_svg_page_refuses_empty_identity_or_empty_markup(page_ref: str, svg: str):
    with pytest.raises(ValidationError):
        SvgPage(
            page_ref=page_ref,
            svg=svg,
            size=PhysicalSize(width_mm=100.0, height_mm=200.0),
        )


def test_svg_page_round_trips_through_json():
    page = _svg_page()

    assert SvgPage.model_validate_json(page.model_dump_json()) == page


def test_page_set_refuses_an_empty_export():
    with pytest.raises(ValidationError):
        SvgPageSet(pages=())


def test_page_set_refuses_repeated_page_references():
    with pytest.raises(ValidationError, match="must be distinct"):
        SvgPageSet(pages=(_svg_page("page-1"), _svg_page("page-1", width_mm=120.0)))


def test_page_set_keeps_order_and_admits_pages_of_different_sizes():
    first = _svg_page("page-1", width_mm=100.0)
    second = _svg_page("page-2", width_mm=150.0)

    page_set = SvgPageSet(pages=[second, first])

    assert isinstance(page_set.pages, tuple)
    assert [page.page_ref for page in page_set.pages] == ["page-2", "page-1"]
    assert page_set.pages[0].size != page_set.pages[1].size


def test_page_set_is_frozen():
    page_set = SvgPageSet(pages=(_svg_page(),))

    _assert_frozen(page_set, "pages", ())


# --------------------------------------------------------------------------------------
# PdfDocument and PdfPageRef
# --------------------------------------------------------------------------------------


def _page_ref(page_ref: str = "page-1", *, width_mm: float = 100.0) -> PdfPageRef:
    return PdfPageRef(
        page_ref=page_ref,
        size=PhysicalSize(width_mm=width_mm, height_mm=200.0),
    )


def test_pdf_document_accepts_a_finished_document():
    document = PdfDocument(data=_pdf(), pages=(_page_ref(),))

    assert document.data.startswith(b"%PDF-")
    assert len(document.pages) == 1


@pytest.mark.parametrize(
    "data",
    [b"1 0 obj\n%%EOF", b"\x00%PDF-1.7\n%%EOF", b"PDF-1.7\n%%EOF"],
)
def test_pdf_document_refuses_bytes_that_do_not_open_with_a_pdf_header(data: bytes):
    with pytest.raises(ValidationError, match=r"%PDF- header"):
        PdfDocument(data=data, pages=(_page_ref(),))


def test_pdf_document_refuses_a_surface_whose_buffer_was_never_flushed():
    with pytest.raises(ValidationError, match="never finished"):
        PdfDocument(data=b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n", pages=(_page_ref(),))


def test_pdf_document_accepts_an_eof_marker_inside_the_trailer_window():
    document = PdfDocument(data=_pdf(pad=1019), pages=(_page_ref(),))

    assert document.data.endswith(b"\x00")


def test_pdf_document_refuses_an_eof_marker_pushed_outside_the_trailer_window():
    with pytest.raises(ValidationError, match="never finished"):
        PdfDocument(data=_pdf(pad=1020), pages=(_page_ref(),))


def test_pdf_document_refuses_zero_pages():
    with pytest.raises(ValidationError):
        PdfDocument(data=_pdf(), pages=())


def test_pdf_document_provenance_is_comparable_element_by_element_to_the_request():
    page_set = SvgPageSet(
        pages=(_svg_page("page-1", width_mm=100.0), _svg_page("page-2", width_mm=150.0))
    )
    honest = PdfDocument(
        data=_pdf(),
        pages=tuple(
            _page_ref(page.page_ref, width_mm=page.size.width_mm) for page in page_set.pages
        ),
    )
    truncating = PdfDocument(data=_pdf(), pages=(_page_ref("page-1", width_mm=100.0),))

    requested = [(page.page_ref, page.size) for page in page_set.pages]
    assert [(ref.page_ref, ref.size) for ref in honest.pages] == requested
    assert [(ref.page_ref, ref.size) for ref in truncating.pages] != requested


def test_pdf_page_ref_refuses_an_empty_page_reference():
    with pytest.raises(ValidationError):
        PdfPageRef(page_ref="", size=PhysicalSize(width_mm=100.0, height_mm=200.0))


def test_pdf_document_is_frozen_and_closed_to_a_self_reported_count():
    document = PdfDocument(data=_pdf(), pages=(_page_ref(),))

    _assert_frozen(document, "pages", ())
    with pytest.raises(ValidationError):
        PdfDocument(data=_pdf(), pages=(_page_ref(),), page_count=1)  # ty: ignore[unknown-argument]


# --------------------------------------------------------------------------------------
# PdfSourceRef -- an opaque token, usable as a dictionary key
# --------------------------------------------------------------------------------------


def _source(value: str) -> PdfSourceRef:
    return PdfSourceRef.model_validate({"token": value})


def test_pdf_source_ref_refuses_an_empty_token():
    with pytest.raises(ValidationError):
        _source("")


def test_pdf_source_ref_keys_a_dictionary_by_value():
    fixtures = {_source("doc-a/page.pdf"): b"a", _source("doc-b"): b"b"}

    assert fixtures[_source("doc-a/page.pdf")] == b"a"
    assert _source("doc-a/page.pdf") == _source("doc-a/page.pdf")
    assert _source("doc-a") != _source("doc-b")


def test_pdf_source_ref_is_frozen_and_closed_to_extra_fields():
    source = _source("doc-a")
    replacement = "doc-b"

    _assert_frozen(source, "token", replacement)
    with pytest.raises(ValidationError):
        PdfSourceRef.model_validate({"token": "doc-a", "path": "file:///exports/doc-a.pdf"})


# --------------------------------------------------------------------------------------
# PdfPageBackground
# --------------------------------------------------------------------------------------


def _background(
    *,
    page_index: int = 0,
    pixel_size: PixelSize | None = None,
    page_size: PhysicalSize | None = None,
) -> PdfPageBackground:
    size = pixel_size or PixelSize(width_px=10, height_px=20)
    return PdfPageBackground(
        page_index=page_index,
        data=_png(size.width_px, size.height_px),
        pixel_size=size,
        page_size=page_size or PhysicalSize(width_mm=100.0, height_mm=200.0),
    )


def test_background_pins_its_media_to_png_without_being_told():
    background = _background()

    assert background.media is ImageMedia.PNG


def test_background_refuses_jpeg_media():
    with pytest.raises(ValidationError):
        PdfPageBackground(
            page_index=0,
            media=ImageMedia.JPEG,  # ty: ignore[invalid-argument-type]
            data=_jpeg(),
            pixel_size=PixelSize(width_px=10, height_px=20),
            page_size=PhysicalSize(width_mm=100.0, height_mm=200.0),
        )


def test_background_refuses_a_pixel_size_its_header_contradicts():
    with pytest.raises(ValidationError, match="disagrees with PNG header"):
        PdfPageBackground(
            page_index=0,
            data=_png(8, 20),
            pixel_size=PixelSize(width_px=10, height_px=20),
            page_size=PhysicalSize(width_mm=100.0, height_mm=200.0),
        )


def test_background_refuses_a_truncated_stream():
    with pytest.raises(ValidationError, match="truncated"):
        PdfPageBackground(
            page_index=0,
            data=_png(10, 20, trailer=b""),
            pixel_size=PixelSize(width_px=10, height_px=20),
            page_size=PhysicalSize(width_mm=100.0, height_mm=200.0),
        )


def test_background_accepts_the_first_page_and_refuses_a_negative_index():
    assert _background(page_index=0).page_index == 0
    with pytest.raises(ValidationError):
        _background(page_index=-1)


def test_background_pixel_size_can_be_derived_from_the_shared_fit_formula():
    page_size = PhysicalSize(width_mm=100.0, height_mm=200.0)
    box = PixelSize(width_px=100, height_px=200)
    fitted = PixelSize.fit_within(page_size, box, oversample=2)

    background = _background(pixel_size=fitted, page_size=page_size)

    assert background.pixel_size == PixelSize.fit_within(background.page_size, box, oversample=2)
    assert background.pixel_size != PixelSize(width_px=8, height_px=8)


def test_background_is_frozen_and_closed_to_extra_fields():
    background = _background()

    _assert_frozen(background, "page_index", 3)
    with pytest.raises(ValidationError):
        PdfPageBackground.model_validate({**background.model_dump(), "render_dpi": 96})


# --------------------------------------------------------------------------------------
# ArtifactName
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "../../etc/passwd",
        "notes/page-1",
        "notes\\page-1",
        "/absolute",
        ".",
        "..",
        "page-1.",
        "trailing ",
        " leading",
        "\tpadded\t",
        "page\x001",
        "",
    ],
)
def test_artifact_name_refuses_anything_but_one_clean_segment(value: str):
    with pytest.raises(ValidationError):
        ArtifactName(value=value)


@pytest.mark.parametrize(
    "value",
    ["page-001", "a", "my.notes.v2", "..hidden", "Meeting Notes 2026", "page_1-final"],
)
def test_artifact_name_accepts_a_plain_stem(value: str):
    assert ArtifactName(value=value).value == value


@given(value=_SAFE_NAME)
@settings(deadline=None)
def test_artifact_name_accepts_every_separator_free_stem(value: str):
    assert ArtifactName(value=value).value == value


@given(value=_SAFE_NAME, separator=st.sampled_from(["/", "\\", "\x00"]), index=st.integers(0, 40))
@settings(deadline=None)
def test_artifact_name_refuses_a_stem_with_a_separator_spliced_in(
    value: str,
    separator: str,
    index: int,
):
    position = index % (len(value) + 1)
    spliced = value[:position] + separator + value[position:]

    with pytest.raises(ValidationError):
        ArtifactName(value=spliced)


def test_artifact_name_is_frozen_and_closed_to_extra_fields():
    name = ArtifactName(value="page-001")

    _assert_frozen(name, "value", "../escape")
    with pytest.raises(ValidationError):
        ArtifactName(value="page-001", suffix="svg")  # ty: ignore[unknown-argument]


# --------------------------------------------------------------------------------------
# ArtifactRef
# --------------------------------------------------------------------------------------


def test_artifact_ref_reports_a_simulated_write_as_uncommitted():
    receipt = ArtifactRef(
        name=ArtifactName(value="page-001"),
        uri="file:///tmp/out/page-001.png",
        byte_count=0,
        media=ArtifactMedia.PNG,
        committed=False,
    )

    assert receipt.committed is False
    assert receipt.byte_count == 0
    assert receipt.name.value == "page-001"


def test_artifact_ref_refuses_a_negative_byte_count():
    with pytest.raises(ValidationError):
        ArtifactRef(
            name=ArtifactName(value="page-001"),
            uri="file:///tmp/out/page-001.png",
            byte_count=-1,
            media=ArtifactMedia.PNG,
            committed=True,
        )


def test_artifact_ref_refuses_an_empty_uri():
    with pytest.raises(ValidationError):
        ArtifactRef(
            name=ArtifactName(value="page-001"),
            uri="",
            byte_count=10,
            media=ArtifactMedia.PNG,
            committed=True,
        )


def test_artifact_ref_requires_the_commit_flag_to_be_stated():
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate(
            {
                "name": {"value": "page-001"},
                "uri": "file:///tmp/out/page-001.pdf",
                "byte_count": 10,
                "media": "pdf",
            }
        )


def test_artifact_ref_validates_a_nested_name_and_a_media_string():
    receipt = ArtifactRef.model_validate(
        {
            "name": {"value": "page-001"},
            "uri": "s3://bucket/exports/page-001.pdf",
            "byte_count": 10,
            "media": "pdf",
            "committed": True,
        }
    )

    assert receipt.media is ArtifactMedia.PDF
    assert isinstance(receipt.name, ArtifactName)
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate(
            {
                "name": {"value": "../escape"},
                "uri": "s3://bucket/exports",
                "byte_count": 10,
                "media": "pdf",
                "committed": True,
            }
        )


def test_artifact_ref_round_trips_through_json():
    receipt = ArtifactRef(
        name=ArtifactName(value="page-001"),
        uri="file:///tmp/out/page-001.svg",
        byte_count=4096,
        media=ArtifactMedia.SVG,
        committed=True,
    )

    assert ArtifactRef.model_validate_json(receipt.model_dump_json()) == receipt


def test_artifact_ref_refuses_a_media_value_outside_the_three_artifact_kinds():
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate(
            {
                "name": {"value": "page-001"},
                "uri": "file:///tmp/out/page-001.jpg",
                "byte_count": 10,
                "media": "jpeg",
                "committed": True,
            }
        )


# --------------------------------------------------------------------------------------
# the two enums and the module surface
# --------------------------------------------------------------------------------------


def test_image_media_members_are_their_own_wire_values():
    assert ImageMedia.PNG == "png"
    assert ImageMedia("jpeg") is ImageMedia.JPEG
    assert [member.value for member in ImageMedia] == ["png", "jpeg"]


def test_artifact_media_supplies_the_suffix_for_each_artifact_kind():
    assert [member.value for member in ArtifactMedia] == ["svg", "png", "pdf"]
    assert f"page-001.{ArtifactMedia.PDF}" == "page-001.pdf"


def test_every_exported_name_resolves():
    missing = [name for name in export_module.__all__ if not hasattr(export_module, name)]

    assert missing == []
