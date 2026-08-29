"""The USB web API's ``/documents/`` entry shape, decoded into domain values.

One listing response is a json array of flat objects, and this module is the only place
that knows their keys. It moves no bytes and spells no route beyond the one it names in a
protocol error: it is a pure function of a payload, which is what lets every branch below
be exercised without a transport and what keeps ``usb.py`` free of key names.

The shape is measured, not inferred. Firmware 3.27.3.0 answers ``GET /documents/`` with
9 keys per ``DocumentType`` entry -- ``Bookmarked``, ``CurrentPage``, ``ID``,
``ModifiedClient``, ``Parent``, ``Type``, ``VisibleName``, ``VissibleName`` (sic) and
``fileType`` -- and the same set minus ``CurrentPage`` and ``fileType`` per
``CollectionType`` entry. See ``specs/device/3.27.3.0/http.json``.

Relocated from ``src/remarkable_spec/device/web_api.py``, with seven changes
---------------------------------------------------------------------------
``WebAPI.list_documents`` returned ``list[dict[str, Any]]`` and
``WebAPI.list_all_documents`` walked folders with a bare ``except Exception: continue``.
Both are replaced, and each divergence is forced by ``ports/device.py``:

1. **Typed values, not dicts.** A caller receives :class:`DeviceDocument` and
   :class:`DeviceFolder`, so a folder identifier cannot reach a port that pulls bytes and
   no CLI re-reads ``d["Type"]``. Documents and folders are separate tuples for the same
   reason.
2. **A dropped entry is now data.** The legacy walk swallowed every failure, so a listing
   silently shrank. Each per-entry problem becomes a :class:`SkippedEntry` carrying a
   closed-set :class:`SkipReason` and a human-readable detail, and only a payload that is
   not a json array at all raises. That split is the port's rule: per-entry failure is
   data, whole-transport failure raises.
3. **``VisibleName`` is canonical.** Legacy read
   ``d.get("VissibleName", d.get("VisibleName", d.get("VisssibleName", "Untitled")))`` --
   it preferred the misspelling, invented a triple-``s`` spelling no firmware writes, and
   defaulted to a name the device never recorded. Here ``VisibleName`` wins,
   ``VissibleName`` is read only when ``VisibleName`` is absent, and an entry naming
   itself with neither is skipped rather than christened ``"Untitled"``. The two are
   equal on all 31 entries observed, so the fallback is a legacy alias and not a second
   fact.
4. **``page_count`` is always ``None``.** The wire's only page-ish number is
   ``CurrentPage``, the last-opened page *index*. Reporting it as a count would publish a
   different fact under the right field's name, and ``DeviceDocument.page_count``
   documents ``None`` as "the device reported none".
5. **``trashed`` is always ``False``, and there is no sentinel branch.** Measured: the
   USB API filters trashed entries out entirely -- a full breadth-first walk reaches 41 of
   the 42 on-disk entities, the one missing being the only one whose on-disk metadata says
   ``parent: "trash"``, and no entry in any listing at any depth ever carries
   ``Parent == "trash"``. ``False`` is therefore *accurate* for every entry this decoder
   returns. Sentinel-handling code here would be unreachable, and unreachable code cannot
   be covered, so it is deliberately absent. The legacy client's
   ``list_documents(parent="trash")`` filter is gone with it.
6. **The root is ``None``, not ``""``.** ``Parent`` is the empty string at the library
   root, and ``DeviceDocument.parent_uuid`` spells that ``None`` so no caller compares
   against a magic empty string.
7. **No ``Accept``-header or route knowledge.** This module decodes; ``usb.py`` fetches.

What counts as a required key
-----------------------------
Only the keys this decoder reads: ``Type``, ``ID``, ``Parent``, ``ModifiedClient``, a name
key, and -- for a document -- ``fileType``. ``Bookmarked`` and ``CurrentPage`` are
measured present on every real entry and are deliberately *not* required, because refusing
an entry over a key nothing maps would fail a future firmware for no gain. A json ``null``
is treated as absent: both are the same MALFORMED diagnosis, and distinguishing them would
buy a branch that says the same thing twice.

``SkipReason`` assignment
-------------------------
``MALFORMED_METADATA`` -- the entry is not a json object, a required key is absent or of
the wrong json type, or ``ModifiedClient`` will not parse as an offset-bearing instant.
``VALIDATION_FAILED`` -- the entry decoded but describes nothing this domain can
represent: an unknown ``Type``, an unknown ``fileType``, an empty ``ID``, or any other
pydantic ``ValidationError``. ``UNREADABLE`` is never produced here: it means the
transport saw a folder and was refused its children, which only the catalog's walk can
observe.

``ModifiedClient`` and the naive-datetime rule
----------------------------------------------
The value is ISO-8601 shaped ``9999-99-99T99:99:99.999Z``, and
``datetime.fromisoformat`` accepts the trailing ``Z`` on 3.11+. The port's validator
normalizes to UTC at millisecond precision and *rejects* a naive datetime, so an
offset-less string would fail deep inside pydantic with a message about a field the user
never typed. This module checks ``tzinfo`` itself and reports MALFORMED with the offending
string, which is the diagnosis a reader can act on.
"""

