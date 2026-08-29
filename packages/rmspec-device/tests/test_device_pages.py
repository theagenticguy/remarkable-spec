"""The narrowed ``.content`` page-order walk.

Two jobs. The first is the ordinary one: every branch of ``_pages.decode_page_order``,
including both sidecar shapes and the four ways a template can be spelled.

The second is the reason this suite exists at all. ``_pages.decode_page_order`` is a
narrower sibling of ``rmspec.formats.page_index.decode_page_index`` and ``rmspec.device``
may not import ``rmspec.formats``, so the two readers can drift. The expectations below are
**re-derived** from the sibling's documented rules rather than imported from it -- an
import would be the very dependency the split exists to prevent, and would also make this
suite pass whenever the two agree on something wrong. Folding both onto one reader is a
step 7 item; until then these rows are the contract between them.

Fixtures are synthesised: a real ``.content`` names the user's templates and page
identifiers, and nothing from the device is committed.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rmspec.device._pages import PageOrderEntry, decode_page_order


def content(**members: object) -> bytes:
    """Render a ``.content`` sidecar from json members."""
    return json.dumps(members).encode()


def cpages(*pages: object) -> bytes:
    """Render the firmware-3.x sidecar shape: ``cPages.pages[]`` under ``formatVersion 2``.

    Measured on 39 of 40 real ``.content`` files. Real entries also carry ``idx``,
    ``scrollTime`` and ``verticalScroll``, and PDF-backed ones carry ``redir``; none of the
    four is read here.
    """
    return content(formatVersion=2, cPages={"pages": list(pages)})


def crdt(value: object) -> dict[str, object]:
    """Wrap a value in the ``{"timestamp", "value"}`` envelope firmware 3.x stamps facts with."""
    return {"timestamp": "1:2", "value": value}


# ─────────────────────────────── the two sidecar shapes ───────────────────────────────


def test_the_firmware_3x_cpages_shape_is_read_in_file_order():
    raw = cpages(
        {"id": "page-a", "idx": crdt("ba"), "template": crdt("Lined")},
        {"id": "page-b", "idx": crdt("bb"), "template": crdt("Grid")},
    )

    assert decode_page_order(raw) == (
        PageOrderEntry(page_id="page-a", template_name="Lined"),
        PageOrderEntry(page_id="page-b", template_name="Grid"),
    )


def test_the_pre_v2_flat_page_list_of_bare_uuid_strings_is_still_read():
    """Present on exactly 1 of 40 real sidecars, and never a sibling of ``cPages``."""
    raw = content(formatVersion=1, pages=["page-a", "page-b"])

    assert decode_page_order(raw) == (
        PageOrderEntry(page_id="page-a", template_name=None),
        PageOrderEntry(page_id="page-b", template_name=None),
    )


def test_cpages_wins_when_a_sidecar_somehow_carries_both_shapes():
    """Re-derived from the sibling: ``cPages`` is checked first and the flat list ignored."""
    raw = content(pages=["old"], cPages={"pages": [{"id": "new"}]})

    assert [entry.page_id for entry in decode_page_order(raw)] == ["new"]


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
    assert decode_page_order(raw) == ()


def test_an_absent_sidecar_yields_no_pages_rather_than_failing():
    """A folder has no ``.content``, and the walk must still be able to visit it."""
    assert decode_page_order(None) == ()


def test_the_ordering_and_scroll_keys_real_entries_carry_are_ignored():
    """``idx`` is the CRDT order key; the array's order is what this reader trusts."""
    raw = cpages(
        {"id": "second", "idx": crdt("bz"), "scrollTime": crdt("0"), "verticalScroll": crdt(0)},
        {"id": "first", "idx": crdt("ba"), "redir": crdt(4)},
    )

    assert [entry.page_id for entry in decode_page_order(raw)] == ["second", "first"]


# ─────────────────────────────── templates ───────────────────────────────


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        pytest.param({"id": "p"}, None, id="an absent template key"),
        pytest.param({"id": "p", "template": crdt("")}, None, id="an empty template name"),
        pytest.param({"id": "p", "template": {}}, None, id="an envelope with no value"),
        pytest.param({"id": "p", "template": crdt(None)}, None, id="an explicit null value"),
        pytest.param({"id": "p", "template": crdt("Blank")}, "Blank", id="a stored Blank"),
        pytest.param(
            {"id": "p", "template": crdt("P Grid small")},
            "P Grid small",
            id="a name with spaces",
        ),
    ],
)
def test_the_legacy_blank_default_is_gone_and_a_stored_blank_survives(
    page: dict[str, object], expected: str | None
):
    """``PageRef.template`` defaulted to ``"Blank"``; ``None`` is now the only "no template".

    Re-derived from the sibling's divergence 1: an absent key and an empty string both
    become ``None``, and a sidecar that really did record a template called Blank keeps it.
    """
    assert decode_page_order(cpages(page)) == (
        PageOrderEntry(page_id="p", template_name=expected),
    )


def test_pagedata_is_not_consulted_even_though_a_mirror_reader_would():
    """The archive has no ``.pagedata`` member, and the sidecar is non-authoritative.

    Measured: both real ``.pagedata`` files are 6 bytes holding ``"Blank"``, one of them
    against a 432-page document, and on the 1-page one the real template is
    ``"P Grid small"``. So the entry's own ``template.value`` is the only source, and a
    ``pagedata`` member in the json changes nothing.
    """
    raw = content(
        formatVersion=2,
        pagedata=["Blank"],
        cPages={"pages": [{"id": "p", "template": crdt("P Grid small")}]},
    )

    assert decode_page_order(raw)[0].template_name == "P Grid small"


