"""The USB web API transport, its breadth-first walk, its bundle, and its empty facts.

Every request here goes through ``httpx.MockTransport``. Nothing constructs a client
against a real address: the reference tablet is attached while this is being written, so a
test that reached the network would silently **pass** instead of failing.

Every fixture is **synthesised** from the measured shape rather than captured. A real
``/documents/`` body carries the user's document titles and a real ``.rmdoc`` carries their
handwriting, so nothing from the device is committed, and each builder's docstring names
the shape and the evidence it stands on.

Four things carry this suite, and the sections below follow them:

* the request seam -- what a failed response, a short body and a dead cable each become;
* the walk -- that it reaches depth 2, that the silent root fallback contributes nothing,
  that it terminates, and that one refused folder is one skipped entry rather than a
  vanished subtree;
* the bundle -- the three page states, the dropped orphan layer, and all-or-nothing;
* the deliberate absences -- no uploader, no write verb, and every fact ``unsupported``.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import TYPE_CHECKING

import httpx
import pytest

from rmspec.device import usb
from rmspec.device._archive import RMDOC_ROUTE
from rmspec.device._wire import COLLECTION_TYPE, DOCUMENT_TYPE, LISTING_ROUTE
from rmspec.device.addresses import DEFAULT_USB_HOST, Endpoint
from rmspec.device.usb import (
    UsbBundleSource,
    UsbCatalog,
    UsbFacts,
    UsbWebApi,
    _only_children_of,
)
from rmspec.domain.errors import (
    DeviceDocumentNotFound,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    MalformedDeviceMetadata,
    TransportKind,
)
from rmspec.domain.ports.device import DeviceFileType, SkipReason

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

ROOT_DOC = "aaaaaaaa-0000-4000-8000-000000000001"
"""A document at the library root."""

WORK = "bbbbbbbb-0000-4000-8000-000000000002"
"""A folder at the library root, standing in for the measured depth-1 collection."""

WORK_DOC = "cccccccc-0000-4000-8000-000000000003"
"""A document one level down."""

ARCHIVE = "dddddddd-0000-4000-8000-000000000004"
"""A folder inside ``WORK``: depth 2, which is where the measured nesting stops."""

DEEP_DOC = "eeeeeeee-0000-4000-8000-000000000005"
"""A document at depth 2."""

SKIPPED = "ffffffff-0000-4000-8000-000000000006"
"""An entry the decoder cannot represent, or a folder whose children are refused."""

UNKNOWN = "99999999-0000-4000-8000-000000000009"
"""An identifier the tablet holds nothing under."""

PAGE_ONE = "11111111-0000-4000-8000-00000000000a"
PAGE_TWO = "22222222-0000-4000-8000-00000000000b"
ORPHAN = "33333333-0000-4000-8000-00000000000c"
"""A ``.rm`` member the page order does not claim. Measured: 16 members for 10 pages."""

MODIFIED = "2026-08-29T14:52:11.412Z"
"""One ``ModifiedClient`` value, in the measured shape ``9999-99-99T99:99:99.999Z``."""

JSON_CONTENT_TYPE = "application/json; charset=ISO-8859-1"
"""The charset every json body advertises while carrying UTF-8 titles. A lie, honoured."""

INK = b"v6-scene-bytes"
"""Stand-in for a page's v6 scene payload, which no adapter interprets."""

PDF_BYTES = b"%PDF-1.7 synthetic underlay"
"""Stand-in for the original underlay the archive carries for a non-notebook document."""


# ─────────────────────────────── the wire builders ───────────────────────────────


def document_entry(
    doc_uuid: str,
    *,
    parent: str = "",
    file_type: str = "notebook",
    name: str = "Sprint notes",
    **overrides: object,
) -> dict[str, object]:
    """Build a ``DocumentType`` listing entry: the 9 keys measured on firmware 3.27.3.0.

    ``Bookmarked``, ``CurrentPage``, ``ID``, ``ModifiedClient``, ``Parent``, ``Type``,
    ``VisibleName``, ``VissibleName`` (sic, equal to ``VisibleName`` on all 31 entries
    observed) and ``fileType``. ``CurrentPage`` is the last-opened page *index*, not a
    count, which is why ``DeviceDocument.page_count`` is always ``None``.
    """
    entry: dict[str, object] = {
        "Bookmarked": False,
        "CurrentPage": 3,
        "ID": doc_uuid,
        "ModifiedClient": MODIFIED,
        "Parent": parent,
        "Type": DOCUMENT_TYPE,
        "VisibleName": name,
        "VissibleName": name,
        "fileType": file_type,
    }
    entry.update(overrides)
    return entry


def folder_entry(
    folder_uuid: str,
    *,
    parent: str = "",
    name: str = "Work",
    **overrides: object,
) -> dict[str, object]:
    """Build a ``CollectionType`` entry: the measured document set minus two keys.

    ``CurrentPage`` and ``fileType`` are absent, because a folder has neither a
    last-opened page nor an underlay.
    """
    entry = document_entry(folder_uuid, parent=parent, name=name)
    del entry["CurrentPage"]
    del entry["fileType"]
    entry["Type"] = COLLECTION_TYPE
    entry.update(overrides)
    return entry


def listing_body(*entries: object) -> bytes:
    """Render a ``/documents/`` response body: a bare json array of flat objects."""
    return json.dumps(list(entries)).encode()


