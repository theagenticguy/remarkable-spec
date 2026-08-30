"""The OpenAI Chat Completions envelope, as two pure functions and one decoded value.

This module builds the request body and reads the response body. It holds no client, imports
neither ``boto3`` nor ``botocore``, and names no model. That is what makes the whole wire
contract testable without a credential, an endpoint, or a billable call -- and the wire
contract is where the expensive mistakes live, so it is the half that must be exercised
hardest.

What the envelope actually is (measured 2026-08-28, ``us-east-1``)
-----------------------------------------------------------------
The ``global.openai.gpt-5.6-*`` inference profiles speak **OpenAI Chat Completions**, not the
Anthropic envelope every other Bedrock model in this workspace's history used. Sending
``{"anthropic_version": ...}`` is rejected with
``ValidationException / unknown_parameter``, so this is not a superset -- it is a different
body:

- The output budget is **``max_completion_tokens``**, never ``max_tokens``.
- An image is an ``image_url`` content part whose ``url`` is a
  ``data:image/<png|jpeg>;base64,...`` URI. It is *not* Bedrock's
  ``{"type": "image", "source": {...}}`` block; that block is silently not this API.
- The system turn is a message with ``role: "developer"``. :attr:`VisionRequest.system` goes
  there and nowhere else.
- ``stream`` must be ``false`` or absent, because :meth:`invoke_model` is not the streaming
  operation. It is omitted here rather than sent as ``false``, so there is one fewer field
  that can be wrong.
- ``model`` inside the body may be omitted; Bedrock fills it from the ``modelId`` header. It
  is omitted, so the profile id has exactly one spelling per call and the body cannot
  disagree with the header.
- ``reasoning_effort`` accepts exactly ``{none, low, medium, high}``. ``minimal`` is
  **rejected** with ``unsupported_value``, which is why :data:`ACCEPTED_REASONING_EFFORTS` is
  a closed set and the effort is mapped from :class:`ReasoningEffort` through
  :data:`_EFFORT_TOKENS` rather than passed through as a ``str``.

``Decoding.temperature`` is deliberately not sent
-------------------------------------------------
No probe ever sent a temperature to these profiles, so no measurement says what field name
they accept, what range they take, or whether they accept one at all. Inventing a field the
measurements do not show is how an adapter starts sending something the provider rejects --
or worse, silently ignores while the caller believes it took effect.

The honest consequence, stated here because the caller deserves to read it rather than
discover it: **:attr:`~rmspec.domain.ports.ocr.Decoding.temperature` is unhonoured by this
binding.** Every call runs at the profile's own default sampling temperature. The value is
still folded into :meth:`VisionRequest.digest` through
:meth:`~rmspec.domain.ports.ocr.Decoding.canonical`, so two requests differing only in
temperature remain two cache rows -- they are simply two rows that will hold the same kind of
answer. When a temperature field is measured, adding it here is a change to
:data:`WIRE_REVISION`, which mechanically invalidates every row this binding wrote.

The silent-failure trap, and why ``None`` is never an empty transcription
------------------------------------------------------------------------
Same prompt, same image, only the budget differing:

===========================  ===============  ==================  =====================
``max_completion_tokens``    ``finish_reason``  ``reasoning_tokens``  ``message.content``
===========================  ===============  ==================  =====================
24                           ``length``       24                  ``None``
2000                         ``stop``         0                   ``'rmspec probe 7431'``
===========================  ===============  ==================  =====================

At the tight budget the entire allowance went to latent reasoning and ``content`` came back
``None`` **with no exception raised**. A client doing ``...["content"].strip()`` gets an
``AttributeError`` from inside a library; one treating falsy content as "no text found"
caches an empty transcription for a page that has ink, under a key that gives no hint
anything went wrong. So :func:`decode_body` treats ``content is None`` as a malformed body
*always*, whatever ``finish_reason`` says, and it checks content before it validates
``finish_reason`` so an unknown reason cannot pre-empt the check. ``""`` is a different
answer and is passed through: the domain says an empty completion is data.

Why the failure is a package-private exception
----------------------------------------------
:class:`WireFormatError` is private to ``rmspec.ocr``, exactly as
:class:`rmspec.export._pillow.PillowError` is to that package: an adapter catches it and
raises the domain error its port documents. It is *not*
``ModelResponseMalformed`` because that error requires a ``model_id``, and a module that
names a model is a module a second provider speaking this same envelope could not reuse.
:mod:`rmspec.ocr.vision_model` performs that one translation.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, ClassVar, Final, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from rmspec.domain.ports.ocr import ImageMedia, ReasoningEffort, StopReason, TokenUsage

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rmspec.domain.ports.ocr import RasterImage, VisionRequest

__all__ = [
    "ACCEPTED_REASONING_EFFORTS",
    "ENVELOPE",
    "WIRE_REVISION",
    "WireCompletion",
    "WireFormatError",
    "build_body",
    "decode_body",
    "encode_request",
    "image_data_uri",
]

ENVELOPE: Final = "openai-chat-completions"
"""Name of the wire envelope, carried by every error this module raises."""

WIRE_REVISION: Final = "1"
"""Revision of the request-building and decoding bodies in this module.

