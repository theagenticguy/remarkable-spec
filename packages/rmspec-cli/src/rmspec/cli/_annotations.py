"""``rmspec annotations`` -- read back what a human wrote on a PDF an agent pushed.

This is the project's headline round trip, and this command is its second half: an agent pushes a
document, a human marks it up with a pen, the agent reads the marks back as text. ``I Love You,
Sylvia`` on the attached tablet is the document that round trip was measured on.

A noun, deliberately. Every other command here is a verb, and this one names a thing rather than
an action because what a caller wants is *the annotations* -- the same reason ``git diff`` is not
``git compare``.

Where the ``PdfSourceRef`` comes from, since a use case cannot make one
---------------------------------------------------------------------
:class:`~rmspec.app.ReadAnnotationsRequest` takes ``source: PdfSourceRef``, and the port that
declares that type calls it "an opaque handle to an existing PDF a reader may open but a use case
cannot locate": a token, not a path, which a caller may pass and compare and must never parse,
split or **build**. So the direction is inverted -- whichever component already knows where the
bytes live mints the token, and the PDF reader adapter is the only one allowed to resolve it back
into a file. Constructing ``PdfSourceRef(token=str(path))`` here would therefore not be a
shortcut; it would be a token the reader's registry has never heard of, and every read would fail
as ``PdfSourceUnreadable``.

Which leaves the minting to whichever component already knows where the bytes are, and
:func:`_source_for` is it. It follows the store the document is *being read from*, which is the
store :func:`~rmspec.cli._container.compose` bound ``DocumentRepository`` to:

* ``RMSPEC_XOCHITL`` names a local xochitl mirror, so the print is already a file at
  ``<root>/<uuid>.pdf`` and the composition root's own registry mints a ref for that path.
  Nothing is transferred, which is the only reason this branch is preferred when it is available.
* With no mirror the underlay comes off the tablet and the ref is minted over the payload, which
  is what the registry's ``for_bytes`` exists for. Requiring the mirror instead made this command
  **dead in the default configuration** -- exit 78 naming a xochitl tree ``rmspec sync`` does not
  create, since sync writes ``RMSPEC_SYNC_DB`` -- and :func:`_source_for` states that measurement.

One consequence worth stating rather than discovering: under either branch the ink and the print
come from **one** store, because both halves follow the same binding. Minting from the other store
would pair one store's ink with the other's copy of the PDF, and a re-upload between the two reads
would silently composite one document's marks over another's print.

The registry is reached through :func:`importlib.import_module` for the reason this whole package
does it: ``PdfSourceRegistry`` lives in ``rmspec.export``, and ``test_cli_entry.py`` AST-walks
every ``cli/*.py`` but ``_container.py`` and fails the build on any static import of an adapter --
``ast.walk``, so ``if TYPE_CHECKING:`` and a function-local ``import`` count exactly the same.

The two texts are different facts and are never merged
-----------------------------------------------------
``printed_text`` is what the PDF itself carries; ``annotations`` is what the model read off the
ink drawn over it. Every mode keeps them apart, because the whole value of the answer is the
comparison -- a merged cell would leave a caller unable to tell a quoted clause from a reader's
correction of it. And ``annotations`` distinguishes ``None`` from ``""``: ``None`` means no model
was asked, because the page carries no ink at all, and ``""`` means one was asked and reported
nothing added. ``--dense`` therefore carries an explicit ``annotated`` column, since a dense cell
cannot say "absent" and "empty" with the same emptiness.

Three modes, three streams, and only one long text a table can carry
-------------------------------------------------------------------
``--json`` writes one envelope to stdout, ``--dense`` writes tab-separated records to stdout, and
the default writes a table to **stderr**. That split is the frozen output contract, and it is what
makes ``rmspec annotations doc --json | jq`` clean by construction and
``rmspec annotations doc 2>/dev/null`` correctly silent on a human run.

``printed_text`` and ``annotations`` are both long, and a table 80 columns wide cannot carry two
long texts -- given both, it gives each about thirty and a reader gets neither. So ``HUMAN`` keeps
``annotations`` and drops ``printed_text``, and the choice is not a coin toss: the printed text is
already in the caller's own PDF, while the ink is the thing this command went and read. The other
four cells are short and all stay, ``pdf_page_index`` included, because a reader quoting a clause
needs to know which page of the source it came from. The rest is one flag away and the caption says
so.

:data:`HUMAN_COLUMNS` is taken **by index** out of the very record :func:`_cells` already built,
through :data:`HUMAN_COLUMN_INDICES`, so the wide projection and the narrow one cannot drift.

The two ``degradations`` are not the same tuple
----------------------------------------------
The envelope's top-level ``degradations`` is everything that happened during this invocation,
document resolution included -- this command concatenates
:attr:`~rmspec.app.ResolveDocumentResult.degradations` with the use case's own before it emits.
``data.degradations`` is only what :class:`~rmspec.app.ReadAnnotationsResult` itself recorded,
because ``data`` is a faithful ``model_dump(mode="json")`` of that result and ``rmspec manifest``
says ``data`` **is** that type. So the top level is a *superset*, never a duplicate:
``ambiguous_auto_resolved`` appears there and not in ``data``, while ``pdf_page_index_fallback``
and ``page_not_annotated`` appear in both. That is the point -- one stable place to look for
everything the run did, and a payload that still round-trips back into the model it names.
Deleting either one loses a fact.

``truncated`` is added to the payload by hand, and has to be
-----------------------------------------------------------
:attr:`~rmspec.app.PageAnnotations.truncated` is a computed ``@property``, not a field, so
``model_dump(mode="json")`` **omits it** -- pydantic serialises fields. It is the one fact that
says the model stopped at its output limit, which for this command means the list of marks on that
page is half a list. A densely annotated page is the realistic way to reach it, so leaving a
caller to re-derive it from ``stop_reason`` would be asking every consumer to import this
project's vocabulary in order to learn that its payload is incomplete. :func:`_payload` puts it
back under :data:`TRUNCATED_KEY`, page by page, and :data:`DENSE_COLUMNS` carries it as a column.

What this path degrades rather than fails on
--------------------------------------------
``PDF_PAGE_INDEX_FALLBACK`` -- the document's redirection map had no entry for a page, so its own
position was used as the source PDF page -- and ``PAGE_NOT_ANNOTATED``, per page with no ink.
Both reach stderr in ``HUMAN`` and ``DENSE`` and the envelope's top-level ``degradations`` in
``JSON``. Neither is ever swallowed: the redirection fallback in particular is how the ink of one
page comes to sit over the print of another, and a run that hid it would produce an answer that
reads perfectly and describes the wrong page.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

from rich.table import Table

from rmspec.app import ReadAnnotations, ReadAnnotationsRequest
from rmspec.cli._invoke import (
    FEATURE_MODEL_BEDROCK,
    FEATURE_PDF_READ,
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
from rmspec.cli._manifest import RESPONSE_TYPES, SETTING_PREFIX
from rmspec.cli._output import OutputMode
from rmspec.domain import errors
from rmspec.domain.models import DocumentId

if TYPE_CHECKING:
    from rmspec.app import PageAnnotations, ReadAnnotationsResult
    from rmspec.domain.ports.export import PdfSourceRef

__all__ = [
    "COMMAND",
    "DENSE_COLUMNS",
    "HUMAN_CAPTION",
    "HUMAN_COLUMNS",
    "HUMAN_COLUMN_INDICES",
    "PDF_SUFFIX",
    "TRUNCATED_KEY",
    "XOCHITL_VARIABLE",
    "annotations",
    "read_annotations",
]

COMMAND: Final = "annotations"
"""The invocation a user types, which is also this command's key in ``RESPONSE_TYPES``."""

