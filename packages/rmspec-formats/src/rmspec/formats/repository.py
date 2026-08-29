"""The ``DocumentRepository`` adapter over a local xochitl directory.

Relocates ``formats/document_loader.py``. The legacy free function
``load_document(xochitl_dir, doc_uuid)`` becomes a bound port with six methods, and the
four that had no legacy counterpart inside ``formats/`` -- ``list_documents``,
``summary``, ``page_fingerprint``, ``page_fingerprints`` -- fold in the catalog scan
that lived in ``cli/_resolve.py`` and the hashing that lived in ``sync/hasher.py``.

The one structural change: three page states, all values
--------------------------------------------------------
The legacy loader wrapped its parse in ``except Exception: logger.warning(...)`` and
produced an empty layer list for anything that went wrong, so "no artifact", "blank
page", "truncated file" and "unsupported version" were one indistinguishable result and
the reason was a log line nobody read. This adapter produces exactly the three states
``ports/formats.py`` documents, and catches only the two errors ``PageCodec`` is allowed
to raise:

* an artifact that decoded -- ``content`` plus whatever defects the decode accepted;
* a page the document claims with no artifact file at all -- ``content=None`` and
  ``ARTIFACT_ABSENT``, which is the routine unannotated page of a PDF;
* an artifact that is present and will not decode -- ``content=None`` and
  ``CONTENT_UNDECODABLE`` carrying the error's own message.

A zero-byte artifact is none of the last two: it decodes, to a blank page. That is 62
of the 92 files in the reference corpus, and the whole reason ``load`` on a PDF-backed
document now completes at all.

One listing decides "has an artifact", for every method
------------------------------------------------------
``load``, ``load_page``, ``page_fingerprint`` and ``page_fingerprints`` all reach the
store through :meth:`XochitlDocumentRepository._artifact`, which consults one directory
listing per call. The port requires each value of ``page_fingerprints`` to be "exactly
what ``page_fingerprint`` would return"; deciding presence from a listing in the batch
method and from an ``open`` in the single one made that false on any case-insensitive
filesystem -- the default on macOS -- where a page id differing from the on-disk name
only in case is absent to the listing and present to the ``open``. Routing every path
through the listing makes the agreement structural instead of asserted, and pays for
itself on ``load``: an annotated PDF's 192 unwritten pages cost one listing rather than
192 failed opens.

Two further divergences from the legacy loader, recorded rather than smuggled
----------------------------------------------------------------------------
1. **A missing ``.content`` is a document with no pages, not an error.** The legacy
   loader called its content reader unguarded, so a folder -- which never has one --
   raised ``FileNotFoundError``. ``list_documents`` has to include folders for a tree to
   be buildable, and ``DocumentMetadata.decode`` already supports ``content=None``.
2. **A store entry whose stem is not a uuid now loads.** The legacy loader ended with
   ``UUID(doc_uuid)`` and crashed on anything else. ``DocumentId.uuid`` is a constrained
   ``str``, so such an entry is accepted -- unless it fails that constraint, in which
   case ``list_documents`` omits it rather than failing the whole listing.

Nothing here catches a bare ``Exception``. Everything a domain constructor or a sidecar
reader can raise -- ``TypeError``, ``ValueError``, ``UnicodeDecodeError``, and pydantic's
``ValidationError``, which is a ``ValueError`` -- is translated to ``MalformedDocument``
against the artifact that produced it, so no stdlib or pydantic type escapes a method
documented to raise domain errors only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rmspec.domain.errors import (
    CorruptPageData,
    DocumentNotFound,
    DocumentStoreUnavailable,
    MalformedDocument,
    PageNotFound,
    UnsupportedPageFormat,
)
from rmspec.domain.models import (
    Document,
    DocumentId,
    DocumentMetadata,
    DocumentSummary,
    Page,
    PageDefect,
    PageDefectCode,
    PageId,
)
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT
from rmspec.formats import layout
from rmspec.formats.fingerprint import fingerprint_bytes
from rmspec.formats.page_index import PageIndexEntry, decode_page_index, decode_pagedata

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from rmspec.domain.models import PageContent
    from rmspec.domain.ports.formats import PageCodec

__all__ = ["XochitlDocumentRepository"]

_ABSENT_DETAIL = "the store holds no scene artifact for this page"
"""Detail on the ``ARTIFACT_ABSENT`` defect. A stub file is not this state."""


@dataclass(frozen=True, slots=True)
class _Index:
    """One document's decoded sidecars: everything needed before any page is read."""

    metadata: DocumentMetadata
    entries: tuple[PageIndexEntry, ...]
    page_ids: tuple[PageId, ...]


