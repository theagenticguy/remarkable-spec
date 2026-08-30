"""The Bedrock binding of :class:`~rmspec.domain.ports.ocr.VisionLanguageModel`.

One class, assembled from the two halves either of which would be untestable alone:
:mod:`rmspec.ocr._openai_wire` owns the envelope and holds no client, and
:mod:`rmspec.ocr._bedrock` owns the SDK and stops every provider exception. This module is the
only place that knows both, and the only place that knows a model id.

The single replacement for the legacy tree's three copies of the Bedrock vision call
(``_invoke_bedrock_vision`` twice, ``_invoke_annotation_analysis`` once) and its four hardcoded
model ids. **Model ids are constructor arguments, never constants here.** The only ranking any
measurement supports is reasoning spend on one identical task -- luna 48, sol 63, terra 252
reasoning tokens -- which is one data point about one page, so "luna reads pages and terra
merges them" is a composition decision the container makes and revises, not a fact this module
should encode.

Truncation is data, and the reconciliation is deliberate
-------------------------------------------------------
``finish_reason == "length"`` **with** content becomes :attr:`StopReason.OUTPUT_LIMIT` on a
returned completion. It is not raised. The Bedrock contract's own note says to treat ``length``
as an error, and :meth:`VisionLanguageModel.complete` says truncation is data the caller
decides on; those read like a contradiction and are not one, because they are answers to two
different questions:

- *May a truncated body be reported as a success?* No -- and it is not. It arrives with
  ``stop_reason`` set and :attr:`VisionCompletion.is_complete` ``False``, which is a fact the
  caller must branch on rather than a field it can overlook.
- *May a truncated body be cached?* No -- and the app is where that is enforced: it writes a
  cache row only for a completion whose ``is_complete`` is ``True``. A half-transcribed page is
  never stored, which is what "treat length as an error" was protecting.

Raising instead would move the decision out of the caller's hands and break the port for the
one caller that legitimately accepts a partial body: diagram extraction, whose legacy call site
did exactly that. What must never happen is the third case -- ``content is None`` -- and that
is refused unconditionally by :func:`rmspec.ocr._openai_wire.decode_body` and surfaces here as
:class:`~rmspec.domain.errors.ModelResponseMalformed`.

``Decoding.temperature`` is not honoured by this binding
--------------------------------------------------------
Stated here as well as in :mod:`rmspec.ocr._openai_wire`, because it is a real limitation of
this class and not an implementation note: no measurement shows what temperature field these
profiles accept, so none is sent, and every call runs at the profile's own default sampling
temperature. The value still changes :meth:`VisionRequest.digest`, so two requests differing
only in temperature are two cache rows -- honest about being different requests, and honest
that this binding will answer them the same way.

Where the served model identity goes, and why not into the fingerprint
---------------------------------------------------------------------
The response reports ``model`` and ``system_fingerprint``, and they are the only signal that
the model served behind a stable inference profile id changed underneath this binding -- a
change that makes every cache row written before it silently stale.

They cannot go into :attr:`fingerprint`. That value is contractually stable for the process
lifetime and must be known before the first call, and these are not known until a call has been
paid for; folding them in would make the fingerprint change mid-process, which is the one thing
the port forbids of it. They must also not go into a cache key by any other route, for the same
reason -- a key component nobody can compute before the first call cannot be looked up.

Discarding them would throw the drift signal away, so they are neither. They are decoded into
:class:`~rmspec.ocr._openai_wire.WireCompletion` and logged, once per completion, on this
module's logger together with the profile id and this binding's own fingerprint. An operator
who sees the served identity change knows the rows keyed on the surrounding fingerprint are
suspect; a stateful in-process comparison was rejected because it would need a lock to serve a
diagnostic, and would forget everything on restart while the log line persists.

Reasoning text
--------------
:attr:`VisionCompletion.reasoning` is always ``None``. The envelope reports reasoning as a
*token count*, never as text, so there is no reasoning content to return -- and the count is
not silently dropped: it travels as
:attr:`~rmspec.domain.ports.ocr.TokenUsage.reasoning_tokens`, which is what makes the
budget-eaten-by-reasoning failure visible to a caller who only sees numbers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import ModelResponseMalformed
from rmspec.domain.ports.ocr import VisionCompletion
from rmspec.ocr import _bedrock, _openai_wire

if TYPE_CHECKING:
    from rmspec.domain.ports.ocr import VisionRequest
    from rmspec.ocr._bedrock import BedrockRuntimeClient

__all__ = ["ADAPTER_REVISION", "FINGERPRINT_TAG", "BedrockOpenAiVisionModel"]

_LOGGER = logging.getLogger(__name__)

ADAPTER_REVISION: Final = "1"
"""Revision of this class's own request-and-response behaviour.

