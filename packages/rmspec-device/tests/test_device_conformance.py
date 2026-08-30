"""The port contracts, bound to every implementation this package ships.

Fourteen bindings: the four USB adapters over ``httpx.MockTransport``, the five SSH adapters
over the shipped in-memory shell, and the five doubles. The assertions are literally the same
objects, imported from ``device_contracts.py``, so a double that quietly narrowed or widened
its behaviour fails here rather than three packages away -- and so does an adapter.

Every fixture is **synthesised** from the measured shape, never captured. A real
``/documents/`` body carries the user's document titles and a real ``.rmdoc`` carries their
handwriting, so nothing from the attached device is committed; each builder's docstring names
the shape and the evidence it stands on.

Where a binding cannot inject the failure the contract asks for, it *causes* it instead. The
USB bindings have no seam that hands an adapter a domain error, so ``failing_catalog`` builds
a handler that raises ``httpx.ConnectError`` and lets ``_errors.translate_httpx`` classify it
-- which is a better test than injection would have been, because the translation is on the
path being asserted.

The last section asserts the shape of the write surface and one genuine absence. It used to
assert that ``DocumentUploader`` and ``SearchIndexSource`` each had exactly one binding, for
two different reasons -- an unprobed write route and a route family that does not exist. Only
the second reason survived: ``POST /upload`` was measured on 2026-08-29, so the uploader
assertion is replaced by one that pins **two** bindings and the different half each of them
refuses, while the search-index assertion stands unchanged.
"""

from __future__ import annotations

import datetime
import io
import json
import zipfile
from typing import TYPE_CHECKING

import httpx
import pytest
from device_contracts import (
    FOLDER_UUID,
    INK,
    MODIFIED,
    NOTEBOOK_PAGES,
    NOTEBOOK_UUID,
    PAGE_ONE,
    PAGE_THREE,
    PAGE_TWO,
    PDF_UUID,
    SEARCH_INDEX_IMAGE,
    UNDERLAY,
    BoundCatalog,
    BoundUploader,
    DeviceCatalogContract,
    DeviceFactsSourceContract,
    DocumentUploaderContract,
    RawBundleSourceContract,
    SearchIndexSourceContract,
    SkipCase,
    an_upload,
)

