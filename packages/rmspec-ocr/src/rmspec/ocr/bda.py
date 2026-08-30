"""Amazon Bedrock Data Automation-backed :class:`~rmspec.domain.ports.ocr.TextRecognizer`.

Reads one rendered page through the **synchronous** ``InvokeDataAutomation`` operation: bytes in,
inline standard output, nothing staged in S3 and no job to poll. The asynchronous sibling would
have needed all three, plus a bucket in the settings and a polling loop in the app, and would
have made this adapter unable to satisfy a port whose one method returns a reading.

Documents in the sync API, which the user guide denies
-----------------------------------------------------
``bda-using-api`` states that ``InvokeDataAutomation`` "only supports processing images" and
warns that an image "semantically classified as a document" raises an error -- then, in the next
paragraph, gives "the structure of the JSON request for both image and document". Two sibling
pages (``bda-limits``, ``bda-output-documents``) publish sync *document* requirements and a sync
document response. Measured against firmware-independent reality by ``probes/bda_sync_document``:
a 2372x3080 PNG of 192 rmspec ink strokes returned ``semanticModality: "DOCUMENT"`` with a full
document standard output and no error, while ``metadata.file_type`` was ``IMAGE``. So the
"images only" sentence is stale and this adapter sends a document.

Three preconditions the user guide never states, each found by calling
---------------------------------------------------------------------
- ``dataAutomationConfiguration`` is optional in the API model and mandatory in practice: omit
  it and the service answers ``At least one of project or inline blueprints must be provided``.
- A project carries a ``projectType`` of ``ASYNC`` or ``SYNC``, and this operation refuses the
  former with ``Sync API only supports SYNC project type``. The setting therefore has to name a
  SYNC-type project, and no amount of reading tells a user that.
- A SYNC project accepts exactly **one** document text format; two is
  ``cannot have more than 1 document text format types``.

Why the words are read and the lines are not
--------------------------------------------
The standard output carries both ``text_lines`` and ``text_words``, both with a ``confidence``
the published schema omits entirely. The line-level number is not a measurement: every line of
the probe page came back at exactly ``0.01`` while the words beneath those same lines ranged
0.869 to 1.0 -- and the single lowest word, ``0.869``, was the one token BDA actually got wrong
(the digit ``0`` read as the letter ``O``). Folding ``0.01`` through
:func:`~rmspec.ocr._confidence.mean_character_confidence` would report every page at
approximately 0.01, which that module documents as reading like a garbage transcription. So this
adapter groups ``text_words`` by ``line_id`` and never trusts a line's own confidence.

When a project was configured without ``WORD`` granularity there are no words to read. That is
not an error and not a zero: the lines are returned with ``confidence=None``, which the port and
:mod:`rmspec.ocr._confidence` already define as "this engine reported no confidence signal".

Why the retry vocabulary is not shared with the other two AWS adapters
---------------------------------------------------------------------
:mod:`rmspec.ocr.textract` and :mod:`rmspec.ocr._bedrock` each classify a ``ClientError`` with
their own code set, because the three services spell their transient failures differently --
Textract says ``ServiceUnavailable`` and ``InternalServerError`` where this one says
``ServiceUnavailableException`` and ``InternalServerException``. A shared set would have to be
the union, and a union classifies a permanent failure of one service as retryable because
another service uses that word for a transient one. The thing worth sharing between recognizers
is the arithmetic, and that already lives in one module.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, NamedTuple, Protocol, Self

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotocoreConnectionError

from rmspec.domain.errors import RecognitionFailed
from rmspec.domain.ports.ocr import Recognition
from rmspec.ocr._confidence import RecognizedLine, joined_text, mean_character_confidence

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rmspec.domain.ports.ocr import RasterImage

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_PROFILE",
    "DEFAULT_READ_TIMEOUT",
    "DEFAULT_REVISION",
    "PROJECT_RESOURCE",
    "PROJECT_TYPE",
    "PROVIDER",
    "STAGE",
    "SYNC_PROJECT_CONFIG",
    "BdaRecognizer",
    "DataAutomationInvoker",
    "profile_arn_for",
]

PROVIDER = "aws-bda"
"""Engine half of this adapter's provider slug, without the revision."""

DEFAULT_REVISION = 1
"""Current revision of this adapter's reading behaviour.

Bumping it changes :attr:`BdaRecognizer.provider_id`, which the app folds into the OCR cache key,
so every row this engine wrote is invalidated. Bump it when what the adapter *reads* changes --
the move from lines to words would have been such a change -- and not when a docstring does.
"""

