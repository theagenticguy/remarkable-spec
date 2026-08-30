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
"no ``accepts()``" rule exists to prevent. Both halves of that rule now have a binding,
which is what keeps them from being theoretical: the USB uploader refuses a destination
its route cannot express, and the SSH uploader refuses :attr:`UploadMedia.RMDOC`, because
placing an archive over SSH means unpacking it and writing the sidecars by hand rather
than converting a media.

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

Writing *into* an existing document is a fourth shape, and it is this module's most
dangerous one. :class:`DocumentUploader` creates; :class:`SceneWriter` rewrites one page of
something a human made by hand, over a transport reMarkable's own documentation warns
against using while its reader is running. So the read-modify-write cycle is not left to a
caller to sequence safely: :class:`ScenePrecondition` makes the artifact's read-time
identity a required argument of the write, and the write refuses rather than merges. The
closest neighbouring project performs the same cycle with no precondition at all and
destroys strokes the human added between its read and its write, silently. That is the
failure this port exists to make impossible to write.

Errors named in the "Raises" sections below live in :mod:`rmspec.domain.errors`:
``DeviceUnreachable``, ``DeviceAuthFailed``, ``DeviceProtocolError``,
``DeviceDocumentNotFound`` (a sibling of ``DeviceProtocolError``, never a subclass -- a
missing document is not a contract violation), ``MalformedDeviceMetadata``,
``DeviceTransferInterrupted``, ``DeviceUploadRejected``,
``DeviceOperationUnsupported``, ``DeviceStateMismatchError`` and ``UsageError``. Adapters
derive them from the device's uniform ``{"error": "<msg>"}`` body rather than its status
code, because that firmware answers an unknown id with 500 "Unknown file" and ignores the
HTTP method entirely.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import AfterValidator, BaseModel, Field, model_validator

from rmspec.domain._digest import digest_of
from rmspec.domain.errors import TransportKind

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
    "ScenePrecondition",
    "SceneRead",
    "SceneVisibility",
    "SceneWriteReceipt",
    "SceneWriter",
    "SearchIndexSource",
    "SkipReason",
    "SkippedEntry",
    "UnsupportedField",
    "UploadMedia",
    "UploadReceipt",
    "UploadRequest",
]

_SCENE_FINGERPRINT_TAG = b"rmspec.device.scene.v1"
"""Domain-and-version label folded into every scene fingerprint.

A tag rather than a bare ``sha256`` so that a change to *how* a scene is fingerprinted is a
mechanical mismatch -- every precondition captured under the old scheme refuses instead of
being reinterpreted -- which is the one direction this value may fail in.
"""


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


class UnsupportedField(BaseModel, frozen=True, extra="forbid"):
    """One field a transport cannot answer, and which transports can.

    A set of bare names cannot tell two different absences apart, and the difference is
    load-bearing. "*This* transport cannot answer ``firmware``, SSH can" tells a user to
    change transports. "*No* transport can answer ``serial``, because the value exists only
    inside a file this project may never open" tells them to stop asking. Both used to render
    as the one sentence "not available over this transport", and only the first was true.

    A member of :attr:`DeviceFacts.unsupported` and :attr:`DeviceResources.unsupported`
    alongside plain ``str`` rather than living in a field of its own, and that choice is the
    reason nothing outside this module had to change. Those sets are the only place a
    transport declares an absence, so widening what a member may *be* leaves every reading
    that passes bare names valid, keeps both models' ``model_fields`` exactly as they were --
    which two adapters and three tests derive their own field lists from -- and gives an
    adapter with more to say somewhere to say it.

    Deliberately the same shape :class:`~rmspec.app.capabilities.OperationLimit` already
    uses, down to the empty tuple meaning "nothing serves this", because it is the same
    question asked about a field instead of an operation. It is *not* the ``capabilities``
    or ``supports`` predicate these ports deliberately do not offer: a caller cannot branch
    on this to decide whether to attempt something, because reading a fact is not an
    operation that can be attempted differently. It is a sentence a report prints, in the
    same category as the name it annotates.
    """

    name: str = Field(min_length=1)
    """The port field this is about, such as ``serial``. Never a display label.

    Spelled ``name`` rather than ``field`` to match :class:`~rmspec.app.facts.ReportedFact`
    and :class:`~rmspec.app.facts.ReportedGauge`, so a report can walk answered and
    unavailable entries with one accessor.
    """

    supported_by: tuple[TransportKind, ...] | None = None
    """Transports that can answer :attr:`name`, which may be empty, or ``None``.

    Three states, and they are three different sentences:

    * A non-empty tuple -- *this* transport cannot, and these can. Change transports.
    * ``()`` -- **no** transport can. Empty is a real answer rather than a missing one,
      exactly as it is on ``OperationLimit.supported_by``, and it is what the device serial
      needs: the value is not readable by any means this project permits itself.
    * ``None`` -- nothing is claimed either way.

    ``None`` is rejected inside :attr:`DeviceFacts.unsupported` and
    :attr:`DeviceResources.unsupported`, because a bare ``str`` in that set already says it
    and says it in the shape every adapter has always used. It is what
    :attr:`~rmspec.app.facts.ReportDeviceFactsResult.unsupported` fills in for those bare
    names, so a renderer reads one attribute and gets three sentences instead of narrowing a
    union.
    """