def content_sidecar(*pages: tuple[str, str | None]) -> bytes:
    """Render a firmware-3.x ``.content`` page order: ``cPages.pages[]``, CRDT-stamped.

    Measured on the archived member of a 1-page pdf document: 22 top-level keys with
    ``cPages`` present, the flat top-level ``pages`` array **absent**, ``formatVersion``
    2, and each page's template wrapped in a ``{"timestamp", "value"}`` envelope. Only the
    keys this transport reads are reproduced.
    """
    listed: list[dict[str, object]] = []
    for page_id, template in pages:
        entry: dict[str, object] = {"id": page_id, "idx": {"timestamp": "1:2", "value": "ba"}}
        if template is not None:
            entry["template"] = {"timestamp": "1:2", "value": template}
        listed.append(entry)
    return json.dumps(
        {
            "cPages": {"pages": listed},
            "fileType": "notebook",
            "formatVersion": 2,
            "pageCount": len(listed),
        }
    ).encode()


def rmdoc_archive(
    *,
    doc_uuid: str = ROOT_DOC,
    content: bytes | None = None,
    scenes: Mapping[str, bytes] | None = None,
    underlay: tuple[str, bytes] | None = None,
) -> bytes:
    """Build the measured ``.rmdoc`` member set as an in-memory zip.

    Reproduces what firmware 3.27.3.0 returns from ``GET /download/{id}/rmdoc``:
    ``<docUUID>.metadata`` (always), ``<docUUID>.content`` (always), one
    ``<docUUID>/<pageUUID>.rm`` per page in the content's page list **including zero-byte
    placeholders**, and ``<docUUID>.<fileType>`` for a non-notebook document. Never
    ``.local``, ``.pagedata``, ``.thumbnails`` or ``.failure``, and never nesting below
    ``<docUUID>/``.

    Evidence, from a 1-page pdf document re-measured 2026-08-29: archive 8557 bytes, magic
    ``504b0304``, members ``.metadata`` 381 / ``.content`` 2329 / ``.pdf`` 5481 / one
    ``.rm`` of 0 bytes and one of 6051, both at depth 1.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{doc_uuid}.metadata", b'{"type": "DocumentType"}')
        archive.writestr(
            f"{doc_uuid}.content",
            content_sidecar((PAGE_ONE, None)) if content is None else content,
        )
        for page_id, payload in (scenes or {}).items():
            archive.writestr(f"{doc_uuid}/{page_id}.rm", payload)
        if underlay is not None:
            archive.writestr(f"{doc_uuid}.{underlay[0]}", underlay[1])
    return buffer.getvalue()


# ─────────────────────────────── the fake tablet ───────────────────────────────


def json_response(body: bytes) -> httpx.Response:
    """One listing response as measured: chunked, **no** ``Content-Length``, lying charset.

    The measured header dump for ``GET /documents/`` is ``Content-Type`` plus
    ``Transfer-Encoding: chunked`` and nothing else, so the listing route genuinely
    announces no length.
    """
    return httpx.Response(200, content=iter([body]), headers={"content-type": JSON_CONTENT_TYPE})


def archive_response(payload: bytes, *, announced: int | None = None) -> httpx.Response:
    """One ``/download`` response as measured: **both** ``Content-Length`` and chunked.

    Measured verbatim: ``Content-Length: 6021585`` and ``Transfer-Encoding: chunked`` in
    the same response. *announced* overrides the length, which is how a short read is
    expressed without a real dropped connection.
    """
    return httpx.Response(
        200,
        content=iter([payload]),
        headers={
            "content-length": str(len(payload) if announced is None else announced),
            "transfer-encoding": "chunked",
            "content-type": "application/zip",
        },
    )


def error_response(status: int, message: str) -> httpx.Response:
    """One error response: the uniform ``{"error": "<msg>"}`` body under the json type.

    Four status codes are in use and the code carries almost no information: 400 covers
    five semantically different failures and 500 covers both "no such route" and "route
    exists, entry missing". The body is the discriminator.
    """
    return httpx.Response(
        status,
        content=json.dumps({"error": message}).encode(),
        headers={"content-type": JSON_CONTENT_TYPE},
    )


def refused() -> httpx.Response:
    """Answer a routed refusal: the folder is listed, and its children are not served."""
    return error_response(400, "Malformed URL")


def tablet(
    *,
    tree: Mapping[str, Sequence[object]],
    answers: Mapping[str, Callable[[], httpx.Response]] | None = None,
    downloads: Mapping[str, Callable[[], httpx.Response]] | None = None,
    seen: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler reproducing the measured ``/documents/`` resolution rule.

    ``GET /documents/`` answers ``tree[""]``. ``GET /documents/{pid}`` answers
    ``tree[pid]`` when *pid* names a folder the tree knows, and **the byte-identical root
    listing** otherwise -- the silent fallback measured for a document id and for an
    unknown id, which returns status 200 and logs nothing, so a 200 is not proof the id was
    recognised.

    *answers* overrides one identifier with an arbitrary response, which is how a refused,
    truncated or non-array sub-listing is expressed. *downloads* is keyed by the raw path
    of a download route. *seen* records every request as ``"<METHOD> <raw path>"``, which
    is what proves a walk terminated and that a memoised listing was not re-fetched.

    Any other path fails the test rather than being answered: a route this module was not
    supposed to reach must be loud, which is the whole point of the percent-encoding
    assertions below.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        if seen is not None:
            seen.append(f"{request.method} {path}")
        if path.startswith(LISTING_ROUTE):
            requested = path.removeprefix(LISTING_ROUTE)
            canned = (answers or {}).get(requested)
            if canned is not None:
                return canned()
            return json_response(listing_body(*tree.get(requested, tree[""])))
        download = (downloads or {}).get(path)
        if download is not None:
            return download()
        pytest.fail(f"the fake tablet was asked for {path!r}, which it does not route")

    return handler


def web_api(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    endpoint: Endpoint | None = None,
) -> UsbWebApi:
    """Bind the transport to a fake device. The client is injected, never constructed."""
    return UsbWebApi(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        endpoint=endpoint or Endpoint(),
    )


def sources(
    answer: Callable[[], httpx.Response],
    *,
    file_type: str = "notebook",
    doc_uuid: str = ROOT_DOC,
    seen: list[str] | None = None,
) -> tuple[UsbCatalog, UsbBundleSource]:
    """Wire a catalog and a bundle source over a one-document library.

    Both ports share one :class:`UsbWebApi`, which is what the composition root does: every
    port is one view over a single ``Scope.REQUEST`` transport resource.
    """
    api = web_api(
        tablet(
            tree={"": [document_entry(doc_uuid, file_type=file_type)]},
            downloads={f"/download/{doc_uuid}/rmdoc": answer},
            seen=seen,
        )
    )
    catalog = UsbCatalog(api=api)
    return catalog, UsbBundleSource(api=api, catalog=catalog)


# ─────────────────────────────── the request seam ───────────────────────────────


def test_a_route_is_appended_to_the_endpoints_origin():
    seen: list[str] = []
    api = web_api(tablet(tree={"": []}, seen=seen))

    assert api.get(LISTING_ROUTE) == b"[]"
    assert seen == [f"GET {LISTING_ROUTE}"]


def test_a_non_default_port_reaches_the_url_through_the_endpoint():
    """The origin comes from ``Endpoint.base_url``; this module spells no host and no port."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return json_response(b"[]")

    api = web_api(handler, endpoint=Endpoint(port=8080))
    api.get(LISTING_ROUTE)

    assert requested == [f"http://{DEFAULT_USB_HOST}:8080{LISTING_ROUTE}"]


