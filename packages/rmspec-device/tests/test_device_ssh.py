"""The five SSH adapters, against a shell that is a dict and never a socket.

Six properties carry this file.

**The commands are BusyBox-safe, and their exact text is pinned.** The device runs BusyBox
1.36.1 with no ``file``, ``sqlite3`` or ``python3``, and no GNU long options -- ``head -25``
is rejected where ``sed -n 1,25p`` is not. A command is data here, so the table in the design
is asserted rather than remembered.

**``df -Pk``, and the trap that makes the ``-P`` mandatory, are both asserted.** The same
parser is run over the measured ``df -Pk`` output *and* over the wrapped ``df -k`` output, so
the reason for the flag is pinned by a test instead of surviving as a comment somebody
deletes.

**Both spellings of "no answer" are exercised.** ``ports/device.py`` draws a line between a
field a transport structurally cannot ask -- named in ``unsupported`` -- and a field it asked
for and got nothing intelligible back from, which is an unnamed ``None``. Over SSH, ``serial``
is the first and an unmatched firmware line is the second, and a test exists for each.

**Every ``SkipReason`` is produced by a real payload**, not by constructing the enum. A skip
is the port's way of saying "this entry exists and cannot be represented", and the whole
point is that the diagnosis is decided by what the store held.

**Write ordering is a correctness property, so it is asserted as one.** ``.metadata`` is what
makes an identifier a document, so it is written last; the fake records one ordered log
across commands, reads, listings and writes, and the upload tests assert that log -- including
that a failure at step 3 leaves no ``.metadata`` behind.

**An absent search index is data, and a dead session is not.** ``PathUnreadableError`` is the
seam that keeps those apart, and :class:`~rmspec.device.ssh.SshSearchIndexSource` is the one
adapter whose per-path answer is a plain ``None`` rather than a ``SkippedEntry`` or a
``DeviceProtocolError``. The containment section below therefore covers it in the shape its
port has: it must never raise the package-private error, and it must never answer ``None`` to
a failure that describes the session.
"""

from __future__ import annotations

import ast
import inspect
import json
from typing import TYPE_CHECKING

import pytest
from device_contracts import SEARCH_INDEX_IMAGE

from rmspec.device import ssh as ssh_module
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
from rmspec.device.ssh import (
    FIRMWARE_TEMPLATE,
    MAKE_DIR_TEMPLATE,
    MEMINFO_TEMPLATE,
    MODEL_TEMPLATE,
    REFRESH_TEMPLATE,
    SERIAL_FIELD,
    STORAGE_TEMPLATE,
    UNPLACEABLE_MEDIA,
    UNPLACEABLE_OPERATION,
    SshBundleSource,
    SshCatalog,
    SshFacts,
    SshSearchIndexSource,
    SshUploader,
)
from rmspec.domain.errors import (
    DeviceAuthFailed,
    DeviceDocumentNotFound,
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    DeviceUploadRejected,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.models import DocumentMetadata
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceFactsSource,
    DeviceFileType,
    DocumentUploader,
    LibraryRefresh,
    RawBundleSource,
    SearchIndexSource,
    SkipReason,
    UploadMedia,
    UploadRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rmspec.device._shell import RemoteShell

ROOT = RemotePath.root()
ENDPOINT = "10.11.99.1:22"

#: Where the search index sits: one child of the xochitl root, composed the way the adapter
#: composes it so a test cannot pass while agreeing with itself and not with the adapter.
INDEX_PATH = ROOT.child(SEARCH_INDEX_NAME)

DOC = "b8ff2c3d-0a1e-4f77-9c21-6a0e5d4b7f10"
PDF_DOC = "7e1c4a90-55b2-4d31-8f6e-0a2b3c4d5e6f"
FOLDER = "3c9f1a55-7b2e-4c6d-9a18-5f0e1d2c3b4a"
PAGE_A = "1f0a9c72-3d44-4e18-8b56-2c7d9e0a5b31"
PAGE_B = "2a1b8d63-4e55-4f29-9c67-3d8e0f1a6c42"
PAGE_C = "3b2c9e74-5f66-4a3a-8d78-4e9f102b7d53"

NOW_MS = 1_755_000_000_000
MINTED = "0d1e2f30-4152-4364-9576-8798a9bacbdc"

#: The three lines of ``/proc/meminfo`` this adapter reads, measured 2026-08-29.
MEMINFO_OUTPUT = (
    "MemTotal:        2009400 kB\nMemFree:         1224912 kB\nMemAvailable:    1251016 kB\n"
)

#: ``df -Pk`` on the xochitl root, measured 2026-08-29. Six fields on one line.
DF_PK_OUTPUT = (
    "Filesystem           1024-blocks    Used Available Capacity Mounted on\n"
    "/dev/mapper/home-encrypted-disk             48568796    112564  47929504   0% /home\n"
)

#: The same reading through plain ``df -k``: the 31-character device name overflows
#: BusyBox's 20-column field and the numbers land on the *third* line.
DF_K_WRAPPED_OUTPUT = (
    "Filesystem           1024-blocks    Used Available Capacity Mounted on\n"
    "/dev/mapper/home-encrypted-disk\n"
    "                       48568796    112564  47929504   0% /home\n"
)

FIRMWARE_COMMAND = RemoteCommand.of(FIRMWARE_TEMPLATE, RemotePath.absolute(OS_RELEASE)).text
MODEL_COMMAND = RemoteCommand.of(MODEL_TEMPLATE, RemotePath.absolute(SOC_MACHINE)).text
MEMINFO_COMMAND = RemoteCommand.of(MEMINFO_TEMPLATE, RemotePath.absolute(PROC_MEMINFO)).text
STORAGE_COMMAND = RemoteCommand.of(STORAGE_TEMPLATE, ROOT).text

HEALTHY_FACTS = {
    FIRMWARE_COMMAND: "3.27.3.0\n",
    MODEL_COMMAND: "reMarkable Ferrari\n",
    MEMINFO_COMMAND: MEMINFO_OUTPUT,
    STORAGE_COMMAND: DF_PK_OUTPUT,
}


# ─────────────────────────── the fake shell ───────────────────────────


class FakeShell:
    """An in-memory :class:`~rmspec.device._shell.RemoteShell` with one seam per property.

    ``paramiko`` ships no fake, and the tablet is attached, so this double is what keeps the
    suite from silently testing nothing. Each seam exists because it makes one guarantee
    assertable:

    ``outputs``
        A command's stdout, keyed by exact command text. An unscripted command raises rather
        than returning ``""``, so a test that misspells the command under test fails loudly.
    ``files`` / ``dirs``
        The store. A missing file or directory raises ``PathUnreadableError``, which is what
        the real shell produces for a path it was pointed at and could not open.
    ``refuse_reads``
        One path the listing named and the read is refused for -- the only way to reach
        :attr:`~rmspec.domain.ports.device.SkipReason.UNREADABLE`.
    ``short_writes``
        A write that lands short, so ``DeviceTransferInterrupted`` is reachable without a
        real truncated transfer.
    ``refuse_commands``
        One command that exits non-zero, which is how the upload's step 1 and step 5 are
        failed independently of its writes.
    ``fail_with`` / ``fail_after_reads``
        A whole-*transport* failure, optionally delayed until N reads have already
        succeeded. The delay is what makes the regression this fake exists for testable at
        all: a cable pulled part-way through a 42-read catalog walk must raise, not return a
        shrunken library.

    ``log`` records every operation in order across all four methods, because the
    ``.metadata``-last rule is an ordering property and an unordered per-method counter
    cannot express it.
    """

    def __init__(
        self,
        *,
        outputs: Mapping[str, str] | None = None,
        files: Mapping[str, bytes] | None = None,
        dirs: Mapping[str, tuple[str, ...]] | None = None,
        refuse_reads: Sequence[str] = (),
        short_writes: Sequence[str] = (),
        refuse_commands: Sequence[str] = (),
        fail_with: DeviceError | None = None,
        fail_after_reads: int = 0,
    ) -> None:
        self.outputs = {} if outputs is None else dict(outputs)
        self.files = {} if files is None else dict(files)
        self.dirs = {} if dirs is None else dict(dirs)
        self.refuse_reads = frozenset(refuse_reads)
        self.short_writes = frozenset(short_writes)
        self.refuse_commands = frozenset(refuse_commands)
        self.fail_with = fail_with
        self.fail_after_reads = fail_after_reads
        self.log: list[str] = []
        self.commands: list[str] = []
        self.reads: list[str] = []
        self.listings: list[str] = []
        self.writes: list[tuple[str, bytes]] = []

    def run(self, command: RemoteCommand, /) -> str:
        """Answer one scripted command.

        Parameters
        ----------
        command
            The command, already quoted.

        Returns
        -------
        str
            The scripted stdout.

        Raises
        ------
        DeviceError
            ``fail_with``, or a protocol error for a refused or unscripted command.
        """
        self._guard()
        self.log.append(f"run {command.text}")
        self.commands.append(command.text)
        if command.text in self.refuse_commands:
            raise self._refused(command.text, "the command was scripted to exit non-zero")
        if command.text not in self.outputs:
            raise self._refused(command.text, "no output was scripted for this command")
        return self.outputs[command.text]

    def read_file(self, path: RemotePath, /) -> bytes:
        """Read one path from the fake store.

        Parameters
        ----------
        path
            The file to read.

        Returns
        -------
        bytes
            The stored bytes.

        Raises
        ------
        PathUnreadableError
            The path is refused or absent -- the per-path vocabulary, exactly as the real
            shell reports it.
        DeviceError
            ``fail_with``, which describes the session rather than the path.
        """
        self._guard()
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
                detail="FileNotFoundError: no such file",
            )
        return self.files[path.value]

    def list_dir(self, path: RemotePath, /) -> tuple[str, ...]:
        """List one directory of the fake store.

        Parameters
        ----------
        path
            The directory to list.

        Returns
        -------
        tuple[str, ...]
            The scripted names.

        Raises
        ------
        PathUnreadableError
            The fake holds no such directory -- what a non-zero ``ls`` becomes.
        DeviceError
            ``fail_with``.
        """
        self._guard()
        self.log.append(f"list {path.value}")
        self.listings.append(path.value)
        if path.value not in self.dirs:
            raise PathUnreadableError(
                path=path.value,
                detail=f"exit status 1 from {ENDPOINT}: ls: no such directory",
            )
        return self.dirs[path.value]

    def write_file(self, path: RemotePath, data: bytes, /) -> None:
        """Record one write into the fake store.

        Parameters
        ----------
        path
            Where to write.
        data
            The payload.

        Raises
        ------
        DeviceError
            ``fail_with``, or ``DeviceTransferInterrupted`` for a path in ``short_writes``.
        """
        self._guard()
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
        """Return the bytes written to one path.

        Parameters
        ----------
        path
            The path to look up.

        Returns
        -------
        bytes
            What the adapter wrote there.
        """
        return self.files[path.value]

    def _guard(self) -> None:
        """Raise the scripted whole-transport failure, once enough reads have gone through.

        Raises
        ------
        DeviceError
            ``fail_with``, from every method, once ``fail_after_reads`` reads have already
            succeeded. Zero -- the default -- means immediately.
        """
        if self.fail_with is not None and len(self.reads) >= self.fail_after_reads:
            raise self.fail_with

    @staticmethod
    def _refused(route: str, got: str) -> DeviceProtocolError:
        """Build the protocol error a refused command or listing produces.

        Parameters
        ----------
        route
            What was addressed.
        got
            Why it failed.

        Returns
        -------
        DeviceProtocolError
            The error, shaped like the one ``command_failed`` produces.
        """
        return DeviceProtocolError(
            transport=TransportKind.SSH,
            route=route,
            expected="exit status 0",
            got=got,
        )


