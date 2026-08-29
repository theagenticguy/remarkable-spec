"""The typed error tree for the whole workspace, plus the degradation vocabulary.

Every failure this codebase reports is an instance of :class:`RmspecError`. Nothing in
``rmspec.domain`` or ``rmspec.app`` raises a bare stdlib exception, and no adapter lets a
third-party exception cross its boundary: ``rmscene.UnexpectedBlockError``,
``sqlite3.OperationalError``, ``httpx.ConnectError``, ``paramiko.AuthenticationException``
and ``botocore.exceptions.ClientError`` are all translated here, chained through
``__cause__`` so the original is still in the traceback and still absent from the type.
``except RmspecError`` at the CLI boundary is therefore the one broad catch the
architecture permits, and it replaces the 27 broad ``except Exception`` blocks the legacy
command bodies used.

This module is the union of the error lists the seven slice designers produced, deduped
against what the port modules actually ship. Names appearing in a ``Raises`` section of
:mod:`rmspec.domain.ports` are reproduced here verbatim -- those docstrings are the
contract and this file follows them, which is why the suffixes are mixed: the persistence
slice wrote ``StoreUnavailableError`` while the formats slice wrote
``DocumentStoreUnavailable``. Proposals the port modules deleted on purpose are *not*
resurrected: ``EmptyPageSet``, ``PageCountMismatch``, ``PdfPageCountMismatch``,
``PdfSourceEncrypted``, ``DestinationExists``, ``DestinationNotWritable``,
``InvalidResolution`` and ``RecognizerTransportError`` were each rejected with a reason in
the module that owned them, most often because a pydantic constraint already makes the
state unconstructible. An error class for an unrepresentable state is dead weight in a
tree the type checker walks at error level.

Four defects shaped the hierarchy
---------------------------------
The app layer querying SQLite directly is answered by :class:`PersistenceError` and its
children being the *only* storage failures in the tree: there is no ``sqlite3`` in any
signature, and :class:`StoreSchemaMismatchError` turns hand-mirrored SQL drifting away
from the row models into a loud, named failure at first use instead of a silently wrong
object.

Three copies of one Bedrock call under two names collapse into :class:`ModelError` and its
five children, which describe what the provider did -- unreachable, denied, throttled,
rejected, answered unreadably -- rather than which copy of the call site was running.
:class:`ModelUnavailable` is the fifth because four could not tell an outage from a
throttle, and an adapter with no name for one either reports a permanently dead endpoint as
retryable or lets ``httpx.ConnectError`` cross the port.

Caches keyed on ``rm_hash`` alone are answered by omission. There is no
``CacheKeyMismatchError``: the model id, prompt version and render DPI are part of the
cache key model itself, so a changed prompt is a cache *miss* -- reported as
``DegradationKind.CACHE_MISS_KEY_CHANGED`` -- and a row that cannot be reconstructed is
:class:`StoredRecordUnreadableError`.

Lazy function-local imports are answered by :class:`MissingDependencyError`, which hangs
directly off the root rather than under a slice base. That placement is deliberate: a use
case's ``except ExportError`` must not be able to swallow a wiring bug. It carries the
import name, the extra that provides it and the feature that wanted it, and the
composition root raises it during the container's eager resolution pass -- after folding
:class:`~rmspec.domain.ports.errors.DependencyProbe` over an adapter's requirements and
before any use case runs.

Errors and degradations are two halves of one contract
-----------------------------------------------------
Where the legacy code substituted a plausible value and logged at debug level, the
replacement is either an error in this tree or a :class:`Degradation` travelling back
inside a use-case result. :class:`DegradationKind` is closed, so naming a new silent
fallback requires a reviewed change to this file. Slices that need a richer vocabulary own
their own closed enum next to the port that produces it --
``ports.device.SkipReason`` and ``ports.render.RenderNoticeCode`` -- and this enum stays
small enough to be the CLI's summary line.

Notes
-----
Third-party dependency: pydantic only, for :class:`Degradation` and
:class:`DocumentCandidate`. No ``rmscene``, ``httpx``, ``sqlite3``, ``boto3`` or ``cairo``,
not even under ``TYPE_CHECKING``, and no ``pathlib.Path`` in any field -- a filesystem
location is an adapter's identity for a resource, so it is carried as an opaque ``str``
that is displayed and logged, never reopened.

:func:`exit_code` is the single table from error class to process exit status. The domain
decides, the CLI renders.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every name below that lacks an ``Error`` suffix is quoted from a ``Raises`` section of a
# port module in :mod:`rmspec.domain.ports`, which was authored first and is the contract
# adapters implement. Renaming them here to satisfy N818 would make seven port modules
# document errors that do not exist, so the rule is disabled for this file only.
# ruff: noqa: N818

__all__ = [
    "AllRecognizersFailed",
    "AmbiguousDocument",
    "ArtifactWriteFailed",
    "ArtifactWriteReason",
    "AuditWriteFailedError",
    "BackgroundUnreadable",
    "ConfigurationError",
    "CorruptPageData",
    "Degradation",
    "DegradationKind",
    "DeviceAuthFailed",
    "DeviceDocumentNotFound",
    "DeviceError",
    "DeviceOperationUnsupported",
    "DeviceProtocolError",
    "DeviceTransferInterrupted",
    "DeviceUnreachable",
    "DeviceUploadRejected",
    "DocumentCandidate",
    "DocumentNotFound",
    "DocumentSourceError",
    "DocumentStoreUnavailable",
    "ExportError",
    "FormatError",
    "InvalidSettingError",
    "MalformedDeviceMetadata",
    "MalformedDocument",
    "MissingDependencyError",
    "ModelAccessDenied",
    "ModelError",
    "ModelRejectedRequest",
    "ModelResponseMalformed",
    "ModelThrottled",
    "ModelUnavailable",
    "NoTextRecognized",
    "OcrError",
    "PageNotFound",
    "PdfCompositionFailed",
    "PdfPageOutOfRange",
    "PdfSourceUnreadable",
    "PersistenceError",
    "RasterizationFailed",
    "RecognitionFailed",
    "RenderError",
    "RmspecError",
    "StoreSchemaMismatchError",
    "StoreUnavailableError",
    "StoredRecordUnreadableError",
    "TransportKind",
    "UnsupportedPageFormat",
    "UnsupportedPenType",
    "UsageError",
    "XochitlDirNotConfigured",
    "exit_code",
]


class TransportKind(StrEnum):
    """A way of reaching an attached tablet, as a closed set the shell can advise on.

    Lives with the errors rather than with the device ports because
    :class:`DeviceOperationUnsupported` is the only domain type that has to name a
    transport: capability asymmetry is otherwise expressed as which ports exist, never as
    data a caller branches on.
    """

    USB_WEB_API = "usb_web_api"
    """The firmware's five-route HTTP API on the USB network interface."""

    SSH = "ssh"
    """A shell and file transfer over SSH to the device's BusyBox userland."""

    LOCAL_MIRROR = "local_mirror"
    """An already-pulled copy of a xochitl tree on this host."""


