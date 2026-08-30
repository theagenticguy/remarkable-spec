"""``rmspec diagram`` -- read the Mermaid a human drew on selected pages of one document.

One use case, :class:`~rmspec.app.ExtractDiagrams`, and the thinnest possible adapter over it.
Everything below is either a flag being translated into a request field or a result being
projected into one of the three output modes.

A page with no diagram is a row, never a crash
----------------------------------------------
This is the named legacy defect the command exists to fix. ``diagram_cmd.py:144-149`` omitted
the ``None`` guard its ``ocr`` sibling had, handed a ``None`` path to the parser and raised
``TypeError``, while the ``hash_file(None)`` failure one line earlier was swallowed by a bare
``except Exception`` -- so an unannotated page both crashed the run and silently disabled the
cache for its neighbours. :class:`~rmspec.app.diagrams.DiagramSkipReason` is the replacement,
and it is **data**: every selected page comes back as a row, and a row with no artifact carries
the reason it has none. That includes
:attr:`~rmspec.app.diagrams.DiagramSkipReason.UNREADABLE_VERDICT`, which means the model looked
at the page and answered something this project cannot read -- a successful, paid-for call whose
body does not answer the question, which is a fact about the page rather than a failure of the
command. ``Frontier workstream`` on the attached tablet is a real zero-stroke page; it produces
one row saying ``no_ink`` and exit status 0.

Three facts a caller cannot get from the Mermaid alone
-----------------------------------------------------
Each one is projected in **every** mode, because an agent pasting generated Mermaid somewhere
needs all three before it does:

``check``
    :class:`~rmspec.app.diagrams.MermaidCheck`. This command reports it and never re-stamps it.
    The app layer can only ever say ``unchecked`` or ``not_applicable``, and the layer allowed to
    say ``valid`` or ``invalid`` is one that really runs a Mermaid toolchain -- ``mmdc`` plus
    headless Chromium, an npm binary no Python extra can supply and which this CLI therefore does
    not declare. So ``unchecked`` here means exactly what it says: nobody validated this, and no
    keyword-prefix match is dressing it up as though somebody had.
``skipped``
    Why a row carries no artifact. Empty means it carries one, since every member of the enum has
    a non-empty value.
``served_from_cache``
    Whether the row was paid for. The same reason ``ocr`` reports ``tier_reached``: an agent
    deciding whether to widen a selection needs to know what the last one cost.

``truncated`` is added to the payload by hand, and has to be
-----------------------------------------------------------
:attr:`~rmspec.app.PageDiagram.truncated` is a computed ``@property``, not a field, so
``model_dump(mode="json")`` **omits it** -- pydantic serialises fields. It is the one fact that
says the model's answer was cut off at the output limit, which for this command means the Mermaid
in that row may be half a diagram. Leaving it to a caller to re-derive from ``stop_reason`` would
be asking every consumer of the envelope to import this project's vocabulary in order to learn
that its payload is incomplete. So :func:`_payload` puts it back under
:data:`TRUNCATED_KEY`, page by page, and :data:`DENSE_COLUMNS` carries it as a column.

Three modes, three streams, and one narrower view
------------------------------------------------
``--json`` writes one envelope to stdout, ``--dense`` writes tab-separated records to stdout, and
the default writes a table to **stderr**. That split is the frozen output contract, and it is what
makes ``rmspec diagram doc --json | jq`` clean by construction and
``rmspec diagram doc 2>/dev/null`` correctly silent on a human run. Seven dense columns in an
80-column terminal is seven ellipses, so ``HUMAN`` shows :data:`HUMAN_COLUMNS` instead -- taken
**by index** out of the very row :func:`_cells` already built, through
:data:`HUMAN_COLUMN_INDICES`, so the wide projection and the narrow one cannot drift. What it
drops is the Mermaid itself, because a diagram is many lines and a table cell is one; the caption
says which mode carries it.

The two ``degradations`` are not the same tuple
----------------------------------------------
The envelope's top-level ``degradations`` is everything that happened during this invocation,
document resolution included -- this command concatenates
:attr:`~rmspec.app.ResolveDocumentResult.degradations` with the use case's own before it emits.
``data.degradations`` is only what :class:`~rmspec.app.ExtractDiagramsResult` itself recorded,
because ``data`` is a faithful ``model_dump(mode="json")`` of that result and ``rmspec manifest``
says ``data`` **is** that type. So the top level is a *superset*, never a duplicate:
``ambiguous_auto_resolved`` appears there and not in ``data``, while ``page_not_annotated``
appears in both. That is the point -- one stable place to look for everything the run did, and a
payload that still round-trips back into the model it names. Deleting either one loses a fact.

This command is where the clock is read
--------------------------------------
:attr:`~rmspec.app.ExtractDiagramsRequest.now` is a request field rather than a clock the use case
reads, because the domain requires ``DiagramArtifact.created_at`` and declares no clock port. One
``datetime.now(UTC)`` here is therefore the whole run's instant: every artifact a run produces
shares a timestamp, which is also what lets an app-layer test assert one without freezing
anything.

Why the probe is the first statement of the body
-----------------------------------------------
:meth:`~rmspec.cli._invoke.Invoked.probe` proves ``rmscene``, the raster stack and ``boto3``
before a page is decoded, rendered or sent anywhere. Legacy raised ``ImportError`` from 27
function-local import sites, every one of them reached *after* the user had already paid for a
render or a device round trip, and every one naming a module rather than the extra that ships it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from rich.table import Table

from rmspec.app import ExtractDiagrams, ExtractDiagramsRequest
from rmspec.cli._invoke import (
    FEATURE_MODEL_BEDROCK,
    FEATURE_RASTER,
    FEATURE_SCENE_DECODE,
    DenseFlag,
    Invoked,
    JsonFlag,
    LimitOption,
    MaxPagesOption,
    PagesOption,
    StrictFlag,
    run,
)
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import OutputMode
from rmspec.domain.models import DocumentId

if TYPE_CHECKING:
    from rmspec.app import ExtractDiagramsResult, PageDiagram

__all__ = [
    "COMMAND",
    "DENSE_COLUMNS",
    "HUMAN_CAPTION",
    "HUMAN_COLUMNS",
    "HUMAN_COLUMN_INDICES",
    "TRUNCATED_KEY",
    "diagram",
]

COMMAND: Final = "diagram"
"""The invocation a user types, which is also this command's key in ``RESPONSE_TYPES``.

