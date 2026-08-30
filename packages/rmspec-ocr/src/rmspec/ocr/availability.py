"""The composition root's health check for this package's optional recognition backend.

Availability is not a port error. No method on any OCR port raises "adapter unavailable" or
"dependency missing", because a use case writing ``except OcrError`` must not be able to
swallow a wiring bug -- so :class:`~rmspec.domain.errors.MissingDependencyError` is a direct
child of the root error, and this function is where it comes from. It replaces the legacy
tree's function-local ``ImportError`` raises, which reported a missing package from inside a
command body after the render had already been paid for.

It is called once, by the container's eager resolution pass, and never by an adapter method.

How this differs from :mod:`rmspec.export.availability`
------------------------------------------------------
Export's backends are *hard* dependencies of that distribution: they are present by
construction in a synced environment, so the only question that module can ask is whether
they work, and a genuinely absent one fails while ``rmspec.export`` is being imported. Here
the two situations are split:

- ``boto3`` is a hard dependency of ``rmspec-ocr``. Absent is not a legitimate state for it,
  and it has no extra to name, so it gets no probe. There is nothing this module could
  truthfully say about it: :class:`~rmspec.domain.errors.MissingDependencyError`'s whole
  vocabulary is "install this extra", and an environment missing a hard dependency is broken
  in a way ``uv sync --extra ...`` does not describe.
- ``Vision`` and ``Quartz`` come from the ``vision`` extra, which is additionally gated on
  ``sys_platform == 'darwin'`` -- without that marker ``uv sync --all-extras`` fails outright
  on a Linux runner. Absent *is* a legitimate state, on every non-macOS machine and on any
  macOS machine that synced without the extra, and this is the one place that says so.

Hence the ``engines`` parameter. A composition that binds only Textract must not be told to
install ``pyobjc``, and a composition that binds both must be told before it reads its first
page rather than after. The check is over the subset the container is actually building.

What the probe proves
---------------------
Importing the bindings proves a wheel is installed. Only a request proves the framework
bundle loaded and its model is present, which is why
:func:`rmspec.ocr._vision_framework.probe_backend` reads a real blank image -- the same
reasoning that has :mod:`rmspec.export.availability` rasterize a 1x1 SVG rather than trust
that ``import cairosvg`` succeeded.

The framework module is loaded by name, not imported at the top of this file, and that is the
whole point: a module that raised ``ModuleNotFoundError`` on import could not report a missing
dependency, it would only be one.
"""

from __future__ import annotations

from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING

from rmspec.domain.errors import MissingDependencyError

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from types import ModuleType

__all__ = ["EXTRA", "FEATURE", "OcrEngine", "require_backends"]

EXTRA = "vision"
"""The optional dependency group that provides this package's native backend."""

FEATURE = "Apple Vision handwriting recognition"
"""What the user was trying to do, for the message."""

_FRAMEWORK_MODULE = "rmspec.ocr._vision_framework"
"""The only module that imports the bindings, and so the only one whose import can fail."""

_DEFAULT_PACKAGE = "Vision"
"""Named when a failure does not say which of the two bindings was missing."""


class OcrEngine(StrEnum):
    """Which recognizers a composition is binding.

    Not a domain type and not a closed set of *engines*: adding Tesseract stays an adapter
    plus a container edit, and an engine that lives in another distribution brings its own
    availability module with it. This enum is the vocabulary of one question -- which of the
    recognizers *this* distribution ships are being composed -- and its values are the engine
    halves of their provider slugs, so ``OcrEngine.APPLE_VISION`` reads back in
    :attr:`rmspec.ocr.apple_vision.AppleVisionRecognizer.provider_id`.
    """

    APPLE_VISION = "apple-vision"
    AWS_BDA = "aws-bda"
    AWS_TEXTRACT = "aws-textract"


def require_backends(
    engines: Collection[OcrEngine],
    /,
    *,
    load: Callable[[str], ModuleType] = import_module,
) -> None:
    """Prove that every engine in ``engines`` can do its work, or name the failing package.

    Parameters
    ----------
    engines
        The recognizers the container is about to build. Only these are checked: a
        Textract-only composition is not told to install ``pyobjc``, and an empty collection
        checks nothing. Two of the three members reach no branch below and that is not an
        oversight -- ``boto3`` is a required dependency of this distribution rather than an
        extra, so there is no install step to tell a Textract or Data Automation user about.
        What *can* be missing for those two is configuration, and a setting the composition
        root owns is not something this function could name.
    load
        How to load the backend module. Injected so both outcomes are exercised on either
        platform, the way :func:`rmspec.export._dyld.fallback_library_path` injects its
        filesystem probe; the default is the real import.

    Raises
    ------
    MissingDependencyError
        A backend is absent, or imported and is unusable, naming the import package and the
        extra that provides it. Deliberately the same error for both, because the user's next
        action -- install the extra -- is the same.
    """
    if OcrEngine.APPLE_VISION in engines:
        _require_vision_framework(load)


def _require_vision_framework(load: Callable[[str], ModuleType]) -> None:
    """Load the Vision bindings and make them read one image.

    Parameters
    ----------
    load
        How to load the backend module.

    Raises
    ------
    MissingDependencyError
        The bindings are not installed, or could not read the probe image.
    """
    try:
        framework = load(_FRAMEWORK_MODULE)
    except ImportError as exc:
        # ``name`` is the module that could not be found -- "Vision" or "Quartz" -- so the
        # message names the binding the user is actually missing rather than a guess.
        raise MissingDependencyError(
            package=exc.name or _DEFAULT_PACKAGE,
            extra=EXTRA,
            feature=FEATURE,
        ) from exc
    try:
        framework.probe_backend()
    except RuntimeError as exc:
        # VisionFrameworkError is a RuntimeError precisely so this module can catch it
        # without importing the macOS-only module that defines it.
        raise MissingDependencyError(
            package=_DEFAULT_PACKAGE,
            extra=EXTRA,
            feature=FEATURE,
        ) from exc
