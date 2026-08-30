r"""Place one new document on the tablet, live, while a human is holding it.

This is the north-star write path and the only *supported* one: xochitl performs the
import itself, so the new document is visible in the tablet UI with no restart and no
stopped process. Measured 2026-08-29 on firmware 3.27.3.0 -- ``POST /upload`` carrying
one multipart part named ``file`` answered ``201 {"status": "Upload successful"}`` and
``GET /documents/`` then reported one more root entry. See
``specs/device/3.27.3.0/http.json``, claims[14].

:attr:`~rmspec.domain.ports.device.UploadMedia.RMDOC` is what makes the path interesting
rather than merely useful: an ``.rmdoc`` archive imports as a *notebook*, so an agent can
author a whole document -- sidecars, page order, v6 ink -- and have it appear on a powered-on
tablet. That is why this use case exists before any of the other write shapes.

What this use case refuses to paper over
----------------------------------------
**It cannot target a folder, and it does not pretend otherwise.**
:attr:`CreateDocumentRequest.parent_uuid` is handed to
:attr:`~rmspec.domain.ports.device.UploadRequest.parent_uuid` verbatim. That firmware's
import route has no destination parameter, so its adapter raises
``DeviceOperationUnsupported``, and nothing here catches it: a caller who asked for
``/Books`` and silently received the library root has been given the wrong answer with a
success status. :class:`~rmspec.domain.ports.device.DocumentUploader` forbids the adapter
from degrading the request; this module is the other half of that rule, which is to not
swallow the refusal on the way back out.

**It is create-only and does not preserve identity.** The import re-keys the document uuid
*and* every page uuid, so round-tripping a pulled document produces a copy rather than an
edit. :attr:`~rmspec.domain.ports.device.UploadReceipt.doc_uuid` is therefore ``None`` over
USB -- the 201 body carries no id -- and :attr:`CreateDocumentResult.doc_uuid` reports that
``None`` unchanged. It is never guessed: re-listing the library and taking the new entry is
wrong the moment two uploads overlap or the name already exists, which is the reason the
receipt made the field optional instead of promising an identifier no transport can keep.
The catalog is consulted *before* the write and never after it.

**Visibility is reported, not assumed.**
:class:`~rmspec.domain.ports.device.LibraryRefresh` has exactly two members so that
"uploaded but never made visible" is unrepresentable, and
:attr:`CreateDocumentResult.library_refresh` carries the receipt's answer to the caller
rather than dropping it.

Refusing to deliver nothing
---------------------------
A neighbouring project uploads a valid, empty PDF whenever its own parser silently returns
nothing. Silent successful delivery of nothing, to a device the user trusts, is the worst
outcome on this route, so two checks run before the wire and both are
:class:`~rmspec.domain.errors.UsageError`:

*A content witness for the media.* A PDF must carry ``%PDF-``; an EPUB and an ``.rmdoc``
are zip containers and must carry a local file header, ``PK\x03\x04``. The zip check is the
load-bearing one: an *empty* zip is a valid 22-byte end-of-central-directory record and
nothing else, so ``PK\x05\x06`` at offset zero is exactly the "valid, empty archive" case
and it fails the witness. The witness is matched inside the first
:data:`_WITNESS_WINDOW` bytes rather than at offset zero, because a PDF is permitted junk
ahead of its header and refusing a real document is a cost this check may not impose.

*A declared page count.* Bytes cannot answer "is this document blank" -- a one-blank-page
PDF is structurally perfect -- and the layer that materialized the payload is the only one
that knows. :attr:`CreateDocumentRequest.page_count` is therefore required and a zero is
refused, on the same reasoning :class:`~rmspec.domain.ports.persistence.DiagramCache` gives
for truncation: the fact is knowable at one boundary only, so it is carried across that
boundary instead of being re-derived downstream from evidence that cannot express it.

Why an existing document of the same name is refused by default
---------------------------------------------------------------
Created is irreversible. That firmware's route table is closed at six families and **none
of them deletes**, so an upload cannot be undone over HTTP, and removing the files over SSH
while xochitl is running leaves phantom entries in the library. A duplicate therefore costs
the user a manual delete on the tablet, which means the name and the bytes have to be right
*before* the call rather than fixable after it.

So the library is read once, before the write, and a live root document whose name matches
-- case-insensitively, because two entries differing only in case read as duplicates in the
UI -- makes this a :class:`~rmspec.domain.errors.UsageError` naming the collision.
``UsageError`` and not a device error because nothing about the device is wrong and the fix
is on the command line the user just typed: rename, or pass
:attr:`CreateDocumentRequest.allow_duplicate_name`. The opt-in exists because a second copy
is sometimes exactly what is wanted; the default is the refusal because the recovery is
manual. Trashed entries and entries inside folders do not collide: the trash is not the
library, and this route lands at the root, so a folder's contents are not what the user will
see next to the new document.

The collision check is *skipped* for an ``.rmdoc``, and that is the honest answer rather
than a gap: the two accepted container kinds disagree about the title. With a PDF or EPUB
the multipart filename becomes ``visibleName`` verbatim, extension included; an ``.rmdoc``
carries its own ``.metadata`` and that name wins, so :attr:`CreateDocumentRequest.name` is
not the name the tablet will show and refusing on it would refuse the wrong string.
:attr:`CreateDocumentResult.visible_name` is ``None`` for that media, which says "this layer
cannot know", and it is the field a caller prints instead of echoing the request back as
though it were a fact. No degradation is recorded for it: ``DegradationKind`` is closed and
has no member meaning "the container chose its own name", and adding one is a reviewed
change to the domain rather than something this module may decide -- the same judgement
:mod:`rmspec.app.resolve` records about the trash filter.

An incomplete listing degrades rather than refuses. Every
:class:`~rmspec.domain.ports.device.SkippedEntry` becomes a
``DegradationKind.CATALOG_ENTRY_SKIPPED``, and the upload proceeds, because one unreadable
and unrelated entry must not close the only supported write path. A caller that wants the
strict reading already has it: ``--strict`` is one check over a non-empty
``degradations`` at the CLI boundary, so a strict run refuses exactly the inconclusive case
without this module having two code paths through one policy.

History is appended for a success and not for a failure
-------------------------------------------------------
:class:`~rmspec.domain.models.SyncOperation.PUSH` exists for this operation, and an
irreversible write with no history is the thing the audit log is for. An
:class:`~rmspec.domain.errors.AuditWriteFailedError` is a
``DegradationKind.AUDIT_NOT_RECORDED`` degradation -- the operation succeeded and its
history did not land, which is not a failure of the operation and must not be retried as
one.

Nothing is appended when the upload raises, deliberately. The uploader's contract is that a
rejected or interrupted upload created nothing, so there is no new state on the device for
history to be about; and a degradation can only travel on a result, so an audit failure on
the raise path could only be dropped silently or thrown in place of the device error the
caller actually needs. ``SyncOutcome.FAILED`` stays available to the use cases that have a
result to carry it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import AwareDatetime, BaseModel, Field

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import (
    AuditWriteFailedError,
    Degradation,
    DegradationKind,
    DocumentCandidate,
    UsageError,
)
from rmspec.domain.models import SyncAuditEntry, SyncOperation, SyncOutcome
from rmspec.domain.ports.device import LibraryRefresh, UploadMedia, UploadRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.ports.device import DeviceCatalog, DeviceDocument, DocumentUploader
    from rmspec.domain.ports.persistence import SyncAuditLog

__all__ = ["CreateDocument", "CreateDocumentRequest", "CreateDocumentResult"]

_UNIDENTIFIED: Final = "<entry with no recoverable identifier>"
"""Subject for a skipped entry whose ``uuid`` the transport could not recover."""

_PDF_HEADER: Final = b"%PDF-"
"""The witness that a payload claiming :attr:`UploadMedia.PDF` is one."""

_ZIP_MEMBER_HEADER: Final = b"PK\x03\x04"
"""The witness that a zip container holds at least one member.

