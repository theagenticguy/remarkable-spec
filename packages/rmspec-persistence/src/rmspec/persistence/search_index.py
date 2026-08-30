"""The tablet's own handwriting index, read as OCR tier 0.

The reMarkable runs a recognizer over every page it indexes and keeps the result in
an FTS5 database, ``rm-search-index.db``. Reading it costs one file transfer and no
model call, which makes it a free prior in front of the paid tiers -- and a
*degradable* one: every failure here is a named error the caller answers with "no
prior available", never with a failed command.

Why this class is in ``rmspec-persistence`` and the transport is not
-------------------------------------------------------------------
The capability is two packages wide and cannot be one. Reading a SQLite image needs
``sqlite3``, which only this package may import; fetching it off the tablet needs
paramiko, which only ``rmspec-device`` may import. So
:class:`~rmspec.domain.ports.device.SearchIndexSource` hands over ``bytes`` and this
class turns them into
:class:`~rmspec.domain.ports.ocr.IndexedHandwriting`. Nothing here imports
``rmspec.device``, and an architecture test holds that.

Bytes rather than a query interface is not a preference either: firmware 3.27.3.0
ships no ``sqlite3`` binary and no BusyBox applet for one, so querying on the device
is not an available shape.

``PRAGMA quick_check`` is not paranoia
-------------------------------------
reMarkable's own documentation says *"It is possible to copy this directory, but note
that Xochitl should not be running when accessing and/or changing the stored
documents"* -- verbatim, because an earlier version of this paragraph tightened it to
"must not run" and then quoted the tightening. Read off disk while xochitl may be
part-way through writing it, this database is exactly the case that sentence warns
about, and it is xochitl's live index. A torn read is therefore the expected
hazard here rather than an exotic one, and it is the worst-behaved one measured: an
image truncated by a single byte deserializes cleanly, answers
``SELECT ... WHERE pageId = ?`` with a row, and reports nothing wrong. Without the
check this class answers with **another page's handwriting and no error** -- the same
defect class as the digest collision the domain commit just fixed, arriving by a
different road. :func:`~rmspec.persistence._sqlite.deserialized_image` runs the check
and this class refuses anything but ``[("ok",)]``.

Three answers, not two
----------------------
``None`` and ``""`` are different answers and must stay different. ``None`` is "the
tablet has not indexed this page", which is the normal state of any page written
since the last index build -- measured: a notebook written at 21:46 had zero rows in
an index last written at 19:22. ``""`` is "it indexed the page and found nothing",
which two rows of the measured corpus genuinely mean. Collapsing the first onto the
second would let a stale index report a page with ink as blank and suppress the paid
read, which is the free prior deleting the answer it was supposed to precede.

What is memoized, and what is not
---------------------------------
The image is asked for once per instance and then held, ``None`` included, because a
92-page document would otherwise be 92 SSH reads of a 503 KB file. The *connection*
is not held: :class:`~rmspec.domain.ports.ocr.HandwrittenTextIndex` declares no
``close``, an unclosed ``sqlite3.Connection`` raises ``ResourceWarning`` when it is
collected, and this workspace turns that into a failure on whichever unrelated test
is running at the time. So each lookup deserializes the held bytes into a private
database, integrity-checks it, reads its one row, and closes -- about 2 ms per page
on the measured image, and an integrity guarantee that covers every answer rather
than only the first.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import (
    DeviceError,
    StoredRecordUnreadableError,
    StoreSchemaMismatchError,
    StoreUnavailableError,
)
from rmspec.domain.ports.ocr import IndexedHandwriting
from rmspec.persistence._sqlite import deserialized_image

if TYPE_CHECKING:
    from rmspec.domain.ports.device import SearchIndexSource
    from rmspec.persistence._sqlite import StoreConnection

__all__ = ["DeviceSearchIndex"]

_LOGGER: Final = logging.getLogger(__name__)

#: Label every error from this reader carries. Not the file name: the caller never
#: sees a path, because the bytes arrived over a transport this package cannot name.
_STORE: Final = "rm-search-index"

#: The FTS5 table holding one row per indexed page.
_INDEX_TABLE: Final = "search"

#: The single-row table holding the whole index's generation counter.
_GENERATION_TABLE: Final = "generation"

#: What the measured firmware's ``version`` table holds. Reported in a schema
#: mismatch and gated on by nothing -- see :func:`_require_schema`.
_EXPECTED_SCHEMA_VERSION: Final = 9

#: Stands in for ``version.value`` on an image that has no readable one, so a
#: mismatch raised against a database missing the version table still carries a
#: number rather than failing while composing its own message.
_UNKNOWN_SCHEMA_VERSION: Final = 0

#: Every column this reader reads, by table. The gate is stated as columns because a
#: firmware bump that keeps them must keep working; ``generation.value`` is in the
#: list because :class:`~rmspec.domain.ports.ocr.IndexedHandwriting` requires a
#: generation and there is no honest value to invent for one.
_REQUIRED_COLUMNS: Final = {
    _INDEX_TABLE: ("entryId", "pageId", "handwrittenText"),
    _GENERATION_TABLE: ("value",),
}

#: The one row lookup. A bound parameter, never interpolation: ``page_ref`` is a
#: string this package received from a use case and has not parsed.
_ROW_SQL: Final = "SELECT entryId, handwrittenText FROM search WHERE pageId = ?"


def _columns(conn: StoreConnection, table: str, /) -> frozenset[str]:
    """Return the column names ``table`` exposes.

    Uses the ``pragma_table_info`` table-valued function rather than
    ``PRAGMA table_info(...)`` so the table name is a bound parameter and no SQL is
    built by string formatting.

    Parameters
    ----------
    conn
        A connection over the index image.
    table
        Name of the table to describe.

    Returns
    -------
    frozenset[str]
        The column names, empty when ``table`` does not exist -- the pragma reports
        an absent table and an empty one the same way, and for this reader they are
        the same problem.

    Raises
    ------
    StoreUnavailableError
        The image could not be read.
    """
    described = conn.query("SELECT name FROM pragma_table_info(?)", (table,))
    return frozenset(str(row[0]) for row in described)


def _schema_version(conn: StoreConnection, /) -> int:
    """Return the ``version`` table's single value, for a message and nothing else.

    Total on purpose. It is called only while composing a
    ``StoreSchemaMismatchError``, and an image whose columns are wrong is exactly the
    image whose ``version`` table may also be gone; raising from here would replace a
    precise diagnosis with a vague one.

    Parameters
    ----------
    conn
        A connection over the index image.

    Returns
    -------
    int
        The stored version, or :data:`_UNKNOWN_SCHEMA_VERSION` when the table is
        absent, empty, or holds something that is not an integer.
    """
    with contextlib.suppress(StoreUnavailableError, TypeError, ValueError):
        row = conn.query_one("SELECT value FROM version")
        if row is not None:
            return int(row[0])
    return _UNKNOWN_SCHEMA_VERSION


def _require_schema(conn: StoreConnection, /) -> None:
    """Refuse an image that does not expose the columns this reader reads.

    Gated on columns rather than on the ``version`` integer: a firmware bump that
    renumbers the schema while keeping ``entryId``, ``pageId`` and
    ``handwrittenText`` must keep working, and one that keeps the number while moving
    a column must not. The numbers only make the message useful.

    Parameters
    ----------
    conn
        A connection over the index image.

    Raises
    ------
    StoreSchemaMismatchError
        A required column is missing. The missing names are logged, because the
        error's own message can only carry the two version numbers.
    StoreUnavailableError
        The image could not be read.
    """
    for table, required in _REQUIRED_COLUMNS.items():
        present = _columns(conn, table)
        absent = [name for name in required if name not in present]
        if absent:
            _LOGGER.warning(
                "%s.%s does not expose %s, so tier 0 has nothing to read",
                _STORE,
                table,
                ", ".join(absent),
            )
            raise StoreSchemaMismatchError(
                store=_STORE,
                found=_schema_version(conn),
                expected=_EXPECTED_SCHEMA_VERSION,
            )


def _generation(conn: StoreConnection, /) -> object:
    """Return the index's generation counter as the driver reported it.

    One value for the whole database, read once per open and stamped onto the row, so
    a caller can tell two readings came from two snapshots of the index. Returned
    unvalidated: it is checked where the row is, by the model that has to hold it.

    Parameters
    ----------
    conn
        A connection over the index image.

    Returns
    -------
    object
        The stored counter, or ``None`` when the table holds no row.

    Raises
    ------
    StoreUnavailableError
        The image could not be read.
    """
    row = conn.query_one("SELECT value FROM generation")
    return None if row is None else row[0]


class DeviceSearchIndex:
    """The tablet's own search index, read once per command and answered per page.

    The :class:`~rmspec.domain.ports.ocr.HandwrittenTextIndex` binding. REQUEST
    scoped, because it holds one snapshot of a database the tablet is still writing:
    two instances built a minute apart may legitimately disagree, and the
    ``generation`` on every row is how a caller sees that.
    """

    def __init__(self, source: SearchIndexSource, *, revision: str = "1") -> None:
        self._source = source
        self._provider_id = f"device-index@{revision}"
        self._image: bytes | None = None
        self._asked = False
        self._failure: str | None = None

    @property
    def provider_id(self) -> str:
        """Return this index's stable identity slug.

        Returns
        -------
        str
            ``"device-index@<revision>"``. The app folds it into the cache key
            exactly like a recognizer slug, so bumping ``revision`` -- which is what
            a change to what this reader reads or how it reads it calls for --
            mechanically invalidates every row a previous revision wrote.
        """
        return self._provider_id

    def lookup(self, page_ref: str, /) -> IndexedHandwriting | None:
        """Return the index's row for one page, or ``None`` when it has none.

        Parameters
        ----------
        page_ref
            The page uuid, which is the ``.rm`` filename with no translation and is
            what the index stores in ``pageId``.

        Returns
        -------
        IndexedHandwriting | None
            The device's own reading, or ``None`` when the index holds no row for
            this page -- the normal state of a page written since the last index
            build. ``None`` is not ``text=""``: see this module's docstring.

        Raises
        ------
        StoreUnavailableError
            The image could not be fetched or opened, or failed its integrity check.
            A caller treats this as "no prior available" and goes on to the paid
            tiers; it is never a reason to fail the command.
        StoreSchemaMismatchError
            The image opened and does not expose the columns this reader reads.
        StoredRecordUnreadableError
            Two rows share this ``pageId``, or the row cannot be read as an
            :class:`~rmspec.domain.ports.ocr.IndexedHandwriting`. Neither is
            recoverable by guessing: a duplicated page uuid means this reader's
            identity assumption is wrong, and picking a winner is how one page's
            handwriting becomes another's.
        """
        image = self._held_image()
        if image is None:
            return None
        with deserialized_image(image, store=_STORE) as conn:
            _require_schema(conn)
            generation = _generation(conn)
            rows = conn.query(_ROW_SQL, (page_ref,))
        matched = len(rows)
        if matched > 1:
            raise StoredRecordUnreadableError(
                store=_STORE,
                table=_INDEX_TABLE,
                key=page_ref,
                detail=f"{matched} rows share this pageId, so no row identifies this page",
            )
        if matched == 0:
            return None
        entry_ref = rows[0][0]
        reading = rows[0][1]
        try:
            return IndexedHandwriting.model_validate(
                {
                    "page_ref": page_ref,
                    "entry_ref": entry_ref,
                    # A NULL reading is the empty string. Never observed on the
                    # measured device, representable in FTS5 all the same, and
                    # crashing on it would be a worse answer than the one the two
                    # genuinely-blank rows already carry.
                    "text": "" if reading is None else str(reading),
                    "generation": generation,
                },
            )
        except ValueError as exc:
            raise StoredRecordUnreadableError(
                store=_STORE,
                table=_INDEX_TABLE,
                key=page_ref,
                detail=str(exc),
            ) from exc

    def _held_image(self) -> bytes | None:
        """Return the index image, asking the source at most once.

        The memoization covers all three outcomes -- an image, no index at all, and a
        transport that died -- because the caller is a per-page loop and any of the
        three would otherwise be re-asked once per page.

        Returns
        -------
        bytes | None
            The whole image, or ``None`` when the device has no index.

        Raises
        ------
        StoreUnavailableError
            The transport failed. Translated rather than propagated: a
            ``DeviceError`` crossing this port would put a transport this package
            cannot even import into an OCR caller's ``except`` clause, and the port
            promises the caller only store errors. Memoized, so the failure is
            reported once per page from one dead read rather than from 92.
        """
        if not self._asked:
            self._asked = True
            try:
                self._image = self._source.read_index()
            except DeviceError as exc:
                self._failure = str(exc)
        if self._failure is not None:
            raise StoreUnavailableError(store=_STORE, detail=self._failure)
        return self._image
