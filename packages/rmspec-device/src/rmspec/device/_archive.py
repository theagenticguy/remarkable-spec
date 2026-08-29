"""The ``.rmdoc`` zip, with its members routed by the role each one plays.

``GET /download/{id}/rmdoc`` answers ``application/zip``. This module turns those bytes
into the four things a bundle needs -- the two sidecars, one scene payload per archived
page, and the original underlay -- and refuses an archive that is internally inconsistent.
It knows no route beyond the one it names in an error and opens no socket, so every branch
below is exercised by an archive built in a test.

The measured member set
-----------------------
Firmware 3.27.3.0, re-measured 2026-08-29. An archive carries
``<docUUID>.metadata`` (always), ``<docUUID>.content`` (always), one
``<docUUID>/<pageUUID>.rm`` per page in the content's page list **including zero-byte
placeholders**, and -- for a document whose ``fileType`` is not ``notebook`` --
``<docUUID>.<fileType>``, the original underlay. Never ``.local``, ``.pagedata``,
``.thumbnails`` or ``.failure``, and never nesting below ``<docUUID>/``.

This **refutes** an earlier spec claim that the archive carries no ``.pdf``; the refutation
is what makes a complete ``DocumentSourceBundle`` constructible over USB with no SSH
credential. Evidence, from a 1-page pdf document: archive 8557 bytes, magic ``504b0304``,
members ``.metadata`` 381 / ``.content`` 2329 / ``.pdf`` 5481 / one ``.rm`` of 0 bytes and
one of 6051, both at depth 1.

Decisions
---------
**Read from bytes, never a temp file.** ``zipfile.ZipFile(io.BytesIO(payload))``. No port
in this system touches the filesystem, so an adapter that spilled the archive to disk would
be the one place a "no space left" error could reach a use case.

**Never trust archive ordering.** :func:`read_rmdoc` returns a name-keyed mapping and the
*caller* walks the page order it decodes from :attr:`ArchiveMembers.content`. On real PDFs
the count of ``.rm`` members can exceed the page count -- measured, 16 members for 10 pages
-- because layers orphaned by an edit stay in the store and are unreachable from
``cPages``. Iterating the archive instead of the page order renders ghost pages. This
reader returns **everything it finds**, orphans included; dropping them is the caller's
job, and it is a job the caller can only do because it holds the page order and this
module does not.

**Zero-length values are preserved.** 86 of 194 real ``.rm`` files are zero bytes. A page
with a zero-byte member is a page that carries no ink, which
:attr:`DevicePageSource.scene` spells ``None`` -- but that translation is the caller's,
because "the member was absent" and "the member was empty" are different facts about the
archive and only one of them survives being collapsed here.

**A missing underlay is a protocol error, not an unsupported operation.** The archive is
the transport's own answer, and an answer that omits the underlay of a pdf document
contradicts itself. That is also the only honest encoding for the unmeasured epub case:
this reader is told which suffix to expect and reports the archive's disagreement rather
than guessing.

**Member names are checked before they are read.** A zip stores names verbatim, so a
crafted archive can name ``../../etc/passwd``. Nothing here writes to the filesystem, so
the check is not what stops a write -- it is what stops an unexpected shape being routed as
if it were a page, and it keeps that guarantee true for a future caller that does write.
Every member's owning uuid is checked against ``doc_uuid`` for the same reason: an archive
carrying another document's files means the transport served the wrong document, and
silently ignoring the foreign members would produce a bundle from a mix of two.
"""

from __future__ import annotations

import io
import types
import zipfile
from collections.abc import Mapping
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel

from rmspec.domain.errors import DeviceProtocolError, TransportKind

__all__ = ["RMDOC_ROUTE", "ArchiveMembers", "read_rmdoc"]

RMDOC_ROUTE: Final = "/download/{id}/rmdoc"
"""The route family an archive comes from, named in every error this module raises.

The template and not one request: the document identifier is already in every message
that needs it, and substituting it into the route would make two spellings of the same
route appear in logs.
"""

METADATA_SUFFIX: Final = ".metadata"
"""Suffix of the always-present metadata sidecar member."""

CONTENT_SUFFIX: Final = ".content"
"""Suffix of the always-present content sidecar member."""

SCENE_SUFFIX: Final = ".rm"
"""Suffix of a page's v6 scene member, one directory below the archive root."""