# ─────────────────────────── store fixtures ───────────────────────────


def store_metadata(
    *,
    name: str = "Notes",
    kind: str = "DocumentType",
    parent: str = "",
    last_modified: str = "1755000000000",
    extra: Mapping[str, object] | None = None,
) -> bytes:
    """Build a device-shaped ``.metadata`` sidecar.

    Synthesised from the measured 10-key shape rather than captured: a real file carries the
    user's document titles.

    Parameters
    ----------
    name
        ``visibleName``.
    kind
        ``type``: ``DocumentType`` or ``CollectionType``.
    parent
        ``parent``: a uuid, ``""`` for the root, or the literal ``"trash"``.
    last_modified
        ``lastModified``, a millisecond epoch in a json *string*, as the store writes it.
    extra
        Extra members, for the malformed and invalid cases.

    Returns
    -------
    bytes
        The sidecar, as UTF-8 json.
    """
    payload: dict[str, object] = {
        "createdTime": last_modified,
        "lastModified": last_modified,
        "lastOpened": "",
        "lastOpenedPage": 0,
        "parent": parent,
        "pinned": False,
        "type": kind,
        "visibleName": name,
    }
    if extra is not None:
        payload.update(extra)
    return json.dumps(payload).encode()


def store_content(
    *,
    file_type: str | None = "notebook",
    pages: Sequence[str] = (),
    template: str | None = None,
    raw: object = None,
) -> bytes:
    """Build a device-shaped ``.content`` sidecar with a ``cPages`` page list.

    Parameters
    ----------
    file_type
        ``fileType``, or ``None`` to omit the key -- which is the "the sidecar did not say"
        case.
    pages
        Page identifiers, in the order the device recorded.
    template
        A template name to record on every page, or ``None`` for none.
    raw
        Replaces ``cPages`` entirely, for the payloads whose page order will not decode.

    Returns
    -------
    bytes
        The sidecar, as UTF-8 json.
    """
    envelope = None if template is None else {"timestamp": "1:2", "value": template}
    listed = [
        {"id": page_id, **({} if envelope is None else {"template": envelope})}
        for page_id in pages
    ]
    payload: dict[str, object] = {
        "cPages": {"pages": listed} if raw is None else raw,
        "formatVersion": 2,
        "orientation": "portrait",
        "pageCount": len(pages),
        "tags": [],
        "extraMetadata": {},
    }
    if file_type is not None:
        payload["fileType"] = file_type
    return json.dumps(payload).encode()


def shell_for(
    root_files: Mapping[str, bytes],
    *,
    page_dirs: Mapping[str, Mapping[str, bytes]] | None = None,
    listing_only: Sequence[str] = (),
    outputs: Mapping[str, str] | None = None,
    refuse_reads: Sequence[str] = (),
    short_writes: Sequence[str] = (),
    refuse_commands: Sequence[str] = (),
    fail_with: DeviceError | None = None,
) -> FakeShell:
    """Build a fake shell over a described xochitl store.

    Parameters
    ----------
    root_files
        Root-level filenames mapped to their bytes.
    page_dirs
        Document uuid mapped to the scene filenames its directory holds.
    listing_only
        Names the root listing reports but which no read can reach -- the store entries
        whose stem is not usable as one path component.
    outputs
        Scripted command output.
    refuse_reads
        Paths whose read is refused.
    short_writes
        Paths whose write lands short.
    refuse_commands
        Commands that exit non-zero.
    fail_with
        A whole-transport failure.

    Returns
    -------
    FakeShell
        The double, with its file and directory maps already built.
    """
    dirs: dict[str, tuple[str, ...]] = {}
    files: dict[str, bytes] = {ROOT.child(name).value: data for name, data in root_files.items()}
    for doc_uuid, scenes in (page_dirs or {}).items():
        page_dir = ROOT.child(doc_uuid)
        dirs[page_dir.value] = tuple(scenes)
        for scene_name, data in scenes.items():
            files[page_dir.child(scene_name).value] = data
    dirs[ROOT.value] = (*root_files, *(page_dirs or {}), *listing_only)
    return FakeShell(
        outputs=outputs,
        files=files,
        dirs=dirs,
        refuse_reads=refuse_reads,
        short_writes=short_writes,
        refuse_commands=refuse_commands,
        fail_with=fail_with,
    )


def notebook_store(*, pages: Sequence[str] = (PAGE_A, PAGE_B)) -> FakeShell:
    """Build a store holding one notebook whose pages all carry ink.

    Parameters
    ----------
    pages
        The page identifiers to record and to write artifacts for.

    Returns
    -------
    FakeShell
        The double.
    """
    return shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=pages, template="P Grid small"),
        },
        page_dirs={DOC: {f"{page_id}{SCENE_SUFFIX}": b"scene" for page_id in pages}},
    )


def many_documents(
    count: int,
    *,
    fail_with: DeviceError | None = None,
    fail_after_reads: int = 0,
) -> FakeShell:
    """Build a store of *count* notebooks, so a walk performs several reads in sequence.

    One document is not enough to express a *mid*-walk failure: the reference store has 42
    entities and the property under test is what happens between the first read and the last.

    Parameters
    ----------
    count
        How many documents to place, named ``doc-1`` upwards so a failure can name one.
    fail_with
        A whole-transport failure for the shell to raise.
    fail_after_reads
        How many reads succeed first.

    Returns
    -------
    FakeShell
        The double.
    """
    root_files: dict[str, bytes] = {}
    for index in range(1, count + 1):
        root_files[f"doc-{index}{METADATA_SUFFIX}"] = store_metadata(name=f"Notes {index}")
        root_files[f"doc-{index}{CONTENT_SUFFIX}"] = store_content(pages=[PAGE_A])
    shell = shell_for(root_files)
    shell.fail_with = fail_with
    shell.fail_after_reads = fail_after_reads
    return shell