DEFAULT_CONNECT_TIMEOUT = 5.0
"""Seconds to wait for the TCP connection, when :meth:`BdaRecognizer.for_project` builds it."""

DEFAULT_READ_TIMEOUT = 60.0
"""Seconds to wait for one ``InvokeDataAutomation`` response.

Twice :data:`rmspec.ocr.textract.DEFAULT_READ_TIMEOUT`, and chosen as headroom rather than
measured: the sync operation runs a generative pipeline over the page where
``DetectDocumentText`` runs a detector, and the service's own guidance for speeding it up is to
turn output features off. A run that needs a tighter bound passes one.
"""

DEFAULT_MAX_ATTEMPTS = 3
"""Attempts botocore itself makes before this adapter sees a throttle."""

DEFAULT_PROFILE = "us.data-automation-v1"
"""The data automation profile id :func:`profile_arn_for` composes when none is given.

Measured in ``us-west-2``, where it is what the service accepts. The ``us.`` prefix is a region
family rather than a constant, so a project in a European or Asia Pacific region needs its own
profile id -- and since no ``ListDataAutomationProfiles`` operation exists to discover one, the
setting that supplies it is the only way to be right there.
"""

STAGE = "LIVE"
"""The project stage this adapter invokes.

``LIVE`` rather than ``DEVELOPMENT`` because a recognizer reading a human's real notebook is
production traffic. A project published only to ``DEVELOPMENT`` is a configuration a user can
see in the ARN they set, and pointing at it fails loudly at the first call.
"""

PROJECT_TYPE = "SYNC"
"""The project type this operation accepts, and the one the console does not create.

``CreateDataAutomationProject`` defaults to ``ASYNC``, and the sync operation refuses one with
``Sync API only supports SYNC project type``. Neither the field nor the constraint appears in the
user guide's API page; both were found by being refused.
"""

PROJECT_RESOURCE = "data-automation-project"
"""The ARN resource type a project ARN carries, and the one :func:`profile_arn_for` requires."""

SYNC_PROJECT_CONFIG: Final[Mapping[str, Any]] = {
    "document": {
        "extraction": {
            "granularity": {"types": ["PAGE", "ELEMENT", "WORD", "LINE"]},
            "boundingBox": {"state": "ENABLED"},
        },
        "generativeField": {"state": "DISABLED"},
        "outputFormat": {
            "textFormat": {"types": ["PLAIN_TEXT"]},
            "additionalFileFormat": {"state": "DISABLED"},
        },
    },
}
"""The standard-output configuration a project must have for this adapter to read it properly.

Data rather than a call: nothing in this package creates a project, because provisioning an
account resource is not something a recognizer does. It lives here anyway, beside the adapter
whose reading it decides, so the two cannot drift -- ``probes/bda_project.py`` creates a project
*from this constant*, and a project made any other way is one this adapter may read worse.

Every entry is load-bearing:

``WORD`` granularity
    The only reason a reading has a confidence at all. Without it the response carries lines
    whose confidence is the constant ``0.01`` placeholder, and :func:`_reading_from` correctly
    reports ``None`` rather than trusting it -- a usable transcription with no confidence signal.
one text format
    Not a preference. A ``SYNC`` project is refused outright with ``Sync project standard output
    configuration cannot have more than 1 document text format types``.
``PLAIN_TEXT`` rather than ``MARKDOWN``
    The port carries text, and markdown's ``#`` and ``|`` would be characters no pen wrote.
``generativeField`` disabled
    A 250-word summary of a handwritten page is latency the recognizer pays for and the port has
    nowhere to put. The service's own guidance for speeding this operation up is to turn it off.
``LINE`` granularity and bounding boxes
    Not read today. Kept because they cost nothing measurable and because a project is a resource
    a user creates once: a later revision of this adapter that wanted line geometry would
    otherwise need every existing project recreated.

Typed ``Mapping[str, Any]`` rather than left to inference, for the same reason
:meth:`DataAutomationInvoker.invoke_data_automation` takes ``**kwargs: object``: the shape belongs
to the service's API model, and a structural type inferred from this literal makes reading
``["document"]["extraction"]["granularity"]`` a type error while the service accepts it happily.
"""

_PROFILE_RESOURCE = "data-automation-profile"
"""The ARN resource type a profile ARN carries."""

