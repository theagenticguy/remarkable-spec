"""``rmspec sync``: one pull, one prediction of a pull, and one reading of what pulls did.

Three legacy commands, one verb
-------------------------------
``sync pull``, ``sync status`` and ``sync log`` are ``rmspec sync``, ``rmspec sync --dry-run``
and ``rmspec sync --history``. The first two are one call with one flag on purpose:
:class:`~rmspec.app.SyncDocuments` enumerates, compares against the mirror, and then performs
the transfers the comparison implies, so a dry run is the same comparison stopped one step
early. A separate status command would be a second implementation of the change signal, and
two implementations of a change signal is how a status command comes to disagree with the pull
it is supposed to predict.

``--history`` is the one flag in this CLI that selects a discriminator
--------------------------------------------------------------------
It calls :class:`~rmspec.app.ReportSyncHistory` rather than
:class:`~rmspec.app.SyncDocuments`, so its ``data`` is a different shape and the envelope must
say so: ``RESPONSE_TYPES["sync"]`` is ``"sync"`` and this command passes
:data:`HISTORY_RESPONSE_TYPE` itself on that branch. Everywhere else in this CLI the command
name determines the discriminator; here the flag does, and a caller that branched on
``type`` alone would otherwise try to read ``documents`` out of a page of audit entries.

``--history`` with ``--dry-run`` is refused rather than resolved. There is no defensible
reading of "predict a pull, from the history": one of the two flags would have to be ignored,
and silently ignoring a flag a caller typed is the failure mode this whole surface is built
against. ``--limit`` without ``--history`` is refused for the same reason -- it bounds a page
of history and there is no page to bound.

Exit status comes from the result, and ``SKIPPED`` is genuinely success
---------------------------------------------------------------------
Legacy's ``sync pull`` exited 0 while reporting skipped documents. That defect is not fixed by
reporting harder; it is fixed by scoring the run from what it actually did. The per-document
failures :class:`~rmspec.app.SyncDocuments` folds into
:class:`~rmspec.app.SyncedDocumentOutcome` -- ``DeviceDocumentNotFound``,
``DeviceTransferInterrupted``, ``MalformedDeviceMetadata`` -- never reach
:func:`~rmspec.cli._invoke.run`'s error boundary, so nothing else can score them.

**The rule: exit 0 for ``SUCCEEDED`` and ``SKIPPED``, and**
:data:`INCOMPLETE_EXIT_STATUS` **for anything else.** Spelled as
:data:`CLEAN_OUTCOMES`, an allow-list rather than a deny-list, so that a fifth
:class:`~rmspec.domain.models.SyncOutcome` member would exit non-zero until somebody decided
otherwise -- the loud direction, given what the quiet direction cost last time.

``SKIPPED`` exits 0 because in *this* vocabulary it means "nothing needed doing": every
document was already current, or the run was a dry one. It is not legacy's "skipped", which
meant "we could not do this and said so in a warning that scrolled off the top of the
terminal"; that case is ``FAILED`` on the document and ``PARTIAL`` or ``FAILED`` on the run.
``PARTIAL`` therefore exits non-zero even though most of the library landed, because a run
that reports a failed document and exits 0 is exactly the defect.

The result document is still emitted before the non-zero status is returned. A caller needs to
know *which* document failed, and that is in ``data.documents`` -- so the failure is rendered
as a result with a non-zero status rather than as an error envelope, which would have replaced
the per-document detail with one sentence.

:data:`INCOMPLETE_EXIT_STATUS` is 1, which is
:func:`~rmspec.domain.errors.exit_code`'s own answer for a failure with no more specific row
in its table. No second vocabulary is invented: the per-document failures were folded into the
result rather than raised, so there is no single error class left to score, and the generic
bucket is the honest one.

``absent`` and ``forgotten`` are two lists and stay two lists
------------------------------------------------------------
``absent`` is every mirrored document the device's enumeration did not contain -- what a prune
*would* remove. ``forgotten`` is what it actually removed. They differ whenever a prune guard
tripped: a dry run, an enumeration that represented no documents at all, or one that reported
any entry it could not represent. Merging them into one list would erase the difference
between "the tablet no longer has this" and "we deleted our copy", which is the difference
between a report and an irreversible act. In ``--dense`` they are told apart by a leading
``kind`` column rather than by two streams, so one record stream still carries all three
projections and ``cut -f1`` selects between them.

The clock is a function, so a test can hold it still
---------------------------------------------------
:attr:`~rmspec.app.SyncDocumentsRequest.synced_at` is required and must be timezone-aware --
the app layer holds no clock deliberately, so that no test has to freeze one, and ruff's
``DTZ`` rules make a naive datetime a lint failure. :func:`now` is where the clock lives: one
module-level function, so a test replaces it with a fixed instant and can then assert on the
rows a run wrote. A command that read the clock inline would be untestable under ``-n auto``,
where two workers cannot agree on what "now" was.
"""

