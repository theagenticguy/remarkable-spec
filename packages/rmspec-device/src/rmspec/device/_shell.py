"""The SSH transport: four operations, and the seam that makes them fakeable.

Everything in this package that reaches the tablet over SSH goes through
:class:`RemoteShell`. It is a ``Protocol`` with four methods and no others, and
:class:`ParamikoShell` is the one implementation that owns a socket. The four adapters in
``rmspec.device.ssh`` accept the Protocol, so every one of them is testable against an
in-memory double -- which matters because ``paramiko`` ships no fake, and a test that
accidentally opened a real session against an attached tablet would *pass*.

Why four methods and not one ``execute(command)``
------------------------------------------------
An arbitrary-command port has an unbounded error set: every caller would have to know
which BusyBox utilities exist, what each writes to stderr, and how to classify each
non-zero status, and a double would have to emulate a shell to answer. That is the same
argument ``rmspec.domain.ports.device`` makes when it declares
:class:`~rmspec.domain.ports.device.DeviceFactsSource` as a closed set of typed fields
instead of a ``run(cmd)`` method, and this Protocol is the transport-level restatement of
it. The commands this package sends are literals written here and in
``rmspec.device.ssh``; a caller supplies arguments, never operators.

Relocated from ``src/remarkable_spec/device/connection.py``
----------------------------------------------------------
Replaces ``DeviceConnection``. Every divergence below is deliberate.

1. **No exception type escapes, and nothing catches bare ``Exception``.** Legacy
   ``connect`` wrapped its whole body in ``except Exception as exc`` (line 112) and
   re-raised ``ConnectionError``, so an authentication failure, a changed host key and an
   unplugged cable all arrived at the CLI as the same class with paramiko's own wording
   pasted into the message. Legacy ``execute`` raised a bare
   ``RuntimeError(f"Command failed (exit {exit_code}): ...")`` (line 162) and
   ``ConnectionError("Not connected to device...")``, neither of which is in
   ``rmspec.domain.errors``. Here every failure passes through
   :func:`~rmspec.device._errors.translate_ssh` or
   :func:`~rmspec.device._errors.command_failed`, and the ``except`` clause names its
   types: ``SSHException`` covers the thirteen paramiko types inside that tree,
   ``OSError`` covers ``NoValidConnectionsError`` -- which sits on ``OSError`` and not on
   ``SSHException`` -- and ``socket.timeout``, and ``EOFError`` and ``UnicodeDecodeError``
   cover a session that died mid-frame and stdout that is not text. This module classifies
   nothing itself: the translator is total, so adding a check here would be a second
   opinion that could disagree with it.

2. **No password authentication, ever.** Legacy ``DeviceConnection`` took a ``password``
   argument, the CLI read it from settings, and the value it wanted lives in the tablet's
   own configuration file next to two bearer tokens.
   ``tests/architecture/test_secret_containment.py`` fails the build if any source file in
   this workspace so much as names that file, and this shell has no reason to: it
   authenticates with a key file when one is given and with the local agent and the user's
   default keys otherwise. There is no code path here that puts a secret on the wire.

3. **The host key is verified.** Legacy set ``AutoAddPolicy()``, which accepts whatever
   key the far end presents -- on a point-to-point cable that is a small risk, and on a
   forwarded port it is not a risk at all, it is the whole attack. This shell loads the
   user's ``known_hosts`` and keeps paramiko's default ``RejectPolicy``, so a first
   connection is an explicit act by the user and a *changed* key arrives as
   ``BadHostKeyException`` and therefore as ``DeviceAuthFailed``, which is exactly what it
   is.

4. **Bytes in, bytes out -- no temporary files.** Legacy ``get_file``/``put_file`` took
   ``Path`` arguments, and the legacy pusher wrote each sidecar to a
   ``tempfile.NamedTemporaryFile`` and then SFTP-``put`` it, one temp file and one round
   trip per sidecar per document (``device/sync.py``). ``ports/device.py`` says no port
   touches the filesystem, so :meth:`RemoteShell.read_file` returns ``bytes`` and
   :meth:`RemoteShell.write_file` accepts them; the CLI owns every sink. One SFTP channel
   is opened per connection and paramiko pipelines over it, so a per-file call is not a
   per-file handshake.

5. **A short write is reported, not assumed away.**
   :attr:`~rmspec.domain.ports.device.UploadReceipt.byte_count` is documented as equal to
   the payload's length whenever a receipt exists, so somebody has to check. The shell is
   the only layer that knows how many bytes landed, so :meth:`ParamikoShell.write_file`
   stats the path after writing and raises ``DeviceTransferInterrupted`` on a mismatch.
   That costs one extra round trip per write and buys the receipt's only substantive
   promise.

6. **``list_dir`` is ``ls -A`` over the exec channel, not SFTP ``listdir``.** It keeps the
   design's command table literally true, and a non-zero exit status is a *better* per-path
   signal than an ``errno``: an ``ls`` that ran and failed proves the session is alive and
   the path is the problem. The cost is that output is newline-framed, so a filename
   containing a newline would be reported as two entries; every name in the xochitl store is
   a uuid plus a suffix this package spells, so that shape cannot arise there.

7. **``close`` suppresses three types, not ``Exception``.** Legacy used
   ``contextlib.suppress(Exception)`` twice. A close that fails cannot be acted on and
   must not mask the error that prompted it, which is an argument for suppressing
   *transport* failures -- not for suppressing a bug in this module.

One path, or the whole session: the distinction the ports depend on
------------------------------------------------------------------
``ports/device.py`` states the rule this section exists to keep: *per-entry failure is data;
whole-transport failure raises*. A catalog walk over the reference store is 42 sequential
reads, so a cable pulled **during** the walk is the common case rather than the edge -- and
if a refused read and a dead session are the same typed error, the catalog has no choice but
to convert both into ``SkippedEntry``, and a disconnect returns a shrunken library with a
success exit status.

An earlier revision of this module did exactly that: :meth:`RemoteShell.read_file` documented
``DeviceUnreachable`` as covering "the path could not be opened", because
:func:`~rmspec.device._errors.translate_ssh` folded every ``OSError`` into it. That is fixed
in two halves.

* ``_errors`` grew :data:`~rmspec.device._errors.PATH_FAILURES` and an arm ahead of its
  ``OSError`` arm, so a per-path ``errno`` no longer reads as an unreachable tablet even for
  a caller that wants a domain error.
* :class:`PathUnreadableError` is raised instead by the two methods whose callers may have a
  per-entry answer. It is **not** a domain error and it must never leave this package.
  That is sound because :class:`RemoteShell` is an *internal* Protocol rather than a port:
  ``ports/device.py`` constrains what ``rmspec.device`` raises at its public surface, and
  this seam is two modules inside it, free to speak a narrower vocabulary that the port's
  error tree has no member for. ``rmspec.device.ssh`` catches it at every one of the six
  places it can arise and converts -- to a ``SkippedEntry`` where an entry-level answer
  exists, and to ``DeviceProtocolError`` where the store has contradicted itself.

Everything else still goes through the translator untouched, including
:meth:`ParamikoShell.write_file`: an uploader has no per-path answer to give, so it wants a
domain error, and thanks to the new arm a write into a directory that does not exist is now
``DeviceProtocolError`` rather than "cannot reach the tablet".
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Final, Protocol

import paramiko
from paramiko.ssh_exception import SSHException

from rmspec.device._errors import PATH_FAILURES, command_failed, translate_ssh
from rmspec.device.addresses import RemoteCommand
from rmspec.domain.errors import (
    DeviceError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    TransportKind,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rmspec.device.addresses import Endpoint, RemotePath

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USER",
    "LIST_DIR_TEMPLATE",
    "ParamikoShell",
    "PathUnreadableError",
    "RemoteShell",
]

#: The only account firmware 3.27.3.0 offers over SSH. A parameter rather than a constant
#: at the call site so a tunnel to something else is expressible, but this is the value.
DEFAULT_USER: Final = "root"

#: How long a connect, a command or a transfer may stall before it is a failure. Fifteen
#: seconds: the USB link is point-to-point, so the only thing a longer wait buys is a
#: hung command line.
DEFAULT_TIMEOUT_SECONDS: Final = 15.0

#: The listing command, from the design's BusyBox-safe table. ``-A`` includes dotfiles and
#: excludes ``.`` and ``..``; no GNU long option, because the device's ``ls`` is BusyBox
#: 1.36.1.
LIST_DIR_TEMPLATE: Final = "ls -A {}"

#: What a caller did wrong when a method is reached before :meth:`ParamikoShell.connect`.
_NOT_CONNECTED: Final = "the shell is not connected; call connect() before using it"

#: The exception types a ``paramiko`` call can produce. ``SSHException`` is the root of
#: thirteen of the fourteen paramiko exception types; ``NoValidConnectionsError`` is the
#: fourteenth and sits on ``OSError``, which also covers ``socket.timeout``. ``EOFError``
#: is what a transport that died mid-frame surfaces as, and ``UnicodeDecodeError`` is
#: stdout that is not text. Every one of them is handed to
#: :func:`~rmspec.device._errors.translate_ssh`, which is total.
_TRANSPORT_FAILURES: Final = (SSHException, OSError, EOFError, UnicodeDecodeError)


class PathUnreadableError(Exception):
    """One path the session could see and could not read. Package-private, by design.

    Raised by :meth:`RemoteShell.read_file` and :meth:`RemoteShell.list_dir`, and by nothing
    else. It exists because the two failures a caller must tell apart -- "this one file was
    refused" and "the session is gone" -- both arrive from ``paramiko`` as an ``OSError``,
    and a caller handed one typed domain error for both has to treat every read failure as
    per-entry. That is what let a mid-walk disconnect return a shrunken library instead of
    raising; see the module docstring.

    Not a ``rmspec.domain.errors.DeviceError``, and deliberately so. The port's error tree
    has no member for "one path, and the caller may have a per-entry answer" -- adding one
    would put a transport-internal distinction into the domain vocabulary every adapter and
    fake has to implement. So the distinction lives here, in a Protocol that is internal to
    ``rmspec.device``, and ``rmspec.device.ssh`` converts it into a
    ``SkippedEntry`` or a ``DeviceProtocolError`` at every point it can surface. **Nothing
    in this package's public surface may let it escape**, and
    ``tests/test_device_ssh.py`` asserts that against every port method rather than trusting
    it.
    """

    def __init__(self, *, path: str, detail: str) -> None:
        super().__init__(f"{path} could not be read: {detail}")
        self.path = path
        """The path that could not be read, for the ``route`` of a converted error."""
        self.detail = detail
        """Why, in the transport's own words. Displayed and logged, never matched."""


