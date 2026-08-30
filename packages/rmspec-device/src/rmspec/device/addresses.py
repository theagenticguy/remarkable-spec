"""Every address this package can reach, and the two types that keep them safe.

``rmspec.device`` binds four ports to two transports, and this module is the only place
in the workspace that spells the USB endpoint, either port number, the xochitl root, or
any other on-device path. Nothing here performs I/O: an address is data, so a wrong one
is a construction failure rather than a request that goes somewhere unintended.

Relocated from the legacy tree
------------------------------
Replaces ``src/remarkable_spec/device/paths.py`` -- the ``DevicePaths`` class, whose
members were bare ``str`` class attributes -- and the ``10.11.99.1`` literal that legacy
code repeated in ``device/web_api.py``, ``cli/_util.py`` and three ``cli`` command
modules. ``XOCHITL_ROOT`` is legacy ``DevicePaths.XOCHITL_DATA`` under the name the
firmware and ``specs/device/3.27.3.0/filesystem.json`` both use.

Quoting is a type, not a convention
-----------------------------------
The legacy pusher interpolated a remote path into a shell command unquoted three times
-- ``src/remarkable_spec/device/sync.py`` lines 226, 530 and 532, each an
``f"mkdir -p {remote_base}"`` or ``f"touch {remote_base}/{page_uuid}.rm"``. A document
name or uuid carrying a space, a quote or a ``;`` was therefore a command the user did
not write, running as root on their tablet.

Telling the next builder to remember ``shlex.quote`` would not fix that, so the unquoted
form is made **unreachable** instead. :class:`RemotePath` has no ``__str__`` that yields
the bare path -- interpolating one into an f-string produces pydantic's ``value='...'``
repr, which is visibly broken rather than silently dangerous -- and the only way to put
any value into a command is :meth:`RemoteCommand.of`, which quotes every argument it is
given. There is no code path through this module that emits an unquoted argument, which
is why the property is asserted over arbitrary input rather than over a list of the
metacharacters somebody thought of.

The *template* passed to :meth:`RemoteCommand.of` is trusted, and it must stay a literal
written in this package: it is the part that carries the operators. The arguments are the
untrusted part, and they are the part that gets quoted.

Suffixes are appended, never replaced
-------------------------------------
:meth:`RemotePath.with_suffix` appends. It shares only its name with
``pathlib.Path.with_suffix``, which *replaces* the last dot-segment of the stem and would
turn a dotted identifier's ``.metadata`` sidecar into a filename that does not exist --
the same defect ``rmspec.formats.layout`` documents and avoids by explicit
concatenation. Every canonical uuid is dot-free, so the two agree on every real document;
appending is what makes the agreement unconditional.

Two legacy paths are deliberately absent
----------------------------------------
``DevicePaths`` named two files that have no successor here.

The first is the tablet's own configuration file under ``/home/root/.config/remarkable/``,
which holds the developer password and two bearer tokens in cleartext.
``tests/architecture/test_secret_containment.py`` fails the build if any source file in
this workspace so much as names it, so this module does not -- and it does not need to:
identity comes from :data:`OS_RELEASE` and :data:`SOC_MACHINE`, and SSH authentication
comes from the user's own key.

The second is ``/usr/share/remarkable/update.conf``, which legacy
``DevicePaths.UPDATE_CONF`` named as the firmware-version source. It was measured on
2026-08-29 to **not exist** on firmware 3.27.3.0. :data:`OS_RELEASE` replaces it.

Deliberately not modelled
-------------------------
``DevicePaths`` also named the two template directories, the splash-screen directory and
``authorized_keys``. No port in ``rmspec.domain.ports.device`` reads or writes any of
them, and a constant with no consumer is a claim about the device that nothing checks.
They come back if and when a port needs them.
"""

from __future__ import annotations

import posixpath
import shlex
from typing import Final, Self

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "BOOT_ID",
    "CONTENT_SUFFIX",
    "DEFAULT_USB_HOST",
    "METADATA_SUFFIX",
    "OS_RELEASE",
    "PLACEHOLDER",
    "PROC_MEMINFO",
    "SCENE_SUFFIX",
    "SEARCH_INDEX_NAME",
    "SOC_MACHINE",
    "SSH_PORT",
    "WEB_API_PORT",
    "XOCHITL_ROOT",
    "DocumentPaths",
    "Endpoint",
    "RemoteCommand",
    "RemotePath",
    "document_paths",
]

