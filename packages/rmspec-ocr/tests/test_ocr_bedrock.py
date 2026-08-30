"""The botocore translation seam, driven by constructing exceptions.

No test here builds a client, opens a socket, or resolves a credential. Every provider failure
is constructed directly, which is the only way to cover the ones that cannot be provoked on
purpose -- a real ``InternalServerException``, a DNS failure inside a build -- and the reason
:func:`rmspec.ocr._bedrock.translated` returns an error instead of raising one.

``boto3`` is imported here for exactly one assertion -- that
:data:`rmspec.ocr._bedrock.DEFAULT_FACTORY` *is* ``boto3.client`` -- and is never called.
Importing the SDK builds nothing; calling its factory would build a ``bedrock-runtime`` client,
which is the one thing this suite may not do.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import boto3
import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
    UnknownServiceError,
)

from rmspec.domain.errors import (
    ModelAccessDenied,
    ModelError,
    ModelRejectedRequest,
    ModelResponseMalformed,
    ModelThrottled,
    ModelUnavailable,
)
from rmspec.ocr import _bedrock

if TYPE_CHECKING:
    from collections.abc import Mapping

MODEL_ID = "global.openai.gpt-5.6-luna"
REGION = "us-east-1"
ENDPOINT = "https://bedrock-runtime.us-east-1.amazonaws.com"
BODY = b'{"choices":[]}'


class StubClient:
    """The whole client surface this package uses, as a recording stand-in."""

    def __init__(
        self,
        *,
        response: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response: Mapping[str, object] = {} if response is None else response
        self.error = error
        self.calls: list[Mapping[str, str | bytes]] = []

    def invoke_model(self, /, **request: str | bytes) -> Mapping[str, object]:
        """Record the keywords it was called with, then answer or fail as configured."""
        self.calls.append(dict(request))
        if self.error is not None:
            raise self.error
        return self.response


class Readable:
    """A response stream that answers with whatever it was built with."""

    def __init__(self, value: object) -> None:
        self.value = value

    def read(self) -> object:
        """Return the configured value, standing in for ``StreamingBody.read``."""
        return self.value


class Unreadable:
    """A response stream whose read fails the way a torn HTTP response does."""

    def read(self) -> object:
        """Raise the transport error botocore raises when the body stalls."""
        raise ReadTimeoutError(endpoint_url=ENDPOINT)


class RecordingFactory:
    """A stand-in for ``boto3.client`` that records how it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.client = StubClient()

    def __call__(self, service_name: str, /, *, region_name: str) -> _bedrock.BedrockRuntimeClient:
        """Record the service and region, and hand back a stub rather than a real client."""
        self.calls.append((service_name, region_name))
        return self.client


def client_error(
    code: object,
    *,
    status: object = None,
    headers: Mapping[str, object] | None = None,
    message: object = "boom",
) -> ClientError:
    metadata: dict[str, object] = {}
    if status is not None:
        metadata["HTTPStatusCode"] = status
    if headers is not None:
        metadata["HTTPHeaders"] = headers
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": metadata},
        "InvokeModel",
    )


def translate(exc: ClientError | UnknownServiceError | NoCredentialsError) -> ModelError:
    return _bedrock.translated(exc, model_id=MODEL_ID, region=REGION)


# --------------------------------------------------------------------------- construction


def test_the_endpoint_names_the_regional_bedrock_runtime_host():
    assert _bedrock.endpoint_for(REGION) == ENDPOINT


def test_build_client_asks_the_injected_factory_for_the_bedrock_runtime_service():
    factory = RecordingFactory()
    built = _bedrock.build_client(factory, region="eu-west-1")
    assert factory.calls == [("bedrock-runtime", "eu-west-1")]
    assert built is factory.client


def test_the_default_factory_is_the_sdks_own_and_is_asserted_by_identity_not_by_calling_it():
    assert _bedrock.DEFAULT_FACTORY is boto3.client


def test_build_client_really_defaults_to_that_factory_and_not_merely_publishes_it():
    default = inspect.signature(_bedrock.build_client).parameters["factory"].default
    assert default is boto3.client


