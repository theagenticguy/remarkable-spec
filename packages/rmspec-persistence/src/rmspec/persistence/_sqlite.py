"""Connection ownership, transaction control, and error translation.

This is the only module in the workspace that imports ``sqlite3``, and it is the
only module that names a sqlite3 type. Everything above it -- the port adapters,
the search-index reader, the maintenance class -- speaks :class:`StoreConnection`
and never sees a driver exception, which is what keeps ``rmspec.domain.errors`` the
whole error surface of this package.

Three decisions here are load-bearing, and each replaces a legacy defect.

Explicit transactions, never the driver's implicit ones
-------------------------------------------------------
The connection is opened with ``isolation_level=None``, so the driver never
begins a transaction on our behalf, and every multi-statement write brackets
itself with ``BEGIN IMMEDIATE`` / ``COMMIT`` issued as SQL. The combination that
does *not* work, and that is easy to reach for, is ``autocommit=True`` plus
``Connection.commit()``: in that mode ``commit()`` is a documented no-op, so
``in_transaction`` stays true, the write lock is never released, and the next
``BEGIN`` raises ``cannot start a transaction within a transaction``. ``BEGIN
IMMEDIATE`` rather than a deferred begin because a deferred transaction that
upgrades to a write under WAL can be handed ``SQLITE_BUSY_SNAPSHOT``
immediately, which ``busy_timeout`` does not retry.

Pragmas are verified, not merely issued
---------------------------------------
``PRAGMA journal_mode=WAL`` is a silent no-op inside a transaction, and
``PRAGMA foreign_keys`` is documented as one. Both are per-connection, and the
audit log deliberately opens a second connection, so an unverified pragma block
means a connection that quietly does not cascade -- and the cascade is the only
thing enforcing "text cannot outlive the page it describes". Every connection
this module hands out has had its pragmas read back and compared.

Lazy connect is gone
--------------------
The legacy store connected, set pragmas and ran DDL as a side effect of a
property access, so an unwritable database announced itself from the middle of a
command. :meth:`SqliteDatabase.open` does all of it once, eagerly, and
:meth:`SqliteDatabase.close` closes every connection it handed out -- an unclosed
connection is a ``ResourceWarning`` at garbage-collection time, which this
workspace's ``filterwarnings = ["error"]`` turns into a failure on whichever
unrelated test happens to be running.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Final, Self

from rmspec.domain.errors import StoredRecordUnreadableError, StoreUnavailableError
from rmspec.persistence.migrations import apply_migrations

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from pydantic import BaseModel

__all__ = [
    "MINIMUM_SQLITE_VERSION",
    "SqliteDatabase",
    "StoreConnection",
    "deserialized_image",
    "dumps",
    "loads",
    "open_legacy_readonly",
    "translated",
]

_LOGGER: Final = logging.getLogger(__name__)

#: The oldest ``libsqlite3`` this package's statements run on. 3.24 introduced
#: ``ON CONFLICT ... DO UPDATE`` and 3.37 introduced ``STRICT`` tables, both of
#: which the baseline schema uses. Python 3.13 itself only requires 3.15.2, so
#: the floor is checked rather than assumed: a CI container with an older
#: library must fail at composition with a readable message, not with an
#: ``OperationalError`` from the middle of a pull.
MINIMUM_SQLITE_VERSION: Final = (3, 37, 0)

#: How long a writer waits for a lock held by the other connection before it
#: gives up. New in this rewrite: the legacy store had exactly one connection so
#: ``SQLITE_BUSY`` was unreachable, and the audit log now holds a second.
_BUSY_TIMEOUT_MS: Final = 5_000

#: The sixteen bytes every SQLite database file begins with, trailing NUL included.
#: Checked before opening because a file too short for the driver to recognise is
#: silently overwritten rather than refused.
_SQLITE_MAGIC: Final = b"SQLite format 3\x00"

#: The single row ``PRAGMA quick_check`` returns for a sound database. Anything else
#: is a list of complaints, one row each, and every one of them means the image
#: cannot be trusted to answer a query with its own rows.
_QUICK_CHECK_OK: Final = ("ok",)

#: Row shape. sqlite3 returns ``Any`` per column; the adapters always select an
#: explicit column list, so position is fixed and a dropped column fails in the
#: driver as a schema fault rather than as a silent shift.
type Row = tuple[Any, ...]

#: Parameter shape. Never a ``datetime``: the stdlib's default datetime adapter
#: is deprecated in 3.12+, and a ``DeprecationWarning`` is an error here. Every
#: timestamp reaches the store as text produced by a pydantic model.
type Params = Sequence[str | int | None]


@contextmanager
def translated(store: str) -> Iterator[None]:
    """Re-raise any driver failure as :class:`StoreUnavailableError`.

    ``StoreSchemaMismatchError`` passes through untouched without a clause of its
    own: it is not a ``sqlite3.Error``, so it is never caught here. That matters
    because it is a *subclass* of the error this raises, and it is the one the
    CLI maps to a different exit code -- the one that means "delete the database
    and re-sync".

    Parameters
    ----------
    store
        Label naming the database, carried into the raised error.

    Yields
    ------
    None
        Control returns to the guarded block.

    Raises
    ------
    StoreUnavailableError
        The guarded block raised ``sqlite3.Error`` or ``OSError``.
    """
    try:
        yield
    except (sqlite3.Error, OSError) as exc:
        raise StoreUnavailableError(store=store, detail=str(exc)) from exc


def dumps(model: BaseModel, /) -> str:
    """Serialize a domain model to the text stored in a payload column.

    One function, used by every writer, so there is no second place a field list
    could be spelled out and drift from the model -- which is the defect this
    package exists to remove.

    Parameters
    ----------
    model
        Any frozen domain model destined for a payload column.

    Returns
    -------
    str
        Compact JSON, with timestamps in pydantic's ISO-8601 form.
    """
    return model.model_dump_json()


def loads[M: BaseModel](
    model_type: type[M],
    raw: object,
    /,
    *,
    store: str,
    table: str,
    key: str,
) -> M:
    """Reconstruct a domain model from a payload column, or fail loudly.

    Parameters
    ----------
    model_type
        The model the payload is expected to reconstruct as.
    raw
        The payload column's value, as the driver returned it.
    store
        Label naming the database.
    table
        Table the row came from.
    key
        Primary key of the row, for the error message.

    Returns
    -------
    M
        The reconstructed model.

    Raises
    ------
    StoredRecordUnreadableError
        The payload is not text, is not JSON, or does not validate.
        ``pydantic.ValidationError`` is a ``ValueError``, so both a malformed
        document and a missing field arrive here rather than escaping raw the way
        the legacy row mappers let them.
    """
    try:
        if not isinstance(raw, str):
            msg = f"payload column holds {type(raw).__name__}, not text"
            raise TypeError(msg)  # noqa: TRY301
        return model_type.model_validate_json(raw)
    except (ValueError, TypeError) as exc:
        raise StoredRecordUnreadableError(
            store=store,
            table=table,
            key=key,
            detail=str(exc),
        ) from exc


class StoreConnection:
    """One sqlite3 connection, with explicit transactions and translated errors.

    Every method funnels through :func:`translated`, so a caller sees only
    ``rmspec.domain.errors`` types. Rows come back as plain tuples against an
    explicit column list rather than as ``sqlite3.Row``: name-indexing a
    ``sqlite3.Row`` raises ``IndexError`` for a column that is not there, which
    is exactly the builtin the legacy mappers let escape to the CLI.
    """

    def __init__(self, raw: sqlite3.Connection, /, *, store: str) -> None:
        self._raw = raw
        self._store = store

    @property
    def store(self) -> str:
        """Label naming the database this connection speaks to.

        Returns
        -------
        str
            The label every error raised from this connection carries.
        """
        return self._store

    @property
    def in_transaction(self) -> bool:
        """Whether a transaction is currently open on this connection.

        Returns
        -------
        bool
            True between ``BEGIN`` and ``COMMIT``. Asserted after every public
            write in the test suite, because a leaked transaction holds the
            write lock for the life of the process.
        """
        return self._raw.in_transaction

    def query(self, sql: str, params: Params = (), /) -> list[Row]:
        """Run a SELECT and return every row.

        Parameters
        ----------
        sql
            The statement, with ``?`` placeholders.
        params
            Bound parameters.

        Returns
        -------
        list[Row]
            Rows in the statement's declared order.

        Raises
        ------
        StoreUnavailableError
            The store could not be read.
        """
        with translated(self._store):
            return self._raw.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Params = (), /) -> Row | None:
        """Run a SELECT and return its first row, or ``None``.

        Parameters
        ----------
        sql
            The statement, with ``?`` placeholders.
        params
            Bound parameters.

        Returns
        -------
        Row | None
            The first row, or ``None`` when the statement matched nothing.

        Raises
        ------
        StoreUnavailableError
            The store could not be read.
        """
        with translated(self._store):
            return self._raw.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Params = (), /) -> int:
        """Run one statement and return how many rows it changed.

        Parameters
        ----------
        sql
            The statement, with ``?`` placeholders.
        params
            Bound parameters.

        Returns
        -------
        int
            ``Cursor.rowcount``: rows inserted, updated or deleted.

        Raises
        ------
        StoreUnavailableError
            The store could not be written.
        """
        with translated(self._store):
            return self._raw.execute(sql, params).rowcount

    def execute_many(self, sql: str, rows: Sequence[Params], /) -> None:
        """Run one statement once per parameter tuple.

        Parameters
        ----------
        sql
            The statement, with ``?`` placeholders.
        rows
            One parameter tuple per execution.

        Raises
        ------
        StoreUnavailableError
            The store could not be written.
        """
        with translated(self._store):
            self._raw.executemany(sql, rows)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Bracket a block in ``BEGIN IMMEDIATE`` / ``COMMIT``.

        ``IMMEDIATE`` takes the write lock up front, so a WAL reader that later
        upgrades cannot be handed an unretryable ``SQLITE_BUSY_SNAPSHOT``. The
        rollback is guarded on :attr:`in_transaction`, because ``ROLLBACK`` with
        nothing active raises and would replace the real failure with a bogus
        one.

        Yields
        ------
        None
            Control returns to the guarded block.

        Raises
        ------
        StoreUnavailableError
            The transaction could not be started or committed.
        """
        with translated(self._store):
            self._raw.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if self._raw.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    self._raw.execute("ROLLBACK")
            raise
        with translated(self._store):
            self._raw.execute("COMMIT")

    def probe_write(self) -> None:
        """Take and release the write lock, changing nothing.

        What distinguishes a readable file on a read-only mount from a usable
        store. Used by :meth:`SqliteDatabase.verify` so the failure lands at
        container composition rather than mid-command.

        Raises
        ------
        StoreUnavailableError
            The write lock could not be taken.
        """
        with translated(self._store):
            self._raw.execute("BEGIN IMMEDIATE")
            self._raw.execute("ROLLBACK")

    def close(self) -> None:
        """Close the connection, swallowing a driver complaint about doing so.

        Idempotent, and safe to call on a connection that is already closed, so
        a fixture's teardown never depends on what the test did.
        """
        with contextlib.suppress(sqlite3.Error):
            self._raw.close()


