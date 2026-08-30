"""The five device ports bound to SSH: catalog, bundles, uploads, facts and the search index.

Every adapter here takes a :class:`~rmspec.device._shell.RemoteShell` and nothing else that
touches a wire, so all five are exercised against an in-memory double. What they add on top
of the shell is knowledge of the xochitl store's layout, of the ``.metadata``/``.content``
sidecar pair, and of the seven BusyBox commands this package is allowed to send.

SSH is the documented-hazard fallback, not the default
------------------------------------------------------
reMarkable's own documentation states that *"Xochitl must not run when manually accessing
document files"*, and nothing in this module stops it. So every read here is a read of a
store its owner is still writing. The default read path in this project is therefore the USB
web API: ``GET /download/{id}/rmdoc`` is served by xochitl itself, which makes its answer a
consistent snapshot by construction rather than by timing. Three of the five adapters here --
:class:`SshCatalog`, :class:`SshBundleSource` and, since ``POST /upload`` was measured on
2026-08-29, :class:`SshUploader` -- therefore duplicate a capability the USB transport also
has and are the fallback for it, while the other two exist because that firmware's six route
families do not serve their capability at all: reporting the device's own facts and gauges,
and handing over the search-index image. :class:`SshSearchIndexSource` restates the hazard at
its own docstring rather than leaving a reader of one class to find it here.

The two uploaders are not interchangeable and neither is redundant. This one honours a
destination folder, so it can place a document anywhere in the tree; the USB one cannot,
because no folder parameter exists in its route. This one cannot place an ``.rmdoc`` archive;
the USB one can, because xochitl unpacks it. So "the default read path is USB" does not extend
to "the default write path is USB": the request decides, and each adapter raises rather than
degrading when handed the half it cannot serve.

Relocated from ``src/remarkable_spec/device/sync.py`` and ``device/push.py``
---------------------------------------------------------------------------
``SyncManager.pull_all``/``pull_document``/``sync_pull`` become :class:`SshCatalog` and
:class:`SshBundleSource`; ``push_pdf``/``sync_push_file`` become :class:`SshUploader`; the
``rmspec device info`` body in ``cli/device_cmd.py`` becomes :class:`SshFacts`. Nine
divergences, each forced by something measured or by ``ports/device.py``.

1. **No unquoted interpolation, anywhere.** The legacy pusher built three commands with
   f-strings -- ``f"mkdir -p {remote_base}"`` at ``sync.py`` line 226 and 530, and
   ``f"touch {remote_base}/{page_uuid_str}.rm"`` at line 532 -- where ``remote_base`` was
   ``f"{self.XOCHITL_DIR}/{doc_uuid}"``. A uuid carrying a space, a quote or a ``;`` was a
   command the user did not write, running as root on their tablet. Here the only way to
   build a command is :meth:`~rmspec.device.addresses.RemoteCommand.of`, which quotes every
   argument, and the only way to build a path is
   :meth:`~rmspec.device.addresses.RemotePath.child`, which refuses a name that is not one
   component. The unquoted form is not reachable from this module.

2. **Bytes, not temporary files.** Both legacy pushers wrote each sidecar to a
   ``tempfile.NamedTemporaryFile`` and SFTP-``put`` it, then unlinked it in a ``finally``.
   ``ports/device.py`` says no port touches the filesystem, so the sidecars are composed as
   ``bytes`` here and handed to :meth:`~rmspec.device._shell.RemoteShell.write_file`.

3. **The gauges are parsed from machine-readable output, not from GNU human-readable
   output.** ``cli/device_cmd.py`` ran ``free -h | head -2`` and ``df -h /home | tail -1``
   and printed the resulting strings. Neither survives contact with the device: its
   userland is BusyBox 1.36.1, whose ``free`` has no ``-h``, and ``-h`` output is rounded
   for humans in the first place. :class:`SshFacts` reads ``/proc/meminfo`` and ``df -Pk``
   and reports integers, and an unparseable reading becomes ``None`` rather than a string
   the CLI would have to re-parse.

4. **``df -Pk``, never ``df -k``.** Measured 2026-08-29: the documents partition's device
   name is ``/dev/mapper/home-encrypted-disk``, 31 characters against BusyBox's 20-column
   ``Filesystem`` field, so plain ``df -k`` wraps -- the device name occupies line 2 alone
   and the numbers land on line 3. The legacy shape ``awk 'NR==2 {print $4}'`` reads nothing
   from that. ``-P`` is POSIX output format and guarantees one line per filesystem.
   :func:`_storage_bytes` therefore reads :data:`DF_DATA_LINE` and no other, which is what
   makes the wrapped shape report "did not answer" instead of a fabricated number; the
   test suite asserts that on the wrapping output, so the reason for ``-P`` is pinned rather
   than remembered.

5. **The firmware version comes from ``IMG_VERSION``.** ``cat /etc/version`` returns the
   build stamp ``20260612085811``, which is not what a user recognises, and legacy
   ``DevicePaths.UPDATE_CONF`` named ``/usr/share/remarkable/update.conf``, measured on
   2026-08-29 to not exist on this firmware. :data:`~rmspec.device.addresses.OS_RELEASE`'s
   ``IMG_VERSION`` line gives ``3.27.3.0``.

6. **``serial`` is declared unsupported rather than answered with the SoC id.** Legacy read
   ``/sys/devices/soc0/serial_number``. That file exists and holds 16 characters, but it is
   the i.MX8MM SoC unique id -- a *different fact* from the ``RM02A...`` serial the tablet
   UI shows, which is recorded only in the tablet's own credential-bearing config file and
   in redacted journal lines. Reporting the SoC id under the field named ``serial`` would be
   one fact wearing another's name, so the field is named in
   :attr:`~rmspec.domain.ports.device.DeviceFacts.unsupported`. That is the port's
   "structurally cannot ask", and it is the correct encoding because it stays true on the
   next run.

7. **The uploaded ``.metadata`` carries ``createdTime`` and a real ``lastModified``.**
   ``push_pdf`` wrote ``"lastModified": ""`` and no ``createdTime`` at all;
   ``sync_push_file`` wrote a real ``lastModified`` and still no ``createdTime``. See
   "The upload payload, and the hypothesis under it" below.

8. **The uploaded ``.content`` uses ``cPages.pages[]``.** Both legacy pushers wrote the
   pre-v2 flat ``"pages"`` array. On the device that key exists on exactly 1 of 40
   ``.content`` files; ``cPages`` is what firmware 3.x writes and what
   :func:`~rmspec.device._pages.decode_page_order` prefers when a sidecar somehow carries
   both.

9. **No zero-byte ``.rm`` stubs, and no ``.local`` sidecar.** ``sync_push_file`` created one
   empty ``.rm`` per synthesised page uuid so "xochitl doesn't complain". It complains
   anyway and does not care: the journal holds 859 "no file found" lines against 18
   successful page loads, so a missing artifact is the routine state of an unannotated page.
   Zero-byte stubs, meanwhile, correlate exactly with the ``.failure`` archetype. The
   ``<uuid>/`` directory is still created. ``.local`` is written by the firmware at
   *notebook* creation and is absent on all 13 real PDFs, so writing one would fabricate a
   shape this firmware does not produce.

The upload payload, and the hypothesis under it
-----------------------------------------------
12 of the 13 PDFs on the reference device carry a ``.failure`` sidecar, every one of them
with ``createdTime`` either ``0`` or absent, and the single ``.content`` file in the store
using the flat ``pages`` array is one of them. Both legacy pushers wrote exactly that shape.
**This module takes the position that the legacy pusher produced that archetype**, and
writes the modern shape instead.

That is a falsifiable hypothesis, not a measurement: the sidecar is written asynchronously
by the device's synchronizer after validation, so nothing observable at upload time
confirms or refutes it. It is falsified by a hardware test that pushes one document and
checks whether a ``.failure`` sidecar appears. Until such a test runs, the honest statement
is that :class:`SshUploader` writes what the firmware itself writes and does not claim to
have fixed anything.

Because of the same asynchrony, :class:`~rmspec.domain.ports.device.UploadReceipt` promises
only what the transport observed -- the bytes written, the uuid this adapter minted, and
that visibility was forced. It does **not** promise the document will sync.

``SkipReason`` assignment
-------------------------
The same split the USB sibling uses, so an entry that is unreadable over one transport gets
the same diagnosis over the other.

``MALFORMED_METADATA``
    The ``.metadata`` sidecar will not decode on its own -- not json, json of another type, a
    timestamp or flag of a type the store never writes.
``VALIDATION_FAILED``
    The sidecar decoded and describes nothing this domain can represent: a pydantic
    constraint refused a field, the ``.content`` sibling is absent or names no ``fileType``,
    its ``fileType`` is outside :class:`~rmspec.domain.ports.device.DeviceFileType`, or the
    store entry's own name is not usable as one path component.
``UNREADABLE``
    The listing named a sidecar and *that one path* could not be read -- which the shell
    reports as ``PathUnreadableError`` and nothing else does. Every other ``DeviceError``
    from a per-entry read **propagates**, which is what keeps the port's rule true: a cable
    pulled part-way through the walk raises instead of returning a shrunken library with
    thirty ``UNREADABLE`` entries and a success exit status. On the reference store the walk
    is 42 sequential reads, so that window is the common case and not the edge.

What is not implemented, and where it went
------------------------------------------
Nothing here walks folders: the store is one flat directory and the tree is reconstructed
from each entry's ``parent``, so the breadth-first walk the USB catalog needs has no
counterpart. Nothing here decodes a scene, counts PDF pages, or converts a document -- the
first belongs to ``rmspec-formats``, and the last two to ``rmspec-export``, which owns the
only PDF library in the workspace. That constraint is why an uploaded document's
``pageCount`` is ``0``; see :meth:`SshUploader.upload`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from rmspec.device._pages import decode_page_order
from rmspec.device._shell import PathUnreadableError
from rmspec.device.addresses import (
    CONTENT_SUFFIX,
    METADATA_SUFFIX,
    OS_RELEASE,
    PROC_MEMINFO,
    SCENE_SUFFIX,
    SEARCH_INDEX_NAME,
    SOC_MACHINE,
    RemoteCommand,
    RemotePath,
    document_paths,
)
from rmspec.domain.errors import (
    DeviceAuthFailed,
    DeviceDocumentNotFound,
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.models import DocumentKind, DocumentMetadata, SourceKind
from rmspec.domain.ports.device import (
    DeviceDocument,
    DeviceFacts,
    DeviceFileType,
    DeviceFolder,
    DeviceListing,
    DevicePageSource,
    DeviceResources,
    DocumentSourceBundle,
    LibraryRefresh,
    SkippedEntry,
    SkipReason,
    UploadMedia,
    UploadReceipt,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rmspec.device._pages import PageOrderEntry
    from rmspec.device._shell import RemoteShell
    from rmspec.device.addresses import DocumentPaths
    from rmspec.domain.ports.device import UploadRequest

__all__ = [
    "AFTER_COMMIT_NOTE",
    "AT_COMMIT_NOTE",
    "BEFORE_COMMIT_NOTE",
    "CONTENT_FORMAT_VERSION",
    "DF_AVAILABLE_FIELD",
    "DF_DATA_LINE",
    "DF_TOTAL_FIELD",
    "DOCUMENT_TYPE",
    "FIRMWARE_TEMPLATE",
    "JSON_INDENT",
    "MAKE_DIR_TEMPLATE",
    "MEMINFO_TEMPLATE",
    "MODEL_TEMPLATE",
    "REFRESH_TEMPLATE",
    "SERIAL_FIELD",
    "STORAGE_TEMPLATE",
    "UNPLACEABLE_MEDIA",
    "UNPLACEABLE_OPERATION",
    "SshBundleSource",
    "SshCatalog",
    "SshFacts",
    "SshSearchIndexSource",
    "SshUploader",
]

# ─────────────────────────── the commands, all BusyBox-safe ───────────────────────────
#
#  BusyBox 1.36.1, and no GNU long options: `head -25` is not accepted, `sed -n 1,25p` is.
#  Neither `file`, `sqlite3` nor `python3` is installed, so nothing here can lean on them.
#  Every template is a literal spelled in this module; only its arguments come from data,
#  and `RemoteCommand.of` quotes those.

#: Read the first three lines of ``/proc/meminfo``, which is where ``MemTotal``,
#: ``MemFree`` and ``MemAvailable`` are. Three rather than two: ``MemFree`` sits between the
#: two lines that are wanted.
MEMINFO_TEMPLATE: Final = "sed -n 1,3p {}"

#: Report the filesystem holding the argument, in POSIX output format with 1024-byte
#: blocks. See divergence 4: the ``-P`` is what keeps the numbers on one line.
STORAGE_TEMPLATE: Final = "df -Pk {}"

#: Print the value of the ``IMG_VERSION`` line and nothing else. A ``sed`` script rather
#: than ``grep | cut``: one process, and a file that does not carry the line prints nothing
#: instead of failing, which is the "asked and did not answer" case the port wants.
FIRMWARE_TEMPLATE: Final = "sed -n 's/^IMG_VERSION=\"\\(.*\\)\"$/\\1/p' {}"

#: Print the board name.
MODEL_TEMPLATE: Final = "cat {}"

#: Create a document's page directory, and its parents if somehow absent.
MAKE_DIR_TEMPLATE: Final = "mkdir -p {}"

#: Restart the tablet's UI process so it re-indexes the store. See
#: :meth:`SshUploader.upload` for the blast radius.
REFRESH_TEMPLATE: Final = "systemctl restart xochitl"


# ─────────────────────────── what the sidecars say ───────────────────────────

#: The ``type`` value that makes a store entry a document rather than a folder.
DOCUMENT_TYPE: Final = "DocumentType"

#: The ``formatVersion`` all 36 real documents on the reference device carry.
CONTENT_FORMAT_VERSION: Final = 2

#: Indentation of the sidecars this module writes. Four spaces, matching 41 of the 42 real
#: ``.metadata`` files, so a user diffing an uploaded document against a native one sees
#: only the fields differ.
JSON_INDENT: Final = 4

#: The one :class:`~rmspec.domain.ports.device.DeviceFacts` field this transport
#: structurally cannot answer. See divergence 6.
SERIAL_FIELD: Final = "serial"

#: The one :class:`~rmspec.domain.ports.device.UploadMedia` member :class:`SshUploader`
#: refuses. See its docstring: an archive is a container of a whole document, so placing one
#: here would mean unpacking it and writing the sidecars this module composes for the other
#: two -- a different operation with different failure modes, not a media conversion.
UNPLACEABLE_MEDIA: Final = UploadMedia.RMDOC

#: What the refusal of :data:`UNPLACEABLE_MEDIA` is called. Derived from the member's own
#: value rather than spelled, so the two cannot disagree, and shaped like the USB uploader's
#: ``upload`` so a shell sees one vocabulary for both refusals.
UNPLACEABLE_OPERATION: Final = f"upload {UNPLACEABLE_MEDIA.value}"

#: The xochitl root :class:`SshSearchIndexSource` reads when its caller names none. A module
#: constant rather than a ``RemotePath.root()`` call written into the signature: a call in a
#: default argument is evaluated once at import whatever it looks like, and ruff's ``B008``
#: refuses the spelling that hides that. The other four adapters take ``root`` without a
#: default because each is constructed next to a catalog that must be given the same one.
_DEFAULT_ROOT: Final = RemotePath.root()

_ORIENTATION: Final = "portrait"
_MEM_TOTAL_LABEL: Final = "MemTotal:"
_MEM_AVAILABLE_LABEL: Final = "MemAvailable:"
_BYTES_PER_BLOCK: Final = 1024

#: Which line of ``df -Pk`` output carries the numbers, zero-based. Exactly one line, and
#: it is guaranteed by ``-P``; plain ``df -k`` wraps a long device name and puts the
#: numbers on the next line instead, which is why reading any *other* line would hide the
#: bug this constant exists to avoid.
DF_DATA_LINE: Final = 1

#: Field index of the total 1024-byte block count in one ``df -Pk`` data line.
DF_TOTAL_FIELD: Final = 1

#: Field index of the available 1024-byte block count. Not derived from ``Used`` and
#: ``Available``: reserved blocks make the two disagree with the total (measured
#: 112564 + 47929504 < 48568796), so a derived total would be quietly wrong.
DF_AVAILABLE_FIELD: Final = 3

_FILE_TYPES: Final[Mapping[SourceKind, DeviceFileType]] = {
    SourceKind.NOTEBOOK: DeviceFileType.NOTEBOOK,
    SourceKind.PDF: DeviceFileType.PDF,
    SourceKind.EPUB: DeviceFileType.EPUB,
}
"""Every :class:`~rmspec.domain.models.SourceKind` mapped to its device file type.

