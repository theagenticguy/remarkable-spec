"""The ``.rmdoc`` member router.

Archives are **built here**, never captured: a real archive carries the user's handwriting
and their document title. :func:`rmdoc_archive` reproduces the measured member set
byte-for-role, and its docstring records the evidence it stands on.

The suite is organised around the four things this module promises: the member set it
accepts, the states a page can be in, the five ways it refuses an archive, and the
immutability of what it returns.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import MutableMapping

import pytest

from rmspec.device._archive import RMDOC_ROUTE, ArchiveMembers, read_rmdoc
from rmspec.domain.errors import DeviceProtocolError, TransportKind

DOC = "dddddddd-1111-2222-3333-444444444444"
"""One document identifier, in the canonical shape the device writes."""

OTHER_DOC = "eeeeeeee-9999-8888-7777-666666666666"
"""A second identifier, for proving a foreign member is refused rather than ignored."""

METADATA = b'{"type": "DocumentType", "visibleName": "synthetic"}'
CONTENT = b'{"formatVersion": 2, "cPages": {"pages": [{"id": "page-one"}]}}'


def rmdoc_archive(
    *,
    doc_uuid: str = DOC,
    scenes: dict[str, bytes] | None = None,
    underlay: tuple[str, bytes] | None = None,
    metadata: bytes | None = METADATA,
    content: bytes | None = CONTENT,
    extra: dict[str, bytes] | None = None,
    directories: tuple[str, ...] = (),
) -> bytes:
    """Build the measured ``.rmdoc`` member set as an in-memory zip.

    Reproduces what firmware 3.27.3.0 returns from ``GET /download/{id}/rmdoc``:
    ``<docUUID>.metadata`` (always), ``<docUUID>.content`` (always), one
    ``<docUUID>/<pageUUID>.rm`` per page **including zero-byte placeholders**, and
    ``<docUUID>.<fileType>`` for a non-notebook document. Never ``.local``, ``.pagedata``,
    ``.thumbnails`` or ``.failure``, and never nesting below ``<docUUID>/``.

    Evidence, from a 1-page pdf document re-measured 2026-08-29: archive 8557 bytes, magic
    ``504b0304``, members ``.metadata`` 381 / ``.content`` 2329 / ``.pdf`` 5481 / one
    ``.rm`` of 0 bytes and one of 6051, both at depth 1.

    Parameters
    ----------
    doc_uuid
        Identifier every member is named after.
    scenes
        Page identifier to scene bytes. ``b""`` reproduces a zero-byte placeholder.
    underlay
        ``(suffix, bytes)`` for the original pdf or epub, or ``None`` for a notebook.
    metadata, content
        Sidecar bytes, or ``None`` to omit the member entirely.
    extra
        Members written verbatim under the names given, for the refusal cases.
    directories
        Directory entries to write, which a real zip may or may not carry.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in directories:
            archive.writestr(name if name.endswith("/") else f"{name}/", b"")
        if metadata is not None:
            archive.writestr(f"{doc_uuid}.metadata", metadata)
        if content is not None:
            archive.writestr(f"{doc_uuid}.content", content)
        for page_id, payload in (scenes or {}).items():
            archive.writestr(f"{doc_uuid}/{page_id}.rm", payload)
        if underlay is not None:
            archive.writestr(f"{doc_uuid}.{underlay[0]}", underlay[1])
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# ─────────────────────────────── the member set ───────────────────────────────


def test_a_notebook_archive_routes_both_sidecars_and_every_scene():
    payload = rmdoc_archive(scenes={"page-one": b"v6-ink", "page-two": b"more-ink"})

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert members.doc_uuid == DOC
    assert members.metadata == METADATA
    assert members.content == CONTENT
    assert dict(members.scenes) == {"page-one": b"v6-ink", "page-two": b"more-ink"}
    assert members.underlay is None