#: The address the tablet answers on over its USB-C network interface. Fixed by the
#: firmware, not discovered: the USB gadget brings up ``10.11.99.0/29`` with the device
#: at ``.1`` and the host at ``.2``.
DEFAULT_USB_HOST: Final = "10.11.99.1"

#: The port the firmware's HTTP API listens on. Plain HTTP: there is no TLS on the USB
#: interface, and the link is a point-to-point cable rather than a network.
WEB_API_PORT: Final = 80

#: The port the device's SSH daemon listens on.
SSH_PORT: Final = 22

#: The directory holding every document in the tablet's library, one set of sidecars per
#: document. Legacy ``DevicePaths.XOCHITL_DATA``.
XOCHITL_ROOT: Final = "/home/root/.local/share/remarkable/xochitl"

#: The file whose ``IMG_VERSION`` line carries the firmware version a user recognises
#: (``3.27.3.0``). Not ``/etc/version``, which holds the build stamp ``20260612085811``.
OS_RELEASE: Final = "/etc/os-release"

#: The file naming the board (``reMarkable Ferrari``). The SoC serial number next to it is
#: a different fact from the serial the tablet UI shows, so it is not named here.
SOC_MACHINE: Final = "/sys/devices/soc0/machine"

#: The kernel's memory report, whose ``MemTotal`` and ``MemAvailable`` lines are in units
#: of 1024 bytes.
PROC_MEMINFO: Final = "/proc/meminfo"

#: A random identifier the kernel mints once per boot and never changes while it is up. It
#: is the only cheap, monotonic evidence available over this transport that the device the
#: second half of an operation is talking to is the same *running system* as the first half:
#: a different value means the tablet rebooted in between.
#:
#: Read with ``cat`` over the exec channel rather than over SFTP -- like :data:`SOC_MACHINE`
#: and :data:`PROC_MEMINFO`, and unlike every path in the xochitl store. Two reasons, and the
#: second is the load-bearing one: a pseudo-file's ``st_size`` is zero while its content is
#: not, so nothing about it is a normal file read; and ``RemoteShell.read_file`` reports a
#: per-path failure as the package-private ``PathUnreadableError``, which every reader must
#: convert, while a command that exits non-zero is already a domain error. Reading this one
#: through a command is what lets the guarded restart in ``rmspec.device.ssh`` speak one
#: failure vocabulary from end to end.
#:
#: Not a credential. It is regenerated on every boot, it identifies no user and no device
#: across boots, and it is exactly what ``journalctl --list-boots`` prints.
BOOT_ID: Final = "/proc/sys/kernel/random/boot_id"

#: Suffix of the sidecar whose presence makes an identifier a document in the store.
METADATA_SUFFIX: Final = ".metadata"

#: Suffix of the sidecar carrying the page order and the layout facts.
CONTENT_SUFFIX: Final = ".content"

#: Suffix of one page's scene artifact, inside the document's own directory.
SCENE_SUFFIX: Final = ".rm"

#: Filename of the tablet's own handwriting search index, one component under
#: :data:`XOCHITL_ROOT`. A whole filename rather than a suffix, because there is exactly one
#: of these per device instead of one per document. Measured 2026-08-29: 503,808 bytes, with
#: no ``-wal`` and no ``-shm`` sibling, so the file is a self-contained database image and a
#: single read of it is a complete one.
SEARCH_INDEX_NAME: Final = "rm-search-index.db"

#: The substring :meth:`RemoteCommand.of` substitutes an argument for. Two characters, so
#: it cannot collide with the ``\\(``, ``$4`` and ``NR==2`` that the BusyBox commands in
#: this package's templates already contain.
PLACEHOLDER: Final = "{}"


