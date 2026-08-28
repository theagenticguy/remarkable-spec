"""Ports for the formats slice: getting parsed reMarkable documents into the app layer.

Two Protocols and one sentinel live here and nothing else:

:class:`DocumentRepository`
    The app-facing altitude. Identity in, domain models out. Every use case that
    needs a document, a page, or the fingerprint that keys a cache talks to this.
:class:`PageCodec`
    Scene bytes in, page content out. The single seam a ``.rm`` parser is bound
    to, so exactly one package in the system imports ``rmscene``. Kept as a
    separate port because ``rmspec inspect rm <path>`` decodes a user-supplied
    file that has no document identity and no xochitl root at all.
:data:`ABSENT_ARTIFACT_FINGERPRINT`
    The digest :meth:`DocumentRepository.page_fingerprint` returns for a page the
    document claims but stores no scene artifact for, so a blank page of an
    annotated PDF has a cache key like every other page instead of an exception
    every caller has to special-case.

Notes
-----
Decisions that are settled, recorded so later readers do not re-open them.

**Why the repository is coarse.** A port pair of "fetch blobs" plus "decode
blobs" would make the app layer sequence the xochitl on-disk algorithm: read
``.content``, decode it, walk its page refs, read each page file, reconcile
``.pagedata`` by index. That algorithm and the layout it encodes are firmware
knowledge and stay inside the adapter. The adapter is therefore free to batch,
to avoid N+1 round trips, and to decide which artifacts are required versus
optional without any use case changing. Decoding scene bytes is the one step it
delegates rather than owns: a repository adapter calls :class:`PageCodec` instead
of inlining a parser, so the parser stays bound at exactly one seam.

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

**Failure altitude.** An exception means the request cannot be answered at all:
no such document, an unreadable store, document-level metadata that will not
decode, or a page id the document does not claim. Everything the store *can*
answer is a value on the returned page, and there are three such values rather
than one silently empty layer list: a page with ink (content plus the defects
accepted while decoding it), a page the document claims with no scene artifact
stored (``content=None`` plus ``PageDefectCode.ARTIFACT_ABSENT``), and a page
whose artifact is present but will not decode (``content=None`` plus
``PageDefectCode.CONTENT_UNDECODABLE``). The two contentless states are the same
values on the whole-document path and the single-page path, so
:meth:`DocumentRepository.load_page` is a cheaper :meth:`DocumentRepository.load`
for one page and never a different contract, and
:meth:`DocumentRepository.page_fingerprint` keys an artifactless page instead of
refusing it. Strict-versus-lenient handling is then an explicit policy on the use
case reading ``defects``, never a hidden ``except Exception`` and never a policy
argument threaded through this port.

**One meaning for PageNotFound.** This narrows the error's own docstring in
:mod:`rmspec.domain.errors`, which also lists "a claimed page whose artifact is
absent": this port raises it for a page id the document does not claim, and never
for a stored artifact that is missing. A page of an annotated PDF that was never
written on is routine, not an error; its template is a fact only the store holds,
so a caller handed an exception could neither fabricate the page nor recover
without calling ``load`` and paying the whole-document decode that ``load_page``
exists to avoid. The error's own ``page_count`` field fits the meaning that
survives -- a page id outside what the document claims.

**The codec is handed a label, not an identity.**
:meth:`PageCodec.decode_page` takes the bytes plus a caller-supplied
``page_ref``, used for exactly one purpose: filling the ``page_uuid`` field that
``CorruptPageData`` and ``UnsupportedPageFormat`` both require. Without it no
conforming codec could construct the errors it is documented to raise, and every
implementation including the fake would pass ``page_uuid=""`` and render "page
is not a decodable scene file". The label is required and has no default, so
nothing is invented inside the port: a repository adapter passes the page uuid it
already holds, and ``rmspec inspect rm <path>`` passes the path it read. The
codec still performs no identity resolution -- it never looks the ref up, never
validates it, and returns nothing derived from it. The error field is the
narrower name of the two; widening it to ``page_ref`` is a change in
:mod:`rmspec.domain.errors`, not here.

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

from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rmspec.domain.models import (
        Document,
        DocumentId,
        DocumentSummary,
        Page,
        PageContent,
        PageId,
    )

__all__ = ["ABSENT_ARTIFACT_FINGERPRINT", "DocumentRepository", "PageCodec"]

ABSENT_ARTIFACT_FINGERPRINT: Final = "absent"
"""Fingerprint of a page the document claims but stores no scene artifact for.