def catalog(shell: FakeShell) -> SshCatalog:
    """Build a catalog over a fake shell.

    Parameters
    ----------
    shell
        The double.

    Returns
    -------
    SshCatalog
        The adapter under test.
    """
    return SshCatalog(shell=shell, root=ROOT)


def bundles(shell: FakeShell) -> SshBundleSource:
    """Build a bundle source and the catalog it reads documents from.

    Parameters
    ----------
    shell
        The double.

    Returns
    -------
    SshBundleSource
        The adapter under test.
    """
    return SshBundleSource(shell=shell, root=ROOT, catalog=catalog(shell))


def uploader(shell: FakeShell) -> SshUploader:
    """Build an uploader with both callables pinned, so the payload is deterministic.

    Parameters
    ----------
    shell
        The double.

    Returns
    -------
    SshUploader
        The adapter under test.
    """
    return SshUploader(shell=shell, root=ROOT, now_ms=lambda: NOW_MS, new_uuid=lambda: MINTED)


def upload_shell(**seams: object) -> FakeShell:
    """Build a shell that can accept an upload: an empty root and the two commands.

    Parameters
    ----------
    **seams
        Forwarded to :class:`FakeShell` -- ``short_writes``, ``refuse_commands``,
        ``fail_with``.

    Returns
    -------
    FakeShell
        The double.
    """
    mkdir = RemoteCommand.of(MAKE_DIR_TEMPLATE, ROOT.child(MINTED)).text
    refresh = RemoteCommand.of(REFRESH_TEMPLATE).text
    shell = shell_for({}, outputs={mkdir: "", refresh: ""})
    for name, value in seams.items():
        setattr(shell, name, frozenset(value) if isinstance(value, tuple) else value)
    return shell


# ─────────────────────────── the ports are satisfied ───────────────────────────


def test_the_five_adapters_satisfy_the_five_device_ports():
    """``ty`` checks these annotations; the assertions keep the bindings from being dead."""
    shell = notebook_store()
    listing: DeviceCatalog = catalog(shell)
    source: RawBundleSource = bundles(shell)
    writer: DocumentUploader = uploader(shell)
    facts: DeviceFactsSource = SshFacts(shell=shell)
    index: SearchIndexSource = SshSearchIndexSource(shell)

    assert callable(listing.list_documents)
    assert callable(source.load_bundle)
    assert callable(writer.upload)
    assert callable(facts.read_facts)
    assert callable(index.read_index)


def test_the_fake_shell_satisfies_the_remote_shell_protocol():
    shell: RemoteShell = FakeShell()

    for name in ("run", "read_file", "list_dir", "write_file"):
        assert callable(getattr(shell, name))


# ────────────── the package-private error never leaves the public surface ──────────────


def test_this_module_never_raises_the_package_private_error():
    """Asserted by construction: every mention of it in ``ssh.py`` must be an ``except``.

    ``PathUnreadableError`` is not a ``DeviceError``, so letting one out of a port method
    would break the contract ``ports/device.py`` states for every implementation -- and it
    would do so in a way no type checker catches, because a port is a Protocol and Python has
    no checked exceptions.
    """
    tree = ast.parse(inspect.getsource(ssh_module))
    raised: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(target, ast.Name):
            raised.add(target.id)

    assert raised, "found no raise statements, so this assertion would be vacuous"
    assert PathUnreadableError.__name__ not in raised


@pytest.mark.parametrize(
    "drive",
    [
        pytest.param(lambda shell: catalog(shell).list_documents(), id="list_documents"),
        pytest.param(lambda shell: catalog(shell).get_document(DOC), id="get_document"),
        pytest.param(lambda shell: bundles(shell).load_bundle(DOC), id="load_bundle"),
    ],
)
def test_no_port_method_lets_the_package_private_error_escape(
    drive: Callable[[FakeShell], object],
):
    # Behavioural half of the containment claim: a store where every path is unreadable makes
    # each entry point reach its own conversion site.
    shell = FakeShell()

    with pytest.raises(DeviceError) as raised:
        drive(shell)

    assert not isinstance(raised.value, PathUnreadableError)


def test_a_bundle_over_an_entirely_unreadable_store_still_reports_a_domain_error():
    shell = notebook_store()
    shell.refuse_reads = frozenset(shell.files)

    with pytest.raises(DeviceError) as raised:
        bundles(shell).load_bundle(DOC)

    assert not isinstance(raised.value, PathUnreadableError)


def test_the_index_source_converts_the_package_private_error_into_the_ports_own_absence():
    """The fifth adapter's conversion site, whose per-path answer is ``None``.

    It cannot join the parametrized set above, because that set asserts a *raise*: this port's
    return type spells "no such file" as a value, so the containment claim here is that
    nothing escapes at all -- neither the package-private error nor a domain one -- for a store
    that holds no index.
    """
    assert SshSearchIndexSource(FakeShell()).read_index() is None


# ─────────────────────────── the command table ───────────────────────────


def test_every_command_is_busybox_safe_and_spelled_as_the_design_requires():
    # BusyBox 1.36.1: no GNU long options, and `head -N` is not accepted.
    assert MEMINFO_COMMAND == "sed -n 1,3p /proc/meminfo"
    assert STORAGE_COMMAND == "df -Pk /home/root/.local/share/remarkable/xochitl"
    assert FIRMWARE_COMMAND == "sed -n 's/^IMG_VERSION=\"\\(.*\\)\"$/\\1/p' /etc/os-release"
    assert MODEL_COMMAND == "cat /sys/devices/soc0/machine"
    assert RemoteCommand.of(REFRESH_TEMPLATE).text == "systemctl restart xochitl"
    assert RemoteCommand.of(MAKE_DIR_TEMPLATE, ROOT).text == (
        "mkdir -p /home/root/.local/share/remarkable/xochitl"
    )


def test_the_storage_command_is_the_posix_form_not_the_wrapping_one():
    assert STORAGE_COMMAND.startswith("df -Pk ")
    assert not STORAGE_COMMAND.startswith("df -k ")


@pytest.mark.parametrize("command", [MEMINFO_COMMAND, STORAGE_COMMAND, FIRMWARE_COMMAND])
def test_no_command_uses_a_utility_or_flag_the_device_lacks(command: str):
    for absent in ("head -", "tail -", "--", "sqlite3", "python3", "file "):
        assert absent not in command


# ─────────────────────────── facts ───────────────────────────


def test_the_facts_are_the_ones_the_firmware_reports():
    facts = SshFacts(shell=FakeShell(outputs=HEALTHY_FACTS)).read_facts()

    assert facts.firmware == "3.27.3.0"
    assert facts.model == "reMarkable Ferrari"


def test_the_serial_is_named_unsupported_rather_than_answered_with_the_soc_id():
    # /sys/devices/soc0/serial_number exists and holds 16 characters, but that is the SoC
    # unique id, not the RM02A... serial the tablet UI shows. Reporting it under this field
    # name would be a different fact wearing the right name.
    shell = FakeShell(outputs=HEALTHY_FACTS)

    facts = SshFacts(shell=shell).read_facts()

    assert facts.serial is None
    assert facts.unsupported == frozenset({SERIAL_FIELD})
    assert not any("serial" in command for command in shell.commands)


def test_an_unmatched_firmware_line_is_an_unnamed_none_not_an_unsupported_field():
    """The second cause of ``None``: asked, and did not answer intelligibly."""
    facts = SshFacts(shell=FakeShell(outputs={**HEALTHY_FACTS, FIRMWARE_COMMAND: ""})).read_facts()

    assert facts.firmware is None
    assert "firmware" not in facts.unsupported


def test_a_model_command_that_answers_with_blank_lines_is_an_unnamed_none():
    outputs = {**HEALTHY_FACTS, MODEL_COMMAND: "\n \n"}

    facts = SshFacts(shell=FakeShell(outputs=outputs)).read_facts()

    assert facts.model is None
    assert "model" not in facts.unsupported


def test_the_two_encodings_of_none_are_distinguishable_on_one_reading():
    facts = SshFacts(shell=FakeShell(outputs={**HEALTHY_FACTS, FIRMWARE_COMMAND: ""})).read_facts()

    assert (facts.firmware, facts.serial) == (None, None)
    assert facts.unsupported == frozenset({SERIAL_FIELD})


def test_the_memory_gauges_are_read_from_meminfo_in_kibibytes():
    resources = SshFacts(shell=FakeShell(outputs=HEALTHY_FACTS)).read_resources()

    assert resources.total_memory_bytes == 2009400 * 1024
    assert resources.available_memory_bytes == 1251016 * 1024