class Endpoint(BaseModel, frozen=True, extra="forbid"):
    """Where a transport connects, defaulting to an attached tablet over USB.

    Carried by both transports. ``port`` is validated against the TCP range rather than
    against a set of two, because a user tunnelling either service through a local
    forward is reaching a real tablet by a different number.
    """

    host: str = Field(default=DEFAULT_USB_HOST, min_length=1)
    """The address or name to connect to. Never empty, so a blank setting fails at
    construction rather than producing ``http://``."""

    port: int = Field(default=WEB_API_PORT, ge=1, le=65535)
    """The TCP port. Defaults to the web API's, which is the only one a URL needs."""

    @property
    def base_url(self) -> str:
        """Return the origin every web API route is appended to.

        The default port is omitted, so the common case reads exactly as the firmware's
        own documentation and the user's browser bar do: ``http://10.11.99.1``. Only
        meaningful for the web API transport; an :class:`Endpoint` built for SSH carries
        the same fields and simply never has this asked of it.

        Returns
        -------
        str
            ``http://host``, or ``http://host:port`` when *port* is not the web API's.
            The host is interpolated as given, which is correct for a hostname or an
            IPv4 literal; this project has no IPv6 transport, and bracketing a v6
            literal here would be untested code for a case that cannot arise.
        """
        if self.port == WEB_API_PORT:
            return f"http://{self.host}"
        return f"http://{self.host}:{self.port}"


class RemotePath(BaseModel, frozen=True, extra="forbid"):
    """An absolute POSIX path on the device, which cannot be interpolated unquoted.

    Every path this package sends to the device is one of these. The type exists for two
    guarantees that a ``str`` cannot make:

    * The only way into a shell command is :meth:`RemoteCommand.of`, which quotes. There
      is no accessor that yields the bare value already shell-safe by accident, and
      :attr:`quoted` is the one that yields it shell-safe on purpose.
    * A constructed value is a single absolute path with no ``.`` or ``..`` component, so
      :meth:`child` is the only way to descend and it cannot be undone by a parent that
      already pointed upwards.

    Raises
    ------
    ValueError
        From every construction path -- ``pydantic.ValidationError`` is a ``ValueError``
        -- when the value is not absolute, is empty, ends in ``/`` while naming
        something, contains an empty, ``.`` or ``..`` component, or contains a NUL.
    """

    value: str = Field(min_length=1, pattern=r"^/")
    """The path itself, absolute and normalised at construction."""

    @field_validator("value")
    @classmethod
    def _reject_unnormalised(cls, value: str) -> str:
        """Refuse anything that is not already one plain absolute path.

        Normalising instead would be worse: ``posixpath.normpath`` resolves ``..``
        against the textual parent, so ``/home/root/../etc/passwd`` would be *accepted*
        as ``/etc/passwd`` and :meth:`child`'s containment guarantee would hold over a
        parent that had already escaped. Rejecting keeps the guarantee local.

        Parameters
        ----------
        value
            The candidate path, already known to be non-empty and to start with ``/``.

        Returns
        -------
        str
            *value* unchanged.

        Raises
        ------
        ValueError
            If *value* has a trailing slash while naming something, contains an empty,
            ``.`` or ``..`` component, or contains a NUL byte -- which cannot appear in a
            POSIX filename and would truncate the argument at ``exec``.
        """
        if "\0" in value:
            msg = "a remote path may not contain a NUL byte"
            raise ValueError(msg)
        if value == "/":
            return value
        if value.endswith("/"):
            msg = f"remote path {value!r} has a trailing slash, which would break with_suffix"
            raise ValueError(msg)
        bad = [part for part in value.split("/")[1:] if part in {"", ".", ".."}]
        if bad:
            msg = f"remote path {value!r} is not normalised: it has a {bad[0]!r} component"
            raise ValueError(msg)
        return value

    @classmethod
    def root(cls) -> Self:
        """Build the xochitl root, which is where every document path starts.

        Returns
        -------
        Self
            :data:`XOCHITL_ROOT`.
        """
        return cls(value=XOCHITL_ROOT)

    @classmethod
    def absolute(cls, value: str, /) -> Self:
        """Build a path from a literal spelled in this package.

        The named constructor rather than the field: a caller writing
        ``RemotePath.absolute(OS_RELEASE)`` is stating that the value is a path this
        package knows, which is the only place a raw string is allowed to become one.

        Parameters
        ----------
        value
            An absolute POSIX path.

        Returns
        -------
        Self
            The path.

        Raises
        ------
        ValueError
            If *value* is not one plain absolute path. See the class docstring.
        """
        return cls(value=value)

    def child(self, name: str, /) -> Self:
        """Descend exactly one level.

        The only way to build a longer path, and it cannot produce one outside *self*.
        That is what makes a document uuid or a page id from the wire safe to use as a
        path component without the caller inspecting it first.

        Parameters
        ----------
        name
            One path component: a document uuid, a page id, or a sidecar filename.

        Returns
        -------
        Self
            ``self/name``.

        Raises
        ------
        ValueError
            If *name* is empty, contains ``/``, is ``.`` or ``..``, or begins with ``-``.
            A leading ``-`` is refused because BusyBox would read the quoted argument as
            an option: ``ls -A -rf`` is a different command from ``ls -A ./-rf``, and
            quoting does not change that.
        """
        if not name:
            msg = "a path component may not be empty"
            raise ValueError(msg)
        if "/" in name:
            msg = f"path component {name!r} contains a separator, so it is not one component"
            raise ValueError(msg)
        if name in {".", ".."}:
            msg = f"path component {name!r} does not name a child"
            raise ValueError(msg)
        if name.startswith("-"):
            msg = f"path component {name!r} would be read as an option by the remote command"
            raise ValueError(msg)
        return type(self)(value=posixpath.join(self.value, name))

    def with_suffix(self, suffix: str, /) -> Self:
        """Append a suffix to the last component, never replacing one.

        Named for ``pathlib.Path.with_suffix`` and deliberately unlike it: that method
        replaces the last dot-segment of the stem, so a dotted identifier's ``.metadata``
        sidecar would come out as a filename that does not exist -- a document that reads
        as absent. See the module docstring.

        Parameters
        ----------
        suffix
            The text to append, including its leading dot: ``".metadata"``, not
            ``"metadata"``. Matching the ``*_SUFFIX`` constants and
            ``rmspec.formats.layout``, so the same literal spells the same file on both
            sides of the transport.

        Returns
        -------
        Self
            ``self`` with *suffix* appended.

        Raises
        ------
        ValueError
            If *suffix* is empty or contains ``/``, either of which would name a
            different file rather than the same one with a suffix.
        """
        if not suffix:
            msg = "a suffix may not be empty"
            raise ValueError(msg)
        if "/" in suffix:
            msg = f"suffix {suffix!r} contains a separator, so it would name another file"
            raise ValueError(msg)
        return type(self)(value=f"{self.value}{suffix}")

    @property
    def quoted(self) -> str:
        """Return the path as one shell word.

        Returns
        -------
        str
            ``shlex.quote`` of the value. The single accessor that yields something
            interpolable, and it yields the *safe* form -- so there is nothing to
            remember at the call site.
        """
        return shlex.quote(self.value)