Total over a closed enum, and subscripted rather than ``get``-ed on purpose: a missing key
would mean the domain grew a source kind this adapter has not been taught, which is a
change to review and not a runtime condition to degrade around.
"""

_UNKNOWN_SOURCE: Final = (
    "the .content sidecar records no file type this domain represents, so the entry's kind "
    "is unknown; a pdf reported as a notebook would export with no background"
)

#: What the device is left holding when a step *before* the commit point fails. No listing
#: reports these as a document, because the ``.metadata`` that would make them one was never
#: attempted. Nothing is deleted: removal is itself destructive, and a failed ``mkdir`` may
#: mean the directory already held something the user wants.
BEFORE_COMMIT_NOTE: Final = (
    "nothing was removed, so {} and any sidecar already written are orphans that no listing "
    "reports as a document"
)

#: What the device is left holding when the commit point itself fails. Distinct from
#: :data:`BEFORE_COMMIT_NOTE` because a partly written ``.metadata`` *is* named by a listing:
#: the entry becomes visible to the catalog and skipped as unreadable, which is a different
#: thing for a user to go and look at.
AT_COMMIT_NOTE: Final = (
    "nothing was removed; {} may exist partly written, in which case a listing reports the "
    "entry and cannot decode it"
)

#: What the device is left holding when only the refresh failed. The document is complete and
#: correct; it is simply not in the running UI process's index yet.
AFTER_COMMIT_NOTE: Final = (
    "{} is completely written and will appear the next time the tablet UI starts"
)


class SshCatalog:
    """The library, read from one listing of the xochitl root over SSH.

    Implements :class:`~rmspec.domain.ports.device.DeviceCatalog`. The store is a flat
    directory: ``<uuid>.metadata`` is the only mandatory sidecar, and its presence is what
    makes an identifier a document. So one ``ls -A`` names every entry, and that single
    listing also decides which optional sidecars exist -- a folder, which never has a
    ``.content``, therefore costs no failed read.

    ``trashed`` is real here, unlike over the USB web API, which filters trashed entries out
    of every listing. The censuses on the reference device are ``parent`` = 32 uuid / 9
    empty string / 1 literal ``"trash"``, and ``deleted`` absent on 28 of 42, ``false`` on
    14 and never ``true`` -- so the ``parent`` sentinel is the only one ever exercised, and
    :class:`~rmspec.domain.models.DocumentMetadata` already reports the *or* of both. A
    trashed entry's original parent is not recoverable, because the field's value *is*
    ``"trash"``, which is why ``parent_uuid`` is ``None`` for one.

    Parameters
    ----------
    shell
        The transport. Nothing else in this class touches a wire.
    root
        The xochitl root to enumerate. A parameter rather than
        :meth:`~rmspec.device.addresses.RemotePath.root` so a test can build a synthetic
        tree anywhere and so a future mirror transport needs no second copy of this walk.
    """

    def __init__(self, *, shell: RemoteShell, root: RemotePath) -> None:
        self._shell = shell
        self._root = root
        self._listing: DeviceListing | None = None

    def list_documents(self) -> DeviceListing:
        """Enumerate every document and folder the store holds.

        Memoised: one :class:`~rmspec.domain.ports.device.DeviceListing` per instance,
        built on first need. ``ports/device.py`` says every port is one view over a single
        ``Scope.REQUEST`` transport resource, so an instance's lifetime is one command and
        a second call cannot observe a changed store.

        Returns
        -------
        DeviceListing
            Every entry that validated, split by kind, plus every entry that did not.
            Entries are in the order ``ls -A`` produced them; no order is imposed, because
            the port documents none.

        Raises
        ------
        DeviceUnreachable
            The listing itself failed: nothing answered, or the session stalled. A failure
            of the listing is a whole-transport failure and propagates.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            The root could not be listed.
        """
        if self._listing is None:
            self._listing = self._enumerate()
        return self._listing

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Look up one document in the memoised listing.

        Honours the three coherence rules ``ports/device.py`` states. An identifier naming a
        *folder* falls through to the same ``DeviceDocumentNotFound`` an unknown identifier
        gets, and deliberately has no branch of its own: that error carries only the
        identifier, so a separate branch would build an indistinguishable value and could
        not be told apart by any assertion.

        Parameters
        ----------
        doc_uuid
            The identifier to resolve.

        Returns
        -------
        DeviceDocument
            The listed document, equal to the one
            :meth:`list_documents` reports.

        Raises
        ------
        DeviceDocumentNotFound
            No entry has that identifier, or it names a folder.
        MalformedDeviceMetadata
            The entry exists and was skipped, whichever
            :class:`~rmspec.domain.ports.device.SkipReason` the listing recorded.
        DeviceUnreachable
            The listing had not been built and the transport failed.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            The root could not be listed.
        """
        listing = self.list_documents()
        for document in listing.documents:
            if document.uuid == doc_uuid:
                return document
        for entry in listing.skipped:
            if entry.uuid == doc_uuid:
                raise MalformedDeviceMetadata(
                    transport=TransportKind.SSH,
                    document_uuid=doc_uuid,
                    detail=entry.detail,
                )
        raise DeviceDocumentNotFound(transport=TransportKind.SSH, document_uuid=doc_uuid)

    def _enumerate(self) -> DeviceListing:
        """Walk the root listing once, turning each ``.metadata`` stem into a value.

        Returns
        -------
        DeviceListing
            The documents, folders and skips, in listing order.

        Raises
        ------
        DeviceProtocolError
            The xochitl root itself could not be listed.
        DeviceError
            The transport failed. Only a *per-path* read failure is recorded rather than
            raised; anything that describes the session propagates.
        """
        try:
            names = self._shell.list_dir(self._root)
        except PathUnreadableError as unreadable:
            raise _contradiction(unreadable, expected="a readable xochitl root") from unreadable
        present = frozenset(names)
        documents: list[DeviceDocument] = []
        folders: list[DeviceFolder] = []
        skipped: list[SkippedEntry] = []
        for name in names:
            if not name.endswith(METADATA_SUFFIX):
                continue
            doc_uuid = name.removesuffix(METADATA_SUFFIX)
            decoded = self._entry(
                doc_uuid,
                has_content=f"{doc_uuid}{CONTENT_SUFFIX}" in present,
            )
            if isinstance(decoded, DeviceDocument):
                documents.append(decoded)
            elif isinstance(decoded, DeviceFolder):
                folders.append(decoded)
            else:
                skipped.append(decoded)
        return DeviceListing(
            documents=tuple(documents),
            folders=tuple(folders),
            skipped=tuple(skipped),
        )

    def _entry(
        self,
        doc_uuid: str,
        /,
        *,
        has_content: bool,
    ) -> DeviceDocument | DeviceFolder | SkippedEntry:
        """Read one entry's sidecars and decode them, or say why it could not become a value.

        The two sidecars are decoded in two steps, and the order is what makes the
        ``SkipReason`` split honest. ``.metadata`` alone decides whether the entry is
        *well-formed*; ``.content`` decides what *kind* of document it is. So a ``fileType``
        this domain has no member for is ``VALIDATION_FAILED`` -- it decoded, and describes
        nothing representable, which is also what the USB sibling reports for the same
        payload -- while a corrupt ``.metadata`` stays ``MALFORMED_METADATA``.
        :meth:`~rmspec.domain.models.DocumentMetadata.decode` raises the same ``ValueError``
        for both cases, and matching on its message would be worse than paying for a second
        json parse on a path that only runs for entries that are already unusual.

        Parameters
        ----------
        doc_uuid
            The stem of the ``.metadata`` file the listing named.
        has_content
            Whether the same listing also named a ``.content`` sibling. The one listing
            decides presence, so an absent optional sidecar costs no failed read.

        Returns
        -------
        DeviceDocument | DeviceFolder | SkippedEntry
            The decoded value, or the reason there is none.

        Raises
        ------
        DeviceError
            The transport failed. Only :class:`~rmspec.device._shell.PathUnreadableError` --
            *this one path* was refused -- becomes a skip; a session-level failure propagates,
            because "the cable was pulled at entry 30" is not a fact about entry 30.
        """
        try:
            paths = document_paths(self._root, doc_uuid)
        except ValueError as unusable:
            return SkippedEntry(
                uuid=doc_uuid or None,
                reason=SkipReason.VALIDATION_FAILED,
                detail=f"the store entry cannot be addressed as one path component: {unusable}",
            )
        try:
            raw = self._shell.read_file(paths.metadata)
            content = self._shell.read_file(paths.content) if has_content else None
        except PathUnreadableError as refused:
            return SkippedEntry(
                uuid=doc_uuid,
                reason=SkipReason.UNREADABLE,
                detail=str(refused),
            )
        try:
            bare = DocumentMetadata.decode(raw)
        except ValidationError as invalid:
            return SkippedEntry(
                uuid=doc_uuid,
                reason=SkipReason.VALIDATION_FAILED,
                detail=_validation_detail(invalid),
            )
        except (TypeError, ValueError) as malformed:
            return SkippedEntry(
                uuid=doc_uuid,
                reason=SkipReason.MALFORMED_METADATA,
                detail=str(malformed),
            )
        if bare.kind is DocumentKind.COLLECTION:
            return DeviceFolder(
                uuid=doc_uuid,
                name=bare.visible_name,
                parent_uuid=bare.parent_uuid,
                last_modified=bare.last_modified,
                trashed=bare.trashed,
            )
        return _as_document(doc_uuid, raw, content)


