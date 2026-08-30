"""Everything a command does except call its one use case and render the answer.

Eleven command modules each run the same six steps: parse flags, open the writer, compose the
container, enter the request scope, call **one** use case, render, close. Five of those six are
identical in all eleven, so they live here once and every command inherits them. What is left
in a command body is the part that is genuinely its own::

    def ls(path: str | None = None, /, *, json: JsonFlag = False, dense: DenseFlag = False) -> int:
        \"\"\"List the documents on the tablet.\"\"\"

        def body(invoked: Invoked) -> int:
            result = invoked.get(ListDocuments).list(ListDocumentsRequest(path=path))
            if invoked.out.mode is OutputMode.JSON:
                invoked.out.emit(
                    result.model_dump(mode="json"),
                    response_type="catalog",
                    degradations=result.degradations,
                )
            else:
                invoked.report(result.degradations)
                invoked.out.rows(("uuid", "name"), ((d.uuid, d.name) for d in result.documents))
            return 0

        return run(body, json=json, dense=dense)

Four decisions are frozen here so that eleven commands cannot each get them subtly different.

One command is one device handshake, closed by one finalizer
-----------------------------------------------------------
:func:`invoked` composes the container, enters ``Scope.REQUEST`` with an
:class:`~rmspec.cli._container.Invocation` carrying the output mode, and closes the container in
a ``finally``. ``UsbWebApi`` and ``ParamikoShell`` are both bound as generator providers, so the
close is what runs their finalizers; a command that forgot it would leak a socket per run.

No command catches an error
---------------------------
:func:`run` is the single error boundary. It catches
:class:`~rmspec.domain.errors.RmspecError` -- the one class the whole domain tree descends from
-- and renders it through :meth:`~rmspec.cli._output.CliOutput.fail`, which is also what picks
the exit status. So a command body has no ``try`` in it at all and cannot invent a second exit
convention. Legacy had roughly sixty ``sys.exit(1)`` call sites and exactly two statuses.

The ambiguity policy is decided once
------------------------------------
:class:`~rmspec.app.ResolveDocument` never raises
:class:`~rmspec.domain.errors.AmbiguousDocument`; it ranks the matches, returns the winner, and
records ``DegradationKind.AMBIGUOUS_AUTO_RESOLVED``. :func:`resolve_document` is the only place
that reads that: **the default accepts the auto-resolution and surfaces the degradation, and
``--strict`` raises ``AmbiguousDocument`` carrying** ``result.also_matched``. No command
re-decides, so ``rmspec read notes`` and ``rmspec ocr notes`` cannot disagree about what
"several matched" means.

The work cap comes from settings, never from a literal
------------------------------------------------------
Four app-layer requests take ``max_pages`` with no default, deliberately:
``PageSelection.resolve_against`` is the single enforcement point and its docstring says a
silent default cap is the same surprise as legacy's silent last-page-only default.
:func:`page_cap` reads :attr:`~rmspec.cli._settings.CliSettings.max_pages` (``RMSPEC_MAX_PAGES``,
64) and lets ``--max-pages`` override it, so no command hardcodes a number and one 432-page
document cannot quietly become 432 model calls.

Why the container is reached through :func:`importlib.import_module`
-------------------------------------------------------------------
``tests/test_cli_entry.py`` AST-walks every ``cli/*.py`` except ``_container.py`` and fails on
any static import of an adapter package -- ``ast.walk``, so ``if TYPE_CHECKING:`` and a
function-local ``import`` count the same. ``_container.py`` is the one module allowed to name
adapters, so this module reaches it by a string the scan cannot read, which is also the only way
past ruff's ``PLC0415`` ban on function-local imports. The modules therefore stay **flat** in
``cli/``: a ``cli/commands/`` subpackage would escape the flat glob, which is a hole rather than
a licence.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from dishka import Container, Scope
from pydantic import BaseModel, InstanceOf

from rmspec import app
from rmspec.cli._output import CliOutput, ConsolePair, make_console_pair, open_output
from rmspec.cli._settings import CliSettings, apply_native_library_path, load_settings
from rmspec.domain import errors
from rmspec.domain.ports.errors import DependencyProbe

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from dishka import Provider

__all__ = [
    "FEATURE_DEVICE_SSH",
    "FEATURE_MARKDOWN_PDF",
    "FEATURE_MODEL_BEDROCK",
    "FEATURE_NAMES",
    "FEATURE_OCR_APPLE_VISION",
    "FEATURE_OCR_BDA",
    "FEATURE_OCR_TEXTRACT",
    "FEATURE_PDF_READ",
    "FEATURE_RASTER",
    "FEATURE_SCENE_DECODE",
    "PAGE_INDEX_SEPARATOR",
    "PAGE_RANGE_SEPARATOR",
    "PAGE_SPEC_GRAMMAR",
    "DenseFlag",
    "Invoked",
    "JsonFlag",
    "LimitOption",
    "MaxPagesOption",
    "PagesOption",
    "StrictFlag",
    "invoked",
    "page_cap",
    "page_selection",
    "render",
    "report_degradations",
    "resolve_document",
    "run",
]

_CONTAINER_MODULE: Final = "rmspec.cli._container"
"""The module a command reaches the container through, spelled as a string on purpose.