# ─────────────────────────────── identity ───────────────────────────────


@pytest.mark.parametrize(
    "page_id",
    [
        pytest.param("A1B2C3D4-1111-2222-3333-444444444444", id="upper-case uuid"),
        pytest.param("not-a-uuid-at-all", id="not a uuid"),
        pytest.param("  padded  ", id="surrounding whitespace"),
    ],
)
def test_a_page_id_is_carried_verbatim_and_never_through_uuid(page_id: str):
    """The legacy ``UUID(...)`` round trip both rejected and case-normalised identities."""
    assert decode_page_order(cpages({"id": page_id}))[0].page_id == page_id


def test_nothing_is_filtered_including_an_entry_the_sidecar_marks_deleted():
    """Filtering would renumber every later page, and position is the addressing scheme."""
    raw = cpages({"id": "a"}, {"id": "b", "deleted": crdt(1)}, {"id": "c"})

    assert [entry.page_id for entry in decode_page_order(raw)] == ["a", "b", "c"]


def test_a_repeated_id_is_dropped_after_its_first_occurrence():
    """``DocumentSourceBundle`` refuses a duplicated ``page_id``, so one cannot pass here."""
    raw = cpages(
        {"id": "a", "template": crdt("Lined")},
        {"id": "b"},
        {"id": "a", "template": crdt("Grid")},
        {"id": "b"},
    )

    assert decode_page_order(raw) == (
        PageOrderEntry(page_id="a", template_name="Lined"),
        PageOrderEntry(page_id="b", template_name=None),
    )


def test_a_repeated_id_in_the_flat_shape_is_dropped_too():
    raw = content(pages=["a", "b", "a"])

    assert [entry.page_id for entry in decode_page_order(raw)] == ["a", "b"]


# ─────────────────────────────── failure vocabulary ───────────────────────────────


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        pytest.param(b"[]", "expected a json object", id="a json array payload"),
        pytest.param(b'"text"', "expected a json object", id="a json string payload"),
        pytest.param(b"7", "expected a json object", id="a json number payload"),
        pytest.param(
            content(cPages={"pages": {"id": "p"}}),
            "expected a json array at cPages.pages",
            id="cPages.pages is an object",
        ),
        pytest.param(
            content(pages="page-a"),
            "expected a json array at pages",
            id="the flat list is a string",
        ),
        pytest.param(cpages("page-a"), "expected a json object per page", id="a bare string page"),
        pytest.param(
            cpages({"id": "p", "template": "Lined"}),
            "expected a json object at template",
            id="an unenveloped template",
        ),
        pytest.param(
            cpages({"id": "p", "template": crdt(7)}),
            "expected a json string template name",
            id="a numeric template name",
        ),
        pytest.param(
            cpages({"id": 7}),
            "expected a json string page id",
            id="a numeric page id",
        ),
    ],
)
def test_a_value_of_the_wrong_json_type_raises_typeerror(raw: bytes, fragment: str):
    with pytest.raises(TypeError, match=fragment):
        decode_page_order(raw)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        pytest.param(cpages({"template": crdt("Lined")}), "page entry has no id", id="no id key"),
        pytest.param(cpages({"id": None}), "page entry has no id", id="an explicit null id"),
        pytest.param(cpages({"id": ""}), "page entry has an empty id", id="an empty id"),
        pytest.param(content(pages=[""]), "page entry has an empty id", id="an empty flat id"),
    ],
)
def test_a_value_that_cannot_be_read_raises_valueerror(raw: bytes, fragment: str):
    with pytest.raises(ValueError, match=fragment):
        decode_page_order(raw)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"{not json", id="a truncated object"),
        pytest.param(b"", id="an empty payload"),
    ],
)
def test_invalid_json_raises_a_valueerror_and_not_a_typeerror(raw: bytes):
    """The caller maps both to one domain error, but the split is the domain's rule.

    ``json.JSONDecodeError`` is a ``ValueError``, so the promise in the ``Raises`` section
    holds without this module catching and re-raising anything.
    """
    with pytest.raises(ValueError, match="Expecting") as raised:
        decode_page_order(raw)

    assert isinstance(raised.value, json.JSONDecodeError)


# ─────────────────────────────── the order property ───────────────────────────────

_TEMPLATE_SHAPES = st.sampled_from(
    [
        {},
        {"template": {}},
        {"template": {"timestamp": "1:2", "value": ""}},
        {"template": {"timestamp": "1:2", "value": "Blank"}},
        {"template": {"value": "P Grid small"}},
    ]
)
"""Every template spelling a real ``cPages`` entry can carry, none of them refusable."""


@given(
    entries=st.lists(
        st.tuples(st.sampled_from(["a", "b", "c", "d"]), _TEMPLATE_SHAPES),
        max_size=12,
    )
)
def test_the_order_is_always_a_duplicate_free_subsequence_of_the_ids_present(
    entries: list[tuple[str, dict[str, object]]],
):
    """For any ``cPages`` shape: no duplicate, no invention, no reordering.

    ``dict.fromkeys`` is exactly "first occurrence wins, order otherwise untouched", so
    comparing against it asserts all three properties at once.
    """
    pages = [{"id": page_id, **shape} for page_id, shape in entries]

    order = decode_page_order(json.dumps({"cPages": {"pages": pages}}).encode())

    ids = [entry.page_id for entry in order]
    assert ids == list(dict.fromkeys(page_id for page_id, _ in entries))
    assert len(set(ids)) == len(ids)