class RemoteShell(Protocol):
    """The four operations ``rmspec.device.ssh`` performs against a tablet.

    Structural, so :class:`ParamikoShell` satisfies it without inheriting and an in-memory
    double satisfies it without importing ``paramiko``. Implementations raise
    ``rmspec.domain.errors.DeviceError`` subclasses and -- from the two read methods only --
    :class:`PathUnreadableError`. An implementation that let a raw ``OSError`` escape would
    put the classification burden back on every adapter, which is the defect this Protocol
    exists to remove.

    Presence is decided by :meth:`list_dir` and nowhere else. Callers list a directory once
    and read only the paths that listing named, so an optional sidecar that is absent costs
    no failed read -- the same rule ``rmspec.formats.repository`` adopted, and for the same
    reason: on a case-insensitive filesystem, deciding presence from a listing in one place
    and from an open in another makes the two disagree.
    """

    def run(self, command: RemoteCommand, /) -> str:
        """Run one command on the device and return its standard output.

        Parameters
        ----------
        command
            The command, already assembled by
            :meth:`~rmspec.device.addresses.RemoteCommand.of` with every argument quoted.

        Returns
        -------
        str
            Everything the command wrote to stdout, decoded as UTF-8 and otherwise
            untouched -- no stripping, no splitting, because a caller that wants lines
            says so.

        Raises
        ------
        DeviceProtocolError
            The command ran and exited non-zero, or the session misbehaved.
        DeviceUnreachable
            No session could be opened, or one stalled.
        DeviceAuthFailed
            The device refused the credentials, or its host key changed.
        """
        ...

    def read_file(self, path: RemotePath, /) -> bytes:
        """Read one whole file from the device.

        Parameters
        ----------
        path
            The file to read.

        Returns
        -------
        bytes
            The file's contents. A zero-length file reads as ``b""``; deciding what that
            means is the caller's business.

        Raises
        ------
        PathUnreadableError
            This one path could not be opened -- it is absent, or access to it was refused.
            Never confused with a failure of the session: a caller that has a per-entry
            answer catches this, and everything below propagates.
        DeviceProtocolError
            The read failed in a way the transport could describe.
        DeviceUnreachable
            No session could be opened, or a read stalled.
        DeviceAuthFailed
            The device refused the credentials, or its host key changed.
        """
        ...

    def list_dir(self, path: RemotePath, /) -> tuple[str, ...]:
        """List the names one directory holds.

        Parameters
        ----------
        path
            The directory to list.

        Returns
        -------
        tuple[str, ...]
            Every entry name, without ``.`` or ``..`` and without a leading path. Bare
            names, so a caller composes them with
            :meth:`~rmspec.device.addresses.RemotePath.child` rather than string
            arithmetic.

        Raises
        ------
        PathUnreadableError
            This one directory could not be listed -- it does not exist, or access was
            refused.
        DeviceProtocolError
            The listing failed in a way the transport could describe.
        DeviceUnreachable
            No session could be opened, or one stalled.
        DeviceAuthFailed
            The device refused the credentials, or its host key changed.
        """
        ...

    def write_file(self, path: RemotePath, data: bytes, /) -> None:
        """Write one whole file to the device, replacing anything already there.

        Parameters
        ----------
        path
            Where to write. Its parent directory must already exist.
        data
            The complete contents.

        Raises
        ------
        DeviceTransferInterrupted
            Fewer bytes than were offered arrived. The implementation confirms the size
            rather than trusting the write, because
            :attr:`~rmspec.domain.ports.device.UploadReceipt.byte_count` promises the
            payload's full length whenever a receipt exists.
        DeviceProtocolError
            The write failed in a way the transport could describe.
        DeviceUnreachable
            No session could be opened, or one stalled.
        DeviceAuthFailed
            The device refused the credentials, or its host key changed.
        """
        ...


