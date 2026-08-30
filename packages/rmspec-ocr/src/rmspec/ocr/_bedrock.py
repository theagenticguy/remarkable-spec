"""The one place ``boto3`` and ``botocore`` are spoken, and the one place they stop.

Two jobs, deliberately together because they are the two halves of owning a provider SDK:
building the client, and translating everything the SDK can throw into the five model errors
:meth:`rmspec.domain.ports.ocr.VisionLanguageModel.complete` documents. Nothing above this
module sees a ``ClientError``, an ``EndpointConnectionError`` or a ``StreamingBody``.

Why the translation is a function of the exception
-------------------------------------------------
:func:`translated` takes an exception and returns an error -- it does not raise, and it does
no I/O. That is what makes the whole botocore-facing surface of this package testable by
constructing exceptions, with no client, no credential chain and no network, which is also the
only way to cover the codes that are expensive or impossible to provoke on purpose (a genuine
``InternalServerException``, a DNS failure inside a build). :func:`invoke` is the thin
imperative wrapper that raises what it returns, and it holds the only ``except`` clause in
this package that names a third-party class.

Why the client factory is injectable *and* has a default
--------------------------------------------------------
:func:`build_client` takes the factory as an argument, defaulting to
:data:`DEFAULT_FACTORY`, which is ``boto3.client``. Both halves of that are load-bearing.

The default is what makes the declared dependency a true one: ``boto3`` is in this
distribution's ``[project.dependencies]``, and a package that declares an SDK and imports it
nowhere is telling the same lie the legacy tree told about ``pillow`` -- a ``[render]`` extra
that shipped it and a docstring that promised a fallback using it, with no import site
anywhere. ``tests/architecture/test_declared_dependencies.py`` exists because of that, and it
is right to fail on it.

The argument is what makes the seam testable: no test in this package may construct a
``bedrock-runtime`` client, so the whole suite passes a recording stub and asserts that this
module asks for the right service under the right keyword -- ``("bedrock-runtime",
region_name=...)``. A typo in either is a lint-clean, type-clean runtime failure at
composition, and this is the only layer that can catch it before an account does.

The default is bound once, at import, as a module constant rather than resolved inside the
function with an ``if factory is None`` branch. That is deliberate: a branch would have to be
*taken* to be covered, and taking it means calling ``boto3.client`` for real. With a constant
there is no branch to cover -- the suite asserts ``DEFAULT_FACTORY is boto3.client`` by
identity and never invokes it, so the wiring is proven without a credential chain, a network
stack, or an account.

``boto3`` is legal here only because the ban has a matching exemption. The workspace's
``flake8-tidy-imports.banned-api`` names ``rmspec-ocr`` as ``boto3``'s owner, and
``per-file-ignores`` grants this package ``TID251`` alongside ``rmspec-persistence`` for
``sqlite3`` and ``rmspec-device`` for ``paramiko``. Without that pairing the ban would be a
wall rather than a boundary: the layer that says who owns a dependency would forbid the owner
from using it.

The five errors, and the reasoning behind each edge
---------------------------------------------------
``ModelUnavailable``
    Refused connection, DNS failure, timeout, HTTP 503/529, and Bedrock's own
    ``ModelTimeoutException`` / ``ServiceUnavailableException`` / ``InternalServerException``.
    Carries ``retryable``, which is ``True`` for a transport failure and for the service codes
    above: retrying an outage can work, and the CLI's retry-once branch reads this flag rather
    than parsing prose.
``ModelAccessDenied``
    ``AccessDeniedException`` and an unknown model id (``ResourceNotFoundException``), plus the
    credential failures botocore raises before a request is ever signed. It no longer takes a
    ``region``, so this adapter supplies the deployment axis as prose in the remediation it
    authors -- ``f"enable {model_id} in {region} in the Bedrock console"`` -- which is where a
    human reads it and where nothing can read it back as a field.
``ModelThrottled``
    ``ThrottlingException`` and the quota codes. Carries ``retry_after_s`` when the response
    supplied a ``Retry-After`` header, so a retry policy is a decision made from data.
``ModelRejectedRequest``
    ``ValidationException`` -- which is what a payload above the ceiling, an unsupported image,
    or a budget above the model's maximum comes back as -- plus botocore's own
    ``ParamValidationError``, since an SDK that refuses to serialise the call has rejected the
    request just as firmly as the service would have.
``ModelResponseMalformed``
    A well-formed exchange whose envelope this adapter cannot read: no ``body`` in the
    response, or a ``body`` that does not read as bytes.

An unrecognised ``ClientError`` code is not guessed at. It is split on the HTTP status the
response reports: ``5xx`` (and the two overload statuses) is an outage and retryable, anything
else is a rejected request. Reporting an unknown 5xx as "rejected" would teach a caller to
stop retrying something that would have succeeded; reporting an unknown 4xx as "unavailable"
would send it into a retry loop that cannot succeed.

Retries
-------
This adapter adds no retry loop of its own: botocore already retries the throttling and 5xx
families on its own schedule, so a second loop here would multiply the wait and hide the first
one. ``ModelThrottled`` therefore means "still throttled after the SDK's retries", which is
what the domain error's docstring already says. Configuring that schedule would mean a
``botocore.config.Config`` object, which is a deliberate non-goal until a measurement says the
default is wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    HTTPClientError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
)
from botocore.exceptions import ConnectionError as BotocoreConnectionError

from rmspec.domain.errors import (
    ModelAccessDenied,
    ModelError,
    ModelRejectedRequest,
    ModelResponseMalformed,
    ModelThrottled,
    ModelUnavailable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ACCESS_CODES",
    "DEFAULT_FACTORY",
    "REJECT_CODES",
    "SERVICE",
    "THROTTLE_CODES",
    "UNAVAILABLE_CODES",
    "UNAVAILABLE_STATUSES",
    "BedrockRuntimeClient",
    "ClientFactory",
    "ResponseStream",
    "build_client",
    "endpoint_for",
    "invoke",
    "translated",
]

SERVICE: Final = "bedrock-runtime"
"""The boto3 service name, and part of the model binding's fingerprint."""