def _configure(raw: sqlite3.Connection, /, *, store: str) -> None:
    """Apply and then verify the per-connection pragmas.

    Parameters
    ----------
    raw
        A freshly opened connection, outside any transaction.
    store
        Label naming the database.

    Raises
    ------
    StoreUnavailableError
        A pragma did not take effect. Read back rather than assumed: both WAL
        and foreign-key enforcement are silent no-ops inside a transaction, and
        both are per-connection.
    """
    with translated(store):
        journal = raw.execute("PRAGMA journal_mode=WAL").fetchone()
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        foreign_keys = raw.execute("PRAGMA foreign_keys").fetchone()
        busy_timeout = raw.execute("PRAGMA busy_timeout").fetchone()
    observed = {
        "journal_mode": None if journal is None else journal[0],
        "foreign_keys": None if foreign_keys is None else foreign_keys[0],
        "busy_timeout": None if busy_timeout is None else busy_timeout[0],
    }
    expected = {"journal_mode": "wal", "foreign_keys": 1, "busy_timeout": _BUSY_TIMEOUT_MS}
    if observed != expected:
        raise StoreUnavailableError(
            store=store,
            detail=f"pragmas did not take effect: wanted {expected}, got {observed}",
        )


def _reject_non_database(path: Path, /, *, store: str) -> None:
    """Refuse a path that exists, holds bytes, and is not a SQLite database.

    The driver does not refuse all of them. A file of one to fifteen bytes is
    *adopted*: SQLite treats it as empty, writes a fresh header over it and
    creates the schema, so whatever those bytes were is gone. Sixteen bytes or
    more of non-database fails on its own with "file is not a database", and a
    genuinely empty file is the normal first-run case. Checking the magic here
    keeps :meth:`SqliteDatabase.open`'s promise -- everything that can be wrong
    with a database file is wrong at composition -- from having a hole in it that
    destroys a user's file.

    Parameters
    ----------
    path
        The database file, which need not exist yet.
    store
        Label naming the database.

    Raises
    ------
    StoreUnavailableError
        The path exists, is not empty, and does not begin with the SQLite magic;
        or its first bytes cannot be read.
    """
    with translated(store):
        head = b""
        if path.is_file():
            with path.open("rb") as handle:
                head = handle.read(len(_SQLITE_MAGIC))
    if head and head != _SQLITE_MAGIC:
        raise StoreUnavailableError(
            store=store,
            detail=f"{path} exists and is not a SQLite database",
        )


