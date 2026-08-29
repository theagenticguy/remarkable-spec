"""The cairosvg shim: no backend exception and no empty success escapes it."""

from __future__ import annotations

import pytest
from export_support import LEGACY_RM2_BOX_PT, png_header_size, svg_markup

from rmspec.domain.ports.export import PixelSize
from rmspec.export import _cairo

MARKUP = svg_markup(*LEGACY_RM2_BOX_PT)
BROKEN_MARKUP = '<svg xmlns="http://www.w3.org/2000/svg"><line x1="1" y1=</svg>'


def test_the_backend_is_named_for_its_errors() -> None:
    assert _cairo.BACKEND == "cairosvg"


def test_png_is_produced_at_exactly_the_size_asked_for() -> None:
    data = _cairo.render_png(MARKUP, width_px=321, height_px=654)
    assert png_header_size(data) == PixelSize(width_px=321, height_px=654)


def test_png_conversion_failure_is_wrapped() -> None:
    with pytest.raises(_cairo.CairoError, match="svg2png failed"):
        _cairo.render_png(BROKEN_MARKUP, width_px=10, height_px=10)


@pytest.mark.parametrize("returned", [b"", None])
def test_png_returning_nothing_is_a_failure_not_an_empty_file(
    monkeypatch: pytest.MonkeyPatch,
    returned: bytes | None,
) -> None:
    monkeypatch.setattr(_cairo.cairosvg, "svg2png", lambda **_kwargs: returned)
    with pytest.raises(_cairo.CairoError, match="svg2png returned no bytes"):
        _cairo.render_png(MARKUP, width_px=10, height_px=10)


def test_pdf_is_produced_without_a_scale_and_with_one() -> None:
    unscaled = _cairo.render_pdf(MARKUP)
    scaled = _cairo.render_pdf(MARKUP, scale=96 / 72)
    assert unscaled.startswith(b"%PDF-")
    assert scaled.startswith(b"%PDF-")
    assert len(scaled) != len(unscaled) or scaled != unscaled


def test_pdf_conversion_failure_is_wrapped() -> None:
    with pytest.raises(_cairo.CairoError, match="svg2pdf failed"):
        _cairo.render_pdf(BROKEN_MARKUP)


def test_pdf_conversion_failure_with_a_scale_is_wrapped() -> None:
    with pytest.raises(_cairo.CairoError, match="svg2pdf failed"):
        _cairo.render_pdf(BROKEN_MARKUP, scale=2.0)


@pytest.mark.parametrize("returned", [b"", None])
def test_pdf_returning_nothing_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    returned: bytes | None,
) -> None:
    monkeypatch.setattr(_cairo.cairosvg, "svg2pdf", lambda **_kwargs: returned)
    with pytest.raises(_cairo.CairoError, match="svg2pdf returned no bytes"):
        _cairo.render_pdf(MARKUP)


def test_the_error_carries_the_backend_name_in_its_message() -> None:
    error = _cairo.CairoError("detail here")
    assert str(error) == "cairosvg: detail here"
    assert error.detail == "detail here"