UNAVAILABLE_CODES: Final = frozenset(
    {
        "InternalServerException",
        "ModelNotReadyException",
        "ModelTimeoutException",
        "ServiceUnavailableException",
    }
)
"""Service codes that mean the model did not answer, and that retrying could help.

``ModelNotReadyException`` joins the three the port names because it is the same statement --
the binding exists and is not answering yet -- and reporting it as a rejected request would
teach the caller to stop trying.
"""

THROTTLE_CODES: Final = frozenset(
    {
        "ServiceQuotaExceededException",
        "ThrottlingException",
        "TooManyRequestsException",
    }
)
"""Rate and quota codes. Retryable by definition, which is why they are not an outage."""

ACCESS_CODES: Final = frozenset(
    {
        "AccessDeniedException",
        "ExpiredTokenException",
        "ResourceNotFoundException",
        "UnrecognizedClientException",
    }
)
"""Entitlement codes, including the unknown-model-id case.

``ResourceNotFoundException`` is how a profile id that does not exist -- or a bare
``openai.gpt-5.6-*`` id for a model that is ``INFERENCE_PROFILE`` only -- comes back, and it is
an entitlement problem in the sense that matters: a permanent misconfiguration to report,
never a throttle to retry.
"""

REJECT_CODES: Final = frozenset(
    {
        "RequestEntityTooLargeException",
        "SerializationException",
        "ValidationException",
    }
)
"""Codes that mean the request itself was refused.

``ValidationException`` is the measured answer to an unknown body field
(``anthropic_version``), an unsupported ``reasoning_effort`` value, and a budget above the
model's ceiling. It is *not* a content refusal: a refusal is a stop reason on a successful
completion.
"""

UNAVAILABLE_STATUSES: Final = frozenset({503, 529})
"""HTTP statuses that mean an outage or overload even when the code is unrecognised."""

_SERVER_ERROR_FLOOR: Final = 500
"""Status at or above which an unrecognised code reads as an outage, not a refusal."""

_RETRY_AFTER_HEADERS: Final = ("retry-after", "x-amzn-retry-after")
"""Headers a throttle response may carry its delay in, lowercase as botocore reports them."""


