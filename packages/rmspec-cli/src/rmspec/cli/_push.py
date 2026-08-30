"""``rmspec push FILE`` -- place one new document on the attached tablet.

The only *supported* write path in this project, and the one command whose effect a human
sees immediately: xochitl performs the import itself, so the document appears in the tablet UI
with no restart and nothing stopped. Measured 2026-08-29 on firmware 3.27.3.0 -- the root
listing went from 10 entries to 11 while the tablet was in the user's hands.

It is also the one command that cannot be taken back. That firmware's route table is closed at
six families and **none of them deletes**, so a document pushed by mistake costs a manual
delete on the tablet. Every refusal this module can make it therefore makes *before* the
multipart body is built: the suffix, the readability of the file, the page count, the blank
name, the duplicate. :class:`~rmspec.app.CreateDocument` owns four of those and this module
owns the ones that need the filesystem.

Where the page count comes from, and why it is never a literal
-------------------------------------------------------------
``CreateDocumentRequest.page_count`` is required, has no default, and a zero is a
``UsageError``. The prior art that rule exists for is a neighbouring project that uploads a
valid, empty PDF whenever its own parser silently returns nothing -- silent successful
delivery of nothing, to a device the user trusts. So this command's job is to pass the count
some component actually measured, and it has a different measurer per input:

===============  ====================================================================
input            who counted the pages
===============  ====================================================================
``.md``          ``weasyprint``, from the layout it just performed. See ``_markdown.py``.
``.pdf``         :class:`~rmspec.domain.ports.export.PdfPageReader`, the one component in
                 this repo allowed to answer "how many pages is this PDF". Its own
                 docstring records that it *replaced* a regular-expression scan for page
                 objects which over- and under-counted on real documents, so this module
                 does not write a second one.
``.rmdoc``       the archive itself, from the ``cPages`` page list in its ``.content``
                 member -- read with :mod:`zipfile` and :mod:`json`, which are standard
                 library and so not the adapter import the entry test forbids here.
===============  ====================================================================

An ``.epub`` is refused rather than guessed at. The upload route accepts one and
:class:`~rmspec.domain.ports.device.UploadMedia` has a member for it, but nothing in this
process can state an EPUB's real page count -- a reflowable book has none until a reader lays
it out -- and the alternative is the placeholder the zero-page refusal exists to catch. The
refusal names the accepted suffixes, so the gap is a sentence a user reads rather than a
surprise they discover.

``--parent`` is handed on verbatim, never quietly root-placed
------------------------------------------------------------
The USB import route has **no destination parameter**, and there is a second reason beyond
that: it targets the last folder the caller listed, so steering it would mean owning
device-global mutable traversal state, which is a race rather than an API. So
:class:`~rmspec.device.UsbUploader` raises ``DeviceOperationUnsupported`` naming SSH as the
transport that can, before the first byte is sent, and neither
:class:`~rmspec.app.CreateDocument` nor this module catches it. Whether ``--parent`` works is
therefore a fact about ``RMSPEC_TRANSPORT``, decided by the binding, and a user who asked for
``/Books`` is told no instead of being handed the library root with a success status.

One receipt, three modes, and a rotation rather than a one-row table
-------------------------------------------------------------------
``--json`` writes one envelope to stdout, ``--dense`` writes one tab-separated record to stdout,
and the default writes to **stderr**. That split is the frozen output contract, and it is what
makes ``rmspec push notes.md --json | jq`` clean by construction and
``rmspec push notes.md 2>/dev/null`` correctly silent on a human run.

This command answers with exactly one row, so the two page commands' problem -- too many columns
for a terminal -- is not the one it has. A one-row table is still the wrong shape for a person:
six headers over six cells makes a reader scan sideways to pair each value with its name. So
``HUMAN`` **rotates** the same record into labelled lines through :func:`_receipt`, built by
zipping :data:`PUSH_COLUMNS` with :func:`_row`'s own output under ``strict=True``. Nothing is
dropped and nothing can drift, because the human rendering and the dense one are the same tuple
read two ways. The one substitution is cosmetic and stated: a cell the run genuinely has no value
for reads :data:`UNREPORTED` rather than as a label with nothing after it, which is how
``doc_uuid`` comes back over USB and how ``visible_name`` comes back for an ``.rmdoc``.

The two ``degradations`` are not the same tuple, even where they hold the same entries
-------------------------------------------------------------------------------------
Across this CLI the envelope's top-level ``degradations`` is everything that happened during the
invocation, document resolution included, while ``data.degradations`` is only what the use case
itself recorded -- ``data`` being a faithful ``model_dump(mode="json")`` of the result, which is
what ``rmspec manifest`` says ``data`` **is**. The top level is a superset of the payload's, never
a duplicate of it.

``push`` resolves no document: it is handed a path, not a selector, so there is no
``ambiguous_auto_resolved`` to hoist and the two tuples happen to hold the same entries here. That
is a fact about this command, not the contract. A reader who concludes from this one file that the
two are one tuple, and deletes either, breaks the eight commands that do resolve a document.

Why ``_markdown.py`` is reached by string
-----------------------------------------
``importlib.import_module`` and not ``from rmspec.cli._markdown import to_pdf``, because that
module imports ``weasyprint`` at module scope and ``weasyprint`` links against native
libraries. ``__init__.py`` imports this module in order to register the command, so a static
import would put a ``dlopen`` on the path of ``rmspec --help``. The feature is probed first, so
a user without the extra gets ``MissingDependencyError`` and ``uv sync --extra push`` rather
than an ``OSError`` from inside a conversion they have already started.
"""

