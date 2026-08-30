"""Ports for the formats slice: getting parsed reMarkable documents into the app layer.

Three Protocols, one value object and one sentinel live here and nothing else:

:class:`DocumentRepository`
    The app-facing altitude. Identity in, domain models out. Every use case that
    needs a document, a page, or the fingerprint that keys a cache talks to this.
:class:`PageCodec`
    Scene bytes in, page content out. The single seam a ``.rm`` parser is bound
    to, so exactly one package in the system imports ``rmscene``. Kept as a
    separate port because ``rmspec inspect rm <path>`` decodes a user-supplied
    file that has no document identity and no xochitl root at all.
:class:`SceneAppender`
    Scene bytes plus ink in, scene bytes out. The write direction of the same
    format, and a *separate* port rather than a second method on
    :class:`PageCodec` -- see below.
:class:`SceneEdit`
    What :meth:`SceneAppender.append_strokes` returns: the new bytes plus the two
    decisions the adapter made on the caller's behalf.
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
blobs between two fakes. Remote reads stay a device-slice concern rather than an
implementation of this port -- but on a narrower ground than this paragraph
originally gave.

The original ground was that the device *cannot* serve scene bytes: "the USB web
API is five routes, ``GET /download/{id}/{name}`` always returns
``application/pdf``, and no route yields scene bytes". Measured against firmware
3.27.3.0 that is false on all three counts. The route table is six families; the
third path segment is a format selector rather than a filename, so
``GET /download/{id}/rmdoc`` returns ``application/zip``; and that archive carries
one ``<docUUID>/<pageUUID>.rm`` per page. A route does yield scene bytes.

The conclusion survives anyway, and it is worth saying why, because "the device
can now do it" is exactly the argument that would otherwise reopen this. The
device's own transport already hands the application layer a decoded,
ordered :class:`~rmspec.domain.ports.device.DocumentSourceBundle`; a byte-source
port here would give it a second, lower-altitude way to say the same thing, and
the two would drift. What changed is not the shape of this port but the *value*
of the device slice: :class:`~rmspec.domain.ports.device.RawBundleSource` is now
bindable over USB as well as SSH, which is where the capability belongs. See
``specs/device/3.27.3.0/http.json``, claim ``artifact:.rmdoc archive shape``.

**No version probe and no capability query.** Nothing branches on the scene
format version, so :class:`PageCodec` has no ``probe_version`` and publishes no
supported-version set. A multi-version codec is a composite adapter that
dispatches internally; the observed version is carried on the raised error, not
returned for callers to compare against integers.

**Writing is its own port, and the reason is mechanical.** :class:`PageCodec` has
exactly one method so that a conforming fake is one canned return value, and every
fake in the workspace is annotated against it -- ``rmspec-app``'s render tests
include a one-method double declared as a ``PageCodec``. A second method there would
stop all of them satisfying the port they were written against, to publish something
only a writer calls. So the encoder gets :class:`SceneAppender`, and the split says
something true rather than merely convenient: reading a page is available everywhere
and safe by construction, while writing one is a capability a composition root binds
deliberately, over a transport that has to hold a precondition, and its failures are a
different set. A container that only reads never resolves the writer at all.

**The write surface is additive and nothing else.** There is no method that edits,
reorders or removes what is already on a page. That is not an unfinished surface: the
tablet's own guidance is that its reader must not be running while its files are
touched, so every write here races a human who may be drawing. Appending cannot
destroy an existing stroke even when that race is lost, which is the only class of
edit for which that is true, and it is the reason the port stops there. The
transport's own precondition -- capture the artifact's identity at read time,
re-check it immediately before the write, refuse if it moved -- is a device-slice
concern and is not restated here.

**Ink, not typed text, and that is a measurement.** A page-scoped typed-text block
written into a real page by a foreign author was *preserved* by firmware 3.27.3.0
across the tablet's own re-save, at the exact position set, with the foreign author
id intact -- and was never *drawn*. Strokes are what the tablet renders. So a reply a
human can read has to be ink, and a port method that wrote text would put bytes on a
page nobody can see. Text becomes ink by tracing glyph outlines into polylines, which
happens above this port; what arrives here is
:class:`~rmspec.domain.models.Stroke`.

**Coordinates are the domain's, not the wire's.** :class:`SceneAppender` takes strokes
whose samples are already in screen units with x measured from the centre of the page,
because that is what :class:`~rmspec.domain.models.Point` means everywhere else and
what ``ports/render.py`` draws. A port that took normalised ``[0, 1]`` coordinates and
scaled them inside the adapter would mean the ink that got written is not the ink that
got previewed, and the centre-origin convention would be stated in two places.

**No bounds check, measured rather than assumed.** The page's own scene info declares a
paper size, and it is tempting to refuse ink outside it, since ink off the page is
invisible to the human -- the same defect as the text block above. The reference corpus
refutes it: 13 of its 30 non-empty pages carry strokes outside the declared x range and
17 outside the y range, one of them reaching y 81,159 on a page that declares 2,160.
So a coordinate range is not a validity test on this format, and the honest check is to
render the proposed page with this project's own renderer before writing it.

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
``Document``, ``Page``, ``PageContent`` and ``Stroke`` from
:mod:`rmspec.domain.models`, and names its errors from
:mod:`rmspec.domain.errors`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rmspec.domain.models import (
        Document,
        DocumentId,
        DocumentSummary,
        Page,
        PageContent,
        PageId,
        Stroke,
    )

__all__ = [
    "ABSENT_ARTIFACT_FINGERPRINT",
    "DocumentRepository",
    "PageCodec",
    "SceneAppender",
    "SceneEdit",
]

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
            here, so nothing is lost to a log line. Typed text arrives on
            ``PageContent.text_blocks``, page-level, because the scene block
            carrying it names no layer; ``Layer.text_blocks`` is the format's
            separate layer-owned text item, which no current parser decodes. A
            consumer of "the text on this page" reads both.

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


class SceneEdit(BaseModel, frozen=True, extra="forbid"):
    """One page's new scene bytes, plus the decisions the writer made for the caller.

    A receipt rather than a wrapper. The bytes are what a transport writes; the other two
    fields are the two things :meth:`SceneAppender.append_strokes` decided that the caller
    did not, and both are facts about the file rather than about the call. Reporting them is
    what keeps "which identity wrote this, and where did it land" answerable without
    re-decoding the result and guessing.

    There is deliberately no field echoing the input back -- no original length, no stroke
    count. A receipt that restates its own request invites a caller to check the request
    instead of the result.
    """

    scene: bytes = Field(min_length=1)
    """The whole page, ready to write. Never a patch, a diff or a tail.

    An implementation that appends may make the input a literal prefix of this, and one
    that re-encodes may not; a caller cannot tell and must not try, because "is my input a
    prefix of the output" is a property of the adapter rather than of the format. Write
    these bytes as the file's entire new contents.
    """

    author_id: int = Field(gt=0)
    """The CRDT author component every id in the appended ink was minted under.

    Positive, and greater than every author already present in the artifact, which is what
    makes the minted ids collision-free by construction rather than by searching for an
    unused sequence number. Measured on firmware 3.27.3.0: a foreign author id written this
    way was accepted and *kept* through the tablet's own re-save of the page, so this is the
    identity a later reader will attribute the ink to.
    """

    layer_index: int = Field(ge=0)
    """Which layer of the page the ink was attached to, indexed as the codec reports layers.

    The same index into ``PageContent.layers`` that decoding :attr:`scene` produces, so a
    caller can render exactly the layer it just wrote into. Present because the layer is a
    choice the adapter makes -- a page has one or more, the caller named none -- and an
    unreported choice is one nobody can preview or disagree with.
    """


class SceneAppender(Protocol):
    """Add ink to a page that already exists, and hand back the whole page's new bytes.

    The write direction of :class:`PageCodec`, and the narrowest surface that serves it:
    one method, additive only, whole bytes in and whole bytes out. It performs no I/O and
    holds no state -- the bytes arrive from a transport and the result goes back to one --
    so an implementation is cheap to construct and a fake is one canned
    :class:`SceneEdit`.

    Notes
    -----
    **Every failure is a refusal, never a repair.** A page is the only copy of something a
    human made by hand, so an implementation that cannot do exactly what was asked raises
    instead of doing something adjacent. In particular it must not invent a layer for a
    scene that has none, must not create a scene for an artifact that is empty, and must
    not return the input unchanged when there was nothing to add.

    **The lossless precondition is per call.** An implementation must establish, for the
    bytes in hand, that this build's own reader and writer agree about them before it
    returns anything derived from them -- and raise ``SceneRewriteUnsafe`` when they do not.
    That is a checked fact about one artifact rather than an assumption about a firmware and
    a parser version, and it is checked on every call because a page whose ink was silently
    dropped looks exactly like a page.

    **Nothing may key on a scene id across a round trip.** The tablet renumbers them: a
    measured page's layer moved from author 0 / sequence 11 to author 1 / sequence 334
    across xochitl's own re-save. An implementation reads every id it needs out of the bytes
    it was handed, on every call, and caches none of them -- and a caller that stored
    :attr:`SceneEdit.layer_index` from an earlier call must re-derive it rather than trust
    it against later bytes.
    """

    def append_strokes(
        self, raw: bytes, page_ref: str, /, *, strokes: tuple[Stroke, ...]
    ) -> SceneEdit:
        """Append strokes to a page's scene bytes.

        Parameters
        ----------
        raw
            The entire, uninterpreted contents of the page's scene file, read immediately
            before this call. Checking or amending a stale copy is meaningless: the bytes
            passed here are the bytes the result is derived from, and the transport's own
            precondition is what establishes that they are still the bytes on the device.
        page_ref
            What to call these bytes when reporting a failure: the page uuid when the caller
            holds one, the path the user typed when it does not. Required and without a
            default, for the same reason as on :meth:`PageCodec.decode_page` -- every error
            below takes it as a mandatory field. Never resolved, never validated, and never
            reflected in the returned bytes.
        strokes
            The ink to add, in draw order, with samples already in screen units. Must not be
            empty. Each stroke lands above everything already on its layer, and the tuple's
            own order is preserved, so the last stroke draws last. A stroke with no samples
            is legal and means a tap. ``Stroke.color_override`` is carried through to the
            wire field it came from, so a decode-then-append round trip does not silently
            turn a coloured highlight yellow.

        Returns
        -------
        SceneEdit
            The whole page's new bytes, the author id the ink was minted under, and the
            layer it landed on.

        Raises
        ------
        UsageError
            ``strokes`` is empty. A write that appends nothing would report success for a
            transport round trip, a snapshot and a rewrite that changed no ink, which is
            worse than a refusal.
        CorruptPageData
            The bytes are not a decodable scene file, so there is nothing to append to.
        SceneRewriteUnsafe
            The bytes decode and this build will not write them: a zero-byte artifact, which
            is a scene to *create* rather than one to amend; a round trip this build cannot
            reproduce; or a scene with no layer that ink could be attached to and be seen on.
        """
        ...
