"""The composition root: the only module in the workspace that names an adapter.

Everything here exists because a dependency-injection container resolves lazily and nothing
fires "at composition" on its own. That laziness is what the legacy tree's 27 function-local
``ImportError`` raises exploited, and what this module's eager pass takes away.

Why importing this module is expensive, and why that is correct
--------------------------------------------------------------
Measured: importing the six adapter packages adds **821 modules** and eagerly loads
``cairocffi``, ``rmscene``, ``httpx``, ``paramiko`` and ``boto3``. That is the honest price of
composing a binding, and it is a price ``rmspec --help`` must not pay -- legacy charged 447
modules on every invocation because its command modules imported the formats package at module
scope. So :mod:`rmspec.cli` does not import this module while building its command table; it
loads it inside the one command that composes something, and ``test_cli_entry.py`` measures
that rather than trusting it.

The eager pass, and why it collects before it raises
---------------------------------------------------
:class:`~rmspec.domain.ports.errors.DependencyProbe`'s docstring is the specification: for
every module a *selected* provider needs call ``is_installed``; for the subset that loads
native code, and only for providers actually being composed, call ``load_error`` as well; and
collect the whole failure set before raising, so a user missing two extras learns both from one
run rather than one per attempt.

:func:`resolve_dependencies` therefore **returns** its failures instead of raising them, and
the caller renders every one before raising the first as the exit-status carrier. That split is
what makes "both from one run" true: ``MissingDependencyError`` has room for exactly one
package, so a single raise can only ever teach one.

:data:`FEATURE_MODULES` is the import-name-to-extra table, and it lives here rather than in the
domain for the reason the port gives: it is composition-root data, and the domain is forbidden
from knowing third-party names.

``rmspec --help`` selects no feature, so :func:`resolve_dependencies` over an empty feature set
probes nothing -- and because it is this module that holds the adapters, ``--help`` never gets
here at all. :func:`require_engines` passes ``rmspec.ocr.require_backends`` only the engines a
composition actually contains, so a Textract-only run is never told to install ``pyobjc``.

The unloadable state has nowhere structured to go, and that is recorded not hidden
---------------------------------------------------------------------------------
``MissingDependencyError`` fits the *absent* state only: its message reads "is not installed"
and its remediation is ``uv sync --extra <extra>``, both wrong for a wheel that is installed
whose dylib is missing, and its fields have no slot for the loader's message. The port's
docstring says so, and says the sibling error does not exist yet. So
:attr:`DependencyFailure.detail` carries the loader's words, the renderer prints them, and the
raised error is still the closest one the domain owns. Nothing is invented here.

Bindings: USB is the default now, and one port still crosses to SSH to work
--------------------------------------------------------------------------
USB is the default read path -- ``GET /download/{id}/rmdoc`` is served **by** xochitl and so is
a consistent snapshot by construction, while reading ``.rm`` off disk is the torn-read hazard
measured on ``rm-search-index.db``. This container composed **SSH** for one release because
``UsbWebApi`` was believed to need an ``httpx.Client`` handed to it. It does not:
``UsbWebApi.over_usb(endpoint)`` builds and owns that client, and pairs with an idempotent
``close()``. So ``httpx`` still never appears in this package -- which is what the old
restriction was actually protecting -- and :func:`composed_transport` reads the transport off
the settings rather than asserting one.

One port does not follow the transport, and that is a measured cost rather than an oversight.
``SearchIndexSource`` has **no USB binding and never will**: the firmware's HTTP route table is
closed at six families and none of them serves a file from the xochitl tree. So a ``usb`` run
that wants tier-0 handwriting text, or the search index at all, opens an SSH session *as well*.
:func:`describe_bindings` reports that as a limit on the USB transport rather than hiding it,
because it is exactly the "this transport cannot, SSH can" distinction that
``DeviceFacts.unsupported: frozenset[str]`` structurally cannot express.

``mirror`` is a value :class:`~rmspec.cli._settings.Transport` accepts and nothing implements
yet. It raises ``DeviceOperationUnsupported`` naming the two transports that do serve the
operation, rather than falling back to SSH -- a silent fallback would make
``RMSPEC_TRANSPORT=mirror`` look like it worked while every read still went to the tablet.

The bridge, and why ``RenderPages`` was not changed to fit
---------------------------------------------------------
:class:`~rmspec.app.diagrams.ExtractDiagrams` and
:class:`~rmspec.app.page_annotations.ReadAnnotations` each declare a structural
``PageRasterizer`` wanting ``raster_for(doc_id, page_id, /) -> RasterizedPage`` with a
``render_digest`` **per page**. :class:`~rmspec.app.RenderPages` offers ``render(request) ->
RenderPagesResult`` with ``render_digest`` on the *result* and ``raster: RasterImage | None``
per page. Three mismatches, and none of them is a defect in ``RenderPages``: ``raster`` must
stay optional because SVG-only render is a real mode, and a per-result digest is right for a
batch. So the adaptation lives here, in :class:`RenderPagesRasterizer`, which is where an
impedance mismatch between two layers belongs.

One class satisfies both protocols, because the annotations variant is a superset: adding
``page_box`` and a keyword-only ``underlay`` with a default leaves ``raster_for(doc_id,
page_id, /)`` callable exactly as the diagrams protocol declares it.

The second bridge, one layer down: a ``DocumentRepository`` over ``RawBundleSource``
------------------------------------------------------------------------------------
``ExtractDiagrams`` and ``ReadAnnotations`` both ask for
:class:`~rmspec.domain.ports.formats.DocumentRepository`, which this container bound to
``XochitlDocumentRepository`` -- a *filesystem* mirror rooted at ``RMSPEC_XOCHITL``. So with that
variable unset both commands died at composition, and the refusal named a directory **no command
in this tool creates**: ``rmspec sync`` populates the SQLite store at ``RMSPEC_SYNC_DB``, which is
a different artifact. Measured after a successful 41-document sync, ``rmspec diagram`` and
``rmspec annotations`` both exited 78 with ``XochitlDirNotConfigured`` while ``rmspec render``,
which asks for ``RawBundleSource`` instead, rendered the same page over USB.

The read path existed; those two use cases just asked for a different port. So
:class:`BundleDocumentRepository` satisfies ``DocumentRepository`` from ``RawBundleSource`` plus
:class:`~rmspec.domain.ports.formats.PageCodec`, and ``RMSPEC_XOCHITL`` becomes what it should
always have been -- an *offline* convenience rather than a prerequisite.

It lives here for the same reason :class:`RenderPagesRasterizer` does, and for one more: it names
no adapter and no third-party module at all. It composes two domain ports into a third using only
``rmspec.domain`` types, so neither slice on either side of it can host it without acquiring a
dependency on the other. ``rmspec-formats`` would have to import
``rmspec.domain.ports.device``, and ``ports/formats.py`` keeps remote reads a device-slice
concern; ``rmspec-device`` would have to import ``rmspec.domain.ports.formats`` and take a
dependency on the codec seam the formats slice owns. Neither slice owns both sides. The
composition root does.

Precedence, and why the mirror wins when it is configured
---------------------------------------------------------
``RMSPEC_XOCHITL`` set binds the mirror; unset binds the bundle-backed repository. That
direction, and not the reverse, on three grounds. A configured mirror needs no device, so it is
the binding an offline run wants and the one a user who exported the variable asked for.
Preferring the tablet would make a configured mirror unreachable *and* would open a device
handshake on a host with no tablet attached. And the mirror answers two things a bundle cannot --
``list_documents`` and ``Page.pdf_page_index`` -- so preferring it loses nothing and gains both.

The choice is made in :func:`compose` rather than inside one provider, which is the opposite of
what :func:`_for_transport` does, and the difference is measured rather than stylistic. dishka
resolves a factory's parameters *before* calling it, and the bundle path's graph reaches
:meth:`DeviceProvider.shell`, which **connects**. A single provider taking both ``CliSettings``
and ``RawBundleSource`` would therefore open an SSH session on every mirror-backed run -- exactly
the regression this change exists to remove. ``_for_transport`` may decide inside one function
because both of its arguments are pure constructors; this decision cannot.

``DocumentRepository`` moved to ``Scope.REQUEST`` for both bindings. The bundle-backed one must
be: it holds a device handshake and memoizes one pulled document, and neither may outlive the
command. The mirror follows it so that :data:`REQUEST_SCOPED_PORTS` stays one fixed tuple -- a
port whose *scope* moved with an environment variable would make the manifest's whole purpose,
telling a caller which half it may resolve when, unanswerable. The cost is one stateless object
per command.

One pull per document per command, wherever the caller sits
-----------------------------------------------------------
A repository memo is not enough, because not every caller goes through the repository.
``rmspec annotations`` needs the underlay bytes as well as the ink, and it gets them by
resolving ``RawBundleSource`` and calling ``load_bundle`` on the port directly -- a call
:class:`BundleDocumentRepository`'s own memo structurally cannot see. So a no-mirror run pulled
one document twice: measured at **13.6 MB instead of 6.8 MB** for one command on a document
called ``Quick sheets``, and 5,481 bytes twice on a small one, which is why it went unnoticed.

:class:`MemoizingBundleSource` is bound in the adapter's place at ``Scope.REQUEST``, so the
sharing is a property of the *port* rather than of any one consumer, and the next caller to want
bytes from a document a use case is already reading pays nothing. The narrower alternative -- an
underlay accessor on ``DocumentRepository``, so the component that already memoizes serves both
-- would need a new method on a domain port and would make every implementation of it, the
mirror included, answer a question one caller asks.

``Scope.REQUEST`` is what makes it safe rather than a staleness bug, which is the same property
the device catalog's per-request listing memo depends on. The seam is single-slot for the same
reason the repository's memo is, and with more force: it is shared by every caller in the
request, and ``SyncDocuments`` walks the whole library through it.

The third bridge: two ``InkTextStyle`` classes and two ``InkText`` classes
-------------------------------------------------------------------------
``rmspec.domain.ports.render`` declares ``InkTextStyle``, ``InkText`` and ``PhysicalSize``;
``rmspec.render`` declares its own ``InkTextStyle`` and ``InkText`` under the same two names.
They are **not** the same objects -- ``rmspec.domain.ports.render.InkTextStyle is
rmspec.render.InkTextStyle`` is ``False`` -- and they are not the same shape either. The domain's
pair are ``pydantic.BaseModel`` subclasses; the render slice's are frozen dataclasses, which is
the split this repository draws everywhere: a port is data a use case validates, an adapter's own
value object is not.

So :class:`TextToInkEngraver` converts rather than forwards, and the field that makes the
conversion load-bearing is ``extent_mm``. ``rmspec.render.InkText.extent_mm`` is
``(left, top, right, bottom)`` in millimetres from the page's top-left;
``rmspec.domain.ports.render.InkText.extent_mm`` is a ``PhysicalSize``, a *size*, because
:class:`~rmspec.app.ReplyOnPage` computes ``request.top_mm + ink.extent_mm.height_mm`` to decide
whether the reply runs off the page. Handing the 4-tuple's third and fourth elements straight
through as width and height would double the placement offsets and refuse replies that fit; and
measuring the size from the ink's own bounding box instead of from the requested corner would
under-report by one side bearing on the right and by a whole ascender on the top -- measured, a
5mm em at ``top_mm=15`` puts the first ink at 16.46mm and the descender at 21.0mm, so the box
occupied *from where the caller asked* is 6.0mm tall and the box the ink's own bounds describe is
4.54mm. The second number is the one that lets a caller stack a reply under this one and have it
overlap. The conversion therefore subtracts the **requested** corner, which is also what makes
the use case's fit arithmetic exact rather than approximately right.

It lives here for the reason :class:`RenderPagesRasterizer`'s paragraph gives. ``rmspec.render``
cannot host it without importing the domain's port module to build the port's own return type,
which is the dependency direction ``rmspec.domain`` exists to forbid; and the app layer cannot
host it without importing ``rmspec.render``, which is the adapter import the CLI's own
architecture test bans. The composition root owns both sides, so it owns the translation.

The fourth bridge, and the largest: three more duplicated names, on the write path
------------------------------------------------------------------------------------
``rmspec.device.writeback`` declares ``SceneRead``, ``ScenePrecondition`` and
``SceneWriteReceipt``; ``rmspec.domain.ports.device`` declares three classes with those exact
names. **None of the three pairs is one class**, and this one is not merely a shape difference
either -- the device slice's receipt has ``path: RemotePath``, ``digest`` and ``pruned`` where the
domain's has ``location: str``, ``fingerprint``, ``replaced`` and ``visibility``. So
``SshSceneWriter`` does **not** satisfy ``SceneWriter``: ``ty`` reports "``SceneRead`` is not
assignable to ``SceneRead``", and binding it raw would fail again at run time when
``ReplyOnPageResult`` validated a foreign model into a declared one. Duck typing gets as far as
the three method names and stops.

:class:`WritebackSceneWriter` is that conversion, and it carries two things the other bridges do
not.

**An error translation.** ``SshSceneWriter`` raises ``DeviceProtocolError`` when a page's identity
moved between the read and the write, while :meth:`~rmspec.app.ReplyOnPage.reply` documents
``DeviceStateMismatchError`` for that case -- and ``DeviceStateMismatchError``'s own docstring
says the former was "the wrong stand-in", because a human picking up a stylus breaks the
*caller's* precondition rather than the firmware's contract. Both score ``EX_UNAVAILABLE``, so
nothing about a shell's exit status changes; what changes is the identity an agent branches on and
the ``retryable`` field that says re-reading is the recovery. The durable fix is one layer down,
in ``rmspec.device``, at which point the ``except`` here becomes dead code to delete.

**A record of each read, because the two slices do not fingerprint a scene the same way.** The
transport uses ``sha256(raw).hexdigest()``; the domain's ``SceneRead.precondition`` uses a tagged
digest over a framed stream, deliberately, so that "unchanged" has one definition rather than one
per transport. Both are 64 hex characters, so each satisfies the other's constraints and neither
is ever rejected -- they are simply never equal. Handing the domain's number to the transport as
its ``digest`` therefore typechecks, validates, and makes ``verify`` compare two unrelated values,
so **every reply would have been refused as though somebody had drawn on the page**. That is the
worst available failure: the exact refusal this whole command exists to report, produced by the
wiring instead of by a stylus, and indistinguishable from the real thing.

No function converts one into the other. What exists is the read that produced both, which is
what the port already points at -- ``read_scene`` is "the only way to obtain a
``ScenePrecondition``", and a write takes that precondition "unmodified". So the bridge files the
transport precondition under the domain one per read and translates a write by lookup. That is
also what ``Scope.REQUEST`` on this port is for: one command is one read and one write of the same
page through one object.

Why every port is imported at runtime, and :data:`BOUND_PORTS` exists
--------------------------------------------------------------------
dishka resolves a provider's annotations with ``get_type_hints``, so every name in a provider
signature must be importable at run time. Measured: moving one into ``if TYPE_CHECKING`` makes
container construction raise ``UndefinedTypeAnalysisError``, and dishka's own message says "if
you are using ``if TYPE_CHECKING`` to import 'Iterator' then try removing it". ruff's ``TC001``
and ``TC003`` want the opposite for any name used only in an annotation, and this repository
allows neither a ``noqa`` nor a config edit from here -- the same tension its ruff config
already documents for pydantic field annotations under ``runtime-evaluated-base-classes``.

So the names get real runtime uses rather than suppressions. :data:`BOUND_PORTS` is the
manifest of ports this container binds, which the container test iterates to assert that each
one resolves; and :data:`Finalized` names what a generator provider means in dishka, which is
where ``Iterator`` is spent. Both are things a composition root wants anyway.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from importlib.util import find_spec
from time import time_ns
from typing import TYPE_CHECKING, Final, Protocol
from uuid import uuid4

from dishka import Provider, Scope, from_context, make_container, provide
from pydantic import BaseModel, Field, ValidationError

from rmspec.app import (
    CreateDocument,
    ExtractDiagrams,
    ListDocuments,
    OperationLimit,
    PageSelection,
    PortBinding,
    ReadAnnotations,
    RenderPages,
    RenderPagesRequest,
    ReplyOnPage,
    ReportCapabilities,
    ReportCapabilitiesRequest,
    ReportDeviceFacts,
    ReportSyncHistory,
    ResolveDocument,
    SearchText,
    SyncDocuments,
    TranscribePages,
)
from rmspec.cli._output import CliOutput, ConsolePair, OutputMode
from rmspec.cli._settings import CliSettings, OcrEngineName, Transport
from rmspec.device import (
    ParamikoShell,
    SshBundleSource,
    SshCatalog,
    SshFacts,
    SshSearchIndexSource,
    SshUploader,
    UsbBundleSource,
    UsbCatalog,
    UsbFacts,
    UsbUploader,
    UsbWebApi,
)
from rmspec.device.addresses import SSH_PORT, WEB_API_PORT, Endpoint, RemotePath
from rmspec.device.writeback import (
    ABSENT_IDENTITY,
    UNDO_CREATE_OPERATION,
    SshSceneWriter,
)
from rmspec.device.writeback import ScenePrecondition as WritebackPrecondition
from rmspec.device.writeback import SceneRead as WritebackRead
from rmspec.device.writeback import SceneWriteReceipt as WritebackReceipt
from rmspec.domain.errors import (
    CorruptPageData,
    DeviceDocumentNotFound,
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceStateMismatchError,
    DocumentNotFound,
    DocumentStoreUnavailable,
    InvalidSettingError,
    MalformedDeviceMetadata,
    MalformedDocument,
    MissingDependencyError,
    PageNotFound,
    RasterizationFailed,
    TransportKind,
    UnsupportedPageFormat,
    UsageError,
)
from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    Document,
    DocumentKind,
    DocumentMetadata,
    DocumentSummary,
    Page,
    PageDefect,
    PageDefectCode,
    PageId,
    Palette,
    ScreenSpec,
    SourceKind,
)
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceFactsSource,
    DeviceFileType,
    DocumentUploader,
    RawBundleSource,
    ScenePrecondition,
    SceneRead,
    SceneVisibility,
    SceneWriter,
    SceneWriteReceipt,
    SearchIndexSource,
)
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.export import (
    PdfComposer,
    PdfPageReader,
    PhysicalSize,
    PixelSize,
    SvgRasterizer,
)
from rmspec.domain.ports.formats import (
    ABSENT_ARTIFACT_FINGERPRINT,
    DocumentRepository,
    PageCodec,
    SceneAppender,
)
from rmspec.domain.ports.ocr import HandwrittenTextIndex, TextRecognizer, VisionLanguageModel
from rmspec.domain.ports.persistence import DiagramCache, DocumentSyncStore, OcrCache, SyncAuditLog
from rmspec.domain.ports.render import (
    InkText,
    InkTextStyle,
    PageBackground,
    PageRenderer,
    RenderStyle,
    TextEngraver,
    TextStyle,
)
from rmspec.domain.ports.render import PhysicalSize as InkExtent
from rmspec.export import (
    CairoSvgPdfComposer,
    CairoSvgRasterizer,
    PdfSourceRegistry,
    PyMuPdfPageReader,
)
from rmspec.formats import (
    AppendOnlySceneWriter,
    SceneCodec,
    XochitlDocumentRepository,
    fingerprint_bytes,
)
from rmspec.ocr import (
    AppleVisionRecognizer,
    BdaRecognizer,
    BedrockOpenAiVisionModel,
    OcrEngine,
    TextractRecognizer,
    build_client,
    require_backends,
)
from rmspec.persistence import (
    DeviceSearchIndex,
    SqliteDatabase,
    SqliteDiagramCache,
    SqliteDocumentSyncStore,
    SqliteOcrCache,
    SqliteSyncAuditLog,
)
from rmspec.render import (
    LEGACY_MIN_PADDING_MM,
    SVG_RENDERER_REVISION,
    SvgPageRenderer,
    text_to_ink,
)
from rmspec.render import InkTextStyle as EngravingStyle

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from pathlib import Path

    from dishka import Container

    from rmspec.app import RenderPagesResult
    from rmspec.domain.models import DocumentId
    from rmspec.domain.ports.device import (
        DeviceDocument,
        DevicePageSource,
        DocumentSourceBundle,
    )
    from rmspec.domain.ports.export import RasterImage
    from rmspec.domain.ports.render import PageUnderlay

__all__ = [
    "APP_SCOPED_PORTS",
    "BOUND_PORTS",
    "FEATURE_MODULES",
    "PROBE_FAILURE_TYPES",
    "REQUEST_SCOPED_PORTS",
    "AdjudicatorModel",
    "BundleDocumentRepository",
    "BundleRepositoryProvider",
    "CoreProvider",
    "DependencyFailure",
    "DeviceProvider",
    "DocumentPageOrder",
    "ExportProvider",
    "Feature",
    "Finalized",
    "FormatsProvider",
    "ImportProbe",
    "Invocation",
    "MemoizingBundleSource",
    "MirrorRepositoryProvider",
    "OcrProvider",
    "OptionalModule",
    "PageBatchRenderer",
    "PageSceneWriteback",
    "PersistenceProvider",
    "RasterTemplate",
    "RasterizedPage",
    "ReaderModel",
    "RenderPagesRasterizer",
    "RenderProvider",
    "TextToInkEngraver",
    "UseCaseProvider",
    "WritebackSceneWriter",
    "compose",
    "composed_transport",
    "describe_bindings",
    "probe_features",
    "require_engines",
    "resolve_dependencies",
]

Finalized = Iterator
"""What a generator provider means in dishka: this dependency has a finalizer.

