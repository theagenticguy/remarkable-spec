"""The one place a transport failure becomes a domain error.

Every ``httpx``, ``paramiko``, ``socket`` and ``OSError`` failure in this package passes
through one of the four functions here, and none of them escapes. That is the invariant
``rmspec.domain.errors.DeviceError`` opens by stating, and this module is where it is
enforced -- not spread across the adapters, because an adapter that translates inline
grows one ``except`` clause per call site and the clauses drift.

Pure functions, and why that matters
------------------------------------
Nothing here holds a client, opens a session, or reads a response. Each function is a
function of its arguments alone -- an exception object, or a status plus a body -- and
returns the error rather than raising it, so the ``raise`` stays at the call site where
the traceback is useful. The reason is testability: the branches for exception types a
fake transport will never produce (a host key that changed, an SSH message arriving out
of order) are reached by constructing the exception and calling the translator, which is
only possible because there is nothing else to stand up first. The alternative -- a
translation method on the transport -- would leave those branches uncoverable, and an
uncovered translation branch is a bare ``raise`` waiting to reach the CLI.

Relocated from the legacy tree
------------------------------
Replaces ``response.raise_for_status()`` at seven call sites in
``src/remarkable_spec/device/web_api.py``, which let ``httpx.HTTPStatusError`` reach the
CLI and threw the device's own ``{"error": ...}`` message away; the
``except Exception as exc`` at ``device/connection.py`` line 112; and the bare
``RuntimeError(f"Command failed (exit {exit_code}): ...")`` at ``connection.py`` line 162,
which is now :func:`command_failed`. There is no bare ``except Exception`` in this
package.

Classification is by message, never by status code
--------------------------------------------------
Firmware 3.27.3.0 answers an unknown document id with **HTTP 500** and
``{"error": "Unknown file"}``, answers a *routed* request for a missing entry with **400**
``{"error": "No such entry"}``, answers a directory read with **404**, and ignores the
request method entirely. The status code therefore carries almost no information and the
body carries all of it, which is why :func:`translate_http` derives the class from the
message and keeps *status* only as evidence inside the error's ``got``.

The vocabulary is a lower bound, not a closed set
-------------------------------------------------
Nine messages are measured -- eight from live probes and ``"No file sent"`` from the
firmware's own web bundle, recorded in ``specs/device/3.27.3.0/http.json`` claim 15. That
claim's own history is the argument for treating the list as open: a previous pass
asserted a closed five-value vocabulary and probing the ``download`` and ``thumbnail``
argument spaces produced four more strings in one session.

So an unrecognised message is a first-class outcome, not a lookup miss. It becomes
:class:`~rmspec.domain.errors.DeviceProtocolError` carrying the message verbatim, which
is the honest report -- the device answered, in its own error shape, something this
adapter has not met -- and it is what a ``dict[str, type[DeviceError]]`` lookup would
have turned into a ``KeyError`` from inside an exception handler. A body that is not JSON,
or JSON without an ``"error"`` key, lands in the same place: that is what a captive
portal or an intercepting proxy on the USB interface produces, and it is a protocol
violation rather than a device fault.

Which messages mean "no document"
---------------------------------
Three of the nine do, and only one of them looks like it.

``"No such entry"``
    The id is not in the store.
``"Can only download documents"``
    The id names a folder. ``ports/device.py`` says an identifier in
    ``DeviceListing.folders`` raises ``DeviceDocumentNotFound``, so a folder id reaching
    a download route resolves to the same class rather than to a protocol error: the
    request was well-formed and the document does not exist.
``"Unable to get thumbnail for entry"``
    An unknown id on the thumbnail route -- the same fact under a different route's
    wording.

``"Unknown file"`` is **not** in that set even though the firmware reports an unknown id
with it, because it is also what an unrouted path returns: it means the request never
reached a handler. That is a client bug -- a route this package spelled wrongly -- and
reporting it as a missing document would send the user looking for their notes.

Per-path failure is not an unreachable tablet, and the arm order is the mechanism
---------------------------------------------------------------------------------
paramiko's SFTP client turns a server status code into an ``OSError``, and for the two
*per-path* codes it supplies an ``errno``: ``SFTP_NO_SUCH_FILE`` becomes
``IOError(errno.ENOENT, ...)`` and ``SFTP_PERMISSION_DENIED`` becomes
``IOError(errno.EACCES, ...)``. ``OSError.__new__`` maps those to ``FileNotFoundError``
and ``PermissionError``. Any other status becomes a bare ``IOError(text)`` with no
``errno`` at all.

That ``errno`` is the **only** signal available for telling "this one file was refused"
from "the cable was pulled", and both arrive as an ``OSError``. So :data:`PATH_FAILURES`
is tested **before** the ``NoValidConnectionsError | OSError`` arm in
:func:`translate_ssh`. Reversed, the base class would swallow the subclasses and one
unreadable sidecar would be reported as a detached tablet -- which is exactly the mistake
that made a mid-walk disconnect look like a library full of unreadable documents instead
of raising. ``rmspec.device._shell`` branches on this same constant to decide whether to
raise its package-private ``PathUnreadableError`` instead of a domain error, so the two
layers agree by construction rather than by convention.

``TimeoutError`` -- and therefore ``socket.timeout`` -- and the ``ConnectionError``
family are deliberately *not* in the set: none of them says anything about a path. Neither
is a status paramiko could not map to an ``errno``, which stays in the transport arm for
the conservative reason: a generic SFTP failure is genuinely ambiguous, and calling it
per-path would let one broken session be reported as forty-two unreadable documents.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import httpx
from paramiko.ssh_exception import (
    AuthenticationException,
    BadHostKeyException,
    NoValidConnectionsError,
    SSHException,
)

from rmspec.domain.errors import (
    DeviceAuthFailed,
    DeviceDocumentNotFound,
    DeviceError,
    DeviceProtocolError,
    DeviceUnreachable,
    DeviceUploadRejected,
    TransportKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "BODY_EXCERPT_LIMIT",
    "DEVICE_ERROR_KEY",
    "NOT_FOUND_MESSAGES",
    "PATH_FAILURES",
    "PATH_UNREADABLE_DIAGNOSIS",
    "PROTOCOL_DIAGNOSES",
    "UNOPENABLE_FILE_PREFIX",
    "UPLOAD_REJECTED_MESSAGES",
    "command_failed",
    "device_message",
    "translate_http",
    "translate_httpx",
    "translate_ssh",
]

#: The single key the firmware's error bodies carry. Uniform across all four observed
#: status codes, which is the one thing about this error contract that is dependable.
DEVICE_ERROR_KEY: Final = "error"

#: The measured messages that mean the tablet holds no such document. See the module
#: docstring for why ``"Unknown file"`` is not one of them.
NOT_FOUND_MESSAGES: Final = frozenset(
    {
        "No such entry",
        "Can only download documents",
        "Unable to get thumbnail for entry",
    }
)

#: The measured messages that mean the device took the connection and refused the payload.
#: One member, from the ``/upload`` handler in the firmware's own web bundle rather than
#: from a probe -- that route is never requested by this package, because a ``GET`` to it
#: could not have been proven non-mutating.
UPLOAD_REJECTED_MESSAGES: Final = frozenset({"No file sent"})

#: Every measured message that means the client addressed the firmware wrongly, mapped to
#: the contract it broke. The value becomes ``DeviceProtocolError.expected``, so each of
#: these reads as a statement about what the request should have been rather than as a
#: restatement of the device's wording.
PROTOCOL_DIAGNOSES: Final[Mapping[str, str]] = {
    "Unknown file": "a path this firmware routes at all",
    "Malformed URL": "a route with every path segment supplied",
    "Missing thumbnail ID": "a route with every path segment supplied",
    "Filetype not supported": "one of the format selectors this route serves",
}

#: The ninth measured message embeds the filesystem path it failed to open, so it is
#: matched by prefix. Observed as ``404`` on a directory under the static asset route.
UNOPENABLE_FILE_PREFIX: Final = "Unable to open file"

#: What the contract was when the firmware could not open a static asset.
UNOPENABLE_FILE_DIAGNOSIS: Final = "a static file this firmware can open"

#: What the contract was when the message is one this adapter has never seen. Named, not
#: silent: the error says the vocabulary fell short, and carries the message so the next
#: probe pass can add it.
UNRECOGNISED_DIAGNOSIS: Final = "one of the error messages measured on this firmware"

#: How many bytes of an unusable body go into the error message. Enough to recognise an
#: HTML login page or an empty body, bounded so a 9 MB log excerpt cannot become the text
#: of an exception.
BODY_EXCERPT_LIMIT: Final = 160

#: The ``OSError`` subclasses that describe **one path** rather than the session carrying
#: it. Every member is one Python raises only when an ``errno`` was supplied, which over
#: SFTP happens for exactly the two per-path status codes paramiko maps. Tested ahead of
#: the ``OSError`` arm in :func:`translate_ssh`; see the module docstring for why the order
#: is the whole mechanism, and note that ``TimeoutError`` and the ``ConnectionError``
#: family are excluded because neither says anything about a path.
PATH_FAILURES: Final = (
    FileNotFoundError,
    PermissionError,
    IsADirectoryError,
    NotADirectoryError,
)

#: What the contract was when a path the transport was pointed at could not be opened. A
#: protocol diagnosis and not an unreachable one: the session answered, and what it
#: answered contradicts whatever named the path.
PATH_UNREADABLE_DIAGNOSIS: Final = "a path this session can open"


def _excerpt(body: bytes, /) -> str:
    """Describe a body that could not be understood, without quoting all of it.

    Parameters
    ----------
    body
        The raw response body.

    Returns
    -------
    str
        The ``repr`` of the body, truncated to :data:`BODY_EXCERPT_LIMIT` bytes with the
        full length named. ``repr`` rather than a decode, because a body that is not JSON
        may not be text either.
    """
    if len(body) <= BODY_EXCERPT_LIMIT:
        return f"the body {body!r}"
    return f"the body {body[:BODY_EXCERPT_LIMIT]!r} (truncated from {len(body)} bytes)"


def _describe(exc: Exception, /) -> str:
    """Name an exception by type and message, for an error's ``detail`` or ``got``.

    Parameters
    ----------
    exc
        The exception being translated.

    Returns
    -------
    str
        ``module.QualName: str(exc)``. The type is included because several of these
        carry an empty message -- an ``OSError`` from SFTP often does -- and the type is
        then the only diagnosis there is.
    """
    kind = type(exc)
    return f"{kind.__module__}.{kind.__qualname__}: {exc}"


def device_message(body: bytes, /) -> str | None:
    """Extract the device's own error message from a response body.

    Total by construction: every way the body can fail to be the uniform error shape --
    not JSON, not UTF-8, a JSON array or scalar, an object without the key, an object
    whose value is not a string -- returns ``None`` rather than raising, so the caller
    has one condition to handle instead of five.

    Parameters
    ----------
    body
        The raw response body.

    Returns
    -------
    str | None
        The ``{"error": "<message>"}`` string, or ``None`` when the body is not that
        shape.
    """
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    message = decoded.get(DEVICE_ERROR_KEY)
    return message if isinstance(message, str) else None


def _protocol_diagnosis(message: str, /) -> str:
    """State the contract a message says the request broke.

    Parameters
    ----------
    message
        The device's own error string.

    Returns
    -------
    str
        The matching entry in :data:`PROTOCOL_DIAGNOSES`,
        :data:`UNOPENABLE_FILE_DIAGNOSIS` for the one message that embeds a path, or
        :data:`UNRECOGNISED_DIAGNOSIS` for a message this adapter has not measured.
    """
    known = PROTOCOL_DIAGNOSES.get(message)
    if known is not None:
        return known
    if message.startswith(UNOPENABLE_FILE_PREFIX):
        return UNOPENABLE_FILE_DIAGNOSIS
    return UNRECOGNISED_DIAGNOSIS


def translate_http(
    *,
    route: str,
    status: int,
    body: bytes,
    endpoint: str,
    doc_uuid: str | None = None,
) -> DeviceError:
    """Map one failed USB web API response to a domain error.

    Derives the class from the body's message, not from *status*. See the module
    docstring: on this firmware the status code is nearly uninformative and the message
    carries the diagnosis.

    Parameters
    ----------
    route
        The path that was requested, as this package spells it -- ``/documents/``,
        ``/download/{id}/rmdoc``. Becomes ``DeviceProtocolError.route``.
    status
        The HTTP status. Never branched on; carried into the error's ``got`` as evidence,
        so a report of a misclassified response says which code accompanied the message.
    body
        The raw response body, expected to be ``{"error": "<message>"}`` and handled when
        it is not.
    endpoint
        The origin the response came from, for the diagnosis text. The error tree has no
        field for it on these classes -- :class:`~rmspec.domain.errors.DeviceUnreachable`
        is the one that carries an endpoint -- so it goes into the prose, which matters
        when the user reached a tunnel or a second tablet rather than the default host.
    doc_uuid
        The document the request was about, when it was about one. Used as the subject of
        a not-found or rejected error; *route* stands in when the caller named none, so a
        message that identifies a document cannot produce an error that identifies
        nothing.

    Returns
    -------
    DeviceError
        :class:`~rmspec.domain.errors.DeviceDocumentNotFound` for the three messages that
        mean the store holds no such document,
        :class:`~rmspec.domain.errors.DeviceUploadRejected` for the ``/upload`` handler's
        refusal, and :class:`~rmspec.domain.errors.DeviceProtocolError` for everything
        else -- including a message this adapter has never seen and a body that is not the
        error shape at all. Returned, never raised.
    """
    message = device_message(body)
    subject = doc_uuid if doc_uuid is not None else route
    if message is None:
        return DeviceProtocolError(
            transport=TransportKind.USB_WEB_API,
            route=route,
            expected=f'an error body shaped {{"{DEVICE_ERROR_KEY}": "<message>"}}',
            got=f"status {status} from {endpoint} and {_excerpt(body)}",
        )
    if message in NOT_FOUND_MESSAGES:
        return DeviceDocumentNotFound(
            transport=TransportKind.USB_WEB_API,
            document_uuid=subject,
        )
    if message in UPLOAD_REJECTED_MESSAGES:
        return DeviceUploadRejected(
            transport=TransportKind.USB_WEB_API,
            name=subject,
            device_message=message,
        )
    return DeviceProtocolError(
        transport=TransportKind.USB_WEB_API,
        route=route,
        expected=_protocol_diagnosis(message),
        got=f"status {status} from {endpoint} and the message {message!r}",
    )


def translate_httpx(exc: Exception, /, *, endpoint: str) -> DeviceError:
    """Map any exception raised by an ``httpx`` call to a domain error.

    Total: no ``httpx`` type escapes, including the ones this package has no reason to
    provoke. The parameter is typed ``Exception`` rather than ``httpx.HTTPError`` for
    exactly that reason -- the call site wraps a block, not a single call, and a
    translator that only accepted the expected types would leave the unexpected ones to
    reach the CLI raw.

    Parameters
    ----------
    exc
        The exception that came out of the client.
    endpoint
        The origin that was being reached.

    Returns
    -------
    DeviceError
        :class:`~rmspec.domain.errors.DeviceUnreachable` for every
        ``httpx.TransportError`` -- which is the base of ``ConnectError``,
        ``ConnectTimeout``, ``ReadTimeout`` and the rest -- and for every ``OSError``,
        which is what a dead USB interface produces below ``httpx``. Anything else is a
        :class:`~rmspec.domain.errors.DeviceProtocolError`: ``httpx.InvalidURL`` and
        ``httpx.StreamError`` are client-side mistakes rather than an unreachable tablet,
        and so is whatever type this list has not anticipated.
    """
    if isinstance(exc, httpx.TransportError | OSError):
        return DeviceUnreachable(
            transport=TransportKind.USB_WEB_API,
            endpoint=endpoint,
            detail=_describe(exc),
        )
    return DeviceProtocolError(
        transport=TransportKind.USB_WEB_API,
        route=endpoint,
        expected="either a response or a transport failure this adapter classifies",
        got=_describe(exc),
    )


def translate_ssh(
    exc: Exception,
    /,
    *,
    endpoint: str,
    user: str,
    key_source: str | None = None,
    path: str | None = None,
) -> DeviceError:
    """Map any exception raised by a ``paramiko`` call to a domain error.

    Total: no ``paramiko`` type escapes. The order of the checks is load-bearing, because
    ``paramiko.ssh_exception`` is mostly one tree --

    * ``PasswordRequiredException`` derives from ``AuthenticationException``, which
      derives from ``SSHException``, so authentication is tested first or every auth
      failure would read as a protocol failure.
    * :data:`PATH_FAILURES` -- ``FileNotFoundError``, ``PermissionError``,
      ``IsADirectoryError``, ``NotADirectoryError`` -- are ``OSError`` **subclasses**, so
      they are tested before the ``OSError`` arm below. Reversed, the base would swallow
      them and one refused sidecar would be reported as a detached tablet. See the module
      docstring: this ordering is the entire mechanism for telling a per-path failure from
      a transport failure, and both halves arrive as an ``OSError``.
    * ``NoValidConnectionsError`` derives from ``OSError``, **not** from ``SSHException``,
      and is named explicitly even though the ``OSError`` arm would catch it: if a future
      paramiko reparents it under ``SSHException``, the explicit name keeps "no route to
      the tablet" from silently becoming "the tablet broke the protocol".
    * ``socket.timeout`` has been an alias of ``TimeoutError`` since Python 3.10 and is an
      ``OSError``, so the same arm covers an SFTP read that stalled. It is deliberately not
      in :data:`PATH_FAILURES`: a stall says nothing about the path.
    * ``ChannelException``, ``ConfigParseError`` and ``MessageOrderError`` all derive from
      ``SSHException`` and are covered by that arm, which is why they are not listed
      separately -- an ``isinstance`` per leaf would be a branch that cannot be
      distinguished by any assertion.

    Parameters
    ----------
    exc
        The exception that came out of the client, transport, channel or SFTP handle.
    endpoint
        The host being reached, for :class:`~rmspec.domain.errors.DeviceUnreachable`.
    user
        The account authentication was attempted for. Carried, never the secret.
    key_source
        Where the key came from, when a key was used. ``None`` when the agent was used or
        when the failure was not about credentials.
    path
        The path the failing call was pointed at, when it was pointed at one. Becomes the
        ``route`` of a per-path protocol error, so the report names the file rather than the
        host; *endpoint* stands in when the caller named none.

    Returns
    -------
    DeviceError
        :class:`~rmspec.domain.errors.DeviceAuthFailed` when the device refused the
        credentials or the host key changed,
        :class:`~rmspec.domain.errors.DeviceUnreachable` when no session could be opened
        or a read stalled, and :class:`~rmspec.domain.errors.DeviceProtocolError` both for
        one path the session could not open and for a session that misbehaved -- and for any
        type this list has not anticipated, which is reported as such rather than re-raised.
    """
    if isinstance(exc, AuthenticationException | BadHostKeyException):
        return DeviceAuthFailed(
            transport=TransportKind.SSH,
            user=user,
            detail=_describe(exc),
            key_source=key_source,
        )
    if isinstance(exc, PATH_FAILURES):
        return DeviceProtocolError(
            transport=TransportKind.SSH,
            route=endpoint if path is None else path,
            expected=PATH_UNREADABLE_DIAGNOSIS,
            got=_describe(exc),
        )
    if isinstance(exc, NoValidConnectionsError | OSError):
        return DeviceUnreachable(
            transport=TransportKind.SSH,
            endpoint=endpoint,
            detail=_describe(exc),
        )
    if isinstance(exc, SSHException):
        return DeviceProtocolError(
            transport=TransportKind.SSH,
            route=endpoint,
            expected="a session that keeps to the SSH protocol",
            got=_describe(exc),
        )
    return DeviceProtocolError(
        transport=TransportKind.SSH,
        route=endpoint,
        expected="an exception type this adapter classifies",
        got=_describe(exc),
    )


def command_failed(
    *,
    command: str,
    exit_status: int,
    stderr: str,
    endpoint: str,
) -> DeviceProtocolError:
    """Report a remote command that ran and exited non-zero.

    Not a translation of an exception: paramiko reports a non-zero exit status as data on
    the channel, so this is the one failure in the SSH transport that arrives without one.
    A protocol error rather than anything else because every command this package sends is
    one the device is expected to be able to run -- a ``ls`` of the xochitl root, a
    ``sed`` of ``/etc/os-release`` -- so a non-zero status means the userland is not the
    one measured.

    The caller has already established that *exit_status* is non-zero; this function does
    not re-check it, so there is no branch here that a caller bug could reach.

    Parameters
    ----------
    command
        The command as it was sent, already quoted by
        :meth:`~rmspec.device.addresses.RemoteCommand.of`. Becomes ``route``: over SSH the
        command is what was addressed.
    exit_status
        The status the device returned.
    stderr
        Everything the command wrote to stderr. Only the first non-empty line reaches the
        message: BusyBox writes one line per failure, and a multi-line diagnostic is
        usually a shell trace that would bury it.
    endpoint
        The host the command ran on.

    Returns
    -------
    DeviceProtocolError
        Naming the command, the status, the host and the first line of stderr. Returned,
        never raised.
    """
    trimmed = stderr.strip()
    first_line = trimmed.splitlines()[0] if trimmed else "no stderr"
    return DeviceProtocolError(
        transport=TransportKind.SSH,
        route=command,
        expected="exit status 0",
        got=f"exit status {exit_status} from {endpoint}: {first_line}",
    )
