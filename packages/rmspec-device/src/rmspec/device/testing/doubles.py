"""In-memory doubles for the four device ports, plus the shell seam all four share.

Test doubles, not second product adapters. They ship under ``src/`` rather than in a
``tests/`` helper for the reason ``rmspec.persistence.testing.doubles`` gives and this
package inherits: the architecture suite scans ``src/`` for import direction, so an
application **test** in step 6 may bind ``rmspec.device.testing`` while application
**source** stays domain-only. They ship in the wheel and are held to the same coverage
gate as the adapters, which is what keeps a double honest about the contract it claims to
satisfy -- ``packages/rmspec-device/tests/device_contracts.py`` runs one assertion set
against every double *and* every real adapter, so a double that quietly narrowed its
behaviour fails here rather than three packages away.

Nothing here opens a file, opens a socket, or imports ``sqlite3``, and no double's behaviour
depends on ``httpx`` or a live ``paramiko`` session. The four port doubles need only
``rmspec.domain``. :class:`FakeRemoteShell` additionally imports
:class:`~rmspec.device._shell.PathUnreadableError`, because the Protocol it satisfies raises
that type and a double that substituted something else would be a different contract; the
type is a plain ``Exception`` declared next to the Protocol, not a transport object.

Every seam is here because it makes one port guarantee assertable
----------------------------------------------------------------
``documents=`` / ``folders=`` / ``skipped=`` on the catalog
    The three coherence rules ``DeviceCatalog`` states -- an identifier in ``documents``
    resolves and equals the listed document, one in ``folders`` raises
    ``DeviceDocumentNotFound``, one in ``skipped`` raises ``MalformedDeviceMetadata``
    whichever :class:`~rmspec.domain.ports.device.SkipReason` the listing gave. A listing
    is *data* to this double, so all three branches are reachable in one construction
    instead of needing three malformed payloads.

``fail_with``
    A whole-transport failure raised from every method. ``ports/device.py`` says per-entry
    failure is data and whole-transport failure raises, and the failure mode it exists to
    forbid -- a dead cable degrading to an empty listing -- is indistinguishable from a
    genuinely empty library unless the transport can be told to die.

``truncate_at`` on the bundle source
    ``DeviceTransferInterrupted`` without a real short read, so "fetching is
    all-or-nothing" is assertable: no partial bundle is returned, and the raised error
    carries both byte counts.

``refresh`` on the uploader
    Both :class:`~rmspec.domain.ports.device.LibraryRefresh` members. The SSH adapter only
    ever produces ``VISIBILITY_FORCED`` -- it restarts the tablet UI unconditionally -- and
    there is no USB uploader, so ``ALREADY_VISIBLE`` is otherwise unreachable and therefore
    untested anywhere in the workspace.

``reject_with``
    ``DeviceUploadRejected`` carrying the device's own message, which is the only diagnosis
    that error has and the only thing a shell can show a user.

``honours_parent``
    ``DeviceOperationUnsupported`` from ``upload`` when the destination cannot be honoured.
    ``DocumentUploader`` forbids degrading a request -- placing at the root and reporting
    success is the exact failure the "no ``accepts()``" rule exists to prevent -- and no
    shipped adapter can produce it: the SSH uploader always writes ``parent`` into the
    ``.metadata``, and the USB uploader deliberately does not exist. Without this seam the
    rule is stated by the port and checked by nothing.

Call counters, on every double
    That one command is one handshake. ``list_documents()`` followed by ``get_document()``
    must not re-enumerate, and through a total port a memoised listing and a re-fetched one
    return equal values -- so :attr:`InMemoryDeviceCatalog.builds` is the only evidence
    which happened.

:class:`FakeRemoteShell`, and the private fake it supersedes
-----------------------------------------------------------
``packages/rmspec-device/tests/test_device_ssh.py`` already defines a module-private
``FakeShell`` over the same Protocol, and ``test_device_shell.py`` defines
``paramiko``-shaped fakes one layer below it -- those exist to test
:class:`~rmspec.device._shell.ParamikoShell` itself and are not what this is. This class is
the **durable public version** of the former: step 6's use-case tests need a
:class:`~rmspec.device._shell.RemoteShell` they can import rather than copy, and a double
that lives in one package's test module cannot be imported from another package's tests.
Saying that here rather than duplicating silently is the point; the private one stays where
it is because this work package does not own that file, and the conformance contract binds
*this* one, so the two cannot drift apart unnoticed without the contract going red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from rmspec.device._shell import PathUnreadableError
from rmspec.domain.errors import (
    DeviceDocumentNotFound,
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUploadRejected,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.ports.device import (
    DeviceFacts,
    DeviceFileType,
    DeviceListing,
    DeviceResources,
    DocumentSourceBundle,
    LibraryRefresh,
    UploadReceipt,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from rmspec.device.addresses import RemoteCommand, RemotePath
    from rmspec.domain.ports.device import (
        DeviceDocument,
        DeviceFolder,
        DevicePageSource,
        SkippedEntry,
        UploadRequest,
    )

__all__ = [
    "IN_MEMORY_ENDPOINT",
    "IN_MEMORY_TRANSPORT",
    "UPLOAD_OPERATION",
    "FakeRemoteShell",
    "InMemoryDeviceCatalog",
    "InMemoryDeviceFactsSource",
    "InMemoryDocumentUploader",
    "InMemoryRawBundleSource",
]

#: The transport every double names in the errors it raises. ``LOCAL_MIRROR`` is the one
#: member of the closed set that no adapter in this package binds -- D9 defers the mirror
#: transport to step 6 and puts it in ``rmspec-formats`` -- so an error raised by a double
#: is distinguishable from one raised by either real transport here.
IN_MEMORY_TRANSPORT: Final = TransportKind.LOCAL_MIRROR

#: The address label the doubles put in every error that has to name where a failure
#: happened, so a test can tell a double's failure from an adapter's.
IN_MEMORY_ENDPOINT: Final = "in-memory"

#: The operation name ``DeviceOperationUnsupported`` carries out of ``upload``. The same
#: literal the composition root uses when no uploader is bindable at all, so a shell
#: matching on it sees one spelling.
UPLOAD_OPERATION: Final = "upload"


def _raise_if(failure: DeviceError | None, /) -> None:
    """Raise the seeded whole-transport failure, when one was set.

    Parameters
    ----------
    failure
        The failure to raise, or ``None`` when the transport is healthy.

    Raises
    ------
    DeviceError
        *failure*, unchanged. Raised rather than rebuilt so a test asserts on the exact
        object it seeded, including a class the doubles would never construct themselves.
    """
    if failure is not None:
        raise failure


def _unique(pages: Sequence[DevicePageSource], /) -> tuple[DevicePageSource, ...]:
    """Drop a repeated page identifier after the first, as the page-order reader does.

    Mirrors :func:`~rmspec.device._pages.decode_page_order`, which drops a duplicate id
    rather than letting :class:`~rmspec.domain.ports.device.DocumentSourceBundle`'s
    validator refuse the whole bundle. Both real adapters build their page tuple through
    that reader, so the validator's duplicate arm is unreachable from either of them -- and
    a double that instead surfaced a ``ValidationError`` would disagree with both.

    Parameters
    ----------
    pages
        The seeded pages, in the order a device would have recorded them.

    Returns
    -------
    tuple[DevicePageSource, ...]
        The same pages with every repeat of an identifier after the first removed.
    """
    seen: set[str] = set()
    kept: list[DevicePageSource] = []
    for page in pages:
        if page.page_id not in seen:
            seen.add(page.page_id)
            kept.append(page)
    return tuple(kept)


def _payload_size(pages: Sequence[DevicePageSource], base: bytes | None, /) -> int:
    """Count the bytes a transport would have had to move for one bundle.

    Parameters
    ----------
    pages
        The document's pages. A page carrying no ink contributes nothing, which is what
        ``scene=None`` means.
    base
        The underlay, or ``None`` for a notebook.

    Returns
    -------
    int
        Total payload bytes, used as ``bytes_expected`` on a seeded truncation so the error
        names a plausible pair rather than two unrelated numbers.
    """
    inked = sum(len(page.scene) for page in pages if page.scene is not None)
    return inked + len(base if base is not None else b"")


class InMemoryDeviceCatalog:
    """A library held in three tuples, resolving identifiers by the port's own rules.

    Applies the same resolution order as
    :class:`~rmspec.device.usb.UsbCatalog` and :class:`~rmspec.device.ssh.SshCatalog`:
    documents first, then skipped entries, then a fall-through that answers
    ``DeviceDocumentNotFound`` for a folder identifier and an unknown one alike. Meant to
    be read side by side with either.

    Parameters
    ----------
    documents
        Every document the listing reports.
    folders
        Every folder the listing reports, naming the tree the documents hang from.
    skipped
        Every entry the transport saw and could not represent. A folder identifier may
        appear here as well as in *folders*: that is what the USB catalog produces for a
        folder whose children the device refused.
    fail_with
        A whole-transport failure raised from both methods instead of answering, or
        ``None``.

    Raises
    ------
    ValueError
        An identifier appears in *documents* and also in *folders* or *skipped*. Neither
        real adapter can produce that -- the USB walk keeps a ``visited`` set and the SSH
        walk yields exactly one value per store entry -- so a double that accepted it would
        make ``get_document`` answer for a state no device can be in.
    """

    def __init__(
        self,
        *,
        documents: Sequence[DeviceDocument] = (),
        folders: Sequence[DeviceFolder] = (),
        skipped: Sequence[SkippedEntry] = (),
        fail_with: DeviceError | None = None,
    ) -> None:
        placed = {document.uuid for document in documents}
        elsewhere = {folder.uuid for folder in folders} | {
            entry.uuid for entry in skipped if entry.uuid is not None
        }
        clashes = sorted(placed & elsewhere)
        if clashes:
            msg = f"{clashes} are reported both as documents and as something else"
            raise ValueError(msg)

        self.fail_with = fail_with
        """The whole-transport failure both methods raise, or ``None``."""

        self.list_calls = 0
        """How many times :meth:`list_documents` was entered, faults included."""

        self.get_calls = 0
        """How many times :meth:`get_document` was entered, faults included."""

        self.builds = 0
        """How many times the listing was actually materialised. One per instance."""

        self._documents = tuple(documents)
        self._folders = tuple(folders)
        self._skipped = tuple(skipped)
        self._listing: DeviceListing | None = None

    def list_documents(self) -> DeviceListing:
        """Return the whole library, documents and folders alike.

        Memoised, as both real catalogs are: every port is one view over a single
        ``Scope.REQUEST`` transport resource, so an instance's lifetime is one command.

        Returns
        -------
        DeviceListing
            All three tuples, always stated. The same object on every call.

        Raises
        ------
        DeviceError
            ``fail_with`` was set. A whole-transport failure, so it raises rather than
            degrading to an empty listing.
        """
        self.list_calls += 1
        _raise_if(self.fail_with)
        if self._listing is None:
            self.builds += 1
            self._listing = DeviceListing(
                documents=self._documents,
                folders=self._folders,
                skipped=self._skipped,
            )
        return self._listing

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Look up one document against the memoised listing.

        Parameters
        ----------
        doc_uuid
            The identifier to resolve.

        Returns
        -------
        DeviceDocument
            The listed document, equal to the one :meth:`list_documents` reports.

        Raises
        ------
        MalformedDeviceMetadata
            The identifier is in ``skipped``, whichever ``SkipReason`` it carried. Carries
            that entry's own detail.
        DeviceDocumentNotFound
            No document has that identifier, or it names a folder. The two share one arm
            deliberately: the error carries only the identifier, so a separate branch would
            build an indistinguishable value.
        DeviceError
            ``fail_with`` was set.
        """
        self.get_calls += 1
        listing = self.list_documents()
        for document in listing.documents:
            if document.uuid == doc_uuid:
                return document
        for entry in listing.skipped:
            if entry.uuid == doc_uuid:
                raise MalformedDeviceMetadata(
                    transport=IN_MEMORY_TRANSPORT,
                    document_uuid=doc_uuid,
                    detail=entry.detail,
                )
        raise DeviceDocumentNotFound(transport=IN_MEMORY_TRANSPORT, document_uuid=doc_uuid)