import rmspec.device
from rmspec.device import (
    SshBundleSource,
    SshCatalog,
    SshFacts,
    SshSearchIndexSource,
    SshUploader,
    UsbBundleSource,
    UsbCatalog,
    UsbFacts,
    UsbUploader,
    UsbWebApi,
    usb,
)
from rmspec.device._archive import RMDOC_ROUTE
from rmspec.device._wire import COLLECTION_TYPE, DOCUMENT_TYPE, LISTING_ROUTE
from rmspec.device.addresses import (
    BOOT_ID,
    CONTENT_SUFFIX,
    METADATA_SUFFIX,
    OS_RELEASE,
    PROC_MEMINFO,
    SCENE_SUFFIX,
    SEARCH_INDEX_NAME,
    SOC_MACHINE,
    Endpoint,
    RemoteCommand,
    RemotePath,
)
from rmspec.device.ssh import (
    ACTIVE_STATE,
    BOOT_ID_TEMPLATE,
    FIRMWARE_TEMPLATE,
    MAKE_DIR_TEMPLATE,
    MEMINFO_TEMPLATE,
    MODEL_TEMPLATE,
    NO_SERIAL_SOURCE,
    RESET_FAILED_TEMPLATE,
    RESTART_TEMPLATE,
    SERIAL_FIELD,
    SERVICE_STATE_TEMPLATE,
    STORAGE_TEMPLATE,
    UI_SERVICE,
    UNPLACEABLE_MEDIA,
    UNPLACEABLE_OPERATION,
)
from rmspec.device.testing import (
    FakeRemoteShell,
    FakeSearchIndexSource,
    InMemoryDeviceCatalog,
    InMemoryDeviceFactsSource,
    InMemoryDocumentUploader,
    InMemoryRawBundleSource,
)
from rmspec.device.testing import doubles as device_doubles
from rmspec.device.usb import UPLOAD_CREATED, UPLOAD_FIELD, UPLOAD_MEDIA_TYPES, UPLOAD_ROUTE
from rmspec.domain.errors import (
    DeviceOperationUnsupported,
    DeviceTransferInterrupted,
    TransportKind,
)
from rmspec.domain.ports.device import (
    DeviceDocument,
    DeviceFacts,
    DeviceFileType,
    DeviceFolder,
    DevicePageSource,
    DeviceResources,
    LibraryRefresh,
    SkippedEntry,
    SkipReason,
    UploadMedia,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rmspec.domain.errors import DeviceError
    from rmspec.domain.ports.device import (
        DeviceCatalog,
        DeviceFactsSource,
        RawBundleSource,
        SearchIndexSource,
    )

ROOT = RemotePath.root()
"""The xochitl root every SSH binding builds its synthetic store under."""

INDEX_PATH = ROOT.child(SEARCH_INDEX_NAME)
"""Where the search index sits: directly under the root, no document directory involved.
Composed here rather than spelled, so the binding cannot pass while the adapter reads a path
this file does not agree with."""

SKIPPED_UUID = "ffffffff-0000-4000-8000-000000000006"
"""The entry each binding makes unrepresentable, one way per ``SkipReason``."""

MINTED_UUID = "0d1e2f30-4152-4364-9576-8798a9bacbdc"
"""What the SSH uploader's injected mint returns, so the written paths are deterministic."""

NOW_MS = 1_755_000_000_000
"""What the SSH uploader's injected clock returns, for the same reason."""

TEMPLATE = "P Grid small"
"""One template name, recorded on the notebook's first page."""

NOTEBOOK_NAME = "Sprint notes"
FOLDER_NAME = "Work"
PDF_NAME = "Interface spec"

MODIFIED_CLIENT = MODIFIED.isoformat().replace("+00:00", "Z")
"""``ModifiedClient`` as the USB wire spells it: ISO-8601 with a literal ``Z``. Derived from
the contract's instant rather than spelled twice, so the two cannot drift."""

MODIFIED_EPOCH_MS = str(int(MODIFIED.timestamp()) * 1000 + MODIFIED.microsecond // 1000)
"""``lastModified`` as the xochitl store spells it: epoch milliseconds in a json *string*.
Computed rather than multiplied through a float, which loses the millisecond."""

JSON_CONTENT_TYPE = "application/json; charset=ISO-8859-1"
"""The charset every json body advertises while carrying UTF-8 titles. A lie, honoured."""

FIRMWARE = "3.27.3.0"
"""``IMG_VERSION`` on the reference device, measured 2026-08-29."""

MODEL = "reMarkable Ferrari"
"""``/sys/devices/soc0/machine`` on the reference device."""

MEMINFO_OUTPUT = (
    "MemTotal:        2009400 kB\nMemFree:         1224912 kB\nMemAvailable:    1251016 kB\n"
)
"""The three lines of ``/proc/meminfo`` the facts adapter reads, measured 2026-08-29."""

DF_PK_OUTPUT = (
    "Filesystem           1024-blocks    Used Available Capacity Mounted on\n"
    "/dev/mapper/home-encrypted-disk             48568796    112564  47929504   0% /home\n"
)
"""``df -Pk`` on the xochitl root, measured 2026-08-29. Six fields on one line, which is the
whole reason for the ``-P``: the 31-character device name overflows BusyBox's 20-column
field and plain ``df -k`` puts the numbers on the following line."""

_BYTES_PER_KIB = 1024

IN_MEMORY_FACTS = DeviceFacts(
    firmware=FIRMWARE,
    model=MODEL,
    serial=None,
    unsupported=frozenset({SERIAL_FIELD}),
)
"""What the doubles report as fixed facts. Both causes of ``None`` in one value would need
two fields; this one exercises the *named* cause, and ``IN_MEMORY_RESOURCES`` the other.

Deliberately a **bare name** where the two real adapters now pass an
``UnsupportedField``. That is the shape a fake seeds and the shape every adapter used before
the port could carry a claim, so keeping it here is what exercises the other arm of the
union: the contract suite runs over a source that annotates nothing and two that do."""

IN_MEMORY_RESOURCES = DeviceResources(
    total_memory_bytes=2009400 * _BYTES_PER_KIB,
    available_memory_bytes=1251016 * _BYTES_PER_KIB,
    total_storage_bytes=48568796 * _BYTES_PER_KIB,
    available_storage_bytes=None,
    unsupported=frozenset(),
)
"""What the doubles report as gauges. ``available_storage_bytes`` is an *unnamed* ``None``:
asked for and not answered intelligibly, which is the port's second cause and the one the
SSH adapter produces for a ``df`` line it cannot read."""

IN_MEMORY_NOTEBOOK = DeviceDocument(
    uuid=NOTEBOOK_UUID,
    name=NOTEBOOK_NAME,
    file_type=DeviceFileType.NOTEBOOK,
    parent_uuid=None,
    last_modified=MODIFIED,
    page_count=len(NOTEBOOK_PAGES),
)
IN_MEMORY_PDF = DeviceDocument(
    uuid=PDF_UUID,
    name=PDF_NAME,
    file_type=DeviceFileType.PDF,
    parent_uuid=FOLDER_UUID,
    last_modified=MODIFIED,
    page_count=1,
)
IN_MEMORY_FOLDER = DeviceFolder(uuid=FOLDER_UUID, name=FOLDER_NAME, last_modified=MODIFIED)


# ───────────────────────────── the USB wire, synthesised ─────────────────────────────


def document_entry(
    doc_uuid: str,
    *,
    parent: str = "",
    file_type: str = "notebook",
    name: str = NOTEBOOK_NAME,
    **overrides: object,
) -> dict[str, object]:
    """Build a ``DocumentType`` listing entry: the 9 keys measured on firmware 3.27.3.0.

    ``Bookmarked``, ``CurrentPage``, ``ID``, ``ModifiedClient``, ``Parent``, ``Type``,
    ``VisibleName``, ``VissibleName`` (sic, equal to ``VisibleName`` on all 31 entries
    observed) and ``fileType``. ``CurrentPage`` is the last-opened page *index* and not a
    count, which is why a USB-decoded document's ``page_count`` is always ``None``.

    Parameters
    ----------
    doc_uuid
        The entry's ``ID``.
    parent
        The entry's ``Parent``: a folder identifier, or ``""`` for the library root.
    file_type
        The entry's ``fileType``.
    name
        Both spellings of the visible name.
    **overrides
        Members to replace, which is how a malformed entry is expressed.

    Returns
    -------
    dict[str, object]
        The entry.
    """
    entry: dict[str, object] = {
        "Bookmarked": False,
        "CurrentPage": 3,
        "ID": doc_uuid,
        "ModifiedClient": MODIFIED_CLIENT,
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
    name: str = FOLDER_NAME,
) -> dict[str, object]:
    """Build a ``CollectionType`` entry: the measured document set minus two keys.

    ``CurrentPage`` and ``fileType`` are absent, because a folder has neither a last-opened
    page nor an underlay.

    Parameters
    ----------
    folder_uuid
        The entry's ``ID``.
    parent
        The entry's ``Parent``.
    name
        Both spellings of the visible name.

    Returns
    -------
    dict[str, object]
        The entry.
    """
    entry = document_entry(folder_uuid, parent=parent, name=name)
    del entry["CurrentPage"]
    del entry["fileType"]
    entry["Type"] = COLLECTION_TYPE
    return entry


def listing_body(*entries: object) -> bytes:
    """Render a ``/documents/`` response body: a bare json array of flat objects.

    Parameters
    ----------
    *entries
        The entries to list.

    Returns
    -------
    bytes
        The body.
    """
    return json.dumps(list(entries)).encode()


def content_sidecar(*pages: tuple[str, str | None], file_type: str = "notebook") -> bytes:
    """Render a firmware-3.x ``.content`` page order: ``cPages.pages[]``, CRDT-stamped.

    Measured on the archived member of a 1-page pdf document: 22 top-level keys with
    ``cPages`` present, the flat top-level ``pages`` array **absent**, ``formatVersion`` 2,
    and each page's template wrapped in a ``{"timestamp", "value"}`` envelope. Only the keys
    a transport reads are reproduced.

    Parameters
    ----------
    *pages
        One ``(page identifier, template name or None)`` pair per page, in recorded order.
    file_type
        The sidecar's ``fileType``.

    Returns
    -------
    bytes
        The sidecar, as UTF-8 json.
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
            "fileType": file_type,
            "formatVersion": 2,
            "orientation": "portrait",
            "pageCount": len(listed),
        }
    ).encode()


def rmdoc_archive(
    *,
    doc_uuid: str,
    content: bytes,
    scenes: Mapping[str, bytes],
    underlay: tuple[str, bytes] | None = None,
) -> bytes:
    """Build the measured ``.rmdoc`` member set as an in-memory zip.

    Reproduces what firmware 3.27.3.0 returns from ``GET /download/{id}/rmdoc``:
    ``<docUUID>.metadata`` (always), ``<docUUID>.content`` (always), one
    ``<docUUID>/<pageUUID>.rm`` per page in the content's page list **including zero-byte
    placeholders**, and ``<docUUID>.<fileType>`` for a non-notebook document. Never
    ``.local``, ``.pagedata``, ``.thumbnails`` or ``.failure``, and never nesting below
    ``<docUUID>/``.

    Parameters
    ----------
    doc_uuid
        The document the archive belongs to. Every member name derives from it.
    content
        The ``.content`` member's bytes.
    scenes
        Page identifier mapped to its scene bytes, zero length included.
    underlay
        ``(suffix, bytes)`` for a non-notebook document, or ``None``.

    Returns
    -------
    bytes
        The archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{doc_uuid}{METADATA_SUFFIX}", b'{"type": "DocumentType"}')
        archive.writestr(f"{doc_uuid}{CONTENT_SUFFIX}", content)
        for page_id, payload in scenes.items():
            archive.writestr(f"{doc_uuid}/{page_id}{SCENE_SUFFIX}", payload)
        if underlay is not None:
            archive.writestr(f"{doc_uuid}.{underlay[0]}", underlay[1])
    return buffer.getvalue()


NOTEBOOK_ARCHIVE = rmdoc_archive(
    doc_uuid=NOTEBOOK_UUID,
    content=content_sidecar((PAGE_ONE, TEMPLATE), (PAGE_TWO, None)),
    scenes={PAGE_ONE: INK, PAGE_TWO: b""},
)
"""The reference notebook's archive. ``PAGE_TWO``'s member is zero bytes, which is the
routine state of an unannotated page: 86 of 194 real ``.rm`` files are exactly that."""

PDF_ARCHIVE = rmdoc_archive(
    doc_uuid=PDF_UUID,
    content=content_sidecar((PAGE_THREE, None), file_type="pdf"),
    scenes={PAGE_THREE: INK},
    underlay=("pdf", UNDERLAY),
)
"""The reference annotated PDF's archive, underlay included."""


def json_response(body: bytes) -> httpx.Response:
    """Answer one listing as measured: chunked, **no** ``Content-Length``, lying charset.

    Parameters
    ----------
    body
        The response body.

    Returns
    -------
    httpx.Response
        The response.
    """
    return httpx.Response(200, content=iter([body]), headers={"content-type": JSON_CONTENT_TYPE})


def archive_response(payload: bytes, *, announced: int | None = None) -> httpx.Response:
    """Answer one ``/download`` as measured: **both** ``Content-Length`` and chunked.

    Parameters
    ----------
    payload
        The archive bytes actually sent.
    announced
        The length to advertise, which is how a short read is expressed without a real
        dropped connection. Defaults to the payload's own length.

    Returns
    -------
    httpx.Response
        The response.
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


def created() -> httpx.Response:
    """Answer one ``POST /upload`` as measured: ``201`` and a body carrying no identifier.

    Returns
    -------
    httpx.Response
        ``201 {"status": "Upload successful"}``, which is verbatim what firmware 3.27.3.0
        answered to a part named ``file`` -- for a PDF and for a ``.rmdoc`` alike.
    """
    return httpx.Response(
        UPLOAD_CREATED,
        content=json.dumps({"status": "Upload successful"}).encode(),
        headers={"content-type": JSON_CONTENT_TYPE},
    )


def refused() -> httpx.Response:
    """Answer a routed refusal: the folder is listed, and its children are not served.

    Returns
    -------
    httpx.Response
        ``400`` with the uniform ``{"error": ...}`` body, whose message
        ``_errors.translate_http`` maps to ``DeviceProtocolError`` -- a *routed* failure, so
        the walk records one ``UNREADABLE`` skip rather than aborting.
    """
    return httpx.Response(
        400,
        content=json.dumps({"error": "Malformed URL"}).encode(),
        headers={"content-type": JSON_CONTENT_TYPE},
    )


def tablet(
    *,
    tree: Mapping[str, Sequence[object]],
    answers: Mapping[str, Callable[[], httpx.Response]] | None = None,
    downloads: Mapping[str, Callable[[], httpx.Response]] | None = None,
    seen: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler reproducing the measured ``/documents/`` resolution rule.

    ``GET /documents/`` answers ``tree[""]``. ``GET /documents/{pid}`` answers ``tree[pid]``
    when *pid* names a folder the tree knows, and **the byte-identical root listing**
    otherwise -- the silent fallback measured for a document id and for an unknown id, which
    returns status 200 and logs nothing, so a 200 is not proof the id was recognised.

    Parameters
    ----------
    tree
        Parent identifier mapped to its children, with ``""`` for the root.
    answers
        Identifier mapped to an arbitrary response, which is how a refused sub-listing is
        expressed.
    downloads
        Raw request path mapped to a response, for the download routes.
    seen
        Records every request as ``"<METHOD> <raw path>"``, which is what proves a walk
        terminated and that a memoised listing was not re-fetched.

    Returns
    -------
    Callable[[httpx.Request], httpx.Response]
        The handler, for ``httpx.MockTransport``.
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


def web_api(handler: Callable[[httpx.Request], httpx.Response]) -> UsbWebApi:
    """Bind the transport to a fake device. The client is injected, never constructed.

    Parameters
    ----------
    handler
        What answers every request.

    Returns
    -------
    UsbWebApi
        The transport.
    """
    return UsbWebApi(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        endpoint=Endpoint(),
    )


def dead_tablet(failure: DeviceError) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler whose every request fails below httpx, as an unplugged cable does.

    Parameters
    ----------
    failure
        The domain failure the contract asked for. Its message is carried into the
        ``httpx`` exception so the raised ``DeviceUnreachable`` names what was seeded --
        the class itself comes from ``_errors.translate_httpx``, which is the point: the
        USB bindings have no seam that hands an adapter a ready-made domain error, so the
        translation is exercised rather than bypassed.

    Returns
    -------
    Callable[[httpx.Request], httpx.Response]
        The handler.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(str(failure), request=request)

    return handler


def _upload_tablet(
    placed: list[str],
    *,
    recorder: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler that accepts one upload, answers its pre-flight, and routes nothing else.

    Deliberately narrower than :func:`tablet`: this fake serves exactly the two routes the
    uploader is allowed to touch -- the root listing it sends to pin the destination, and the
    write -- so a request to any other path fails the test rather than being answered.

    ``placed`` counts only the write, so the pre-flight cannot inflate the placement counter the
    contract asserts on.

    Parameters
    ----------
    placed
        Appended to once per accepted upload, which is the uploader binding's placement
        counter.
    recorder
        Every request, for the assertions about the multipart body this package builds.

    Returns
    -------
    Callable[[httpx.Request], httpx.Response]
        The handler, for ``httpx.MockTransport``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request)
        path = request.url.raw_path.decode()
        if request.method == "GET" and path == LISTING_ROUTE:
            return json_response(b"[]")
        if request.method == "POST" and path == UPLOAD_ROUTE:
            placed.append(path)
            return created()
        pytest.fail(f"the fake tablet was asked for {request.method} {path!r}, which it refuses")

    return handler


def usb_tree(*extra_root: object) -> dict[str, list[object]]:
    """Build the reference library's ``/documents/`` tree, plus any extra root entries.

    Parameters
    ----------
    *extra_root
        Entries to append to the root listing, which is how a skip is caused.

    Returns
    -------
    dict[str, list[object]]
        The root listing and the one folder listing below it. Nesting to depth 1 is what
        makes the breadth-first walk load-bearing here rather than a formality.
    """
    return {
        "": [
            document_entry(NOTEBOOK_UUID, file_type="notebook", name=NOTEBOOK_NAME),
            folder_entry(FOLDER_UUID),
            *extra_root,
        ],
        FOLDER_UUID: [
            document_entry(PDF_UUID, parent=FOLDER_UUID, file_type="pdf", name=PDF_NAME),
        ],
    }


# ───────────────────────────── the xochitl store, synthesised ─────────────────────────────


def store_metadata(
    *,
    name: str,
    kind: str = DOCUMENT_TYPE,
    parent: str = "",
) -> bytes:
    """Build a device-shaped ``.metadata`` sidecar.

    Synthesised from the measured 8-key universal shape rather than captured: a real file
    carries the user's document titles.

    Parameters
    ----------
    name
        ``visibleName``.
    kind
        ``type``: ``DocumentType`` or ``CollectionType``.
    parent
        ``parent``: a uuid, or ``""`` for the library root.

    Returns
    -------
    bytes
        The sidecar, as UTF-8 json.
    """
    return json.dumps(
        {
            "createdTime": MODIFIED_EPOCH_MS,
            "lastModified": MODIFIED_EPOCH_MS,
            "lastOpened": "",
            "lastOpenedPage": 0,
            "parent": parent,
            "pinned": False,
            "type": kind,
            "visibleName": name,
        }
    ).encode()


def store_content(*, file_type: str, pages: Sequence[tuple[str, str | None]]) -> bytes:
    """Build a device-shaped ``.content`` sidecar with a ``cPages`` page list.

    Parameters
    ----------
    file_type
        ``fileType``, which is what decides a document's kind over SSH.
    pages
        One ``(page identifier, template name or None)`` pair per page, in recorded order.

    Returns
    -------
    bytes
        The sidecar, as UTF-8 json.
    """
    return content_sidecar(*pages, file_type=file_type)


def ssh_store(
    *,
    extra_files: Mapping[str, bytes] | None = None,
    refuse_reads: Sequence[str] = (),
    outputs: Mapping[str, str] | None = None,
) -> FakeRemoteShell:
    """Build a shell over the reference library laid out as a xochitl store.

    Parameters
    ----------
    extra_files
        Extra root-level filenames mapped to their bytes, which is how a skip is caused.
    refuse_reads
        Root-level filenames whose read is refused, which is the only route to
        ``UNREADABLE``.
    outputs
        Scripted command output, for the facts and upload bindings.

    Returns
    -------
    FakeRemoteShell
        The double, with its file and directory maps already built.
    """
    root_files: dict[str, bytes] = {
        f"{NOTEBOOK_UUID}{METADATA_SUFFIX}": store_metadata(name=NOTEBOOK_NAME),
        f"{NOTEBOOK_UUID}{CONTENT_SUFFIX}": store_content(
            file_type="notebook",
            pages=[(PAGE_ONE, TEMPLATE), (PAGE_TWO, None)],
        ),
        f"{FOLDER_UUID}{METADATA_SUFFIX}": store_metadata(
            name=FOLDER_NAME,
            kind=COLLECTION_TYPE,
        ),
        f"{PDF_UUID}{METADATA_SUFFIX}": store_metadata(name=PDF_NAME, parent=FOLDER_UUID),
        f"{PDF_UUID}{CONTENT_SUFFIX}": store_content(
            file_type="pdf",
            pages=[(PAGE_THREE, None)],
        ),
        f"{PDF_UUID}.pdf": UNDERLAY,
        **(extra_files or {}),
    }
    page_dirs: dict[str, dict[str, bytes]] = {
        NOTEBOOK_UUID: {f"{PAGE_ONE}{SCENE_SUFFIX}": INK, f"{PAGE_TWO}{SCENE_SUFFIX}": b""},
        PDF_UUID: {f"{PAGE_THREE}{SCENE_SUFFIX}": INK},
    }
    files = {ROOT.child(name).value: data for name, data in root_files.items()}
    dirs: dict[str, tuple[str, ...]] = {ROOT.value: (*root_files, *page_dirs)}
    for doc_uuid, scenes in page_dirs.items():
        page_dir = ROOT.child(doc_uuid)
        dirs[page_dir.value] = tuple(scenes)
        for scene_name, data in scenes.items():
            files[page_dir.child(scene_name).value] = data
    return FakeRemoteShell(
        outputs=outputs,
        files=files,
        dirs=dirs,
        refuse_reads=[ROOT.child(name).value for name in refuse_reads],
    )


FIRMWARE_COMMAND = RemoteCommand.of(FIRMWARE_TEMPLATE, RemotePath.absolute(OS_RELEASE)).text
MODEL_COMMAND = RemoteCommand.of(MODEL_TEMPLATE, RemotePath.absolute(SOC_MACHINE)).text
MEMINFO_COMMAND = RemoteCommand.of(MEMINFO_TEMPLATE, RemotePath.absolute(PROC_MEMINFO)).text
STORAGE_COMMAND = RemoteCommand.of(STORAGE_TEMPLATE, RemotePath.root()).text

HEALTHY_FACTS = {
    FIRMWARE_COMMAND: f"{FIRMWARE}\n",
    MODEL_COMMAND: f"{MODEL}\n",
    MEMINFO_COMMAND: MEMINFO_OUTPUT,
    STORAGE_COMMAND: DF_PK_OUTPUT,
}
"""The four commands ``SshFacts`` sends, each with the output measured 2026-08-29."""

RESTART_COMMAND = RemoteCommand.of(RESTART_TEMPLATE, UI_SERVICE).text
FENCE_COMMAND = RemoteCommand.of(BOOT_ID_TEMPLATE, RemotePath.absolute(BOOT_ID)).text

#: A synthetic per-boot identifier, answered identically to both fence reads so the guarded
#: restart concludes -- correctly -- that the tablet did not reboot.
BOOT_ID_VALUE = "2f7c1e04-9a3b-4d58-8e21-6b5c0d4a7f93"

UPLOAD_COMMANDS = {
    RemoteCommand.of(MAKE_DIR_TEMPLATE, ROOT.child(MINTED_UUID)).text: "",
    RemoteCommand.of(SERVICE_STATE_TEMPLATE, UI_SERVICE).text: f"{ACTIVE_STATE}\n",
    FENCE_COMMAND: f"{BOOT_ID_VALUE}\n",
    RemoteCommand.of(RESET_FAILED_TEMPLATE, UI_SERVICE).text: "",
    RESTART_COMMAND: "",
}
"""The six commands ``SshUploader`` sends around its three writes: the ``mkdir`` before them,
and the five of the guarded restart after -- read the UI's state, arm the reboot fence, clear
the start-limit counter, restart, verify the fence. ``test_device_ssh.py`` owns the assertions
about each one; this map exists so the contract suite drives a healthy device."""


# ───────────────────────────── the USB bindings ─────────────────────────────


class TestUsbCatalog(DeviceCatalogContract):
    """The catalog contract over the USB web API and its breadth-first walk."""

    @pytest.fixture
    def bound(self) -> BoundCatalog:
        """Return a catalog over the reference library, and its request counter.

        Returns
        -------
        BoundCatalog
            The adapter, which ``ty`` checks against the Protocol here.
        """
        seen: list[str] = []
        catalog = UsbCatalog(api=web_api(tablet(tree=usb_tree(), seen=seen)))
        return BoundCatalog(catalog=catalog, fetches=lambda: len(seen))

    def catalog_with_skip(self, reason: SkipReason) -> SkipCase:
        """Cause one skip of *reason* with a payload the decoder refuses.

        Parameters
        ----------
        reason
            Which diagnosis to cause.

        Returns
        -------
        SkipCase
            The adapter and the skipped identifier.
        """
        answers: dict[str, Callable[[], httpx.Response]] = {}
        if reason is SkipReason.MALFORMED_METADATA:
            extra: object = document_entry(SKIPPED_UUID, ModifiedClient="not-an-instant")
        elif reason is SkipReason.VALIDATION_FAILED:
            extra = document_entry(SKIPPED_UUID, file_type="docx")
        else:
            # UNREADABLE is only reachable for a *folder*: the transport has to have seen
            # the entry and been refused its children, and a document id has no children
            # to be refused. So this identifier is in `folders` as well as in `skipped`,
            # and get_document still answers MalformedDeviceMetadata because the catalog
            # searches `skipped` before falling through.
            extra = folder_entry(SKIPPED_UUID, name="Refused")
            answers[SKIPPED_UUID] = refused
        tree = usb_tree(extra)
        catalog = UsbCatalog(api=web_api(tablet(tree=tree, answers=answers)))
        return SkipCase(catalog=catalog, doc_uuid=SKIPPED_UUID)

    def failing_catalog(self, failure: DeviceError) -> DeviceCatalog:
        """Return a catalog whose every request fails below httpx.

        Parameters
        ----------
        failure
            The failure the contract seeded.

        Returns
        -------
        DeviceCatalog
            The adapter.
        """
        return UsbCatalog(api=web_api(dead_tablet(failure)))

    def test_the_walk_reaches_depth_one_and_terminates(self, bound: BoundCatalog) -> None:
        """The walk reaches depth one and terminates."""
        # Two requests and no more: the root, then the one folder. Without a visited set the
        # silent root fallback would re-enqueue the root's folders forever.
        bound.catalog.list_documents()
        assert bound.fetches() == 2


class TestUsbBundleSource(RawBundleSourceContract):
    """The bundle contract over ``GET /download/{id}/rmdoc``."""

    @staticmethod
    def _sources(
        *,
        notebook: Callable[[], httpx.Response],
    ) -> UsbBundleSource:
        """Wire a bundle source and the catalog it reads documents from.

        Parameters
        ----------
        notebook
            What answers the notebook's download route.

        Returns
        -------
        UsbBundleSource
            The adapter. Both ports share one transport, which is what the composition root
            does: every port is one view over a single ``Scope.REQUEST`` resource.
        """
        api = web_api(
            tablet(
                tree=usb_tree(),
                downloads={
                    _download_route(NOTEBOOK_UUID): notebook,
                    _download_route(PDF_UUID): lambda: archive_response(PDF_ARCHIVE),
                },
            )
        )
        return UsbBundleSource(api=api, catalog=UsbCatalog(api=api))

    @pytest.fixture
    def source(self) -> RawBundleSource:
        """Return a bundle source over the reference library.

        Returns
        -------
        RawBundleSource
            The adapter, which ``ty`` checks against the Protocol here.
        """
        return self._sources(notebook=lambda: archive_response(NOTEBOOK_ARCHIVE))

    def truncated_source(self) -> RawBundleSource:
        """Return a source whose notebook archive arrives shorter than announced.

        Returns
        -------
        RawBundleSource
            The adapter.
        """
        return self._sources(
            notebook=lambda: archive_response(
                NOTEBOOK_ARCHIVE,
                announced=len(NOTEBOOK_ARCHIVE) + 64,
            )
        )

    def failing_source(self, failure: DeviceError) -> RawBundleSource:
        """Return a source whose every request fails below httpx.

        Parameters
        ----------
        failure
            The failure the contract seeded.

        Returns
        -------
        RawBundleSource
            The adapter.
        """
        api = web_api(dead_tablet(failure))
        return UsbBundleSource(api=api, catalog=UsbCatalog(api=api))

    def test_an_orphan_layer_the_page_order_does_not_claim_is_dropped(self) -> None:
        """An orphan layer the page order does not claim is dropped."""
        # Measured: 16 .rm members for 10 pages. Iterating the archive instead of the page
        # order renders ghost pages.
        orphan = "44444444-0000-4000-8000-00000000000d"
        archive = rmdoc_archive(
            doc_uuid=NOTEBOOK_UUID,
            content=content_sidecar((PAGE_ONE, TEMPLATE), (PAGE_TWO, None)),
            scenes={PAGE_ONE: INK, PAGE_TWO: b"", orphan: INK},
        )
        source = self._sources(notebook=lambda: archive_response(archive))
        pages = source.load_bundle(NOTEBOOK_UUID).pages
        assert tuple(page.page_id for page in pages) == NOTEBOOK_PAGES


class TestUsbFacts(DeviceFactsSourceContract):
    """The facts contract over a transport whose route table answers none of them."""

    @pytest.fixture
    def source(self) -> DeviceFactsSource:
        """Return a facts source over an attached tablet.

        Returns
        -------
        DeviceFactsSource
            The adapter, which ``ty`` checks against the Protocol here.
        """
        return UsbFacts(api=web_api(tablet(tree=usb_tree())))

    def failing_source(self, failure: DeviceError) -> DeviceFactsSource:
        """Return a facts source whose probe fails below httpx.

        Parameters
        ----------
        failure
            The failure the contract seeded.

        Returns
        -------
        DeviceFactsSource
            The adapter.
        """
        return UsbFacts(api=web_api(dead_tablet(failure)))

    def test_every_field_is_structurally_unsupported(self, source: DeviceFactsSource) -> None:
        """Every field is structurally unsupported."""
        # The route table is closed at six families and none reports firmware, model,
        # serial, memory or storage -- so this transport names all of them rather than
        # reporting an unnamed None, which would read as "asked and got nothing back".
        assert source.read_facts().unsupported_names == frozenset({"firmware", "model", "serial"})
        assert source.read_resources().unsupported_names == frozenset(
            {
                "total_memory_bytes",
                "available_memory_bytes",
                "total_storage_bytes",
                "available_storage_bytes",
            }
        )

    def test_every_declaration_says_who_could_answer_it(self, source: DeviceFactsSource) -> None:
        """Every declaration says who could answer it."""
        # The route table is the constraint, so the answer is the same for six of the seven
        # fields -- SSH, which has a shell and reads all six from files. `serial` is the
        # exception and the empty tuple is the point of it: no transport answers that one, so a
        # report has nothing to advise rather than a transport to name.
        facts = source.read_facts()
        resources = source.read_resources()
        declared = (*facts.alternatives, *resources.alternatives)
        claims = {entry.name: entry.supported_by for entry in declared}
        assert claims.keys() == facts.unsupported_names | resources.unsupported_names
        assert claims["serial"] == ()
        assert all(
            claim == (TransportKind.SSH,) for name, claim in claims.items() if name != "serial"
        )


class TestUsbUploader(DocumentUploaderContract):
    """The uploader contract over ``POST /upload``, the route xochitl imports through."""

    def uploader_for(self, *, honours_parent: bool) -> BoundUploader | None:
        """Return the uploader, or ``None`` when the behaviour asked for is unreachable.

        Parameters
        ----------
        honours_parent
            Whether the uploader must honour a destination folder.

        Returns
        -------
        BoundUploader | None
            The adapter, or ``None`` when a destination is required: no folder parameter
            exists in the route, in the SPA's own call to it, or in the response, and the
            created entry carries ``Parent == ""``. This is the implementation the contract's
            "cannot honour a destination" branch was written for -- it used to be reachable
            only through the double.
        """
        if honours_parent:
            return None
        placed: list[str] = []
        uploader = UsbUploader(api=web_api(_upload_tablet(placed)))
        return BoundUploader(uploader=uploader, placed=lambda: len(placed))

    def test_the_multipart_body_is_one_part_named_file(self) -> None:
        """The multipart body is one part named file."""
        # Measured: one part named `document` carrying the same PDF is answered
        # 400 {"error": "No file sent"}, so the field name is the contract and not a
        # convention. The filename is `request.name` verbatim, because for a PDF that string
        # *becomes* the visible name -- extension included, or missing if the caller left it
        # out.
        sent: list[httpx.Request] = []
        uploader = UsbUploader(api=web_api(_upload_tablet([], recorder=sent)))

        uploader.upload(an_upload(name="Design review.pdf"))

        # `sent[0]` is the destination pre-flight -- `GET /documents/`, sent immediately before
        # every write so the created entry lands at the root whatever listed last. The write is
        # the second request, and picking it by verb keeps this assertion about the body.
        write = next(request for request in sent if request.method == "POST")
        body = write.content.decode("latin-1")
        assert [request.method for request in sent] == ["GET", "POST"]
        assert write.url.raw_path.decode() == UPLOAD_ROUTE
        assert f'name="{UPLOAD_FIELD}"' in body
        assert 'filename="Design review.pdf"' in body
        assert UPLOAD_MEDIA_TYPES[UploadMedia.PDF] in body

    def test_the_receipt_reports_no_identifier_and_no_forced_refresh(self) -> None:
        """The receipt reports no identifier and no forced refresh."""
        # The 201 body is {"status": "Upload successful"} and carries no id, and
        # GET /documents/ went 10 -> 11 root entries with no restart and no stop of xochitl.
        # Both halves are measurements, not readings of a status code.
        bound = self._require(honours_parent=False)

        receipt = bound.uploader.upload(an_upload())

        assert receipt.doc_uuid is None
        assert receipt.library_refresh is LibraryRefresh.ALREADY_VISIBLE

    def test_a_refused_destination_sends_nothing_at_all(self) -> None:
        """A refused destination sends nothing at all."""
        # Stronger than the contract's `placed() == 0`, which only proves no document was
        # created: this proves no *request* left the process, which is what "raised before
        # anything is written" has to mean for a route that cannot be undone.
        sent: list[httpx.Request] = []
        uploader = UsbUploader(api=web_api(_upload_tablet([], recorder=sent)))

        with pytest.raises(DeviceOperationUnsupported):
            uploader.upload(an_upload(parent_uuid=FOLDER_UUID))

        assert sent == []


# ───────────────────────────── the SSH bindings ─────────────────────────────


class TestSshCatalog(DeviceCatalogContract):
    """The catalog contract over one ``ls -A`` of a flat xochitl store."""

    @pytest.fixture
    def bound(self) -> BoundCatalog:
        """Return a catalog over the reference library, and its round-trip counter.

        Returns
        -------
        BoundCatalog
            The adapter, which ``ty`` checks against the Protocol here.
        """
        shell = ssh_store()
        catalog = SshCatalog(shell=shell, root=ROOT)
        return BoundCatalog(catalog=catalog, fetches=lambda: len(shell.log))

    def catalog_with_skip(self, reason: SkipReason) -> SkipCase:
        """Cause one skip of *reason* with a store entry the decoder refuses.

        Parameters
        ----------
        reason
            Which diagnosis to cause.

        Returns
        -------
        SkipCase
            The adapter and the skipped identifier.
        """
        sidecar = f"{SKIPPED_UUID}{METADATA_SUFFIX}"
        if reason is SkipReason.MALFORMED_METADATA:
            extra = {sidecar: b"this is not json"}
            refuse: tuple[str, ...] = ()
        elif reason is SkipReason.VALIDATION_FAILED:
            # A .metadata with no .content sibling. The metadata decoded; what it describes
            # has no file type this domain represents, and defaulting the kind would report
            # a pdf as a notebook -- an export with no background and no defect recorded.
            extra = {sidecar: store_metadata(name="Unknown kind")}
            refuse = ()
        else:
            extra = {sidecar: store_metadata(name="Refused")}
            refuse = (sidecar,)
        shell = ssh_store(extra_files=extra, refuse_reads=refuse)
        return SkipCase(catalog=SshCatalog(shell=shell, root=ROOT), doc_uuid=SKIPPED_UUID)

    def failing_catalog(self, failure: DeviceError) -> DeviceCatalog:
        """Return a catalog whose shell fails on every operation.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        DeviceCatalog
            The adapter.
        """
        return SshCatalog(shell=FakeRemoteShell(fail_with=failure), root=ROOT)

    def test_one_listing_decides_presence_so_a_folder_costs_no_failed_read(
        self,
        bound: BoundCatalog,
    ) -> None:
        """One listing decides presence so a folder costs no failed read."""
        bound.catalog.list_documents()
        assert bound.fetches() == 6


class TestSshBundleSource(RawBundleSourceContract):
    """The bundle contract over one SFTP read per artifact the listing named."""

    @pytest.fixture
    def source(self) -> RawBundleSource:
        """Return a bundle source over the reference library.

        Returns
        -------
        RawBundleSource
            The adapter, which ``ty`` checks against the Protocol here.
        """
        shell = ssh_store()
        return SshBundleSource(
            shell=shell,
            root=ROOT,
            catalog=SshCatalog(shell=shell, root=ROOT),
        )

    def truncated_source(self) -> RawBundleSource:
        """Return a source whose transfer ends early after the listing has succeeded.

        Returns
        -------
        RawBundleSource
            The adapter. The catalog is warmed first, so what fails is the *transfer* and
            not the enumeration -- the real shell raises this class from a transfer it
            could not complete, never from ``ls``.
        """
        shell = ssh_store()
        catalog = SshCatalog(shell=shell, root=ROOT)
        catalog.list_documents()
        shell.fail_with = DeviceTransferInterrupted(
            transport=TransportKind.SSH,
            subject=NOTEBOOK_UUID,
            bytes_transferred=7,
            bytes_expected=len(INK),
        )
        return SshBundleSource(shell=shell, root=ROOT, catalog=catalog)

    def failing_source(self, failure: DeviceError) -> RawBundleSource:
        """Return a source whose shell fails on every operation.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        RawBundleSource
            The adapter.
        """
        shell = FakeRemoteShell(fail_with=failure)
        return SshBundleSource(
            shell=shell,
            root=ROOT,
            catalog=SshCatalog(shell=shell, root=ROOT),
        )


class TestSshFacts(DeviceFactsSourceContract):
    """The facts contract over the four BusyBox-safe commands this package may send."""

    @pytest.fixture
    def source(self) -> DeviceFactsSource:
        """Return a facts source over an attached tablet.

        Returns
        -------
        DeviceFactsSource
            The adapter, which ``ty`` checks against the Protocol here.
        """
        return SshFacts(shell=ssh_store(outputs=HEALTHY_FACTS))

    def failing_source(self, failure: DeviceError) -> DeviceFactsSource:
        """Return a facts source whose shell fails on every operation.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        DeviceFactsSource
            The adapter.
        """
        return SshFacts(shell=FakeRemoteShell(fail_with=failure))

    def test_the_serial_is_named_unsupported_and_the_other_two_are_answered(
        self,
        source: DeviceFactsSource,
    ) -> None:
        """The serial is named unsupported and the other two are answered."""
        # The SoC unique id next to it is a *different fact* from the RM02A... serial the
        # tablet UI shows, so reporting it under this field's name would be one fact wearing
        # another's. "Structurally cannot ask" is the honest encoding, and it stays true on
        # the next run.
        facts = source.read_facts()
        assert facts.firmware == FIRMWARE
        assert facts.model == MODEL
        assert facts.unsupported == frozenset({NO_SERIAL_SOURCE})
        # And the annotation is the empty tuple rather than `USB_WEB_API`: the sources are a
        # file this workspace may not name and a redacted journal, neither of which the other
        # transport reaches either, so there is nowhere to send a user.
        assert facts.alternatives == (NO_SERIAL_SOURCE,)
        assert facts.alternatives[0].supported_by == ()


class TestSshUploader(DocumentUploaderContract):
    """The uploader contract over the transport that composes the sidecars itself."""

    def unplaceable_media(self) -> frozenset[UploadMedia]:
        """Name the one media this transport refuses.

        Returns
        -------
        frozenset[UploadMedia]
            The archive. It is a container of a whole document rather than an underlay, so
            placing one here would mean unpacking it and writing its members -- a different
            operation, and the reason the refusal names ``USB_WEB_API`` as the transport that
            can.
        """
        return frozenset({UNPLACEABLE_MEDIA})

    def uploader_for(self, *, honours_parent: bool) -> BoundUploader | None:
        """Return the uploader, or ``None`` when the behaviour asked for is unreachable.

        Parameters
        ----------
        honours_parent
            Whether the uploader must honour a destination folder.

        Returns
        -------
        BoundUploader | None
            The adapter, or ``None``: this adapter writes ``parent`` into the ``.metadata``
            unconditionally, so it *always* honours a destination and cannot be made to
            refuse one. That branch of the contract is covered by
            :class:`~rmspec.device.usb.UsbUploader`, whose route has no destination parameter
            at all, and by the double.
        """
        if not honours_parent:
            return None
        shell = FakeRemoteShell(outputs=UPLOAD_COMMANDS)
        uploader = SshUploader(
            shell=shell,
            root=ROOT,
            now_ms=lambda: NOW_MS,
            new_uuid=lambda: MINTED_UUID,
        )
        return BoundUploader(
            uploader=uploader,
            placed=lambda: sum(1 for path, _ in shell.writes if path.endswith(METADATA_SUFFIX)),
        )

    def test_the_metadata_sidecar_is_written_last_and_the_refresh_after_it(self) -> None:
        """The metadata sidecar is written last and the refresh after it."""
        # `.metadata` is what makes an identifier a document in the store, so a failure
        # before it leaves orphans no listing reports as a document. The refresh is five
        # commands rather than one -- see `SshUploader._refresh` -- so the claim here is that
        # the commit is the last *write* and that everything after it is one of those five.
        shell = FakeRemoteShell(outputs=UPLOAD_COMMANDS)
        uploader = SshUploader(
            shell=shell,
            root=ROOT,
            now_ms=lambda: NOW_MS,
            new_uuid=lambda: MINTED_UUID,
        )
        uploader.upload(an_upload())
        commit = ROOT.child(MINTED_UUID).with_suffix(METADATA_SUFFIX).value
        assert shell.writes[-1][0] == commit, "the commit point is the last write"
        after = shell.log[shell.log.index(f"write {commit}") + 1 :]
        assert all(entry.startswith("run ") for entry in after), "nothing is written after it"
        assert f"run {RESTART_COMMAND}" in after


class TestSshSearchIndexSource(SearchIndexSourceContract):
    """The search-index contract over one SFTP read of one file under the xochitl root."""

    @pytest.fixture
    def source(self) -> SearchIndexSource:
        """Return a source over a device holding the synthetic index image.

        Returns
        -------
        SearchIndexSource
            The adapter, which ``ty`` checks against the Protocol here. Constructed
            positionally with no ``root``, so the default is on the path being asserted.
        """
        return SshSearchIndexSource(FakeRemoteShell(files={INDEX_PATH.value: SEARCH_INDEX_IMAGE}))

    def absent_source(self) -> SearchIndexSource:
        """Return a source over a store holding no index file at all.

        Returns
        -------
        SearchIndexSource
            The adapter. The shell reports an absent path as
            ``PathUnreadableError``, exactly as the real one does for the ``errno`` paramiko
            attaches to that SFTP status code, so the ``None`` is *converted* here rather
            than injected.
        """
        return SshSearchIndexSource(FakeRemoteShell())

    def failing_source(self, failure: DeviceError) -> SearchIndexSource:
        """Return a source whose shell fails on every operation.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        SearchIndexSource
            The adapter.
        """
        return SshSearchIndexSource(FakeRemoteShell(fail_with=failure))

    def test_one_read_of_one_path_and_nothing_else(self) -> None:
        """One read of one path and nothing else."""
        # No `ls` deciding presence first, unlike every other adapter in this module: absence
        # *is* an answer this port can give, so a listing would be a round trip that changes
        # nothing. Asserted on the ordered log, because the image is 503,808 bytes on the
        # measured device and a second read per page is the cost REQUEST scope exists to
        # avoid.
        shell = FakeRemoteShell(files={INDEX_PATH.value: SEARCH_INDEX_IMAGE})

        SshSearchIndexSource(shell).read_index()

        assert shell.log == [f"read {INDEX_PATH.value}"]


# ───────────────────────────── the in-memory bindings ─────────────────────────────


def _in_memory_catalog(
    *,
    skipped: Sequence[SkippedEntry] = (),
    fail_with: DeviceError | None = None,
) -> InMemoryDeviceCatalog:
    """Build the reference library as a double.

    Parameters
    ----------
    skipped
        Entries the listing reports as unrepresentable.
    fail_with
        A whole-transport failure, or ``None``.

    Returns
    -------
    InMemoryDeviceCatalog
        The double.
    """
    return InMemoryDeviceCatalog(
        documents=(IN_MEMORY_NOTEBOOK, IN_MEMORY_PDF),
        folders=(IN_MEMORY_FOLDER,),
        skipped=skipped,
        fail_with=fail_with,
    )


def _in_memory_pages() -> dict[str, tuple[DevicePageSource, ...]]:
    """Build the reference library's pages as domain values.

    Returns
    -------
    dict[str, tuple[DevicePageSource, ...]]
        Document identifier mapped to its pages, in recorded order.
    """
    return {
        NOTEBOOK_UUID: (
            DevicePageSource(page_id=PAGE_ONE, scene=INK, template_name=TEMPLATE),
            DevicePageSource(page_id=PAGE_TWO, scene=None),
        ),
        PDF_UUID: (DevicePageSource(page_id=PAGE_THREE, scene=INK),),
    }


class TestInMemoryDeviceCatalog(DeviceCatalogContract):
    """The catalog contract over three tuples."""

    @pytest.fixture
    def bound(self) -> BoundCatalog:
        """Return the double, and its build counter.

        Returns
        -------
        BoundCatalog
            The double, which ``ty`` checks against the Protocol here.
        """
        catalog = _in_memory_catalog()
        return BoundCatalog(catalog=catalog, fetches=lambda: catalog.builds)

    def catalog_with_skip(self, reason: SkipReason) -> SkipCase:
        """Seed one skip carrying *reason*.

        Parameters
        ----------
        reason
            Which diagnosis the listing reports.

        Returns
        -------
        SkipCase
            The double and the skipped identifier.
        """
        entry = SkippedEntry(
            uuid=SKIPPED_UUID,
            reason=reason,
            detail=f"seeded as {reason.value}",
        )
        return SkipCase(catalog=_in_memory_catalog(skipped=(entry,)), doc_uuid=SKIPPED_UUID)

    def failing_catalog(self, failure: DeviceError) -> DeviceCatalog:
        """Return a double whose transport fails.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        DeviceCatalog
            The double.
        """
        return _in_memory_catalog(fail_with=failure)


class TestInMemoryRawBundleSource(RawBundleSourceContract):
    """The bundle contract over two mappings and a catalog."""

    @pytest.fixture
    def source(self) -> RawBundleSource:
        """Return the double.

        Returns
        -------
        RawBundleSource
            The double, which ``ty`` checks against the Protocol here.
        """
        return InMemoryRawBundleSource(
            catalog=_in_memory_catalog(),
            pages=_in_memory_pages(),
            bases={PDF_UUID: UNDERLAY},
        )

    def truncated_source(self) -> RawBundleSource:
        """Return a double whose transfer ends early.

        Returns
        -------
        RawBundleSource
            The double.
        """
        return InMemoryRawBundleSource(
            catalog=_in_memory_catalog(),
            pages=_in_memory_pages(),
            bases={PDF_UUID: UNDERLAY},
            truncate_at=7,
        )

    def failing_source(self, failure: DeviceError) -> RawBundleSource:
        """Return a double whose transport fails.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        RawBundleSource
            The double.
        """
        return InMemoryRawBundleSource(
            catalog=_in_memory_catalog(),
            pages=_in_memory_pages(),
            bases={PDF_UUID: UNDERLAY},
            fail_with=failure,
        )


class TestInMemoryDeviceFactsSource(DeviceFactsSourceContract):
    """The facts contract over two seeded readings."""

    @pytest.fixture
    def source(self) -> DeviceFactsSource:
        """Return the double.

        Returns
        -------
        DeviceFactsSource
            The double, which ``ty`` checks against the Protocol here.
        """
        return InMemoryDeviceFactsSource(facts=IN_MEMORY_FACTS, resources=IN_MEMORY_RESOURCES)

    def failing_source(self, failure: DeviceError) -> DeviceFactsSource:
        """Return a double whose transport fails.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        DeviceFactsSource
            The double.
        """
        return InMemoryDeviceFactsSource(fail_with=failure)


class TestInMemoryDocumentUploader(DocumentUploaderContract):
    """The uploader contract over a list, for both destination behaviours."""

    def uploader_for(self, *, honours_parent: bool) -> BoundUploader | None:
        """Return an uploader with that destination behaviour.

        Parameters
        ----------
        honours_parent
            Whether the uploader honours a destination folder. Both are producible here,
            which is the point: the ``False`` branch is unreachable in every shipped
            adapter, so without this the port's "degrading a request is forbidden" rule
            would be checked by nothing.

        Returns
        -------
        BoundUploader | None
            The double. Never ``None``.
        """
        uploader = InMemoryDocumentUploader(
            doc_uuid=MINTED_UUID,
            honours_parent=honours_parent,
        )
        return BoundUploader(uploader=uploader, placed=lambda: len(uploader.uploaded))


class TestFakeSearchIndexSource(SearchIndexSourceContract):
    """The search-index contract over one seeded value."""

    @pytest.fixture
    def source(self) -> SearchIndexSource:
        """Return the double, holding the synthetic index image.

        Returns
        -------
        SearchIndexSource
            The double, which ``ty`` checks against the Protocol here.
        """
        return FakeSearchIndexSource(image=SEARCH_INDEX_IMAGE)

    def absent_source(self) -> SearchIndexSource:
        """Return the double as a device that has never built an index.

        Returns
        -------
        SearchIndexSource
            The double, seeded with nothing -- which is its default, because that state is a
            legal device and not an empty one.
        """
        return FakeSearchIndexSource()

    def failing_source(self, failure: DeviceError) -> SearchIndexSource:
        """Return a double whose transport fails.

        Parameters
        ----------
        failure
            The failure the contract seeded, raised verbatim.

        Returns
        -------
        SearchIndexSource
            The double.
        """
        return FakeSearchIndexSource(fail_with=failure)


# ─────────────────── the write surface, and one deliberate absence ───────────────────


def _download_route(doc_uuid: str, /) -> str:
    """Spell the archive route for one document.

    Parameters
    ----------
    doc_uuid
        The document to download.

    Returns
    -------
    str
        The raw request path the adapter will send.
    """
    return RMDOC_ROUTE.replace("{id}", doc_uuid)


def _looks_like_an_uploader(candidate: object, /) -> bool:
    """Report whether a name from the package satisfies ``DocumentUploader`` structurally.

    No port is ``runtime_checkable`` and nothing in this workspace calls ``isinstance`` on
    one, so the check is the method the Protocol declares.

    Parameters
    ----------
    candidate
        A name exported by :mod:`rmspec.device`.

    Returns
    -------
    bool
        ``True`` when it has a callable ``upload``.
    """
    return callable(getattr(candidate, "upload", None))


def test_the_package_binds_exactly_two_document_uploaders() -> None:
    """The package binds exactly two document uploaders."""
    # This assertion used to read `== ["SshUploader"]`, on the premise that POST /upload had
    # never been probed in any form and that a guessed multipart body was not a trade this
    # package would make. Retired by measurement on 2026-08-29: the route was read out of the
    # tablet's own SPA bundle and probed four ways, and there is nothing left to guess.
    # `ports/device.py` still expresses capability asymmetry as which bindings exist, so the
    # replacement pins the pair and the two tests below pin what each of them refuses.
    uploaders = [
        name
        for name in rmspec.device.__all__
        if _looks_like_an_uploader(getattr(rmspec.device, name))
    ]
    assert uploaders == ["SshUploader", "UsbUploader"]


def test_exactly_one_usb_name_can_write_to_the_device() -> None:
    """Exactly one usb name can write to the device."""
    # The module used to export nothing that could write, and this asserted that. One name can
    # now, and it is still exactly one: the transport's `post_file` is not an uploader, so a
    # reader looking for "what in here creates a document" gets a single answer.
    assert usb.__all__ == [
        "UPLOAD_CREATED",
        "UPLOAD_FIELD",
        "UPLOAD_MEDIA_TYPES",
        "UPLOAD_OPERATION",
        "UPLOAD_ROUTE",
        "UPLOAD_TIMEOUT_SECONDS",
        "USB_TIMEOUT_SECONDS",
        "UsbBundleSource",
        "UsbCatalog",
        "UsbFacts",
        "UsbUploader",
        "UsbWebApi",
    ]
    writers = [name for name in usb.__all__ if _looks_like_an_uploader(getattr(usb, name))]
    assert writers == ["UsbUploader"]


def test_the_two_uploaders_refuse_different_halves_of_the_same_port() -> None:
    """The two uploaders refuse different halves of the same port."""
    # The asymmetry is per-request data, so it cannot be expressed by which bindings exist --
    # only by what each raises. Asserted against both real adapters in one place, because the
    # pair is the design and a reader who sees only one of them learns the wrong lesson.
    usb_uploader = UsbUploader(api=web_api(_upload_tablet([])))
    ssh_uploader = SshUploader(
        shell=FakeRemoteShell(outputs=UPLOAD_COMMANDS),
        root=ROOT,
        now_ms=lambda: NOW_MS,
        new_uuid=lambda: MINTED_UUID,
    )

    with pytest.raises(DeviceOperationUnsupported) as refused_destination:
        usb_uploader.upload(an_upload(parent_uuid=FOLDER_UUID))
    with pytest.raises(DeviceOperationUnsupported) as refused_archive:
        ssh_uploader.upload(an_upload(media=UNPLACEABLE_MEDIA))

    assert refused_destination.value.supported_by == (TransportKind.SSH,)
    assert refused_archive.value.supported_by == (TransportKind.USB_WEB_API,)
    # And each places what the other refuses, so neither refusal is a general inability.
    assert ssh_uploader.upload(an_upload(parent_uuid=FOLDER_UUID)).doc_uuid == MINTED_UUID
    assert usb_uploader.upload(an_upload(media=UNPLACEABLE_MEDIA)).media is UNPLACEABLE_MEDIA


def test_the_two_uploaders_report_the_visibility_their_wires_actually_produce() -> None:
    """The two uploaders report the visibility their wires actually produce."""
    # Measured, not assumed. USB: GET /documents/ went 10 -> 11 root entries with no restart
    # and no stop of xochitl, because xochitl performs the import itself. SSH: nothing in the
    # store is indexed until the tablet UI process is restarted, which this adapter does.
    usb_uploader = UsbUploader(api=web_api(_upload_tablet([])))
    ssh_uploader = SshUploader(
        shell=FakeRemoteShell(outputs=UPLOAD_COMMANDS),
        root=ROOT,
        now_ms=lambda: NOW_MS,
        new_uuid=lambda: MINTED_UUID,
    )

    assert usb_uploader.upload(an_upload()).library_refresh is LibraryRefresh.ALREADY_VISIBLE
    assert ssh_uploader.upload(an_upload()).library_refresh is LibraryRefresh.VISIBILITY_FORCED


def test_the_refusal_vocabulary_is_one_spelling_across_the_adapters_and_the_doubles() -> None:
    """The refusal vocabulary is one spelling across the adapters and the doubles."""
    # `rmspec.device.testing.doubles` cannot import the adapter module -- that would pull httpx
    # into every fake's import graph -- so the operation name is spelled twice. This is what
    # keeps the two copies from drifting into a shell that matches on one of them.
    assert device_doubles.UPLOAD_OPERATION == usb.UPLOAD_OPERATION
    # And the SSH media refusal is the same word plus what was refused, so a shell reporting
    # either failure prints one vocabulary rather than two.
    assert f"{usb.UPLOAD_OPERATION} {UNPLACEABLE_MEDIA.value}" == UNPLACEABLE_OPERATION


def _looks_like_a_search_index_source(candidate: object, /) -> bool:
    """Report whether a name from the package satisfies ``SearchIndexSource`` structurally.

    Parameters
    ----------
    candidate
        A name exported by :mod:`rmspec.device`.

    Returns
    -------
    bool
        ``True`` when it has a callable ``read_index``.
    """
    return callable(getattr(candidate, "read_index", None))


def test_the_package_binds_exactly_one_search_index_source() -> None:
    """The package binds exactly one search index source."""
    # The mirror of the uploader assertion above, and the absence has a stronger cause: not an
    # unprobed route but *no route*. That firmware's HTTP route table is closed at six families
    # and none of them serves a file from the xochitl tree, so a USB binding here would be a
    # method with nothing to call. Asserted rather than left to a docstring, so a later reader
    # cannot "fix" the omission without failing a test.
    sources = [
        name
        for name in rmspec.device.__all__
        if _looks_like_a_search_index_source(getattr(rmspec.device, name))
    ]
    assert sources == ["SshSearchIndexSource"]


def test_no_usb_name_can_serve_the_search_index() -> None:
    """No usb name can serve the search index."""
    for name in usb.__all__:
        assert not _looks_like_a_search_index_source(getattr(usb, name))


def test_the_usb_transport_has_two_read_verbs_and_exactly_one_write_verb() -> None:
    """The usb transport has two read verbs and exactly one write verb."""
    # This asserted `== {"get", "head"}` under the heading "exactly two read-only verbs", whose
    # premise was that the write route was unprobed. Retired by measurement; the replacement is
    # stronger, because it pins the *count* of write verbs rather than their absence -- a
    # second one would still fail here. `over_usb` and `close` joined the set when the
    # composition root needed a client it is not allowed to build: they carry the client's
    # lifetime, not a request, and they are the same pair `ParamikoShell` spells as
    # `connect`/`close`.
    verbs = {name for name in vars(UsbWebApi) if not name.startswith("_")}
    assert verbs == {"close", "get", "head", "over_usb", "post_file"}


def test_the_reference_instant_survives_both_wire_spellings() -> None:
    """The reference instant survives both wire spellings."""
    # The two bindings decode the same instant from two different encodings. If either
    # derivation above were wrong, every last_modified assertion would still pass while the
    # two transports reported different times for the same document.
    from_usb = datetime.datetime.fromisoformat(MODIFIED_CLIENT)
    from_store = datetime.datetime.fromtimestamp(int(MODIFIED_EPOCH_MS) / 1000, tz=datetime.UTC)
    assert from_usb == MODIFIED
    assert from_store == MODIFIED