class XochitlDocumentRepository:
    """Read documents out of a local xochitl directory.

    Implements ``rmspec.domain.ports.formats.DocumentRepository``. Both collaborators
    are constructor arguments and neither is imported inside a method body: the
    composition root decides which codec is bound, and a local directory has nothing to
    close, so an instance is safe at application scope.

    ``Path`` appears in the constructor and nowhere else -- no port method mentions one,
    which is what keeps a filesystem out of the app layer's vocabulary.
    """

    def __init__(self, *, root: Path, codec: PageCodec) -> None:
        self._root = root
        self._store = str(root)
        self._codec = codec

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """List every document in the store, without decoding any page.

        An entry whose ``.metadata`` will not decode, or whose stem cannot be a
        ``DocumentId``, is omitted rather than raised -- the behaviour the domain's own
        port contract suite blesses. The count of what was skipped is recoverable by
        comparing against the store's own catalog, which is why this method does not
        invent a second return value to report it.

        Returns
        -------
        tuple[DocumentSummary, ...]
            One summary per readable document, ordered by identifier.

        Raises
        ------
        DocumentStoreUnavailable
            The root does not exist, is not a directory, or cannot be listed.
        """
        summaries: list[DocumentSummary] = []
        for uuid in self._catalog():
            doc_id = _document_id(uuid)
            if doc_id is None:
                continue
            try:
                index = self._index(doc_id)
            except (DocumentNotFound, MalformedDocument):
                continue
            summaries.append(_summary_of(doc_id, index))
        return tuple(summaries)

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Summarise one document, without decoding any of its pages.

        Parameters
        ----------
        doc_id
            Identity of the document to summarise.

        Returns
        -------
        DocumentSummary
            The same summary :meth:`list_documents` carries for this document.

        Raises
        ------
        DocumentNotFound
            The store holds no ``.metadata`` for this identity.
        MalformedDocument
            A sidecar would not decode, so no page order can be established.
        DocumentStoreUnavailable
            The store could not be read.
        """
        return _summary_of(doc_id, self._index(doc_id))

    def load(self, doc_id: DocumentId, /) -> Document:
        """Load one whole document, with every page decoded.

        Parameters
        ----------
        doc_id
            Identity of the document to load.

        Returns
        -------
        Document
            Metadata plus every claimed page in document order. A page with no artifact
            and a page whose artifact would not decode are both present and carry the
            defect saying so.

        Raises
        ------
        DocumentNotFound
            The store holds no ``.metadata`` for this identity.
        MalformedDocument
            A sidecar would not decode, so no aggregate can be assembled. Per-page
            problems never raise; they arrive as defects.
        DocumentStoreUnavailable
            The store could not be read.
        """
        index = self._index(doc_id)
        stored = self._artifact_names(doc_id)
        pages = tuple(
            self._page(doc_id, page_id, entry, position, stored)
            for position, (page_id, entry) in enumerate(
                zip(index.page_ids, index.entries, strict=True)
            )
        )
        return Document(doc_id=doc_id, metadata=index.metadata, pages=pages)

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Load a single page of a document.

        Returns exactly the ``Page`` :meth:`load` places at this identity: both paths
        run the same private assembly, so they cannot drift.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page, as listed on the document's summary.

        Returns
        -------
        Page
            The page as the store can produce it, in one of the three documented states.

        Raises
        ------
        DocumentNotFound
            The store holds no ``.metadata`` for this identity.
        PageNotFound
            The document claims no page with this identity. Only that: a claimed page
            with nothing stored for it is the contentless page above.
        MalformedDocument
            A sidecar would not decode, so the page cannot be resolved to an artifact.
        DocumentStoreUnavailable
            The store could not be read.
        """
        index = self._index(doc_id)
        position = _position_of(doc_id, page_id, index)
        return self._page(
            doc_id, page_id, index.entries[position], position, self._artifact_names(doc_id)
        )

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Fingerprint one page's stored bytes, for cache invalidation.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page to fingerprint.

        Returns
        -------
        str
            Lowercase hex SHA-256 of the artifact as read, or
            ``ABSENT_ARTIFACT_FINGERPRINT`` when the document claims the page and the
            store holds no artifact for it. A zero-byte artifact hashes normally.

        Raises
        ------
        DocumentNotFound
            The store holds no ``.metadata`` for this identity.
        PageNotFound
            The document claims no page with this identity.
        MalformedDocument
            A sidecar would not decode, so the page cannot be resolved to an artifact.
        DocumentStoreUnavailable
            The store could not be read.
        """
        index = self._index(doc_id)
        _position_of(doc_id, page_id, index)
        return self._fingerprint(doc_id, page_id, self._artifact_names(doc_id))

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Fingerprint every page of one document in one pass.

        One directory listing decides which pages have an artifact at all, so a
        200-page PDF with 8 annotated pages opens 8 files instead of probing 200. Each
        value is what :meth:`page_fingerprint` returns for that page, and that agreement
        is structural rather than asserted: *every* method that has to decide whether a
        page has an artifact asks the same listing, through :meth:`_artifact`. Deciding
        it here from a listing and there from an ``open`` disagreed on a
        case-insensitive filesystem -- which is the default on macOS -- where a page id
        whose case differs from the on-disk name is absent to a listing and present to
        an ``open``.

        Parameters
        ----------
        doc_id
            Identity of the document whose pages to fingerprint.

        Returns
        -------
        Mapping[PageId, str]
            One entry per claimed page, in document order.

        Raises
        ------
        DocumentNotFound
            The store holds no ``.metadata`` for this identity.
        MalformedDocument
            A sidecar would not decode, so the pages cannot be enumerated.
        DocumentStoreUnavailable
            The store could not be read.
        """
        index = self._index(doc_id)
        stored = self._artifact_names(doc_id)
        return {page_id: self._fingerprint(doc_id, page_id, stored) for page_id in index.page_ids}

    # ---------------------------------------------------------------- internals

    def _read(self, path: Path, /) -> bytes | None:
        """Read one artifact, or report that the store holds none.

        Parameters
        ----------
        path
            Absolute path of the artifact.

        Returns
        -------
        bytes | None
            The bytes, or ``None`` when nothing is stored at that path.

        Raises
        ------
        DocumentStoreUnavailable
            For every other ``OSError``: a permission failure, an I/O error, a stale
            mount. The legacy loader let all of these through untyped.
        """
        try:
            return path.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        except OSError as err:
            raise DocumentStoreUnavailable(store=self._store, detail=str(err)) from err

    def _require_store(self) -> None:
        """Refuse to call a missing store a missing document.

        Every method must report an unreachable store as ``DocumentStoreUnavailable``,
        including the identity-addressed ones. Without this check a root that does not
        exist answers ``DocumentNotFound`` for every identity -- which reads as "the
        store is fine and your document is not in it" and sends a caller looking for the
        wrong thing. Paid only on the path where a ``.metadata`` was already absent.

        Raises
        ------
        DocumentStoreUnavailable
            If the root is not a directory.
        """
        if not self._root.is_dir():
            raise DocumentStoreUnavailable(store=self._store, detail="no such xochitl directory")

    def _catalog(self) -> tuple[str, ...]:
        """List every document identifier the store holds.

        Returns
        -------
        tuple[str, ...]
            Identifiers, sorted.

        Raises
        ------
        DocumentStoreUnavailable
            The root does not exist, is not a directory, or cannot be listed.
        """
        try:
            return layout.catalog_uuids(self._root)
        except OSError as err:
            raise DocumentStoreUnavailable(store=self._store, detail=str(err)) from err

    def _artifact_names(self, doc_id: DocumentId, /) -> frozenset[str]:
        """List the filenames in one document's page directory.

        Parameters
        ----------
        doc_id
            Identity of the document.

        Returns
        -------
        frozenset[str]
            Every name in the directory, or an empty set when there is no directory --
            a folder, or a document nothing was ever drawn on.

        Raises
        ------
        DocumentStoreUnavailable
            The directory exists and cannot be listed.
        """
        try:
            return frozenset(
                entry.name for entry in layout.page_dir(self._root, doc_id.uuid).iterdir()
            )
        except (FileNotFoundError, NotADirectoryError):
            return frozenset()
        except OSError as err:
            raise DocumentStoreUnavailable(store=self._store, detail=str(err)) from err

    def _index(self, doc_id: DocumentId, /) -> _Index:
        """Decode one document's sidecars into metadata plus an ordered page list.

        Parameters
        ----------
        doc_id
            Identity of the document.

        Returns
        -------
        _Index
            The metadata, the page entries in document order, and their identities.

        Raises
        ------
        DocumentNotFound
            The store holds no ``.metadata`` for this identity.
        MalformedDocument
            A sidecar would not decode, or the page list claims one identity twice --
            which would make ``load_page`` and ``page_fingerprints`` disagree with
            ``load`` about which page an identity names.
        DocumentStoreUnavailable
            The store could not be read.
        """
        uuid = doc_id.uuid
        metadata_raw = self._read(layout.metadata_path(self._root, uuid))
        if metadata_raw is None:
            self._require_store()
            raise DocumentNotFound(query=uuid, store=self._store)
        content_raw = self._read(layout.content_path(self._root, uuid))
        pagedata_raw = self._read(layout.pagedata_path(self._root, uuid))
        entries, page_ids = self._page_list(uuid, content_raw, self._templates(uuid, pagedata_raw))
        return _Index(
            metadata=self._metadata(uuid, metadata_raw, content_raw),
            entries=entries,
            page_ids=page_ids,
        )

    @staticmethod
    def _templates(uuid: str, raw: bytes | None, /) -> tuple[str, ...]:
        """Decode the ``.pagedata`` sidecar's template lines.

        Parameters
        ----------
        uuid
            The document's identifier, for the error.
        raw
            Bytes of the sidecar, or ``None`` when the store holds none -- which is
            normal and means no page has a template line.

        Returns
        -------
        tuple[str, ...]
            One name per line, in file order.

        Raises
        ------
        MalformedDocument
            The sidecar is not utf-8.
        """
        if raw is None:
            return ()
        try:
            return decode_pagedata(raw)
        except ValueError as err:
            raise MalformedDocument(
                document_uuid=uuid, artifact=layout.PAGEDATA_SUFFIX, detail=_detail(err)
            ) from err

    @staticmethod
    def _page_list(
        uuid: str, raw: bytes | None, templates: tuple[str, ...], /
    ) -> tuple[tuple[PageIndexEntry, ...], tuple[PageId, ...]]:
        """Decode the ``.content`` page list and validate every claimed identity.

        Both halves share one translation because both are facts of the same artifact:
        pydantic's refusal of a page identifier is a statement about the ``.content``
        page list, not about the page.

        Parameters
        ----------
        uuid
            The document's identifier, for the error.
        raw
            Bytes of the ``.content`` sidecar, or ``None`` when the store holds none.
        templates
            The ``.pagedata`` lines, applied to the page list by position.

        Returns
        -------
        tuple[tuple[PageIndexEntry, ...], tuple[PageId, ...]]
            The entries in document order, and their identities in the same order.

        Raises
        ------
        MalformedDocument
            The sidecar will not decode, an identifier is not one a page can have, or
            the list claims the same identity twice.
        """
        try:
            entries = decode_page_index(raw, templates=templates)
            return entries, _page_identities(entries)
        except (TypeError, ValueError) as err:
            raise MalformedDocument(
                document_uuid=uuid, artifact=layout.CONTENT_SUFFIX, detail=_detail(err)
            ) from err

    def _metadata(
        self, uuid: str, metadata_raw: bytes, content_raw: bytes | None, /
    ) -> DocumentMetadata:
        """Decode the ``.metadata`` sidecar, with its ``.content`` sibling when present.

        Parameters
        ----------
        uuid
            The document's identifier, for the error.
        metadata_raw
            Bytes of the ``.metadata`` sidecar.
        content_raw
            Bytes of the ``.content`` sidecar, or ``None`` when the store holds none.

        Returns
        -------
        DocumentMetadata
            The decoded metadata, carrying the layout facts when ``.content`` was read.

        Raises
        ------
        MalformedDocument
            Either sidecar would not decode. Which one is decided by re-decoding the
            metadata alone -- paid only on the failure path -- so the error names the
            artifact a reader has to go and look at.
        """
        try:
            return DocumentMetadata.decode(metadata_raw, content=content_raw)
        except (TypeError, ValueError) as err:
            raise MalformedDocument(
                document_uuid=uuid,
                artifact=self._blame(metadata_raw),
                detail=_detail(err),
            ) from err

    @staticmethod
    def _blame(metadata_raw: bytes, /) -> str:
        """Decide which sidecar a joint decode failure belongs to.

        Parameters
        ----------
        metadata_raw
            Bytes of the ``.metadata`` sidecar.

        Returns
        -------
        str
            ``".metadata"`` when the metadata alone will not decode either, otherwise
            ``".content"`` -- the only remaining input.
        """
        try:
            DocumentMetadata.decode(metadata_raw)
        except (TypeError, ValueError):
            return layout.METADATA_SUFFIX
        return layout.CONTENT_SUFFIX

    def _artifact(
        self, doc_id: DocumentId, page_id: PageId, stored: frozenset[str], /
    ) -> bytes | None:
        """Read one page's artifact, deciding presence from the document's own listing.

        The single place any method decides whether a page has an artifact, so
        ``load``, ``load_page``, ``page_fingerprint`` and ``page_fingerprints`` cannot
        disagree about it. The listing is authoritative on the *name*: a page id that
        differs from the on-disk filename only in case is absent, on every filesystem,
        rather than absent on a case-sensitive one and present on macOS.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page.
        stored
            Names in the document's page directory, from :meth:`_artifact_names`.

        Returns
        -------
        bytes | None
            The bytes, or ``None`` when the listing does not name this page's artifact,
            or names it but it cannot be opened as a file -- a store racing with a
            delete, or a directory wearing an artifact's name.

        Raises
        ------
        DocumentStoreUnavailable
            The artifact exists and could not be read.
        """
        if f"{page_id.uuid}{layout.SCENE_SUFFIX}" not in stored:
            return None
        return self._read(layout.page_path(self._root, doc_id.uuid, page_id.uuid))

    def _fingerprint(self, doc_id: DocumentId, page_id: PageId, stored: frozenset[str], /) -> str:
        """Fingerprint one page's artifact without consulting the page index.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page.
        stored
            Names in the document's page directory.

        Returns
        -------
        str
            Lowercase hex SHA-256, or ``ABSENT_ARTIFACT_FINGERPRINT`` when no artifact
            is stored.

        Raises
        ------
        DocumentStoreUnavailable
            The artifact exists and could not be read.
        """
        raw = self._artifact(doc_id, page_id, stored)
        if raw is None:
            return ABSENT_ARTIFACT_FINGERPRINT
        return fingerprint_bytes(raw)

    def _page(
        self,
        doc_id: DocumentId,
        page_id: PageId,
        entry: PageIndexEntry,
        position: int,
        stored: frozenset[str],
        /,
    ) -> Page:
        """Assemble one page, defecting rather than raising on anything page-local.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page.
        entry
            The page's claim from the ``.content`` index: its template and pdf page.
        position
            Zero-based position in document order, which becomes ``Page.index``.
        stored
            Names in the document's page directory, so this page's state is decided by
            the same listing the fingerprint methods use.

        Returns
        -------
        Page
            One of the three documented states. Template and pdf page are populated in
            every one of them, because those are facts the sidecar holds and the
            artifact does not.

        Raises
        ------
        DocumentStoreUnavailable
            The artifact exists and could not be read.
        """
        raw = self._artifact(doc_id, page_id, stored)
        content: PageContent | None = None
        defects: tuple[PageDefect, ...] = ()
        if raw is None:
            defects = (PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail=_ABSENT_DETAIL),)
        else:
            try:
                content = self._codec.decode_page(raw, page_id.uuid)
            except (CorruptPageData, UnsupportedPageFormat) as err:
                defects = (
                    PageDefect(code=PageDefectCode.CONTENT_UNDECODABLE, detail=err.message),
                )
        return Page(
            page_id=page_id,
            index=position,
            template_name=entry.template_name,
            pdf_page_index=entry.pdf_page_index,
            content=content,
            defects=defects,
        )