from __future__ import annotations

import importlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from pydantic import BaseModel, Field
from rich.table import Table

from rmspec.app import CreateDocument, CreateDocumentRequest
from rmspec.cli._invoke import (
    FEATURE_MARKDOWN_PDF,
    FEATURE_PDF_READ,
    DenseFlag,
    Invoked,
    JsonFlag,
    run,
)
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import NextAction, OutputMode
from rmspec.domain.errors import UsageError
from rmspec.domain.ports.device import UploadMedia
from rmspec.domain.ports.export import PdfPageReader

if TYPE_CHECKING:
    from rmspec.app import CreateDocumentResult

__all__ = ["ACCEPTED_SUFFIXES", "PUSH_COLUMNS", "UNREPORTED", "push"]

_MARKDOWN_MODULE: Final = "rmspec.cli._markdown"
"""Where Markdown becomes a PDF, spelled as a string so ``--help`` never loads it.

See this module's docstring. The same escape ``_invoke.py`` uses to reach the container, and
for a related reason: a name the AST scan cannot read is also the only way past ruff's
``PLC0415`` ban on a function-local ``import``.
"""

_CONTAINER_MODULE: Final = "rmspec.cli._container"
"""The composition root, reached by string because it is the one module allowed to name
adapters and this one may not. ``PdfSourceRegistry`` is the only name taken from it: it mints
the opaque reference :class:`~rmspec.domain.ports.export.PdfPageReader` resolves, and the
reader was constructed with *that* registry, so a second instance would not do."""

_REGISTRY_ATTRIBUTE: Final = "PdfSourceRegistry"
"""The container attribute that mints a PDF source reference, named once so a test can pin it.

Not part of ``_container.__all__``, so a rename would otherwise turn ``rmspec push doc.pdf``
into an ``AttributeError`` at the moment of use. ``test_cli_push.py`` resolves it, which fails
the build here instead."""

_MARKDOWN_SUFFIXES: Final = (".md", ".markdown")
"""What is converted to a PDF before it is uploaded."""

_PDF_SUFFIX: Final = ".pdf"
"""What is uploaded as-is, with its pages counted through the PDF reader port."""

_ARCHIVE_SUFFIX: Final = ".rmdoc"
"""The tablet's own whole-document container, uploaded as-is and imported as a notebook."""

_EPUB_SUFFIX: Final = ".epub"
"""Accepted by the route, refused here. See this module's docstring."""

_CONTENT_SUFFIX: Final = ".content"
"""The archive member carrying the page order. Exactly one per ``.rmdoc``."""

_PAGES_CONTAINER: Final = "cPages"
"""The ``.content`` key holding the firmware-3.x page list."""

