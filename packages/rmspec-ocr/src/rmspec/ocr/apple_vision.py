"""Apple Vision-backed :class:`~rmspec.domain.ports.ocr.TextRecognizer`."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, Self

from rmspec.domain.errors import RecognitionFailed
from rmspec.domain.ports.ocr import Recognition
from rmspec.ocr._confidence import joined_text, mean_character_confidence

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import ModuleType

    from rmspec.domain.ports.ocr import RasterImage
    from rmspec.ocr._confidence import RecognizedLine

__all__ = ["DEFAULT_REVISION", "PROVIDER", "AppleVisionRecognizer", "LineReader"]

PROVIDER = "apple-vision"
"""Engine half of this adapter's provider slug, without the revision."""

DEFAULT_REVISION = 1
"""Current revision of this adapter's reading behaviour.

See :attr:`AppleVisionRecognizer.provider_id` for what bumping it invalidates.
"""

_FRAMEWORK_MODULE = "rmspec.ocr._vision_framework"
"""The macOS-only module :meth:`AppleVisionRecognizer.on_this_machine` binds."""


class LineReader(Protocol):
    """The one call this adapter makes into a text-recognition engine.

    The seam exists so the adapter is driven in a test without ``Vision`` -- which imports
    only on macOS, and only with the ``vision`` extra installed -- and so the entire native
    surface stays inside :mod:`rmspec.ocr._vision_framework`. A double is one function.
    """

    def __call__(self, data: bytes, /) -> Sequence[RecognizedLine]:
        """Read the lines of text in one encoded image.

        Parameters
        ----------
        data
            Encoded image bytes. Never a path: no reader below the app line opens a file.

        Returns
        -------
        Sequence[RecognizedLine]
            One line per recognised region, in reading order, empty for an image with no
            text on it.

        Raises
        ------
        RuntimeError
            The engine could not read the bytes.
            :class:`rmspec.ocr._vision_framework.VisionFrameworkError` is one, and is what
            the shipped reader raises; the base class is named here because this module
            stays importable where that one cannot be imported at all.
        """
        ...


class AppleVisionRecognizer:
    """Recognise handwriting on one rendered page with Apple's on-device Vision framework.

    Satisfies :class:`~rmspec.domain.ports.ocr.TextRecognizer`. Scope ``APP``: the reader is
    a stateless function and the framework handles are created per call, inside
    :mod:`rmspec.ocr._vision_framework`.

    Concurrency
    -----------
    Thread-safe with no lock, and the lock's absence is the measured claim rather than an
    omission: this instance holds one callable and one string, both read-only after
    construction, and every Objective-C handle a recognition needs -- the image source, the
    request, the request handler -- is built inside the call that uses it and is unreachable
    from any other. There is nothing thread-hostile to serialise, so nothing is exported to
    the caller either; the port requires one instance to tolerate concurrent
    :meth:`recognize` calls, and a fan-out test asserts the attribution holds.

    Two legacy defects fixed rather than relocated
    ----------------------------------------------
    The legacy recognizer wrote a temporary PNG and passed Vision a file URL; this one goes
    from :attr:`~rmspec.domain.ports.ocr.RasterImage.data` to a ``CGImage`` in memory. And it
    averaged confidence per line, which a page of one crisp word and a hundred characters of
    scribble reports as 0.5 rather than 0.01 -- see :mod:`rmspec.ocr._confidence`, whose fold
    both recognizers in this package share so the two cannot drift apart again.

    Why there is no "engine unavailable" failure
    --------------------------------------------
    A missing ``pyobjc`` is a composition failure that names the package and its extra --
    :func:`rmspec.ocr.availability.require_backends` -- not something :meth:`recognize`
    reaches from its own ``except``. A deliberately degraded set of engines is likewise a
    visible binding in the composition root. So the only failure this class raises is
    ``RecognitionFailed``.
    """

    def __init__(self, read_lines: LineReader, /, *, revision: int = DEFAULT_REVISION) -> None:
        self._read_lines = read_lines
        self._provider_id = f"{PROVIDER}@{revision}"

    @classmethod
    def on_this_machine(
        cls,
        *,
        revision: int = DEFAULT_REVISION,
        load: Callable[[str], ModuleType] = import_module,
    ) -> Self:
        """Bind the shipped reader, which is the local Vision framework.

        The composition root's one call. It loads
        :mod:`rmspec.ocr._vision_framework` by name rather than importing it at the top of
        this module, because that module resolves only on macOS with the ``vision`` extra
        installed and this one must stay importable everywhere -- including in the process
        that is about to report the extra as missing.

        Parameters
        ----------
        revision
            Revision to fold into :attr:`provider_id`.
        load
            How to load the framework module. Injected so the binding is exercised on a
            machine that has no ``Vision``, exactly as
            :func:`rmspec.export._dyld.fallback_library_path` injects its filesystem probe.

        Returns
        -------
        Self
            A recognizer bound to the local framework.

        Raises
        ------
        ImportError
            The bindings are not installed. Deliberately not converted: the composition root
            calls :func:`rmspec.ocr.availability.require_backends` first, which reports the
            missing package and the extra that provides it, so an ``ImportError`` arriving
            here means that call was skipped -- a wiring bug, not a user's missing extra.
        """
        framework = load(_FRAMEWORK_MODULE)
        return cls(framework.recognize_lines, revision=revision)

    @property
    def provider_id(self) -> str:
        """Return this engine's stable identity slug.

        Returns
        -------
        str
            ``"apple-vision@<revision>"``. The revision is part of the slug because the app
            folds this exact string into its cache key, so bumping it invalidates every row
            produced by the older reading behaviour instead of silently reusing it.
        """
        return self._provider_id

    def recognize(self, image: RasterImage, /) -> Recognition:
        """Recognise the text on one rendered page.

        Parameters
        ----------
        image
            The rendered page, carrying its own bytes and page identity.

        Returns
        -------
        Recognition
            This engine's reading, built with
            :meth:`~rmspec.domain.ports.ocr.Recognition.attributed` so its ``page_ref`` is
            derived from the raster that was read rather than filled by hand -- a recognizer
            that returned another page's slot would have the app cache one page's text under
            another. A blank page is a successful empty reading: ``text=""`` with
            ``mean_confidence=None``, because there is nothing to be confident about.

        Raises
        ------
        RecognitionFailed
            The reader could not produce a reading. Always ``retryable=False``: this engine
            runs on-device with no quota, no endpoint and no clock, so every failure it can
            report -- undecodable bytes, a refused image, a bridge that could not answer --
            gives the same answer again on the same input. Reporting it as retryable would
            spend the caller's retry budget on a certainty.
        """
        try:
            lines = self._read_lines(image.data)
        except RuntimeError as exc:
            raise RecognitionFailed(
                provider_id=self._provider_id,
                detail=str(exc),
                retryable=False,
            ) from exc
        return Recognition.attributed(
            image,
            provider_id=self._provider_id,
            text=joined_text(lines),
            mean_confidence=mean_character_confidence(lines),
        )