_SERVICE = "bedrock-data-automation-runtime"
"""The boto3 service name, for :meth:`BdaRecognizer.for_project`."""

_ARN_FIELDS = 6
"""Fields in ``arn:partition:service:region:account:resource``, split on ``:``."""

_SERVER_ERROR_STATUS = 500
"""HTTP status at or above which the service, not the request, is at fault."""

_RETRYABLE_CODES = frozenset(
    {
        "InternalServerException",
        "ServiceUnavailableException",
        "ThrottlingException",
    }
)
"""The service-side error codes worth trying again, from this operation's own error shapes.

``InvokeDataAutomation`` declares exactly five: these three plus ``ValidationException`` and
``AccessDeniedException``. The two absent ones are permanent for the same input -- a malformed
request and a denied caller each produce the same answer on every attempt -- so reporting them
as retryable would spend the caller's one retry on a certainty.
"""


class DataAutomationInvoker(Protocol):
    """The one boto3 Data Automation runtime method this adapter calls.

    Narrow on purpose, and for the same reason
    :class:`~rmspec.ocr.textract.DocumentTextDetector` is: it is what lets no test in this
    package construct a client. A double is three lines, every branch below is reachable through
    it, and a real ``boto3.client("bedrock-data-automation-runtime")`` satisfies it structurally
    with no wrapper.
    """

    def invoke_data_automation(self, **kwargs: object) -> Mapping[str, Any]:
        """Process one file synchronously and return the insights inline.

        Parameters
        ----------
        **kwargs
            The wire request. Keyword-only and untyped because the shape belongs to the
            service's API model, not to this port.

        Returns
        -------
        Mapping[str, Any]
            The parsed ``InvokeDataAutomation`` response.
        """
        ...


def profile_arn_for(project_arn: str, /, *, profile: str = DEFAULT_PROFILE) -> str:
    """Compose the data automation profile ARN a project's own ARN implies.

    The operation requires a profile ARN, no API lists the profiles an account has, and asking a
    user for a second ARN whose partition, region and account must match the first is a setting
    that exists only to be typed wrong. Those three fields are facts carried by the project ARN,
    so only the profile id is a choice, and that one has a default and an override.

    Parameters
    ----------
    project_arn
        A ``data-automation-project`` ARN, as ``RMSPEC_BDA_PROJECT_ARN`` supplies it.
    profile
        The profile id to name. See :data:`DEFAULT_PROFILE` for why it is not derived.

    Returns
    -------
    str
        ``arn:<partition>:bedrock:<region>:<account>:data-automation-profile/<profile>``.

    Raises
    ------
    ValueError
        *project_arn* is not a project ARN. Raised rather than a domain error because the name
        of the setting that supplied it is the composition root's knowledge, not this package's;
        the caller translates. Guessing a partition or an account from a malformed ARN would
        send a request that fails later and further away.
    """
    fields = project_arn.split(":", _ARN_FIELDS - 1)
    if len(fields) != _ARN_FIELDS or fields[0] != "arn":
        msg = f"not an ARN: {project_arn!r}"
        raise ValueError(msg)
    _, partition, service, region, account, resource = fields
    if service != "bedrock" or not resource.startswith(f"{PROJECT_RESOURCE}/"):
        msg = (
            f"not a {PROJECT_RESOURCE} ARN: {project_arn!r}. The profile ARN is composed from "
            f"this one's partition, region and account, so a different resource would compose "
            f"a profile in the wrong place."
        )
        raise ValueError(msg)
    return f"arn:{partition}:bedrock:{region}:{account}:{_PROFILE_RESOURCE}/{profile}"


def _client_error_is_retryable(exc: ClientError) -> bool:
    """Decide whether one service error is worth trying again.

    Parameters
    ----------
    exc
        The error botocore raised.

    Returns
    -------
    bool
        Whether retrying could produce a different answer. Reads the service's error code and
        the HTTP status, both defensively: an error carrying neither is classified permanent
        rather than raising a second error from inside the handler for the first.
    """
    error = exc.response.get("Error") or {}
    metadata = exc.response.get("ResponseMetadata") or {}
    status = metadata.get("HTTPStatusCode")
    return str(error.get("Code", "")) in _RETRYABLE_CODES or (
        isinstance(status, int) and status >= _SERVER_ERROR_STATUS
    )