def test_the_storage_gauges_are_read_from_the_df_pk_data_line():
    resources = SshFacts(shell=FakeShell(outputs=HEALTHY_FACTS)).read_resources()

    assert resources.total_storage_bytes == 48568796 * 1024
    assert resources.available_storage_bytes == 47929504 * 1024


def test_the_total_is_not_derived_from_used_plus_available():
    # Reserved blocks make them disagree: 112564 + 47929504 < 48568796.
    resources = SshFacts(shell=FakeShell(outputs=HEALTHY_FACTS)).read_resources()

    assert resources.total_storage_bytes != (112564 + 47929504) * 1024


def test_the_wrapped_df_k_shape_reports_no_answer_rather_than_a_wrong_number():
    """The reason the command carries ``-P``, pinned as a test rather than as a comment."""
    outputs = {**HEALTHY_FACTS, STORAGE_COMMAND: DF_K_WRAPPED_OUTPUT}

    resources = SshFacts(shell=FakeShell(outputs=outputs)).read_resources()

    assert resources.total_storage_bytes is None
    assert resources.available_storage_bytes is None
    assert resources.unsupported == frozenset()


def test_nothing_is_structurally_unsupported_on_the_resource_reading():
    resources = SshFacts(shell=FakeShell(outputs=HEALTHY_FACTS)).read_resources()

    assert resources.unsupported == frozenset()


@pytest.mark.parametrize(
    ("meminfo", "total", "available"),
    [
        pytest.param("MemTotal:  100 kB\n", 100 * 1024, None, id="no-available-line"),
        pytest.param("MemAvailable:  50 kB\n", None, 50 * 1024, id="no-total-line"),
        pytest.param("", None, None, id="empty"),
        pytest.param("\n\n", None, None, id="blank-lines"),
        pytest.param("MemTotal:\n", None, None, id="label-with-no-value"),
        pytest.param("MemTotal:  lots kB\n", None, None, id="value-not-a-number"),
        pytest.param("MemTotal:  -1 kB\n", None, None, id="negative-value"),
        pytest.param("MemTotal: 1 kB\nMemAvailable: 2 kB\n", None, None, id="impossible-pair"),
    ],
)
def test_an_unreadable_memory_line_is_an_unnamed_none(
    meminfo: str,
    total: int | None,
    available: int | None,
):
    outputs = {**HEALTHY_FACTS, MEMINFO_COMMAND: meminfo}

    resources = SshFacts(shell=FakeShell(outputs=outputs)).read_resources()

    assert resources.total_memory_bytes == total
    assert resources.available_memory_bytes == available


@pytest.mark.parametrize(
    "storage",
    [
        pytest.param("Filesystem 1024-blocks\n", id="header-only"),
        pytest.param("", id="empty"),
        pytest.param("Filesystem\n/dev/one\n", id="too-few-fields"),
        pytest.param("Filesystem\n/dev/one x y z\n", id="fields-not-numbers"),
        pytest.param("Filesystem\n/dev/one 10 1 20 0% /home\n", id="impossible-pair"),
    ],
)
def test_an_unreadable_df_line_is_an_unnamed_none(storage: str):
    outputs = {**HEALTHY_FACTS, STORAGE_COMMAND: storage}

    resources = SshFacts(shell=FakeShell(outputs=outputs)).read_resources()

    assert resources.total_storage_bytes is None
    assert resources.available_storage_bytes is None


def test_a_whole_transport_failure_from_a_facts_command_propagates():
    failure = DeviceUnreachable(transport=TransportKind.SSH, endpoint=ENDPOINT, detail="cable")
    facts = SshFacts(shell=FakeShell(outputs=HEALTHY_FACTS, fail_with=failure))

    with pytest.raises(DeviceUnreachable):
        facts.read_facts()
    with pytest.raises(DeviceUnreachable):
        facts.read_resources()


# ─────────────────────────── catalog ───────────────────────────


def test_the_metadata_sidecar_is_what_makes_an_entry_a_document():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(name="Notes"),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A]),
            f"{FOLDER}{METADATA_SUFFIX}": store_metadata(name="Books", kind="CollectionType"),
            f"{PDF_DOC}{CONTENT_SUFFIX}": store_content(file_type="pdf"),
        }
    )

    listing = catalog(shell).list_documents()

    assert [document.uuid for document in listing.documents] == [DOC]
    assert [folder.uuid for folder in listing.folders] == [FOLDER]
    assert listing.skipped == ()


def test_a_document_carries_the_facts_the_sidecars_recorded():
    shell = shell_for(
        {
            f"{PDF_DOC}{METADATA_SUFFIX}": store_metadata(name="Paper", parent=FOLDER),
            f"{PDF_DOC}{CONTENT_SUFFIX}": store_content(file_type="pdf", pages=[PAGE_A, PAGE_B]),
        }
    )

    document = catalog(shell).list_documents().documents[0]

    assert document.name == "Paper"
    assert document.file_type is DeviceFileType.PDF
    assert document.parent_uuid == FOLDER
    assert document.page_count == 2
    assert document.trashed is False
    assert document.last_modified is not None
    assert document.last_modified.tzinfo is not None


def test_the_root_is_reported_as_no_parent_rather_than_an_empty_string():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(parent=""),
            f"{DOC}{CONTENT_SUFFIX}": store_content(),
        }
    )

    assert catalog(shell).list_documents().documents[0].parent_uuid is None


def test_the_trash_sentinel_in_parent_becomes_a_trashed_flag_and_no_parent():
    # Measured censuses: parent = 32 uuid / 9 empty / 1 "trash"; deleted absent on 28 of 42,
    # false on 14, never true -- so only the parent sentinel is ever exercised. A trashed
    # entry's original parent is not recoverable, because the field's value *is* "trash".
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(parent="trash"),
            f"{DOC}{CONTENT_SUFFIX}": store_content(),
        }
    )

    document = catalog(shell).list_documents().documents[0]

    assert document.trashed is True
    assert document.parent_uuid is None


def test_a_trashed_folder_is_reported_as_trashed_too():
    shell = shell_for(
        {f"{FOLDER}{METADATA_SUFFIX}": store_metadata(kind="CollectionType", parent="trash")}
    )

    folder = catalog(shell).list_documents().folders[0]

    assert (folder.trashed, folder.parent_uuid) == (True, None)


def test_the_page_count_is_the_length_of_what_the_content_sidecar_recorded():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A, PAGE_B, PAGE_C]),
        }
    )

    assert catalog(shell).list_documents().documents[0].page_count == 3


def test_a_page_order_that_will_not_decode_costs_the_count_and_not_the_entry():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(raw={"pages": "not a list"}),
        }
    )

    document = catalog(shell).list_documents().documents[0]

    assert document.page_count is None
    assert document.file_type is DeviceFileType.NOTEBOOK


def test_one_listing_decides_presence_so_a_folder_costs_no_failed_read():
    shell = shell_for({f"{FOLDER}{METADATA_SUFFIX}": store_metadata(kind="CollectionType")})

    catalog(shell).list_documents()

    assert shell.reads == [ROOT.child(f"{FOLDER}{METADATA_SUFFIX}").value]


def test_the_content_sidecar_is_read_only_when_the_listing_named_it():
    shell = notebook_store()

    catalog(shell).list_documents()

    assert shell.reads == [
        ROOT.child(f"{DOC}{METADATA_SUFFIX}").value,
        ROOT.child(f"{DOC}{CONTENT_SUFFIX}").value,
    ]


def test_names_that_are_not_metadata_sidecars_are_not_entries():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(),
            f"{DOC}.pagedata": b"Blank\n",
            f"{DOC}.local": b"{}",
            f"{PDF_DOC}.pdf": b"%PDF-1.7\n",
        },
        page_dirs={DOC: {}},
    )

    listing = catalog(shell).list_documents()

    assert len(listing.documents) == 1
    assert listing.skipped == ()


def test_the_listing_is_built_once_per_instance():
    shell = notebook_store()
    instance = catalog(shell)

    first = instance.list_documents()
    second = instance.list_documents()

    assert first is second
    assert shell.listings == [ROOT.value]


def test_a_listing_failure_propagates_rather_than_reporting_an_empty_library():
    failure = DeviceUnreachable(transport=TransportKind.SSH, endpoint=ENDPOINT, detail="cable")
    shell = FakeShell(fail_with=failure)

    with pytest.raises(DeviceUnreachable):
        catalog(shell).list_documents()


def test_a_root_that_cannot_be_listed_is_a_protocol_error():
    with pytest.raises(DeviceProtocolError) as raised:
        catalog(FakeShell()).list_documents()

    assert raised.value.route == ROOT.value
    assert raised.value.expected == "a readable xochitl root"


