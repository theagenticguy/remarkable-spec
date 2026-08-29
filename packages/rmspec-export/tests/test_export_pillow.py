"""The one job Pillow is kept for: resampling an encoded PNG to an exact pixel size."""

from __future__ import annotations

import pytest
from export_support import png_header_size, solid_png

from rmspec.domain.ports.export import PixelSize
from rmspec.export._pillow import BACKEND, PillowError, resize_png_exact


def test_the_backend_is_named_for_its_errors() -> None:
    assert BACKEND == "pillow"


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ((200, 300), (200, 299)),
        ((200, 300), (199, 300)),
        ((200, 300), (400, 600)),
        ((200, 300), (1, 1)),
        ((10, 10), (10, 10)),
    ],
)
def test_the_result_declares_exactly_the_requested_size(
    source: tuple[int, int],
    target: tuple[int, int],
) -> None:
    wanted = PixelSize(width_px=target[0], height_px=target[1])
    resized = resize_png_exact(
        solid_png(PixelSize(width_px=source[0], height_px=source[1])),
        target=wanted,
    )
    assert png_header_size(resized) == wanted


def test_the_result_is_a_complete_png() -> None:
    resized = resize_png_exact(
        solid_png(PixelSize(width_px=40, height_px=50)),
        target=PixelSize(width_px=41, height_px=50),
    )
    assert resized.startswith(b"\x89PNG\r\n\x1a\n")
    assert resized.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82")


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\ntruncated"])
def test_undecodable_bytes_become_a_typed_failure(data: bytes) -> None:
    with pytest.raises(PillowError, match="could not resize"):
        resize_png_exact(data, target=PixelSize(width_px=10, height_px=10))
