"""The wire contract, driven with no client, no credential and no billable call.

Every body in here is either the measured shape from the 2026-08-28 probes or a deliberate
mutation of it. The two rows of the silent-failure table are both present verbatim, because the
difference between them is the whole reason this module exists.
"""

from __future__ import annotations

import base64
import json

import pytest

from rmspec.domain.ports.ocr import (
    Decoding,
    ImageMedia,
    RasterImage,
    ReasoningEffort,
    StopReason,
    TokenUsage,
    VisionRequest,
)
from rmspec.ocr import _openai_wire

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"probe-png-payload"
JPEG_BYTES = b"\xff\xd8\xff" + b"probe-jpeg-payload"

#: The measured 2000-token answer: content present, finish_reason "stop", no reasoning spend.
MEASURED_ANSWER = {
    "choices": [
        {"finish_reason": "stop", "index": 0, "message": {"content": "rmspec probe 7431"}}
    ],
    "created": 1756425600,
    "id": "chatcmpl-probe",
    "model": "openai.gpt-5.6-luna",
    "object": "chat.completion",
    "service_tier": "default",
    "system_fingerprint": "fp_probe_0001",
    "usage": {
        "completion_tokens": 11,
        "completion_tokens_details": {
            "accepted_prediction_tokens": 0,
            "audio_tokens": 0,
            "reasoning_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
        "prompt_tokens": 177,
        "prompt_tokens_details": {"audio_tokens": 0, "cache_write_tokens": 0, "cached_tokens": 0},
        "total_tokens": 201,
    },
}

#: The measured 24-token answer: the entire budget went to reasoning and content came back null
#: with no exception from the SDK.
MEASURED_TRAP = {
    "choices": [{"finish_reason": "length", "index": 0, "message": {"content": None}}],
    "created": 1756425600,
    "id": "chatcmpl-probe",
    "model": "openai.gpt-5.6-luna",
    "object": "chat.completion",
    "system_fingerprint": "fp_probe_0001",
    "usage": {
        "completion_tokens": 24,
        "completion_tokens_details": {"reasoning_tokens": 24},
        "prompt_tokens": 177,
        "total_tokens": 201,
    },
}


def raster(*, page_ref: str = "page-1", media: ImageMedia = ImageMedia.PNG) -> RasterImage:
    data = PNG_BYTES if media is ImageMedia.PNG else JPEG_BYTES
    return RasterImage(
        page_ref=page_ref,
        media=media,
        data=data,
        width=1620,
        height=2160,
        render_dpi=229,
    )


def request(
    *,
    prompt: str = "Transcribe the text in this image. Output only the text.",
    system: str | None = None,
    images: tuple[RasterImage, ...] = (),
    max_output_tokens: int = 2000,
    temperature: float = 0.0,
    reasoning: ReasoningEffort = ReasoningEffort.MEDIUM,
) -> VisionRequest:
    return VisionRequest(
        prompt=prompt,
        system=system,
        images=images,
        decoding=Decoding(
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning=reasoning,
        ),
    )


def body_of(payload: object) -> bytes:
    return json.dumps(payload).encode()


# --------------------------------------------------------------------------- mappings


def test_effort_tokens_cover_every_domain_effort():
    assert set(_openai_wire._EFFORT_TOKENS) == set(ReasoningEffort)


def test_every_emitted_effort_token_is_one_the_provider_accepts():
    assert set(_openai_wire._EFFORT_TOKENS.values()) <= _openai_wire.ACCEPTED_REASONING_EFFORTS


def test_the_rejected_effort_value_is_not_in_the_accepted_set():
    assert "minimal" not in _openai_wire.ACCEPTED_REASONING_EFFORTS


def test_data_uri_prefixes_cover_every_domain_media():
    assert set(_openai_wire._DATA_URI_PREFIXES) == set(ImageMedia)


def test_stop_sequence_is_unreachable_through_this_envelope():
    assert StopReason.STOP_SEQUENCE not in set(_openai_wire._STOP_REASONS.values())


# --------------------------------------------------------------------------- data URIs


@pytest.mark.parametrize(
    ("media", "prefix", "data"),
    [
        (ImageMedia.PNG, "data:image/png;base64,", PNG_BYTES),
        (ImageMedia.JPEG, "data:image/jpeg;base64,", JPEG_BYTES),
    ],
)
def test_image_data_uri_uses_the_openai_vision_convention(
    media: ImageMedia,
    prefix: str,
    data: bytes,
):
    uri = _openai_wire.image_data_uri(raster(media=media))
    assert uri.startswith(prefix)
    assert base64.b64decode(uri.removeprefix(prefix)) == data


# --------------------------------------------------------------------------- request body


def test_body_carries_only_the_three_fields_the_probes_verified():
    assert set(_openai_wire.build_body(request())) == {
        "messages",
        "max_completion_tokens",
        "reasoning_effort",
    }


def test_body_uses_max_completion_tokens_and_never_max_tokens():
    body = _openai_wire.build_body(request(max_output_tokens=1234))
    assert body["max_completion_tokens"] == 1234
    assert "max_tokens" not in body


@pytest.mark.parametrize("absent", ["stream", "model", "temperature", "anthropic_version"])
def test_body_omits_every_field_no_measurement_supports(absent: str):
    assert absent not in _openai_wire.build_body(request(temperature=0.7))


@pytest.mark.parametrize(
    ("effort", "token"),
    [
        (ReasoningEffort.NONE, "none"),
        (ReasoningEffort.LOW, "low"),
        (ReasoningEffort.MEDIUM, "medium"),
        (ReasoningEffort.HIGH, "high"),
    ],
)
def test_body_maps_the_domain_effort_onto_the_accepted_token(effort: ReasoningEffort, token: str):
    assert _openai_wire.build_body(request(reasoning=effort))["reasoning_effort"] == token


def test_a_request_without_a_system_turn_sends_one_user_message():
    assert _openai_wire.build_body(request())["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Transcribe the text in this image. Output only the text.",
                }
            ],
        }
    ]


