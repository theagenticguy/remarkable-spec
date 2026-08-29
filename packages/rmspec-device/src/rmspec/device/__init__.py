"""reMarkable device adapters: the USB-C web API over ``httpx``, and SSH over ``paramiko``.

This package binds the four Protocols in :mod:`rmspec.domain.ports.device` to the two ways
a plugged-in tablet can be reached on firmware 3.27.3.0. It may import ``rmspec.domain`` and
nothing else from the workspace. It is not a sync engine, not a use case, and not a place
where policy lives: it moves bytes and decodes the wire, and deciding what to do about a
skipped entry, a missing page or a ``.failure`` sidecar belongs to ``rmspec.app``.

What is exported, and what stays behind an underscore
----------------------------------------------------
Two transports -- :class:`~rmspec.device.usb.UsbWebApi` and
:class:`~rmspec.device._shell.ParamikoShell` -- and the seven adapter classes the
composition root binds to ports. Everything else is private: ``_errors`` (the single
translation seam per transport), ``_wire`` (listing-entry decode), ``_pages`` (the ``cPages``
page-order walk), ``_archive`` (``.rmdoc`` member routing) and ``_shell``'s own
``RemoteShell`` Protocol, which is a seam for this package's tests rather than a port.

:mod:`rmspec.device.addresses` stays a public *module* and is deliberately not re-exported
here: a composition root reaches for ``Endpoint`` and ``RemotePath`` by importing that
module, and keeping them out of this ``__all__`` means every name here is something that can
be bound to a port.

:mod:`rmspec.device.testing` ships the in-memory doubles. It is imported explicitly rather
than re-exported, so a production import of this package never pulls a double into scope.

There is no USB uploader, and the absence is the design
------------------------------------------------------
``DocumentUploader`` has exactly one binding, :class:`~rmspec.device.ssh.SshUploader`.
``POST /upload`` has **never been probed in any form**: the firmware ignores the HTTP request
method, so a ``GET`` to that path could not have been proven non-mutating, and its multipart
field name, accepted content types and response body are all unmeasured. Shipping a guessed
multipart body against the user's only copy of their notes is not a trade this package makes.
``ports/device.py`` opens by arguing that capability asymmetry is expressed as *which ports
exist*, so the composition root fails to bind and raises
``DeviceOperationUnsupported(operation="upload", supported_by=(TransportKind.SSH,))``, and
the shell says "retry over SSH". ``test_device_conformance.py`` asserts that no name here
satisfies ``DocumentUploader`` except the SSH one, so a later reader cannot "fix" the
omission without failing a test.

There is no local-mirror transport either
-----------------------------------------
``TransportKind.LOCAL_MIRROR`` exists and ``ports/device.py`` names the already-pulled local
mirror as a third implementation, but it is not built here. ``rmspec.device`` may not import
``rmspec.formats``, which already owns the xochitl layout, the ``cPages`` walk and a
repository over a local root -- a mirror adapter inside this package would be a second copy
of all three. Its home is ``rmspec-formats``, which imports ``rmspec.domain.ports.formats``
already; ports are addresses, not packages, so a formats module implementing a device-slice
Protocol is coherent. Deferred to step 6, where the composition root and the ``--mirror``
flag that would select it are written.
"""

from __future__ import annotations

from rmspec.device._shell import ParamikoShell
from rmspec.device.ssh import SshBundleSource, SshCatalog, SshFacts, SshUploader
from rmspec.device.usb import UsbBundleSource, UsbCatalog, UsbFacts, UsbWebApi

__all__ = [
    "ParamikoShell",
    "SshBundleSource",
    "SshCatalog",
    "SshFacts",
    "SshUploader",
    "UsbBundleSource",
    "UsbCatalog",
    "UsbFacts",
    "UsbWebApi",
]