def _unsupported_names(unsupported: frozenset[str | UnsupportedField]) -> list[str]:
    """Reduce a mixed unsupported set to the field names it declares, with duplicates kept.

    Parameters
    ----------
    unsupported
        Bare names and annotated entries, mixed freely.

    Returns
    -------
    list[str]
        One name per member, in arbitrary order -- a ``list`` rather than a set so that the
        caller can still see a field declared twice.
    """
    return [entry if isinstance(entry, str) else entry.name for entry in unsupported]


def _check_claims_are_not_empty(alternatives: tuple[UnsupportedField, ...]) -> None:
    """Reject an annotated entry that claims nothing, which a bare name already says.

    Parameters
    ----------
    alternatives
        The annotated members of an unsupported set.

    Raises
    ------
    ValueError
        An entry's ``supported_by`` is ``None``, which carries no more than the field's name
        does on its own.
    """
    silent = sorted(entry.name for entry in alternatives if entry.supported_by is None)
    if silent:
        msg = (
            f"unsupported annotates these fields with no claim: {', '.join(silent)}; "
            f"name them as plain strings instead, which already means the same thing"
        )
        raise ValueError(msg)


def _check_one_claim_per_field(names: list[str]) -> None:
    """Reject a set that declares one field twice, which is two claims about one absence.

    Parameters
    ----------
    names
        The declared field names, duplicates included.

    Raises
    ------
    ValueError
        A field is declared more than once, which happens when a bare name and an annotated
        entry for it are both in the set.
    """
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        msg = (
            f"unsupported declares these fields more than once: {', '.join(repeated)}; "
            f"a bare name and an UnsupportedField for one field are two answers to one "
            f"question"
        )
        raise ValueError(msg)


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
    """What a tablet accepts as a new document: two underlays, and one whole-document archive.

    A domain enum, so no adapter sniffs a file extension and no port signature carries
    an HTTP ``Content-Type``; each adapter maps a member to whatever its wire needs.

    :attr:`PDF` and :attr:`EPUB` are also exactly the underlay a pulled document can carry,
    and the reason a notebook is absent still holds: a notebook has no underlay, so there is
    nothing to place *as* one. What an earlier revision of this docstring then concluded --
    that a notebook therefore "cannot be uploaded" -- is measured false, and :attr:`RMDOC`
    is that refutation. An archive is not an underlay: it is the tablet's own container for a
    whole document, sidecars and pages included, and it is the third thing the import route
    accepts. So this set is no longer a subset of :class:`DeviceFileType` -- two members name
    an underlay and one names a container, and the two sets merely overlap.

    Measured 2026-08-29 on firmware 3.27.3.0: ``POST /upload`` carrying one multipart part
    named ``file`` whose payload was a notebook ``.rmdoc`` answered ``201 {"status": "Upload
    successful"}``, and ``GET /documents/`` then reported one more root entry -- a notebook --
    with no restart and no stop of xochitl. See ``specs/device/3.27.3.0/http.json``,
    claims[14].

    The two container kinds disagree about where the title comes from, which is why
    :attr:`UploadRequest.name` states what the *caller* wants rather than what the tablet
    will show: with a PDF or EPUB the multipart filename becomes ``visibleName`` verbatim,
    while a ``.rmdoc``'s own ``.metadata`` wins and the filename is ignored. Each adapter
    says which of the two its wire does.
    """

    PDF = "pdf"
    EPUB = "epub"
    RMDOC = "rmdoc"