def test_a_disconnect_part_way_through_the_walk_raises_rather_than_reporting_skips():
    """The regression this whole split exists to prevent.

    ``ports/device.py``: *per-entry failure is data; whole-transport failure raises*. The walk
    over the reference store is 42 sequential reads, so a cable pulled **during** it is the
    common case, not the edge -- and while a refused read and a dead session were the same
    typed error, the catalog had to convert both into ``SkippedEntry``. That returned a
    shrunken library with a success exit status: the user is told some notes are unreadable
    when in fact nobody looked at them.
    """
    failure = DeviceUnreachable(
        transport=TransportKind.SSH,
        endpoint=ENDPOINT,
        detail="cable pulled mid-walk",
    )
    shell = many_documents(4, fail_with=failure, fail_after_reads=3)

    with pytest.raises(DeviceUnreachable) as raised:
        catalog(shell).list_documents()

    assert raised.value.detail.endswith("cable pulled mid-walk")
    # Reads did happen before the failure, which is what makes this a *mid*-walk disconnect
    # and not the already-covered case of a tablet that was gone before the listing.
    assert len(shell.reads) == 3


def test_one_refused_metadata_yields_one_unreadable_entry_and_the_rest_of_the_listing():
    # The other side of the same split: a per-path refusal *is* a fact about that entry, so
    # the walk records it and carries on.
    shell = many_documents(4)
    shell.refuse_reads = frozenset({ROOT.child(f"doc-2{METADATA_SUFFIX}").value})

    listing = catalog(shell).list_documents()

    assert [entry.uuid for entry in listing.skipped] == ["doc-2"]
    assert listing.skipped[0].reason is SkipReason.UNREADABLE
    assert [document.uuid for document in listing.documents] == ["doc-1", "doc-3", "doc-4"]


# ─────────────────────────── every SkipReason ───────────────────────────


def test_metadata_that_is_not_json_is_malformed():
    shell = shell_for({f"{DOC}{METADATA_SUFFIX}": b"not json at all"})

    skipped = catalog(shell).list_documents().skipped

    assert [(entry.uuid, entry.reason) for entry in skipped] == [
        (DOC, SkipReason.MALFORMED_METADATA)
    ]
    assert skipped[0].detail


def test_metadata_of_the_wrong_json_type_is_malformed():
    shell = shell_for({f"{DOC}{METADATA_SUFFIX}": b'["a list"]'})

    assert catalog(shell).list_documents().skipped[0].reason is SkipReason.MALFORMED_METADATA


def test_a_field_the_domain_refuses_is_a_validation_failure():
    # lastOpenedPage is ge=0, so a negative one is a pydantic ValidationError -- which is a
    # ValueError, hence the ordering of the two except clauses in the adapter.
    shell = shell_for({f"{DOC}{METADATA_SUFFIX}": store_metadata(extra={"lastOpenedPage": -1})})

    skipped = catalog(shell).list_documents().skipped[0]

    assert skipped.reason is SkipReason.VALIDATION_FAILED
    assert "last_opened_page" in skipped.detail


def test_a_document_with_no_content_sidecar_is_skipped_rather_than_called_a_notebook():
    # A pdf silently reported as a notebook is an export with no background and no defect
    # recorded anywhere, which is why the source is never defaulted.
    shell = shell_for({f"{DOC}{METADATA_SUFFIX}": store_metadata()})

    skipped = catalog(shell).list_documents().skipped[0]

    assert (skipped.uuid, skipped.reason) == (DOC, SkipReason.VALIDATION_FAILED)
    assert "file type" in skipped.detail


def test_a_content_sidecar_that_names_no_file_type_is_skipped():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(file_type=None),
        }
    )

    assert catalog(shell).list_documents().skipped[0].reason is SkipReason.VALIDATION_FAILED


def test_a_file_type_outside_the_domains_closed_set_is_a_validation_failure():
    # The same diagnosis the USB sibling reports for the same payload: the entry decoded and
    # describes nothing this domain can represent. Never coerced to a member -- a pdf
    # reported as a notebook would be pulled without its underlay.
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(file_type="docx"),
        }
    )

    skipped = catalog(shell).list_documents().skipped[0]

    assert skipped.reason is SkipReason.VALIDATION_FAILED
    assert "docx" in skipped.detail


def test_a_content_sidecar_that_is_not_json_costs_the_entry_but_not_the_walk():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": b"not json",
            f"{PDF_DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{PDF_DOC}{CONTENT_SUFFIX}": store_content(file_type="pdf"),
        }
    )

    listing = catalog(shell).list_documents()

    assert [entry.uuid for entry in listing.skipped] == [DOC]
    assert [document.uuid for document in listing.documents] == [PDF_DOC]


def test_a_sidecar_the_listing_named_and_the_read_refused_is_unreadable():
    shell = notebook_store()
    shell.refuse_reads = frozenset({ROOT.child(f"{DOC}{CONTENT_SUFFIX}").value})

    skipped = catalog(shell).list_documents().skipped[0]

    assert (skipped.uuid, skipped.reason) == (DOC, SkipReason.UNREADABLE)


def test_a_metadata_read_that_is_refused_is_unreadable():
    shell = notebook_store()
    shell.refuse_reads = frozenset({ROOT.child(f"{DOC}{METADATA_SUFFIX}").value})

    assert catalog(shell).list_documents().skipped[0].reason is SkipReason.UNREADABLE


def test_a_store_entry_whose_stem_is_not_one_path_component_is_skipped():
    shell = shell_for({}, listing_only=(f"-rf{METADATA_SUFFIX}",))

    skipped = catalog(shell).list_documents().skipped[0]

    assert (skipped.uuid, skipped.reason) == ("-rf", SkipReason.VALIDATION_FAILED)


def test_a_bare_metadata_suffix_recovers_no_identifier():
    shell = shell_for({}, listing_only=(METADATA_SUFFIX,))

    skipped = catalog(shell).list_documents().skipped[0]

    assert skipped.uuid is None
    assert skipped.reason is SkipReason.VALIDATION_FAILED


# ─────────────────────────── get_document coherence ───────────────────────────


def test_a_listed_document_resolves_to_the_value_the_listing_reported():
    shell = notebook_store()
    instance = catalog(shell)

    listed = instance.list_documents().documents[0]

    assert instance.get_document(DOC) == listed


def test_a_folder_identifier_is_not_a_document():
    shell = shell_for({f"{FOLDER}{METADATA_SUFFIX}": store_metadata(kind="CollectionType")})

    with pytest.raises(DeviceDocumentNotFound) as raised:
        catalog(shell).get_document(FOLDER)

    assert raised.value.document_uuid == FOLDER


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        pytest.param(b"not json", SkipReason.MALFORMED_METADATA, id="malformed"),
        pytest.param(
            store_metadata(extra={"lastOpenedPage": -1}),
            SkipReason.VALIDATION_FAILED,
            id="invalid",
        ),
    ],
)
def test_a_skipped_identifier_raises_malformed_whichever_reason_it_carried(
    payload: bytes,
    reason: SkipReason,
):
    shell = shell_for({f"{DOC}{METADATA_SUFFIX}": payload})
    instance = catalog(shell)
    detail = instance.list_documents().skipped[0].detail
    assert instance.list_documents().skipped[0].reason is reason

    with pytest.raises(MalformedDeviceMetadata) as raised:
        instance.get_document(DOC)

    assert raised.value.document_uuid == DOC
    assert raised.value.detail == detail


def test_an_unreadable_identifier_also_raises_malformed():
    shell = notebook_store()
    shell.refuse_reads = frozenset({ROOT.child(f"{DOC}{METADATA_SUFFIX}").value})

    with pytest.raises(MalformedDeviceMetadata):
        catalog(shell).get_document(DOC)


def test_an_identifier_the_store_does_not_hold_is_not_found():
    with pytest.raises(DeviceDocumentNotFound):
        catalog(notebook_store()).get_document("00000000-0000-4000-8000-000000000000")


def test_the_skip_list_is_searched_past_entries_that_do_not_match():
    # Two skips, and the second is the one asked for -- so a lookup that stopped at the
    # first would report the wrong entry's detail, or none at all.
    shell = shell_for(
        {
            f"{PDF_DOC}{METADATA_SUFFIX}": b"not json",
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
        }
    )
    instance = catalog(shell)
    assert len(instance.list_documents().skipped) == 2

    with pytest.raises(MalformedDeviceMetadata) as raised:
        instance.get_document(DOC)

    assert raised.value.document_uuid == DOC
    assert "file type" in raised.value.detail
    with pytest.raises(DeviceDocumentNotFound):
        instance.get_document("00000000-0000-4000-8000-000000000000")