from __future__ import annotations

import datetime
import json
from typing import Final

from pydantic import BaseModel, ValidationError

from rmspec.domain.errors import DeviceProtocolError, TransportKind
from rmspec.domain.ports.device import (
    DeviceDocument,
    DeviceFileType,
    DeviceFolder,
    SkippedEntry,
    SkipReason,
)

__all__ = [
    "COLLECTION_TYPE",
    "DOCUMENT_TYPE",
    "LISTING_ROUTE",
    "DecodedEntries",
    "decode_entries",
    "entry_id",
    "entry_parent",
    "is_collection",
]

LISTING_ROUTE: Final = "/documents/"
"""The route family this payload comes from, named in a protocol error.

The family and not one request: :func:`decode_entries` takes only bytes, so it cannot
know whether they came from the root listing or from ``/documents/{folder}``. Naming the
family is honest; inventing a folder identifier the function was never given is not.
"""

DOCUMENT_TYPE: Final = "DocumentType"
"""The ``Type`` value of an entry that is a document."""

COLLECTION_TYPE: Final = "CollectionType"
"""The ``Type`` value of an entry that is a folder."""

_ROOT_PARENT: Final = ""
"""The ``Parent`` value that means the library root rather than a folder."""

_ID_KEY: Final = "ID"
_PARENT_KEY: Final = "Parent"
_TYPE_KEY: Final = "Type"
_MODIFIED_KEY: Final = "ModifiedClient"
_FILE_TYPE_KEY: Final = "fileType"
_NAME_KEY: Final = "VisibleName"
_LEGACY_NAME_KEY: Final = "VissibleName"


class DecodedEntries(BaseModel, frozen=True, extra="forbid"):
    """One ``/documents/`` response, split by what each entry turned out to be.

    Three tuples rather than one sequence tagged by kind, mirroring
    :class:`DeviceListing`: the catalog concatenates several of these across a
    breadth-first walk and never re-sorts them. None of the three has a default, so a
    decoder cannot construct a result that quietly omits what it could not read.
    """

    documents: tuple[DeviceDocument, ...]
    """Every ``DocumentType`` entry that validated, in payload order."""

    folders: tuple[DeviceFolder, ...]
    """Every ``CollectionType`` entry that validated, in payload order."""

    skipped: tuple[SkippedEntry, ...]
    """Every entry that did not validate, reported rather than dropped."""


class _EntryRejectedError(Exception):
    """One entry cannot become a domain value, with the reason already decided.

    An internal control-flow signal, never raised past :func:`decode_entries`. It exists
    so each reader below can refuse a value at the point it inspects it and still leave
    one place -- :func:`_decode_one` -- that turns a refusal into a
    :class:`SkippedEntry`, rather than threading a result union through six helpers.
    """

    def __init__(self, reason: SkipReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def decode_entries(payload: bytes, /) -> DecodedEntries:
    """Decode one ``/documents/`` response into documents, folders and skips.

    Parameters
    ----------
    payload
        The response body, exactly as the transport received it.

    Returns
    -------
    DecodedEntries
        Every entry that validated, split by kind, plus every entry that did not. All
        three tuples are in payload order, and every one of them may be empty.

    Raises
    ------
    DeviceProtocolError
        The payload is not a json array at all -- not valid json, or valid json of some
        other type. Nothing about one entry ever raises: the device answering with an
        object where the contract says array is a broken contract, while one unreadable
        entry is a fact about the user's library.
    """
    documents: list[DeviceDocument] = []
    folders: list[DeviceFolder] = []
    skipped: list[SkippedEntry] = []
    for entry in _json_array(payload):
        decoded = _decode_one(entry)
        if isinstance(decoded, DeviceDocument):
            documents.append(decoded)
        elif isinstance(decoded, DeviceFolder):
            folders.append(decoded)
        else:
            skipped.append(decoded)
    return DecodedEntries(
        documents=tuple(documents),
        folders=tuple(folders),
        skipped=tuple(skipped),
    )


def entry_parent(entry: object, /) -> str | None:
    """Read one raw entry's ``Parent``, with the root spelled ``None``.

    Used by the catalog's breadth-first walk to discard the silent root fallback: an
    unrecognised folder identifier makes this firmware return the byte-identical root
    listing, and comparing this against the identifier that was requested is what tells a
    real folder from that fallback. The comparison must happen before decoding, which is
    why this reads a raw json value and is total -- an entry with no usable ``Parent``
    answers ``None``, which equals no folder identifier and so is discarded.

    Parameters
    ----------
    entry
        One item of the decoded json array.

    Returns
    -------
    str | None
        The containing folder's identifier, or ``None`` at the library root and for any
        entry whose ``Parent`` is absent, empty, or not a json string.
    """
    value = _string_member(entry, _PARENT_KEY)
    return None if value == _ROOT_PARENT else value


def entry_id(entry: object, /) -> str | None:
    """Read one raw entry's ``ID``.

    Total, and deliberately: it is what names the entry in a :class:`SkippedEntry` whose
    decode already failed, so it cannot itself refuse anything.

    Parameters
    ----------
    entry
        One item of the decoded json array.

    Returns
    -------
    str | None
        The identifier verbatim, or ``None`` when it is absent, empty, or not a json
        string. An empty ``ID`` is not a recovered identifier.
    """
    return _string_member(entry, _ID_KEY)


def is_collection(entry: object, /) -> bool:
    """Report whether one raw entry is a folder.

    Parameters
    ----------
    entry
        One item of the decoded json array.

    Returns
    -------
    bool
        ``True`` when ``Type`` is exactly ``CollectionType``. Anything else -- a
        document, an unknown type, a non-object -- is ``False``, so a walk built on this
        never enqueues something it cannot list.
    """
    return _string_member(entry, _TYPE_KEY) == COLLECTION_TYPE


def _json_array(payload: bytes, /) -> list[object]:
    """Parse a response body into the items of one json array.

    Parameters
    ----------
    payload
        The response body.

    Returns
    -------
    list[object]
        The array's items, still untyped.

    Raises
    ------
    DeviceProtocolError
        The bytes are not valid json, or are valid json that is not an array.
    """
    try:
        decoded: object = json.loads(payload)
    except ValueError as exc:
        raise _protocol_error(got=f"bytes that are not json ({exc})") from exc
    if not isinstance(decoded, list):
        raise _protocol_error(got=f"a json {type(decoded).__name__}")
    return list(decoded)


def _protocol_error(*, got: str) -> DeviceProtocolError:
    """Build the one protocol error this module raises.

    Parameters
    ----------
    got
        What the payload turned out to be.

    Returns
    -------
    DeviceProtocolError
        The error, naming the USB web API and the listing route family.
    """
    return DeviceProtocolError(
        transport=TransportKind.USB_WEB_API,
        route=LISTING_ROUTE,
        expected="a json array of library entries",
        got=got,
    )


def _decode_one(entry: object, /) -> DeviceDocument | DeviceFolder | SkippedEntry:
    """Turn one raw entry into a domain value, or into the reason it could not be one.

    Parameters
    ----------
    entry
        One item of the decoded json array.

    Returns
    -------
    DeviceDocument | DeviceFolder | SkippedEntry
        The decoded value, or a skip carrying whichever identifier could be recovered.
    """
    try:
        return _decode_strict(entry)
    except _EntryRejectedError as rejected:
        return SkippedEntry(uuid=entry_id(entry), reason=rejected.reason, detail=rejected.detail)
    except ValidationError as invalid:
        return SkippedEntry(
            uuid=entry_id(entry),
            reason=SkipReason.VALIDATION_FAILED,
            detail=_validation_detail(invalid),
        )


def _decode_strict(entry: object, /) -> DeviceDocument | DeviceFolder:
    """Decode one entry, refusing anything the domain cannot represent.

    ``page_count`` and ``trashed`` are passed explicitly rather than left to their
    defaults, because both are decisions this adapter made -- see divergences 4 and 5 --
    and a default would hide them at the one place they are taken.

    Parameters
    ----------
    entry
        One item of the decoded json array.

    Returns
    -------
    DeviceDocument | DeviceFolder
        The entry as a domain value.

    Raises
    ------
    _EntryRejectedError
        The entry is not a json object, a required key is absent or ill-typed, or its
        ``Type`` or ``fileType`` names something outside the domain's closed sets.
    ValidationError
        The values read are individually well-formed and the domain model still refuses
        them -- an empty ``ID`` being the one case a real device could produce.
    """
    members = _members(entry)
    if members is None:
        msg = f"expected a json object per entry, got {type(entry).__name__}"
        raise _EntryRejectedError(SkipReason.MALFORMED_METADATA, msg)
    kind = _required_string(members, _TYPE_KEY)
    if kind not in {DOCUMENT_TYPE, COLLECTION_TYPE}:
        msg = f"Type {kind!r} is neither {DOCUMENT_TYPE} nor {COLLECTION_TYPE}"
        raise _EntryRejectedError(SkipReason.VALIDATION_FAILED, msg)
    uuid = _required_string(members, _ID_KEY)
    name = _entry_name(members)
    parent = _required_string(members, _PARENT_KEY)
    parent_uuid = None if parent == _ROOT_PARENT else parent
    last_modified = _instant(_required_string(members, _MODIFIED_KEY))
    if kind == COLLECTION_TYPE:
        return DeviceFolder(
            uuid=uuid,
            name=name,
            parent_uuid=parent_uuid,
            last_modified=last_modified,
            trashed=False,
        )
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=_file_type(_required_string(members, _FILE_TYPE_KEY)),
        parent_uuid=parent_uuid,
        last_modified=last_modified,
        page_count=None,
        trashed=False,
    )


