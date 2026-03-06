"""SSH connection management for the reMarkable tablet.

Provides a ``DeviceConnection`` class that wraps paramiko for SSH and SFTP
operations against the reMarkable device over USB networking.

Requires the ``device`` extra: ``pip install remarkable-spec[device]``

The paramiko library is lazily imported -- this module can be imported without
paramiko installed, but calling ``connect()`` will raise ``ImportError`` with
a helpful message if the dependency is missing.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from remarkable_spec.device.paths import DevicePaths

if TYPE_CHECKING:
    import paramiko


def _import_paramiko() -> paramiko:
    """Lazily import paramiko, raising a helpful error if not installed."""
    try:
        import paramiko as _paramiko

        return _paramiko
    except ImportError:
        raise ImportError(
            "paramiko is required for SSH device connections. "
            "Install it with: pip install remarkable-spec[device]"
        ) from None


class DeviceConnection:
    """Manages an SSH connection to a reMarkable tablet.

    Provides methods for command execution, file transfer (get/put), and
    directory listing over SFTP. Supports both password and key-based
    authentication.

    The connection is established lazily on the first call to ``connect()``
    and cleaned up by ``disconnect()`` or the context manager exit.

    Args:
        host: IP address or hostname of the device. Defaults to USB IP.
        user: SSH username. Defaults to ``root``.
        key_path: Path to an SSH private key file. If provided, key-based
            authentication is used.
        password: SSH password. Used if ``key_path`` is not provided.
            The default root password is shown on the device under
            Settings > Help > Copyrights and licenses.

    Usage:
        >>> with DeviceConnection(password="my-password") as conn:
        ...     print(conn.execute("cat /etc/version"))
        ...     conn.get_file("/home/root/.local/share/remarkable/xochitl/...", Path("./local"))

    Raises:
        ImportError: If paramiko is not installed.
        ConnectionError: If the SSH connection fails.
    """

    def __init__(
        self,
        host: str = DevicePaths.USB_IP,
        user: str = "root",
        key_path: Path | None = None,
        password: str | None = None,
    ) -> None:
        self.host = host
        self.user = user
        self.key_path = key_path
        self.password = password
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        """Establish an SSH connection to the device.

        Uses key-based authentication if ``key_path`` is set, otherwise
        falls back to password authentication.

        Raises:
            ImportError: If paramiko is not installed.
            ConnectionError: If the connection cannot be established.
        """
        paramiko = _import_paramiko()

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            connect_kwargs: dict = {
                "hostname": self.host,
                "username": self.user,
                "port": DevicePaths.SSH_PORT,
            }
            if self.key_path is not None:
                connect_kwargs["key_filename"] = str(self.key_path)
            elif self.password is not None:
                connect_kwargs["password"] = self.password
            else:
                # Try agent-based authentication
                connect_kwargs["allow_agent"] = True

            self._client.connect(**connect_kwargs)
            self._sftp = self._client.open_sftp()
        except Exception as exc:
            self._client = None
            self._sftp = None
            raise ConnectionError(
                f"Failed to connect to reMarkable at {self.host}: {exc}"
            ) from exc

    def disconnect(self) -> None:
        """Close the SSH and SFTP connections.

        Safe to call multiple times or when not connected.
        """
        if self._sftp is not None:
            with contextlib.suppress(Exception):
                self._sftp.close()
            self._sftp = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    def _ensure_connected(self) -> None:
        """Verify an active connection exists, raising if not."""
        if self._client is None:
            raise ConnectionError(
                "Not connected to device. Call connect() first or use as a context manager."
            )

    def execute(self, command: str) -> str:
        """Execute a command on the device and return stdout.

        Args:
            command: Shell command to execute on the device.

        Returns:
            The command's standard output as a string.

        Raises:
            ConnectionError: If not connected.
            RuntimeError: If the command exits with a non-zero status.
        """
        self._ensure_connected()
        assert self._client is not None

        _, stdout, stderr = self._client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8")

        if exit_code != 0:
            err = stderr.read().decode("utf-8")
            raise RuntimeError(f"Command failed (exit {exit_code}): {command}\nstderr: {err}")

        return output

    def get_file(self, remote: str, local: Path) -> None:
        """Download a file from the device via SFTP.

        Args:
            remote: Absolute path on the device.
            local: Local destination path. Parent directories will be created.

        Raises:
            ConnectionError: If not connected.
            FileNotFoundError: If the remote file does not exist.
        """
        self._ensure_connected()
        assert self._sftp is not None

        local.parent.mkdir(parents=True, exist_ok=True)
        self._sftp.get(remote, str(local))

    def put_file(self, local: Path, remote: str) -> None:
        """Upload a file to the device via SFTP.

        Args:
            local: Local file path to upload.
            remote: Absolute destination path on the device.

        Raises:
            ConnectionError: If not connected.
            FileNotFoundError: If the local file does not exist.
        """
        self._ensure_connected()
        assert self._sftp is not None

        if not local.exists():
            raise FileNotFoundError(f"Local file not found: {local}")

        self._sftp.put(str(local), remote)

    def list_dir(self, path: str) -> list[str]:
        """List directory contents on the device.

        Args:
            path: Absolute directory path on the device.

        Returns:
            List of filenames (not full paths) in the directory.

        Raises:
            ConnectionError: If not connected.
        """
        self._ensure_connected()
        assert self._sftp is not None

        return self._sftp.listdir(path)

    def __enter__(self) -> DeviceConnection:
        """Connect to the device on context manager entry."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Disconnect from the device on context manager exit."""
        self.disconnect()
