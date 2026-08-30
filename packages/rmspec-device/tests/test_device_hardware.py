"""The eight measurements this package's design rests on, re-taken against the real tablet.

Deselected by default. Every mise task passes ``-m 'not hardware'``, so the default suite and
CI never reach this file; ``mise run test-hardware`` is the only thing that does, and it needs
the tablet plugged in. The marker is applied at module scope *and* on each test: the module
marker is what makes an unmarked test impossible to add here, and the per-test decorators are
what a reader grepping for the marker finds.

These tests exist because every number in ``DESIGN-step4-device.md`` came from one probe pass
on 2026-08-29, and a firmware update or a settings change can falsify any of them. When one
goes red the answer is to re-measure and update the design, not to relax the assertion.

A detached tablet makes all eight fail with ``DeviceUnreachable``, whose remediation already
reads "check the cable and the host". That is deliberate rather than a rough edge: selecting
``-m hardware`` is an assertion that the tablet is plugged in, and a skip would let a run that
re-measured nothing read as a run that confirmed everything.

Eight tests, and one port with none: the uploader
------------------------------------------------
:class:`~rmspec.device.usb.UsbUploader` is the only device port in this package that is *not*
re-measured here, and the omission is the point rather than an oversight. ``POST /upload``
**creates** a document, and the firmware's route table is closed at six path families with
none that deletes -- so there is no way to undo what a test run would do. Deleting the files
over SSH while xochitl runs leaves phantom library entries, which is worse. An automated test
of that route would therefore accumulate entries in the author's own library on every run,
removable only by hand from the tablet UI, and a test whose cost is manual cleanup is a test
that gets skipped and then deleted.

It is covered by fakes against the recorded measurement instead:
``specs/device/3.27.3.0/http.json`` claims[14] holds the four probes, and
``test_device_usb.py`` asserts the multipart shape, the status rule and both refusals against
``httpx.MockTransport``. When that claim needs re-measuring it is re-measured by hand, once,
and the claim is updated -- which is the honest trade for an operation with no inverse.

Safety, which is not a matter of care but of construction
---------------------------------------------------------
The tablet holds the user's only copy of their notes, so this file is read-only by mechanism.

**Nothing here can request the upload route.** :func:`_only_read_the_listing` is installed as
an ``httpx`` request hook and fails the test for any request that is not a ``GET`` or ``HEAD``
under :data:`~rmspec.device._wire.LISTING_ROUTE`. That path is therefore unreachable from this
module rather than merely unwritten, which is the difference between a rule and a guarantee.
The hook raises off ``BaseException``, so ``UsbWebApi._answer``'s ``except`` clause cannot
swallow it and report a device failure instead. It also happens to exclude ``/download``, which
is a read route and would be safe -- no test here needs one, and blocking it means none can
accidentally pull megabytes of the user's handwriting into a test process.

That hook was written when ``POST /upload`` was unprobed and the firmware's indifference to the
request method meant even a ``GET`` to it could not be proven non-mutating. It is now
**load-bearing rather than precautionary**: the route is measured, it works, one adapter in
this package sends it, and it is exactly the request this module must never make.

**Nothing here writes over SSH.** :class:`~rmspec.device.ssh.SshUploader` is never constructed,
:class:`~rmspec.device.writeback.SshSceneWriter` is constructed but only
:meth:`~rmspec.device.writeback.SshSceneWriter.verify` is ever called on it, and **no service is
restarted**. The commands sent are the four :class:`~rmspec.device.ssh.SshFacts` builds -- each a
``sed``, a ``cat`` or a ``df`` against a path ``addresses.py`` spells -- one ``ls -A`` of the
xochitl root, and the two reads the guarded restart takes *before* it acts:
``systemctl is-active`` and a ``cat`` of the boot identifier. There is no ``mkdir``, no ``mv``
and no ``rm``, which are the three commands the write path needs, so a write is not merely
unwritten here: the temp file it would rename has nothing to create it.

``systemctl`` therefore appears in this module, which it did not before, and the line between
what is read-only about it and what is not is worth stating: ``is-active`` reports state and
changes none, while ``reset-failed`` mutates a counter and ``restart`` spends one of the four
starts per ten minutes that the firmware allows before it isolates ``emergency.target``.
:data:`~rmspec.device.ssh.RESET_FAILED_TEMPLATE` and
:data:`~rmspec.device.ssh.RESTART_TEMPLATE` are **never** built here. The refusals they trigger
are asserted in ``test_device_ssh.py`` against a command log, which is where a test of a
restart belongs; what needs the real tablet is the *precondition* -- that the probe answers
``active`` and the fence answers a stable identifier -- because a guard whose reads do not work
on the real device refuses every upload instead of protecting one.

The SFTP *reads* are ``<xochitl>/rm-search-index.db``, one or more ``.content`` sidecars, and
one page's ``.rm``. The last of those is the widening a reader should notice, and it is why
:func:`test_the_write_precondition_agrees_with_the_real_page` exists at all: the
concurrency precondition is a comparison of an artifact's digest across two reads, so proving
it works against the real filesystem requires reading a real artifact. No ``.metadata`` is
read at all -- the page is found from the root listing and a ``.content``, neither of which
carries a document title.

**Nothing here reads the tablet's own configuration file.** It holds a cleartext developer
password and two bearer tokens, ``tests/architecture/test_secret_containment.py`` fails the
build if any source file in this workspace so much as names it, and this module has no reason
to: identity comes from ``/etc/os-release`` and ``/sys/devices/soc0/machine``, and
authentication comes from the user's own key. No password is ever offered --
:class:`~rmspec.device.ssh.ParamikoShell` has no code path that can.

**Nothing here asserts on the user's content.** Every assertion is about a shape, a count, a
set relation or a firmware constant. No document title, no page identifier and no payload is
compared against a literal or captured into a file, so a green run reveals nothing about the
library it ran against and nothing from the device is committed.

That rule is strictest for the two things that *are* the user's handwriting: the search index,
90 of whose 92 rows on the measured device carry recognised text, and the one page artifact the
precondition test reads.

:func:`test_the_search_index_is_a_whole_sqlite_image_of_a_plausible_size` reads the index into
memory, looks at the first sixteen bytes and the length, and stops. It does not decode the
database and asserts on no row; this package cannot even import ``sqlite3``, so the reading
half is unreachable from here by construction rather than by restraint. The bytes are never
written to local disk, which is the rule that replaced an earlier session's ``scp`` of that
same file.

:func:`test_the_write_precondition_agrees_with_the_real_page` reads one page's strokes and
asserts on nothing but their SHA-256 -- against *itself*, twice, never against a literal. A
digest is not the content and the test commits none, so a green run says the check works and
says nothing about what is on the page. The page id it uses comes from the device and is never
compared with anything either.

Authentication
--------------
The key is named by :data:`KEY_PATH_VARIABLE`, defaulting to the reference machine's
``remarkable`` identity file -- a path, never a secret, which is the same distinction
``_errors.translate_ssh`` draws when it reports ``key_source``. ``ParamikoShell`` does not read
``~/.ssh/config``, so the host and port are spelled from ``addresses.py`` rather than resolved
from a host alias, and the host key is verified against ``known_hosts`` with paramiko's default
reject policy.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import TYPE_CHECKING, Final
from urllib.parse import quote

import httpx
import pytest
from device_contracts import SQLITE_MAGIC

from rmspec.device import (
    ParamikoShell,
    SshFacts,
    SshSearchIndexSource,
    UsbCatalog,
    UsbFacts,
    UsbWebApi,
)
from rmspec.device._pages import decode_page_order
from rmspec.device._wire import LISTING_ROUTE, decode_entries, entry_parent
from rmspec.device.addresses import (
    BOOT_ID,
    CONTENT_SUFFIX,
    DEFAULT_USB_HOST,
    SSH_PORT,
    Endpoint,
    RemoteCommand,
    RemotePath,
    document_paths,
)
from rmspec.device.ssh import (
    ACTIVE_STATE,
    BOOT_ID_TEMPLATE,
    NO_SERIAL_SOURCE,
    SERVICE_STATE_TEMPLATE,
    UI_SERVICE,
)
from rmspec.device.writeback import ScenePrecondition, SshSceneWriter
from rmspec.domain.errors import DeviceProtocolError, TransportKind

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.hardware

#: Where the USB web API answers, and the origin every route below is appended to.
WEB_ENDPOINT: Final = Endpoint()

#: The xochitl store on the attached device.
STORE_ROOT: Final = RemotePath.root()

#: A digest no page can have: 64 zeroes. Used to make the precondition *fail* against a real
#: read without any second writer, which is the only half of the check that cannot be
#: demonstrated by simply reading the same file twice.
IMPOSSIBLE_DIGEST: Final = "0" * 64

#: Where the SSH daemon answers. Spelled from ``addresses.py`` rather than resolved from an
#: ``~/.ssh/config`` host alias, because ``ParamikoShell`` does not read that file.
SSH_ENDPOINT: Final = Endpoint(host=DEFAULT_USB_HOST, port=SSH_PORT)

#: Override for the private key to authenticate with.
KEY_PATH_VARIABLE: Final = "RMSPEC_DEVICE_KEY"

#: The reference machine's identity file for the tablet. A path is not a secret; the key it
#: names is, and nothing here reads or logs its contents.
DEFAULT_KEY_PATH: Final = "~/.ssh/id_ed25519_remarkable"

#: How long any single request or command may stall. The link is a point-to-point cable and the
#: attached tablet answers in milliseconds, so a longer wait buys nothing except a slower
#: failure when the cable is out -- which is the case this value is actually tuned for.
TIMEOUT_SECONDS: Final = 5.0

#: The two verbs this module is allowed to send. Both are read-only on every route the
#: firmware serves.
READ_METHODS: Final = frozenset({"GET", "HEAD"})

#: The ``Parent`` value the on-disk store uses for a trashed entry. Measured absent from every
#: USB listing at every depth, which is what :data:`test_the_walk_reaches_every_folder_and_no_
#: entry_is_in_the_trash` re-checks.
TRASH_PARENT: Final = "trash"

#: What the USB web API can say about the tablet, which is nothing: the route table is closed
#: at six families and none of them reports any of these.
UNANSWERABLE_FACTS: Final = frozenset({"firmware", "model", "serial"})

#: ``IMG_VERSION`` in ``/etc/os-release``, measured 2026-08-29.
FIRMWARE: Final = "3.27.3.0"

#: ``/sys/devices/soc0/machine``, measured 2026-08-29.
MODEL: Final = "reMarkable Ferrari"

#: Bytes ``rm-search-index.db`` held on 2026-08-29. Not asserted as an equality: the tablet
#: rebuilds this file as the user writes, so the measured value is a *scale* and an exact match
#: would go red for the ordinary reason.
SEARCH_INDEX_BYTES: Final = 503_808

#: SQLite's page size on that image -- 503,808 is exactly 123 pages of it. A database file is
#: always a whole number of pages, so the remainder is the one size assertion that stays exact
#: while the index is rebuilt at a different length, and it is precisely what a torn read of a
#: file being written breaks.
SQLITE_PAGE_BYTES: Final = 4096

#: Characters in a canonical uuid, which is what the kernel writes into the boot identifier.
#: The length is asserted rather than the value: a truncated or empty read would make the reboot
#: fence unarmable and every SSH upload refuse.
BOOT_ID_LENGTH: Final = 36

#: Hyphens in that shape. Together with the length, enough to say "this is a uuid" without
#: importing a parser or naming the value.
BOOT_ID_GROUPS: Final = 4

#: How far from :data:`SEARCH_INDEX_BYTES` the live index may be before this test calls the
#: reading implausible. A factor rather than a byte tolerance, because the index grows with the
#: library; an order of magnitude either way still fails on the two shapes that matter, a
#: badly truncated transfer and the wrong file entirely.
PLAUSIBLE_FACTOR: Final = 8


def _only_read_the_listing(request: httpx.Request) -> None:
    """Fail the test unless this request is a read of the listing route family.

    Installed as an ``httpx`` request hook, so it runs before the bytes leave this machine and
    covers every request any adapter in this module makes -- including one a future edit adds
    without thinking about it. ``pytest.fail`` raises off ``BaseException``, so
    ``UsbWebApi._answer``'s ``except`` clause cannot swallow it and report a device failure.

    The request this exists to stop is now one an adapter in this package really sends:
    :class:`~rmspec.device.usb.UsbUploader` posts to ``/upload``, that route creates a document,
    and nothing in the route table can delete one. See the module docstring.

    Parameters
    ----------
    request
        The outgoing request.
    """
    path = request.url.raw_path.decode()
    if request.method not in READ_METHODS or not path.startswith(LISTING_ROUTE):
        pytest.fail(
            f"this module tried to send {request.method} {path}. Only "
            f"{sorted(READ_METHODS)} under {LISTING_ROUTE} are permitted here: the firmware "
            f"ignores the request method, and POST /upload creates a document that no route "
            f"in this firmware can delete."
        )


def _key_path() -> str:
    """Return the private key file to authenticate with.

    Returns
    -------
    str
        The expanded path.
    """
    raw = os.environ.get(KEY_PATH_VARIABLE, DEFAULT_KEY_PATH)
    return str(pathlib.Path(raw).expanduser())


def _guarded_client() -> httpx.Client:
    """Build a client that can only send reads of the listing route.

    The same construction the :func:`client` fixture uses, factored out because one test needs
    two *additional* short-lived clients -- it measures a ``HEAD`` that deliberately poisons
    the connection it was sent on, which must not be the shared one. Building those by hand
    would have put unguarded clients in this module and quietly voided the guarantee its own
    docstring makes, so there is one construction and no way to get an unguarded client.

    Returns
    -------
    httpx.Client
        The client. The caller closes it.
    """
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        event_hooks={"request": [_only_read_the_listing]},
    )


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    """Yield a real HTTP client that can only send reads of the listing route.

    Yields
    ------
    httpx.Client
        The client, closed on the way out.
    """
    with _guarded_client() as live:
        yield live


@pytest.fixture
def api(client: httpx.Client) -> UsbWebApi:
    """Return the USB transport over the guarded client.

    Parameters
    ----------
    client
        The guarded HTTP client.

    Returns
    -------
    UsbWebApi
        The transport, bound to the attached tablet.
    """
    return UsbWebApi(client=client, endpoint=WEB_ENDPOINT)


@pytest.fixture
def shell() -> Iterator[ParamikoShell]:
    """Yield a connected SSH shell, authenticated with the user's own key.

    Yields
    ------
    ParamikoShell
        The shell, closed on the way out.
    """
    live = ParamikoShell(
        endpoint=SSH_ENDPOINT,
        key_path=_key_path(),
        timeout=TIMEOUT_SECONDS,
    )
    live.connect()
    try:
        yield live
    finally:
        live.close()


@pytest.mark.hardware
def test_the_head_probe_answers_and_the_facts_source_reports_attached(api: UsbWebApi) -> None:
    """``HEAD /documents/`` is the safe existence probe, and the only fact it establishes.

    Measured 2026-08-30, correcting what this test used to assert. The firmware does **not**
    answer a ``HEAD`` with a zero-length body: it ignores the request method, so it replies
    ``200`` with ``Transfer-Encoding: chunked``, no ``Content-Length``, and the whole listing.
    ``response.content`` is empty because ``httpx`` follows RFC 9110 and reads no body for a
    ``HEAD`` -- the emptiness is the client declining to read, not the device declining to
    write, and the difference is the whole defect. Those unread bytes stay in the socket, so
    the next response on that connection begins at the chunk-size line and httpx reports
    ``illegal status line: bytearray(b'105d')`` -- ``0x105d`` is 4189, the listing's exact
    length.

    ``Connection: close`` does more than discard the poison -- it changes the framing the
    firmware chooses. The two shapes, both measured below on throwaway clients so neither can
    contaminate the shared one:

    ==========================  ==================================================
    request                     response headers
    ==========================  ==================================================
    ``HEAD`` bare               ``content-type``, ``transfer-encoding: chunked``
    ``HEAD`` + close            ``content-type``, ``connection: close``
    ==========================  ==================================================

    So with the header there is no chunked body to leave behind and the connection is torn
    down regardless. :meth:`UsbWebApi.head` sends it for that reason, and an earlier version
    of this test called ``client.head`` bare and then read facts on the same pooled client,
    which failed roughly one run in six.
    """
    url = f"{WEB_ENDPOINT.base_url}{LISTING_ROUTE}"

    # The hazard, demonstrated rather than described. Its own client, discarded immediately,
    # because this request deliberately leaves an unread body in the socket.
    with _guarded_client() as poisoned:
        bare = poisoned.head(url)
        assert bare.status_code == 200
        assert bare.headers["transfer-encoding"] == "chunked"
        assert "content-length" not in bare.headers
        # Empty because httpx declined to read it, not because the device declined to write it.
        assert bare.content == b""

    # The fix, on its own client too.
    with _guarded_client() as closing:
        closed = closing.head(url, headers={"Connection": "close"})
        assert closed.status_code == 200
        assert closed.headers["connection"] == "close"
        assert "transfer-encoding" not in closed.headers
        assert closed.content == b""

    # The shipped path, which is what a caller actually uses, and which must survive being
    # followed immediately by a read on the same client.
    assert api.head(LISTING_ROUTE) is None

    facts = UsbFacts(api=api).read_facts()

    assert facts.unsupported_names == UNANSWERABLE_FACTS
    assert (facts.firmware, facts.model, facts.serial) == (None, None, None)
    # Against the live tablet, not just a fake: the two fields SSH answers on this very device
    # are annotated as answerable there, and the one it does not is annotated as answerable
    # nowhere. `test_the_facts_are_the_ones_this_firmware_reports` below is the other half --
    # it reads `firmware` and `model` over SSH on the same run.
    assert {entry.name: entry.supported_by for entry in facts.alternatives} == {
        "firmware": (TransportKind.SSH,),
        "model": (TransportKind.SSH,),
        "serial": (),
    }


@pytest.mark.hardware
def test_the_root_listing_decodes_with_nothing_skipped(api: UsbWebApi) -> None:
    """Every entry the firmware returns is representable in this domain.

    A non-empty ``skipped`` here means the wire grew a shape ``_wire`` does not decode, which
    is a design change and not a test to relax. Counts only: the entries carry the user's own
    document titles.
    """
    decoded = decode_entries(api.get(LISTING_ROUTE))

    assert decoded.skipped == ()
    assert len(decoded.documents) + len(decoded.folders) > 0


@pytest.mark.hardware
def test_the_walk_reaches_every_folder_and_no_entry_is_in_the_trash(api: UsbWebApi) -> None:
    """Two measurements at once, both load-bearing for D3.

    First, the breadth-first walk reaches every folder that any entry names as its parent --
    so no document is reported hanging off a folder the listing never returned, which is what
    would happen if the walk terminated early or the parent filter discarded a real child.

    Second, no listing at any depth carries ``Parent == "trash"``. That is what makes
    ``DeviceDocument.trashed`` accurately ``False`` for every entry this transport returns, and
    what makes sentinel-handling code in the USB adapter unreachable rather than merely absent.
    """
    listing = UsbCatalog(api=api).list_documents()

    assert listing.skipped == ()
    reached = {folder.uuid for folder in listing.folders}
    named_as_parent = {
        entry.parent_uuid
        for entry in (*listing.documents, *listing.folders)
        if entry.parent_uuid is not None
    }
    assert named_as_parent <= reached

    bodies = [api.get(LISTING_ROUTE)]
    bodies.extend(api.get(f"{LISTING_ROUTE}{quote(uuid, safe='')}") for uuid in sorted(reached))
    trashed = [
        entry_parent(entry)
        for body in bodies
        for entry in json.loads(body)
        if entry_parent(entry) == TRASH_PARENT
    ]
    assert trashed == []


@pytest.mark.hardware
def test_the_gauges_parse_into_a_valid_resource_reading(shell: ParamikoShell) -> None:
    """``df -Pk`` and ``/proc/meminfo`` both answer, and both answers are internally sound.

    A ``None`` in any of the four fields would mean a reading this adapter could not parse --
    which for storage is exactly what plain ``df -k`` produces on this device, because the
    31-character device name overflows BusyBox's 20-column field and the numbers land on the
    following line. So this test is what keeps the ``-P`` honest against the real output.
    """
    resources = SshFacts(shell=shell).read_resources()

    assert resources.unsupported == frozenset()
    pairs = (
        (resources.total_memory_bytes, resources.available_memory_bytes),
        (resources.total_storage_bytes, resources.available_storage_bytes),
    )
    for total, free in pairs:
        assert total is not None
        assert free is not None
        assert 0 < free <= total


@pytest.mark.hardware
def test_the_facts_are_the_ones_this_firmware_reports(shell: ParamikoShell) -> None:
    """The firmware and model constants, and the one field that stays structurally unaskable.

    ``serial`` is named in ``unsupported`` rather than answered with
    ``/sys/devices/soc0/serial_number``, which exists and holds 16 characters: that is the SoC
    unique id, a *different fact* from the serial the tablet UI shows. The distinction is not
    observable from here, which is why it is recorded as "cannot ask" rather than re-measured.
    """
    facts = SshFacts(shell=shell).read_facts()

    assert facts.firmware == FIRMWARE
    assert facts.model == MODEL
    assert facts.serial is None
    assert facts.unsupported == frozenset({NO_SERIAL_SOURCE})
    assert facts.alternatives[0].supported_by == ()


@pytest.mark.hardware
def test_the_search_index_is_a_whole_sqlite_image_of_a_plausible_size(
    shell: ParamikoShell,
) -> None:
    """``rm-search-index.db`` exists, is a SQLite image, and arrives whole.

    Three claims, and no more than three. It is **not** ``None``, which is what the adapter
    answers for a device that has built no index -- so this is also the measurement that the
    file is where ``addresses.py`` says. Its first sixteen bytes are the format's magic, which
    is what makes "we transported a database" a fact rather than an assumption. And its length
    is a whole number of SQLite pages within an order of magnitude of the measured 503,808,
    which is the strongest size claim that survives the tablet rebuilding the index: a
    truncated transfer lands mid-page and fails the remainder.

    The bytes stop here. Nothing decodes them, no row is asserted on, and nothing is written to
    local disk -- see the module docstring, because this file is the user's handwriting.
    """
    image = SshSearchIndexSource(shell).read_index()

    assert image is not None
    assert image[: len(SQLITE_MAGIC)] == SQLITE_MAGIC
    assert len(image) % SQLITE_PAGE_BYTES == 0
    assert len(image) >= SEARCH_INDEX_BYTES // PLAUSIBLE_FACTOR
    assert len(image) <= SEARCH_INDEX_BYTES * PLAUSIBLE_FACTOR


@pytest.mark.hardware
def test_the_restart_guard_can_read_what_it_refuses_on(shell: ParamikoShell) -> None:
    """The two reads the guarded restart takes before it acts. Sends neither command that acts.

    :meth:`~rmspec.device.ssh.SshUploader._refresh` refuses to restart unless both of these
    answer, so a guard whose reads do not work against the real firmware does not fail safe --
    it fails *closed*, and every SSH upload stops working. That makes these two the only halves
    of the guard worth taking against hardware, and the halves that must never be:
    ``reset-failed`` mutates a counter and ``restart`` spends one of four starts per ten minutes.
    Neither template is built anywhere in this module.

    Three claims.

    **The probe answers exactly** :data:`~rmspec.device.ssh.ACTIVE_STATE`. The unit is
    ``loaded/active/running`` per ``specs/device/3.27.3.0/systemd.json`` claim
    ``unit:xochitl.service``, and it must be: it is the process serving the USB web API that the
    tests above just used, so any other answer here would mean the tablet's UI is down.

    **The probe exits zero even so.** ``is-active`` exits 3 for every state that is not active,
    and the ``|| true`` in the template is what keeps the *state* reaching the adapter as stdout
    instead of being replaced by an exit status. This is the measurement that the shell on this
    firmware honours the operator at all -- a ``systemctl is-active`` that raised here would mean
    the guard could never read a state and would refuse every upload.

    **The fence is a stable 36-character identifier.** Two reads, one value: a fence that
    changed while nothing restarted would make every upload report a phantom reboot. Its shape is
    asserted, its value is not compared with any literal, and it is not committed -- it is a
    per-boot random value that identifies no user and no device across boots, but the rule in this
    module is that nothing from the tablet is captured, and it holds here too.
    """
    state = shell.run(RemoteCommand.of(SERVICE_STATE_TEMPLATE, UI_SERVICE))
    fence = RemoteCommand.of(BOOT_ID_TEMPLATE, RemotePath.absolute(BOOT_ID))
    first = shell.run(fence).strip()
    second = shell.run(fence).strip()

    assert state.strip() == ACTIVE_STATE
    assert first == second
    assert len(first) == BOOT_ID_LENGTH
    assert first.count("-") == BOOT_ID_GROUPS
    assert first == first.lower()


def _first_page_on_the_device(shell: ParamikoShell) -> tuple[str, str]:
    """Find one real page to check the precondition against, reading no ``.metadata``.

    Walks the root listing for ``.content`` sidecars and returns the first document whose
    sidecar claims at least one page. A ``.content`` carries the page order and the layout
    facts and no document title, so this route to a page id exposes strictly less of the
    library than :meth:`~rmspec.device.ssh.SshCatalog.list_documents` would.

    Parameters
    ----------
    shell
        The connected shell.

    Returns
    -------
    tuple[str, str]
        The document uuid and the first page id its sidecar records.
    """
    stems = sorted(
        name.removesuffix(CONTENT_SUFFIX)
        for name in shell.list_dir(STORE_ROOT)
        if name.endswith(CONTENT_SUFFIX)
    )
    for doc_uuid in stems:
        order = decode_page_order(shell.read_file(document_paths(STORE_ROOT, doc_uuid).content))
        if order:
            return doc_uuid, order[0].page_id
    pytest.fail(
        "no document on the attached tablet records a page, so the precondition cannot be "
        "checked against a real artifact. This is a fact about the library, not about the "
        "code -- but it is not a pass either."
    )


@pytest.mark.hardware
def test_the_write_precondition_agrees_with_the_real_page(shell: ParamikoShell) -> None:
    """The concurrency precondition, run against a real page. Reads only; writes nothing.

    Three claims, and the third is the one that matters.

    **An artifact's identity is stable across two real reads.** Two ``read_scene`` calls over
    the live SFTP channel produce equal preconditions. That is not a tautology: it is the
    property the whole design rests on, because if a page's bytes were not byte-stable at rest
    -- if the firmware rewrote or re-serialised them between reads -- then every precondition
    would fail spuriously and refusing on mismatch would be useless. It also proves the read is
    whole rather than truncated, twice, since a torn read would differ from the other one.

    **The check passes against the artifact it was taken from.** ``verify`` returns the same
    bytes it read.

    **The check fails against an identity the artifact does not have**, with the page's path in
    ``route`` and the real identity in ``got``. This is what makes the refusal a measured
    behaviour rather than an in-memory one: the digest it compares against came off the device
    on this run. :data:`IMPOSSIBLE_DIGEST` stands in for the concurrent human -- the adapter
    cannot tell "the digest I was given is not this file's" apart from "somebody changed this
    file", because they are the same condition.

    What this test deliberately cannot show is that a write is refused, because reaching
    ``write_scene``'s check means first staging a temp file on the device. That half is asserted
    in ``test_device_writeback.py`` against an in-memory store, and the two together are the
    whole property: the comparison is measured here, the refusal it triggers is measured there.
    """
    doc_uuid, page_id = _first_page_on_the_device(shell)
    writer = SshSceneWriter(shell=shell, root=STORE_ROOT)

    first = writer.read_scene(doc_uuid, page_id)
    second = writer.read_scene(doc_uuid, page_id)

    assert first.precondition == second.precondition
    assert writer.verify(first.precondition) == first.scene
    assert first.precondition.digest != IMPOSSIBLE_DIGEST

    stale = ScenePrecondition(doc_uuid=doc_uuid, page_id=page_id, digest=IMPOSSIBLE_DIGEST)
    with pytest.raises(DeviceProtocolError) as caught:
        writer.verify(stale)

    assert caught.value.transport is TransportKind.SSH
    assert caught.value.route == first.path.value
    assert caught.value.expected == f"sha256 {IMPOSSIBLE_DIGEST}"
    assert caught.value.got != caught.value.expected
