"""Every transport failure becomes a domain error, and nothing else escapes.

Three claims are pinned here.

The class follows the **body**, not the status code. Firmware 3.27.3.0 answers an unknown
document id with HTTP 500, a routed miss with 400 and a directory read with 404, and
ignores the request method -- so a translator that switched on status would report a
missing document as a broken contract and vice versa. The same message is therefore
asserted to produce the same class across five different status codes.

The measured vocabulary is a **lower bound**. All nine strings recorded in
``specs/device/3.27.3.0/http.json`` claim 15 are classified, and a string that is not one
of them still produces a typed error carrying the message verbatim -- not a ``KeyError``
from inside an exception handler, and not a silent default that would hide the next
firmware's new wording.

The translators are **total**. Every ``paramiko`` type on the 4.0.0 surface, plus
``socket.timeout``, plus a bare ``OSError`` from SFTP, plus an exception nobody
anticipated, is mapped rather than re-raised. These are the branches a fake transport
cannot reach, which is the whole reason ``_errors`` is a pure function of its inputs.

**One path is not the whole session, and the arm order is what says so.** Every member of
``PATH_FAILURES`` is an ``OSError`` *subclass*, so an arm placed after the ``OSError`` arm
would never run and a single refused sidecar would be classified as a detached tablet --
which is exactly what let a mid-walk disconnect return a shrunken library instead of
raising. The ordering is therefore asserted directly, along with the exclusions:
``TimeoutError`` and the ``ConnectionError`` family say nothing about a path and must stay
in the transport arm.
"""

from __future__ import annotations

import errno
import json
import socket

import httpx
import pytest
from paramiko import ssh_exception
from paramiko.ssh_exception import (
    AuthenticationException,
    BadHostKeyException,
    ChannelException,
    ConfigParseError,
    MessageOrderError,
    NoValidConnectionsError,
    PasswordRequiredException,
    SSHException,
)

