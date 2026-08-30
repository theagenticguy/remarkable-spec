"""The only module in this workspace that imports ``Vision`` or ``Quartz``. Bytes in, lines out.

Two public functions, one pure fold, one private exception, and no domain type: the public
adapter translates :class:`VisionFrameworkError` into
:class:`~rmspec.domain.errors.RecognitionFailed`, so no Objective-C object and no ``objc``
exception ever reaches a port. Mirrors :mod:`rmspec.export._cairo` and
:mod:`rmspec.export._pillow`, which do the same job for ``cairosvg`` and Pillow.

Bytes, never a path
-------------------
The legacy recognizer wrote the rendered page to a temporary PNG and handed Vision a
``fileURLWithPath_``. :class:`~rmspec.domain.ports.ocr.RasterImage` carries bytes and no port
below the app line touches a filesystem, so this module goes from bytes to a ``CGImage`` in
memory through ``CGImageSourceCreateWithData``. That also deletes the legacy leak: the
temporary file was created before the request and unlinked after it, so any failure in
between left it on disk.

Why the two framework modules are re-bound as ``Any``
----------------------------------------------------
``pyobjc`` builds a framework module's members at import time from the system bundle, so
``Vision.VNRecognizeTextRequest`` exists at runtime and is invisible to every static
analyser -- ``ty`` reports five ``unresolved-attribute`` errors against the plain spelling.
The two aliases below state that fact once, in the one module allowed to know it, instead of
scattering suppressions across every call. Their absence on an older macOS then surfaces as
an ``AttributeError`` inside :func:`recognize_lines`, which is converted like any other
bridge failure.

Why :class:`VisionFrameworkError` is a ``RuntimeError``
------------------------------------------------------
:mod:`rmspec.ocr.apple_vision` must catch this error to raise the one its port documents,
and it must stay importable on a Linux CI runner where the two imports at the top of this
module cannot resolve at all. It therefore never imports this module, and catches the
documented base class instead: ``RuntimeError`` is narrow enough that a mis-shaped test
double's ``TypeError`` still propagates as the bug it is, which a bare ``except Exception``
at that seam would have swallowed.

The probe image is built, not pasted
------------------------------------
:func:`probe_backend` needs a real image, and a hand-pasted 70-byte PNG literal cannot be
reviewed. :func:`_blank_png` assembles one with the standard library. Its size is not
arbitrary: Vision refuses anything 2 pixels or smaller in either dimension with
"The image is too small in at least one dimension 2 x 2", measured here, so the probe uses 8.
"""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING, Any

import Quartz
import Vision

from rmspec.ocr._confidence import RecognizedLine

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "BACKEND",
    "VisionFrameworkError",
    "lines_from_observations",
    "probe_backend",
    "recognize_lines",
]

BACKEND = "vision.framework"
"""Backend name carried by every error this module's failures become."""

_QUARTZ: Any = Quartz
"""``Quartz``, as the dynamically populated namespace it is. See this module's docstring."""

_VISION: Any = Vision
"""``Vision``, as the dynamically populated namespace it is. See this module's docstring."""

_PROBE_SIDE = 8
"""Side of the probe image, in pixels. Vision refuses 2 or fewer in either dimension."""


