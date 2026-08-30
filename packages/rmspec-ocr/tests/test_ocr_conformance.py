"""The port contracts, bound to every implementation this package ships.

Five bindings: the Bedrock model over a body-keyed stub client, the Textract recognizer over a
stub Textract client, the Apple Vision recognizer over a stub line reader, and the two shipped
doubles. The assertions are literally the same objects, imported from ``ocr_contracts.py``, so a
double that quietly narrowed or widened its behaviour fails here rather than a package away in
step 6 -- and so does an adapter.

Nothing here constructs an AWS client, and nothing imports ``Vision``
--------------------------------------------------------------------
Every client and every engine is a constructor argument, so each stub below is a few lines. That
is not a convenience: a suite that had to build a ``bedrock-runtime`` client would carry a
credential chain, a network stack and an account, and an outage, a null ``content`` and a
truncated answer would each cost a billable call to reproduce -- if they could be reproduced on
purpose at all. :mod:`rmspec.ocr._vision_framework` is never named here either; the Apple
binding's line-reader seam is one callable, and only
:class:`~rmspec.ocr._confidence.RecognizedLine` -- a plain ``NamedTuple`` with no native
dependency -- is imported to build its answers.

Where a binding cannot inject what the contract asks for, it *causes* it instead
------------------------------------------------------------------------------
``VisionLanguageModelContract.failing`` names a domain error *class*, and the Bedrock binding has
no seam that hands the adapter one. It hands its stub client the botocore exception that
translates to that class -- and for :class:`~rmspec.domain.errors.ModelResponseMalformed`, an
envelope with no readable body, because that error is not an exception the SDK raises at all. The
same holds for the Textract recognizer's retryability, which is *derived* from a service code and
an HTTP status rather than injected. Both are better tests than injection would have been, because
the translation is then on the path being asserted rather than bypassed.

The stub client is keyed by the body it is sent, not by call order
----------------------------------------------------------------
:class:`StubClient` holds a dictionary from the exact serialised request body to the payload it
answers with, built by running the adapter's own
:func:`rmspec.ocr._openai_wire.encode_request` over each request the contract scripted. A
body-agnostic stub would have made the model half of this suite pass for an adapter that always
sent the *first* request it ever saw, and it would have left the shipped double as the only
implementation whose replies were keyed at all -- which is exactly the divergence a shared
contract exists to catch.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any, Final

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from ocr_contracts import (
    CONTRACT_PAGES,
    CONTRACT_REQUESTS,
    INKED_TEXT,
    MEAN_CONFIDENCE,
    TextRecognizerContract,
    VisionLanguageModelContract,
    a_model_error,
    a_raster,
)

import rmspec.ocr
import rmspec.ocr.testing
from rmspec.domain.errors import (
    ModelAccessDenied,
    ModelRejectedRequest,
    ModelResponseMalformed,
    ModelThrottled,
    ModelUnavailable,
    RecognitionFailed,
)
from rmspec.domain.ports.ocr import StopReason
from rmspec.ocr import _openai_wire
from rmspec.ocr._confidence import RecognizedLine
from rmspec.ocr.apple_vision import AppleVisionRecognizer
from rmspec.ocr.testing import (
    DEFAULT_ENGINE_REVISION,
    DEFAULT_PROVIDER,
    ScriptedTextRecognizer,
    ScriptedVisionLanguageModel,
)
from rmspec.ocr.textract import TextractRecognizer
from rmspec.ocr.vision_model import ADAPTER_REVISION, BedrockOpenAiVisionModel

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rmspec.domain.errors import ModelError
    from rmspec.domain.ports.ocr import TextRecognizer, VisionLanguageModel, VisionRequest

MODEL_ID: Final = "global.openai.gpt-5.6-luna"
"""The measured inference profile id. A profile id and not a bare model id: the measured models
are ``INFERENCE_PROFILE`` only, so a bare one comes back as an entitlement failure."""

OTHER_MODEL_ID: Final = "global.openai.gpt-5.6-terra"
"""A second profile, for the fingerprint variance seam. Which model merges and which reads pages
is a composition decision, so both ids belong to the container rather than to the adapter."""

REGION: Final = "us-east-1"
OTHER_REGION: Final = "eu-west-1"
"""Two regions. The same profile id in two regions is two bindings that can answer differently,
which is why the region is folded into the fingerprint and never read back off an error."""

OTHER_REVISION: Final = "2"
"""A bumped adapter revision, which must invalidate every row the previous one wrote."""

OTHER_ENGINE_REVISION: Final = 2
"""A bumped engine revision, which must move a recognizer's provider slug."""