class SqliteDatabase:
    """An open SQLite file, its pragmas verified and its schema migrated.

    The opaque handle the four adapters take. It exists so no adapter
    constructor, and therefore no provider signature in ``rmspec-cli``, ever
    names ``sqlite3.Connection`` -- which the architecture suite and ruff's
    banned-api rule both forbid outside this package.

    The store label every error carries is derived from the file name once, here,
    rather than chosen by each adapter: four adapters naming themselves would
    produce four different ``store`` strings for one broken database.
    """

    def __init__(self, path: Path, /, *, primary: StoreConnection) -> None:
        self._path = path
        self._primary = primary
        self._extra: list[StoreConnection] = []

    @classmethod
    def open(cls, path: Path, /) -> Self:
        """Open, configure and migrate the database at ``path``.

        Everything that can fail about a database file fails here, at container
        composition, rather than at a call site: a non-empty file is checked for
        the SQLite magic, the parent directory is created, the connection is made,
        the pragmas are verified, the migrations run, and the write lock is taken
        once as a probe.

        Parameters
        ----------
        path
            The database file. Parent directories are created.

        Returns
        -------
        SqliteDatabase
            An open, migrated handle. The caller owns it and must
            :meth:`close` it.

        Raises
        ------
        StoreUnavailableError
            ``libsqlite3`` is older than :data:`MINIMUM_SQLITE_VERSION`, the path
            holds bytes that are not a SQLite database, the parent directory
            cannot be created, the file cannot be opened or written, or a pragma
            did not take effect.
        StoreSchemaMismatchError
            The file's schema is newer than this build's, or it is a legacy
            database.
        """
        store = path.name
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
            wanted = ".".join(str(part) for part in MINIMUM_SQLITE_VERSION)
            raise StoreUnavailableError(
                store=store,
                detail=f"libsqlite3 {sqlite3.sqlite_version} is older than {wanted}",
            )
        _reject_non_database(path, store=store)
        with translated(store):
            path.parent.mkdir(parents=True, exist_ok=True)
        primary = _connect(path, store=store)
        database = cls(path, primary=primary)
        try:
            apply_migrations(primary)
            database.verify()
        except BaseException:
            database.close()
            raise
        return database

    @property
    def path(self) -> Path:
        """Filesystem path of the database file.

        Returns
        -------
        Path
            The path this handle was opened on.
        """
        return self._path

    @property
    def store(self) -> str:
        """Label naming this database in every error raised against it.

        Returns
        -------
        str
            The database file's name.
        """
        return self._path.name

    @property
    def primary(self) -> StoreConnection:
        """The connection the sync store, caches and maintenance share.

        Returns
        -------
        StoreConnection
            The connection opened by :meth:`open`.
        """
        return self._primary

    def connect(self) -> StoreConnection:
        """Open an additional connection with the identical pragma block.

        The audit log takes one of these, so an append never waits on, or rolls
        back with, the sync store's write transaction. One function rather than a
        second connect site, because ``foreign_keys`` is per-connection and a
        connection that forgets it silently stops cascading.

        Returns
        -------
        StoreConnection
            A new connection to the same file. Closed by :meth:`close`.

        Raises
        ------
        StoreUnavailableError
            The file could not be opened, or a pragma did not take effect.
        """
        extra = _connect(self._path, store=self.store)
        self._extra.append(extra)
        return extra

    def verify(self) -> None:
        """Prove the database is readable and writable, changing nothing.

        Takes and releases the write lock, which is what distinguishes a
        readable file on a read-only mount from a usable store. Called by
        :meth:`open` so the failure lands at composition.

        Raises
        ------
        StoreUnavailableError
            The probe could not read the schema or take the write lock.
        """
        self._primary.query_one("SELECT count(*) FROM sqlite_master")
        self._primary.probe_write()

    def close(self) -> None:
        """Close every connection this handle opened.

        Idempotent. An unclosed connection surfaces as a ``ResourceWarning`` at
        collection time, which this workspace escalates to an error on whichever
        test happens to be running -- so closing is a correctness requirement
        here, not tidiness.
        """
        for extra in self._extra:
            extra.close()
        self._extra.clear()
        self._primary.close()


