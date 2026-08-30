"""AWS Textract-backed :class:`~rmspec.domain.ports.ocr.TextRecognizer`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, Self

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotocoreConnectionError

from rmspec.domain.errors import RecognitionFailed
from rmspec.domain.ports.ocr import Recognition
from rmspec.ocr._confidence import RecognizedLine, joined_text, mean_character_confidence

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rmspec.domain.ports.ocr import RasterImage

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_READ_TIMEOUT",
    "DEFAULT_REVISION",
    "PROVIDER",
    "DocumentTextDetector",
    "TextractRecognizer",
]

PROVIDER = "aws-textract"
"""Engine half of this adapter's provider slug, without the revision."""

DEFAULT_REVISION = 1
"""Current revision of this adapter's reading behaviour.

See :attr:`TextractRecognizer.provider_id` for what bumping it invalidates.
"""

DEFAULT_CONNECT_TIMEOUT = 5.0
"""Seconds to wait for the TCP connection, when :meth:`TextractRecognizer.in_region` builds it."""

DEFAULT_READ_TIMEOUT = 30.0
"""Seconds to wait for one ``DetectDocumentText`` response."""

DEFAULT_MAX_ATTEMPTS = 3
"""Attempts botocore itself makes before this adapter sees a throttle."""

_SERVICE = "textract"
"""The boto3 service name, for :meth:`TextractRecognizer.in_region`."""

_LINE_BLOCK = "LINE"
"""The one ``BlockType`` this adapter reads. Textract also returns PAGE and WORD blocks."""

_CONFIDENCE_SCALE = 100.0
"""Textract reports confidence on a 0 -- 100 scale; the port's field is 0.0 -- 1.0."""

_SERVER_ERROR_STATUS = 500
"""HTTP status at or above which the service, not the request, is at fault."""

_RETRYABLE_CODES = frozenset(
    {
        "InternalServerError",
        "LimitExceededException",
        "ProvisionedThroughputExceededException",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
        "ThrottlingException",
        "Throttling",
        "TooManyRequestsException",
    }
)
"""Service-side error codes worth trying again.

Everything absent from this set is treated as permanent for the same input, which is the
honest default: a rejected, unsupported or oversized document produces the same rejection on
every attempt, and reporting it as retryable spends the caller's one retry on a certainty.
"""


class DocumentTextDetector(Protocol):
    """The one boto3 Textract client method this adapter calls.

    Narrow on purpose. It is the whole reason no test in this package constructs a Textract
    client: a double is three lines, every branch below is reachable through it, and a real
    ``boto3.client("textract")`` satisfies it structurally with no wrapper.
    """

    def detect_document_text(self, **kwargs: object) -> Mapping[str, Any]:
        """Detect lines and words of text in one document.

        Parameters
        ----------
        **kwargs
            The wire request, ``Document={"Bytes": ...}`` here. Keyword-only and untyped
            because the shape belongs to the service's API model, not to this port.

        Returns
        -------
        Mapping[str, Any]
            The parsed ``DetectDocumentText`` response.
        """
        ...


def _client_error_is_retryable(exc: ClientError) -> bool:
    """Decide whether one service error is worth trying again.

    Reads the two facts botocore publishes on the exception -- the service's error code and
    the HTTP status -- and treats a throttle or any 5xx as transient. Both are read
    defensively: an error whose response carries neither is classified permanent rather than
    raising a second error from inside the handler for the first.

    Parameters
    ----------
    exc
        The error botocore raised.

    Returns
    -------
    bool
        Whether retrying could produce a different answer.
    """
    error = exc.response.get("Error") or {}
    metadata = exc.response.get("ResponseMetadata") or {}
    status = metadata.get("HTTPStatusCode")
    return str(error.get("Code", "")) in _RETRYABLE_CODES or (
        isinstance(status, int) and status >= _SERVER_ERROR_STATUS
    )


def _lines_from(response: Mapping[str, Any]) -> tuple[RecognizedLine, ...]:
    """Read the LINE blocks out of one ``DetectDocumentText`` response.

    Parameters
    ----------
    response
        The parsed response.

    Returns
    -------
    tuple[RecognizedLine, ...]
        One line per LINE block, with confidence rescaled from the service's 0 -- 100 to the
        port's 0.0 -- 1.0. Empty when the page carried no text, which Textract reports as a
        response holding only a PAGE block.

    Raises
    ------
    KeyError
        A block or the response was missing a member this reader needs.
    TypeError
        The response was not shaped like a response at all.
    ValueError
        A confidence was not a number.
    """
    return tuple(
        RecognizedLine(
            text=str(block["Text"]),
            confidence=float(block["Confidence"]) / _CONFIDENCE_SCALE,
        )
        for block in response["Blocks"]
        if block["BlockType"] == _LINE_BLOCK
    )


