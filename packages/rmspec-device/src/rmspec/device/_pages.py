"""The ``.content`` page-order walk, narrowed to what a transport needs.

A device transport reads ``.content`` for exactly one reason: to learn which pages a
document claims, in what order, and against what template. That is enough to turn the
files it fetched into an ordered :class:`DevicePageSource` tuple, which is the only page
shape ``ports/device.py`` lets across its boundary -- no json key and no CRDT envelope
reaches a use case.

Known duplication, deliberate
-----------------------------
This is a **narrower sibling** of ``rmspec.formats.page_index.decode_page_index``, which
reads the same two sidecar shapes and additionally reads ``redir`` for the source-pdf page
index and applies ``.pagedata`` template precedence. ``rmspec.device`` may import
``rmspec.domain`` and nothing else from the workspace -- an architecture test enforces it
-- so this walk is reimplemented rather than shared. Both modules are adapters that
legitimately own firmware knowledge; ``page_index.py``'s own docstring says the walk
"stays inside the adapter". Folding the two onto one domain-level reader is a **step 7**
item. Until then the two must agree on page order and template precedence, which
``test_device_pages.py`` asserts by re-deriving the sibling's expectations here.

Relocated from ``formats/content.py`` and ``formats/page_index.py``
------------------------------------------------------------------
Legacy ``parse_content_json`` built a ``ContentInfo`` of ``PageRef`` objects; the walk
itself now lives in ``formats/page_index.py``. Everything about the *shape* is kept from
those two: both sidecar spellings, file order, and no filtering of any kind.

Four deliberate divergences, three of them the sibling's own and honoured verbatim:

1. **The legacy ``"Blank"`` default is gone.** ``PageRef.template`` defaulted to the
   literal string ``"Blank"`` when a ``cPages`` entry carried no ``template`` key, so pages
   the store said nothing about came back naming a template.
   :attr:`DevicePageSource.template_name` documents ``None`` as the only spelling of "no
   template", and ``"Blank"`` as meaning the store really did record a template called
   Blank. An absent key and an empty string therefore both become ``None``, and a stored
   ``"Blank"`` survives verbatim.
2. **No uuid normalisation.** The legacy reader funnelled every id through ``UUID(...)``,
   which both rejected a non-uuid id -- costing the whole document -- and normalised case
   and grouping, so an identity read from the device stopped comparing equal to the same
   identity read from a cache row. The id is carried verbatim.
3. **``.pagedata`` is not read here, and would be wrong if it were.** The sibling applies
   ``.pagedata`` lines positionally because a local mirror has them. This transport does
   not: the ``.rmdoc`` archive contains no ``.pagedata`` member at all, and the sidecar is
   non-authoritative anyway -- measured, both real files are 6 bytes holding ``"Blank"``,
   one of them against a 432-page document, and on the 1-page one the real template is
   ``"P Grid small"``. Reading it would overwrite 431 correct templates with a wrong one.
   The entry's own ``template.value`` is therefore the only source, which is exactly the
   branch the sibling falls through to when no line exists at a position.
4. **A repeated id is dropped after its first occurrence.** New here, and forced by the
   port: :class:`DocumentSourceBundle` refuses a duplicated ``page_id``, so passing one
   through would fail validation deep inside a caller with no way back to the sidecar that
   caused it. Order is otherwise untouched -- first occurrence keeps its position -- so
   this narrows the sibling's output only on a payload the sibling's consumer would have
   rejected too.

Failure vocabulary
------------------
``TypeError`` for a value of the wrong json type and ``ValueError`` for a value of the
right type that cannot be read -- the same split the sibling and the domain's own sidecar
readers use. The caller maps either one to ``MalformedDeviceMetadata``, so this module
raises no domain error and imports none.
"""

from __future__ import annotations

import json
from typing import Final

from pydantic import BaseModel, Field

__all__ = ["PageOrderEntry", "decode_page_order"]