_TRAVERSAL_SEGMENTS: Final = frozenset({"", ".", ".."})
"""Path segments no member name may contain: they are how a name escapes its root."""

_SCENE_DEPTH: Final = 2
"""Segment count of a scene member -- ``<docUUID>/<pageUUID>.rm`` and nothing deeper."""


def _read_only(scenes: Mapping[str, bytes], /) -> Mapping[str, bytes]:
    """Make a validated scene mapping genuinely immutable.

    ``frozen=True`` stops a field being reassigned; it does nothing about mutating the
    ``dict`` pydantic built inside one. This closes that hole, which matters because the
    mapping is handed to a caller that walks a page order over it and must not be able to
    change what the archive said.

    Parameters
    ----------
    scenes
        The mapping pydantic produced for the field.

    Returns
    -------
    Mapping[str, bytes]
        A read-only view over a private copy.
    """
    return types.MappingProxyType(dict(scenes))


_SceneBytes = Annotated[Mapping[str, bytes], AfterValidator(_read_only)]
"""Page identifier to raw scene bytes, read-only once validated."""


class ArchiveMembers(BaseModel, frozen=True, extra="forbid"):
    """One ``.rmdoc`` archive's members, addressed by the role each one plays.

    Members are named by what they are rather than by their archive path, so no caller
    learns the ``<docUUID>/<pageUUID>.rm`` layout or which archive format a future
    transport might use. Both sidecars are raw bytes: decoding them is the calling
    adapter's work, and this module deliberately does not import the page-order reader so
    that a malformed ``.content`` fails where its error can be attributed to the document
    rather than to the archive.
    """

    doc_uuid: str
    """The document every member belongs to, as the caller asked for it."""

    metadata: bytes
    """The ``<docUUID>.metadata`` member, undecoded."""

    content: bytes
    """The ``<docUUID>.content`` member, undecoded."""

    scenes: _SceneBytes
    """Page identifier to raw scene bytes, zero-length values preserved.

    Every ``.rm`` member the archive holds, including layers orphaned by an edit and
    unreachable from the content's page list. The caller intersects this with the page
    order it decodes; nothing is dropped here.
    """

    underlay: bytes | None
    """The original pdf or epub, or ``None`` for a notebook.

    No default: a notebook stating ``None`` and a pdf whose underlay was never looked for
    are different facts, and a default would let the second masquerade as the first.
    """


