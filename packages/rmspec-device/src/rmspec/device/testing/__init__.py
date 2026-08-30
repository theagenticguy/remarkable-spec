"""In-memory doubles for the device ports, shipped rather than vendored.

Every later application-layer test binds these, and this is the import path they bind. They
live under ``src/`` on purpose, following ``rmspec.persistence.testing``: the architecture
suite checks import direction over ``src/`` only, so an application *test* may depend on
``rmspec.device.testing`` while application *source* stays domain-only. They ship in the
wheel and are held to the same coverage gate as the adapters, which is what keeps a double
honest about the contract it claims to satisfy.

Nothing here opens a file, opens a socket, or imports ``sqlite3``, and no double's behaviour
depends on ``httpx`` or ``paramiko``: :mod:`rmspec.device.testing.doubles` imports neither.
Importing this subpackage does run the parent package's ``__init__``, which binds the real
adapters and therefore both transport libraries -- that is a fact about Python packages, not
a dependency of the doubles.
"""

from __future__ import annotations

from rmspec.device.testing.doubles import (
    IN_MEMORY_ENDPOINT,
    IN_MEMORY_TRANSPORT,
    UPLOAD_OPERATION,
    FakeRemoteShell,
    FakeSearchIndexSource,
    InMemoryDeviceCatalog,
    InMemoryDeviceFactsSource,
    InMemoryDocumentUploader,
    InMemoryRawBundleSource,
)

__all__ = [
    "IN_MEMORY_ENDPOINT",
    "IN_MEMORY_TRANSPORT",
    "UPLOAD_OPERATION",
    "FakeRemoteShell",
    "FakeSearchIndexSource",
    "InMemoryDeviceCatalog",
    "InMemoryDeviceFactsSource",
    "InMemoryDocumentUploader",
    "InMemoryRawBundleSource",
]
