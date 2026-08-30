"""The ports: every seam through which the application layer reaches a technology.

Twenty-three Protocols across seven slices. An adapter package binds them to ``rmscene``,
``httpx``, ``paramiko``, ``sqlite3``, ``boto3``, ``pyobjc``, ``cairo`` and ``pymupdf``;
``rmspec.app`` imports only this package and :mod:`rmspec.domain.errors`, and
``rmspec.cli`` is the one place that names an adapter at all. Nothing here imports a
third-party package other than pydantic.

Import a Protocol from here. It is the stable address: a port that moves between slice
modules -- and the split above is a judgement about which technologies are swappable, so
some will move -- does not break its dependents.

Value objects are not re-exported
---------------------------------
The models the Protocols exchange stay in their slice modules and are imported from there:
``from rmspec.domain.ports.device import DeviceListing``. Three slices independently define
``RasterImage``, ``ImageMedia`` and ``PhysicalSize``, because a ports module may not import
a sibling and because the rasterizing seam the OCR use case is given must be the same one
the export slice owns -- otherwise one technology lands in two adapters. Re-exporting
same-named twins from one namespace would make which one a caller got depend on import
order. Hoist them into a shared values module when that is done deliberately; until then
the slice module is the address.

Errors are not re-exported either. They live in :mod:`rmspec.domain.errors` as one tree for
the whole workspace, and no port module imports them -- not even under ``TYPE_CHECKING`` --
so the ports and the error tree cannot deadlock on naming. Port docstrings name errors in
their ``Raises`` sections and nothing more.

Notes
-----
Every Protocol is resolved by the dishka composition root. Stores and probes are
``Scope.APP``; transports, codecs and renderers are ``Scope.REQUEST``, so one command's
device work is one handshake closed by one finalizer.

Availability is not a port concern. No method here raises "backend missing": an absent
optional package is ``MissingDependencyError``, raised once during the container's eager
resolution pass, which is what replaced the 27 lazy function-local imports.
"""

from __future__ import annotations

from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceFactsSource,
    DocumentUploader,
    RawBundleSource,
    SceneWriter,
    SearchIndexSource,
)
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.export import (
    ArtifactSink,
    PdfComposer,
    PdfPageReader,
    SvgRasterizer,
)
from rmspec.domain.ports.formats import DocumentRepository, PageCodec, SceneAppender
from rmspec.domain.ports.ocr import (
    HandwrittenTextIndex,
    TextRecognizer,
    VisionLanguageModel,
)
from rmspec.domain.ports.persistence import (
    DiagramCache,
    DocumentSyncStore,
    OcrCache,
    SyncAuditLog,
)
from rmspec.domain.ports.render import PageRenderer, TextEngraver

__all__ = [
    "ArtifactSink",
    "DependencyProbe",
    "DeviceCatalog",
    "DeviceFactsSource",
    "DiagramCache",
    "DocumentRepository",
    "DocumentSyncStore",
    "DocumentUploader",
    "HandwrittenTextIndex",
    "OcrCache",
    "PageCodec",
    "PageRenderer",
    "PdfComposer",
    "PdfPageReader",
    "RawBundleSource",
    "SceneAppender",
    "SceneWriter",
    "SearchIndexSource",
    "SvgRasterizer",
    "SyncAuditLog",
    "TextEngraver",
    "TextRecognizer",
    "VisionLanguageModel",
]