@runtime_checkable
class ResponseStream(Protocol):
    """Structural view of the streaming body ``invoke_model`` returns.

    Structural, and ``runtime_checkable``, because the response is untrusted wire shape: this
    package holds no botocore type and must be able to say "that was not a readable body"
    rather than raise ``AttributeError`` from inside a library.

    Notes
    -----
    :meth:`read` is declared as returning ``object`` rather than ``bytes`` on purpose. An SDK
    method's declared return type is a claim, and the point of this seam is that the claim is
    checked -- typing it ``bytes`` would make the check that catches a non-``bytes`` body look
    like dead code to a reader and to a coverage report.
    """

    def read(self) -> object:
        """Read the whole body.

        Returns
        -------
        object
            The body's bytes, if the SDK behaved.
        """
        ...


class BedrockRuntimeClient(Protocol):
    """Structural view of the one client method this package calls.

    A protocol rather than the concrete client type, because the client is a constructor
    argument of every adapter here and a three-line stub must satisfy it -- no test in this
    package may construct a ``bedrock-runtime`` client, so the type that describes one has to
    be structural.

    Notes
    -----
    The parameters are typed as ``**request`` rather than spelled out because the SDK's keyword
    is ``modelId``, and a camelCase parameter *name* in a definition is a lint failure this
    repo does not exempt. The call site in :func:`invoke` still passes ``modelId=``, which is
    the spelling Bedrock requires, and the stub in the suite asserts the exact keywords it
    received -- a stricter check of the wire name than an annotation could make.
    """

    def invoke_model(self, /, **request: str | bytes) -> Mapping[str, object]:
        """Invoke a model synchronously.

        Returns
        -------
        Mapping[str, object]
            The response envelope, whose ``body`` is a readable stream.
        """
        ...


class ClientFactory(Protocol):
    """Structural view of ``boto3.client``, so a recording stub can stand in for it.

    Named as a type rather than left implicit because it is the seam that keeps
    :func:`build_client` testable: the suite passes something with this shape and asserts the
    service name and the ``region_name`` keyword, and never builds an AWS client to do it.
    """

    def __call__(self, service_name: str, /, *, region_name: str) -> BedrockRuntimeClient:
        """Build a client for one service in one region.

        Returns
        -------
        BedrockRuntimeClient
            The client.
        """
        ...


DEFAULT_FACTORY: Final[ClientFactory] = boto3.client
"""``boto3.client``, bound once at import so the default costs no call to cover.

The only reference to ``boto3`` in this package, and the reason the ``boto3`` declared in this
distribution's dependencies is a requirement rather than a claim. Exposed as a constant so a
test can assert this *is* the SDK's factory by identity -- see this module's docstring for why
identity rather than invocation.
"""


def endpoint_for(region: str, /) -> str:
    """Return the regional endpoint this binding talks to, for a failure message.

    Parameters
    ----------
    region
        The AWS region the client was built for.

    Returns
    -------
    str
        The public ``bedrock-runtime`` endpoint URL. Reported by
        :class:`~rmspec.domain.errors.ModelUnavailable` so the message names what could not be
        reached, rather than leaving the reader to guess between the model and the network.
    """
    return f"https://{SERVICE}.{region}.amazonaws.com"


def build_client(
    factory: ClientFactory = DEFAULT_FACTORY, /, *, region: str
) -> BedrockRuntimeClient:
    """Build a ``bedrock-runtime`` client for one region.

    Called by the composition root, never by an adapter method, and never with the default
    factory by a test: a suite that builds a real client has a credential chain, a network stack
    and an account in its dependency set.

    Parameters
    ----------
    factory
        ``boto3.client`` by default, or anything with its shape. Overridable so the suite can
        assert what this function asks for without an AWS client ever existing.
    region
        The AWS region to bind to. It is folded into the model binding's fingerprint, because
        the same inference profile id in two regions is two bindings that can answer
        differently.

    Returns
    -------
    BedrockRuntimeClient
        The client, described structurally so the adapters that take it can also take a stub.
    """
    return factory(SERVICE, region_name=region)