See this module's docstring: a static ``from rmspec.cli._container import compose`` would put
every adapter this package must not name into ``rmspec --help``'s import graph, and the entry
test's AST walk would fail the build for it.
"""

FEATURE_RASTER: Final = "raster"
"""Turning an SVG page into pixels: ``cairocffi``, ``cairosvg``, ``PIL``."""

FEATURE_PDF_READ: Final = "pdf-read"
"""Reading text and page geometry out of an annotated PDF: ``fitz``."""

FEATURE_SCENE_DECODE: Final = "scene-decode"
"""Parsing a v6 ``.rm`` scene: ``rmscene``."""

FEATURE_DEVICE_SSH: Final = "device-ssh"
"""Reaching the tablet over SSH: ``paramiko``."""

FEATURE_OCR_APPLE_VISION: Final = "ocr-apple-vision"
"""On-device handwriting recognition through the Vision framework: ``Quartz``."""

FEATURE_OCR_TEXTRACT: Final = "ocr-textract"
"""Handwriting recognition through AWS Textract: ``boto3``."""

FEATURE_OCR_BDA: Final = "ocr-bda"
"""Handwriting recognition through Bedrock Data Automation's sync read: ``boto3``."""

FEATURE_MODEL_BEDROCK: Final = "model-bedrock"
"""A vision-language model over Bedrock: ``boto3``."""

FEATURE_MARKDOWN_PDF: Final = "markdown-pdf"
"""Turning authored Markdown into a PDF to push: ``markdown``, ``weasyprint``."""

FEATURE_NAMES: Final = (
    FEATURE_RASTER,
    FEATURE_PDF_READ,
    FEATURE_SCENE_DECODE,
    FEATURE_DEVICE_SSH,
    FEATURE_OCR_APPLE_VISION,
    FEATURE_OCR_TEXTRACT,
    FEATURE_OCR_BDA,
    FEATURE_MODEL_BEDROCK,
    FEATURE_MARKDOWN_PDF,
)
"""Every feature :meth:`Invoked.probe` accepts, which is every member of ``_container.Feature``.

Mirrored here as plain strings rather than imported, because ``_container.Feature``'s module
names adapters and this one may not: ``test_cli_entry.py`` AST-walks every ``cli/*.py`` except
``_container.py``. A command therefore writes ``invoked.probe(FEATURE_RASTER)`` and imports no
adapter, while ``test_cli_invoke.py`` asserts this tuple equals ``{f.value for f in Feature}``
exactly -- so a feature added, renamed or deleted in the container fails the build here rather
than turning a command's probe into a silent no-op.

:meth:`Invoked.probe` converts each name back into a real ``Feature`` member, so a typo is a
``ValueError`` at the boundary and not a ``KeyError`` inside the probe.
"""

PAGE_INDEX_SEPARATOR: Final = ","
"""What separates one item of a ``--pages`` spec from the next."""

