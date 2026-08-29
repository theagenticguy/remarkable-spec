"""Shared fixtures for the device suite, and the guard that makes a real connection fail.

The tablet is reachable from this machine. That is not a hypothetical: on 2026-08-29 the
USB-C gadget was up at ``10.11.99.1`` with ports 22 and 80 open and working key
authentication. So a test in this package that constructs a real ``httpx.Client`` against
the default endpoint, or a real ``paramiko.SSHClient``, does not fail with a connection
error the way it would in CI -- **it succeeds**, silently, against the author's own
documents. It would then pass here, pass for anyone else with a tablet plugged in, and fail
in CI for reasons that look like flakiness.

:func:`forbid_real_sockets` closes that hole at the only place every path has to go through.
It is autouse, so a test opts *out* deliberately by requesting the ``hardware`` marker
rather than opting in by remembering to. The two mechanisms the adapters are designed around
-- ``httpx.MockTransport`` and the in-memory ``RemoteShell`` double -- never reach this code,
so the guard costs the honest tests nothing.

Notes
-----
The patch is on ``socket.socket.connect`` rather than on ``httpx`` or ``paramiko``. Both
libraries are free to reorganise their internals, and a guard bound to a library's private
call graph silently stops guarding when they do; every TCP connection in this interpreter
reaches ``connect``. ``create_connection`` is patched as well because it is the entry point
``paramiko`` uses and it would otherwise reach a socket object this fixture never sees.

``filterwarnings = ["error"]`` and ``pytest-randomly`` are both on for this suite, so a
fixture that leaked state between tests would surface as an order-dependent failure. This
one restores through ``monkeypatch``, which unwinds per test.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Never

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

#: What a test sees when it reaches the network. Names the mechanism it should have used,
#: because the failure is otherwise indistinguishable from the tablet being unplugged.
NETWORK_FORBIDDEN = (
    "this test opened a real network connection. The device suite runs with no network and "
    "no tablet: reach the USB web API through httpx.MockTransport and the SSH transport "
    "through a RemoteShell double. If the test genuinely needs the attached tablet, mark it "
    "@pytest.mark.hardware -- it is then deselected by default and never runs in CI."
)


def _refuse(*_args: object, **_kwargs: object) -> Never:
    """Refuse a connection attempt, naming the fixture that should have been used.

    Raises
    ------
    RuntimeError
        Always. The signature is deliberately permissive because it stands in for two
        callables with different shapes.
    """
    raise RuntimeError(NETWORK_FORBIDDEN)


@pytest.fixture(autouse=True)
def forbid_real_sockets(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Make any real TCP connection from this suite raise, unless the test is hardware-marked.

    Parameters
    ----------
    request
        Used only to read the test's own markers, so a ``hardware`` test is left alone.
    monkeypatch
        Applies and unwinds the patch per test, so no state leaks under random ordering.

    Yields
    ------
    None
        After the guard is in place.
    """
    if request.node.get_closest_marker("hardware") is not None:
        yield
        return
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield
