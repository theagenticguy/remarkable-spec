"""``rmspec read`` -- resolve one selector to one document and report what the catalog knows.

Replaces legacy ``inspect``, and the rename is the point: this command reads a **document**,
not a file path. Its argument is the same selector every other document-taking command
accepts -- a name substring, a full uuid, or a uuid prefix -- so ``rmspec read notes`` and
``rmspec ocr notes`` cannot disagree about which document "notes" is. A command that took a
path would have made the tablet's identifiers a detail of the host filesystem, which is
precisely what made the legacy tree unable to talk about a document at all until it had
already copied one.

The ambiguity policy is not re-decided here
-------------------------------------------
:class:`~rmspec.app.ResolveDocument` never raises
:class:`~rmspec.domain.errors.AmbiguousDocument`: it ranks the matches by page count then last
modified, both descending, returns the winner, and records
``DegradationKind.AMBIGUOUS_AUTO_RESOLVED``. :meth:`~rmspec.cli._invoke.Invoked.document`
owns what to do about that -- the default accepts the ranked winner and surfaces the
degradation, and ``--strict`` raises ``AmbiguousDocument`` carrying ``also_matched``. This
module calls that one helper and adds no branch of its own, which is why ``read --strict`` and
``ocr --strict`` cannot drift.

Why ``also_matched`` is rendered even on the successful path
----------------------------------------------------------
A result that can hide a second match is how the wrong document gets rendered silently. So
every mode reports the other matches: the JSON envelope carries ``also_matched`` inside
``data`` and the degradation at the top level, ``--dense`` gives each candidate its own record
with ``chosen=false``, and a human run lists them under the table. A caller therefore learns
that its query was ambiguous *without* having to opt into ``--strict`` first, which is the
whole reason the use case reports the choice twice.

The next command is named literally, not described
-------------------------------------------------
``read`` exists to identify a document you are about to act on, so the envelope carries a
``next`` whose ``command`` is the literal shell line to run -- ``rmspec ocr`` for handwriting,
``rmspec annotations`` for a document with an underlay. A description of a next step is a
sentence an agent has to translate; a command is a command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from rmspec.cli._invoke import DenseFlag, Invoked, JsonFlag, StrictFlag, run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import NextAction, OutputMode
from rmspec.domain.ports.device import DeviceFileType

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rmspec import app
    from rmspec.cli._output import CliOutput
    from rmspec.domain.ports.device import DeviceDocument

__all__ = [
    "ABSENT_CELL",
    "CHOSEN_CELL",
    "DENSE_COLUMNS",
    "OTHER_CELL",
    "READ_RESPONSE_TYPE",
    "read",
]

READ_RESPONSE_TYPE: Final = RESPONSE_TYPES["read"]
"""This command's ``type`` discriminator, taken from the table the manifest publishes.

``"document"`` rather than ``"read"``: §1.4 names every discriminator for the payload rather
than for the verb, because two commands may one day answer the same shape and an agent
branches on the shape.
"""

DENSE_COLUMNS: Final = (
    "chosen",
    "uuid",
    "name",
    "file_type",
    "page_count",
    "parent_uuid",
    "last_modified",
)
"""The seven cells of a ``--dense`` record: one identity plus everything the catalog knows.

``chosen`` comes first so that ``awk -F'\\t' '$1=="true"'`` isolates the resolved document and
the rest of the stream is the ambiguity the query carried. The remaining six are exactly the
fields :class:`~rmspec.domain.ports.device.DeviceDocument` holds, minus ``trashed``: the
resolver skips the trash, so every document it can return has ``trashed=False`` and a column
that is constant is a column that says nothing.

A candidate record fills only ``chosen``, ``uuid`` and ``name``, because
:class:`~rmspec.domain.errors.DocumentCandidate` holds only the latter two. Leaving the rest
empty is the honest projection; re-querying the catalog once per candidate to fill them would
turn one listing into N and would still be reporting facts the result never claimed.
"""

CHOSEN_CELL: Final = "true"
"""The ``chosen`` cell of the record describing the resolved document."""

OTHER_CELL: Final = "false"
"""The ``chosen`` cell of a record describing a document the query also matched."""

ABSENT_CELL: Final = ""
"""What a cell holds for a field the device reported no value for, or a candidate lacks.

Empty rather than ``-`` or ``null``, for the reason a dense stream exists at all: it is read
with ``cut`` and ``awk``, where an empty field tests false and a placeholder has to be
un-learned by every consumer.
"""

_NEXT_ACTIONS: Final = {
    DeviceFileType.NOTEBOOK: ("ocr", "read this document's handwriting as text"),
    DeviceFileType.PDF: ("annotations", "read this document's annotations over its underlay"),
    DeviceFileType.EPUB: ("annotations", "read this document's annotations over its underlay"),
}
"""The obvious next command per file type, as a verb and the purpose of running it.

