"""Ports for the device slice: what an attached tablet holds, and moving bytes to it.

The Protocols here are the only vocabulary the application layer has for an attached
reMarkable. Nothing in this module knows how bytes move: ``httpx`` over the USB web API,
``paramiko`` over SSH, and the already-pulled local mirror are adapter concerns that
live in ``rmspec-device``. The value objects the Protocols exchange are defined here
too, next to the only ports that speak them.

Six decisions are baked into the shapes below.

Capability asymmetry is which ports exist, never data a caller branches on. There is no
``capabilities`` field, no ``supports()`` method and no ``probe()``. A use case that
needs to *write* to the tablet declares :class:`DocumentUploader` and the composition
root either binds one or fails. The already-pulled local mirror is a read-only copy of a
store, so under a mirror transport that binding is absent and the container raises
``DeviceOperationUnsupported``, which carries the operation name and a closed
``TransportKind`` enum so the shell can say "attach the tablet" instead of leaking a
dependency-injection resolution failure. A missing optional package is the sibling
case: ``MissingAdapterDependency``, raised once at composition, naming both the package
and the extra that provides it.

This paragraph previously argued the same point from :class:`RawBundleSource` over the
USB web API, on the claim that "firmware 3.27.3.0's USB web API is five routes and cannot
serve a raw bundle at all -- ``Accept: application/zip`` does not yield a ``.rmdoc``".
Both halves are measured false and the example is replaced rather than repaired. The
route table is six families, and ``GET /download/{id}/rmdoc`` returns
``application/zip`` carrying ``<docUUID>.metadata``, ``<docUUID>.content``, one
``<docUUID>/<pageUUID>.rm`` per page, and -- for a document whose recorded type is not a
notebook -- ``<docUUID>.<fileType>``, the original underlay. The legacy client sent that
``Accept`` header against a *filename*-shaped third path segment, and the segment is a
format selector that accepts only the exact lowercase ``pdf`` and ``rmdoc``: a bare
filename is answered ``400 {"error": "Filetype not supported"}``, so that client received
neither a ``.rmdoc`` nor a pdf and surfaced a bare transport error instead. A USB
transport can therefore serve a
complete :class:`DocumentSourceBundle`, ``base`` included, with no SSH credential. See
``specs/device/3.27.3.0/http.json``, claim ``artifact:.rmdoc archive shape`` and the
refutation above it.

Where an asymmetry is per-request data rather than a binding, the same error is raised
at the call and never degraded silently. ``POST /upload`` on that firmware has no
destination parameter and no media negotiation, so an adapter that cannot honor
:attr:`UploadRequest.parent_uuid` or :attr:`UploadRequest.media` raises
``DeviceOperationUnsupported`` from :meth:`DocumentUploader.upload`. Dropping the
destination and reporting success -- the caller asks for ``/Books`` and gets the root --
is forbidden by this module, because a silent wrong placement is exactly the failure the
"no ``accepts()``" rule exists to prevent.

No wire format crosses this boundary. The device's ``.content`` and ``.pagedata`` files
are decoded by the transport adapter, which reads them anyway to report a page count, so
:class:`DocumentSourceBundle` hands the application layer an ordered
:class:`DevicePageSource` tuple -- page identifier, scene bytes, template name -- rather
than JSON and line-list bytes it would have to parse in a use-case body. The only
undecoded bytes here are the v6 scene payloads a page codec accepts and a PDF or EPUB
underlay, neither of which any adapter can interpret.

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
from typing import Annotated, Protocol, Self

from pydantic import AfterValidator, BaseModel, Field, model_validator

__all__ = [
    "DeviceCatalog",
    "DeviceDocument",
    "DeviceFacts",
    "DeviceFactsSource",
    "DeviceFileType",
    "DeviceFolder",
    "DeviceListing",
    "DevicePageSource",
    "DeviceResources",
    "DocumentSourceBundle",
    "DocumentUploader",
    "LibraryRefresh",
    "RawBundleSource",
    "SearchIndexSource",
    "SkipReason",
    "SkippedEntry",
    "UploadMedia",
    "UploadReceipt",
    "UploadRequest",
]


def _to_utc_milliseconds(value: datetime.datetime) -> datetime.datetime:
    """Reject a naive timestamp and pin the comparison rule to whole milliseconds.

    The device stores modification times as an epoch-millisecond string, so the adapter
    converts and this validator fixes the canonical form: aware, UTC, truncated to
    millisecond precision. Change detection therefore compares equal values regardless
    of which transport produced them, and a naive datetime -- the shape that silently
    mis-compares against a stored epoch -- cannot be constructed.

    Parameters
    ----------
    value
        The candidate timestamp.

    Returns
    -------
    datetime.datetime
        The same instant in UTC with sub-millisecond precision discarded.

    Raises
    ------
    ValueError
        The timestamp carried no timezone.
    """
    if value.tzinfo is None:
        msg = "last_modified must be timezone-aware; convert epoch milliseconds to UTC"
        raise ValueError(msg)
    in_utc = value.astimezone(datetime.UTC)
    return in_utc.replace(microsecond=in_utc.microsecond // 1000 * 1000)


_UtcInstant = Annotated[datetime.datetime, AfterValidator(_to_utc_milliseconds)]
"""A timezone-aware instant, normalized to UTC at millisecond precision."""


def _check_unsupported(unsupported: frozenset[str], answerable: dict[str, object]) -> None:
    """Reject an ``unsupported`` set that names a non-field or an answered field.

    Parameters
    ----------
    unsupported
        Field names the adapter declared it structurally cannot answer.
    answerable
        Every field the model can report, mapped to the value it carries.

    Raises
    ------
    ValueError
        A name is not a field of the model, or names a field that carries a value.
    """
    unknown = sorted(name for name in unsupported if name not in answerable)
    if unknown:
        msg = f"unsupported names fields that do not exist: {', '.join(unknown)}"
        raise ValueError(msg)
    answered = sorted(name for name in unsupported if answerable[name] is not None)
    if answered:
        msg = f"unsupported names fields that carry a value: {', '.join(answered)}"
        raise ValueError(msg)


class SkipReason(StrEnum):
    """Why one entry a transport saw could not become a :class:`DeviceDocument`.

    A closed set, so a caller can branch on it and a fake can produce it. It replaces
    the bare ``except Exception: continue`` that dropped unreadable entries silently.

    The members differ in diagnosis, not in outcome. Whichever one a listing reports for
    an entry, :meth:`DeviceCatalog.get_document` raises ``MalformedDeviceMetadata`` for
    that same identifier, so an adapter cannot report ``UNREADABLE`` from one method and
    a different error class from the other.
    """

    MALFORMED_METADATA = "malformed_metadata"
    """The entry's metadata was not well-formed and could not be decoded."""

    VALIDATION_FAILED = "validation_failed"
    """The metadata decoded but described no document this domain can represent."""

    UNREADABLE = "unreadable"
    """The transport saw the entry but was refused access to its metadata."""