def test_a_listing_that_announces_no_length_is_accepted():
    """The measured ``/documents/`` response is chunked with no ``Content-Length`` at all."""
    api = web_api(tablet(tree={"": [document_entry(ROOT_DOC)]}))

    assert api.get(LISTING_ROUTE).startswith(b"[")


def test_a_body_that_matches_the_announced_length_is_returned_whole():
    payload = rmdoc_archive()
    api = web_api(
        tablet(tree={"": []}, downloads={"/download/x/rmdoc": lambda: archive_response(payload)})
    )

    assert api.get("/download/x/rmdoc", doc_uuid=ROOT_DOC) == payload


def test_a_body_shorter_than_the_announced_length_is_a_transfer_interruption():
    payload = rmdoc_archive()
    api = web_api(
        tablet(
            tree={"": []},
            downloads={
                "/download/x/rmdoc": lambda: archive_response(payload, announced=len(payload) + 64)
            },
        )
    )

    with pytest.raises(DeviceTransferInterrupted) as raised:
        api.get("/download/x/rmdoc", doc_uuid=ROOT_DOC)

    assert raised.value.subject == ROOT_DOC
    assert raised.value.bytes_transferred == len(payload)
    assert raised.value.bytes_expected == len(payload) + 64
    assert raised.value.transport is TransportKind.USB_WEB_API


def test_a_short_body_on_a_route_about_no_document_names_the_route():
    """``doc_uuid`` is the subject when there is one; the route stands in when there is not."""
    api = web_api(
        tablet(
            tree={"": []},
            answers={
                "": lambda: httpx.Response(
                    200, content=iter([b"[]"]), headers={"content-length": "99"}
                )
            },
        )
    )

    with pytest.raises(DeviceTransferInterrupted) as raised:
        api.get(LISTING_ROUTE)

    assert raised.value.subject == LISTING_ROUTE


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        pytest.param(
            400, "No such entry", DeviceDocumentNotFound, id="the id is not in the store"
        ),
        pytest.param(400, "Can only download documents", DeviceDocumentNotFound, id="a folder id"),
        pytest.param(
            500, "Unknown file", DeviceProtocolError, id="a route this client spelled wrongly"
        ),
        pytest.param(
            400, "Filetype not supported", DeviceProtocolError, id="a bad format selector"
        ),
        pytest.param(
            404, "Unable to open file /x", DeviceProtocolError, id="an unopenable static file"
        ),
        pytest.param(500, "Brand new wording", DeviceProtocolError, id="a message never measured"),
        pytest.param(200, "", DeviceProtocolError, id="a body that is not the error shape"),
    ],
)
def test_every_failed_response_is_classified_by_its_body_and_not_its_status(
    status: int,
    message: str,
    expected: type[Exception],
):
    """The status carries almost nothing: 400 covers five failures and 500 covers two."""
    body = (
        b"<html>captive portal</html>" if not message else json.dumps({"error": message}).encode()
    )
    answer = httpx.Response(500 if status == 200 else status, content=body)
    api = web_api(tablet(tree={"": []}, answers={"": lambda: answer}))

    with pytest.raises(expected):
        api.get(LISTING_ROUTE)


