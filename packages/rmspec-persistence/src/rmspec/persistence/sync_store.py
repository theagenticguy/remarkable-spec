"""The SQLite adapter for :class:`~rmspec.domain.ports.persistence.DocumentSyncStore`.

Relocates ``SyncDB.upsert_document`` and ``SyncDB.upsert_page`` (merged into one
all-or-nothing :meth:`SqliteDocumentSyncStore.record_document`),
``SyncDB.get_document``, ``SyncDB.list_documents``, ``SyncDB.get_pages`` and
``SyncDB.delete_document``. The ``ON CONFLICT ... DO UPDATE`` upsert shapes move
across intact; the row mappers do not, because there are no per-field columns left
to map.

Four departures from the legacy behaviour, each deliberate
---------------------------------------------------------
Ordering. Legacy ``ORDER BY visible_name`` used SQLite's BINARY collation, which
sorts every uppercase letter before every lowercase one. The port declares
``(visible_name.casefold(), uuid)``, so a mixed-case library lists in a different
order than it used to. That is a visible, intended change, and it is exact rather
than approximate: the fold is stored in a column, and BINARY on UTF-8 is
code-point order, which is what Python's ``sorted`` uses.

Page-set replacement. Legacy ``upsert_page`` never deleted anything, so a page
removed on the tablet lingered in the mirror for ever. :meth:`record_document`
replaces the set. Crucially it deletes *only* the departed pages: a blanket
``DELETE FROM page WHERE doc_uuid = ?`` would cascade away the text of every page
that survived the replacement, which is paid OCR output and exactly what the port
promises to keep.

Page index on surviving text. When a surviving page moves within its document,
its recorded text is re-indexed to match -- both the sort column and the
``page_index`` inside the payload. The alternative, leaving the stored index
stale, makes :meth:`page_texts` and :meth:`pages` disagree about order, and
because a double and an adapter would be stale in the same way, a contract test
would pass straight over the divergence.

Page ownership is a precondition, not a hint. Legacy ``upsert_page`` took one page
at a time and filed it under the page's own ``doc_uuid``, so pairing a document
with a page set is new -- and it introduced a state the port never named: a page
whose ``doc_uuid`` is not ``document.uuid``. Left undefined, this adapter answered
"the page's uuid wins" (``_UPSERT_PAGE`` inserts ``page.doc_uuid``, so recording
document A with a page of B recorded *nothing* for A and silently rewrote B's row)
while the in-memory double answered "the document's uuid wins", and every existing
call site passes matching uuids so no contract test could see it. Both now refuse
the pair with ``ValueError`` before any store is touched, which is the same channel
the ports already use for an argument the caller got wrong, and the refusal is a
contract case so the two cannot drift apart again. Refusing rather than
normalising: a row keyed by one document whose payload claims another is two wrong
answers agreeing, which is worse than the disagreement it hides.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import StoredRecordUnreadableError
from rmspec.domain.models import PageText, SyncedDocument, SyncedPage
from rmspec.persistence._sqlite import dumps, loads
from rmspec.persistence.derived import name_fold

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.persistence._sqlite import Params, SqliteDatabase, StoreConnection

__all__ = ["SqliteDocumentSyncStore"]

_LOGGER: Final = logging.getLogger(__name__)

_UPSERT_DOCUMENT: Final = """
INSERT INTO document (uuid, name_fold, payload) VALUES (?, ?, ?)
ON CONFLICT (uuid) DO UPDATE SET name_fold = excluded.name_fold, payload = excluded.payload
"""

_UPSERT_PAGE: Final = """
INSERT INTO page (doc_uuid, page_uuid, page_index, payload) VALUES (?, ?, ?, ?)
ON CONFLICT (doc_uuid, page_uuid)
DO UPDATE SET page_index = excluded.page_index, payload = excluded.payload
"""

# The existence guard is what makes text for an unknown page a successful no-op
# rather than a foreign-key violation dressed up as StoreUnavailableError -- a lie
# about a store that is working fine. The WHERE clause is also what lets SQLite
# parse an upsert attached to an INSERT ... SELECT at all.
_UPSERT_PAGE_TEXT: Final = """
INSERT INTO page_text (doc_uuid, page_uuid, page_index, payload)
SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM page WHERE doc_uuid = ? AND page_uuid = ?)
ON CONFLICT (doc_uuid, page_uuid)
DO UPDATE SET page_index = excluded.page_index, payload = excluded.payload
"""


class SqliteDocumentSyncStore:
    """Records and reads the tracked mirror of the tablet.

    Takes the opaque :class:`~rmspec.persistence._sqlite.SqliteDatabase` handle
    and nothing else -- no path, no settings object, no home-directory read -- so
    the container provider that builds it is one line and names no sqlite3 type.
    """

    def __init__(self, database: SqliteDatabase, /) -> None:
        self._conn: StoreConnection = database.primary
        self._store = database.store

    def record_document(
        self,
        document: SyncedDocument,
        pages: Sequence[SyncedPage],
        /,
    ) -> None:
        """Record a document together with its complete page set, atomically.

        Parameters
        ----------
        document
            The document to record, keyed by its uuid.
        pages
            The document's complete page set, every page owned by ``document``.
            Pages that were recorded before and are absent here are deleted, and
            their recorded text goes with them. A repeated ``page_uuid`` keeps the
            last occurrence.

        Raises
        ------
        ValueError
            A page's ``doc_uuid`` is not ``document.uuid``. Refused before the
            transaction opens: see the module docstring.
        StoreUnavailableError
            The store cannot be written.
        """
        stray = sorted({page.page_uuid for page in pages if page.doc_uuid != document.uuid})
        if stray:
            msg = f"pages {stray} do not belong to {document.uuid}"
            raise ValueError(msg)
        incoming = {page.page_uuid: page for page in pages}
        with self._conn.transaction():
            self._conn.execute(
                _UPSERT_DOCUMENT,
                (document.uuid, name_fold(document), dumps(document)),
            )
            self._delete_departed_pages(document.uuid, keeping=frozenset(incoming))
            self._conn.execute_many(
                _UPSERT_PAGE,
                [
                    (page.doc_uuid, page.page_uuid, page.page_index, dumps(page))
                    for page in incoming.values()
                ],
            )
            self._reindex_surviving_text(document.uuid, incoming)

    def _delete_departed_pages(self, doc_uuid: str, /, *, keeping: frozenset[str]) -> None:
        """Delete the recorded pages this document no longer has.

        Computed in Python from the stored set rather than expressed as one
        ``NOT IN`` over the incoming set, because a chunked ``NOT IN`` is not
        decomposable: ``page_uuid NOT IN (chunk one)`` deletes precisely the pages
        listed in chunk two.

        Parameters
        ----------
        doc_uuid
            The document being recorded.
        keeping
            Page uuids in the incoming set.
        """
        stored = {
            str(row[0])
            for row in self._conn.query(
                "SELECT page_uuid FROM page WHERE doc_uuid = ?", (doc_uuid,)
            )
        }
        departed = sorted(stored - keeping)
        if not departed:
            return
        placeholders = ", ".join("?" * len(departed))
        params: Params = [doc_uuid, *departed]
        self._conn.execute(
            # S608 is justified here: the only interpolation is a run of `?` marks whose
            # length comes from a set of uuids read out of this same database.
            f"DELETE FROM page WHERE doc_uuid = ? AND page_uuid IN ({placeholders})",  # noqa: S608
            params,
        )

    def _reindex_surviving_text(
        self,
        doc_uuid: str,
        incoming: dict[str, SyncedPage],
        /,
    ) -> None:
        """Move recorded text to the page index its page now has.

        A payload whose text cannot be validated has its sort column corrected
        and its payload left alone: rewriting it is not possible, and dropping it
        would destroy paid output. The next read of that row raises
        ``StoredRecordUnreadableError``, which is the reader's declared
        behaviour; :meth:`record_document` declares no such error and must not
        start raising one.

        Parameters
        ----------
        doc_uuid
            The document being recorded.
        incoming
            The incoming page set, keyed by page uuid.
        """
        rows = self._conn.query(
            "SELECT page_uuid, page_index, payload FROM page_text WHERE doc_uuid = ?",
            (doc_uuid,),
        )
        for page_uuid, stored_index, payload in rows:
            page = incoming.get(str(page_uuid))
            if page is None or int(stored_index) == page.page_index:
                continue
            self._conn.execute(
                "UPDATE page_text SET page_index = ?, payload = ? "
                "WHERE doc_uuid = ? AND page_uuid = ?",
                (
                    page.page_index,
                    self._reindexed(payload, page.page_index),
                    doc_uuid,
                    page.page_uuid,
                ),
            )

    def _reindexed(self, payload: object, page_index: int, /) -> str:
        """Return ``payload`` with its ``page_index`` moved, or unchanged.

        Parameters
        ----------
        payload
            The stored ``page_text.payload`` value.
        page_index
            The page's new index.

        Returns
        -------
        str
            The rewritten payload, or the original text when it does not
            validate as a :class:`~rmspec.domain.models.PageText`.
        """
        try:
            text = loads(
                PageText,
                payload,
                store=self._store,
                table="page_text",
                key=f"index {page_index}",
            )
        except StoredRecordUnreadableError:
            _LOGGER.warning(
                "%s.page_text payload could not be re-indexed and was left as stored",
                self._store,
            )
            return str(payload)
        return dumps(text.model_copy(update={"page_index": page_index}))

    def get_document(self, doc_uuid: str, /) -> SyncedDocument | None:
        """Return the recorded document with this uuid, or ``None`` if untracked.

        Parameters
        ----------
        doc_uuid
            Uuid of the document to look up.

        Returns
        -------
        SyncedDocument | None
            The recorded document, or ``None``.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            The stored payload cannot be reconstructed.
        """
        row = self._conn.query_one("SELECT payload FROM document WHERE uuid = ?", (doc_uuid,))
        if row is None:
            return None
        return loads(
            SyncedDocument,
            row[0],
            store=self._store,
            table="document",
            key=doc_uuid,
        )

    def list_documents(self) -> list[SyncedDocument]:
        """Return every recorded document, case-folded by name then by uuid.

        Returns
        -------
        list[SyncedDocument]
            Every recorded document in the port's declared order.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed.
        """
        rows = self._conn.query("SELECT uuid, payload FROM document ORDER BY name_fold, uuid")
        return [
            loads(
                SyncedDocument,
                payload,
                store=self._store,
                table="document",
                key=str(uuid),
            )
            for uuid, payload in rows
        ]

    def pages(self, doc_uuid: str, /) -> list[SyncedPage]:
        """Return the recorded pages of one document, ordered by page index.

        Parameters
        ----------
        doc_uuid
            Uuid of the document whose recorded pages are wanted.

        Returns
        -------
        list[SyncedPage]
            The recorded pages, ``page_index`` ascending then page uuid; empty
            when the document is untracked or has no pages.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed.
        """
        rows = self._conn.query(
            "SELECT page_uuid, payload FROM page WHERE doc_uuid = ? "
            "ORDER BY page_index, page_uuid",
            (doc_uuid,),
        )
        return [
            loads(
                SyncedPage,
                payload,
                store=self._store,
                table="page",
                key=f"{doc_uuid}/{page_uuid}",
            )
            for page_uuid, payload in rows
        ]

    def forget_document(self, doc_uuid: str, /) -> None:
        """Forget a document, its pages, and its page text.

        Forgetting an untracked document is a successful no-op. The pages and
        text go with it through the schema's cascade, which is why every
        connection this package opens verifies ``PRAGMA foreign_keys``.

        Parameters
        ----------
        doc_uuid
            Uuid of the document to forget.

        Raises
        ------
        StoreUnavailableError
            The store cannot be written.
        """
        self._conn.execute("DELETE FROM document WHERE uuid = ?", (doc_uuid,))

    def record_page_text(self, page_text: PageText, /) -> None:
        """Record the extracted text of one page, replacing any earlier text.

        Parameters
        ----------
        page_text
            The page identity, its extracted text, and the provenance of the
            extraction. Text naming a page uuid the mirror does not have is not
            stored, and the call still succeeds.

        Raises
        ------
        StoreUnavailableError
            The store cannot be written.
        """
        stored = self._conn.execute(
            _UPSERT_PAGE_TEXT,
            (
                page_text.doc_uuid,
                page_text.page_uuid,
                page_text.page_index,
                dumps(page_text),
                page_text.doc_uuid,
                page_text.page_uuid,
            ),
        )
        if stored == 0:
            _LOGGER.debug(
                "dropped text for %s/%s: the page is not in the recorded page set",
                page_text.doc_uuid,
                page_text.page_uuid,
            )

    def page_texts(self, doc_uuid: str, /) -> list[PageText]:
        """Return recorded page text for one document, ordered by page index.

        Parameters
        ----------
        doc_uuid
            Uuid of the document whose page text is wanted.

        Returns
        -------
        list[PageText]
            Recorded page text, ``page_index`` ascending then page uuid.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed.
        """
        rows = self._conn.query(
            "SELECT page_uuid, payload FROM page_text WHERE doc_uuid = ? "
            "ORDER BY page_index, page_uuid",
            (doc_uuid,),
        )
        return [
            loads(
                PageText,
                payload,
                store=self._store,
                table="page_text",
                key=f"{doc_uuid}/{page_uuid}",
            )
            for page_uuid, payload in rows
        ]

    def all_page_texts(self) -> list[PageText]:
        """Return recorded page text for every tracked document.

        Returns
        -------
        list[PageText]
            All recorded page text, ordered by ``(doc_uuid, page_index,
            page_uuid)``.

        Raises
        ------
        StoreUnavailableError
            The store cannot be read.
        StoredRecordUnreadableError
            A stored payload cannot be reconstructed.
        """
        rows = self._conn.query(
            "SELECT doc_uuid, page_uuid, payload FROM page_text "
            "ORDER BY doc_uuid, page_index, page_uuid",
        )
        return [
            loads(
                PageText,
                payload,
                store=self._store,
                table="page_text",
                key=f"{doc_uuid}/{page_uuid}",
            )
            for doc_uuid, page_uuid, payload in rows
        ]
