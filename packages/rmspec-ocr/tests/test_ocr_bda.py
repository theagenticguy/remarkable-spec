"""The Bedrock Data Automation recognizer, driven entirely through an injected client.

Nothing here constructs a Data Automation client -- not once, not in a fixture, not behind a skip.
:class:`~rmspec.ocr.bda.DataAutomationInvoker` is one method wide, so a double is a few lines and
every branch of the adapter is reachable through it.

Where the scripted responses came from
--------------------------------------
Not invented. Every shape below is the shape ``probes/bda_sync_document.py`` measured against the
real service on a page of 192 rmspec ink strokes: ``standardOutput`` as a JSON *string*,
``text_words`` carrying ``text``, ``confidence`` and ``line_id``, ``text_lines`` carrying a
``confidence`` of exactly ``0.01`` on every line while the words beneath them ran 0.869 to 1.0.
The last of those is why this file asserts that a line's own confidence is never read.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, cast

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    ReadTimeoutError,
)

from rmspec.domain.errors import RecognitionFailed
from rmspec.domain.ports.ocr import ImageMedia, RasterImage, Recognition, TextRecognizer
from rmspec.ocr.bda import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PROFILE,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_REVISION,
    PROJECT_RESOURCE,
    PROVIDER,
    STAGE,
    BdaRecognizer,
    DataAutomationInvoker,
    profile_arn_for,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

ACCOUNT = "123456789012"
REGION = "us-west-2"
PROJECT_ARN = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:{PROJECT_RESOURCE}/abc123"
PROFILE_ARN = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:data-automation-profile/{DEFAULT_PROFILE}"

#: What the service really put on every line of the probe page while its words ran 0.869 to 1.0.
PLACEHOLDER_LINE_CONFIDENCE = 0.01


def raster(page_ref: str = "page-1", payload: bytes = b"pixels") -> RasterImage:
    """Build a raster whose bytes are recognisable in a recorded request."""
    return RasterImage(
        page_ref=page_ref,
        media=ImageMedia.PNG,
        data=PNG_MAGIC + payload,
        width=1620,
        height=2160,
        render_dpi=229,
    )


def word(text: str, confidence: float, line_id: str) -> Mapping[str, Any]:
    """Build one ``text_words`` entry as the service reports it."""
    return {"id": f"w-{text}", "text": text, "confidence": confidence, "line_id": line_id}


def output_of(payload: Mapping[str, Any], /, *more: Mapping[str, Any]) -> Mapping[str, Any]:
    """Wrap standard-output payloads in a response, JSON-encoding each as the service does."""
    return {
        "semanticModality": "DOCUMENT",
        "outputSegments": [{"standardOutput": json.dumps(one)} for one in (payload, *more)],
    }


def words_of(*entries: Mapping[str, Any]) -> Mapping[str, Any]:
    """Wrap word entries in a one-segment response."""
    return output_of({"text_words": list(entries)})


def client_error(code: str, status: int | None = None) -> ClientError:
    """Build the error botocore raises for a service-side refusal."""
    response: dict[str, Any] = {"Error": {"Code": code, "Message": "measured shape"}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status, "RequestId": "r-1"}
    return ClientError(response, "InvokeDataAutomation")


class StubDataAutomation:
    """A Data Automation client that answers from a script, and records what it was asked."""

    def __init__(
        self,
        response: Mapping[str, Any] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response if response is not None else words_of()
        self.error = error
        self.calls: list[Mapping[str, object]] = []

    def invoke_data_automation(self, **kwargs: object) -> Mapping[str, Any]:
        """Record the request and answer with the scripted response, or raise."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class EchoingDataAutomation:
    """A client that reads back the bytes it was given, one page at a time."""

    def invoke_data_automation(self, **kwargs: object) -> Mapping[str, Any]:
        """Answer with a single scored word holding the payload after the PNG magic."""
        configuration = cast("Mapping[str, bytes]", kwargs["inputConfiguration"])
        payload = configuration["bytes"].removeprefix(PNG_MAGIC).decode()
        return words_of(word(payload, 0.99, "l-0"))