class ArtifactWriteReason(StrEnum):
    """Why a sink refused or lost a write, as one closed set instead of one class per errno.

    Carried by :class:`ArtifactWriteFailed` so the CLI can phrase advice -- "pass
    ``--force``", "free some space" -- without the tree growing a subclass per
    ``OSError`` code and without the domain naming ``EEXIST`` or ``EACCES``.
    """

    ALREADY_PRESENT = "already_present"
    """A different artifact holds the name and the sink's policy forbids overwriting."""

    NOT_WRITABLE = "not_writable"
    """The destination cannot be created or replaced, decided before any backend work."""

    OUT_OF_SPACE = "out_of_space"
    """The filesystem refused the bytes."""

    INTERRUPTED = "interrupted"
    """The write started and did not finish; the sink removed its temporary file."""


class DegradationKind(StrEnum):
    """The closed set of substitutions a run may make instead of failing.

    Every member replaces a place where the legacy code guessed and logged. Adding one is
    a reviewed change to the domain, which is the point: a silent fallback cannot be
    introduced by an adapter on its own.
    """

    CATALOG_ENTRY_SKIPPED = "catalog_entry_skipped"
    """An entry was unreadable while enumerating, so the listing omits it. Naming a
    document explicitly raises :class:`MalformedDocument` instead."""

    PAGE_NOT_ANNOTATED = "page_not_annotated"
    """A page the document lists has no annotation artifact, so it was skipped rather
    than pushed through the pipeline as an empty page."""

    AMBIGUOUS_AUTO_RESOLVED = "ambiguous_auto_resolved"
    """More than one document matched and non-strict mode picked one. Strict mode raises
    :class:`AmbiguousDocument` with the candidates instead."""

    PDF_PAGE_INDEX_FALLBACK = "pdf_page_index_fallback"
    """A page had no entry in the PDF redirection map, so its position in the page list
    was used as the background page index."""

    PDF_PAGE_COUNT_ESTIMATED = "pdf_page_count_estimated"
    """A PDF's page count came from scanning bytes rather than from a parser."""

    CACHE_MISS_KEY_CHANGED = "cache_miss_key_changed"
    """A cached row existed for this document but under a different key -- another model
    id, prompt version or render DPI -- so the result was recomputed."""

    AUDIT_NOT_RECORDED = "audit_not_recorded"
    """The operation succeeded but :class:`AuditWriteFailedError` stopped its history
    entry from landing. The one degradation raised from a failure rather than a guess."""


class DocumentCandidate(BaseModel, frozen=True, extra="forbid"):
    """One document a selector matched, in the only detail an ambiguity message needs.

    Carried by :class:`AmbiguousDocument` so the shell formats the candidate list and the
    human chooses. The legacy resolver ranked candidates by page count and last-modified
    date and silently returned the winner, which is the decision this type moves out of
    the domain.
    """

    uuid: str = Field(min_length=1)
    """The document's identifier, which is what the user retypes to disambiguate."""

    name: str
    """The name shown in the tablet UI, which is what the user recognises."""


class Degradation(BaseModel, frozen=True, extra="forbid"):
    """A named substitution a run made instead of failing, reported rather than logged.

    Not an exception. Degradations travel back inside use-case results as a tuple of these
    models, the CLI summarises them, and ``--strict`` is one check at that boundary which
    turns a non-empty tuple into a non-zero exit. There is no sink port: a
    write-through collector would have put a test double's buffer into the contract.
    """

    kind: DegradationKind
    """Which closed-set substitution was made."""

    subject: str
    """What it happened to -- a document uuid, a page uuid, or a store identity. An
    opaque display string, never parsed or matched."""

    detail: str
    """Why the substitution was necessary, for a human reading the summary."""

    substituted: str | None = None
    """The value used instead, when there was one. ``None`` when the degradation was to
    skip rather than to substitute."""


