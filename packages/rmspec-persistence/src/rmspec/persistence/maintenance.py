"""Cache enumeration, retention, and the two one-way imports. No port.

The persistence ports deliberately have no ``evict`` and no ``prune``: cache
enumeration and eviction are maintenance, not use cases, and an ``evict()`` whose
empty filters mean "everything" turns a computed-empty selection into the loss of
paid Textract and Bedrock output. So they live here, on a class the CLI
constructs directly -- the CLI being the only package allowed to import an
adapter.

Every destructive method requires an explicit bound and raises ``ValueError``
without one. That is asserted by signature inspection in the test suite as well
as by behaviour, so a wipe-everything default cannot come back as a keyword
default nobody notices.

The bound's *correctness* is checked too, not merely its presence. ``older_than``
is the one timestamp in this package that never passes an ``AwareDatetime``
field, and ``datetime.astimezone`` on a naive value reads the host's local zone:
the same ``prune_ocr(older_than=datetime(2026, 1, 1, 11, 59))`` deleted nothing
under ``TZ=UTC`` and destroyed a 12:00Z entry under ``TZ=America/Los_Angeles``,
because the cutoff silently became 19:59Z. A bound whose meaning depends on
``$TZ`` is not an explicit bound, so a naive ``older_than`` is refused --
:func:`rmspec.persistence.derived.utc_key` refuses it for every future caller as
well, and this module names the table in the message before any SQL runs.

``page_hashes`` names content, not a page. Two pages whose scenes are
byte-identical share one hash, and 62 of the reference corpus's 92 ``.rm`` files
are zero-byte stubs that all hash to :data:`EMPTY_SCENE_DIGEST` -- so pruning
that one hash prunes every stub in the library, across documents. The methods say
so, because the caller who typed one hash cannot otherwise know how many pages it
covers.

Two imports, both one-way
-------------------------
:meth:`StoreMaintenance.import_ocr_sidecars` replaces ``migrate_ocr_sidecars``,
which could never have run: it derived ``{uuid}.ocr.rm`` from ``{uuid}.ocr.txt``
with ``Path.with_suffix`` and so always returned 0, and its bare ``except
Exception: continue`` swallowed every failure. The replacement strips the whole
``.ocr.txt`` suffix and writes through ``DocumentSyncStore.record_page_text``, so
the model validates each import exactly once.

:meth:`StoreMaintenance.rescue_legacy_page_texts` is new. A legacy database is
refused by the migration rather than dropped, because its ``ocr_cache.ocr_text``
is paid output -- and it *is* rescuable, contrary to the obvious reading: legacy
``pages.rm_hash`` joins legacy ``ocr_cache.rm_hash``, which yields a
``(doc_uuid, page_uuid, text)`` triple that is a valid ``PageText``. What is not
rescuable is a cache *entry*: a digest folds ``render_digest``,
``raster_digest``, ``request_digest`` and ``model_fingerprint``, none of which a
legacy row recorded, and synthesising one would manufacture exactly the
stale-hit-that-looks-valid the new keys exist to prevent. So the text is rescued
as page text and the caches start cold.

That triple is only valid when the legacy hash identifies *one* page, which is
the qualification the join cannot express and the corpus does not grant. Legacy
hashed the ``.rm`` file, 62 of the corpus's 92 are zero bytes, and every one of
them therefore carries :data:`EMPTY_SCENE_DIGEST`: one legacy cache row for that
digest joins every stub page in the library, in every document, and each match
looks like a different page so no ``(doc_uuid, page_uuid)`` dedup can see it. One
row would become 62 confident, durable, wrong ``PageText`` records -- and unlike a
cache entry a ``PageText`` cannot be evicted. So the rescue keeps only text whose
hash matches exactly one page, refuses a page with no ink outright, and logs each
hash it declined together with how many pages it matched, so the user learns the
legacy cache was ambiguous instead of silently receiving another document's text.

Hashing does not happen here. The fingerprint that keys everything is
``DocumentRepository.page_fingerprint`` in ``rmspec-formats``; a second SHA-256
implementation in this package would be a second definition of the cache
invalidation key, which is the class of drift this rewrite exists to remove.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from rmspec.domain.models import (
    DiagramCacheKey,
    OcrCacheKey,
    PageText,
    TextProvenance,
)
from rmspec.persistence._sqlite import loads, open_legacy_readonly
from rmspec.persistence.derived import utc_key

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from rmspec.domain.ports.persistence import DocumentSyncStore
    from rmspec.persistence._sqlite import Params, SqliteDatabase, StoreConnection

__all__ = ["EMPTY_SCENE_DIGEST", "SIDECAR_SUFFIX", "StoreCounts", "StoreMaintenance"]

_LOGGER: Final = logging.getLogger(__name__)

#: The legacy sidecar's full suffix. Two dots, which is why ``Path.with_suffix``
#: was the wrong tool and ``Path.stem`` is not the page uuid either.
SIDECAR_SUFFIX: Final = ".ocr.txt"

#: SHA-256 of no bytes: the ``rm_hash`` legacy recorded for every zero-byte ``.rm``
#: file, which is 62 of the reference corpus's 92. Written out rather than computed
#: because this module hashes nothing -- the one fingerprint definition lives in
#: ``rmspec-formats`` -- and pinned against ``hashlib`` in the test suite so the
#: literal cannot rot.
EMPTY_SCENE_DIGEST: Final = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

#: Legacy join that lifts paid OCR text out of a pre-rewrite database, carrying the
#: two facts that decide whether a match may be believed: how many pages share the
#: matched hash, and whether the page held any ink. ``engine`` is in the ordering so
#: that a page with one row per engine rescues deterministically rather than by
#: whichever row the planner reached first. The cardinality is counted in SQL and
#: judged in Python, so the rescue can report what it declined rather than silently
#: returning fewer rows.
_LEGACY_RESCUE: Final = """
SELECT p.doc_uuid, p.page_uuid, p.rm_hash, p.rm_size_bytes, o.ocr_text,
       (SELECT count(*) FROM pages AS q WHERE q.rm_hash = p.rm_hash) AS pages_sharing
