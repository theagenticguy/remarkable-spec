"""``rmspec ls`` -- one enumeration of the library, rendered flat, as a tree, or as records.

Replaces five legacy invocation paths and three separate tree walkers: ``ls``, ``ls --tree``,
``tree``, ``device ls`` and ``device ls --tree``. There is one query --
:class:`~rmspec.app.ListDocuments` -- because a tree is a *rendering* of a hierarchy rather
than a different question, and three renderers over one query is how the legacy paths drifted
until two of them reported page counts the third did not and only one listed folders at all.

``--tree`` is a rendering flag and nothing else
----------------------------------------------
In :attr:`~rmspec.cli._output.OutputMode.JSON` the whole
:class:`~rmspec.app.ListDocumentsResult` goes out **unchanged whether or not ``--tree`` was
passed**, because the result already carries both views over the same values:
``documents`` is flat and complete and ``root_folders`` is the placed hierarchy. A flag that
changed the shape of the payload an agent parses would mean an agent had to know which flags
a human happened to type before it could pick a parser, which is the opposite of what the
``type`` discriminator is for. ``--dense`` is flag-invariant for the same reason: one record
per entry, whichever view a human asked for. ``--tree`` therefore changes exactly one thing,
the indented listing a person reads on stderr.

``PATH`` is filtered here, and a miss is a refusal rather than an empty listing
-----------------------------------------------------------------------------
:class:`~rmspec.app.ListDocumentsRequest` has no path parameter -- the use case enumerates the
library and places it, and narrowing that placement to one subtree is a presentation decision
the same way ``--tree`` is. So :func:`_selected` walks ``root_folders`` here. A ``PATH`` that
names no folder raises :class:`~rmspec.domain.errors.UsageError` **naming the segment it
looked for and where**, because an empty success is indistinguishable from an empty folder and
the whole point of asking is to learn which one you have.

Orphans are surfaced, never dropped
-----------------------------------
``unrooted_folders`` and ``unrooted_documents`` hold entries whose parent the listing does not
contain, or whose ancestor chain cycles. An entry the tablet has orphaned is exactly what
someone is looking for when a document "vanished", so every mode reports it: the JSON envelope
carries both tuples verbatim, ``--dense`` marks each record with an ``unrooted`` column, and a
human run names them on stderr underneath the table or tree.

``--source`` overrides ``RMSPEC_TRANSPORT`` by setting it
--------------------------------------------------------
:func:`~rmspec.cli._invoke.run` loads the settings and composes the container itself, which is
what makes every command's failure path identical -- and it means a per-command transport
override has to reach ``load_settings`` the one way settings are read at all. So
:func:`_select_source` assigns :data:`TRANSPORT_VARIABLE` in the process environment before
``run`` is called. That is the same environment ``apply_native_library_path`` already writes to
at the top of every command, and it keeps the promise ``RMSPEC_*`` exists for: a child process
that runs ``rmspec env`` inside this one reports the transport this run actually used.

``mirror`` is a configured source that nothing implements yet, and it says so
---------------------------------------------------------------------------
``--source mirror`` selects :attr:`~rmspec.cli._settings.Transport.MIRROR`, whose
``DeviceCatalog`` binding does not exist: the container refuses with
:class:`~rmspec.domain.errors.DeviceOperationUnsupported`, naming the two transports that do
serve a listing and carrying "retry with ..." as its remediation. That refusal is the feature.
It arrives through the ordinary error boundary with the ordinary envelope and exit status, and
it is why this module does not fall back to USB: a silent fallback would make
``--source mirror`` look like it worked while every read still went to the tablet.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from rich.table import Table

from rmspec import app
from rmspec.cli._invoke import DenseFlag, Invoked, JsonFlag, run
from rmspec.cli._manifest import RESPONSE_TYPES, SETTING_PREFIX
from rmspec.cli._output import OutputMode
from rmspec.cli._settings import Transport
from rmspec.domain import errors

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from rmspec.cli._output import CliOutput
    from rmspec.domain.ports.device import DeviceDocument, DeviceFolder

__all__ = [
    "DENSE_COLUMNS",
    "DOCUMENT_KIND",
    "FOLDER_KIND",
    "LS_RESPONSE_TYPE",
    "PATH_SEPARATOR",
    "TRANSPORT_VARIABLE",
    "IncludeTrashedFlag",
    "Source",
    "SourceOption",
    "TreeFlag",
    "ls",
]

LS_RESPONSE_TYPE: Final = RESPONSE_TYPES["ls"]
"""This command's ``type`` discriminator, taken from the table the manifest publishes.