_PAGES_KEY: Final = "pages"
"""The key inside :data:`_PAGES_CONTAINER` whose list length is the page count."""

ACCEPTED_SUFFIXES: Final = (*_MARKDOWN_SUFFIXES, _PDF_SUFFIX, _ARCHIVE_SUFFIX)
"""Every suffix ``rmspec push`` accepts, in the order the refusal lists them.

Public because it is the answer to "what can I push", and because ``--help`` and the refusal
should not be able to disagree about it.
"""

PUSH_COLUMNS: Final = (
    "doc_uuid",
    "requested_name",
    "visible_name",
    "media",
    "byte_count",
    "library_refresh",
)
"""The columns the non-JSON modes project: the identity, then what the tablet now holds.

``doc_uuid`` first even though it is empty over USB -- the 201 body carries no identifier and
this project refuses to guess one by re-listing, so an empty first cell is a true statement
about the transport rather than a missing value.

Both non-JSON modes read this one tuple: ``--dense`` writes it across as a record, and ``HUMAN``
rotates it into labelled lines. Neither can carry a fact the other does not.
"""

UNREPORTED: Final = "(not reported)"
"""What the ``HUMAN`` receipt puts where a cell is empty, so a label is never left dangling.

``--dense`` leaves those cells empty and must: a record's consumer needs "no value" to be
distinguishable from the string ``"None"``, and an empty field is how a tab-separated stream says
it. A person reading ``doc_uuid`` with nothing after it reads a bug instead, so the human rendering
says it in words. Two cells reach it, both honestly: ``doc_uuid`` over USB, where the 201 body
carries no identifier and this project will not guess one, and ``visible_name`` for an ``.rmdoc``,
whose name the firmware takes from the archive's own metadata after the upload has already
returned.
"""

_NEXT_ACTION: Final = NextAction(
    command="rmspec ls --json",
    purpose="read the new document's uuid, which the upload route does not report",
)
"""Where a caller goes next, because ``doc_uuid`` is genuinely unknown over USB."""

_NameOption = Annotated[str | None, Parameter(name="--name")]
"""``--name``: what the document should be called, defaulting to the file's own name."""

_ParentOption = Annotated[str | None, Parameter(name="--parent")]
"""``--parent``: the uuid of a destination folder, passed to the transport verbatim."""

_AllowDuplicateNameFlag = Annotated[bool, Parameter(name="--allow-duplicate-name", negative="")]
"""``--allow-duplicate-name``: proceed even though the library root already holds the name."""


class _Plan(BaseModel, frozen=True, extra="forbid"):
    """What one input file's suffix decided, before anything has been read.

    Carries the path as well as the decision, so nothing downstream takes the two as separate
    arguments that could be paired wrongly. Declaring it as a ``pydantic.BaseModel`` field is
    also what gives :class:`~pathlib.Path` the runtime use ``TC003`` demands -- ruff's config
    names ``pydantic.BaseModel`` as runtime-evaluated -- which matters because cyclopts
    resolves ``push``'s annotations at registration and a ``Path`` moved into an
    ``if TYPE_CHECKING:`` block would make the command unregisterable. Same discipline as
    ``_container.py``'s ``BOUND_PORTS``, and for the same reason: a real use, not a
    suppression.
    """

    source: Path
    """The file the user named."""

    media: UploadMedia
    """Which of the route's three container kinds the payload will be."""

    features: tuple[str, ...]
    """The optional modules this input needs, probed before the file is opened."""

    converted: bool
    """Whether the bytes on disk are the payload, or a conversion produces it."""


class _Payload(BaseModel, frozen=True, extra="forbid"):
    """The three facts a :class:`~rmspec.app.CreateDocumentRequest` needs from the filesystem."""

    name: str
    """The default name, which ``--name`` overrides."""

    data: bytes
    """The complete payload."""

    page_count: int = Field(ge=0)
    """How many pages some component actually counted. Never a literal."""


