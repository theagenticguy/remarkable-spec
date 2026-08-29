"""The five measurements this package's design rests on, re-taken against the real tablet.

Deselected by default. Every mise task passes ``-m 'not hardware'``, so the default suite and
CI never reach this file; ``mise run test-hardware`` is the only thing that does, and it needs
the tablet plugged in. The marker is applied at module scope *and* on each test: the module
marker is what makes a sixth test impossible to add without it, and the per-test decorators are
what a reader grepping for the marker finds.

These tests exist because every number in ``DESIGN-step4-device.md`` came from one probe pass
on 2026-08-29, and a firmware update or a settings change can falsify any of them. When one
goes red the answer is to re-measure and update the design, not to relax the assertion.

A detached tablet makes all five fail with ``DeviceUnreachable``, whose remediation already
reads "check the cable and the host". That is deliberate rather than a rough edge: selecting
``-m hardware`` is an assertion that the tablet is plugged in, and a skip would let a run that
re-measured nothing read as a run that confirmed everything.

Safety, which is not a matter of care but of construction
---------------------------------------------------------
The tablet holds the user's only copy of their notes, so this file is read-only by mechanism.

**Nothing here can request the unprobed upload route.** ``POST /upload`` has never been probed
in any form: the firmware ignores the HTTP request method, so even a ``GET`` to that path could
not have been proven non-mutating. :func:`_only_read_the_listing` is installed as an ``httpx``
request hook and fails the test for any request that is not a ``GET`` or ``HEAD`` under
:data:`~rmspec.device._wire.LISTING_ROUTE`. That path is therefore unreachable from this module
rather than merely unwritten, which is the difference between a rule and a guarantee. The hook
raises off ``BaseException``, so ``UsbWebApi._answer``'s ``except`` clause cannot swallow it and
report a device failure instead. It also happens to exclude ``/download``, which is a read
route and would be safe -- no test here needs one, and blocking it means none can accidentally
pull megabytes of the user's handwriting into a test process.

**Nothing here writes over SSH.** :class:`~rmspec.device.ssh.SshUploader` is never constructed,
no ``systemctl`` is run, and no service is restarted. The only commands sent are the four
:class:`~rmspec.device.ssh.SshFacts` builds, each a ``sed``, a ``cat`` or a ``df`` against a
path ``addresses.py`` spells.

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

from rmspec.device import ParamikoShell, SshFacts, UsbCatalog, UsbFacts, UsbWebApi
from rmspec.device._wire import LISTING_ROUTE, decode_entries, entry_parent
from rmspec.device.addresses import DEFAULT_USB_HOST, SSH_PORT, Endpoint
from rmspec.device.ssh import SERIAL_FIELD

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.hardware

#: Where the USB web API answers, and the origin every route below is appended to.
WEB_ENDPOINT: Final = Endpoint()

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


def _only_read_the_listing(request: httpx.Request) -> None:
    """Fail the test unless this request is a read of the listing route family.

    Installed as an ``httpx`` request hook, so it runs before the bytes leave this machine and
    covers every request any adapter in this module makes -- including one a future edit adds
    without thinking about it. ``pytest.fail`` raises off ``BaseException``, so
    ``UsbWebApi._answer``'s ``except`` clause cannot swallow it and report a device failure.

    Parameters
    ----------
    request
        The outgoing request.
    """
    path = request.url.raw_path.decode()
    if request.method not in READ_METHODS or not path.startswith(LISTING_ROUTE):
        pytest.fail(
            f"this module tried to send {request.method} {path}. Only "
            f"{sorted(READ_METHODS)} under {LISTING_ROUTE} are permitted here, because the "
            f"firmware ignores the request method and the write routes are unprobed."
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


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    """Yield a real HTTP client that can only send reads of the listing route.

    Yields
    ------
    httpx.Client
        The client, closed on the way out.
    """
    with httpx.Client(
        timeout=TIMEOUT_SECONDS,
        event_hooks={"request": [_only_read_the_listing]},
    ) as live:
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
def test_the_head_probe_answers_and_the_facts_source_reports_attached(
    client: httpx.Client,
    api: UsbWebApi,
) -> None:
    """``HEAD /documents/`` is the safe existence probe, and the only fact it establishes."""
    response = client.head(f"{WEB_ENDPOINT.base_url}{LISTING_ROUTE}")

    assert response.status_code == 200
    # A zero-length body is what makes HEAD safe to probe with and what makes it the one
    # response that must never be length-checked: its Content-Length describes the body it
    # deliberately omitted.
    assert response.content == b""

    facts = UsbFacts(api=api).read_facts()

    assert facts.unsupported == UNANSWERABLE_FACTS
    assert (facts.firmware, facts.model, facts.serial) == (None, None, None)


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
    assert facts.unsupported == frozenset({SERIAL_FIELD})