def invoke(
    client: BedrockRuntimeClient,
    /,
    *,
    model_id: str,
    region: str,
    payload: bytes,
) -> bytes:
    """Invoke one model and return its raw response bytes, letting no provider error escape.

    Parameters
    ----------
    client
        The client to call. Injected, so the suite drives this function with a stub.
    model_id
        The inference profile id, passed as ``modelId``. A profile id rather than a bare model
        id because the measured ``global.openai.gpt-5.6-*`` models are ``INFERENCE_PROFILE``
        only and a bare id will not invoke.
    region
        The region the client was built for, used only to name the endpoint and to author the
        remediation prose.
    payload
        The already-serialised request body.

    Returns
    -------
    bytes
        The raw response body, for :func:`rmspec.ocr._openai_wire.decode_body` to read.

    Raises
    ------
    ModelUnavailable
        The endpoint could not be reached, or the service reported an outage.
    ModelAccessDenied
        The caller is not entitled to the model, the model id is unknown, or no usable
        credentials were found.
    ModelThrottled
        A rate or quota limit survived the SDK's own retries.
    ModelRejectedRequest
        The service, or the SDK's own serialiser, refused the request.
    ModelResponseMalformed
        The exchange succeeded and the response envelope carried no readable body.
    """
    try:
        response = client.invoke_model(modelId=model_id, body=payload)
        return _response_bytes(response, model_id=model_id)
    except (ClientError, BotoCoreError) as exc:
        raise translated(exc, model_id=model_id, region=region) from exc


def translated(
    exc: ClientError | BotoCoreError,
    /,
    *,
    model_id: str,
    region: str,
) -> ModelError:
    """Map one provider exception onto the model error the port documents.

    Pure: it inspects the exception and returns an error object. The caller raises it, so this
    function can be driven directly by a test that constructs the exception it wants translated
    -- including the codes that cannot be provoked deliberately.

    Parameters
    ----------
    exc
        The exception botocore raised. A ``ClientError`` carries the service's own code; every
        other ``BotoCoreError`` is a failure that happened before or below the request.
    model_id
        The inference profile id, named by every error this returns.
    region
        The region, used to author the remediation prose and to name the endpoint.

    Returns
    -------
    ModelError
        One of the five, never a base ``ModelError`` and never something the port's ``Raises``
        clause does not name.
    """
    if isinstance(exc, ClientError):
        return _from_service_error(exc, model_id=model_id, region=region)
    return _from_transport_error(exc, model_id=model_id, region=region)


def _from_service_error(exc: ClientError, /, *, model_id: str, region: str) -> ModelError:
    """Translate a modelled service error, which carries a code and an HTTP status.

    Parameters
    ----------
    exc
        The ``ClientError``.
    model_id
        The inference profile id.
    region
        The region.

    Returns
    -------
    ModelError
        The mapped error. An unrecognised code falls back on the HTTP status rather than on a
        guess about which family it belongs to.
    """
    response = _mapping(exc.response)
    error = _mapping(response.get("Error"))
    code = _text(error.get("Code"))
    detail = f"{code or 'unknown error'}: {_text(error.get('Message')) or exc!s}"
    if code in ACCESS_CODES:
        return ModelAccessDenied(
            model_id=model_id,
            remediation=f"enable {model_id} in {region} in the Bedrock console",
        )
    if code in THROTTLE_CODES:
        return ModelThrottled(model_id=model_id, retry_after_s=_retry_after(response))
    if code in REJECT_CODES:
        return ModelRejectedRequest(model_id=model_id, detail=detail)
    status = _mapping(response.get("ResponseMetadata")).get("HTTPStatusCode")
    if code in UNAVAILABLE_CODES or _is_outage(status):
        return ModelUnavailable(endpoint=endpoint_for(region), detail=detail, retryable=True)
    return ModelRejectedRequest(model_id=model_id, detail=detail)


