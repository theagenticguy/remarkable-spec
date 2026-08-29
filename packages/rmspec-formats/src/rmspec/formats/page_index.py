"""The structural half of the ``.content`` sidecar: which pages, in what order.

``ports/formats.py`` declines a sidecar codec port because decoding
``.metadata`` / ``.content`` is json plus pydantic, and both already live on the
domain models as ``DocumentMetadata.decode`` and ``DocumentLayout.decode``. This
module is the one piece those two do not own. The domain dropped the legacy
``ContentInfo.page_refs`` on the grounds that it folded into ``Document.pages`` plus
``Page.template_name`` plus ``Page.pdf_page_index`` -- correct as a *model* decision,
but the walk that produces those three facts from one sidecar still has to exist
somewhere, and this is it.

Relocated from ``ContentInfo.from_json`` and ``load_document``, with three changes
------------------------------------------------------------------------------------
Everything about the shape is legacy-exact: both sidecar spellings (firmware 3.x
``cPages.pages`` and the pre-v2 flat ``pages`` list of bare uuid strings), the file
order of the entries, no filtering of any kind, and the template precedence -- a
``.pagedata`` line at position *i* wins whenever one exists, *even when it is
empty*, and only then does the entry's own ``template.value`` apply.

The three deliberate divergences, each forced by the domain:

1. **The legacy ``"Blank"`` default is gone.** ``PageRef.template`` defaulted to the
   literal string ``"Blank"`` when the ``cPages`` entry carried no ``template`` key,
   so pages the store said nothing about came back naming a template.
   ``Page.template_name`` documents ``None`` as the only spelling of "no template",
   and ``"Blank"`` as meaning the store really did record a template called Blank. An
   absent key and an empty string therefore both become ``None``, and a stored
   ``"Blank"`` survives verbatim.
2. **``redir``, not ``redirect``.** The legacy reader looked up
   ``p.get("redirect", {}).get("value")``; firmware 3.x writes ``redir``, so
   ``PageRef.redirect`` was always ``None`` on every real document. Reading the key
   the firmware actually writes makes :attr:`PageIndexEntry.pdf_page_index` populated
   for the first time, which is a behaviour change on every PDF-backed document and
   is what makes ``DegradationKind.PDF_PAGE_INDEX_FALLBACK`` reachable at all. Typed
   ``int`` rather than the legacy ``str | None``, per ``Page.pdf_page_index``.

   The key is read *leniently*, and deliberately so: it is accepted both bare and
   inside the ``{"value": ...}`` envelope -- the same two shapes the only legacy
   reader that ever touched it branched on -- and every value that cannot be read as
   a page index becomes ``None`` instead of an exception. A bad ``redir`` must not
   cost the document. It says nothing about page *order*: the id, the position and
   the template are all intact, so escalating it to the repository's
   ``MalformedDocument`` -- which is documented as "no page order can be
   established" -- would drop the whole document out of a listing over one junk
   optional field. ``None`` is already the domain's spelling of "the redirection map
   named none", and ``DegradationKind.PDF_PAGE_INDEX_FALLBACK`` is where the
   positional fallback a consumer then applies lives. Reading a numeric *string* is
   a widening over legacy, which compared ``isinstance(value, int)`` and fell back to
   the position for a string; it is kept because the firmware's own envelope is
   untyped and a quoted integer is unambiguous.
3. **No uuid normalisation.** The legacy reader funnelled every id through
   ``UUID(...)``, which both rejected a non-uuid id (a whole-document failure) and
   normalised case and grouping. ``models.py`` records that round trip as a defect --
   an identity read from the device stopped comparing equal to the same identity read
   from a cache row -- so the id is carried verbatim and validated by ``PageId``
   instead. Every id in a real store is already canonical, so no filename changes.

Failure vocabulary
------------------
``TypeError`` for a value of the wrong json type and ``ValueError`` for a value of
the right type that cannot be read -- the same split the domain's own sidecar readers
use, and the reason both are named in one ``Raises`` section instead of a pydantic
``ValidationError`` shaped by whichever field validated first. The repository turns
either one into ``MalformedDocument`` with the artifact that produced it, so this
module raises no domain error and imports none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

__all__ = ["PageIndexEntry", "decode_page_index", "decode_pagedata"]


@dataclass(frozen=True, slots=True)
class PageIndexEntry:
    """One page as the ``.content`` sidecar claims it, before anything is decoded.

    A stdlib dataclass rather than a pydantic model, and deliberately: this is an
    adapter-internal value that must never be mistaken for a domain type or appear in
    a port signature. It answers only "what pages does this document claim, in what
    order, against what template and which source-pdf page", which is exactly what the
    repository needs to build a ``Page`` and nothing more.

    Attributes
    ----------
    page_uuid
        The page's identifier, verbatim as the sidecar spelled it. Becomes both the
        ``PageId`` and the ``PAGE.rm`` filename, so it is never reformatted.
    template_name
        The background template, or ``None`` when neither the ``.pagedata`` line nor
        the entry named one. ``None`` is the only spelling of "no template".
    pdf_page_index
        Zero-based page of the source pdf this page annotates, or ``None`` when the
        redirection map named none.
    """

    page_uuid: str
    template_name: str | None
    pdf_page_index: int | None


def decode_pagedata(raw: bytes, /) -> tuple[str, ...]:
    """Read the ``.pagedata`` sidecar into one template name per line.

    Relocated verbatim from ``parse_pagedata``, including the whole-text ``strip()``
    before ``splitlines()``. That strip is load-bearing and is kept even though it
    looks incidental: it drops leading and trailing blank lines, and since the
    resulting list is applied to the page list *by position*, changing it would
    silently re-template every page after the first blank line.

    Parameters
    ----------
    raw
        The sidecar's bytes, exactly as the store holds them.

    Returns
    -------
    tuple[str, ...]
        One name per line, in file order. Empty for a sidecar that holds only
        whitespace. A name may be the empty string, which the page index reads as
        "this page has no template" and which still consumes its position.

    Raises
    ------
    ValueError
        If the bytes are not utf-8. ``UnicodeDecodeError`` is a ``ValueError``, and
        the legacy reader specified utf-8 explicitly, so this is that failure typed
        rather than widened.
    """
    text = raw.decode("utf-8").strip()
    if not text:
        return ()
    return tuple(text.splitlines())


def decode_page_index(
    raw: bytes | None, /, *, templates: tuple[str, ...] = ()
) -> tuple[PageIndexEntry, ...]:
    """Read the ordered page list out of one ``.content`` sidecar.

    Parameters
    ----------
    raw
        The sidecar's bytes, or ``None`` when the store holds no ``.content`` for this
        entry. ``None`` yields no pages rather than failing, which is what lets a
        folder and a ``.content``-less entry appear in a listing at all -- the legacy
        loader called its content reader unguarded and raised ``FileNotFoundError``.
    templates
        The ``.pagedata`` lines from :func:`decode_pagedata`, applied to the page list
        by position. Shorter than the page list is normal and leaves the remaining
        pages on their own ``template.value``.

    Returns
    -------
    tuple[PageIndexEntry, ...]
        Every page the sidecar claims, in file order, with nothing filtered out --
        including an entry the sidecar marks deleted. Filtering would renumber every
        later page, and page position *is* the addressing scheme for ``Page.index``,
        for the positional ``.pagedata`` alignment and for ``DocumentSummary``.

    Raises
    ------
    TypeError
        If the payload is not a json object, or a member carries a json type this
        reader does not accept. A ``redir`` of any shape is never one of them.
    ValueError
        If the payload is not valid json, or a page id is of the right json type but
        cannot be read.
    """
    if raw is None:
        return ()
    claimed = _claimed_pages(_json_object(raw))
    return tuple(
        replace(entry, template_name=templates[position] or None)
        if position < len(templates)
        else entry
        for position, entry in enumerate(claimed)
    )


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
        If the payload is valid json that is not an object.
    ValueError
        If the payload is not valid json at all.
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


def _claimed_pages(data: dict[str, object], /) -> tuple[PageIndexEntry, ...]:
    """Read the page list from whichever of the two sidecar shapes is present.

    Parameters
    ----------
    data
        The members of a ``.content`` sidecar.

    Returns
    -------
    tuple[PageIndexEntry, ...]
        The pages in file order, each carrying its own template and redirection, with
        no ``.pagedata`` applied yet. Empty when the sidecar lists no pages, which is
        a folder or a document whose pages were never enumerated.

    Raises
    ------
    TypeError
        If a page list or a page entry is of the wrong json type.
    ValueError
        If a page id cannot be read.
    """
    cpages = _members(data.get("cPages"))
    if cpages is not None and "pages" in cpages:
        return tuple(
            _entry_from_cpage(page) for page in _json_list(cpages["pages"], where="cPages.pages")
        )
    pages = data.get("pages")
    if pages is not None:
        return tuple(
            PageIndexEntry(page_uuid=_page_uuid(item), template_name=None, pdf_page_index=None)
            for item in _json_list(pages, where="pages")
        )
    return ()


def _entry_from_cpage(page: object, /) -> PageIndexEntry:
    """Read one firmware-3.x ``cPages.pages`` entry.

    Parameters
    ----------
    page
        One member of the ``cPages.pages`` array.

    Returns
    -------
    PageIndexEntry
        The page's identity, its own template, and its redirection index.

    Raises
    ------
    TypeError
        If the entry is not a json object, or a member carries a json type this reader
        does not accept. ``redir`` is never one of them: see divergence 2.
    ValueError
        If the page id is absent or empty.
    """
    members = _members(page)
    if members is None:
        msg = f"expected a json object per page, got {type(page).__name__}"
        raise TypeError(msg)
    # `redir` is read both bare and enveloped, and never refused. `template` keeps the
    # strict envelope: a wrong type there really is a malformed sidecar, because a
    # template name has no positional fallback for a consumer to recover through.
    envelope = _members(members.get("redir"))
    redir = envelope.get("value") if envelope is not None else members.get("redir")
    return PageIndexEntry(
        page_uuid=_page_uuid(members.get("id")),
        template_name=_template(_wrapped_value(members.get("template"), where="template")),
        pdf_page_index=_page_offset(redir),
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
        If the value is not a json array.
    """
    if isinstance(value, list):
        return list(value)
    msg = f"expected a json array at {where}, got {type(value).__name__}"
    raise TypeError(msg)