from rmspec.device._errors import (
    BODY_EXCERPT_LIMIT,
    DEVICE_ERROR_KEY,
    NO_FILE_SENT,
    NO_FILE_SENT_DIAGNOSIS,
    NOT_FOUND_MESSAGES,
    PATH_FAILURES,
    PATH_UNREADABLE_DIAGNOSIS,
    PROTOCOL_DIAGNOSES,
    UNOPENABLE_FILE_DIAGNOSIS,
    UNRECOGNISED_DIAGNOSIS,
    UPLOAD_REJECTED_MESSAGES,
    command_failed,
    device_message,
    translate_http,
    translate_httpx,
    translate_ssh,
    translate_upload,
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

ENDPOINT = "http://10.11.99.1"
HOST = "10.11.99.1:22"
ROUTE = "/download/{id}/rmdoc"
UPLOAD_ROUTE = "/upload"
DOC = "b8ff2c3d-0a1e-4f77-9c21-6a0e5d4b7f10"
NAME = "Design review.pdf"
USER = "root"

#: The nine error strings measured on firmware 3.27.3.0, with the status each was observed
#: under and the domain class :func:`translate_http` must produce for it. "No file sent" was
#: read out of the firmware's own web bundle first and became a live measurement on 2026-08-29,
#: when POST /upload was probed; :func:`translate_upload` classifies it differently on the
#: write route, and the test for that pair says why.
MEASURED: list[tuple[str, int, type[DeviceError]]] = [
    ("No such entry", 400, DeviceDocumentNotFound),
    ("Can only download documents", 400, DeviceDocumentNotFound),
    ("Unable to get thumbnail for entry", 500, DeviceDocumentNotFound),
    ("Unknown file", 500, DeviceProtocolError),
    ("Malformed URL", 400, DeviceProtocolError),
    ("Missing thumbnail ID", 400, DeviceProtocolError),
    ("Filetype not supported", 400, DeviceProtocolError),
    ("Unable to open file /usr/share/remarkable/webui/assets", 404, DeviceProtocolError),
    ("No file sent", 400, DeviceUploadRejected),
]

#: Bodies that are not the uniform ``{"error": "<message>"}`` shape. A captive portal, an
#: intercepting proxy and a firmware that changed its mind all land here.
UNUSABLE_BODIES: list[bytes] = [
    b"",
    b"<html><body>Sign in to the network</body></html>",
    b"\xff\xfe\x00garbage",
    b"[1, 2, 3]",
    b'"just a string"',
    b"5",
    b"null",
    b'{"detail": "no error key"}',
    b'{"error": 5}',
    b'{"error": null}',
    b'{"error": ["No such entry"]}',
]


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


def error_body(message: str, /) -> bytes:
    """Build the uniform error body the firmware sends.

    Parameters
    ----------
    message
        The device's own error string.

    Returns
    -------
    bytes
        ``{"error": "<message>"}`` as UTF-8, built with ``json.dumps`` so the fixture
        cannot disagree with the decoder about escaping.
    """
    return json.dumps({DEVICE_ERROR_KEY: message}).encode()


# ─────────────────────────── device_message ───────────────────────────


def test_the_uniform_error_body_yields_its_message():
    assert device_message(error_body("No such entry")) == "No such entry"


def test_a_message_with_characters_that_need_escaping_survives():
    assert device_message(error_body('a "quoted" \\ message')) == 'a "quoted" \\ message'


@pytest.mark.parametrize("body", UNUSABLE_BODIES, ids=lambda raw: repr(raw)[:32])
def test_a_body_that_is_not_the_error_shape_yields_none_rather_than_raising(body: bytes):
    # Five different failures -- not JSON, not UTF-8, not an object, no key, wrong value
    # type -- collapse to one condition, so the caller has one branch instead of five.
    assert device_message(body) is None


# ─────────────────────────── translate_http ───────────────────────────


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    MEASURED,
    ids=[message for message, _, _ in MEASURED],
)
def test_every_measured_message_maps_to_its_domain_class(
    message: str,
    status: int,
    expected: type[DeviceError],
):
    error = translate_http(
        route=ROUTE,
        status=status,
        body=error_body(message),
        endpoint=ENDPOINT,
        doc_uuid=DOC,
    )

    assert type(error) is expected
    assert error.transport is TransportKind.USB_WEB_API


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    MEASURED,
    ids=[message for message, _, _ in MEASURED],
)
def test_no_measured_message_falls_through_to_the_unrecognised_diagnosis(
    message: str,
    status: int,
    expected: type[DeviceError],
):
    """The vocabulary is a lower bound, but it must at least cover what was measured."""
    error = translate_http(
        route=ROUTE,
        status=status,
        body=error_body(message),
        endpoint=ENDPOINT,
        doc_uuid=DOC,
    )

    assert issubclass(expected, DeviceError)
    if isinstance(error, DeviceProtocolError):
        assert error.expected != UNRECOGNISED_DIAGNOSIS
        assert message in error.got


@pytest.mark.parametrize("status", [200, 400, 404, 500, 503])
def test_the_class_follows_the_message_whatever_the_status_was(status: int):
    # The reason this module exists: on this firmware the status code carries almost no
    # information, so branching on it would misclassify most failures.
    error = translate_http(
        route=ROUTE,
        status=status,
        body=error_body("No such entry"),
        endpoint=ENDPOINT,
        doc_uuid=DOC,
    )

    assert type(error) is DeviceDocumentNotFound
    assert error.document_uuid == DOC


def test_a_folder_id_is_reported_as_a_missing_document():
    """``ports/device.py``: an identifier in ``DeviceListing.folders`` raises this.

    The request was well-formed and the device answered it -- there is simply no
    *document* under that id -- so it is not a contract violation.
    """
    error = translate_http(
        route=ROUTE,
        status=400,
        body=error_body("Can only download documents"),
        endpoint=ENDPOINT,
        doc_uuid=DOC,
    )

    assert type(error) is DeviceDocumentNotFound


def test_an_unrouted_path_is_a_client_bug_not_a_missing_document():
    """The string "Unknown file" is what an unrouted path returns, so it is not a 404.

    The firmware also uses it for an unknown id, which is exactly why it cannot be
    trusted to mean that: reporting it as a missing document would send the user looking
    for notes that are on the tablet.
    """
    error = translate_http(
        route="/nope-xyz",
        status=500,
        body=error_body("Unknown file"),
        endpoint=ENDPOINT,
    )

    assert type(error) is DeviceProtocolError
    assert not isinstance(error, DeviceDocumentNotFound)
    assert error.expected == PROTOCOL_DIAGNOSES["Unknown file"]
    assert "Unknown file" in error.got