def _connect(path: Path, /, *, store: str) -> StoreConnection:
    """Open one connection to ``path`` and configure it.

    Parameters
    ----------
    path
        The database file.
    store
        Label naming the database.

    Returns
    -------
    StoreConnection
        The configured connection.

    Raises
    ------
    StoreUnavailableError
        The file could not be opened, or a pragma did not take effect.
    """
    with translated(store):
        raw = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
        )
    try:
        _configure(raw, store=store)
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            raw.close()
        raise
    return StoreConnection(raw, store=store)


def open_legacy_readonly(path: Path, /) -> StoreConnection:
    """Open a legacy ``sync.db`` read-only, for the one-way rescue read.

    Read-only and pragma-free on purpose: the file belongs to a schema this
    build does not speak, so nothing may write to it, migrate it, or take a lock
    on it. The rescue in :mod:`rmspec.persistence.maintenance` uses this to lift
    paid OCR text out of a legacy database before the user deletes it.

    Parameters
    ----------
    path
        The legacy database file.

    Returns
    -------
    StoreConnection
        A read-only connection. The caller must close it.

    Raises
    ------
    StoreUnavailableError
        The file does not exist or cannot be opened read-only.
    """
    store = path.name
    if not path.is_file():
        raise StoreUnavailableError(store=store, detail=f"{path} is not a file")
    # Path.as_uri percent-encodes, which f"file:{path}" does not. An unescaped `?`
    # in the path ends it and turns the rest into a query key SQLite does not
    # recognise: `file:/tmp/we?ird.db?mode=ro` opens `/tmp/we`, creating it, and
    # does NOT honour mode=ro -- so the promise of this function ("nothing may
    # write to it") failed on exactly the path shape that reports "no such table:
    # pages" and invites the user to delete the real database. as_uri needs an
    # absolute path; the caller's `path` is whatever the CLI was handed.
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with translated(store):
        raw = sqlite3.connect(uri, isolation_level=None, uri=True, check_same_thread=False)
    _LOGGER.debug("opened legacy database %s read-only", path)
    return StoreConnection(raw, store=store)