An empty zip is a valid end-of-central-directory record (``PK\x05\x06``) with no local file
header anywhere in it, which is precisely the archive that would upload and import as
nothing at all.
"""

_CONTENT_WITNESS: Final = {
    UploadMedia.PDF: _PDF_HEADER,
    UploadMedia.EPUB: _ZIP_MEMBER_HEADER,
    UploadMedia.RMDOC: _ZIP_MEMBER_HEADER,
}
"""One witness per media. Total over :class:`UploadMedia`, so the lookup cannot miss."""

_WITNESS_WINDOW: Final = 1024
"""How far into the payload a witness may appear.

Not offset zero: a PDF is permitted arbitrary bytes ahead of its header, and refusing a
real document is a cost this check may not impose. Far enough in to catch a prefix, short
enough that no witness can be "found" in a megabyte of unrelated payload.
"""


class CreateDocumentRequest(BaseModel, frozen=True, extra="forbid"):
    """One document to create on the tablet, already materialized as bytes.

    Bytes rather than a path, following
    :class:`~rmspec.domain.ports.device.UploadRequest`: conversion has already happened by
    the time this use case is reached, and a bytes payload keeps the filesystem out of both
    the request and every fake.
    """

    name: str
    """The name the caller wants, as the user typed it.

    Unconstrained by pydantic and stripped by :meth:`CreateDocument.create`, whose
    :class:`~rmspec.domain.errors.UsageError` is what a blank name becomes -- the same split
    :attr:`~rmspec.app.resolve.ResolveDocumentRequest.query` makes, and for the same reason:
    where the domain names a condition, this package raises the domain's error for it.

    What the tablet does with it depends on the media, and
    :attr:`CreateDocumentResult.visible_name` is where that answer is reported.
    """

    media: UploadMedia
    """Which of the two underlays or the one container ``data`` holds."""

    data: bytes
    """The complete payload. Checked against the media's content witness before the wire."""

    page_count: int = Field(ge=0)
    """How many pages of content the payload carries, counted by whoever built it.

    Required and never defaulted. Zero is refused, because "my parser returned nothing" is
    knowable here and unknowable from bytes: a blank one-page PDF is structurally perfect.
    """

    occurred_at: AwareDatetime
    """The instant to record in history.

    Supplied by the caller rather than read from a clock, so this package holds no clock and
    no test has to freeze one -- the rule :mod:`rmspec.domain.models` states about itself.
    """

    parent_uuid: str | None = None
    """Folder to create the document in, or ``None`` for the library root.

    Passed to the uploader verbatim. A transport whose wire has no destination parameter
    raises ``DeviceOperationUnsupported``, which this use case does not catch.
    """

    allow_duplicate_name: bool = False
    """Whether to proceed when a live root document already carries this name.

    Defaults to refusing, because an upload cannot be undone over this route and the
    recovery is a manual delete on the tablet. Setting it reports the collision on
    :attr:`CreateDocumentResult.also_named` instead of raising.
    """