def test_the_upload_refusal_carries_the_devices_own_words():
    """The read seam's mapping for the one string only the write handler produces.

    Kept rather than narrowed when ``translate_upload`` arrived: this function classifies by
    vocabulary alone and knows nothing about a route's direction, and the string cannot reach
    it from any route this package reads. ``translate_upload`` explains why the *write* seam
    answers differently.
    """
    error = translate_http(
        route=UPLOAD_ROUTE,
        status=400,
        body=error_body(NO_FILE_SENT),
        endpoint=ENDPOINT,
        doc_uuid=DOC,
    )

    assert type(error) is DeviceUploadRejected
    assert error.name == DOC
    assert error.device_message == NO_FILE_SENT
    assert NO_FILE_SENT in UPLOAD_REJECTED_MESSAGES


def test_the_static_asset_message_is_matched_by_prefix():
    # The measured string embeds the filesystem path the firmware failed to open, so an
    # equality match would classify a different asset as an unknown message.
    error = translate_http(
        route="/assets/",
        status=404,
        body=error_body("Unable to open file /usr/share/remarkable/webui/other"),
        endpoint=ENDPOINT,
    )

    assert type(error) is DeviceProtocolError
    assert error.expected == UNOPENABLE_FILE_DIAGNOSIS


def test_an_unrecognised_message_is_typed_and_carries_the_message_verbatim():
    # The vocabulary grew by four strings in one probe session once already. A dict
    # lookup here would have raised KeyError from inside an exception handler.
    message = "Storage medium is on fire"
    error = translate_http(route=ROUTE, status=418, body=error_body(message), endpoint=ENDPOINT)

    assert type(error) is DeviceProtocolError
    assert error.expected == UNRECOGNISED_DIAGNOSIS
    assert message in error.got
    assert "418" in error.got


# ─────────────────────────── translate_upload ───────────────────────────


def test_the_write_seam_reports_no_file_sent_as_our_own_defect():
    """Measured 2026-08-29: no body, and one part named ``document``, both answer this string.

    By the time the real adapter provokes it the multipart body is one *this package* built, so
    the fault is in our request and not in the caller's document. Reporting it as
    ``DeviceUploadRejected`` would send a user to inspect a file that is fine.
    """
    error = translate_upload(
        route=UPLOAD_ROUTE,
        status=400,
        body=error_body(NO_FILE_SENT),
        endpoint=ENDPOINT,
        name=NAME,
    )

    assert type(error) is DeviceProtocolError
    assert error.route == UPLOAD_ROUTE
    assert error.expected == NO_FILE_SENT_DIAGNOSIS
    assert NO_FILE_SENT in error.got
    assert "400" in error.got


def test_the_write_seams_default_is_the_opposite_of_the_read_seams():
    """The whole reason there are two functions rather than one with a flag.

    An unmeasured message on a read route means "we cannot interpret the answer"; on the write
    route it means "the device received the document and declined it". The vocabulary is a
    documented lower bound, so the unmeasured case is the common one.
    """
    message = "Insufficient Storage"
    body = error_body(message)

    on_read = translate_http(route=ROUTE, status=507, body=body, endpoint=ENDPOINT, doc_uuid=DOC)
    on_write = translate_upload(
        route=UPLOAD_ROUTE, status=507, body=body, endpoint=ENDPOINT, name=NAME
    )

    assert type(on_read) is DeviceProtocolError
    assert type(on_write) is DeviceUploadRejected
    assert on_write.name == NAME
    assert on_write.device_message == message


@pytest.mark.parametrize("body", UNUSABLE_BODIES, ids=lambda raw: repr(raw)[:32])
def test_a_write_answer_that_is_not_the_error_shape_names_no_refusal(body: bytes):
    # There is no device message to carry, so there is nothing to report as a refusal: a
    # captive portal's HTML is a protocol violation, not the tablet declining a document.
    error = translate_upload(
        route=UPLOAD_ROUTE, status=302, body=body, endpoint=ENDPOINT, name=NAME
    )

    assert type(error) is DeviceProtocolError
    assert DEVICE_ERROR_KEY in error.expected


