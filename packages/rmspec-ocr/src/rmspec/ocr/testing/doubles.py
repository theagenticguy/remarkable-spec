"""Keyed doubles for the two OCR ports, shipped rather than vendored.

Test doubles, not second product adapters. They live under ``src/`` for the reason
:mod:`rmspec.persistence.testing.doubles` gives and this package inherits: the architecture
suite scans ``src/`` for import direction, so an application **test** may bind
``rmspec.ocr.testing`` while application **source** stays domain-only. They ship in the wheel
and are held to the same coverage gate as the adapters, and
``packages/rmspec-ocr/tests/ocr_contracts.py`` runs one assertion set against every double
*and* every real adapter -- which is what keeps a double from quietly narrowing or widening the
contract it claims to satisfy.

They import ``rmspec.domain`` and the standard library. Nothing here opens a socket, builds a
``bedrock-runtime`` or ``textract`` client, or touches ``Vision``. Importing this subpackage does
run the parent package's ``__init__``, which binds the real adapters and therefore ``boto3`` --
that is a fact about Python packages, not a dependency of these doubles.

Keyed by the input, never by call order
--------------------------------------
Both doubles are dictionaries, and neither is a queue. That is the port docstrings' requirement
rather than a preference:

:class:`ScriptedVisionLanguageModel`
    Keys its replies by the :class:`~rmspec.domain.ports.ocr.VisionRequest` itself.
    ``VisionRequest`` is frozen and hashable *precisely* so this is possible -- and a FIFO fake
    "would happily return another page's text", because the app owns fan-out and per-page cache
    hits, so calls are neither ordered nor one-to-one. There is no ``script.pop(0)`` here and
    there must never be one.
:class:`ScriptedTextRecognizer`
    Keys its readings by :attr:`~rmspec.domain.ports.ocr.RasterImage.page_ref`, which the port
    says is the whole reason that field exists: "to make a test double a dictionary lookup
    instead of ``script.pop(0)``".

An input nothing was scripted for is a :exc:`KeyError` naming what *was* scripted. It is not a
default answer and not the nearest match: a double that invented a reading would let a use-case
test pass while asserting text no engine produced, which is the failure these doubles exist to
make impossible.

Identity is derived, so the stale-cache defect stays falsifiable
---------------------------------------------------------------
``ports/ocr.py`` records "a ``fingerprint`` member is a constant in every double, leaving the
stale-cache defect unfalsifiable" as a *fatal* criticism of an earlier design. So neither double
declares its identity:

* :attr:`ScriptedVisionLanguageModel.fingerprint` is a SHA-256 over a tag, ``model_id`` and
  ``revision``, framed as canonical JSON exactly as
  :func:`rmspec.ocr.vision_model._fingerprint_of` frames its own components -- so a separator
  inside a caller-supplied string cannot make two bindings share one fingerprint.
* :attr:`ScriptedTextRecognizer.provider_id` is ``f"{provider}@{revision}"``, the adapters' own
  slug format, so a test that folds the slug into a cache key exercises the real shape.

The contract suite's "two instances differing in one identity argument have different
fingerprints" assertion therefore runs against the doubles as well as the adapters, and no
constant can pass it. The mirror of that rule matters too: the *script* is deliberately not
folded in, because it is these doubles' collaborator -- the analogue of the injected client the
real Bedrock binding excludes from its own fingerprint, since two clients built for the same
profile and region are the same binding and hashing object identity would make every row either
one wrote unreachable by the other.

Every returned value is built by the domain
-------------------------------------------
:meth:`~rmspec.domain.ports.ocr.VisionCompletion.answering` and
:meth:`~rmspec.domain.ports.ocr.Recognition.attributed` fill every echoed field. No double here
passes ``request_digest``, ``model_fingerprint`` or ``page_ref`` by hand, for the same reason no
adapter is asked to: an echo filled by hand can be filled wrongly exactly once and then poison
every cache row keyed on it. That also means a double cannot attribute a reading to a page it
was not given, which is the anti-misattribution property the contract asserts for adapters and
doubles alike.

Failure on demand, for both ports
---------------------------------
:meth:`ScriptedVisionLanguageModel.fail` takes any of the five
:class:`~rmspec.domain.errors.ModelError` children and
:meth:`ScriptedTextRecognizer.fail` takes a
:class:`~rmspec.domain.errors.RecognitionFailed`, so a use case's partial-failure and
retry-once branches are reachable in an application-layer test with no AWS account, no network
and no billable call. Availability is deliberately *not* one of the seams: a missing optional
package is :class:`~rmspec.domain.errors.MissingDependencyError` raised once by
:func:`rmspec.ocr.availability.require_backends`, never something a port method reaches from its
own ``except``, so a double that could raise it would be modelling a state the port forbids.

There is no ``HandwrittenTextIndex`` double here
-----------------------------------------------
:class:`rmspec.persistence.testing.FakeHandwrittenTextIndex` already is one, built in this same
step, and it answers the three states that port distinguishes -- a reading, an indexed page that
held nothing, and no row at all -- with the reader's own failure seams. A second one here would
be a second thing to keep in step with :class:`rmspec.persistence.search_index.DeviceSearchIndex`
for no gain, and ``rmspec-ocr`` may not import ``rmspec.persistence`` to reuse it anyway. The
port lives in ``ports/ocr.py`` because that is where the tiering decision is documented; the
implementation and its double live where ``sqlite3`` is allowed.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final, NamedTuple

from rmspec.domain.errors import ModelError, RecognitionFailed
from rmspec.domain.ports.ocr import Recognition, StopReason, VisionCompletion

if TYPE_CHECKING:
    from rmspec.domain.ports.ocr import RasterImage, TokenUsage, VisionRequest

__all__ = [
    "DEFAULT_ENGINE_REVISION",
    "DEFAULT_MODEL_ID",
    "DEFAULT_PROVIDER",
    "DEFAULT_REVISION",
    "FINGERPRINT_TAG",
    "ScriptedTextRecognizer",
    "ScriptedVisionLanguageModel",
]

FINGERPRINT_TAG: Final = "rmspec.ocr.testing.scripted-model.fingerprint.v1"
"""Folded in first, so no double's fingerprint can collide with a real binding's.