Folded into :attr:`rmspec.ocr.vision_model.BedrockOpenAiVisionModel.fingerprint`, so editing
what this module sends -- adding a temperature field, changing the developer turn's shape --
is a mechanical cache miss rather than a stale row that looks fresh. Bump it whenever
:func:`build_body` would produce different bytes for the same request.
"""

ACCEPTED_REASONING_EFFORTS: Final = frozenset({"none", "low", "medium", "high"})
"""The closed set of ``reasoning_effort`` tokens the measured profiles accept.

``minimal`` is not in it: it was sent and rejected with
``{"code": "unsupported_value", ...}``. Published so a test can assert that every token
:data:`_EFFORT_TOKENS` can emit is a member, which is what makes "validated before it is
sent" a property of the code rather than a claim in a comment.
"""

_EFFORT_TOKENS: Final[Mapping[ReasoningEffort, str]] = {
    ReasoningEffort.NONE: "none",
    ReasoningEffort.LOW: "low",
    ReasoningEffort.MEDIUM: "medium",
    ReasoningEffort.HIGH: "high",
}
"""Domain effort to wire token. Total over :class:`ReasoningEffort` by test, not by trust.

A mapping rather than ``effort.value``: the two vocabularies agree today, and a lookup that
must be extended when the domain grows a member is a build failure, while ``.value`` would
quietly ship a token the provider rejects.
"""

_DATA_URI_PREFIXES: Final[Mapping[ImageMedia, str]] = {
    ImageMedia.PNG: "data:image/png;base64,",
    ImageMedia.JPEG: "data:image/jpeg;base64,",
}
"""Domain encoding to data-URI prefix. Also total over its enum by test.

:class:`ImageMedia` exists because a wire ``Content-Type`` can lie, and
:class:`~rmspec.domain.ports.ocr.RasterImage` validates the bytes' magic against it -- so the
prefix chosen here is derived from a checked fact, not from a header.
"""

_STOP_REASONS: Final[Mapping[str, StopReason]] = {
    "stop": StopReason.COMPLETE,
    "length": StopReason.OUTPUT_LIMIT,
    "content_filter": StopReason.REFUSAL,
}
"""Wire ``finish_reason`` to domain :class:`StopReason`.

:attr:`StopReason.STOP_SEQUENCE` is absent because it is unreachable through this binding:
Chat Completions reports ``stop`` for both a natural end and a stop-sequence hit, and
:class:`~rmspec.domain.ports.ocr.Decoding` has no stop-sequence field, so this adapter never
sends one. A reason outside this map is a malformed body rather than a guessed fifth state --
``tool_calls`` arriving from a binding that sends no tools means the response is not the one
that was asked for.
"""

_MISSING_TOKENS = "usage is missing integer prompt_tokens and completion_tokens"


class WireFormatError(Exception):
    """A Chat Completions body could not be read as a completion.

    Private to this package. Adapters catch it and raise the domain error their port
    documents -- :class:`~rmspec.domain.errors.ModelResponseMalformed` -- so no
    envelope-shaped exception crosses a port.

    Attributes
    ----------
    detail
        Human-readable cause, already stringified so no wire object is retained.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"{ENVELOPE}: {detail}")
        self.detail = detail