class RmspecError(Exception):
    """Root of the tree. Never raised directly.

    Exists so the CLI has exactly one class to catch and so :func:`exit_code` has one type
    to switch on. Subclasses build their own message from structured fields in
    ``__init__``, which keeps the wording in one reviewed place and keeps message literals
    out of every ``raise`` site.
    """

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        """The full human-readable explanation, already assembled."""
        self.remediation = remediation
        """The next thing the user could do, when there is a single obvious one."""

    @property
    def code(self) -> str:
        """Return the stable machine-readable identity of this failure.

        Returns
        -------
        str
            The class name, which is what logs, tests and the CLI's non-human output
            modes key on. Derived rather than declared, so it cannot drift from the class
            it names.
        """
        return type(self).__name__


class MissingDependencyError(RmspecError):
    """Raised at container composition when an adapter's third-party package is absent.

    A direct child of the root, never of a slice base: a use case that writes ``except
    ExportError`` must not be able to swallow a wiring bug. Raised once during the
    container's eager resolution pass, after the composition root folds a
    :class:`~rmspec.domain.ports.errors.DependencyProbe` over the modules an adapter
    needs, so a missing extra surfaces before a render or a device round trip is paid for
    rather than as an ``ImportError`` from inside a command body.

    ``package`` is the import name and may differ from the distribution -- ``pymupdf``
    ships ``fitz``, the Vision bindings ship ``Quartz`` -- so the message names both it
    and the extra a user can install.
    """

    def __init__(self, *, package: str, extra: str, feature: str) -> None:
        msg = f"{feature} needs the {package} package, which is not installed"
        super().__init__(msg, remediation=f"uv sync --extra {extra}")
        self.package = package
        self.extra = extra
        self.feature = feature


class ConfigurationError(RmspecError):
    """Base for settings that are wrong before any I/O is attempted.

    Never raised directly. Every child is detectable at composition, which is where they
    are raised, so no command body starts work it cannot finish.
    """


class XochitlDirNotConfigured(ConfigurationError):
    """Raised when no xochitl data directory could be resolved from any source.

    Replaces a settings helper that returned ``None`` into eight command bodies, each of
    which then built a path from it. The error names every source that was consulted, so
    the user learns which one to set rather than that something was missing.
    """

    def __init__(self, *, consulted: tuple[str, ...]) -> None:
        sources = ", ".join(consulted)
        msg = f"no reMarkable data directory found; consulted {sources}"
        super().__init__(msg, remediation="pass --xochitl PATH or set RMSPEC_XOCHITL")
        self.consulted = consulted


class InvalidSettingError(ConfigurationError):
    """Raised when a setting resolved to a value the rest of the run cannot use.

    Covers the shapes a type alone does not exclude: a non-positive DPI or thickness, an
    empty region name, a cache path whose parent cannot be created. Raised at composition
    rather than at first use, so the arithmetic downstream -- the division that made a DPI
    of zero a ``ZeroDivisionError`` two layers away -- can assume a usable value.
    """

    def __init__(self, *, setting: str, value: str, requirement: str) -> None:
        msg = f"setting {setting} is {value!r}, which is not {requirement}"
        super().__init__(msg, remediation=f"set {setting} to {requirement}")
        self.setting = setting
        self.value = value
        self.requirement = requirement


class UsageError(RmspecError):
    """Raised when the invocation contradicts itself and no work should start.

    For contradictions the argument parser cannot see: mutually exclusive flags that are
    only exclusive in combination, an output suffix no exporter claims, an empty search
    query. Distinct from :class:`ConfigurationError` because the fix is in the command
    line the user just typed, not in the environment.
    """

    def __init__(self, *, subject: str, requirement: str) -> None:
        msg = f"{subject} is not usable: expected {requirement}"
        super().__init__(msg, remediation=f"retry with {requirement}")
        self.subject = subject
        self.requirement = requirement


class DocumentSourceError(RmspecError):
    """Base for every failure between a user's request and the bytes of a page.

    Never raised directly. The four children cover the whole distance: the store is
    unreachable, nothing matched, too much matched, or the page is not there. A use case
    that only needs "I could not get the document" catches this one class.
    """


class DocumentStoreUnavailable(DocumentSourceError):
    """Raised when the document store could not be reached or read at all.

    A whole-store failure, never one entry: the xochitl root is missing or unreadable, a
    mirror directory is gone, an SSH transport died mid-read. Wraps the ``OSError`` the
    legacy loader let through untouched, and carries the store's identity as a display
    string rather than a ``Path`` or a transport object.
    """

    def __init__(self, *, store: str, detail: str) -> None:
        msg = f"document store {store} could not be read: {detail}"
        super().__init__(msg)
        self.store = store
        self.detail = detail


class DocumentNotFound(DocumentSourceError):
    """Raised when nothing in the store matches the requested document.

    One class for both spellings of absence, because they are the same fact: an
    explicitly named uuid that the store does not hold, and a user query -- name
    substring or uuid prefix -- that matched no candidate. Replaces a resolver that
    printed in red and returned ``None``, leaving each caller to notice.
    """

    def __init__(self, *, query: str, store: str) -> None:
        msg = f"no document in {store} matches {query!r}"
        super().__init__(msg)
        self.query = query
        self.store = store


class AmbiguousDocument(DocumentSourceError):
    """Raised when a query matched several documents and the caller required exactly one.

    Carries the candidates so the shell prints them and the human decides. In non-strict
    mode the same situation is a ``DegradationKind.AMBIGUOUS_AUTO_RESOLVED`` instead --
    the legacy behaviour of ranking by page count and last-modified date, but stated.
    """

    def __init__(self, *, query: str, candidates: tuple[DocumentCandidate, ...]) -> None:
        msg = f"{len(candidates)} documents match {query!r}; name one exactly"
        super().__init__(msg, remediation="repeat the query with a full document uuid")
        self.query = query
        self.candidates = candidates