class _Reading(NamedTuple):
    """One page as this adapter read it, at the two levels the response reports.

    Attributes
    ----------
    lines
        One entry per line of the page, in reading order, each carrying the joined text of its
        words. This is what becomes :attr:`~rmspec.domain.ports.ocr.Recognition.text`.
    measured
        The level whose confidence the service actually measured -- the words when the project
        enabled ``WORD`` granularity, and otherwise the lines, whose confidence is then ``None``
        rather than the placeholder the service sent. This is what becomes ``mean_confidence``.

    Two fields rather than one because the two reductions want different weights. The port's
    mean is per *character*, so taking it over the words is exact, while taking it over lines
    would weight each line by a length that includes the separators this adapter inserted --
    characters the service never scored.
    """

    lines: tuple[RecognizedLine, ...]
    measured: tuple[RecognizedLine, ...]


def _lines_from_words(words: Sequence[Mapping[str, Any]]) -> _Reading:
    """Group scored words into lines.

    Parameters
    ----------
    words
        The ``text_words`` entries, each with ``text``, ``confidence`` and ``line_id``.

    Returns
    -------
    _Reading
        Lines in first-appearance order, which is reading order: the service emits words with an
        ascending ``reading_order`` and this preserves it without trusting a second field to
        agree with the first.

    Raises
    ------
    KeyError
        A word was missing a member this reader needs.
    TypeError
        The entries were not shaped like entries.
    ValueError
        A confidence was not a number.
    """
    grouped: dict[str, list[RecognizedLine]] = {}
    for word in words:
        grouped.setdefault(str(word["line_id"]), []).append(
            RecognizedLine(text=str(word["text"]), confidence=float(word["confidence"]))
        )
    return _Reading(
        lines=tuple(
            RecognizedLine(
                text=" ".join(word.text for word in line),
                confidence=mean_character_confidence(line),
            )
            for line in grouped.values()
        ),
        measured=tuple(word for line in grouped.values() for word in line),
    )


def _reading_from(response: Mapping[str, Any]) -> _Reading:
    """Read one page out of an ``InvokeDataAutomation`` response.

    Parameters
    ----------
    response
        The parsed response. ``standardOutput`` inside it is a JSON *string* rather than a
        structure -- the API model types it as one, and the service sends one.

    Returns
    -------
    _Reading
        The page's lines, and the level that carries its confidence. Empty for a blank page,
        which is a successful empty reading and not a failure.

    Raises
    ------
    KeyError
        The response was missing a member this reader needs.
    TypeError
        The response was not shaped like a response at all.
    ValueError
        ``standardOutput`` was not JSON, or a confidence was not a number.
    """
    lines: list[RecognizedLine] = []
    measured: list[RecognizedLine] = []
    for segment in response["outputSegments"]:
        parsed = json.loads(segment["standardOutput"])
        words = parsed.get("text_words")
        if words:
            reading = _lines_from_words(words)
        else:
            reading = _Reading(
                lines=tuple(
                    RecognizedLine(text=str(line["text"]), confidence=None)
                    for line in parsed.get("text_lines") or ()
                ),
                measured=(),
            )
        lines.extend(reading.lines)
        measured.extend(reading.measured)
    return _Reading(lines=tuple(lines), measured=tuple(measured) or tuple(lines))