class WireCompletion(BaseModel):
    """One decoded Chat Completions body, before it becomes a domain completion.

    Not a :class:`~rmspec.domain.ports.ocr.VisionCompletion`: that type's
    ``request_digest`` and ``model_fingerprint`` are cache-key components and may only be
    filled by :meth:`~rmspec.domain.ports.ocr.VisionCompletion.answering`, which needs the
    request and the binding this module deliberately does not know about.

    Attributes
    ----------
    text
        The model's answer. May be empty -- emptiness is data. Never ``None``: a null
        ``content`` is a malformed body, not an empty answer.
    stop_reason
        Why generation stopped, as domain data. Truncation arrives here, not as an
        exception.
    usage
        Token accounting when the body carried a readable ``usage`` object, else ``None``.
    served_model
        The ``model`` the response reports having served, when it reports one.
    served_fingerprint
        The response's ``system_fingerprint``, when it reports one.

    Notes
    -----
    ``served_model`` and ``served_fingerprint`` are **diagnostics, never cache-key
    components**. They are the only signal that the model served under a stable inference
    profile id changed underneath this binding, and they are also unknowable until a call has
    already been paid for -- while
    :attr:`~rmspec.domain.ports.ocr.VisionLanguageModel.fingerprint` must be stable for the
    process lifetime and known before the first call. Folding them into a fingerprint would
    therefore break its contract; discarding them would throw away the drift signal. They are
    carried here and logged by :mod:`rmspec.ocr.vision_model` instead.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    text: str
    stop_reason: StopReason
    usage: TokenUsage | None = None
    served_model: str | None = None
    served_fingerprint: str | None = None


def image_data_uri(image: RasterImage, /) -> str:
    """Render one raster as the ``data:`` URI an ``image_url`` part requires.

    Parameters
    ----------
    image
        The already-rendered page. Its ``media`` has been validated against its bytes'
        leading magic by :class:`~rmspec.domain.ports.ocr.RasterImage`, so the prefix chosen
        here cannot restate a lie a ``Content-Type`` told.

    Returns
    -------
    str
        ``data:image/<png|jpeg>;base64,`` followed by the base64 of ``image.data``.
    """
    return _DATA_URI_PREFIXES[image.media] + base64.b64encode(image.data).decode("ascii")


def build_body(request: VisionRequest, /) -> dict[str, object]:
    """Build the Chat Completions request body for one vision request.

    Parameters
    ----------
    request
        The request, carrying prompt text, an optional system turn, images and decoding
        settings.

    Returns
    -------
    dict[str, object]
        ``messages``, ``max_completion_tokens`` and ``reasoning_effort``, and nothing else.
        ``stream``, ``model`` and any temperature field are absent by decision -- see this
        module's docstring for each.
    """
    parts: list[dict[str, object]] = [{"type": "text", "text": request.prompt}]
    parts.extend(
        {"type": "image_url", "image_url": {"url": image_data_uri(image)}}
        for image in request.images
    )
    messages: list[dict[str, object]] = []
    if request.system is not None:
        # The typed-parts list is the only content shape any probe verified, so the
        # developer turn reuses it rather than relying on the string form being accepted.
        developer: list[dict[str, object]] = [{"type": "text", "text": request.system}]
        messages.append({"role": "developer", "content": developer})
    messages.append({"role": "user", "content": parts})
    return {
        "messages": messages,
        "max_completion_tokens": request.decoding.max_output_tokens,
        "reasoning_effort": _EFFORT_TOKENS[request.decoding.reasoning],
    }


def encode_request(request: VisionRequest, /) -> bytes:
    """Serialise one request body to the bytes :meth:`invoke_model` takes.

    Parameters
    ----------
    request
        The request to encode.

    Returns
    -------
    bytes
        Compact ASCII JSON. Compact because a base64 page raster is already the dominant
        cost of the payload, and ASCII because the escape form travels through any
        transport encoding unchanged.
    """
    return json.dumps(build_body(request), separators=(",", ":")).encode("ascii")


def decode_body(payload: bytes, /) -> WireCompletion:
    """Read one Chat Completions response body, refusing every shape that is not a completion.

    Parameters
    ----------
    payload
        The raw response bytes.

    Returns
    -------
    WireCompletion
        The decoded answer, its stop reason, its token accounting and the served identity.

    Raises
    ------
    WireFormatError
        The body is not JSON, is not an object, carries no ``choices[0]``, carries a
        ``content`` that is ``None`` or not a string, reports a ``finish_reason`` this
        envelope does not define, or carries a ``usage`` object that cannot be read as token
        accounting. ``content is None`` is checked before ``finish_reason`` is validated, so
        a truncated-into-silence body is reported as what it is rather than as an unknown
        reason.
    """
    body = _loaded(payload)
    usage = _usage(body)
    choice = _first_choice(body)
    text = _message_text(choice, usage=usage)
    return WireCompletion(
        text=text,
        stop_reason=_stop_reason(choice),
        usage=usage,
        served_model=_optional_str(body.get("model")),
        served_fingerprint=_optional_str(body.get("system_fingerprint")),
    )


def _loaded(payload: bytes, /) -> Mapping[str, object]:
    """Parse the payload as a JSON object.

    Parameters
    ----------
    payload
        The raw response bytes.

    Returns
    -------
    Mapping[str, object]
        The decoded top-level object.

    Raises
    ------
    WireFormatError
        The bytes are not UTF-8, are not JSON, or are JSON that is not an object.
    """
    try:
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"response body is not JSON: {exc}"
        raise WireFormatError(msg) from exc
    return _as_object(body, what="response body")


def _first_choice(body: Mapping[str, object], /) -> Mapping[str, object]:
    """Return ``choices[0]``, which every non-streaming completion has exactly one of.

    Parameters
    ----------
    body
        The decoded top-level object.

    Returns
    -------
    Mapping[str, object]
        The first choice.

    Raises
    ------
    WireFormatError
        ``choices`` is absent, is not a list, is empty, or its first element is not an
        object. An empty ``choices`` list was one of the two latent crashes the legacy
        Bedrock call sites carried.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        msg = f"response body carries no choices[0]: choices={choices!r}"
        raise WireFormatError(msg)
    return _as_object(choices[0], what="choices[0]")