class VisionFrameworkError(RuntimeError):
    """A Vision or Core Graphics call failed, or returned nothing usable.

    Private to this package. The adapter catches it and raises the domain error its port
    documents, which is what keeps a third-party exception type off every port. Based on
    :class:`RuntimeError` rather than :class:`Exception` so that
    :mod:`rmspec.ocr.apple_vision` can name the class it catches without importing this
    macOS-only module -- see this module's docstring.

    Attributes
    ----------
    detail
        Human-readable cause, already stringified so no Objective-C object is retained.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"{BACKEND}: {detail}")
        self.detail = detail


def _png_chunk(tag: bytes, body: bytes) -> bytes:
    """Frame one PNG chunk: big-endian length, tag, body, CRC-32 over tag and body.

    Parameters
    ----------
    tag
        The four-byte chunk type.
    body
        The chunk's payload.

    Returns
    -------
    bytes
        The framed chunk.
    """
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))


def _blank_png(side: int) -> bytes:
    """Build a white square PNG of ``side`` pixels, using only the standard library.

    Parameters
    ----------
    side
        Width and height in pixels.

    Returns
    -------
    bytes
        A complete 8-bit greyscale PNG, every pixel white.
    """
    header = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    scanlines = (bytes(1) + bytes([255]) * side) * side
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(scanlines, 9)),
            _png_chunk(b"IEND", b""),
        )
    )


_PROBE_PNG = _blank_png(_PROBE_SIDE)
"""A blank image the recogniser will accept, for :func:`probe_backend`."""


def _candidate_confidence(candidate: object) -> float | None:
    """Read one recognised candidate's confidence, or ``None`` when it reports none.

    Vision does not always publish a confidence: the member is absent on older revisions of
    the text request, and the bridge returns ``None`` where the framework returns nothing.
    Both are reported as ``None`` rather than as a fabricated number, which is what the port
    asks for.

    Parameters
    ----------
    candidate
        A ``VNRecognizedText``, or any object shaped like one.

    Returns
    -------
    float | None
        The confidence in ``0.0`` -- ``1.0`` as Vision already normalises it, or ``None``.
    """
    reported = getattr(candidate, "confidence", None)
    if reported is None:
        return None
    value = reported()
    return None if value is None else float(value)


def lines_from_observations(observations: Iterable[Any]) -> tuple[RecognizedLine, ...]:
    """Fold Vision's observations into this package's line values.

    Pure: it calls no framework function and holds no handle, so it is exercised directly
    with objects shaped like observations rather than only through a real recognition.

    Parameters
    ----------
    observations
        ``VNRecognizedTextObservation`` values, in the order Vision reported them.

    Returns
    -------
    tuple[RecognizedLine, ...]
        One line per observation that offered a candidate. An observation with no candidate
        is skipped rather than failing the page: Apple documents the array as possibly
        empty, and one unreadable region is not a broken read of the rest.
    """
    lines: list[RecognizedLine] = []
    for observation in observations:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        best = candidates[0]
        lines.append(
            RecognizedLine(text=str(best.string()), confidence=_candidate_confidence(best))
        )
    return tuple(lines)


def _attempt(data: bytes) -> tuple[tuple[Any, ...], str | None]:
    """Run one accurate text recognition, reporting a refusal as data rather than raising.

    Every ``raise`` this module performs is outside a ``try``, which is what lets
    :func:`recognize_lines` convert an unexpected bridge failure in one place without also
    catching this module's own diagnosis and wrapping it twice.

    Parameters
    ----------
    data
        Encoded image bytes.

    Returns
    -------
    tuple[tuple[Any, ...], str | None]
        The observations and ``None``, or an empty tuple and the reason the recogniser
        produced nothing.
    """
    source = _QUARTZ.CGImageSourceCreateWithData(data, None)
    image = _QUARTZ.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        return (), f"could not read {len(data)} bytes as an image"
    request = _VISION.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(_VISION.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    handler = _VISION.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    performed, error = handler.performRequests_error_([request], None)
    if not performed:
        return (), f"the recogniser refused the image: {error}"
    return tuple(request.results()), None


def recognize_lines(data: bytes) -> tuple[RecognizedLine, ...]:
    """Recognise every line of text in one encoded image, in memory.

    Parameters
    ----------
    data
        Encoded image bytes -- PNG or JPEG, whatever ``ImageIO`` can decode. Never a path.

    Returns
    -------
    tuple[RecognizedLine, ...]
        One line per recognised region, empty for an image with no text on it. A blank page
        is a successful empty reading, which is what the port requires.

    Raises
    ------
    VisionFrameworkError
        The bytes could not be decoded, the recogniser refused the image, or the bridge
        failed. Zero lines is a success, never an error.
    """
    try:
        observations, refusal = _attempt(data)
    except Exception as exc:
        msg = f"the Vision request failed: {exc}"
        raise VisionFrameworkError(msg) from exc
    if refusal is not None:
        raise VisionFrameworkError(refusal)
    return lines_from_observations(observations)


def probe_backend() -> int:
    """Prove the recogniser can actually run, by reading a blank image.

    The Vision analogue of :func:`rmspec.export._pymupdf.probe_backend`: importing the
    bindings proves the wheel is installed, and only a real request proves the framework
    bundle loaded and its model is present. Called once, by
    :func:`rmspec.ocr.availability.require_backends`.

    Returns
    -------
    int
        The number of lines read from the probe image, which is ``0``. A blank image is used
        so a non-zero answer means the recogniser is reporting text that is not there.

    Raises
    ------
    VisionFrameworkError
        The recogniser could not run at all.
    """
    return len(recognize_lines(_PROBE_PNG))