class LibraryRefresh(StrEnum):
    """What an upload had to do to become visible in the tablet UI.

    Reported as a post-condition of :meth:`DocumentUploader.upload` rather than through
    a separate refresh port, so "uploaded but never made visible" is unrepresentable
    instead of being one mis-paired binding away.

    Both members name an observed outcome, not the mechanism that produced it: the SSH
    adapter forces visibility by restarting the tablet's UI process, the USB adapter needs
    to do nothing at all because that firmware's import route is served by the tablet's own
    UI process and the new entry is listed before the request returns, and a caller
    branches on neither. Visibility is
    per-upload, so a batch of N documents over SSH forces it N times; a use case that
    wants one refresh for a batch is asking for a different port than this one.
    """

    VISIBILITY_FORCED = "visibility_forced"
    """The adapter had to act before the tablet UI would show the new document."""

    ALREADY_VISIBLE = "already_visible"
    """The document was already visible when the transport acknowledged the write."""


class SceneVisibility(StrEnum):
    """What the human holding the tablet can see once one page's scene has been rewritten.

    A sibling of :class:`LibraryRefresh` rather than a third member of it, and that enum's
    own invariant is the reason: it exists so "uploaded but never made visible" is
    unrepresentable, and a member meaning "not visible yet" would hand every
    :class:`DocumentUploader` a way to report precisely that. The create path and the edit
    path have different guarantees, so they get different vocabularies.

    One member, which is a measurement rather than an unfinished set. Firmware 3.27.3.0
    holds an open document's scene in memory, so a page rewritten underneath it is not drawn
    until the document is reopened -- and :class:`SceneWriter` deliberately does not force
    the issue, because stock Paper Pro firmware limits its UI process to four starts per ten
    minutes and maps start-limit failure onto a target whose handler **reboots the tablet**.
    A scene write therefore has exactly one honest thing to say about visibility, and a
    closed member is how it says it: a ``bool`` would let an adapter claim the human can
    already see the reply, which is the single claim this type exists to make
    unconstructible. A writer that one day does force a refresh adds the second member, and
    only then is there something for a caller to branch on.
    """

    REOPEN_REQUIRED = "reopen_required"
    """The bytes are on the device, and the tablet will not draw them until the document is
    reopened. Never a promise that anyone has seen them."""


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
    """The name the caller wants the document to show in the tablet UI.

    What a wire *does* with it differs by media, and each adapter states which: with a PDF
    or EPUB underlay firmware 3.27.3.0's import route takes this verbatim, extension
    included, while an :attr:`UploadMedia.RMDOC` archive carries its own ``visibleName``
    and that one wins. Measured 2026-08-29; see :class:`UploadMedia`.
    """

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


