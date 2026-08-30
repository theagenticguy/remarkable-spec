"""The bound on ``limit``, the refusal instead of the clamp, and the sequence on the page.

How a ``SyncAuditLog`` is bound here, and why
---------------------------------------------
With a local in-memory fake annotated against the Protocol, for the reasons
``test_app_resolve.py`` sets out at length: ``rmspec.app`` may import ``rmspec.domain`` and
nothing else, these tests hold themselves to the rule their source obeys, and the
architecture check only scans ``src/`` -- so binding ``rmspec-persistence``'s shipped
``InMemorySyncAuditLog`` here would pass the gate while breaking the property the gate
exists to protect, and would make a pure-policy suite need the persistence package
installed to test an integer comparison.

Conformance is still checked, and by the type gate rather than by convention: every
construction below passes ``_InMemoryAuditLog`` to ``ReportSyncHistory(audit=...)``, whose
parameter is annotated ``SyncAuditLog``, so ``ty`` verifies structural conformance at every
call site.

The fake implements both port methods because the Protocol has two, and carries three seams
and nothing more: a ``failure`` the read can be told to raise, because a damaged log must be
provably not degraded into a short page; a ``calls`` counter, because a refused request must
cost no read; and :meth:`_InMemoryAuditLog.forget_oldest`, which is the retention pass the
port permits -- it drops the oldest entry *without renumbering*, which is the only honest way
to produce the sequence gap that proves ``latest_sequence`` is not a count.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from rmspec.app.history import (
    _DEFAULT_LIMIT,
    _LIMIT_CEILING,
    ReportSyncHistory,
    ReportSyncHistoryRequest,
    ReportSyncHistoryResult,
)
from rmspec.domain.errors import (
    RmspecError,
    StoredRecordUnreadableError,
    StoreUnavailableError,
    UsageError,
)
from rmspec.domain.models import (
    RecordedSyncAuditEntry,
    SyncAuditEntry,
    SyncOperation,
    SyncOutcome,
)

if TYPE_CHECKING:
    from rmspec.domain.ports.persistence import SyncAuditLog

WHEN = datetime.datetime(2026, 8, 29, 12, tzinfo=datetime.UTC)


class _InMemoryAuditLog:
    """A :class:`SyncAuditLog` over a list, with a read that can be told to die."""

    def __init__(self, *, failure: RmspecError | None = None) -> None:
        self.calls = 0
        self._recorded: list[RecordedSyncAuditEntry] = []
        self._next = 1
        self._failure = failure

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Allocate the next sequence, which is never reused, and keep the entry."""
        recorded = RecordedSyncAuditEntry(sequence=self._next, entry=entry)
        self._next += 1
        self._recorded.append(recorded)
        return recorded

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the newest entries first, or die as an unreadable store does."""
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        return list(reversed(self._recorded))[:limit]

    def forget_oldest(self) -> None:
        """Drop the oldest entry the way a retention pass does: without renumbering."""
        del self._recorded[0]


def _entry(name: str, *, outcome: SyncOutcome = SyncOutcome.SUCCEEDED) -> SyncAuditEntry:
    return SyncAuditEntry(
        operation=SyncOperation.PULL,
        outcome=outcome,
        doc_uuid=name,
        doc_name=name,
        pages_affected=1,
        detail="" if outcome is SyncOutcome.SUCCEEDED else "something went wrong",
        occurred_at=WHEN,
    )


def _log(*names: str) -> _InMemoryAuditLog:
    log = _InMemoryAuditLog()
    for name in names:
        log.append(_entry(name))
    return log


def _report(log: _InMemoryAuditLog, **kwargs: int) -> ReportSyncHistoryResult:
    return ReportSyncHistory(audit=log).report(ReportSyncHistoryRequest(**kwargs))


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so that frozen-ness is a runtime fact, not a type error.

    The same device ``test_app_resolve.py`` uses, and for the same reason: a direct
    assignment is rejected by the type gate before the test can prove pydantic rejects it at
    runtime, and this repository allows no ``type: ignore`` to get past that.
    """
    setattr(target, field, value)


# ───────────────────────── the ceiling: a refusal, not a clamp ─────────────────────────


def test_a_limit_above_the_ceiling_is_refused():
    with pytest.raises(UsageError) as caught:
        _report(_log("a"), limit=_LIMIT_CEILING + 1)
    assert str(_LIMIT_CEILING) in caught.value.message


def test_the_refusal_names_the_ceiling_as_the_requirement():
    """So the message says what to ask for instead, rather than only what was wrong."""
    with pytest.raises(UsageError) as caught:
        _report(_log("a"), limit=100_000)
    assert caught.value.remediation is not None
    assert f"at most {_LIMIT_CEILING}" in caught.value.remediation


def test_an_oversized_limit_is_never_silently_clamped():
    """The whole point: a clamp and a short log are indistinguishable on the wire."""
    log = _log(*(f"doc {index}" for index in range(3)))
    with pytest.raises(UsageError):
        _report(log, limit=_LIMIT_CEILING + 1)


