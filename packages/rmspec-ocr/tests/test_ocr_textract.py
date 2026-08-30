"""The Textract recognizer, driven entirely through an injected client.

Nothing here constructs a Textract client -- not once, not in a fixture, not behind a skip.
:class:`~rmspec.ocr.textract.DocumentTextDetector` is one method wide, so a double is a few
lines and every branch of the adapter is reachable through it, including the three families of
botocore failure and the four ways a response can be unreadable.
"""

from __future__ import annotations

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
from rmspec.ocr.textract import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_REVISION,
    PROVIDER,
    DocumentTextDetector,
    TextractRecognizer,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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


def line(text: str, confidence: float) -> Mapping[str, Any]:
    """Build one LINE block as Textract reports it, with confidence on its 0-100 scale."""
    return {"BlockType": "LINE", "Text": text, "Confidence": confidence}


def response_of(*blocks: Mapping[str, Any]) -> Mapping[str, Any]:
    """Wrap blocks in a response, with the PAGE block the service always returns."""
    return {"Blocks": [{"BlockType": "PAGE"}, *blocks]}


def client_error(code: str, status: int | None = None) -> ClientError:
    """Build the error botocore raises for a service-side refusal."""
    response: dict[str, Any] = {"Error": {"Code": code, "Message": "measured shape"}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status, "RequestId": "r-1"}
    return ClientError(response, "DetectDocumentText")


class StubTextract:
    """A Textract client that answers from a script, and records what it was asked."""

    def __init__(
        self,
        response: Mapping[str, Any] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response if response is not None else response_of()
        self.error = error
        self.calls: list[Mapping[str, object]] = []

    def detect_document_text(self, **kwargs: object) -> Mapping[str, Any]:
        """Record the request and answer with the scripted response, or raise."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class EchoingTextract:
    """A Textract client that reads back the bytes it was given, one page at a time."""

    def detect_document_text(self, **kwargs: object) -> Mapping[str, Any]:
        """Answer with a single LINE holding the payload after the PNG magic."""
        document = cast("Mapping[str, bytes]", kwargs["Document"])
        payload = document["Bytes"].removeprefix(PNG_MAGIC).decode()
        return response_of(line(payload, 99.0))


class RecordingFactory:
    """A client factory that records its arguments instead of reaching AWS."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.client = StubTextract()

    def __call__(self, *args: object, **kwargs: object) -> DocumentTextDetector:
        """Record the call and hand back a stub."""
        self.calls.append((args, kwargs))
        return self.client


def test_the_default_slug_is_the_one_the_app_keys_cache_rows_on() -> None:
    assert TextractRecognizer(StubTextract()).provider_id == "aws-textract@1"
    assert PROVIDER == "aws-textract"
    assert DEFAULT_REVISION == 1


def test_bumping_the_revision_changes_the_slug_and_so_invalidates_older_rows() -> None:
    # The revision lives inside the slug precisely so this is mechanical: the app folds the
    # slug into its cache key, so a bumped revision cannot reuse a row the old engine wrote.
    default = TextractRecognizer(StubTextract()).provider_id
    bumped = TextractRecognizer(StubTextract(), revision=2).provider_id
    assert bumped == "aws-textract@2"
    assert bumped != default


def test_the_document_carries_the_rasters_own_bytes_and_nothing_else() -> None:
    client = StubTextract()
    image = raster()
    TextractRecognizer(client).recognize(image)
    assert client.calls == [{"Document": {"Bytes": image.data}}]


def test_only_line_blocks_are_read() -> None:
    client = StubTextract(
        {
            "Blocks": [
                {"BlockType": "PAGE"},
                line("first", 99.0),
                {"BlockType": "WORD", "Text": "first", "Confidence": 99.0},
                line("second", 99.0),
            ]
        }
    )
    assert TextractRecognizer(client).recognize(raster()).text == "first\nsecond"


def test_confidence_is_rescaled_from_the_services_hundred_point_scale() -> None:
    client = StubTextract(response_of(line("hello", 90.0)))
    assert TextractRecognizer(client).recognize(raster()).mean_confidence == pytest.approx(0.9)


def test_the_mean_is_character_weighted_not_line_weighted() -> None:
    # One confident character against ninety-nine characters of noise. Line-weighted -- what
    # both legacy engines did -- reports 0.5, which says "half of this page is trustworthy".
    client = StubTextract(response_of(line("x", 100.0), line("y" * 99, 0.0)))
    reading = TextractRecognizer(client).recognize(raster())
    assert reading.mean_confidence == pytest.approx(0.01)


@pytest.mark.parametrize(("reported", "expected"), [(150.0, 1.0), (-10.0, 0.0)])
def test_a_confidence_outside_the_scale_is_clamped_rather_than_rejected(
    reported: float,
    expected: float,
) -> None:
    # mean_confidence is a validated 0.0-1.0 field on the port, so an out-of-range figure
    # would otherwise surface as a pydantic error two layers up from a page that read fine.
    client = StubTextract(response_of(line("hello", reported)))
    assert TextractRecognizer(client).recognize(raster()).mean_confidence == expected


def test_a_blank_page_is_a_successful_empty_reading() -> None:
    reading = TextractRecognizer(StubTextract(response_of())).recognize(raster())
    assert reading.text == ""
    assert reading.mean_confidence is None
    assert reading.has_text is False


def test_a_reading_with_no_characters_reports_no_confidence_rather_than_zero() -> None:
    # None is the honest answer when there is nothing to be confident about; 0.0 would read
    # as a garbage reading and 1.0 as a perfect one.
    client = StubTextract(response_of(line("", 99.0)))
    assert TextractRecognizer(client).recognize(raster()).mean_confidence is None


def test_the_reading_is_attributed_to_the_raster_that_was_read() -> None:
    reading = TextractRecognizer(StubTextract()).recognize(raster("page-77"))
    assert reading.page_ref == "page-77"
    assert reading.provider_id == "aws-textract@1"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("ThrottlingException", 400),
        ("ProvisionedThroughputExceededException", 400),
        ("LimitExceededException", 400),
        ("InternalServerError", 500),
        ("ServiceUnavailable", 503),
        # An unrecognised code with a 5xx behind it: the service, not the request, is at
        # fault, so the status alone is enough to make it worth another attempt.
        ("SomethingNobodyHasSeenYet", 503),
        # A throttle whose response carries no metadata at all still classifies on its code.
        ("ThrottlingException", None),
    ],
)
def test_a_service_side_error_is_retryable(code: str, status: int | None) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        TextractRecognizer(StubTextract(error=client_error(code, status))).recognize(raster())
    assert caught.value.retryable is True
    assert caught.value.provider_id == "aws-textract@1"
    assert code in caught.value.detail


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("InvalidParameterException", 400),
        ("UnsupportedDocumentException", 400),
        ("DocumentTooLargeException", 400),
        ("BadDocumentException", 400),
        ("AccessDeniedException", 403),
        # No metadata and an unrecognised code: permanent is the honest default, because a
        # rejected document is rejected again on every attempt.
        ("SomethingNobodyHasSeenYet", None),
    ],
)
def test_a_rejected_document_is_not_retryable(code: str, status: int | None) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        TextractRecognizer(StubTextract(error=client_error(code, status))).recognize(raster())
    assert caught.value.retryable is False
    assert code in caught.value.detail


