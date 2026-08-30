"""The two artifact caches: one implementation, two tables, plus the null pair.

Relocates the ``ON CONFLICT ... DO UPDATE`` upsert shape and the single-row SELECT
of ``SyncDB.put_diagram`` / ``SyncDB.get_diagram``. The *methods* are not a
relocation of ``get_ocr`` / ``put_ocr``: those keyed on the source hash alone
while storing the model id and the render DPI as ordinary columns, which is the
stale-hit defect itself -- change the prompt, the model or the DPI and the legacy
lookup returned a hit computed under the old settings. Here the key is
``key.digest``, which folds every one of those inputs, so a changed input is
mechanically a miss.

Totality is structural, not incidental
--------------------------------------
Every method on both ports is declared total -- the three they share, plus
``OcrCache.equivalent_raster``, which only the OCR binding carries: none raises. So each body
catches ``PersistenceError`` -- both store faults and unreadable payloads -- plus
the value and type errors a payload can produce, logs at ``WARNING`` through this
module's logger, and returns ``None`` or drops the write. A miss costs a
recomputation; failing the command that just did the paid work costs the work.

The one thing that must not drift: the swallow lives here, in the adapter, and
nowhere inward. Only a faulting test double with call counters can prove a
swallow happened at all, because through a total port a swallowed fault and a
genuine miss are the same ``None``.

Emptiness is never truthiness
-----------------------------
``if not payload`` and ``if not text`` appear nowhere in this package. An
``OcrArtifact(text="")`` for one of the corpus's zero-byte pages is a stored,
returned hit; only ``row is None`` decides a miss, and only a failed validation
decides unreadable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import PersistenceError
from rmspec.domain.models import DiagramArtifact, DiagramCacheKey, OcrArtifact, OcrCacheKey
from rmspec.persistence._sqlite import dumps, loads
from rmspec.persistence.derived import utc_key

if TYPE_CHECKING:
    from rmspec.persistence._sqlite import SqliteDatabase, StoreConnection

__all__ = [
    "NullDiagramCache",
    "NullOcrCache",
    "SqliteDiagramCache",
    "SqliteOcrCache",
]

_LOGGER: Final = logging.getLogger(__name__)


class _SqliteArtifactCache[
    K: (OcrCacheKey, DiagramCacheKey),
    A: (OcrArtifact, DiagramArtifact),
]:
    """Digest-keyed artifact storage over one table.

    Bound twice below, once per port. The type parameters are constrained rather
    than bounded by a structural protocol: a bound of "anything with ``.digest``
    and ``.page_hash``" would not also give ``model_validate_json``, which the
    read path needs.
    """

    def __init__(
        self,
        database: SqliteDatabase,
        /,
        *,
        table: str,
        key_type: type[K],
        artifact_type: type[A],
    ) -> None:
        self._conn: StoreConnection = database.primary
        self._store = database.store
        self._table = table
        self._key_type = key_type
        self._artifact_type = artifact_type

    def get(self, key: K, /) -> A | None:
        """Return the artifact stored under this exact key, or ``None``.

        Parameters
        ----------
        key
            The complete cache key; only ``key.digest`` is matched.

        Returns
        -------
        A | None
            The cached artifact, or ``None`` on a miss or any read fault.
        """
        try:
            row = self._conn.query_one(
                # S608 is justified here: `self._table` is one of two module constants.
                f"SELECT artifact_payload FROM {self._table} WHERE digest = ?",  # noqa: S608
                (key.digest,),
            )
            if row is None:
                return None
            return loads(
                self._artifact_type,
                row[0],
                store=self._store,
                table=self._table,
                key=key.digest,
            )
        except (PersistenceError, ValueError, TypeError) as exc:
            _LOGGER.warning(
                "%s.%s read failed, treating as a miss: %s", self._store, self._table, exc
            )
            return None

    def put(self, key: K, artifact: A, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry.

        Parameters
        ----------
        key
            The complete cache key, stored beside the artifact so an entry can
            never exist without the provenance that produced it.
        artifact
            The result to cache. Stored as it is, including a ``truncated`` flag
            where the artifact type has one.
        """
        try:
            self._conn.execute(
                # S608 is justified here: `self._table` is one of two module constants.
                f"INSERT INTO {self._table} "  # noqa: S608
                "(digest, page_hash, created_at_utc, key_payload, artifact_payload) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (digest) DO UPDATE SET "
                "page_hash = excluded.page_hash, "
                "created_at_utc = excluded.created_at_utc, "
                "key_payload = excluded.key_payload, "
                "artifact_payload = excluded.artifact_payload",
                (
                    key.digest,
                    key.page_hash,
                    utc_key(artifact.created_at),
                    dumps(key),
                    dumps(artifact),
                ),
            )
        except (PersistenceError, ValueError, TypeError) as exc:
            _LOGGER.warning("%s.%s write dropped: %s", self._store, self._table, exc)

    def superseded(self, key: K, /) -> K | None:
        """Return a stored key for the same page under other inputs, or ``None``.

        Parameters
        ----------
        key
            The key that missed. Only ``key.page_hash`` and ``key.digest`` are
            used.

        Returns
        -------
        K | None
            A stored key with the same ``page_hash`` and the greatest differing
            ``digest``; ``None`` when there is none, when ``key`` itself is
            stored, or on any read fault. Diagnostic only -- it names
            provenance and never an artifact.
        """
        try:
            # S608 is justified on both: `self._table` is one of two module constants.
            own = self._conn.query_one(
                f"SELECT 1 FROM {self._table} WHERE digest = ?",  # noqa: S608
                (key.digest,),
            )
            if own is not None:
                return None
            row = self._conn.query_one(
                f"SELECT digest, key_payload FROM {self._table} "  # noqa: S608
                "WHERE page_hash = ? AND digest <> ? ORDER BY digest DESC LIMIT 1",
                (key.page_hash, key.digest),
            )
            if row is None:
                return None
            return loads(
                self._key_type,
                row[1],
                store=self._store,
                table=self._table,
                key=str(row[0]),
            )
        except (PersistenceError, ValueError, TypeError) as exc:
            _LOGGER.warning(
                "%s.%s provenance lookup failed, reporting a bare miss: %s",
                self._store,
                self._table,
                exc,
            )
            return None