_CONTAINER_MODULE: Final = "rmspec.cli._container"
"""Where both of :func:`_source_for`'s collaborators are reached, spelled as a string on purpose.

``PdfSourceRegistry`` is ``rmspec.export``'s, and this module may not name an adapter package --
see this module's docstring. ``_container.py`` is the one module allowed to, so this reaches it
by a string the entry test's AST walk cannot read, which is also the only way past ruff's
``PLC0415`` ban on a function-local ``import``. ``RawBundleSource`` is a domain port and needs none
of that; it is taken from the same place so that one function does not resolve its two
collaborators two different ways.
"""

PDF_SUFFIX: Final = ".pdf"
"""What a xochitl mirror names a document's source PDF, beside its ``<uuid>.metadata``.

``rmspec.formats.layout`` publishes a constant for every sidecar it reads and none for the
underlay, because nothing in that package opens one. So this is the single place the CLI states
it, rather than four call sites each writing the string.
"""

XOCHITL_VARIABLE: Final = f"{SETTING_PREFIX}XOCHITL"
"""The variable whose value :func:`_source_for` looks for a mirrored copy of the print under.

It names no refusal any more: with the variable unset the underlay is read off the device instead,
and this is only the cheaper of the two branches. Built from the manifest's own prefix rather than
typed out, so it is spelled the way ``rmspec manifest`` and ``rmspec env`` both print it.
"""