class RemoteCommand(BaseModel, frozen=True, extra="forbid"):
    """A shell command whose every interpolated value is already quoted.

    ``RemoteShell.run`` accepts nothing else, so no adapter in this package can send a
    command it assembled with an f-string. Construct with :meth:`of`; the field is
    public only because a frozen model has no way to hide it, and constructing one
    directly is exactly as unsafe as the legacy code was.
    """

    text: str = Field(min_length=1)
    """The command as it will be sent, with every argument already a single shell word."""

    @classmethod
    def of(cls, template: str, /, *args: RemotePath | str) -> Self:
        """Build a command by substituting quoted arguments into a literal template.

        Parameters
        ----------
        template
            The command, with one :data:`PLACEHOLDER` where each argument goes. Must be a
            literal written in this package: it is the half that carries the operators,
            redirections and ``sed`` scripts, and nothing quotes it.
        *args
            One value per placeholder. A :class:`RemotePath` contributes
            :attr:`RemotePath.quoted`; a ``str`` -- a document uuid, a template name, a
            ``df`` argument -- is passed through ``shlex.quote``. Either way the result is
            one shell word, whatever bytes it contains.

        Returns
        -------
        Self
            The assembled command.

        Raises
        ------
        ValueError
            If the number of placeholders and the number of arguments differ, which is
            the one way a caller could otherwise leave a literal ``{}`` in a command or
            silently drop an argument. Also if *template* is empty.
        """
        literals = template.split(PLACEHOLDER)
        holes = len(literals) - 1
        if holes != len(args):
            msg = (
                f"template has {holes} {PLACEHOLDER} placeholder(s) "
                f"and {len(args)} argument(s) were given"
            )
            raise ValueError(msg)
        quoted = (arg.quoted if isinstance(arg, RemotePath) else shlex.quote(arg) for arg in args)
        parts = [literals[0]]
        for value, literal in zip(quoted, literals[1:], strict=True):
            parts.append(value)
            parts.append(literal)
        return cls(text="".join(parts))