def test_a_write_refusal_names_the_document_because_there_is_no_identifier_yet():
    # The route creates, so nothing on the device has an id to name until it answers 201 -- and
    # that body carries none either. The name the caller offered is the only subject available.
    error = translate_upload(
        route=UPLOAD_ROUTE,
        status=500,
        body=error_body("Unknown file"),
        endpoint=ENDPOINT,
        name=NAME,
    )

    assert type(error) is DeviceUploadRejected
    assert error.name == NAME
    assert DOC not in str(error)


@pytest.mark.parametrize("body", UNUSABLE_BODIES, ids=lambda raw: repr(raw)[:32])
def test_a_body_that_is_not_the_error_shape_is_a_protocol_error(body: bytes):
    error = translate_http(route=ROUTE, status=500, body=body, endpoint=ENDPOINT, doc_uuid=DOC)

    assert type(error) is DeviceProtocolError
    assert DEVICE_ERROR_KEY in error.expected
    assert repr(body) in error.got
    assert ENDPOINT in error.got


def test_a_short_unusable_body_is_quoted_whole():
    error = translate_http(route=ROUTE, status=500, body=b"<html>", endpoint=ENDPOINT)

    assert isinstance(error, DeviceProtocolError)
    assert "b'<html>'" in error.got
    assert "truncated" not in error.got


def test_a_huge_unusable_body_is_truncated_in_the_message():
    # A 9 MB log excerpt must not become the text of an exception.
    body = b"x" * 5000
    error = translate_http(route=ROUTE, status=500, body=body, endpoint=ENDPOINT)

    assert isinstance(error, DeviceProtocolError)
    assert "truncated from 5000 bytes" in error.got
    assert len(error.got) < BODY_EXCERPT_LIMIT * 3


@pytest.mark.parametrize("message", ["No such entry", "No file sent"])
def test_a_request_that_named_no_document_falls_back_to_the_route(message: str):
    # A message that identifies a document must not produce an error that identifies
    # nothing, so the route stands in as the subject.
    error = translate_http(route=ROUTE, status=400, body=error_body(message), endpoint=ENDPOINT)

    subject = getattr(error, "document_uuid", None) or getattr(error, "name", None)
    assert subject == ROUTE


def test_the_three_message_sets_are_disjoint():
    # A message in two sets would make the classification depend on check order.
    assert not NOT_FOUND_MESSAGES & UPLOAD_REJECTED_MESSAGES
    assert not NOT_FOUND_MESSAGES & PROTOCOL_DIAGNOSES.keys()
    assert not UPLOAD_REJECTED_MESSAGES & PROTOCOL_DIAGNOSES.keys()


def test_the_classified_vocabulary_is_exactly_the_measured_nine():
    classified = NOT_FOUND_MESSAGES | UPLOAD_REJECTED_MESSAGES | set(PROTOCOL_DIAGNOSES)

    # Eight by equality; the ninth embeds a path and is matched by prefix.
    assert classified == {message for message, _, _ in MEASURED[:-2]} | {"No file sent"}
    assert len(classified) == len(MEASURED) - 1


# ─────────────────────────── translate_httpx ───────────────────────────

