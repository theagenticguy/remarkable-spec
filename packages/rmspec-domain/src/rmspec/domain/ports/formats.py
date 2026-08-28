"""Ports for the formats slice: getting parsed reMarkable documents into the app layer.

Two Protocols live here and nothing else:

:class:`DocumentRepository`
    The app-facing altitude. Identity in, domain models out. Every use case that
    needs a document, a page, or the fingerprint that keys a cache talks to this.
:class:`PageCodec`
    Scene bytes in, page content out. The single seam a ``.rm`` parser is bound
    to, so exactly one package in the system imports ``rmscene``. Kept as a
    separate port because ``rmspec inspect rm <path>`` decodes a user-supplied
    file that has no document identity and no xochitl root at all.

Notes
-----
Decisions that are settled, recorded so later readers do not re-open them.

**Why the repository is coarse.** A port pair of "fetch blobs" plus "decode
blobs" would make the app layer sequence the xochitl on-disk algorithm: read
``.content``, decode it, walk its page refs, read each page file, reconcile
``.pagedata`` by index. That algorithm and the layout it encodes are firmware
knowledge and stay inside the adapter. The adapter is therefore free to batch,
to avoid N+1 round trips, and to decide which artifacts are required versus
optional without any use case changing.

**No sidecar codec port.** Decoding ``.metadata`` / ``.content`` / ``.pagedata``
is ``json`` plus pydantic validation, and both are already legal inside this
package, so the decode belongs on the domain models themselves (a ``decode``
classmethod over ``bytes``), not behind a Protocol with one possible
implementation. Filenames, suffixes, and encodings stay inside the formats
adapter; ``rmspec inspect metadata <path>`` reads bytes and calls the model
classmethod directly. A port with one implementation and no swap candidate is
ceremony, and a five-method port whose method names *are* the vendor's filename
suffixes leaks the storage layout into the app layer as well.

**No byte-source port, no artifact-kind enum.** ``bytes`` are useless above the
formats adapter, since nothing outside it may import a scene parser, so a byte
port forces every caller to hold a second collaborator and correlate opaque
blobs between two fakes. It also cannot be implemented by the device: the USB
web API is five routes, ``GET /download/{id}/{name}`` always returns
``application/pdf``, and no route yields scene bytes. Remote reads are a mirror
concern belonging to the device slice, not an implementation of this port.

**No version probe and no capability query.** Nothing branches on the scene
format version, so :class:`PageCodec` has no ``probe_version`` and publishes no
supported-version set. A multi-version codec is a composite adapter that
dispatches internally; the observed version is carried on the raised error, not
returned for callers to compare against integers.

**Failure altitude.** ``load`` and ``load_page`` raise only when the request
cannot be answered at all: no such document, no such page, unreadable store,
undecodable document-level metadata. A page that is present but degraded is a
*value*: the returned page carries its defects, so "file absent", "page blank",
and "page unparsable" are three distinct states rather than one silently empty
layer list. Strict-versus-lenient handling is then an explicit policy on the use
case reading ``defects``, never a hidden ``except Exception`` and never a
policy argument threaded through this port.

**Scope is not a property of a port.** Whether an implementation is bound at
``Scope.APP`` (a local xochitl root: nothing to close) or ``Scope.REQUEST`` (a
live transport that must close) is decided in the composition root. Nothing here
annotates it.

**Adapter availability is a composition-time fact.** A missing optional
dependency must surface while the container is built -- as
``MissingAdapterDependency``, naming the package and the extra that ships it --
so no implementation of these Protocols may be a null object that raises on
call. Deferring the failure into a method body is the defect this design
removes.

**Accepted constraint.** These ports isolate *the parser*, not *the format*. The
page model still carries per-point capability fields that came from the v6 wire
format, so a second codec omits fields rather than fabricating them. Binding a
different codec does not make the format free.

This module expects ``DocumentId``, ``PageId``, ``DocumentSummary``,
``Document``, ``Page``, and ``PageContent`` from :mod:`rmspec.domain.models`, and
names its errors from :mod:`rmspec.domain.errors`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rmspec.domain.models import (
        Document,
        DocumentId,
        DocumentSummary,
        Page,
        PageContent,
        PageId,
    )

__all__ = ["DocumentRepository", "PageCodec"]


class DocumentRepository(Protocol):
    """Read access to reMarkable documents, addressed by domain identity.

    Implementations own everything the app layer must not know: where documents
    live, what the artifacts are called, which of them are optional, how a page
    file is decoded, and what a parse failure means for one page. Callers pass
    identities and receive domain models.

    Notes
    -----
    A fake is a mapping of identity to prebuilt models; no byte fixture and no
    second collaborator are needed to exercise a use case against it.
    """

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """List every document in the store, cheaply.

        Returns a summary rather than a full document so that listing and tree
        rendering never pay to decode scene bytes. Summaries carry page
        identities in document order, which is how a caller addresses "page 3"
        for :meth:`load_page` without decoding pages 1 and 2.

        The result is a materialised tuple, not a lazy iterator: an iterator
        outliving a request-scoped transport would be read after close, and it
        would defer the store-unavailable failure into the caller's loop.

        Returns
        -------
        tuple[DocumentSummary, ...]
            One summary per document, in unspecified order. Empty if the store
            holds no documents.

        Raises
        ------
        DocumentStoreUnavailable
            The store could not be reached or read at all.
        """
        ...

    def load(self, doc_id: DocumentId, /) -> Document:
        """Load one whole document, with every page decoded.

        Parameters
        ----------
        doc_id
            Identity of the document to load.

        Returns
        -------
        Document
            The assembled aggregate: metadata, ordered pages, and the defects
            recorded for each page. Pages that are absent on the store and pages
            that failed to decode are represented as such rather than as empty
            ones.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        MalformedDocument
            Document-level metadata could not be decoded, so no aggregate can be
            assembled. Per-page problems never raise; they arrive as defects.
        DocumentStoreUnavailable
            The store could not be reached or read at all.
        """
        ...

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Load a single page of a document.

        Present so that rendering or reading one page does not decode the whole
        document.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page, as listed in document order on the document's
            summary or aggregate.

        Returns
        -------
        Page
            The page, including its template, its decoded content, and the
            defects accepted while decoding it. A page with no strokes is
            returned as an empty page; a page whose artifact is missing raises.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        PageNotFound
            The document exists but claims no such page, or the page's artifact
            is absent on the store.
        DocumentStoreUnavailable
            The store could not be reached or read at all.
        """
        ...

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Fingerprint a page's stored bytes, for cache invalidation.

        The bytes themselves never cross this boundary, so the store computes
        the digest. Callers combine it with whatever else changes their result --
        model identity, prompt revision, render resolution -- to form a cache
        key; a fingerprint alone is not a cache key.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page to fingerprint.

        Returns
        -------
        str
            Lowercase hex SHA-256 of the page's stored bytes, exactly as read,
            before any decoding.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        PageNotFound
            The document exists but claims no such page, or the page's artifact
            is absent on the store.
        DocumentStoreUnavailable
            The store could not be reached or read at all.
        """
        ...


class PageCodec(Protocol):
    """Decode one page's scene bytes into domain page content.

    The only port in the system that takes a wire format as input, and the only
    place a scene parser is bound. Bytes in keeps two things possible that
    identity-addressed loading cannot serve: decoding a file the user named on
    the command line, and decoding bytes that were never on a local store.

    Notes
    -----
    Implementations translate every parser failure into the domain error tree; no
    parser type, exception, or log record escapes. Version dispatch, if a second
    scene version ever needs it, is internal to a composite implementation --
    this Protocol has exactly one method so a fake has exactly one behaviour.
    """

    def decode_page(self, raw: bytes, /) -> PageContent:
        """Decode complete scene bytes into layers, strokes, and text.

        Parameters
        ----------
        raw
            The entire, uninterpreted contents of one page's scene file.

        Returns
        -------
        PageContent
            Layers, strokes, and text blocks, together with the defects the
            decode had to accept -- an unknown pen or colour that fell back to a
            default, dropped items, a synthesised layer. Degradations are values
            here, so nothing is lost to a log line.

        Raises
        ------
        UnsupportedPageFormat
            The bytes are a scene file of a version this codec does not decode.
            The observed version is carried on the error, so no caller compares
            raw version numbers.
        CorruptPageData
            The bytes are not a decodable scene file: truncated, malformed, or
            structurally invalid.
        """
        ...