class SshBundleSource:
    """One document's ordered pages and its underlay, read file by file over SSH.

    Implements :class:`~rmspec.domain.ports.device.RawBundleSource`. The page order comes
    from the ``.content`` sidecar's ``cPages`` list, and a ``.rm`` file present in the
    document's directory but absent from that list is an **orphan layer** and is dropped:
    iterating the directory instead would render ghost pages, which is measurable -- one
    reference document holds 16 ``.rm`` files for 10 pages.

    A scene is read only when the directory listing names it. That avoids one failed read
    per unwritten page, which on a real annotated PDF is 429 of them. An absent artifact and
    a zero-byte artifact both become ``scene=None``:
    :attr:`~rmspec.domain.ports.device.DevicePageSource.scene` documents ``None`` as "the
    page carries no ink", and 86 of the 194 real ``.rm`` files are exactly zero bytes.

    A read during active editing can see a half-written or absent artifact: the firmware
    writes a page to a ``<page>.rm.tmp`` sibling and renames it over the target, so the
    window exists. An artifact the listing did not name is the routine case and needs no
    special handling -- it is a page with no ink. One the listing *did* name and that then
    could not be opened is the rename window, and it raises ``DeviceProtocolError`` rather
    than being folded into ``scene=None``: a page that had ink a moment ago must not be
    exported blank, and ``DocumentSourceBundle`` is documented as all-or-nothing precisely so
    a half-pulled document cannot be recorded as complete. A partially written artifact that
    opens is still indistinguishable from a complete one at this layer; saying so here is the
    point, because the alternative is a future reader assuming this transport is atomic.

    Parameters
    ----------
    shell
        The transport.
    root
        The xochitl root the document lives under.
    catalog
        Where ``document`` comes from, so ``bundle.document`` equals what
        :meth:`SshCatalog.get_document` returns rather than a second decode of the same
        sidecar that could disagree with it.
    """

    def __init__(self, *, shell: RemoteShell, root: RemotePath, catalog: SshCatalog) -> None:
        self._shell = shell
        self._root = root
        self._catalog = catalog

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Fetch every source file of one document.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.

        Returns
        -------
        DocumentSourceBundle
            The pages in recorded order with their scenes and templates, plus the underlay
            for a document that has one. ``base`` is ``None`` for a notebook and bytes
            otherwise; :class:`~rmspec.domain.ports.device.DocumentSourceBundle` rejects
            the mismatch either way, so neither case can be reported wrongly.

        Raises
        ------
        DeviceDocumentNotFound
            The catalog holds no such document, or the identifier names a folder.
        MalformedDeviceMetadata
            The ``.content`` sidecar will not decode, or it names a page this transport
            cannot address as one path component.
        DeviceTransferInterrupted
            A transfer ended early.
        DeviceUnreachable
            The tablet did not answer.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            The store contradicted itself, or a read failed. A non-notebook document whose
            underlay is absent is the clearest case: the document's own ``.content`` says it
            annotates a pdf or an epub and the store does not hold one, which is a fault in
            what the device answered and not a reachability problem. The same class covers a
            missing ``.content``, an unlistable page directory, and an artifact the listing
            named that could not then be opened.
        DeviceUnreachable
            The tablet did not answer, or a read stalled.
        """
        document = self._catalog.get_document(doc_uuid)
        paths = document_paths(self._root, doc_uuid)
        order = self._page_order(doc_uuid, paths)
        pages = self._pages(doc_uuid, paths, order)
        base = (
            None
            if document.file_type is DeviceFileType.NOTEBOOK
            else self._underlay(paths, document.file_type)
        )
        return DocumentSourceBundle(document=document, pages=pages, base=base)

    def _underlay(self, paths: DocumentPaths, file_type: DeviceFileType, /) -> bytes:
        """Read the source file a non-notebook document annotates.

        Parameters
        ----------
        paths
            Where the document's artifacts live.
        file_type
            The recorded kind, whose value is the bare suffix ``.content`` spells.

        Returns
        -------
        bytes
            The underlay, zero-length included --
            :class:`~rmspec.domain.ports.device.DocumentSourceBundle` requires bytes here and
            deciding whether an empty pdf is usable belongs to the export slice.

        Raises
        ------
        DeviceProtocolError
            The store holds no underlay for a document whose ``.content`` names one. Not
            pre-checked with a second listing of the root: the read already answers the
            question, and this is the honest classification of the answer.
        DeviceError
            The transport failed.
        """
        path = paths.underlay(file_type.value)
        try:
            return self._shell.read_file(path)
        except PathUnreadableError as unreadable:
            raise _contradiction(
                unreadable,
                expected=f"the {file_type.value} underlay this document's .content names",
            ) from unreadable

    def _page_order(self, doc_uuid: str, paths: DocumentPaths, /) -> tuple[PageOrderEntry, ...]:
        """Read the ``.content`` sidecar and decode the page order it claims.

        Parameters
        ----------
        doc_uuid
            The document's identifier, for the error.
        paths
            Where its artifacts live.

        Returns
        -------
        tuple[PageOrderEntry, ...]
            The pages the sidecar claims, in file order.

        Raises
        ------
        MalformedDeviceMetadata
            The sidecar is not the shape a page order can be read from.
        DeviceProtocolError
            The sidecar could not be read at all, even though the catalog read it a moment
            ago to decide this document's kind -- so the store has changed under the command
            or is contradicting itself.
        DeviceError
            The transport failed.
        """
        try:
            content = self._shell.read_file(paths.content)
        except PathUnreadableError as unreadable:
            raise _contradiction(
                unreadable,
                expected="the .content sidecar the catalog read for this document",
            ) from unreadable
        try:
            return decode_page_order(content)
        except (TypeError, ValueError) as invalid:
            raise MalformedDeviceMetadata(
                transport=TransportKind.SSH,
                document_uuid=doc_uuid,
                detail=f"the .content page order cannot be read: {invalid}",
            ) from invalid

    def _pages(
        self,
        doc_uuid: str,
        paths: DocumentPaths,
        order: tuple[PageOrderEntry, ...],
        /,
    ) -> tuple[DevicePageSource, ...]:
        """Fetch the scene of every page the order claims, in that order.

        Parameters
        ----------
        doc_uuid
            The document's identifier, for an error.
        paths
            Where its artifacts live.
        order
            The claimed pages.

        Returns
        -------
        tuple[DevicePageSource, ...]
            One source per claimed page. An empty order costs no directory listing, because
            there is nothing to look for in it.

        Raises
        ------
        MalformedDeviceMetadata
            A claimed page id is not usable as one path component.
        DeviceProtocolError
            The document's page directory could not be listed, although its ``.content``
            claims pages that would live in it.
        DeviceError
            The transport failed.
        """
        if not order:
            return ()
        try:
            present = frozenset(self._shell.list_dir(paths.page_dir))
        except PathUnreadableError as unreadable:
            raise _contradiction(
                unreadable,
                expected="a readable page directory for a document that claims pages",
            ) from unreadable
        return tuple(
            DevicePageSource(
                page_id=entry.page_id,
                scene=self._scene(doc_uuid, paths, entry, present),
                template_name=entry.template_name,
            )
            for entry in order
        )

    def _scene(
        self,
        doc_uuid: str,
        paths: DocumentPaths,
        entry: PageOrderEntry,
        present: frozenset[str],
        /,
    ) -> bytes | None:
        """Read one page's scene bytes, or report that it carries no ink.

        Parameters
        ----------
        doc_uuid
            The document's identifier, for an error.
        paths
            Where its artifacts live.
        entry
            The claimed page.
        present
            Names the document's directory holds.

        Returns
        -------
        bytes | None
            The artifact's bytes, or ``None`` when the directory does not hold it or holds
            it at zero length. Both are "the page carries no ink"; ``b""`` is never
            returned, because the port spells that state ``None``.

        Raises
        ------
        MalformedDeviceMetadata
            The page id is not usable as one path component, so no artifact can be named
            for it.
        DeviceProtocolError
            The listing named this artifact and it could not then be opened -- the rename
            window of an active edit, or a permission fault. Never folded into ``None``: a
            page that had ink a moment ago must not be exported blank.
        DeviceError
            The transport failed.
        """
        if f"{entry.page_id}{SCENE_SUFFIX}" not in present:
            return None
        try:
            path = paths.page(entry.page_id)
        except ValueError as unusable:
            raise MalformedDeviceMetadata(
                transport=TransportKind.SSH,
                document_uuid=doc_uuid,
                detail=f"page {entry.page_id!r} cannot be addressed: {unusable}",
            ) from unusable
        try:
            return self._shell.read_file(path) or None
        except PathUnreadableError as unreadable:
            raise _contradiction(
                unreadable,
                expected="the scene artifact this document's directory listing named",
            ) from unreadable


class SshUploader:
    """Place one document in the xochitl store, sidecars and all, over SSH.

    Implements :class:`~rmspec.domain.ports.device.DocumentUploader`. ``parent_uuid`` is
    always honoured -- written into the ``.metadata`` this class composes, as ``parent`` -- so
    a destination is never degraded to the library root, which ``ports/device.py`` forbids
    outright. This is the half of the write surface the USB uploader does not have.

    What it will not place: :data:`UNPLACEABLE_MEDIA`
    -----------------------------------------------
    :attr:`~rmspec.domain.ports.device.UploadMedia.RMDOC` raises
    ``DeviceOperationUnsupported`` naming ``TransportKind.USB_WEB_API``, which is where an
    archive *can* be placed -- xochitl's own import route accepts one, measured 2026-08-29.

    It is refused here rather than supported because it is not a media this class could
    substitute into the sidecars it writes. The two placeable members are **underlays**: the
    payload is written to one path and this module composes the ``.content`` and ``.metadata``
    that describe it. An archive is a container of a whole document -- its own ``.metadata``,
    its own ``.content``, one ``.rm`` per page -- so placing one means unzipping it, deciding
    what to do when a member's uuid collides with a document already in the store, re-keying
    the pages if it does, and writing the result. That is a different operation with different
    failure modes, and pretending it is a third value of ``media`` would hide every one of
    them behind a signature that promises none.

    ``DeviceOperationUnsupported`` and not a silent conversion for the same reason
    ``parent_uuid`` is honoured rather than dropped: the port forbids substituting a media the
    adapter does prefer, because the receipt would then report success for something the
    caller did not ask for.

    Order, so a failure cannot leave a document the tablet will index
    ----------------------------------------------------------------
    ``<uuid>.metadata`` is what makes an identifier a document in the store, so it is
    written **last**:

    1. ``mkdir -p <root>/<uuid>``
    2. write ``<root>/<uuid>.<pdf|epub>``
    3. write ``<root>/<uuid>.content``
    4. write ``<root>/<uuid>.metadata``  -- the commit point
    5. ``systemctl restart xochitl``

    A failure before step 4 leaves files that no listing reports as a document. **Nothing is
    cleaned up.** Deleting is itself destructive, and this package will not remove files
    from a user's device on a path it cannot fully reason about -- a failed ``mkdir`` may
    mean the directory already held something. The orphan is named in the raised error
    instead, so a user can decide.

    Parameters
    ----------
    shell
        The transport.
    root
        The xochitl root to write into.
    now_ms
        Current time in epoch milliseconds. Injected so the composed sidecar bytes are
        deterministic under test, which is what makes a snapshot of them meaningful.
    new_uuid
        Mints the new document's identifier. Injected for the same reason. Its value is a
        composition-root fact rather than device data, so a return that is not one path
        component raises ``ValueError`` from
        :func:`~rmspec.device.addresses.document_paths` -- a wiring bug reported as one,
        not disguised as a device failure.
    """

    def __init__(
        self,
        *,
        shell: RemoteShell,
        root: RemotePath,
        now_ms: Callable[[], int],
        new_uuid: Callable[[], str],
    ) -> None:
        self._shell = shell
        self._root = root
        self._now_ms = now_ms
        self._new_uuid = new_uuid

    def upload(self, request: UploadRequest, /) -> UploadReceipt:
        """Write one document into the store and force the tablet UI to show it.

        Step 5 restarts the tablet's **entire UI process**, which also owns the firmware's
        HTTP listener -- so a USB catalog held open across an SSH upload will see its
        endpoint drop. :class:`~rmspec.domain.ports.device.LibraryRefresh` exists to report
        exactly this, and visibility is per upload: N documents force N refreshes.

        The receipt promises what the transport observed and no more. The device's
        synchronizer writes a ``.failure`` sidecar asynchronously, after validation, so
        nothing observable here says the document will sync. That gap is real and belongs to
        a sync-status use case.

        Parameters
        ----------
        request
            The document to place, with its media and destination folder.

        Returns
        -------
        UploadReceipt
            Carrying the minted identifier, ``byte_count == len(request.data)``, and
            :attr:`~rmspec.domain.ports.device.LibraryRefresh.VISIBILITY_FORCED`.

        Raises
        ------
        DeviceOperationUnsupported
            ``request.media`` is :data:`UNPLACEABLE_MEDIA`. Raised before anything is written,
            and naming the transport that can place one.
        DeviceTransferInterrupted
            A write landed short. Never a receipt reporting fewer bytes than were offered.
        DeviceUnreachable
            The tablet did not answer, or a step stalled.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            A command exited non-zero, or the session misbehaved.
        """
        if request.media is UNPLACEABLE_MEDIA:
            raise DeviceOperationUnsupported(
                transport=TransportKind.SSH,
                operation=UNPLACEABLE_OPERATION,
                supported_by=(TransportKind.USB_WEB_API,),
            )
        doc_uuid = self._new_uuid()
        paths = document_paths(self._root, doc_uuid)
        stamp = self._now_ms()
        try:
            self._shell.run(RemoteCommand.of(MAKE_DIR_TEMPLATE, paths.page_dir))
            self._shell.write_file(paths.underlay(request.media.value), request.data)
            self._shell.write_file(paths.content, _content_bytes(media=request.media))
        except DeviceError as failure:
            note = BEFORE_COMMIT_NOTE.format(paths.page_dir.value)
            raise _annotated(failure, note=note) from failure
        try:
            self._shell.write_file(
                paths.metadata,
                _metadata_bytes(
                    name=request.name,
                    parent_uuid=request.parent_uuid,
                    now_ms=stamp,
                ),
            )
        except DeviceError as failure:
            note = AT_COMMIT_NOTE.format(paths.metadata.value)
            raise _annotated(failure, note=note) from failure
        try:
            self._shell.run(RemoteCommand.of(REFRESH_TEMPLATE))
        except DeviceError as failure:
            raise _annotated(failure, note=AFTER_COMMIT_NOTE.format(doc_uuid)) from failure
        return UploadReceipt(
            doc_uuid=doc_uuid,
            name=request.name,
            media=request.media,
            byte_count=len(request.data),
            library_refresh=LibraryRefresh.VISIBILITY_FORCED,
        )


class SshFacts:
    """The tablet's fixed facts and its two gauges, read with four BusyBox commands.

    Implements :class:`~rmspec.domain.ports.device.DeviceFactsSource`. Nothing here raises
    for an unparseable reading: the port draws a distinction between a field this transport
    *structurally cannot ask* -- named in ``unsupported`` -- and a field it asked for and
    did not get an answer to, which is an unnamed ``None``. Both occur here.
    :attr:`SERIAL_FIELD` is the first; a firmware line that does not match, or a ``df``
    line that is not the expected shape, is the second. One bad reading never fails the
    whole command.

    Parameters
    ----------
    shell
        The transport. There is no ``root`` parameter: the storage reading needs a path on
        the documents partition, and :meth:`~rmspec.device.addresses.RemotePath.root` is the
        only xochitl root this firmware has. Passing it to ``df`` resolves to the ``/home``
        mount, so no caller has to know the mount point.
    """

    def __init__(self, *, shell: RemoteShell) -> None:
        self._shell = shell

    def read_facts(self) -> DeviceFacts:
        """Read the firmware version and the board name.

        Returns
        -------
        DeviceFacts
            ``firmware`` from the ``IMG_VERSION`` line of
            :data:`~rmspec.device.addresses.OS_RELEASE` (``3.27.3.0``), ``model`` from
            :data:`~rmspec.device.addresses.SOC_MACHINE` (``reMarkable Ferrari``), and
            ``serial`` ``None`` with :data:`SERIAL_FIELD` named in ``unsupported``. Either
            answered field is ``None`` when its command produced nothing readable.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            A command exited non-zero, or the session misbehaved.
        """
        firmware = _first_line(
            self._shell.run(RemoteCommand.of(FIRMWARE_TEMPLATE, RemotePath.absolute(OS_RELEASE)))
        )
        model = _first_line(
            self._shell.run(RemoteCommand.of(MODEL_TEMPLATE, RemotePath.absolute(SOC_MACHINE)))
        )
        return DeviceFacts(
            firmware=firmware,
            model=model,
            serial=None,
            unsupported=frozenset({SERIAL_FIELD}),
        )

    def read_resources(self) -> DeviceResources:
        """Read the memory and storage gauges as they are right now.

        Returns
        -------
        DeviceResources
            Memory from ``MemTotal`` and ``MemAvailable``, storage from ``df -Pk``, all
            four in bytes -- both sources report 1024-byte units. ``unsupported`` is empty:
            this transport can ask for every one of them, so a ``None`` here always means
            the reading was not intelligible. A pair whose free value exceeds its total is
            the signature of a mis-read column, so both halves of that pair are reported
            as unanswered rather than passed to a validator that would reject the whole
            reading.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            A command exited non-zero, or the session misbehaved.
        """
        memory = _memory_bytes(
            self._shell.run(RemoteCommand.of(MEMINFO_TEMPLATE, RemotePath.absolute(PROC_MEMINFO)))
        )
        storage = _storage_bytes(
            self._shell.run(RemoteCommand.of(STORAGE_TEMPLATE, RemotePath.root()))
        )
        return DeviceResources(
            total_memory_bytes=memory[0],
            available_memory_bytes=memory[1],
            total_storage_bytes=storage[0],
            available_storage_bytes=storage[1],
            unsupported=frozenset(),
        )


class SshSearchIndexSource:
    """The tablet's own handwriting search index, moved across as one database image.

    Implements :class:`~rmspec.domain.ports.device.SearchIndexSource`. One read of one file,
    and that is deliberately the whole of it.

    Why this port hands over bytes rather than rows
    ----------------------------------------------
    **There is no ``sqlite3`` binary on the device and no BusyBox applet for one**, measured
    2026-08-29 on firmware 3.27.3.0 against ``busybox --list``. Querying on-device is
    therefore not an available shape, and transport-the-image-read-it-here is the only one
    left. The reading half is :class:`rmspec.persistence.DeviceSearchIndex`, in the one
    package allowed to import ``sqlite3``; this one may not, so it cannot look inside what it
    carries even to sanity-check it.

    The bytes stay in memory. Nothing here opens a local file, and the port takes no path,
    so the image goes straight to a caller that deserialises it in process. An earlier
    session ``scp``-ed this file onto local disk to inspect it; that is what this shape
    replaced, and it matters because the index holds the user's handwriting -- 90 of the 92
    rows on the measured device carry recognised text.

    The bytes may be a torn snapshot, and the reader must check
    ----------------------------------------------------------
    reMarkable's own documentation states that *"Xochitl must not run when manually accessing
    document files"*, and this file is xochitl's **live** search index. So this adapter reads
    a database that may be mid-write, and it is deliberately **not** its job to detect that:
    it moves bytes, and a per-page integrity opinion formed by a transport would be a second
    opinion the reader could disagree with.

    Saying so here is the load-bearing part, because the failure is silent. Measured locally
    against CPython 3.13 and SQLite 3.50.4: an image truncated to ~99% of its length
    **deserialises cleanly, answers queries, and returns confidently wrong rows** -- one page's
    handwriting attributed to another, with no exception anywhere. ``PRAGMA quick_check``
    catches it, reporting ``Rowid ... out of order`` where ``select count(*)`` happily
    returned 500. :class:`rmspec.persistence.DeviceSearchIndex` is therefore **required** to
    run that pragma and require exactly ``[("ok",)]`` before trusting a row, and a second
    consumer of these bytes that skips it is the same defect wearing a new name.

    Why there is no USB binding
    ---------------------------
    SSH file access is this project's documented-hazard *fallback*, not its default: the
    default read path is the USB web API, whose ``GET /download/{id}/rmdoc`` is served by
    xochitl itself and is a consistent snapshot by construction. This adapter exists because
    the search index has **no** USB route at all -- that firmware's route table is closed at
    six families and none of them serves a file from the xochitl tree. So there is nothing to
    bind on the USB side, and ``test_device_conformance.py`` asserts that no other name in this
    package satisfies this port, so a later reader cannot "fix" the absence without failing a
    test.

    This used to cite :class:`~rmspec.domain.ports.device.DocumentUploader` as the sibling case
    of a port with one binding. It is no longer one: ``POST /upload`` was measured on
    2026-08-29 and :class:`~rmspec.device.usb.UsbUploader` exists, which sharpens rather than
    weakens the argument here -- an absence justified by "unprobed" is provisional and turned
    out to be, while an absence justified by "the route table has no such family" is not.

    Parameters
    ----------
    shell
        The transport. Nothing else in this class touches a wire.
    root
        The xochitl root the index sits directly under, defaulting to
        :data:`~rmspec.device.addresses.XOCHITL_ROOT`. A parameter, like every other root in
        this module, so a test can build a synthetic tree anywhere; defaulted, unlike the
        others, because this adapter is bound alone rather than paired with a catalog that
        has to agree with it.
    """

    def __init__(self, shell: RemoteShell, *, root: RemotePath = _DEFAULT_ROOT) -> None:
        self._shell = shell
        self._root = root

    def read_index(self) -> bytes | None:
        """Read the whole search-index image, or report that the device has none.

        Returns
        -------
        bytes | None
            The image, or ``None`` when the file is not there. A device that has never built
            an index is the honest cause of that, and the port spells it ``None`` so a caller
            can tell it apart from an index that exists and holds no row for a page -- which
            is the dominant state, since the index lags the tablet.

            A file that exists at **zero length** reads as ``b""`` and not as ``None``, which
            is the opposite of the rule :class:`SshBundleSource` applies to a scene artifact
            and deliberately so. There, ``None`` means "this page carries no ink" and a
            zero-byte artifact is the routine way the firmware says it -- 86 of the 194 real
            ones are exactly that. Here, ``None`` means "this device has no index", so a
            zero-length index file is a device that *has* one and whose one is unusable. That
            is a different fact, and the reader reports it as ``StoreUnavailableError`` when
            it tries to open it rather than as "no index".

        Raises
        ------
        DeviceUnreachable
            The transport died, or the shell was never connected. A per-path read failure is
            *not* this: an absent or refused index is ``None``, because
            :class:`~rmspec.device._shell.PathUnreadableError` separates the two and this is
            the case that separation exists for.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            The channel misbehaved.
        """
        try:
            return self._shell.read_file(self._root.child(SEARCH_INDEX_NAME))
        except PathUnreadableError:
            return None


def _contradiction(
    unreadable: PathUnreadableError,
    /,
    *,
    expected: str,
) -> DeviceProtocolError:
    """Convert one unreadable path into the domain error for a store that contradicts itself.

    The single place :class:`~rmspec.device._shell.PathUnreadableError` becomes a domain
    error, used wherever this module has *no* per-entry answer to give. Every such site
    reached the path from something the device itself said -- a directory listing, a
    ``.content`` sidecar, the catalog -- so a path that then cannot be opened is the device
    disagreeing with its own answer, which is what
    :class:`~rmspec.domain.errors.DeviceProtocolError` means. It is emphatically not
    ``DeviceUnreachable``: the session is demonstrably alive, since it answered the thing that
    named the path.

    Parameters
    ----------
    unreadable
        What the shell raised.
    expected
        The contract the store broke, phrased as what should have been there.

    Returns
    -------
    DeviceProtocolError
        Naming the path as the route, so the report identifies the file rather than the host.
        Returned, never raised, so the traceback starts at the call site.
    """
    return DeviceProtocolError(
        transport=TransportKind.SSH,
        route=unreadable.path,
        expected=expected,
        got=unreadable.detail,
    )


def _as_document(
    doc_uuid: str,
    raw: bytes,
    content: bytes | None,
    /,
) -> DeviceDocument | SkippedEntry:
    """Decode a document entry's kind from its ``.content`` sidecar, or refuse it.

    Parameters
    ----------
    doc_uuid
        The entry's identifier.
    raw
        The ``.metadata`` bytes, already known to decode on their own.
    content
        The ``.content`` bytes, or ``None`` when the listing named none.

    Returns
    -------
    DeviceDocument | SkippedEntry
        The document when the recorded source names a file type this domain represents;
        otherwise a skip with
        :attr:`~rmspec.domain.ports.device.SkipReason.VALIDATION_FAILED`, because the
        alternative is *defaulting* the kind -- and a pdf silently reported as a notebook is
        an export with no background and no defect recorded anywhere.
    """
    if content is None:
        return SkippedEntry(
            uuid=doc_uuid,
            reason=SkipReason.VALIDATION_FAILED,
            detail=_UNKNOWN_SOURCE,
        )
    try:
        metadata = DocumentMetadata.decode(raw, content=content)
    except (TypeError, ValueError) as unrepresentable:
        return SkippedEntry(
            uuid=doc_uuid,
            reason=SkipReason.VALIDATION_FAILED,
            detail=f"the .content sidecar describes nothing representable: {unrepresentable}",
        )
    if metadata.source is None:
        return SkippedEntry(
            uuid=doc_uuid,
            reason=SkipReason.VALIDATION_FAILED,
            detail=_UNKNOWN_SOURCE,
        )
    return DeviceDocument(
        uuid=doc_uuid,
        name=metadata.visible_name,
        file_type=_FILE_TYPES[metadata.source],
        parent_uuid=metadata.parent_uuid,
        last_modified=metadata.last_modified,
        page_count=_page_count(content),
        trashed=metadata.trashed,
    )


def _page_count(content: bytes, /) -> int | None:
    """Count the pages the ``.content`` sidecar records.

    ``DeviceDocument.page_count`` is "whatever the device recorded", and the ``cPages`` list
    *is* what the device recorded -- so its length is the honest answer over a transport
    that has the sidecar in hand.

    Parameters
    ----------
    content
        The sidecar's bytes.

    Returns
    -------
    int | None
        How many pages it claims, or ``None`` when the page order will not decode. The
        metadata around it may still be perfectly readable, so one unreadable list must not
        cost the whole entry.
    """
    try:
        return len(decode_page_order(content))
    except (TypeError, ValueError):
        return None


def _validation_detail(error: ValidationError, /) -> str:
    """Render a pydantic failure as one line a human can read.

    Parameters
    ----------
    error
        The failure raised while decoding store metadata.

    Returns
    -------
    str
        Every complaint as ``field: message``, joined.
        :attr:`~rmspec.domain.ports.device.SkippedEntry.detail` is documented as displayed
        and logged, never parsed, so the shape is free to change.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )


def _metadata_bytes(*, name: str, parent_uuid: str | None, now_ms: int) -> bytes:
    """Compose the ``.metadata`` sidecar for a newly uploaded document.

    Eight keys: the 10-key set universal across all 42 real files, minus ``new`` and
    ``source``, which co-occur on only 27 of them and are not a generation marker. The
    legacy sync-v1 block -- ``deleted``, ``metadatamodified``, ``modified``, ``synced``,
    ``version`` -- is not written: it appears on 14 of 42, all older documents, and
    ``synced: false`` on a document the cloud has never seen states nothing.

    Parameters
    ----------
    name
        What the tablet UI should show.
    parent_uuid
        Destination folder, or ``None`` for the library root, which the store spells as the
        empty string.
    now_ms
        Epoch milliseconds for both timestamps. Written as *strings*, because that is what
        the store holds and what :meth:`~rmspec.domain.models.DocumentMetadata.decode`
        reads.

    Returns
    -------
    bytes
        UTF-8 JSON, indented by :data:`JSON_INDENT`.
    """
    stamp = str(now_ms)
    payload: dict[str, object] = {
        "createdTime": stamp,
        "lastModified": stamp,
        "lastOpened": "",
        "lastOpenedPage": 0,
        "parent": "" if parent_uuid is None else parent_uuid,
        "pinned": False,
        "type": DOCUMENT_TYPE,
        "visibleName": name,
    }
    return json.dumps(payload, indent=JSON_INDENT).encode("utf-8")