from __future__ import annotations

import datetime
from functools import partial
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from rich.table import Table

from rmspec.app import (
    ReportSyncHistory,
    ReportSyncHistoryRequest,
    SyncDocuments,
    SyncDocumentsRequest,
)
from rmspec.cli._invoke import DenseFlag, Invoked, JsonFlag, LimitOption, run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import NextAction, OutputMode
from rmspec.domain import errors
from rmspec.domain.models import SyncOutcome

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rmspec.app import ReportSyncHistoryResult, SyncDocumentsResult

__all__ = [
    "CLEAN_OUTCOMES",
    "DOCUMENT_COLUMNS",
    "HISTORY_COLUMNS",
    "HISTORY_RESPONSE_TYPE",
    "INCOMPLETE_EXIT_STATUS",
    "DryRunFlag",
    "HistoryFlag",
    "now",
    "sync",
]

DryRunFlag = Annotated[bool, Parameter(name="--dry-run", negative="")]
"""``--dry-run``: report what a pull would do and write nothing at all.

``negative=""`` for the reason :data:`~rmspec.cli._invoke.JsonFlag` gives: cyclopts would
otherwise generate a ``--no-dry-run`` that ``rmspec manifest`` does not list and ``--help``
does not explain, which is two surfaces disagreeing about the same flag.
"""

HistoryFlag = Annotated[bool, Parameter(name="--history", negative="")]
"""``--history``: read what previous runs recorded instead of touching the tablet."""

HISTORY_RESPONSE_TYPE: Final = "history"
"""The discriminator ``--history`` selects, in place of ``RESPONSE_TYPES["sync"]``.

Spelled here rather than taken from that table because the table is keyed by the invocation a
user types and ``sync --history`` is one command with two result shapes -- the single place in
this CLI where a flag decides the envelope's ``type``. A caller reads this key before it reads
``data``, which is the whole point of a discriminator.
"""

INCOMPLETE_EXIT_STATUS: Final = 1
"""The status a run that finished while reporting failed documents exits with.

Deliberately :func:`~rmspec.domain.errors.exit_code`'s own fallback rather than a number
invented here: that function answers 1 for any failure with no more specific row in its table,
and a run whose per-document failures were folded into its result has no single error class
left to score. See this module's docstring for why the result is emitted first and the status
returned second.
"""

CLEAN_OUTCOMES: Final = frozenset({SyncOutcome.SUCCEEDED, SyncOutcome.SKIPPED})
"""The run outcomes that exit 0. An allow-list, so a new member exits non-zero by default.

``SKIPPED`` is here because in this vocabulary it means "nothing needed doing" -- every
document already current, or a dry run that by construction landed nothing. ``PARTIAL`` is
deliberately absent even though most of the library landed.
"""