Read out of :data:`~rmspec.cli._manifest.RESPONSE_TYPES` rather than retyped, so the envelope
this command emits and the ``response_type`` ``rmspec manifest`` advertises for it cannot
disagree.
"""

PATH_SEPARATOR: Final = "/"
"""What separates one folder name from the next inside a ``PATH`` argument.

The separator a user already types for a nested folder, and the one the tree rendering appends
to a folder's name, so ``rmspec ls --tree`` shows the path that ``rmspec ls`` accepts.
"""

TREE_INDENT: Final = "  "
"""One level of indentation in the human tree. Two spaces, because depth 2 is real."""

DOCUMENT_KIND: Final = "document"
"""The ``kind`` cell of a ``--dense`` record describing a document."""

FOLDER_KIND: Final = "folder"
"""The ``kind`` cell of a ``--dense`` record describing a folder."""

DENSE_COLUMNS: Final = ("kind", "uuid", "name", "page_count", "parent_uuid", "unrooted")
"""The six cells of a ``--dense`` record: the identity, plus what a listing is *for*.

Each earns its place and nothing else is projected, following §1.5's rule that a dense
projection is the identity plus the command's purpose rather than every field:

``kind``
    Whether the record is a document or a folder, so one homogeneous record stream can carry
    the whole library and ``wc -l`` counts it. A folder has no ``page_count`` and its cell is
    empty, which is the honest projection of a field the domain does not give folders.
``uuid``
    The identity every other command takes as its ``DOC`` argument.
``name``
    What a person searched for and what ``rmspec read`` matches a substring against.
``page_count``
    What decides whether ``--max-pages`` will bite, and the first key
    :class:`~rmspec.app.ResolveDocument` ranks ambiguous matches by. Empty when the device
    reported none.
``parent_uuid``
    Where the entry sits. Without it two documents of the same name in different folders are
    indistinguishable in a flat stream. Empty at the library root.
``unrooted``
    ``true`` when this entry's parent is missing from the listing or its ancestor chain
    cycles. The one fact a flat listing otherwise destroys, and the one a caller wants when a
    document has "vanished".
"""

TRUE_CELL: Final = "true"
"""The ``unrooted`` cell of an entry the hierarchy could not place."""

FALSE_CELL: Final = "false"
"""The ``unrooted`` cell of an entry it could."""

ABSENT_CELL: Final = ""
"""What a ``--dense`` cell holds when the device reported no value for that field.

Empty rather than ``-`` or ``null``: a dense stream is read with ``cut`` and ``awk``, where an
empty field tests false and a placeholder does not, so a sentinel would have to be un-learned
by every consumer.
"""

_TRANSPORT_FIELD: Final = "transport"
"""The :class:`~rmspec.cli._settings.CliSettings` field ``--source`` overrides."""

TRANSPORT_VARIABLE: Final = f"{SETTING_PREFIX}{_TRANSPORT_FIELD.upper()}"
"""``RMSPEC_TRANSPORT``, assembled from the prefix and the field rather than retyped.

Built from :data:`~rmspec.cli._manifest.SETTING_PREFIX` -- which a manifest test already pins
equal to ``CliSettings.model_config["env_prefix"]`` -- and the field name, so this command
cannot advertise an override through a variable the settings model does not read.
"""


class Source(StrEnum):
    """Where ``rmspec ls`` reads the library from, as a user spells it.

    Two members rather than three, deliberately: this flag names a *source* while
    :class:`~rmspec.cli._settings.Transport` names a *transport*, and the tablet is one source
    reachable two ways. A run that must reach the tablet over SSH sets ``RMSPEC_TRANSPORT=ssh``
    and leaves this flag off, which is also why the flag defaults to ``None`` rather than to
    ``device``: an absent flag changes nothing, so a configured transport survives.
    """

    DEVICE = "device"
    """The tablet itself, over USB, which is the default read path."""

    MIRROR = "mirror"
    """A local copy of the document tree. Configurable, and not implemented yet."""


_SOURCE_TRANSPORTS: Final = {Source.DEVICE: Transport.USB, Source.MIRROR: Transport.MIRROR}
"""The one mapping from the source a user names to the transport that serves it.

