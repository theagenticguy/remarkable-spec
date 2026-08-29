"""The SQLite adapter for :class:`~rmspec.domain.ports.persistence.SyncAuditLog`.

Relocates ``SyncDB.log_sync`` (the INSERT moves intact) and
``SyncDB.get_sync_log``, whose ``ORDER BY timestamp DESC LIMIT ?`` becomes
``ORDER BY sequence DESC LIMIT ?`` and whose ``limit: int = 50`` default is gone:
how many entries to show is a display decision, and a non-positive bound is now
rejected before the store is touched.

Two structural changes
----------------------
Its own connection. The log opens a second connection to the same file, so an
append never waits on -- or rolls back with -- the sync store's write
transaction. That is what the port's "an append survives the failure it
describes" requires. It also makes ``SQLITE_BUSY`` reachable for the first time,
which is why every connection carries a busy timeout and every writer opens with
``BEGIN IMMEDIATE``.

Its own sequence counter. Legacy used an ``AUTOINCREMENT`` rowid ordered by
timestamp. Neither half survives: ``sqlite_sequence`` is reset by ``VACUUM INTO``
and by dropping a table, and a plain ``INTEGER PRIMARY KEY`` hands out ``max +
1`` again as soon as a retention trim deletes the highest row -- so the next
append would reuse a retired sequence, and the port's no-reuse promise would
break silently. The sequence comes from ``audit_counter``, which retention never
touches.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import AuditWriteFailedError, PersistenceError
from rmspec.domain.models import RecordedSyncAuditEntry, SyncAuditEntry
from rmspec.persistence._sqlite import dumps, loads

if TYPE_CHECKING:
    from rmspec.persistence._sqlite import SqliteDatabase, StoreConnection

__all__ = ["SqliteSyncAuditLog"]

_LOGGER: Final = logging.getLogger(__name__)


class SqliteSyncAuditLog:
    """Append-only history over ``sync_audit``, sequenced from ``audit_counter``."""

    def __init__(self, database: SqliteDatabase, /) -> None:
        self._conn: StoreConnection = database.connect()
        self._store = database.store

    def append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Append one entry and return it as the store recorded it.

        Parameters
        ----------
        entry
            The operation to record. Failed and partial operations are recorded
            too.

        Returns
        -------
        RecordedSyncAuditEntry
            The appended entry with its store-assigned sequence.

        Raises
        ------
        AuditWriteFailedError
            The entry did not land. Every store failure on this path folds into
            this one error and never into ``StoreUnavailableError``, so the
            caller degrades -- "operation succeeded, history not recorded" --
            instead of failing the work the entry describes.
        """
        try:
            return self._append(entry)
        except PersistenceError as exc:
            _LOGGER.warning("%s.sync_audit append failed: %s", self._store, exc)
            raise AuditWriteFailedError(detail=exc.message) from exc

    def _append(self, entry: SyncAuditEntry, /) -> RecordedSyncAuditEntry:
        """Allocate a sequence and insert one entry, in one transaction.

        Parameters
        ----------
        entry
            The operation to record.

        Returns
        -------
        RecordedSyncAuditEntry
            The appended entry with its sequence.

        Raises
        ------
        AuditWriteFailedError
            The counter row is missing, so no sequence can be allocated without
            risking one that has already been handed out.
        StoreUnavailableError
            The store cannot be written. Translated by :meth:`append`.
        """
        with self._conn.transaction():
            row = self._conn.query_one("SELECT next_sequence FROM audit_counter WHERE id = 1")
            if row is None:
                detail = f"{self._store}.audit_counter has no row, so no sequence can be issued"
                raise AuditWriteFailedError(detail=detail)
            sequence = int(row[0])
            self._conn.execute(
                "UPDATE audit_counter SET next_sequence = ? WHERE id = 1",
                (sequence + 1,),
            )
            self._conn.execute(
                "INSERT INTO sync_audit (sequence, payload) VALUES (?, ?)",
                (sequence, dumps(entry)),
            )
        return RecordedSyncAuditEntry(sequence=sequence, entry=entry)

    def recent(self, *, limit: int) -> list[RecordedSyncAuditEntry]:
        """Return the most recently appended entries, newest first.

        Parameters
        ----------
        limit
            Maximum number of entries to return. Must be at least 1.

        Returns
        -------
        list[RecordedSyncAuditEntry]
            Up to ``limit`` entries, ``sequence`` descending.

        Raises
        ------
        ValueError
            ``limit`` is less than 1. Checked before the connection is touched,
            because a list slice and a SQL ``LIMIT`` disagree about a
            non-positive bound and the caller cannot know which store it holds.
        StoreUnavailableError
            The log cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed. Raised rather than skipped:
            a silently short list cannot answer the one question a reader of an
            append-only log has, which is whether the history is complete.
        """
        if limit < 1:
            msg = f"limit must be at least 1, got {limit}"
            raise ValueError(msg)
        rows = self._conn.query(
            "SELECT sequence, payload FROM sync_audit ORDER BY sequence DESC LIMIT ?",
            (limit,),
        )
        return [
            RecordedSyncAuditEntry(
                sequence=int(sequence),
                entry=loads(
                    SyncAuditEntry,
                    payload,
                    store=self._store,
                    table="sync_audit",
                    key=str(sequence),
                ),
            )
            for sequence, payload in rows
        ]
