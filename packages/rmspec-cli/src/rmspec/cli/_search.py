"""``rmspec search``: one term, one backend, and every hit says which source read it.

One backend, because the firmware has no search route
-----------------------------------------------------
The legacy tree had ``search`` and ``search --device`` as two commands over two code paths,
and the second one was a fiction: the tablet's route table is closed at six families and none
of them serves a query. So ``--device`` is gone, and the distinction it was reaching for is
kept where it belongs -- on the match. :class:`~rmspec.app.SearchText` reads the local mirror
*and* the tablet's own handwriting index in one pass and tags every
:class:`~rmspec.app.TextMatch` with a
:class:`~rmspec.app.search.MatchSource`, so a caller can tell the tablet's reading of a page
from ours without asking twice and without a flag that decides for it.

The two attributions are not equally strong and the command must not flatten them. A
``mirror`` reading came from a transcription this tool paid for and carries full provenance;
a ``device_index`` reading is a free prior with no provenance that lags the tablet. Both are
surfaced, and a page both sources matched is reported twice with ``corroborated`` set on both
rows -- two independent recognizers agreeing is the evidence, and collapsing it to one row
would throw the evidence away.

The matcher is a substring test and says so
------------------------------------------
``--help`` describes a case-insensitive substring scan, because that is what it is: no
inverted index, no tokenizer, no stemming, no ranking. Results come back in the store's order
rather than by relevance. The honest surface is the whole point -- a ``--help`` that said
"ranked" or "fuzzy" would be advertising an FTS5 index that has not been built, and the first
caller to trust that sentence would silently get worse answers than it thought.

When real search lands it is a persistence-layer change (an FTS5 table maintained inside
``record_page_text``, plus one port method), not a bigger loop in the app layer -- and this
command's flags do not change when it does.

Judging a thin result, and why a missing index never fails the run
-----------------------------------------------------------------
Three fields exist so a caller can weigh an answer instead of trusting it: ``outcome``
distinguishes "your term is not in your notes" from "nothing has been pulled" and from
"nothing has been transcribed"; ``recent_ocr_attempt`` says whether an ``ocr`` run appears in
recent history at all, which is evidence and never proof; and ``corroborated`` says whether
two recognizers agreed. All three are in the envelope, and the first two are summarised on
stderr for a human.

``DegradationKind.DEVICE_INDEX_UNAVAILABLE`` is reported and **never** fails the command. The
device index is a free prior: losing it costs the prior and nothing else, so failing a search
over it would trade a real answer for none. The mirror and the audit log do not degrade --
they are the answer -- and their failures propagate to :func:`~rmspec.cli._invoke.run` like
any other.

An empty result is still exit status 0
-------------------------------------
"No page contained that term" is an answer, not a failure. ``grep`` exits 1 for it; this
command does not, because every non-zero status in this CLI comes from
:func:`~rmspec.domain.errors.exit_code` over a domain error class, and inventing a
"nothing matched" status would be a second exit vocabulary for a result the ``outcome`` field
already reports precisely. A caller branches on ``data.outcome``.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from rich.table import Table

from rmspec.app import SearchText, SearchTextRequest
from rmspec.app.search import SearchOutcome
from rmspec.cli._invoke import DenseFlag, Invoked, JsonFlag, run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import NextAction, OutputMode

if TYPE_CHECKING:
    from rmspec.app import SearchTextResult, TextMatch

__all__ = [
    "DENSE_COLUMNS",
    "EXCERPT_WIDTH",
    "DocOption",
    "search",
]

DocOption = Annotated[str | None, Parameter(name="--doc")]
"""``--doc``: the uuid of the one recorded document to search in.

A uuid rather than the name substring every *other* document-taking command accepts, and the
asymmetry is deliberate. :attr:`~rmspec.app.SearchTextRequest.doc_uuid` selects between the
mirror's two entry points -- one document or all of them -- and the mirror is what search
reads. Resolving a name here would mean asking the *tablet's* catalog which document a
substring meant, which is a device round trip this command otherwise does not need, and would
still end in ``DocumentNotFound`` from the mirror whenever the resolved document had never
been pulled. So there is no ``--strict`` on this command: there is no ambiguity to resolve.
"""

EXCERPT_WIDTH: Final = 120
"""How many characters of a matching page a human sees in the table, around the term.