def _from_transport_error(exc: BotoCoreError, /, *, model_id: str, region: str) -> ModelError:
    """Translate a failure that happened before or below the signed request.

    Parameters
    ----------
    exc
        The ``BotoCoreError``.
    model_id
        The inference profile id.
    region
        The region.

    Returns
    -------
    ModelError
        ``ModelAccessDenied`` for a credential failure, ``ModelRejectedRequest`` when the SDK
        refused to serialise the call, and ``ModelUnavailable`` otherwise -- retryable only for
        the connection and HTTP families, since an unreachable host may come back while a
        missing service model never will.
    """
    if isinstance(exc, NoCredentialsError | PartialCredentialsError):
        return ModelAccessDenied(
            model_id=model_id,
            remediation=f"configure AWS credentials with access to {model_id} in {region}",
        )
    if isinstance(exc, ParamValidationError):
        return ModelRejectedRequest(model_id=model_id, detail=str(exc))
    return ModelUnavailable(
        endpoint=endpoint_for(region),
        detail=str(exc),
        retryable=isinstance(exc, BotocoreConnectionError | HTTPClientError),
    )


def _response_bytes(response: Mapping[str, object], /, *, model_id: str) -> bytes:
    """Read the response envelope's streaming body.

    Parameters
    ----------
    response
        The envelope ``invoke_model`` returned.
    model_id
        The inference profile id, named by the error.

    Returns
    -------
    bytes
        The raw body.

    Raises
    ------
    ModelResponseMalformed
        The envelope carried no readable ``body``, or reading it did not produce bytes. Both
        are "a well-formed exchange whose body could not be read as a completion", which is
        exactly what this error is for.
    """
    stream = response.get("body")
    if not isinstance(stream, ResponseStream):
        detail = f"response envelope has no readable body: keys={sorted(map(str, response))}"
        raise ModelResponseMalformed(model_id=model_id, detail=detail)
    raw = stream.read()
    if not isinstance(raw, bytes):
        detail = f"response body read as a {type(raw).__name__}, not bytes"
        raise ModelResponseMalformed(model_id=model_id, detail=detail)
    return raw


def _mapping(raw: object, /) -> Mapping[str, object]:
    """Narrow one value out of an error response to a mapping, without raising.

    Parameters
    ----------
    raw
        A value read off the exception or out of its response.

    Returns
    -------
    Mapping[str, object]
        ``raw`` when it is a mapping, else an empty one. Defensive rather than strict because
        this seam must translate every exception it is handed -- including one a caller built
        by hand with no ``response`` at all -- and a translation seam that can itself raise is
        a seam that lets a provider exception escape after all.
    """
    if isinstance(raw, dict):
        return cast("Mapping[str, object]", raw)
    return {}


def _text(raw: object, /) -> str:
    """Return a string field of an error response, or the empty string.

    Parameters
    ----------
    raw
        The value read out of the response.

    Returns
    -------
    str
        ``raw`` when it is a string, else ``""`` -- so an absent code compares equal to no
        member of any code set and falls through to the status-based split.
    """
    return raw if isinstance(raw, str) else ""


def _is_outage(status: object, /) -> bool:
    """Report whether an HTTP status means the service, not the request, was at fault.

    Parameters
    ----------
    status
        ``ResponseMetadata.HTTPStatusCode``, which may be absent or any type.

    Returns
    -------
    bool
        ``True`` for ``5xx`` and for the overload statuses. An absent status is ``False``: with
        neither a known code nor a status there is nothing to suggest the service was at fault,
        and a retry loop started on that assumption cannot terminate.
    """
    if not isinstance(status, int):
        return False
    return status >= _SERVER_ERROR_FLOOR or status in UNAVAILABLE_STATUSES


def _retry_after(response: Mapping[str, object], /) -> float | None:
    """Read the provider's own retry delay out of a throttle response.

    Parameters
    ----------
    response
        The ``ClientError`` response.

    Returns
    -------
    float | None
        The delay in seconds when the response carried a numeric ``Retry-After``, else
        ``None`` -- which the domain error renders as no delay rather than as zero, so no
        caller reads a guessed sleep as a measured one.
    """
    headers = _mapping(_mapping(response.get("ResponseMetadata")).get("HTTPHeaders"))
    for name in _RETRY_AFTER_HEADERS:
        raw = headers.get(name)
        if isinstance(raw, str):
            try:
                return float(raw)
            except ValueError:
                return None
    return None