class DeviceFileType(StrEnum):
    """What kind of document an entry is, as the device recorded it.

    Reported by the catalog because every transport already reads the file that states
    it: a caller deciding whether a document has a PDF or EPUB underlay must not have to
    fetch a bundle to find out. An entry whose recorded type is outside this closed set
    is reported in ``DeviceListing.skipped`` with
    :attr:`SkipReason.VALIDATION_FAILED` rather than coerced into one of these members.
    """

    NOTEBOOK = "notebook"
    """Handwriting only: the document has no underlay."""

    PDF = "pdf"
    """Annotations over a PDF the device stores alongside them."""

    EPUB = "epub"
    """Annotations over an EPUB the device stores alongside them."""


class UploadMedia(StrEnum):
    """Media a tablet accepts as a new document, and stores as an underlay.

    A domain enum, so no adapter sniffs a file extension and no port signature carries
    an HTTP ``Content-Type``; each adapter maps a member to whatever its wire needs. The
    same closed set describes the underlay a pulled document carries, because a notebook
    -- the third :class:`DeviceFileType` member -- has no underlay and cannot be
    uploaded as one.
    """

    PDF = "pdf"
    EPUB = "epub"


class LibraryRefresh(StrEnum):
    """What an upload had to do to become visible in the tablet UI.

    Reported as a post-condition of :meth:`DocumentUploader.upload` rather than through
    a separate refresh port, so "uploaded but never made visible" is unrepresentable
    instead of being one mis-paired binding away.

    Both members name an observed outcome, not the mechanism that produced it: the SSH
    adapter forces visibility by restarting the tablet's UI process, a future adapter
    might call a reindex route, and a caller branches on neither. Visibility is
    per-upload, so a batch of N documents over SSH forces it N times; a use case that
    wants one refresh for a batch is asking for a different port than this one.
    """

    VISIBILITY_FORCED = "visibility_forced"
    """The adapter had to act before the tablet UI would show the new document."""

    ALREADY_VISIBLE = "already_visible"
    """The document was already visible when the transport acknowledged the write."""