An alias rather than ``Iterator`` spelled inline, for two reasons that point the same way. It
reads as the intent -- ``-> Finalized[SqliteDatabase]`` says "a database that gets closed" where
``-> Finalized[SqliteDatabase]`` says "a stream of databases", which is not what happens. And it
gives ``collections.abc.Iterator`` the runtime use it must have, because dishka evaluates
annotations at run time while ``TC003`` would otherwise demand the import move into a
type-checking block and break every provider in this file.
"""


class ReaderModel(VisionLanguageModel, Protocol):
    """The tier-2 vision model: it reads the raster and sees no other engine's answer.

    A ``Protocol`` subclass that adds nothing, used purely as a distinct resolution key, because
    dishka cannot resolve one type to two different values and ``TranscribePages`` wants two
    vision models with different model ids.

    Notes
    -----
    ``NewType("ReaderModel", VisionLanguageModel)`` was the obvious spelling and does not
    typecheck: ``ty`` reports ``invalid-newtype`` -- "the base of a ``NewType`` is not allowed to
    be a protocol class" -- and then loses every attribute of the aliased type. A one-line
    ``Protocol`` subclass is the same idea with none of that, and it has a property ``NewType``
    lacks: binding it does **not** remove the bare :class:`VisionLanguageModel` key, which
    ``ExtractDiagrams`` and ``ReadAnnotations`` legitimately ask for because they do not care
    which of the two models they are handed.
    """


class AdjudicatorModel(VisionLanguageModel, Protocol):
    """The tier-3 vision model: it is shown tiers 0-2 and decides between them.

    The sibling key to :class:`ReaderModel`, for the same reason and by the same mechanism.
    Bound from ``RMSPEC_MERGE_MODEL``, so a run can read with one model and adjudicate with
    another -- which is the whole point of the tier, and would be unobservable if one model
    were bound twice.
    """


APP_SCOPED_PORTS: Final = (
    DependencyProbe,
    PageCodec,
    SceneAppender,
    PageRenderer,
    TextEngraver,
    SvgRasterizer,
    PdfComposer,
    PdfPageReader,
    DocumentSyncStore,
    OcrCache,
    DiagramCache,
    SyncAuditLog,
    Sequence[TextRecognizer],
    VisionLanguageModel,
    ReaderModel,
    AdjudicatorModel,
)
"""The ports bound once per process, which resolve without a request scope being entered.

``SceneAppender`` and ``TextEngraver`` are here because both say so on their own ports and both
are true of the shipped implementations: ``AppendOnlySceneWriter`` is stateless and performs no
I/O, and ``TextEngraver`` documents its scope as ``APP`` in the first line of its docstring --
"stateless, deterministic and thread-safe; every per-invocation input arrives as an argument, so
there is nothing to open, cache or close".
"""

REQUEST_SCOPED_PORTS: Final = (
    DeviceCatalog,
    RawBundleSource,
    DocumentUploader,
    SceneWriter,
    SearchIndexSource,
    DeviceFactsSource,
    HandwrittenTextIndex,
    DocumentRepository,
)
"""The ports bound once per command, which is one device handshake.

``SceneWriter`` is here because its own port docstring says so and gives the reason: "one
transport, one handshake, closed by one finalizer, so a command that reads a page and then writes
it does not open two sessions". It is the only port on this list that *changes* something a human
made by hand, and sharing one session between its read and its write is what makes the
precondition it re-checks describe the bytes it is about to replace.

``DocumentRepository`` is here rather than in the app-scoped half, and unconditionally so.
:class:`BundleDocumentRepository` holds a device handshake and memoizes one pulled document, so it
cannot outlive a command; ``XochitlDocumentRepository`` could be bound once per process but is
bound the same way, because a port whose scope moved with ``RMSPEC_XOCHITL`` would make this very
manifest -- which half a caller may resolve, and when -- a function of the environment.

Separated from the app-scoped ones because resolving any of these outside
``with container(context={Invocation: ...})`` raises, so a caller warming the graph up has to
know which half it may ask for at which point. dishka 1.10.1 has no built-in eager resolution --
``make_container`` takes no warmup parameter and its validation is structural only, proven by a
container whose factory raised passing validation and then failing on first ``get`` -- so the
eager pass is a manual loop, and a manual loop needs this split.
"""

BOUND_PORTS: Final = APP_SCOPED_PORTS + REQUEST_SCOPED_PORTS
"""Every port this container binds an implementation of, as the type objects themselves.

The manifest a container test iterates: for each entry, resolve it and assert something came
back, so a provider deleted or a ``provides=`` typo fails a test instead of failing a user's
command. Listing the types rather than their names is what makes that possible, and it is also
the runtime use that keeps them out of a type-checking block dishka cannot read.
"""

_TRANSPORT_KINDS: Final = {
    Transport.USB: TransportKind.USB_WEB_API,
    Transport.SSH: TransportKind.SSH,
    Transport.MIRROR: TransportKind.LOCAL_MIRROR,
}
"""The one mapping from the word a user types to the domain's name for that transport.

Two vocabularies, deliberately: ``RMSPEC_TRANSPORT=usb`` is what a shell writes, and
``TransportKind.USB_WEB_API`` is what a capability report reads. Neither is spelled for the
other's audience, so this dictionary is the only place they meet.
"""


def composed_transport(settings: CliSettings, /) -> TransportKind:
    """Name the transport this container will bind for these settings.

    Parameters
    ----------
    settings
        Supplies ``transport``.

    Returns
    -------
    TransportKind
        The domain's name for the configured transport.

    Notes
    -----
    This was ``COMPOSED_TRANSPORT = TransportKind.SSH``, a constant whose docstring said USB
    was unbuildable here because ``UsbWebApi`` took an ``httpx.Client`` and no public factory in
    ``rmspec.device`` built one. That premise stopped being true: ``UsbWebApi.over_usb`` is that
    factory, it owns the client it builds, and it pairs with an idempotent ``close()``. So the
    constant became this function, USB became the default, and ``httpx`` still never appears in
    this package -- which is what the old docstring was actually protecting.
    """
    return _TRANSPORT_KINDS[settings.transport]


PROBE_FAILURE_TYPES = (ImportError, OSError, RuntimeError)
"""What :meth:`ImportProbe.load_error` catches, and why it is not ``Exception``.

The port asks for totality, and this repository forbids a bare ``except Exception`` -- the
phrase appears eleven times across the tree, every time as the name of a legacy defect, and
``BLE001`` is selected with no per-file exemption for this package. So the tuple is the union
of what has actually been measured failing in this workspace: ``ImportError`` for an absent or
half-installed wheel, ``OSError`` for ``cairocffi``'s ``no library called "cairo-2" was found``
and ``weasyprint``'s pango equivalent, and ``RuntimeError`` because ``rmspec.ocr``'s own
``VisionFrameworkError`` is one and its availability check already catches it there.

The residual gap is real and is stated rather than papered over: a third-party module whose
top-level code raises something outside this tuple would propagate out of the probe. Closing it
needs either a ``BLE001`` exemption for this package or a sibling error in the domain, and both
are edits to files this module does not own.
"""

_TEXT_STYLE = TextStyle(family="Noto Sans, sans-serif", size_px=32.0, line_height=1.25)
"""The text style every in-tree renderer test already uses, hoisted to one place."""

_ONE_PAGE = 1
"""The page cap the rasterizer bridge renders under. It is asked for one page at a time."""


class Feature(StrEnum):
    """A capability a command selects, and the unit the eager pass probes.

    Not a command name and not a package name. A command selects the features it will use, the
    table below maps each to its modules, and the probe runs over exactly that union -- so
    ``rmspec env`` selecting nothing probes nothing, and no command can quietly require a
    module it never imports.
    """

    RASTER = "raster"
    """Turning an SVG page into pixels. ``cairocffi``, ``cairosvg``, ``PIL``."""

    PDF_READ = "pdf-read"
    """Reading text and page geometry out of a PDF the tablet is annotating. ``fitz``."""

    SCENE_DECODE = "scene-decode"
    """Parsing a v6 ``.rm`` scene. ``rmscene``."""

    DEVICE_SSH = "device-ssh"
    """Reaching the tablet over SSH. ``paramiko``."""

    OCR_APPLE_VISION = "ocr-apple-vision"
    """On-device handwriting recognition through the Vision framework. ``Quartz``."""

    OCR_TEXTRACT = "ocr-textract"
    """Handwriting recognition through AWS Textract. ``boto3``."""

    OCR_BDA = "ocr-bda"
    """Handwriting recognition through Bedrock Data Automation's sync read. ``boto3``."""

    MODEL_BEDROCK = "model-bedrock"
    """A vision-language model over Bedrock. ``boto3``."""

    MARKDOWN_PDF = "markdown-pdf"
    """Turning authored Markdown into a PDF to push. ``markdown``, ``weasyprint``.

    ``weasyprint`` is declared ``native=True`` and ``markdown`` is not, which is a measurement
    rather than a guess: with the loader path unset, ``markdown`` imports cleanly while
    ``weasyprint`` resolves as installed and then dies with ``OSError: cannot load library
    'libgobject-2.0-0'``. That is the ``cairocffi`` failure with a different library name, and
    a spec-only check would have reported the feature available and then failed mid-command.
    """


class OptionalModule(BaseModel, frozen=True, extra="forbid"):
    """One third-party module a feature needs, and how a user would obtain it."""

    package: str = Field(min_length=1)
    """The **import** name, which is often not the distribution name.

    ``pymupdf`` ships ``fitz`` and the Vision bindings ship ``Quartz``, and
    :meth:`DependencyProbe.is_installed` is documented to take the name you would actually
    import. Getting this wrong reports a present package as absent.
    """

    extra: str = Field(min_length=1)
    """The extra that installs it, which is what the remediation tells the user to run."""

    feature: str = Field(min_length=1)
    """What stops working without it, phrased for the error message's first clause."""

    native: bool
    """Whether importing it loads a native library, and so needs ``load_error`` too.

    No default. ``cairocffi`` resolves as installed and then dies in ``dlopen`` with ``OSError:
    no library called "cairo-2" was found``, so a spec-only check reports it available and the
    failure lands mid-command naming a C library. Deciding this per module is the whole reason
    the port has two methods rather than one predicate.
    """


FEATURE_MODULES: dict[Feature, tuple[OptionalModule, ...]] = {
    Feature.RASTER: (
        OptionalModule(
            package="cairocffi", extra="render", feature="rasterizing a page", native=True
        ),
        OptionalModule(
            package="cairosvg", extra="render", feature="rasterizing a page", native=True
        ),
        OptionalModule(package="PIL", extra="render", feature="resizing a raster", native=True),
    ),
    Feature.PDF_READ: (
        # `pymupdf`, not the `fitz` alias this probed until the built wheel was run against a
        # fresh resolution. `rmspec/export/_pymupdf.py` -- the only module here that touches
        # the library -- imports `pymupdf`, so probing `fitz` asked about a module the adapter
        # never loads: from pymupdf 1.28 `import fitz` prints "The `fitz` API is deprecated and
        # will be removed in future" to stderr, which is the human's channel, and when it is
        # removed `doctor` will report PDF reading unavailable while the adapter still works.
        # The lockfile pins 1.27.1, so this was reachable only from an installed artifact.
        OptionalModule(
            package="pymupdf", extra="render", feature="reading an annotated PDF", native=True
        ),
    ),
    Feature.SCENE_DECODE: (
        OptionalModule(
            package="rmscene", extra="render", feature="decoding a v6 scene", native=False
        ),
    ),
    Feature.DEVICE_SSH: (
        OptionalModule(
            package="paramiko",
            extra="device",
            feature="reaching the tablet over SSH",
            native=False,
        ),
    ),
    Feature.OCR_APPLE_VISION: (
        OptionalModule(
            package="Quartz",
            extra="ocr",
            feature="recognising handwriting on device",
            native=True,
        ),
    ),
    Feature.OCR_TEXTRACT: (
        OptionalModule(
            package="boto3",
            extra="aws",
            feature="recognising handwriting with Textract",
            native=False,
        ),
    ),
    Feature.OCR_BDA: (
        OptionalModule(
            package="boto3",
            extra="aws",
            feature="recognising handwriting with Bedrock Data Automation",
            native=False,
        ),
    ),
    Feature.MODEL_BEDROCK: (
        OptionalModule(
            package="boto3", extra="aws", feature="calling a model on Bedrock", native=False
        ),
    ),
    Feature.MARKDOWN_PDF: (
        OptionalModule(
            package="markdown",
            extra="push",
            feature="converting Markdown to HTML",
            native=False,
        ),
        OptionalModule(
            package="weasyprint",
            extra="push",
            feature="rendering HTML to a PDF",
            native=True,
        ),
    ),
}
"""Import name to extra, per feature. Composition-root data, which the domain may not hold.

Two features naming the same module is deliberate rather than duplication to factor out:
``boto3`` is the ``aws`` extra for both Textract and Bedrock, and a run selecting both must
report it once, which :func:`resolve_dependencies` achieves by keying on the package name.
"""

_FEATURE_ENGINES: dict[Feature, OcrEngine] = {
    Feature.OCR_APPLE_VISION: OcrEngine.APPLE_VISION,
    Feature.OCR_TEXTRACT: OcrEngine.AWS_TEXTRACT,
    Feature.OCR_BDA: OcrEngine.AWS_BDA,
}
"""Which recognisers a feature set implies, so ``require_backends`` is told only those."""

_SETTING_ENGINES: Final = {
    OcrEngineName.BDA: OcrEngine.AWS_BDA,
    OcrEngineName.TEXTRACT: OcrEngine.AWS_TEXTRACT,
    OcrEngineName.APPLE_VISION: OcrEngine.APPLE_VISION,
}
"""The word a user writes in ``RMSPEC_OCR_ENGINES`` to the engine ``rmspec.ocr`` names.

A third vocabulary and the last one: ``textract`` is what fits in an environment variable,
``OcrEngine.AWS_TEXTRACT`` is what the availability check keys on, and neither should be spelled
for the other's reader. Two dictionaries reach ``OcrEngine`` -- this one from a setting, and
:data:`_FEATURE_ENGINES` from the feature a command selected -- because the eager pass and the
binding are answering different questions.
"""

_BDA_PROJECT_SETTING: Final = "RMSPEC_BDA_PROJECT_ARN"
"""The setting an unusable Data Automation project ARN is reported against.

Spelled here rather than imported from ``rmspec.ocr``: an adapter has no business knowing the
name of the environment variable a particular front end reads, so ``bda.profile_arn_for`` raises
``ValueError`` and this module -- the composition root, which does know -- names the setting.
"""

_BDA_PROJECT_REQUIREMENT: Final = (
    "the ARN of a SYNC-type Bedrock Data Automation project, e.g. "
    "arn:aws:bedrock:us-west-2:123456789012:data-automation-project/abc123"
)
"""What the setting has to be, phrased to finish both halves of ``InvalidSettingError``.

Its message reads "setting X is Y, which is not Z" and its remediation "set X to Z", so this has
to work as the object of both. It names SYNC because an ``ASYNC`` project -- which is what the
console creates unless told otherwise -- is refused by the operation with ``Sync API only
supports SYNC project type``, and that is not checkable without a control-plane call.
"""


