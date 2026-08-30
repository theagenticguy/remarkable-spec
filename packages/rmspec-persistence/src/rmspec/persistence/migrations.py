"""Versioned schema migrations, and the version comparison the legacy code skipped.

The legacy ``init_schema`` wrote a ``schema_version`` row once, read it back, and
never compared it to ``SCHEMA_VERSION``. Every table was ``CREATE TABLE IF NOT
EXISTS``, so an old shape simply stayed in place and today's statements ran
against yesterday's tables. Deleting the file was the only migration.

What replaces it
----------------
``PRAGMA user_version``, an integer in the database header, is the version. It is
written in the same transaction that applies the migration that earned it, so a
crash halfway leaves the previous version rather than a half shape, and it cannot
grow a second row the way a one-column table can. A file whose version is newer
than this build's raises :class:`StoreSchemaMismatchError` carrying both numbers,
which is the comparison the legacy code did not make.

What the baseline schema deliberately does not have
---------------------------------------------------
No ``AUTOINCREMENT``, anywhere. The legacy ``ocr_cache``, ``diagram_cache`` and
``sync_log`` tables each carried a surrogate ``id`` no domain model could
represent, so it was unqueryable, unassertable and unreturnable. The caches are
keyed by the digest that already identifies them; the audit log's identity is
``RecordedSyncAuditEntry.sequence``, allocated from an explicit counter row --
``sqlite_sequence`` is reset by ``VACUUM INTO`` and by dropping a table, so
``AUTOINCREMENT`` cannot underwrite the port's "a sequence is never reused"
promise.

No per-field columns either. One JSON payload per row, produced by the pydantic
model itself, plus only the columns a declared ordering or an indexed lookup
actually needs -- ``name_fold``, ``page_index``, ``page_hash``,
``created_at_utc``. Each of those is written by exactly one named function in
:mod:`rmspec.persistence.derived`, and the schema-agreement test re-derives every
one of them from its own row's payload. That is the structural cure for SQL
literals hand-mirrored into models: there is no second copy of the field list for
a model to drift from.

``STRICT`` tables, so a column's declared type is enforced. That is what makes it
impossible to store a ``datetime`` object as a payload by accident and quietly
pick up the deprecated stdlib datetime adapter on the way.

The legacy file is not touched
------------------------------
A file at version 0 that still has a legacy table raises
:class:`StoreSchemaMismatchError` rather than being dropped and recreated. Its
``ocr_cache.ocr_text`` is paid Textract and Bedrock output, and
``rmspec.persistence.maintenance.StoreMaintenance.rescue_legacy_page_texts``
reads it out before the user deletes the file. Migrating those rows into the new
cache tables is impossible -- a digest folds ``render_digest``,
``raster_digest``, ``request_digest`` and ``model_fingerprint``, none of which the
legacy row recorded -- and synthesising one would manufacture exactly the
stale-hit-that-looks-valid the new keys exist to prevent.

Refusing is not the same as stranding the user, though it was until this module
started attaching a ``remediation``. This refusal is what **every** holder of a
legacy ``sync.db`` meets on their first run of this build, and it exits
``EX_CONFIG`` -- the environment is wrong, not the request -- so there is always an
action available and the error now names it. Two situations, two sentences:
:data:`LEGACY_REMEDIATION_TEMPLATE` and :data:`NEWER_SCHEMA_REMEDIATION`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import StoreSchemaMismatchError

if TYPE_CHECKING:
    from rmspec.persistence._sqlite import StoreConnection

__all__ = [
    "LEGACY_REMEDIATION_TEMPLATE",
    "LEGACY_TABLES",
    "MIGRATIONS",
    "NEWER_SCHEMA_REMEDIATION",
    "SCHEMA_VERSION",
    "Migration",
    "apply_migrations",
]

_LOGGER: Final = logging.getLogger(__name__)

#: Schema this build speaks. Bumped only together with a new :data:`MIGRATIONS`
#: entry, and also whenever a stored model's field set changes -- see
#: :mod:`rmspec.persistence.payload_schema`, whose pinned fingerprints fail the
#: build until this number and a migration move together.
SCHEMA_VERSION: Final = 1

#: Tables that only a legacy database has. Their presence at ``user_version`` 0
#: is what tells a pre-rewrite file apart from an empty one.
LEGACY_TABLES: Final = frozenset({"schema_version", "documents", "pages", "sync_log"})

#: What a user can do about a file this build refuses to migrate forward, and the
#: reason the refusal is not the end of the story. Every user of the legacy CLI
#: reaches this on their first run of this build, so the sentence is reviewed here
#: rather than assembled inside the ``raise``. Both options are measured: moving the
#: file aside lets ``open`` create a current one, and pointing ``RMSPEC_SYNC_DB`` at a
#: new path leaves the old file in place for
#: ``StoreMaintenance.rescue_legacy_page_texts`` to lift its paid OCR text out of.
#: ``{store}`` is the label the error already carries, which is the file's name --
#: this package never learns the path the user set, by design.
LEGACY_REMEDIATION_TEMPLATE: Final = "move {store} aside, or set RMSPEC_SYNC_DB to a new path"

#: What a user can do about a file a *newer* build wrote. Downgrading the file is not
#: an option this package offers -- ``PRAGMA user_version`` records that a migration
#: ran and nothing here reverses one -- so the two real moves are to run the build
#: that wrote it or to give this one its own file.
NEWER_SCHEMA_REMEDIATION: Final = "upgrade rmspec, or set RMSPEC_SYNC_DB to a new path"


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered, all-or-nothing step from the previous schema to this one.

    Attributes
    ----------
    version
        The ``user_version`` the database carries once this step has been applied.
    description
        What the step does, for the log line it emits.
    statements
        DDL and seed statements, applied in order inside one transaction. No
        ``IF NOT EXISTS``: the version number decides whether a step runs, and a
        conditional DDL is how a stale shape survives a migration.
    """

    version: int
    description: str
    statements: tuple[str, ...]