def _content_bytes(*, media: UploadMedia) -> bytes:
    """Compose the ``.content`` sidecar for a newly uploaded document.

    ``cPages.pages`` is empty and ``pageCount`` is ``0``. That is not a placeholder that
    somebody forgot: this port takes *bytes*, and counting the pages of a PDF needs a PDF
    library, which belongs to ``rmspec-export`` and which ``rmspec-device`` may not import.
    The device recomputes the page list when it imports the document, and a caller that
    needs an accurate count before then goes through the export slice first. Synthesising
    one page entry -- ``{"id": <uuid>, "idx": {"timestamp": "1:2", "value": "ba"}}``, which
    is the shape the firmware writes -- would claim a page structure this adapter has not
    read.

    Parameters
    ----------
    media
        What the payload is; becomes ``fileType`` under the same spelling ``.content`` uses.

    Returns
    -------
    bytes
        UTF-8 JSON, indented by :data:`JSON_INDENT`.
    """
    payload: dict[str, object] = {
        "cPages": {"pages": []},
        "fileType": media.value,
        "formatVersion": CONTENT_FORMAT_VERSION,
        "orientation": _ORIENTATION,
        "pageCount": 0,
        "tags": [],
        "extraMetadata": {},
    }
    return json.dumps(payload, indent=JSON_INDENT).encode("utf-8")