class DependencyFailure(BaseModel, frozen=True, extra="forbid"):
    """One module a selected provider needs and cannot use, with the reason if there is one."""

    package: str = Field(min_length=1)
    """The import name that failed."""

    extra: str = Field(min_length=1)
    """The extra that would supply it."""

    feature: str = Field(min_length=1)
    """What is unavailable without it."""

    detail: str | None
    """The loader's own message when the module is installed but will not import.

    ``None`` means simply absent. No default, so the two states are always distinguished at the
    construction site. The domain has no error carrying this string -- the port's docstring
    records that the sibling error does not exist yet -- so this field is where it lives until
    one does.
    """

    def as_error(self) -> MissingDependencyError:
        """Build the closest domain error for this failure.

        Returns
        -------
        MissingDependencyError
            Named for the package, the extra and the feature. Its wording fits the absent case
            exactly and the unloadable case only approximately, which is why :attr:`detail` is
            rendered alongside it rather than folded into it.
        """
        return MissingDependencyError(
            package=self.package,
            extra=self.extra,
            feature=self.feature,
        )


class ImportProbe:
    """The production :class:`~rmspec.domain.ports.errors.DependencyProbe`.

    ``find_spec`` answers installation without executing module code, and ``import_module``
    answers loadability by doing the real import -- which leaves the module in ``sys.modules``,
    so the adapter's own import afterwards is free.

    Notes
    -----
    Both answers are memoized. Neither can change inside one process, the port says a probe may
    memoize per module name, and the memo is also what keeps the two answers consistent across
    the several features that name ``boto3``.
    """

    def __init__(self) -> None:
        self._installed: dict[str, bool] = {}
        self._errors: dict[str, str | None] = {}

    def is_installed(self, module_name: str, /) -> bool:
        """Report whether a module of this name is discoverable on ``sys.path``.

        Parameters
        ----------
        module_name
            Top-level import name, never a dotted path and never a distribution name.

        Returns
        -------
        bool
            ``True`` when a spec is found. A statement about installation, not about loading:
            ``find_spec("cairocffi")`` succeeds on a host with no ``libcairo``.

        Notes
        -----
        Total. ``ImportError`` from a missing parent package and the ``ValueError`` raised for a
        name already in ``sys.modules`` with ``__spec__`` set to ``None`` are both answered
        ``False``, so a failure of the probe can never be mistaken for a failure of the thing
        being probed.
        """
        cached = self._installed.get(module_name)
        if cached is not None:
            return cached
        try:
            found = find_spec(module_name) is not None
        except (ImportError, ValueError):
            found = False
        self._installed[module_name] = found
        return found

    def load_error(self, module_name: str, /) -> str | None:
        """Report why importing ``module_name`` fails, or ``None`` when it succeeds.

        Parameters
        ----------
        module_name
            Top-level import name, under the same rules as :meth:`is_installed`.

        Returns
        -------
        str | None
            ``None`` when the import completed, otherwise the raised exception's message --
            the loader's own words, which name the C library the wheel does not carry. An
            exception with an empty message is reported as its class name, so the return value
            is never an empty string and a caller never renders a blank explanation.

        Notes
        -----
        Catches :data:`PROBE_FAILURE_TYPES`, whose docstring explains why that tuple and not
        ``Exception``.
        """
        if module_name in self._errors:
            return self._errors[module_name]
        reason: str | None = None
        try:
            import_module(module_name)
        except PROBE_FAILURE_TYPES as exc:
            reason = str(exc) or type(exc).__name__
        self._errors[module_name] = reason
        return reason


def resolve_dependencies(
    probe: DependencyProbe,
    features: Collection[Feature],
    /,
) -> tuple[DependencyFailure, ...]:
    """Probe every module the selected features need, and return **all** the failures.

    Parameters
    ----------
    probe
        The probe to fold over the modules. A port rather than a helper, so a test can exercise
        the missing-extra path without uninstalling a package from the interpreter it runs in.
    features
        The features a command actually composes. Empty probes nothing.

    Returns
    -------
    tuple[DependencyFailure, ...]
        One entry per unusable module, deduplicated by package name and ordered by it. Empty
        when everything selected is usable.

    Notes
    -----
    Returns rather than raises, because ``MissingDependencyError`` has room for one package and
    the requirement is that a user missing two extras learns both from one run. The caller
    renders the whole tuple and then raises.

    ``load_error`` is called only for modules marked ``native``, and only for features being
    composed, which is the restriction that keeps :meth:`ImportProbe.is_installed`'s
    prohibition on executing module code meaningful.
    """
    failures: dict[str, DependencyFailure] = {}
    for feature in features:
        for module in FEATURE_MODULES[feature]:
            if module.package in failures:
                continue
            failure = _probe_one(probe, module)
            if failure is not None:
                failures[module.package] = failure
    return tuple(failures[package] for package in sorted(failures))


def _probe_one(probe: DependencyProbe, module: OptionalModule, /) -> DependencyFailure | None:
    """Classify one module as absent, unloadable, or fine.

    Parameters
    ----------
    probe
        The probe to ask.
    module
        The module and its extra.

    Returns
    -------
    DependencyFailure | None
        ``None`` when the module is usable.
    """
    if not probe.is_installed(module.package):
        return DependencyFailure(
            package=module.package,
            extra=module.extra,
            feature=module.feature,
            detail=None,
        )
    if not module.native:
        return None
    detail = probe.load_error(module.package)
    if detail is None:
        return None
    return DependencyFailure(
        package=module.package,
        extra=module.extra,
        feature=module.feature,
        detail=detail,
    )


def probe_features(
    probe: DependencyProbe,
    features: Collection[Feature],
    /,
) -> tuple[DependencyFailure, ...]:
    """Run the whole eager pass: every module, then the recogniser backends that can run.

    Parameters
    ----------
    probe
        The probe to fold over the modules.
    features
        The features being composed.

    Returns
    -------
    tuple[DependencyFailure, ...]
        Every failure, module-level ones first. Never partial: this is the tuple that makes
        "a user missing two extras learns both from one run" true.

    Notes
    -----
    ``require_backends`` is the deeper check -- it makes the Vision framework actually read an
    image rather than merely importing ``Quartz`` -- so it runs *after* the module pass and only
    for recognisers whose modules already probed clean. Asking it about an engine whose package
    is absent would raise a second error about the same missing extra, and the user would learn
    one fact twice instead of two facts once.
    """
    failures = list(resolve_dependencies(probe, features))
    broken = {failure.package for failure in failures}
    healthy = {
        feature
        for feature in features
        if feature in _FEATURE_ENGINES
        and not any(module.package in broken for module in FEATURE_MODULES[feature])
    }
    try:
        require_engines(healthy)
    except MissingDependencyError as err:
        failures.append(
            DependencyFailure(
                package=err.package,
                extra=err.extra,
                feature=err.feature,
                detail=err.message,
            )
        )
    return tuple(failures)


def require_engines(features: Collection[Feature], /) -> None:
    """Ask ``rmspec.ocr`` to prove only the recognisers this composition contains.

    Parameters
    ----------
    features
        The selected features. Only the two that name a recogniser contribute.

    Raises
    ------
    MissingDependencyError
        A selected recogniser's backend is absent or unusable. Raised by
        ``rmspec.ocr.require_backends``, which is the one place that knows how to make the
        Vision framework read an image rather than merely importing it.

    Notes
    -----
    The engine set is derived from the features rather than passed in, so a Textract-only
    composition is structurally incapable of being told to install ``pyobjc``.
    """
    require_backends({_FEATURE_ENGINES[f] for f in features if f in _FEATURE_ENGINES})


class Invocation(BaseModel, frozen=True, extra="forbid"):
    """What one command invocation decided, as opposed to what the process configured.

    ``Scope.REQUEST``'s context object. The pair of it and :class:`CliSettings` is the whole
    input to a composition: settings come from the environment, this comes from the command
    line.
    """

    mode: OutputMode
    """Which of the three output modes this command is in.

    No default, and one value rather than two booleans: ``--json`` and ``--dense`` are mutually
    exclusive, so a command that carried both flags could be asked to be in two modes at once.
    ``CliOutput.machine_readable`` remains available and is defined as ``mode is
    OutputMode.JSON``, so no existing branch changed meaning when the third mode arrived.
    """


class RasterTemplate(BaseModel, frozen=True, extra="forbid"):
    """Every part of a render request except the two that vary per page.

    Deliberately not a :class:`~rmspec.app.RenderPagesRequest`. That model requires a non-empty
    ``document_uuid`` and a ``selection``, and a template carrying a placeholder for both would
    be a lie some later reader trusts. This holds what is genuinely constant for a run, and
    :meth:`request_for` supplies the rest.
    """

    screen: ScreenSpec
    """The panel being emulated, which fixes the page geometry."""

    palette: Palette
    """Pen colour to RGB, which for export is not the on-screen palette."""

    style: RenderStyle
    """Thickness, padding, text metrics and the renderer revision."""

    raster_dpi: int = Field(gt=0)
    """The raster density. 300 for recognition work, which is not the 229 of a 1:1 render.

    The number moved on 2026-08-30: a 1:1 render was 226 until then, which is ``RM2_SCREEN``'s
    density rather than this tablet's, so the sentence above was comparing 300 against the wrong
    panel. See :attr:`~rmspec.cli._settings.CliSettings.render_dpi` for the arithmetic. 300 itself
    did not move and is not a panel measurement at all -- it is the density the recognisers were
    tuned against, which is the whole reason the one legacy ``RMSPEC_DPI`` had to become two.
    """

    def request_for(
        self,
        doc_id: DocumentId,
        index: int,
        /,
        *,
        background: PageBackground | None,
    ) -> RenderPagesRequest:
        """Build the one-page render request the bridge needs.

        Parameters
        ----------
        doc_id
            The document to render from.
        index
            The zero-based page index within that document.
        background
            The underlay to composite beneath the ink, or ``None`` for bare ink.

        Returns
        -------
        RenderPagesRequest
            Capped at one page, which is where a cap belongs: at the boundary, before anything
            expensive, rather than inside a loop.
        """
        return RenderPagesRequest(
            document_uuid=doc_id.uuid,
            selection=PageSelection.of(index),
            max_pages=_ONE_PAGE,
            screen=self.screen,
            palette=self.palette,
            style=self.style,
            background=background,
            raster_dpi=self.raster_dpi,
        )


class PageBatchRenderer(Protocol):
    """Exactly what the bridge needs of :class:`~rmspec.app.RenderPages`, and nothing else.

    A structural type rather than the class, for the reason every port in this workspace is
    one: the bridge uses one method, so depending on the whole use case would make a test of
    the bridge assemble a bundle source, a codec, a renderer and a rasterizer in order to
    exercise a dictionary lookup. ``RenderPages`` satisfies this as written.
    """

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        """Render the requested pages.

        Parameters
        ----------
        request
            What to render.

        Returns
        -------
        RenderPagesResult
            The artifacts and the digest of the render that made them.
        """
        ...


class DocumentPageOrder(Protocol):
    """Exactly what the bridge needs of a :class:`DocumentRepository`: the page order.

    Same reasoning as :class:`PageBatchRenderer`. ``DocumentRepository`` declares six methods
    and the bridge calls one of them, so a test fake implements one.
    """

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Describe one document.

        Parameters
        ----------
        doc_id
            The document.

        Returns
        -------
        DocumentSummary
            Whose ``pages`` is the ordered tuple of page ids.
        """
        ...


@dataclass(frozen=True, slots=True)
class RasterizedPage:
    """One page's pixels paired with the digest of the render that produced them.

    Satisfies the ``RasterizedPage`` protocol both use cases declare. The digest comes off the
    ``RenderPagesResult`` rather than off the page, because that is where ``RenderPages`` puts
    it and moving it would change a shipped result model to suit a consumer.
    """

    raster: RasterImage
    """The pixels. Structurally a ``PageRasterLike``, which is what the port asks for."""

    render_digest: str
    """The result's digest, identical for every page of one render, which is correct."""


class RenderPagesRasterizer:
    """Adapt :class:`~rmspec.app.RenderPages` to the ``PageRasterizer`` both use cases want.

    Holds a use case, a repository, and a template. One instance satisfies the diagrams
    protocol and the annotations protocol at once, because the latter is a superset of the
    former.

    Notes
    -----
    The protocols identify a page by uuid and ``RenderPages`` selects pages by index, so the
    document summary closes the gap -- the same summary the calling use case is already
    iterating. It is memoized per document, because a 432-page document would otherwise re-read
    its metadata 432 times.
    """

    def __init__(
        self,
        *,
        pages: PageBatchRenderer,
        repository: DocumentPageOrder,
        template: RasterTemplate,
    ) -> None:
        self._pages = pages
        self._repository = repository
        self._template = template
        self._summaries: dict[DocumentId, DocumentSummary] = {}

    @property
    def page_box(self) -> PixelSize:
        """Give the pixel box a PDF underlay must be rasterized into.

        Returns
        -------
        PixelSize
            The screen's physical size at the template's raster density. ``ReadAnnotations``
            needs this because a PDF page has to be scaled into the ink's coordinate space, and
            only whatever decided the density knows the scale.
        """
        return PixelSize.from_dpi(
            PhysicalSize(
                width_mm=self._template.screen.width_mm,
                height_mm=self._template.screen.height_mm,
            ),
            self._template.raster_dpi,
        )

    def raster_for(
        self,
        doc_id: DocumentId,
        page_id: PageId,
        /,
        *,
        underlay: PageUnderlay | None = None,
    ) -> RasterizedPage:
        """Render exactly one page and hand back its pixels and the render's digest.

        Parameters
        ----------
        doc_id
            The document.
        page_id
            The page, by uuid, which is how both protocols name it.
        underlay
            A PDF page to composite beneath the ink. Keyword-only with a default, which is what
            lets the diagrams protocol's two-positional-argument call still bind.

        Returns
        -------
        RasterizedPage
            The page's raster paired with the result's ``render_digest``.

        Raises
        ------
        PageNotFound
            The document has no page with that uuid, which the summary can say before any
            rendering is paid for.
        RasterizationFailed
            The render produced no raster. Structurally possible because ``RenderPages`` leaves
            ``raster`` optional for SVG-only mode, and narrowing it is the bridge's job rather
            than the use case's.
        """
        summary = self._summary(doc_id)
        if page_id not in summary.pages:
            raise PageNotFound(
                document_uuid=doc_id.uuid,
                page=page_id.uuid,
                page_count=len(summary.pages),
            )
        background = None if underlay is None else PageBackground(underlay=underlay)
        result = self._pages.render(
            self._template.request_for(
                doc_id,
                summary.pages.index(page_id),
                background=background,
            )
        )
        artifact = result.pages[0]
        if artifact.raster is None:
            raise RasterizationFailed(
                backend="RenderPages",
                detail="the render returned no raster, so raster_dpi did not reach it",
                page_ref=artifact.page_ref,
            )
        return RasterizedPage(raster=artifact.raster, render_digest=result.render_digest)

    def _summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Read the document summary once per document.

        Parameters
        ----------
        doc_id
            The document.

        Returns
        -------
        DocumentSummary
            From the memo when it has already been read.
        """
        cached = self._summaries.get(doc_id)
        if cached is not None:
            return cached
        summary = self._repository.summary(doc_id)
        self._summaries[doc_id] = summary
        return summary


_SOURCE_KINDS: Final = {
    DeviceFileType.NOTEBOOK: SourceKind.NOTEBOOK,
    DeviceFileType.PDF: SourceKind.PDF,
    DeviceFileType.EPUB: SourceKind.EPUB,
}
"""The device slice's name for what a document was made from, in the store slice's vocabulary.

Two closed enums with the same three members and two audiences: ``DeviceFileType`` is what a
transport read off the tablet, ``SourceKind`` is what ``DocumentMetadata`` publishes. Total by
construction over the members that exist today, and a test asserts every ``DeviceFileType`` has an
entry, so widening either enum fails here rather than raising ``KeyError`` at a user.
"""

_ABSENT_SCENE_DETAIL: Final = (
    "the device listed this page and its bundle carried no scene bytes for it, "
    "which is the ordinary state of an unannotated page"
)
"""What ``PageDefectCode.ARTIFACT_ABSENT`` says when the absence came off a bundle.

