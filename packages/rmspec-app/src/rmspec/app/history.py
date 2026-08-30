"""Read back the append-only history of what previous runs did, newest first.

This replaces ``sync log`` and the reporting half of ``sync status`` -- the half that
answered "what happened last time", as opposed to the half that predicted the next pull,
which is :class:`~rmspec.app.sync.SyncDocuments` with
:attr:`~rmspec.app.sync.SyncDocumentsRequest.dry_run` set. Splitting it that way is what
keeps the change signal single-sourced: a reporter that also re-derived staleness would be
a second implementation of the prediction, and two implementations of a prediction are how
a status command comes to disagree with the pull it describes.

What this deliberately is not
-----------------------------
**It is not a query language.** One collaborator, one port method,
:meth:`~rmspec.domain.ports.persistence.SyncAuditLog.recent`, and one knob. There is no
document filter, no date range, no operation filter, and no sort option -- not because
those are hard, but because the port declines to offer them and the app layer is not the
place to fake them. ``recent`` returns entries ordered by the store-assigned sequence, and
that order is *total*; a sort option here would have to re-sort by ``occurred_at``, which
is ambiguous three ways the port already documents (entries from one pull share a
timestamp, tests freeze the clock, and each adapter broke the tie differently). A document
filter would mean either pushing a predicate into a port that has none or reading the whole
table and filtering in memory -- and reading the whole table is exactly what the bound below
exists to forbid. When history genuinely needs filtering, the honest change is a filtered
port method with a store-side index, not a loop here.

Why ``limit`` has a ceiling, and why exceeding it raises
-------------------------------------------------------
Legacy's ``sync log --limit`` was unbounded and passed straight through to ``LIMIT ?``, so
``--limit 100000`` dumped the table. That hole is not hypothetical: a neighbouring project
ships the same shape in its MCP tool, where a model passing ``limit: 100000`` pours the
corpus into its own context window -- and that project's *web* endpoint clamps while its
tool does not, which is the asymmetry that makes the bug survive review.

:data:`_LIMIT_CEILING` closes it here, once, for every caller -- terminal, ``--json`` and
tool alike -- because a bound that lives in one front end is a bound the next front end
does not have.

Exceeding it is a :class:`~rmspec.domain.errors.UsageError`, **not** a silent clamp, and the
difference is the only thing that makes the returned page interpretable. Under a clamp, a
caller who asked for 500 and received 100 cannot tell whether the log holds 100 entries or
whether its request was trimmed to 100 -- the two states are identical on the wire and imply
opposite next actions ("that is all the history there is" versus "page again"). Refusing
keeps the invariant that makes :attr:`ReportSyncHistoryResult.entries` readable at all:
**a page shorter than the limit that was asked for is a short log, full stop.** The result
echoes :attr:`ReportSyncHistoryResult.limit` so that inference can be made from the result
alone, without the caller having to remember what it sent.

The lower bound is a ``ValidationError`` rather than a ``UsageError``, and the asymmetry is
deliberate. "At least 1" is the port's own precondition -- ``recent`` documents it and
raises ``ValueError`` for it -- so a pydantic constraint keeps that state unconstructible
and the port's check unreachable. The ceiling is *this layer's* policy, invented here over
a bound the port would happily honour, so it has to be a refusal a caller can read and act
on rather than a schema detail.

Why the sequence is on the result
---------------------------------
:meth:`~rmspec.domain.ports.persistence.SyncAuditLog.append` returns the entry as it landed,
carrying a store-allocated sequence, and the port promises a sequence is never reused for
the life of the store. :attr:`ReportSyncHistoryResult.latest_sequence` reports the highest
one on the page, and it is the only thing that lets a caller tell "nothing has happened
since I last looked" from "I am holding a stale page": two reads whose newest sequence is
equal saw the same history, and no timestamp comparison can promise that.

What it does not promise is contiguity. The port allows gaps so a retention pass may drop
old entries without renumbering, so ``latest_sequence`` is an identity and never a count of
what was ever written. Nothing here prunes, and nothing here paginates by sequence either:
a resume cursor would need a ``before`` argument the port does not have.

Nothing degrades
----------------
:attr:`ReportSyncHistoryResult.degradations` is always empty, and the field is present
because convention 3 requires it of every result model rather than because this reporter is
expected to fill it one day. A reader that substitutes nothing has nothing to report, and
the one failure it could plausibly paper over -- ``StoredRecordUnreadableError`` from a
damaged entry -- must not be papered over: ``recent`` raises rather than skipping precisely
so that the one question a reader of an append-only log has to answer, whether the history
in hand is complete, stays answerable. Catching it here and reporting a shorter list plus a
degradation would put the answer back out of reach, and would let a ``--strict`` run exit 0
over a partly destroyed log. It propagates, and a smaller ``limit`` still recovers the
readable prefix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field

from rmspec.domain.errors import Degradation, UsageError
from rmspec.domain.models import RecordedSyncAuditEntry

if TYPE_CHECKING:
    from rmspec.domain.ports.persistence import SyncAuditLog

__all__ = [
    "ReportSyncHistory",
    "ReportSyncHistoryRequest",
    "ReportSyncHistoryResult",
]

_DEFAULT_LIMIT: Final = 20
"""Entries returned when the caller expresses no preference.

