"""The only module in this workspace that imports Pillow, and its one honest job.

Pillow was declared in the legacy distribution and imported nowhere. The legacy PNG
exporter's docstring promised "CairoSVG (preferred) or Pillow as a fallback" and its
fallback branch was ``try: raise ImportError(...) except ImportError: raise ImportError(...)
from None`` -- the same message twice, no Pillow anywhere. That fallback is deleted rather
than relocated, because Pillow cannot rasterize SVG and never could.

What Pillow is kept for is the one operation the export ports genuinely require and no other
installed library performs: resampling an already-encoded PNG to an exact pixel size.
:meth:`PixelSize.fit_within` rounds and MuPDF's pixmap sizing ceils, and the port demands
the domain's figure exactly. :func:`rmspec.export._pymupdf.rasterize` removes the
disagreement at its source by deriving a per-axis zoom from the target, which agreed with
the domain in every combination measured -- so this function is the residual guard for a
page geometry measurement has not reached. It is exercised by a test that forces the
mismatch, because an untested correction path is a correction that fails the first time a
real page needs it.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from rmspec.domain.ports.export import PixelSize

__all__ = ["BACKEND", "PillowError", "resize_png_exact"]

BACKEND = "pillow"
"""Backend name carried by every error this module's failures become."""


class PillowError(Exception):
    """A Pillow decode, resize or re-encode failed.

    Private to this package. Adapters catch it and raise the domain error their port
    documents.

    Attributes
    ----------
    detail
        Human-readable cause, already stringified so no Pillow object is retained.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"{BACKEND}: {detail}")
        self.detail = detail


def resize_png_exact(data: bytes, *, target: PixelSize) -> bytes:
    """Resample PNG ``data`` to exactly ``target`` pixels and re-encode it as PNG.

    Parameters
    ----------
    data
        Encoded PNG bytes.
    target
        The pixel size the result must declare.

    Returns
    -------
    bytes
        PNG bytes whose ``IHDR`` declares exactly ``target``.

    Raises
    ------
    PillowError
        The bytes could not be decoded, resampled or re-encoded.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            resized = image.resize(
                (target.width_px, target.height_px),
                resample=Image.Resampling.LANCZOS,
            )
            buffer = io.BytesIO()
            resized.save(buffer, format="PNG")
    except Exception as exc:
        msg = f"could not resize to {target.width_px}x{target.height_px}: {exc}"
        raise PillowError(msg) from exc
    return buffer.getvalue()