class DocumentPaths(BaseModel, frozen=True, extra="forbid"):
    """Where one document's artifacts live on the device.

    Built by :func:`document_paths`, which is the only place the sidecar suffixes are
    applied. The three fields are the artifacts every document has; the two methods build
    the ones whose names depend on data -- a page id, a file type -- and each validates
    that data as a path component on the way.
    """

    metadata: RemotePath
    """``ROOT/DOC.metadata``. Its presence is what makes ``DOC`` a document, which is why
    the uploader writes it last."""

    content: RemotePath
    """``ROOT/DOC.content``, carrying the ``cPages`` page order."""

    page_dir: RemotePath
    """``ROOT/DOC``, the directory holding one scene artifact per annotated page. Also the
    stem the sidecar and underlay names are built from."""

    def page(self, page_id: str, /) -> RemotePath:
        """Locate one page's scene artifact.

        Parameters
        ----------
        page_id
            The page's identifier, exactly as the ``.content`` page order spells it.

        Returns
        -------
        RemotePath
            ``ROOT/DOC/PAGE.rm``.

        Raises
        ------
        ValueError
            If *page_id* is not usable as one path component. See
            :meth:`RemotePath.child`.
        """
        return self.page_dir.child(page_id).with_suffix(SCENE_SUFFIX)

    def underlay(self, suffix: str, /) -> RemotePath:
        """Locate the source file a PDF or EPUB document annotates.

        Parameters
        ----------
        suffix
            The bare file type as ``.content`` spells it: ``"pdf"`` or ``"epub"``, never
            ``".pdf"``. Undotted deliberately -- the dot is added here rather than
            stripped if present, so there is no normalisation step that could accept two
            spellings of one thing and hide a caller's confusion.

        Returns
        -------
        RemotePath
            ``ROOT/DOC.pdf`` or ``ROOT/DOC.epub``.

        Raises
        ------
        ValueError
            If *suffix* is empty, contains ``/``, or begins with ``.``.
        """
        if not suffix:
            msg = "a file type may not be empty"
            raise ValueError(msg)
        if suffix.startswith("."):
            msg = f"file type {suffix!r} is dotted; pass the bare type, as .content spells it"
            raise ValueError(msg)
        return self.page_dir.with_suffix(f".{suffix}")


def document_paths(root: RemotePath, doc_uuid: str, /) -> DocumentPaths:
    """Locate one document's artifacts under a xochitl root.

    The single place the sidecar suffixes are applied to a uuid, and the single place a
    uuid from the wire becomes a path -- so :meth:`RemotePath.child` validates every one
    of them exactly once.

    Parameters
    ----------
    root
        The xochitl root, normally :meth:`RemotePath.root`. A parameter rather than the
        constant so a test can build a tree anywhere and so a future mirror transport
        needs no second copy of this function.
    doc_uuid
        The document's identifier, exactly as the store spells it.

    Returns
    -------
    DocumentPaths
        The three artifacts every document has, plus the two builders for the ones whose
        names depend on data. Nothing is probed: no path in this module touches a device.

    Raises
    ------
    ValueError
        If *doc_uuid* is not usable as one path component. See :meth:`RemotePath.child`.
    """
    stem = root.child(doc_uuid)
    return DocumentPaths(
        metadata=stem.with_suffix(METADATA_SUFFIX),
        content=stem.with_suffix(CONTENT_SUFFIX),
        page_dir=stem,
    )
