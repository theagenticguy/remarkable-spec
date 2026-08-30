"""Keyed doubles for the two OCR ports, shipped rather than vendored.

Every later application-layer test binds these, and this is the import path they bind. They live
under ``src/`` on purpose, following :mod:`rmspec.persistence.testing` and
:mod:`rmspec.device.testing`: the architecture suite checks import direction over ``src/`` only,
so an application **test** may depend on ``rmspec.ocr.testing`` while application **source**
stays domain-only. They ship in the wheel and are held to the same coverage gate as the adapters,
which is what keeps a double honest about the contract it claims to satisfy.

This subpackage is imported explicitly and is never re-exported from :mod:`rmspec.ocr`. A double
bound in production would answer every page from an empty dictionary or, worse, from a script
some other command left behind -- and it would report the result as a reading, under a cache key
that gives no hint anything went wrong. Keeping the doubles off the package's ``__all__`` is what
stops a composition root binding one by accident.

There is deliberately no :class:`~rmspec.domain.ports.ocr.HandwrittenTextIndex` double here:
:class:`rmspec.persistence.testing.FakeHandwrittenTextIndex` already is one. See
:mod:`rmspec.ocr.testing.doubles` for why that is the right home rather than this one.
"""

from __future__ import annotations

from rmspec.ocr.testing.doubles import (
    DEFAULT_ENGINE_REVISION,
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER,
    DEFAULT_REVISION,
    FINGERPRINT_TAG,
    ScriptedTextRecognizer,
    ScriptedVisionLanguageModel,
)

__all__ = [
    "DEFAULT_ENGINE_REVISION",
    "DEFAULT_MODEL_ID",
    "DEFAULT_PROVIDER",
    "DEFAULT_REVISION",
    "FINGERPRINT_TAG",
    "ScriptedTextRecognizer",
    "ScriptedVisionLanguageModel",
]