@contextmanager
def deserialized_image(image: bytes, /, *, store: str) -> Iterator[StoreConnection]:
    """Open a database that arrived as bytes, integrity-check it, and close it after.

    The second way into :class:`StoreConnection`, for a database with no path: the
    tablet's own search index is copied off the device whole and read here, because
    the device has no ``sqlite3`` binary to query it with. The bytes are copied into
    a private in-memory database, so nothing done through the connection can reach
    the caller's ``bytes`` object or the device.

    It cannot reuse :func:`_connect`, and not for want of trying: ``PRAGMA
    journal_mode=WAL`` reports ``memory`` on an in-memory database, so
    :func:`_configure`'s read-back would reject every image. None of those three
    pragmas means anything here anyway -- nothing writes, so there is no lock to wait
    for and no cascade to enforce.

    ``PRAGMA quick_check`` is mandatory rather than defensive, and this is the one
    place it runs. Measured on CPython 3.13 with SQLite 3.50.4: an image truncated by
    a single byte deserializes cleanly, answers ``SELECT`` with a row, and fails
    ``quick_check`` -- so a reader that skips the check answers with rows it cannot
    vouch for and raises nothing. ``MemoryError`` is caught by name because that, and
    not ``sqlite3.DatabaseError``, is what an empty image raises.

    Parameters
    ----------
    image
        The whole database image, header included.
    store
        Label naming the image, carried into every error raised against it.

    Yields
    ------
    StoreConnection
        A connection over a private copy of ``image``, closed on exit.

    Raises
    ------
    StoreUnavailableError
        The image is empty, is not a database, is truncated, or failed
        ``PRAGMA quick_check``. Also raised when the driver was built without
        deserialisation support, which arrives as ``sqlite3.NotSupportedError``.
    """
    with translated(store):
        raw = sqlite3.connect(":memory:", isolation_level=None)
    conn = StoreConnection(raw, store=store)
    try:
        try:
            with translated(store):
                raw.deserialize(image)
        except MemoryError as exc:
            raise StoreUnavailableError(
                store=store,
                detail=f"cannot open a {len(image)}-byte image: {exc!r}",
            ) from exc
        verdict = conn.query("PRAGMA quick_check")
        if verdict != [_QUICK_CHECK_OK]:
            complaints = "; ".join(str(row[0]) for row in verdict)
            raise StoreUnavailableError(
                store=store,
                detail=f"integrity check reported {complaints}",
            )
        yield conn
    finally:
        conn.close()