DOCUMENT_COLUMNS: Final = (
    "kind",
    "uuid",
    "name",
    "outcome",
    "changed",
    "pages_changed",
    "detail",
)
"""The ``--dense`` projection of a pull: one record per listed, absent or forgotten document.

``kind`` leads so that ``cut -f1`` tells the three projections apart without merging them.
``pages_recorded`` is the one field of
:class:`~rmspec.app.SyncedDocumentOutcome` left out: it is how many pages the mirror now holds
rather than anything this run learned, and it is in ``--json``. An ``absent`` or ``forgotten``
record carries only its kind and its uuid, because that is all the result holds for one -- both
lists are tuples of uuids, and re-reading the store to decorate a prune would make a report
cost a query.
"""

HISTORY_COLUMNS: Final = (
    "sequence",
    "occurred_at",
    "operation",
    "outcome",
    "doc_uuid",
    "doc_name",
    "pages_affected",
    "detail",
)
"""The ``--dense`` projection of ``--history``: one record per audit entry.

Every field of :class:`~rmspec.domain.models.RecordedSyncAuditEntry`, and that is the point --
an audit record is already the projection, eight narrow columns wide, and a log line that
omits one is a log line the reader has to fetch again as JSON.
"""

_KIND_DOCUMENT: Final = "document"
"""``kind`` for a document the device's enumeration contained."""

_KIND_ABSENT: Final = "absent"
"""``kind`` for a mirrored document the enumeration did not contain."""

_KIND_FORGOTTEN: Final = "forgotten"
"""``kind`` for a mirrored document this run actually removed."""

_BLANK: Final = ""
"""What an ``absent`` or ``forgotten`` record puts in the columns a uuid list cannot fill."""

_PRUNE_REFUSED: Final = (
    "absent but not forgotten: nothing was deleted from the mirror -- a dry run never prunes, "
    "and a real run refuses to prune against an enumeration it cannot trust as complete"
)
"""Said once, when the two lists differ, so a refused prune is visible instead of silent."""

_PULL_PURPOSE: Final = (
    "perform the pull this report predicted, because at least one document would change"
)
"""Why ``rmspec sync`` is the next command after a dry run that found work to do."""


def now() -> datetime.datetime:
    """Give the instant every row and history entry this run is stamped with.

    Returns
    -------
    datetime.datetime
        The current time in UTC, timezone-aware.
        :attr:`~rmspec.app.SyncDocumentsRequest.synced_at` is an ``AwareDatetime`` and ruff's
        ``DTZ`` rules refuse a naive one, so the timezone is not optional in either direction.

    Notes
    -----
    A module-level function purely so that a test can replace it with a fixed instant and then
    assert on what a run wrote. Reading the clock inline in the command body would make the
    same assertion flaky under ``-n auto``.
    """
    return datetime.datetime.now(datetime.UTC)


def _exit_status(result: SyncDocumentsResult, /) -> int:
    """Score the run from what it did, not from whether the transport stayed up.

    Parameters
    ----------
    result
        What the pull, or the prediction of one, reported.

    Returns
    -------
    int
        ``0`` when the outcome is in :data:`CLEAN_OUTCOMES`, else
        :data:`INCOMPLETE_EXIT_STATUS`. See this module's docstring for the rule and for the
        legacy defect it exists to close.
    """
    return 0 if result.outcome in CLEAN_OUTCOMES else INCOMPLETE_EXIT_STATUS


def _pull_summary(result: SyncDocumentsResult, /) -> str:
    """Phrase one pull in one line, keeping ``absent`` and ``forgotten`` apart.

    Parameters
    ----------
    result
        What the run reported.

    Returns
    -------
    str
        One line for stderr, naming whether anything was written, the run's outcome, how many
        documents the listing represented, how many the mirror holds that it did not, and how
        many were actually removed.
    """
    scope = "dry run" if result.dry_run else "pull"
    return (
        f"{scope} {result.outcome.value}: {len(result.documents)} document(s) listed, "
        f"{len(result.absent)} absent from the device, "
        f"{len(result.forgotten)} forgotten from the mirror"
    )