def test_the_service_name_is_the_one_the_fingerprint_folds_in():
    assert _bedrock.SERVICE == "bedrock-runtime"


# --------------------------------------------------------------------------- code families


def test_no_service_code_belongs_to_two_families():
    families = [
        _bedrock.ACCESS_CODES,
        _bedrock.THROTTLE_CODES,
        _bedrock.REJECT_CODES,
        _bedrock.UNAVAILABLE_CODES,
    ]
    seen: set[str] = set()
    for family in families:
        assert not (seen & family), f"{sorted(seen & family)} is claimed twice"
        seen |= family


@pytest.mark.parametrize("code", sorted(_bedrock.ACCESS_CODES))
def test_an_entitlement_code_becomes_model_access_denied(code: str):
    error = translate(client_error(code, status=403))
    assert isinstance(error, ModelAccessDenied)
    assert error.model_id == MODEL_ID


def test_the_remediation_carries_the_region_the_error_no_longer_has_a_field_for():
    error = translate(client_error("AccessDeniedException", status=403))
    assert error.remediation == f"enable {MODEL_ID} in {REGION} in the Bedrock console"
    assert not hasattr(error, "region")


def test_an_unknown_profile_id_is_an_entitlement_failure_and_not_a_throttle():
    error = translate(client_error("ResourceNotFoundException", status=404))
    assert isinstance(error, ModelAccessDenied)


@pytest.mark.parametrize("code", sorted(_bedrock.THROTTLE_CODES))
def test_a_rate_or_quota_code_becomes_model_throttled(code: str):
    error = translate(client_error(code, status=429))
    assert isinstance(error, ModelThrottled)
    assert error.model_id == MODEL_ID


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (None, None),
        ({}, None),
        ({"retry-after": "3.5"}, 3.5),
        ({"x-amzn-retry-after": "2"}, 2.0),
        ({"retry-after": "soon"}, None),
        ({"retry-after": 4}, None),
    ],
)
def test_the_throttle_delay_comes_from_the_response_or_is_left_unstated(
    headers: Mapping[str, object] | None,
    expected: float | None,
):
    error = translate(client_error("ThrottlingException", status=429, headers=headers))
    assert isinstance(error, ModelThrottled)
    assert error.retry_after_s == expected


@pytest.mark.parametrize("code", sorted(_bedrock.REJECT_CODES))
def test_a_refused_request_code_becomes_model_rejected_request(code: str):
    error = translate(client_error(code, status=400, message="max_tokens is unknown"))
    assert isinstance(error, ModelRejectedRequest)
    assert error.detail == f"{code}: max_tokens is unknown"


@pytest.mark.parametrize("code", sorted(_bedrock.UNAVAILABLE_CODES))
def test_an_outage_code_becomes_a_retryable_model_unavailable(code: str):
    error = translate(client_error(code, status=500))
    assert isinstance(error, ModelUnavailable)
    assert error.endpoint == ENDPOINT
    assert error.retryable is True


@pytest.mark.parametrize("status", [500, 502, 503, 529])
def test_an_unrecognised_code_on_a_server_status_is_treated_as_an_outage(status: int):
    error = translate(client_error("SomethingNewException", status=status))
    assert isinstance(error, ModelUnavailable)
    assert error.retryable is True


@pytest.mark.parametrize("status", [400, 404, 409, None, "500"])
def test_an_unrecognised_code_without_a_server_status_is_treated_as_a_refusal(status: object):
    error = translate(client_error("SomethingNewException", status=status))
    assert isinstance(error, ModelRejectedRequest)


def test_a_response_with_no_error_section_is_still_translated_rather_than_crashing():
    error = _bedrock.translated(ClientError({}, "InvokeModel"), model_id=MODEL_ID, region=REGION)
    assert isinstance(error, ModelRejectedRequest)
    assert error.detail.startswith("unknown error: ")


def test_a_non_string_code_and_message_fall_back_instead_of_being_stringified():
    error = translate(client_error(7, status=400, message=None))
    assert isinstance(error, ModelRejectedRequest)
    assert error.detail.startswith("unknown error: An error occurred")


