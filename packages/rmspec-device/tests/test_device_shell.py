"""No socket, no password, and no ``paramiko`` type reaching a caller.

Three properties carry this file.

**Nothing here opens a connection.** The tablet is attached at 10.11.99.1 with working key
authentication, so a test that reached the network would silently *pass* and an assertion
about it would be meaningless. Every test replaces ``paramiko.SSHClient`` with a double
whose ``connect`` does nothing, and the doubles record what they were asked for -- which is
how the "no password" and "host key verified" claims are asserted at all, rather than
asserted by reading the source.

**No credential ever reaches the wire.** ``ParamikoShell`` authenticates with a key file or
with the local agent. The developer credential the legacy client accepted lives in the
tablet's own config file, which no source file in this workspace may name, so the assertion
here is the absence of the keyword in what was handed to ``connect``.

**Every exception type is translated.** The four families
:func:`~rmspec.device._errors.translate_ssh` distinguishes are each provoked through the
shell rather than by calling the translator directly, so the wiring between the two is what
is under test -- ``_errors`` already has its own suite for the classification itself.
"""

from __future__ import annotations

import errno
from typing import TYPE_CHECKING, Self

import paramiko
import pytest
from paramiko.ssh_exception import (
    AuthenticationException,
    BadHostKeyException,
    NoValidConnectionsError,
    PasswordRequiredException,
    SSHException,
)

from rmspec.device._errors import PATH_UNREADABLE_DIAGNOSIS
from rmspec.device._shell import (
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER,
    LIST_DIR_TEMPLATE,
    ParamikoShell,
    PathUnreadableError,
)
from rmspec.device.addresses import SSH_PORT, Endpoint, RemoteCommand, RemotePath
from rmspec.domain.errors import (
    DeviceAuthFailed,
    DeviceError,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    TransportKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rmspec.device._shell import RemoteShell

HOST = "10.11.99.1"
WHERE = f"{HOST}:{SSH_PORT}"
KEY_PATH = "/home/user/.ssh/id_ed25519_remarkable"
ROOT = RemotePath.root()
FILE = ROOT.child("b8ff2c3d-0a1e-4f77-9c21-6a0e5d4b7f10").with_suffix(".metadata")


class _FakeKey:
    """Enough of ``paramiko.PKey`` for ``BadHostKeyException`` to build its message."""

    def get_base64(self) -> str:
        """Return a stand-in for a base64 host key.

        Returns
        -------
        str
            A fixed, meaningless value. No real key material belongs in a test.
        """
        return "AAAAC3NzaC1lZDI1NTE5"


class _FakeChannel:
    """The exit-status half of a paramiko exec channel."""

    def __init__(self, exit_status: int) -> None:
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        """Return the status the command exited with.

        Returns
        -------
        int
            The scripted status.
        """
        return self._exit_status


class _FakeStream:
    """One of the three file objects ``exec_command`` returns."""

    def __init__(self, payload: bytes, exit_status: int = 0) -> None:
        self.channel = _FakeChannel(exit_status)
        self._payload = payload

    def read(self) -> bytes:
        """Return the whole stream.

        Returns
        -------
        bytes
            The scripted payload.
        """
        return self._payload


class _FakeHandle:
    """An SFTP file handle, used as a context manager exactly as the shell uses one."""

    def __init__(self, payload: bytes = b"", sink: list[bytes] | None = None) -> None:
        self._payload = payload
        self._sink = sink

    def __enter__(self) -> Self:
        """Return self, so ``with sftp.open(...) as handle`` works.

        Returns
        -------
        Self
            This handle.
        """
        return self

    def __exit__(self, *_: object) -> None:
        """Close the handle, which costs nothing here."""
        return

    def read(self) -> bytes:
        """Return the file's bytes.

        Returns
        -------
        bytes
            The scripted payload.
        """
        return self._payload

    def write(self, data: bytes) -> None:
        """Record the bytes offered to the device.

        Parameters
        ----------
        data
            What the shell wrote.
        """
        if self._sink is not None:
            self._sink.append(data)


class _FakeStat:
    """The one field of ``SFTPAttributes`` the shell reads."""

    def __init__(self, st_size: int | None) -> None:
        self.st_size = st_size


class _FakeSftp:
    """An SFTP channel over two dicts, with a failure seam per operation."""

    def __init__(
        self,
        *,
        files: dict[str, bytes] | None = None,
        sizes: dict[str, int | None] | None = None,
        open_error: Exception | None = None,
        stat_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.files = {} if files is None else dict(files)
        self.sizes = {} if sizes is None else dict(sizes)
        self.open_error = open_error
        self.stat_error = stat_error
        self.close_error = close_error
        self.opened: list[tuple[str, str]] = []
        self.closes = 0
        self._sinks: dict[str, list[bytes]] = {}

    @property
    def writes(self) -> list[tuple[str, bytes]]:
        """Return every write the shell performed, in order.

        Returns
        -------
        list[tuple[str, bytes]]
            One ``(path, bytes)`` pair per opened write handle.
        """
        return [(path, b"".join(chunks)) for path, chunks in self._sinks.items()]

    def open(self, filename: str, mode: str = "r", bufsize: int = -1) -> _FakeHandle:
        """Open one path, recording the mode.

        Parameters
        ----------
        filename
            The remote path, as a bare string -- which is how paramiko takes it.
        mode
            ``"rb"`` or ``"wb"``.
        bufsize
            Accepted for signature compatibility and unused.

        Returns
        -------
        _FakeHandle
            A handle over the scripted bytes, or a sink for a write.

        Raises
        ------
        Exception
            The scripted ``open_error``, whatever type it is.
        """
        del bufsize
        if self.open_error is not None:
            raise self.open_error
        self.opened.append((filename, mode))
        if "w" in mode:
            chunks: list[bytes] = []
            self._sinks[filename] = chunks
            return _FakeHandle(sink=chunks)
        return _FakeHandle(self.files.get(filename, b""))

    def stat(self, path: str) -> _FakeStat:
        """Report the size the device holds for a path.

        Parameters
        ----------
        path
            The remote path.

        Returns
        -------
        _FakeStat
            The scripted size when one was set, else the length actually written.

        Raises
        ------
        Exception
            The scripted ``stat_error``, whatever type it is.
        """
        if self.stat_error is not None:
            raise self.stat_error
        if path in self.sizes:
            return _FakeStat(self.sizes[path])
        chunks = self._sinks.get(path)
        if chunks is not None:
            return _FakeStat(len(b"".join(chunks)))
        return _FakeStat(len(self.files.get(path, b"")))

    def close(self) -> None:
        """Close the channel.

        Raises
        ------
        Exception
            The scripted ``close_error``, whatever type it is.
        """
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


class _FakeSshClient:
    """A stand-in for ``paramiko.SSHClient`` that never touches a socket."""

    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        sftp: _FakeSftp | None = None,
        exec_error: Exception | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        exit_status: int = 0,
        close_error: Exception | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.sftp = _FakeSftp() if sftp is None else sftp
        self.exec_error = exec_error
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status
        self.close_error = close_error
        self.connect_kwargs: dict[str, object] = {}
        self.commands: list[str] = []
        self.timeouts: list[float | None] = []
        self.host_keys_loaded = 0
        self.policies: list[object] = []
        self.closes = 0

    def load_system_host_keys(self, filename: str | None = None) -> None:
        """Record that the user's ``known_hosts`` was consulted.

        Parameters
        ----------
        filename
            Accepted for signature compatibility and unused.
        """
        del filename
        self.host_keys_loaded += 1

    def set_missing_host_key_policy(self, policy: object) -> None:
        """Record a policy change, which this shell must never make.

        Parameters
        ----------
        policy
            Whatever was offered.
        """
        self.policies.append(policy)

    def connect(self, **kwargs: object) -> None:
        """Record the connection arguments without connecting.

        Parameters
        ----------
        **kwargs
            Exactly what the shell passed.

        Raises
        ------
        Exception
            The scripted ``connect_error``, whatever type it is.
        """
        self.connect_kwargs = dict(kwargs)
        if self.connect_error is not None:
            raise self.connect_error

    def open_sftp(self) -> _FakeSftp:
        """Return the scripted SFTP channel.

        Returns
        -------
        _FakeSftp
            The channel.
        """
        return self.sftp

    def exec_command(
        self,
        command: str,
        timeout: float | None = None,
    ) -> tuple[None, _FakeStream, _FakeStream]:
        """Record a command and return its scripted streams.

        Parameters
        ----------
        command
            The command text, as the shell assembled it.
        timeout
            The stall budget the shell chose.

        Returns
        -------
        tuple[None, _FakeStream, _FakeStream]
            The stdin placeholder the shell discards, then stdout and stderr.

        Raises
        ------
        Exception
            The scripted ``exec_error``, whatever type it is.
        """
        self.commands.append(command)
        self.timeouts.append(timeout)
        if self.exec_error is not None:
            raise self.exec_error
        return (None, _FakeStream(self.stdout, self.exit_status), _FakeStream(self.stderr))

    def close(self) -> None:
        """Close the session.

        Raises
        ------
        Exception
            The scripted ``close_error``, whatever type it is.
        """
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


def _install(monkeypatch: pytest.MonkeyPatch, client: _FakeSshClient) -> None:
    """Make ``paramiko.SSHClient()`` yield *client* for the duration of one test.

    Parameters
    ----------
    monkeypatch
        pytest's patcher, which undoes this afterwards.
    client
        The double the shell will get.
    """
    monkeypatch.setattr(paramiko, "SSHClient", lambda: client)


def _shell(client: _FakeSshClient, *, key_path: str | None = None) -> ParamikoShell:
    """Build a shell aimed at the USB endpoint on the SSH port.

    Parameters
    ----------
    client
        Unused by the constructor; named so a caller reads the pairing.
    key_path
        Passed straight through.

    Returns
    -------
    ParamikoShell
        An unconnected shell.
    """
    del client
    return ParamikoShell(endpoint=Endpoint(host=HOST, port=SSH_PORT), key_path=key_path)


def _connected(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeSshClient,
    *,
    key_path: str | None = None,
) -> ParamikoShell:
    """Build and connect a shell against a double.

    Parameters
    ----------
    monkeypatch
        pytest's patcher.
    client
        The double.
    key_path
        Passed straight through.

    Returns
    -------
    ParamikoShell
        A connected shell.
    """
    _install(monkeypatch, client)
    shell = _shell(client, key_path=key_path)
    shell.connect()
    return shell


def _raise(failure: Exception) -> Callable[[], object]:
    """Build a zero-argument callable that raises *failure*.

    Parameters
    ----------
    failure
        What to raise.

    Returns
    -------
    Callable[[], object]
        A callable that always raises, for patching one method of a double.
    """

    def go() -> object:
        raise failure

    return go


# ─────────────────────────── the seam ───────────────────────────


def test_the_paramiko_shell_satisfies_the_remote_shell_protocol(monkeypatch: pytest.MonkeyPatch):
    """``ty`` checks the annotation; this asserts the four members exist at runtime."""
    client = _FakeSshClient()
    _install(monkeypatch, client)
    shell: RemoteShell = _shell(client)

    for name in ("run", "read_file", "list_dir", "write_file"):
        assert callable(getattr(shell, name))


# ─────────────────────────── connect ───────────────────────────


def test_connect_never_offers_a_password(monkeypatch: pytest.MonkeyPatch):
    """The whole reason this shell exists rather than the legacy ``DeviceConnection``."""
    client = _FakeSshClient()

    _connected(monkeypatch, client)

    assert "password" not in client.connect_kwargs
    assert "passphrase" not in client.connect_kwargs
    assert "auth_strategy" not in client.connect_kwargs


def test_connect_uses_the_key_file_when_one_is_given(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()

    _connected(monkeypatch, client, key_path=KEY_PATH)

    assert client.connect_kwargs["key_filename"] == KEY_PATH


def test_connect_falls_back_to_the_agent_and_default_keys(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()

    _connected(monkeypatch, client)

    assert client.connect_kwargs["key_filename"] is None
    assert client.connect_kwargs["allow_agent"] is True
    assert client.connect_kwargs["look_for_keys"] is True


def test_connect_addresses_the_endpoint_it_was_given(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()

    _connected(monkeypatch, client)

    assert client.connect_kwargs["hostname"] == HOST
    assert client.connect_kwargs["port"] == SSH_PORT
    assert client.connect_kwargs["username"] == DEFAULT_USER
    assert client.connect_kwargs["timeout"] == DEFAULT_TIMEOUT_SECONDS


def test_connect_verifies_the_host_key_instead_of_auto_adding_it(monkeypatch: pytest.MonkeyPatch):
    # Legacy set AutoAddPolicy(), which trusts whatever key the far end presents. Keeping
    # paramiko's default RejectPolicy is what turns a changed key into DeviceAuthFailed.
    client = _FakeSshClient()

    _connected(monkeypatch, client)

    assert client.host_keys_loaded == 1
    assert client.policies == []


def test_a_shell_may_be_built_with_a_non_default_user(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()
    _install(monkeypatch, client)
    shell = ParamikoShell(endpoint=Endpoint(host=HOST, port=SSH_PORT), user="other", timeout=1.0)

    shell.connect()

    assert client.connect_kwargs["username"] == "other"
    assert client.connect_kwargs["timeout"] == 1.0


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        pytest.param(AuthenticationException("no"), DeviceAuthFailed, id="auth"),
        pytest.param(PasswordRequiredException("no"), DeviceAuthFailed, id="passphrase"),
        pytest.param(
            BadHostKeyException(HOST, _FakeKey(), _FakeKey()),
            DeviceAuthFailed,
            id="host-key-changed",
        ),
        pytest.param(
            NoValidConnectionsError({(HOST, SSH_PORT): OSError("no route")}),
            DeviceUnreachable,
            id="no-valid-connections",
        ),
        pytest.param(TimeoutError("stalled"), DeviceUnreachable, id="socket-timeout"),
        pytest.param(OSError("cable"), DeviceUnreachable, id="oserror"),
        pytest.param(SSHException("banner"), DeviceProtocolError, id="ssh-protocol"),
        pytest.param(EOFError("mid-frame"), DeviceProtocolError, id="unanticipated"),
    ],
)
def test_every_paramiko_failure_arrives_as_its_domain_class(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: type[DeviceError],
):
    client = _FakeSshClient(connect_error=failure)
    _install(monkeypatch, client)
    shell = _shell(client)

    with pytest.raises(expected) as raised:
        shell.connect()

    assert raised.value.transport is TransportKind.SSH


def test_the_two_unanticipated_arms_are_reported_as_such(monkeypatch: pytest.MonkeyPatch):
    """``EOFError`` is neither an ``SSHException`` nor an ``OSError``, and is still named."""
    client = _FakeSshClient(connect_error=EOFError("mid-frame"))
    _install(monkeypatch, client)

    with pytest.raises(DeviceProtocolError) as raised:
        _shell(client).connect()

    assert raised.value.expected == "an exception type this adapter classifies"


def test_a_protocol_failure_names_the_ssh_contract(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(connect_error=SSHException("banner"))
    _install(monkeypatch, client)

    with pytest.raises(DeviceProtocolError) as raised:
        _shell(client).connect()

    assert raised.value.expected == "a session that keeps to the SSH protocol"


def test_an_auth_failure_names_the_key_file_but_never_its_contents(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _FakeSshClient(connect_error=AuthenticationException("refused"))
    _install(monkeypatch, client)

    with pytest.raises(DeviceAuthFailed) as raised:
        _shell(client, key_path=KEY_PATH).connect()

    assert raised.value.key_source == KEY_PATH
    assert raised.value.user == DEFAULT_USER


def test_an_auth_failure_over_the_agent_names_no_key_source(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(connect_error=AuthenticationException("refused"))
    _install(monkeypatch, client)

    with pytest.raises(DeviceAuthFailed) as raised:
        _shell(client).connect()

    assert raised.value.key_source is None


def test_an_unreachable_endpoint_is_named_as_host_and_port(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(connect_error=OSError("cable"))
    _install(monkeypatch, client)

    with pytest.raises(DeviceUnreachable) as raised:
        _shell(client).connect()

    assert raised.value.endpoint == WHERE


def test_a_failed_connect_closes_the_client_it_opened(monkeypatch: pytest.MonkeyPatch):
    # Otherwise a retry loop leaks one client per attempt.
    client = _FakeSshClient(connect_error=OSError("cable"))
    _install(monkeypatch, client)

    with pytest.raises(DeviceUnreachable):
        _shell(client).connect()

    assert client.closes == 1


def test_a_failure_opening_the_sftp_channel_is_translated(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()
    monkeypatch.setattr(client, "open_sftp", _raise(SSHException("no channel")))
    _install(monkeypatch, client)

    with pytest.raises(DeviceProtocolError):
        _shell(client).connect()


# ─────────────────────────── not connected ───────────────────────────


def test_every_operation_refuses_before_connect(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()
    _install(monkeypatch, client)
    shell = _shell(client)

    for operation in (
        lambda: shell.run(RemoteCommand.of("ls")),
        lambda: shell.read_file(FILE),
        lambda: shell.list_dir(ROOT),
        lambda: shell.write_file(FILE, b"x"),
    ):
        with pytest.raises(DeviceUnreachable, match="not connected"):
            operation()


def test_operations_refuse_again_after_close(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient()
    shell = _connected(monkeypatch, client)

    shell.close()

    with pytest.raises(DeviceUnreachable, match="not connected"):
        shell.read_file(FILE)


# ─────────────────────────── run ───────────────────────────


def test_run_returns_stdout_verbatim(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(stdout=b"3.27.3.0\n")
    shell = _connected(monkeypatch, client)

    assert shell.run(RemoteCommand.of("cat {}", RemotePath.absolute("/etc/os-release"))) == (
        "3.27.3.0\n"
    )
    assert client.commands == ["cat /etc/os-release"]
    assert client.timeouts == [DEFAULT_TIMEOUT_SECONDS]


def test_a_non_zero_exit_status_is_a_protocol_error_naming_the_command(
    monkeypatch: pytest.MonkeyPatch,
):
    # Legacy raised a bare RuntimeError here, which the CLI could not classify.
    client = _FakeSshClient(exit_status=1, stderr=b"ls: /nope: No such file\nnoise\n")
    shell = _connected(monkeypatch, client)

    with pytest.raises(DeviceProtocolError) as raised:
        shell.run(RemoteCommand.of("ls -A {}", ROOT))

    assert raised.value.route == f"ls -A {ROOT.value}"
    assert raised.value.expected == "exit status 0"
    assert "ls: /nope: No such file" in raised.value.got
    assert "noise" not in raised.value.got


def test_stdout_that_is_not_text_is_reported_rather_than_mangled(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _FakeSshClient(stdout=b"\xff\xfe")
    shell = _connected(monkeypatch, client)

    with pytest.raises(DeviceProtocolError):
        shell.run(RemoteCommand.of("ls"))


def test_a_failure_on_the_exec_channel_is_translated(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(exec_error=SSHException("channel closed"))
    shell = _connected(monkeypatch, client)

    with pytest.raises(DeviceProtocolError):
        shell.run(RemoteCommand.of("ls"))


# ─────────────────────────── list_dir ───────────────────────────


def test_list_dir_sends_the_busybox_listing_command(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(stdout=b"a.metadata\nb.content\n")
    shell = _connected(monkeypatch, client)

    names = shell.list_dir(ROOT)

    assert names == ("a.metadata", "b.content")
    assert client.commands == [RemoteCommand.of(LIST_DIR_TEMPLATE, ROOT).text]
    assert client.commands == [f"ls -A {ROOT.value}"]


def test_list_dir_drops_blank_lines_and_keeps_ls_order(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(stdout=b"z\n\ny\n\n")
    shell = _connected(monkeypatch, client)

    assert shell.list_dir(ROOT) == ("z", "y")


def test_an_empty_directory_lists_as_nothing(monkeypatch: pytest.MonkeyPatch):
    client = _FakeSshClient(stdout=b"")
    shell = _connected(monkeypatch, client)

    assert shell.list_dir(ROOT) == ()


def test_a_directory_that_cannot_be_listed_is_a_per_path_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    # An `ls` that ran and exited non-zero proves the session is alive and the path is the
    # problem, so it is a stronger per-path signal than an errno -- and a caller with a
    # per-entry answer needs to be able to tell it from a dead session.
    client = _FakeSshClient(exit_status=1, stderr=b"ls: can't open: Permission denied\nnoise\n")
    shell = _connected(monkeypatch, client)

    with pytest.raises(PathUnreadableError) as raised:
        shell.list_dir(ROOT)

    assert raised.value.path == ROOT.value
    assert "exit status 1" in raised.value.detail
    assert "Permission denied" in raised.value.detail
    assert "noise" not in raised.value.detail
    assert not isinstance(raised.value, DeviceError)


def test_a_listing_whose_channel_dies_is_a_transport_failure_not_a_per_path_one(
    monkeypatch: pytest.MonkeyPatch,
):
    # The other half of the split: nothing here is a statement about the path.
    client = _FakeSshClient(exec_error=OSError("cable"))
    shell = _connected(monkeypatch, client)

    with pytest.raises(DeviceUnreachable):
        shell.list_dir(ROOT)


# ─────────────────────────── read_file ───────────────────────────


def test_read_file_returns_the_whole_file(monkeypatch: pytest.MonkeyPatch):
    sftp = _FakeSftp(files={FILE.value: b'{"visibleName": "x"}'})
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    assert shell.read_file(FILE) == b'{"visibleName": "x"}'
    assert sftp.opened == [(FILE.value, "rb")]


def test_a_zero_length_file_reads_as_empty_bytes_not_none(monkeypatch: pytest.MonkeyPatch):
    # 86 of the 194 real .rm files are exactly this. Deciding what it means is the
    # caller's business, which is why the shell does not decide.
    sftp = _FakeSftp(files={FILE.value: b""})
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    assert shell.read_file(FILE) == b""


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(FileNotFoundError(errno.ENOENT, "No such file"), id="absent"),
        pytest.param(PermissionError(errno.EACCES, "Permission denied"), id="refused"),
        pytest.param(IsADirectoryError(errno.EISDIR, "Is a directory"), id="directory"),
        pytest.param(NotADirectoryError(errno.ENOTDIR, "Not a directory"), id="not-a-directory"),
    ],
)
def test_a_path_that_cannot_be_opened_is_a_per_path_failure_not_an_unreachable_tablet(
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
):
    """The regression this split exists for, at the layer that can still tell them apart.

    paramiko converts ``SFTP_NO_SUCH_FILE`` to ``IOError(errno.ENOENT, ...)`` and
    ``SFTP_PERMISSION_DENIED`` to ``IOError(errno.EACCES, ...)``, and ``OSError.__new__``
    turns those into these subclasses. Folding them into ``DeviceUnreachable`` -- which the
    first revision of this module did -- forces every caller to treat a dead session as a
    per-entry fact.
    """
    sftp = _FakeSftp(open_error=failure)
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(PathUnreadableError) as raised:
        shell.read_file(FILE)

    assert raised.value.path == FILE.value
    assert type(failure).__name__ in raised.value.detail
    assert not isinstance(raised.value, DeviceError)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(NoValidConnectionsError({(HOST, SSH_PORT): OSError("no route")}), id="route"),
        pytest.param(TimeoutError("stalled"), id="timeout"),
        pytest.param(OSError("generic sftp failure"), id="bare-oserror"),
        pytest.param(ConnectionResetError("peer reset"), id="reset"),
    ],
)
def test_a_read_whose_session_died_is_a_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
):
    # None of these carries a per-path errno. A generic SFTP failure stays here on purpose:
    # it is genuinely ambiguous, and calling it per-path would let one broken session be
    # reported as forty-two unreadable documents.
    sftp = _FakeSftp(open_error=failure)
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(DeviceUnreachable) as raised:
        shell.read_file(FILE)

    assert raised.value.transport is TransportKind.SSH
    assert not isinstance(raised.value, PathUnreadableError)


def test_a_read_whose_channel_misbehaved_is_a_protocol_failure(monkeypatch: pytest.MonkeyPatch):
    sftp = _FakeSftp(open_error=SSHException("channel closed"))
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(DeviceProtocolError):
        shell.read_file(FILE)


# ─────────────────────────── write_file ───────────────────────────


def test_write_file_offers_the_whole_payload(monkeypatch: pytest.MonkeyPatch):
    sftp = _FakeSftp()
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    shell.write_file(FILE, b"payload")

    assert sftp.opened == [(FILE.value, "wb")]
    assert sftp.writes == [(FILE.value, b"payload")]


def test_a_short_write_is_a_transfer_interruption_carrying_both_counts(
    monkeypatch: pytest.MonkeyPatch,
):
    sftp = _FakeSftp(sizes={FILE.value: 3})
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(DeviceTransferInterrupted) as raised:
        shell.write_file(FILE, b"payload")

    assert raised.value.subject == FILE.value
    assert raised.value.bytes_transferred == 3
    assert raised.value.bytes_expected == len(b"payload")


def test_a_device_that_reports_no_size_is_treated_as_having_written_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    sftp = _FakeSftp(sizes={FILE.value: None})
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(DeviceTransferInterrupted) as raised:
        shell.write_file(FILE, b"payload")

    assert raised.value.bytes_transferred == 0


def test_an_empty_payload_that_lands_empty_is_not_an_interruption(
    monkeypatch: pytest.MonkeyPatch,
):
    sftp = _FakeSftp(sizes={FILE.value: 0})
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    shell.write_file(FILE, b"")


def test_a_failure_confirming_the_size_is_translated(monkeypatch: pytest.MonkeyPatch):
    sftp = _FakeSftp(stat_error=OSError("gone"))
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(DeviceUnreachable):
        shell.write_file(FILE, b"payload")


def test_a_write_into_a_directory_that_does_not_exist_is_a_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
):
    # An uploader has no per-entry answer to give, so a write wants the domain
    # classification rather than PathUnreadableError -- and the route names the path, which
    # is what the new arm in translate_ssh buys. Before it, this read as "cannot reach the
    # tablet", which sends the user to check a cable that is fine.
    sftp = _FakeSftp(open_error=FileNotFoundError(errno.ENOENT, "No such file"))
    shell = _connected(monkeypatch, _FakeSshClient(sftp=sftp))

    with pytest.raises(DeviceProtocolError) as raised:
        shell.write_file(FILE, b"payload")

    assert raised.value.route == FILE.value
    assert raised.value.expected == PATH_UNREADABLE_DIAGNOSIS
    assert not isinstance(raised.value, PathUnreadableError)


# ─────────────────────────── close ───────────────────────────


def test_close_closes_both_handles(monkeypatch: pytest.MonkeyPatch):
    sftp = _FakeSftp()
    client = _FakeSshClient(sftp=sftp)
    shell = _connected(monkeypatch, client)

    shell.close()

    assert (sftp.closes, client.closes) == (1, 1)


def test_close_is_idempotent_and_safe_on_a_shell_that_never_connected(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _FakeSshClient()
    _install(monkeypatch, client)
    never = _shell(client)

    never.close()
    never.close()

    assert client.closes == 0


def test_a_failure_while_closing_is_suppressed_and_does_not_stop_the_other_handle(
    monkeypatch: pytest.MonkeyPatch,
):
    # A close that fails cannot be acted on, and letting it propagate would replace
    # whatever error prompted the close in the first place.
    sftp = _FakeSftp(close_error=OSError("already gone"))
    client = _FakeSshClient(sftp=sftp, close_error=SSHException("already gone"))
    shell = _connected(monkeypatch, client)

    shell.close()

    assert (sftp.closes, client.closes) == (1, 1)