A constructor default rather than a hardcoded component, so a caller can invalidate this
binding's cache rows without editing the package -- and so the suite can prove the fingerprint
moves when it changes.
"""

FINGERPRINT_TAG: Final = "rmspec.ocr.bedrock-openai.fingerprint.v1"
"""Tag folded in first, so no other adapter's fingerprint scheme can collide with this one."""

_ADAPTER_FIXED_SETTINGS: Final = "temperature=unsent;stream=absent;model_in_body=absent"
"""The settings this binding fixes rather than taking from the request.

Part of the fingerprint because they change what is sent for a given
:class:`~rmspec.domain.ports.ocr.VisionRequest`, which is exactly the contract's trigger for
the fingerprint to move. Sending a temperature one day is therefore a cache miss by
construction, not something a reviewer has to remember to bump.
"""

_EMPTY_ARGUMENT = "must not be empty: it is a component of this binding's fingerprint"


class BedrockOpenAiVisionModel:
    """Run one multimodal completion against an OpenAI-envelope model on Bedrock.

    Binds :class:`~rmspec.domain.ports.ocr.VisionLanguageModel`. Scope: APP -- one client and
    one model binding per process, every call stateless.

    Parameters
    ----------
    client
        The ``bedrock-runtime`` client, injected. Positional-only, and never built here: the
        composition root builds it, which is what lets the whole suite drive this class with a
        three-line stub and never construct an AWS client. Deliberately **not** part of
        :attr:`fingerprint` -- see the notes below.
    model_id
        The inference profile id, e.g. ``"global.openai.gpt-5.6-luna"``. Must be a profile id:
        the measured models are ``INFERENCE_PROFILE`` only, so a bare ``openai.gpt-5.6-*`` will
        not invoke and comes back as
        :class:`~rmspec.domain.errors.ModelAccessDenied`.
    region
        The region the client was built for. Not a port parameter and never read back off an
        error -- it is folded into :attr:`fingerprint` and spent as prose in the remediation
        this adapter authors.
    revision
        This binding's revision, defaulting to :data:`ADAPTER_REVISION`. Bump it to invalidate
        every cache row this binding wrote.

    Raises
    ------
    ValueError
        Any of ``model_id``, ``region`` or ``revision`` is empty. A composition-time
        programming error rather than a port failure, and refused here because an empty
        component would produce a perfectly valid-looking fingerprint for a binding that cannot
        work.

    Notes
    -----
    Every attribute is set once in the constructor and never reassigned, so one instance
    tolerates concurrent :meth:`complete` calls to whatever extent the injected client does --
    boto3 clients are documented as safe to share between threads for calls like this one.

    The client is excluded from :attr:`fingerprint` on purpose, and it is the only constructor
    argument that is. Two clients built for the same region and profile are the same binding,
    so hashing object identity would give two processes -- or one process that rebuilt its
    client -- different fingerprints for identical work, and every row either wrote would be
    unreachable by the other. That is the same cache defect a hand-written constant causes, in
    the opposite direction.
    """

    def __init__(
        self,
        client: BedrockRuntimeClient,
        /,
        *,
        model_id: str,
        region: str,
        revision: str = ADAPTER_REVISION,
    ) -> None:
        for name, value in (("model_id", model_id), ("region", region), ("revision", revision)):
            if not value:
                msg = f"{name} {_EMPTY_ARGUMENT}"
                raise ValueError(msg)
        self._client = client
        self._model_id = model_id
        self._region = region
        self._fingerprint = _fingerprint_of(
            model_id=model_id,
            region=region,
            revision=revision,
        )

    @property
    def fingerprint(self) -> str:
        """Return the opaque identity of this model binding.

        Derived, never declared. It is a SHA-256 over :data:`FINGERPRINT_TAG`, the service
        name, the inference profile id, the region, this binding's revision, the wire envelope
        and its revision, and the settings this adapter fixes rather than taking from the
        request. A hand-written constant is the one implementation of this property that makes
        every stale cache row look fresh, so there is nothing constant in it: change any input
        and every row keyed on the old value becomes unreachable instead of being reinterpreted.

        Returns
        -------
        str
            Lowercase hex SHA-256, computed once in the constructor and therefore stable for
            the lifetime of the process and known before the first call -- both of which the
            port requires and neither of which a value read out of a response could satisfy.
        """
        return self._fingerprint

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Run one multimodal completion.

        Parameters
        ----------
        request
            The request, carrying prompt text, an optional system turn that becomes the
            ``developer`` message, already-rendered image bytes, and decoding settings. Nothing
            is pre-screened against a published ceiling: this binding has no honest set of them,
            so a request the model will not accept surfaces as the provider's own answer.

        Returns
        -------
        VisionCompletion
            The answer, built with :meth:`VisionCompletion.answering` so ``request_digest`` and
            ``model_fingerprint`` are derived from ``request`` and :attr:`fingerprint` rather
            than copied -- an adapter that hand-fills a cache-key component can transpose it
            once and poison every row keyed on it. ``stop_reason`` is data, including for a
            truncated body; ``reasoning`` is always ``None`` because this envelope reports
            reasoning as a token count and not as text.

        Raises
        ------
        ModelUnavailable
            The endpoint could not be reached, or the service reported an outage.
        ModelAccessDenied
            The caller is not entitled to this profile id, the id is unknown, or no usable
            credentials were found.
        ModelThrottled
            A rate or quota limit survived the SDK's own retries.
        ModelRejectedRequest
            The provider refused the request itself -- payload too large, unsupported image, a
            budget above the model's ceiling.
        ModelResponseMalformed
            A well-formed exchange whose body is not a completion. Includes the measured
            silent-failure case: a ``content`` of ``None``, which arrives with no exception from
            the SDK when the whole output budget went to latent reasoning, and which must never
            be read as an empty transcription.
        """
        raw = _bedrock.invoke(
            self._client,
            model_id=self._model_id,
            region=self._region,
            payload=_openai_wire.encode_request(request),
        )
        try:
            decoded = _openai_wire.decode_body(raw)
        except _openai_wire.WireFormatError as exc:
            raise ModelResponseMalformed(model_id=self._model_id, detail=exc.detail) from exc
        _LOGGER.info(
            "bedrock completion: profile=%s region=%s served_model=%s system_fingerprint=%s "
            "binding_fingerprint=%s stop_reason=%s",
            self._model_id,
            self._region,
            decoded.served_model,
            decoded.served_fingerprint,
            self._fingerprint,
            decoded.stop_reason.value,
        )
        return VisionCompletion.answering(
            request,
            fingerprint=self._fingerprint,
            text=decoded.text,
            stop_reason=decoded.stop_reason,
            reasoning=None,
            usage=decoded.usage,
        )


def _fingerprint_of(*, model_id: str, region: str, revision: str) -> str:
    """Fold this binding's identity into one opaque digest.

    The components are hashed as a canonical JSON array rather than joined on a separator.
    Three of them -- the profile id, the region and the revision -- are open strings a caller
    supplies, so a separator-joined stream is ambiguous the moment one of them contains the
    separator: ``model_id="a|b", region="c"`` and ``model_id="a", region="b|c"`` would be one
    fingerprint for two bindings, which is the digest-framing defect this workspace already
    fixed once in its cache keys. JSON escaping makes the stream parseable back into exactly one
    component list, which is the same property length-framing gives.

    It does not call :func:`rmspec.domain._digest.digest_of`, deliberately. That function is
    package-private to the domain, and unlike ``RasterImage.digest`` this value is never
    compared against anything the domain computes -- it is contractually opaque and wholly
    adapter-authored, so there is no second body that has to agree with it byte for byte.
    :mod:`rmspec.formats.fingerprint` records the same decision for the same kind of value.

    Parameters
    ----------
    model_id
        The inference profile id.
    region
        The region the client was built for.
    revision
        This binding's revision.

    Returns
    -------
    str
        Lowercase hex SHA-256. Differs whenever any component differs, which is what the
        contract suite asserts by varying one constructor argument at a time.
    """
    components = [
        FINGERPRINT_TAG,
        _bedrock.SERVICE,
        model_id,
        region,
        revision,
        _openai_wire.ENVELOPE,
        _openai_wire.WIRE_REVISION,
        _ADAPTER_FIXED_SETTINGS,
    ]
    canonical = json.dumps(components, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