# ─────────────────────────── bundles ───────────────────────────


def test_a_notebook_bundle_carries_its_pages_in_order_and_no_underlay():
    shell = notebook_store()

    bundle = bundles(shell).load_bundle(DOC)

    assert [page.page_id for page in bundle.pages] == [PAGE_A, PAGE_B]
    assert [page.scene for page in bundle.pages] == [b"scene", b"scene"]
    assert [page.template_name for page in bundle.pages] == ["P Grid small", "P Grid small"]
    assert bundle.base is None


def test_a_pdf_bundle_carries_the_underlay_it_annotates():
    shell = shell_for(
        {
            f"{PDF_DOC}{METADATA_SUFFIX}": store_metadata(name="Paper"),
            f"{PDF_DOC}{CONTENT_SUFFIX}": store_content(file_type="pdf", pages=[PAGE_A]),
            f"{PDF_DOC}.pdf": b"%PDF-1.7\n",
        },
        page_dirs={PDF_DOC: {f"{PAGE_A}{SCENE_SUFFIX}": b"ink"}},
    )

    bundle = bundles(shell).load_bundle(PDF_DOC)

    assert bundle.base == b"%PDF-1.7\n"
    assert bundle.document.file_type is DeviceFileType.PDF


def test_the_bundles_document_is_the_catalogs_document():
    shell = notebook_store()
    source = bundles(shell)

    assert source.load_bundle(DOC).document == catalog(shell).get_document(DOC)


def test_a_page_the_directory_does_not_hold_carries_no_ink_and_costs_no_read():
    # The routine state of an annotated PDF: one reference document has 3 annotated pages
    # against 432 recorded ones, so a read per absent page would be 429 failures.
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A, PAGE_B]),
        },
        page_dirs={DOC: {f"{PAGE_B}{SCENE_SUFFIX}": b"ink"}},
    )

    bundle = bundles(shell).load_bundle(DOC)

    assert [page.scene for page in bundle.pages] == [None, b"ink"]
    assert ROOT.child(DOC).child(f"{PAGE_A}{SCENE_SUFFIX}").value not in shell.reads


def test_a_zero_byte_artifact_is_no_ink_rather_than_empty_bytes():
    # 86 of the 194 real .rm files are exactly zero bytes, and the port spells "carries no
    # ink" as None.
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A]),
        },
        page_dirs={DOC: {f"{PAGE_A}{SCENE_SUFFIX}": b""}},
    )

    assert bundles(shell).load_bundle(DOC).pages[0].scene is None


def test_an_artifact_absent_from_the_page_order_is_an_orphan_layer_and_is_dropped():
    # Measured: 16 .rm files for 10 pages. Iterating the directory would render ghosts.
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A]),
        },
        page_dirs={
            DOC: {f"{PAGE_A}{SCENE_SUFFIX}": b"ink", f"{PAGE_C}{SCENE_SUFFIX}": b"orphan"},
        },
    )

    bundle = bundles(shell).load_bundle(DOC)

    assert [page.page_id for page in bundle.pages] == [PAGE_A]


def test_a_document_with_no_recorded_pages_costs_no_directory_listing():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[]),
        }
    )

    bundle = bundles(shell).load_bundle(DOC)

    assert bundle.pages == ()
    assert shell.listings == [ROOT.value]


def test_a_content_sidecar_that_will_not_decode_is_malformed_metadata():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(raw={"pages": 5}),
        }
    )

    with pytest.raises(MalformedDeviceMetadata) as raised:
        bundles(shell).load_bundle(DOC)

    assert raised.value.document_uuid == DOC


def test_a_recorded_page_id_that_cannot_be_addressed_is_malformed_metadata():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=["-rf"]),
        },
        page_dirs={DOC: {}},
    )
    shell.dirs[ROOT.child(DOC).value] = (f"-rf{SCENE_SUFFIX}",)

    with pytest.raises(MalformedDeviceMetadata, match="cannot be addressed"):
        bundles(shell).load_bundle(DOC)


def test_a_non_notebook_whose_underlay_is_missing_is_a_protocol_error_not_an_unreachable_one():
    # The document's own .content says it annotates a pdf and the store holds none: the
    # device has contradicted its own answer. Reporting that as DeviceUnreachable -- which is
    # what folding every errno into the transport arm produced -- sends the user to check a
    # cable that is fine.
    shell = shell_for(
        {
            f"{PDF_DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{PDF_DOC}{CONTENT_SUFFIX}": store_content(file_type="pdf", pages=[]),
        }
    )

    with pytest.raises(DeviceProtocolError) as raised:
        bundles(shell).load_bundle(PDF_DOC)

    assert raised.value.route == ROOT.child(PDF_DOC).with_suffix(".pdf").value
    assert "pdf underlay" in raised.value.expected
    assert not isinstance(raised.value, DeviceUnreachable)


def test_a_content_sidecar_the_catalog_read_and_the_bundle_cannot_is_a_protocol_error():
    shell = notebook_store()
    catalogue = catalog(shell)
    catalogue.list_documents()
    shell.refuse_reads = frozenset({ROOT.child(f"{DOC}{CONTENT_SUFFIX}").value})

    with pytest.raises(DeviceProtocolError) as raised:
        SshBundleSource(shell=shell, root=ROOT, catalog=catalogue).load_bundle(DOC)

    assert raised.value.route == ROOT.child(f"{DOC}{CONTENT_SUFFIX}").value
    assert ".content sidecar" in raised.value.expected


def test_a_page_directory_that_cannot_be_listed_is_a_protocol_error():
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A]),
        }
    )

    with pytest.raises(DeviceProtocolError) as raised:
        bundles(shell).load_bundle(DOC)

    assert raised.value.route == ROOT.child(DOC).value
    assert "page directory" in raised.value.expected


def test_an_artifact_the_listing_named_and_the_read_refused_is_not_folded_into_no_ink():
    # The rename window: the firmware writes <page>.rm.tmp and renames over the target, so a
    # listing can name an artifact a read then cannot open. A page that had ink a moment ago
    # must not be exported blank, and DocumentSourceBundle is all-or-nothing by contract.
    shell = shell_for(
        {
            f"{DOC}{METADATA_SUFFIX}": store_metadata(),
            f"{DOC}{CONTENT_SUFFIX}": store_content(pages=[PAGE_A]),
        },
        page_dirs={DOC: {f"{PAGE_A}{SCENE_SUFFIX}": b"ink"}},
        refuse_reads=(ROOT.child(DOC).child(f"{PAGE_A}{SCENE_SUFFIX}").value,),
    )

    with pytest.raises(DeviceProtocolError) as raised:
        bundles(shell).load_bundle(DOC)

    assert raised.value.route == ROOT.child(DOC).child(f"{PAGE_A}{SCENE_SUFFIX}").value
    assert "scene artifact" in raised.value.expected


def test_a_bundle_whose_session_dies_part_way_propagates_rather_than_returning_a_hole():
    shell = notebook_store()
    shell.fail_with = DeviceUnreachable(
        transport=TransportKind.SSH,
        endpoint=ENDPOINT,
        detail="cable pulled",
    )
    shell.fail_after_reads = 3

    with pytest.raises(DeviceUnreachable):
        bundles(shell).load_bundle(DOC)


def test_an_unknown_identifier_never_reaches_a_read():
    shell = notebook_store()

    with pytest.raises(DeviceDocumentNotFound):
        bundles(shell).load_bundle("00000000-0000-4000-8000-000000000000")


def test_a_folder_identifier_never_reaches_a_bundle():
    shell = shell_for({f"{FOLDER}{METADATA_SUFFIX}": store_metadata(kind="CollectionType")})

    with pytest.raises(DeviceDocumentNotFound):
        bundles(shell).load_bundle(FOLDER)


# ─────────────────────────── upload ───────────────────────────


def request_for(
    *,
    media: UploadMedia = UploadMedia.PDF,
    parent_uuid: str | None = None,
    data: bytes = b"%PDF-1.7\npayload\n",
) -> UploadRequest:
    """Build an upload request.

    Parameters
    ----------
    media
        What the payload is.
    parent_uuid
        Destination folder, or ``None`` for the root.
    data
        The payload.

    Returns
    -------
    UploadRequest
        The request.
    """
    return UploadRequest(name="Monthly Report", media=media, data=data, parent_uuid=parent_uuid)


def test_an_upload_reports_what_the_transport_observed():
    shell = upload_shell()
    request = request_for()

    receipt = uploader(shell).upload(request)

    assert receipt.doc_uuid == MINTED
    assert receipt.name == "Monthly Report"
    assert receipt.media is UploadMedia.PDF
    assert receipt.byte_count == len(request.data)
    assert receipt.library_refresh is LibraryRefresh.VISIBILITY_FORCED