FROM pages AS p JOIN ocr_cache AS o ON o.rm_hash = p.rm_hash
ORDER BY p.doc_uuid, p.page_index, p.page_uuid, o.engine
"""


@dataclass(frozen=True, slots=True)
class StoreCounts:
    """How many rows each table holds, for ``rmspec cache status``.

    Attributes
    ----------
    documents
        Rows in the document mirror.
    pages
        Rows in the page mirror.
    page_texts
        Pages with recorded text.
    ocr_entries
        Cached transcriptions.
    diagram_entries
        Cached diagram extractions.
    audit_entries
        Retained history entries. Not the number ever written -- the sequence is
        allowed to have gaps, and no reader may treat it as a count.
    """

    documents: int
    pages: int
    page_texts: int
    ocr_entries: int
    diagram_entries: int
    audit_entries: int


class StoreMaintenance:
    """Enumeration, retention and one-way imports over an open database."""

    def __init__(self, database: SqliteDatabase, /) -> None:
        self._conn: StoreConnection = database.primary
        self._store = database.store

    def counts(self) -> StoreCounts:
        """Return the row count of every table.

        Returns
        -------
        StoreCounts
            One count per table.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        """
        return StoreCounts(
            documents=self._count("document"),
            pages=self._count("page"),
            page_texts=self._count("page_text"),
            ocr_entries=self._count("ocr_cache"),
            diagram_entries=self._count("diagram_cache"),
            audit_entries=self._count("sync_audit"),
        )

    def _count(self, table: str, /) -> int:
        """Return one table's row count.

        Parameters
        ----------
        table
            Table name, always a literal from this module.

        Returns
        -------
        int
            The number of rows.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        """
        # S608 is justified here: `table` is a literal supplied by `counts` above.
        row = self._conn.query_one(f"SELECT count(*) FROM {table}")  # noqa: S608
        return 0 if row is None else int(row[0])

    def ocr_keys(self, *, page_hash: str | None = None) -> list[OcrCacheKey]:
        """Return the stored OCR cache keys, optionally for one page only.

        This is the "why did this miss" answer: the stored provenance, with no
        artifact attached, so enumeration can never become a fallback that serves
        output produced under inputs nobody asked for.

        Parameters
        ----------
        page_hash
            Restrict to keys for one page's scene bytes, or ``None`` for all.

        Returns
        -------
        list[OcrCacheKey]
            Stored keys, ``digest`` ascending.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored key payload cannot be reconstructed.
        """
        return [
            loads(OcrCacheKey, payload, store=self._store, table="ocr_cache", key=str(digest))
            for digest, payload in self._key_rows("ocr_cache", page_hash)
        ]

    def diagram_keys(self, *, page_hash: str | None = None) -> list[DiagramCacheKey]:
        """Return the stored diagram cache keys, optionally for one page only.

        Parameters
        ----------
        page_hash
            Restrict to keys for one page's scene bytes, or ``None`` for all.

        Returns
        -------
        list[DiagramCacheKey]
            Stored keys, ``digest`` ascending.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored key payload cannot be reconstructed.
        """
        return [
            loads(
                DiagramCacheKey,
                payload,
                store=self._store,
                table="diagram_cache",
                key=str(digest),
            )
            for digest, payload in self._key_rows("diagram_cache", page_hash)
        ]

    def _key_rows(self, table: str, page_hash: str | None, /) -> list[tuple[object, object]]:
        """Return ``(digest, key_payload)`` rows from one cache table.

        Parameters
        ----------
        table
            Cache table name, always a literal from this module.
        page_hash
            Restrict to one page, or ``None`` for all.

        Returns
        -------
        list[tuple[object, object]]
            Rows, ``digest`` ascending.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        """
        # S608 is justified on both: `table` is a literal supplied by the two public
        # enumerators above.
        if page_hash is None:
            rows = self._conn.query(
                f"SELECT digest, key_payload FROM {table} ORDER BY digest",  # noqa: S608
            )
        else:
            rows = self._conn.query(
                f"SELECT digest, key_payload FROM {table} "  # noqa: S608
                "WHERE page_hash = ? ORDER BY digest",
                (page_hash,),
            )
        return [(row[0], row[1]) for row in rows]

    def prune_ocr(
        self,
        *,
        older_than: datetime | None = None,
        page_hashes: Sequence[str] | None = None,
    ) -> int:
        """Delete OCR cache entries matching an explicit bound.

        Parameters
        ----------
        older_than
            Delete entries created strictly before this instant. Must be aware.
            Compared against the indexed ``created_at_utc`` column, which is
            derived from the artifact's own ``created_at``.
        page_hashes
            Delete entries whose page hash is one of these. A hash names page
            *content*, so one hash covers every page whose scene bytes are
            identical -- including all of a PDF-backed document's zero-byte
            stubs, in every document, which all hash to
            :data:`EMPTY_SCENE_DIGEST`.

        Returns
        -------
        int
            How many entries were deleted.

        Raises
        ------
        ValueError
            Neither bound was given, ``page_hashes`` was given and empty, or
            ``older_than`` is naive. A computed-empty selection must never mean
            "everything", and a cutoff whose instant depends on the host's zone
            is not the bound the caller named: these rows are paid Textract and
            Bedrock output.
        StoreUnavailableError
            The store cannot be written.
        """
        return self._prune("ocr_cache", older_than=older_than, page_hashes=page_hashes)

    def prune_diagram(
        self,
        *,
        older_than: datetime | None = None,
        page_hashes: Sequence[str] | None = None,
    ) -> int:
        """Delete diagram cache entries matching an explicit bound.

        Parameters
        ----------
        older_than
            Delete entries created strictly before this instant. Must be aware.
        page_hashes
            Delete entries whose page hash is one of these. A hash names page
            content, so one hash covers every page whose scene bytes are
            identical.

        Returns
        -------
        int
            How many entries were deleted.

        Raises
        ------
        ValueError
            Neither bound was given, ``page_hashes`` was given and empty, or
            ``older_than`` is naive.
        StoreUnavailableError
            The store cannot be written.
        """
        return self._prune("diagram_cache", older_than=older_than, page_hashes=page_hashes)

    def _prune(
        self,
        table: str,
        /,
        *,
        older_than: datetime | None,
        page_hashes: Sequence[str] | None,
    ) -> int:
        """Delete cache entries from one table under an explicit bound.

        Parameters
        ----------
        table
            Cache table name, always a literal from this module.
        older_than
            Age bound, or ``None``.
        page_hashes
            Page bound, or ``None``.

        Returns
        -------
        int
            How many entries were deleted.

        Raises
        ------
        ValueError
            No bound was given, the page bound was empty, or the age bound is
            naive. All three are checked before any SQL runs, so a bound the
            caller got wrong deletes nothing.
        StoreUnavailableError
            The store cannot be written.
        """
        if older_than is None and page_hashes is None:
            msg = f"pruning {table} needs an explicit bound: older_than, page_hashes, or both"
            raise ValueError(msg)
        if page_hashes is not None and not page_hashes:
            msg = f"pruning {table} was given an empty page_hashes; that is not 'everything'"
            raise ValueError(msg)
        if older_than is not None and older_than.tzinfo is None:
            msg = (
                f"pruning {table} needs an aware older_than; {older_than!r} has no tzinfo, "
                "so its instant -- and how many paid entries it deletes -- would depend on $TZ"
            )
            raise ValueError(msg)
        clauses: list[str] = []
        params: list[str | int | None] = []
        if older_than is not None:
            clauses.append("created_at_utc < ?")
            params.append(utc_key(older_than))
        if page_hashes:
            placeholders = ", ".join("?" * len(page_hashes))
            clauses.append(f"page_hash IN ({placeholders})")
            params.extend(page_hashes)
        where = " AND ".join(clauses)
        bound: Params = params
        # S608 is justified here: `table` is a literal from this module and `where` is
        # built from fixed fragments plus a run of `?` marks.
        return self._conn.execute(f"DELETE FROM {table} WHERE {where}", bound)  # noqa: S608

    def trim_audit_log(self, *, keep: int) -> int:
        """Keep the newest ``keep`` history entries and delete the rest.

        Never touches ``audit_counter``, so the next append's sequence still
        exceeds every sequence ever handed out. Gaps in the sequence are
        explicitly allowed by the port; reuse is not.

        Parameters
        ----------
        keep
            How many of the newest entries to retain. Must be at least 1.

        Returns
        -------
        int
            How many entries were deleted.

        Raises
        ------
        ValueError
            ``keep`` is less than 1. Trimming to nothing is a separate decision
            from retention and is not available here.
        StoreUnavailableError
            The store cannot be written.
        """
        if keep < 1:
            msg = f"keep must be at least 1, got {keep}"
            raise ValueError(msg)
        return self._conn.execute(
            "DELETE FROM sync_audit WHERE sequence NOT IN ("
            "SELECT sequence FROM sync_audit ORDER BY sequence DESC LIMIT ?)",
            (keep,),
        )

    def vacuum(self) -> None:
        """Rebuild the database file, reclaiming space freed by a prune.

        Raises
        ------
        StoreUnavailableError
            The rebuild failed. Cannot run inside a transaction, which is why
            this package never leaves one open.
        """
        self._conn.execute("VACUUM")

    def import_ocr_sidecars(self, xochitl_dir: Path, store: DocumentSyncStore, /) -> int:
        """Import ``{page_uuid}.ocr.txt`` sidecars as recorded page text.

        Parameters
        ----------
        xochitl_dir
            A local xochitl tree, scanned recursively for sidecars.
        store
            The sync store to write through, so each import is validated by the
            model exactly once. Text for a page the mirror does not have is
            dropped by the store's own rule.

        Returns
        -------
        int
            How many sidecars were imported. Blank sidecars, sidecars whose page
            is not in the mirror, and sidecars that cannot be read are skipped
            and not counted.
        """
        if not xochitl_dir.is_dir():
            _LOGGER.warning("no xochitl directory at %s; nothing to import", xochitl_dir)
            return 0
        indexes: dict[str, dict[str, int]] = {}
        imported = 0
        for sidecar in sorted(xochitl_dir.rglob(f"*{SIDECAR_SUFFIX}")):
            doc_uuid = sidecar.parent.name
            page_uuid = sidecar.name.removesuffix(SIDECAR_SUFFIX)
            page_index = self._page_indexes(store, doc_uuid, into=indexes).get(page_uuid)
            if page_index is None:
                _LOGGER.debug("skipped %s: page is not in the recorded page set", sidecar)
                continue
            text = self._sidecar_text(sidecar)
            if text is None:
                continue
            store.record_page_text(
                PageText(
                    doc_uuid=doc_uuid,
                    page_uuid=page_uuid,
                    page_index=page_index,
                    text=text,
                    provenance=TextProvenance(
                        extracted_at=datetime.fromtimestamp(sidecar.stat().st_mtime, UTC),
                    ),
                ),
            )
            imported += 1
        return imported

    @staticmethod
    def _sidecar_text(sidecar: Path, /) -> str | None:
        """Return a sidecar's text, or ``None`` when there is nothing to import.

        Parameters
        ----------
        sidecar
            The ``.ocr.txt`` file.

        Returns
        -------
        str | None
            The file's text, or ``None`` when it is blank or unreadable. A blank
            sidecar is not an error and not an import.

            This is the one place the imports cannot preserve the three page
            states the store otherwise insists on. A blank sidecar could be "read,
            and there was no ink" -- which the store can represent, as a
            ``PageText`` with empty text -- or a truncated write, or a file some
            other tool created; legacy recorded nothing that tells them apart. So
            imported history cannot express "read, no ink", and a blank sidecar is
            treated as "nobody looked". Anything recorded by this build carries
            that distinction; only what came from before does not.
        """
        try:
            text = sidecar.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _LOGGER.warning("skipped %s: %s", sidecar, exc)
            return None
        if not text.strip():
            _LOGGER.debug("skipped %s: blank sidecar", sidecar)
            return None
        return text

    def rescue_legacy_page_texts(
        self,
        legacy_path: Path,
        store: DocumentSyncStore,
        /,
        *,
        extracted_at: datetime,
    ) -> int:
        """Lift paid OCR text out of a pre-rewrite database, read-only.

        Run this after a fresh pull has repopulated the mirror: text is written
        through the store, so text for a page the new mirror does not have is
        dropped rather than orphaned.

        Parameters
        ----------
        legacy_path
            A pre-rewrite ``sync.db``. Opened read-only and never modified.
        store
            The sync store to write through.
        extracted_at
            Provenance timestamp for the rescued text. Supplied by the caller
            because the legacy row's own timestamp is unreliable -- the sidecar
            importer wrote SQLite's ``datetime('now')``, which is naive and would
            fail ``AwareDatetime`` validation.

        Returns
        -------
        int
            How many pages of text were rescued. Six things are skipped and not
            counted: blank text; a page uuid the new mirror does not have; a
            repeated ``(doc_uuid, page_uuid)``, because the legacy cache held one
            row per engine per page; a hash that matches more than one legacy
            page, because then no page owns the text; a page whose ``.rm`` was
            zero bytes, because a page with no ink cannot own a transcription;
            and a page whose legacy hash was empty rather than a digest.

        Raises
        ------
        StoreUnavailableError
            The legacy file is missing, is not a database, or has no legacy
            tables to read.
        """
        conn = open_legacy_readonly(legacy_path)
        try:
            rows = conn.query(_LEGACY_RESCUE)
        finally:
            conn.close()
        provenance = TextProvenance(extracted_at=extracted_at)
        indexes: dict[str, dict[str, int]] = {}
        seen: set[tuple[str, str]] = set()
        shared: dict[str, int] = {}
        inkless: set[str] = set()
        rescued = 0
        for doc_uuid, page_uuid, rm_hash, rm_size_bytes, text, pages_sharing in rows:
            identity = (str(doc_uuid), str(page_uuid))
            if identity in seen:
                continue
            seen.add(identity)
            if int(pages_sharing) > 1:
                shared[str(rm_hash)] = int(pages_sharing)
                continue
            if str(rm_hash) == EMPTY_SCENE_DIGEST or rm_size_bytes == 0 or not str(rm_hash):
                inkless.add(str(rm_hash))
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            # The page index comes from the current mirror, not from the legacy
            # row: after a fresh pull the mirror is authoritative, and a page the
            # mirror does not have is one whose text the store would drop anyway.
            page_index = self._page_indexes(store, identity[0], into=indexes).get(identity[1])
            if page_index is None:
                _LOGGER.debug("skipped legacy row %s: page is not in the mirror", identity)
                continue
            store.record_page_text(
                PageText(
                    doc_uuid=identity[0],
                    page_uuid=identity[1],
                    page_index=page_index,
                    text=text,
                    provenance=provenance,
                ),
            )
            rescued += 1
        self._report_unrescuable(legacy_path, shared=shared, inkless=inkless)
        _LOGGER.info("rescued %d page texts from %s", rescued, legacy_path)
        return rescued

    @staticmethod
    def _report_unrescuable(
        legacy_path: Path,
        /,
        *,
        shared: dict[str, int],
        inkless: set[str],
    ) -> None:
        """Warn about legacy text no page could be shown to own.

        Silence here would be the whole defect: the user deletes the legacy file
        believing the rescue took everything, when what it declined was a hash so
        ambiguous that believing it would have written another document's text
        onto their page.

        Parameters
        ----------
        legacy_path
            The database that was read, named in every warning.
        shared
            Declined hash to the number of legacy pages it matched.
        inkless
            Hashes declined because the page held no ink.
        """
        for digest, pages in sorted(shared.items()):
            _LOGGER.warning(
                "%s: legacy OCR text for hash %s was left behind -- %d pages share that hash, "
                "so no single page owns the text",
                legacy_path,
                digest,
                pages,
            )
        if inkless:
            _LOGGER.warning(
                "%s: legacy OCR text for %d zero-byte page hash(es) was left behind -- "
                "a page with no ink cannot own a transcription",
                legacy_path,
                len(inkless),
            )

    @staticmethod
    def _page_indexes(
        store: DocumentSyncStore,
        doc_uuid: str,
        /,
        *,
        into: dict[str, dict[str, int]],
    ) -> dict[str, int]:
        """Return one document's recorded ``page_uuid`` to ``page_index`` map.

        Memoized into ``into``, so a document with 92 pages of sidecars costs one
        read of the mirror rather than 92.

        Parameters
        ----------
        store
            The sync store to read.
        doc_uuid
            The document whose page set is wanted.
        into
            The memo, keyed by document uuid.

        Returns
        -------
        dict[str, int]
            Page uuid to page index, empty when the document is untracked.
        """
        if doc_uuid not in into:
            into[doc_uuid] = {page.page_uuid: page.page_index for page in store.pages(doc_uuid)}
        return into[doc_uuid]
