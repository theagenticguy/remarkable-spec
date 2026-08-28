"""Ports for the device slice: what an attached tablet holds, and moving bytes to it.

The Protocols here are the only vocabulary the application layer has for an attached
reMarkable. Nothing in this module knows how bytes move: ``httpx`` over the USB web API,
``paramiko`` over SSH, and the already-pulled local mirror are adapter concerns that
live in ``rmspec-device``. The value objects the Protocols exchange are defined here
too, next to the only ports that speak them.

Five decisions are baked into the shapes below.

Capability asymmetry is which ports exist, never data a caller branches on. There is no
``capabilities`` field, no ``supports()`` method and no ``probe()``. A use case that
needs raw ``.rm`` bytes declares :class:`RawBundleSource` and the composition root
either binds one or fails. Firmware 3.27.3.0's USB web API is five routes and cannot
serve a raw bundle at all -- ``Accept: application/zip`` does not yield a ``.rmdoc`` --
so under ``--usb`` that binding is absent and the container raises
``DeviceOperationUnsupported``, which carries the operation name and a closed
``TransportKind`` enum so the shell can say "retry over SSH" instead of leaking a
dependency-injection resolution failure. A missing optional package is the sibling
case: ``MissingAdapterDependency``, raised once at composition, naming both the package
and the extra that provides it.

No port touches the filesystem. Reads return bytes and writes accept bytes, so the CLI
owns every sink, no port declares ``OSError``, and every fake is in-memory.

Per-entry failure is data; whole-transport failure raises. ``DeviceListing.skipped``
has no default, so it cannot be forgotten at construction, and the shell is expected to
exit non-zero when it is non-empty.

Every port is one view over a single ``Scope.REQUEST`` transport resource provided by a
dishka generator, so a command that lists and then pulls performs one handshake and
closes it in the finalizer.

Errors named in the "Raises" sections below live in :mod:`rmspec.domain.errors`:
``DeviceUnreachable``, ``DeviceAuthFailed``, ``DeviceProtocolError``,
``DeviceDocumentNotFound`` (a sibling of ``DeviceProtocolError``, never a subclass -- a
missing document is not a contract violation), ``MalformedDeviceMetadata``,
``DeviceTransferInterrupted``, ``DeviceUploadRejected`` and
``DeviceOperationUnsupported``. Adapters derive them from the device's uniform
``{"error": "<msg>"}`` body rather than its status code, because that firmware answers
an unknown id with 500 "Unknown file" and ignores the HTTP method entirely.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "DeviceCatalog",
    "DeviceDocument",
    "DeviceEntryKind",
    "DeviceFacts",
    "DeviceFactsSource",
    "DeviceListing",
    "DocumentSourceBundle",
    "DocumentUploader",
    "LibraryRefresh",
    "RawBundleSource",
    "SkipReason",
    "SkippedEntry",
    "UploadMedia",
    "UploadReceipt",
    "UploadRequest",
]


class DeviceEntryKind(StrEnum):
    """Whether a listed library entry is a document or a folder."""

    DOCUMENT = "document"
    FOLDER = "folder"


class SkipReason(StrEnum):
    """Why one entry a transport saw could not become a :class:`DeviceDocument`.

    A closed set, so a caller can branch on it and a fake can produce it. It replaces
    the bare ``except Exception: continue`` that dropped unreadable entries silently.
    """

    MALFORMED_METADATA = "malformed_metadata"
    """The entry's metadata was not well-formed and could not be decoded."""

    VALIDATION_FAILED = "validation_failed"
    """The metadata decoded but described no document this domain can represent."""

    UNREADABLE = "unreadable"
    """The transport saw the entry but was refused access to its metadata."""


class UploadMedia(StrEnum):
    """Media a tablet accepts as a new document.

    A domain enum, so no adapter sniffs a file extension and no port signature carries
    an HTTP ``Content-Type``; each adapter maps a member to whatever its wire needs.
    """

    PDF = "pdf"
    EPUB = "epub"


class LibraryRefresh(StrEnum):
    """What an upload had to do to become visible in the tablet UI.

    Reported as a post-condition of :meth:`DocumentUploader.upload` rather than through
    a separate refresh port, so "uploaded but never made visible" is unrepresentable
    instead of being one mis-paired binding away.
    """

    RESTARTED = "restarted"
    """The adapter restarted the tablet's UI process to index the new document."""

    NOT_REQUIRED = "not_required"
    """The document was already visible when the transport acknowledged the write."""