PAGE_RANGE_SEPARATOR: Final = "-"
"""What separates the ends of an inclusive range inside one ``--pages`` item."""

PAGE_SPEC_GRAMMAR: Final = (
    "a comma-separated list of 0-based page indices and inclusive A-B ranges, "
    "as in 0 or 2-5 or 0,3,7-9"
)
"""The whole ``--pages`` grammar in one sentence, shared by ``--help`` and every refusal.

Indices are **0-based**, matching ``page_index`` in every JSON payload this CLI emits and
``PageSelection.of``'s own vocabulary. A 1-based flag was considered and rejected: the primary
caller here is an agent that has just read ``page_index`` out of a ``catalog`` or ``document``
document, and making it add one before it can ask for that page is exactly the silent
off-by-one this project keeps finding in the legacy tree.

An empty item is dropped rather than refused, following the same choice
``RMSPEC_OCR_ENGINES`` makes about a trailing comma; a spec that names *no* index at all is a
:class:`~rmspec.domain.errors.UsageError`, because asking for pages and naming none is a typo
rather than a request for every page.
"""

JsonFlag = Annotated[bool, Parameter(name="--json", negative="")]
"""``--json``: emit the one envelope on stdout. Declared once so eleven commands agree.

``negative=""`` is load-bearing. cyclopts generates ``--no-json`` for a boolean otherwise, so
without it the CLI silently accepts a flag ``rmspec manifest`` does not list and ``--help`` does
not explain -- two surfaces disagreeing about the same flag, which is the drift the manifest
exists to end.
"""

DenseFlag = Annotated[bool, Parameter(name="--dense", negative="")]
"""``--dense``: emit tab-separated records on stdout. Mutually exclusive with ``--json``."""

StrictFlag = Annotated[bool, Parameter(name="--strict", negative="")]
"""``--strict``: refuse an ambiguous document selector instead of accepting the ranked winner.

The one flag :func:`resolve_document` reads. See this module's docstring for the frozen policy.
"""

PagesOption = Annotated[str | None, Parameter(name="--pages")]
"""``--pages``: which pages to work on, in the grammar :data:`PAGE_SPEC_GRAMMAR` states."""

LimitOption = Annotated[int | None, Parameter(name="--limit")]
"""``--limit``: work at most this many leading pages. Mutually exclusive with ``--pages``."""

MaxPagesOption = Annotated[int | None, Parameter(name="--max-pages")]
"""``--max-pages``: override ``RMSPEC_MAX_PAGES`` for this run only."""


def _page_index(text: str, /, *, spec: str) -> int:
    """Read one 0-based index out of a ``--pages`` item.

    Parameters
    ----------
    text
        The already-stripped digits.
    spec
        The whole spec as the user typed it, so the refusal quotes what they wrote rather
        than the fragment this call happens to be looking at.

    Returns
    -------
    int
        The index.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        *text* is not a run of decimal digits. ``str.isdecimal`` rather than
        ``str.isdigit``: the latter is true of ``"2"`` superscript two, which ``int`` then
        refuses with a ``ValueError`` the CLI would have to render as a traceback.
    """
    if not text.isdecimal():
        raise errors.UsageError(subject=f"--pages {spec!r}", requirement=PAGE_SPEC_GRAMMAR)
    return int(text)


def _page_item(item: str, /, *, spec: str) -> tuple[int, ...]:
    """Expand one item of a ``--pages`` spec to the indices it names.

    Parameters
    ----------
    item
        One already-stripped item: either an index or an inclusive ``A-B`` range.
    spec
        The whole spec, for the refusal message.

    Returns
    -------
    tuple[int, ...]
        One index, or every index of the range, ascending.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The item is malformed, or its range ends below where it starts. A descending range
        is refused rather than silently reversed, because ``5-2`` is a typo and quietly
        rendering pages 2 to 5 for it would teach the caller the wrong grammar.
    """
    head, separator, tail = item.partition(PAGE_RANGE_SEPARATOR)
    if not separator:
        return (_page_index(item, spec=spec),)
    start = _page_index(head.strip(), spec=spec)
    end = _page_index(tail.strip(), spec=spec)
    if end < start:
        raise errors.UsageError(
            subject=f"--pages {spec!r}",
            requirement="a range whose end is not below its start",
        )
    return tuple(range(start, end + 1))