_CPAGES_KEY: Final = "cPages"
_PAGES_KEY: Final = "pages"
_ID_KEY: Final = "id"
_TEMPLATE_KEY: Final = "template"
_VALUE_KEY: Final = "value"


class PageOrderEntry(BaseModel, frozen=True, extra="forbid"):
    """One page as the ``.content`` sidecar claims it, before any bytes are fetched.

    Carries the two facts a transport can learn from the sidecar alone, which are also the
    two :class:`DevicePageSource` fields that are not bytes. A caller zips this order
    against the archive's members; the tuple's position is the page index, so nothing here
    restates it.
    """

    page_id: str = Field(min_length=1)
    """The page's identifier, verbatim as the sidecar spelled it."""

    template_name: str | None = None
    """The template the sidecar recorded, or ``None`` when it recorded none."""


def decode_page_order(content: bytes | None, /) -> tuple[PageOrderEntry, ...]:
    """Read the ordered page list out of one ``.content`` sidecar.

    Parameters
    ----------
    content
        The sidecar's bytes, or ``None`` when the transport holds no ``.content`` for this
        entry. ``None`` yields no pages rather than failing, which is what lets a folder
        and a ``.content``-less entry be walked at all.

    Returns
    -------
    tuple[PageOrderEntry, ...]
        Every page the sidecar claims, in file order, with nothing filtered out --
        including an entry the sidecar marks deleted -- except a repeat of an id already
        seen. Both sidecar shapes are read: firmware 3.x ``cPages.pages[]`` and the pre-v2
        flat ``pages`` list of bare uuid strings. ``cPages`` wins when a sidecar somehow
        carries both, matching the sibling reader.

    Raises
    ------
    TypeError
        The payload is not a json object, or a member carries a json type this reader does
        not accept: a page list that is not an array, a page entry that is not an object,
        a ``template`` that is not an object, a ``template.value`` or ``id`` that is not a
        string.
    ValueError
        The payload is not valid json, or a page entry has no ``id`` or an empty one. A
        page with no identity cannot be addressed, ordered, or matched to an archive
        member.
    """
    if content is None:
        return ()
    seen: set[str] = set()
    ordered: list[PageOrderEntry] = []
    for entry in _claimed_pages(_json_object(content)):
        if entry.page_id in seen:
            continue
        seen.add(entry.page_id)
        ordered.append(entry)
    return tuple(ordered)


def _json_object(raw: bytes, /) -> dict[str, object]:
    """Parse sidecar bytes into the members of one json object.

    Parameters
    ----------
    raw
        The sidecar's bytes.

    Returns
    -------
    dict[str, object]
        The object's members, still untyped.

    Raises
    ------
    TypeError
        The payload is valid json that is not an object.
    ValueError
        The payload is not valid json at all.
    """
    decoded: object = json.loads(raw)
    members = _members(decoded)
    if members is None:
        msg = f"expected a json object, got {type(decoded).__name__}"
        raise TypeError(msg)
    return members


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


def _claimed_pages(data: dict[str, object], /) -> tuple[PageOrderEntry, ...]:
    """Read the page list from whichever of the two sidecar shapes is present.

    Parameters
    ----------
    data
        The members of a ``.content`` sidecar.

    Returns
    -------
    tuple[PageOrderEntry, ...]
        The pages in file order, duplicates included -- de-duplication happens once, in
        :func:`decode_page_order`. Empty when the sidecar lists no pages, which is a
        folder or a document whose pages were never enumerated.

    Raises
    ------
    TypeError
        A page list or a page entry is of the wrong json type.
    ValueError
        A page id is absent or empty.
    """
    cpages = _members(data.get(_CPAGES_KEY))
    if cpages is not None and _PAGES_KEY in cpages:
        listed = _json_list(cpages[_PAGES_KEY], where=f"{_CPAGES_KEY}.{_PAGES_KEY}")
        return tuple(_entry_from_cpage(page) for page in listed)
    flat = data.get(_PAGES_KEY)
    if flat is not None:
        return tuple(
            PageOrderEntry(page_id=_page_id(item), template_name=None)
            for item in _json_list(flat, where=_PAGES_KEY)
        )
    return ()