The real Bedrock binding tags its own digest with
:data:`rmspec.ocr.vision_model.FINGERPRINT_TAG`. Two different tags is what makes a cache row
written under a double unreachable by the adapter and vice versa, which is the honest answer:
they are not the same binding and never answer the same way.
"""

DEFAULT_MODEL_ID: Final = "scripted-vision-model"
"""Model identity a double claims when the caller states none.

Not a real profile id, and deliberately not shaped like one: a double that defaulted to
``global.openai.gpt-5.6-luna`` would let a log line or a failure message read as though a paid
model had answered.
"""

DEFAULT_REVISION: Final = "1"
"""Revision of :class:`ScriptedVisionLanguageModel`'s scripted behaviour.

A ``str`` because :data:`rmspec.ocr.vision_model.ADAPTER_REVISION` is one, so a test that
varies the revision on either binding writes the same literal.
"""

DEFAULT_PROVIDER: Final = "scripted-ocr"
"""Engine half of :class:`ScriptedTextRecognizer`'s provider slug, without the revision.

Not ``apple-vision`` or ``aws-textract``: those slugs are what the app writes into a cache key,
and a double that answered to one of them by default could have its readings reused by a run
that bound the real engine.
"""

DEFAULT_ENGINE_REVISION: Final = 1
"""Revision of :class:`ScriptedTextRecognizer`'s reading behaviour.