Spelled once so the discriminator in the envelope and the one in ``rmspec manifest`` are read
from the same table entry and cannot drift apart.
"""

TRUNCATED_KEY: Final = "truncated"
"""Where :attr:`~rmspec.app.PageDiagram.truncated` is written into each page of the payload.

Named rather than inlined because two places must agree on it: :func:`_payload`, which adds the
key, and :data:`DENSE_COLUMNS`, which projects the same fact as a column. See this module's
docstring for why the value has to be added at all.
"""

DENSE_COLUMNS: Final = (
    "page_index",
    "content_kind",
    "check",
    "skipped",
    "cached",
    "truncated",
    "mermaid",
)
"""The columns ``--dense`` projects: the page's identity, then what this command is *for*.

Not every field. ``page_ref`` is dropped because it is an identity for a filename rather than
one a caller addresses a page by, ``diagram_kind`` because it is by construction the first word
of ``mermaid``, and ``stop_reason`` because the one thing a caller must branch on -- the answer
was cut off -- is already ``truncated``. What is left is the diagram plus the four facts that
decide whether it can be trusted, used, or was paid for.
"""

HUMAN_COLUMN_INDICES: Final = (0, 1, 3, 5)
"""Which cells of a :func:`_cells` record the ``HUMAN`` table shows, by position.