def _annotated(failure: DeviceError, /, *, note: str) -> DeviceError:
    """Restate a failure with what it left behind, keeping its class.

    Rebuilt rather than mutated, and rebuilt as the *same* class rather than folded into
    one: ``upload``'s documented ``Raises`` set distinguishes an unreachable tablet from
    refused credentials from a short write, and a caller deciding whether to retry needs
    that distinction more than it needs one uniform wrapper.

    Parameters
    ----------
    failure
        What the shell raised.
    note
        What the device is left holding.

    Returns
    -------
    DeviceError
        An equivalent error whose free-text field also carries *note*, or *failure*
        unchanged when its class has no field this function can extend -- in which case the
        original diagnosis is still the better value to raise.
    """
    match failure:
        case DeviceUnreachable():
            return DeviceUnreachable(
                transport=failure.transport,
                endpoint=failure.endpoint,
                detail=f"{failure.detail}; {note}",
            )
        case DeviceAuthFailed():
            return DeviceAuthFailed(
                transport=failure.transport,
                user=failure.user,
                detail=f"{failure.detail}; {note}",
                key_source=failure.key_source,
            )
        case DeviceTransferInterrupted():
            return DeviceTransferInterrupted(
                transport=failure.transport,
                subject=f"{failure.subject} ({note})",
                bytes_transferred=failure.bytes_transferred,
                bytes_expected=failure.bytes_expected,
            )
        case DeviceProtocolError():
            return DeviceProtocolError(
                transport=failure.transport,
                route=failure.route,
                expected=failure.expected,
                got=f"{failure.got}; {note}",
            )
        case _:
            return failure