class PageNotFound(DocumentSourceError):
    """Raised when the document exists but the requested page cannot be produced.

    Covers a page index outside the document's ordered list, a page uuid the document
    does not claim, and a claimed page whose artifact is absent from the store -- the
    third being the case the legacy loader answered with ``None`` in a list of page paths,
    so the failure surfaced later as an unrelated crash.
    """

    def __init__(self, *, document_uuid: str, page: str, page_count: int | None = None) -> None:
        of_count = "" if page_count is None else f" of {page_count}"
        msg = f"document {document_uuid} has no usable page {page}{of_count}"
        super().__init__(msg)
        self.document_uuid = document_uuid
        self.page = page
        self.page_count = page_count


class FormatError(RmspecError):
    """Base for bytes that were obtained but do not decode into a domain model.

    Never raised directly. The split from :class:`DocumentSourceError` is the line between
    "could not get the bytes" and "got them and they are not what they claim", which is the
    distinction the legacy parser lost when a bare ``except Exception`` turned every decode
    failure into an empty page.
    """


class MalformedDocument(FormatError):
    """Raised when document-level metadata decoded to something no aggregate can be built from.

    A missing required field, a wrong type, a schema version whose page-reference location
    is unknown, or bytes that are not the sidecar they were named as. Per-page problems
    never come here: they arrive as defects on the parsed page, because one bad page must
    not cost the whole document.
    """

    def __init__(self, *, document_uuid: str, artifact: str, detail: str) -> None:
        msg = f"document {document_uuid} has an undecodable {artifact}: {detail}"
        super().__init__(msg)
        self.document_uuid = document_uuid
        self.artifact = artifact
        self.detail = detail


class CorruptPageData(FormatError):
    """Raised when page bytes are not a decodable scene file.

    Truncated, malformed, or structurally invalid -- the scene graph cannot be walked. The
    typed replacement for a leaked ``rmscene.UnexpectedBlockError``, ``struct.error`` or
    ``EOFError``: the original is chained through ``__cause__`` and never re-exported, and
    the byte offset is carried when the decoder supplies one.
    """

    def __init__(self, *, page_uuid: str, detail: str, offset: int | None = None) -> None:
        at = "" if offset is None else f" at byte {offset}"
        msg = f"page {page_uuid} is not a decodable scene file{at}: {detail}"
        super().__init__(msg)
        self.page_uuid = page_uuid
        self.detail = detail
        self.offset = offset


class UnsupportedPageFormat(FormatError):
    """Raised when page bytes are a scene file of a version this codec does not decode.

    Migrated devices still hold v3 and v5 pages, and a v7 will exist. The observed version
    and the supported set are carried on the error, so no caller compares raw version
    numbers and a future format is a named refusal rather than an opaque block error from
    inside the decoder.
    """

    def __init__(
        self,
        *,
        page_uuid: str,
        observed_version: str,
        supported_versions: tuple[str, ...],
    ) -> None:
        supported = ", ".join(supported_versions)
        msg = (
            f"page {page_uuid} declares scene version {observed_version}, "
            f"and this build decodes {supported}"
        )
        super().__init__(msg)
        self.page_uuid = page_uuid
        self.observed_version = observed_version
        self.supported_versions = supported_versions


class RenderError(RmspecError):
    """Base for every failure while turning a page into markup.

    Never raised directly. Lets the shell map the whole render slice to one exit status,
    which is all it does about any of them.
    """


class UnsupportedPenType(RenderError):
    """Raised when a stroke carries a pen the render rules do not implement.

    Loud on purpose. The legacy renderer substituted a fineliner, and a plausible-looking
    page drawn with the wrong physics is indistinguishable from a correct one -- the worst
    kind of silent fallback, because it produces an artifact nobody re-checks. The pen is
    carried as a display string so the error can name a value no enum member covers.
    """

    def __init__(self, *, pen: str, page_ref: str) -> None:
        msg = f"page {page_ref} uses pen {pen}, which has no physics model"
        super().__init__(msg)
        self.pen = pen
        self.page_ref = page_ref


class BackgroundUnreadable(RenderError):
    """Raised when a page's background layer exists but cannot be embedded.

    Template markup that will not parse, a root element that is not ``<svg>``, invalid
    base64, or bytes whose signature contradicts the media they were given as. The legacy
    renderer returned silently from its parse-error handler, so a background simply
    vanished from the output and the page still looked finished.
    """

    def __init__(self, *, page_ref: str, detail: str) -> None:
        msg = f"background of page {page_ref} cannot be embedded: {detail}"
        super().__init__(msg)
        self.page_ref = page_ref
        self.detail = detail


class ExportError(RmspecError):
    """Base for every failure originating in an export adapter.

    Never raised directly. The five children are the whole slice, because the shell takes
    only two actions -- report and stop, or skip this page -- and a sixth class would be a
    branch no caller could act on.
    """


class RasterizationFailed(ExportError):
    """Raised when a backend accepted markup and could not turn it into pixels.

    The markup is malformed for this backend, a background is undecodable, an unsupported
    filter is used, the native library failed mid-render, or the call returned zero bytes.
    Zero-length output is a failure here, not a success with an empty file.
    """

    def __init__(self, *, backend: str, detail: str, page_ref: str | None = None) -> None:
        subject = "markup" if page_ref is None else f"page {page_ref}"
        msg = f"{backend} could not rasterize {subject}: {detail}"
        super().__init__(msg)
        self.backend = backend
        self.detail = detail
        self.page_ref = page_ref