class ScenePrecondition(BaseModel, frozen=True, extra="forbid"):
    """The identity one page's scene had when it was read, to be re-checked before writing.

    This is the whole answer to "the human drew while you were thinking". A read-modify-write
    of a page file races a person holding a stylus, and the closest neighbouring project runs
    that cycle with no precondition of any kind: strokes added between its read and its write
    are destroyed, silently, and its truncating write leaves a half-written page behind if the
    link drops. Making this a *required* argument of :meth:`SceneWriter.write_scene` is what
    turns that race into a typed refusal a caller can act on.

    Built from :attr:`SceneRead.precondition` and not assembled by hand. Assembling one is
    how a write ends up checking a fingerprint of some other page's bytes, and the property
    that makes this port safe is that the identity checked and the bytes amended came from
    one read.

    Two round trips remain between the read and the write, and this type does not pretend
    otherwise. It closes the window at the far end -- the writer re-checks immediately before
    the rename -- rather than eliminating it, which is the honest guarantee an unlocked
    filesystem allows.
    """

    doc_uuid: str = Field(min_length=1)
    """The document whose page this is, as the device identifies it."""

    page_id: str = Field(min_length=1)
    """The page, as the device identifies it."""

    fingerprint: str | None = Field(min_length=1)
    """The scene's identity at read time, or ``None`` when the device stored no scene at all.

    Spelled ``fingerprint`` and typed as an opaque ``str`` rather than as a hash, because
    :meth:`~rmspec.domain.ports.formats.DocumentRepository.page_fingerprint` already settled
    this vocabulary for the read side: "an opaque, non-empty token over the page's stored
    bytes as read", which a caller never parses and never assumes the length of. The domain
    gains nothing from a second word, or from a type asserting an algorithm -- a hash type in
    a port signature is an adapter's implementation promoted to a contract, and the contract
    a caller actually needs is *comparability*, which equality over an opaque token gives.

    Derived by :attr:`SceneRead.precondition` from the bytes themselves, so the value is a
    domain fact rather than an adapter-authored one and two implementations cannot disagree
    about what "unchanged" means. ``None`` is a real assertion and not a missing one: it
    means the write requires the page to *still* have no scene, which is distinguishable from
    the fingerprint of an existing but zero-byte artifact.

    No default. A precondition that defaults to ``None`` is one a caller can forget to
    capture, and while forgetting fails safe -- the write refuses against any page with ink
    -- it fails for a reason nobody can read. Non-empty when present, so ``""`` cannot become
    a third spelling of "no scene" that compares unequal to the real one.
    """


class SceneRead(BaseModel, frozen=True, extra="forbid"):
    """One page's scene as the device holds it, with the identity those bytes carried.

    :attr:`precondition` is a property rather than a field, and that is deliberate: a caller
    cannot hold these bytes without also holding a consistent identity for them, and there is
    no way to construct a read whose precondition names another page or other bytes. As a
    field it would be one more thing an adapter fills in, and the entire safety of the write
    path rests on the two agreeing.

    Bytes rather than a handle, like every other byte-carrying port here, so no fake needs a
    filesystem and nothing above this boundary can hold an open remote file across a request.
    """

    doc_uuid: str = Field(min_length=1)
    """The document these bytes belong to, as the device identifies it."""

    page_id: str = Field(min_length=1)
    """The page, as the device identifies it."""

    location: str = Field(min_length=1)
    """Where the transport found the scene, as an opaque display string.

    A ``str`` and not a path type, and never a value a caller may join, split, or reopen.
    :mod:`rmspec.domain.errors` already states the rule this follows -- "a filesystem
    location is an adapter's identity for a resource, so it is carried as an opaque ``str``
    that is displayed and logged, never reopened" -- and this module's own "no port touches
    the filesystem" is the other half of it. A domain path type would be a second addressing
    scheme for a resource only the adapter that produced it can open, and the first caller to
    build one by concatenation would have invented a traversal.

    It is here because it is what a person reads in a receipt, a log line, or a
    ``DeviceStateMismatchError``, and because the snapshot on
    :attr:`SceneWriteReceipt.snapshot` is the same kind of value and has to be comparable
    with it by eye.
    """

    scene: bytes | None
    """The page's v6 scene bytes, or ``None`` when the device stores no scene for this page.

    ``None`` is the routine state of a blank page of an annotated PDF, and it is *not* the
    same as ``b""``: an artifact that exists and is empty is a page the device wrote and
    something truncated. The two get different fingerprints, so a precondition can tell them
    apart, and a caller that must append ink can refuse both for the right reason.
    """

    @property
    def precondition(self) -> ScenePrecondition:
        """Return the identity these bytes must still have for a write to be safe.

        Returns
        -------
        ScenePrecondition
            The document, the page, and a fingerprint over :attr:`scene` -- ``None`` when
            there was no scene. Computing it here rather than accepting it from an adapter is
            what makes "unchanged" one definition instead of one per transport: a writer
            re-reads and compares two values this property produced.
        """
        return ScenePrecondition(
            doc_uuid=self.doc_uuid,
            page_id=self.page_id,
            fingerprint=(
                None if self.scene is None else digest_of(_SCENE_FINGERPRINT_TAG, self.scene)
            ),
        )