class TextractRecognizer:
    """Recognise handwriting on one rendered page with Amazon Textract.

    Satisfies :class:`~rmspec.domain.ports.ocr.TextRecognizer`. Scope ``APP``: one client per
    process, every call stateless.

    Concurrency
    -----------
    Thread-safe with no lock, and which of the two cases that is matters: a boto3 client is
    not thread-safe to *create* but is thread-safe to *use*, and this class only ever uses
    one. The client is built once -- by the caller, or by :meth:`in_region` at composition --
    and is never rebuilt, replaced or lazily created on a call, so the unsafe operation
    cannot happen concurrently and there is nothing to serialise. Had the client been created
    on first use, the lock would belong here rather than being exported to every call site,
    which is what the port requires of an adapter holding a thread-hostile handle.

    Timeouts
    --------
    The port publishes no timeout, because a value there would be unenforceable for an
    in-process engine and false for a blocking client. Here it is construction
    configuration: :meth:`in_region` folds connect and read timeouts and botocore's own retry
    count into the client's ``Config``, and a caller who builds its own client sets them
    there.

    What is not raised
    ------------------
    No botocore exception crosses the port -- each of the three families below becomes
    ``RecognitionFailed`` -- and there is no "engine unavailable" error at all. A missing
    package is a composition failure that names the package and its extra
    (:func:`rmspec.ocr.availability.require_backends`), and a deliberately degraded set of
    engines is a visible binding in the composition root.
    """

    def __init__(
        self,
        client: DocumentTextDetector,
        /,
        *,
        revision: int = DEFAULT_REVISION,
    ) -> None:
        self._client = client
        self._provider_id = f"{PROVIDER}@{revision}"

    @classmethod
    def in_region(
        cls,
        region_name: str,
        /,
        *,
        revision: int = DEFAULT_REVISION,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        build: Callable[..., DocumentTextDetector] = boto3.client,
    ) -> Self:
        """Build a recognizer over a Textract client in one region.

        This package owns ``boto3``, so this is where a Textract client is created; the
        composition root names a region and timeouts and never speaks to AWS directly.

        Parameters
        ----------
        region_name
            The AWS region to call. The port carries no region -- an app that read one for a
            cache key would have imported AWS by another name -- so it lives here.
        revision
            Revision to fold into :attr:`provider_id`.
        connect_timeout
            Seconds to wait for the connection.
        read_timeout
            Seconds to wait for one response.
        max_attempts
            Attempts botocore makes before this adapter reports a throttle.
        build
            How to build the client. Injected for one reason: no test in this package may
            construct a Textract client, not once and not in a fixture, and a default-only
            factory would make this method the one line no test can reach.

        Returns
        -------
        Self
            A recognizer over a client configured with those timeouts.
        """
        config = Config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries={"max_attempts": max_attempts, "mode": "standard"},
        )
        return cls(
            build(_SERVICE, region_name=region_name, config=config),
            revision=revision,
        )

    @property
    def provider_id(self) -> str:
        """Return this engine's stable identity slug.

        Returns
        -------
        str
            ``"aws-textract@<revision>"``. The revision is part of the slug because the app
            folds this exact string into its cache key, so bumping it invalidates every row
            produced by the older reading behaviour instead of silently reusing it.
        """
        return self._provider_id

    def recognize(self, image: RasterImage, /) -> Recognition:
        """Recognise the text on one rendered page.

        Parameters
        ----------
        image
            The rendered page. Its bytes are sent inline as ``Document={"Bytes": ...}``;
            nothing is staged in S3 and nothing is written to disk.

        Returns
        -------
        Recognition
            This engine's reading, built with
            :meth:`~rmspec.domain.ports.ocr.Recognition.attributed` so its ``page_ref`` is
            derived from the raster that was read rather than filled by hand -- a recognizer
            that returned another page's slot would have the app cache one page's text under
            another. A blank page is a successful empty reading: ``text=""`` with
            ``mean_confidence=None``, because there is nothing to be confident about.

        Raises
        ------
        RecognitionFailed
            The service refused the request, could not be reached, or answered with a body
            this reader could not use. ``retryable`` is the only distinction a caller acts
            on: a throttle, a 5xx and a connection or timeout failure are retryable, while a
            rejected, unsupported or oversized document, a credentials problem and an
            unreadable response are not.
        """
        try:
            response = self._client.detect_document_text(Document={"Bytes": image.data})
        except ClientError as exc:
            raise RecognitionFailed(
                provider_id=self._provider_id,
                detail=str(exc),
                retryable=_client_error_is_retryable(exc),
            ) from exc
        except (BotocoreConnectionError, HTTPClientError) as exc:
            raise RecognitionFailed(
                provider_id=self._provider_id,
                detail=str(exc),
                retryable=True,
            ) from exc
        except BotoCoreError as exc:
            raise RecognitionFailed(
                provider_id=self._provider_id,
                detail=str(exc),
                retryable=False,
            ) from exc
        try:
            lines = _lines_from(response)
        except (KeyError, TypeError, ValueError) as exc:
            raise RecognitionFailed(
                provider_id=self._provider_id,
                detail=f"could not read the DetectDocumentText response: {exc!r}",
                retryable=False,
            ) from exc
        return Recognition.attributed(
            image,
            provider_id=self._provider_id,
            text=joined_text(lines),
            mean_confidence=mean_character_confidence(lines),
        )