def _first_line(stdout: str, /) -> str | None:
    """Read a one-value command's answer.

    Parameters
    ----------
    stdout
        Everything the command wrote.

    Returns
    -------
    str | None
        The first non-blank line, stripped, or ``None`` when there was none -- which is
        what a ``sed`` script that matched nothing produces, and is the port's "asked and
        did not answer".
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _memory_bytes(stdout: str, /) -> tuple[int | None, int | None]:
    """Read ``MemTotal`` and ``MemAvailable`` out of ``/proc/meminfo``.

    ``MemAvailable`` rather than ``MemFree``: free memory excludes the reclaimable page
    cache, so on a device holding 194 scene files it understates what a program can have by
    a wide margin. Measured on the reference device: ``MemTotal 2009400 kB``,
    ``MemFree 1224912 kB``, ``MemAvailable 1251016 kB``.

    Parameters
    ----------
    stdout
        The command's output.

    Returns
    -------
    tuple[int | None, int | None]
        Total and available bytes. Either is ``None`` when its line was absent or
        unreadable, and both are ``None`` when the pair is impossible.
    """
    total: int | None = None
    available: int | None = None
    for line in stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == _MEM_TOTAL_LABEL:
            total = _blocks_to_bytes(fields, 1)
        elif fields[0] == _MEM_AVAILABLE_LABEL:
            available = _blocks_to_bytes(fields, 1)
    return _consistent(total, available)


def _storage_bytes(stdout: str, /) -> tuple[int | None, int | None]:
    """Read the total and available block counts out of one ``df -Pk`` data line.

    Reads :data:`DF_DATA_LINE` and nothing else, deliberately. See divergence 4 in the
    module docstring: plain ``df -k`` wraps this device's 31-character device name past
    BusyBox's 20-column field and puts the numbers on the following line, and the point of
    ``-P`` is that it cannot. A parser that searched for "the first line with six fields"
    would read the wrapped shape correctly and thereby hide the reason ``-P`` is mandatory.

    Parameters
    ----------
    stdout
        The command's output, header line included.

    Returns
    -------
    tuple[int | None, int | None]
        Total and available bytes, or ``(None, None)`` when the expected line is missing or
        is not the expected shape.
    """
    lines = stdout.splitlines()
    if len(lines) <= DF_DATA_LINE:
        return (None, None)
    fields = lines[DF_DATA_LINE].split()
    return _consistent(
        _blocks_to_bytes(fields, DF_TOTAL_FIELD),
        _blocks_to_bytes(fields, DF_AVAILABLE_FIELD),
    )


def _blocks_to_bytes(fields: list[str], index: int, /) -> int | None:
    """Read one whitespace-separated field as a count of 1024-byte blocks.

    Parameters
    ----------
    fields
        The line, already split.
    index
        Which field to read.

    Returns
    -------
    int | None
        The field times 1024, or ``None`` when the field is absent, not an integer, or
        negative. ``None`` and not an exception: the port's second cause of ``None`` is
        exactly "asked and answered unintelligibly", and one bad column must not fail the
        whole command.
    """
    if index >= len(fields):
        return None
    try:
        blocks = int(fields[index])
    except ValueError:
        return None
    if blocks < 0:
        return None
    return blocks * _BYTES_PER_BLOCK


def _consistent(total: int | None, available: int | None, /) -> tuple[int | None, int | None]:
    """Refuse a gauge pair that cannot both be true.

    Parameters
    ----------
    total
        The total reading, or ``None``.
    available
        The free reading, or ``None``.

    Returns
    -------
    tuple[int | None, int | None]
        The pair, or ``(None, None)`` when the free value exceeds the total.
        :class:`~rmspec.domain.ports.device.DeviceResources` rejects that pair outright, and
        it is the signature of a mis-read column -- so reporting both halves as unanswered
        keeps one bad reading from failing a whole command, which is what the port asks for.
    """
    if total is not None and available is not None and available > total:
        return (None, None)
    return (total, available)