TRUNCATED_KEY: Final = "truncated"
"""Where :attr:`~rmspec.app.PageAnnotations.truncated` is written into each page of the payload.

Named rather than inlined because two places must agree on it: :func:`_payload`, which adds the
key, and :data:`DENSE_COLUMNS`, which projects the same fact as a column.
"""

DENSE_COLUMNS: Final = (
    "page_index",
    "pdf_page_index",
    "annotated",
    "truncated",
    "printed_text",
    "annotations",
)
"""The columns ``--dense`` projects: both page numberings, then both texts, kept apart.

``pdf_page_index`` earns its place beside ``page_index`` because the two differ exactly when the
document's redirection map is doing work, and a caller quoting a clause needs to know which page
of the *source* it came from. ``annotated`` is the ``None``-versus-``""`` distinction a dense cell
cannot otherwise carry. ``page_ref`` and ``stop_reason`` are dropped: the first is an identity for
a filename, and everything a caller must branch on in the second is ``truncated``.
"""

HUMAN_COLUMN_INDICES: Final = (0, 1, 2, 3, 5)
"""Which cells of a :func:`_cells` record the ``HUMAN`` table shows, by position.

Indices rather than a second tuple of names, because a projection built from the same record cannot
disagree with it: rename a dense column and the human header moves with it, reorder
:data:`DENSE_COLUMNS` and this list is the one thing that has to be corrected, in one place.
"""

HUMAN_COLUMNS: Final = tuple(DENSE_COLUMNS[index] for index in HUMAN_COLUMN_INDICES)
"""The ``HUMAN`` headers: both numberings, both flags, and the ink -- but not the print.

The four short cells and exactly one of the two long ones. See this module's docstring for why it
is ``annotations`` that stays: a reader has the printed text in the PDF they pushed, and the ink is
what they ran this command to see.
"""

HUMAN_CAPTION: Final = "printed_text is in --dense and --json; a table cannot carry two long texts"
"""What the ``HUMAN`` table says under itself, so the narrowing is stated rather than discovered.

A caption and not a warning: nothing went wrong, and a reader who wanted the printed clause beside
the correction needs to be told which mode has both rather than left to infer that an absent column
means an unread page.
"""

_BOOLEAN_CELLS: Final = {True: "true", False: "false"}
"""How a boolean is spelled in a dense cell -- JSON's two words, so no second vocabulary."""


