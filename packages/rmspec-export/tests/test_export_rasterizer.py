"""The SVG rasterizer: the domain's pixel count, and every failure as one typed error."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from export_support import (
    LEGACY_RM2_BOX_PT,
    MemoryRasterizer,
    check_rasterizer,
    png_header_size,
    solid_png,
)

from rmspec.domain.errors import RasterizationFailed
from rmspec.domain.ports.export import PhysicalSize, PixelSize, SvgPage
from rmspec.export import _cairo
from rmspec.export.rasterizer import CairoSvgRasterizer

if TYPE_CHECKING:
    from conftest import PageFactory


DPI_VALUES = (72, 150, 300)


def test_the_real_adapter_satisfies_the_contract() -> None:
    check_rasterizer(CairoSvgRasterizer(), dpi_values=DPI_VALUES)


def test_the_in_memory_double_satisfies_the_same_contract() -> None:
    # If a ten-line double cannot pass, the suite above is testing cairosvg rather than the port.
    check_rasterizer(MemoryRasterizer(), dpi_values=DPI_VALUES)


@pytest.mark.parametrize("dpi", DPI_VALUES)
def test_pixel_count_is_the_domain_formula_not_the_backend_scale(
    page: PageFactory, dpi: int
) -> None:
    subject = page("p", LEGACY_RM2_BOX_PT)
    image = CairoSvgRasterizer().to_png(subject, dpi=dpi)
    expected = PixelSize.from_dpi(subject.size, dpi)
    assert png_header_size(image.data) == expected


def test_a_padded_page_is_not_anisotropically_squashed(page: PageFactory) -> None:
    # The legacy renderer's declared box has aspect 0.7729, not the screen's 0.75. Forcing an
    # output size derived from the screen instead of from the markup's own box would squash the
    # raster on one axis while every size assertion still passed.
    subject = page("p", LEGACY_RM2_BOX_PT)
    image = CairoSvgRasterizer().to_png(subject, dpi=150)
    markup_aspect = LEGACY_RM2_BOX_PT[0] / LEGACY_RM2_BOX_PT[1]
    assert image.width / image.height == pytest.approx(markup_aspect, abs=1e-3)


def test_an_ink_free_page_still_rasterizes_to_a_full_size_png(page: PageFactory) -> None:
    subject = page("blank", LEGACY_RM2_BOX_PT, ink=False)
    image = CairoSvgRasterizer().to_png(subject, dpi=72)
    assert (image.width, image.height) == (507, 656)
    assert len(image.data) > 0


def test_malformed_markup_becomes_a_typed_failure_carrying_the_page_reference() -> None:
    broken = SvgPage(
        page_ref="broken-page",
        svg='<svg xmlns="http://www.w3.org/2000/svg"><line x1="1" y1=</svg>',
        size=PhysicalSize(width_mm=100.0, height_mm=200.0),
    )
    with pytest.raises(RasterizationFailed) as caught:
        CairoSvgRasterizer().to_png(broken, dpi=72)
    assert caught.value.backend == "cairosvg"
    assert caught.value.page_ref == "broken-page"


def test_a_backend_returning_no_bytes_is_a_failure_not_an_empty_file(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cairo, "render_png", lambda *_args, **_kwargs: b"")
    with pytest.raises(RasterizationFailed, match="did not match the requested raster"):
        CairoSvgRasterizer().to_png(page("p"), dpi=72)


def test_a_backend_raising_mid_render_becomes_a_typed_failure(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> bytes:
        msg = "surface died"
        raise _cairo.CairoError(msg)

    monkeypatch.setattr(_cairo, "render_png", explode)
    with pytest.raises(RasterizationFailed) as caught:
        CairoSvgRasterizer().to_png(page("p"), dpi=72)
    assert caught.value.detail == "surface died"


def test_a_backend_rounding_its_own_way_is_refused_rather_than_reported(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real PNG, one pixel narrow. Without the model's header check this would return a
    # RasterImage whose declared width was a lie and whose digest keyed an OCR cache row.
    subject = page("p")
    honest = PixelSize.from_dpi(subject.size, 72)
    off_by_one = PixelSize(width_px=honest.width_px - 1, height_px=honest.height_px)
    monkeypatch.setattr(_cairo, "render_png", lambda *_a, **_k: solid_png(off_by_one))
    with pytest.raises(RasterizationFailed, match="disagrees with PNG header"):
        CairoSvgRasterizer().to_png(subject, dpi=72)


def test_a_truncated_stream_is_refused_even_though_its_header_is_right(
    page: PageFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is the export-side twin of the empty-stub distinction: a PNG cut short after IHDR
    # still declares the right dimensions, so only the IEND requirement catches it.
    subject = page("p")
    honest = PixelSize.from_dpi(subject.size, 72)
    truncated = solid_png(honest)[:-12]
    monkeypatch.setattr(_cairo, "render_png", lambda *_a, **_k: truncated)
    with pytest.raises(RasterizationFailed, match="truncated"):
        CairoSvgRasterizer().to_png(subject, dpi=72)


@pytest.mark.parametrize("dpi", [0, -1])
def test_a_non_positive_dpi_is_a_caller_bug_not_an_export_error(
    page: PageFactory, dpi: int
) -> None:
    with pytest.raises(ValueError, match="dpi must be positive"):
        CairoSvgRasterizer().to_png(page("p"), dpi=dpi)


def test_no_import_error_is_reachable_from_the_public_method(page: PageFactory) -> None:
    # Legacy export_png's tail was `try: raise ImportError(...) except ImportError: raise
    # ImportError(...) from None`. Availability is a composition concern now, so no method here
    # may raise it.
    image = CairoSvgRasterizer().to_png(page("p"), dpi=72)
    assert image.render_dpi == 72