class DeviceFolder(BaseModel, frozen=True, extra="forbid"):
    """One folder in the tablet's library.

    A separate type from :class:`DeviceDocument` so that a folder identifier cannot
    reach a port that pulls or renders bytes. It carries no ``page_count`` and no
    ``file_type``, because a folder has neither.
    """

    uuid: str = Field(min_length=1)
    """The folder's identifier on the device."""

    name: str
    """The name shown in the tablet UI."""

    parent_uuid: str | None = None
    """The containing folder's identifier, or ``None`` at the library root."""

    last_modified: _UtcInstant | None = None
    """Last modification instant, timezone-aware and normalized to UTC."""

    trashed: bool = False
    """Whether the user deleted this folder on the tablet."""


class DeviceDocument(BaseModel, frozen=True, extra="forbid"):
    """One document in the tablet's library, exactly as the device reports it.

    Pure device metadata: nothing here is derived by parsing a scene. In particular
    ``page_count`` is whatever the device recorded, which is ``None`` for any transport
    that does not report it. Counting pages by decoding them belongs to the formats and
    export slices, never to a transport.

    ``trashed`` mirrors ``DocumentMetadata.trashed`` on the store side, so the same
    decision reads the same way on both sides of a sync. It is a real field rather than a
    sentinel in ``parent_uuid``: the xochitl metadata overloads ``parent`` with the literal
    ``"trash"``, and an adapter that reads that store translates the sentinel into
    ``trashed=True`` with ``parent_uuid`` set to ``None``. ``parent_uuid`` therefore never
    names a phantom folder, and a caller can write ``if not doc.trashed`` instead of
    matching an undocumented wire string each adapter happens to produce.

    ``parent_uuid`` is ``None`` and not the containing folder for a trashed entry because
    firmware 3.27.3.0 does not preserve the original parent: the field's value *is*
    ``"trash"``, so there is nothing to recover. ``DocumentMetadata.parent_uuid``
    documents the same rule, which is what keeps the two sides of a sync comparable.

    An earlier revision of this docstring added "and the USB web API models the trash as a
    parent value", and claimed an adapter reports "the entry's real parent". Both are
    measured false. Over the USB web API the trash is not modelled at all -- a trashed
    document is simply absent from every listing at every depth, and no entry ever carries
    ``Parent == "trash"``. A USB catalog therefore reports ``trashed=False`` on every entry
    it returns and that is *accurate*, because it never returns a trashed one; sentinel
    translation in a USB adapter would be unreachable code. Note also that ``deleted`` is
    not the trash signal on this firmware -- absent on 28 of 42 metadata files, ``false``
    on 14, never ``true`` -- so only the ``parent`` sentinel is ever exercised.
    :meth:`DeviceCatalog.list_documents` already stated the correct version, so the two
    halves of this module disagreed until this was fixed. See
    ``specs/device/3.27.3.0/http.json``, claim ``route:GET /documents/ trash filtering``.
    """

    uuid: str = Field(min_length=1)
    """The document's identifier on the device."""

    name: str
    """The name shown in the tablet UI."""

    file_type: DeviceFileType
    """Whether this document is handwriting only or annotations over an underlay."""

    parent_uuid: str | None = None
    """The containing folder's identifier, or ``None`` at the library root."""

    last_modified: _UtcInstant | None = None
    """Last modification instant, timezone-aware and normalized to UTC."""

    page_count: int | None = Field(default=None, ge=0)
    """Pages the device recorded, or ``None`` when it reported none."""

    trashed: bool = False
    """Whether the user deleted this document on the tablet."""


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

    Documents and folders are separate tuples rather than one sequence tagged by kind,
    so no caller filters before handing an identifier to a port that reads bytes and
    ``get_document`` cannot return something unpullable. The folder tree is
    reconstructable from ``parent_uuid`` on both halves.

    Neither ``folders`` nor ``skipped`` has a default. An adapter cannot construct a
    listing without stating what it could not read -- the shell exits non-zero when
    ``skipped`` is non-empty -- and it cannot quietly omit the folder half, which would
    make a nested library look flat.
    """

    documents: tuple[DeviceDocument, ...]
    """Every document that validated, from anywhere in the folder tree."""

    folders: tuple[DeviceFolder, ...]
    """Every folder that validated, naming the tree the documents hang from."""

    skipped: tuple[SkippedEntry, ...]
    """Every entry that did not validate, reported rather than dropped."""


class DevicePageSource(BaseModel, frozen=True, extra="forbid"):
    """One page of a document as the device holds it.

    Its position in :attr:`DocumentSourceBundle.pages` is the page order the device
    recorded, so a caller has the index it needs to name "page 3" without decoding the
    document's content file, and the template that page uses is here rather than in a
    positional list a caller would have to zip.
    """

    page_id: str = Field(min_length=1)
    """The page's identifier on the device."""

    scene: bytes | None = None
    """The page's v6 scene bytes, or ``None`` when the page carries no ink."""

    template_name: str | None = None
    """The template the device recorded for this page, or ``None`` when it has none."""