class PdfCompositionFailed(ExportError):
    """Raised when pages could not be merged into one PDF, or the result read back wrong.

    Also the home of the adapter's own page-count postcondition: an exporter that was
    asked for N pages and can only account for a different number wraps that readback
    here. The legacy writer emitted page one for an N-page document and reported success,
    which is exactly the silence this class ends.
    """

    def __init__(
        self,
        *,
        expected_pages: int,
        detail: str,
        actual_pages: int | None = None,
    ) -> None:
        got = "" if actual_pages is None else f" (composed {actual_pages})"
        msg = f"could not compose a {expected_pages}-page PDF{got}: {detail}"
        super().__init__(msg)
        self.expected_pages = expected_pages
        self.detail = detail
        self.actual_pages = actual_pages


class PdfSourceUnreadable(ExportError):
    """Raised when a source PDF is missing, is not a PDF, is corrupt, or needs a password.

    One class for all four, because the caller does the same thing about each of them.
    Encryption is not split out: a password prompt would mirror one library's ``needs_pass``
    flag into the domain. A declared backing document that the store does not hold arrives
    here too, at the moment something tries to read it, rather than as a ``None`` that
    renders as a blank background.
    """

    def __init__(self, *, source: str, detail: str) -> None:
        msg = f"PDF {source} could not be read: {detail}"
        super().__init__(msg)
        self.source = source
        self.detail = detail


class PdfPageOutOfRange(ExportError):
    """Raised when a requested page index is not a page of the referenced PDF.

    Replaces two shapes: an ``IndexError`` from indexing a background list shorter than
    the page list, and the worse one where the lists were merely misaligned and every page
    got a neighbour's background.
    """

    def __init__(self, *, source: str, page_index: int, page_count: int) -> None:
        msg = f"PDF {source} has {page_count} pages, so page index {page_index} does not exist"
        super().__init__(msg)
        self.source = source
        self.page_index = page_index
        self.page_count = page_count


class ArtifactWriteFailed(ExportError):
    """Raised when a sink could not commit an artifact.

    Carries a closed :class:`ArtifactWriteReason` -- already present, not writable, out of
    space, interrupted -- rather than growing one subclass per errno, and guarantees the
    partial write is gone: a sink either commits whole or leaves nothing behind.
    """

    def __init__(self, *, name: str, reason: ArtifactWriteReason, detail: str) -> None:
        msg = f"could not write {name} ({reason.value}): {detail}"
        super().__init__(msg)
        self.name = name
        self.reason = reason
        self.detail = detail


class DeviceError(RmspecError):
    """Base for every failure at the device boundary.

    Never raised directly. The invariant the children enforce is that no ``httpx``,
    ``paramiko``, ``socket`` or ``ssl`` exception ever crosses an adapter boundary. Every
    child carries the :class:`TransportKind` that failed, because the firmware's USB web
    API and its SSH userland fail in different ways and the shell's advice differs.

    Adapters classify by the device's uniform ``{"error": "<msg>"}`` body, not by status
    code: firmware 3.27.3.0 answers an unknown id with HTTP 500 "Unknown file" and ignores
    the request method entirely.
    """

    def __init__(
        self,
        message: str,
        *,
        transport: TransportKind,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message, remediation=remediation)
        self.transport = transport
        """Which way of reaching the tablet produced this failure."""


class DeviceUnreachable(DeviceError):
    """Raised when no session could be opened, or one died before any answer arrived.

    Cable unplugged, connect refused, DNS or route failure, read timeout, SSH banner never
    received. Deliberately not shaped around status codes, so the SSH transport raises the
    same class as the HTTP one.
    """

    def __init__(self, *, transport: TransportKind, endpoint: str, detail: str) -> None:
        msg = f"cannot reach the tablet at {endpoint} over {transport.value}: {detail}"
        super().__init__(msg, transport=transport, remediation="check the cable and the host")
        self.endpoint = endpoint
        self.detail = detail


class DeviceAuthFailed(DeviceError):
    """Raised when the device refused the credentials offered.

    SSH key rejected, passphrase wrong, key file unreadable, host key changed, or a locked
    tablet. Separate from :class:`DeviceUnreachable` because retrying does not help.
    Carries the user and the key's location, never the secret itself.
    """

    def __init__(
        self,
        *,
        transport: TransportKind,
        user: str,
        detail: str,
        key_source: str | None = None,
    ) -> None:
        using = "" if key_source is None else f" using {key_source}"
        msg = f"the tablet refused authentication for {user}{using}: {detail}"
        super().__init__(msg, transport=transport)
        self.user = user
        self.detail = detail
        self.key_source = key_source


class DeviceProtocolError(DeviceError):
    """Raised when the device answered but broke its own contract.

    Non-JSON where JSON was promised, a folder listing that is not a list, a download body
    that does not start with ``%PDF-``, an error status whose body is not the uniform
    ``{"error": ...}`` shape, or a ``Content-Type`` the bytes contradict -- the thumbnail
    route advertises ``image/jpeg`` and returns PNG. Replaces every ``raise_for_status()``.
    """

    def __init__(self, *, transport: TransportKind, route: str, expected: str, got: str) -> None:
        msg = f"{route} over {transport.value} returned {got}, and the contract is {expected}"
        super().__init__(msg, transport=transport)
        self.route = route
        self.expected = expected
        self.got = got