@pytest.mark.parametrize(
    "error",
    [
        EndpointConnectionError(endpoint_url="https://textract.us-west-2.amazonaws.com/"),
        ReadTimeoutError(endpoint_url="https://textract.us-west-2.amazonaws.com/"),
        ConnectionClosedError(endpoint_url="https://textract.us-west-2.amazonaws.com/"),
    ],
)
def test_a_service_that_could_not_be_reached_is_retryable(error: Exception) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        TextractRecognizer(StubTextract(error=error)).recognize(raster())
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "error",
    [NoCredentialsError(), ParamValidationError(report="Document is required")],
)
def test_a_misconfigured_caller_is_not_retryable(error: Exception) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        TextractRecognizer(StubTextract(error=error)).recognize(raster())
    assert caught.value.retryable is False


def test_no_botocore_exception_crosses_the_port() -> None:
    error = client_error("ThrottlingException", 400)
    with pytest.raises(RecognitionFailed) as caught:
        TextractRecognizer(StubTextract(error=error)).recognize(raster())
    assert not isinstance(caught.value, ClientError)
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"Blocks": 5},
        {"Blocks": [{"BlockType": "LINE"}]},
        {"Blocks": [{"BlockType": "LINE", "Text": "x", "Confidence": "high"}]},
    ],
)
def test_a_response_this_reader_cannot_use_is_a_permanent_failure(
    response: Mapping[str, Any],
) -> None:
    with pytest.raises(RecognitionFailed) as caught:
        TextractRecognizer(StubTextract(response)).recognize(raster())
    assert caught.value.retryable is False
    assert "could not read the DetectDocumentText response" in caught.value.detail


def test_one_instance_tolerates_concurrent_calls_from_several_threads() -> None:
    # The port mandates this because the app fans recognizers out. A shared client is safe to
    # use from many threads; what a fan-out would expose is per-call state kept on the
    # instance, which would show up here as one page's text attributed to another.
    recognizer = TextractRecognizer(EchoingTextract())
    pages = [raster(f"page-{index}", f"page-{index}".encode()) for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        readings = list(pool.map(recognizer.recognize, pages))
    assert [(reading.page_ref, reading.text) for reading in readings] == [
        (page.page_ref, page.page_ref) for page in pages
    ]


def test_in_region_builds_one_client_with_the_timeouts_as_construction_configuration() -> None:
    # There is no timeout on the port, so this is where one lives.
    factory = RecordingFactory()
    recognizer = TextractRecognizer.in_region("us-west-2", revision=4, build=factory)
    (args, kwargs) = factory.calls[0]
    # botocore builds a Config's attributes in __init__ from its own option table, so they are
    # invisible to a static reading of the class -- the same fact the Vision bindings have.
    config = cast("Any", kwargs["config"])
    assert args == ("textract",)
    assert kwargs["region_name"] == "us-west-2"
    assert config.connect_timeout == DEFAULT_CONNECT_TIMEOUT
    assert config.read_timeout == DEFAULT_READ_TIMEOUT
    assert config.retries == {"max_attempts": DEFAULT_MAX_ATTEMPTS, "mode": "standard"}
    assert recognizer.provider_id == "aws-textract@4"
    recognizer.recognize(raster())
    assert factory.client.calls, "the recognizer must use the client the factory built"


def test_in_region_passes_the_timeouts_it_is_given() -> None:
    factory = RecordingFactory()
    TextractRecognizer.in_region(
        "eu-west-1",
        connect_timeout=1.5,
        read_timeout=2.5,
        max_attempts=7,
        build=factory,
    )
    config = cast("Any", factory.calls[0][1]["config"])
    assert (config.connect_timeout, config.read_timeout) == (1.5, 2.5)
    assert config.retries == {"max_attempts": 7, "mode": "standard"}


def test_it_satisfies_the_text_recognizer_port() -> None:
    # A typed binding rather than an isinstance check: the port is not runtime_checkable, and
    # a structural check at runtime would only verify member names.
    recognizer: TextRecognizer = TextractRecognizer(StubTextract())
    reading = recognizer.recognize(raster())
    assert isinstance(reading, Recognition)