def _plan(path: Path, /) -> _Plan:
    """Decide what an input is from its suffix alone, paying for nothing.

    Parameters
    ----------
    path
        The file the user named.

    Returns
    -------
    _Plan
        The media, the features to probe, and whether a conversion is needed.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The suffix is an ``.epub``, whose real page count nothing here can state, or is not
        one this command accepts at all. Raised from the suffix, so a typo costs no read,
        no conversion and no device round trip.
    """
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        return _Plan(
            source=path,
            media=UploadMedia.PDF,
            features=(FEATURE_MARKDOWN_PDF,),
            converted=True,
        )
    if suffix == _PDF_SUFFIX:
        return _Plan(
            source=path,
            media=UploadMedia.PDF,
            features=(FEATURE_PDF_READ,),
            converted=False,
        )
    if suffix == _ARCHIVE_SUFFIX:
        return _Plan(source=path, media=UploadMedia.RMDOC, features=(), converted=False)
    if suffix == _EPUB_SUFFIX:
        raise UsageError(
            subject="an .epub, whose page count no component in this process can state",
            requirement=(
                "one of "
                + ", ".join(ACCEPTED_SUFFIXES)
                + ", because a reflowable book has no page count until a reader lays it out "
                "and a placeholder is what the zero-page refusal exists to catch"
            ),
        )
    raise UsageError(
        subject=f"a {suffix or 'suffixless'} file",
        requirement="one of " + ", ".join(ACCEPTED_SUFFIXES),
    )


def _archive_page_list(data: bytes, /) -> object:
    """Read the page list out of an ``.rmdoc``'s own ``.content`` member.

    Parameters
    ----------
    data
        The whole archive.

    Returns
    -------
    object
        Whatever the ``cPages.pages`` member held, unvalidated. The caller decides whether
        it is a list, because every way this can go wrong here is an exception and doing
        both in one function would mean raising inside the ``try`` that catches them.

    Notes
    -----
    Read from bytes with :class:`io.BytesIO` and never spilled to a temporary, which is the
    rule ``rmspec.device._archive`` states for the same archive on the download side. The
    page list and not the count of ``.rm`` members: measured, an archive can carry 16 scene
    members for 10 pages, because layers orphaned by an edit stay in the store and are
    unreachable from ``cPages``.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        (member,) = [name for name in archive.namelist() if name.endswith(_CONTENT_SUFFIX)]
        content = json.loads(archive.read(member))
    return content[_PAGES_CONTAINER][_PAGES_KEY]


def _archive_page_count(data: bytes, /) -> int:
    """Count the pages an ``.rmdoc`` archive declares.

    Parameters
    ----------
    data
        The whole archive.

    Returns
    -------
    int
        How many pages the archive's own page order names.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The bytes are not a zip, carry no single ``.content`` member, or carry one whose
        ``cPages.pages`` is absent or is not a list. A ``UsageError`` and not a device
        error, because nothing about the device is wrong and the fix is the file the user
        just named.
    """
    try:
        pages = _archive_page_list(data)
    except (zipfile.BadZipFile, LookupError, TypeError, ValueError) as bad:
        raise UsageError(
            subject=f"an {_ARCHIVE_SUFFIX} whose page order could not be read: {bad}",
            requirement=(
                f"an archive carrying one {_CONTENT_SUFFIX} member with a "
                f"{_PAGES_CONTAINER}.{_PAGES_KEY} list"
            ),
        ) from bad
    if not isinstance(pages, list):
        raise UsageError(
            subject=f"an {_ARCHIVE_SUFFIX} whose {_PAGES_CONTAINER}.{_PAGES_KEY} is not a list",
            requirement=f"a {_PAGES_CONTAINER}.{_PAGES_KEY} list, one entry per page",
        )
    return len(pages)


def _pdf_page_count(invoked: Invoked, path: Path, /) -> int:
    """Count the pages of a PDF through the one port allowed to answer that.

    Parameters
    ----------
    invoked
        The open invocation, for its request scope.
    path
        The PDF on disk. Registered by path rather than by bytes, so nothing is spooled to
        a second temporary file that the binding has no finalizer to remove.

    Returns
    -------
    int
        The page count :class:`~rmspec.domain.ports.export.PdfPageReader` read.

    Raises
    ------
    ~rmspec.domain.errors.PdfSourceUnreadable
        The file is missing, is not a PDF, is corrupt, or needs a password. Raised by the
        port and rendered by :func:`~rmspec.cli._invoke.run` like any other failure.
    """
    container = importlib.import_module(_CONTAINER_MODULE)
    registry = invoked.get(getattr(container, _REGISTRY_ATTRIBUTE))
    return invoked.get(PdfPageReader).page_count(registry.for_path(path))


def _decoded(path: Path, /) -> str:
    """Read a Markdown file as text.

    Parameters
    ----------
    path
        The file.

    Returns
    -------
    str
        Its contents.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The bytes are not UTF-8. Refused rather than decoded with replacement characters:
        a document silently full of ``U+FFFD`` is delivered content that misrepresents what
        the author wrote, on a route that cannot take it back.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as bad:
        raise UsageError(
            subject=f"{path.name!r}, which is not UTF-8 text",
            requirement="UTF-8 encoded Markdown",
        ) from bad