class SceneWriteReceipt(BaseModel, frozen=True, extra="forbid"):
    """What the transport knows once one page's scene has been replaced, and how to undo it.

    Also the undo token. :meth:`SceneWriter.undo` takes this value whole rather than the four
    fields it needs, because a caller that has to reassemble a receipt in order to reverse a
    write is a caller who can transpose two of them and restore the wrong snapshot over the
    wrong page. A result model that carries this therefore carries it entire.

    No field has a default. A receipt is the record of something that has already happened to
    a page of somebody's handwriting, and every one of these is knowable by the transport
    that did it.
    """

    doc_uuid: str = Field(min_length=1)
    """The document whose page was written, as the device identifies it."""

    page_id: str = Field(min_length=1)
    """The page that was written, as the device identifies it."""

    location: str = Field(min_length=1)
    """Where the new scene now lives, as an opaque display string. See
    :attr:`SceneRead.location`."""

    byte_count: int = Field(gt=0)
    """Bytes now in the scene. Positive: a scene write always writes a whole page, and a
    short or empty write is ``DeviceTransferInterrupted``, never a receipt."""

    fingerprint: str = Field(min_length=1)
    """Identity of the bytes now on the device, in the vocabulary
    :attr:`ScenePrecondition.fingerprint` defines.

    Equal to what :attr:`SceneRead.precondition` would report for the same bytes, so a caller
    that reads the page back can tell "still mine" from "the human has drawn since" without
    holding the bytes it wrote.
    """

    replaced: str | None
    """Identity of the scene this write superseded, or ``None`` when there was none.

    The receipt's account of what was on the page a moment ago, and the field that makes
    :attr:`snapshot` checkable: superseding ink without keeping a copy of it is
    unconstructible, rather than merely discouraged.
    """

    snapshot: str | None
    """Where the superseded scene was kept, as an opaque display string.

    One snapshot **per write**, never one per page. A single backup that only ever holds the
    *first* pre-write state is worse than none, because it looks like a safety net while the
    second write to a page has nothing behind it -- which is exactly what the neighbouring
    project ships.

    ``None`` if and only if :attr:`replaced` is ``None``: there was no artifact, so there was
    nothing to copy. Any other combination is refused by this model, so "wrote over a page of
    handwriting and kept no copy" cannot be reported as a success.
    """

    visibility: SceneVisibility
    """What the human can see, from the closed set that has no way to say "already visible"."""

    @model_validator(mode="after")
    def _check_superseded_ink_was_snapshotted(self) -> Self:
        """Reject a receipt whose snapshot and superseded scene disagree about existing.

        Returns
        -------
        Self
            The validated receipt.

        Raises
        ------
        ValueError
            A scene was superseded and no snapshot holds it, or a snapshot is named for a
            write that superseded nothing.
        """
        if self.replaced is not None and self.snapshot is None:
            msg = "a write that superseded a scene must name the snapshot holding it"
            raise ValueError(msg)
        if self.replaced is None and self.snapshot is not None:
            msg = "a write that superseded nothing cannot have snapshotted anything"
            raise ValueError(msg)
        return self


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
    transport structurally cannot ask -- the USB web API has no route that reports the
    firmware version -- and is displayed as "not available over this transport". A field
    that is ``None`` and unnamed is one the device was asked for and did not answer, or
    answered unintelligibly; an adapter reports that as ``None`` rather than raising
    ``DeviceProtocolError``, so one unparseable reading never fails the whole command.

    A name alone cannot say *who could*, and that is a third fact rather than a nicety.
    "This transport cannot, SSH can" and "no transport can, because the value lives only in a
    file this project may never open" are different sentences to print, and the device serial
    is the second kind while most of what the USB web API declines is the first. So a member
    of ``unsupported`` may be a bare name, meaning what it always meant, or an
    :class:`UnsupportedField`, which adds who can. Widening the member type rather than adding
    a field is what lets every adapter that passes a name set keep working unchanged -- and
    keeps its silence read as silence rather than as "nobody can".
    """

    firmware: str | None = None
    """Firmware version string as the device reports it."""

    model: str | None = None
    """Hardware model identifier."""

    serial: str | None = None
    """Device serial number."""

    unsupported: frozenset[str | UnsupportedField] = frozenset()
    """The fields above this transport structurally cannot answer.

    A bare ``str`` names a field and claims nothing about other transports -- what every
    adapter written before :class:`UnsupportedField` existed said, and all it knew. An
    :class:`UnsupportedField` names one and adds which transports can answer it, empty
    meaning none can. One field may appear once, either way: a bare name and an annotated
    entry for the same field are two answers to one question and are rejected.

    Read it through :attr:`unsupported_names` and :attr:`alternatives` rather than by
    narrowing the union at each call site.
    """

    @property
    def unsupported_names(self) -> frozenset[str]:
        """Return every field name this transport declared it cannot answer.

        Returns
        -------
        frozenset[str]
            The names, however each was declared. This is the set the old
            ``frozenset[str]`` field was, so a caller that only wants membership is unchanged.
        """
        return frozenset(_unsupported_names(self.unsupported))

    @property
    def alternatives(self) -> tuple[UnsupportedField, ...]:
        """Return only the declarations that say something about other transports.

        Returns
        -------
        tuple[UnsupportedField, ...]
            The annotated members, ordered by :attr:`UnsupportedField.name`. Sorted because
            the underlying set has no order and a report that listed them differently on two
            runs would look like the device had changed.
        """
        return tuple(
            sorted(
                (entry for entry in self.unsupported if not isinstance(entry, str)),
                key=lambda entry: entry.name,
            )
        )

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
            A name is not a fact field, names a field that carries a value, is declared
            twice, or is annotated with no claim where a bare name would have said the same.
        """
        declared = _unsupported_names(self.unsupported)
        _check_one_claim_per_field(declared)
        _check_claims_are_not_empty(self.alternatives)
        # This dict is the list of fact fields, and it has to move with them: a field added
        # above and forgotten here could never be declared unsupported.
        _check_unsupported(
            frozenset(declared),
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
    consistent. ``unsupported`` and the two causes of ``None`` mean exactly what they mean on
    :class:`DeviceFacts`, :class:`UnsupportedField` included.

    That last part is a decision rather than symmetry for its own sake. The gauges are exactly
    as asymmetric as the facts -- reading free memory and free disk needs a shell, which one
    transport has and the other does not -- and
    :attr:`~rmspec.app.facts.ReportDeviceFactsResult.unsupported` mixes both models' names
    into one tuple, so a shape only one of them could carry would make that tuple mean two
    different things depending on which model a name came from. The sentence above already
    promises the two sets mean the same thing; letting them diverge here would make it false.
    """

    total_memory_bytes: int | None = Field(default=None, ge=0)
    """Total RAM."""

    available_memory_bytes: int | None = Field(default=None, ge=0)
    """RAM available at the moment of the reading."""

    total_storage_bytes: int | None = Field(default=None, ge=0)
    """Total capacity of the partition holding documents."""

    available_storage_bytes: int | None = Field(default=None, ge=0)
    """Free space on that partition at the moment of the reading."""

    unsupported: frozenset[str | UnsupportedField] = frozenset()
    """The gauges above this transport structurally cannot read.

    A bare name or an :class:`UnsupportedField`, under exactly the rules
    :attr:`DeviceFacts.unsupported` states; read it through :attr:`unsupported_names` and
    :attr:`alternatives`.
    """

    @property
    def unsupported_names(self) -> frozenset[str]:
        """Return every gauge name this transport declared it cannot read.

        Returns
        -------
        frozenset[str]
            The names, however each was declared.
        """
        return frozenset(_unsupported_names(self.unsupported))

    @property
    def alternatives(self) -> tuple[UnsupportedField, ...]:
        """Return only the declarations that say something about other transports.

        Returns
        -------
        tuple[UnsupportedField, ...]
            The annotated members, ordered by :attr:`UnsupportedField.name`.
        """
        return tuple(
            sorted(
                (entry for entry in self.unsupported if not isinstance(entry, str)),
                key=lambda entry: entry.name,
            )
        )

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
            A free value exceeds its total, or ``unsupported`` names a non-field, a field
            that carries a value, one field twice, or one annotated with no claim.
        """
        declared = _unsupported_names(self.unsupported)
        _check_one_claim_per_field(declared)
        _check_claims_are_not_empty(self.alternatives)
        # This dict is the list of gauge fields, and it has to move with them: a gauge added
        # above and forgotten here could never be declared unsupported.
        _check_unsupported(
            frozenset(declared),
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


class SceneWriter(Protocol):
    """Replace one page's scene on a powered-on tablet, safely, and be able to take it back.

    The edit half of the write story, and the one reMarkable warns against: its own guidance
    is that its reader "must not run when manually accessing document files", and this port
    runs while it does. Every guarantee below exists because that warning is being
    deliberately overridden, and the only honest way to override it is to make each hazard a
    checked precondition rather than an assumption.

    Scope: ``REQUEST``. One transport, one handshake, closed by one finalizer, so a command
    that reads a page and then writes it does not open two sessions.

    Notes
    -----
    **Three methods, and the two that are missing are the interesting ones.** A shipped
    adapter also offers a standalone verify and a snapshot listing, and neither is declared
    here.

    A standalone verify is absent because a caller that checks and *then* writes has opened a
    second window after the one it just closed -- the check-then-act shape this port exists to
    delete. :meth:`write_scene` performs the only re-check that can be sound, immediately
    before the replacement lands, inside the operation it guards. Publishing an earlier one
    would make the unsafe sequence the obvious one.

    A snapshot listing is absent because no policy decides anything from a list of backups. A
    caller undoing a write already holds the receipt naming the one snapshot that matters, and
    a method whose only consumer is a human reading output belongs to whichever command prints
    it rather than to the seam a use case is written against.

    **Nothing may key on a scene id, an author id or a layer index across a call.** The tablet
    renumbers them: a measured page's layer moved from author 0 / sequence 11 to author 1 /
    sequence 334 across xochitl's own re-save. Everything a write needs is read out of the
    bytes handed to it, on the call that uses them.

    **Restarting the tablet's UI process is no part of any method here.** Visibility is
    reported and never forced: stock firmware limits that process to four starts per ten
    minutes and maps start-limit failure onto a target whose handler reboots the tablet.
    """

    def read_scene(self, doc_uuid: str, page_id: str, /) -> SceneRead:
        """Read one page's scene, together with the identity a later write must re-check.

        The only way to obtain a :class:`ScenePrecondition`, and therefore the only way to
        begin a safe edit. A caller amends the bytes this returns and hands back the
        precondition they came with; amending a copy read earlier is meaningless, because the
        precondition would then describe bytes that are not the ones being changed.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.
        page_id
            The page's identifier on the device, as the document's own page order lists it.

        Returns
        -------
        SceneRead
            The scene bytes -- ``None`` when the device stores none for this page -- the
            opaque location they were found at, and, through
            :attr:`SceneRead.precondition`, their identity.

        Raises
        ------
        DeviceDocumentNotFound
            No document on the device has that identifier, or the identifier names a folder.
        MalformedDeviceMetadata
            The document exists and its page order could not be decoded, so the page cannot be
            resolved to an artifact and "claims no such page" cannot be decided.
        DeviceTransferInterrupted
            The read ended early. No partial scene is returned, because a truncated page would
            be appended to and written back as though it had been whole.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...

    def write_scene(self, precondition: ScenePrecondition, scene: bytes, /) -> SceneWriteReceipt:
        """Replace the page's whole scene, refusing if the page moved since it was read.

        Four obligations, every one of them a measured requirement rather than good practice:

        1. **Re-check, then refuse.** The precondition is verified immediately before the
           replacement lands. If the page moved, this raises and changes nothing: it never
           merges the two versions and never wins by writing last. Merging CRDT scenes from
           two authors is not something this project has measured, and guessing at it would
           risk the only copy of something a human made by hand.
        2. **Replace atomically.** The new bytes arrive by a rename within the same directory,
           so a dropped link leaves either the old page or the new one and never a truncated
           file. A byte-count check on the transfer is necessary and not sufficient.
        3. **Snapshot this write.** The superseded scene is copied somewhere the receipt names,
           once per write and never once per page.
        4. **Do not force visibility.** The receipt reports
           :attr:`SceneVisibility.REOPEN_REQUIRED`, and nothing here restarts anything.

        Parameters
        ----------
        precondition
            The identity captured by :meth:`read_scene`, unmodified.
        scene
            The page's entire new contents, as
            :attr:`~rmspec.domain.ports.formats.SceneEdit.scene` supplies them. Never a patch,
            a diff or a tail, and never empty: a zero-byte page is a page whose ink has been
            deleted, which nothing may ask this port for.

        Returns
        -------
        SceneWriteReceipt
            What landed, what it superseded, where the copy of that is, and what the human can
            see -- which is not yet the new ink.

        Raises
        ------
        UsageError
            ``scene`` is empty. Nothing is written.
        DeviceStateMismatchError
            The page is not the page the precondition describes: the human drew, or another
            writer landed first. It carries both identities and ``retryable=True``, because
            the refusal happens before the replacement and provably changed nothing -- re-read,
            re-compose against the new bytes, and decide again.
        DeviceTransferInterrupted
            The transfer ended early. The page is as it was, because an incomplete copy was
            never renamed into place.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceAuthFailed
            The tablet refused the supplied credentials.
        DeviceProtocolError
            The tablet answered with something this transport cannot interpret.
        """
        ...

    def undo(self, receipt: SceneWriteReceipt, /) -> SceneWriteReceipt:
        """Restore the scene a write superseded, under the same guarantees as the write.

        This is why a scene write is *reversible* rather than merely regrettable, and why a
        caller weighing "should I write this" is not weighing an upload -- that firmware's
        route table is closed at six families and none of them deletes, so a created document
        cannot be taken back at all, while a page edit can.

        Not a weaker operation than :meth:`write_scene`. It is atomic, it snapshots what *it*
        replaces, and it re-checks that the page still holds the bytes the receipt reported
        landing, so an undo cannot silently discard whatever the human drew after the write it
        reverses.

        Parameters
        ----------
        receipt
            The receipt :meth:`write_scene` returned, whole. Its ``fingerprint`` is the
            precondition and its ``snapshot`` is the source.

        Returns
        -------
        SceneWriteReceipt
            A receipt for the restoring write, whose ``replaced`` is the reversed write's
            ``fingerprint``. Reversing an undo therefore needs no second method.

        Raises
        ------
        UsageError
            The receipt names no snapshot, which is only true of a write that superseded
            nothing. There is no prior state to restore, and removing an artifact is a
            different operation with different failure modes -- the same distinction the SSH
            uploader draws when it refuses to unpack an archive as a media conversion.
        DeviceStateMismatchError
            The page no longer holds the bytes the receipt reported, so the human has drawn
            since. ``retryable=False``: what a repeat would discard is that drawing, and
            reading the page again is the only correct next step.
        DeviceDocumentNotFound
            The document, or the snapshot the receipt names, is no longer on the device.
        DeviceTransferInterrupted
            The transfer ended early, so the page is as the write left it.
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