Indices rather than a second tuple of names, because a projection built from the same record
cannot disagree with it: rename a dense column and the human header moves with it, reorder
:data:`DENSE_COLUMNS` and this list is the one thing that has to be corrected, in one place.
"""

HUMAN_COLUMNS: Final = tuple(DENSE_COLUMNS[index] for index in HUMAN_COLUMN_INDICES)
"""The ``HUMAN`` headers: ``page_index  content_kind  skipped  truncated``.

The three questions a person at a terminal is actually asking, and nothing else. Which pages
produced Mermaid -- ``content_kind`` is ``diagram`` or ``mixed`` for a page that did and ``text``
for a page the model read as prose, and it is empty exactly when the row carries no artifact at
all. Which pages were skipped and why -- ``skipped``. Whether anything was cut off --
``truncated``, the fact that says the Mermaid in that row may be half a diagram.

``cached`` is dropped because what a run cost is an agent's question when it decides whether to
widen a selection, not a reader's, and ``mermaid`` is dropped because it is many lines and a table
cell is one -- collapsing a flowchart into 30 columns of ellipsis would show a person less than
the empty cell does. Both are one flag away, which is what :data:`HUMAN_CAPTION` says.
"""

HUMAN_CAPTION: Final = "the Mermaid itself is in --dense and --json, which a table cannot carry"
"""What the ``HUMAN`` table says under itself, so the narrowing is stated rather than discovered.

A caption and not a warning: nothing went wrong, and a person who wanted the diagram needs to be
told which mode has it rather than left to infer that an absent column means an absent answer.
"""


_BOOLEAN_CELLS: Final = {True: "true", False: "false"}
"""How a boolean is spelled in a dense cell.

