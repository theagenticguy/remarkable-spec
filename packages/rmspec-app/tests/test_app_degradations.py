"""The private accumulator every use case reports substitutions through.

Small enough to test exhaustively, and worth testing exhaustively: if this object drops
a record or hands out a live reference to its own list, every use case in the package
under-reports and nothing else in the suite would notice.
"""

from __future__ import annotations

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import Degradation, DegradationKind


def _degradation(subject: str) -> Degradation:
    return Degradation(
        kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
        subject=subject,
        detail="metadata could not be decoded",
    )


def test_a_fresh_log_snapshots_as_an_empty_tuple():
    assert DegradationLog().frozen() == ()


def test_records_are_returned_in_the_order_they_were_recorded():
    log = DegradationLog()
    log.record(_degradation("first"))
    log.record(_degradation("second"))
    assert [entry.subject for entry in log.frozen()] == ["first", "second"]


def test_the_same_degradation_twice_is_two_records():
    """Two facts about two entries are two facts; deduplicating is the CLI's call."""
    log = DegradationLog()
    log.record(_degradation("same"))
    log.record(_degradation("same"))
    assert len(log.frozen()) == 2


def test_frozen_is_a_snapshot_so_a_later_record_cannot_reach_a_returned_result():
    log = DegradationLog()
    log.record(_degradation("first"))
    snapshot = log.frozen()
    log.record(_degradation("second"))
    assert [entry.subject for entry in snapshot] == ["first"]
    assert len(log.frozen()) == 2


def test_the_snapshot_is_the_immutable_type_a_result_model_carries():
    log = DegradationLog()
    log.record(_degradation("only"))
    assert isinstance(log.frozen(), tuple)


def test_every_field_of_a_record_survives_verbatim():
    """The log stores, it does not summarise -- ``substituted`` included."""
    recorded = Degradation(
        kind=DegradationKind.AMBIGUOUS_AUTO_RESOLVED,
        subject="notes",
        detail="two documents matched",
        substituted="d3b38661",
    )
    log = DegradationLog()
    log.record(recorded)
    assert log.frozen() == (recorded,)


def test_a_log_accepts_every_member_of_the_closed_kind_vocabulary():
    """No kind is special-cased here, so a new domain member needs no change to this class."""
    log = DegradationLog()
    for kind in DegradationKind:
        log.record(Degradation(kind=kind, subject="s", detail="d"))
    assert [entry.kind for entry in log.frozen()] == list(DegradationKind)