:attr:`~rmspec.app.TextMatch.text` is the page's whole reading, deliberately: the app layer's
own docstring says "windowing text around the term is a display decision, and the CLI is the
only layer that knows how wide the terminal is". This is that decision, made once. It applies
to the ``HUMAN`` table and to nothing else -- ``--json`` and ``--dense`` both carry the full
reading, because a silently truncated payload is the defect a bounded-context mode must not
have.
"""

DENSE_COLUMNS: Final = (
    "doc_uuid",
    "doc_name",
    "page_index",
    "source",
    "corroborated",
    "text",
)
"""The ``--dense`` projection: what addresses a hit, what qualifies it, and the reading.

``text`` is **last** on purpose, so ``cut -f1-5`` is the metadata and ``cut -f6-`` is the
reading -- the one field that may be kilobytes long. ``provenance`` and ``index_generation``
are dropped rather than flattened: each is meaningful for exactly one ``source``, and a column
that is empty for half the rows is worse than a documented trip to ``--json``.
"""

_NOTHING_SYNCED_PURPOSE: Final = (
    "pull the tablet's library into the local mirror, because search covers recorded pages "
    "and there are none"
)
"""Why ``rmspec sync`` is the next command when nothing has been recorded at all."""

_NOTHING_TRANSCRIBED_PURPOSE: Final = (
    "transcribe this document's pages, because they are recorded and none of them has been "
    "read by either source"
)
"""Why ``rmspec ocr`` is the next command when the scope holds pages and no text."""


def _excerpt(text: str, term: str, /) -> str:
    """Window one page's reading around the term, for a human reading a table.

    Parameters
    ----------
    text
        The page's whole reading, newlines and all.
    term
        The term that matched, used to decide where the window sits.

    Returns
    -------
    str
        Whitespace collapsed to single spaces, and at most :data:`EXCERPT_WIDTH` characters
        with ``...`` marking each end that was cut. A reading short enough to show whole is
        returned whole.

    Notes
    -----
    The term's position is found under ``str.casefold`` on the collapsed text, and the start
    is then clamped into range. Clamping is what makes the "term not found" case need no
    branch: collapsing whitespace can only ever merge runs of it, so a term containing a
    newline may legitimately fail to reappear here, and ``find``'s ``-1`` clamps to the head
    of the reading -- which is the right answer for a caller who cannot be shown their term.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_WIDTH:
        return collapsed
    lead = EXCERPT_WIDTH // 4
    found = collapsed.casefold().find(term.casefold())
    start = max(0, min(found - lead, len(collapsed) - EXCERPT_WIDTH))
    end = start + EXCERPT_WIDTH
    head = "..." if start else ""
    tail = "..." if end < len(collapsed) else ""
    return f"{head}{collapsed[start:end]}{tail}"


def _summary(result: SearchTextResult, /) -> str:
    """Phrase the fields that make an empty or thin result interpretable.

    Parameters
    ----------
    result
        What the search found.

    Returns
    -------
    str
        One line for stderr, naming the outcome, how many readings matched, how many pages
        were examined, and -- only when the search asked -- whether an ``ocr`` run appears in
        recent history.

    Notes
    -----
    Written to stderr in ``HUMAN`` *and* in ``DENSE``, because a ``DENSE`` stdout is one match
    per line and a summary row would break that contract. A caller that must parse the outcome
    asks for ``--json``, which is the same rule :meth:`~rmspec.cli._output.CliOutput.fail`
    already states for a failure in ``DENSE``.
    """
    line = (
        f"{result.outcome.value}: {len(result.matches)} reading(s) matched "
        f"across {result.pages_searched} recorded page(s)"
    )
    if result.recent_ocr_attempt is None:
        return line
    seen = "an ocr run appears" if result.recent_ocr_attempt else "no ocr run appears"
    return f"{line}; {seen} in recent history, which is evidence rather than proof"


def _next_action(result: SearchTextResult, /, *, doc: str | None) -> NextAction | None:
    """Give the command that would make the next search better, when there is one.

    Parameters
    ----------
    result
        What the search found.
    doc
        The ``--doc`` uuid, or ``None``.

    Returns
    -------
    ~rmspec.cli._output.NextAction | None
        ``rmspec sync`` when nothing is recorded, ``rmspec ocr <uuid>`` when a named document
        is recorded and unread, and ``None`` otherwise.

    Notes
    -----
    Nothing is suggested for ``NOTHING_TRANSCRIBED`` without ``--doc``, and that is the
    contract rather than an omission: :class:`~rmspec.cli._output.NextAction` carries "the
    exact shell line to run, ready to execute", and this command has no document uuid to put
    in one -- an empty ``matches`` names none. ``NO_MATCH`` and ``MATCHED`` suggest nothing
    because trying another term is not a command.
    """
    if result.outcome is SearchOutcome.NOTHING_SYNCED:
        return NextAction(command="rmspec sync", purpose=_NOTHING_SYNCED_PURPOSE)
    if result.outcome is SearchOutcome.NOTHING_TRANSCRIBED and doc is not None:
        return NextAction(command=f"rmspec ocr {doc}", purpose=_NOTHING_TRANSCRIBED_PURPOSE)
    return None


