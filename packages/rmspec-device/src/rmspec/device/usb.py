"""The USB web API transport, and the three ports it can honestly bind.

Firmware 3.27.3.0 answers on ``10.11.99.1:80`` over the USB-C gadget interface with a
route table closed at six path families. This module binds three of the four Protocols in
``rmspec.domain.ports.device`` to that table -- :class:`UsbCatalog`,
:class:`UsbBundleSource` and :class:`UsbFacts` -- over one injected ``httpx.Client``. It
decodes nothing itself: entry shapes belong to ``_wire``, archive members to ``_archive``,
the page order to ``_pages``, and failure classification to ``_errors``. What is left here
is the part that is genuinely about *moving bytes over HTTP*: the request, the breadth-first
walk the route table forces, and the assembly of a bundle from members somebody else routed.

The client is injected and never constructed
--------------------------------------------
:class:`UsbWebApi` takes an ``httpx.Client``. It does not build one, does not set a
timeout, and does not own a lifetime -- the composition root in step 6 does, and a test
passes ``httpx.Client(transport=httpx.MockTransport(handler))``. Legacy
``WebAPI._get_client`` built a fresh client per call behind a lazy ``import httpx``, so
every request paid a connection setup, the timeout was a constructor default nobody could
override per call, and a missing extra surfaced as an ``ImportError`` from inside a command
body rather than as ``MissingDependencyError`` at composition.

Relocated from ``src/remarkable_spec/device/web_api.py``, with every divergence named
------------------------------------------------------------------------------------
``WebAPI`` had eight methods. Five of them have no successor here, and the three that do
are all changed.

1. **``download_rmdoc`` never asked for an archive.** It sent
   ``GET /download/{id}/placeholder`` with ``Accept: application/zip``, believing the third
   path segment was the *output filename*. The segment is a format selector, so the
   ``Accept`` header was irrelevant and the request was wrong. On firmware 3.27.3.0 the
   measured answer to any third segment other than the exact lowercase ``pdf`` or ``rmdoc``
   -- a bare filename included -- is ``400 {"error": "Filetype not supported"}``
   (``specs/device/3.27.3.0/http.json``, ``route:GET /download/{id}/{format}``), which
   ``raise_for_status()`` then turned into a bare ``httpx.HTTPStatusError``. So legacy could
   not have produced a ``.rmdoc`` on this firmware at all. This module spells the selector
   ``rmdoc`` -- through ``_archive.RMDOC_ROUTE``, the one place it is written -- and sends
   no ``Accept`` header, because the selector already chose the format.
2. **``list_all_documents`` swallowed every per-folder failure and kept no visited set.**
   Its ``except Exception: continue`` made a shrinking listing indistinguishable from a
   complete one, and without a visited set the silent root fallback (below) re-enqueues the
   root's folders forever. Both are fixed by :class:`_Walk`: a refused folder becomes one
   ``SkippedEntry``, and an id already placed is never placed or enqueued twice.
3. **``search`` read ``VisssibleName``, with three ``s``.** No firmware writes that key, so
   the name half of every search term matched nothing. There is no search here at all: the
   route table has no search route, and filtering a listing is an application-layer
   concern that ``rmspec.app`` owns in step 6.
4. **Nothing writes to the filesystem.** ``download_pdf`` and ``download_rmdoc`` took an
   output ``Path`` and called ``output.write_bytes``. No port in this system touches a
   filesystem, so ``get`` returns bytes and the CLI owns every sink -- which is also why no
   method here declares ``OSError``.
5. **``download_pdf``, ``get_thumbnail``, ``upload_pdf`` and ``upload_epub`` are absent.**
   The device-rendered PDF is an export concern, not a source one; ``/thumbnail/{id}``
   advertises ``image/jpeg`` and returns PNG and no port consumes it; and the two uploads
   are the subject of the next section.
6. **``raise_for_status()`` is gone from all seven call sites.** Every failed response goes
   through ``_errors.translate_http``, which classifies on the device's own
   ``{"error": ...}`` message rather than on the status code.

There is no USB uploader, and that absence is the design
--------------------------------------------------------
``DocumentUploader`` is **not** bound here, and this module exports nothing that writes.
``POST /upload`` has never been probed in any form: the server ignores the request method
(``protocol:methods``), so a ``GET`` to that path could not have been proven non-mutating,
and its multipart field name, accepted content types and response body are all unmeasured.
Shipping a guessed multipart body against the user's only copy of their notes is not a
trade this package makes. ``ports/device.py`` opens by arguing that capability asymmetry is
expressed as *which ports exist*, so the composition root fails to bind and raises
``DeviceOperationUnsupported(operation="upload", supported_by=(TransportKind.SSH,))``, and
the shell says "retry over SSH". :class:`UsbWebApi` therefore has exactly two verbs, ``get``
and ``head``, and ``test_device_usb.py`` asserts that set so a later reader cannot "fix"
the omission without failing a test.

The breadth-first walk, and the silent root fallback that makes it subtle
------------------------------------------------------------------------
``GET /documents/`` returns the **root only** -- 9 of 41 entities on the reference store.
``GET /documents/{parentId}`` resolves a folder id, but for a document id or an unknown id
the handler falls back to the **byte-identical root listing** with status 200 and logs
nothing, so a 200 is not proof the id was recognised. Nesting reaches depth 2 (9 at the
root, 30 at depth 1, 2 at depth 2), so the walk is load-bearing rather than a formality.

Two rules make it terminate and stay correct, and the *order* of the second is what makes
it exact. Every entry a sub-listing returns is discarded unless its raw ``Parent`` equals
the id that was asked for, and the comparison happens **before** ``decode_entries`` --
:func:`_only_children_of` filters the raw json array and re-serialises the survivors. The
alternative, decoding first and filtering on ``parent_uuid``, double-counts a
``SkippedEntry`` from a fallback listing: a skipped entry has no parent to compare, so the
root's unreadable entries would be reported once per folder walked. Second, a
``visited: set[str]`` of ids already *placed* means no entry is placed twice and no folder
is enqueued twice, so a device that reported one child under two parents cannot loop.

A folder segment is percent-encoded, and that is not decoration
--------------------------------------------------------------
Every identifier interpolated into a route goes through ``urllib.parse.quote(..., safe="")``.
``DeviceFolder.uuid`` is only constrained non-empty, and ``load_bundle`` takes its argument
from a CLI, so an identifier carrying a ``/`` is representable. Measured against httpx
0.28.1: ``http://10.11.99.1/documents/../upload`` is normalised to the path ``/upload``
before it reaches the wire, while ``quote("../upload", safe="")`` sends
``/documents/..%2Fupload`` -- which this firmware treats as an unrecognised folder id and
answers with the root listing, which the parent filter then discards. This is the URL
analogue of the shell hole ``addresses.RemotePath`` exists to close, and on a server that
ignores the request method a read command reaching ``/upload`` is not a hypothetical.

``trashed`` is always ``False``, and there is no sentinel branch
---------------------------------------------------------------
The USB API filters trashed entries out entirely: a full walk reaches 41 of the 42
on-disk entities, the one missing being the only one whose on-disk metadata says
``parent: "trash"``, and no entry in any listing at any depth ever carries
``Parent == "trash"``. ``False`` is therefore *accurate* for every entry this transport
returns, ``_wire`` sets it explicitly, and sentinel-handling code here would be
unreachable -- unreachable code cannot be covered, so it is deliberately absent. The
legacy ``list_documents(parent="trash")`` filter went with it.

Wire facts this module honours
------------------------------
The advertised charset is a lie: every json body is labelled ``charset=ISO-8859-1`` while
carrying UTF-8 document titles. Nothing here reads ``Content-Type`` at all --
``json.loads`` on ``bytes`` detects UTF-8, so the header cannot mislead it.

A successful ``/download`` sends **both** ``Content-Length`` and
``Transfer-Encoding: chunked``. httpx handles that; this module does not assert that one
excludes the other, and reads the announced length only to detect a body that arrived
*short*, which is ``DeviceTransferInterrupted`` and never a partial bundle.

``HEAD`` returns identical status and ``Content-Type`` with a **zero-length body**, which
makes it the safe existence probe -- and makes it the one response that must never be
length-checked, since its ``Content-Length`` describes the body it deliberately omitted.
:meth:`UsbWebApi.head` therefore reads no body and checks no length.

The three page states, and the orphan layers
--------------------------------------------
:meth:`UsbBundleSource.load_bundle` walks the page order decoded from the archive's own
``.content`` and looks each page up in the archive's members. A page with no member and a
page whose member is **zero bytes** both become ``scene=None``:
``DevicePageSource.scene`` documents ``None`` as "the page carries no ink", and a zero-byte
artifact is exactly that (86 of 194 real ``.rm`` files). A ``.rm`` member the page order
does not claim is an orphan layer left behind by an edit and is **dropped** -- measured, 16
members for 10 pages, so iterating the archive instead of the page order renders ghost
pages.

``document`` comes from the catalog, never from the archive's ``.metadata``, so
``bundle.document`` is equal to what ``get_document`` returns for the same identifier. The
underlay member is named ``<docUUID>.<fileType>`` with the file type verbatim, so
:class:`~rmspec.domain.ports.device.DeviceFileType`'s own value *is* the suffix and no
lookup table is written -- a table would be a second place for the two spellings to
disagree.

One listing per instance, and one request from the facts source
---------------------------------------------------------------
No route takes a document id and returns that document, so :meth:`UsbCatalog.get_document`
is answered from a memoised ``DeviceListing`` built on first need. That is sound because
``ports/device.py`` says every port is one view over a single ``Scope.REQUEST`` transport
resource, so an instance's lifetime is one command: ``list_documents`` then
``get_document`` performs one walk, not two.

:class:`UsbFacts` reports everything ``unsupported`` -- the route table has no firmware,
model, serial, memory or storage route -- but still makes **one request**, a
``HEAD /documents/``. A facts source that never touched the wire would report a detached
tablet as "everything unsupported", and the port documents ``DeviceUnreachable`` as a raise
from both methods. The two ``unsupported`` sets are *derived* from the port's own field
names rather than spelled as literals: the constraint is the closed route table, so it holds
for every field either model has or gains, and deriving it means the adapter cannot fall out
of step with the port. ``test_device_usb.py`` pins the literal names, so the derivation is
checked rather than trusted.

Parsing ``/log.txt`` for a firmware string is rejected: 9.7 MB of the user's own journal,
from a rolling byte-capped window, to obtain one version number is not a trade worth making.
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Final
from urllib.parse import quote

import httpx

from rmspec.device._archive import RMDOC_ROUTE, read_rmdoc
from rmspec.device._errors import translate_http, translate_httpx
from rmspec.device._pages import decode_page_order
from rmspec.device._wire import LISTING_ROUTE, decode_entries, entry_parent
from rmspec.domain.errors import (
    DeviceDocumentNotFound,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUploadRejected,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.ports.device import (
    DeviceFacts,
    DeviceFileType,
    DeviceListing,
    DevicePageSource,
    DeviceResources,
    DocumentSourceBundle,
    SkippedEntry,
    SkipReason,
)

if TYPE_CHECKING:
    from rmspec.device._archive import ArchiveMembers
    from rmspec.device._pages import PageOrderEntry
    from rmspec.device._wire import DecodedEntries
    from rmspec.device.addresses import Endpoint
    from rmspec.domain.ports.device import DeviceDocument, DeviceFolder

__all__ = ["UsbBundleSource", "UsbCatalog", "UsbFacts", "UsbWebApi"]

_GET: Final = "GET"
"""The verb for every route this transport reads."""

_HEAD: Final = "HEAD"
"""The verb for the existence probe. Same status and ``Content-Type``, no body."""

_CONTENT_LENGTH: Final = "content-length"
"""The header naming how many bytes the device said it was sending."""

_ROUTE_ID: Final = "{id}"
"""The placeholder :data:`~rmspec.device._archive.RMDOC_ROUTE` carries for the document."""

_UNSUPPORTED_FIELD: Final = "unsupported"
"""The one field of a facts model that is not itself a fact, so never named as unsupported."""

_UNANSWERABLE_FACTS: Final = frozenset(DeviceFacts.model_fields) - {_UNSUPPORTED_FIELD}
"""Every fixed fact the port can report, none of which the closed route table reaches."""

_UNANSWERABLE_RESOURCES: Final = frozenset(DeviceResources.model_fields) - {_UNSUPPORTED_FIELD}
"""Every gauge the port can report, none of which the closed route table reaches."""

_ROUTED_FAILURES: Final = (DeviceProtocolError, DeviceDocumentNotFound, DeviceUploadRejected)
"""Exactly what ``translate_http`` can produce: the device answered, about this request.

