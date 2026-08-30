"""Use cases for remarkable-spec. Imports rmspec.domain and nothing else.

This is the layer that decides. A use case orchestrates ports, applies policy, and
returns a value; the adapters below it know a technology and no policy, and the CLI
above it knows a terminal and no policy. Nothing here knows either.

The six conventions every use case in this package follows
----------------------------------------------------------
These are decided, not suggested. They exist so that reading one use case teaches you
all of them, and so that a reviewer can tell at a glance whether a new one belongs.

1. **One class per use case, named for the action in the imperative.**
   :class:`~rmspec.app.resolve.ResolveDocument`, and by the same rule
   ``TranscribePages`` or ``RenderPages`` -- never ``DocumentResolver`` or a
   ``*Service``. Collaborators arrive as **keyword-only constructor arguments** and
   every one of them is a Protocol from :mod:`rmspec.domain.ports`, never a concrete
   type. Keyword-only because a use case with six collaborators has no meaningful
   positional order, and because adding a seventh must not silently reorder a call
   site. ``PLR0913`` is ignored repo-wide for exactly this reason.

2. **Exactly one public method per use case, named for the action.** ``resolve``,
   ``transcribe``, ``render``. It takes **one frozen request model, positional-only**,
   and returns **one frozen result model**. Not ``__call__``: a named method is
   greppable, and ``resolver.resolve(request)`` reads at the call site where
   ``resolver(request)`` does not. One request model rather than loose arguments means
   a new option is a new field with a default, not a new parameter every existing
   caller must be revisited for.

3. **Every result model is frozen, ``extra="forbid"``, and carries
   ``degradations: tuple[Degradation, ...]``.** A use case that can substitute a value
   must be able to say that it did, in the closed vocabulary of
   :class:`~rmspec.domain.errors.DegradationKind`. The field has no default, so a
   construction site cannot forget it -- the reason
   :class:`~rmspec.domain.ports.device.DeviceListing` gives for the same choice about
   ``skipped``. :mod:`rmspec.app._degradations` is how every use case accumulates them,
   so no two do it differently.

4. **No use case prints, logs to stdout, or touches a filesystem.** stdout belongs to
   the CLI, which is the only layer that knows whether it is talking to a human or to
   ``jq``. This is the convention the legacy tree got wrong most expensively: it passed
   a ``rich.Console`` into its document resolver and printed warnings from four sites
   inside resolution, which is why four of its ``--json`` modes emitted prose before
   their JSON and could not be piped.

5. **Errors come only from :mod:`rmspec.domain.errors`.** This package defines no
   exception type and no error class hierarchy of its own. The corollary is a rule about
   validation: where the domain names a condition, raise the domain's error for it;
   where the domain deliberately declined to name one, a pydantic constraint keeps the
   state unconstructible instead. That is why
   :attr:`~rmspec.app.resolve.ResolveDocumentRequest.query` is an unconstrained ``str``
   whose blankness is a :class:`~rmspec.domain.errors.UsageError`, while a negative page
   index in :class:`~rmspec.app.selection.PageSelection` is a ``ValidationError``.

6. **``rmspec.app`` may import :mod:`rmspec.domain` and nothing else from the
   workspace.** ``test_app_layer_imports_domain_only``, in
   ``tests/architecture/test_dependency_direction.py``,
   fails the build otherwise, and it is the single most important edge in the
   architecture: if a use case can reach an adapter, that adapter's third-party
   dependency becomes a dependency of every test that touches the use case, and the
   ports stop earning their keep. The tests in ``packages/rmspec-app/tests/`` hold
   themselves to the same rule -- they bind a port with a local in-test fake annotated
   with the Protocol, which is what makes the type gate check conformance -- so a pure
   policy suite never needs ``paramiko`` installed to run.

What is public here
-------------------
Use cases, their request and result models, and :mod:`rmspec.app.selection`, which the
CLI constructs. :mod:`rmspec.app._degradations` is private: use cases import it, and
nothing outside this package should need to.

Enums and the structural Protocols a use case declares for a collaborator are public
*module* names and deliberately stay out of ``__all__``. ``_use_cases()`` in
``tests/test_app_public_surface.py`` classifies every exported class that is not a
``BaseModel`` as a use case and then asserts keyword-only collaborators over its
``__init__``, which no ``StrEnum`` or ``Protocol`` can satisfy -- so re-exporting one
would fail three of that file's gates. Reach them at their module.

Why the annotation reader is :mod:`rmspec.app.page_annotations`
--------------------------------------------------------------
Because every module in this workspace begins ``from __future__ import annotations``, this
package's own namespace binds the name ``annotations`` to a ``__future__._Feature``. A
module called ``annotations`` therefore made ``from rmspec.app import annotations`` resolve
to the feature flag rather than the module -- silently, and depending on whether the
submodule had already been imported, so it was order-dependent at run time. The obvious
answer is a test forbidding that import form, but this codebase prefers a wrong thing that
cannot be written to a wrong thing that is documented, and a name is the only fix that
holds for code nobody has written yet.
"""