def _dense_row(match: TextMatch, /) -> tuple[str, ...]:
    """Project one match onto :data:`DENSE_COLUMNS`.

    Parameters
    ----------
    match
        One attributed reading.

    Returns
    -------
    tuple[str, ...]
        One record's cells, already stringified. ``corroborated`` is spelled ``true`` or
        ``false`` so that the same value reads the same way in ``--dense`` and ``--json``.
    """
    return (
        match.doc_uuid,
        match.doc_name,
        str(match.page_index),
        match.source.value,
        str(match.corroborated).lower(),
        match.text,
    )


def _table(result: SearchTextResult, /) -> Table:
    """Build the table a human sees on stderr.

    Parameters
    ----------
    result
        What the search found.

    Returns
    -------
    ~rich.table.Table
        One row per attributed reading. ``both`` is ticked on each row of a page two sources
        agreed on, so either row alone says the reading was corroborated.
    """
    table = Table(title=f"matches for {result.query!r}")
    table.add_column("document")
    table.add_column("page", justify="right")
    table.add_column("source")
    table.add_column("both", justify="center")
    table.add_column("text")
    for match in result.matches:
        table.add_row(
            match.doc_name,
            str(match.page_index),
            match.source.value,
            "yes" if match.corroborated else "",
            _excerpt(match.text, result.query),
        )
    return table


def _report(invoked: Invoked, /, *, query: str, doc: str | None) -> int:
    """Search, then render the answer the way this invocation's mode requires.

    A module-level function rather than a closure inside :func:`search`, so a test can drive
    the whole body through :func:`~rmspec.cli._invoke.run` with the shipped in-memory doubles
    bound over the real ports -- which is the only way to exercise this without opening a
    database, an SSH session or a model client.

    Parameters
    ----------
    invoked
        The open invocation: the writer, the settings and the request scope.
    query
        The term, as the user typed it.
    doc
        The ``--doc`` uuid, or ``None`` to search every recorded document.

    Returns
    -------
    int
        ``0``. Nothing about the number of matches changes the status.
    """
    result = invoked.get(SearchText).search(SearchTextRequest(query=query, doc_uuid=doc))
    action = _next_action(result, doc=doc)
    if invoked.out.mode is OutputMode.JSON:
        invoked.out.emit(
            result.model_dump(mode="json"),
            response_type=RESPONSE_TYPES["search"],
            degradations=result.degradations,
            next_action=action,
        )
        return 0
    invoked.report(result.degradations)
    invoked.out.display(_summary(result))
    if action is not None:
        invoked.out.display(f"next: {action.command}  # {action.purpose}")
    if invoked.out.mode is OutputMode.DENSE:
        invoked.out.rows(DENSE_COLUMNS, (_dense_row(match) for match in result.matches))
    else:
        invoked.out.display(_table(result))
    return 0


def search(
    query: str,
    /,
    *,
    doc: DocOption = None,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Find a term in transcribed page text, saying of every hit which source read it.

    Matching is a case-insensitive substring test over the text already recorded for each
    page: there is no ranking, no fuzzy matching and no stemming, and hits come back in the
    mirror's own order rather than by relevance. Two sources are consulted in one pass -- the
    local mirror's transcriptions, which carry full provenance, and the tablet's own
    handwriting index, which is free, unprovenanced and behind the tablet -- and every hit
    names the one that produced it. A page both sources matched is reported twice, marked as
    corroborated.

    An empty result is not a failure and exits ``0``. The ``outcome`` field says which kind of
    nothing happened: no recorded pages at all, recorded pages with no text yet, or text that
    did not contain the term.

    Parameters
    ----------
    query
        The term to look for. Surrounding whitespace is stripped; a blank term is refused.
    doc
        The uuid of one recorded document to search in, or omitted to search every recorded
        document. A uuid the local mirror does not track is an error rather than an empty
        result.
    json
        Emit the one JSON envelope on stdout instead of a table on stderr.
    dense
        Emit tab-separated records on stdout, one per match, with the columns
        ``doc_uuid``, ``doc_name``, ``page_index``, ``source``, ``corroborated``, ``text``.

    Returns
    -------
    int
        ``0``. Every non-zero status comes from a domain failure through the shared error
        boundary, never from how many pages matched.
    """
    return run(partial(_report, query=query, doc=doc), json=json, dense=dense)