def _wrapped_value(value: object, /, *, where: str) -> object:
    """Unwrap the ``{"value": ...}`` envelope firmware 3.x puts CRDT-stamped facts in.

    Parameters
    ----------
    value
        The raw json value of the wrapping member, or ``None`` when it was absent.
    where
        Name of the member, for the message.

    Returns
    -------
    object
        The wrapped value, or ``None`` when the envelope or its ``value`` was absent.

    Raises
    ------
    TypeError
        If the member is present and is not a json object.
    """
    if value is None:
        return None
    members = _members(value)
    if members is None:
        msg = f"expected a json object at {where}, got {type(value).__name__}"
        raise TypeError(msg)
    return members.get("value")


def _page_uuid(value: object, /) -> str:
    """Read a page identifier.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.

    Returns
    -------
    str
        The identifier verbatim, never normalised.

    Raises
    ------
    TypeError
        If the value is present and is not a json string.
    ValueError
        If the key was absent or the string is empty: a page with no identity cannot
        be addressed, ordered, or read off the store.
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
        The name, or ``None`` for an absent or empty one -- the domain's single
        spelling of "no template".

    Raises
    ------
    TypeError
        If the value is present and is not a json string.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"expected a json string template name, got {type(value).__name__}"
        raise TypeError(msg)
    return value or None


def _page_offset(value: object, /) -> int | None:
    """Read a zero-based source-pdf page index, refusing nothing.

    Total by construction: every json value this cannot read as a page index becomes
    ``None``. That is the whole of divergence 2's leniency, and it is why this reader
    raises nothing at all. ``None`` means "the redirection map named none", so a
    consumer falls back to the page's position and records
    ``DegradationKind.PDF_PAGE_INDEX_FALLBACK`` -- a page-local, recoverable state,
    where an exception here would have cost the entire document.

    Parameters
    ----------
    value
        The ``redir`` value, enveloped or bare, or ``None`` when the key was absent.

    Returns
    -------
    int | None
        The index, or ``None`` when the value is absent, negative, of a type no page
        index can be (``bool`` included -- json ``true`` is not page 1), a string that
        is not a number, or a float that has no integer value (``NaN``, an infinity).
        A negative value is read as "no redirection" rather than refused: this
        firmware writes ``-1`` for "automatic" in ``lineHeight``, and
        ``Page.pdf_page_index`` is constrained ``ge=0``. A numeric string is read as
        the number it spells, which widens over legacy -- see divergence 2.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    candidate = value.strip() if isinstance(value, str) else value
    try:
        index = int(candidate)
    except (ValueError, OverflowError):
        return None
    return None if index < 0 else index