OTHER_PROVIDER: Final = "other-engine"
"""A second engine name, which only the double can be built with."""

OTHER_SCRIPTED_MODEL_ID: Final = "other-scripted-model"
"""A second claimed identity, which only the double can be built with."""

SERVED_MODEL: Final = "openai.gpt-5.6-luna"
SERVED_FINGERPRINT: Final = "fp_probe_0001"
"""What the measured response reports having served. Diagnostics, logged and never folded into a
cache key, because neither is knowable until a call has already been paid for."""

PROMPT_TOKENS: Final = 177
COMPLETION_TOKENS: Final = 11
"""The measured accounting for one page at a 2000-token budget."""

BEDROCK_ENDPOINT: Final = "https://bedrock-runtime.us-east-1.amazonaws.com"
"""The regional host an unreachable-endpoint failure names."""

CONFIDENCE_SCALE: Final = 100.0
"""Textract reports confidence on a 0 -- 100 scale; the port's field is 0.0 -- 1.0."""

REFUSED_IMAGE: Final = "vision.framework: could not read 8 bytes as an image"
"""What the shipped Vision reader's error says. Raised here as the ``RuntimeError`` base class the
adapter catches, because it may not import the macOS-only module that defines the real one."""

SEEDED_FAILURE: Final = "the contract seeded a failing engine"
"""What a scripted recognition failure carries as its detail, which a shell displays verbatim."""

_FINISH_REASONS: Final[Mapping[StopReason, str]] = {
    StopReason.COMPLETE: "stop",
    StopReason.OUTPUT_LIMIT: "length",
    StopReason.REFUSAL: "content_filter",
}
"""Domain stop reason to the wire token that produces it.

:attr:`~rmspec.domain.ports.ocr.StopReason.STOP_SEQUENCE` is absent, which is the same statement
``_openai_wire``'s own decode table makes from the other direction: Chat Completions reports
``stop`` for a natural end and a stop-sequence hit alike, so this binding declares that member
unreachable rather than faking it.
"""