from __future__ import annotations

from rmspec.app.capabilities import (
    OperationLimit,
    PortBinding,
    RefusedOperation,
    ReportCapabilities,
    ReportCapabilitiesRequest,
    ReportCapabilitiesResult,
)
from rmspec.app.catalog import (
    CatalogFolder,
    ListDocuments,
    ListDocumentsRequest,
    ListDocumentsResult,
)
from rmspec.app.create import CreateDocument, CreateDocumentRequest, CreateDocumentResult
from rmspec.app.diagrams import (
    ExtractDiagrams,
    ExtractDiagramsRequest,
    ExtractDiagramsResult,
    PageDiagram,
)
from rmspec.app.facts import (
    ReportDeviceFacts,
    ReportDeviceFactsRequest,
    ReportDeviceFactsResult,
    ReportedFact,
    ReportedGauge,
)
from rmspec.app.history import (
    ReportSyncHistory,
    ReportSyncHistoryRequest,
    ReportSyncHistoryResult,
)
from rmspec.app.page_annotations import (
    PageAnnotations,
    ReadAnnotations,
    ReadAnnotationsRequest,
    ReadAnnotationsResult,
)
from rmspec.app.render import (
    RenderedPageArtifact,
    RenderPages,
    RenderPagesRequest,
    RenderPagesResult,
)
from rmspec.app.reply import ReplyOnPage, ReplyOnPageRequest, ReplyOnPageResult
from rmspec.app.resolve import ResolveDocument, ResolveDocumentRequest, ResolveDocumentResult
from rmspec.app.search import SearchText, SearchTextRequest, SearchTextResult, TextMatch
from rmspec.app.selection import PageSelection
from rmspec.app.sync import (
    SyncDocuments,
    SyncDocumentsRequest,
    SyncDocumentsResult,
    SyncedDocumentOutcome,
)
from rmspec.app.transcribe import (
    TranscribedPage,
    TranscribePages,
    TranscribePagesRequest,
    TranscribePagesResult,
)

__all__ = [
    "CatalogFolder",
    "CreateDocument",
    "CreateDocumentRequest",
    "CreateDocumentResult",
    "ExtractDiagrams",
    "ExtractDiagramsRequest",
    "ExtractDiagramsResult",
    "ListDocuments",
    "ListDocumentsRequest",
    "ListDocumentsResult",
    "OperationLimit",
    "PageAnnotations",
    "PageDiagram",
    "PageSelection",
    "PortBinding",
    "ReadAnnotations",
    "ReadAnnotationsRequest",
    "ReadAnnotationsResult",
    "RefusedOperation",
    "RenderPages",
    "RenderPagesRequest",
    "RenderPagesResult",
    "RenderedPageArtifact",
    "ReplyOnPage",
    "ReplyOnPageRequest",
    "ReplyOnPageResult",
    "ReportCapabilities",
    "ReportCapabilitiesRequest",
    "ReportCapabilitiesResult",
    "ReportDeviceFacts",
    "ReportDeviceFactsRequest",
    "ReportDeviceFactsResult",
    "ReportSyncHistory",
    "ReportSyncHistoryRequest",
    "ReportSyncHistoryResult",
    "ReportedFact",
    "ReportedGauge",
    "ResolveDocument",
    "ResolveDocumentRequest",
    "ResolveDocumentResult",
    "SearchText",
    "SearchTextRequest",
    "SearchTextResult",
    "SyncDocuments",
    "SyncDocumentsRequest",
    "SyncDocumentsResult",
    "SyncedDocumentOutcome",
    "TextMatch",
    "TranscribePages",
    "TranscribePagesRequest",
    "TranscribePagesResult",
    "TranscribedPage",
]