def test_the_system_turn_becomes_a_developer_message_ahead_of_the_user_turn():
    body = _openai_wire.build_body(request(prompt="read it", system="You transcribe handwriting."))
    assert body["messages"] == [
        {
            "role": "developer",
            "content": [{"type": "text", "text": "You transcribe handwriting."}],
        },
        {"role": "user", "content": [{"type": "text", "text": "read it"}]},
    ]


def test_images_follow_the_prompt_text_in_the_order_they_were_given():
    first = raster(page_ref="page-1", media=ImageMedia.PNG)
    second = raster(page_ref="page-2", media=ImageMedia.JPEG)
    body = _openai_wire.build_body(request(prompt="read it", images=(first, second)))
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "read it"},
                {"type": "image_url", "image_url": {"url": _openai_wire.image_data_uri(first)}},
                {"type": "image_url", "image_url": {"url": _openai_wire.image_data_uri(second)}},
            ],
        }
    ]


def test_no_image_is_sent_as_bedrocks_own_image_block():
    payload = _openai_wire.encode_request(request(images=(raster(),)))
    assert b'"type":"image"' not in payload
    assert b'"source"' not in payload


def test_encode_request_is_compact_ascii_json_of_the_body():
    payload = _openai_wire.encode_request(request(system="s", images=(raster(),)))
    assert payload.decode("ascii")
    assert b", " not in payload
    assert json.loads(payload) == _openai_wire.build_body(request(system="s", images=(raster(),)))


# --------------------------------------------------------------------------- response decode


def test_the_measured_answer_decodes_into_every_field_the_domain_wants():
    decoded = _openai_wire.decode_body(body_of(MEASURED_ANSWER))
    assert decoded.text == "rmspec probe 7431"
    assert decoded.stop_reason is StopReason.COMPLETE
    assert decoded.usage == TokenUsage(input_tokens=177, output_tokens=11, reasoning_tokens=0)
    assert decoded.served_model == "openai.gpt-5.6-luna"
    assert decoded.served_fingerprint == "fp_probe_0001"


def test_the_measured_trap_is_refused_and_the_message_names_the_reasoning_spend():
    with pytest.raises(_openai_wire.WireFormatError) as caught:
        _openai_wire.decode_body(body_of(MEASURED_TRAP))
    detail = caught.value.detail
    assert "content is null" in detail
    assert "finish_reason='length'" in detail
    assert "reasoning_tokens=24" in detail
    assert "not an empty page" in detail


def test_the_envelope_name_prefixes_every_failure_message():
    with pytest.raises(_openai_wire.WireFormatError) as caught:
        _openai_wire.decode_body(body_of(MEASURED_TRAP))
    assert str(caught.value).startswith(f"{_openai_wire.ENVELOPE}: ")


@pytest.mark.parametrize("finish", ["stop", "length", "content_filter", "tool_calls", None])
def test_null_content_is_refused_whatever_the_finish_reason_says(finish: str | None):
    payload = {"choices": [{"finish_reason": finish, "message": {"content": None}}]}
    with pytest.raises(_openai_wire.WireFormatError, match="content is null"):
        _openai_wire.decode_body(body_of(payload))


def test_null_content_without_a_usage_object_still_reports_the_spend_as_unreported():
    payload = {"choices": [{"finish_reason": "length", "message": {"content": None}}]}
    with pytest.raises(_openai_wire.WireFormatError, match="reasoning_tokens=unreported"):
        _openai_wire.decode_body(body_of(payload))


def test_truncation_with_content_is_data_and_not_an_exception():
    payload = {
        "choices": [{"finish_reason": "length", "message": {"content": "half a pa"}}],
        "usage": {"prompt_tokens": 177, "completion_tokens": 2000},
    }
    decoded = _openai_wire.decode_body(body_of(payload))
    assert decoded.stop_reason is StopReason.OUTPUT_LIMIT
    assert decoded.text == "half a pa"