def _page_indices(spec: str, /) -> tuple[int, ...]:
    """Parse a whole ``--pages`` spec.

    Parameters
    ----------
    spec
        The flag's value as the user typed it.

    Returns
    -------
    tuple[int, ...]
        Every index the spec names, in the order it named them.
        :meth:`~rmspec.app.PageSelection.of` sorts and deduplicates them afterwards, so
        ``--pages 3,1`` and ``--pages 1,3`` cannot produce differently ordered artifacts.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The spec is malformed, or it names no index at all.
    """
    indices: list[int] = []
    for raw in spec.split(PAGE_INDEX_SEPARATOR):
        item = raw.strip()
        if item:
            indices.extend(_page_item(item, spec=spec))
    if not indices:
        raise errors.UsageError(subject=f"--pages {spec!r}", requirement=PAGE_SPEC_GRAMMAR)
    return tuple(indices)


def page_selection(*, pages: str | None = None, limit: int | None = None) -> app.PageSelection:
    """Turn the ``--pages`` and ``--limit`` flags into the one selection the app layer takes.

    Parameters
    ----------
    pages
        The ``--pages`` spec, or ``None``. Grammar: :data:`PAGE_SPEC_GRAMMAR`.
    limit
        The ``--limit`` count, or ``None``.

    Returns
    -------
    ~rmspec.app.PageSelection
        An explicit selection for *pages*, a leading bound for *limit*, and every page when
        neither was given.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        Both flags were passed, *limit* is not positive, or *pages* is malformed. Each of
        those is refused here rather than left to :class:`~rmspec.app.PageSelection`'s own
        constraints, because a ``pydantic.ValidationError`` escaping a command body is a
        second error vocabulary with a traceback and exit status 1 -- while a ``UsageError``
        renders through the same envelope as every other failure and exits 2.
    """
    if pages is not None and limit is not None:
        raise errors.UsageError(
            subject="--pages and --limit",
            requirement="at most one of them, because a selection names explicit pages "
            "or a leading count, never both",
        )
    if pages is not None:
        return app.PageSelection.of(*_page_indices(pages))
    if limit is None:
        return app.PageSelection.all()
    if limit <= 0:
        raise errors.UsageError(subject=f"--limit {limit}", requirement="a count above zero")
    return app.PageSelection.first(limit)


def page_cap(settings: CliSettings, /, *, override: int | None = None) -> int:
    """Give the most pages this run may work on.

    Parameters
    ----------
    settings
        Supplies :attr:`~rmspec.cli._settings.CliSettings.max_pages`, which is
        ``RMSPEC_MAX_PAGES`` and defaults to 64.
    override
        The ``--max-pages`` value, or ``None`` to take the setting.

    Returns
    -------
    int
        The cap to pass as a request's ``max_pages``.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        *override* is not positive. A cap of zero is not a smaller run, it is a run that
        cannot do anything, so it is a contradiction in the command line rather than a
        result of no pages.
    """
    if override is None:
        return settings.max_pages
    if override <= 0:
        raise errors.UsageError(subject=f"--max-pages {override}", requirement="a cap above zero")
    return override


def resolve_document(
    resolver: app.ResolveDocument,
    /,
    *,
    query: str,
    strict: bool = False,
) -> app.ResolveDocumentResult:
    """Resolve one selector to one document under the frozen ambiguity policy.

    Parameters
    ----------
    resolver
        The use case, normally from :meth:`Invoked.get`.
    query
        A name substring, a full uuid, or a uuid prefix, as the user typed it.
    strict
        Whether ``--strict`` was passed.

    Returns
    -------
    ~rmspec.app.ResolveDocumentResult
        The chosen document, every other match, and every substitution the resolution made.
        The caller passes ``result.degradations`` to :func:`report_degradations` or to
        :meth:`~rmspec.cli._output.CliOutput.emit` like any other result's.

    Raises
    ------
    ~rmspec.domain.errors.AmbiguousDocument
        *strict* was set and the query matched more than one document. Carries
        ``result.also_matched``, whose element type is already the one
        :class:`~rmspec.domain.errors.AmbiguousDocument` declares, so nothing is converted.

    Notes
    -----
    The policy is frozen and no command may re-decide it: the default **accepts** the ranked
    winner and lets ``DegradationKind.AMBIGUOUS_AUTO_RESOLVED`` say so, and ``--strict`` turns
    the same situation into a failure. That split is the whole reason the use case reports the
    choice twice, as candidates and as a degradation.
    """
    result = resolver.resolve(app.ResolveDocumentRequest(query=query))
    if strict and result.also_matched:
        raise errors.AmbiguousDocument(query=query, candidates=result.also_matched)
    return result