def _converted(path: Path, /) -> _Payload:
    """Turn a Markdown file into the PDF payload that will be uploaded.

    Parameters
    ----------
    path
        The Markdown file.

    Returns
    -------
    _Payload
        The PDF bytes, the renderer's own page count, and a default name whose suffix is
        the one the payload now has.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The source is blank. This is the guard the prior-art defect actually needs:
        ``weasyprint`` lays out **one** page for empty input, so an empty ``.md`` renders
        to a structurally perfect one-page PDF with nothing on it -- which is precisely the
        "valid, empty PDF" a neighbouring project delivers when its parser returns nothing.
        The page count cannot catch that, so the source is checked instead.
    """
    text = _decoded(path)
    if not text.strip():
        raise UsageError(
            subject=f"{path.name!r}, which carries no Markdown",
            requirement=(
                "a source with content, since blank input still lays out one empty page and "
                "delivering that to a route with no delete is the failure this check exists for"
            ),
        )
    module = importlib.import_module(_MARKDOWN_MODULE)
    converted = module.to_pdf(text, title=path.stem, base_url=str(path.parent))
    return _Payload(
        name=f"{path.stem}{_PDF_SUFFIX}",
        data=converted.data,
        page_count=converted.page_count,
    )


def _payload(invoked: Invoked, plan: _Plan, /) -> _Payload:
    """Materialize the payload and its real page count.

    Parameters
    ----------
    invoked
        The open invocation, for the PDF reader port.
    plan
        What :func:`_plan` decided, including the path it decided about.

    Returns
    -------
    _Payload
        The default name, the bytes, and the page count.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The path is not a readable file, or the payload's page count cannot be established.
    """
    if not plan.source.is_file():
        raise UsageError(
            subject=f"{plan.source!s}, which is not a file this process can read",
            requirement="a path to an existing file",
        )
    if plan.converted:
        return _converted(plan.source)
    data = plan.source.read_bytes()
    count = (
        _archive_page_count(data)
        if plan.media is UploadMedia.RMDOC
        else _pdf_page_count(invoked, plan.source)
    )
    return _Payload(name=plan.source.name, data=data, page_count=count)


def _row(result: CreateDocumentResult, /) -> tuple[str, ...]:
    """Project one creation onto :data:`PUSH_COLUMNS`.

    Parameters
    ----------
    result
        What the use case reported.

    Returns
    -------
    tuple[str, ...]
        One cell per column. ``doc_uuid`` and ``visible_name`` become empty strings when
        the transport or the container kind genuinely could not say -- an empty cell rather
        than the string ``"None"``, which would read as a name.
    """
    return (
        result.doc_uuid or "",
        result.requested_name,
        result.visible_name or "",
        result.media.value,
        str(result.byte_count),
        result.library_refresh.value,
    )