def test_an_empty_string_answer_is_data_and_is_not_the_null_case():
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
    assert _openai_wire.decode_body(body_of(payload)).text == ""


def test_a_content_filter_stop_becomes_a_refusal():
    payload = {"choices": [{"finish_reason": "content_filter", "message": {"content": "no"}}]}
    assert _openai_wire.decode_body(body_of(payload)).stop_reason is StopReason.REFUSAL


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"not json at all", "is not JSON"),
        (b"\xff\xfe\xfd", "is not JSON"),
        (b'["a list"]', "is a list, not an object"),
        (b"{}", "carries no choices[0]"),
        (b'{"choices": []}', "carries no choices[0]"),
        (b'{"choices": "stop"}', "carries no choices[0]"),
        (b'{"choices": ["stop"]}', "choices[0] is a str, not an object"),
        (b'{"choices": [{}]}', "choices[0].message is a NoneType, not an object"),
        (b'{"choices": [{"message": 7}]}', "choices[0].message is a int, not an object"),
    ],
)
def test_a_body_that_is_not_a_completion_is_refused(payload: bytes, expected: str):
    with pytest.raises(_openai_wire.WireFormatError) as caught:
        _openai_wire.decode_body(payload)
    assert expected in caught.value.detail


def test_a_non_string_content_is_refused_rather_than_stringified():
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": [{"text": "hi"}]}}]}
    with pytest.raises(_openai_wire.WireFormatError, match="content is a list, not a string"):
        _openai_wire.decode_body(body_of(payload))


@pytest.mark.parametrize("finish", ["tool_calls", "", None, 7])
def test_a_finish_reason_this_envelope_does_not_define_is_refused(finish: object):
    payload = {"choices": [{"finish_reason": finish, "message": {"content": "text"}}]}
    with pytest.raises(_openai_wire.WireFormatError, match="is not one of"):
        _openai_wire.decode_body(body_of(payload))


# --------------------------------------------------------------------------- usage decode


def test_a_body_with_no_usage_reports_no_accounting_rather_than_zeroes():
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": "t"}}]}
    assert _openai_wire.decode_body(body_of(payload)).usage is None


def test_the_measured_truncated_pair_satisfies_the_domains_reasoning_validator():
    decoded = _openai_wire.decode_body(
        body_of(
            {
                "choices": [{"finish_reason": "length", "message": {"content": "t"}}],
                "usage": {
                    "prompt_tokens": 177,
                    "completion_tokens": 24,
                    "completion_tokens_details": {"reasoning_tokens": 24},
                },
            }
        )
    )
    assert decoded.usage == TokenUsage(input_tokens=177, output_tokens=24, reasoning_tokens=24)


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ("not an object", "usage is a str, not an object"),
        ({"completion_tokens": 11}, "missing integer prompt_tokens"),
        ({"prompt_tokens": 177}, "missing integer prompt_tokens"),
        ({"prompt_tokens": "177", "completion_tokens": 11}, "missing integer prompt_tokens"),
        (
            {
                "prompt_tokens": 177,
                "completion_tokens": 10,
                "completion_tokens_details": {"reasoning_tokens": 30},
            },
            "not valid token accounting",
        ),
        ({"prompt_tokens": -1, "completion_tokens": 11}, "not valid token accounting"),
        (
            {
                "prompt_tokens": 177,
                "completion_tokens": 11,
                "completion_tokens_details": {"reasoning_tokens": "many"},
            },
            "reasoning_tokens is a str, not an integer",
        ),
    ],
)
def test_a_present_but_unreadable_usage_is_refused_rather_than_reduced_to_none(
    usage: object,
    expected: str,
):
    payload = {
        "choices": [{"finish_reason": "stop", "message": {"content": "t"}}],
        "usage": usage,
    }
    with pytest.raises(_openai_wire.WireFormatError) as caught:
        _openai_wire.decode_body(body_of(payload))
    assert expected in caught.value.detail


@pytest.mark.parametrize(
    "details",
    [None, "not an object", {}, {"accepted_prediction_tokens": 0}],
)
def test_an_unreported_reasoning_split_is_none_and_never_zero(details: object):
    payload = {
        "choices": [{"finish_reason": "stop", "message": {"content": "t"}}],
        "usage": {
            "prompt_tokens": 177,
            "completion_tokens": 11,
            "completion_tokens_details": details,
        },
    }
    usage = _openai_wire.decode_body(body_of(payload)).usage
    assert usage is not None
    assert usage.reasoning_tokens is None


# --------------------------------------------------------------------------- served identity


@pytest.mark.parametrize("served", [None, "", 7, {"id": "x"}])
def test_an_absent_or_unusable_served_identity_never_fails_a_good_answer(served: object):
    payload = {
        "choices": [{"finish_reason": "stop", "message": {"content": "t"}}],
        "model": served,
        "system_fingerprint": served,
    }
    decoded = _openai_wire.decode_body(body_of(payload))
    assert decoded.served_model is None
    assert decoded.served_fingerprint is None