Caught per folder in :meth:`_Walk._children` and turned into one ``UNREADABLE`` skip,
because the transport saw the folder and was refused its children. Everything else
propagates: ``DeviceUnreachable`` is a whole-transport failure, and
``DeviceTransferInterrupted`` means the listing body arrived short -- neither is a fact
about one folder, and reporting either as a skipped entry would let a dead cable read as a
library of unreadable folders.
"""


class UsbWebApi:
    """One request against the tablet's web API, with every failure already translated.

    Two verbs, both read-only, and no third -- see the module docstring on why there is no
    USB uploader. Nothing here knows what a route *means*: it takes the path, returns the
    body, and raises a domain error for every way that can fail, so the three ports below
    contain no ``httpx`` and no status codes.

    The client is a constructor argument and is never built here, which is what lets a test
    pass an ``httpx.MockTransport`` and the composition root own the real client's lifetime.
    No timeout is spelled in this module for the same reason.
    """

    def __init__(self, *, client: httpx.Client, endpoint: Endpoint) -> None:
        self._client = client
        self._endpoint = endpoint

    def get(self, route: str, /, *, doc_uuid: str | None = None) -> bytes:
        """Fetch one route's body.

        Parameters
        ----------
        route
            The path to request, already percent-encoded by the caller wherever it
            interpolated an identifier. Carried verbatim into any error, so a report names
            the request a reader could re-issue.
        doc_uuid
            The document this request is about, when it is about one. Always supplied on a
            document route: ``translate_http`` uses it as the subject of a not-found error
            and falls back to *route* when it is ``None``, so omitting it here would make a
            "no such document" error name a path instead of a document.

        Returns
        -------
        bytes
            The response body exactly as received. The advertised charset is never
            consulted -- see the module docstring.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer: connect refused, timeout, dead USB interface.
        DeviceDocumentNotFound
            The device reported that it holds no such document, or that the identifier
            names a folder.
        DeviceUploadRejected
            The device refused a payload. Unreachable from this package's own routes and
            translated anyway, because the classification lives in one place.
        DeviceTransferInterrupted
            The device announced a ``Content-Length`` and sent fewer bytes than that.
        DeviceProtocolError
            The device answered with something this adapter cannot interpret: an
            unrecognised error message, a body that is not the error shape, or an httpx
            failure that is not a transport failure.
        """
        response = self._answer(_GET, route)
        body = response.content
        if not response.is_success:
            raise translate_http(
                route=route,
                status=response.status_code,
                body=body,
                endpoint=self._endpoint.base_url,
                doc_uuid=doc_uuid,
            )
        announced = response.headers.get(_CONTENT_LENGTH)
        # A non-decimal Content-Length is rejected by h11 below httpx and arrives here as
        # an httpx.RemoteProtocolError, so there is no parse guard to write: the only value
        # that can reach this line is an integer.
        if announced is not None and len(body) < int(announced):
            raise DeviceTransferInterrupted(
                transport=TransportKind.USB_WEB_API,
                subject=route if doc_uuid is None else doc_uuid,
                bytes_transferred=len(body),
                bytes_expected=int(announced),
            )
        return body

    def head(self, route: str, /) -> None:
        """Probe one route for existence, transferring no body.

        The firmware returns the same status and ``Content-Type`` for ``HEAD`` as for
        ``GET`` with a zero-length body, which makes this the cheap reachability check --
        and the one response that must not be length-checked, since its ``Content-Length``
        describes the body it deliberately omitted.

        Parameters
        ----------
        route
            The path to probe.

        Returns
        -------
        None
            Nothing. A return means the device answered successfully.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer.
        DeviceProtocolError
            The device answered unsuccessfully. A ``HEAD`` body is empty by contract, so
            the device's own ``{"error": ...}`` message is *not* available and the failure
            is reported as the missing error shape rather than guessed at.
        DeviceDocumentNotFound
            Never, in practice, for the same reason: the classification is derived from a
            body this verb does not return. Declared because the seam is shared.
        """
        response = self._answer(_HEAD, route)
        if not response.is_success:
            raise translate_http(
                route=route,
                status=response.status_code,
                body=response.content,
                endpoint=self._endpoint.base_url,
            )

    def _answer(self, method: str, route: str, /) -> httpx.Response:
        """Send one request, letting no ``httpx`` exception escape.

        The ``except`` clause names the four bases that between them cover everything
        ``httpx`` raises -- ``HTTPError`` for the request and status tree, ``InvalidURL``
        and ``StreamError`` for the two that hang off ``Exception`` and ``RuntimeError``
        instead, and ``OSError`` for what a dead interface produces below httpx. It is
        deliberately not a bare ``except Exception``: ``translate_httpx`` is total over
        ``Exception`` so that an unanticipated type is still classified rather than
        re-raised raw, and naming the bases here is what keeps a genuine bug in this
        module -- an ``AttributeError``, a ``TypeError`` -- from being reported as a device
        failure.

        Parameters
        ----------
        method
            ``GET`` or ``HEAD``.
        route
            The path to request.

        Returns
        -------
        httpx.Response
            The response, already read.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer.
        DeviceProtocolError
            The client failed for a reason that is not an unreachable tablet.
        """
        try:
            response = self._client.request(method, f"{self._endpoint.base_url}{route}")
        except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError, OSError) as exc:
            raise translate_httpx(exc, endpoint=self._endpoint.base_url) from exc
        return response


class UsbCatalog:
    """Read the tablet's library over the USB web API.

    Implements ``rmspec.domain.ports.device.DeviceCatalog``. Both methods are answered
    from one breadth-first enumeration, memoised for the life of the instance -- which is
    one command, because every port is one view over a single ``Scope.REQUEST`` transport
    resource.

    ``DeviceAuthFailed`` appears in the port's ``Raises`` sections and is never raised
    here: the USB interface carries no credential, so there is nothing to refuse.
    """

    def __init__(self, *, api: UsbWebApi) -> None:
        self._api = api
        self._listing: DeviceListing | None = None

    def list_documents(self) -> DeviceListing:
        """Enumerate the whole library, documents and folders alike.

        Returns
        -------
        DeviceListing
            Every document and folder the walk reached, plus one entry per thing it could
            not read. The same object on every call: the walk runs once.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceProtocolError
            A listing payload was not a json array at all, or a request failed in a way
            that is not about one folder.
        DeviceTransferInterrupted
            A listing body arrived shorter than the device announced.
        """
        if self._listing is None:
            self._listing = _Walk(self._api).run()
        return self._listing

    def get_document(self, doc_uuid: str, /) -> DeviceDocument:
        """Look up one document by identifier, against the memoised listing.

        No route takes a document id and returns that document -- ``/documents/{id}`` with
        a document id answers the root listing -- so this resolves against the enumeration
        and honours the port's three coherence rules. A folder identifier needs no branch
        of its own: the fall-through already raises ``DeviceDocumentNotFound``, which is
        exactly what the port requires for one, and a separate loop producing the identical
        error would be code no assertion could tell apart.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.

        Returns
        -------
        DeviceDocument
            The document as :meth:`list_documents` reports it, equal by construction.

        Raises
        ------
        DeviceDocumentNotFound
            No document has that identifier, or it names a folder.
        MalformedDeviceMetadata
            The walk saw the entry and could not represent it, whichever ``SkipReason`` it
            recorded, carrying that entry's own detail.
        DeviceUnreachable
            The tablet did not answer, on the walk this call had to perform.
        DeviceProtocolError
            The tablet answered the walk with something this transport cannot interpret.
        DeviceTransferInterrupted
            A listing body arrived shorter than the device announced.
        """
        listing = self.list_documents()
        found = next((entry for entry in listing.documents if entry.uuid == doc_uuid), None)
        if found is not None:
            return found
        unreadable = next((entry for entry in listing.skipped if entry.uuid == doc_uuid), None)
        if unreadable is not None:
            raise MalformedDeviceMetadata(
                transport=TransportKind.USB_WEB_API,
                document_uuid=doc_uuid,
                detail=unreadable.detail,
            )
        raise DeviceDocumentNotFound(
            transport=TransportKind.USB_WEB_API,
            document_uuid=doc_uuid,
        )


class UsbBundleSource:
    """Fetch one document's whole source out of the ``.rmdoc`` archive.

    Implements ``rmspec.domain.ports.device.RawBundleSource``, which the domain's own port
    docstring once said the USB web API could not serve. The re-measurement refutes it:
    ``GET /download/{id}/rmdoc`` returns the document's authoritative on-disk files,
    underlay included, so a complete ``DocumentSourceBundle`` is constructible with no SSH
    credential.

    The catalog is a collaborator rather than a second listing walk, so
    ``bundle.document`` and ``catalog.get_document(uuid)`` cannot disagree -- and the
    catalog's ``file_type`` is what decides whether an underlay member is required at all.
    """

    def __init__(self, *, api: UsbWebApi, catalog: UsbCatalog) -> None:
        self._api = api
        self._catalog = catalog

    def load_bundle(self, doc_uuid: str, /) -> DocumentSourceBundle:
        """Fetch every source file of one document in a single request.

        The catalog is consulted first, for three reasons: it is what makes
        ``bundle.document`` equal to ``get_document``'s answer, it is where a missing
        document is diagnosed without spending a download, and its ``file_type`` decides
        the underlay suffix. Then one ``GET`` of the archive, whose members ``_archive``
        routes and whose page order ``_pages`` decodes.

        Parameters
        ----------
        doc_uuid
            The document's identifier on the device.

        Returns
        -------
        DocumentSourceBundle
            The pages in the order the device recorded, each with its scene bytes and
            template, plus the underlay for a PDF or EPUB and ``None`` for a notebook.

        Raises
        ------
        DeviceDocumentNotFound
            No document has that identifier, or it names a folder.
        MalformedDeviceMetadata
            The archived ``.content`` sidecar will not decode, so no ordered bundle can be
            built, or the catalog reported the entry as unreadable.
        DeviceTransferInterrupted
            The archive body arrived shorter than the device announced. No partial bundle
            is ever returned.
        DeviceUnreachable
            The tablet did not answer at the configured address.
        DeviceProtocolError
            The payload is not a zip, a member belongs to another document, a required
            sidecar is absent, or a non-notebook document's underlay member is missing --
            the archive contradicting its own answer.
        """
        document = self._catalog.get_document(doc_uuid)
        route = RMDOC_ROUTE.replace(_ROUTE_ID, quote(doc_uuid, safe=""))
        members = read_rmdoc(
            self._api.get(route, doc_uuid=doc_uuid),
            doc_uuid=doc_uuid,
            suffix=_underlay_suffix(document.file_type),
        )
        return DocumentSourceBundle(
            document=document,
            pages=_pages_of(members),
            base=members.underlay,
        )


class UsbFacts:
    """Report what the USB web API can say about the tablet, which is nothing.

    Implements ``rmspec.domain.ports.device.DeviceFactsSource``. The route table is closed
    at six families and none of them reports firmware, model, serial, memory or storage, so
    both methods name every field ``unsupported`` -- the port's own spelling of
    "structurally cannot ask", as distinct from a field that was asked for and not answered.

    Both still make one request. See the module docstring: a facts source that never
    touched the wire would report a detached tablet as "everything unsupported", and the
    port documents ``DeviceUnreachable`` as a raise from both.
    """

    def __init__(self, *, api: UsbWebApi) -> None:
        self._api = api

    def read_facts(self) -> DeviceFacts:
        """Read the device's firmware, model and serial.

        Returns
        -------
        DeviceFacts
            Every field ``None`` and every field named in ``unsupported``.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer the probe.
        DeviceProtocolError
            The tablet answered the probe unsuccessfully.
        """
        self._probe()
        return DeviceFacts(unsupported=_UNANSWERABLE_FACTS)

    def read_resources(self) -> DeviceResources:
        """Read the device's memory and storage gauges.

        Returns
        -------
        DeviceResources
            Every field ``None`` and every field named in ``unsupported``.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer the probe.
        DeviceProtocolError
            The tablet answered the probe unsuccessfully.
        """
        self._probe()
        return DeviceResources(unsupported=_UNANSWERABLE_RESOURCES)

    def _probe(self) -> None:
        """Establish that the tablet is attached, transferring no body.

        ``HEAD /documents/`` rather than anything else: the spec establishes that ``HEAD``
        returns identical status and ``Content-Type`` with a zero-length body, and
        ``/documents/`` is a read route with no argument to get wrong.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer.
        DeviceProtocolError
            The tablet answered unsuccessfully.
        """
        self._api.head(LISTING_ROUTE)


class _Walk:
    """One breadth-first enumeration of the library, and the state that terminates it.

    A class rather than a closure because the walk carries five pieces of state that three
    methods share, and because ``visited`` is the whole correctness argument -- it deserves
    to be a named field somebody can find. One instance per enumeration; the memoisation
    that stops a second enumeration lives on :class:`UsbCatalog`.
    """

    def __init__(self, api: UsbWebApi, /) -> None:
        self._api = api
        self._documents: list[DeviceDocument] = []
        self._folders: list[DeviceFolder] = []
        self._skipped: list[SkippedEntry] = []
        self._visited: set[str] = set()
        self._queue: deque[str] = deque()

    def run(self) -> DeviceListing:
        """Walk the root listing and then every folder it leads to.

        Returns
        -------
        DeviceListing
            Every entry reached, in the order encountered: the root's first, then each
            folder's children as the queue reaches them.

        Raises
        ------
        DeviceUnreachable
            The tablet did not answer.
        DeviceProtocolError
            A listing payload was not a json array at all, or the root request failed.
        DeviceTransferInterrupted
            A listing body arrived shorter than the device announced.
        """
        self._place(decode_entries(self._api.get(LISTING_ROUTE)))
        while self._queue:
            children = self._children(self._queue.popleft())
            if children is not None:
                self._place(children)
        return DeviceListing(
            documents=tuple(self._documents),
            folders=tuple(self._folders),
            skipped=tuple(self._skipped),
        )

    def _place(self, entries: DecodedEntries, /) -> None:
        """Add every entry not already placed, enqueueing each folder exactly once.

        The ``visited`` check covers documents as well as folders. Termination only needs
        the folder half, but a device that reported one document under two parents would
        otherwise put it in the listing twice, and a caller iterating the result would
        pull it twice. Skips are appended unconditionally: a ``SkippedEntry`` may carry no
        identifier at all, so there is nothing to deduplicate on -- which is exactly why
        the fallback listing is filtered out before it is decoded rather than after.

        Parameters
        ----------
        entries
            One decoded listing response.
        """
        for document in entries.documents:
            if document.uuid not in self._visited:
                self._visited.add(document.uuid)
                self._documents.append(document)
        for folder in entries.folders:
            if folder.uuid not in self._visited:
                self._visited.add(folder.uuid)
                self._folders.append(folder)
                self._queue.append(folder.uuid)
        self._skipped.extend(entries.skipped)

    def _children(self, folder_uuid: str, /) -> DecodedEntries | None:
        """Fetch one folder's children, or record that the device refused them.

        Parameters
        ----------
        folder_uuid
            The folder to list. Percent-encoded into the route -- see the module docstring.

        Returns
        -------
        DecodedEntries | None
            The folder's real children, or ``None`` when the device answered with a routed
            failure, in which case one ``UNREADABLE`` skip has been recorded instead. A
            fallback root listing decodes to three empty tuples rather than to ``None``:
            nothing was refused, the id simply named no folder.

        Raises
        ------
        DeviceUnreachable
            The tablet stopped answering. A whole-transport failure is not a fact about
            this folder, so it propagates rather than becoming a skip.
        DeviceTransferInterrupted
            The listing body arrived short, which is likewise not a fact about the folder.
        DeviceProtocolError
            The payload was not a json array at all. Raised by ``decode_entries`` after
            the fetch succeeded, so it is not one of the routed failures caught here.
        """
        route = f"{LISTING_ROUTE}{quote(folder_uuid, safe='')}"
        try:
            payload = self._api.get(route)
        except _ROUTED_FAILURES as err:
            self._skipped.append(
                SkippedEntry(
                    uuid=folder_uuid,
                    reason=SkipReason.UNREADABLE,
                    detail=err.message,
                )
            )
            return None
        return decode_entries(_only_children_of(payload, folder_uuid))


def _only_children_of(payload: bytes, folder_uuid: str, /) -> bytes:
    """Drop every entry that is not really a child of the folder that was asked for.

    This is the filter that distinguishes a real folder listing from the silent root
    fallback, and it runs on the **raw** json array rather than on decoded values. See the
    module docstring: filtering after decoding would double-count the root's skipped
    entries, once per folder walked, because a ``SkippedEntry`` carries no parent.

    Parameters
    ----------
    payload
        The response body of one ``/documents/{parentId}`` request.
    folder_uuid
        The identifier that was requested. An entry survives only when its raw ``Parent``
        equals this exactly.

    Returns
    -------
    bytes
        A json array of the surviving entries, or *payload* verbatim when it is not a json
        array at all. The pass-through is deliberate: ``_wire.decode_entries`` owns the
        "not a json array" diagnosis and names the listing route family in it, so handing
        it the untouched bytes produces a better message than a second copy of that check
        would.
    """
    try:
        decoded: object = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(decoded, list):
        return payload
    return json.dumps([entry for entry in decoded if entry_parent(entry) == folder_uuid]).encode()


def _underlay_suffix(file_type: DeviceFileType, /) -> str | None:
    """Name the archive member that holds the document's underlay.

    The member is ``<docUUID>.<fileType>`` with the file type spelled exactly as the wire
    spells it, so the enum's own value is the suffix. No mapping table: a table would be a
    second place for ``"pdf"`` and ``"epub"`` to be written, and therefore a place for them
    to disagree.

    Parameters
    ----------
    file_type
        The kind the catalog reported for this document.

    Returns
    -------
    str | None
        ``"pdf"`` or ``"epub"``, or ``None`` for a notebook -- which has no underlay, and
        whose ``base`` ``DocumentSourceBundle`` refuses to accept.
    """
    if file_type is DeviceFileType.NOTEBOOK:
        return None
    return file_type.value


def _pages_of(members: ArchiveMembers, /) -> tuple[DevicePageSource, ...]:
    """Assemble the ordered page tuple from the archive's own page order.

    Driven by the page order, never by the archive's member list: a ``.rm`` member the
    order does not claim is an orphan layer and is dropped here by simply never being
    looked up.

    Parameters
    ----------
    members
        The archive's members, routed by role.

    Returns
    -------
    tuple[DevicePageSource, ...]
        One entry per claimed page, in the order the sidecar recorded.

    Raises
    ------
    MalformedDeviceMetadata
        The archived ``.content`` sidecar will not decode.
    """
    return tuple(
        DevicePageSource(
            page_id=entry.page_id,
            scene=_scene(members, entry.page_id),
            template_name=entry.template_name,
        )
        for entry in _page_order(members)
    )


def _page_order(members: ArchiveMembers, /) -> tuple[PageOrderEntry, ...]:
    """Decode the archived ``.content`` page order into domain terms.

    Parameters
    ----------
    members
        The archive's members. ``content`` is always present -- ``_archive`` refuses an
        archive without it -- so the ``None`` arm of ``decode_page_order`` is not reachable
        from here.

    Returns
    -------
    tuple[PageOrderEntry, ...]
        Every page the sidecar claims, in file order.

    Raises
    ------
    MalformedDeviceMetadata
        The sidecar is not json, is json of the wrong shape, or claims a page with no
        identifier. ``decode_page_order`` raises ``TypeError`` and ``ValueError`` by
        design and imports no domain error; this is the one place either becomes one.
    """
    try:
        return decode_page_order(members.content)
    except (TypeError, ValueError) as err:
        raise MalformedDeviceMetadata(
            transport=TransportKind.USB_WEB_API,
            document_uuid=members.doc_uuid,
            detail=_first_line(err),
        ) from err


def _scene(members: ArchiveMembers, page_id: str, /) -> bytes | None:
    """Read one page's scene bytes, collapsing both empty states to ``None``.

    Parameters
    ----------
    members
        The archive's members.
    page_id
        The page to look up, as the page order spelled it.

    Returns
    -------
    bytes | None
        The scene bytes, or ``None`` when the archive holds no member for this page **or**
        holds a zero-byte one. The two are different facts about the archive -- which is
        why ``_archive`` preserves the distinction -- and the same fact about the page:
        ``DevicePageSource.scene`` documents ``None`` as "the page carries no ink", and a
        zero-byte artifact is exactly that. 86 of 194 real ``.rm`` files are zero bytes.
    """
    stored = members.scenes.get(page_id)
    if not stored:
        return None
    return stored


def _first_line(err: Exception, /) -> str:
    """Render one decode failure as a single line of human detail.

    Parameters
    ----------
    err
        The failure to describe.

    Returns
    -------
    str
        The first non-empty line of the message, or the exception's type name when it
        carries no message at all. A domain error's ``detail`` is read by a person looking
        at one line of CLI output.
    """
    text = str(err).strip()
    first = text.splitlines()[0].strip() if text else ""
    return first or type(err).__name__