class BdaRecognizer:
    """Recognise handwriting on one rendered page with Bedrock Data Automation.

    Satisfies :class:`~rmspec.domain.ports.ocr.TextRecognizer`. Scope ``APP``: one client per
    process, every call stateless.

    Concurrency
    -----------
    Thread-safe with no lock, for the reason spelled out on
    :class:`~rmspec.ocr.textract.TextractRecognizer`: a boto3 client is unsafe to *create*
    concurrently and safe to *use*, and this class only ever uses one. The client is built once
    -- by the caller, or by :meth:`for_project` at composition -- and never rebuilt, replaced or
    lazily created on a call, so the unsafe operation cannot happen concurrently.

    Timeouts
    --------
    Construction configuration, not a port concern. :meth:`for_project` folds connect and read
    timeouts and botocore's retry count into the client's ``Config``; a caller building its own
    client sets them there.

    What is not raised
    ------------------
    No botocore exception crosses the port -- each of the three families below becomes
    ``RecognitionFailed`` -- and there is no "engine unavailable" error. A missing package is a
    composition failure naming the package and its extra, and a deliberately degraded set of
    engines is a visible binding in the composition root.
    """

    def __init__(
        self,
        client: DataAutomationInvoker,
        /,
        *,
        project_arn: str,
        profile_arn: str,
        revision: int = DEFAULT_REVISION,
    ) -> None:
        self._client = client
        self._project_arn = project_arn
        self._profile_arn = profile_arn
        self._provider_id = f"{PROVIDER}@{revision}"

    @classmethod
    def for_project(
        cls,
        project_arn: str,
        /,
        *,
        region_name: str,
        profile: str = DEFAULT_PROFILE,
        revision: int = DEFAULT_REVISION,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        build: Callable[..., DataAutomationInvoker] = boto3.client,
    ) -> Self:
        """Build a recognizer over a Data Automation runtime client for one project.

        This package owns ``boto3``, so this is where the client is created; the composition
        root names a project, a region and timeouts and never speaks to AWS directly.

        Parameters
        ----------
        project_arn
            The **SYNC-type** project to invoke. An ``ASYNC`` project is refused by the service
            with ``Sync API only supports SYNC project type``, which is not checkable here
            without a control-plane call this adapter has no business making.
        region_name
            The AWS region to call. Not derived from *project_arn* even though the ARN carries
            one: a client whose endpoint disagreed with its ARN would be a configuration error
            worth seeing, not one worth silently repairing.
        profile
            The profile id to compose a profile ARN from. See :data:`DEFAULT_PROFILE`.
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
            construct one, not once and not in a fixture, and a default-only factory would make
            this method the one line no test can reach.

        Returns
        -------
        Self
            A recognizer over a client configured with those timeouts.

        Raises
        ------
        ValueError
            *project_arn* is not a project ARN. See :func:`profile_arn_for`.
        """
        # Composed before the client is built, not inside the constructor call. Python evaluates
        # a positional argument before a keyword one, so the obvious spelling built a client --
        # a credential chain and a connection pool -- for a project whose ARN was about to be
        # refused on the next line.
        profile_arn = profile_arn_for(project_arn, profile=profile)
        config = Config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries={"max_attempts": max_attempts, "mode": "standard"},
        )
        return cls(
            build(_SERVICE, region_name=region_name, config=config),
            project_arn=project_arn,
            profile_arn=profile_arn,
            revision=revision,
        )

    @property
    def provider_id(self) -> str:
        """Return this engine's identity slug, e.g. ``aws-bda@1``.

        Returns
        -------
        str
            Stable for the lifetime of the process. The project ARN is deliberately **not** in
            it: two projects configured alike read a page alike, and folding an account id into
            the OCR cache key would make every cached page unreadable after a project is
            recreated with identical settings.
        """
        return self._provider_id

    def recognize(self, image: RasterImage, /) -> Recognition:
        """Recognise the text on one rendered page.

        Parameters
        ----------
        image
            The rendered page. Its bytes are sent inline as ``inputConfiguration={"bytes": ...}``
            and the output comes back inline, so nothing is staged in S3, nothing is written to
            disk and there is no job to poll. ``outputConfiguration`` is deliberately absent:
            supplying an S3 uri there makes the service write the output to the bucket *instead*
            of returning it.

        Returns
        -------
        Recognition
            This engine's reading, built with
            :meth:`~rmspec.domain.ports.ocr.Recognition.attributed` so its ``page_ref`` is
            derived from the raster that was read rather than filled by hand. A blank page is a
            successful empty reading: ``text=""`` with ``mean_confidence=None``.

        Raises
        ------
        RecognitionFailed
            The service refused the request, could not be reached, or answered with a body this
            reader could not use. ``retryable`` is the only distinction a caller acts on: a
            throttle, a 5xx and a connection or timeout failure are retryable, while a rejected
            request, a denied caller and an unreadable response are not.
        """
        try:
            response = self._client.invoke_data_automation(
                inputConfiguration={"bytes": image.data},
                dataAutomationProfileArn=self._profile_arn,
                dataAutomationConfiguration={
                    "dataAutomationProjectArn": self._project_arn,
                    "stage": STAGE,
                },
            )
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
            reading = _reading_from(response)
        except (KeyError, TypeError, ValueError) as exc:
            raise RecognitionFailed(
                provider_id=self._provider_id,
                detail=f"could not read the InvokeDataAutomation response: {exc!r}",
                retryable=False,
            ) from exc
        return Recognition.attributed(
            image,
            provider_id=self._provider_id,
            text=joined_text(reading.lines),
            mean_confidence=mean_character_confidence(reading.measured),
        )