``device`` resolves to USB and not to "whatever was configured", because resolving it against
the environment would mean reading the settings before :func:`~rmspec.cli._invoke.run`'s error
boundary exists -- and a malformed ``RMSPEC_*`` would then leave as a traceback instead of an
``InvalidSettingError`` envelope.
"""

TreeFlag = Annotated[bool, Parameter(name="--tree", negative="")]
"""``--tree``: indent the hierarchy for a person. Changes no machine-readable payload.

``negative=""`` for the reason :data:`~rmspec.cli._invoke.JsonFlag` gives: cyclopts would
otherwise generate a ``--no-tree`` that means the default and that nobody documented.
"""

SourceOption = Annotated[Source | None, Parameter(name="--source")]
"""``--source``: which source to read, overriding ``RMSPEC_TRANSPORT`` for this run only."""

IncludeTrashedFlag = Annotated[bool, Parameter(name="--include-trashed", negative="")]
"""``--include-trashed``: report what the user deleted on the tablet, as well as the library."""


def _select_source(source: Source | None, /) -> None:
    """Point this run's transport at the source the flag named, if it named one.

    Parameters
    ----------
    source
        The ``--source`` value, or ``None`` to leave ``RMSPEC_TRANSPORT`` exactly as the
        environment set it -- including unset, which means USB.

    Notes
    -----
    Writes :data:`TRANSPORT_VARIABLE` into ``os.environ`` because
    :func:`~rmspec.cli._invoke.run` owns ``load_settings`` and the container, so the
    environment is the only seam a per-command override has. See this module's docstring.
    """
    if source is not None:
        os.environ[TRANSPORT_VARIABLE] = _SOURCE_TRANSPORTS[source].value


def _walk(nodes: Sequence[app.CatalogFolder], /) -> Iterator[app.CatalogFolder]:
    """Yield every node of a placed forest, depth first, each node before its children.

    Parameters
    ----------
    nodes
        The folders at one level, each carrying its own subtree.

    Yields
    ------
    ~rmspec.app.CatalogFolder
        Every node reachable from *nodes*, in transport order at each level.

    Notes
    -----
    Needs no visited set, and that is the use case's guarantee rather than an assumption:
    everything reachable from ``root_folders`` is rooted by construction, and a cycle is
    unreachable from any root -- which is the same fact ``unrooted_folders`` reports.
    """
    for node in nodes:
        yield node
        yield from _walk(node.folders)


def _segments(path: str, /) -> tuple[str, ...]:
    """Split a ``PATH`` argument into the folder names it walks.

    Parameters
    ----------
    path
        The argument as the user typed it.

    Returns
    -------
    tuple[str, ...]
        One name per non-empty segment, whitespace stripped. An empty segment is dropped, so
        a leading, trailing or doubled separator is tolerated rather than refused -- the same
        choice ``--pages`` makes about a trailing comma.
    """
    return tuple(part for part in (raw.strip() for raw in path.split(PATH_SEPARATOR)) if part)


def _descend(
    nodes: Sequence[app.CatalogFolder],
    segments: Sequence[str],
    /,
    *,
    path: str,
    walked: tuple[str, ...] = (),
) -> app.CatalogFolder:
    """Follow a ``PATH``'s segments down a placed forest to exactly one folder.

    Parameters
    ----------
    nodes
        The folders at this level.
    segments
        The remaining names to match, at least one.
    path
        The whole argument, so a refusal quotes what the user wrote rather than the fragment
        this call happens to be looking at.
    walked
        The names already matched, for a refusal that says *where* it looked.

    Returns
    -------
    ~rmspec.app.CatalogFolder
        The folder the last segment named, carrying its own subtree.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        A segment names no folder at that level. Refused rather than answered with an empty
        listing, which would be indistinguishable from an empty folder.

    Notes
    -----
    Matching is a case-insensitive **exact** name comparison per segment, not a substring
    test. ``rmspec read`` matches substrings because it ranks its candidates and reports the
    others; a path has no ranking and no candidates, so a substring match here would silently
    pick one of two sibling folders whose names share a prefix.
    """
    head, rest = segments[0], segments[1:]
    needle = head.casefold()
    node = next((entry for entry in nodes if entry.folder.name.casefold() == needle), None)
    if node is None:
        where = f"inside {PATH_SEPARATOR.join(walked)!r}" if walked else "at the library root"
        raise errors.UsageError(
            subject=f"the folder path {path!r}",
            requirement=f"a folder named {head!r} {where}",
        )
    if not rest:
        return node
    return _descend(node.folders, rest, path=path, walked=(*walked, node.folder.name))


def _scoped(
    result: app.ListDocumentsResult,
    node: app.CatalogFolder,
    /,
) -> app.ListDocumentsResult:
    """Narrow a whole-library result to one folder's subtree.

    Parameters
    ----------
    result
        The enumeration, for the degradations it recorded.
    node
        The folder the ``PATH`` named.

    Returns
    -------
    ~rmspec.app.ListDocumentsResult
        The same shape, scoped: ``documents`` is every document in the subtree,
        ``root_documents`` the ones directly in *node*, and ``root_folders`` is *node* itself
        -- so ``--tree`` prints the named folder as the root of the tree it was asked for.

    Notes
    -----
    ``unrooted_folders`` and ``unrooted_documents`` are empty, and that is not a drop: an
    entry the hierarchy could not place is by definition not inside a named subtree, and every
    entry reachable from *node* is rooted. A caller that wants the orphans asks for the
    unfiltered listing.

    ``degradations`` is carried over untouched. A degradation records an entry the *transport*
    could not represent, which is a fact about the run rather than about the subtree, so
    filtering it out here would be exactly the silent substitution this CLI exists to stop.
    """
    return app.ListDocumentsResult(
        documents=tuple(doc for folder in _walk((node,)) for doc in folder.documents),
        root_documents=node.documents,
        root_folders=(node,),
        unrooted_folders=(),
        unrooted_documents=(),
        degradations=result.degradations,
    )


def _selected(result: app.ListDocumentsResult, path: str | None, /) -> app.ListDocumentsResult:
    """Apply the ``PATH`` filter, if there was one.

    Parameters
    ----------
    result
        The whole enumeration.
    path
        The ``PATH`` argument, or ``None``.

    Returns
    -------
    ~rmspec.app.ListDocumentsResult
        *result* unchanged, or the subtree *path* named.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        *path* names no folder, or names nothing at all -- ``rmspec ls /`` asked for a subtree
        and identified none, which is a typo rather than a request for the whole library.
    """
    if path is None:
        return result
    segments = _segments(path)
    if not segments:
        raise errors.UsageError(
            subject=f"the folder path {path!r}",
            requirement=f"at least one folder name, separated by {PATH_SEPARATOR!r}",
        )
    return _scoped(result, _descend(result.root_folders, segments, path=path))


def _pages(document: DeviceDocument, /) -> str:
    """Render a document's page count as a cell.

    Parameters
    ----------
    document
        The document.

    Returns
    -------
    str
        The count, or :data:`ABSENT_CELL` when the device reported none. ``page_count`` is
        genuinely optional on the port -- a transport that does not report it says ``None``
        rather than guessing -- so the cell says nothing rather than saying zero.
    """
    return ABSENT_CELL if document.page_count is None else str(document.page_count)


def _unrooted(uuid: str, orphans: frozenset[str], /) -> str:
    r"""Render one entry's placement as a greppable cell.

    Parameters
    ----------
    uuid
        The entry's identifier.
    orphans
        The identifiers the hierarchy could not place.

    Returns
    -------
    str
        ``"true"`` or ``"false"``, always one of the two, so ``awk -F'\t' '$6=="true"'``
        works and an absent value is never confused with a false one.
    """
    return TRUE_CELL if uuid in orphans else FALSE_CELL


def _folders(result: app.ListDocumentsResult, /) -> tuple[DeviceFolder, ...]:
    """List every folder the enumeration represented, placed ones first.

    Parameters
    ----------
    result
        The enumeration.

    Returns
    -------
    tuple[~rmspec.domain.ports.device.DeviceFolder, ...]
        Every rooted folder in depth-first order, then every unrooted one in transport order.
        The result has no flat folder tuple of its own -- ``root_folders`` is a hierarchy and
        ``unrooted_folders`` is what it could not place -- so the flat view is assembled here,
        which is the layer that wanted it.
    """
    return (*(node.folder for node in _walk(result.root_folders)), *result.unrooted_folders)


def _dense_rows(result: app.ListDocumentsResult, /) -> Iterator[tuple[str, ...]]:
    """Project one record per entry, in :data:`DENSE_COLUMNS` order.

    Parameters
    ----------
    result
        The enumeration, already filtered by ``PATH``.

    Yields
    ------
    tuple[str, ...]
        Every document first, in transport order, then every folder. Documents lead because
        they are what a caller acts on next; folders follow so that an orphaned *empty* folder
        is still in the stream, which is the one entry no document record would reveal.
    """
    orphaned_documents = frozenset(document.uuid for document in result.unrooted_documents)
    for document in result.documents:
        yield (
            DOCUMENT_KIND,
            document.uuid,
            document.name,
            _pages(document),
            document.parent_uuid or ABSENT_CELL,
            _unrooted(document.uuid, orphaned_documents),
        )
    orphaned_folders = frozenset(folder.uuid for folder in result.unrooted_folders)
    for folder in _folders(result):
        yield (
            FOLDER_KIND,
            folder.uuid,
            folder.name,
            ABSENT_CELL,
            folder.parent_uuid or ABSENT_CELL,
            _unrooted(folder.uuid, orphaned_folders),
        )


def _document_line(document: DeviceDocument, /, *, depth: int) -> str:
    """Render one document as a line of the human tree.

    Parameters
    ----------
    document
        The document.
    depth
        How many levels of folder contain it.

    Returns
    -------
    str
        The indented name, its page count when the device reported one, and its identifier --
        which is there so that a person reading the tree can paste it into the next command.
    """
    pages = ABSENT_CELL if document.page_count is None else f" ({document.page_count}p)"
    return f"{TREE_INDENT * depth}{document.name}{pages}  {document.uuid}"


def _folder_lines(nodes: Sequence[app.CatalogFolder], /, *, depth: int) -> Iterator[str]:
    """Render a level of the hierarchy and everything under it.

    Parameters
    ----------
    nodes
        The folders at this level.
    depth
        How many levels contain them.

    Yields
    ------
    str
        One line per folder, then its documents, then its subfolders. A folder's name carries
        :data:`PATH_SEPARATOR` so the tree shows the paths ``rmspec ls PATH`` accepts.
    """
    for node in nodes:
        yield f"{TREE_INDENT * depth}{node.folder.name}{PATH_SEPARATOR}"
        for document in node.documents:
            yield _document_line(document, depth=depth + 1)
        yield from _folder_lines(node.folders, depth=depth + 1)


def _tree_lines(result: app.ListDocumentsResult, /) -> Iterator[str]:
    """Render the whole placed hierarchy for a person.

    Parameters
    ----------
    result
        The enumeration, already filtered by ``PATH``.

    Yields
    ------
    str
        The root documents, then each root folder's subtree. Orphans are not here; they are
        named separately by :func:`_report_unrooted`, because a tree cannot show an entry it
        has no place for and silently omitting one is the failure this whole result shape
        exists to avoid.
    """
    for document in result.root_documents:
        yield _document_line(document, depth=0)
    yield from _folder_lines(result.root_folders, depth=0)


def _table(result: app.ListDocumentsResult, /) -> Table:
    """Build the flat human listing.

    Parameters
    ----------
    result
        The enumeration, already filtered by ``PATH``.

    Returns
    -------
    rich.table.Table
        Every document the enumeration represented, wherever in the tree it sits, with the
        same four facts ``--dense`` projects minus the ``kind`` a document-only table does
        not need.
    """
    table = Table(title="documents")
    table.add_column("uuid")
    table.add_column("name")
    table.add_column("pages", justify="right")
    table.add_column("parent")
    for document in result.documents:
        table.add_row(
            document.uuid,
            document.name,
            _pages(document),
            document.parent_uuid or ABSENT_CELL,
        )
    return table


def _report_unrooted(out: CliOutput, result: app.ListDocumentsResult, /) -> None:
    """Name every entry the hierarchy could not place, on stderr.

    Parameters
    ----------
    out
        This invocation's writer.
    result
        The enumeration.

    Notes
    -----
    A warning rather than a table row, because an orphan is a fact about the tablet's state
    and not about the listing: either a parent folder is missing from the enumeration or two
    folders name each other. Only the human rendering needs this -- ``--json`` carries both
    tuples in the payload and ``--dense`` marks each record's ``unrooted`` cell -- so it is
    called on the human path alone rather than duplicating a fact a machine reader already has.
    """
    for folder in result.unrooted_folders:
        out.warn(f"unrooted folder: {folder.name} ({folder.uuid})")
    for document in result.unrooted_documents:
        out.warn(f"unrooted document: {document.name} ({document.uuid})")


def ls(
    path: str | None = None,
    /,
    *,
    tree: TreeFlag = False,
    source: SourceOption = None,
    include_trashed: IncludeTrashedFlag = False,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """List the documents and folders the tablet holds.

    Parameters
    ----------
    path
        Limit the listing to one folder's subtree, as a ``/``-separated path from the library
        root; each segment is matched against a folder name case-insensitively and exactly.
        A path naming no folder is refused rather than answered with an empty listing, since
        an empty answer cannot be told apart from an empty folder.
    tree
        Indent the hierarchy for a person instead of listing documents flat. A **rendering**
        flag: ``--json`` emits the same whole result either way, because it already carries
        both views, and ``--dense`` emits the same records -- so no agent has to know which
        flags a human typed before it can pick a parser.
    source
        Read the tablet (``device``, over USB) or a local copy of the document tree
        (``mirror``), overriding ``RMSPEC_TRANSPORT`` for this run only. Leave it off to keep
        the configured transport, which is the only way to read the tablet over SSH.
        ``mirror`` has no implementation yet and refuses with the transports that do serve a
        listing rather than quietly reading the tablet instead.
    include_trashed
        Report entries the user deleted on the tablet as well as the library. Trashed entries
        appear at the root because the firmware overwrites a deleted entry's parent, so its
        original folder is not recoverable. A no-op over USB, whose listings never contain a
        trashed entry at all.
    json
        Emit one JSON envelope on stdout: the whole ``catalog`` result, its degradations
        hoisted to the top level, and nothing on stdout that is not part of it.
    dense
        Emit tab-separated ``kind  uuid  name  page_count  parent_uuid  unrooted`` records on
        stdout, one per entry, header first. Mutually exclusive with ``--json``.

    Returns
    -------
    int
        ``0``, or the exit status of the failure that ended the run -- ``2`` for a ``PATH``
        that names no folder or for two output modes at once, and whatever
        :func:`~rmspec.domain.errors.exit_code` gives a transport failure.
    """
    _select_source(source)

    def body(invoked: Invoked) -> int:
        result = _selected(
            invoked.get(app.ListDocuments).list_documents(
                app.ListDocumentsRequest(include_trashed=include_trashed)
            ),
            path,
        )
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                result.model_dump(mode="json"),
                response_type=LS_RESPONSE_TYPE,
                degradations=result.degradations,
            )
            return 0
        invoked.report(result.degradations)
        if invoked.out.mode is OutputMode.DENSE:
            invoked.out.rows(DENSE_COLUMNS, _dense_rows(result))
            return 0
        if tree:
            invoked.out.display("\n".join(_tree_lines(result)))
        else:
            invoked.out.display(_table(result))
        _report_unrooted(invoked.out, result)
        return 0

    return run(body, json=json, dense=dense)