def _members(value: object, /) -> dict[str, object] | None:
    """Narrow a json value to the members of an object, or to ``None``.

    Every key is rendered with ``str``: json guarantees string keys, and this is what
    turns an unparameterised ``dict`` into one whose members can be read by name.

    Parameters
    ----------
    value
        The raw json value.

    Returns
    -------
    dict[str, object] | None
        The members, or ``None`` when the value is not a json object.
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return None


def _string_member(entry: object, key: str, /) -> str | None:
    """Read one member of a raw entry as a non-empty json string, refusing nothing.

    Parameters
    ----------
    entry
        One item of the decoded json array.
    key
        The member to read.

    Returns
    -------
    str | None
        The value, or ``None`` when the entry is not an object or the member is absent,
        empty, or not a json string.
    """
    members = _members(entry)
    if members is None:
        return None
    value = members.get(key)
    return value if isinstance(value, str) and value else None


def _required_string(members: dict[str, object], key: str, /) -> str:
    """Read a member this decoder cannot proceed without.

    Parameters
    ----------
    members
        The entry's members.
    key
        The member to read.

    Returns
    -------
    str
        The value verbatim, empty string included -- an empty ``ID`` is a validation
        failure and not a malformed one, so it is refused downstream by the model.

    Raises
    ------
    _EntryRejectedError
        The member is absent, json ``null``, or not a json string.
    """
    value = members.get(key)
    if value is None:
        msg = f"required key {key!r} is absent"
        raise _EntryRejectedError(SkipReason.MALFORMED_METADATA, msg)
    if not isinstance(value, str):
        msg = f"key {key!r} is a json {type(value).__name__}, not a string"
        raise _EntryRejectedError(SkipReason.MALFORMED_METADATA, msg)
    return value


def _entry_name(members: dict[str, object], /) -> str:
    """Read the name the tablet UI shows, preferring the canonical spelling.

    Parameters
    ----------
    members
        The entry's members.

    Returns
    -------
    str
        ``VisibleName`` when the key is present, else ``VissibleName`` (sic). An empty
        name is returned as-is: ``DeviceDocument.name`` permits one, and the device does
        record untitled documents.

    Raises
    ------
    _EntryRejectedError
        Neither key is present, or whichever one is present is not a json string. The
        misspelling is never consulted to repair an ill-typed ``VisibleName``: that would
        make an entry's name depend on which of two equal keys happened to be valid.
    """
    for key in (_NAME_KEY, _LEGACY_NAME_KEY):
        if key in members:
            return _required_string(members, key)
    msg = f"neither {_NAME_KEY!r} nor {_LEGACY_NAME_KEY!r} is present"
    raise _EntryRejectedError(SkipReason.MALFORMED_METADATA, msg)


def _instant(value: str, /) -> datetime.datetime:
    """Read a ``ModifiedClient`` timestamp as an offset-bearing instant.

    Parameters
    ----------
    value
        The raw ``ModifiedClient`` string.

    Returns
    -------
    datetime.datetime
        The instant, timezone-aware. The port's validator converts it to UTC and
        truncates to milliseconds, so no rounding happens here.

    Raises
    ------
    _EntryRejectedError
        The string is not ISO-8601, or parses to a naive datetime. The port rejects a
        naive value outright, so catching it here is what turns an opaque pydantic
        message into a diagnosis naming the string the device sent.
    """
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"{_MODIFIED_KEY} {value!r} is not an ISO-8601 instant ({exc})"
        raise _EntryRejectedError(SkipReason.MALFORMED_METADATA, msg) from exc
    if parsed.tzinfo is None:
        msg = f"{_MODIFIED_KEY} {value!r} carries no timezone offset"
        raise _EntryRejectedError(SkipReason.MALFORMED_METADATA, msg)
    return parsed


def _file_type(value: str, /) -> DeviceFileType:
    """Read a ``fileType`` as a member of the domain's closed set.

    ``pdf`` and ``notebook`` are the only values measured on firmware 3.27.3.0, because no
    epub document exists on the reference device. ``epub`` is accepted anyway: it is
    already a :class:`DeviceFileType` member, so honouring the obvious spelling costs
    nothing, and what is forbidden is inventing a fourth.

    Parameters
    ----------
    value
        The raw ``fileType`` string.

    Returns
    -------
    DeviceFileType
        The matching member.

    Raises
    ------
    _EntryRejectedError
        The value is outside the closed set. It is never coerced to a member: a document
        silently reported as a notebook would be pulled without its underlay.
    """
    try:
        return DeviceFileType(value)
    except ValueError as exc:
        msg = f"{_FILE_TYPE_KEY} {value!r} is not a kind this domain represents"
        raise _EntryRejectedError(SkipReason.VALIDATION_FAILED, msg) from exc


def _validation_detail(error: ValidationError, /) -> str:
    """Render a pydantic failure as one line a human can read.

    Parameters
    ----------
    error
        The failure raised while constructing a domain value.

    Returns
    -------
    str
        Every complaint as ``field: message``, joined. ``SkippedEntry.detail`` is
        documented as displayed and logged, never parsed, so the shape is free to change.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