def test_a_document_route_names_the_document_rather_than_the_path():
    api = web_api(
        tablet(
            tree={"": []},
            downloads={"/download/x/rmdoc": lambda: error_response(400, "No such entry")},
        )
    )

    with pytest.raises(DeviceDocumentNotFound) as raised:
        api.get("/download/x/rmdoc", doc_uuid=ROOT_DOC)

    assert raised.value.document_uuid == ROOT_DOC


def test_a_document_route_that_forgot_its_doc_uuid_would_name_the_path_instead():
    """Why every document route supplies ``doc_uuid``: the subject falls back to the route."""
    api = web_api(
        tablet(
            tree={"": []},
            downloads={"/download/x/rmdoc": lambda: error_response(400, "No such entry")},
        )
    )

    with pytest.raises(DeviceDocumentNotFound) as raised:
        api.get("/download/x/rmdoc")

    assert raised.value.document_uuid == "/download/x/rmdoc"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        pytest.param(httpx.ConnectError("connection refused"), DeviceUnreachable, id="cable out"),
        pytest.param(httpx.ReadTimeout("timed out"), DeviceUnreachable, id="read stalled"),
        pytest.param(OSError("usb1 went away"), DeviceUnreachable, id="interface below httpx"),
        pytest.param(
            httpx.StreamError("the response stream was already consumed"),
            DeviceProtocolError,
            id="a client-side failure, not an absent tablet",
        ),
    ],
)
def test_every_client_failure_becomes_a_domain_error(raised: Exception, expected: type[Exception]):
    """No ``httpx`` type crosses this boundary, including the ones off the ``HTTPError`` tree."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise raised

    with pytest.raises(expected):
        web_api(handler).get(LISTING_ROUTE)


def test_a_successful_head_transfers_no_body_and_answers_nothing():
    seen: list[str] = []
    api = web_api(tablet(tree={"": []}, seen=seen))

    assert api.head(LISTING_ROUTE) is None
    assert seen == [f"HEAD {LISTING_ROUTE}"]


def test_a_head_that_announces_a_length_it_deliberately_omits_is_not_a_short_read():
    """Measured: ``HEAD`` returns the same status and ``Content-Type`` with a zero-length body."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-length": "2902"})

    assert web_api(handler).head(LISTING_ROUTE) is None


def test_a_failed_head_cannot_carry_the_devices_message_and_says_so():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"")

    with pytest.raises(DeviceProtocolError) as raised:
        web_api(handler).head(LISTING_ROUTE)

    assert raised.value.route == LISTING_ROUTE
    assert "error" in raised.value.expected


# ─────────────────────────────── the breadth-first walk ───────────────────────────────


def test_a_root_only_library_is_returned_whole():
    seen: list[str] = []
    catalog = UsbCatalog(api=web_api(tablet(tree={"": [document_entry(ROOT_DOC)]}, seen=seen)))

    listing = catalog.list_documents()

    assert [entry.uuid for entry in listing.documents] == [ROOT_DOC]
    assert listing.folders == ()
    assert listing.skipped == ()
    assert seen == [f"GET {LISTING_ROUTE}"]


def test_the_walk_reaches_depth_two_because_the_root_listing_is_not_the_library():
    """Measured: 9 entries at the root, 30 at depth 1, 2 at depth 2 -- 9 of 41 from one GET."""
    seen: list[str] = []
    catalog = UsbCatalog(
        api=web_api(
            tablet(
                tree={
                    "": [document_entry(ROOT_DOC), folder_entry(WORK)],
                    WORK: [
                        document_entry(WORK_DOC, parent=WORK),
                        folder_entry(ARCHIVE, parent=WORK, name="Archive"),
                    ],
                    ARCHIVE: [document_entry(DEEP_DOC, parent=ARCHIVE)],
                },
                seen=seen,
            )
        )
    )

    listing = catalog.list_documents()

    assert [entry.uuid for entry in listing.documents] == [ROOT_DOC, WORK_DOC, DEEP_DOC]
    assert [entry.uuid for entry in listing.folders] == [WORK, ARCHIVE]
    assert listing.skipped == ()
    assert seen == [
        f"GET {LISTING_ROUTE}",
        f"GET {LISTING_ROUTE}{WORK}",
        f"GET {LISTING_ROUTE}{ARCHIVE}",
    ]


def test_a_folder_the_device_will_not_resolve_contributes_nothing_and_the_walk_terminates():
    """Two unresolved folders, two fallback root listings, and exactly three requests.

    Without the parent filter each fallback re-supplies the root's folders, and the walk
    re-enqueues them forever.
    """
    seen: list[str] = []
    catalog = UsbCatalog(
        api=web_api(
            tablet(
                tree={"": [folder_entry(WORK), folder_entry(ARCHIVE, name="Archive")]}, seen=seen
            )
        )
    )

    listing = catalog.list_documents()

    assert listing.documents == ()
    assert [entry.uuid for entry in listing.folders] == [WORK, ARCHIVE]
    assert listing.skipped == ()
    assert len(seen) == 3


def test_the_roots_unreadable_entries_are_not_recounted_once_per_folder_walked():
    """The reason the parent filter runs *before* decoding: a skip has no parent to compare."""
    catalog = UsbCatalog(
        api=web_api(
            tablet(
                tree={
                    "": [
                        folder_entry(WORK),
                        folder_entry(ARCHIVE, name="Archive"),
                        document_entry(SKIPPED, ModifiedClient="not-an-instant"),
                    ]
                }
            )
        )
    )

    listing = catalog.list_documents()

    assert [entry.uuid for entry in listing.skipped] == [SKIPPED]