def _pull_next_action(result: SyncDocumentsResult, /) -> NextAction | None:
    """Give the command a dry run's reader should run, when the report found work.

    Parameters
    ----------
    result
        What the run reported.

    Returns
    -------
    ~rmspec.cli._output.NextAction | None
        ``rmspec sync`` after a dry run that found at least one changed document, and ``None``
        otherwise. A real run suggests nothing: repeating a transfer that failed is a decision
        about a device, not an obvious next step.
    """
    if result.dry_run and any(entry.changed for entry in result.documents):
        return NextAction(command="rmspec sync", purpose=_PULL_PURPOSE)
    return None


def _document_rows(result: SyncDocumentsResult, /) -> Iterator[tuple[str, ...]]:
    """Project a pull onto :data:`DOCUMENT_COLUMNS`, one record at a time.

    Parameters
    ----------
    result
        What the run reported.

    Yields
    ------
    tuple[str, ...]
        One record per listed document, then one per absent uuid, then one per forgotten uuid.
        A generator, so a four-hundred-document library is not materialised twice.
    """
    for entry in result.documents:
        yield (
            _KIND_DOCUMENT,
            entry.uuid,
            entry.visible_name,
            entry.outcome.value,
            str(entry.changed).lower(),
            str(entry.pages_changed),
            entry.detail,
        )
    for uuid in result.absent:
        yield (_KIND_ABSENT, uuid, _BLANK, _BLANK, _BLANK, _BLANK, _BLANK)
    for uuid in result.forgotten:
        yield (_KIND_FORGOTTEN, uuid, _BLANK, _BLANK, _BLANK, _BLANK, _BLANK)


def _document_table(result: SyncDocumentsResult, /) -> Table:
    """Build the per-document table a human sees on stderr.

    Parameters
    ----------
    result
        What the run reported.

    Returns
    -------
    ~rich.table.Table
        One row per document the listing represented. ``absent`` and ``forgotten`` are
        reported as their own lines rather than as rows here, because a uuid with no name and
        no outcome in a table of documents reads as a document that failed.
    """
    table = Table(title="dry run" if result.dry_run else "pull")
    table.add_column("document")
    table.add_column("outcome")
    table.add_column("changed", justify="center")
    table.add_column("pages", justify="right")
    table.add_column("detail")
    for entry in result.documents:
        table.add_row(
            entry.visible_name or entry.uuid,
            entry.outcome.value,
            "yes" if entry.changed else "",
            str(entry.pages_changed),
            entry.detail,
        )
    return table


def _history_request(limit: int | None, /) -> ReportSyncHistoryRequest:
    """Turn the ``--limit`` flag into the one request the reporter takes.

    Parameters
    ----------
    limit
        The ``--limit`` value, or ``None`` to take the use case's own default.

    Returns
    -------
    ~rmspec.app.ReportSyncHistoryRequest
        The request.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        *limit* is not positive. Refused here rather than left to the request's ``ge=1``
        constraint, because a ``pydantic.ValidationError`` escaping a command body is a second
        error vocabulary with a traceback and exit status 1.

    Notes
    -----
    The *upper* bound is deliberately not checked here. The ceiling is
    :class:`~rmspec.app.ReportSyncHistory`'s own policy, it is a
    :class:`~rmspec.domain.errors.UsageError` there, and it is **never clamped** -- so a page
    shorter than the limit asked for always means a short log. Restating the number in this
    module would be a second copy of a policy that exists precisely so every front end shares
    one.
    """
    if limit is None:
        return ReportSyncHistoryRequest()
    if limit <= 0:
        raise errors.UsageError(subject=f"--limit {limit}", requirement="a count above zero")
    return ReportSyncHistoryRequest(limit=limit)