class DocumentSourceBundle(BaseModel, frozen=True, extra="forbid"):
    """One document's source, addressed by role and already ordered.

    Members are named by what they are, so no caller learns the on-device directory
    layout, POSIX separator semantics, or which archive a future transport might use.
    The document's metadata and content files do not appear: the adapter decodes them
    into ``document`` and ``pages`` -- it is already reading both to report a page count
    -- which keeps every JSON key and line-list offset on the far side of this boundary
    instead of being re-parsed in a use-case body next to the mirror repository's own
    copy of the same parsing.

    An empty ``pages`` tuple is now unambiguous: a document with no pages at all. A PDF
    nobody has annotated has its full page order here with every ``scene`` set to
    ``None``, and a folder identifier never reaches this port at all.

    Fetching is all-or-nothing. A truncated transfer raises
    ``DeviceTransferInterrupted`` rather than producing a bundle with holes, so a
    half-pulled document can never be hashed and recorded as complete.
    """

    document: DeviceDocument
    """The document these bytes belong to, as the catalog would report it."""

    pages: tuple[DevicePageSource, ...]
    """Every page, in the order the device recorded, with its scene and template."""

    base: bytes | None = None
    """The PDF or EPUB the annotations sit over, or ``None`` for a notebook."""

    @model_validator(mode="after")
    def _check_pages_and_base(self) -> Self:
        """Reject a duplicated page identifier and an underlay that contradicts the type.

        Returns
        -------
        Self
            The validated bundle.

        Raises
        ------
        ValueError
            Two pages share an identifier, a notebook carries an underlay, or a document
            over a PDF or EPUB is missing one.
        """
        seen: set[str] = set()
        for page in self.pages:
            if page.page_id in seen:
                msg = f"pages repeats the page identifier {page.page_id!r}"
                raise ValueError(msg)
            seen.add(page.page_id)
        is_notebook = self.document.file_type is DeviceFileType.NOTEBOOK
        if is_notebook and self.base is not None:
            msg = "a notebook has no underlay, so base must be None"
            raise ValueError(msg)
        if not is_notebook and self.base is None:
            msg = f"a {self.document.file_type} document must carry its underlay in base"
            raise ValueError(msg)
        return self


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
    """Folder to place the document in, or ``None`` for the library root.

    An adapter whose wire has no destination parameter raises
    ``DeviceOperationUnsupported`` when this is not ``None``. It never uploads to the
    root and reports success.
    """


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
    """Bytes the device accepted. Equal to ``len(request.data)`` whenever a receipt
    exists: a short write is ``DeviceTransferInterrupted``, never a receipt reporting
    fewer bytes than were offered."""

    library_refresh: LibraryRefresh
    """What was needed to make the document visible in the tablet UI."""