@pytest.mark.parametrize(
    "requested",
    [
        pytest.param(ROOT_DOC, id="a document id, which the walk itself never requests"),
        pytest.param(UNKNOWN, id="an id the store holds nothing under"),
    ],
)
def test_no_entry_of_a_fallback_root_listing_survives_the_parent_filter(requested: str):
    """The comparison that tells a real folder from the silent fallback, on raw entries."""
    root = listing_body(document_entry(ROOT_DOC), folder_entry(WORK))

    assert json.loads(_only_children_of(root, requested)) == []


def test_every_entry_of_a_real_folder_listing_survives_the_parent_filter():
    children = listing_body(document_entry(WORK_DOC, parent=WORK))

    assert [entry["ID"] for entry in json.loads(_only_children_of(children, WORK))] == [WORK_DOC]


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("Malformed URL", id="a routed request with a bad argument"),
        pytest.param("No such entry", id="the folder's children are not in the store"),
        pytest.param("Unknown file", id="a 500 that means the path was not routed"),
    ],
)
def test_a_folder_whose_children_are_refused_becomes_one_unreadable_skip(message: str):
    """The transport saw the folder and was refused its children: per-entry failure is data."""
    status = 500 if message == "Unknown file" else 400
    catalog = UsbCatalog(
        api=web_api(
            tablet(
                tree={"": [document_entry(ROOT_DOC), folder_entry(SKIPPED)]},
                answers={SKIPPED: lambda: error_response(status, message)},
            )
        )
    )

    listing = catalog.list_documents()

    assert [entry.uuid for entry in listing.documents] == [ROOT_DOC]
    assert [entry.uuid for entry in listing.folders] == [SKIPPED]
    assert len(listing.skipped) == 1
    assert listing.skipped[0].uuid == SKIPPED
    assert listing.skipped[0].reason is SkipReason.UNREADABLE
    assert listing.skipped[0].detail


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        pytest.param(
            lambda: (_ for _ in ()).throw(httpx.ConnectError("the cable came out mid-walk")),
            DeviceUnreachable,
            id="a whole-transport failure",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=iter([b"[]"]), headers={"content-length": "99"}),
            DeviceTransferInterrupted,
            id="a listing body that arrived short",
        ),
    ],
)
def test_a_failure_that_is_not_about_one_folder_propagates(
    answer: Callable[[], httpx.Response],
    expected: type[Exception],
):
    """Reporting a dead cable as a folder of unreadable children would hide the real fault."""
    catalog = UsbCatalog(
        api=web_api(tablet(tree={"": [folder_entry(WORK)]}, answers={WORK: answer}))
    )

    with pytest.raises(expected):
        catalog.list_documents()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"<html>a captive portal</html>", id="bytes that are not json"),
        pytest.param(b'{"documents": []}', id="valid json that is not an array"),
    ],
)
def test_a_sub_listing_that_is_not_a_json_array_is_a_protocol_error(body: bytes):
    """The decoder owns that diagnosis, so the unfilterable payload reaches it untouched."""
    catalog = UsbCatalog(
        api=web_api(
            tablet(tree={"": [folder_entry(WORK)]}, answers={WORK: lambda: json_response(body)})
        )
    )

    with pytest.raises(DeviceProtocolError) as raised:
        catalog.list_documents()

    assert raised.value.route == LISTING_ROUTE


def test_a_folder_reported_under_two_parents_is_placed_and_enqueued_once():
    """``visited`` makes termination structural rather than a property of a coherent device."""
    seen: list[str] = []
    catalog = UsbCatalog(
        api=web_api(
            tablet(
                tree={
                    "": [folder_entry(WORK), folder_entry(ARCHIVE, name="Archive")],
                    WORK: [folder_entry(ARCHIVE, parent=WORK, name="Archive")],
                    ARCHIVE: [document_entry(DEEP_DOC, parent=ARCHIVE)],
                },
                seen=seen,
            )
        )
    )

    listing = catalog.list_documents()

    assert [entry.uuid for entry in listing.folders] == [WORK, ARCHIVE]
    assert seen.count(f"GET {LISTING_ROUTE}{ARCHIVE}") == 1


def test_a_document_reported_under_two_folders_is_placed_once():
    """A caller iterating the listing would otherwise pull the same document twice."""
    catalog = UsbCatalog(
        api=web_api(
            tablet(
                tree={
                    "": [folder_entry(WORK), folder_entry(ARCHIVE, name="Archive")],
                    WORK: [document_entry(WORK_DOC, parent=WORK)],
                    ARCHIVE: [document_entry(WORK_DOC, parent=ARCHIVE)],
                }
            )
        )
    )

    assert [entry.uuid for entry in catalog.list_documents().documents] == [WORK_DOC]


def test_the_library_is_walked_once_however_often_it_is_asked_for():
    """One command is one instance, so ``list_documents`` then ``get_document`` is one walk."""
    seen: list[str] = []
    catalog = UsbCatalog(api=web_api(tablet(tree={"": [document_entry(ROOT_DOC)]}, seen=seen)))

    first = catalog.list_documents()
    second = catalog.list_documents()
    catalog.get_document(ROOT_DOC)

    assert first is second
    assert seen == [f"GET {LISTING_ROUTE}"]