def _message_text(choice: Mapping[str, object], /, *, usage: TokenUsage | None) -> str:
    """Return the answer text, refusing a null ``content`` whatever the reason says.

    Parameters
    ----------
    choice
        ``choices[0]``.
    usage
        The already-decoded token accounting, used only to make the failure message name the
        reasoning spend that caused it.

    Returns
    -------
    str
        ``choices[0].message.content``, possibly empty.

    Raises
    ------
    WireFormatError
        ``message`` is absent or not an object, or ``content`` is ``None`` or not a string.
        ``None`` is refused unconditionally: it is what a budget consumed entirely by latent
        reasoning returns, and treating it as an empty reading would cache a blank
        transcription for a page that has ink.
    """
    message = _as_object(choice.get("message"), what="choices[0].message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if content is None:
        spent = "unreported" if usage is None else repr(usage.reasoning_tokens)
        msg = (
            f"choices[0].message.content is null with "
            f"finish_reason={choice.get('finish_reason')!r} and reasoning_tokens={spent}: "
            f"the model returned no text at all, which is what a completion budget spent "
            f"entirely on reasoning looks like -- it is not an empty page"
        )
        raise WireFormatError(msg)
    msg = f"choices[0].message.content is a {type(content).__name__}, not a string"
    raise WireFormatError(msg)


def _stop_reason(choice: Mapping[str, object], /) -> StopReason:
    """Map the wire ``finish_reason`` onto the domain's stop reason.

    Parameters
    ----------
    choice
        ``choices[0]``.

    Returns
    -------
    StopReason
        The domain reason. ``length`` becomes :attr:`StopReason.OUTPUT_LIMIT` as *data*, not
        an exception: the port's contract is that truncation is the caller's decision, and
        the app satisfies "never store a truncated read" by caching only
        :attr:`~rmspec.domain.ports.ocr.VisionCompletion.is_complete` rows.

    Raises
    ------
    WireFormatError
        ``finish_reason`` is absent, is not a string, or is a token this envelope does not
        define.
    """
    finish = choice.get("finish_reason")
    if isinstance(finish, str):
        stop = _STOP_REASONS.get(finish)
        if stop is not None:
            return stop
    msg = f"finish_reason {finish!r} is not one of {sorted(_STOP_REASONS)}"
    raise WireFormatError(msg)


def _usage(body: Mapping[str, object], /) -> TokenUsage | None:
    """Read the token accounting, including the reasoning share of the output.

    Parameters
    ----------
    body
        The decoded top-level object.

    Returns
    -------
    TokenUsage | None
        ``None`` when the body reports no ``usage`` at all, which the domain already models
        as "the provider reported nothing". A ``usage`` that is present and unreadable is
        *not* silently reduced to ``None``: fabricating ``input_tokens=0`` would be a lie
        the caller cannot detect.

    Raises
    ------
    WireFormatError
        ``usage`` is present and is not an object, lacks integer ``prompt_tokens`` or
        ``completion_tokens``, or reports counts the domain refuses -- a negative total, or a
        reasoning share larger than the output it is part of.
    """
    raw = body.get("usage")
    if raw is None:
        return None
    reported = _as_object(raw, what="usage")
    prompt = reported.get("prompt_tokens")
    completion = reported.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        msg = f"{_MISSING_TOKENS}: {reported!r}"
        raise WireFormatError(msg)
    try:
        usage = TokenUsage(
            input_tokens=prompt,
            output_tokens=completion,
            reasoning_tokens=_reasoning_tokens(reported),
        )
    except ValidationError as exc:
        msg = f"usage {reported!r} is not valid token accounting: {exc}"
        raise WireFormatError(msg) from exc
    return usage


def _reasoning_tokens(usage: Mapping[str, object], /) -> int | None:
    """Read the reasoning share out of ``completion_tokens_details``.

    Parameters
    ----------
    usage
        The response's ``usage`` object.

    Returns
    -------
    int | None
        The reasoning token count, or ``None`` when the provider reported no split -- which
        the domain documents as "no split reported", never as "did no reasoning".

    Raises
    ------
    WireFormatError
        ``reasoning_tokens`` is present and is not an integer.
    """
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return None
    reasoning = details.get("reasoning_tokens")
    if reasoning is None or isinstance(reasoning, int):
        return reasoning
    msg = (
        f"completion_tokens_details.reasoning_tokens is a "
        f"{type(reasoning).__name__}, not an integer"
    )
    raise WireFormatError(msg)


def _optional_str(raw: object, /) -> str | None:
    """Accept a non-empty string and reject everything else, without raising.

    Parameters
    ----------
    raw
        A value read out of the response body.

    Returns
    -------
    str | None
        ``raw`` when it is a non-empty string, else ``None``. Used only for the served
        identity, which is a diagnostic: a provider that stops reporting it must not turn a
        good transcription into a failure.
    """
    return raw if isinstance(raw, str) and raw else None


def _as_object(raw: object, /, *, what: str) -> Mapping[str, object]:
    """Narrow one decoded JSON value to an object, or refuse the whole body.

    The single place this module turns an untrusted decoded value into something it will
    index. Consolidated rather than repeated per field so every "not an object" refusal reads
    the same way, and so the one cast this narrowing needs exists once: ``isinstance(x, dict)``
    proves the value is a mapping but says nothing about its key type, and a JSON object's
    keys are strings by construction.

    Parameters
    ----------
    raw
        A value read out of the response body, or ``None`` when the key was absent.
    what
        Dotted path of the value, for the failure message.

    Returns
    -------
    Mapping[str, object]
        ``raw`` as a mapping.

    Raises
    ------
    WireFormatError
        ``raw`` is not a JSON object.
    """
    if not isinstance(raw, dict):
        msg = f"{what} is a {type(raw).__name__}, not an object"
        raise WireFormatError(msg)
    return cast("Mapping[str, object]", raw)
