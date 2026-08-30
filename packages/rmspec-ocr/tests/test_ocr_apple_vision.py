"""The Apple Vision recognizer, and the native module it is deliberately decoupled from.

Two halves, and the split is the design rather than a convenience:

The adapter is driven entirely through its injected :class:`~rmspec.ocr.apple_vision.LineReader`,
so every test in the first half runs on any platform. ``rmspec.ocr.apple_vision`` never imports
``rmspec.ocr._vision_framework``, and that module is the only one that imports ``Vision`` and
``Quartz`` -- which resolve on macOS only, and only with the ``vision`` extra installed.

The second half exercises the native module itself, and asks for it through a fixture that
skips when the bindings are absent. So on a Linux runner the framework tests skip and every
adapter test still runs; nothing here imports ``Vision`` at module scope, which would take the
whole file down with it.
"""

from __future__ import annotations

import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from rmspec.domain.errors import RecognitionFailed
from rmspec.domain.ports.ocr import ImageMedia, RasterImage, Recognition, TextRecognizer
from rmspec.ocr._confidence import RecognizedLine
from rmspec.ocr.apple_vision import DEFAULT_REVISION, PROVIDER, AppleVisionRecognizer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_GLYPHS = {
    "H": ("#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "E": ("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"),
    "L": ("#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"),
    "O": (" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
}
_GLYPH_WIDTH = 5
_GLYPH_HEIGHT = 7


class StubFrameworkError(RuntimeError):
    """Stands in for ``VisionFrameworkError``, which is a ``RuntimeError`` for this reason.

    The adapter catches the base class, not the concrete error, because the module that
    defines the concrete one imports ``Vision`` and so cannot be imported here at all.
    """


class StubReader:
    """A line reader that answers from a script and records what it was handed."""

    def __init__(
        self,
        lines: Sequence[RecognizedLine] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.lines = lines
        self.error = error
        self.seen: list[bytes] = []

    def __call__(self, data: bytes, /) -> Sequence[RecognizedLine]:
        """Record the bytes and answer with the scripted lines, or raise."""
        self.seen.append(data)
        if self.error is not None:
            raise self.error
        return self.lines


class EchoingReader:
    """A line reader that reads back the payload after the PNG magic, one page at a time."""

    def __call__(self, data: bytes, /) -> Sequence[RecognizedLine]:
        """Answer with a single line holding the bytes' own payload."""
        return [RecognizedLine(text=data.removeprefix(PNG_MAGIC).decode(), confidence=0.99)]


def raster(page_ref: str = "page-1", payload: bytes = b"pixels") -> RasterImage:
    """Build a raster whose bytes are recognisable in a recorded call."""
    return RasterImage(
        page_ref=page_ref,
        media=ImageMedia.PNG,
        data=PNG_MAGIC + payload,
        width=1620,
        height=2160,
        render_dpi=229,
    )


def candidate(text: str, confidence: float | None) -> object:
    """Build something shaped like a ``VNRecognizedText``."""
    return SimpleNamespace(string=lambda: text, confidence=lambda: confidence)


def candidate_without_confidence(text: str) -> object:
    """Build a candidate from a Vision revision that publishes no confidence at all."""
    return SimpleNamespace(string=lambda: text)


def observation(*candidates: object) -> object:
    """Build something shaped like a ``VNRecognizedTextObservation``."""
    return SimpleNamespace(topCandidates_=lambda _maximum: list(candidates))


def png_chunk(tag: bytes, body: bytes) -> bytes:
    """Frame one PNG chunk, so the fixtures below are built rather than pasted."""
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))


def word_png(word: str, *, scale: int = 10, pad: int = 20) -> bytes:
    """Draw ``word`` in a 5x7 block font on a white background, as an 8-bit greyscale PNG.

    A real image with real text on it, so one test proves the native path end to end rather
    than only proving that the stubs agree with each other.
    """
    columns = len(word) * (_GLYPH_WIDTH + 1) - 1
    ink = [[False] * columns for _ in range(_GLYPH_HEIGHT)]
    for index, letter in enumerate(word):
        rows = _GLYPHS[letter]
        for y in range(_GLYPH_HEIGHT):
            for x in range(_GLYPH_WIDTH):
                ink[y][index * (_GLYPH_WIDTH + 1) + x] = rows[y][x] == "#"
    width = columns * scale + 2 * pad
    height = _GLYPH_HEIGHT * scale + 2 * pad
    scanlines = bytearray()
    for y in range(height):
        row = (y - pad) // scale
        scanlines.append(0)
        for x in range(width):
            column = (x - pad) // scale
            inside = 0 <= row < _GLYPH_HEIGHT and 0 <= column < columns
            scanlines.append(0 if inside and ink[row][column] else 255)
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"".join(
        (
            PNG_MAGIC,
            png_chunk(b"IHDR", header),
            png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 9)),
            png_chunk(b"IEND", b""),
        )
    )