class SqliteOcrCache(_SqliteArtifactCache[OcrCacheKey, OcrArtifact]):
    """The ``ocr_cache`` binding of :class:`~rmspec.domain.ports.persistence.OcrCache`.

    One method more than its diagram sibling, because
    :class:`~rmspec.domain.models.OcrCacheKey` has one component
    :class:`~rmspec.domain.models.DiagramCacheKey` lacks; see
    :meth:`~rmspec.domain.ports.persistence.OcrCache.equivalent_raster`. It cannot
    live on the shared base for that reason, and it is the only place in this
    package where a lookup is not an indexed equality on ``digest``.
    """

    def __init__(self, database: SqliteDatabase, /) -> None:
        super().__init__(
            database,
            table="ocr_cache",
            key_type=OcrCacheKey,
            artifact_type=OcrArtifact,
        )

    def equivalent_raster(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return a stored artifact for identical pixels under a different page hash.

        Parameters
        ----------
        key
            The key that missed. Matched on
            :attr:`~rmspec.domain.models.OcrCacheKey.raster_identity`, with
            ``page_hash`` required to differ.

        Returns
        -------
        OcrArtifact | None
            The artifact stored under the greatest qualifying ``digest``, or
            ``None`` when none qualifies and on any read fault.

        Notes
        -----
        A scan of the rows for other pages rather than an indexed lookup: there is
        no ``raster_identity`` column to index, and adding one would be a second
        mirrored copy of a key component -- the exact defect this table's
        no-per-field-columns shape exists to prevent. ``page_hash`` is indexed, so
        the scan is over "every row whose page differs", which is bounded by the
        cache and not by the corpus. The rows are walked in descending ``digest``
        order and the first qualifying one wins, which is the port's declared rule.

        The only SQL in this module with no interpolation in it, and therefore the
        only statement needing no ``S608`` suppression: this method belongs to one
        binding rather than to the base class, so the table is a literal.
        """
        try:
            rows = self._conn.query(
                "SELECT key_payload, artifact_payload FROM ocr_cache "
                "WHERE page_hash <> ? ORDER BY digest DESC",
                (key.page_hash,),
            )
            wanted = key.raster_identity
            for row in rows:
                stored = loads(
                    OcrCacheKey,
                    row[0],
                    store=self._store,
                    table=self._table,
                    key=wanted,
                )
                if stored.raster_identity == wanted:
                    return loads(
                        OcrArtifact,
                        row[1],
                        store=self._store,
                        table=self._table,
                        key=stored.digest,
                    )
        except (PersistenceError, ValueError, TypeError) as exc:
            _LOGGER.warning(
                "%s.%s equivalent-raster lookup failed, treating as a miss: %s",
                self._store,
                self._table,
                exc,
            )
        return None


class SqliteDiagramCache(_SqliteArtifactCache[DiagramCacheKey, DiagramArtifact]):
    """The ``diagram_cache`` binding of :class:`~rmspec.domain.ports.persistence.DiagramCache`."""

    def __init__(self, database: SqliteDatabase, /) -> None:
        super().__init__(
            database,
            table="diagram_cache",
            key_type=DiagramCacheKey,
            artifact_type=DiagramArtifact,
        )


class NullOcrCache:
    """An OCR cache that stores nothing and misses everything.

    What ``--no-cache`` binds, so "do not use the cache" is a wiring decision
    rather than an ``if`` inside a use case. Takes no arguments and touches no
    file.
    """

    def get(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return ``None``: this cache holds nothing.

        Parameters
        ----------
        key
            Ignored.

        Returns
        -------
        OcrArtifact | None
            Always ``None``.
        """
        _ = key
        return None

    def put(self, key: OcrCacheKey, artifact: OcrArtifact, /) -> None:
        """Discard the artifact.

        Parameters
        ----------
        key
            Ignored.
        artifact
            Ignored.
        """
        _ = (key, artifact)

    def superseded(self, key: OcrCacheKey, /) -> OcrCacheKey | None:
        """Return ``None``: nothing is stored, so no provenance can be named.

        Parameters
        ----------
        key
            Ignored.

        Returns
        -------
        OcrCacheKey | None
            Always ``None``.
        """
        _ = key
        return None

    def equivalent_raster(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return ``None``: nothing is stored, so no pixels can be equivalent to it.

        Parameters
        ----------
        key
            Ignored.

        Returns
        -------
        OcrArtifact | None
            Always ``None``. ``--no-cache`` means the run pays, and a fallback that
            fired here would make it mean something else.
        """
        _ = key
        return None


class NullDiagramCache:
    """A diagram cache that stores nothing and misses everything.

    The ``--no-cache`` binding for the diagram pass; see :class:`NullOcrCache`.
    """

    def get(self, key: DiagramCacheKey, /) -> DiagramArtifact | None:
        """Return ``None``: this cache holds nothing.

        Parameters
        ----------
        key
            Ignored.

        Returns
        -------
        DiagramArtifact | None
            Always ``None``.
        """
        _ = key
        return None

    def put(self, key: DiagramCacheKey, artifact: DiagramArtifact, /) -> None:
        """Discard the artifact.

        Parameters
        ----------
        key
            Ignored.
        artifact
            Ignored.
        """
        _ = (key, artifact)

    def superseded(self, key: DiagramCacheKey, /) -> DiagramCacheKey | None:
        """Return ``None``: nothing is stored, so no provenance can be named.

        Parameters
        ----------
        key
            Ignored.

        Returns
        -------
        DiagramCacheKey | None
            Always ``None``.
        """
        _ = key
        return None