def _history_summary(result: ReportSyncHistoryResult, /) -> str:
    """Phrase one page of history, including what makes it comparable to the next page.

    Parameters
    ----------
    result
        The page that was read.

    Returns
    -------
    str
        One line for stderr. ``latest_sequence`` is reported because two reads whose newest
        sequence is equal saw the same history, which is a promise no timestamp can make.
    """
    latest = "none" if result.latest_sequence is None else str(result.latest_sequence)
    return (
        f"{len(result.entries)} of at most {result.limit} entries, newest first; "
        f"latest sequence {latest}"
    )


def _history_rows(result: ReportSyncHistoryResult, /) -> Iterator[tuple[str, ...]]:
    """Project a page of history onto :data:`HISTORY_COLUMNS`, one record at a time.

    Parameters
    ----------
    result
        The page that was read.

    Yields
    ------
    tuple[str, ...]
        One record per entry. A library-wide operation carries no document uuid, which is
        written as an empty cell rather than the string ``None``.
    """
    for record in result.entries:
        entry = record.entry
        yield (
            str(record.sequence),
            entry.occurred_at.isoformat(),
            entry.operation.value,
            entry.outcome.value,
            entry.doc_uuid or _BLANK,
            entry.doc_name,
            str(entry.pages_affected),
            entry.detail,
        )


def _history_table(result: ReportSyncHistoryResult, /) -> Table:
    """Build the history table a human sees on stderr.

    Parameters
    ----------
    result
        The page that was read.

    Returns
    -------
    ~rich.table.Table
        One row per entry, newest first, in the order the log returned them.
    """
    table = Table(title="sync history")
    table.add_column("seq", justify="right")
    table.add_column("when")
    table.add_column("operation")
    table.add_column("outcome")
    table.add_column("document")
    table.add_column("pages", justify="right")
    table.add_column("detail")
    for record in result.entries:
        entry = record.entry
        table.add_row(
            str(record.sequence),
            entry.occurred_at.isoformat(),
            entry.operation.value,
            entry.outcome.value,
            entry.doc_name or entry.doc_uuid or _BLANK,
            str(entry.pages_affected),
            entry.detail,
        )
    return table


def _history(invoked: Invoked, /, *, limit: int | None) -> int:
    """Read the recorded history and render it, under its own discriminator.

    Parameters
    ----------
    invoked
        The open invocation.
    limit
        The ``--limit`` value, or ``None``.

    Returns
    -------
    int
        ``0``. Reading history cannot be partially successful: the log either hands back a
        complete page or raises, deliberately, so that a reader can trust the page it holds.
    """
    result = invoked.get(ReportSyncHistory).report(_history_request(limit))
    if invoked.out.mode is OutputMode.JSON:
        invoked.out.emit(
            result.model_dump(mode="json"),
            response_type=HISTORY_RESPONSE_TYPE,
            degradations=result.degradations,
        )
        return 0
    invoked.report(result.degradations)
    invoked.out.display(_history_summary(result))
    if invoked.out.mode is OutputMode.DENSE:
        invoked.out.rows(HISTORY_COLUMNS, _history_rows(result))
    else:
        invoked.out.display(_history_table(result))
    return 0


def _pull(invoked: Invoked, /, *, dry_run: bool) -> int:
    """Pull the library, or report what pulling would do, and score the run.

    Parameters
    ----------
    invoked
        The open invocation.
    dry_run
        Whether ``--dry-run`` was passed.

    Returns
    -------
    int
        :func:`_exit_status`'s answer. The result document is written first either way, so a
        caller of a partial run still learns which document failed.
    """
    result = invoked.get(SyncDocuments).sync(
        SyncDocumentsRequest(synced_at=now(), dry_run=dry_run)
    )
    status = _exit_status(result)
    action = _pull_next_action(result)
    if invoked.out.mode is OutputMode.JSON:
        invoked.out.emit(
            result.model_dump(mode="json"),
            response_type=RESPONSE_TYPES["sync"],
            degradations=result.degradations,
            next_action=action,
        )
        return status
    invoked.report(result.degradations)
    invoked.out.display(_pull_summary(result))
    if len(result.absent) != len(result.forgotten):
        invoked.out.display(_PRUNE_REFUSED)
    if action is not None:
        invoked.out.display(f"next: {action.command}  # {action.purpose}")
    if invoked.out.mode is OutputMode.DENSE:
        invoked.out.rows(DOCUMENT_COLUMNS, _document_rows(result))
    else:
        invoked.out.display(_document_table(result))
    return status