@pytest.fixture
def framework() -> ModuleType:
    """Load the native module, or skip: its two imports resolve on macOS only.

    Only a missing *binding* skips. An import error naming anything else -- a typo in this
    package, a renamed helper -- is re-raised, because a fixture that swallowed those would
    turn every test below into a silent pass on the one platform that can run them.
    """
    try:
        return import_module("rmspec.ocr._vision_framework")
    except ImportError as exc:
        if exc.name in {"Vision", "Quartz"}:
            pytest.skip(f"the pyobjc Vision bindings are macOS-only: {exc}")
        raise


# ── the adapter, through its injected seam ──────────────────────────────────────────────


def test_the_default_slug_is_the_one_the_app_keys_cache_rows_on() -> None:
    assert AppleVisionRecognizer(StubReader()).provider_id == "apple-vision@1"
    assert PROVIDER == "apple-vision"
    assert DEFAULT_REVISION == 1


def test_bumping_the_revision_changes_the_slug_and_so_invalidates_older_rows() -> None:
    default = AppleVisionRecognizer(StubReader()).provider_id
    bumped = AppleVisionRecognizer(StubReader(), revision=3).provider_id
    assert bumped == "apple-vision@3"
    assert bumped != default


def test_the_reader_is_handed_bytes_and_never_a_path() -> None:
    # The legacy recognizer wrote a temporary PNG and passed Vision a file URL, which leaked
    # the file on any failure between create and unlink. There is no filesystem here at all.
    reader = StubReader()
    image = raster()
    AppleVisionRecognizer(reader).recognize(image)
    assert reader.seen == [image.data]


def test_the_lines_are_joined_in_reading_order() -> None:
    reader = StubReader(
        [
            RecognizedLine(text="first", confidence=0.9),
            RecognizedLine(text="second", confidence=0.9),
        ]
    )
    assert AppleVisionRecognizer(reader).recognize(raster()).text == "first\nsecond"


def test_the_mean_is_character_weighted_not_line_weighted() -> None:
    # The same arithmetic as the Textract suite asserts, over the same shape, because both
    # recognizers fold their lines with one shared helper and must not drift apart.
    reader = StubReader(
        [
            RecognizedLine(text="x", confidence=1.0),
            RecognizedLine(text="y" * 99, confidence=0.0),
        ]
    )
    reading = AppleVisionRecognizer(reader).recognize(raster())
    assert reading.mean_confidence == pytest.approx(0.01)


def test_an_engine_that_reports_no_confidence_reports_none_rather_than_a_number() -> None:
    # Vision does not publish a confidence in every configuration. Inventing one would make
    # an unmeasured reading indistinguishable from a measured one.
    reader = StubReader([RecognizedLine(text="unmeasured", confidence=None)])
    reading = AppleVisionRecognizer(reader).recognize(raster())
    assert reading.text == "unmeasured"
    assert reading.mean_confidence is None