def test_the_metadata_sidecar_is_written_last_and_the_refresh_after_it():
    shell = upload_shell()
    paths = document_paths(ROOT, MINTED)

    uploader(shell).upload(request_for())

    assert shell.log == [
        f"run mkdir -p {paths.page_dir.value}",
        f"write {paths.underlay('pdf').value}",
        f"write {paths.content.value}",
        f"write {paths.metadata.value}",
        "run systemctl restart xochitl",
    ]


def test_no_zero_byte_scene_stubs_and_no_local_sidecar_are_written():
    # xochitl treats a missing .rm as routine (859 "no file found" log lines against 18
    # successful loads), and 0-byte stubs correlate exactly with the .failure archetype.
    shell = upload_shell()

    uploader(shell).upload(request_for())

    written = [path for path, _ in shell.writes]
    assert not any(path.endswith(SCENE_SUFFIX) for path in written)
    assert not any(path.endswith(".local") for path in written)
    assert not any("touch" in command for command in shell.commands)


def test_the_page_directory_is_created_even_though_it_stays_empty():
    shell = upload_shell()

    uploader(shell).upload(request_for())

    assert shell.commands[0] == f"mkdir -p {ROOT.child(MINTED).value}"


def test_the_written_sidecars_are_exactly_these_bytes(snapshot: object):
    shell = upload_shell()
    paths = document_paths(ROOT, MINTED)

    uploader(shell).upload(request_for(parent_uuid=FOLDER))

    assert {
        ".metadata": shell.written(paths.metadata).decode(),
        ".content": shell.written(paths.content).decode(),
    } == snapshot


def test_the_metadata_carries_created_time_and_a_real_last_modified():
    # Legacy push_pdf wrote lastModified: "" and no createdTime at all.
    shell = upload_shell()

    uploader(shell).upload(request_for())

    payload = json.loads(shell.written(document_paths(ROOT, MINTED).metadata))
    assert payload["createdTime"] == str(NOW_MS)
    assert payload["lastModified"] == str(NOW_MS)
    assert sorted(payload) == [
        "createdTime",
        "lastModified",
        "lastOpened",
        "lastOpenedPage",
        "parent",
        "pinned",
        "type",
        "visibleName",
    ]


def test_the_legacy_sync_v1_block_is_not_written():
    shell = upload_shell()

    uploader(shell).upload(request_for())

    payload = json.loads(shell.written(document_paths(ROOT, MINTED).metadata))
    for legacy in ("deleted", "metadatamodified", "modified", "synced", "version"):
        assert legacy not in payload


def test_the_content_uses_cpages_and_not_the_legacy_flat_array():
    # The flat "pages" key exists on exactly 1 of 40 real .content files.
    shell = upload_shell()

    uploader(shell).upload(request_for())

    payload = json.loads(shell.written(document_paths(ROOT, MINTED).content))
    assert payload["cPages"] == {"pages": []}
    assert "pages" not in payload
    assert payload["formatVersion"] == 2
    assert payload["pageCount"] == 0
    assert payload["fileType"] == "pdf"


def test_the_written_metadata_reads_back_through_the_domain_decoder():
    """The store's own reader must accept what this adapter writes, or the upload is a lie."""
    shell = upload_shell()
    paths = document_paths(ROOT, MINTED)

    uploader(shell).upload(request_for(parent_uuid=FOLDER))

    metadata = DocumentMetadata.decode(
        shell.written(paths.metadata),
        content=shell.written(paths.content),
    )
    assert metadata.visible_name == "Monthly Report"
    assert metadata.parent_uuid == FOLDER
    assert metadata.trashed is False
    assert metadata.last_opened is None
    assert metadata.last_modified is not None


def test_the_destination_folder_is_honoured_and_never_degraded_to_the_root():
    shell = upload_shell()

    uploader(shell).upload(request_for(parent_uuid=FOLDER))

    payload = json.loads(shell.written(document_paths(ROOT, MINTED).metadata))
    assert payload["parent"] == FOLDER


def test_the_library_root_is_written_as_the_empty_string_the_store_uses():
    shell = upload_shell()

    uploader(shell).upload(request_for(parent_uuid=None))

    assert json.loads(shell.written(document_paths(ROOT, MINTED).metadata))["parent"] == ""


def test_an_epub_is_placed_beside_its_sidecars_under_the_bare_type():
    shell = upload_shell()

    receipt = uploader(shell).upload(request_for(media=UploadMedia.EPUB, data=b"epub bytes"))

    assert receipt.media is UploadMedia.EPUB
    assert (document_paths(ROOT, MINTED).underlay("epub").value, b"epub bytes") in shell.writes
    assert json.loads(shell.written(document_paths(ROOT, MINTED).content))["fileType"] == "epub"


def test_an_archive_is_refused_before_a_single_path_is_touched():
    """Placing a ``.rmdoc`` here would be a different operation, not a third ``media`` value.

    The two placeable members are underlays: the payload goes to one path and this adapter
    composes the ``.content`` and ``.metadata`` that describe it. An archive carries its own
    copies of both plus one ``.rm`` per page, so placing one means unzipping it, resolving a
    uuid that already exists in the store, and re-keying pages -- with failure modes this
    signature promises nothing about. The refusal names the transport that *can* place one,
    because xochitl's own import route unpacks it.
    """
    shell = upload_shell()

    with pytest.raises(DeviceOperationUnsupported) as raised:
        uploader(shell).upload(request_for(media=UNPLACEABLE_MEDIA))

    assert raised.value.operation == UNPLACEABLE_OPERATION
    assert raised.value.transport is TransportKind.SSH
    assert raised.value.supported_by == (TransportKind.USB_WEB_API,)
    assert "usb_web_api" in str(raised.value.remediation)
    # Nothing at all happened: no directory, no sidecar, no orphan to go and find.
    assert shell.log == []


@pytest.mark.parametrize("media", [UploadMedia.PDF, UploadMedia.EPUB])
def test_the_two_underlay_media_are_still_placed(media: UploadMedia):
    """The narrowing is one member wide, asserted so it cannot quietly become two."""
    shell = upload_shell()

    receipt = uploader(shell).upload(request_for(media=media, data=b"payload"))

    assert receipt.media is media
    assert (document_paths(ROOT, MINTED).underlay(media.value).value, b"payload") in shell.writes


def test_an_uploaded_document_is_listed_by_the_catalog_that_reads_the_same_store():
    """End to end over one fake store: what was written is what a listing reports."""
    shell = upload_shell()
    shell.dirs[ROOT.value] = (f"{MINTED}{METADATA_SUFFIX}", f"{MINTED}{CONTENT_SUFFIX}")

    uploader(shell).upload(request_for())
    document = catalog(shell).list_documents().documents[0]

    assert (document.uuid, document.name) == (MINTED, "Monthly Report")
    assert document.file_type is DeviceFileType.PDF
    assert document.page_count == 0


# ─────────────────────────── upload failures ───────────────────────────


def test_a_failure_creating_the_directory_writes_nothing_at_all():
    paths = document_paths(ROOT, MINTED)
    shell = upload_shell(refuse_commands=(f"mkdir -p {paths.page_dir.value}",))

    with pytest.raises(DeviceProtocolError):
        uploader(shell).upload(request_for())

    assert shell.writes == []


@pytest.mark.parametrize("step", ["pdf", "content", "metadata"])
def test_a_failure_at_any_write_step_leaves_no_metadata_sidecar(step: str):
    paths = document_paths(ROOT, MINTED)
    target = {
        "pdf": paths.underlay("pdf"),
        "content": paths.content,
        "metadata": paths.metadata,
    }[step]
    shell = upload_shell(short_writes=(target.value,))

    with pytest.raises(DeviceTransferInterrupted):
        uploader(shell).upload(request_for())

    assert paths.metadata.value not in shell.files
    assert shell.log[-1] == f"short {target.value}"


def test_a_failure_before_the_commit_point_names_the_orphan_it_left():
    paths = document_paths(ROOT, MINTED)
    shell = upload_shell(short_writes=(paths.content.value,))

    with pytest.raises(DeviceTransferInterrupted) as raised:
        uploader(shell).upload(request_for())

    assert paths.page_dir.value in raised.value.subject
    assert "orphan" in raised.value.subject
    assert raised.value.bytes_expected is not None


def test_a_failure_at_the_commit_point_says_the_metadata_may_be_partly_written():
    # Distinct from the before-commit note on purpose: a partly written .metadata *is* named
    # by a listing, so the entry becomes visible and undecodable rather than invisible.
    paths = document_paths(ROOT, MINTED)
    shell = upload_shell(short_writes=(paths.metadata.value,))

    with pytest.raises(DeviceTransferInterrupted) as raised:
        uploader(shell).upload(request_for())

    assert paths.metadata.value in raised.value.subject
    assert "partly written" in raised.value.subject
    assert "orphan" not in raised.value.subject