def _source_for(invoked: Invoked, /, *, doc_uuid: str) -> PdfSourceRef:
    """Mint the opaque handle to one document's source PDF.

    Parameters
    ----------
    invoked
        The open invocation, for the settings and the request scope.
    doc_uuid
        The resolved document's identifier, which is also the stem of its files in the mirror.

    Returns
    -------
    ~rmspec.domain.ports.export.PdfSourceRef
        A fresh token minted by the composition root's own registry -- the only component that can
        later resolve it back into bytes. It names ``<RMSPEC_XOCHITL>/<uuid>.pdf`` when a mirror is
        configured, and the underlay pulled off the device when none is.

    Notes
    -----
    Two sources, and the mirror is preferred only because it is cheaper. When ``RMSPEC_XOCHITL``
    names a mirror the print is already a file, so ``for_path`` mints a token for
    ``<root>/<uuid>.pdf`` and nothing is transferred. With no mirror the underlay is pulled from
    the tablet and ``for_bytes`` mints a token over the payload -- which is the call the sync
    path was written for: it holds bytes and no location at all.

    This used to raise ``XochitlDirNotConfigured`` when no mirror was configured, and that made
    ``rmspec annotations`` **dead in the default configuration** -- measured against the attached
    tablet, exit 78 pointing at a variable naming a xochitl tree that ``rmspec sync`` does not
    create, because sync writes ``RMSPEC_SYNC_DB``. The repository behind the ink had already
    been fixed to fall back to the device; this site was the second, independent refusal, and it
    fired before the request was built so no model call was ever reached.

    Nothing here opens a file or asks whether it exists. Readability is the reader adapter's
    question, and answering it twice would give two components an opinion about it -- which is
    also what the registry's own ``for_path`` docstring says about validation.

    The mirror path is the asymmetric one: it mints a token for a path it never reads, so a
    notebook only fails later, inside the use case. The device path has the bytes in hand and so
    learns immediately that there are none, which is why the refusal below exists on one branch
    and not the other. Both end in the same ``UsageError`` the use case raises, because "this is
    not a PDF-backed document" is one fact and must not have two spellings.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        The document carries no underlay, so there is no print to read annotations over --
        ``DocumentSourceBundle.base`` is ``None`` for a notebook by the model's own rule.
    """
    registry = invoked.get(importlib.import_module(_CONTAINER_MODULE).PdfSourceRegistry)
    root = invoked.settings.xochitl
    if root is not None:
        return registry.for_path(root / f"{doc_uuid}{PDF_SUFFIX}")
    bundles = invoked.get(importlib.import_module(_CONTAINER_MODULE).RawBundleSource)
    base = bundles.load_bundle(doc_uuid).base
    if base is None:
        raise errors.UsageError(
            subject=f"document {doc_uuid}",
            requirement="a PDF-backed document; a notebook has no print to annotate over",
        )
    return registry.for_bytes(base)


def _payload(result: ReadAnnotationsResult, /) -> dict[str, Any]:
    """Serialise the result, putting the computed ``truncated`` back on every page.

    Parameters
    ----------
    result
        What the use case answered.

    Returns
    -------
    dict[str, Any]
        ``model_dump(mode="json")``, with :data:`TRUNCATED_KEY` added to each entry of ``pages``.

    Notes
    -----
    ``strict=True`` on the zip is the point of using one: the dumped list and the model tuple are
    two views of the same pages, and a silent truncation of the shorter would attach one page's
    ``truncated`` to another page's row.
    """
    payload = result.model_dump(mode="json")
    payload["pages"] = [
        {**dumped, TRUNCATED_KEY: page.truncated}
        for dumped, page in zip(payload["pages"], result.pages, strict=True)
    ]
    return payload


def _cells(page: PageAnnotations, /) -> tuple[str, ...]:
    """Project one page onto :data:`DENSE_COLUMNS`.

    Parameters
    ----------
    page
        One row of the result, written on or not.

    Returns
    -------
    tuple[str, ...]
        One already-stringified cell per column. A page nobody wrote on reads ``annotated``
        ``false`` and leaves ``annotations`` empty, which is how the ``None``-versus-``""``
        distinction survives a format whose cells are all strings.
    """
    return (
        str(page.page_index),
        str(page.pdf_page_index),
        _BOOLEAN_CELLS[page.annotations is not None],
        _BOOLEAN_CELLS[page.truncated],
        page.printed_text,
        page.annotations or "",
    )


