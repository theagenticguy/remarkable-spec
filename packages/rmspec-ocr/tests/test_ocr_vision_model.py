"""The Bedrock vision binding, driven with a stub client and never an AWS one.

The stub is four lines because the client is a constructor argument. That is not a convenience:
a suite that had to build a ``bedrock-runtime`` client would carry a credential chain, a network
stack and an account, and every one of the cases below -- an outage, a null content, a truncated
answer -- would cost a billable call to reproduce, if it could be reproduced at all.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from rmspec.domain.errors import (
    ModelRejectedRequest,
    ModelResponseMalformed,
    ModelUnavailable,
)
from rmspec.domain.ports.ocr import (
    Decoding,
    ImageMedia,
    RasterImage,
    ReasoningEffort,
    StopReason,
    VisionLanguageModel,
    VisionRequest,
)
from rmspec.ocr import _openai_wire
from rmspec.ocr.vision_model import ADAPTER_REVISION, BedrockOpenAiVisionModel

if TYPE_CHECKING:
    from collections.abc import Mapping

MODEL_ID = "global.openai.gpt-5.6-luna"
MERGE_MODEL_ID = "global.openai.gpt-5.6-terra"
REGION = "us-east-1"
ENDPOINT = "https://bedrock-runtime.us-east-1.amazonaws.com"
HEX_DIGEST_LENGTH = 64
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"probe-png-payload"

MEASURED_ANSWER = {
    "choices": [
        {"finish_reason": "stop", "index": 0, "message": {"content": "rmspec probe 7431"}}
    ],
    "model": "openai.gpt-5.6-luna",
    "system_fingerprint": "fp_probe_0001",
    "usage": {
        "completion_tokens": 11,
        "completion_tokens_details": {"reasoning_tokens": 0},
        "prompt_tokens": 177,
        "total_tokens": 201,
    },
}

MEASURED_TRAP = {
    "choices": [{"finish_reason": "length", "index": 0, "message": {"content": None}}],
    "model": "openai.gpt-5.6-luna",
    "system_fingerprint": "fp_probe_0001",
    "usage": {
        "completion_tokens": 24,
        "completion_tokens_details": {"reasoning_tokens": 24},
        "prompt_tokens": 177,
        "total_tokens": 201,
    },
}

TRUNCATED_WITH_CONTENT = {
    "choices": [{"finish_reason": "length", "index": 0, "message": {"content": "half a pa"}}],
    "model": "openai.gpt-5.6-luna",
    "system_fingerprint": "fp_probe_0001",
    "usage": {
        "completion_tokens": 2000,
        "completion_tokens_details": {"reasoning_tokens": 1400},
        "prompt_tokens": 177,
        "total_tokens": 2177,
    },
}


class StubClient:
    """The whole client surface this binding uses, as a recording stand-in."""

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


def envelope(payload: object) -> Mapping[str, object]:
    return {"body": io.BytesIO(json.dumps(payload).encode())}


def answering(payload: object) -> StubClient:
    return StubClient(response=envelope(payload))


def build_model(
    *,
    client: StubClient | None = None,
    model_id: str = MODEL_ID,
    region: str = REGION,
    revision: str = ADAPTER_REVISION,
) -> BedrockOpenAiVisionModel:
    return BedrockOpenAiVisionModel(
        StubClient() if client is None else client,
        model_id=model_id,
        region=region,
        revision=revision,
    )


def request(*, prompt: str = "Transcribe this page.", with_image: bool = False) -> VisionRequest:
    images = ()
    if with_image:
        images = (
            RasterImage(
                page_ref="page-1",
                media=ImageMedia.PNG,
                data=PNG_BYTES,
                width=1620,
                height=2160,
                render_dpi=229,
            ),
        )
    return VisionRequest(
        prompt=prompt,
        system="You transcribe handwriting.",
        images=images,
        decoding=Decoding(
            max_output_tokens=2000,
            temperature=0.0,
            reasoning=ReasoningEffort.MEDIUM,
        ),
    )


# --------------------------------------------------------------------------- construction


def test_the_adapter_satisfies_the_port_it_binds():
    model: VisionLanguageModel = build_model()
    assert model.fingerprint


@pytest.mark.parametrize(
    ("model_id", "region", "revision"),
    [("", REGION, ADAPTER_REVISION), (MODEL_ID, "", ADAPTER_REVISION), (MODEL_ID, REGION, "")],
)
def test_an_empty_identity_component_is_refused_before_it_can_reach_a_cache_key(
    model_id: str,
    region: str,
    revision: str,
):
    with pytest.raises(ValueError, match="component of this binding's fingerprint"):
        build_model(model_id=model_id, region=region, revision=revision)


# --------------------------------------------------------------------------- fingerprint


def test_the_fingerprint_is_a_derived_digest_and_not_a_readable_setting():
    fingerprint = build_model().fingerprint
    assert len(fingerprint) == HEX_DIGEST_LENGTH
    assert bytes.fromhex(fingerprint)
    assert MODEL_ID not in fingerprint
    assert REGION not in fingerprint


def test_the_fingerprint_is_stable_for_the_lifetime_of_the_instance():
    model = build_model(client=answering(MEASURED_ANSWER))
    before = model.fingerprint
    model.complete(request())
    assert model.fingerprint == before


@pytest.mark.parametrize(
    ("model_id", "region", "revision"),
    [
        (MERGE_MODEL_ID, REGION, ADAPTER_REVISION),
        (MODEL_ID, "eu-west-1", ADAPTER_REVISION),
        (MODEL_ID, REGION, "2"),
    ],
)
def test_varying_any_single_identity_bearing_argument_changes_the_fingerprint(
    model_id: str,
    region: str,
    revision: str,
):
    varied = build_model(model_id=model_id, region=region, revision=revision)
    assert varied.fingerprint != build_model().fingerprint


def test_two_instances_built_from_different_clients_share_one_fingerprint():
    first = build_model(client=answering(MEASURED_ANSWER))
    second = build_model(client=StubClient())
    assert first.fingerprint == second.fingerprint


def test_the_components_are_framed_so_a_boundary_cannot_shift_between_them():
    left = build_model(model_id="ab", region="c")
    right = build_model(model_id="a", region="bc")
    assert left.fingerprint != right.fingerprint


# --------------------------------------------------------------------------- complete


def test_the_measured_answer_becomes_a_completion_with_every_echo_derived():
    asked = request(with_image=True)
    model = build_model(client=answering(MEASURED_ANSWER))
    completion = model.complete(asked)
    assert completion.text == "rmspec probe 7431"
    assert completion.stop_reason is StopReason.COMPLETE
    assert completion.is_complete is True
    assert completion.request_digest == asked.digest()
    assert completion.model_fingerprint == model.fingerprint


def test_the_reasoning_split_is_surfaced_and_the_reasoning_text_is_not_invented():
    completion = build_model(client=answering(MEASURED_ANSWER)).complete(request())
    assert completion.usage is not None
    assert completion.usage.input_tokens == 177
    assert completion.usage.output_tokens == 11
    assert completion.usage.reasoning_tokens == 0
    assert completion.reasoning is None


def test_a_body_with_no_usage_reports_no_accounting():
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": "t"}}]}
    assert build_model(client=answering(payload)).complete(request()).usage is None


def test_two_different_requests_are_echoed_as_two_different_digests():
    model = build_model(client=answering(MEASURED_ANSWER))
    first = model.complete(request(prompt="read page one"))
    second = build_model(client=answering(MEASURED_ANSWER)).complete(
        request(prompt="read page two")
    )
    assert first.request_digest != second.request_digest


def test_the_call_sends_the_profile_id_as_modelid_and_nothing_else_besides_the_body():
    asked = request(with_image=True)
    client = answering(MEASURED_ANSWER)
    build_model(client=client).complete(asked)
    assert len(client.calls) == 1
    call = client.calls[0]
    assert set(call) == {"modelId", "body"}
    assert call["modelId"] == MODEL_ID
    assert json.loads(call["body"]) == _openai_wire.build_body(asked)


# --------------------------------------------------------------------------- the trap


def test_a_null_content_is_a_malformed_response_and_never_an_empty_transcription():
    with pytest.raises(ModelResponseMalformed) as caught:
        build_model(client=answering(MEASURED_TRAP)).complete(request())
    assert caught.value.model_id == MODEL_ID
    assert "reasoning_tokens=24" in caught.value.detail
    assert isinstance(caught.value.__cause__, _openai_wire.WireFormatError)


def test_truncation_with_content_is_returned_as_data_rather_than_raised():
    completion = build_model(client=answering(TRUNCATED_WITH_CONTENT)).complete(request())
    assert completion.stop_reason is StopReason.OUTPUT_LIMIT
    assert completion.is_complete is False
    assert completion.text == "half a pa"


# --------------------------------------------------------------------------- provider failures


def test_a_service_rejection_reaches_the_caller_as_the_domain_error():
    error = ClientError(
        {
            "Error": {"Code": "ValidationException", "Message": "Unknown parameter"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "InvokeModel",
    )
    with pytest.raises(ModelRejectedRequest, match="Unknown parameter"):
        build_model(client=StubClient(error=error)).complete(request())


def test_an_unreachable_endpoint_reaches_the_caller_as_a_retryable_outage():
    client = StubClient(error=EndpointConnectionError(endpoint_url=ENDPOINT))
    with pytest.raises(ModelUnavailable) as caught:
        build_model(client=client).complete(request())
    assert caught.value.retryable is True
    assert caught.value.endpoint == ENDPOINT


def test_a_response_envelope_with_no_readable_body_is_a_malformed_response():
    with pytest.raises(ModelResponseMalformed, match="no readable body"):
        build_model(client=StubClient()).complete(request())


# --------------------------------------------------------------------------- served identity


def test_the_served_identity_is_logged_rather_than_folded_into_the_fingerprint(
    caplog: pytest.LogCaptureFixture,
):
    model = build_model(client=answering(MEASURED_ANSWER))
    with caplog.at_level(logging.INFO, logger="rmspec.ocr.vision_model"):
        model.complete(request())
    logged = caplog.text
    assert "served_model=openai.gpt-5.6-luna" in logged
    assert "system_fingerprint=fp_probe_0001" in logged
    assert f"binding_fingerprint={model.fingerprint}" in logged
    assert MODEL_ID != "openai.gpt-5.6-luna", "the served model must differ from the profile id"