def test_a_pdf_archive_carries_its_original_underlay():
    """The re-measurement refutes the earlier claim that the archive has no ``.pdf``."""
    payload = rmdoc_archive(scenes={"page-one": b"ink"}, underlay=("pdf", b"%PDF-1.7 body"))

    members = read_rmdoc(payload, doc_uuid=DOC, suffix="pdf")

    assert members.underlay == b"%PDF-1.7 body"


def test_an_epub_archive_is_read_the_same_way_although_the_case_is_unmeasured():
    payload = rmdoc_archive(underlay=("epub", b"PK epub body"))

    members = read_rmdoc(payload, doc_uuid=DOC, suffix="epub")

    assert members.underlay == b"PK epub body"


def test_an_archive_with_no_pages_at_all_has_an_empty_scene_mapping():
    members = read_rmdoc(rmdoc_archive(), doc_uuid=DOC, suffix=None)

    assert dict(members.scenes) == {}


def test_directory_entries_are_skipped_rather_than_name_checked():
    """A ``<docUUID>/`` entry ends in a separator, which the traversal check would refuse."""
    payload = rmdoc_archive(scenes={"page-one": b"ink"}, directories=(DOC,))

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert dict(members.scenes) == {"page-one": b"ink"}


# ─────────────────────────────── the page states ───────────────────────────────


def test_a_zero_byte_scene_member_is_preserved_and_not_collapsed_to_none():
    """86 of 194 real ``.rm`` files are zero bytes; the caller turns empty into ``None``."""
    payload = rmdoc_archive(scenes={"blank-page": b"", "inked-page": b"ink"})

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert members.scenes["blank-page"] == b""
    assert "blank-page" in members.scenes


def test_an_orphan_scene_unreachable_from_the_page_list_is_still_returned():
    """Measured: 16 ``.rm`` members for 10 pages. Dropping orphans is the caller's job."""
    payload = rmdoc_archive(scenes={"page-one": b"ink", "orphan-layer": b"stale-ink"})

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert set(members.scenes) == {"page-one", "orphan-layer"}


def test_a_scene_nested_below_the_document_directory_is_not_read_as_a_page():
    """No measured archive nests, and inventing a page id from an unknown path would lie."""
    payload = rmdoc_archive(
        scenes={"page-one": b"ink"},
        extra={f"{DOC}/layers/deep.rm": b"ink"},
    )

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert set(members.scenes) == {"page-one"}


def test_a_page_id_keeps_the_case_the_archive_spelled_it_with():
    page_id = "A1B2C3D4-1111-2222-3333-444444444444"
    payload = rmdoc_archive(scenes={page_id: b"ink"})

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert set(members.scenes) == {page_id}


# ─────────────────────────── the underlay contract ───────────────────────────


def test_a_notebook_ignores_an_underlay_shaped_member_rather_than_returning_it():
    """``DocumentSourceBundle`` refuses a notebook that carries a ``base``."""
    payload = rmdoc_archive(underlay=("pdf", b"%PDF-"))

    members = read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert members.underlay is None


def test_a_pdf_document_whose_underlay_member_is_absent_is_a_protocol_error():
    """The archive is the transport's own answer and it contradicts itself."""
    payload = rmdoc_archive(scenes={"page-one": b"ink"})

    with pytest.raises(DeviceProtocolError) as raised:
        read_rmdoc(payload, doc_uuid=DOC, suffix="pdf")

    assert raised.value.expected == f"a {DOC}.pdf member"
    assert f"{DOC}.metadata" in raised.value.got
    assert raised.value.route == RMDOC_ROUTE
    assert raised.value.transport is TransportKind.USB_WEB_API


# ─────────────────────────────── the refusals ───────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"%PDF-1.7 not a zip at all", id="the device-rendered pdf"),
        pytest.param(b"", id="an empty body"),
        pytest.param(b"PK\x03\x04truncated", id="a truncated zip header"),
    ],
)
def test_a_payload_that_is_not_a_zip_is_a_protocol_error(payload: bytes):
    with pytest.raises(DeviceProtocolError) as raised:
        read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert raised.value.expected == "an application/zip .rmdoc archive"
    assert "zipfile refuses" in raised.value.got
    assert raised.value.route == RMDOC_ROUTE