class DeviceFacts(BaseModel, frozen=True, extra="forbid"):
    """Fixed facts about the attached tablet: what it is, not what it currently has.

    A closed set of typed fields exists so that no port method takes a shell command
    string: an arbitrary command has an unbounded error set and cannot be faked. Each
    adapter runs whatever it must internally, and how it does so is its own business.

    Nothing here changes while the device is attached, so a caller may cache it for the
    life of a session. Volatile readings live in :class:`DeviceResources` precisely so
    that caching this model cannot serve a stale free-space number.

    Every field is optional, and ``None`` has two distinct causes a device-information
    command must be able to tell apart. A field named in ``unsupported`` is one this
    transport structurally cannot ask -- the USB web API has no route that reports a
    serial number -- and is displayed as "not available over this transport". A field
    that is ``None`` and unnamed is one the device was asked for and did not answer, or
    answered unintelligibly; an adapter reports that as ``None`` rather than raising
    ``DeviceProtocolError``, so one unparseable reading never fails the whole command.
    """

    firmware: str | None = None
    """Firmware version string as the device reports it."""

    model: str | None = None
    """Hardware model identifier."""

    serial: str | None = None
    """Device serial number."""

    unsupported: frozenset[str] = frozenset()
    """Names of the fields above this transport structurally cannot answer."""

    @model_validator(mode="after")
    def _check_unsupported_names(self) -> Self:
        """Reject an ``unsupported`` set that names a non-field or an answered field.

        Returns
        -------
        Self
            The validated facts.

        Raises
        ------
        ValueError
            A name is not a fact field, or names a field that carries a value.
        """
        _check_unsupported(
            self.unsupported,
            {"firmware": self.firmware, "model": self.model, "serial": self.serial},
        )
        return self


class DeviceResources(BaseModel, frozen=True, extra="forbid"):
    """One reading of the tablet's memory and storage gauges.

    Separate from :class:`DeviceFacts` because these numbers change while the device is
    attached. A caller that memoizes the fixed facts therefore cannot serve a stale free
    space figure, which is the same staleness bug as a cache keyed on less than what it
    depends on.

    Totals are reported alongside the free values rather than with the fixed facts,
    because a transport reads a pair from one command and this way the pair is internally
    consistent. ``unsupported`` and the two causes of ``None`` mean exactly what they
    mean on :class:`DeviceFacts`.
    """

    total_memory_bytes: int | None = Field(default=None, ge=0)
    """Total RAM."""

    available_memory_bytes: int | None = Field(default=None, ge=0)
    """RAM available at the moment of the reading."""

    total_storage_bytes: int | None = Field(default=None, ge=0)
    """Total capacity of the partition holding documents."""

    available_storage_bytes: int | None = Field(default=None, ge=0)
    """Free space on that partition at the moment of the reading."""

    unsupported: frozenset[str] = frozenset()
    """Names of the fields above this transport structurally cannot answer."""

    @model_validator(mode="after")
    def _check_readings(self) -> Self:
        """Reject an impossible gauge pair and an ill-formed ``unsupported`` set.

        A free value above its total is the signature of a mis-read column, which
        otherwise validates and prints an absurd number instead of being caught.

        Returns
        -------
        Self
            The validated reading.

        Raises
        ------
        ValueError
            A free value exceeds its total, or ``unsupported`` names a non-field or a
            field that carries a value.
        """
        _check_unsupported(
            self.unsupported,
            {
                "total_memory_bytes": self.total_memory_bytes,
                "available_memory_bytes": self.available_memory_bytes,
                "total_storage_bytes": self.total_storage_bytes,
                "available_storage_bytes": self.available_storage_bytes,
            },
        )
        if (
            self.total_memory_bytes is not None
            and self.available_memory_bytes is not None
            and self.available_memory_bytes > self.total_memory_bytes
        ):
            msg = "available_memory_bytes exceeds total_memory_bytes"
            raise ValueError(msg)
        if (
            self.total_storage_bytes is not None
            and self.available_storage_bytes is not None
            and self.available_storage_bytes > self.total_storage_bytes
        ):
            msg = "available_storage_bytes exceeds total_storage_bytes"
            raise ValueError(msg)
        return self