class InMemoryRawBundleSource:
    """One document's ordered pages and its underlay, served from two mappings.

    Reads ``document`` from a catalog rather than holding a second copy, exactly as both
    real bundle sources do, so ``bundle.document`` and ``catalog.get_document(uuid)``
    cannot disagree and the catalog's three coherence rules apply here for free.

    Parameters
    ----------
    catalog
        Where ``document`` comes from, and what diagnoses a missing identifier before any
        transfer is attempted.
    pages
        Document identifier mapped to its pages, in the order a device recorded them. A
        repeated page identifier is dropped after the first; see :func:`_unique`.
    bases
        Document identifier mapped to its PDF or EPUB underlay. A notebook must not appear
        here, and a document that is not a notebook must.
    truncate_at
        Bytes to report as transferred before raising ``DeviceTransferInterrupted``, or
        ``None`` for a complete transfer.
    fail_with
        A whole-transport failure raised instead of answering, or ``None``.
    """

    def __init__(
        self,
        *,
        catalog: InMemoryDeviceCatalog,
        pages: Mapping[str, Sequence[DevicePageSource]] | None = None,
        bases: Mapping[str, bytes] | None = None,
        truncate_at: int | None = None,
        fail_with: DeviceError | None = None,
    ) -> None:
        self.truncate_at = truncate_at
        """Bytes reported as transferred before a seeded interruption, or ``None``."""

        self.fail_with = fail_with
        """The whole-transport failure :meth:`load_bundle` raises, or ``None``."""

        self.load_calls = 0
        """How many times :meth:`load_bundle` was entered, faults included."""

        self._catalog = catalog
        self._pages = {} if pages is None else {key: tuple(value) for key, value in pages.items()}
        self._bases = {} if bases is None else dict(bases)

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Fetch one document's whole source, or fail without returning part of it.

        Parameters
        ----------
        doc_uuid
            The document's identifier.

        Returns
        -------
        DocumentSourceBundle
            The pages in recorded order, plus the underlay for a document that has one and
            ``None`` for a notebook.

        Raises
        ------
        DeviceDocumentNotFound
            The catalog holds no such document, or the identifier names a folder.
        MalformedDeviceMetadata
            The catalog reported the entry as unreadable.
        DeviceTransferInterrupted
            ``truncate_at`` was set. Raised after the catalog lookup and before anything is
            assembled, so no partial bundle exists to return.
        DeviceProtocolError
            The document is not a notebook and no underlay was seeded -- the transport
            contradicting its own answer, which is what the USB archive reader reports for
            an archive missing the member its own file type requires.
        DeviceError
            ``fail_with`` was set.
        """
        self.load_calls += 1
        _raise_if(self.fail_with)
        document = self._catalog.get_document(doc_uuid)
        pages = _unique(self._pages.get(doc_uuid, ()))
        seeded = self._bases.get(doc_uuid)
        if self.truncate_at is not None:
            raise DeviceTransferInterrupted(
                transport=IN_MEMORY_TRANSPORT,
                subject=doc_uuid,
                bytes_transferred=self.truncate_at,
                bytes_expected=_payload_size(pages, seeded),
            )
        is_notebook = document.file_type is DeviceFileType.NOTEBOOK
        if not is_notebook and seeded is None:
            raise DeviceProtocolError(
                transport=IN_MEMORY_TRANSPORT,
                route=IN_MEMORY_ENDPOINT,
                expected=f"a {document.file_type.value} underlay for {doc_uuid}",
                got="a source holding no underlay for it",
            )
        return DocumentSourceBundle(
            document=document,
            pages=pages,
            base=None if is_notebook else seeded,
        )


class InMemoryDocumentUploader:
    """Records what it was asked to place, and reports what a transport would know.

    Parameters
    ----------
    refresh
        What the receipt says was needed to make the document visible. Defaults to
        ``VISIBILITY_FORCED``, which is what the SSH adapter always produces;
        ``ALREADY_VISIBLE`` is reachable only through this seam.
    reject_with
        The device's own refusal message, or ``None`` to accept. When set, ``upload``
        raises ``DeviceUploadRejected`` and records nothing.
    doc_uuid
        The identifier the receipt reports. ``None`` models a transport that mints no
        identifier, which is the state ``UploadReceipt.doc_uuid`` is optional for.
    honours_parent
        Whether a non-``None`` ``parent_uuid`` can be honoured. When false, ``upload``
        raises ``DeviceOperationUnsupported`` before writing anything rather than placing
        at the library root.
    fail_with
        A whole-transport failure raised instead of accepting, or ``None``.
    """

    def __init__(
        self,
        *,
        refresh: LibraryRefresh = LibraryRefresh.VISIBILITY_FORCED,
        reject_with: str | None = None,
        doc_uuid: str | None = None,
        honours_parent: bool = True,
        fail_with: DeviceError | None = None,
    ) -> None:
        self.refresh = refresh
        """What every receipt reports as the visibility outcome."""

        self.reject_with = reject_with
        """The device's refusal message, or ``None``."""

        self.doc_uuid = doc_uuid
        """The identifier every receipt reports, or ``None``."""

        self.honours_parent = honours_parent
        """Whether a destination folder can be honoured."""

        self.fail_with = fail_with
        """The whole-transport failure :meth:`upload` raises, or ``None``."""

        self.upload_calls = 0
        """How many times :meth:`upload` was entered, faults included."""

        self.uploaded: list[UploadRequest] = []
        """Every request that was actually placed, in order. Empty after any failure,
        which is what makes "raised before anything is written" assertable."""

    def upload(self, request: UploadRequest, /) -> UploadReceipt:
        """Place one document, or refuse it without degrading what was asked for.

        Parameters
        ----------
        request
            The document to place, with its media and destination folder.

        Returns
        -------
        UploadReceipt
            Carrying the request's own ``name`` and ``media``,
            ``byte_count == len(request.data)``, and ``refresh``.

        Raises
        ------
        DeviceOperationUnsupported
            ``honours_parent`` is false and ``request.parent_uuid`` is not ``None``.
            Checked first, before every other seam, because the port documents it as
            "raised before anything is written".
        DeviceUploadRejected
            ``reject_with`` was set. Carries that message verbatim.
        DeviceError
            ``fail_with`` was set.
        """
        self.upload_calls += 1
        if not self.honours_parent and request.parent_uuid is not None:
            raise DeviceOperationUnsupported(
                transport=IN_MEMORY_TRANSPORT,
                operation=UPLOAD_OPERATION,
                supported_by=(TransportKind.SSH,),
            )
        _raise_if(self.fail_with)
        if self.reject_with is not None:
            raise DeviceUploadRejected(
                transport=IN_MEMORY_TRANSPORT,
                name=request.name,
                device_message=self.reject_with,
            )
        self.uploaded.append(request)
        return UploadReceipt(
            doc_uuid=self.doc_uuid,
            name=request.name,
            media=request.media,
            byte_count=len(request.data),
            library_refresh=self.refresh,
        )