Keyed on :class:`~rmspec.domain.ports.device.DeviceFileType`, which is closed, so a new member
would fail this lookup loudly rather than silently suggesting the wrong command. Handwriting
goes to ``ocr`` and a document with an underlay goes to ``annotations``, which is the whole
distinction the port made ``file_type`` a real field for: a caller deciding which of the two
to run must not have to fetch a bundle to find out.
"""


def _next_action(document: DeviceDocument, /) -> NextAction:
    """Name the literal command to run against this document next.

    Parameters
    ----------
    document
        The resolved document.

    Returns
    -------
    ~rmspec.cli._output.NextAction
        The shell line, ready to execute, and what running it accomplishes. ``--json`` is part
        of the line because the caller most likely to read a ``next`` is the one that asked for
        the envelope this ``next`` arrived in.
    """
    verb, purpose = _NEXT_ACTIONS[document.file_type]
    return NextAction(command=f"rmspec {verb} {document.uuid} --json", purpose=purpose)


def _pages(document: DeviceDocument, /) -> str:
    """Render the document's page count as a cell.

    Parameters
    ----------
    document
        The resolved document.

    Returns
    -------
    str
        The count, or :data:`ABSENT_CELL` when the device reported none. ``page_count`` is
        genuinely optional on the port, so the cell says nothing rather than saying zero --
        and a caller reading it to decide whether ``--max-pages`` will bite needs that
        difference.
    """
    return ABSENT_CELL if document.page_count is None else str(document.page_count)


def _moment(document: DeviceDocument, /) -> str:
    """Render the document's modification instant as a cell.

    Parameters
    ----------
    document
        The resolved document.

    Returns
    -------
    str
        ISO 8601, timezone-aware and normalised to UTC by the port, or :data:`ABSENT_CELL`
        when the transport reported no instant.
    """
    return ABSENT_CELL if document.last_modified is None else document.last_modified.isoformat()


def _dense_rows(result: app.ResolveDocumentResult, /) -> Iterator[tuple[str, ...]]:
    """Project the resolution as records, in :data:`DENSE_COLUMNS` order.

    Parameters
    ----------
    result
        The resolution.

    Yields
    ------
    tuple[str, ...]
        The chosen document first, with every cell filled, then one record per document the
        query also matched, in the same ranked order the use case returned them in.
    """
    chosen = result.chosen
    yield (
        CHOSEN_CELL,
        chosen.uuid,
        chosen.name,
        chosen.file_type.value,
        _pages(chosen),
        chosen.parent_uuid or ABSENT_CELL,
        _moment(chosen),
    )
    for candidate in result.also_matched:
        yield (
            OTHER_CELL,
            candidate.uuid,
            candidate.name,
            ABSENT_CELL,
            ABSENT_CELL,
            ABSENT_CELL,
            ABSENT_CELL,
        )


def _report(out: CliOutput, result: app.ResolveDocumentResult, /) -> None:
    """Show the resolution to a person on stderr.

    Parameters
    ----------
    out
        This invocation's writer.
    result
        The resolution.

    Notes
    -----
    One ``field: value`` line per fact rather than a table, because a table of one row is
    wider than the terminal and reads worse than the seven lines it replaces. The candidates
    are indented under an ``also matched`` heading, which is the same shape
    :meth:`~rmspec.cli._output.CliOutput.fail` already prints them in for
    ``AmbiguousDocument`` -- so ``read`` and ``read --strict`` show the same list whether the
    ambiguity was accepted or refused.
    """
    chosen = result.chosen
    values = (
        ("uuid", chosen.uuid),
        ("name", chosen.name),
        ("file_type", chosen.file_type.value),
        ("page_count", _pages(chosen)),
        ("parent_uuid", chosen.parent_uuid or ABSENT_CELL),
        ("last_modified", _moment(chosen)),
    )
    out.display("\n".join(f"{field}: {value}" for field, value in values))
    if result.also_matched:
        out.display("also matched:")
        for candidate in result.also_matched:
            out.display(f"  {candidate.uuid}  {candidate.name}")
    action = _next_action(chosen)
    out.display(f"next: {action.command}")


def read(
    doc: str,
    /,
    *,
    strict: StrictFlag = False,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Resolve one document selector and report what the catalog knows about it.

    Parameters
    ----------
    doc
        Which document: a case-insensitive substring of its name, its full uuid, or a uuid
        prefix of eight or more characters. The stages are tried in that order and the first
        that matches anything wins. A selector matching only a document in the trash is not
        found, because the trash is not the library.
    strict
        Refuse an ambiguous selector instead of accepting the ranked winner. The default
        accepts it and reports ``ambiguous_auto_resolved`` plus every other match, so a caller
        always learns that a choice was made for it; ``--strict`` turns the same situation into
        an ``AmbiguousDocument`` failure carrying the candidates.
    json
        Emit one JSON envelope on stdout: the ``document`` result, its degradations hoisted to
        the top level, and a ``next`` naming the literal command to run against this document.
    dense
        Emit tab-separated ``chosen  uuid  name  file_type  page_count  parent_uuid
        last_modified`` records on stdout, the resolved document first and then one per other
        match, header first. Mutually exclusive with ``--json``.

    Returns
    -------
    int
        ``0``, or the exit status of the failure that ended the run -- ``2`` for a blank
        selector, an ambiguous one under ``--strict``, or two output modes at once, ``66`` when
        nothing matched a complete listing, and ``69`` when nothing matched an *incomplete*
        one, since then whether the document exists is unknown rather than false.
    """

    def body(invoked: Invoked) -> int:
        result = invoked.document(doc, strict=strict)
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                result.model_dump(mode="json"),
                response_type=READ_RESPONSE_TYPE,
                degradations=result.degradations,
                next_action=_next_action(result.chosen),
            )
            return 0
        invoked.report(result.degradations)
        if invoked.out.mode is OutputMode.DENSE:
            invoked.out.rows(DENSE_COLUMNS, _dense_rows(result))
            return 0
        _report(invoked.out, result)
        return 0

    return run(body, json=json, dense=dense)