_BASELINE: Final = Migration(
    version=1,
    description="baseline: mirror, page text, two digest-keyed caches, sequenced audit log",
    statements=(
        # ── The tracked mirror ──────────────────────────────────────────────
        """
        CREATE TABLE document (
            uuid      TEXT PRIMARY KEY,
            name_fold TEXT NOT NULL,
            payload   TEXT NOT NULL
        ) STRICT
        """,
        "CREATE INDEX document_name_order ON document (name_fold, uuid)",
        """
        CREATE TABLE page (
            doc_uuid   TEXT NOT NULL REFERENCES document (uuid) ON DELETE CASCADE,
            page_uuid  TEXT NOT NULL,
            page_index INTEGER NOT NULL,
            payload    TEXT NOT NULL,
            PRIMARY KEY (doc_uuid, page_uuid)
        ) STRICT
        """,
        "CREATE INDEX page_order ON page (doc_uuid, page_index, page_uuid)",
        # The composite foreign key is what makes "text cannot outlive the page
        # it describes" a property of the schema rather than of a code path: a
        # departed page takes its text with it, and a forgotten document takes
        # both. It only fires with PRAGMA foreign_keys ON, which is per
        # connection and therefore verified on every connection this package
        # opens.
        """
        CREATE TABLE page_text (
            doc_uuid   TEXT NOT NULL,
            page_uuid  TEXT NOT NULL,
            page_index INTEGER NOT NULL,
            payload    TEXT NOT NULL,
            PRIMARY KEY (doc_uuid, page_uuid),
            FOREIGN KEY (doc_uuid, page_uuid)
                REFERENCES page (doc_uuid, page_uuid) ON DELETE CASCADE
        ) STRICT
        """,
        "CREATE INDEX page_text_order ON page_text (doc_uuid, page_index, page_uuid)",
        # ── The two paid caches ─────────────────────────────────────────────
        """
        CREATE TABLE ocr_cache (
            digest           TEXT PRIMARY KEY,
            page_hash        TEXT NOT NULL,
            created_at_utc   TEXT NOT NULL,
            key_payload      TEXT NOT NULL,
            artifact_payload TEXT NOT NULL
        ) STRICT
        """,
        "CREATE INDEX ocr_cache_page ON ocr_cache (page_hash, digest)",
        "CREATE INDEX ocr_cache_age ON ocr_cache (created_at_utc)",
        """
        CREATE TABLE diagram_cache (
            digest           TEXT PRIMARY KEY,
            page_hash        TEXT NOT NULL,
            created_at_utc   TEXT NOT NULL,
            key_payload      TEXT NOT NULL,
            artifact_payload TEXT NOT NULL
        ) STRICT
        """,
        "CREATE INDEX diagram_cache_page ON diagram_cache (page_hash, digest)",
        "CREATE INDEX diagram_cache_age ON diagram_cache (created_at_utc)",
        # ── The audit log ───────────────────────────────────────────────────
        # `sequence` is supplied by the writer from audit_counter, never
        # allocated by SQLite: a bare INTEGER PRIMARY KEY hands out max+1 again
        # after a retention trim removes the highest row, which would break the
        # port's no-reuse promise silently.
        """
        CREATE TABLE sync_audit (
            sequence INTEGER PRIMARY KEY,
            payload  TEXT NOT NULL
        ) STRICT
        """,
        """
        CREATE TABLE audit_counter (
            id            INTEGER PRIMARY KEY,
            next_sequence INTEGER NOT NULL,
            CHECK (id = 1)
        ) STRICT
        """,
        "INSERT INTO audit_counter (id, next_sequence) VALUES (1, 1)",
    ),
)