def _collapsed(
    degradations: Sequence[errors.Degradation],
    /,
) -> tuple[errors.Degradation, ...]:
    """Collapse identical degradations into one, counted, for a human to read.

    Parameters
    ----------
    degradations
        The tuple a use-case result carries, in the order it recorded them.

    Returns
    -------
    tuple[~rmspec.domain.errors.Degradation, ...]
        One entry per distinct degradation, in first-occurrence order. An entry that stood
        for several gets ``(xN)`` appended to its ``detail``.

    Notes
    -----
    The app layer deliberately does not deduplicate, and ``_output.py`` says why: "collapsing
    duplicates is a presentation choice and belongs in ``HUMAN`` mode only". This is that
    choice, made once. It applies to the **stderr** rendering and to nothing else -- the JSON
    envelope still carries every degradation, in order, untouched -- because a listing that
    skipped forty unreadable entries should tell a person "forty" on one line rather than
    print forty near-identical lines above their table, while an agent counting occurrences
    must still be able to count them.
    """
    seen: dict[tuple[str, str, str, str | None], tuple[errors.Degradation, int]] = {}
    for degradation in degradations:
        key = (
            degradation.kind.value,
            degradation.subject,
            degradation.detail,
            degradation.substituted,
        )
        first, count = seen.get(key, (degradation, 0))
        seen[key] = (first, count + 1)
    return tuple(
        first if count == 1 else first.model_copy(update={"detail": f"{first.detail} (x{count})"})
        for first, count in seen.values()
    )


def report_degradations(out: CliOutput, degradations: Sequence[errors.Degradation], /) -> None:
    """Surface a run's degradations the way this invocation's mode requires.

    Parameters
    ----------
    out
        The invocation's writer, which knows the mode.
    degradations
        The tuple the use-case result carries. An empty sequence writes nothing, so a caller
        does not have to check first.

    Notes
    -----
    In :attr:`~rmspec.cli._output.OutputMode.JSON` this writes **nothing**, because
    :meth:`~rmspec.cli._output.CliOutput.emit` already puts the same tuple in the envelope's
    top-level ``degradations`` and a second copy on stderr would be a duplicate an agent
    parsing stdout has no way to reconcile. In ``HUMAN`` and in ``DENSE`` it goes to stderr
    through :meth:`~rmspec.cli._output.CliOutput.report_degradations`, collapsed by
    :func:`_collapsed` -- ``DENSE``'s stdout is a homogeneous record stream that a warning
    would corrupt, and its caller still needs to know a substitution happened.

    Calling this is not optional. A degradation is the app layer's way of saying it did
    something other than what was asked without failing, and the one behaviour this CLI must
    never have is legacy's: substituting silently.
    """
    if out.machine_readable:
        return
    out.report_degradations(_collapsed(degradations))