class InMemoryDeviceFactsSource:
    """Two readings held as values, so both causes of ``None`` are seedable.

    The readings are whole :class:`~rmspec.domain.ports.device.DeviceFacts` and
    :class:`~rmspec.domain.ports.device.DeviceResources` values rather than loose fields,
    which means the port's own validators run on whatever a test seeds: an ``unsupported``
    set naming a field that carries a value, or naming a field that does not exist, is
    unconstructible here just as it is in an adapter.

    Parameters
    ----------
    facts
        The fixed facts to report. Defaults to every field ``None`` with nothing named
        ``unsupported`` -- the port's "asked and did not answer" for all three at once,
        which is a legal reading and not the same value as naming them.
    resources
        The gauge reading to report, with the same default and the same meaning.
    fail_with
        A whole-transport failure raised from both methods, or ``None``.
    """

    def __init__(
        self,
        *,
        facts: DeviceFacts | None = None,
        resources: DeviceResources | None = None,
        fail_with: DeviceError | None = None,
    ) -> None:
        self.facts = DeviceFacts() if facts is None else facts
        """The reading :meth:`read_facts` returns."""

        self.resources = DeviceResources() if resources is None else resources
        """The reading :meth:`read_resources` returns."""

        self.fail_with = fail_with
        """The whole-transport failure both methods raise, or ``None``."""

        self.facts_calls = 0
        """How many times :meth:`read_facts` was entered, faults included."""

        self.resources_calls = 0
        """How many times :meth:`read_resources` was entered, faults included."""

    def read_facts(self) -> DeviceFacts:
        """Return the seeded fixed facts.

        Returns
        -------
        DeviceFacts
            ``facts``, unchanged.

        Raises
        ------
        DeviceError
            ``fail_with`` was set. Both methods raise rather than reporting a detached
            tablet as "everything unsupported".
        """
        self.facts_calls += 1
        _raise_if(self.fail_with)
        return self.facts

    def read_resources(self) -> DeviceResources:
        """Return the seeded gauge reading.

        Returns
        -------
        DeviceResources
            ``resources``, unchanged. A fresh call rather than a cached one, because the
            port separates the volatile reading from the fixed facts precisely so a caller
            holding the latter still gets a current free-space number.

        Raises
        ------
        DeviceError
            ``fail_with`` was set.
        """
        self.resources_calls += 1
        _raise_if(self.fail_with)
        return self.resources