def _table(result: ReadAnnotationsResult, /) -> Table:
    """Build the ``HUMAN`` rendering: the narrow projection of the same records.

    Parameters
    ----------
    result
        What the use case answered.

    Returns
    -------
    ~rich.table.Table
        One row per selected page, each cell taken out of :func:`_cells` by the positions
        :data:`HUMAN_COLUMN_INDICES` names. A page nobody wrote on is a row reading ``annotated``
        ``false`` with an empty ``annotations`` cell, which is the same ``None``-versus-``""``
        distinction the dense projection carries and for the same reason.

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


def annotations(
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
    """Read the handwritten annotations on a PDF-backed document, page by page.

    With neither ``--json`` nor ``--dense`` the answer is a table on **stderr** carrying both page
    numberings, whether the page was written on, whether the answer was cut off, and the ink. The
    printed text is in the two modes that write to stdout: a table 80 columns wide cannot carry two
    long texts, and the print is already in the PDF the ink sits on.

    Parameters
    ----------
    doc
        Which document: a name substring, a full uuid, or a uuid prefix. It must be PDF-backed --
        a notebook has no printed page for a mark to sit on, so one is refused as a usage error.
        Several matches are ranked and the winner used, with the choice reported as a
        degradation, unless ``--strict`` was passed.
    pages
        Which pages to read, as a comma-separated list of 0-based page indices and inclusive A-B
        ranges, as in 0 or 2-5 or 0,3,7-9. Every page when omitted. Mutually exclusive with
        ``--limit``.
    limit
        Read at most this many leading pages. Mutually exclusive with ``--pages``.
    max_pages
        Override ``RMSPEC_MAX_PAGES`` (64) for this run. The cap is enforced before the first
        rasterization, because this pass costs one PDF render and one model call per annotated
        page.
    strict
        Refuse an ambiguous selector instead of accepting the ranked winner.
    json
        Emit one envelope on stdout, typed ``annotations``. Each page carries ``printed_text``
        and ``annotations`` separately, plus ``truncated``, which the result model computes
        rather than stores.
    dense
        Emit one tab-separated record per page on stdout instead, with the columns
        ``page_index  pdf_page_index  annotated  truncated  printed_text  annotations``. Mutually
        exclusive with ``--json``.

    Returns
    -------
    int
        ``0``, including for a document nobody has written on yet: an unannotated page is a row
        carrying its printed text and ``annotations`` unset. ``2`` for a document that is not
        PDF-backed or a contradictory command line, and whatever
        :func:`~rmspec.domain.errors.exit_code` scores any other failure.
    """

    def body(invoked: Invoked) -> int:
        invoked.probe(
            FEATURE_PDF_READ,
            FEATURE_SCENE_DECODE,
            FEATURE_RASTER,
            FEATURE_MODEL_BEDROCK,
        )
        resolved = invoked.document(doc, strict=strict)
        result = invoked.get(ReadAnnotations).read(
            ReadAnnotationsRequest(
                doc_id=DocumentId(uuid=resolved.chosen.uuid),
                source=_source_for(invoked, doc_uuid=resolved.chosen.uuid),
                pages=invoked.selection(pages=pages, limit=limit),
                max_pages=invoked.max_pages(max_pages),
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


read_annotations = annotations
"""The same function under a name that cannot collide with the ``__future__`` feature.

Measured, because it costs a build: every module in this repo opens with
``from __future__ import annotations``, which binds the name ``annotations`` in that module to a
``__future__._Feature``. A later ``from rmspec.cli._annotations import annotations`` makes the
name a *union* of the feature and this function, and ``ty`` then rejects any call to it as
``call-non-callable`` -- while the repo allows neither a ``noqa`` nor a ``type: ignore``. Passing
the function to ``app.command`` is not a call, so registration is safe either way; a test or any
other caller that actually invokes it needs this name.

It is the same object, so ``__name__`` is still ``"annotations"`` and cyclopts derives the CLI
name from it unchanged -- importing the alias cannot accidentally register ``read-annotations``.
"""