def test_nothing_is_cleaned_up_after_a_failed_upload():
    # Deleting is itself destructive, and a failed mkdir may mean the directory already
    # held something. The orphan is reported instead of removed.
    paths = document_paths(ROOT, MINTED)
    shell = upload_shell(short_writes=(paths.content.value,))

    with pytest.raises(DeviceTransferInterrupted):
        uploader(shell).upload(request_for())

    assert paths.underlay("pdf").value in shell.files
    assert not any("rm " in command for command in shell.commands)


def test_a_failure_forcing_visibility_says_the_document_is_already_written():
    shell = upload_shell(refuse_commands=(RemoteCommand.of(REFRESH_TEMPLATE).text,))
    paths = document_paths(ROOT, MINTED)

    with pytest.raises(DeviceProtocolError) as raised:
        uploader(shell).upload(request_for())

    assert paths.metadata.value in shell.files
    assert "completely written" in raised.value.got


def test_a_short_write_is_never_a_receipt_reporting_fewer_bytes():
    paths = document_paths(ROOT, MINTED)
    shell = upload_shell(short_writes=(paths.underlay("pdf").value,))
    request = request_for()

    with pytest.raises(DeviceTransferInterrupted) as raised:
        uploader(shell).upload(request)

    assert raised.value.bytes_transferred == 0
    assert raised.value.bytes_expected == len(request.data)


@pytest.mark.parametrize(
    ("failure", "reader"),
    [
        pytest.param(
            DeviceUnreachable(transport=TransportKind.SSH, endpoint=ENDPOINT, detail="cable"),
            "detail",
            id="unreachable",
        ),
        pytest.param(
            DeviceAuthFailed(transport=TransportKind.SSH, user="root", detail="refused"),
            "detail",
            id="auth",
        ),
        pytest.param(
            DeviceProtocolError(
                transport=TransportKind.SSH,
                route="mkdir",
                expected="exit status 0",
                got="exit status 1",
            ),
            "got",
            id="protocol",
        ),
    ],
)
def test_every_failure_class_keeps_its_class_and_gains_the_orphan_note(
    failure: DeviceError,
    reader: str,
):
    shell = upload_shell(fail_with=failure)

    with pytest.raises(type(failure)) as raised:
        uploader(shell).upload(request_for())

    assert "orphan" in getattr(raised.value, reader)


def test_a_failure_class_with_no_extendable_field_is_raised_unchanged():
    failure = DeviceUploadRejected(
        transport=TransportKind.SSH,
        name="Monthly Report",
        device_message="no free space",
    )
    shell = upload_shell(fail_with=failure)

    with pytest.raises(DeviceUploadRejected) as raised:
        uploader(shell).upload(request_for())

    assert raised.value is failure


def test_an_auth_failure_keeps_the_key_source_it_carried():
    failure = DeviceAuthFailed(
        transport=TransportKind.SSH,
        user="root",
        detail="refused",
        key_source="/home/user/.ssh/id_ed25519_remarkable",
    )
    shell = upload_shell(fail_with=failure)

    with pytest.raises(DeviceAuthFailed) as raised:
        uploader(shell).upload(request_for())

    assert raised.value.key_source == "/home/user/.ssh/id_ed25519_remarkable"
    assert raised.value.user == "root"


# ─────────────────────────── the search index ───────────────────────────


def index_shell(**seams: object) -> FakeShell:
    """Build a shell holding the search index directly under the root.

    Parameters
    ----------
    **seams
        Forwarded to :class:`FakeShell` -- ``refuse_reads``, ``fail_with``.

    Returns
    -------
    FakeShell
        The double. The store holds nothing else, because the adapter reads nothing else.
    """
    shell = shell_for({SEARCH_INDEX_NAME: SEARCH_INDEX_IMAGE})
    for name, value in seams.items():
        setattr(shell, name, frozenset(value) if isinstance(value, tuple) else value)
    return shell


def test_the_index_is_read_once_from_one_path_under_the_root():
    # No `ls` first, unlike every other adapter here: absence *is* an answer this port can
    # give, so a listing to decide presence would be a round trip that changes nothing. And
    # exactly one read, because the image is 503,808 bytes on the measured device -- the port's
    # REQUEST scope exists so a caller pays that once per command rather than once per page.
    shell = index_shell()

    image = SshSearchIndexSource(shell).read_index()

    assert image == SEARCH_INDEX_IMAGE
    assert shell.log == [f"read {INDEX_PATH.value}"]
    assert shell.listings == []


def test_a_device_with_no_index_answers_absence_rather_than_failing():
    # The index is built by the tablet on its own schedule, so this is an ordinary condition
    # and not an error. `TestNb` was measured with zero rows in an index built two hours
    # earlier; a device that has built none at all is the same fact one step further back.
    assert SshSearchIndexSource(shell_for({})).read_index() is None


def test_a_read_the_device_refuses_is_absence_too():
    # The real shell cannot tell "absent" from "permission denied": paramiko attaches an errno
    # for both SFTP status codes and PathUnreadableError carries both. So a branch that
    # answered only for absence would let the package-private error out of a port method on
    # the other one -- which is exactly what this port's None is here to prevent.
    shell = index_shell(refuse_reads=frozenset({INDEX_PATH.value}))

    assert SshSearchIndexSource(shell).read_index() is None


def test_a_zero_length_index_file_is_empty_bytes_and_not_absence():
    # The opposite of this module's rule for a scene artifact, deliberately. There, None means
    # "this page carries no ink" and a zero-byte artifact is how the firmware says it -- 86 of
    # 194 real ones are exactly that. Here None means "this device has no index", so a
    # zero-length file is a device that *has* one and whose one is unusable, which the reader
    # reports as a store failure rather than as a miss that suppresses a paid read forever.
    shell = shell_for({SEARCH_INDEX_NAME: b""})

    assert SshSearchIndexSource(shell).read_index() == b""


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(
            DeviceUnreachable(transport=TransportKind.SSH, endpoint=ENDPOINT, detail="no cable"),
            id="unreachable",
        ),
        pytest.param(
            DeviceAuthFailed(
                transport=TransportKind.SSH,
                user="root",
                detail="refused",
                key_source=None,
            ),
            id="auth",
        ),
        pytest.param(
            DeviceProtocolError(
                transport=TransportKind.SSH,
                route=INDEX_PATH.value,
                expected="a readable channel",
                got="the channel closed mid-frame",
            ),
            id="protocol",
        ),
    ],
)
def test_a_failure_that_describes_the_session_propagates_rather_than_reading_as_no_index(
    failure: DeviceError,
):
    # The whole point of the PathUnreadableError split. An unplugged tablet reported as "no
    # index" would suppress the free prior silently and be indistinguishable from a cache miss
    # on every page, forever -- the same defect class as a mid-walk disconnect returning a
    # shrunken library with a success exit status.
    shell = index_shell(fail_with=failure)

    with pytest.raises(type(failure)) as raised:
        SshSearchIndexSource(shell).read_index()

    assert raised.value is failure


def test_the_root_defaults_to_the_xochitl_root_and_is_still_a_parameter():
    # Defaulted because this adapter is bound alone rather than paired with a catalog that has
    # to be given the same root; still a parameter, so a synthetic tree is addressable and a
    # future mirror transport needs no second copy of this read.
    elsewhere = RemotePath.absolute("/home/root/synthetic-store")
    shell = FakeShell(
        files={
            INDEX_PATH.value: SEARCH_INDEX_IMAGE,
            elsewhere.child(SEARCH_INDEX_NAME).value: b"another store's index",
        }
    )

    assert SshSearchIndexSource(shell).read_index() == SEARCH_INDEX_IMAGE
    assert SshSearchIndexSource(shell, root=elsewhere).read_index() == b"another store's index"


def test_nothing_in_this_module_imports_sqlite3():
    """The reason this port hands over bytes instead of rows, asserted rather than remembered.

    There is no ``sqlite3`` binary on the device and no BusyBox applet for one, so an
    on-device query is not an available shape; and ``rmspec-device`` may not import
    ``sqlite3`` either, which assigns the reading half to ``rmspec-persistence``.
    ``tests/architecture/test_dependency_direction.py`` enforces the second half across the
    whole package -- this is the local statement of it, next to the adapter that would be the
    tempting place to break it.
    """
    tree = ast.parse(inspect.getsource(ssh_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported, "found no imports, so this assertion would be vacuous"
    assert "sqlite3" not in imported
