"""reMarkable device adapters: the USB-C web API over ``httpx``, and SSH over ``paramiko``.

This package binds the five Protocols in :mod:`rmspec.domain.ports.device` to the two ways
a plugged-in tablet can be reached on firmware 3.27.3.0. It may import ``rmspec.domain`` and
nothing else from the workspace. It is not a sync engine, not a use case, and not a place
where policy lives: it moves bytes and decodes the wire, and deciding what to do about a
skipped entry, a missing page or a ``.failure`` sidecar belongs to ``rmspec.app``.

What is exported, and what stays behind an underscore
----------------------------------------------------
Two transports -- :class:`~rmspec.device.usb.UsbWebApi` and
:class:`~rmspec.device._shell.ParamikoShell` -- and the nine adapter classes the
composition root binds to ports. Everything else is private: ``_errors`` (the translation
seams, one per transport plus one for the write route), ``_wire`` (listing-entry decode),
``_pages`` (the ``cPages`` page-order walk), ``_archive`` (``.rmdoc`` member routing) and
``_shell``'s own ``RemoteShell`` Protocol, which is a seam for this package's tests rather
than a port.

:mod:`rmspec.device.addresses` stays a public *module* and is deliberately not re-exported
here: a composition root reaches for ``Endpoint`` and ``RemotePath`` by importing that
module, and keeping them out of this ``__all__`` means every name here is something that can
be bound to a port.

:mod:`rmspec.device.testing` ships the in-memory doubles. It is imported explicitly rather
than re-exported, so a production import of this package never pulls a double into scope.

``DocumentUploader`` has two bindings, and the asymmetry moved rather than vanished
----------------------------------------------------------------------------------
Until 2026-08-29 this section was titled "There is no USB uploader, and the absence is the
design". It argued that ``DocumentUploader`` had exactly one binding,
:class:`~rmspec.device.ssh.SshUploader`, because ``POST /upload`` had **never been probed in
any form** -- the firmware ignores the HTTP request method, so a ``GET`` to that path could
not have been proven non-mutating, and its multipart field name, accepted content types and
response body were all unmeasured. Shipping a guessed multipart body against the user's only
copy of their notes was not a trade this package would make.

That premise is retired **by measurement**. The route was read out of the tablet's own SPA
bundle and probed four ways on 2026-08-29 (``specs/device/3.27.3.0/http.json`` claims[14]):
one multipart part named ``file`` is answered ``201 {"status": "Upload successful"}``, one
named ``document`` is answered ``400 {"error": "No file sent"}``, and the new document is in
``GET /documents/`` with no restart and no stop of xochitl. So the caution was about
*guessing*, and there is nothing left to guess.

The reasoning survives intact and now cuts the other way: capability asymmetry is still
expressed as which bindings exist and what each of them refuses, and both uploaders refuse
something. :class:`~rmspec.device.usb.UsbUploader` raises ``DeviceOperationUnsupported`` for a
non-``None`` ``parent_uuid``, because no folder parameter exists anywhere in that route, and
reports ``LibraryRefresh.ALREADY_VISIBLE``, because xochitl performs the import itself.
:class:`~rmspec.device.ssh.SshUploader` honours a destination and reports
``VISIBILITY_FORCED`` -- it restarts the tablet UI -- and refuses
``UploadMedia.RMDOC``, because placing an archive means unpacking it and writing the sidecars
by hand rather than converting a media. Neither degrades: ``ports/device.py`` forbids
returning a receipt that reports success for something the caller did not ask for.

``test_device_conformance.py`` asserts exactly that pair -- two bindings, each refusal, and
the two refresh outcomes. It is the replacement for the assertion that used to say no name
here except the SSH one satisfied the port, which was true when it was written and is false
now.

There is no USB search-index source, and that absence has a stronger cause
-------------------------------------------------------------------------
``SearchIndexSource`` has exactly one binding,
:class:`~rmspec.device.ssh.SshSearchIndexSource`, and here the absence is not caution about
an unprobed route -- it is that **no such route exists**. That firmware's HTTP route table is
closed at six families and not one of them serves a file from the xochitl tree, so a USB
adapter would be a method with nothing to call. An assertion of its own covers it: no name
here satisfies ``SearchIndexSource`` except the SSH one.

The uploader's history is the reason this distinction is spelled out. "Unprobed, so we will
not guess" is a provisional absence and it turned out to be provisional. "The route table has
no such family" is not, and only the second kind is worth calling a design.

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
from rmspec.device.ssh import (
    SshBundleSource,
    SshCatalog,
    SshFacts,
    SshSearchIndexSource,
    SshUploader,
)
from rmspec.device.usb import UsbBundleSource, UsbCatalog, UsbFacts, UsbUploader, UsbWebApi

__all__ = [
    "ParamikoShell",
    "SshBundleSource",
    "SshCatalog",
    "SshFacts",
    "SshSearchIndexSource",
    "SshUploader",
    "UsbBundleSource",
    "UsbCatalog",
    "UsbFacts",
    "UsbUploader",
    "UsbWebApi",
]