class ParamikoShell:
    """A :class:`RemoteShell` over one ``paramiko`` session and one SFTP channel.

    Satisfies :class:`RemoteShell` structurally. The session is opened by
    :meth:`connect` and closed by :meth:`close`; every other method requires the former and
    raises ``DeviceUnreachable`` rather than reconnecting, because an implicit reconnect
    turns one handshake per command into a per-call cost that nothing measures.

    Parameters
    ----------
    endpoint
        Where to connect. :class:`~rmspec.device.addresses.Endpoint` defaults its port to
        the *web API's*, so a caller building one for this shell passes
        ``port=SSH_PORT``. No substitution happens here: silently rewriting a port a caller
        chose is how a tunnel ends up pointed somewhere it was not aimed.
    user
        The account to authenticate as. Defaults to :data:`DEFAULT_USER`.
    key_path
        Path to a private key file, or ``None`` to use the local agent and the user's
        default keys. Never a passphrase and never a password -- see the module docstring.
    timeout
        Seconds a connect, command or transfer may stall.
    """

    def __init__(
        self,
        *,
        endpoint: Endpoint,
        user: str = DEFAULT_USER,
        key_path: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint
        self._user = user
        self._key_path = key_path
        self._timeout = timeout
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        """Open the session and its SFTP channel.

        Authenticates with :attr:`key_path` when one was given, and otherwise with the
        local agent and the user's default keys. No password is offered under any
        circumstance, so an account that has only one is simply unreachable from here.

        Raises
        ------
        DeviceAuthFailed
            The device refused the credentials, or the host key is not the one
            ``known_hosts`` records.
        DeviceUnreachable
            Nothing answered at the endpoint, or the handshake stalled.
        DeviceProtocolError
            The far end answered and did not speak SSH.
        """
        client = paramiko.SSHClient()
        try:
            with self._translated():
                client.load_system_host_keys()
                client.connect(
                    hostname=self._endpoint.host,
                    port=self._endpoint.port,
                    username=self._user,
                    key_filename=self._key_path,
                    allow_agent=True,
                    look_for_keys=True,
                    timeout=self._timeout,
                )
                sftp = client.open_sftp()
        except DeviceError:
            self._shut(client)
            raise
        self._client = client
        self._sftp = sftp

    def close(self) -> None:
        """Close the SFTP channel and the session, if they are open.

        Idempotent, and safe on a shell that never connected. A failure to close is
        suppressed: it cannot be acted on, and letting it propagate would replace whatever
        error prompted the close.
        """
        handles = (self._sftp, self._client)
        self._sftp = None
        self._client = None
        for handle in handles:
            self._shut(handle)

    def run(self, command: RemoteCommand, /) -> str:
        """Run one command on the device and return its standard output.

        Parameters
        ----------
        command
            The command, already quoted by
            :meth:`~rmspec.device.addresses.RemoteCommand.of`.

        Returns
        -------
        str
            Standard output, decoded as UTF-8 and otherwise verbatim.

        Raises
        ------
        DeviceProtocolError
            The command exited non-zero, or the session misbehaved. A non-zero status is
            data on the channel rather than an exception, which is why it is the one SSH
            failure that does not arrive through the translator.
        DeviceUnreachable
            The shell is not connected, or the command stalled.
        DeviceAuthFailed
            The session's credentials were refused.
        """
        exit_status, stdout, stderr = self._exec(command)
        if exit_status != 0:
            raise command_failed(
                command=command.text,
                exit_status=exit_status,
                stderr=stderr,
                endpoint=self._where,
            )
        return stdout

    def read_file(self, path: RemotePath, /) -> bytes:
        """Read one whole file over SFTP.

        Parameters
        ----------
        path
            The file to read.

        Returns
        -------
        bytes
            The file's contents, zero-length included.

        Raises
        ------
        PathUnreadableError
            This path is absent or access to it was refused. Recognised by the ``errno``
            paramiko attaches for exactly those two SFTP status codes -- see
            :data:`~rmspec.device._errors.PATH_FAILURES` -- and raised instead of a domain
            error so a caller with a per-entry answer can give it without also swallowing
            a dead session.
        DeviceUnreachable
            The shell is not connected, or the read stalled.
        DeviceAuthFailed
            The session's credentials were refused.
        DeviceProtocolError
            The channel misbehaved.
        """
        sftp = self._live_sftp()
        with self._readable(path), sftp.open(path.value, "rb") as handle:
            return handle.read()

    def list_dir(self, path: RemotePath, /) -> tuple[str, ...]:
        """List one directory with ``ls -A``.

        Parameters
        ----------
        path
            The directory to list.

        Returns
        -------
        tuple[str, ...]
            One bare name per non-empty output line, in the order ``ls`` produced them.

        Raises
        ------
        PathUnreadableError
            ``ls`` ran and exited non-zero: the directory does not exist, or access was
            refused. A non-zero exit status is a stronger per-path signal than an ``errno``,
            because a command that ran at all proves the session is alive -- so this is the
            one place where the distinction costs nothing to establish.
        DeviceUnreachable
            The shell is not connected, or the command stalled.
        DeviceAuthFailed
            The session's credentials were refused.
        DeviceProtocolError
            The channel misbehaved.
        """
        command = RemoteCommand.of(LIST_DIR_TEMPLATE, path)
        exit_status, stdout, stderr = self._exec(command)
        if exit_status != 0:
            refused = command_failed(
                command=command.text,
                exit_status=exit_status,
                stderr=stderr,
                endpoint=self._where,
            )
            raise PathUnreadableError(path=path.value, detail=refused.got)
        return tuple(line for line in stdout.splitlines() if line)

    def write_file(self, path: RemotePath, data: bytes, /) -> None:
        """Write one whole file over SFTP and confirm its size.

        Parameters
        ----------
        path
            Where to write. The parent directory must exist; this method creates nothing.
        data
            The complete contents.

        Raises
        ------
        DeviceTransferInterrupted
            The file on the device is not the length that was offered, including the case
            where the device reported no size at all. Naming the shortfall is the point:
            a receipt reporting fewer bytes than were sent is forbidden by
            ``ports/device.py``.
        DeviceProtocolError
            The path could not be opened -- most often a parent directory that does not
            exist -- or the channel misbehaved. A domain error and never
            :class:`PathUnreadableError`: an uploader has no per-entry answer to give, so
            what it needs is the classification, and the ``route`` names this path.
        DeviceUnreachable
            The shell is not connected, or the write stalled.
        DeviceAuthFailed
            The session's credentials were refused.
        """
        sftp = self._live_sftp()
        with self._translated(path):
            with sftp.open(path.value, "wb") as handle:
                handle.write(data)
            reported = sftp.stat(path.value).st_size
        landed = reported if isinstance(reported, int) else 0
        if landed != len(data):
            raise DeviceTransferInterrupted(
                transport=TransportKind.SSH,
                subject=path.value,
                bytes_transferred=landed,
                bytes_expected=len(data),
            )

    @property
    def _where(self) -> str:
        """Return the endpoint as one string, for an error that has to name it.

        Returns
        -------
        str
            ``host:port``. Not
            :attr:`~rmspec.device.addresses.Endpoint.base_url`, which spells an HTTP origin
            and would describe the wrong transport.
        """
        return f"{self._endpoint.host}:{self._endpoint.port}"

    @property
    def _key_source(self) -> str | None:
        """Return where credentials came from, for :class:`DeviceAuthFailed`.

        Returns
        -------
        str | None
            The key file's path, or ``None`` when the agent and the user's default keys
            were used. ``None`` is what
            :func:`~rmspec.device._errors.translate_ssh` documents for the agent case, and
            the path is never a secret -- the key it names is.
        """
        return self._key_path

    def _exec(self, command: RemoteCommand, /) -> tuple[int, str, str]:
        """Run one command and return its status and both streams, judging nothing.

        Exists so :meth:`run` and :meth:`list_dir` can draw *different* conclusions from the
        same non-zero exit status -- a domain error for the first, a per-path signal for the
        second -- without either having to catch and re-interpret the other's exception,
        which could not be told apart from a genuine channel failure.

        Parameters
        ----------
        command
            The command, already quoted.

        Returns
        -------
        tuple[int, str, str]
            Exit status, stdout, stderr.

        Raises
        ------
        DeviceError
            The shell is not connected, or the session failed. A non-zero exit status is not
            a failure here: it is the first element of the result.
        """
        client = self._live_client()
        with self._translated():
            _, out, err = client.exec_command(command.text, timeout=self._timeout)
            return (
                out.channel.recv_exit_status(),
                out.read().decode("utf-8"),
                err.read().decode("utf-8"),
            )

    @contextlib.contextmanager
    def _translated(self, path: RemotePath | None = None, /) -> Iterator[None]:
        """Turn any ``paramiko`` failure inside the block into a domain error.

        One place rather than an ``except`` per call site: the clauses would drift, and
        :func:`~rmspec.device._errors.translate_ssh` is total, so there is nothing for a
        second clause to add.

        Parameters
        ----------
        path
            The path the guarded call was pointed at, when it was pointed at one, so a
            per-path protocol error names the file instead of the host.

        Yields
        ------
        None
            Control, for the guarded block.

        Raises
        ------
        DeviceError
            Whatever the translator returned for the failure the block produced.
        """
        try:
            yield
        except _TRANSPORT_FAILURES as exc:
            raise self._as_device_error(exc, path) from exc

    @contextlib.contextmanager
    def _readable(self, path: RemotePath, /) -> Iterator[None]:
        """Guard a read, separating this one path's failure from the session's.

        The per-path clause is first because every member of
        :data:`~rmspec.device._errors.PATH_FAILURES` is an ``OSError`` subclass, so the
        transport clause below would otherwise swallow it. That ordering is the whole
        mechanism; see the module docstring.

        Parameters
        ----------
        path
            The path being read.

        Yields
        ------
        None
            Control, for the guarded block.

        Raises
        ------
        PathUnreadableError
            The failure describes this path: absent, or refused.
        DeviceError
            The failure describes the session.
        """
        try:
            yield
        except PATH_FAILURES as exc:
            raise PathUnreadableError(
                path=path.value,
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        except _TRANSPORT_FAILURES as exc:
            raise self._as_device_error(exc, path) from exc

    def _as_device_error(self, exc: Exception, path: RemotePath | None, /) -> DeviceError:
        """Hand one failure to the translator, which is the only thing that classifies.

        Parameters
        ----------
        exc
            The failure.
        path
            The path the call was pointed at, when it was pointed at one.

        Returns
        -------
        DeviceError
            Whatever :func:`~rmspec.device._errors.translate_ssh` returned. This method adds
            no opinion: a second classifier here could disagree with the first.
        """
        return translate_ssh(
            exc,
            endpoint=self._where,
            user=self._user,
            key_source=self._key_source,
            path=None if path is None else path.value,
        )

    def _live_client(self) -> paramiko.SSHClient:
        """Return the connected client, or refuse.

        Returns
        -------
        paramiko.SSHClient
            The open client.

        Raises
        ------
        DeviceUnreachable
            :meth:`connect` has not run, or :meth:`close` already did. A domain error and
            not the legacy ``ConnectionError``, so the CLI has one tree to catch.
        """
        if self._client is None:
            raise DeviceUnreachable(
                transport=TransportKind.SSH,
                endpoint=self._where,
                detail=_NOT_CONNECTED,
            )
        return self._client

    def _live_sftp(self) -> paramiko.SFTPClient:
        """Return the connected SFTP channel, or refuse.

        Returns
        -------
        paramiko.SFTPClient
            The open channel.

        Raises
        ------
        DeviceUnreachable
            :meth:`connect` has not run, or :meth:`close` already did.
        """
        if self._sftp is None:
            raise DeviceUnreachable(
                transport=TransportKind.SSH,
                endpoint=self._where,
                detail=_NOT_CONNECTED,
            )
        return self._sftp

    @staticmethod
    def _shut(handle: paramiko.SSHClient | paramiko.SFTPClient | None) -> None:
        """Close one handle, ignoring a transport failure while doing so.

        Parameters
        ----------
        handle
            The client or channel to close, or ``None`` when there is none.
        """
        if handle is None:
            return
        with contextlib.suppress(*_TRANSPORT_FAILURES):
            handle.close()