def read_rmdoc(payload: bytes, /, *, doc_uuid: str, suffix: str | None) -> ArchiveMembers:
    """Route one ``.rmdoc`` archive's members by role.

    Parameters
    ----------
    payload
        The response body, exactly as the transport received it.
    doc_uuid
        The document that was asked for. Every member must belong to it.
    suffix
        Extension of the underlay member to require -- ``"pdf"`` or ``"epub"`` -- or
        ``None`` for a notebook. When ``None``, an underlay-shaped member is ignored
        rather than returned: ``DocumentSourceBundle`` refuses a notebook that carries a
        ``base``, so returning one would fail validation in the caller.

    Returns
    -------
    ArchiveMembers
        The two sidecars, every ``.rm`` member keyed by page identifier with zero-length
        values preserved, and the underlay when one was required.

    Raises
    ------
    DeviceProtocolError
        The payload is not a zip; a member name escapes the archive root; a member belongs
        to a document other than ``doc_uuid``; the ``.metadata`` or ``.content`` member is
        absent; or ``suffix`` was given and the underlay member is absent. Every one of
        these is the transport contradicting its own answer, which is what
        ``DeviceProtocolError`` means -- an absent document is reported by the route that
        was asked for it, not by this reader.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return _routed(archive, doc_uuid=doc_uuid, suffix=suffix)
    except zipfile.BadZipFile as exc:
        raise _protocol_error(
            expected="an application/zip .rmdoc archive",
            got=f"bytes zipfile refuses ({exc})",
        ) from exc


def _routed(archive: zipfile.ZipFile, /, *, doc_uuid: str, suffix: str | None) -> ArchiveMembers:
    """Check every member name, then read the ones a bundle needs.

    Names are validated before anything is read, so an archive with one foreign member
    fails before its other members are decompressed.

    Parameters
    ----------
    archive
        The opened archive.
    doc_uuid
        The document that was asked for.
    suffix
        Extension of the underlay member to require, or ``None`` for a notebook.

    Returns
    -------
    ArchiveMembers
        The members, routed by role.

    Raises
    ------
    DeviceProtocolError
        A member name escapes the root or names another document, or a required member is
        absent.
    """
    present = frozenset(info.filename for info in archive.infolist() if not info.is_dir())
    for name in sorted(present):
        _check_member_name(name, doc_uuid=doc_uuid)
    metadata_name = f"{doc_uuid}{METADATA_SUFFIX}"
    content_name = f"{doc_uuid}{CONTENT_SUFFIX}"
    underlay_name = None if suffix is None else f"{doc_uuid}.{suffix}"
    for required in (metadata_name, content_name, underlay_name):
        if required is not None:
            _require_member(present, required)
    return ArchiveMembers(
        doc_uuid=doc_uuid,
        metadata=archive.read(metadata_name),
        content=archive.read(content_name),
        scenes={
            _page_id(name): archive.read(name)
            for name in sorted(present)
            if _is_scene(name, doc_uuid=doc_uuid)
        },
        underlay=None if underlay_name is None else archive.read(underlay_name),
    )


def _check_member_name(name: str, /, *, doc_uuid: str) -> None:
    """Refuse a member name that escapes the archive root or names another document.

    Parameters
    ----------
    name
        The member's name, exactly as the archive stored it.
    doc_uuid
        The document that was asked for.

    Raises
    ------
    DeviceProtocolError
        The name is absolute, uses a backslash separator, contains a ``.`` or ``..``
        segment, or its first path component does not resolve to ``doc_uuid``.
    """
    segments = name.split("/")
    if "\\" in name or any(segment in _TRAVERSAL_SEGMENTS for segment in segments):
        raise _protocol_error(
            expected="member names relative to the archive root",
            got=f"member {name!r}",
        )
    owner = segments[0] if len(segments) > 1 else segments[0].rsplit(".", 1)[0]
    if owner != doc_uuid:
        raise _protocol_error(
            expected=f"every member to belong to document {doc_uuid}",
            got=f"member {name!r}",
        )


def _require_member(present: frozenset[str], name: str, /) -> None:
    """Refuse an archive that omits a member a bundle cannot be built without.

    Parameters
    ----------
    present
        Every file member the archive holds.
    name
        The member that must be there.

    Raises
    ------
    DeviceProtocolError
        The member is absent, with every member that *is* there named so the omission can
        be read off the message.
    """
    if name not in present:
        raise _protocol_error(
            expected=f"a {name} member",
            got=f"members: {', '.join(sorted(present))}",
        )


def _is_scene(name: str, /, *, doc_uuid: str) -> bool:
    """Report whether a member is one page's scene payload.

    Parameters
    ----------
    name
        The member's name.
    doc_uuid
        The document that was asked for.

    Returns
    -------
    bool
        ``True`` for ``<docUUID>/<pageUUID>.rm`` and nothing else. A ``.rm`` nested deeper
        is not a page: the measured archive has no such member, and treating one as a page
        would invent an identifier out of a path this reader does not understand.
    """
    segments = name.split("/")
    return (
        len(segments) == _SCENE_DEPTH
        and segments[0] == doc_uuid
        and segments[1].endswith(SCENE_SUFFIX)
    )


def _page_id(name: str, /) -> str:
    """Read the page identifier a scene member's name carries.

    Parameters
    ----------
    name
        A member name :func:`_is_scene` accepted.

    Returns
    -------
    str
        The basename with ``.rm`` removed, verbatim -- never through ``UUID(...)``, which
        would both reject a non-uuid name and normalise its case, so a page id from the
        archive would stop comparing equal to the same id from the content sidecar.
    """
    return name.split("/")[1].removesuffix(SCENE_SUFFIX)


def _protocol_error(*, expected: str, got: str) -> DeviceProtocolError:
    """Build one of this module's protocol errors.

    Parameters
    ----------
    expected
        What the archive's contract promises.
    got
        What the archive turned out to hold.

    Returns
    -------
    DeviceProtocolError
        The error, naming the USB web API and the download route family.
    """
    return DeviceProtocolError(
        transport=TransportKind.USB_WEB_API,
        route=RMDOC_ROUTE,
        expected=expected,
        got=got,
    )