class DeviceCatalog(Protocol):
    """Read side of the library: which documents an attached tablet holds.

    Three implementations exist today -- the USB web API, SSH, and the already-pulled
    local mirror -- which is what proves the shape is not HTTP-shaped. Both methods are
    total for every one of them, so nothing here is conditionally unimplemented.

    The two methods answer coherently, and the rules are stated here rather than left
    for each adapter and each fake to guess:

    * An identifier reported in ``DeviceListing.skipped`` raises
      ``MalformedDeviceMetadata`` from :meth:`get_document`, whichever
      :class:`SkipReason` the listing gave. It is never ``DeviceDocumentNotFound``: the
      entry exists, it just cannot be represented.
    * An identifier in ``DeviceListing.folders`` raises ``DeviceDocumentNotFound``.
      Folders are not documents and :meth:`get_document` has no way to return one.
    * An identifier in ``DeviceListing.documents`` resolves, and the returned document
      equals the listed one.
    """

    def list_documents(self) -> DeviceListing:
        """Enumerate the whole library, documents and folders alike.

        Trashed entries are not filtered here. Whether the transport can see the trash
        at all is a transport fact -- the USB web API does not expose it, SSH and the
        local mirror do -- but every entry that is returned carries an accurate
        ``trashed`` flag, so ``if not doc.trashed`` is a decision the caller can make
        against all three adapters, and none of them reports a deleted document as live.

        Returns
        -------
        DeviceListing
            Every document and folder that validated, plus every entry that did not.

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
            No document on the device has that identifier, or the identifier names a
            folder. Typed, never an empty result.
        MalformedDeviceMetadata
            The entry exists but its metadata could not be decoded or validated. This is
            what every :class:`SkipReason` becomes when the same entry is asked for by
            identifier.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...


class RawBundleSource(Protocol):
    """Fetch one document's source: ordered pages, their scenes, and any underlay.

    This is the live retrieval path: it is what renders, OCR, and diagram extraction
    consume. Only transports that can serve the real files provide it -- SSH and the
    local mirror -- so the USB web API, whose download route always answers with a
    device-rendered PDF, simply has no binding and the container refuses the wiring.

    Decoding the document's metadata, content and template files is the adapter's work,
    not the caller's. The adapter is already parsing them to report a page count, so
    doing it once here is what keeps a second, independently drifting copy of that
    parsing out of the application layer. What crosses the boundary is a page order, a
    template per page, scene bytes a page codec accepts, and an underlay -- everything a
    page needs to be named, rendered and read.
    """

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Fetch every source file of one document in a single operation.

        One call rather than a path-addressed read per file: batching, ordering, and
        stream lifetime are the adapter's business, and there is no half-consumed
        iterator for a caller to manage. The returned bundle's ``document.uuid`` equals
        ``doc_uuid``.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.

        Returns
        -------
        DocumentSourceBundle
            The document's pages in order, each with its scene bytes and template, plus
            the PDF or EPUB underlay when it has one.

        Raises
        ------
        DeviceDocumentNotFound
            No document on the device has that identifier, or the identifier names a
            folder.
        MalformedDeviceMetadata
            The entry exists but its metadata, page order or template list could not be
            decoded, so no ordered bundle can be built.
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

    There is no ``accepts()`` and no format capability set for a caller to consult.
    Whether a transport can write at all is a static wiring fact, so an unbindable
    transport fails at composition. The two facts that are per-request -- the media in
    :attr:`UploadRequest.media` and the destination in
    :attr:`UploadRequest.parent_uuid` -- are answered at the call with
    ``DeviceOperationUnsupported``, which names the operation and the ``TransportKind``
    so the shell can say "retry over SSH".

    Degrading a request is forbidden. An adapter may not drop ``parent_uuid`` and place
    the document at the root, and may not substitute a media it does prefer. Both would
    return a receipt reporting success for something the caller did not ask for.
    """

    def upload(self, request: UploadRequest, /) -> UploadReceipt:
        """Place one document on the device.

        Making the document visible in the tablet UI is part of this operation, not a
        separate call: the receipt states what was needed. Visibility is per upload, so
        a caller placing N documents gets N receipts and, over SSH, N forced refreshes.

        Parameters
        ----------
        request
            The document to place, with its media and destination folder.

        Returns
        -------
        UploadReceipt
            What the transport observed, including the new identifier when it has one.
            ``byte_count`` equals ``len(request.data)``.

        Raises
        ------
        DeviceOperationUnsupported
            This transport cannot honor part of the request: a non-``None``
            ``parent_uuid`` when its wire has no destination parameter, or a
            :class:`UploadMedia` member it cannot place. Raised before anything is
            written, and never replaced by a silent placement at the root.
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
    """Report the tablet's facts and gauges, with no shell command crossing the boundary.

    This is what a device information command depends on. Reachability is a use-case
    concern answered here, on demand, not a probe the composition root performs while
    building a container: a provider must stay cheap, and "the adapter package is
    absent" is a different fact from "the tablet is not plugged in".

    Both methods are total for every transport and take no arguments. A transport that
    cannot ask for a given fact still answers, naming that field in ``unsupported``
    rather than raising, so a caller never has to know which transport it was given.
    """

    def read_facts(self) -> DeviceFacts:
        """Read the device's firmware, model and serial.

        Returns
        -------
        DeviceFacts
            The fixed facts, with whatever this transport cannot ask for named in
            ``unsupported``.

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

    def read_resources(self) -> DeviceResources:
        """Read the device's memory and storage gauges as they are right now.

        Separate from :meth:`read_facts` so that a caller holding cached fixed facts
        still gets a current free-space number.

        Returns
        -------
        DeviceResources
            One consistent reading, with whatever this transport cannot ask for named in
            ``unsupported``.

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


class SearchIndexSource(Protocol):
    """Hand over the device's own search-index database image, as bytes.

    Scope: REQUEST. One image per command, read once and reused for every page.

    Bytes rather than a path, like every other byte-carrying port here, and bytes rather
    than a query interface because **there is no sqlite3 binary on the device and no
    BusyBox applet for one** -- an on-device query is not an available shape on firmware
    3.27.3.0. The image is 503,808 bytes on the measured device, so one read per command is
    cheap; per-page reads would not be.

    There is deliberately no USB binding. The firmware's HTTP route table is closed at six
    families and none of them serves a file from the xochitl tree, so a USB adapter here
    would be a method that cannot be implemented -- the same capability asymmetry
    ``DocumentUploader`` expresses by having exactly one binding. The composition root
    fails to bind and raises ``DeviceOperationUnsupported``.
    """

    def read_index(self) -> bytes | None:
        """Return the search-index image, or ``None`` when the device has none.

        Returns
        -------
        bytes | None
            The whole database image. ``None`` -- never ``b""`` -- when no index file
            exists, which is the honest state of a device that has not built one yet and is
            distinguishable by the caller from an index that exists and holds no row for a
            page.

        Raises
        ------
        DeviceUnreachable
            The transport died. A per-path read failure is *not* this: an absent index is
            ``None``.
        DeviceAuthFailed
            The device refused the credentials offered.
        DeviceProtocolError
            The transport answered in a shape this adapter cannot read.
        """
        ...