class DeviceDocumentNotFound(DeviceError):
    """Raised when the device holds no document under the requested identifier.

    A sibling of :class:`DeviceProtocolError` and never a subclass: a missing document is
    not a contract violation, even though this firmware reports it as HTTP 500 "Unknown
    file". Also raised when an SSH transport cannot stat the document's metadata.
    """

    def __init__(self, *, transport: TransportKind, document_uuid: str) -> None:
        msg = f"the tablet has no document {document_uuid}"
        super().__init__(msg, transport=transport)
        self.document_uuid = document_uuid


class MalformedDeviceMetadata(DeviceError):
    """Raised when one document's metadata was retrieved and cannot be understood.

    Not JSON, or JSON that describes no document this domain can represent -- a missing
    identifier or name, a non-integer timestamp. The typed replacement for the bare
    ``except Exception: continue`` that shrank listings silently. While enumerating, this
    condition is captured as a skipped entry on the listing instead of raised, so one bad
    document can neither abort the walk nor disappear from it.
    """

    def __init__(
        self,
        *,
        transport: TransportKind,
        detail: str,
        document_uuid: str | None = None,
    ) -> None:
        subject = "an entry" if document_uuid is None else f"document {document_uuid}"
        msg = f"metadata for {subject} is not usable: {detail}"
        super().__init__(msg, transport=transport)
        self.detail = detail
        self.document_uuid = document_uuid


class DeviceTransferInterrupted(DeviceError):
    """Raised when a transfer ended before the advertised number of bytes arrived.

    The connection dropped mid-body, the stream ran short of its declared length, or a
    remote read failed part-way. Carries what was expected and what was moved, so a
    truncated pull can never be recorded as a complete one.
    """

    def __init__(
        self,
        *,
        transport: TransportKind,
        subject: str,
        bytes_transferred: int,
        bytes_expected: int | None = None,
    ) -> None:
        of_total = "" if bytes_expected is None else f" of {bytes_expected}"
        msg = f"transfer of {subject} stopped after {bytes_transferred} bytes{of_total}"
        super().__init__(msg, transport=transport)
        self.subject = subject
        self.bytes_transferred = bytes_transferred
        self.bytes_expected = bytes_expected


class DeviceUploadRejected(DeviceError):
    """Raised when the device accepted the connection and refused the payload.

    Unsupported media, a malformed multipart body, a name conflict, or no free space --
    reported by the firmware as HTTP 400 with its own message, which is carried verbatim
    because it is the only diagnosis available. The local file is untouched and the
    operation had no effect.
    """

    def __init__(self, *, transport: TransportKind, name: str, device_message: str) -> None:
        msg = f"the tablet refused {name}: {device_message}"
        super().__init__(msg, transport=transport)
        self.name = name
        self.device_message = device_message


class DeviceOperationUnsupported(DeviceError):
    """Raised when the bound transport cannot serve the requested operation at all.

    The firmware's USB web API is five routes and cannot produce a raw ``.rmdoc`` bundle --
    ``Accept: application/zip`` is answered with a PDF -- so a use case needing raw
    annotation bytes has no binding under ``--usb`` and this is raised at composition,
    naming the transports that can. Raised eagerly so the broken bundle download cannot
    return silently as a PDF, and so the shell says "retry over SSH" instead of leaking a
    dependency-injection resolution failure.
    """

    def __init__(
        self,
        *,
        transport: TransportKind,
        operation: str,
        supported_by: tuple[TransportKind, ...],
    ) -> None:
        alternatives = ", ".join(kind.value for kind in supported_by) or "no transport"
        msg = f"{operation} is not possible over {transport.value}; it needs {alternatives}"
        super().__init__(msg, transport=transport, remediation=f"retry with {alternatives}")
        self.operation = operation
        self.supported_by = supported_by


class OcrError(RmspecError):
    """Base for every failure in the recognition slice.

    Never raised directly. There is no "engine unavailable" member: a missing optional
    package is :class:`MissingDependencyError`, raised once at composition, and an engine
    that is installed but refuses a specific image is :class:`RecognitionFailed`.
    """


class RecognitionFailed(OcrError):
    """Raised when one recognition engine could not produce a reading for one image.

    The engine was reached and returned an error or unusable output: a refused handler, an
    unsupported document, a payload above the service's ceiling, a throttle. Carries the
    provider slug and whether retrying could help, which is the only distinction a caller
    acts on -- the reason there is no separate transport-error class, since a local
    recognizer could never raise one.
    """

    def __init__(self, *, provider_id: str, detail: str, retryable: bool) -> None:
        msg = f"recognizer {provider_id} failed: {detail}"
        super().__init__(msg)
        self.provider_id = provider_id
        self.detail = detail
        self.retryable = retryable


class AllRecognizersFailed(OcrError):
    """Raised when every recognizer in an ensemble failed, so there is no text to merge.

    Partial failure never comes here: it travels back on the reading as per-recognizer
    failures, because one engine's outage must not discard another's output. Carries each
    provider's reason so the message names all of them instead of only the last.
    """

    def __init__(self, *, failures: Mapping[str, str]) -> None:
        listed = "; ".join(
            f"{provider}: {reason}" for provider, reason in sorted(failures.items())
        )
        msg = f"every recognizer failed ({listed})"
        super().__init__(msg)
        self.failures: Mapping[str, str] = dict(failures)