class CreateDocumentResult(BaseModel, frozen=True, extra="forbid"):
    """What the device actually knows once a document has been created.

    No field has a default: a caller cannot construct this without stating what the tablet
    will show, what else already carried the name, and what was substituted.
    """

    doc_uuid: str | None
    """The new document's identifier when the transport reported one, else ``None``.

    ``None`` over USB, and never a guess. See this module's docstring.
    """

    requested_name: str
    """The name asked for, stripped. What the tablet does with it depends on the media."""

    visible_name: str | None
    """The name the tablet will show, or ``None`` when this layer cannot know it.

    Equal to :attr:`requested_name` for a PDF or EPUB, whose multipart filename becomes
    ``visibleName`` verbatim, extension included. ``None`` for an ``.rmdoc``, whose own
    ``.metadata`` name wins over the filename.
    """

    media: UploadMedia
    """What kind of document was placed."""

    byte_count: int = Field(ge=0)
    """Bytes the device accepted, from the receipt. A short write is never a receipt."""

    library_refresh: LibraryRefresh
    """What was needed to make the document visible in the tablet UI."""

    also_named: tuple[DocumentCandidate, ...]
    """Live root documents that already carried this name when the check ran.

    Empty when the name was free, when the caller's media made the check meaningless
    (``.rmdoc``), or when the listing could not see the colliding entry -- which is what the
    ``CATALOG_ENTRY_SKIPPED`` degradations are for. Non-empty means the caller opted in to a
    duplicate and now owes the tablet a manual delete if that was a mistake.
    """

    degradations: tuple[Degradation, ...]
    """Every substitution this creation made instead of failing.

    ``CATALOG_ENTRY_SKIPPED`` once per entry the pre-upload listing could not represent, and
    ``AUDIT_NOT_RECORDED`` when the document was created and its history entry was not.
    """