class DeviceDocument(BaseModel, frozen=True, extra="forbid"):
    """One entry in the tablet's library, exactly as the device reports it.

    Pure device metadata: nothing here is derived by opening a document. In particular
    ``page_count`` is whatever the device recorded, which is ``None`` for notebooks and
    for any transport that does not report it. Counting pages means parsing a file, and
    that belongs to the formats and export slices, never to a transport.
    """

    uuid: str = Field(min_length=1)
    """The document's identifier on the device."""

    name: str
    """The name shown in the tablet UI."""

    kind: DeviceEntryKind
    """Whether this entry is a document or a folder."""

    parent_uuid: str | None = None
    """The containing folder's identifier, or ``None`` at the library root."""

    last_modified: datetime.datetime | None = None
    """Last modification instant, timezone-aware and normalized to UTC."""

    page_count: int | None = Field(default=None, ge=0)
    """Pages the device recorded, or ``None`` when it reported none."""

    @field_validator("last_modified")
    @classmethod
    def _normalize_last_modified(
        cls,
        value: datetime.datetime | None,
    ) -> datetime.datetime | None:
        """Reject naive timestamps and pin the comparison rule to whole milliseconds.

        The device stores this field as an epoch-millisecond string, so the adapter
        converts and this validator fixes the canonical form: aware, UTC, truncated to
        millisecond precision. Change detection therefore compares equal values
        regardless of which transport produced them, and a naive datetime -- the shape
        that silently mis-compares against a stored epoch -- cannot be constructed.

        Parameters
        ----------
        value
            The candidate timestamp, or ``None`` when the device reported none.

        Returns
        -------
        datetime.datetime | None
            The same instant in UTC with sub-millisecond precision discarded.

        Raises
        ------
        ValueError
            The timestamp carried no timezone.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "last_modified must be timezone-aware; convert epoch milliseconds to UTC"
            raise ValueError(msg)
        in_utc = value.astimezone(datetime.UTC)
        return in_utc.replace(microsecond=in_utc.microsecond // 1000 * 1000)


class SkippedEntry(BaseModel, frozen=True, extra="forbid"):
    """An entry a transport saw but could not turn into a :class:`DeviceDocument`."""

    uuid: str | None
    """The entry's identifier when the transport recovered one, else ``None``."""

    reason: SkipReason
    """The closed-set reason this entry was not returned as a document."""

    detail: str
    """A human-readable diagnostic. Displayed and logged, never parsed or matched."""


class DeviceListing(BaseModel, frozen=True, extra="forbid"):
    """Everything one enumeration of the tablet's library produced.

    ``skipped`` has no default on purpose: an adapter cannot construct a listing
    without stating what it could not read, and the shell exits non-zero when the field
    is non-empty. A defaulted field would let per-entry failures be forgotten at
    construction, which is the silence this type exists to end.
    """

    documents: tuple[DeviceDocument, ...]
    """Every entry that validated, with the folder tree already flattened."""

    skipped: tuple[SkippedEntry, ...]
    """Every entry that did not validate, reported rather than dropped."""


class DocumentSourceBundle(BaseModel, frozen=True, extra="forbid"):
    """The raw per-document files a v6 parser needs, addressed by role, not by path.

    Members are named by what they are, so no caller learns the on-device directory
    layout, POSIX separator semantics, or which archive a future transport might use.
    ``pages`` is keyed by page identifier; page order is a fact of ``content``, not of
    this mapping's iteration order.

    Fetching is all-or-nothing. A truncated transfer raises
    ``DeviceTransferInterrupted`` rather than producing a bundle with holes, so a
    half-pulled document can never be hashed and recorded as complete.
    """

    doc_uuid: str = Field(min_length=1)
    """The identifier of the document these bytes belong to."""

    metadata: bytes
    """The document's metadata file, undecoded."""

    content: bytes
    """The document's content file, undecoded. It carries the page order."""

    pages: dict[str, bytes]
    """Each page's scene bytes, keyed by page identifier."""

    pagedata: bytes | None = None
    """The per-page template list, or ``None`` when the document has none."""


class UploadRequest(BaseModel, frozen=True, extra="forbid"):
    """One document to place on the tablet, already materialized as bytes.

    Bytes rather than a path: conversion to PDF is an application-layer step that has
    already run by the time this port is reached, and a bytes payload keeps the
    filesystem -- and a "local source missing" error -- out of both the port and its
    fakes.
    """

    name: str = Field(min_length=1)
    """The name the document should show in the tablet UI."""

    media: UploadMedia
    """What kind of document ``data`` holds."""

    data: bytes
    """The complete document payload."""

    parent_uuid: str | None = None
    """Folder to place the document in, or ``None`` for the library root."""