The two spellings sit side by side, and they are JSON's, so a caller moving between ``--json``
and ``--dense`` does not learn a second vocabulary for the same fact. A mapping rather than a
conditional expression at each of the two call sites: there is no branch to cover and no second
place for one of the words to drift.
"""


def _payload(result: ExtractDiagramsResult, /) -> dict[str, Any]:
    """Serialise the result, putting the computed ``truncated`` back on every page.

    Parameters
    ----------
    result
        What the use case answered.

    Returns
    -------
    dict[str, Any]
        ``model_dump(mode="json")``, with :data:`TRUNCATED_KEY` added to each entry of
        ``pages``.

    Notes
    -----
    ``strict=True`` on the zip is the point of using one: the dumped list and the model tuple
    are two views of the same pages, and a silent truncation of the shorter would attach one
    page's ``truncated`` to another page's row -- the same off-by-one this project keeps finding.
    """
    payload = result.model_dump(mode="json")
    payload["pages"] = [
        {**dumped, TRUNCATED_KEY: page.truncated}
        for dumped, page in zip(payload["pages"], result.pages, strict=True)
    ]
    return payload


def _cells(page: PageDiagram, /) -> tuple[str, ...]:
    """Project one page onto :data:`DENSE_COLUMNS`.

    Parameters
    ----------
    page
        One row of the result, artifact-bearing or skipped.

    Returns
    -------
    tuple[str, ...]
        One already-stringified cell per column. An absent artifact leaves ``content_kind`` and
        ``mermaid`` empty, and an unskipped page leaves ``skipped`` empty -- unambiguous either
        way, because every member of both enums has a non-empty value.
    """
    artifact = page.artifact
    return (
        str(page.page_index),
        "" if artifact is None else artifact.content_kind.value,
        page.check.value,
        "" if page.skipped is None else page.skipped.value,
        _BOOLEAN_CELLS[page.served_from_cache],
        _BOOLEAN_CELLS[page.truncated],
        "" if artifact is None or artifact.mermaid is None else artifact.mermaid,
    )


def _table(result: ExtractDiagramsResult, /) -> Table:
    """Build the ``HUMAN`` rendering: the narrow projection of the same records.

    Parameters
    ----------
    result
        What the use case answered.

    Returns
    -------
    ~rich.table.Table
        One row per selected page, each cell taken out of :func:`_cells` by the positions
        :data:`HUMAN_COLUMN_INDICES` names. A document whose every page was skipped is a table of
        skip reasons, and a document with no selected pages is the headers alone -- both of which
        are answers, so neither is special-cased into prose.

    Notes
    -----
    This goes to **stderr** through :meth:`~rmspec.cli._output.CliOutput.display`. Writing it to
    stdout is the defect this function exists to close: the default invocation must leave the
    machine-consumable stream empty, or ``2>/dev/null`` stops meaning "just the payload".
    """
    table = Table(*HUMAN_COLUMNS, caption=HUMAN_CAPTION)
    for page in result.pages:
        cells = _cells(page)
        table.add_row(*(cells[index] for index in HUMAN_COLUMN_INDICES))
    return table


def diagram(
    doc: str,
    /,
    *,
    pages: PagesOption = None,
    limit: LimitOption = None,
    max_pages: MaxPagesOption = None,
    strict: StrictFlag = False,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Extract the Mermaid of any diagram drawn on a document's pages.

    With neither ``--json`` nor ``--dense`` the answer is a table on **stderr** naming each page,
    what the model read it as, why a page was skipped and whether the answer was cut off. The
    Mermaid itself is in the two modes that write to stdout, because a diagram is many lines and a
    table cell is one.

    Parameters
    ----------
    doc
        Which document: a name substring, a full uuid, or a uuid prefix. Several matches are
        ranked and the winner is used, with the choice reported as a degradation, unless
        ``--strict`` was passed.
    pages
        Which pages to examine, as a comma-separated list of 0-based page indices and inclusive
        A-B ranges, as in 0 or 2-5 or 0,3,7-9. Every page when omitted. Mutually exclusive with
        ``--limit``.
    limit
        Examine at most this many leading pages. Mutually exclusive with ``--pages``.
    max_pages
        Override ``RMSPEC_MAX_PAGES`` (64) for this run. The cap is enforced before the first
        render, so one 432-page document cannot quietly become 432 model calls.
    strict
        Refuse an ambiguous selector instead of accepting the ranked winner.
    json
        Emit one envelope on stdout, typed ``diagrams``. Each page carries ``truncated``, which
        the result model computes rather than stores.
    dense
        Emit one tab-separated record per page on stdout instead, with the columns
        ``page_index  content_kind  check  skipped  cached  truncated  mermaid``. Mutually
        exclusive with ``--json``.

    Returns
    -------
    int
        ``0``, including for a document whose every page turned out to hold no diagram: a page
        with nothing on it is a row saying so, never a failure. ``2`` for a contradictory
        command line, and whatever :func:`~rmspec.domain.errors.exit_code` scores any other
        failure.
    """

    def body(invoked: Invoked) -> int:
        invoked.probe(FEATURE_SCENE_DECODE, FEATURE_RASTER, FEATURE_MODEL_BEDROCK)
        resolved = invoked.document(doc, strict=strict)
        result = invoked.get(ExtractDiagrams).extract(
            ExtractDiagramsRequest(
                doc_id=DocumentId(uuid=resolved.chosen.uuid),
                pages=invoked.selection(pages=pages, limit=limit),
                max_pages=invoked.max_pages(max_pages),
                now=datetime.now(UTC),
            )
        )
        degradations = (*resolved.degradations, *result.degradations)
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                _payload(result),
                response_type=RESPONSE_TYPES[COMMAND],
                degradations=degradations,
            )
        elif invoked.out.mode is OutputMode.DENSE:
            invoked.report(degradations)
            invoked.out.rows(DENSE_COLUMNS, (_cells(page) for page in result.pages))
        else:
            invoked.report(degradations)
            invoked.out.display(_table(result))
        return 0

    return run(body, json=json, dense=dense)
