"""The one fold from an engine's per-line output onto the two fields of a ``Recognition``.

Both recognizers in this package -- :mod:`rmspec.ocr.textract` and
:mod:`rmspec.ocr.apple_vision` -- finish with the same question: given the lines one engine
read, what are :attr:`~rmspec.domain.ports.ocr.Recognition.text` and
:attr:`~rmspec.domain.ports.ocr.Recognition.mean_confidence`? The answer lives here, once,
because the two legacy engines got it wrong in the same way and a fix applied to one copy
would have left the other averaging per line forever.

Character-weighted, never line-weighted
---------------------------------------
The port specifies the mean as one *per character*, "so one confident word on a page of
noise cannot outvote the noise". Both legacy engines averaged per line, so a page holding
one crisp word at 1.0 and ninety-nine characters of scribble at 0.0 reported 0.5 where the
truth is 0.01. That single number is what a caller uses to decide whether to trust a
transcription, so the difference is not cosmetic.

``None`` is not zero
--------------------
A reading with no characters to be confident about reports ``None``. The port is explicit
that a required float forced such cases to fabricate ``0.0``, which "reads as a garbage
reading", or ``1.0``, which reads as a perfect one. So a blank page is ``None``, and so is
a reading from an engine that reported no confidence signal at all.

Why this module rather than either adapter
------------------------------------------
Putting the fold in one adapter would make the other import it, coupling two engines that
share nothing but this arithmetic -- a Textract-only composition would import the module
that carries the Apple Vision seam, or a Vision-only one would import the module that
imports ``boto3``. A third module both may import is the only placement in which one
implementation serves both and neither drags the other's dependencies along.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["RecognizedLine", "joined_text", "mean_character_confidence"]


class RecognizedLine(NamedTuple):
    """One line of text as one engine read it, with that engine's confidence in it.

    Package-private and deliberately not a pydantic model: it never crosses a port, it is
    built once per line on a hot path, and every field is normalised by the adapter that
    builds it. The value objects that do cross the port are frozen and validated.

    Attributes
    ----------
    text
        The line's text, exactly as the engine reported it. May be empty.
    confidence
        The engine's confidence in this line, already normalised to ``0.0`` -- ``1.0`` by
        the adapter that read it -- Textract divides its ``0`` -- ``100`` by 100, Apple
        Vision reports the range directly. ``None`` when this engine reports no confidence
        signal, which some Vision configurations do not; inventing a number there would
        make an unmeasured line indistinguishable from a measured one.
    """

    text: str
    confidence: float | None


def joined_text(lines: Iterable[RecognizedLine]) -> str:
    """Join recognised lines into the one string a ``Recognition`` carries.

    Parameters
    ----------
    lines
        The lines one engine read, in the order it reported them.

    Returns
    -------
    str
        The line texts separated by newlines, or ``""`` for no lines at all -- which is a
        successful empty reading of a blank page, not a failure.
    """
    return "\n".join(line.text for line in lines)


def mean_character_confidence(lines: Iterable[RecognizedLine]) -> float | None:
    """Return the character-weighted mean confidence over ``lines``, or ``None``.

    Each line contributes its confidence once per character, so a long uncertain line
    outweighs a short certain one. A line whose confidence is ``None`` contributes neither
    a weight nor a value: its characters were never measured, and folding them in at any
    assumed value would report a mean the engine never gave.

    Parameters
    ----------
    lines
        The lines one engine read.

    Returns
    -------
    float | None
        The mean in ``0.0`` -- ``1.0``, or ``None`` when no character carries a
        confidence -- an empty reading, an engine that reports none, or lines that are all
        empty strings. Never ``0.0`` in those cases: the port reads ``0.0`` as a garbage
        reading rather than as an absence.

        Clamped into range, because the bound is a validated field on the port and one
        provider reporting a confidence outside its documented scale must not turn a
        readable page into a validation error two layers up.
    """
    weighted = 0.0
    characters = 0
    for line in lines:
        if line.confidence is None:
            continue
        weight = len(line.text)
        weighted += line.confidence * weight
        characters += weight
    if characters == 0:
        return None
    return min(1.0, max(0.0, weighted / characters))