def test_unmeasured_lines_contribute_neither_a_weight_nor_a_value() -> None:
    reader = StubReader(
        [
            RecognizedLine(text="ab", confidence=0.5),
            RecognizedLine(text="c" * 98, confidence=None),
        ]
    )
    assert AppleVisionRecognizer(reader).recognize(raster()).mean_confidence == pytest.approx(0.5)


def test_a_blank_page_is_a_successful_empty_reading() -> None:
    reading = AppleVisionRecognizer(StubReader()).recognize(raster())
    assert reading.text == ""
    assert reading.mean_confidence is None
    assert reading.has_text is False


def test_the_reading_is_attributed_to_the_raster_that_was_read() -> None:
    reading = AppleVisionRecognizer(StubReader()).recognize(raster("page-42"))
    assert reading.page_ref == "page-42"
    assert reading.provider_id == "apple-vision@1"


def test_a_reader_failure_becomes_a_permanent_recognition_failure() -> None:
    # On-device with no quota, no endpoint and no clock: every failure this engine can report
    # gives the same answer again on the same bytes, so retrying is a wasted budget.
    error = StubFrameworkError("vision.framework: could not read 8 bytes as an image")
    with pytest.raises(RecognitionFailed) as caught:
        AppleVisionRecognizer(StubReader(error=error)).recognize(raster())
    assert caught.value.retryable is False
    assert caught.value.provider_id == "apple-vision@1"
    assert "could not read 8 bytes" in caught.value.detail
    assert caught.value.__cause__ is error


def test_a_programming_error_in_the_reader_is_not_laundered_into_a_recognition_failure() -> None:
    # The seam catches RuntimeError, not Exception, so a mis-shaped double still fails as the
    # bug it is instead of being reported as an unreadable page.
    reader = StubReader(error=TypeError("string indices must be integers"))
    with pytest.raises(TypeError):
        AppleVisionRecognizer(reader).recognize(raster())