class UploadReceipt(BaseModel, frozen=True, extra="forbid"):
    """What the transport actually knows once an upload has succeeded.

    ``doc_uuid`` is optional because the identifier is a transport fact, not a promise
    this port can keep: an SSH adapter mints the uuid itself and fills it, while
    ``POST /upload`` on firmware 3.27.3.0 reports no id and leaves it ``None``. No
    adapter re-walks the catalog to guess one, because guessing by name races on-device
    indexing and is ambiguous whenever the name already exists. A use case that needs
    the identifier resolves it against :class:`DeviceCatalog` as an explicit step,
    where the ambiguity is visible and testable.
    """

    doc_uuid: str | None
    """The new document's identifier when the transport reported one, else ``None``."""

    name: str
    """The name the document was created under."""

    media: UploadMedia
    """What kind of document was placed."""

    byte_count: int = Field(ge=0)
    """Bytes the adapter observed the device accept."""

    library_refresh: LibraryRefresh
    """What was needed to make the document visible in the tablet UI."""


class DeviceFacts(BaseModel, frozen=True, extra="forbid"):
    """Fixed facts about the attached tablet.

    A closed set of typed fields exists so that no port method takes a shell command
    string: an arbitrary command has an unbounded error set and cannot be faked. Each
    adapter runs whatever it must internally -- the SSH one runs several BusyBox 1.36.1
    commands, short options only, since that userland has no GNU long options.

    Every field is optional because a transport may be unable to answer it, and a
    missing fact is reported as ``None`` rather than as a fabricated value.
    """

    firmware: str | None = None
    """Firmware version string as the device reports it."""

    model: str | None = None
    """Hardware model identifier."""

    serial: str | None = None
    """Device serial number."""

    total_memory_bytes: int | None = Field(default=None, ge=0)
    """Total RAM."""

    available_memory_bytes: int | None = Field(default=None, ge=0)
    """RAM currently available."""

    total_storage_bytes: int | None = Field(default=None, ge=0)
    """Total capacity of the partition holding documents."""

    available_storage_bytes: int | None = Field(default=None, ge=0)
    """Free space on the partition holding documents."""


class DeviceCatalog(Protocol):
    """Read side of the library: which documents an attached tablet holds.

    Three implementations exist today -- the USB web API, SSH, and the already-pulled
    local mirror -- which is what proves the shape is not HTTP-shaped. Both methods are
    total for every one of them, so nothing here is conditionally unimplemented.
    """

    def list_documents(self) -> DeviceListing:
        """Enumerate the whole library, flattening the folder tree.

        Returns
        -------
        DeviceListing
            Every entry that validated, plus every entry that did not.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Look up one document by identifier.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.

        Returns
        -------
        DeviceDocument
            The document's device metadata.

        Raises
        ------
        DeviceDocumentNotFound
            No entry on the device has that identifier. Typed, never an empty result.
        MalformedDeviceMetadata
            The entry exists but its metadata could not be decoded or validated.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...


class RawBundleSource(Protocol):
    """Fetch the source files of one document, undecoded.

    This is the live retrieval path: it is what renders, OCR, and diagram extraction
    consume. Only transports that can serve the real files provide it -- SSH and the
    local mirror -- so the USB web API, whose download route always answers with a
    device-rendered PDF, simply has no binding and the container refuses the wiring.
    """

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Fetch every source file of one document in a single operation.

        One call rather than a path-addressed read per file: batching, ordering, and
        stream lifetime are the adapter's business, and there is no half-consumed
        iterator for a caller to manage.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.

        Returns
        -------
        DocumentSourceBundle
            All of the document's bytes, addressed by role.

        Raises
        ------
        DeviceDocumentNotFound
            No entry on the device has that identifier.
        DeviceTransferInterrupted
            The transfer ended early. Carries bytes written and, when the transport
            announced one, bytes expected. No partial bundle is returned.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...


class DocumentUploader(Protocol):
    """Write side: place one document on the tablet and report what the device knows.

    There is no ``accepts()`` and no format capability set. Which media a transport can
    place is a static wiring fact, so an unbindable combination fails at composition
    with ``DeviceOperationUnsupported`` rather than being probed at runtime by every
    caller.
    """

    def upload(self, request: UploadRequest, /) -> UploadReceipt:
        """Place one document on the device.

        Making the document visible in the tablet UI is part of this operation, not a
        separate call: the receipt states what was needed.

        Parameters
        ----------
        request
            The document to place, with its media and destination folder.

        Returns
        -------
        UploadReceipt
            What the transport observed, including the new identifier when it has one.

        Raises
        ------
        DeviceUploadRejected
            The device refused the document. Carries the device's own message.
        DeviceTransferInterrupted
            The transfer ended early, so no document was created.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...


class DeviceFactsSource(Protocol):
    """Report the tablet's fixed facts, with no shell command crossing the boundary.

    This is what a device information command depends on. Reachability is a use-case
    concern answered here, on demand, not a probe the composition root performs while
    building a container: a provider must stay cheap, and "the adapter package is
    absent" is a different fact from "the tablet is not plugged in".
    """

    def read_facts(self) -> DeviceFacts:
        """Read the device's firmware, model, serial, memory, and storage facts.

        Returns
        -------
        DeviceFacts
            Whichever facts this transport could answer.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...