class RecordingFactory:
    """A client factory that records its arguments instead of reaching AWS."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.client = StubDataAutomation()

    def __call__(self, *args: object, **kwargs: object) -> DataAutomationInvoker:
        """Record the call and hand back a stub."""
        self.calls.append((args, kwargs))
        return self.client


def over(client: DataAutomationInvoker, /, **kwargs: int) -> BdaRecognizer:
    """Build the adapter over one client, with the ARNs every case shares."""
    return BdaRecognizer(client, project_arn=PROJECT_ARN, profile_arn=PROFILE_ARN, **kwargs)


# ── the profile ARN is composed, never asked for twice ───────────────────────────────────


def test_the_profile_arn_inherits_the_projects_partition_region_and_account() -> None:
    assert profile_arn_for(PROJECT_ARN) == PROFILE_ARN


def test_a_non_default_profile_id_is_the_only_part_a_caller_chooses() -> None:
    composed = profile_arn_for(PROJECT_ARN, profile="eu.data-automation-v1")

    assert composed == (
        f"arn:aws:bedrock:{REGION}:{ACCOUNT}:data-automation-profile/eu.data-automation-v1"
    )


def test_a_projects_partition_is_carried_over_rather_than_assumed_to_be_aws() -> None:
    # A GovCloud or China project must not have a commercial profile composed for it.
    composed = profile_arn_for(
        f"arn:aws-us-gov:bedrock:us-gov-west-1:{ACCOUNT}:{PROJECT_RESOURCE}/x"
    )

    assert composed.startswith("arn:aws-us-gov:bedrock:us-gov-west-1:")


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-an-arn",
        "arn:aws:bedrock:us-west-2:123456789012",
        f"urn:aws:bedrock:{REGION}:{ACCOUNT}:{PROJECT_RESOURCE}/x",
    ],
)
def test_something_that_is_not_an_arn_is_refused_rather_than_pattern_matched(
    malformed: str,
) -> None:
    with pytest.raises(ValueError, match="not an ARN"):
        profile_arn_for(malformed)


@pytest.mark.parametrize(
    "wrong",
    [
        f"arn:aws:textract:{REGION}:{ACCOUNT}:{PROJECT_RESOURCE}/x",
        f"arn:aws:bedrock:{REGION}:{ACCOUNT}:data-automation-profile/us.data-automation-v1",
        f"arn:aws:bedrock:{REGION}:{ACCOUNT}:blueprint/x",
    ],
)
def test_an_arn_for_something_other_than_a_project_is_refused(wrong: str) -> None:
    # Composing a profile out of a blueprint's ARN would send a request naming a profile that
    # does not exist, and the failure would arrive from the service rather than from the setting.
    with pytest.raises(ValueError, match=f"not a {PROJECT_RESOURCE} ARN"):
        profile_arn_for(wrong)


# ── identity ────────────────────────────────────────────────────────────────────────────


def test_the_default_slug_is_the_one_the_app_keys_cache_rows_on() -> None:
    assert over(StubDataAutomation()).provider_id == f"{PROVIDER}@{DEFAULT_REVISION}"


def test_bumping_the_revision_changes_the_slug_and_so_invalidates_older_rows() -> None:
    assert over(StubDataAutomation(), revision=7).provider_id == f"{PROVIDER}@7"


def test_two_projects_read_alike_and_so_share_one_slug() -> None:
    # The judgement recorded on `provider_id`: an account id in the cache key would throw away
    # every cached page the moment a project is recreated with identical settings.
    other = BdaRecognizer(
        StubDataAutomation(),
        project_arn=f"arn:aws:bedrock:{REGION}:999988887777:{PROJECT_RESOURCE}/zzz",
        profile_arn=PROFILE_ARN,
    )

    assert other.provider_id == over(StubDataAutomation()).provider_id


# ── the request ─────────────────────────────────────────────────────────────────────────


def test_the_page_is_sent_inline_and_no_output_bucket_is_named() -> None:
    # An `outputConfiguration` s3 uri makes the service write the output to the bucket *instead*
    # of returning it, so naming one would leave this adapter with nothing to read.
    client = StubDataAutomation()
    over(client).recognize(raster(payload=b"the-bytes"))
    (request,) = client.calls

    assert request["inputConfiguration"] == {"bytes": PNG_MAGIC + b"the-bytes"}
    assert "outputConfiguration" not in request


def test_the_request_names_the_project_the_profile_and_the_live_stage() -> None:
    client = StubDataAutomation()
    over(client).recognize(raster())
    (request,) = client.calls

    assert request["dataAutomationProfileArn"] == PROFILE_ARN
    assert request["dataAutomationConfiguration"] == {
        "dataAutomationProjectArn": PROJECT_ARN,
        "stage": STAGE,
    }


# ── the fold ────────────────────────────────────────────────────────────────────────────


def test_words_are_grouped_into_lines_by_the_line_they_belong_to() -> None:
    reading = over(
        StubDataAutomation(
            words_of(
                word("Sprint", 1.0, "l-0"),
                word("notes", 1.0, "l-0"),
                word("fold", 1.0, "l-1"),
                word("the", 1.0, "l-1"),
            )
        )
    ).recognize(raster())

    assert reading.text == "Sprint notes\nfold the"


def test_lines_keep_the_order_the_words_arrived_in() -> None:
    # First appearance, not a sort: the service emits an ascending `reading_order` and this
    # preserves it without trusting a second field to agree with the first.
    reading = over(
        StubDataAutomation(
            words_of(
                word("second", 1.0, "z-line"),
                word("first", 1.0, "a-line"),
            )
        )
    ).recognize(raster())

    assert reading.text == "second\nfirst"


def test_the_mean_is_character_weighted_over_the_words_the_service_scored() -> None:
    # Four characters at 1.0 and sixteen at 0.0 is 0.2 per character and 0.5 per word, and the
    # port specifies the former so one confident word cannot outvote a page of noise.
    reading = over(
        StubDataAutomation(
            words_of(
                word("four", 1.0, "l-0"),
                word("sixteencharacter", 0.0, "l-0"),
            )
        )
    ).recognize(raster())

    assert reading.mean_confidence == pytest.approx(0.2)


def test_a_confidence_outside_the_scale_is_clamped_rather_than_rejected() -> None:
    reading = over(StubDataAutomation(words_of(word("word", 1.4, "l-0")))).recognize(raster())

    assert reading.mean_confidence == 1.0


def test_a_blank_page_is_a_successful_empty_reading() -> None:
    reading = over(StubDataAutomation(words_of())).recognize(raster())

    assert (reading.text, reading.mean_confidence) == ("", None)


def test_a_segment_carrying_neither_words_nor_lines_is_a_blank_page_too() -> None:
    reading = over(StubDataAutomation(output_of({}))).recognize(raster())

    assert (reading.text, reading.mean_confidence) == ("", None)


def test_every_segment_of_a_multi_segment_response_is_read() -> None:
    reading = over(
        StubDataAutomation(
            output_of(
                {"text_words": [word("first", 1.0, "l-0")]},
                {"text_words": [word("second", 1.0, "l-9")]},
            )
        )
    ).recognize(raster())

    assert reading.text == "first\nsecond"


# ── the line-level confidence is never trusted ──────────────────────────────────────────


def test_lines_are_read_only_when_the_project_scored_no_words() -> None:
    # A project configured without WORD granularity still reports lines. Their text is usable.
    reading = over(
        StubDataAutomation(
            output_of(
                {
                    "text_lines": [
                        {"text": "Sprint notes", "confidence": PLACEHOLDER_LINE_CONFIDENCE},
                        {"text": "fold the engines", "confidence": PLACEHOLDER_LINE_CONFIDENCE},
                    ]
                }
            )
        )
    ).recognize(raster())

    assert reading.text == "Sprint notes\nfold the engines"


def test_a_lines_own_confidence_is_never_folded_into_the_reading() -> None:
    # Measured on the real service: every line came back at exactly 0.01 while the words beneath
    # those same lines ran 0.869 to 1.0. Folding 0.01 through the character-weighted mean would
    # report every page at approximately 0.01, which the port reads as a garbage transcription.
    reading = over(
        StubDataAutomation(
            output_of(
                {"text_lines": [{"text": "text", "confidence": PLACEHOLDER_LINE_CONFIDENCE}]}
            )
        )
    ).recognize(raster())

    assert reading.text == "text"
    assert reading.mean_confidence is None, (
        "a line's confidence is a placeholder, and None is what the port means by unmeasured"
    )


def test_words_win_over_lines_when_the_response_carries_both() -> None:
    reading = over(
        StubDataAutomation(
            output_of(
                {
                    "text_words": [word("scored", 0.5, "l-0")],
                    "text_lines": [{"text": "ignored", "confidence": PLACEHOLDER_LINE_CONFIDENCE}],
                }
            )
        )
    ).recognize(raster())

    assert (reading.text, reading.mean_confidence) == ("scored", 0.5)


# ── attribution ─────────────────────────────────────────────────────────────────────────


def test_the_reading_is_attributed_to_the_raster_that_was_read() -> None:
    reading = over(EchoingDataAutomation()).recognize(raster("page-7", b"seven"))

    assert (reading.page_ref, reading.text) == ("page-7", "seven")


def test_one_instance_tolerates_concurrent_calls_from_several_threads() -> None:
    recognizer = over(EchoingDataAutomation())
    pages = [raster(f"page-{index}", str(index).encode()) for index in range(24)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        readings = list(pool.map(recognizer.recognize, pages))

    assert [(r.page_ref, r.text) for r in readings] == [
        (f"page-{index}", str(index)) for index in range(24)
    ]


# ── failure ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("ThrottlingException", 429),
        ("InternalServerException", 500),
        ("ServiceUnavailableException", 503),
        # An unrecognised code split on the status, which is the fallback the classifier keeps.
        ("SomeNewException", 500),
        ("SomeNewException", 599),
    ],
)
def test_a_service_side_error_is_retryable(code: str, status: int | None) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        over(StubDataAutomation(error=client_error(code, status))).recognize(raster())

    assert caught.value.retryable is True
    assert caught.value.provider_id == f"{PROVIDER}@{DEFAULT_REVISION}"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("ValidationException", 400),
        ("AccessDeniedException", 403),
        ("SomeNewException", 404),
        # No status at all: classified permanent rather than raising from inside the handler.
        ("SomeNewException", None),
    ],
)
def test_a_rejected_request_is_not_retryable(code: str, status: int | None) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        over(StubDataAutomation(error=client_error(code, status))).recognize(raster())

    assert caught.value.retryable is False


def test_an_error_response_missing_both_members_is_permanent() -> None:
    # ClientError tolerates a response with neither Error nor ResponseMetadata, and reading it
    # must not raise a second error from inside the handler for the first.
    with pytest.raises(RecognitionFailed) as caught:
        over(StubDataAutomation(error=ClientError({}, "InvokeDataAutomation"))).recognize(raster())

    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "error",
    [
        EndpointConnectionError(endpoint_url="https://bedrock-data-automation-runtime"),
        ConnectionClosedError(endpoint_url="https://bedrock-data-automation-runtime"),
        ReadTimeoutError(endpoint_url="https://bedrock-data-automation-runtime"),
    ],
)
def test_a_service_that_could_not_be_reached_is_retryable(error: Exception) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        over(StubDataAutomation(error=error)).recognize(raster())

    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "error",
    [NoCredentialsError(), ParamValidationError(report="the request was malformed")],
)
def test_a_misconfigured_caller_is_not_retryable(error: Exception) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        over(StubDataAutomation(error=error)).recognize(raster())

    assert caught.value.retryable is False


def test_no_botocore_exception_crosses_the_port() -> None:
    for error in (
        client_error("ThrottlingException", 429),
        EndpointConnectionError(endpoint_url="https://x"),
        NoCredentialsError(),
    ):
        with pytest.raises(RecognitionFailed):
            over(StubDataAutomation(error=error)).recognize(raster())


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"outputSegments": [{}]},
        {"outputSegments": [{"standardOutput": "not json"}]},
        {"outputSegments": [{"standardOutput": json.dumps({"text_words": [{"text": "x"}]})}]},
        {
            "outputSegments": [
                {"standardOutput": json.dumps({"text_words": [{"text": "x", "line_id": "l"}]})}
            ]
        },
        {
            "outputSegments": [
                {
                    "standardOutput": json.dumps(
                        {"text_words": [{"text": "x", "line_id": "l", "confidence": "high"}]}
                    )
                }
            ]
        },
        {"outputSegments": [{"standardOutput": json.dumps({"text_words": 3})}]},
    ],
)
def test_a_response_this_reader_cannot_use_is_a_permanent_failure(
    response: Mapping[str, Any],
) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        over(StubDataAutomation(response)).recognize(raster())

    assert caught.value.retryable is False
    assert "InvokeDataAutomation response" in caught.value.message


# ── construction ────────────────────────────────────────────────────────────────────────


def test_for_project_builds_one_client_with_the_timeouts_as_construction_configuration() -> None:
    factory = RecordingFactory()
    recognizer = BdaRecognizer.for_project(
        PROJECT_ARN, region_name=REGION, revision=4, build=factory
    )
    (args, kwargs) = factory.calls[0]
    # botocore builds a Config's attributes in __init__ from its own option table, so they are
    # invisible to a static reading of the class.
    config = cast("Any", kwargs["config"])

    assert args == ("bedrock-data-automation-runtime",)
    assert kwargs["region_name"] == REGION
    assert config.connect_timeout == DEFAULT_CONNECT_TIMEOUT
    assert config.read_timeout == DEFAULT_READ_TIMEOUT
    assert config.retries == {"max_attempts": DEFAULT_MAX_ATTEMPTS, "mode": "standard"}
    assert recognizer.provider_id == f"{PROVIDER}@4"
    recognizer.recognize(raster())
    assert factory.client.calls, "the recognizer must use the client the factory built"


def test_for_project_composes_the_profile_arn_from_the_project_it_was_given() -> None:
    factory = RecordingFactory()
    BdaRecognizer.for_project(
        PROJECT_ARN, region_name=REGION, profile="eu.data-automation-v1", build=factory
    ).recognize(raster())
    (request,) = factory.client.calls

    assert request["dataAutomationProfileArn"] == (
        f"arn:aws:bedrock:{REGION}:{ACCOUNT}:data-automation-profile/eu.data-automation-v1"
    )


def test_for_project_passes_the_timeouts_it_is_given() -> None:
    factory = RecordingFactory()
    BdaRecognizer.for_project(
        PROJECT_ARN,
        region_name="eu-west-1",
        connect_timeout=1.5,
        read_timeout=2.5,
        max_attempts=7,
        build=factory,
    )
    config = cast("Any", factory.calls[0][1]["config"])

    assert (config.connect_timeout, config.read_timeout) == (1.5, 2.5)
    assert config.retries == {"max_attempts": 7, "mode": "standard"}


def test_for_project_refuses_an_unusable_project_arn_before_building_a_client() -> None:
    factory = RecordingFactory()

    with pytest.raises(ValueError, match="not an ARN"):
        BdaRecognizer.for_project("nonsense", region_name=REGION, build=factory)

    assert factory.calls == [], "a client must not be built for a project that cannot be named"


def test_the_region_is_not_derived_from_the_arn_so_a_disagreement_stays_visible() -> None:
    # A client whose endpoint disagreed with its ARN is a configuration error worth seeing.
    factory = RecordingFactory()
    BdaRecognizer.for_project(PROJECT_ARN, region_name="ap-south-1", build=factory)

    assert factory.calls[0][1]["region_name"] == "ap-south-1"


def test_it_satisfies_the_text_recognizer_port() -> None:
    # A typed binding rather than an isinstance check: the port is not runtime_checkable.
    recognizer: TextRecognizer = over(StubDataAutomation())
    reading = recognizer.recognize(raster())

    assert isinstance(reading, Recognition)