# --------------------------------------------------------------------------- transport


@pytest.mark.parametrize(
    "exc",
    [
        EndpointConnectionError(endpoint_url=ENDPOINT),
        ConnectTimeoutError(endpoint_url=ENDPOINT),
        ReadTimeoutError(endpoint_url=ENDPOINT),
        ConnectionClosedError(endpoint_url=ENDPOINT),
    ],
)
def test_a_transport_failure_is_a_retryable_outage_and_not_a_throttle(exc: ClientError):
    error = _bedrock.translated(exc, model_id=MODEL_ID, region=REGION)
    assert isinstance(error, ModelUnavailable)
    assert error.endpoint == ENDPOINT
    assert error.retryable is True
    assert error.detail == str(exc)


def test_a_botocore_failure_that_cannot_come_back_is_reported_as_not_retryable():
    error = translate(UnknownServiceError(service_name="nope", known_service_names="s3"))
    assert isinstance(error, ModelUnavailable)
    assert error.retryable is False


@pytest.mark.parametrize(
    "exc",
    [
        NoCredentialsError(),
        PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY"),
    ],
)
def test_a_credential_failure_is_an_entitlement_problem_with_its_own_remediation(
    exc: NoCredentialsError,
):
    error = _bedrock.translated(exc, model_id=MODEL_ID, region=REGION)
    assert isinstance(error, ModelAccessDenied)
    assert error.remediation == f"configure AWS credentials with access to {MODEL_ID} in {REGION}"


def test_an_sdk_that_refuses_to_serialise_the_call_has_rejected_the_request():
    exc = ParamValidationError(report="modelId is required")
    error = _bedrock.translated(exc, model_id=MODEL_ID, region=REGION)
    assert isinstance(error, ModelRejectedRequest)
    assert error.detail == str(exc)


# --------------------------------------------------------------------------- invoke


def test_invoke_sends_exactly_modelid_and_body_and_returns_the_raw_bytes():
    client = StubClient(response={"body": Readable(BODY)})
    assert _bedrock.invoke(client, model_id=MODEL_ID, region=REGION, payload=b"{}") == BODY
    assert client.calls == [{"modelId": MODEL_ID, "body": b"{}"}]


def test_invoke_raises_the_translation_of_a_service_error():
    client = StubClient(error=client_error("ValidationException", status=400))
    with pytest.raises(ModelRejectedRequest) as caught:
        _bedrock.invoke(client, model_id=MODEL_ID, region=REGION, payload=b"{}")
    assert isinstance(caught.value.__cause__, ClientError)


def test_invoke_raises_the_translation_of_a_transport_error():
    client = StubClient(error=EndpointConnectionError(endpoint_url=ENDPOINT))
    with pytest.raises(ModelUnavailable) as caught:
        _bedrock.invoke(client, model_id=MODEL_ID, region=REGION, payload=b"{}")
    assert caught.value.retryable is True


def test_invoke_translates_a_transport_error_raised_while_the_body_is_being_read():
    client = StubClient(response={"body": Unreadable()})
    with pytest.raises(ModelUnavailable) as caught:
        _bedrock.invoke(client, model_id=MODEL_ID, region=REGION, payload=b"{}")
    assert isinstance(caught.value.__cause__, ReadTimeoutError)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({}, "no readable body"),
        ({"body": None}, "no readable body"),
        ({"body": "already a string"}, "no readable body"),
        ({"body": Readable("text, not bytes")}, "read as a str, not bytes"),
        ({"body": Readable(None)}, "read as a NoneType, not bytes"),
    ],
)
def test_a_response_envelope_this_adapter_cannot_read_is_a_malformed_response(
    response: Mapping[str, object],
    expected: str,
):
    client = StubClient(response=response)
    with pytest.raises(ModelResponseMalformed) as caught:
        _bedrock.invoke(client, model_id=MODEL_ID, region=REGION, payload=b"{}")
    assert expected in caught.value.detail
    assert caught.value.model_id == MODEL_ID