Returned by :meth:`DocumentRepository.page_fingerprint` and
:meth:`DocumentRepository.page_fingerprints` for that state, so every claimed page
has a cache key. Deliberately not a hex digest: no content hash can collide with
it, and a caller may compare against it to tell "nothing was ever drawn here"
from "these bytes hashed to this". A cache row keyed on it stays valid for as long
as the page has no artifact, and the fingerprint changes the moment one appears.
"""


class DocumentRepository(Protocol):
    """Read access to reMarkable documents, addressed by domain identity.

    Implementations own everything the app layer must not know: where documents
    live, what the artifacts are called, which of them are optional, how a page
    file is decoded, and what a parse failure means for one page. Callers pass
    identities and receive domain models.

    Notes
    -----
    A fake is a mapping of identity to prebuilt models; no byte fixture and no
    second collaborator are needed to exercise a use case against it. Three
    branches of the contract are unreachable for a fake that is *only* a mapping
    of page id to decoded page, so a fake needs a knob for each:

    1. A page the summary claims with no stored artifact, so ``load_page``
       returns the contentless ``ARTIFACT_ABSENT`` page and ``page_fingerprint``
       returns :data:`ABSENT_ARTIFACT_FINGERPRINT`. This is the routine
       annotated-PDF case, and a mapping keyed only on decoded pages cannot say
       it: the page id must be present on the summary while its content is not.
    2. Fingerprints held independently of page content, so a cache-hit or
       cache-miss test is not tautological -- the same content with a changed
       fingerprint, and changed content with the same fingerprint, are both
       states a real store produces.
    3. An "unavailable" switch, so ``DocumentStoreUnavailable`` is reachable
       without deleting fixtures mid-test.

    ``PageNotFound`` needs no knob: it is what asking for a page id the summary
    does not list produces.
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

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Summarise one document, without decoding any of its pages.

        Present so that knowing one document's page order -- to address "page 3"
        for :meth:`load_page`, or to iterate the pages of one document -- costs
        one document's metadata rather than a walk of the whole store, and less
        than ``load``, which decodes every page.

        Parameters
        ----------
        doc_id
            Identity of the document to summarise.

        Returns
        -------
        DocumentSummary
            The same summary :meth:`list_documents` would carry for this
            document, with page identities in document order.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        MalformedDocument
            Document-level metadata could not be decoded, so no page order can
            be established.
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
            recorded for each page. A page whose artifact is absent on the store
            and a page whose artifact would not decode are both returned as
            contentless pages carrying ``ARTIFACT_ABSENT`` or
            ``CONTENT_UNDECODABLE``, never as empty ones.

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
        document. It returns exactly the ``Page`` :meth:`load` would place at
        this page id, so no caller has two code paths for one page.

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
            The page as the store can produce it: its template, its content when
            there was an artifact that decoded, and the defects accepted on the
            way. Three states, all values:

            * ink present -- decoded ``content``, plus any defects the decode
              accepted;
            * claimed with no stored artifact -- ``content=None``,
              ``template_name`` from the document's page data, and
              ``ARTIFACT_ABSENT``. The blank page of an annotated PDF, and the
              common case;
            * artifact present but undecodable -- ``content=None`` and
              ``CONTENT_UNDECODABLE``. The implementation translates
              ``CorruptPageData`` and ``UnsupportedPageFormat`` from
              :class:`PageCodec` into that defect; neither error leaves this
              method.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        PageNotFound
            The document exists and claims no page with this identity. Only that:
            a claimed page with nothing stored for it is the contentless page
            above, not this error.
        MalformedDocument
            Document-level metadata could not be decoded, so the page cannot be
            resolved to an artifact and "claims no such page" cannot be decided.
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
            An opaque, non-empty token over the page's stored bytes as read,
            before any decoding: it changes whenever those bytes change and never
            changes while they do not. Lowercase hex SHA-256 of the bytes is the
            obvious implementation; a store that can only offer an ETag or a
            revision counter satisfies this too, which is why callers must treat
            the value as opaque -- never parsed, never assumed to be a hash of a
            particular length, and not comparable across implementations, so a
            cache shared between stores keys on the store's identity as well.
            :data:`ABSENT_ARTIFACT_FINGERPRINT` when the document claims the page
            but stores no scene artifact for it.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        PageNotFound
            The document exists and claims no page with this identity. A claimed
            page with nothing stored for it returns
            :data:`ABSENT_ARTIFACT_FINGERPRINT` instead.
        MalformedDocument
            Document-level metadata could not be decoded, so the page cannot be
            resolved to an artifact.
        DocumentStoreUnavailable
            The store could not be reached or read at all.
        """
        ...

    def page_fingerprints(self, doc_id: DocumentId, /) -> Mapping[PageId, str]:
        """Fingerprint every page of one document in one pass.

        Present because deciding what is already cached for a long document is
        otherwise one store probe per page: a 200-page notebook asks 200 times
        before any work starts. An implementation answers this from a single
        directory listing or a single remote call, which is the batching freedom
        the coarse repository exists to keep.

        Parameters
        ----------
        doc_id
            Identity of the document whose pages to fingerprint.

        Returns
        -------
        Mapping[PageId, str]
            One entry per page identity the document claims, in document order,
            each value exactly what :meth:`page_fingerprint` would return for
            that page -- :data:`ABSENT_ARTIFACT_FINGERPRINT` included. The keys
            are the page identities on the document's summary, so a caller needs
            no second call to align them.

        Raises
        ------
        DocumentNotFound
            No document with this identity exists in the store.
        MalformedDocument
            Document-level metadata could not be decoded, so the document's pages
            cannot be enumerated.
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
    this Protocol has exactly one method so a fake is one canned return value
    plus a way to make it raise each of the two documented errors.
    """

    def decode_page(self, raw: bytes, page_ref: str, /) -> PageContent:
        """Decode complete scene bytes into layers, strokes, and text.

        Parameters
        ----------
        raw
            The entire, uninterpreted contents of one page's scene file.
        page_ref
            What to call these bytes when reporting a failure: the page uuid when
            the caller holds one -- a repository adapter always does -- and the
            path the user typed when no uuid exists, as for
            ``rmspec inspect rm <path>``. Required and without a default, because
            both errors below take the value as a mandatory field, and an
            implementation that had to invent it would render "page  is not a
            decodable scene file" for the one case this port exists to serve. It
            is never resolved, never validated, and never reflected in the return
            value; passing a different ref cannot change what is decoded.

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
            The bytes are a scene file of a version this codec does not decode,
            reported against ``page_ref``. The observed version is carried on the
            error, so no caller compares raw version numbers.
        CorruptPageData
            The bytes are not a decodable scene file -- truncated, malformed, or
            structurally invalid -- reported against ``page_ref``.
        """
        ...
