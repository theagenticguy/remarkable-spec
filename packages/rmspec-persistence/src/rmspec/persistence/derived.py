"""The only place a value is copied out of a payload into a column of its own.

Four columns in the baseline schema duplicate something the JSON payload beside
them already holds: ``document.name_fold``, ``page.page_index`` /
``page_text.page_index``, ``ocr_cache.page_hash`` and ``ocr_cache.created_at_utc``
(and their diagram-cache twins). Each exists because a declared ordering or an
indexed lookup needs it in a column, and each is written by exactly one function
here, from the model, on every insert. The schema-agreement test re-derives every
one of them from its own row's payload, so a duplicated column is checked rather
than trusted -- which is what the legacy schema, with a mirrored column per
field and nothing comparing the two, did not do.

Two things worth knowing about these values.

``name_fold`` is frozen at insert time
--------------------------------------
It is ``str.casefold()`` as the interpreter that wrote the row implemented it. A
Python upgrade changes ``casefold`` for a handful of code points, and a row
written before the upgrade then sorts by the old folding while
``sorted(key=...)`` in a test uses the new one. The agreement test catches it on
the next read of an already-written row; a Python upgrade that changes folding
therefore wants a migration that rewrites the column, and that is the reason the
column is derived by a named function rather than inlined in the INSERT.

``created_at_utc`` is normalised so text order is time order
------------------------------------------------------------
Pydantic serialises an aware datetime with whatever offset it carries, and
lexicographic comparison across mixed offsets is not chronological. This
function converts to UTC first, so the indexed ``created_at_utc < ?`` that
``prune`` runs means what it says. Fractional seconds are safe under the same
comparison: ``+`` sorts before ``.``, so ``…:00+00:00`` precedes
``…:00.5+00:00``, which is the order wanted.

A naive datetime is refused rather than converted
-------------------------------------------------
``datetime.astimezone`` on a naive value silently reads it as the host's local
zone, so the same call would produce a different key -- and, through
``prune``'s ``created_at_utc < ?``, a different destructive cutoff -- on two
machines. Every timestamp that reaches this module from a model has already
passed an ``AwareDatetime`` field; the one that has not is ``prune``'s
``older_than``, which is an ordinary argument. So the guard lives here, at the
single point where an instant becomes a comparable key, rather than in each
caller.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from rmspec.domain.models import SyncedDocument

__all__ = ["name_fold", "utc_key"]


def name_fold(document: SyncedDocument, /) -> str:
    """Return the sort key for ``list_documents``' declared order.

    The port declares ``(visible_name.casefold(), uuid)`` ascending. Storing the
    fold in a column and ordering by it is byte-exactly Python's ``sorted``,
    because SQLite's BINARY collation compares UTF-8 bytes and UTF-8 byte order
    is code-point order. ``COLLATE NOCASE`` would not have matched: it folds
    ASCII only.

    Parameters
    ----------
    document
        The document being recorded.

    Returns
    -------
    str
        ``document.visible_name.casefold()``.
    """
    return document.visible_name.casefold()


def utc_key(moment: datetime, /) -> str:
    """Return a UTC-normalised ISO-8601 string that sorts chronologically.

    Parameters
    ----------
    moment
        An aware datetime, from a cached artifact or from a prune cutoff.

    Returns
    -------
    str
        The same instant expressed in UTC, so lexicographic comparison of two
        of these agrees with comparing the instants.

    Raises
    ------
    ValueError
        ``moment`` is naive. Converting it would mean guessing a zone: on a
        US-Pacific host ``datetime(2026, 1, 1)`` becomes ``2026-01-01T08:00:00+00:00``
        and on a Tokyo host ``2025-12-31T15:00:00+00:00``, so a prune cutoff the
        caller named would move by up to fourteen hours and delete paid output
        the caller meant to keep. The instant is not knowable from the value, so
        it is refused rather than assumed.
    """
    if moment.tzinfo is None:
        msg = f"a store key needs an aware datetime; {moment!r} carries no tzinfo"
        raise ValueError(msg)
    return moment.astimezone(UTC).isoformat()