def _perform(invoked: Invoked, /, *, dry_run: bool, history: bool, limit: int | None) -> int:
    """Refuse the flag combinations that cannot mean anything, then dispatch to one use case.

    A module-level function rather than a closure inside :func:`sync`, so a test can drive the
    whole body through :func:`~rmspec.cli._invoke.run` with the shipped in-memory doubles bound
    over the real ports.

    Parameters
    ----------
    invoked
        The open invocation.
    dry_run
        Whether ``--dry-run`` was passed.
    history
        Whether ``--history`` was passed.
    limit
        The ``--limit`` value, or ``None``.

    Returns
    -------
    int
        ``0`` from the history branch, and :func:`_exit_status`'s answer from the pull branch.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        ``--history`` was combined with ``--dry-run``, or ``--limit`` was passed without
        ``--history``. Both are refused rather than resolved: honouring one flag and dropping
        the other is a silent substitution, and this surface reports substitutions rather than
        making them.
    """
    if history and dry_run:
        raise errors.UsageError(
            subject="--history with --dry-run",
            requirement="at most one of them, because history reports what already happened "
            "and a dry run predicts what would happen next",
        )
    if limit is not None and not history:
        raise errors.UsageError(
            subject="--limit without --history",
            requirement="--history, because --limit bounds a page of recorded history and a "
            "pull covers whatever the tablet lists",
        )
    if history:
        return _history(invoked, limit=limit)
    return _pull(invoked, dry_run=dry_run)


def sync(
    *,
    dry_run: DryRunFlag = False,
    history: HistoryFlag = False,
    limit: LimitOption = None,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Pull the tablet's library into the local mirror, predict a pull, or read past pulls.

    With no flag, every document the tablet lists is compared against the mirror and the ones
    whose listing changed are fetched and recorded; documents the tablet no longer lists are
    forgotten, but only when the enumeration was complete enough to be trusted. ``--dry-run``
    performs the same comparison, fetches nothing and writes nothing. ``--history`` ignores the
    tablet entirely and reads back what previous runs recorded.

    Exit status is decided from the result: ``0`` when every document that needed work got it
    or nothing needed doing, and ``1`` when any document failed -- including a run where all of
    them did. The per-document report is emitted first either way, so ``data.documents`` says
    which one failed and why.

    Parameters
    ----------
    dry_run
        Report what a pull would do and write nothing at all -- no transfer, no mirror row, no
        history entry, no deletion. Cannot be combined with ``--history``.
    history
        Read the recorded history of previous runs, newest first, instead of touching the
        tablet. Answers with no tablet attached, and emits the ``history`` response type rather
        than ``sync``.
    limit
        How many history entries to return. Requires ``--history``. Defaults to 20, and a page
        above the use case's ceiling is refused rather than trimmed, so a page shorter than the
        limit asked for always means a short log.
    json
        Emit the one JSON envelope on stdout instead of a table on stderr.
    dense
        Emit tab-separated records on stdout: for a pull, ``kind``, ``uuid``, ``name``,
        ``outcome``, ``changed``, ``pages_changed``, ``detail``, where ``kind`` separates
        listed documents from those absent from the device and those forgotten from the mirror;
        for ``--history``, one record per audit entry.

    Returns
    -------
    int
        ``0``, or ``1`` when a pull did not fully land.
    """
    return run(
        partial(_perform, dry_run=dry_run, history=history, limit=limit),
        json=json,
        dense=dense,
    )