An ``int`` because :data:`rmspec.ocr.textract.DEFAULT_REVISION` and
:data:`rmspec.ocr.apple_vision.DEFAULT_REVISION` are ints.
"""

_EMPTY_MODEL_ARGUMENT = "must not be empty: it is a component of this double's fingerprint"
_EMPTY_ENGINE_ARGUMENT = "must not be empty: it is a component of this double's provider slug"
_CONFIDENT_ABOUT_NOTHING = (
    "a reading with no characters must report mean_confidence=None: the port reads 0.0 as a "
    "garbage reading and 1.0 as a perfect one, and both recognizers get None from the shared "
    "character-weighted fold, so a double that could script a number here would let a test "
    "assert a reading no engine can produce"
)


def _has_characters(text: str, /) -> bool:
    r"""Report whether a reading has any character an engine could be confident about.

    Line separators do not count, which is what makes this an exact mirror of
    :func:`rmspec.ocr._confidence.mean_character_confidence` rather than an approximate one: that
    fold weights each line's confidence by ``len(line.text)`` and never sees the separators
    :func:`rmspec.ocr._confidence.joined_text` puts between them. Two blank lines therefore read as
    ``text="\n"`` with ``mean_confidence=None`` -- a non-empty string with nothing measured in it,
    which a plain ``if not text`` check would have let a caller script a number for.

    Parameters
    ----------
    text
        The reading being scripted.

    Returns
    -------
    bool
        ``True`` when at least one character came from a line rather than from a join.
    """
    return bool(text.replace("\n", ""))


class _ScriptedReply(NamedTuple):
    """One scripted model answer, before the domain turns it into a completion.

    A :class:`~typing.NamedTuple` and not a pydantic model for the reason
    :class:`rmspec.ocr._confidence.RecognizedLine` is one: it never crosses a port, and the
    value that does -- :class:`~rmspec.domain.ports.ocr.VisionCompletion` -- is frozen and
    validated.

    Attributes
    ----------
    text
        The answer, possibly empty. Emptiness is data, exactly as it is for a real binding.
    stop_reason
        Why generation stopped. Truncation and refusal live here rather than in
        :meth:`ScriptedVisionLanguageModel.fail`, because the port says they are data.
    reasoning
        Latent reasoning text, or ``None``. The shipped Bedrock binding always reports ``None``
        -- its envelope publishes reasoning as a token count and never as text -- so a use case
        that reads this field needs a double to reach the other branch at all.
    usage
        Token accounting, or ``None`` when nothing is reported.
    """

    text: str
    stop_reason: StopReason
    reasoning: str | None
    usage: TokenUsage | None


class _ScriptedReading(NamedTuple):
    """One scripted engine reading, before the domain attributes it to a page.

    Attributes
    ----------
    text
        Recognised text, possibly empty. Empty means the engine read the page and found
        nothing, which is a success.
    mean_confidence
        Character-weighted mean confidence, or ``None`` when there is nothing to be confident
        about or the engine reports no signal.
    """

    text: str
    mean_confidence: float | None


def _fingerprint_of(*, model_id: str, revision: str) -> str:
    """Fold this double's identity into one opaque digest.

    Framed as a canonical JSON array rather than joined on a separator, for the reason
    :func:`rmspec.ocr.vision_model._fingerprint_of` gives: both components are open strings a
    caller supplies, so a separator-joined stream is ambiguous the moment one of them contains
    the separator, and ``model_id="a|b", revision="c"`` would be one fingerprint for two
    bindings.

    Parameters
    ----------
    model_id
        The identity this double claims.
    revision
        This double's revision.

    Returns
    -------
    str
        Lowercase hex SHA-256. Differs whenever either component differs, which is what the
        contract suite asserts by varying one constructor argument at a time.
    """
    canonical = json.dumps([FINGERPRINT_TAG, model_id, revision], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class ScriptedVisionLanguageModel:
    """A dictionary of replies keyed by request, standing in for a multimodal model.

    The :class:`~rmspec.domain.ports.ocr.VisionLanguageModel` double. Scope ``APP``, like every
    binding of that port: one instance per process, every call stateless.

    Parameters
    ----------
    model_id
        The identity this double claims, folded into :attr:`fingerprint`.
    revision
        This double's revision, folded into :attr:`fingerprint`. Vary it to prove a caller's
        cache key moves when the binding does.

    Raises
    ------
    ValueError
        ``model_id`` or ``revision`` is empty. Refused for the same reason
        :class:`~rmspec.ocr.vision_model.BedrockOpenAiVisionModel` refuses it: an empty
        component still produces a perfectly valid-looking fingerprint, so the one thing a
        reader could not tell from the digest is that the binding is broken.

    Notes
    -----
    Concurrency-tolerant with no lock, which the port requires of every implementation. The two
    dictionaries are only read after construction; the only mutation on the call path is
    ``list.append`` onto :attr:`requests`, and the call counts are derived from that list's
    length rather than kept as a separate counter -- an ``int`` incremented with ``+=`` loses
    increments under a fan-out, which would make the counter lie about the very concurrency the
    port mandates.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        revision: str = DEFAULT_REVISION,
    ) -> None:
        for name, value in (("model_id", model_id), ("revision", revision)):
            if not value:
                msg = f"{name} {_EMPTY_MODEL_ARGUMENT}"
                raise ValueError(msg)

        self.requests: list[VisionRequest] = []
        """Every request :meth:`complete` was entered with, in arrival order, faults included.

        The evidence a caller consulted the cache before paying, which a total port hides: a
        memoised answer and a fresh one are the same value.
        """

        self._model_id = model_id
        self._script: dict[VisionRequest, _ScriptedReply | ModelError] = {}
        self._fingerprint = _fingerprint_of(model_id=model_id, revision=revision)

    @property
    def complete_calls(self) -> int:
        """Return how many times :meth:`complete` was entered, faults included.

        Returns
        -------
        int
            The length of :attr:`requests`, so the count cannot disagree with the record.
        """
        return len(self.requests)

    @property
    def model_id(self) -> str:
        """Return the identity this double claims.

        Not a port member -- :class:`~rmspec.domain.ports.ocr.VisionLanguageModel` publishes no
        ``model_id``, because an app that read one for a cache key would have imported a
        provider's deployment vocabulary by another name. Exposed here so a test can name the
        double in a message without re-deriving what it passed.

        Returns
        -------
        str
            The ``model_id`` this instance was built with.
        """
        return self._model_id

    @property
    def fingerprint(self) -> str:
        """Return the opaque identity of this binding.

        Returns
        -------
        str
            Lowercase hex SHA-256 over :data:`FINGERPRINT_TAG`, ``model_id`` and ``revision``,
            computed once in the constructor -- so it is stable for the lifetime of the process
            and known before the first call, both of which the port requires. Derived rather
            than declared: a constant here is the one implementation that makes every stale
            cache row look fresh.
        """
        return self._fingerprint

    def answer(
        self,
        request: VisionRequest,
        /,
        *,
        text: str,
        stop_reason: StopReason = StopReason.COMPLETE,
        reasoning: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        """Script the answer to one request, replacing anything scripted for it before.

        Parameters
        ----------
        request
            The request this answer belongs to. It is the dictionary key, so an equal-valued
            request built anywhere else finds this reply and a different one finds nothing.
        text
            The answer. ``""`` is a legitimate scripting: an empty completion is data.
        stop_reason
            Why generation stopped. Defaulted to
            :attr:`~rmspec.domain.ports.ocr.StopReason.COMPLETE` because that is the only value
            a caller who does not care about truncation should have to think about; every other
            member is reachable, including the two a real wire cannot report.
        reasoning
            Latent reasoning text, when a caller needs the branch that has some.
        usage
            Token accounting, when a caller needs one.
        """
        self._script[request] = _ScriptedReply(
            text=text,
            stop_reason=stop_reason,
            reasoning=reasoning,
            usage=usage,
        )

    def fail(self, request: VisionRequest, error: ModelError, /) -> None:
        """Script a provider failure for one request, replacing anything scripted for it before.

        Parameters
        ----------
        request
            The request that will fail.
        error
            The failure to raise. Any of the five
            :class:`~rmspec.domain.errors.ModelError` children the port's ``Raises`` clause
            names -- the caller chooses, because the CLI's retry-once branch reads
            ``retryable`` and the report-and-stop branch reads ``remediation``, and a double
            that could only raise one of them would leave the other branch unreachable.
        """
        self._script[request] = error

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Answer one request from the script, or raise what was scripted for it.

        Parameters
        ----------
        request
            The request to answer. Looked up by value, never by position.

        Returns
        -------
        VisionCompletion
            The scripted answer, built with
            :meth:`~rmspec.domain.ports.ocr.VisionCompletion.answering` so ``request_digest``
            and ``model_fingerprint`` are derived from ``request`` and :attr:`fingerprint`
            rather than copied.

        Raises
        ------
        ModelError
            Whichever child :meth:`fail` was given for this request.
        KeyError
            Nothing was scripted for this request. Deliberately not a default answer: a double
            that invented one would let a test assert text no model produced, and deliberately
            not the nearest scripted request either, which is the ``script.pop(0)`` defect
            wearing a dictionary's clothes.
        """
        self.requests.append(request)
        scripted = self._script.get(request)
        if scripted is None:
            msg = (
                f"nothing is scripted for a request digesting to {request.digest()}; "
                f"{len(self._script)} request(s) are scripted on this double"
            )
            raise KeyError(msg)
        if isinstance(scripted, ModelError):
            raise scripted
        return VisionCompletion.answering(
            request,
            fingerprint=self._fingerprint,
            text=scripted.text,
            stop_reason=scripted.stop_reason,
            reasoning=scripted.reasoning,
            usage=scripted.usage,
        )


class ScriptedTextRecognizer:
    """A dictionary of readings keyed by page, standing in for one OCR engine.

    The :class:`~rmspec.domain.ports.ocr.TextRecognizer` double. Scope ``APP``: a real engine's
    handle is a stateless process-lifetime resource and so is this.

    Parameters
    ----------
    provider
        Engine half of :attr:`provider_id`. A constructor argument rather than a constant so one
        double can stand in for two engines at once -- which is what a test of the app's
        "fold the *surviving* engines in **sorted** order" cache-key rule needs, and what a
        hardcoded slug would make impossible without a second class.
    revision
        Reading-behaviour revision, the other half of :attr:`provider_id`.

    Raises
    ------
    ValueError
        ``provider`` is empty. ``Recognition.provider_id`` merely requires one character, so
        ``"@1"`` would validate and then travel into a cache key as a slug naming no engine.

    Notes
    -----
    Concurrency-tolerant with no lock, for the same reason
    :class:`ScriptedVisionLanguageModel` is: the script is read-only after construction and the
    only mutation on the call path is ``list.append`` onto :attr:`pages_read`. The port mandates
    this because the app fans recognizers out, and the contract suite asserts it by reading eight
    pages at once and checking that every reading names the page it came from.
    """

    def __init__(
        self,
        *,
        provider: str = DEFAULT_PROVIDER,
        revision: int = DEFAULT_ENGINE_REVISION,
    ) -> None:
        if not provider:
            msg = f"provider {_EMPTY_ENGINE_ARGUMENT}"
            raise ValueError(msg)

        self.pages_read: list[str] = []
        """Every ``page_ref`` :meth:`recognize` was entered with, in arrival order.

        Faults included, so a caller can prove a failing engine was consulted rather than
        skipped -- which through the port is indistinguishable, since the app tolerates partial
        failure and carries on.
        """

        self._provider_id = f"{provider}@{revision}"
        self._script: dict[str, _ScriptedReading | RecognitionFailed] = {}

    @property
    def recognize_calls(self) -> int:
        """Return how many times :meth:`recognize` was entered, faults included.

        Returns
        -------
        int
            The length of :attr:`pages_read`, so the count cannot disagree with the record.
        """
        return len(self.pages_read)

    @property
    def provider_id(self) -> str:
        """Return this engine's stable identity slug.

        Returns
        -------
        str
            ``"<provider>@<revision>"``, the shipped adapters' own format, so a test that folds
            this string into a cache key exercises the real shape. The revision is inside the
            slug because that is what makes bumping it invalidate the rows the older behaviour
            wrote.
        """
        return self._provider_id

    def read(
        self,
        page_ref: str,
        /,
        *,
        text: str,
        mean_confidence: float | None = None,
    ) -> None:
        """Script the reading of one page, replacing anything scripted for it before.

        Parameters
        ----------
        page_ref
            The page this reading belongs to, matched against
            :attr:`~rmspec.domain.ports.ocr.RasterImage.page_ref`. It is the dictionary key, so
            two pages with identical pixels are still two lookups -- which is the situation
            :meth:`~rmspec.domain.ports.ocr.RasterImage.digest` deliberately creates by
            excluding ``page_ref``.
        text
            Recognised text. ``""`` is a legitimate scripting and is not the same as leaving the
            page unscripted: it means the engine read the page and found nothing.
        mean_confidence
            Character-weighted mean confidence in ``0.0`` -- ``1.0``, or ``None`` when the engine
            reports no signal.

        Raises
        ------
        ValueError
            ``text`` carries no character outside a line separator and ``mean_confidence`` is not
            ``None``. Both real recognizers get ``None`` there from
            :func:`rmspec.ocr._confidence.mean_character_confidence`, which counts characters and
            not lines, so a double that accepted a number would be the only reader in the workspace
            able to produce a confident blank page. See :func:`_has_characters` for why the check
            is not simply ``if not text``.
        """
        if not _has_characters(text) and mean_confidence is not None:
            raise ValueError(_CONFIDENT_ABOUT_NOTHING)
        self._script[page_ref] = _ScriptedReading(text=text, mean_confidence=mean_confidence)

    def fail(self, page_ref: str, error: RecognitionFailed, /) -> None:
        """Script a failure for one page, replacing anything scripted for it before.

        Parameters
        ----------
        page_ref
            The page whose reading will fail.
        error
            The failure to raise. ``retryable`` is the only distinction a caller acts on, and
            both values matter: the shipped Apple Vision binding can only ever report ``False``
            -- it runs on-device with no quota, no endpoint and no clock -- so a use case's
            retry branch is unreachable without a double that can report ``True``.
        """
        self._script[page_ref] = error

    def recognize(self, image: RasterImage, /) -> Recognition:
        """Read one page from the script, or raise what was scripted for it.

        Parameters
        ----------
        image
            The rendered page. Only its ``page_ref`` is consulted; the bytes are never decoded,
            because a double that parsed pixels would be a second product adapter.

        Returns
        -------
        Recognition
            The scripted reading, built with
            :meth:`~rmspec.domain.ports.ocr.Recognition.attributed` so its ``page_ref`` is
            derived from the raster that was read rather than filled by hand -- which is what
            makes it impossible for this double to attribute a reading to a page it was not
            given.

        Raises
        ------
        RecognitionFailed
            Whichever failure :meth:`fail` was given for this page.
        KeyError
            Nothing was scripted for this page. Not a blank reading: ``text=""`` is a positive
            statement that an engine looked and found nothing, and collapsing the two would let
            an unscripted page silently suppress a paid read in a tiering test.
        """
        self.pages_read.append(image.page_ref)
        scripted = self._script.get(image.page_ref)
        if scripted is None:
            msg = (
                f"nothing is scripted for page {image.page_ref!r}; "
                f"scripted pages are {sorted(self._script)}"
            )
            raise KeyError(msg)
        if isinstance(scripted, RecognitionFailed):
            raise scripted
        return Recognition.attributed(
            image,
            provider_id=self._provider_id,
            text=scripted.text,
            mean_confidence=scripted.mean_confidence,
        )