def test_a_refused_request_costs_no_read():
    log = _log("a")
    with pytest.raises(UsageError):
        _report(log, limit=_LIMIT_CEILING + 1)
    assert log.calls == 0


def test_the_ceiling_itself_is_served():
    """The bound is inclusive, so the largest legal page is not also the first refused one."""
    result = _report(_log("a"), limit=_LIMIT_CEILING)
    assert result.limit == _LIMIT_CEILING
    assert len(result.entries) == 1


def test_the_ceiling_is_policy_rather_than_schema():
    """The oversized request must construct, or the refusal would be a ``ValidationError``."""
    assert ReportSyncHistoryRequest(limit=_LIMIT_CEILING + 1).limit == _LIMIT_CEILING + 1


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_is_unconstructible(limit: int):
    """The port's own precondition, so a pydantic constraint keeps its check unreachable."""
    with pytest.raises(ValidationError):
        ReportSyncHistoryRequest(limit=limit)


def test_the_default_page_is_a_screenful():
    assert ReportSyncHistoryRequest().limit == _DEFAULT_LIMIT


# ───────────────────────────── the page, and its order ─────────────────────────────


def test_the_page_is_newest_first():
    result = _report(_log("first", "second", "third"))
    assert [recorded.entry.doc_name for recorded in result.entries] == [
        "third",
        "second",
        "first",
    ]


def test_the_page_is_what_the_appends_returned_reversed():
    """The port's own contract test, restated where a reader of this use case can see it."""
    log = _InMemoryAuditLog()
    appended = [log.append(_entry(name)) for name in ("a", "b", "c")]
    assert list(_report(log).entries) == list(reversed(appended))


def test_the_limit_truncates_the_oldest_entries():
    result = _report(_log("first", "second", "third"), limit=2)
    assert [recorded.entry.doc_name for recorded in result.entries] == ["third", "second"]


def test_the_limit_is_echoed_so_a_short_page_is_recognisable():
    result = _report(_log("only"), limit=10)
    assert result.limit == 10
    assert len(result.entries) < result.limit


def test_an_empty_log_is_an_empty_page_rather_than_an_error():
    result = _report(_InMemoryAuditLog())
    assert result.entries == ()


def test_one_report_is_one_read():
    log = _log("a")
    _report(log)
    assert log.calls == 1


def test_a_failed_entry_is_reported_rather_than_filtered():
    """A history that hides failures is the history the audit log exists to replace."""
    log = _InMemoryAuditLog()
    log.append(_entry("broken", outcome=SyncOutcome.FAILED))
    (recorded,) = _report(log).entries
    assert recorded.entry.outcome is SyncOutcome.FAILED


# ───────────────────────────── the sequence on the page ─────────────────────────────


def test_the_latest_sequence_is_reported():
    result = _report(_log("a", "b", "c"))
    assert result.latest_sequence == 3


def test_an_empty_log_has_no_latest_sequence():
    assert _report(_InMemoryAuditLog()).latest_sequence is None


def test_the_latest_sequence_is_the_newest_and_not_the_page_size():
    result = _report(_log("a", "b", "c"), limit=1)
    assert len(result.entries) == 1
    assert result.latest_sequence == 3


def test_an_unchanged_latest_sequence_means_nothing_was_appended():
    """The one comparison a caller polling for activity can rely on."""
    log = _log("a")
    seen = _report(log).latest_sequence
    assert _report(log).latest_sequence == seen
    log.append(_entry("b"))
    assert _report(log).latest_sequence != seen


def test_the_latest_sequence_is_not_a_count_of_what_was_ever_written():
    """Retention may drop entries without renumbering, so gaps are legal."""
    log = _log("a", "b", "c")
    log.forget_oldest()
    result = _report(log)
    assert len(result.entries) == 2
    assert result.latest_sequence == 3


# ───────────────────── what it refuses to be, and what it never hides ─────────────────────


def test_the_request_offers_nothing_but_a_limit():
    """No document filter, no date range, no sort option: this is not a query language."""
    assert set(ReportSyncHistoryRequest.model_fields) == {"limit"}


def test_a_damaged_entry_is_not_degraded_into_a_shorter_page():
    failure = StoredRecordUnreadableError(
        store="sync.db", table="sync_audit", key="41", detail="not json"
    )
    with pytest.raises(StoredRecordUnreadableError):
        _report(_InMemoryAuditLog(failure=failure))


def test_an_unreadable_log_propagates():
    failure = StoreUnavailableError(store="sync.db", detail="disk image is malformed")
    with pytest.raises(StoreUnavailableError):
        _report(_InMemoryAuditLog(failure=failure))


def test_a_report_substitutes_nothing():
    assert _report(_log("a")).degradations == ()


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_the_fake_is_the_port_the_use_case_declares():
    audit: SyncAuditLog = _log("a")
    assert audit.recent(limit=1)[0].sequence == 1


def test_a_result_is_frozen():
    result = _report(_log("a"))
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "latest_sequence", 99)


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ReportSyncHistoryResult.model_validate(
            {
                "entries": (),
                "limit": 1,
                "latest_sequence": None,
                "degradations": (),
                "total_entries": 0,
            }
        )