#: Append-only. A new schema adds an entry and bumps :data:`SCHEMA_VERSION`;
#: an existing entry is never edited, because a database in the field has
#: already run it.
MIGRATIONS: Final[tuple[Migration, ...]] = (_BASELINE,)


def _found_version(conn: StoreConnection, /) -> int:
    """Return the database's ``user_version``.

    Parameters
    ----------
    conn
        The connection to read.

    Returns
    -------
    int
        0 for a database this package has never migrated.

    Raises
    ------
    StoreUnavailableError
        The header could not be read.
    """
    row = conn.query_one("PRAGMA user_version")
    return 0 if row is None else int(row[0])


def _legacy_tables_present(conn: StoreConnection, /) -> frozenset[str]:
    """Return which pre-rewrite tables this database still has.

    Parameters
    ----------
    conn
        The connection to inspect.

    Returns
    -------
    frozenset[str]
        The intersection of the file's tables with :data:`LEGACY_TABLES`.

    Raises
    ------
    StoreUnavailableError
        The schema could not be read.
    """
    rows = conn.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    return frozenset(str(row[0]) for row in rows) & LEGACY_TABLES


def apply_migrations(conn: StoreConnection, /) -> int:
    """Bring the database up to :data:`SCHEMA_VERSION`, or refuse to touch it.

    Parameters
    ----------
    conn
        A connection outside any transaction, with its pragmas already verified.

    Returns
    -------
    int
        How many migrations were applied. 0 when the file was already current,
        which is what makes re-opening a no-op.

    Raises
    ------
    StoreSchemaMismatchError
        The file's version is newer than this build's, or the file is a legacy
        database whose paid cache rows must be rescued before it is deleted.
        Deliberately a subclass of ``StoreUnavailableError``, so a caller that
        only cares that the store did not open keeps working, while the CLI can
        map this one to the exit code that means "delete the database".

        Both refusals carry a ``remediation``, and they carry *different* ones:
        :data:`NEWER_SCHEMA_REMEDIATION` for a file this build is too old for, and
        :data:`LEGACY_REMEDIATION_TEMPLATE` for a pre-rewrite file, which is the
        one every user of the legacy CLI meets on their first run. Refusing is
        correct -- migrating a legacy row would have to invent the four digests it
        never recorded -- but a refusal with nothing to do next is an upgrade wall,
        and this is the layer that knows which of the two situations it is in.
    StoreUnavailableError
        A statement failed. The transaction is rolled back, so the previous
        version and shape survive.
    """
    found = _found_version(conn)
    if found > SCHEMA_VERSION:
        raise StoreSchemaMismatchError(
            store=conn.store,
            found=found,
            expected=SCHEMA_VERSION,
            remediation=NEWER_SCHEMA_REMEDIATION,
        )
    if found == 0:
        legacy = _legacy_tables_present(conn)
        if legacy:
            _LOGGER.warning(
                "%s is a pre-rewrite database (tables %s); it will not be migrated",
                conn.store,
                ", ".join(sorted(legacy)),
            )
            raise StoreSchemaMismatchError(
                store=conn.store,
                found=found,
                expected=SCHEMA_VERSION,
                remediation=LEGACY_REMEDIATION_TEMPLATE.format(store=conn.store),
            )
    pending = tuple(step for step in MIGRATIONS if step.version > found)
    for step in pending:
        with conn.transaction():
            for statement in step.statements:
                conn.execute(statement)
            # PRAGMA does not accept a bound parameter, and the value is an int
            # from a module constant rather than anything a caller supplies.
            conn.execute(f"PRAGMA user_version = {step.version:d}")
        _LOGGER.info("applied migration %d to %s: %s", step.version, conn.store, step.description)
    return len(pending)