def _detail(err: Exception, /) -> str:
    """Render one decode failure as a single line of human detail.

    Pydantic renders a ``ValidationError`` across several lines with a documentation
    url; a domain error's ``detail`` is read by a person looking at one line of CLI
    output, so only the first line survives.

    Parameters
    ----------
    err
        The failure to describe.

    Returns
    -------
    str
        The first non-empty line of the message, or the exception's type name when it
        carries no message at all -- as a bare ``EOFError`` does.
    """
    text = str(err).strip()
    first = text.splitlines()[0].strip() if text else ""
    return first or type(err).__name__


def _document_id(uuid: str, /) -> DocumentId | None:
    """Build a document identity from a store entry's stem.

    Parameters
    ----------
    uuid
        The stem of a ``.metadata`` sidecar.

    Returns
    -------
    DocumentId | None
        The identity, or ``None`` when the stem cannot be one -- a name too long, a
        name carrying a path separator, a dot segment. Such an entry is omitted from a
        listing rather than failing it.
    """
    try:
        return DocumentId(uuid=uuid)
    except ValueError:
        return None


def _page_identities(entries: tuple[PageIndexEntry, ...], /) -> tuple[PageId, ...]:
    """Validate every claimed page identifier, in order.

    Parameters
    ----------
    entries
        The page entries the ``.content`` index claimed.

    Returns
    -------
    tuple[PageId, ...]
        One identity per entry, in document order.

    Raises
    ------
    ValueError
        If an identifier is not one a page can have, or if two entries claim the same
        one. Pydantic's ``ValidationError`` is a ``ValueError``, so the caller's single
        translation covers both.
    """
    page_ids = tuple(PageId(uuid=entry.page_uuid) for entry in entries)
    # A `Counter` rather than `page_ids.count(...)` per element: the latter is O(n^2) and
    # a 200-page notebook pays it on every `_index` call, which is every method here.
    duplicates = sorted(page_id.uuid for page_id, seen in Counter(page_ids).items() if seen > 1)
    if duplicates:
        msg = f"page list claims the same page more than once: {', '.join(duplicates)}"
        raise ValueError(msg)
    return page_ids


def _summary_of(doc_id: DocumentId, index: _Index, /) -> DocumentSummary:
    """Assemble one document's summary from its decoded sidecars.

    Parameters
    ----------
    doc_id
        Identity of the document.
    index
        Its decoded sidecars.

    Returns
    -------
    DocumentSummary
        Identity, metadata, and page identities in document order.
    """
    return DocumentSummary(doc_id=doc_id, metadata=index.metadata, pages=index.page_ids)


def _position_of(doc_id: DocumentId, page_id: PageId, index: _Index, /) -> int:
    """Resolve a page identity to its position in document order.

    Parameters
    ----------
    doc_id
        Identity of the owning document.
    page_id
        Identity to resolve.
    index
        The document's decoded sidecars.

    Returns
    -------
    int
        Zero-based position.

    Raises
    ------
    PageNotFound
        The document claims no page with this identity.
    """
    try:
        return index.page_ids.index(page_id)
    except ValueError as err:
        raise PageNotFound(
            document_uuid=doc_id.uuid,
            page=page_id.uuid,
            page_count=len(index.page_ids),
        ) from err