def _duplicates(name: str, documents: Sequence[DeviceDocument]) -> tuple[DocumentCandidate, ...]:
    """Find the live root documents whose visible name already matches.

    Parameters
    ----------
    name
        The stripped name the caller asked for.
    documents
        Every document the listing represented, trash included.

    Returns
    -------
    tuple[DocumentCandidate, ...]
        One candidate per colliding document, in listing order. Trashed entries and entries
        inside a folder are not collisions: the trash is not the library, and this route
        places at the root.
    """
    folded = name.casefold()
    return tuple(
        DocumentCandidate(uuid=document.uuid, name=document.name)
        for document in documents
        if not document.trashed
        and document.parent_uuid is None
        and document.name.casefold() == folded
    )


class CreateDocument:
    """Create one document on the attached tablet, and report what the device knows.

    Three collaborators, all Protocols: the uploader that writes, the catalog that answers
    "does this name already exist" before the irreversible call, and the audit log that
    records that something now exists on a device with no delete route.

    Notes
    -----
    The catalog read happens strictly before the upload and never after it::

        result = creator.create(request)
        if result.doc_uuid is None:
            ...  # over USB the identifier is genuinely unknown; do not go looking
    """

    def __init__(
        self,
        *,
        uploader: DocumentUploader,
        catalog: DeviceCatalog,
        audit: SyncAuditLog,
    ) -> None:
        self._uploader = uploader
        self._catalog = catalog
        self._audit = audit

    def create(self, request: CreateDocumentRequest, /) -> CreateDocumentResult:
        """Create the requested document, refusing before the wire when it would be wrong.

        Parameters
        ----------
        request
            The payload, its media, the name the caller wants, and the destination.

        Returns
        -------
        CreateDocumentResult
            What the transport observed, what the tablet will show, and every substitution
            made.

        Raises
        ------
        UsageError
            The name is blank, the payload carries no content witness for its media, the
            declared page count is zero, or a live root document already carries the name
            and :attr:`CreateDocumentRequest.allow_duplicate_name` is not set. Every one of
            these is raised before anything is written.
        DeviceOperationUnsupported
            The transport cannot honour part of the request -- a ``parent_uuid`` its wire
            has no parameter for, or a media it cannot place. Raised by the uploader and
            deliberately not caught: a silently root-placed document is the failure this
            whole path exists to avoid.
        DeviceUploadRejected
            The device refused the payload. It carries the device's own message.
        DeviceTransferInterrupted
            The transfer ended early, so no document was created.
        DeviceUnreachable
            The tablet did not answer. Never degraded into a successful creation.
        DeviceAuthFailed
            The tablet refused the credentials.
        DeviceProtocolError
            The tablet answered with something the transport cannot interpret.
        """
        name = self._checked_name(request)
        log = DegradationLog()
        visible_name = None if request.media is UploadMedia.RMDOC else name
        also_named = self._checked_availability(name, request, log)
        receipt = self._uploader.upload(
            UploadRequest(
                name=name,
                media=request.media,
                data=request.data,
                parent_uuid=request.parent_uuid,
            )
        )
        self._record(receipt.doc_uuid, receipt.name, request, log)
        return CreateDocumentResult(
            doc_uuid=receipt.doc_uuid,
            requested_name=name,
            visible_name=visible_name,
            media=receipt.media,
            byte_count=receipt.byte_count,
            library_refresh=receipt.library_refresh,
            also_named=also_named,
            degradations=log.frozen(),
        )

    def _checked_name(self, request: CreateDocumentRequest, /) -> str:
        """Return the stripped name, refusing a request that cannot become a document.

        Every check here costs no round trip, so a typo, an empty payload or a parser that
        produced nothing is refused before the tablet is touched at all.

        Parameters
        ----------
        request
            The request as the caller built it.

        Returns
        -------
        str
            The name with surrounding whitespace removed, which is what the wire is given.

        Raises
        ------
        UsageError
            The name is blank, the declared page count is zero, or the payload carries no
            content witness for the media it claims.
        """
        name = request.name.strip()
        if not name:
            raise UsageError(
                subject="an empty document name",
                requirement="a name for the document to appear under",
            )
        if request.page_count == 0:
            raise UsageError(
                subject=f"a {request.media.value} document declaring 0 pages",
                requirement="a document with at least one page of content",
            )
        witness = _CONTENT_WITNESS[request.media]
        if witness not in request.data[:_WITNESS_WINDOW]:
            raise UsageError(
                subject=(
                    f"a {len(request.data)}-byte payload with no "
                    f"{request.media.value} content in its first {_WITNESS_WINDOW} bytes"
                ),
                requirement=f"a {request.media.value} document that carries at least one page",
            )
        return name

    def _checked_availability(
        self,
        name: str,
        request: CreateDocumentRequest,
        log: DegradationLog,
        /,
    ) -> tuple[DocumentCandidate, ...]:
        """Read the library once and decide whether this name may be created.

        Parameters
        ----------
        name
            The stripped name the caller asked for.
        request
            The request, for its media and its duplicate policy.
        log
            The accumulator every skipped entry is reported through.

        Returns
        -------
        tuple[DocumentCandidate, ...]
            Live root documents already carrying the name, which is empty unless the caller
            opted in to a duplicate. Empty without a listing at all for an ``.rmdoc``, whose
            visible name this layer cannot predict.

        Raises
        ------
        UsageError
            The name is taken and :attr:`CreateDocumentRequest.allow_duplicate_name` is not
            set.
        DeviceUnreachable
            Raised by the catalog, and never degraded into "the name looks free".
        DeviceAuthFailed
            Raised by the catalog.
        DeviceProtocolError
            Raised by the catalog.
        """
        if request.media is UploadMedia.RMDOC:
            return ()
        listing = self._catalog.list_documents()
        for entry in listing.skipped:
            log.record(
                Degradation(
                    kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
                    subject=entry.uuid or _UNIDENTIFIED,
                    detail=(
                        f"{entry.reason.value}: {entry.detail}; whether it is named "
                        f"{name!r} is unknown"
                    ),
                )
            )
        taken = _duplicates(name, listing.documents)
        if taken and not request.allow_duplicate_name:
            listed = ", ".join(candidate.uuid for candidate in taken)
            raise UsageError(
                subject=(
                    f"the name {name!r}, which {len(taken)} document(s) already use ({listed})"
                ),
                requirement=(
                    "a name no document at the library root uses, since this route cannot "
                    "delete and a duplicate costs a manual delete on the tablet"
                ),
            )
        return taken

    def _record(
        self,
        doc_uuid: str | None,
        created_as: str,
        request: CreateDocumentRequest,
        log: DegradationLog,
        /,
    ) -> None:
        """Append the history entry for a document that now exists, best effort.

        Parameters
        ----------
        doc_uuid
            The new document's identifier when the transport reported one, else ``None``.
        created_as
            The name the receipt says the document was created under.
        request
            The request, for the declared page count and the caller's instant.
        log
            The accumulator an unrecorded history is reported through.
        """
        try:
            self._audit.append(
                SyncAuditEntry(
                    operation=SyncOperation.PUSH,
                    outcome=SyncOutcome.SUCCEEDED,
                    doc_uuid=doc_uuid,
                    doc_name=created_as,
                    pages_affected=request.page_count,
                    occurred_at=request.occurred_at,
                )
            )
        except AuditWriteFailedError as failure:
            log.record(
                Degradation(
                    kind=DegradationKind.AUDIT_NOT_RECORDED,
                    subject=doc_uuid or created_as,
                    detail=(
                        f"the document was created and its history entry did not land: "
                        f"{failure.detail}"
                    ),
                )
            )