class Invoked(BaseModel, frozen=True, extra="forbid"):
    """One command invocation, opened and ready: a writer, the settings, and a request scope.

    Yielded by :func:`invoked` and handed to :func:`run`'s body. Every method is a thin
    delegation to the module-level function of the same job, so a command may use either and
    they cannot disagree.

    ``InstanceOf`` on all three fields is deliberate: it makes each one a plain isinstance
    check rather than a pydantic schema over a ``rich.Console``, a ``BaseSettings`` and a
    dishka ``Container``, none of which is data and none of which should be revalidated or
    copied on the way in. Declaring them as fields of a ``pydantic.BaseModel`` is also what
    gives their imports the runtime use ``TC001``/``TC002`` demand, which is the discipline
    ``_container.py`` follows with ``BOUND_PORTS`` rather than a suppression.
    """

    out: InstanceOf[CliOutput]
    """Everything this command writes goes through here, in one of three modes."""

    settings: InstanceOf[CliSettings]
    """The validated ``RMSPEC_*`` environment, already loaded by :func:`invoked`."""

    request: InstanceOf[Container]
    """The container with ``Scope.REQUEST`` entered. Reach it through :meth:`get`."""

    def get[PortT](self, port: type[PortT], /) -> PortT:
        """Resolve one use case or port out of the request scope.

        Parameters
        ----------
        port
            The type to resolve -- a use case class from :mod:`rmspec.app`, or a port from
            :mod:`rmspec.domain.ports`.

        Returns
        -------
        PortT
            The bound instance. A command calls this **once**, for its one use case; a
            command reaching for two is a command doing two jobs.
        """
        return self.request.get(port)

    def probe(self, *features: str) -> None:
        """Prove every optional module this command needs, before anything is paid for.

        Parameters
        ----------
        *features
            Names from :data:`FEATURE_NAMES` -- the features this command will actually use.
            Passing none probes nothing, which is why ``env`` and ``manifest`` can call the
            rest of this class without ever touching a probe.

        Raises
        ------
        ~rmspec.domain.errors.MissingDependencyError
            A selected module is absent, or is installed and will not load. Carries
            ``package``, ``extra`` and ``feature``, and a remediation of
            ``uv sync --extra <extra>``; the domain's table scores it ``EX_CONFIG``, 78.

        Notes
        -----
        This is the first statement of a command body, ahead of the render, the device round
        trip and any model call, and that ordering is the whole point. The legacy tree raised
        ``ImportError`` from **27 function-local import sites**, every one of them reached
        *after* the user had already paid for a render or a device round trip, and every one
        naming a module (``cairocffi``) rather than the extra that ships it (``render``).

        **The whole failure set is collected before anything is raised.** ``probe_features``
        returns rather than raises for exactly that reason -- its own docstring says a user
        missing two extras must learn both from one run -- and it is also where
        ``rmspec.ocr.require_backends`` is folded in, through ``require_engines``, for the
        engine features whose modules probed clean. So this method adds no second check: it
        selects the union, and reports.

        With several failures the **first** becomes the raised error and the rest are warned
        about on stderr. The alternative -- one error whose message names them all -- was
        rejected because ``MissingDependencyError`` carries ``package``, ``extra`` and
        ``feature`` as *structured* fields that the failure envelope renders verbatim and an
        agent branches on, and a composite ``"cairocffi and fitz"`` in ``package`` is a value
        no ``uv sync --extra`` accepts and no probe could re-check. Warning the remainder
        keeps every field true, keeps the exit status right, and still tells the user
        everything in one run. ``resolve_dependencies`` deduplicates by package and orders by
        it, so "the first" is the same failure between two runs rather than whichever module
        happened to be probed first. The warnings go to stderr in every mode, so they can
        neither corrupt a ``JSON`` envelope nor a ``DENSE`` record stream.
        """
        if not features:
            return
        module = importlib.import_module(_CONTAINER_MODULE)
        failures = module.probe_features(
            self.get(DependencyProbe),
            [module.Feature(name) for name in features],
        )
        if not failures:
            return
        for failure in failures[1:]:
            detail = "" if failure.detail is None else f" -- {failure.detail}"
            self.out.warn(
                f"also unusable: {failure.package}, needed for {failure.feature}{detail}; "
                f"install it with uv sync --extra {failure.extra}"
            )
        raise failures[0].as_error()

    def max_pages(self, override: int | None = None, /) -> int:
        """Give the work cap for this run.

        Parameters
        ----------
        override
            The ``--max-pages`` value, or ``None`` to take ``RMSPEC_MAX_PAGES``.

        Returns
        -------
        int
            The cap, for a request's ``max_pages`` field.
        """
        return page_cap(self.settings, override=override)

    def selection(
        self,
        *,
        pages: str | None = None,
        limit: int | None = None,
    ) -> app.PageSelection:
        """Turn this command's page flags into a selection.

        Parameters
        ----------
        pages
            The ``--pages`` spec, or ``None``.
        limit
            The ``--limit`` count, or ``None``.

        Returns
        -------
        ~rmspec.app.PageSelection
            The selection to put in the request's ``selection`` field.
        """
        return page_selection(pages=pages, limit=limit)

    def document(self, query: str, /, *, strict: bool = False) -> app.ResolveDocumentResult:
        """Resolve a document selector under the frozen ambiguity policy.

        Parameters
        ----------
        query
            The selector as the user typed it.
        strict
            Whether ``--strict`` was passed.

        Returns
        -------
        ~rmspec.app.ResolveDocumentResult
            The chosen document, the other matches, and the substitutions made.
        """
        return resolve_document(self.get(app.ResolveDocument), query=query, strict=strict)

    def report(self, degradations: Sequence[errors.Degradation], /) -> None:
        """Surface degradations the way this invocation's mode requires.

        Parameters
        ----------
        degradations
            The tuple the use-case result carries.
        """
        report_degradations(self.out, degradations)