# ─────────────────────────── get_document, and coherence ───────────────────────────


def test_a_listed_document_resolves_to_the_entry_the_listing_carries():
    catalog = UsbCatalog(
        api=web_api(tablet(tree={"": [document_entry(ROOT_DOC, file_type="pdf")]}))
    )

    document = catalog.get_document(ROOT_DOC)

    assert document == catalog.list_documents().documents[0]
    assert document.file_type is DeviceFileType.PDF
    assert document.page_count is None
    assert document.trashed is False


def test_a_folder_identifier_is_not_a_document():
    """The port: folders are not documents and ``get_document`` has no way to return one."""
    catalog = UsbCatalog(api=web_api(tablet(tree={"": [folder_entry(WORK)]})))

    with pytest.raises(DeviceDocumentNotFound) as raised:
        catalog.get_document(WORK)

    assert raised.value.document_uuid == WORK
    assert WORK in [entry.uuid for entry in catalog.list_documents().folders]


def test_an_identifier_the_library_never_mentions_is_not_found():
    catalog = UsbCatalog(api=web_api(tablet(tree={"": [document_entry(ROOT_DOC)]})))

    with pytest.raises(DeviceDocumentNotFound) as raised:
        catalog.get_document(UNKNOWN)

    assert raised.value.document_uuid == UNKNOWN
    assert raised.value.transport is TransportKind.USB_WEB_API


@pytest.mark.parametrize(
    ("tree", "answers", "reason"),
    [
        pytest.param(
            {"": [document_entry(SKIPPED, ModifiedClient="whenever")]},
            {},
            SkipReason.MALFORMED_METADATA,
            id="a timestamp that will not parse",
        ),
        pytest.param(
            {"": [document_entry(SKIPPED, file_type="djvu")]},
            {},
            SkipReason.VALIDATION_FAILED,
            id="a file type this domain cannot represent",
        ),
        pytest.param(
            {"": [folder_entry(SKIPPED)]},
            {SKIPPED: refused},
            SkipReason.UNREADABLE,
            id="a folder whose children were refused",
        ),
    ],
)
def test_every_skip_reason_becomes_malformed_metadata_when_asked_for_by_identifier(
    tree: Mapping[str, Sequence[object]],
    answers: Mapping[str, Callable[[], httpx.Response]],
    reason: SkipReason,
):
    """The port: an adapter cannot report one reason from the listing and another class here."""
    catalog = UsbCatalog(api=web_api(tablet(tree=tree, answers=answers)))
    listing = catalog.list_documents()

    assert listing.skipped[0].reason is reason

    with pytest.raises(MalformedDeviceMetadata) as raised:
        catalog.get_document(SKIPPED)

    assert raised.value.document_uuid == SKIPPED
    assert raised.value.detail == listing.skipped[0].detail


# ─────────────────────────────── the bundle ───────────────────────────────


def test_a_notebook_bundle_carries_its_pages_in_order_and_no_underlay():
    payload = rmdoc_archive(
        content=content_sidecar((PAGE_ONE, "P Grid small"), (PAGE_TWO, None)),
        scenes={PAGE_ONE: INK, PAGE_TWO: b"more-" + INK},
    )
    _, source = sources(lambda: archive_response(payload))

    bundle = source.load_bundle(ROOT_DOC)

    assert [page.page_id for page in bundle.pages] == [PAGE_ONE, PAGE_TWO]
    assert bundle.pages[0].scene == INK
    assert bundle.pages[0].template_name == "P Grid small"
    assert bundle.pages[1].template_name is None
    assert bundle.base is None


def test_a_pdf_bundle_carries_the_original_underlay_the_archive_holds():
    """The refutation that makes this port bindable over USB with no SSH credential."""
    payload = rmdoc_archive(scenes={PAGE_ONE: INK}, underlay=("pdf", PDF_BYTES))
    _, source = sources(lambda: archive_response(payload), file_type="pdf")

    bundle = source.load_bundle(ROOT_DOC)

    assert bundle.base == PDF_BYTES
    assert bundle.document.file_type is DeviceFileType.PDF


def test_an_epub_bundle_reads_the_same_way_although_the_case_is_unmeasured():
    payload = rmdoc_archive(scenes={PAGE_ONE: INK}, underlay=("epub", b"PK epub"))
    _, source = sources(lambda: archive_response(payload), file_type="epub")

    assert source.load_bundle(ROOT_DOC).base == b"PK epub"


def test_the_bundles_document_is_exactly_what_the_catalog_reports():
    payload = rmdoc_archive(scenes={PAGE_ONE: INK})
    catalog, source = sources(lambda: archive_response(payload))

    assert source.load_bundle(ROOT_DOC).document == catalog.get_document(ROOT_DOC)


def test_a_page_the_archive_holds_no_member_for_carries_no_scene():
    """The routine unannotated page of a PDF, and 192 of them on one measured document."""
    payload = rmdoc_archive(
        content=content_sidecar((PAGE_ONE, None), (PAGE_TWO, None)),
        scenes={PAGE_ONE: INK},
    )
    _, source = sources(lambda: archive_response(payload))

    bundle = source.load_bundle(ROOT_DOC)

    assert [page.page_id for page in bundle.pages] == [PAGE_ONE, PAGE_TWO]
    assert bundle.pages[1].scene is None