def _entry_from_cpage(page: object, /) -> PageOrderEntry:
    """Read one firmware-3.x ``cPages.pages`` entry.

    ``idx``, ``scrollTime``, ``verticalScroll`` and ``redir`` are all present on real
    entries and all ignored: page *order* is the array's order, and the source-pdf page
    index is a formats concern that no device port carries.

    Parameters
    ----------
    page
        One member of the ``cPages.pages`` array.

    Returns
    -------
    PageOrderEntry
        The page's identity and the template it names.

    Raises
    ------
    TypeError
        The entry is not a json object, or its ``template`` envelope or ``id`` carries a
        json type this reader does not accept.
    ValueError
        The page id is absent or empty.
    """
    members = _members(page)
    if members is None:
        msg = f"expected a json object per page, got {type(page).__name__}"
        raise TypeError(msg)
    return PageOrderEntry(
        page_id=_page_id(members.get(_ID_KEY)),
        template_name=_template(_wrapped_value(members.get(_TEMPLATE_KEY))),
    )


def _json_list(value: object, /, *, where: str) -> list[object]:
    """Read a json array.

    Parameters
    ----------
    value
        The raw json value.
    where
        Dotted path of the member, for the message.

    Returns
    -------
    list[object]
        The array's items, still untyped.

    Raises
    ------
    TypeError
        The value is not a json array.
    """
    if isinstance(value, list):
        return list(value)
    msg = f"expected a json array at {where}, got {type(value).__name__}"
    raise TypeError(msg)


def _wrapped_value(value: object, /) -> object:
    """Unwrap the ``{"timestamp": ..., "value": ...}`` envelope firmware 3.x uses.

    The envelope is how the CRDT stamps a fact with the replica and counter that last
    wrote it. Only ``value`` is read: the timestamp orders concurrent edits on the device
    and says nothing a reader of one already-merged sidecar can use.

    Parameters
    ----------
    value
        The raw json value of the wrapping member, or ``None`` when it was absent.

    Returns
    -------
    object
        The wrapped value, or ``None`` when the envelope or its ``value`` was absent.

    Raises
    ------
    TypeError
        The member is present and is not a json object. Kept strict, unlike the sibling's
        lenient ``redir``: a template name has no positional fallback a consumer could
        recover through, so a wrong type there really is a malformed sidecar.
    """
    if value is None:
        return None
    members = _members(value)
    if members is None:
        msg = f"expected a json object at {_TEMPLATE_KEY}, got {type(value).__name__}"
        raise TypeError(msg)
    return members.get(_VALUE_KEY)


def _page_id(value: object, /) -> str:
    """Read a page identifier.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.

    Returns
    -------
    str
        The identifier verbatim, never normalised -- see divergence 2.

    Raises
    ------
    TypeError
        The value is present and is not a json string.
    ValueError
        The key was absent or the string is empty.
    """
    if value is None:
        msg = "page entry has no id"
        raise ValueError(msg)
    if not isinstance(value, str):
        msg = f"expected a json string page id, got {type(value).__name__}"
        raise TypeError(msg)
    if not value:
        msg = "page entry has an empty id"
        raise ValueError(msg)
    return value


def _template(value: object, /) -> str | None:
    """Read a template name.

    Parameters
    ----------
    value
        The unwrapped ``template.value``, or ``None`` when the sidecar named none.

    Returns
    -------
    str | None
        The name, or ``None`` for an absent or empty one -- the domain's single spelling
        of "no template", never the legacy literal ``"Blank"``. A stored ``"Blank"``
        survives verbatim, which is the whole of divergence 1.

    Raises
    ------
    TypeError
        The value is present and is not a json string.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"expected a json string template name, got {type(value).__name__}"
        raise TypeError(msg)
    return value or None