def _receipt(result: CreateDocumentResult, /) -> Table:
    """Rotate the one record into the labelled lines a person reads.

    Parameters
    ----------
    result
        What the use case reported.

    Returns
    -------
    ~rich.table.Table
        A borderless two-column grid, one line per entry of :data:`PUSH_COLUMNS`, carrying the
        cells :func:`_row` built. ``strict=True`` on the zip is the point of using one: the labels
        and the cells are two views of the same record, and a silent truncation of the shorter
        would attribute one fact to another fact's name.

    Notes
    -----
    This goes to **stderr** through :meth:`~rmspec.cli._output.CliOutput.display`. Writing it to
    stdout is the defect this function exists to close: the default invocation must leave the
    machine-consumable stream empty, or ``2>/dev/null`` stops meaning "just the payload".
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right")
    grid.add_column()
    for label, cell in zip(PUSH_COLUMNS, _row(result), strict=True):
        grid.add_row(label, cell or UNREPORTED)
    return grid


def _announce(invoked: Invoked, result: CreateDocumentResult, /) -> None:
    """Put everything a person reads about this creation on stderr, in either non-JSON mode.

    Parameters
    ----------
    invoked
        The open invocation, for its writer.
    result
        What the use case reported.

    Notes
    -----
    Shared by the ``DENSE`` and ``HUMAN`` branches rather than written twice, because the duplicate
    warning is prose about an irreversible act and the two modes differ only in how the *record* is
    shaped. ``report`` is a deliberate no-op in JSON mode, so this is never reached there: a second
    copy on stderr is a duplicate an agent parsing stdout cannot reconcile.
    """
    invoked.report(result.degradations)
    if result.also_named:
        invoked.out.warn(
            f"{len(result.also_named)} document(s) at the library root already carried "
            f"{result.requested_name!r}; this run added another and only a manual "
            f"delete on the tablet removes it"
        )


def push(
    file: Path,
    /,
    *,
    name: _NameOption = None,
    parent: _ParentOption = None,
    allow_duplicate_name: _AllowDuplicateNameFlag = False,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Place one new document on the attached tablet, visible immediately.

    A create cannot be undone from here: this firmware's route table is closed at six
    families and none of them deletes, so a document pushed by mistake costs a manual delete
    on the tablet. Everything checkable is therefore refused before the first byte is sent.

    A ``.md`` is converted to a PDF sized for the tablet's own page and uploaded as one; a
    ``.pdf`` and an ``.rmdoc`` are uploaded as they are. ``--parent`` is handed to the
    transport verbatim and never quietly turned into a placement at the library root: the USB
    import route has no destination parameter, so it refuses and names SSH as the transport
    that can.

    Parameters
    ----------
    file
        The document to place. One of ``.md``, ``.markdown``, ``.pdf`` or ``.rmdoc``.
    name
        What the document should be called, defaulting to the file's own name. For a ``.pdf``
        this becomes the name the tablet shows, verbatim and extension included; for an
        ``.rmdoc`` the archive's own metadata name wins and this is ignored by the firmware.
    parent
        The uuid of a destination folder, or nothing for the library root.
    allow_duplicate_name
        Create the document even though the library root already holds the name. Off by
        default, because the recovery from a duplicate is a manual delete on the tablet.
    json
        Emit one JSON envelope on stdout.
    dense
        Emit tab-separated records on stdout. With neither flag the same record is rotated into
        labelled lines on stderr, so a person reads a receipt rather than one row under six
        headers.

    Returns
    -------
    int
        ``0``.
    """

    def body(invoked: Invoked) -> int:
        plan = _plan(file)
        invoked.probe(*plan.features)
        payload = _payload(invoked, plan)
        result = invoked.get(CreateDocument).create(
            CreateDocumentRequest(
                name=payload.name if name is None else name,
                media=plan.media,
                data=payload.data,
                page_count=payload.page_count,
                occurred_at=datetime.now(UTC),
                parent_uuid=parent,
                allow_duplicate_name=allow_duplicate_name,
            )
        )
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                result.model_dump(mode="json"),
                response_type=RESPONSE_TYPES["push"],
                degradations=result.degradations,
                next_action=_NEXT_ACTION,
            )
        elif invoked.out.mode is OutputMode.DENSE:
            _announce(invoked, result)
            invoked.out.rows(PUSH_COLUMNS, (_row(result),))
        else:
            _announce(invoked, result)
            invoked.out.display(_receipt(result))
        return 0

    return run(body, json=json, dense=dense)