class NoTextRecognized(OcrError):
    """Raised when every recognizer succeeded and every reading was empty.

    Raised before the language model is called, so a blank page cannot cost a token. The
    legacy pipeline sent the empty strings on and cached whatever came back.
    """

    def __init__(self, *, page_ref: str, providers: tuple[str, ...]) -> None:
        listed = ", ".join(providers)
        msg = f"no recognizer found text on page {page_ref} ({listed})"
        super().__init__(msg)
        self.page_ref = page_ref
        self.providers = providers


class ModelError(OcrError):
    """Base for failures of the vision language model port.

    Never raised directly. The five children describe what the provider did, which is what
    the caller can act on. They exist as one group because the legacy code had one Bedrock
    invocation copied into three places under two names, so the same provider failure
    surfaced three different ways -- most often as an opaque client error.

    No child takes a provider deployment axis as a required argument. A region is ``boto3``'s
    ``region_name`` and nothing else has one, so requiring it obliged every non-AWS adapter to
    fabricate ``"n/a"``; the deployment detail that makes a grant fixable travels as
    ``remediation`` prose the adapter authors instead.
    """


class ModelUnavailable(ModelError):
    """Raised when the binding could not be reached or did not answer.

    Refused connection to a local daemon, DNS failure, request timeout, HTTP 503/529, or
    Bedrock's ``ModelTimeoutException`` / ``ServiceUnavailableException`` /
    ``InternalServerException``. Distinct from a throttle because a permanently unreachable
    endpoint reported as "throttled" sends the caller into a retry loop that cannot succeed,
    and distinct from an entitlement failure because retrying an outage can work.
    """

    def __init__(self, *, endpoint: str, detail: str, retryable: bool) -> None:
        msg = f"cannot reach the model at {endpoint}: {detail}"
        super().__init__(msg, remediation="check the endpoint, the region and the network")
        self.endpoint = endpoint
        self.detail = detail
        self.retryable = retryable


class ModelAccessDenied(ModelError):
    """Raised when the caller is not entitled to the model.

    A missing model grant or an unknown model id: a permanent misconfiguration to report,
    which is why it is not a throttle. Today both surface identically.

    Names the model, and nothing else. It used to require a ``region`` too, which is a
    provider deployment axis only ``boto3`` has: every non-AWS adapter had to pass
    ``region="n/a"`` and emit "not available to this caller in n/a", and an app reading
    ``exc.region`` back had imported AWS by another name. The deployment detail now travels
    inside the ``remediation`` the adapter authors -- the Bedrock one passes
    ``f"enable {model_id} in {region} in the Bedrock console"`` -- so the fix is still named
    where a human reads it, and nothing may read it back as a field.
    """

    def __init__(self, *, model_id: str, remediation: str) -> None:
        msg = f"model {model_id} is not available to this caller"
        super().__init__(msg, remediation=remediation)
        self.model_id = model_id


class ModelThrottled(ModelError):
    """Raised when the provider applied a rate or quota limit after the adapter's retries.

    Retryable, and carries the provider's own retry delay when it supplied one, so a retry
    policy is a decision made from data rather than a guessed sleep.
    """

    def __init__(self, *, model_id: str, retry_after_s: float | None = None) -> None:
        after = "" if retry_after_s is None else f"; retry after {retry_after_s}s"
        msg = f"model {model_id} throttled the request{after}"
        super().__init__(msg)
        self.model_id = model_id
        self.retry_after_s = retry_after_s


class ModelRejectedRequest(ModelError):
    """Raised when the provider rejected the request itself.

    Payload too large, unsupported image media, a token budget above the model's ceiling.
    Not a content refusal: a refusal is a stop reason on a successful completion, which the
    app inspects, because storing a refusal as if it were a transcription is the failure
    mode that matters here.
    """

    def __init__(self, *, model_id: str, detail: str) -> None:
        msg = f"model {model_id} rejected the request: {detail}"
        super().__init__(msg)
        self.model_id = model_id
        self.detail = detail


class ModelResponseMalformed(ModelError):
    """Raised when a well-formed exchange returned a body that is not a completion.

    No content, no text block, or a reasoning block where text was assumed. Covers both
    latent legacy crashes: the reverse scan for a text block falling through to index zero,
    and a function annotated to return text returning ``None`` when the loop found nothing.
    """

    def __init__(self, *, model_id: str, detail: str) -> None:
        msg = f"model {model_id} returned a body that is not a completion: {detail}"
        super().__init__(msg)
        self.model_id = model_id
        self.detail = detail


class PersistenceError(RmspecError):
    """Base for every storage failure, and the only storage failures in the tree.

    Never raised directly. Adapters catch ``sqlite3.Error`` and translate, so no
    application-layer signature has a database exception in its surface and no command body
    can query a store directly. A use case that only needs "the store did not work"
    catches this one class.
    """


class StoreUnavailableError(PersistenceError):
    """Raised when a store cannot be opened, read, or written.

    The file's parent cannot be created, the path is a directory, the filesystem is
    read-only, the disk is full, or a lock outlived the busy timeout. Raised at composition
    by an eager health check as well as at use, so an unwritable database is named before a
    command starts work it cannot record.
    """

    def __init__(self, *, store: str, detail: str) -> None:
        msg = f"store {store} is unavailable: {detail}"
        super().__init__(msg)
        self.store = store
        self.detail = detail


