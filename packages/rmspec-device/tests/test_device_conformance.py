"""The port contracts, bound to every implementation this package ships.

Eleven bindings: the three USB adapters over ``httpx.MockTransport``, the four SSH adapters
over the shipped in-memory shell, and the four doubles. The assertions are literally the same
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

The deliberate absence is asserted too. ``DocumentUploader`` has exactly one binding, and the
last section of this file fails if a later reader "fixes" that.
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
    UNDERLAY,
    BoundCatalog,
    BoundUploader,
    DeviceCatalogContract,
    DeviceFactsSourceContract,
    DocumentUploaderContract,
    RawBundleSourceContract,
    SkipCase,
    an_upload,
)

import rmspec.device
from rmspec.device import (
    SshBundleSource,
    SshCatalog,
    SshFacts,
    SshUploader,
    UsbBundleSource,
    UsbCatalog,
    UsbFacts,
    UsbWebApi,
    usb,
)
from rmspec.device._archive import RMDOC_ROUTE
from rmspec.device._wire import COLLECTION_TYPE, DOCUMENT_TYPE, LISTING_ROUTE
from rmspec.device.addresses import (
    CONTENT_SUFFIX,
    METADATA_SUFFIX,
    OS_RELEASE,
    PROC_MEMINFO,
    SCENE_SUFFIX,
    SOC_MACHINE,
    Endpoint,
    RemoteCommand,
    RemotePath,
)
from rmspec.device.ssh import (
    FIRMWARE_TEMPLATE,
    MAKE_DIR_TEMPLATE,
    MEMINFO_TEMPLATE,
    MODEL_TEMPLATE,
    REFRESH_TEMPLATE,
    SERIAL_FIELD,
    STORAGE_TEMPLATE,
)
from rmspec.device.testing import (
    FakeRemoteShell,
    InMemoryDeviceCatalog,
    InMemoryDeviceFactsSource,
    InMemoryDocumentUploader,
    InMemoryRawBundleSource,
)
from rmspec.domain.errors import DeviceTransferInterrupted, TransportKind
from rmspec.domain.ports.device import (
    DeviceDocument,
    DeviceFacts,
    DeviceFileType,
    DeviceFolder,
    DevicePageSource,
    DeviceResources,
    SkippedEntry,
    SkipReason,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rmspec.domain.errors import DeviceError
    from rmspec.domain.ports.device import DeviceCatalog, DeviceFactsSource, RawBundleSource

ROOT = RemotePath.root()
"""The xochitl root both SSH bindings build their synthetic store under."""

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
two fields; this one exercises the *named* cause, and ``IN_MEMORY_RESOURCES`` the other."""

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

UPLOAD_COMMANDS = {
    RemoteCommand.of(MAKE_DIR_TEMPLATE, ROOT.child(MINTED_UUID)).text: "",
    RemoteCommand.of(REFRESH_TEMPLATE).text: "",
}
"""The two commands ``SshUploader`` sends around its three writes."""


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
        assert source.read_facts().unsupported == frozenset({"firmware", "model", "serial"})
        assert source.read_resources().unsupported == frozenset(
            {
                "total_memory_bytes",
                "available_memory_bytes",
                "total_storage_bytes",
                "available_storage_bytes",
            }
        )


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
        assert facts.unsupported == frozenset({SERIAL_FIELD})


class TestSshUploader(DocumentUploaderContract):
    """The uploader contract over the one transport that can write to the tablet."""

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
            refuse one. That branch of the contract is covered by the double, which exists
            partly for this.
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
        # before it leaves orphans no listing reports as a document.
        shell = FakeRemoteShell(outputs=UPLOAD_COMMANDS)
        uploader = SshUploader(
            shell=shell,
            root=ROOT,
            now_ms=lambda: NOW_MS,
            new_uuid=lambda: MINTED_UUID,
        )
        uploader.upload(an_upload())
        commit = ROOT.child(MINTED_UUID).with_suffix(METADATA_SUFFIX).value
        assert shell.log[-2:] == [f"write {commit}", f"run {REFRESH_TEMPLATE}"]


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


# ───────────────────────────── the deliberate absence ─────────────────────────────


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


def test_the_package_binds_exactly_one_document_uploader() -> None:
    """The package binds exactly one document uploader."""
    # POST /upload has never been probed in any form: the firmware ignores the HTTP request
    # method, so a GET to that path could not have been proven non-mutating, and its
    # multipart field name, accepted content types and response body are all unmeasured.
    # The absence *is* the design -- ports/device.py expresses capability asymmetry as which
    # ports exist -- so it is asserted rather than left to a docstring somebody deletes.
    uploaders = [
        name
        for name in rmspec.device.__all__
        if _looks_like_an_uploader(getattr(rmspec.device, name))
    ]
    assert uploaders == ["SshUploader"]


def test_no_usb_name_can_write_to_the_device() -> None:
    """No usb name can write to the device."""
    assert usb.__all__ == ["UsbBundleSource", "UsbCatalog", "UsbFacts", "UsbWebApi"]
    for name in usb.__all__:
        assert not _looks_like_an_uploader(getattr(usb, name))


def test_the_usb_transport_has_exactly_two_read_only_verbs() -> None:
    """The usb transport has exactly two read only verbs."""
    verbs = {name for name in vars(UsbWebApi) if not name.startswith("_")}
    assert verbs == {"get", "head"}


def test_the_reference_instant_survives_both_wire_spellings() -> None:
    """The reference instant survives both wire spellings."""
    # The two bindings decode the same instant from two different encodings. If either
    # derivation above were wrong, every last_modified assertion would still pass while the
    # two transports reported different times for the same document.
    from_usb = datetime.datetime.fromisoformat(MODIFIED_CLIENT)
    from_store = datetime.datetime.fromtimestamp(int(MODIFIED_EPOCH_MS) / 1000, tz=datetime.UTC)
    assert from_usb == MODIFIED
    assert from_store == MODIFIED