@pytest.mark.parametrize(
    ("payload", "missing"),
    [
        pytest.param(rmdoc_archive(metadata=None), ".metadata", id="no metadata sidecar"),
        pytest.param(rmdoc_archive(content=None), ".content", id="no content sidecar"),
    ],
)
def test_an_archive_missing_a_required_sidecar_is_a_protocol_error(payload: bytes, missing: str):
    with pytest.raises(DeviceProtocolError) as raised:
        read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert raised.value.expected == f"a {DOC}{missing} member"
    assert raised.value.got.startswith("members: ")


def test_an_archive_holding_no_members_at_all_names_the_empty_member_list():
    payload = rmdoc_archive(metadata=None, content=None)

    with pytest.raises(DeviceProtocolError) as raised:
        read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert raised.value.got == "members: "


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(f"{OTHER_DOC}.metadata", id="another document's metadata"),
        pytest.param(f"{OTHER_DOC}.content", id="another document's content"),
        pytest.param(f"{OTHER_DOC}/page-one.rm", id="another document's page"),
        pytest.param("README.txt", id="a member belonging to no document"),
        pytest.param("bare-name", id="a member with no suffix at all"),
    ],
)
def test_a_member_belonging_to_another_document_is_a_protocol_error(name: str):
    """Silently ignoring it would build one bundle out of two documents' files."""
    payload = rmdoc_archive(extra={name: b"foreign"})

    with pytest.raises(DeviceProtocolError) as raised:
        read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert raised.value.expected == f"every member to belong to document {DOC}"
    assert repr(name) in raised.value.got


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("../evil.metadata", id="a parent-directory escape"),
        pytest.param(f"../{DOC}.metadata", id="an escape that otherwise looks right"),
        pytest.param(f"{DOC}/../../etc/passwd", id="an escape mid-path"),
        pytest.param(f"/{DOC}.metadata", id="an absolute name"),
        pytest.param(f"./{DOC}.metadata", id="a same-directory prefix"),
        pytest.param(f"{DOC}\\page-one.rm", id="a backslash separator"),
    ],
)
def test_a_member_name_that_escapes_the_archive_root_is_a_protocol_error(name: str):
    """Nothing here writes to the filesystem; the check keeps that true for a caller that does."""
    payload = rmdoc_archive(extra={name: b"payload"})

    with pytest.raises(DeviceProtocolError) as raised:
        read_rmdoc(payload, doc_uuid=DOC, suffix=None)

    assert raised.value.expected == "member names relative to the archive root"
    assert repr(name) in raised.value.got


def test_names_are_all_checked_before_any_member_is_read():
    """One foreign member fails the archive rather than being read past."""
    payload = rmdoc_archive(scenes={"page-one": b"ink"}, extra={"../evil": b"payload"})

    with pytest.raises(DeviceProtocolError):
        read_rmdoc(payload, doc_uuid=DOC, suffix="pdf")


# ─────────────────────────── what the caller receives ───────────────────────────


def test_the_scene_mapping_cannot_be_mutated_through_the_frozen_model():
    """``frozen=True`` alone would leave the ``dict`` pydantic built inside the field open."""
    members = read_rmdoc(rmdoc_archive(scenes={"page-one": b"ink"}), doc_uuid=DOC, suffix=None)

    assert not isinstance(members.scenes, MutableMapping)
    assert not hasattr(members.scenes, "__setitem__")


def test_the_model_refuses_an_unknown_field():
    """``extra="forbid"``: a member role nobody routed cannot be smuggled in beside them."""
    with pytest.raises(ValueError, match="extra_forbidden"):
        ArchiveMembers.model_validate(
            {
                "doc_uuid": DOC,
                "metadata": METADATA,
                "content": CONTENT,
                "scenes": {},
                "underlay": None,
                "pagedata": b"Blank",
            }
        )