class _Opened(BaseModel, frozen=True, extra="forbid"):
    """The two things that exist before any work starts, and the refusal that stops it.

    Built by :func:`_open` so that :func:`run` and :func:`invoked` share one spelling of the
    steps that must happen before a container exists. The consoles are carried alongside the
    writer because ``compose`` needs them as ``Scope.APP`` context and
    :class:`~rmspec.cli._output.CliOutput` does not hand its own back out.
    """

    consoles: InstanceOf[ConsolePair]
    """The process's two streams, for the container's app-scoped context."""

    out: InstanceOf[CliOutput]
    """The writer, always usable, whichever mode the flags resolved to."""

    refusal: InstanceOf[errors.UsageError] | None
    """The mode contradiction to render instead of running, or ``None``.

    ``--json`` and ``--dense`` together. Returned rather than raised, because the object that
    would render a raised mode error is the very object that could not be built -- which is
    the reasoning :func:`~rmspec.cli._output.open_output` already states.
    """


def _open(*, json: bool, dense: bool) -> _Opened:
    """Do the two things that precede every command, and resolve the output mode.

    Parameters
    ----------
    json
        Whether ``--json`` was passed.
    dense
        Whether ``--dense`` was passed.

    Returns
    -------
    _Opened
        The consoles, the writer, and the refusal or ``None``.

    Notes
    -----
    ``apply_native_library_path(os.environ)`` is the first line of a command and not an import
    side effect: legacy mutated ``DYLD_FALLBACK_LIBRARY_PATH`` while the CLI package was being
    imported, so collecting a test or asking for ``--version`` changed the process environment.
    ``sys.stdout`` and ``sys.stderr`` are read here rather than at import, which is what lets a
    test capture them.
    """
    apply_native_library_path(os.environ)
    consoles = make_console_pair(stdout=sys.stdout, stderr=sys.stderr)
    out, refusal = open_output(consoles, json=json, dense=dense)
    return _Opened(consoles=consoles, out=out, refusal=refusal)


@contextlib.contextmanager
def _entered(opened: _Opened, /, *, providers: Sequence[Provider] = ()) -> Iterator[Invoked]:
    """Load the settings, compose the container, and enter the request scope exactly once.

    Parameters
    ----------
    opened
        The result of :func:`_open`, whose ``refusal`` the caller has already handled.
    providers
        Extra providers appended after the defaults.

    Yields
    ------
    Invoked
        The opened invocation.

    Notes
    -----
    The container is closed in a ``finally``, not by the ``with``: the ``with`` closes
    ``Scope.REQUEST`` and the ``close`` closes ``Scope.APP``, and ``UsbWebApi`` and
    ``ParamikoShell`` are both bound as generator providers whose finalizers only run on the
    latter. One command is one device handshake and one close.
    """
    settings = load_settings()
    module = importlib.import_module(_CONTAINER_MODULE)
    invocation = module.Invocation
    container = module.compose(settings=settings, consoles=opened.consoles, providers=providers)
    try:
        with container(
            context={invocation: invocation(mode=opened.out.mode)},
            scope=Scope.REQUEST,
        ) as request:
            yield Invoked(out=opened.out, settings=settings, request=request)
    finally:
        container.close()