UNREACHABLE_HTTPX: list[Exception] = [
    httpx.ConnectError("connection refused"),
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ReadTimeout("timed out reading"),
    httpx.WriteTimeout("timed out writing"),
    httpx.PoolTimeout("no connection free"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.TransportError("something below httpx"),
    OSError("[Errno 51] Network is unreachable"),
    TimeoutError("timed out"),
    ConnectionResetError("peer reset"),
]

MISBEHAVING_HTTPX: list[Exception] = [
    httpx.InvalidURL("not a url"),
    httpx.StreamError("stream consumed"),
    httpx.CookieConflict("two cookies"),
    RuntimeError("nobody anticipated this"),
    ValueError("nor this"),
]


@pytest.mark.parametrize("exc", UNREACHABLE_HTTPX, ids=lambda e: type(e).__name__)
def test_a_transport_failure_over_usb_is_an_unreachable_tablet(exc: Exception):
    error = translate_httpx(exc, endpoint=ENDPOINT)

    assert type(error) is DeviceUnreachable
    assert error.transport is TransportKind.USB_WEB_API
    assert error.endpoint == ENDPOINT
    assert type(exc).__name__ in error.detail


@pytest.mark.parametrize("exc", MISBEHAVING_HTTPX, ids=lambda e: type(e).__name__)
def test_anything_else_from_httpx_is_a_protocol_error_rather_than_a_re_raise(exc: Exception):
    error = translate_httpx(exc, endpoint=ENDPOINT)

    assert type(error) is DeviceProtocolError
    assert error.route == ENDPOINT
    assert type(exc).__name__ in error.got


@pytest.mark.parametrize(
    "exc",
    UNREACHABLE_HTTPX + MISBEHAVING_HTTPX,
    ids=lambda e: type(e).__name__,
)
def test_no_httpx_type_escapes_the_translator(exc: Exception):
    error = translate_httpx(exc, endpoint=ENDPOINT)

    assert isinstance(error, DeviceError)
    assert not isinstance(error, httpx.HTTPError)
    assert type(error).__module__ == "rmspec.domain.errors"


# ─────────────────────────── translate_ssh ───────────────────────────

AUTH_FAILURES: list[Exception] = [
    AuthenticationException("authentication failed"),
    PasswordRequiredException("private key file is encrypted"),
    BadHostKeyException("10.11.99.1", _FakeKey(), _FakeKey()),
]

UNREACHABLE_SSH: list[Exception] = [
    NoValidConnectionsError({("10.11.99.1", 22): OSError("refused")}),
    TimeoutError("timed out"),
    TimeoutError("timed out"),
    OSError("Socket is closed"),
    OSError(),
]

MISBEHAVING_SSH: list[Exception] = [
    SSHException("Error reading SSH protocol banner"),
    ChannelException(2, "administratively prohibited"),
    ConfigParseError("unparsable line in ssh_config"),
    MessageOrderError("received message out of order"),
]

UNANTICIPATED: list[Exception] = [
    RuntimeError("nobody anticipated this"),
    ValueError("nor this"),
    KeyError("nor this either"),
]

#: The per-path failures, each built the way paramiko builds it: ``IOError(errno, text)``,
#: which ``OSError.__new__`` resolves to the matching subclass. ENOENT and EACCES are the two
#: SFTP status codes paramiko maps; EISDIR and ENOTDIR round out the family Python raises for
#: a path that exists and is the wrong kind of thing.
PER_PATH_SSH: list[OSError] = [
    OSError(errno.ENOENT, "No such file"),
    OSError(errno.EACCES, "Permission denied"),
    OSError(errno.EISDIR, "Is a directory"),
    OSError(errno.ENOTDIR, "Not a directory"),
]

REMOTE_PATH = "/home/root/.local/share/remarkable/xochitl/b8ff2c3d.metadata"


@pytest.mark.parametrize("exc", AUTH_FAILURES, ids=lambda e: type(e).__name__)
def test_a_refused_credential_is_an_auth_failure_carrying_no_secret(exc: Exception):
    error = translate_ssh(exc, endpoint=HOST, user=USER, key_source="~/.ssh/id_ed25519")

    assert type(error) is DeviceAuthFailed
    assert error.transport is TransportKind.SSH
    assert error.user == USER
    assert error.key_source == "~/.ssh/id_ed25519"
    assert type(exc).__name__ in error.detail


def test_an_auth_failure_with_no_key_source_says_so_rather_than_inventing_one():
    error = translate_ssh(AuthenticationException("nope"), endpoint=HOST, user=USER)

    assert type(error) is DeviceAuthFailed
    assert error.key_source is None


def test_authentication_is_tested_before_the_ssh_base_class():
    """``PasswordRequiredException`` is an ``SSHException``, so order decides the class.

    Checking ``SSHException`` first would report every wrong key as a protocol violation
    and tell the user to check their cable.
    """
    exc = PasswordRequiredException("private key file is encrypted")

    assert isinstance(exc, SSHException)
    assert type(translate_ssh(exc, endpoint=HOST, user=USER)) is DeviceAuthFailed


@pytest.mark.parametrize("exc", UNREACHABLE_SSH, ids=lambda e: f"{type(e).__name__}-{e}"[:40])
def test_a_session_that_never_opened_is_an_unreachable_tablet(exc: Exception):
    error = translate_ssh(exc, endpoint=HOST, user=USER)

    assert type(error) is DeviceUnreachable
    assert error.transport is TransportKind.SSH
    assert error.endpoint == HOST


def test_no_valid_connections_is_an_oserror_not_an_ssh_exception():
    """Which is why it is named explicitly ahead of the ``SSHException`` arm."""
    exc = NoValidConnectionsError({("10.11.99.1", 22): OSError("refused")})

    assert isinstance(exc, OSError)
    assert not isinstance(exc, SSHException)
    assert type(translate_ssh(exc, endpoint=HOST, user=USER)) is DeviceUnreachable


def test_a_socket_timeout_is_a_timeout_error_and_so_reaches_the_oserror_arm():
    assert socket.timeout is TimeoutError
    assert issubclass(socket.timeout, OSError)


def test_the_detail_names_the_type_even_when_the_exception_carries_no_message():
    error = translate_ssh(OSError(), endpoint=HOST, user=USER)

    assert isinstance(error, DeviceUnreachable)
    assert "OSError" in error.detail


@pytest.mark.parametrize("exc", PER_PATH_SSH, ids=lambda e: type(e).__name__)
def test_one_unopenable_path_is_a_protocol_error_and_not_an_unreachable_tablet(exc: OSError):
    """The fix for the defect that made a mid-walk disconnect look like a skipped library.

    Every one of these is an ``OSError``, so before the dedicated arm existed they all read
    as ``DeviceUnreachable`` -- and a caller could not tell "this file was refused" from "the
    cable was pulled", which forced it to treat both as per-entry facts.
    """
    error = translate_ssh(exc, endpoint=HOST, user=USER, path=REMOTE_PATH)

    assert type(error) is DeviceProtocolError
    assert error.transport is TransportKind.SSH
    assert error.route == REMOTE_PATH
    assert error.expected == PATH_UNREADABLE_DIAGNOSIS
    assert type(exc).__name__ in error.got


def test_an_unopenable_path_the_caller_did_not_name_falls_back_to_the_endpoint():
    error = translate_ssh(OSError(errno.ENOENT, "No such file"), endpoint=HOST, user=USER)

    assert isinstance(error, DeviceProtocolError)
    assert error.route == HOST


def test_the_per_path_arm_is_tested_before_the_oserror_arm():
    """The ordering *is* the mechanism, so it is asserted rather than left to code reading.

    Both halves of the distinction arrive as an ``OSError``; the only signal separating them
    is the ``errno`` Python used to pick the subclass. An arm placed after the base class
    would therefore be unreachable, and the failure mode is silent.
    """
    absent = OSError(errno.ENOENT, "No such file")

    assert isinstance(absent, OSError)
    assert isinstance(absent, PATH_FAILURES)
    assert type(translate_ssh(absent, endpoint=HOST, user=USER)) is DeviceProtocolError


@pytest.mark.parametrize(
    "exc",
    [TimeoutError("stalled"), ConnectionResetError("peer reset"), OSError("generic sftp failure")],
    ids=lambda e: type(e).__name__,
)
def test_a_failure_that_says_nothing_about_a_path_stays_in_the_transport_arm(exc: OSError):
    # A stall and a reset are facts about the session. A status paramiko could not map to an
    # errno is genuinely ambiguous, and calling it per-path would let one broken session be
    # reported as forty-two unreadable documents.
    assert not isinstance(exc, PATH_FAILURES)
    assert type(translate_ssh(exc, endpoint=HOST, user=USER, path=REMOTE_PATH)) is (
        DeviceUnreachable
    )


def test_no_valid_connections_is_not_mistaken_for_a_per_path_failure():
    exc = NoValidConnectionsError({("10.11.99.1", 22): OSError("refused")})

    assert not isinstance(exc, PATH_FAILURES)
    assert type(translate_ssh(exc, endpoint=HOST, user=USER, path=REMOTE_PATH)) is (
        DeviceUnreachable
    )


def test_authentication_is_still_tested_before_the_per_path_arm():
    # PasswordRequiredException is not an OSError, so this cannot regress by accident -- but
    # the auth arm moving below the path arm would be a silent reclassification, so pin it.
    error = translate_ssh(PasswordRequiredException("encrypted"), endpoint=HOST, user=USER)

    assert type(error) is DeviceAuthFailed


@pytest.mark.parametrize("exc", MISBEHAVING_SSH, ids=lambda e: type(e).__name__)
def test_a_session_that_misbehaved_is_a_protocol_error(exc: Exception):
    error = translate_ssh(exc, endpoint=HOST, user=USER)

    assert type(error) is DeviceProtocolError
    assert error.expected == "a session that keeps to the SSH protocol"
    assert type(exc).__name__ in error.got


@pytest.mark.parametrize("exc", UNANTICIPATED, ids=lambda e: type(e).__name__)
def test_an_unanticipated_exception_is_reported_as_such_rather_than_re_raised(exc: Exception):
    error = translate_ssh(exc, endpoint=HOST, user=USER)

    assert type(error) is DeviceProtocolError
    assert error.expected == "an exception type this adapter classifies"
    assert type(exc).__name__ in error.got


@pytest.mark.parametrize(
    "exc",
    AUTH_FAILURES + PER_PATH_SSH + UNREACHABLE_SSH + MISBEHAVING_SSH + UNANTICIPATED,
    ids=lambda e: f"{type(e).__name__}-{e}"[:40],
)
def test_no_paramiko_type_escapes_the_translator(exc: Exception):
    error = translate_ssh(exc, endpoint=HOST, user=USER)

    assert isinstance(error, DeviceError)
    assert not isinstance(error, SSHException)
    assert not isinstance(error, OSError)
    assert type(error).__module__ == "rmspec.domain.errors"


def _paramiko_exception_types() -> dict[str, type[BaseException]]:
    """Every exception class ``paramiko.ssh_exception`` itself defines.

    Returns
    -------
    dict[str, type[BaseException]]
        Name to class, filtered by ``__module__`` so the ``socket`` import and any
        re-exports do not count.
    """
    return {
        name: member
        for name, member in vars(ssh_exception).items()
        if isinstance(member, type)
        and issubclass(member, BaseException)
        and member.__module__ == ssh_exception.__name__
    }


def test_every_exception_type_paramiko_defines_lands_in_one_of_the_named_arms():
    """Totality proved over the type surface, not just over the instances built above.

    paramiko 4.0.0 defines fourteen -- the four this module names by hand plus ten more
    (``BadAuthenticationType``, ``ProxyCommandFailure``, ``IncompatiblePeer``, ...) that
    derive from them. Only ``NoValidConnectionsError`` sits outside the ``SSHException``
    tree, on ``OSError``, which is the fact the check order in ``translate_ssh`` depends
    on. If a release adds a member with a new base, this fails here rather than by
    reaching the CLI raw.
    """
    defined = _paramiko_exception_types()

    assert len(defined) >= len(AUTH_FAILURES) + len(MISBEHAVING_SSH)
    for name, member in defined.items():
        assert issubclass(member, SSHException | OSError), name
    for expected in ("SSHException", "AuthenticationException", "NoValidConnectionsError"):
        assert expected in defined


# ─────────────────────────── command_failed ───────────────────────────


def test_a_non_zero_exit_carries_the_command_the_status_and_the_first_stderr_line():
    error = command_failed(
        command="ls -A /home/root/.local/share/remarkable/xochitl",
        exit_status=1,
        stderr="ls: /home/root/.local/share/remarkable/xochitl: No such file or directory\n",
        endpoint=HOST,
    )

    assert type(error) is DeviceProtocolError
    assert error.transport is TransportKind.SSH
    assert error.route == "ls -A /home/root/.local/share/remarkable/xochitl"
    assert error.expected == "exit status 0"
    assert "exit status 1" in error.got
    assert HOST in error.got
    assert "No such file or directory" in error.got


def test_only_the_first_stderr_line_reaches_the_message():
    # BusyBox writes one line per failure; a multi-line diagnostic is usually a shell
    # trace that would bury it.
    error = command_failed(
        command="df -Pk /root",
        exit_status=2,
        stderr="df: /root: No such file\n+ set -x\n+ exit 2\n",
        endpoint=HOST,
    )

    assert "df: /root: No such file" in error.got
    assert "set -x" not in error.got


@pytest.mark.parametrize("stderr", ["", "   ", "\n\n", "\t \n"])
def test_a_command_that_failed_silently_says_there_was_no_stderr(stderr: str):
    error = command_failed(command="ls -A /", exit_status=1, stderr=stderr, endpoint=HOST)

    assert "no stderr" in error.got