A screenful. Small enough that the common ``rmspec history`` reads like the tail of a log
rather than a dump, and the caller who wants more says so.
"""

_LIMIT_CEILING: Final = 500
"""The largest page this use case will serve.

Chosen against the biggest thing one run can write rather than against a round number: a
pull over a four-hundred-document library appends at most one entry per document touched,
so 500 is the smallest bound under which "show me everything the last full pull did" is
still one page, with headroom. Above that a caller is enumerating the table, which is a
different operation with different costs and no reader on the other end.
"""


class ReportSyncHistoryRequest(BaseModel, frozen=True, extra="forbid"):
    """How much of the history to read. One field, because there is one decision."""

    limit: int = Field(default=_DEFAULT_LIMIT, ge=1)
    """How many entries to return, newest first.

    At least 1, enforced by the schema because that is the port's own precondition. At most
    :data:`_LIMIT_CEILING`, enforced by :meth:`ReportSyncHistory.report` as a
    :class:`~rmspec.domain.errors.UsageError` because that is this layer's policy -- see the
    module docstring for why the two bounds are enforced in different places.
    """


class ReportSyncHistoryResult(BaseModel, frozen=True, extra="forbid"):
    """One page of history, and enough context to know what the page means.

    No field has a default, so a construction site cannot omit the sequence that makes the
    page comparable to the next one.
    """

    entries: tuple[RecordedSyncAuditEntry, ...]
    """The most recently appended entries, newest first, exactly as the log returned them.

    Ordered by ``sequence`` descending, which is total. Shorter than :attr:`limit` means the
    log holds no more than this -- an inference the ceiling being a refusal rather than a
    clamp is what licenses.
    """

    limit: int = Field(ge=1)
    """The number of entries that was asked for, echoed.

    Present so that ``len(entries) < limit`` is checkable from the result alone. A caller
    comparing against the number it sent works only until something else forwards the
    result, which is every ``--json`` consumer.
    """

    latest_sequence: int | None
    """The highest sequence on this page, or ``None`` when the log is empty.

    The value a caller keeps between reads: unchanged means nothing was appended in the
    interval, and that is a promise no timestamp can make. Never a count of entries ever
    written -- the port permits gaps so retention can drop old rows without renumbering.
    """

    degradations: tuple[Degradation, ...]
    """Always empty. Carried because convention 3 requires it of every result model.

    A reader substitutes nothing. The one failure it could hide -- a stored entry that
    cannot be reconstructed -- propagates instead, so a partly destroyed log stays loud.
    """


class ReportSyncHistory:
    """Report what previous runs recorded, newest first, up to a bounded page.

    One collaborator, because there is one thing to read. The log is the only port here: no
    document store, no device, so this use case answers with no tablet attached and costs one
    local read.

    Notes
    -----
    Polling for new activity is two reads and one comparison::

        seen = reporter.report(ReportSyncHistoryRequest()).latest_sequence
        ...
        if reporter.report(ReportSyncHistoryRequest()).latest_sequence == seen:
            ...  # nothing was appended in the interval
    """

    def __init__(self, *, audit: SyncAuditLog) -> None:
        self._audit = audit

    def report(self, request: ReportSyncHistoryRequest, /) -> ReportSyncHistoryResult:
        """Return the most recent history entries, refusing a page too large to be read.

        Parameters
        ----------
        request
            How many entries to return.

        Returns
        -------
        ReportSyncHistoryResult
            The entries newest first, the limit that was asked for, and the highest sequence
            on the page.

        Raises
        ------
        UsageError
            ``request.limit`` exceeds :data:`_LIMIT_CEILING`. Raised before the log is
            touched, and never clamped: a trimmed page is indistinguishable from a short log.
        StoreUnavailableError
            The log cannot be read.
        StoredRecordUnreadableError
            A stored entry cannot be reconstructed. Not degraded into a shorter page -- see
            the module docstring.
        """
        if request.limit > _LIMIT_CEILING:
            raise UsageError(
                subject=f"a page of {request.limit} history entries",
                requirement=(
                    f"at most {_LIMIT_CEILING} entries; a larger page is refused rather than "
                    f"trimmed, so that a page shorter than the one asked for always means a "
                    f"short log"
                ),
            )
        entries = tuple(self._audit.recent(limit=request.limit))
        return ReportSyncHistoryResult(
            entries=entries,
            limit=request.limit,
            latest_sequence=entries[0].sequence if entries else None,
            degradations=(),
        )