def envelope_payload(text: str, stop_reason: StopReason) -> dict[str, object]:
    """Build one Chat Completions response body, in the shape the probe measured.

    Parameters
    ----------
    text
        What ``choices[0].message.content`` carries.
    stop_reason
        The domain reason, translated back to the wire token that produces it.

    Returns
    -------
    dict[str, object]
        The body, carrying the served identity and the token accounting as well, so the log line
        and the usage branch are on the path every model assertion runs down.
    """
    return {
        "choices": [
            {
                "finish_reason": _FINISH_REASONS[stop_reason],
                "index": 0,
                "message": {"content": text},
            }
        ],
        "model": SERVED_MODEL,
        "system_fingerprint": SERVED_FINGERPRINT,
        "usage": {
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": COMPLETION_TOKENS,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def service_error(code: str, status: int) -> ClientError:
    """Build the error botocore raises for one service-side refusal.

    Parameters
    ----------
    code
        The service's own error code, which both adapters' translations classify on.
    status
        The HTTP status behind it, which the fallback split reads when the code is unknown.

    Returns
    -------
    ClientError
        The exception, shaped as botocore raises it.
    """
    return ClientError(
        {
            "Error": {"Code": code, "Message": "the contract seeded a service refusal"},
            "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": "r-1"},
        },
        "InvokeModel",
    )


_BOTOCORE_FAILURES: Final[Mapping[type[ModelError], Callable[[], BaseException]]] = {
    ModelAccessDenied: lambda: service_error("AccessDeniedException", 403),
    ModelRejectedRequest: lambda: service_error("ValidationException", 400),
    ModelThrottled: lambda: service_error("ThrottlingException", 429),
    ModelUnavailable: lambda: EndpointConnectionError(endpoint_url=BEDROCK_ENDPOINT),
}
"""How the Bedrock binding *causes* four of the five model errors.

:class:`~rmspec.domain.errors.ModelResponseMalformed` is absent because it is not an exception the
SDK raises: it is what a well-formed exchange with an unreadable body becomes, so
``TestBedrockOpenAiVisionModel.failing`` produces it with an envelope instead.
"""


class StubClient:
    """The whole client surface the Bedrock binding uses, keyed by the body it is sent.

    Three modes, one per shape the contract asks for: answer the payload scripted for this exact
    request body, raise a botocore exception, or hand back an envelope verbatim so the adapter can
    fail to read it.
    """

    def __init__(
        self,
        *,
        answers: Mapping[str | bytes, Mapping[str, object]] | None = None,
        error: BaseException | None = None,
        envelope: Mapping[str, object] | None = None,
    ) -> None:
        self.answers: Mapping[str | bytes, Mapping[str, object]] = (
            {} if answers is None else answers
        )
        self.error = error
        self.envelope = envelope
        self.calls: list[Mapping[str, str | bytes]] = []

    def invoke_model(self, /, **request: str | bytes) -> Mapping[str, object]:
        """Record the keywords, then answer or fail as configured.

        Parameters
        ----------
        **request
            The wire request, ``modelId`` and ``body``.

        Returns
        -------
        Mapping[str, object]
            The response envelope, whose ``body`` is a fresh stream every call so a second
            completion is never served a spent one.
        """
        self.calls.append(dict(request))
        if self.error is not None:
            raise self.error
        if self.envelope is not None:
            return self.envelope
        body = request["body"]
        payload = self.answers.get(body)
        if payload is None:
            pytest.fail(f"the stub was sent a body it was not scripted for: {body!r}")
        return {"body": io.BytesIO(json.dumps(payload).encode())}


class StubTextract:
    """The one Textract client method the recognizer calls, answering from a script."""

    def __init__(
        self,
        response: Mapping[str, Any] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.response: Mapping[str, Any] = {"Blocks": []} if response is None else response
        self.error = error
        self.calls: list[Mapping[str, object]] = []

    def detect_document_text(self, **kwargs: object) -> Mapping[str, Any]:
        """Record the request and answer with the scripted response, or raise.

        Parameters
        ----------
        **kwargs
            The wire request, ``Document={"Bytes": ...}``.

        Returns
        -------
        Mapping[str, Any]
            The scripted ``DetectDocumentText`` response.
        """
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class StubReader:
    """The one call the Apple binding makes into an engine, answering from a script."""

    def __init__(
        self,
        lines: Sequence[RecognizedLine] = (),
        *,
        error: BaseException | None = None,
    ) -> None:
        self.lines = lines
        self.error = error
        self.seen: list[bytes] = []

    def __call__(self, data: bytes, /) -> Sequence[RecognizedLine]:
        """Record the bytes and answer with the scripted lines, or raise.

        Parameters
        ----------
        data
            Encoded image bytes, never a path.

        Returns
        -------
        Sequence[RecognizedLine]
            One line per recognised region, empty for a page with no text on it.
        """
        self.seen.append(data)
        if self.error is not None:
            raise self.error
        return self.lines


def textract_blocks(text: str, confidence: float) -> Mapping[str, Any]:
    """Build a ``DetectDocumentText`` response holding one LINE block per line of ``text``.

    Parameters
    ----------
    text
        The reading, newline-separated. ``""`` becomes *no* LINE blocks at all, which is how the
        service actually reports a blank page -- a response holding only its PAGE block -- so the
        emptiness of a blank reading is produced by the adapter's own fold rather than scripted.
    confidence
        The confidence every line carries, rescaled to the service's 0 -- 100 scale.

    Returns
    -------
    Mapping[str, Any]
        The response, PAGE block included.
    """
    return {
        "Blocks": [
            {"BlockType": "PAGE"},
            *(
                {"BlockType": "LINE", "Text": line, "Confidence": confidence * CONFIDENCE_SCALE}
                for line in _split(text)
            ),
        ]
    }


def vision_lines(text: str, confidence: float) -> list[RecognizedLine]:
    """Build one recognised line per line of ``text``, as the Vision reader reports them.

    Parameters
    ----------
    text
        The reading, newline-separated. ``""`` becomes no lines at all.
    confidence
        The confidence every line carries, already on the port's 0.0 -- 1.0 scale.

    Returns
    -------
    list[RecognizedLine]
        The lines, in reading order.
    """
    return [RecognizedLine(text=line, confidence=confidence) for line in _split(text)]


def _split(text: str) -> list[str]:
    r"""Split a reading into the lines an engine would have reported it as.

    Parameters
    ----------
    text
        The reading.

    Returns
    -------
    list[str]
        One entry per line, and none at all for ``""`` -- because ``"".split("\n")`` is one empty
        line, which is a page holding one blank region rather than a page holding nothing.
    """
    return text.split("\n") if text else []


def scripted_reading(
    text: str,
    confidence: float | None,
    /,
    *,
    provider: str = DEFAULT_PROVIDER,
    revision: int = DEFAULT_ENGINE_REVISION,
) -> ScriptedTextRecognizer:
    """Build a double scripted with the same reading for every contract page.

    Parameters
    ----------
    text
        The reading to script.
    confidence
        The confidence to script, or ``None``.
    provider
        Engine half of the slug.
    revision
        Reading-behaviour revision.

    Returns
    -------
    ScriptedTextRecognizer
        The double, scripted for every page in ``CONTRACT_PAGES`` -- which is why that tuple is
        published rather than each binding guessing which pages the assertions reach for.
    """
    recognizer = ScriptedTextRecognizer(provider=provider, revision=revision)
    for page_ref in CONTRACT_PAGES:
        recognizer.read(page_ref, text=text, mean_confidence=confidence)
    return recognizer


# ───────────────────────────── the Bedrock model ─────────────────────────────


class TestBedrockOpenAiVisionModel(VisionLanguageModelContract):
    """The model contract over an OpenAI-envelope profile on Bedrock."""

    @staticmethod
    def _model(
        client: StubClient,
        /,
        *,
        model_id: str = MODEL_ID,
        region: str = REGION,
        revision: str = ADAPTER_REVISION,
    ) -> BedrockOpenAiVisionModel:
        """Build the binding, defaulting every identity-bearing argument to the baseline.

        Parameters
        ----------
        client
            The stub client to inject. Deliberately not part of the fingerprint.
        model_id
            The inference profile id.
        region
            The region the client was built for.
        revision
            This binding's revision.

        Returns
        -------
        BedrockOpenAiVisionModel
            The adapter.
        """
        return BedrockOpenAiVisionModel(
            client,
            model_id=model_id,
            region=region,
            revision=revision,
        )

    def answering(
        self,
        *requests: VisionRequest,
        text: str,
        stop_reason: StopReason,
    ) -> VisionLanguageModel:
        """Return a binding whose stub answers each request's own serialised body.

        Parameters
        ----------
        *requests
            The requests to script, run through the adapter's own encoder so the key is the exact
            bytes the adapter will send.
        text
            The answer.
        stop_reason
            Why generation stopped, translated back to its wire token.

        Returns
        -------
        VisionLanguageModel
            The adapter, which ``ty`` checks against the Protocol here.
        """
        payload = envelope_payload(text, stop_reason)
        answers: dict[str | bytes, Mapping[str, object]] = {
            _openai_wire.encode_request(request): payload for request in requests
        }
        return self._model(StubClient(answers=answers))

    def failing(self, error_type: type[ModelError], /) -> VisionLanguageModel:
        """Return a binding that causes that error rather than being handed it.

        Parameters
        ----------
        error_type
            One of the five the port's ``Raises`` clause names.

        Returns
        -------
        VisionLanguageModel
            The adapter, over a stub that raises the botocore exception which translates to
            ``error_type`` -- or, for a malformed response, over one that answers an envelope
            carrying no readable body at all.
        """
        if error_type is ModelResponseMalformed:
            return self._model(StubClient(envelope={}))
        return self._model(StubClient(error=_BOTOCORE_FAILURES[error_type]()))

    def identity_variants(self) -> Mapping[str, VisionLanguageModel]:
        """Return one binding per identity-bearing constructor argument.

        Returns
        -------
        Mapping[str, VisionLanguageModel]
            Three: the profile id, the region, and this binding's own revision. The client is
            absent by design and is covered by :meth:`other_collaborator` instead.
        """
        return {
            "model_id": self._model(StubClient(), model_id=OTHER_MODEL_ID),
            "region": self._model(StubClient(), region=OTHER_REGION),
            "revision": self._model(StubClient(), revision=OTHER_REVISION),
        }

    def other_collaborator(self) -> VisionLanguageModel:
        """Return a binding over a second client with the baseline identity.

        Returns
        -------
        VisionLanguageModel
            The adapter. Two clients built for the same profile and region are the same binding.
        """
        return self._model(StubClient())

    def unreachable_stop_reasons(self) -> frozenset[StopReason]:
        """Return the stop reason this envelope cannot report.

        Returns
        -------
        frozenset[StopReason]
            ``STOP_SEQUENCE``: Chat Completions reports ``stop`` for a natural end and a
            stop-sequence hit alike, and ``Decoding`` carries no stop-sequence field, so this
            adapter never sends one and could not honestly report the fourth member.
        """
        return frozenset({StopReason.STOP_SEQUENCE})

    def test_the_body_the_stub_matched_is_the_one_the_adapter_actually_sent(self) -> None:
        """The body the stub matched is the one the adapter actually sent."""
        # Guard the guard: every model assertion above runs through a body-keyed stub, so a
        # mismatch between the key and what the adapter sends would surface as a failure to answer
        # rather than as a wrong answer. This pins the keying itself, and the wire spelling of the
        # profile id with it -- `modelId`, which a camelCase parameter name could not express.
        answers: dict[str | bytes, Mapping[str, object]] = {
            _openai_wire.encode_request(request): envelope_payload("x", StopReason.COMPLETE)
            for request in CONTRACT_REQUESTS
        }
        client = StubClient(answers=answers)
        self._model(client).complete(CONTRACT_REQUESTS[0])
        assert len(client.calls) == 1
        call = client.calls[0]
        assert set(call) == {"modelId", "body"}
        assert call["modelId"] == MODEL_ID
        assert call["body"] == _openai_wire.encode_request(CONTRACT_REQUESTS[0])


class TestScriptedVisionLanguageModel(VisionLanguageModelContract):
    """The model contract over the shipped double, whose replies are keyed by request."""

    def answering(
        self,
        *requests: VisionRequest,
        text: str,
        stop_reason: StopReason,
    ) -> VisionLanguageModel:
        """Return a double scripted to answer each of those requests.

        Parameters
        ----------
        *requests
            The requests to script. Unlike the adapter's stub, this double genuinely keys on the
            request value rather than on the bytes it would have been serialised to.
        text
            The answer.
        stop_reason
            Why generation stopped.

        Returns
        -------
        VisionLanguageModel
            The double, which ``ty`` checks against the Protocol here.
        """
        model = ScriptedVisionLanguageModel()
        for request in requests:
            model.answer(request, text=text, stop_reason=stop_reason)
        return model

    def failing(self, error_type: type[ModelError], /) -> VisionLanguageModel:
        """Return a double scripted to raise that error for every contract request.

        Parameters
        ----------
        error_type
            One of the five.

        Returns
        -------
        VisionLanguageModel
            The double.
        """
        model = ScriptedVisionLanguageModel()
        for request in CONTRACT_REQUESTS:
            model.fail(request, a_model_error(error_type))
        return model

    def identity_variants(self) -> Mapping[str, VisionLanguageModel]:
        """Return one double per identity-bearing constructor argument.

        Returns
        -------
        Mapping[str, VisionLanguageModel]
            Two: the claimed model identity and the double's own revision. The script is absent
            for the same reason the adapter's client is -- it is the collaborator, not the
            identity.
        """
        return {
            "model_id": ScriptedVisionLanguageModel(model_id=OTHER_SCRIPTED_MODEL_ID),
            "revision": ScriptedVisionLanguageModel(revision=OTHER_REVISION),
        }

    def other_collaborator(self) -> VisionLanguageModel:
        """Return a double with the baseline identity and a different script.

        Returns
        -------
        VisionLanguageModel
            The double. Its script answers other text, for one request rather than two, and its
            fingerprint is still the baseline's -- which is what stops an application test from
            keying a cache row on what a double happened to be told to say.
        """
        return self.answering(
            CONTRACT_REQUESTS[0],
            text="a different answer entirely",
            stop_reason=StopReason.REFUSAL,
        )


# ───────────────────────────── the two recognizers ─────────────────────────────


class TestTextractRecognizer(TextRecognizerContract):
    """The recognition contract over Amazon Textract's ``DetectDocumentText``."""

    @pytest.fixture
    def recognizer(self) -> TextRecognizer:
        """Return a recognizer whose stub reads the reference text off any page.

        Returns
        -------
        TextRecognizer
            The adapter, which ``ty`` checks against the Protocol here.
        """
        return TextractRecognizer(StubTextract(textract_blocks(INKED_TEXT, MEAN_CONFIDENCE)))

    def blank_recognizer(self) -> TextRecognizer:
        """Return a recognizer whose stub answers with only a PAGE block.

        Returns
        -------
        TextRecognizer
            The adapter. No LINE blocks at all, which is how the service reports a blank page, so
            the reading's emptiness is produced by the fold rather than scripted.
        """
        return TextractRecognizer(StubTextract(textract_blocks("", MEAN_CONFIDENCE)))

    def failing_recognizer(self, *, retryable: bool) -> TextRecognizer:
        """Return a recognizer whose stub raises a service error of that retryability.

        Parameters
        ----------
        retryable
            Whether the failure must say retrying could help.

        Returns
        -------
        TextRecognizer
            The adapter. The retryability is *derived* by the adapter from the code and the status
            rather than injected, so the classification is on the path being asserted: a throttle
            is worth another attempt, a document the service refused is not.
        """
        code = "ThrottlingException" if retryable else "BadDocumentException"
        return TextractRecognizer(StubTextract(error=service_error(code, 400)))

    def revision_variants(self) -> Mapping[str, TextRecognizer]:
        """Return one recognizer per identity-bearing constructor argument.

        Returns
        -------
        Mapping[str, TextRecognizer]
            One: the reading-behaviour revision. The engine half of the slug is a module constant
            here, because an adapter that could answer to another engine's name would let one
            engine's cache rows be reused for another's readings.
        """
        stub = StubTextract(textract_blocks(INKED_TEXT, MEAN_CONFIDENCE))
        return {"revision": TextractRecognizer(stub, revision=OTHER_ENGINE_REVISION)}


class TestAppleVisionRecognizer(TextRecognizerContract):
    """The recognition contract over Apple's on-device Vision framework."""

    @pytest.fixture
    def recognizer(self) -> TextRecognizer:
        """Return a recognizer whose stub reader reads the reference text off any page.

        Returns
        -------
        TextRecognizer
            The adapter, which ``ty`` checks against the Protocol here.
        """
        return AppleVisionRecognizer(StubReader(vision_lines(INKED_TEXT, MEAN_CONFIDENCE)))

    def blank_recognizer(self) -> TextRecognizer:
        """Return a recognizer whose stub reader finds no lines.

        Returns
        -------
        TextRecognizer
            The adapter. Zero lines is a success, which is what the shipped reader reports for a
            blank image and what its own probe asserts by reading one.
        """
        return AppleVisionRecognizer(StubReader([]))

    def failing_recognizer(self, *, retryable: bool) -> TextRecognizer:
        """Return a recognizer whose reader fails, which for this engine is always permanent.

        Parameters
        ----------
        retryable
            Whether the failure must say retrying could help. ``True`` is unreachable here and the
            contract skips it -- see :meth:`unreachable_retryability`.

        Returns
        -------
        TextRecognizer
            The adapter, over a reader raising the ``RuntimeError`` base class the adapter catches
            because it may not import the module that defines the real error.
        """
        assert not retryable, "the contract must skip the retryable case for this engine"
        return AppleVisionRecognizer(StubReader(error=RuntimeError(REFUSED_IMAGE)))

    def revision_variants(self) -> Mapping[str, TextRecognizer]:
        """Return one recognizer per identity-bearing constructor argument.

        Returns
        -------
        Mapping[str, TextRecognizer]
            One: the reading-behaviour revision.
        """
        reader = StubReader(vision_lines(INKED_TEXT, MEAN_CONFIDENCE))
        return {"revision": AppleVisionRecognizer(reader, revision=OTHER_ENGINE_REVISION)}

    def unreachable_retryability(self) -> frozenset[bool]:
        """Return the retryability this engine's failures cannot carry.

        Returns
        -------
        frozenset[bool]
            ``True``. This engine runs on-device with no quota, no endpoint and no clock, so every
            failure it can report -- undecodable bytes, a refused image, a bridge that could not
            answer -- gives the same answer again on the same input, and reporting one as
            retryable would spend the caller's retry budget on a certainty.
        """
        return frozenset({True})


class TestScriptedTextRecognizer(TextRecognizerContract):
    """The recognition contract over the shipped double, whose readings are keyed by page."""

    @pytest.fixture
    def recognizer(self) -> TextRecognizer:
        """Return a double scripted to read the reference text off every contract page.

        Returns
        -------
        TextRecognizer
            The double, which ``ty`` checks against the Protocol here.
        """
        return scripted_reading(INKED_TEXT, MEAN_CONFIDENCE)

    def blank_recognizer(self) -> TextRecognizer:
        """Return a double scripted to find nothing on every contract page.

        Returns
        -------
        TextRecognizer
            The double. ``text=""`` is a positive scripting and is not the same as leaving a page
            unscripted, which is a ``KeyError``.
        """
        return scripted_reading("", None)

    def failing_recognizer(self, *, retryable: bool) -> TextRecognizer:
        """Return a double scripted to fail on every contract page with that retryability.

        Parameters
        ----------
        retryable
            Whether the failure says retrying could help. Both values are reachable here, which is
            one of the reasons this double exists: the shipped on-device engine can only ever
            report ``False``, so a use case's retry branch is otherwise unreachable.

        Returns
        -------
        TextRecognizer
            The double.
        """
        recognizer = ScriptedTextRecognizer()
        failure = RecognitionFailed(
            provider_id=recognizer.provider_id,
            detail=SEEDED_FAILURE,
            retryable=retryable,
        )
        for page_ref in CONTRACT_PAGES:
            recognizer.fail(page_ref, failure)
        return recognizer

    def revision_variants(self) -> Mapping[str, TextRecognizer]:
        """Return one double per identity-bearing constructor argument.

        Returns
        -------
        Mapping[str, TextRecognizer]
            Two: the engine half of the slug and the revision. The engine half is an argument here
            and a constant in both adapters, because one double has to be able to stand in for two
            engines at once -- which is what a test of the app's sorted-slug cache key needs.
        """
        return {
            "provider": scripted_reading(INKED_TEXT, MEAN_CONFIDENCE, provider=OTHER_PROVIDER),
            "revision": scripted_reading(
                INKED_TEXT,
                MEAN_CONFIDENCE,
                revision=OTHER_ENGINE_REVISION,
            ),
        }


# ─────────────────── the shape of the surface, and one absence ───────────────────

COVERED_ADAPTERS: Final = (AppleVisionRecognizer, BedrockOpenAiVisionModel, TextractRecognizer)
"""Every adapter bound to a contract above, compared against the exported surface below so a new
adapter cannot ship without a conformance binding."""

COVERED_DOUBLES: Final = (ScriptedTextRecognizer, ScriptedVisionLanguageModel)
"""Every double bound to a contract above, compared against ``rmspec.ocr.testing.__all__``."""


def _looks_like_a_vision_language_model(candidate: object, /) -> bool:
    """Report whether a name satisfies ``VisionLanguageModel`` structurally.

    No port is ``runtime_checkable`` and nothing in this workspace calls ``isinstance`` on one, so
    the check is the method the Protocol declares.

    Parameters
    ----------
    candidate
        A name exported by the package or by its testing subpackage.

    Returns
    -------
    bool
        ``True`` when it has a callable ``complete``.
    """
    return callable(getattr(candidate, "complete", None))


def _looks_like_a_text_recognizer(candidate: object, /) -> bool:
    """Report whether a name satisfies ``TextRecognizer`` structurally.

    Parameters
    ----------
    candidate
        A name exported by the package or by its testing subpackage.

    Returns
    -------
    bool
        ``True`` when it has a callable ``recognize``.
    """
    return callable(getattr(candidate, "recognize", None))


def _bindings_in(module: object, names: Sequence[str], /) -> set[str]:
    """Return the names in ``names`` that satisfy either OCR port.

    Parameters
    ----------
    module
        The module the names belong to.
    names
        That module's declared public surface.

    Returns
    -------
    set[str]
        Every name binding one of the two ports.
    """
    return {
        name
        for name in names
        if _looks_like_a_vision_language_model(getattr(module, name))
        or _looks_like_a_text_recognizer(getattr(module, name))
    }


def test_the_package_binds_one_model_and_two_recognizers_and_no_ensemble() -> None:
    """The package binds one model and two recognizers and no ensemble."""
    # The count *is* the ensemble's absence: a `RecognizerEnsemble` would be a third name
    # satisfying `TextRecognizer`, and it is not built because fan-out width, ordering and
    # partial-failure tolerance are use-case policy -- the app takes `list[TextRecognizer]` and
    # loops. Asserted rather than left to a docstring, so a later reader cannot reintroduce the
    # port without failing a test.
    surface = rmspec.ocr.__all__
    models = sorted(
        name for name in surface if _looks_like_a_vision_language_model(getattr(rmspec.ocr, name))
    )
    recognizers = sorted(
        name for name in surface if _looks_like_a_text_recognizer(getattr(rmspec.ocr, name))
    )
    assert models == ["BedrockOpenAiVisionModel"]
    assert recognizers == ["AppleVisionRecognizer", "TextractRecognizer"]


def test_every_port_binding_the_package_exports_is_held_to_the_contract() -> None:
    """Every port binding the package exports is held to the contract."""
    assert _bindings_in(rmspec.ocr, rmspec.ocr.__all__) == {
        adapter.__name__ for adapter in COVERED_ADAPTERS
    }


def test_every_shipped_double_is_held_to_the_same_contract() -> None:
    """Every shipped double is held to the same contract."""
    # The reason the doubles ship under `src/` rather than living in a tests/ helper: one assertion
    # set runs against them and against the adapters, so a double that narrowed its behaviour fails
    # here rather than inside the application-layer test that trusted it.
    assert _bindings_in(rmspec.ocr.testing, rmspec.ocr.testing.__all__) == {
        double.__name__ for double in COVERED_DOUBLES
    }


def test_three_unrelated_engines_produce_one_reading_for_one_page() -> None:
    """Three unrelated engines produce one reading for one page."""
    # The strongest statement this file can make about the shared fold: two adapters over two
    # unrelated engines and one double, one `Recognition` for the same page. Only `provider_id`
    # differs, and it must -- the app folds that exact string into its cache key, so two engines
    # agreeing on a reading must still be two rows.
    page = a_raster(CONTRACT_PAGES[0])
    readings = [
        TextractRecognizer(StubTextract(textract_blocks(INKED_TEXT, MEAN_CONFIDENCE))).recognize(
            page
        ),
        AppleVisionRecognizer(StubReader(vision_lines(INKED_TEXT, MEAN_CONFIDENCE))).recognize(
            page
        ),
        scripted_reading(INKED_TEXT, MEAN_CONFIDENCE).recognize(page),
    ]
    assert {reading.text for reading in readings} == {INKED_TEXT}
    assert {reading.page_ref for reading in readings} == {page.page_ref}
    assert len({reading.provider_id for reading in readings}) == len(readings)
    assert all(reading.mean_confidence == pytest.approx(MEAN_CONFIDENCE) for reading in readings)