class StoreSchemaMismatchError(StoreUnavailableError):
    """Raised when a store's on-disk schema is not the one this build speaks.

    A subclass, so every port that documents :class:`StoreUnavailableError` keeps its
    contract while the specific fact stays available. This is the defect where SQL written
    as string literals was hand-mirrored into row models with nothing testing that they
    agree: instead of running today's statements against yesterday's tables, or letting
    ``CREATE TABLE IF NOT EXISTS`` leave an old shape in place, the mismatch is loud at
    first use and carries both versions.
    """

    def __init__(self, *, store: str, found: int, expected: int) -> None:
        detail = f"schema version {found} on disk, {expected} expected"
        super().__init__(store=store, detail=detail)
        self.found = found
        self.expected = expected


class StoredRecordUnreadableError(PersistenceError):
    """Raised when a row exists but cannot be reconstructed as the model it represents.

    A required column is null, a timestamp does not parse, a newer build added a field, or
    a cached payload fails validation. Loud rather than swallowed: the caller either
    recomputes or reports, and an adapter never quietly treats it as a miss. Replaces row
    mapping that let a validation error or an index error escape as-is.
    """

    def __init__(self, *, store: str, table: str, key: str, detail: str) -> None:
        msg = f"{store}.{table} row {key} cannot be read back: {detail}"
        super().__init__(msg)
        self.store = store
        self.table = table
        self.key = key
        self.detail = detail


class AuditWriteFailedError(PersistenceError):
    """Raised when an append-only history entry did not land.

    The one failure callers are expected to degrade rather than propagate: the operation it
    describes may well have succeeded, so the caller records
    ``DegradationKind.AUDIT_NOT_RECORDED`` -- "operation succeeded, history not recorded"
    -- and does not retry the operation.
    """

    def __init__(self, *, detail: str) -> None:
        msg = f"history entry not recorded: {detail}"
        super().__init__(msg)
        self.detail = detail


_EXIT_FAILURE: Final = 1
"""Generic failure. Reached only by an :class:`RmspecError` with no more specific row."""

_EXIT_USAGE: Final = 2
"""The invocation must change, and the user is the one who can change it."""

_EXIT_DATA_ERROR: Final = 65
"""``EX_DATAERR``: input was obtained and is not what it claims to be."""

_EXIT_NO_INPUT: Final = 66
"""``EX_NOINPUT``: the named input does not exist."""

_EXIT_UNAVAILABLE: Final = 69
"""``EX_UNAVAILABLE``: a service or device the run depends on did not answer usably."""

_EXIT_INTERNAL: Final = 70
"""``EX_SOFTWARE``: this program could not do its own job on valid input."""

_EXIT_CANT_CREATE: Final = 73
"""``EX_CANTCREAT``: the output could not be produced or written."""

_EXIT_IO_ERROR: Final = 74
"""``EX_IOERR``: a local store failed."""

_EXIT_TEMP_FAIL: Final = 75
"""``EX_TEMPFAIL``: retrying later is expected to work."""

_EXIT_NO_PERM: Final = 77
"""``EX_NOPERM``: credentials were refused or a grant is missing."""

_EXIT_CONFIG: Final = 78
"""``EX_CONFIG``: the environment or wiring is wrong, not the request."""

_EXIT_CODES: Final[Mapping[type[RmspecError], int]] = {
    RmspecError: _EXIT_FAILURE,
    MissingDependencyError: _EXIT_CONFIG,
    ConfigurationError: _EXIT_CONFIG,
    UsageError: _EXIT_USAGE,
    DocumentSourceError: _EXIT_NO_INPUT,
    DocumentStoreUnavailable: _EXIT_UNAVAILABLE,
    AmbiguousDocument: _EXIT_USAGE,
    FormatError: _EXIT_DATA_ERROR,
    RenderError: _EXIT_INTERNAL,
    ExportError: _EXIT_CANT_CREATE,
    PdfSourceUnreadable: _EXIT_DATA_ERROR,
    PdfPageOutOfRange: _EXIT_NO_INPUT,
    DeviceError: _EXIT_UNAVAILABLE,
    DeviceAuthFailed: _EXIT_NO_PERM,
    DeviceDocumentNotFound: _EXIT_NO_INPUT,
    MalformedDeviceMetadata: _EXIT_DATA_ERROR,
    DeviceOperationUnsupported: _EXIT_CONFIG,
    OcrError: _EXIT_UNAVAILABLE,
    ModelAccessDenied: _EXIT_NO_PERM,
    ModelThrottled: _EXIT_TEMP_FAIL,
    ModelRejectedRequest: _EXIT_DATA_ERROR,
    ModelResponseMalformed: _EXIT_DATA_ERROR,
    PersistenceError: _EXIT_IO_ERROR,
    StoreSchemaMismatchError: _EXIT_CONFIG,
    StoredRecordUnreadableError: _EXIT_DATA_ERROR,
}
"""The one table from error class to exit status.

Sparse on purpose: a class with no row of its own inherits its nearest ancestor's status,
so adding a leaf cannot change how an existing one exits, and a slice base is the only row
most slices need.
"""


def exit_code(err: RmspecError) -> int:
    """Map an error to the process exit status the shell should use.

    Pure, total, and the only place the mapping exists. The domain decides which failures
    are equivalent to a caller; the CLI only renders and exits, so a new error class cannot
    silently acquire exit status 1 by being forgotten in a command body.

    Parameters
    ----------
    err
        The failure that ended the run.

    Returns
    -------
    int
        The status for the nearest ancestor of ``type(err)`` that has a row in the table,
        which is :class:`RmspecError` itself in the worst case. Values follow BSD
        ``sysexits`` where one applies.
    """
    for cls in type(err).__mro__:
        status = _EXIT_CODES.get(cls)
        if status is not None:
            return status
    return _EXIT_FAILURE