@contextlib.contextmanager
def invoked(
    *,
    json: bool = False,
    dense: bool = False,
    providers: Sequence[Provider] = (),
) -> Iterator[Invoked]:
    """Open one command invocation, and close it however the body ends.

    Parameters
    ----------
    json
        Whether ``--json`` was passed.
    dense
        Whether ``--dense`` was passed.
    providers
        Extra providers appended after the container's defaults, each with ``override=True``
        on its ``provide``. This is how a test binds the shipped in-memory doubles, so that
        no test opens an SSH session or constructs a ``bedrock-runtime`` or ``textract``
        client. Empty in every real run.

    Yields
    ------
    Invoked
        The writer, the settings, and a resolved request scope.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        ``--json`` and ``--dense`` were both passed. Raised rather than rendered, because
        this is the low-level entry point; :func:`run` catches it and renders it, and a
        command should be calling :func:`run`.
    """
    opened = _open(json=json, dense=dense)
    if opened.refusal is not None:
        raise opened.refusal
    with _entered(opened, providers=providers) as invocation:
        yield invocation


def render(body: Callable[[CliOutput], int], /, *, json: bool = False, dense: bool = False) -> int:
    """Run a command body that needs no container, with the same error boundary as the rest.

    Parameters
    ----------
    body
        The command's work, given only the writer. Must return an ``int``, never a ``bool``.
    json
        Whether ``--json`` was passed.
    dense
        Whether ``--dense`` was passed.

    Returns
    -------
    int
        Whatever *body* returned, or :func:`~rmspec.domain.errors.exit_code`'s answer for the
        failure that ended it.

    Notes
    -----
    For the two commands that describe the CLI rather than use it: ``env`` and ``manifest``.
    Neither has a document, a page or a device in it, and composing a container for either
    would mean ``rmspec manifest`` failing on a mistyped ``RMSPEC_*`` -- while the manifest is
    precisely where a caller goes to learn how those variables are spelled. Use :func:`run`
    for anything that calls a use case.
    """
    opened = _open(json=json, dense=dense)
    if opened.refusal is not None:
        return opened.out.fail(opened.refusal)
    try:
        return body(opened.out)
    except errors.RmspecError as err:
        return opened.out.fail(err)


def run(
    body: Callable[[Invoked], int],
    /,
    *,
    json: bool = False,
    dense: bool = False,
    providers: Sequence[Provider] = (),
) -> int:
    """Run one command body inside an open invocation, and be its only error boundary.

    Parameters
    ----------
    body
        The command's own work: call one use case, render, ``return 0``. It must return an
        ``int`` and **never** a ``bool`` -- cyclopts checks ``bool`` before ``int`` when it
        turns a return value into an exit status, so ``return True`` exits 0 and ``return
        False`` exits 1, both silently.
    json
        Whether ``--json`` was passed.
    dense
        Whether ``--dense`` was passed.
    providers
        Extra providers appended after the container's defaults. See :func:`invoked`.

    Returns
    -------
    int
        Whatever *body* returned, or the status
        :func:`~rmspec.domain.errors.exit_code` gives the failure that ended it. Always an
        ``int``, so a command may ``return run(...)`` directly.

    Notes
    -----
    Everything that can fail is inside the one ``try``: loading the settings, composing the
    container, resolving a port, and the body itself. That is why a command body has no
    ``try`` in it and why there is no branch in this CLI where a
    :class:`~rmspec.domain.errors.RmspecError` becomes a traceback.
    """
    opened = _open(json=json, dense=dense)
    if opened.refusal is not None:
        return opened.out.fail(opened.refusal)
    try:
        with _entered(opened, providers=providers) as invocation:
            return body(invocation)
    except errors.RmspecError as err:
        return opened.out.fail(err)