``DevicePageSource.scene`` is ``None`` for a page with no ink, and the port defines that as a
value rather than an error, so the sentence has to describe a routine state without sounding like
a fault.
"""


def _metadata_of(document: DeviceDocument, /) -> DocumentMetadata:
    """Translate a device document's metadata into the store slice's model.

    Parameters
    ----------
    document
        What the catalog reported, carried on the bundle.

    Returns
    -------
    DocumentMetadata
        With ``kind`` fixed at :attr:`DocumentKind.DOCUMENT`, because
        :class:`~rmspec.domain.ports.device.RawBundleSource` raises rather than returning a bundle
        for a folder identifier, so a bundle-backed summary can never describe a collection.

    Notes
    -----
    Four fields are the model's declared defaults rather than facts: ``pinned``, ``synced``,
    ``version`` and ``last_opened_page``. ``DocumentSourceBundle`` does not carry them, and unlike
    ``source``, ``layout`` and ``last_opened`` -- which are ``None`` for "unrecorded" -- those four
    have no spelling for "unknown", so this reports ``False``, ``False``, ``0`` and ``0``. Neither
    of the two use cases reading a bundle-backed repository looks at any of them, and the honest
    fix is a domain change rather than a guess here, so the state is recorded and not papered over.
    """
    return DocumentMetadata(
        visible_name=document.name,
        kind=DocumentKind.DOCUMENT,
        source=_SOURCE_KINDS[document.file_type],
        parent_uuid=document.parent_uuid,
        trashed=document.trashed,
        last_modified=document.last_modified,
    )


def _fingerprint_of(source: DevicePageSource, /) -> str:
    """Fingerprint one page of a bundle exactly as the mirror repository would.

    Parameters
    ----------
    source
        The page as the device holds it.

    Returns
    -------
    str
        ``rmspec.formats.fingerprint_bytes`` of the scene bytes, or
        :data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` when there are none.

    Notes
    -----
    The same function ``XochitlDocumentRepository`` calls, deliberately: identical scene bytes then
    fingerprint identically whichever store served them, so a diagram or OCR row cached from the
    mirror is a cache *hit* from the tablet instead of a second bill for the same page.
    """
    if source.scene is None:
        return ABSENT_ARTIFACT_FINGERPRINT
    return fingerprint_bytes(source.scene)


class BundleDocumentRepository:
    """Satisfy ``DocumentRepository`` from a ``RawBundleSource`` and a ``PageCodec``.

    The bridge that lets ``rmspec diagram`` and ``rmspec annotations`` read a document over the
    same transport ``rmspec render`` and ``rmspec sync`` already use, instead of requiring a local
    mirror at ``RMSPEC_XOCHITL`` that no command in this tool creates.

    Notes
    -----
    Five of the port's six methods are answerable from one bundle. :meth:`list_documents` is not,
    and it raises rather than inventing an answer -- see its own docstring.

    The bundle is memoized in a **single slot**, keyed by document. The two use cases call
    ``summary``, ``load_page`` and ``page_fingerprint`` for the same document, which would
    otherwise be three whole-document pulls, and over USB a pull is a ``.rmdoc`` archive xochitl
    assembled. One slot rather than a dictionary because a sync-shaped caller walking 41 documents
    would otherwise accumulate the entire library in memory; the memo therefore holds at most one
    document and the port's own ``Scope.REQUEST`` lifetime bounds even that.

    Every failure ``RawBundleSource`` documents is translated into the error
    ``DocumentRepository`` documents, with the device error chained as ``__cause__`` and its own
    words carried in ``detail``. Letting a ``DeviceError`` out would mean a use case catching
    ``DocumentSourceError`` -- "I could not get the document" -- did not catch an unreachable
    tablet.
    """

    def __init__(
        self,
        *,
        bundles: RawBundleSource,
        codec: PageCodec,
        transport: TransportKind,
    ) -> None:
        self._bundles = bundles
        self._codec = codec
        self._transport = transport
        self._store = f"the tablet over {transport.value}"
        self._memo: tuple[DocumentId, DocumentSourceBundle] | None = None

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """Refuse, because a bundle source cannot enumerate page order cheaply.

        Returns
        -------
        tuple[DocumentSummary, ...]
            Never. The signature is the port's.

        Raises
        ------
        DeviceOperationUnsupported
            Always. This is the one method of ``DocumentRepository`` a bundle source genuinely
            cannot answer, and the two honest-looking alternatives are both worse than refusing.

        Notes
        -----
        ``DocumentSummary`` carries *page identities in document order*, and a
        :class:`~rmspec.domain.ports.device.DeviceCatalog` reports a page **count** and no
        identities. So the only way to build one summary per document is one
        ``load_bundle`` per document -- measured at 41 documents and 609 pages over USB -- which
        flatly contradicts the method's own contract that "listing and tree rendering never pay to
        decode scene bytes". Returning summaries with ``pages=()`` is worse still: the model
        defines an empty tuple as "a folder, or an empty notebook", so every document in the
        library would be reported as empty.

        This is deliberately *off* the port's documented ``Raises`` set, which lists only
        ``DocumentStoreUnavailable``. That error would be a lie -- the store is reachable and
        healthy, and it is this altitude of question that cannot be served -- so the closest
        error the domain owns is raised instead and the divergence is stated here. Closing it
        needs either page identities on ``DeviceDocument`` or a
        ``DocumentStoreOperationUnsupported`` sibling in :mod:`rmspec.domain.errors`, and both are
        changes to files this module does not own.
        """
        raise DeviceOperationUnsupported(
            transport=self._transport,
            operation="list every document's page order without pulling every document",
            supported_by=(TransportKind.LOCAL_MIRROR,),
        )

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Summarise one document from its bundle.

        Parameters
        ----------
        doc_id
            Identity of the document to summarise.

        Returns
        -------
        DocumentSummary
            Page identities in the order the device recorded, which is the order
            ``DocumentSourceBundle.pages`` is already in.

        Raises
        ------
        DocumentNotFound
            The device holds no such document, or the identifier names a folder.
        MalformedDocument
            The device's metadata or page order could not be decoded into an ordered bundle.
        DocumentStoreUnavailable
            The tablet could not be reached, refused the credentials, answered unintelligibly, or
            ended the transfer early.
        """
        return self._summary(self._bundle(doc_id), doc_id)

    def load(self, doc_id: DocumentId, /) -> Document:
        """Load one whole document, decoding every page the bundle carried.

        Parameters
        ----------
        doc_id
            Identity of the document to load.

        Returns
        -------
        Document
            Pages in document order. A page with no scene bytes and a page whose scene would not
            decode are both present and contentless, carrying the defect that says which.

        Raises
        ------
        DocumentNotFound
            The device holds no such document, or the identifier names a folder.
        MalformedDocument
            The device's metadata or page order could not be decoded, or it names a page whose
            identifier is not a usable one.
        DocumentStoreUnavailable
            The tablet could not be reached or read.
        """
        bundle = self._bundle(doc_id)
        return Document(
            doc_id=doc_id,
            metadata=_metadata_of(bundle.document),
            pages=tuple(
                self._page(index, source, doc_id) for index, source in enumerate(bundle.pages)
            ),
        )

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Load a single page of a document.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page, as the summary listed it.

        Returns
        -------
        Page
            Exactly the page :meth:`load` would place at this identity. ``pdf_page_index`` is
            always ``None``: ``DevicePageSource`` carries a page identifier and a template and
            nothing else, so the ``.content`` sidecar's redirection map does not survive the
            ``RawBundleSource`` boundary. A caller reading a reordered PDF therefore falls back to
            the page's position and records ``DegradationKind.PDF_PAGE_INDEX_FALLBACK``, which is
            the degradation the domain declares for exactly this.

        Raises
        ------
        DocumentNotFound
            The device holds no such document, or the identifier names a folder.
        PageNotFound
            The document exists and claims no page with this identity.
        MalformedDocument
            The device's metadata or page order could not be decoded.
        DocumentStoreUnavailable
            The tablet could not be reached or read.
        """
        bundle = self._bundle(doc_id)
        index = self._index_of(bundle, doc_id, page_id)
        return self._page(index, bundle.pages[index], doc_id)

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Fingerprint one page's scene bytes, for cache invalidation.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page to fingerprint.

        Returns
        -------
        str
            Lowercase hex SHA-256 of the scene bytes as the device served them, or
            :data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` when the page carries
            none.

        Raises
        ------
        DocumentNotFound
            The device holds no such document, or the identifier names a folder.
        PageNotFound
            The document exists and claims no page with this identity.
        MalformedDocument
            The device's metadata or page order could not be decoded.
        DocumentStoreUnavailable
            The tablet could not be reached or read.
        """
        bundle = self._bundle(doc_id)
        return _fingerprint_of(bundle.pages[self._index_of(bundle, doc_id, page_id)])

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Fingerprint every page of one document from the one bundle.

        Parameters
        ----------
        doc_id
            Identity of the document whose pages to fingerprint.

        Returns
        -------
        Mapping[PageId, str]
            One entry per page the device listed, in document order, each value what
            :meth:`page_fingerprint` returns for that page. This is the batching freedom the
            coarse port exists to keep: one pull answers a 200-page document.

        Raises
        ------
        DocumentNotFound
            The device holds no such document, or the identifier names a folder.
        MalformedDocument
            The device's metadata or page order could not be decoded, or it names a page whose
            identifier is not a usable one.
        DocumentStoreUnavailable
            The tablet could not be reached or read.
        """
        bundle = self._bundle(doc_id)
        return {
            self._page_id(source.page_id, doc_id): _fingerprint_of(source)
            for source in bundle.pages
        }

    def _summary(self, bundle: DocumentSourceBundle, doc_id: DocumentId, /) -> DocumentSummary:
        """Build the summary a bundle already contains.

        Parameters
        ----------
        bundle
            The fetched bundle.
        doc_id
            The identity the caller asked for, which the port guarantees the bundle carries.

        Returns
        -------
        DocumentSummary
            Metadata plus page identities in document order.
        """
        return DocumentSummary(
            doc_id=doc_id,
            metadata=_metadata_of(bundle.document),
            pages=tuple(self._page_id(source.page_id, doc_id) for source in bundle.pages),
        )

    def _page(self, index: int, source: DevicePageSource, doc_id: DocumentId, /) -> Page:
        """Turn one page of a bundle into a domain page.

        Parameters
        ----------
        index
            Zero-based position in document order.
        source
            The page as the device holds it.
        doc_id
            The owning document, for the error a bad page identifier raises.

        Returns
        -------
        Page
            With content when the scene decoded, and contentless plus the defect that says why
            when it was absent or would not decode. Neither ``CorruptPageData`` nor
            ``UnsupportedPageFormat`` leaves this method, which is what the port requires.
        """
        page_id = self._page_id(source.page_id, doc_id)
        if source.scene is None:
            return Page(
                page_id=page_id,
                index=index,
                template_name=source.template_name,
                defects=(
                    PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail=_ABSENT_SCENE_DETAIL),
                ),
            )
        try:
            content = self._codec.decode_page(source.scene, page_id.uuid)
        except (CorruptPageData, UnsupportedPageFormat) as exc:
            return Page(
                page_id=page_id,
                index=index,
                template_name=source.template_name,
                defects=(PageDefect(code=PageDefectCode.CONTENT_UNDECODABLE, detail=str(exc)),),
            )
        return Page(
            page_id=page_id,
            index=index,
            template_name=source.template_name,
            content=content,
        )

    def _index_of(
        self,
        bundle: DocumentSourceBundle,
        doc_id: DocumentId,
        page_id: PageId,
        /,
    ) -> int:
        """Find one page's position in document order.

        Parameters
        ----------
        bundle
            The fetched bundle.
        doc_id
            The owning document.
        page_id
            The page being addressed.

        Returns
        -------
        int
            Zero-based position of the page in ``bundle.pages``.

        Raises
        ------
        PageNotFound
            The document claims no page with this identity. Compared as raw strings so that a
            page identifier the device holds but this domain would refuse cannot be reported as
            merely absent.
        """
        for index, source in enumerate(bundle.pages):
            if source.page_id == page_id.uuid:
                return index
        raise PageNotFound(
            document_uuid=doc_id.uuid,
            page=page_id.uuid,
            page_count=len(bundle.pages),
        )

    def _page_id(self, raw: str, doc_id: DocumentId, /) -> PageId:
        """Turn a device page identifier into the domain's value object.

        Parameters
        ----------
        raw
            The identifier the device recorded.
        doc_id
            The owning document, for the error.

        Returns
        -------
        PageId
            The validated identity.

        Raises
        ------
        MalformedDocument
            The device named a page whose identifier ``PageId`` refuses. That constraint is a
            charset, a length and a dot-segment refusal, and it exists so an identifier cannot be
            joined into a path that escapes its store -- so this is a real guard on untrusted
            input from the tablet, not a formality, and "the page order will not decode" is the
            document-level failure the port declares for it.
        """
        try:
            return PageId(uuid=raw)
        except ValidationError as exc:
            raise MalformedDocument(
                document_uuid=doc_id.uuid,
                artifact="page order",
                detail=f"the device names a page {raw!r} that is not a usable page identifier",
            ) from exc

    def _bundle(self, doc_id: DocumentId, /) -> DocumentSourceBundle:
        """Fetch one document's bundle, or hand back the one already held.

        Parameters
        ----------
        doc_id
            The document to fetch.

        Returns
        -------
        DocumentSourceBundle
            From the single-slot memo when this is the document it holds.
        """
        memo = self._memo
        if memo is not None and memo[0] == doc_id:
            return memo[1]
        bundle = self._fetch(doc_id)
        self._memo = (doc_id, bundle)
        return bundle

    def _fetch(self, doc_id: DocumentId, /) -> DocumentSourceBundle:
        """Pull one document, translating every device failure into a store failure.

        Parameters
        ----------
        doc_id
            The document to pull.

        Returns
        -------
        DocumentSourceBundle
            The device's ordered pages, their scenes, and any underlay.

        Raises
        ------
        DocumentNotFound
            From ``DeviceDocumentNotFound``: no such document, or the identifier names a folder.
        MalformedDocument
            From ``MalformedDeviceMetadata``: the entry exists and no ordered bundle can be built
            from it.
        DocumentStoreUnavailable
            From every remaining ``DeviceError`` -- unreachable, refused credentials, an
            unintelligible answer, or a transfer that ended early. All four are whole-store
            failures from a caller's side, and the device error's own sentence is carried in
            ``detail`` with the error itself chained as ``__cause__``.
        """
        try:
            return self._bundles.load_bundle(doc_id.uuid)
        except DeviceDocumentNotFound as exc:
            raise DocumentNotFound(query=doc_id.uuid, store=self._store) from exc
        except MalformedDeviceMetadata as exc:
            raise MalformedDocument(
                document_uuid=doc_id.uuid,
                artifact="device metadata",
                detail=str(exc),
            ) from exc
        except DeviceError as exc:
            raise DocumentStoreUnavailable(store=self._store, detail=str(exc)) from exc