def test_one_instance_tolerates_concurrent_calls_from_several_threads() -> None:
    recognizer = AppleVisionRecognizer(EchoingReader())
    pages = [raster(f"page-{index}", f"page-{index}".encode()) for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        readings = list(pool.map(recognizer.recognize, pages))
    assert [(reading.page_ref, reading.text) for reading in readings] == [
        (page.page_ref, page.page_ref) for page in pages
    ]


def test_on_this_machine_binds_the_loaded_modules_reader() -> None:
    reader = StubReader([RecognizedLine(text="from the module", confidence=0.75)])
    loaded = cast("ModuleType", SimpleNamespace(recognize_lines=reader))
    asked: list[str] = []

    def load(name: str) -> ModuleType:
        asked.append(name)
        return loaded

    recognizer = AppleVisionRecognizer.on_this_machine(revision=2, load=load)
    assert asked == ["rmspec.ocr._vision_framework"]
    assert recognizer.provider_id == "apple-vision@2"
    assert recognizer.recognize(raster()).text == "from the module"


def test_it_satisfies_the_text_recognizer_port() -> None:
    recognizer: TextRecognizer = AppleVisionRecognizer(StubReader())
    assert isinstance(recognizer.recognize(raster()), Recognition)


# ── the native module ───────────────────────────────────────────────────────────────────


def test_the_backend_is_named_for_its_errors(framework: ModuleType) -> None:
    assert framework.BACKEND == "vision.framework"
    error = framework.VisionFrameworkError("something specific")
    assert error.detail == "something specific"
    assert str(error) == "vision.framework: something specific"
    assert isinstance(error, RuntimeError)


def test_the_probe_really_runs_a_recognition(framework: ModuleType) -> None:
    assert framework.probe_backend() == 0


def test_a_blank_image_reads_as_no_lines_at_all(framework: ModuleType) -> None:
    assert framework.recognize_lines(framework._blank_png(32)) == ()


def test_real_text_is_read_out_of_bytes_held_in_memory(framework: ModuleType) -> None:
    # Measured on this machine: one observation, text "HELLO", confidence 1.0.
    lines = framework.recognize_lines(word_png("HELLO"))
    assert [line.text for line in lines] == ["HELLO"]
    assert lines[0].confidence is not None
    assert lines[0].confidence > 0.5


@pytest.mark.parametrize("side", [1, 2])
def test_an_image_the_recogniser_refuses_is_a_typed_failure(
    framework: ModuleType,
    side: int,
) -> None:
    # Measured: Vision refuses anything 2 pixels or smaller in either dimension, which is
    # what makes this branch reachable with a real request rather than only with a stub.
    with pytest.raises(RuntimeError, match="refused the image"):
        framework.recognize_lines(framework._blank_png(side))


@pytest.mark.parametrize("data", [b"", b"not an image", PNG_MAGIC + b"truncated"])
def test_undecodable_bytes_are_a_typed_failure(framework: ModuleType, data: bytes) -> None:
    with pytest.raises(RuntimeError, match="could not read"):
        framework.recognize_lines(data)


def test_a_bridge_failure_is_converted_rather_than_leaked(
    framework: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pyobjc raises ValueError and objc.error, neither of which is a RuntimeError, so without
    # this conversion a bridge surprise would cross the port as a third-party exception.
    def explode(*_args: object) -> object:
        msg = "NSInvalidArgumentException"
        raise ValueError(msg)

    monkeypatch.setattr(
        framework,
        "_QUARTZ",
        SimpleNamespace(CGImageSourceCreateWithData=explode),
    )
    with pytest.raises(RuntimeError, match="the Vision request failed") as caught:
        framework.recognize_lines(framework._blank_png(8))
    assert isinstance(caught.value.__cause__, ValueError)


def test_observations_fold_into_lines_in_order(framework: ModuleType) -> None:
    lines = framework.lines_from_observations(
        [
            observation(candidate("first", 0.9)),
            observation(candidate("second", 0.5)),
        ]
    )
    assert lines == (
        RecognizedLine(text="first", confidence=0.9),
        RecognizedLine(text="second", confidence=0.5),
    )


def test_an_observation_with_no_candidate_is_skipped_not_fatal(framework: ModuleType) -> None:
    # Apple documents the candidate array as possibly empty. One unreadable region is not a
    # broken read of the rest of the page.
    lines = framework.lines_from_observations([observation(), observation(candidate("kept", 0.8))])
    assert lines == (RecognizedLine(text="kept", confidence=0.8),)


@pytest.mark.parametrize(
    "made",
    [candidate("unmeasured", None), candidate_without_confidence("unmeasured")],
)
def test_a_candidate_that_publishes_no_confidence_reports_none(
    framework: ModuleType,
    made: object,
) -> None:
    assert framework.lines_from_observations([observation(made)]) == (
        RecognizedLine(text="unmeasured", confidence=None),
    )


def test_the_adapter_over_the_real_reader_reads_a_page(framework: ModuleType) -> None:
    image = RasterImage(
        page_ref="page-9",
        media=ImageMedia.PNG,
        data=word_png("HELLO"),
        width=110,
        height=110,
        render_dpi=229,
    )
    reading = AppleVisionRecognizer(framework.recognize_lines).recognize(image)
    assert reading.text == "HELLO"
    assert reading.page_ref == "page-9"
    assert reading.mean_confidence is not None


def test_the_real_frameworks_error_is_caught_by_the_adapters_seam(framework: ModuleType) -> None:
    # The one test that proves the cross-module contract: the adapter cannot import
    # VisionFrameworkError, so it catches RuntimeError, and this asserts the real error is one.
    image = RasterImage(
        page_ref="page-9",
        media=ImageMedia.PNG,
        data=PNG_MAGIC + b"truncated",
        width=10,
        height=10,
        render_dpi=229,
    )
    with pytest.raises(RecognitionFailed) as caught:
        AppleVisionRecognizer(framework.recognize_lines).recognize(image)
    assert caught.value.retryable is False
    assert "could not read" in caught.value.detail
    assert isinstance(caught.value.__cause__, framework.VisionFrameworkError)
