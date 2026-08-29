"""The ``.content`` page walk and the ``.pagedata`` template list.

Table-driven, because this module is a relocation of ``ContentInfo.from_json`` plus the
loader's template precedence and every row is one decision that was made in 2024 and must
still hold: both sidecar shapes, no filtering, positional template precedence, and the
three divergences the domain forced.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rmspec.formats.page_index import PageIndexEntry, decode_page_index, decode_pagedata


def content(**members: object) -> bytes:
    """Render a ``.content`` sidecar from json members."""
    return json.dumps(members).encode()


def cpages(*pages: object) -> bytes:
    """Render a firmware-3.x ``.content`` sidecar around a page list."""
    return content(formatVersion=2, cPages={"pages": list(pages)})


LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
"""Every character ``str.splitlines`` splits on, excluded where a name must be one line."""


# ─────────────────────────── the two sidecar shapes ───────────────────────────


def test_the_firmware_3x_cpages_shape_is_read_in_file_order():
    raw = cpages(
        {"id": "page-a", "template": {"value": "Lined"}},
        {"id": "page-b", "template": {"value": "Grid"}, "redir": {"value": 4}},
    )

    assert decode_page_index(raw) == (
        PageIndexEntry(page_uuid="page-a", template_name="Lined", pdf_page_index=None),
        PageIndexEntry(page_uuid="page-b", template_name="Grid", pdf_page_index=4),
    )


def test_the_pre_v2_flat_page_list_is_still_read():
    raw = content(formatVersion=1, pages=["page-a", "page-b"])

    assert [entry.page_uuid for entry in decode_page_index(raw)] == ["page-a", "page-b"]
    assert {entry.template_name for entry in decode_page_index(raw)} == {None}


def test_cpages_wins_when_a_sidecar_carries_both_shapes():
    raw = content(pages=["old"], cPages={"pages": [{"id": "new"}]})

    assert [entry.page_uuid for entry in decode_page_index(raw)] == ["new"]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(content(formatVersion=2), id="no page list at all"),
        pytest.param(cpages(), id="an empty cPages list"),
        pytest.param(content(pages=[]), id="an empty flat list"),
        pytest.param(content(cPages={}), id="cPages with no pages member"),
    ],
)
def test_a_sidecar_that_lists_no_pages_yields_no_pages(raw: bytes):
    assert decode_page_index(raw) == ()


def test_an_absent_sidecar_yields_no_pages_rather_than_failing():
    """A folder has no ``.content``; the legacy loader raised ``FileNotFoundError``."""
    assert decode_page_index(None) == ()


def test_an_entry_the_sidecar_marks_deleted_is_still_claimed():
    """Filtering would renumber every later page and shift the positional templates.

    ``ContentInfo.from_json`` appended every ``cPages`` entry unfiltered, and page
    position is the addressing scheme for ``Page.index``, for the ``.pagedata``
    alignment and for the source-pdf page. So a deleted marker changes nothing here.
    """
    raw = cpages(
        {"id": "page-a"},
        {"id": "page-b", "deleted": {"value": 1}},
        {"id": "page-c"},
    )

    assert [entry.page_uuid for entry in decode_page_index(raw)] == [
        "page-a",
        "page-b",
        "page-c",
    ]


# ─────────────────────────── templates ───────────────────────────


@pytest.mark.parametrize(
    ("templates", "own", "expected"),
    [
        pytest.param(("Lined",), None, "Lined", id="a pagedata line applies"),
        pytest.param(("Lined",), "Grid", "Lined", id="a pagedata line beats the entry"),
        pytest.param(("",), "Grid", None, id="an EMPTY pagedata line still beats the entry"),
        pytest.param((), "Grid", "Grid", id="no line leaves the entry's own template"),
        pytest.param((), None, None, id="neither means no template"),
        pytest.param((), "", None, id="an empty entry template is no template"),
        pytest.param((), "Blank", "Blank", id="a stored Blank is a real template name"),
    ],
)
def test_template_precedence_is_positional_and_legacy_exact(
    templates: tuple[str, ...], own: str | None, expected: str | None
):
    page: dict[str, object] = {"id": "page-a"}
    if own is not None:
        page["template"] = {"value": own}

    entries = decode_page_index(cpages(page), templates=templates)

    assert entries[0].template_name == expected


def test_the_legacy_blank_default_is_gone():
    """``PageRef.template`` defaulted to the literal ``"Blank"`` for an absent key.

    ``Page.template_name`` documents ``None`` as the only spelling of "no template" and
    ``"Blank"`` as a template the store really named, so the default cannot survive.
    """
    assert decode_page_index(cpages({"id": "page-a"}))[0].template_name is None


def test_a_pagedata_shorter_than_the_page_list_leaves_the_rest_alone():
    raw = cpages({"id": "a"}, {"id": "b", "template": {"value": "Grid"}}, {"id": "c"})

    entries = decode_page_index(raw, templates=("Lined",))

    assert [entry.template_name for entry in entries] == ["Lined", "Grid", None]


def test_a_pagedata_longer_than_the_page_list_is_harmless():
    entries = decode_page_index(cpages({"id": "a"}), templates=("Lined", "Grid", "Dots"))

    assert [entry.template_name for entry in entries] == ["Lined"]


# ─────────────────────────── the source-pdf page ───────────────────────────


@pytest.mark.parametrize(
    ("redir", "expected"),
    [
        pytest.param({"value": 0}, 0, id="page zero is a real page, not a falsy absence"),
        pytest.param({"value": 12}, 12, id="a plain integer"),
        pytest.param({"value": "7"}, 7, id="the store's quoted number"),
        pytest.param({"value": 3.0}, 3, id="a json float that is a whole number"),
        pytest.param({}, None, id="an envelope with no value"),
        pytest.param({"value": None}, None, id="an explicit null"),
        pytest.param({"value": -1}, None, id="the firmware's -1 means no redirection"),
        pytest.param(3, 3, id="a bare integer, the shape the legacy cli reader branched on"),
        pytest.param("7", 7, id="a bare quoted number"),
        pytest.param(-1, None, id="a bare -1"),
    ],
)
def test_the_redirection_index_is_read_as_a_zero_based_page(redir: object, expected: int | None):
    entries = decode_page_index(cpages({"id": "page-a", "redir": redir}))

    assert entries[0].pdf_page_index == expected


@pytest.mark.parametrize(
    "redir",
    [
        pytest.param(True, id="a bare json true, which is not page 1"),
        pytest.param({"value": True}, id="an enveloped boolean"),
        pytest.param("abc", id="a bare non-numeric string"),
        pytest.param({"value": "abc"}, id="an enveloped non-numeric string"),
        pytest.param(None, id="a bare null"),
        pytest.param([], id="a bare json array"),
        pytest.param({"value": []}, id="an enveloped json array"),
        pytest.param({"value": {"nested": 1}}, id="an enveloped object"),
        pytest.param(float("nan"), id="a json NaN, which json.loads accepts by default"),
        pytest.param(float("inf"), id="a json Infinity, which has no integer value"),
    ],
)
def test_an_unreadable_redirection_is_a_page_local_none_and_never_an_exception(redir: object):
    """The confirmed defect: a junk ``redir`` used to cost the whole document.

    ``_page_offset`` raised ``TypeError`` / ``ValueError``, which ``repository._page_list``
    translates to ``MalformedDocument``, which takes out ``load``, ``summary``,
    ``load_page`` and both fingerprint methods -- and makes ``list_documents`` drop the
    document entirely. Neither legacy reader did that: the formats reader never touched
    the key, and ``cli/_resolve.py`` accepted a bare int, accepted a bool, and fell back
    to the page's position for anything else.

    ``redir`` says nothing about page order, and the domain already has the vocabulary
    for "unknown": ``pdf_page_index=None`` plus
    ``DegradationKind.PDF_PAGE_INDEX_FALLBACK`` for the consumer that then counts.
    """
    entries = decode_page_index(cpages({"id": "page-a", "redir": redir}))

    assert entries[0].pdf_page_index is None
    assert entries[0].page_uuid == "page-a", "the id, the position and the template survive"


def test_the_legacy_redirect_key_is_not_read():
    """Legacy read ``redirect``; firmware 3.x writes ``redir``.

    So ``PageRef.redirect`` was always ``None`` on every real document. Reading the key
    the firmware writes is a deliberate divergence, and the legacy spelling is left
    alone rather than accepted as an alias -- it carried a uuid string, not a page index.
    """
    entries = decode_page_index(cpages({"id": "page-a", "redirect": {"value": 4}}))

    assert entries[0].pdf_page_index is None


# ─────────────────────────── identifiers ───────────────────────────


def test_a_page_identifier_is_carried_verbatim_and_never_normalised():
    """The legacy ``UUID(p["id"])`` round trip normalised case and grouping.

    ``models.py`` records that as a defect: an identity read from the device stopped
    comparing equal to the same identity read from a cache row. It is also what builds
    the ``PAGE.rm`` filename, so normalising it would look for a file that is not there.
    """
    raw = cpages({"id": "AB-CD-EF"})

    assert decode_page_index(raw)[0].page_uuid == "AB-CD-EF"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(cpages({"template": {"value": "Lined"}}), id="an entry with no id"),
        pytest.param(cpages({"id": ""}), id="an entry with an empty id"),
        pytest.param(cpages({"id": None}), id="an explicitly null id"),
    ],
)
def test_a_page_with_no_usable_identity_is_a_value_error(raw: bytes):
    with pytest.raises(ValueError, match="id"):
        decode_page_index(raw)


# ─────────────────────────── malformed shapes ───────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"[]", id="valid json that is not an object"),
        pytest.param(cpages({"id": 4}), id="a numeric page id"),
        pytest.param(content(cPages={"pages": {"id": "a"}}), id="a page list that is an object"),
        pytest.param(content(pages="page-a"), id="a flat page list that is a string"),
        pytest.param(cpages({"id": "a", "template": "Lined"}), id="an unwrapped template"),
        pytest.param(cpages({"id": "a", "template": {"value": 7}}), id="a numeric template"),
        pytest.param(cpages("page-a"), id="a page entry that is a bare string"),
    ],
)
def test_a_shape_this_reader_does_not_accept_is_a_type_error(raw: bytes):
    """``template`` stays strict; ``redir`` does not.

    The asymmetry is the point. A wrong-typed ``template`` genuinely is a malformed
    sidecar -- there is no positional fallback for a template name, so nothing downstream
    can recover from silently dropping it. A wrong-typed ``redir`` has one, so it is a
    page-local ``None``; see the leniency table above.
    """
    with pytest.raises(TypeError):
        decode_page_index(raw)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        pytest.param(b"", "Expecting value", id="empty bytes"),
        pytest.param(b"{not json", "Expecting property name", id="truncated json"),
    ],
)
def test_payload_that_is_not_readable_json_is_a_value_error(raw: bytes, match: str):
    with pytest.raises(ValueError, match=match):
        decode_page_index(raw)


# ─────────────────────────── .pagedata ───────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"Blank\nLined\n", ("Blank", "Lined"), id="a trailing newline"),
        pytest.param(b"Blank\nLined", ("Blank", "Lined"), id="no trailing newline"),
        pytest.param(b"", (), id="an empty file"),
        pytest.param(b"\n\n  \n", (), id="a file of whitespace only"),
        pytest.param(b"a\n\nb", ("a", "", "b"), id="an interior blank line keeps its position"),
        pytest.param(b"Grid (small)", ("Grid (small)",), id="a name with spaces and parentheses"),
        pytest.param(b"\nLined\n", ("Lined",), id="a leading blank line is stripped"),
    ],
)
def test_pagedata_lines_are_read_exactly_as_the_legacy_reader_read_them(
    raw: bytes, expected: tuple[str, ...]
):
    assert decode_pagedata(raw) == expected


def test_the_whole_text_strip_is_load_bearing():
    """Stripping shifts the positional alignment, which is why it is kept verbatim.

    A leading blank line means the first template belongs to page 0, not page 1. That is
    what the legacy reader did, and the templates are applied by position, so relaxing
    the strip would silently re-template every page of every document.
    """
    templates = decode_pagedata(b"\nLined\nGrid\n")

    entries = decode_page_index(
        content(cPages={"pages": [{"id": "a"}, {"id": "b"}]}), templates=templates
    )

    assert [entry.template_name for entry in entries] == ["Lined", "Grid"]


def test_pagedata_that_is_not_utf8_is_a_value_error():
    with pytest.raises(UnicodeDecodeError):
        decode_pagedata(b"\xff\xfe invalid")


@given(
    names=st.lists(
        st.text(
            alphabet=st.characters(codec="utf-8", exclude_characters=LINE_BREAKS), min_size=1
        ).filter(lambda name: name == name.strip()),
        min_size=1,
        max_size=8,
    )
)
def test_a_one_line_template_name_round_trips_exactly(names: list[str]):
    """No name can be lost, split, or merged: the list is the store's page alignment.

    Names are drawn already stripped, because the whole-text ``strip()`` is deliberate and
    tested directly above: a first or last line of pure whitespace really does vanish.
    """
    lines = decode_pagedata("\n".join(names).encode())

    assert lines == tuple(names)