class FakeRemoteShell:
    """An in-memory :class:`~rmspec.device._shell.RemoteShell` with one seam per property.

    Satisfies the Protocol structurally and owns no socket. The four SSH adapters take the
    Protocol and nothing else that reaches a wire, so binding this exercises all four end to
    end -- which matters because the tablet is attached to the machine this suite runs on,
    and a test that opened a real session would *pass*.

    It speaks the Protocol's **two-vocabulary** failure contract, and that is the whole
    reason a fake here is not trivial. ``read_file`` and ``list_dir`` report a failure that
    describes *one path* as :class:`~rmspec.device._shell.PathUnreadableError`, which is not
    a domain error, while a failure that describes the *session* is a
    ``rmspec.domain.errors.DeviceError``. The two arrive from ``paramiko`` as the same
    ``OSError``, and a double that collapsed them would let a mid-walk disconnect read as a
    library of unreadable folders -- which is exactly the defect the distinction was
    introduced to fix.

    Parameters
    ----------
    outputs
        A command's standard output, keyed by exact command text. An unscripted command
        raises rather than returning ``""``, so a test that misspells the command under
        test fails loudly instead of asserting against an empty string.
    files
        The store, keyed by absolute path. A path the store does not hold raises
        ``PathUnreadableError``, which is what the real shell reports for an absent path:
        paramiko attaches an ``errno`` for exactly that SFTP status code.
    dirs
        Directory listings, keyed by absolute path. A directory the store does not hold
        raises ``PathUnreadableError`` too -- a non-zero ``ls`` proves the session is alive,
        so it is a per-path signal and not a session one.
    refuse_reads
        Paths whose read is refused although a listing named them. The same class as an
        absent path, deliberately: the real shell cannot tell "absent" from "refused"
        either, and this seam exists to express the *intent* -- the listing saw it and the
        read was denied -- which is the definition of
        :attr:`~rmspec.domain.ports.device.SkipReason.UNREADABLE`.
    short_writes
        Paths whose write lands short, so ``DeviceTransferInterrupted`` is reachable
        without a real truncated transfer. A domain error, because an uploader has no
        per-path answer to give.
    refuse_commands
        Commands that exit non-zero, so a step of a multi-step operation can be failed
        independently of the writes around it.
    fail_with
        A whole-transport failure raised from all four methods, which is how "a listing
        failure propagates rather than degrading to an empty library" is asserted.
    """

    def __init__(
        self,
        *,
        outputs: Mapping[str, str] | None = None,
        files: Mapping[str, bytes] | None = None,
        dirs: Mapping[str, Sequence[str]] | None = None,
        refuse_reads: Sequence[str] = (),
        short_writes: Sequence[str] = (),
        refuse_commands: Sequence[str] = (),
        fail_with: DeviceError | None = None,
    ) -> None:
        self.outputs: dict[str, str] = {} if outputs is None else dict(outputs)
        """Scripted standard output per command text."""

        self.files: dict[str, bytes] = {} if files is None else dict(files)
        """The store's file contents, keyed by absolute path. Writes land here."""

        self.dirs: dict[str, tuple[str, ...]] = (
            {} if dirs is None else {key: tuple(value) for key, value in dirs.items()}
        )
        """The store's directory listings, keyed by absolute path."""

        self.refuse_reads = frozenset(refuse_reads)
        """Paths whose read is refused."""

        self.short_writes = frozenset(short_writes)
        """Paths whose write lands short."""

        self.refuse_commands = frozenset(refuse_commands)
        """Commands that exit non-zero."""

        self.fail_with = fail_with
        """The whole-transport failure all four methods raise, or ``None``."""

        self.log: list[str] = []
        """Every operation in order, across all four methods. One ordered log rather than
        four counters, because "the ``.metadata`` sidecar is written last" is an ordering
        property that no per-method counter can express."""

        self.commands: list[str] = []
        """Command text, in call order."""

        self.reads: list[str] = []
        """Paths read, in call order."""

        self.listings: list[str] = []
        """Directories listed, in call order."""

        self.writes: list[tuple[str, bytes]] = []
        """Path and payload of every write that landed, in call order."""

    def run(self, command: RemoteCommand, /) -> str:
        """Answer one scripted command.

        Parameters
        ----------
        command
            The command, already assembled with every argument quoted.

        Returns
        -------
        str
            The scripted standard output, verbatim.

        Raises
        ------
        DeviceProtocolError
            The command is in ``refuse_commands``, or nothing was scripted for it.
        DeviceError
            ``fail_with`` was set.
        """
        _raise_if(self.fail_with)
        self.log.append(f"run {command.text}")
        self.commands.append(command.text)
        if command.text in self.refuse_commands:
            raise self._refused(command.text, "the command was scripted to exit non-zero")
        if command.text not in self.outputs:
            raise self._refused(command.text, "no output was scripted for this command")
        return self.outputs[command.text]

    def read_file(self, path: RemotePath, /) -> bytes:
        """Read one whole file from the store.

        Parameters
        ----------
        path
            The file to read.

        Returns
        -------
        bytes
            The stored contents. A zero-length file reads as ``b""``; deciding what that
            means is the caller's business, exactly as with the real shell.

        Raises
        ------
        PathUnreadableError
            The path is in ``refuse_reads``, or the store does not hold it. The per-path
            vocabulary, exactly as the real shell reports both cases.
        DeviceError
            ``fail_with`` was set. Describes the session rather than the path, so a caller
            with a per-entry answer must not give one.
        """
        _raise_if(self.fail_with)
        self.log.append(f"read {path.value}")
        self.reads.append(path.value)
        if path.value in self.refuse_reads:
            raise PathUnreadableError(
                path=path.value,
                detail="PermissionError: the read was scripted to be refused",
            )
        if path.value not in self.files:
            raise PathUnreadableError(
                path=path.value,
                detail="FileNotFoundError: the store holds no such file",
            )
        return self.files[path.value]

    def list_dir(self, path: RemotePath, /) -> tuple[str, ...]:
        """List one directory of the store.

        Parameters
        ----------
        path
            The directory to list.

        Returns
        -------
        tuple[str, ...]
            The scripted bare names, in the order they were given.

        Raises
        ------
        PathUnreadableError
            The store holds no such directory, which is what a non-zero ``ls`` becomes -- a
            command that ran at all proves the session is alive, so it is a per-path signal.
        DeviceError
            ``fail_with`` was set.
        """
        _raise_if(self.fail_with)
        self.log.append(f"list {path.value}")
        self.listings.append(path.value)
        if path.value not in self.dirs:
            raise PathUnreadableError(
                path=path.value,
                detail=f"exit status 1 from {IN_MEMORY_ENDPOINT}: ls: no such directory",
            )
        return self.dirs[path.value]

    def write_file(self, path: RemotePath, data: bytes, /) -> None:
        """Write one whole file into the store.

        Parameters
        ----------
        path
            Where to write.
        data
            The complete contents.

        Raises
        ------
        DeviceTransferInterrupted
            The path is in ``short_writes``. Nothing is stored, and the error carries both
            byte counts.
        DeviceError
            ``fail_with`` was set.
        """
        _raise_if(self.fail_with)
        if path.value in self.short_writes:
            self.log.append(f"short {path.value}")
            raise DeviceTransferInterrupted(
                transport=TransportKind.SSH,
                subject=path.value,
                bytes_transferred=0,
                bytes_expected=len(data),
            )
        self.log.append(f"write {path.value}")
        self.writes.append((path.value, data))
        self.files[path.value] = data

    def written(self, path: RemotePath, /) -> bytes:
        """Return the bytes the store now holds at one path.

        Parameters
        ----------
        path
            The path to look up.

        Returns
        -------
        bytes
            What was written there.

        Raises
        ------
        KeyError
            Nothing was written there. A plain ``KeyError`` and not a domain error: this is
            an assertion helper, so a miss is a defect in the test rather than a device
            failure the adapter under test should ever see.
        """
        return self.files[path.value]

    @staticmethod
    def _refused(route: str, got: str) -> DeviceProtocolError:
        """Build the error a refused command or an unlistable directory produces.

        Parameters
        ----------
        route
            What was addressed: the command text, or the ``ls`` that would have run.
        got
            Why it failed.

        Returns
        -------
        DeviceProtocolError
            Shaped like the value :func:`~rmspec.device._errors.command_failed` returns, so
            an adapter cannot tell this apart from the real thing.
        """
        return DeviceProtocolError(
            transport=TransportKind.SSH,
            route=route,
            expected="exit status 0",
            got=got,
        )