def test_a_page_whose_member_is_zero_length_carries_no_scene_rather_than_empty_bytes():
    """``scene=None`` is documented as "carries no ink"; 86 of 194 real files are zero bytes."""
    payload = rmdoc_archive(
        content=content_sidecar((PAGE_ONE, None)),
        scenes={PAGE_ONE: b""},
    )
    _, source = sources(lambda: archive_response(payload))

    assert source.load_bundle(ROOT_DOC).pages[0].scene is None


def test_an_orphan_scene_member_is_dropped_rather_than_rendered_as_a_ghost_page():
    """Measured: 16 ``.rm`` members for 10 pages, the extras orphaned by an edit."""
    payload = rmdoc_archive(
        content=content_sidecar((PAGE_ONE, None)),
        scenes={PAGE_ONE: INK, ORPHAN: b"stale-ink"},
    )
    _, source = sources(lambda: archive_response(payload))

    assert [page.page_id for page in source.load_bundle(ROOT_DOC).pages] == [PAGE_ONE]


def test_a_document_claiming_no_pages_at_all_has_an_empty_page_tuple():
    """``DocumentSourceBundle`` documents an empty ``pages`` as exactly this, unambiguously."""
    _, source = sources(lambda: archive_response(rmdoc_archive(content=content_sidecar())))

    assert source.load_bundle(ROOT_DOC).pages == ()


def test_a_page_the_sidecar_claims_twice_reaches_the_bundle_once():
    """``DocumentSourceBundle`` refuses a repeated ``page_id``, so the order must de-duplicate."""
    payload = rmdoc_archive(
        content=content_sidecar((PAGE_ONE, None), (PAGE_ONE, "second claim")),
        scenes={PAGE_ONE: INK},
    )
    _, source = sources(lambda: archive_response(payload))

    assert [page.page_id for page in source.load_bundle(ROOT_DOC).pages] == [PAGE_ONE]


def test_the_download_route_names_the_rmdoc_format_selector_and_sends_no_accept_header():
    """Legacy sent ``Accept: application/zip`` at a filename-shaped third segment instead.

    The segment is a format selector, so any value other than the exact lowercase ``pdf``
    or ``rmdoc`` is answered ``400 {"error": "Filetype not supported"}``.
    """
    accepts: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(LISTING_ROUTE):
            return json_response(listing_body(document_entry(ROOT_DOC)))
        accepts.append(request.headers.get("accept"))
        assert request.url.path == f"/download/{ROOT_DOC}/rmdoc"
        return archive_response(rmdoc_archive(scenes={PAGE_ONE: INK}))

    api = web_api(handler)
    UsbBundleSource(api=api, catalog=UsbCatalog(api=api)).load_bundle(ROOT_DOC)

    assert accepts == ["*/*"]
    assert RMDOC_ROUTE == "/download/{id}/rmdoc"


def test_an_unknown_document_is_diagnosed_before_a_download_is_spent():
    seen: list[str] = []
    _, source = sources(lambda: archive_response(rmdoc_archive()), seen=seen)

    with pytest.raises(DeviceDocumentNotFound):
        source.load_bundle(UNKNOWN)

    assert seen == [f"GET {LISTING_ROUTE}"]


def test_an_archive_body_that_arrived_short_is_never_a_partial_bundle():
    payload = rmdoc_archive(scenes={PAGE_ONE: INK})
    _, source = sources(lambda: archive_response(payload, announced=len(payload) + 1))

    with pytest.raises(DeviceTransferInterrupted) as raised:
        source.load_bundle(ROOT_DOC)

    assert raised.value.subject == ROOT_DOC
    assert raised.value.bytes_transferred == len(payload)
    assert raised.value.bytes_expected == len(payload) + 1


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"not json at all", id="bytes that are not json"),
        pytest.param(b"[]", id="valid json that is not an object"),
        pytest.param(b'{"cPages": {"pages": [{"idx": {}}]}}', id="a page with no id"),
        pytest.param(b'{"cPages": {"pages": "one"}}', id="a page list that is not an array"),
    ],
)
def test_a_content_sidecar_that_will_not_decode_is_malformed_device_metadata(content: bytes):
    """``decode_page_order`` raises ``TypeError``/``ValueError``; this is where they become one."""
    _, source = sources(lambda: archive_response(rmdoc_archive(content=content)))

    with pytest.raises(MalformedDeviceMetadata) as raised:
        source.load_bundle(ROOT_DOC)

    assert raised.value.document_uuid == ROOT_DOC
    assert raised.value.detail
    assert "\n" not in raised.value.detail


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"%PDF-1.7 the device-rendered pdf", id="the pdf a wrong selector returns"),
        pytest.param(b"", id="an empty body"),
    ],
)
def test_a_download_body_that_is_not_a_zip_is_a_protocol_error(payload: bytes):
    _, source = sources(lambda: archive_response(payload))

    with pytest.raises(DeviceProtocolError) as raised:
        source.load_bundle(ROOT_DOC)

    assert raised.value.route == RMDOC_ROUTE


def test_a_pdf_document_whose_archive_omits_the_underlay_is_a_protocol_error():
    """The archive is the transport's own answer, and an answer without it contradicts itself."""
    _, source = sources(
        lambda: archive_response(rmdoc_archive(scenes={PAGE_ONE: INK})),
        file_type="pdf",
    )

    with pytest.raises(DeviceProtocolError) as raised:
        source.load_bundle(ROOT_DOC)

    assert raised.value.expected == f"a {ROOT_DOC}.pdf member"