class MemoizingBundleSource:
    """Satisfy ``RawBundleSource`` by pulling one document at most once per invocation.

    A seam, not an adapter: it names no transport and holds no connection. It wraps whichever
    adapter :meth:`DeviceProvider.bundles` chose and is bound in its place, so *every* caller
    that resolves ``RawBundleSource`` inside one request shares one transfer -- however many of
    them there are, and without any of them knowing about each other.

    The defect it closes
    --------------------
    ``rmspec annotations`` needs two things from one document: the ink, which
    :class:`BundleDocumentRepository` reads through ``DocumentRepository``, and the underlay
    bytes, which the command reads by resolving ``RawBundleSource`` and calling ``load_bundle``
    on it directly. The repository memoizes, but its memo is *inside* the repository and cannot
    see a call made straight on the port -- so a run with no mirror configured pulled the same
    document twice, and over USB a pull is a whole ``.rmdoc`` archive xochitl assembled.
    Measured on one command: **13.6 MB where 6.8 MB was needed** for a document called
    ``Quick sheets``; 5,481 bytes twice for a small one, which is why this went unnoticed. The
    mirror path never had the defect -- it mints a token for a path and transfers nothing.

    Fixing it here rather than on ``DocumentRepository``
    --------------------------------------------------
    The narrower fix is an underlay accessor on ``DocumentRepository``, so the one component
    that already memoizes serves both the ink and the print. That needs a new method on a domain
    port, and it would also make every implementation of that port -- the mirror included --
    answer a question only one caller asks. This seam needs no port change, and it generalises:
    the next caller to want bytes from a document a use case is already reading gets the sharing
    for free instead of adding a third pull.

    Why one slot and not a dictionary
    ---------------------------------
    Same reason :class:`BundleDocumentRepository` holds one, and with more force, because this
    object is shared by every caller in the request rather than by one use case's three calls.
    ``SyncDocuments`` resolves this port and walks the whole library -- 41 documents and 609
    pages measured -- so a dictionary would accumulate every ``.rmdoc`` in the account in RAM
    before the command ended. One slot collapses the burst of calls for *the same* document,
    which is the shape every caller actually makes, and costs a sync-shaped walk nothing: it
    already pulled each document once.

    Why this is safe, and what makes it safe
    ----------------------------------------
    ``Scope.REQUEST``, which is one command. Nothing memoized here can be served to a later
    command, so the staleness window is one invocation -- exactly the property that makes the
    device catalog's own per-request listing memo safe, and the reason that memo's scope is
    pinned by a test on the device side rather than left to a comment. Bound at ``Scope.APP``
    this would be a bug: a long-lived process would keep serving a document the tablet had since
    changed.

    A failed pull is not memoized. ``load_bundle`` raising means there is no bundle, so a second
    caller asks the device again rather than being handed a cached failure -- which also keeps
    every error this port documents arriving at the caller that provoked it.
    """

    def __init__(self, source: RawBundleSource, /) -> None:
        self._source = source
        self._memo: tuple[str, DocumentSourceBundle] | None = None

    @property
    def source(self) -> RawBundleSource:
        """The adapter underneath, which is the one that names a transport.

        Returns
        -------
        RawBundleSource
            Whichever of the two transports' bundle sources was composed. Exposed because the
            transport claim -- "USB is the default read path" -- is a claim about this object
            and not about the seam in front of it, and a test that could not see through the
            seam would have to stop asserting it.
        """
        return self._source

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Return the document's bundle, pulling it only if this is not the one already held.

        Parameters
        ----------
        doc_uuid
            Identity of the document to pull, exactly as the port declares it.

        Returns
        -------
        DocumentSourceBundle
            From the single slot when it holds this document, and from the wrapped source
            otherwise. The same object both times, so a caller must treat it as shared -- which
            costs nothing, because every model it carries is frozen.

        Raises
        ------
        DeviceError
            Whatever the wrapped source raises, unchanged and unmemoized. Translating anything
            here would put a second opinion about device failures in front of the one component
            that owns the translation, :class:`BundleDocumentRepository`.
        """
        memo = self._memo
        if memo is not None and memo[0] == doc_uuid:
            return memo[1]
        bundle = self._source.load_bundle(doc_uuid)
        self._memo = (doc_uuid, bundle)
        return bundle


class CoreProvider(Provider):
    """Settings, streams, the probe, and the use case that needs no collaborator.

    ``Scope.APP`` throughout: none of these can change inside one process.
    """

    scope = Scope.APP

    settings = from_context(provides=CliSettings, scope=Scope.APP)
    consoles = from_context(provides=ConsolePair, scope=Scope.APP)
    probe = provide(ImportProbe, provides=DependencyProbe)
    capabilities = provide(ReportCapabilities)


class PersistenceProvider(Provider):
    """The SQLite store and the four ports over it, opened once and closed by a finalizer."""

    scope = Scope.APP

    @provide
    def database(self, settings: CliSettings) -> Finalized[SqliteDatabase]:
        """Open the store and close it when the container closes.

        Parameters
        ----------
        settings
            Supplies ``sync_db``.

        Yields
        ------
        SqliteDatabase
            The open store.
        """
        database = SqliteDatabase.open(settings.sync_db)
        try:
            yield database
        finally:
            database.close()

    @provide
    def sync_store(self, database: SqliteDatabase) -> DocumentSyncStore:
        """Bind the mirror of the device library.

        Parameters
        ----------
        database
            The open store.

        Returns
        -------
        DocumentSyncStore
            The SQLite implementation.
        """
        return SqliteDocumentSyncStore(database)

    @provide
    def ocr_cache(self, database: SqliteDatabase) -> OcrCache:
        """Bind the recognition cache, keyed on the page's content hash.

        Parameters
        ----------
        database
            The open store.

        Returns
        -------
        OcrCache
            The SQLite implementation.
        """
        return SqliteOcrCache(database)

    @provide
    def diagram_cache(self, database: SqliteDatabase) -> DiagramCache:
        """Bind the diagram cache.

        Parameters
        ----------
        database
            The open store.

        Returns
        -------
        DiagramCache
            The SQLite implementation.
        """
        return SqliteDiagramCache(database)

    @provide
    def audit_log(self, database: SqliteDatabase) -> SyncAuditLog:
        """Bind the sync history.

        Parameters
        ----------
        database
            The open store.

        Returns
        -------
        SyncAuditLog
            The SQLite implementation.
        """
        return SqliteSyncAuditLog(database)


class FormatsProvider(Provider):
    """The two directions of the ``.rm`` seam: decode a scene, and append ink to one.

    The repository used to be here too. It moved to :class:`MirrorRepositoryProvider` and
    :class:`BundleRepositoryProvider`, one of which :func:`compose` selects, because binding it
    needs a decision that cannot be made inside a provider body -- see the module docstring.

    Notes
    -----
    ``SceneAppender`` is bound to ``AppendOnlySceneWriter`` rather than to ``SceneCodec``: they
    are separate classes because they are separate directions, and only the appender's own
    class has ``append_strokes``. Both are stateless and neither performs I/O, so both are
    ``Scope.APP`` and both take a bare constructor.
    """

    scope = Scope.APP

    codec = provide(SceneCodec, provides=PageCodec)
    appender = provide(AppendOnlySceneWriter, provides=SceneAppender)


class MirrorRepositoryProvider(Provider):
    """Bind ``DocumentRepository`` to the local xochitl mirror ``RMSPEC_XOCHITL`` names.

    Composed only when that setting has a value, which is why the root arrives on the constructor
    already narrowed rather than being re-checked -- and why this provider has no
    ``XochitlDirNotConfigured`` to raise. :func:`compose` is the one place that decides, so there
    is no second opinion here and no branch that only a mis-composition could reach.

    ``Scope.REQUEST`` although nothing here needs closing, so that ``DocumentRepository`` is bound
    at one scope whichever store serves it.
    """

    scope = Scope.REQUEST

    def __init__(self, root: Path, /) -> None:
        super().__init__()
        self._root = root

    @provide
    def repository(self, codec: PageCodec) -> DocumentRepository:
        """Bind the mirror.

        Parameters
        ----------
        codec
            The scene decoder.

        Returns
        -------
        DocumentRepository
            Reading the configured mirror, which needs no device and answers every method of the
            port.
        """
        return XochitlDocumentRepository(root=self._root, codec=codec)


class BundleRepositoryProvider(Provider):
    """Bind ``DocumentRepository`` over whatever transport serves ``RawBundleSource``.

    Composed when ``RMSPEC_XOCHITL`` is unset, which is the default. This is what makes
    ``rmspec diagram`` and ``rmspec annotations`` work on an attached tablet with nothing
    configured, instead of refusing and naming a directory no command creates.
    """

    scope = Scope.REQUEST

    @provide
    def repository(
        self,
        settings: CliSettings,
        bundles: RawBundleSource,
        codec: PageCodec,
    ) -> DocumentRepository:
        """Bind the bundle-backed repository.

        Parameters
        ----------
        settings
            Names the transport, which the repository reports in its own errors.
        bundles
            The live retrieval path, already chosen by the transport.
        codec
            The scene decoder.

        Returns
        -------
        DocumentRepository
            Answering five of six methods from one pull per document. ``list_documents`` refuses,
            for the reason its docstring gives.
        """
        return BundleDocumentRepository(
            bundles=bundles,
            codec=codec,
            transport=composed_transport(settings),
        )


class TextToInkEngraver:
    """Satisfy ``TextEngraver`` from :func:`rmspec.render.text_to_ink`, converting both ends.

    ``text_to_ink`` already has the port's exact signature -- ``(text, /, *, screen, style,
    left_mm, top_mm, width_mm)`` -- and already applies the centre-origin ``x_shift`` inside, so
    nothing about the placement is re-decided here. What it does not have is the port's *shape*:
    it is a function, and its ``style`` and its return value are the render slice's own
    dataclasses rather than the domain's pydantic models. See this module's docstring for why the
    translation belongs in the composition root.

    Notes
    -----
    Stateless, so ``Scope.APP`` and a bare constructor, exactly like ``SceneCodec``.

    The degenerate extent ``text_to_ink`` documents -- all four corners equal, for text that
    draws nothing -- would fail ``PhysicalSize``'s ``gt=0`` on both fields. It is deliberately
    not branched on, because it is unreachable through the only caller: ``ReplyOnPage`` strips
    the message and raises ``UsageError`` on a whitespace-only reply *before* engraving, and
    every remaining character draws something -- an undrawable one draws a struck box rather
    than nothing. A guard here would be an untestable branch asserting a contract two other
    layers already hold.
    """

    def engrave(
        self,
        text: str,
        /,
        *,
        screen: ScreenSpec,
        style: InkTextStyle,
        left_mm: float,
        top_mm: float,
        width_mm: float,
    ) -> InkText:
        """Engrave one message as ink, in the domain's own value types.

        Parameters
        ----------
        text
            The message, already stripped and known non-blank by the caller.
        screen
            The screen the ink is placed against, which supplies the DPI and the ``x_shift``.
        style
            The domain's em size, line height, colour and thickness slider.
        left_mm
            Left edge of the text box, in millimetres from the page's left edge.
        top_mm
            Top edge of the text box, in millimetres from the page's top edge.
        width_mm
            Wrap width in millimetres.

        Returns
        -------
        ~rmspec.domain.ports.render.InkText
            The strokes untouched -- they are already ``rmspec.domain.models.Stroke`` on both
            sides of this call -- with the extent converted from the render slice's
            ``(left, top, right, bottom)`` corners to the size the port declares.

        Notes
        -----
        The size is measured from the **requested** corner rather than from the ink's own
        bounding box, because that is what the port's one consumer does with it: ``ReplyOnPage``
        adds it to ``left_mm`` and ``top_mm`` to find the far edge, and a caller stacking a
        second reply adds ``height_mm`` to ``top_mm`` to find where the first one ended. Both
        arithmetics are exact this way and both are wrong by a side bearing the other way.
        """
        ink = text_to_ink(
            text,
            screen=screen,
            style=EngravingStyle(
                em_mm=style.em_mm,
                line_height=style.line_height,
                color=style.color,
                thickness_scale=style.thickness_scale,
            ),
            left_mm=left_mm,
            top_mm=top_mm,
            width_mm=width_mm,
        )
        _, _, right_mm, bottom_mm = ink.extent_mm
        return InkText(
            strokes=ink.strokes,
            lines=ink.lines,
            substituted=ink.substituted,
            extent_mm=InkExtent(width_mm=right_mm - left_mm, height_mm=bottom_mm - top_mm),
        )


class RenderProvider(Provider):
    """The SVG renderer, its style, the raster template the bridge works from, and the engraver."""

    scope = Scope.APP

    renderer = provide(SvgPageRenderer, provides=PageRenderer)
    engraver = provide(TextToInkEngraver, provides=TextEngraver)

    @provide
    def style(self, settings: CliSettings) -> RenderStyle:
        """Build the render style from the setting that was previously unread.

        Parameters
        ----------
        settings
            Supplies ``thickness``, which had zero readers while four command signatures
            hardcoded it.

        Returns
        -------
        RenderStyle
            Carrying the renderer revision, so a cached result cannot outlive its renderer.
        """
        return RenderStyle(
            thickness_scale=settings.thickness,
            min_padding_mm=LEGACY_MIN_PADDING_MM,
            text=_TEXT_STYLE,
            renderer_revision=SVG_RENDERER_REVISION,
        )

    @provide
    def template(self, settings: CliSettings, style: RenderStyle) -> RasterTemplate:
        """Build the raster template at recognition density.

        Parameters
        ----------
        settings
            Supplies ``ocr_dpi``, which is 300 -- the density three legacy commands hardcoded
            while the declared ``RMSPEC_DPI`` said 226 and nothing read it.
        style
            The render style.

        Returns
        -------
        RasterTemplate
            For the bridge to build one-page requests from.
        """
        return RasterTemplate(
            screen=PAPER_PRO_SCREEN,
            palette=EXPORT_PALETTE,
            style=style,
            raster_dpi=settings.ocr_dpi,
        )


class ExportProvider(Provider):
    """Rasterizing, PDF composition, and PDF reading."""

    scope = Scope.APP

    rasterizer = provide(CairoSvgRasterizer, provides=SvgRasterizer)
    composer = provide(CairoSvgPdfComposer, provides=PdfComposer)
    registry = provide(PdfSourceRegistry)

    @provide
    def pdf_reader(self, registry: PdfSourceRegistry) -> PdfPageReader:
        """Bind the PDF reader over the source registry.

        Parameters
        ----------
        registry
            Resolves a ``PdfSourceRef`` token to bytes on disk.

        Returns
        -------
        PdfPageReader
            Reading through PyMuPDF.
        """
        return PyMuPdfPageReader(registry=registry)


def _recognizer(name: OcrEngineName, /, *, settings: CliSettings) -> TextRecognizer:
    """Build the recogniser a user named, configured as they configured it.

    Parameters
    ----------
    name
        The engine as spelled in ``RMSPEC_OCR_ENGINES``.
    settings
        Supplies ``aws_region`` for both AWS engines, and the project and profile ``bda`` needs.
        The whole settings object rather than the two or three fields, because a per-engine
        argument list would grow with every engine and every engine would take the others'.

    Returns
    -------
    TextRecognizer
        ``BdaRecognizer.for_project`` and ``TextractRecognizer.in_region`` build and own their
        boto3 clients, and ``AppleVisionRecognizer.on_this_machine`` loads the Vision bindings by
        module name from inside itself -- so neither ``boto3`` nor the framework bindings are
        imported here, and importing this module does not touch either.

    Raises
    ------
    InvalidSettingError
        ``bda`` is selected and ``RMSPEC_BDA_PROJECT_ARN`` is unset or is not a project ARN.
        Raised while the binding is built, which is before the first page is rendered,
        rasterised or sent to anything -- not before the document lookup, which a command
        performs first. Left to the service, the same fault arrives as ``At least one of project
        or inline blueprints must be provided``: a ``RecognitionFailed`` naming no setting, after
        a page has already been rendered and rasterised.
    """
    if name is OcrEngineName.BDA:
        return _bda_recognizer(settings)
    if name is OcrEngineName.TEXTRACT:
        return TextractRecognizer.in_region(settings.aws_region)
    return AppleVisionRecognizer.on_this_machine()


def _bda_recognizer(settings: CliSettings, /) -> TextRecognizer:
    """Build the Data Automation recogniser, refusing an unusable project ARN by name.

    Parameters
    ----------
    settings
        Supplies ``bda_project_arn``, ``bda_profile`` and ``aws_region``.

    Returns
    -------
    TextRecognizer
        A recognizer over a ``bedrock-data-automation-runtime`` client.

    Raises
    ------
    InvalidSettingError
        The project ARN is unset, or is not a ``data-automation-project`` ARN. Two settings
        could be at fault and only one ever is: ``profile_arn_for`` composes the profile from
        the project's own partition, region and account, so a malformed profile ARN is not
        reachable and the error always names the setting the user actually wrote.
    """
    project_arn = settings.bda_project_arn
    if project_arn is None:
        raise InvalidSettingError(
            setting=_BDA_PROJECT_SETTING,
            value="",
            requirement=_BDA_PROJECT_REQUIREMENT,
        )
    try:
        return BdaRecognizer.for_project(
            project_arn,
            region_name=settings.aws_region,
            profile=settings.bda_profile,
        )
    except ValueError as exc:
        raise InvalidSettingError(
            setting=_BDA_PROJECT_SETTING,
            value=project_arn,
            requirement=_BDA_PROJECT_REQUIREMENT,
        ) from exc


def _bedrock_model(model_id: str, /, *, region: str) -> VisionLanguageModel:
    """Build one vision-language model over Bedrock.

    Parameters
    ----------
    model_id
        The model to call.
    region
        Where to call it.

    Returns
    -------
    VisionLanguageModel
        ``build_client`` is re-exported at ``rmspec.ocr``'s top level for exactly this: it takes
        the ``boto3`` factory as a defaulted argument, so the client is constructed inside the
        package that declares ``boto3`` and this one never names it.
    """
    return BedrockOpenAiVisionModel(
        build_client(region=region),
        model_id=model_id,
        region=region,
    )


class OcrProvider(Provider):
    """The recognisers a run selected, and the two vision models transcription plays off.

    ``Scope.APP`` throughout, which is what both ports ask for: a Textract client, a Vision
    framework handle and a Bedrock client are stateless process-lifetime resources.
    """

    scope = Scope.APP

    @provide
    def recognizers(self, settings: CliSettings) -> Sequence[TextRecognizer]:
        """Bind the recognisers ``RMSPEC_OCR_ENGINES`` selected, in a reproducible order.

        Parameters
        ----------
        settings
            Supplies ``ocr_engines`` and ``aws_region``.

        Returns
        -------
        Sequence[TextRecognizer]
            In :class:`~rmspec.cli._settings.OcrEngineName` declaration order rather than set
            order. ``ocr_engines`` is a ``frozenset``, so iterating it directly would make the
            tier ordering depend on the hash seed and two runs of the same command disagree.

        Raises
        ------
        MissingDependencyError
            A selected recogniser's backend is absent or unusable. ``require_backends`` is asked
            here rather than only in the eager pass so that a composition selecting
            ``apple_vision`` on a host without the bindings fails while it is being built,
            naming the extra, instead of part-way through a page.
        InvalidSettingError
            ``bda`` is selected without a usable ``RMSPEC_BDA_PROJECT_ARN``. The same argument
            as above, for the one engine whose obstacle is configuration rather than a package:
            before any page is rendered, though a command's document lookup runs first.
        """
        selected = tuple(name for name in OcrEngineName if name in settings.ocr_engines)
        require_backends({_SETTING_ENGINES[name] for name in selected})
        return tuple(_recognizer(name, settings=settings) for name in selected)

    @provide
    def read_model(self, settings: CliSettings) -> VisionLanguageModel:
        """Bind the bare vision-model port, which is the reading model.

        Parameters
        ----------
        settings
            Supplies ``read_model`` and ``aws_region``.

        Returns
        -------
        VisionLanguageModel
            What ``ExtractDiagrams`` and ``ReadAnnotations`` ask for. Both genuinely do not care
            which of the two models they get, so the bare key stays bound rather than being
            replaced by a role -- and it is the reading model because both of those use cases
            are reading a raster, which is the job :attr:`ReaderModel` describes.
        """
        return _bedrock_model(settings.read_model, region=settings.aws_region)

    @provide
    def reader(self, model: VisionLanguageModel) -> ReaderModel:
        """Bind the reading role to the same object the bare port resolves to.

        Parameters
        ----------
        model
            The model built from ``RMSPEC_READ_MODEL``.

        Returns
        -------
        ReaderModel
            The same instance, under the key ``TranscribePages`` names. One client for one model
            id, rather than two clients that would differ only in who asked for them.
        """
        return model

    @provide
    def adjudicator(self, settings: CliSettings) -> AdjudicatorModel:
        """Bind the adjudicating role from the other model id.

        Parameters
        ----------
        settings
            Supplies ``merge_model`` and ``aws_region``.

        Returns
        -------
        AdjudicatorModel
            A second model, deliberately: binding one model twice would make "the reader and the
            judge disagreed" unobservable, which is the only thing tier 3 is for.
        """
        return _bedrock_model(settings.merge_model, region=settings.aws_region)


def _for_transport[Bound](
    settings: CliSettings,
    /,
    *,
    operation: str,
    usb: Bound,
    ssh: Bound,
) -> Bound:
    """Pick the implementation the configured transport serves, or refuse in the domain's words.

    Parameters
    ----------
    settings
        Supplies ``transport``.
    operation
        What the caller was trying to do, phrased for a refusal a user reads.
    usb
        The implementation over the tablet's HTTP server.
    ssh
        The implementation over a shell session.

    Returns
    -------
    Bound
        Whichever of the two the transport names.

    Raises
    ------
    DeviceOperationUnsupported
        The transport is ``mirror``, which nothing implements yet. Naming the two transports
        that do serve the operation is the whole value of refusing here: a silent fallback to
        SSH would make ``RMSPEC_TRANSPORT=mirror`` look like it worked.

    Notes
    -----
    Both arguments are already built by the time this is called, which is deliberate and cheap:
    every one of the eight adapter constructors is pure assignment, and the two handles they
    wrap -- the shell and the web api -- are constructed unconnected, so composing the path not
    taken costs one object and no I/O. Deciding inside one function instead of swapping
    :class:`Provider` classes at :func:`compose` time is what keeps the transport a *setting*
    rather than a second composition root.
    """
    if settings.transport is Transport.USB:
        return usb
    if settings.transport is Transport.SSH:
        return ssh
    raise DeviceOperationUnsupported(
        transport=_TRANSPORT_KINDS[settings.transport],
        operation=operation,
        supported_by=(TransportKind.USB_WEB_API, TransportKind.SSH),
    )


def _stale_page(refused: DeviceProtocolError, /, *, fingerprint: str | None) -> bool:
    """Decide whether one refusal is the precondition check rather than a snapshot fault.

    Parameters
    ----------
    refused
        The refusal ``SshSceneWriter`` raised.
    fingerprint
        The identity the precondition described, or ``None`` for "there was no artifact".

    Returns
    -------
    bool
        ``True`` when the page's identity moved.

    Notes
    -----
    A **positive** test, and that direction is the whole design. Deciding by exclusion --
    "any ``DeviceProtocolError`` that is not one of the two snapshot faults" -- would
    misclassify a refusal added to that class later as "the human drew on your page", and
    telling somebody their handwriting was touched when it was not is worse than an
    under-specific error.

    The identity refusal describes both sides through ``rmspec.device.writeback``'s own private
    formatter, which renders a digest as ``sha256 <64 hex>`` and an absent artifact as
    :data:`~rmspec.device.writeback.ABSENT_IDENTITY`. So the test is: the constant matched
    exactly when nothing was expected, and the 64-character digest appearing anywhere in
    ``expected`` otherwise. Both survive the prefix being respelled, and neither can fire on the
    snapshot faults, whose ``expected`` values are fixed sentences with no hex in them.
    """
    if fingerprint is None:
        return refused.expected == ABSENT_IDENTITY
    return fingerprint in refused.expected


class PageSceneWriteback(Protocol):
    """Exactly what the bridge needs of ``SshSceneWriter``, in that class's own vocabulary.

    A structural type rather than the class, for the reason :class:`PageBatchRenderer` is one:
    the bridge calls three methods, and depending on the concrete writer would make a test of
    the *conversion* assemble a connected shell in order to exercise field renaming.
    ``SshSceneWriter`` satisfies this as written, including the two extra methods it has and
    this protocol deliberately does not name.
    """

    def read_scene(self, doc_uuid: str, page_id: str, /) -> WritebackRead:
        """Read one page's bytes off the device.

        Parameters
        ----------
        doc_uuid
            The document.
        page_id
            The page.

        Returns
        -------
        ~rmspec.device.writeback.SceneRead
            The transport's own read record, whose ``path`` is a ``RemotePath``.
        """
        ...

    def write_scene(
        self,
        precondition: WritebackPrecondition,
        scene: bytes,
        /,
    ) -> WritebackReceipt:
        """Replace one page's scene atomically.

        Parameters
        ----------
        precondition
            The transport's own precondition, whose identity field is ``digest``.
        scene
            The page's whole new bytes.

        Returns
        -------
        ~rmspec.device.writeback.SceneWriteReceipt
            The transport's own receipt.
        """
        ...

    def undo(self, receipt: WritebackReceipt, /) -> WritebackReceipt:
        """Restore what one write replaced.

        Parameters
        ----------
        receipt
            The transport's own receipt of the write to reverse.

        Returns
        -------
        ~rmspec.device.writeback.SceneWriteReceipt
            The receipt of the restoring write.
        """
        ...


class WritebackSceneWriter:
    """Satisfy ``SceneWriter`` from ``SshSceneWriter``: three value types, one error, one digest.

    The fourth and largest bridge in this file, and the one the other three's argument was
    written for. ``rmspec.device.writeback`` declares its own ``SceneRead``,
    ``ScenePrecondition`` and ``SceneWriteReceipt``; ``rmspec.domain.ports.device`` declares
    three classes of the same names. **None of the three pairs is the same object**, so
    ``SshSceneWriter`` does not satisfy the port -- ``ty`` reports "``SceneRead`` is not
    assignable to ``SceneRead``", and pydantic would refuse the foreign model again at run time
    when ``ReplyOnPageResult`` validated its ``receipt``. Duck typing gets as far as the three
    method names and stops.

    Three kinds of difference, in increasing order of how much they matter:

    - **Renames.** ``path: RemotePath`` against ``location: str``, and ``digest`` against
      ``fingerprint``. The device slice speaks its own path type and the domain deliberately
      does not, calling ``location`` "an opaque display string" -- a domain holding a
      ``RemotePath`` would have adopted one transport's filesystem.
    - **Fields on one side only.** ``pruned: tuple[RemotePath, ...]`` exists only on the
      transport receipt; ``replaced: str | None`` and ``visibility: SceneVisibility`` only on
      the domain's.
    - **Two incompatible fingerprint vocabularies**, which is the one that would have silently
      broken every reply. See :meth:`write_scene`.

    Notes
    -----
    **The fingerprints are not the same number.** ``rmspec.device.writeback`` fingerprints a
    scene as ``sha256(raw).hexdigest()``. ``SceneRead.precondition`` -- the *domain's* property,
    computed from the bytes so that "unchanged" has one definition rather than one per transport
    -- fingerprints it as a tagged digest over a framed stream. Both are 64 lowercase hex
    characters, so both satisfy the other's constraints and neither is ever rejected; they are
    simply never equal. Passing the domain's fingerprint through as the transport's ``digest``
    typechecks, validates, and then makes ``verify`` compare two unrelated numbers, so **every
    reply would be refused as though the human had drawn on the page** -- the exact failure this
    port exists to report, arriving because of the wiring rather than because of a stylus.

    There is no conversion between them: a tagged digest is not recoverable from a plain one.
    What *is* available is the read that produced both. The port already says so --
    :meth:`~rmspec.domain.ports.device.SceneWriter.read_scene` is "the only way to obtain a
    ``ScenePrecondition``, and therefore the only way to begin a safe edit", and
    ``precondition`` is documented as arriving at the write "unmodified". So this class records,
    per read, the transport precondition alongside the domain one it handed out, and a write
    translates by looking its precondition up rather than by recomputing anything. That is the
    port's own contract made mechanical, and it is why the port is ``Scope.REQUEST``: one
    command is one read and one write of the same page through the same object.

    The same applies to :meth:`undo`, and there it has a visible cost -- see that method.

    ``pruned`` is never carried across, in either direction. It is the record of a retention
    sweep the write had already finished, the domain has nowhere to put it, and nothing reads
    it: ``SshSceneWriter.undo`` uses four fields of the receipt it is handed, and that is not
    one of them.
    """

    def __init__(self, writer: PageSceneWriteback, /) -> None:
        self._writer = writer
        self._read: dict[ScenePrecondition, WritebackPrecondition] = {}
        self._wrote: dict[SceneWriteReceipt, WritebackReceipt] = {}

    def read_scene(self, doc_uuid: str, page_id: str, /) -> SceneRead:
        """Read one page's bytes, or learn that the device stores none.

        Parameters
        ----------
        doc_uuid
            The document, as the device identifies it.
        page_id
            The page, as the device identifies it.

        Returns
        -------
        ~rmspec.domain.ports.device.SceneRead
            The bytes and the identifiers, with the remote path flattened to the opaque
            ``location`` string the port declares.

        Notes
        -----
        ``precondition`` is a property over the bytes on both sides of this seam rather than a
        field, so the conversion cannot produce a precondition that disagrees with the scene it
        describes -- it does not carry one across at all. What it does carry across is the
        *pairing*: the transport's own precondition for this read is filed under the domain's,
        so that :meth:`write_scene` can find it again.

        An absent scene stays ``None``. Collapsing it to ``b""`` would erase the one distinction
        that makes :class:`~rmspec.app.ReplyOnPage` raise ``SceneRewriteUnsafe`` rather than
        appending ink to a page that has no artifact to append to.
        """
        read = self._writer.read_scene(doc_uuid, page_id)
        seen = SceneRead(
            doc_uuid=read.doc_uuid,
            page_id=read.page_id,
            location=read.path.value,
            scene=read.scene,
        )
        self._read[seen.precondition] = read.precondition
        return seen

    def write_scene(self, precondition: ScenePrecondition, scene: bytes, /) -> SceneWriteReceipt:
        """Replace one page's scene atomically, refusing if the page moved since it was read.

        Parameters
        ----------
        precondition
            The identity :meth:`read_scene` captured, unmodified. A value this object did not
            produce is refused rather than guessed at, because the two slices fingerprint a
            scene differently and only the read that produced both knows the pairing.
        scene
            The page's whole new contents. Never empty.

        Returns
        -------
        ~rmspec.domain.ports.device.SceneWriteReceipt
            The undo token, whose ``visibility`` says the human cannot see the reply yet.

        Raises
        ------
        UsageError
            ``scene`` is empty -- a zero-byte page is a page whose ink has been deleted, which
            the port says nothing may ask it for. Or *precondition* did not come from this
            object's own :meth:`read_scene`.
        DeviceStateMismatchError
            The page is no longer what it was when it was read: the human drew, or another
            writer landed first. ``retryable=True``, because the refusal happens *before* the
            replacement -- nothing was written, so re-reading and re-composing is the whole
            recovery. Never merged, and never resolved by writing last.
        DeviceProtocolError
            Any other way the tablet answered with something the transport cannot interpret,
            re-raised untouched.

        Notes
        -----
        The error translation is the one thing here that is not a value conversion.
        ``SshSceneWriter`` raises ``DeviceProtocolError`` for a moved identity while
        :meth:`~rmspec.app.ReplyOnPage.reply` documents ``DeviceStateMismatchError`` -- two
        surfaces disagreeing about the same refusal, which is the drift this project keeps
        finding. ``DeviceStateMismatchError``'s own docstring says the former was "the wrong
        stand-in", and why: that class means the device broke its own contract, and a human
        picking up a stylus breaks the *caller's* precondition instead. It also carries the
        ``retryable`` field and the "re-read and repeat" remediation that ``DeviceProtocolError``
        structurally cannot. Both score ``EX_UNAVAILABLE``, so no shell's exit status moves;
        what moves is the identity an agent branches on and the advice a person reads.

        The durable fix is one layer down, in ``rmspec.device``. When it lands, this ``except``
        becomes dead and should be deleted rather than kept.

        ``fingerprint`` on the returned receipt is derived by asking the domain what these bytes
        fingerprint to, through the same ``SceneRead.precondition`` property a caller reading the
        page back would use, rather than by re-spelling the digest here. The port requires
        exactly that equality -- "so a caller that reads the page back can tell 'still mine' from
        'the human has drawn since' without holding the bytes it wrote" -- and asking the owner
        is the only way to keep it true when the tag or the framing changes.

        ``replaced`` comes from the precondition, which is the honest source: the write succeeds
        only after the transport has *proved* the page's current bytes are the ones the
        precondition describes, so on success they are what this write superseded. The domain
        model's cross-field rule -- a superseded scene must name the snapshot holding it -- then
        holds by construction, because the transport keeps a snapshot exactly when there were
        bytes to keep, which is exactly when the precondition's identity was not ``None``.
        """
        if not scene:
            raise UsageError(
                subject="a scene write of zero bytes",
                requirement=(
                    "the page's whole new contents, since a zero-byte page is a page whose ink "
                    "has been deleted and no caller may ask this port for one"
                ),
            )
        captured = self._read.get(precondition)
        if captured is None:
            raise UsageError(
                subject=(
                    f"a precondition for page {precondition.page_id} that this writer's own "
                    f"read_scene did not produce"
                ),
                requirement=(
                    "the precondition read_scene returned in this same invocation, unmodified; "
                    "the transport and the domain fingerprint a scene differently, so only that "
                    "read knows which transport identity this one describes"
                ),
            )
        try:
            receipt = self._writer.write_scene(captured, scene)
        except DeviceProtocolError as refused:
            if not _stale_page(refused, fingerprint=captured.digest):
                raise
            raise _moved(refused, precondition.doc_uuid, precondition.page_id) from refused
        return self._recorded(
            receipt,
            fingerprint=_scene_identity(receipt, scene).fingerprint or ABSENT_ARTIFACT_FINGERPRINT,
            replaced=precondition.fingerprint,
        )

    def undo(self, receipt: SceneWriteReceipt, /) -> SceneWriteReceipt:
        """Put back what one write replaced, under the same precondition guarantee.

        Parameters
        ----------
        receipt
            The receipt of the write to reverse, carried whole rather than reassembled.

        Returns
        -------
        ~rmspec.domain.ports.device.SceneWriteReceipt
            The receipt of the restoring write, whose own snapshot holds what was just undone,
            so an undo is itself undoable through this same object.

        Raises
        ------
        DeviceOperationUnsupported
            The write superseded nothing, so reversing it would mean deleting a file from
            somebody's store, and nothing in this workspace does that. ``supported_by`` is empty,
            which is a claim rather than a blank.
        UsageError
            *receipt* did not come from this object's own :meth:`write_scene`. **This is the one
            place the fingerprint split costs a capability**: a receipt is designed to survive a
            JSON envelope so that a later invocation can reverse a write, and the transport
            identity an undo needs is not recoverable from the domain receipt -- a tagged digest
            does not yield the plain one. So an undo has to run in the invocation that wrote.
            Refusing says so; fabricating a transport receipt would restore a snapshot chosen by
            a number that means something else.
        DeviceStateMismatchError
            The page is no longer what the write left, so rolling back would discard newer
            work -- the same data loss, arriving through the recovery path.
        DeviceProtocolError
            The snapshot the receipt names is gone, or the tablet answered unintelligibly.

        Notes
        -----
        The ``replaced``/``fingerprint`` pair inverts. An undo restores exactly the bytes the
        write it reverses superseded, so this receipt's ``fingerprint`` is that write's
        ``replaced`` and its ``replaced`` is that write's ``fingerprint``. Both come from the
        receipt in hand, so neither is recomputed and the two cannot drift apart.

        The ``replaced is None`` refusal duplicates a check ``SshSceneWriter.undo`` makes for
        itself, deliberately: it is the same error with the same operation string, raised before
        a lookup that could not succeed anyway, and it is what makes ``receipt.replaced`` a
        ``str`` for the rest of this method.
        """
        if receipt.replaced is None:
            raise DeviceOperationUnsupported(
                transport=TransportKind.SSH,
                operation=UNDO_CREATE_OPERATION,
                supported_by=(),
            )
        wrote = self._wrote.get(receipt)
        if wrote is None:
            raise UsageError(
                subject=(
                    f"a receipt for page {receipt.page_id} that this writer did not produce, "
                    f"or produced in an earlier invocation"
                ),
                requirement=(
                    "the receipt write_scene returned in this same invocation; the transport "
                    "identity an undo writes under is not recoverable from a stored receipt"
                ),
            )
        try:
            restored = self._writer.undo(wrote)
        except DeviceProtocolError as refused:
            if not _stale_page(refused, fingerprint=wrote.digest):
                raise
            raise _moved(refused, receipt.doc_uuid, receipt.page_id) from refused
        return self._recorded(
            restored,
            fingerprint=receipt.replaced,
            replaced=receipt.fingerprint,
        )

    def _recorded(
        self,
        receipt: WritebackReceipt,
        /,
        *,
        fingerprint: str,
        replaced: str | None,
    ) -> SceneWriteReceipt:
        """Restate one transport receipt as the domain's undo token, and remember the pairing.

        Parameters
        ----------
        receipt
            What the transport recorded.
        fingerprint
            Identity of the bytes now on the page, in the domain's own vocabulary.
        replaced
            Identity of the scene this write superseded, ``None`` only when there was none --
            which is exactly when *receipt* names no snapshot, so the domain model's cross-field
            rule holds without being re-checked here.

        Returns
        -------
        ~rmspec.domain.ports.device.SceneWriteReceipt
            With paths flattened to display strings and ``visibility`` stated rather than
            defaulted, filed so :meth:`undo` can find the transport receipt again.

        Notes
        -----
        ``visibility`` is always ``REOPEN_REQUIRED`` and the enum has no second member. That is
        not a default standing in for something unknown: firmware 3.27.3.0 holds an open
        document's scene in memory, and this writer deliberately does not restart the tablet's UI
        to force a redraw, because four starts in ten minutes reaches a target whose handler
        reboots the device.
        """
        token = SceneWriteReceipt(
            doc_uuid=receipt.doc_uuid,
            page_id=receipt.page_id,
            location=receipt.path.value,
            byte_count=receipt.byte_count,
            fingerprint=fingerprint,
            replaced=replaced,
            snapshot=None if receipt.snapshot is None else receipt.snapshot.value,
            visibility=SceneVisibility.REOPEN_REQUIRED,
        )
        self._wrote[token] = receipt
        return token


def _scene_identity(receipt: WritebackReceipt, scene: bytes, /) -> ScenePrecondition:
    """Ask the domain what one scene's bytes fingerprint to, rather than re-spelling its digest.

    Parameters
    ----------
    receipt
        Supplies the identifiers and the location, so the constructed read describes the page
        the write actually landed on.
    scene
        The bytes now on that page.

    Returns
    -------
    ~rmspec.domain.ports.device.ScenePrecondition
        Whose ``fingerprint`` is the value ``SceneRead.precondition`` reports for *scene* --
        which is what :attr:`~rmspec.domain.ports.device.SceneWriteReceipt.fingerprint` is
        required to equal, so a caller reading the page back can tell "still mine" from "the
        human has drawn since".

    Notes
    -----
    Deriving it through the domain's own property rather than computing a digest here is the
    point. ``digest_of`` and the scene tag are both private to ``rmspec.domain``, and a
    composition root that reached into either would be re-spelling a definition whose whole
    purpose is to have exactly one spelling.

    ``fingerprint`` is ``None`` only for an absent scene, which ``bytes`` cannot be; the caller
    supplies :data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` as the value that
    could not arise, because a branch on it would be one no test could reach.
    """
    return SceneRead(
        doc_uuid=receipt.doc_uuid,
        page_id=receipt.page_id,
        location=receipt.path.value,
        scene=scene,
    ).precondition


def _moved(
    refused: DeviceProtocolError,
    doc_uuid: str,
    page_id: str,
    /,
) -> DeviceStateMismatchError:
    """Say in the domain's vocabulary that a page changed under a write.

    Parameters
    ----------
    refused
        The transport's refusal, which already names both identities.
    doc_uuid
        The document, for the subject the remediation tells a caller to re-read.
    page_id
        The page.

    Returns
    -------
    DeviceStateMismatchError
        Carrying both identities and ``retryable=True``.
    """
    return DeviceStateMismatchError(
        transport=TransportKind.SSH,
        subject=f"page {page_id} of document {doc_uuid}",
        expected=refused.expected,
        observed=refused.got,
        retryable=True,
    )


class DeviceProvider(Provider):
    """One command is one device handshake, closed by one finalizer.

    ``Scope.REQUEST`` for every port here, and the shell is the reason: a session opened at
    ``Scope.APP`` would stay open for a whole process with nothing obvious to close it, while a
    session per port would open five.

    Notes
    -----
    Both transports' handles are provided unconditionally and the *ports* choose between them
    through :func:`_for_transport`. That is not waste: a ``usb`` run needs the shell anyway for
    ``SearchIndexSource``, and an ``ssh`` run pays one unconnected ``httpx.Client`` for the
    symmetry.
    """

    scope = Scope.REQUEST

    @provide
    def shell(self, settings: CliSettings) -> Finalized[ParamikoShell]:
        """Open one SSH session for this invocation and close it afterwards.

        Parameters
        ----------
        settings
            Supplies the host, the user and the key path.

        Yields
        ------
        ParamikoShell
            **Connected.** Closed by the finalizer when the request scope leaves.

        Notes
        -----
        An earlier version of this provider yielded the shell *unconnected*, on the reasoning
        that "construction is cheap and the first call connects, so a command that never reaches
        the device pays nothing". The first half is true and the second half was false, and it
        broke every SSH-served capability in the CLI: measured against the attached tablet,
        ``rmspec device info`` and ``rmspec ls --source`` over SSH both exited 69 with
        *"the shell is not connected; call connect() before using it"*. ``ParamikoShell`` does
        not connect on demand -- its own hardware tests call ``connect()`` themselves, which is
        why the suite stayed green while the composed CLI could not reach the device at all.

        Connecting here loses nothing, because **dishka resolves lazily**: this provider runs
        only when something actually asks for a shell, so a command that never touches an
        SSH-served port never enters this function. The laziness the old docstring wanted lives
        in the container, not in the adapter.

        That matters beyond ``device info``: ``SearchIndexSource`` is SSH-only on every
        transport, so a broken shell also silenced tier-0 OCR -- the tablet's own free
        handwriting reading -- and every device-index hit in ``rmspec search``.

        ``key_path`` is passed explicitly and defaults to ``~/.ssh/id_ed25519_remarkable``,
        because paramiko does not read ``~/.ssh/config`` and ``key_path=None`` raises
        ``DeviceAuthFailed`` against a tablet ``ssh remarkable`` reaches from the same shell.
        """
        shell = ParamikoShell(
            endpoint=Endpoint(host=settings.device_host, port=SSH_PORT),
            user=settings.device_user,
            key_path=str(settings.ssh_key),
        )
        try:
            shell.connect()
            yield shell
        finally:
            shell.close()

    @provide
    def usb_api(self, settings: CliSettings) -> Finalized[UsbWebApi]:
        """Open one HTTP client against the tablet's web server and close it afterwards.

        Parameters
        ----------
        settings
            Supplies the host.

        Yields
        ------
        UsbWebApi
            Owning the client it built, and not connected: ``over_usb`` performs no request, so
            a command that never reaches the device pays a constructor and nothing else.

        Notes
        -----
        Three things are deliberately *not* configured here. The connection pool is left alone,
        because on this firmware only a ``HEAD`` poisons a connection and ``UsbWebApi.head``
        already sends ``Connection: close`` on that verb alone -- disabling keep-alive globally
        would pay for a fault that one method already contains. The per-request upload ceiling
        is left alone, because ``post_file`` passes its own and httpx prefers the per-request
        value. And the client is built by ``rmspec.device``, which is what keeps ``httpx`` out
        of this package while USB is still the default.
        """
        api = UsbWebApi.over_usb(Endpoint(host=settings.device_host, port=WEB_API_PORT))
        try:
            yield api
        finally:
            api.close()

    @provide
    def ssh_catalog(self, shell: ParamikoShell) -> SshCatalog:
        """Build the SSH catalog concretely, because the bundle source needs this exact type.

        Parameters
        ----------
        shell
            This invocation's session.

        Returns
        -------
        SshCatalog
            Walking the xochitl tree.
        """
        return SshCatalog(shell=shell, root=RemotePath.root())

    @provide
    def usb_catalog(self, api: UsbWebApi) -> UsbCatalog:
        """Build the USB catalog concretely, for the same reason as its SSH sibling.

        Parameters
        ----------
        api
            This invocation's web api.

        Returns
        -------
        UsbCatalog
            Reading the tablet's own document listing. ``UsbBundleSource`` demands this exact
            class rather than the ``DeviceCatalog`` port, so it has to be provided concretely
            first and re-provided as the port below.
        """
        return UsbCatalog(api=api)

    @provide
    def catalog(
        self,
        settings: CliSettings,
        usb: UsbCatalog,
        ssh: SshCatalog,
    ) -> DeviceCatalog:
        """Bind the device catalog port over the configured transport.

        Parameters
        ----------
        settings
            Chooses the transport.
        usb
            The catalog over the tablet's HTTP server, which is the default.
        ssh
            The catalog over a shell session.

        Returns
        -------
        DeviceCatalog
            Whichever the transport names.
        """
        return _for_transport(settings, operation="list the document tree", usb=usb, ssh=ssh)

    @provide
    def bundles(
        self,
        settings: CliSettings,
        shell: ParamikoShell,
        api: UsbWebApi,
        usb: UsbCatalog,
        ssh: SshCatalog,
    ) -> RawBundleSource:
        """Bind the raw bundle source over the configured transport.

        Parameters
        ----------
        settings
            Chooses the transport.
        shell
            This invocation's session.
        api
            This invocation's web api.
        usb
            The concrete USB catalog its bundle source demands.
        ssh
            The concrete SSH catalog its bundle source demands.

        Returns
        -------
        RawBundleSource
            A :class:`MemoizingBundleSource` in front of the transport's own adapter, so every
            caller in this request shares one pull per document. Underneath it, USB fetches a
            ``.rmdoc`` xochitl assembled, which is a consistent snapshot; SSH reads ``.rm`` files
            off disk, which is the torn read that snapshot avoids.

        Notes
        -----
        The seam wraps the *result* of :func:`_for_transport` rather than each argument, so the
        refusal a ``mirror`` run gets is raised before anything is wrapped and no caller is
        handed a memo over a source that does not exist.
        """
        over_usb: RawBundleSource = UsbBundleSource(api=api, catalog=usb)
        over_ssh: RawBundleSource = SshBundleSource(
            shell=shell, root=RemotePath.root(), catalog=ssh
        )
        return MemoizingBundleSource(
            _for_transport(
                settings,
                operation="read a document bundle",
                usb=over_usb,
                ssh=over_ssh,
            )
        )

    @provide
    def facts(
        self,
        settings: CliSettings,
        shell: ParamikoShell,
        api: UsbWebApi,
    ) -> DeviceFactsSource:
        """Bind the device facts source over the configured transport.

        Parameters
        ----------
        settings
            Chooses the transport.
        shell
            This invocation's session.
        api
            This invocation's web api.

        Returns
        -------
        DeviceFactsSource
            ``UsbFacts`` reports most fields unsupported, where SSH answers firmware and model,
            so a ``usb`` run genuinely knows less about the tablet than an ``ssh`` one --
            reported by :func:`describe_bindings` rather than silently upgraded.
        """
        over_usb: DeviceFactsSource = UsbFacts(api=api)
        over_ssh: DeviceFactsSource = SshFacts(shell=shell)
        return _for_transport(
            settings,
            operation="read the tablet's identity and resources",
            usb=over_usb,
            ssh=over_ssh,
        )

    @provide
    def index_source(self, shell: ParamikoShell) -> SearchIndexSource:
        """Bind the on-device search index source, over SSH whatever the transport is.

        Parameters
        ----------
        shell
            This invocation's session.

        Returns
        -------
        SearchIndexSource
            Over SSH necessarily and permanently: the firmware's HTTP route table is closed at
            six families and none of them serves a file from the xochitl tree, so no USB
            binding exists to choose between. A ``usb`` run therefore opens an SSH session too
            whenever it wants tier-0 text or the search index, which is a real cost and is
            reported as a limit on the USB transport rather than hidden.
        """
        return SshSearchIndexSource(shell)

    @provide
    def scene_writer(self, shell: ParamikoShell) -> SceneWriter:
        """Bind the page writeback port, over SSH whatever the transport is.

        Parameters
        ----------
        shell
            This invocation's session -- the same one the read side of a reply uses, which is
            what the port's ``REQUEST`` scope is for.

        Returns
        -------
        SceneWriter
            :class:`WritebackSceneWriter` over ``SshSceneWriter``. The wrapper is not optional:
            the two slices declare three same-named value types that are not the same classes,
            so the raw writer does not satisfy the port -- see that class for the measurement.

            Over SSH necessarily and permanently, for the same reason
            :meth:`index_source` is: the firmware's HTTP route table is closed at six families
            and none of them replaces a file in the xochitl tree. ``POST /upload`` creates a
            whole new document with re-keyed page uuids, which is not an edit to a page a human
            is holding. So a ``usb`` run that replies opens an SSH session too, and
            :func:`describe_bindings` reports that as a limit rather than hiding it.

        Notes
        -----
        ``snapshot_root`` and ``depth`` are left at the adapter's own defaults. Neither is a
        composition decision -- the snapshot root is a path on the tablet's own filesystem that
        the adapter creates and prunes, and the depth is how many generations it keeps -- and
        promoting either to ``RMSPEC_*`` would let a setting move where an undo token points
        between the write and the undo.

        The two methods the port does *not* declare, a standalone ``verify`` and a ``snapshots``
        listing, are not re-exposed through the wrapper. That is the port's stated intent rather
        than an oversight: publishing an earlier check would make the check-then-act sequence the
        obvious one, and no policy decides anything from a list of backups.
        """
        return WritebackSceneWriter(SshSceneWriter(shell=shell, root=RemotePath.root()))

    @provide
    def uploader(
        self,
        settings: CliSettings,
        shell: ParamikoShell,
        api: UsbWebApi,
    ) -> DocumentUploader:
        """Bind the document uploader over the configured transport.

        Parameters
        ----------
        settings
            Chooses the transport.
        shell
            This invocation's session.
        api
            This invocation's web api.

        Returns
        -------
        DocumentUploader
            ``POST /upload`` is the route that makes the tablet show a new document without
            reopening it, and it is root-only, create-only and irreversible. The SSH writer is
            what a document destined for a folder needs, and is the ``supported_by`` the
            capability report names for that one limit.
        """
        over_usb: DocumentUploader = UsbUploader(api=api)
        over_ssh: DocumentUploader = SshUploader(
            shell=shell,
            root=RemotePath.root(),
            now_ms=_now_ms,
            new_uuid=_new_uuid,
        )
        return _for_transport(
            settings,
            operation="create a document on the tablet",
            usb=over_usb,
            ssh=over_ssh,
        )

    @provide
    def handwriting(self, source: SearchIndexSource) -> HandwrittenTextIndex:
        """Bind the handwriting index over whichever transport served the index file.

        Parameters
        ----------
        source
            The search index source.

        Returns
        -------
        HandwrittenTextIndex
            Reading the tablet's own index, which is the only source of handwriting text that
            does not require running a recogniser. Over SSH on every transport, because its
            source is.
        """
        return DeviceSearchIndex(source)


class UseCaseProvider(Provider):
    """Use cases whose collaborators are per-invocation, and the bridge between two of them."""

    scope = Scope.REQUEST

    invocation = from_context(provides=Invocation, scope=Scope.REQUEST)

    @provide
    def output(self, consoles: ConsolePair, invocation: Invocation) -> CliOutput:
        """Bind the output mode for this invocation.

        Parameters
        ----------
        consoles
            The process's two consoles.
        invocation
            Carries the mode the flags resolved to.

        Returns
        -------
        CliOutput
            One per command, over one pair of consoles per process.
        """
        return CliOutput(consoles=consoles, mode=invocation.mode)

    @provide
    def render_pages(
        self,
        bundles: RawBundleSource,
        codec: PageCodec,
        renderer: PageRenderer,
        rasterizer: SvgRasterizer,
    ) -> RenderPages:
        """Bind the render use case.

        Parameters
        ----------
        bundles
            Where raw pages come from.
        codec
            Decodes a scene.
        renderer
            Draws it.
        rasterizer
            Turns the drawing into pixels.

        Returns
        -------
        RenderPages
            ``Scope.REQUEST``, because its bundle source is.
        """
        return RenderPages(
            bundles=bundles,
            codec=codec,
            renderer=renderer,
            rasterizer=rasterizer,
        )

    @provide
    def page_rasterizer(
        self,
        pages: RenderPages,
        repository: DocumentRepository,
        template: RasterTemplate,
    ) -> RenderPagesRasterizer:
        """Bind the one object that satisfies both use cases' ``PageRasterizer``.

        Parameters
        ----------
        pages
            The render use case being adapted.
        repository
            Supplies the page order, which is how a uuid becomes an index.
        template
            Everything about the request that does not vary per page.

        Returns
        -------
        RenderPagesRasterizer
            Bound once and injectable into both ``ExtractDiagrams`` and ``ReadAnnotations``.
        """
        return RenderPagesRasterizer(pages=pages, repository=repository, template=template)

    @provide
    def list_documents(self, catalog: DeviceCatalog) -> ListDocuments:
        """Bind the catalog listing use case.

        Parameters
        ----------
        catalog
            The device catalog, over whichever transport was composed.

        Returns
        -------
        ListDocuments
            Backing ``rmspec ls``.
        """
        return ListDocuments(catalog=catalog)

    @provide
    def resolve_document(self, catalog: DeviceCatalog) -> ResolveDocument:
        """Bind the document lookup use case.

        Parameters
        ----------
        catalog
            The device catalog.

        Returns
        -------
        ResolveDocument
            Backing ``rmspec read``, and the shared lookup every other command's argument goes
            through. It never raises ``AmbiguousDocument``: it auto-resolves and records a
            degradation, and the boundary decides whether to accept that.
        """
        return ResolveDocument(catalog=catalog)

    @provide
    def create_document(
        self,
        uploader: DocumentUploader,
        catalog: DeviceCatalog,
        audit: SyncAuditLog,
    ) -> CreateDocument:
        """Bind the document creation use case.

        Parameters
        ----------
        uploader
            The write side, over whichever transport was composed.
        catalog
            Read back, so a name already on the tablet can be refused before the wire.
        audit
            Where the create is recorded, because no HTTP route deletes one.

        Returns
        -------
        CreateDocument
            Backing ``rmspec push``. It refuses a blank name, a zero-page document, a payload
            lacking the content witness its media claims, and a duplicate name -- all before
            anything reaches the tablet.
        """
        return CreateDocument(uploader=uploader, catalog=catalog, audit=audit)

    @provide
    def sync_documents(
        self,
        catalog: DeviceCatalog,
        bundles: RawBundleSource,
        store: DocumentSyncStore,
        audit: SyncAuditLog,
    ) -> SyncDocuments:
        """Bind the mirror-refresh use case.

        Parameters
        ----------
        catalog
            What the tablet holds.
        bundles
            Where a document's raw pages come from.
        store
            The local mirror being brought up to date.
        audit
            The history ``sync --history`` reads back.

        Returns
        -------
        SyncDocuments
            Backing ``rmspec sync``, whose ``--dry-run`` is the old ``status`` command.
        """
        return SyncDocuments(catalog=catalog, bundles=bundles, store=store, audit=audit)

    @provide
    def search_text(
        self,
        store: DocumentSyncStore,
        index: HandwrittenTextIndex,
        audit: SyncAuditLog,
    ) -> SearchText:
        """Bind the handwriting search use case.

        Parameters
        ----------
        store
            The mirror, which supplies document identity for a hit.
        index
            The tablet's own handwriting index, read over SSH on every transport.
        audit
            The history, so a search can say how stale the mirror it searched is.

        Returns
        -------
        SearchText
            Backing ``rmspec search``. Its matcher is a substring test and says so.
        """
        return SearchText(store=store, index=index, audit=audit)

    @provide
    def sync_history(self, audit: SyncAuditLog) -> ReportSyncHistory:
        """Bind the sync history report.

        Parameters
        ----------
        audit
            The log.

        Returns
        -------
        ReportSyncHistory
            Backing ``rmspec sync --history``.
        """
        return ReportSyncHistory(audit=audit)

    @provide
    def reply_on_page(
        self,
        engraver: TextEngraver,
        appender: SceneAppender,
        writer: SceneWriter,
    ) -> ReplyOnPage:
        """Bind the ink-reply use case.

        Parameters
        ----------
        engraver
            :class:`TextToInkEngraver`, which turns the message into strokes locally and for
            free -- which is what lets every refusal happen before the tablet is touched.
        appender
            ``AppendOnlySceneWriter``, which folds the strokes into the page's bytes so the
            human's existing ink is a literal prefix of the result.
        writer
            ``SshSceneWriter``, which replaces the page under a precondition captured at read
            time and re-checked immediately before the replacement lands.

        Returns
        -------
        ReplyOnPage
            Backing ``rmspec reply``. Its receipt is the undo token, its
            ``visibility`` is always ``REOPEN_REQUIRED``, and a page the human drew on between
            the read and the write is refused rather than merged.
        """
        return ReplyOnPage(engraver=engraver, appender=appender, writer=writer)

    @provide
    def device_facts(self, facts_source: DeviceFactsSource) -> ReportDeviceFacts:
        """Bind the device facts report.

        Parameters
        ----------
        facts_source
            The facts source, whose answers depend on the transport: ``UsbFacts`` reports most
            fields unsupported where SSH answers firmware and model.

        Returns
        -------
        ReportDeviceFacts
            Backing ``rmspec device info``.
        """
        return ReportDeviceFacts(facts_source=facts_source)

    @provide
    def extract_diagrams(
        self,
        repository: DocumentRepository,
        rasterizer: RenderPagesRasterizer,
        model: VisionLanguageModel,
        cache: DiagramCache,
    ) -> ExtractDiagrams:
        """Bind the diagram extraction use case.

        Parameters
        ----------
        repository
            The local mirror, which supplies page order and metadata.
        rasterizer
            The bridge, which satisfies this use case's narrow ``PageRasterizer`` protocol.
        model
            The bare vision model, because this use case does not care which one it gets.
        cache
            Keyed on the render digest, so an unchanged page is never re-billed.

        Returns
        -------
        ExtractDiagrams
            Backing ``rmspec diagram``. A ``DiagramSkipReason`` is data here, never an error.
        """
        return ExtractDiagrams(
            repository=repository,
            rasterizer=rasterizer,
            model=model,
            cache=cache,
        )

    @provide
    def read_annotations(
        self,
        repository: DocumentRepository,
        pdf: PdfPageReader,
        rasterizer: RenderPagesRasterizer,
        model: VisionLanguageModel,
    ) -> ReadAnnotations:
        """Bind the PDF annotation reading use case.

        Parameters
        ----------
        repository
            The local mirror.
        pdf
            Reads text and page geometry out of the PDF being annotated.
        rasterizer
            The same bridge instance ``ExtractDiagrams`` gets. This use case's protocol is a
            superset -- it adds ``page_box`` and a keyword-only ``underlay`` -- and one class
            satisfies both, which is why there is one binding rather than two adapters.
        model
            The bare vision model.

        Returns
        -------
        ReadAnnotations
            Backing ``rmspec annotations``, which needs a PDF-backed document.
        """
        return ReadAnnotations(
            repository=repository,
            pdf=pdf,
            rasterizer=rasterizer,
            model=model,
        )

    @provide
    def transcribe_pages(
        self,
        *,
        pipeline: RenderPages,
        recognizers: Sequence[TextRecognizer],
        index: HandwrittenTextIndex,
        reader: ReaderModel,
        adjudicator: AdjudicatorModel,
        cache: OcrCache,
    ) -> TranscribePages:
        """Bind the transcription use case, which is the four-tier OCR ladder.

        Keyword-only, which dishka resolves exactly as it resolves positional collaborators. It
        is the only provider here with six of them, and two -- ``reader`` and ``adjudicator`` --
        are the same protocol under different keys, so a caller reading positionally could not
        see which was which.

        Parameters
        ----------
        pipeline
            ``RenderPages`` itself: it satisfies this use case's ``PageRenderPipeline``
            structurally, so the same object is bound rather than a second adapter written.
        recognizers
            The engines ``RMSPEC_OCR_ENGINES`` selected, in a reproducible order.
        index
            Tier 0, the tablet's own handwriting index -- always available here, so the port's
            optional slot is filled rather than left ``None``.
        reader
            Tier 2, from ``RMSPEC_READ_MODEL``.
        adjudicator
            Tier 3, from ``RMSPEC_MERGE_MODEL``. A different key and a different model id,
            because one model bound twice makes a disagreement between the two unobservable.
        cache
            Keyed on the page content hash.

        Returns
        -------
        TranscribePages
            Backing ``rmspec ocr``, which reports ``tier_reached`` and ``short_circuited``.
        """
        return TranscribePages(
            pipeline=pipeline,
            recognizers=recognizers,
            index=index,
            reader=reader,
            adjudicator=adjudicator,
            cache=cache,
        )


def _now_ms() -> int:
    """Give the current time in milliseconds since the epoch.

    Returns
    -------
    int
        What ``SshUploader`` stamps a new document's metadata with.
    """
    return time_ns() // 1_000_000


def _new_uuid() -> str:
    """Mint an identifier for a document being created.

    Returns
    -------
    str
        A random uuid4 in the 36-character form the tablet's store uses.
    """
    return str(uuid4())


def _repository_provider(settings: CliSettings, /) -> Provider:
    """Choose which store serves ``DocumentRepository`` for these settings.

    Parameters
    ----------
    settings
        Supplies ``xochitl``.

    Returns
    -------
    Provider
        :class:`MirrorRepositoryProvider` when a mirror is configured, else
        :class:`BundleRepositoryProvider`.

    Notes
    -----
    A composition-time choice rather than a resolution-time one, and the reason is dishka's
    resolution order: a factory's parameters are resolved before it runs, so a single provider
    taking both ``CliSettings`` and ``RawBundleSource`` would resolve the bundle source -- and
    therefore connect ``ParamikoShell`` -- on every run, including one whose settings say to read a
    local directory. An offline mirror run would then need a tablet, which is the opposite of what
    ``RMSPEC_XOCHITL`` is for.

    This is also where the narrowing happens: ``settings.xochitl`` is ``Path | None`` here and a
    ``Path`` on the provider that gets it, so the mirror provider has no absent-root branch that
    only a mis-composition could reach.
    """
    root = settings.xochitl
    if root is None:
        return BundleRepositoryProvider()
    return MirrorRepositoryProvider(root)


def compose(
    *,
    settings: CliSettings,
    consoles: ConsolePair,
    providers: Sequence[Provider] = (),
) -> Container:
    """Build the process's container.

    Parameters
    ----------
    settings
        The validated environment. ``xochitl`` selects which store ``DocumentRepository`` is bound
        to, through :func:`_repository_provider`.
    consoles
        The process's two streams.
    providers
        Extra providers appended after the defaults. A test binds the shipped doubles this way,
        with ``override=True`` on each ``provide``, which is how a container test avoids
        constructing an SSH, Textract or Bedrock client.

    Returns
    -------
    Container
        Synchronous, with ``Scope.APP`` entered. Enter ``Scope.REQUEST`` per command with
        ``with container(context={Invocation: ...}) as request:``.
    """
    return make_container(
        CoreProvider(),
        PersistenceProvider(),
        FormatsProvider(),
        _repository_provider(settings),
        RenderProvider(),
        ExportProvider(),
        OcrProvider(),
        DeviceProvider(),
        UseCaseProvider(),
        *providers,
        context={CliSettings: settings, ConsolePair: consoles},
    )


_TORN_READ: Final = OperationLimit(
    operation="read a document while xochitl is running",
    detail=(
        "reMarkable's own documentation says Xochitl should not be running when the stored "
        "documents are accessed or changed; the USB .rmdoc route is served by xochitl and so "
        "is a consistent snapshot by construction"
    ),
    supported_by=(TransportKind.USB_WEB_API,),
)
"""What SSH cannot promise about a read, and the reason USB is now the default read path.

The wording is close to the source's own and deliberately no stronger than it. The sentence
at <https://developer.remarkable.com/documentation/xochitl> is *"It is possible to copy this
directory, but note that Xochitl should not be running when accessing and/or changing the
stored documents"*, and an earlier version of this `detail` rendered it as "must not run …
accessed manually" -- a tightening plus a word the source does not use. This string is user
facing, so it reports their guidance rather than our reading of it; the reading, that a
read off disk under a live xochitl is a torn read waiting to happen, is separately measured
and does not need the quote strengthened to stand.
"""

_SSH_ONLY_INDEX: Final = OperationLimit(
    operation="read the on-device search index without opening an SSH session",
    detail=(
        "the firmware's HTTP route table is closed at six families and none of them serves a "
        "file from the xochitl tree, so tier-0 handwriting text and the search index need a "
        "shell session even on a USB run"
    ),
    supported_by=(TransportKind.SSH,),
)
"""The one port that does not follow the transport, reported rather than hidden.

A *limit* on USB and not an absence: the port is bound and works, it just costs a second
connection. That distinction is exactly what ``DeviceFacts.unsupported: frozenset[str]``
structurally cannot express, which is why it is expressed here.
"""

_UNREADABLE_SERIAL: Final = OperationLimit(
    operation="read the serial the tablet UI shows",
    detail=(
        "the SoC uid next to the board name is a different fact, and no transport reads the "
        "one a user sees in Settings"
    ),
    supported_by=(),
)
"""The fact no transport can reach, which is why its ``supported_by`` is empty."""

_SPARSE_USB_FACTS: Final = OperationLimit(
    operation="read the firmware version, the board model and the free space",
    detail=(
        "UsbFacts answers from the web api, which exposes none of them, so it reports most "
        "fields unsupported; SSH reads them off /etc/os-release, the soc machine node and "
        "/proc/meminfo"
    ),
    supported_by=(TransportKind.SSH,),
)
"""What a ``usb`` run genuinely does not know about the tablet, rather than silently upgrading."""

_NEEDS_REOPEN: Final = OperationLimit(
    operation="have the tablet show a new document without reopening it",
    detail=(
        "an SSH write lands in the store but xochitl has already read its index; POST /upload "
        "over USB is the route that refreshes"
    ),
    supported_by=(TransportKind.USB_WEB_API,),
)
"""What SSH cannot do on the write side, and the reason USB is the uploader's default."""

_UPLOAD_ROOT_ONLY: Final = OperationLimit(
    operation="create a document inside a folder",
    detail=(
        "POST /upload has no folder parameter, so every import lands at the root; a --parent "
        "must route to the SSH uploader rather than be silently root-placed"
    ),
    supported_by=(TransportKind.SSH,),
)
"""The first of ``POST /upload``'s three limits: where it can put a document."""

_UPLOAD_CREATE_ONLY: Final = OperationLimit(
    operation="update a document already on the tablet",
    detail=(
        "the import re-keys both the document uuid and every page uuid, so pushing a document "
        "back makes a second copy rather than replacing the first"
    ),
    supported_by=(),
)
"""The second: it is a create, never an update, and no transport makes it one."""

_UPLOAD_IRREVERSIBLE: Final = OperationLimit(
    operation="delete a document the host just created",
    detail=(
        "no HTTP route deletes, so a successful create is irreversible from the host's side "
        "and has to be undone on the tablet"
    ),
    supported_by=(),
)
"""The third: nothing on either transport takes it back, which is why the create is audited."""

_NO_MIRROR: Final = OperationLimit(
    operation="reach the tablet at all",
    detail=(
        "RMSPEC_TRANSPORT=mirror selects a local copy of the document tree, and no local-mirror "
        "DeviceCatalog or RawBundleSource exists yet -- the value is accepted so the setting "
        "and the report agree about what is missing, rather than falling back to SSH"
    ),
    supported_by=(TransportKind.USB_WEB_API, TransportKind.SSH),
)
"""Why a ``mirror`` run reports four ports unbound instead of quietly using the tablet.

``PortBinding`` refuses ``bound=False`` with no limits, which is the invariant that makes this
constant mandatory rather than optional: an absence always has to be explained.
"""

_MIRROR_IS_NOT_THE_SYNC_STORE: Final = OperationLimit(
    operation="read a document the mirror at RMSPEC_XOCHITL does not hold",
    detail=(
        "rmspec sync populates the SQLite store at RMSPEC_SYNC_DB and never writes a xochitl "
        "tree, so a mirror holds only what was copied there by other means; unset RMSPEC_XOCHITL "
        "to read the attached tablet instead"
    ),
    supported_by=(TransportKind.USB_WEB_API, TransportKind.SSH),
)
"""The cost of preferring a configured mirror, which is the measurement that started this.

``RMSPEC_XOCHITL`` being set binds ``DocumentRepository`` to a directory, and the remediation two
commands used to print asked a user to configure that directory as though something produced it.
Nothing does. The port is bound and correct over whatever is there, so this is a limit rather than
an absence -- and naming the variable that turns it off is the whole point of reporting it.
"""

_REPOSITORY_NEEDS_A_SOURCE: Final = OperationLimit(
    operation="read a document with neither a mirror nor a transport that serves one",
    detail=(
        "RMSPEC_TRANSPORT=mirror binds no RawBundleSource, so with RMSPEC_XOCHITL unset as well "
        "nothing can serve a page at all; set RMSPEC_XOCHITL or choose the usb or ssh transport"
    ),
    supported_by=(TransportKind.USB_WEB_API, TransportKind.SSH),
)
"""The one configuration in which ``DocumentRepository`` is genuinely unbound.

Distinct from :data:`_NO_MIRROR`, which is about reaching the tablet: this names the *pair* of
settings that has to be absent, because either one alone binds the port. ``PortBinding`` refuses
``bound=False`` with no limits, so an absence is always explained.
"""

_READ_SIDE_LIMITS: Final = {
    TransportKind.USB_WEB_API: (),
    TransportKind.SSH: (_TORN_READ,),
}
"""``DeviceCatalog`` and ``RawBundleSource``, which USB serves outright and SSH serves at risk.

Read by the bundle-backed ``DocumentRepository`` row as well, because that binding *is* the bundle
source plus a codec and inherits its limits exactly.
"""

_INDEX_LIMITS: Final = {
    TransportKind.USB_WEB_API: (_SSH_ONLY_INDEX,),
    TransportKind.SSH: (),
}
"""``SearchIndexSource`` and ``HandwrittenTextIndex``, bound over SSH on every transport."""

_FACTS_LIMITS: Final = {
    TransportKind.USB_WEB_API: (_SPARSE_USB_FACTS, _UNREADABLE_SERIAL),
    TransportKind.SSH: (_UNREADABLE_SERIAL,),
}
"""``DeviceFactsSource``. USB knows strictly less, and the serial is unreachable either way."""

_SSH_ONLY_WRITEBACK: Final = OperationLimit(
    operation="write onto an existing page without opening an SSH session",
    detail=(
        "no HTTP route replaces a file in the xochitl tree, and POST /upload creates a whole new "
        "document with re-keyed page uuids rather than editing the page a human is holding, so a "
        "reply needs a shell session even on a USB run"
    ),
    supported_by=(TransportKind.SSH,),
)
"""The write-side twin of :data:`_SSH_ONLY_INDEX`, and the reason ``reply`` is SSH-only.

Stated as a limit rather than an absence because the port *is* bound on every transport --
:meth:`DeviceProvider.scene_writer` resolves the same shell ``SearchIndexSource`` does -- so a
``usb`` run can reply. It just pays for a second connection, which is a cost to report rather
than a capability to claim or deny.
"""

_WRITEBACK_NEEDS_REOPEN: Final = OperationLimit(
    operation="have the tablet redraw a page that was written underneath it",
    detail=(
        "firmware 3.27.3.0 holds an open document's scene in memory, and nothing here restarts "
        "the tablet's UI to force a redraw -- four starts in ten minutes reaches a target whose "
        "handler reboots the device; the reply is in the store and appears when the document is "
        "closed and reopened"
    ),
    supported_by=(),
)
"""Why no transport serves the write side outright, and why the receipt says ``REOPEN_REQUIRED``.

``supported_by=()`` is the honest answer rather than an omission: ``POST /upload`` refreshes the
tablet's *index*, which is what :data:`_NEEDS_REOPEN` is about for a newly created document, and
it has no effect at all on a page edited in place. So this limit is on both transports, and
``SceneWriter`` is ``restricted`` on every one of them.
"""

_UPLOAD_LIMITS: Final = {
    TransportKind.USB_WEB_API: (_UPLOAD_ROOT_ONLY, _UPLOAD_CREATE_ONLY, _UPLOAD_IRREVERSIBLE),
    TransportKind.SSH: (_NEEDS_REOPEN,),
}
"""``DocumentUploader``. Three limits on the route that refreshes, one on the route that does not.

The asymmetry is the measurement: ``POST /upload`` is the only way to make the tablet show a new
document without reopening it, and it is root-only, create-only and irreversible.
"""

_UNDO_IS_THIS_INVOCATION: Final = OperationLimit(
    operation="reverse a reply from a later invocation, using a stored receipt",
    detail=(
        "the transport fingerprints a scene as a plain sha256 and the domain port fingerprints "
        "it as a tagged digest, and neither is recoverable from the other, so the identity an "
        "undo writes under lives only in the writer that produced both; undo in the same "
        "invocation as the reply, or take the reply back on the tablet"
    ),
    supported_by=(),
)
"""The one capability the two fingerprint vocabularies cost, reported rather than discovered.

``supported_by=()`` because no transport helps: the split is between the device slice and the
domain port, so choosing ``ssh`` explicitly does not change it. Named here because
:class:`WritebackSceneWriter` refuses such an undo with a ``UsageError``, and a refusal a caller
can hit is a limit a caller should be able to read about first.
"""

_WRITEBACK_LIMITS: Final = {
    TransportKind.USB_WEB_API: (
        _SSH_ONLY_WRITEBACK,
        _WRITEBACK_NEEDS_REOPEN,
        _UNDO_IS_THIS_INVOCATION,
    ),
    TransportKind.SSH: (_WRITEBACK_NEEDS_REOPEN, _UNDO_IS_THIS_INVOCATION),
}
"""``SceneWriter``, bound over SSH on every transport and never visible without a reopen.

Shaped exactly like :data:`_INDEX_LIMITS` -- the SSH-only cost drops off the SSH run and the rest
stays -- with one difference: the reopen limit is on *both* rows, because it is a property of the
firmware rather than of a transport. So this port is never in ``served``, on any transport, and
that is the report saying the thing the command must not misreport.
"""


def describe_bindings(settings: CliSettings | None = None, /) -> ReportCapabilitiesRequest:
    """Describe what this container bound, for :class:`~rmspec.app.ReportCapabilities`.

    Parameters
    ----------
    settings
        The settings that were composed, or ``None`` to describe a default composition.
        ``CliSettings.model_construct()`` supplies the declared defaults without reading the
        environment, so the fallback cannot restate a default that has since moved and cannot
        raise while a report is being assembled.

    Returns
    -------
    ReportCapabilitiesRequest
        The composed transport, one binding per device port read side before write side, and the
        ``DocumentRepository`` binding the two page-reading use cases resolve.

        ``SceneWriter`` is last and is ``bound=True`` on every transport, carrying its costs as
        limits: SSH-only on a ``usb`` or ``mirror`` run, and never visible without a reopen on
        any of them. So it is in ``restricted`` everywhere and in ``served`` nowhere, which is
        the only shape that does not overclaim -- ``bound=False`` would say a ``usb`` run cannot
        reply, and no limits at all would say the human sees the reply immediately.

    Notes
    -----
    Every fact here was measured on firmware 3.27.3.0, and none of it is a table inside the use
    case -- which is that design's point: a report that can only repeat what the composition
    root told it cannot keep asserting something a re-measurement refuted, which this project
    has already done twice in prose.

    ``DocumentRepository`` is the one non-device port reported, and it is here because leaving it
    out was a measured dishonesty: with ``RMSPEC_XOCHITL`` unset, ``rmspec diagram`` and
    ``rmspec annotations`` could not run at all while this report said every port was served. It
    is bound unless *both* a mirror and a bundle-serving transport are absent, and which of the two
    serves it changes the limits, so the row is a function of ``xochitl`` as well as ``transport``.
    """
    resolved = CliSettings.model_construct() if settings is None else settings
    transport = composed_transport(resolved)
    read_side = _READ_SIDE_LIMITS.get(transport, (_NO_MIRROR,))
    mirrored = resolved.xochitl is not None
    return ReportCapabilitiesRequest(
        transport=transport,
        bindings=(
            PortBinding(
                port="DeviceCatalog",
                bound=transport is not TransportKind.LOCAL_MIRROR,
                limits=read_side,
            ),
            PortBinding(
                port="RawBundleSource",
                bound=transport is not TransportKind.LOCAL_MIRROR,
                limits=read_side,
            ),
            PortBinding(
                port="DocumentRepository",
                bound=mirrored or transport is not TransportKind.LOCAL_MIRROR,
                limits=(
                    (_MIRROR_IS_NOT_THE_SYNC_STORE,)
                    if mirrored
                    else _READ_SIDE_LIMITS.get(transport, (_REPOSITORY_NEEDS_A_SOURCE,))
                ),
            ),
            PortBinding(
                port="SearchIndexSource",
                bound=True,
                limits=_INDEX_LIMITS.get(transport, (_SSH_ONLY_INDEX,)),
            ),
            PortBinding(
                port="HandwrittenTextIndex",
                bound=True,
                limits=_INDEX_LIMITS.get(transport, (_SSH_ONLY_INDEX,)),
            ),
            PortBinding(
                port="DeviceFactsSource",
                bound=transport is not TransportKind.LOCAL_MIRROR,
                limits=_FACTS_LIMITS.get(transport, (_NO_MIRROR,)),
            ),
            PortBinding(
                port="DocumentUploader",
                bound=transport is not TransportKind.LOCAL_MIRROR,
                limits=_UPLOAD_LIMITS.get(transport, (_NO_MIRROR,)),
            ),
            PortBinding(
                port="SceneWriter",
                bound=True,
                limits=_WRITEBACK_LIMITS.get(
                    transport,
                    (_SSH_ONLY_WRITEBACK, _WRITEBACK_NEEDS_REOPEN, _UNDO_IS_THIS_INVOCATION),
                ),
            ),
        ),
    )