# ─────────────────────── identifiers cannot name another route ───────────────────────


def test_a_folder_identifier_carrying_a_separator_cannot_name_another_route():
    """Measured: httpx normalises ``/documents/../upload`` to ``/upload`` before the wire.

    The firmware ignores the request method, so a read command reaching ``/upload`` is not
    a hypothetical. Percent-encoding keeps the identifier inside its own path segment.
    """
    seen: list[str] = []
    catalog = UsbCatalog(api=web_api(tablet(tree={"": [folder_entry("../upload")]}, seen=seen)))

    catalog.list_documents()

    assert seen == [f"GET {LISTING_ROUTE}", f"GET {LISTING_ROUTE}..%2Fupload"]


def test_a_document_identifier_carrying_a_separator_cannot_name_another_route():
    """``load_bundle`` takes its argument from a CLI, so this one starts outside the device.

    The archive reader refuses the crafted identifier a second time -- every member name
    has to resolve to it -- so the assertion here is about the *path that was requested*,
    which is the layer that would otherwise have reached ``/upload``.
    """
    crafted = "../upload"
    seen: list[str] = []
    api = web_api(
        tablet(
            tree={"": [document_entry(crafted)]},
            downloads={
                "/download/..%2Fupload/rmdoc": lambda: archive_response(b"PK not an archive")
            },
            seen=seen,
        )
    )

    with pytest.raises(DeviceProtocolError):
        UsbBundleSource(api=api, catalog=UsbCatalog(api=api)).load_bundle(crafted)

    assert "GET /download/..%2Fupload/rmdoc" in seen


# ─────────────────────────────── the facts source ───────────────────────────────


def test_read_facts_names_every_fixed_fact_unsupported():
    """The route table is closed at six families and none of them reports device identity."""
    facts = UsbFacts(api=web_api(tablet(tree={"": []}))).read_facts()

    assert facts.unsupported == frozenset({"firmware", "model", "serial"})
    assert (facts.firmware, facts.model, facts.serial) == (None, None, None)


def test_read_resources_names_every_gauge_unsupported():
    resources = UsbFacts(api=web_api(tablet(tree={"": []}))).read_resources()

    assert resources.unsupported == frozenset(
        {
            "total_memory_bytes",
            "available_memory_bytes",
            "total_storage_bytes",
            "available_storage_bytes",
        }
    )
    assert resources.total_storage_bytes is None
    assert resources.available_storage_bytes is None


def test_both_readings_probe_the_tablet_with_one_head_of_the_listing_route():
    """A facts source that never touched the wire would call a detached tablet "unsupported"."""
    seen: list[str] = []
    facts = UsbFacts(api=web_api(tablet(tree={"": []}, seen=seen)))

    facts.read_facts()
    facts.read_resources()

    assert seen == [f"HEAD {LISTING_ROUTE}", f"HEAD {LISTING_ROUTE}"]


def test_the_facts_source_never_reads_the_users_journal():
    """``/log.txt`` is 9.7 MB of the user's own journal for one version number."""
    seen: list[str] = []
    UsbFacts(api=web_api(tablet(tree={"": []}, seen=seen))).read_facts()

    assert not [request for request in seen if "log" in request]


@pytest.mark.parametrize("reading", ["read_facts", "read_resources"])
def test_a_detached_tablet_is_unreachable_rather_than_everything_unsupported(reading: str):
    detached = httpx.ConnectError("no route to the tablet")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise detached

    facts = UsbFacts(api=web_api(handler))

    with pytest.raises(DeviceUnreachable):
        getattr(facts, reading)()


@pytest.mark.parametrize("reading", ["read_facts", "read_resources"])
def test_a_probe_the_tablet_refuses_is_a_protocol_error(reading: str):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"")

    facts = UsbFacts(api=web_api(handler))

    with pytest.raises(DeviceProtocolError):
        getattr(facts, reading)()


# ─────────────────────── the absence that is the design (D5) ───────────────────────


def test_the_module_exports_no_usb_uploader():
    """``POST /upload`` has never been probed in any form, and a guess would risk the notes.

    The server ignores the request method, so a ``GET`` to that path could not have been
    proven non-mutating, and its field name, content types and response body are all
    unmeasured. ``ports/device.py`` expresses capability asymmetry as which ports exist, so
    the composition root fails to bind and the shell says "retry over SSH". This test is
    what stops a later reader "fixing" the omission.
    """
    assert usb.__all__ == ["UsbBundleSource", "UsbCatalog", "UsbFacts", "UsbWebApi"]
    # The one upload-shaped name in the namespace is the error class the shared error seam
    # can produce, which is translation vocabulary rather than a capability. Nothing else.
    assert {name for name in dir(usb) if "upload" in name.lower()} == {"DeviceUploadRejected"}
    assert not [
        name
        for name, value in vars(usb).items()
        if "upload" in name.lower() and getattr(value, "__module__", None) == usb.__name__
    ]
    for exported in usb.__all__:
        assert not hasattr(getattr(usb, exported), "upload")


def test_the_transport_has_exactly_two_verbs_and_neither_writes():
    verbs = {name for name in vars(UsbWebApi) if not name.startswith("_")}

    assert verbs == {"get", "head"}
