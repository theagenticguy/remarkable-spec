"""The ``SceneAppender`` adapter: ink onto a page that already exists.

Over real v6 bytes written by ``rmscene``'s own writer, like the codec suite next door, so
no binary fixture is committed and no device is involved.

The load-bearing assertion, and the one place this suite departs from its prior art
-----------------------------------------------------------------------------------
``remarkable-mcp`` guards the same append-only design with a pair of assertions: that the
original bytes survive as an exact prefix, **and** that a naive ``read_blocks`` ->
``write_blocks`` round trip does *not* byte-match. The second half is what proves the first
is measuring the property rather than passing by luck, because their firmware makes the naive
rewrite lossy.

**It is not expressible here.** Measured on firmware 3.27.3.0 with ``rmscene`` 0.7.0: the
naive round trip is byte-identical on all 30 non-empty artifacts of the reference corpus and
on every fixture below, so there is nothing for the prefix to differ from. This suite
therefore pins the same fact from the other side --
:func:`test_an_append_is_byte_identical_to_a_full_re_encode_of_the_same_blocks` asserts the
append's output **equals** a full re-encode -- and separately keeps the prefix honest by
asserting the input is a *strict* prefix, since ``startswith`` on an unchanged file is
vacuous. If a firmware or parser change ever makes the full re-encode lossy, the equality
assertion moves and says so, rather than the append-only shape quietly absorbing it.
"""

from __future__ import annotations

import dataclasses
import io
import logging
import os
import pathlib
import struct
from typing import TYPE_CHECKING

import formats_fixtures as ff
import pytest
from hypothesis import given
from hypothesis import strategies as st
from rmscene import scene_items as si
from rmscene.scene_stream import read_blocks, write_blocks
from rmscene.tagged_block_common import CrdtId

from rmspec.domain.errors import CorruptPageData, SceneRewriteUnsafe, UsageError
from rmspec.domain.models import PenColor, PenType, Point, Rgba, Stroke
from rmspec.formats import AppendOnlySceneWriter, SceneCodec
from rmspec.formats import scene_codec as codec_module

if TYPE_CHECKING:
    from rmspec.domain.ports.formats import SceneAppender

PAGE = "page-1"


def writer() -> SceneAppender:
    """Return the adapter, annotated as the port it implements."""
    return AppendOnlySceneWriter()


def pen_stroke(*, x: float = -100.0, y: float = 1000.0) -> Stroke:
    """Return one two-sample fineliner stroke, the shape a traced glyph contour is."""
    return Stroke(
        pen=PenType.FINELINER_2,
        color=PenColor.BLACK,
        thickness_scale=2.0,
        starting_length=0.25,
        points=(
            Point(x=x, y=y, speed=120, direction=30, width=900, pressure=200),
            Point(x=x + 40.0, y=y),
        ),
    )


def highlight_stroke(*, colour: Rgba) -> Stroke:
    """Return one highlighter stroke carrying its own colour, as the firmware writes it."""
    return Stroke(
        pen=PenType.HIGHLIGHTER_2,
        color=PenColor.HIGHLIGHT,
        thickness_scale=3.0,
        color_override=colour,
        points=(Point(x=0.0, y=1010.0),),
    )


# ─────────────────────────── the shape of the result ───────────────────────────


def test_the_original_bytes_are_a_strict_prefix_of_the_result():
    """The whole of choice (b), and the reason the existing ink cannot be damaged.

    ``startswith`` alone would pass for an append that added nothing, so the length is
    asserted to have grown as well: a vacuous prefix assertion is exactly the failure the
    prior art's second half was guarding against.
    """
    raw = ff.inked_scene()

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert edit.scene.startswith(raw)
    assert len(edit.scene) > len(raw)


def test_an_append_is_byte_identical_to_a_full_re_encode_of_the_same_blocks():
    """The assertion that replaces prior art's "a naive round trip differs".

    It cannot hold here, because the round trip does not differ -- so this pins the equality
    instead. A block's serialisation is self-contained, so once the lossless precondition
    passes, ``original + new`` and ``raw + tail`` are the same bytes. The day that stops being
    true on some firmware, this fails and names it.
    """
    raw = ff.inked_scene()
    ink = (pen_stroke(), highlight_stroke(colour=Rgba(r=190, g=234, b=254)))

    edit = writer().append_strokes(raw, PAGE, strokes=ink)

    blocks = list(read_blocks(io.BytesIO(raw)))
    site = codec_module._append_site(blocks, PAGE)
    minted = codec_module._line_blocks(
        ink, site=site, author_id=codec_module._fresh_author_id(blocks, PAGE)
    )
    buffer = io.BytesIO()
    write_blocks(buffer, [*blocks, *minted])

    assert buffer.getvalue() == edit.scene


def test_the_result_decodes_to_the_original_ink_plus_the_new_ink_in_order():
    raw = ff.inked_scene()
    before = SceneCodec().decode_page(raw, PAGE)

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(), pen_stroke(x=200.0)))
    after = SceneCodec().decode_page(edit.scene, PAGE)

    assert after.stroke_count == before.stroke_count + 2
    strokes = after.layers[edit.layer_index].strokes
    assert strokes[: before.stroke_count] == before.layers[0].strokes
    assert [point.x for point in strokes[-2].points] == [-100.0, -60.0]
    assert [point.x for point in strokes[-1].points] == [200.0, 240.0]


def test_appending_records_no_new_defect_on_the_page():
    """The appended blocks must not look degraded to the reader that decodes them next.

    The colour field is the case that could: it is bytes ``rmscene`` does not read, and the
    codec reports unread bytes as ``BLOCK_BYTES_UNREAD``. It stays quiet because this writer
    spells the field the codec decodes.
    """
    raw = ff.inked_scene()

    edit = writer().append_strokes(
        raw, PAGE, strokes=(highlight_stroke(colour=Rgba(r=255, g=237, b=117)),)
    )

    assert SceneCodec().decode_page(edit.scene, PAGE).defects == ()


def test_every_stroke_field_survives_the_append_and_comes_back_out():
    raw = ff.inked_scene()
    stroke = pen_stroke()

    edit = writer().append_strokes(raw, PAGE, strokes=(stroke,))
    written = SceneCodec().decode_page(edit.scene, PAGE).layers[0].strokes[-1]

    assert written == stroke


@pytest.mark.parametrize(
    "colour",
    [
        pytest.param(Rgba(r=255, g=237, b=117, a=255), id="the measured yellow"),
        pytest.param(Rgba(r=190, g=234, b=254, a=255), id="the measured blue"),
        pytest.param(Rgba(r=0, g=0, b=0, a=0), id="every channel at zero"),
        pytest.param(Rgba(r=255, g=255, b=255, a=255), id="every channel at full scale"),
    ],
)
def test_a_highlighters_own_colour_survives_the_round_trip(colour: Rgba):
    """Without this a page rewritten through here loses colour a reader can see.

    Every highlight reports ``PenColor`` id 9 whatever colour it was drawn in, so the index
    cannot carry the answer: four visibly different highlights decode identical unless the
    per-stroke field is written back.
    """
    edit = writer().append_strokes(
        ff.inked_scene(), PAGE, strokes=(highlight_stroke(colour=colour),)
    )

    written = SceneCodec().decode_page(edit.scene, PAGE).layers[0].strokes[-1]
    assert written.color_override == colour
    assert written.color is PenColor.HIGHLIGHT


def test_a_pen_stroke_writes_no_colour_field_at_all():
    """``None`` is "the colour index is the whole truth", not "black" -- so nothing is added.

    Asserted through the decode rather than on bytes: a spurious field would come back as a
    colour override, and any other stray tail would come back as an unread-bytes defect.
    """
    edit = writer().append_strokes(ff.inked_scene(), PAGE, strokes=(pen_stroke(),))
    content = SceneCodec().decode_page(edit.scene, PAGE)

    assert content.layers[0].strokes[-1].color_override is None
    assert content.defects == ()


def test_a_stroke_with_no_samples_is_a_tap_and_is_written():
    stroke = Stroke(pen=PenType.BALLPOINT_2, color=PenColor.RED, thickness_scale=1.0)

    edit = writer().append_strokes(ff.inked_scene(), PAGE, strokes=(stroke,))

    assert SceneCodec().decode_page(edit.scene, PAGE).layers[0].strokes[-1] == stroke


# ─────────────────────────── the receipt ───────────────────────────


def test_the_fresh_author_id_is_one_past_the_highest_the_artifact_uses():
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, author=7, items=(ff.stroke_item(),)))

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert edit.author_id == 8


def test_the_author_maximum_is_not_taken_over_the_author_directory_alone():
    """The design draft said ``max`` over the ``AuthorIdsBlock``, and the corpus refutes it.

    Every fixture and all 30 corpus artifacts declare only author 1 in the directory while
    carrying ids under author 0, so the directory is already not the set of authors in use.
    Here the layer sits at author 9, above anything the directory mentions, and a
    directory-only maximum would hand out 2 -- which after one append is the id the previous
    append's own ink is under.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, author=9, items=(ff.stroke_item(),)))

    first = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))
    second = writer().append_strokes(first.scene, PAGE, strokes=(pen_stroke(),))

    assert (first.author_id, second.author_id) == (10, 11)


def test_two_appends_in_a_row_mint_disjoint_ids_and_keep_both_strokes():
    raw = ff.inked_scene()

    first = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))
    second = writer().append_strokes(first.scene, PAGE, strokes=(pen_stroke(x=300.0),))

    assert second.scene.startswith(first.scene)
    assert first.author_id != second.author_id
    content = SceneCodec().decode_page(second.scene, PAGE)
    assert content.stroke_count == 3
    assert content.defects == ()
    assert [point.x for point in content.layers[0].strokes[-1].points] == [300.0, 340.0]


def test_the_reported_layer_index_is_the_layer_a_decode_of_the_result_reports():
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, name="under", items=(ff.stroke_item(),)),
        *ff.layer_blocks(node=12, name="over"),
    )

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    content = SceneCodec().decode_page(edit.scene, PAGE)
    assert edit.layer_index == 1
    assert content.layers[edit.layer_index].name == "over"
    assert len(content.layers[edit.layer_index].strokes) == 1
    assert len(content.layers[0].strokes) == 1


def test_ink_goes_to_the_last_visible_layer_and_never_to_a_hidden_one():
    """Ink on a hidden layer is ink the human cannot see, which is the defect being avoided."""
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, name="visible"),
        *ff.layer_blocks(node=12, name="hidden", visible=False),
    )

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert edit.layer_index == 0
    assert SceneCodec().decode_page(edit.scene, PAGE).layers[0].name == "visible"


def test_new_ink_draws_last_within_its_layer_even_after_a_tombstone():
    """An erased stroke keeps its ordering position, so the last member includes tombstones."""
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, items=(ff.stroke_item(),)),
        ff.tombstone_block(node=11, index=1150),
    )

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    content = SceneCodec().decode_page(edit.scene, PAGE)
    assert content.stroke_count == 2, "the tombstone is still not a stroke"
    assert content.defects == (), "and it is still not a defect"
    assert [point.x for point in content.layers[0].strokes[-1].points] == [-100.0, -60.0]


def test_a_page_that_already_holds_typed_text_keeps_it_and_gains_the_ink():
    """The page-scoped text block is in the prefix, so it survives without being understood.

    It is also the one shape whose ids live inside a CRDT *sequence* -- a text block's members
    -- so this is what proves the author walk descends into one rather than stopping at the
    container.
    """
    raw = ff.texted_scene()
    before = SceneCodec().decode_page(raw, PAGE)
    assert before.text_blocks, "the fixture is only useful if the reader sees the text"

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))
    after = SceneCodec().decode_page(edit.scene, PAGE)

    assert after.text_blocks == before.text_blocks
    assert after.stroke_count == before.stroke_count + 1
    assert after.defects == ()


def test_an_empty_layer_accepts_ink_with_nothing_to_chain_it_onto():
    raw = ff.scene_bytes(*ff.layer_blocks(node=11))

    edit = writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert SceneCodec().decode_page(edit.scene, PAGE).stroke_count == 1


def test_the_receipt_carries_only_what_the_writer_decided():
    """No field restates the request, so nothing invites checking the request for the result."""
    edit = writer().append_strokes(ff.inked_scene(), PAGE, strokes=(pen_stroke(),))

    assert set(type(edit).model_fields) == {"scene", "author_id", "layer_index"}
    assert type(edit).model_config.get("frozen") is True


# ─────────────────────────── the refusals ───────────────────────────


def test_appending_nothing_is_refused_rather_than_reported_as_a_successful_write():
    """A no-op write still costs a transport round trip, a snapshot and a file rewrite.

    Reporting success for that is worse than refusing, because the caller learns nothing
    about why their ink did not appear.
    """
    with pytest.raises(UsageError) as caught:
        writer().append_strokes(ff.inked_scene(), PAGE, strokes=())

    assert PAGE in caught.value.subject
    assert caught.value.requirement == "at least one stroke"


def test_an_empty_stroke_tuple_is_refused_before_the_artifact_is_even_parsed():
    """The caller's mistake is reported as the caller's mistake, whatever the bytes are."""
    with pytest.raises(UsageError):
        writer().append_strokes(ff.TRUNCATED_SCENE, PAGE, strokes=())


def test_a_zero_byte_artifact_is_refused_by_the_shared_precondition():
    """62 of the corpus's 92 files are these. Writing one creates a scene, which is not this."""
    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(b"", PAGE, strokes=(pen_stroke(),))

    assert "zero-byte" in caught.value.detail


def test_bytes_that_do_not_decode_are_corrupt_page_data_and_not_a_writer_problem():
    with pytest.raises(CorruptPageData) as caught:
        writer().append_strokes(ff.TRUNCATED_SCENE, PAGE, strokes=(pen_stroke(),))

    assert not isinstance(caught.value, SceneRewriteUnsafe)
    assert isinstance(caught.value.__cause__, EOFError)


def test_a_page_this_build_cannot_re_encode_is_refused_before_any_ink_is_derived():
    """The precondition guards the read as much as the write.

    A scene this build cannot reproduce is one whose layer and author ids it has no business
    trusting either, so nothing is derived from them at all.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11), version="3.0")

    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert str(len(raw)) in caught.value.detail


def test_a_page_with_no_layer_group_is_refused_rather_than_given_an_invented_one():
    """The deliberate asymmetry with the reader, and the reason it is deliberate.

    ``decode_page`` invents a layer to report items that hang off the root, because a reader
    has to report them somehow. Inventing one here would mean writing tree and group blocks
    -- creating structure in someone else's page rather than adding to it.
    """
    raw = ff.rootless_scene(ff.stroke_item())
    assert SceneCodec().decode_page(raw, PAGE).stroke_count == 1, "the reader does invent one"

    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert "0 layer group(s)" in caught.value.detail


def test_a_page_whose_every_layer_is_hidden_is_refused():
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, visible=False))

    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert "none of them visible" in caught.value.detail


def test_a_layer_whose_members_do_not_order_is_corrupt_rather_than_appended_to():
    """The sort happens when the sequence is read, so it has to be forced inside the guard.

    These bytes parse and re-encode exactly; only asking what order the strokes draw in
    fails, which is why the failure has to be caught where the order is read rather than left
    for the caller's own iteration.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11), *ff.cyclic_sequence_blocks(node=11))
    SceneCodec().check_rewritable(raw, PAGE)

    with pytest.raises(CorruptPageData) as caught:
        writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert caught.value.__cause__ is not None
    assert not any(cls.__module__.startswith("rmscene") for cls in type(caught.value).__mro__)


def test_a_block_that_did_not_read_is_refused_because_its_ids_are_opaque():
    """A fresh author id is only collision-free if every id in use can be seen.

    An unreadable block's ids are inside bytes nothing decoded, so freedom cannot be shown --
    and this artifact round-trips exactly, so the shared precondition lets it through.
    """
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, items=(ff.stroke_item(),)), ff.unreadable_block()
    )
    SceneCodec().check_rewritable(raw, PAGE)

    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert str(ff.UNKNOWN_BLOCK_TYPE) in caught.value.detail
    assert "did not read" in caught.value.detail


def test_a_page_that_already_uses_the_highest_encodable_author_id_is_refused():
    """One past 255 does not fit the uint8 the wire format writes an author component as.

    Refused by name rather than left to raise out of the writer after the caller has been
    told the append is going ahead.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, author=255, items=(ff.stroke_item(),)))

    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(raw, PAGE, strokes=(pen_stroke(),))

    assert "author id 255" in caught.value.detail


def test_a_sample_the_writer_cannot_pack_is_refused_and_chains_the_cause():
    """The caller's own values can fail, and ``Point`` constrains x and y not at all.

    A coordinate beyond a 32-bit float is a constructible domain value, so the serialise step
    has to translate the ``struct`` failure rather than let it escape.
    """
    stroke = Stroke(
        pen=PenType.FINELINER_1,
        color=PenColor.BLACK,
        thickness_scale=1.0,
        points=(Point(x=1e300, y=0.0),),
    )

    with pytest.raises(SceneRewriteUnsafe) as caught:
        writer().append_strokes(ff.inked_scene(), PAGE, strokes=(stroke,))

    assert isinstance(caught.value.__cause__, struct.error | OverflowError)
    assert "1 new block(s)" in caught.value.detail
    assert not any(cls.__module__.startswith("rmscene") for cls in type(caught.value).__mro__)


def test_nothing_is_written_to_the_parser_logger_and_its_level_is_restored():
    logger = logging.getLogger("rmscene")
    before = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        writer().append_strokes(ff.inked_scene(), PAGE, strokes=(pen_stroke(),))
        assert logger.level == logging.DEBUG
    finally:
        logger.setLevel(before)


# ─────────────────────────── the encoder's own pieces ───────────────────────────


def test_the_serialised_header_is_the_v6_header_and_is_what_a_real_artifact_opens_with():
    """The strip width is derived from the writer, and this is the pin that keeps it honest.

    Production code slices off ``len(_serialise(()))`` with no runtime check, because the only
    way that is wrong is if ``write_blocks`` stopped writing its header first. This asserts
    the three facts that makes true: what the header is, how long it is, and that a real
    artifact begins with exactly it.
    """
    header = codec_module._serialise(())

    assert header == ff.VALID_HEADER
    assert len(header) == 43
    assert ff.inked_scene().startswith(header)


def test_a_serialised_tail_carries_no_header_of_its_own():
    blocks = codec_module._line_blocks(
        (pen_stroke(),),
        site=codec_module._AppendSite(
            layer_index=0, parent_id=CrdtId(0, 11), left_id=CrdtId(0, 0)
        ),
        author_id=2,
    )

    tail = codec_module._serialise_tail(blocks, PAGE)

    assert tail
    assert not tail.startswith(ff.VALID_HEADER)
    assert codec_module._serialise(blocks) == ff.VALID_HEADER + tail


@given(index=st.integers(min_value=0, max_value=0xFFF), tag_type=st.integers(0, 0xF))
def test_a_written_tag_header_reads_back_as_the_index_and_tag_it_encoded(
    index: int, tag_type: int
):
    """The writer is the reader's inverse over a range wider than the format uses today.

    The header is a varuint, so its width is data: index 8 is two bytes, index 0 is one, and
    an index above 255 is three. Hardcoding ``84 01`` would be right for exactly one field.
    """
    encoded = codec_module._write_tag(index, tag_type)

    assert codec_module._read_tag(encoded) == (index, tag_type, len(encoded))


def test_the_measured_colour_field_is_encoded_exactly_as_the_firmware_wrote_it():
    """Both measured tails, byte for byte, and the header's two-byte width is the point.

    ``(8 << 4) | 4 == 132``, whose low seven bits set the continuation bit, so a one-byte
    header is impossible for this field and a reader that assumed one mis-slices the payload.
    """
    yellow = codec_module._encode_color_override(Rgba(r=255, g=237, b=117, a=255))
    blue = codec_module._encode_color_override(Rgba(r=190, g=234, b=254, a=255))

    assert yellow == ff.MEASURED_YELLOW_TAIL
    assert blue == ff.MEASURED_BLUE_TAIL
    assert codec_module._encode_color_override(None) == ff.NO_TAIL


@given(r=st.integers(0, 255), g=st.integers(0, 255), b=st.integers(0, 255), a=st.integers(0, 255))
def test_an_encoded_colour_decodes_back_to_itself(r: int, g: int, b: int, a: int):
    colour = Rgba(r=r, g=g, b=b, a=a)

    encoded = codec_module._encode_color_override(colour)

    assert codec_module._decode_color_override(encoded) == (colour, len(encoded))


def test_the_author_walk_sees_every_position_an_id_can_hide_in():
    """The walk is structural rather than a per-block-type list, and this is why.

    Four different authors, each reachable only through a different field: the directory, a
    layer's node id, an item id, and a nested group's own node id. A hand-written enumeration
    that forgot any one of them would mint a colliding id and nothing would say so.
    """
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, author=3, items=(ff.stroke_item(),)),
        *ff.layer_blocks(node=12, author=5, parent=CrdtId(3, 11)),
    )
    blocks = list(read_blocks(io.BytesIO(raw)))

    found = set(codec_module._author_ids(blocks))

    assert {0, 1, 3, 5} <= found


def test_the_stylus_sample_the_author_walk_skips_still_carries_no_id():
    """The skip is a measured optimisation, and it is only sound while a sample is numbers.

    0.034s against 0.45s on the largest corpus artifact, which is what justifies naming a
    third-party class in the walk at all. This is what keeps that naming honest.
    """
    declared = {field.type for field in dataclasses.fields(si.Point)}

    assert declared <= {float, int}


@pytest.mark.parametrize(
    ("domain", "parser"),
    [
        pytest.param(PenType, si.Pen, id="tools"),
        pytest.param(PenColor, si.PenColor, id="colours"),
    ],
)
def test_the_domain_and_the_parser_agree_on_every_wire_value(
    domain: type[PenType | PenColor], parser: type[si.Pen | si.PenColor]
):
    """``_scene_line`` constructs both parser enums rather than casting into them.

    That is total only while the two value sets match, so the match is pinned here instead of
    being discovered as a ``ValueError`` escaping a write.
    """
    assert {member.value for member in domain} == {member.value for member in parser}


# ─────────────────────────── against the real corpus ───────────────────────────
#
#  Gated the same way `test_formats_differential_corpus.py` gates its own oracle, and
#  restated rather than shared because these directories are deliberately not importable
#  packages. The skip names the variable and never passes silently.

CORPUS_ENV = "RMSPEC_CORPUS"
DEFAULT_CORPUS_LOCATIONS = (
    pathlib.Path.home() / "remarkable",
    pathlib.Path.home() / "remarkable-backup" / "xochitl",
)


def corpus_scenes() -> list[pathlib.Path]:
    """Return every non-empty ``.rm`` file in the corpus, or skip loudly when there is none."""
    location = os.environ.get(CORPUS_ENV)
    root = (
        pathlib.Path(location)
        if location
        else next((c for c in DEFAULT_CORPUS_LOCATIONS if c.is_dir()), None)
    )
    if root is None or not root.is_dir():
        pytest.skip(f"no reference corpus: set {CORPUS_ENV} to a xochitl backup directory")
    return [path for path in sorted(root.rglob("*.rm")) if path.stat().st_size]


def test_every_non_empty_corpus_artifact_accepts_ink_without_disturbing_what_is_there():
    """The strongest evidence available: 30 real pages, one append each, decoded both ways.

    Checks the four claims that matter on bytes nobody in this repository wrote -- the
    original survives as a strict prefix, the ink arrives on the reported layer, the page's
    existing stroke count and defect list are unchanged, and a full re-encode of the same
    blocks produces the identical file.
    """
    ink = (pen_stroke(), highlight_stroke(colour=Rgba(r=190, g=234, b=254)))
    scenes = corpus_scenes()
    assert scenes, "the gate found a corpus directory but no artifacts in it"

    for path in scenes:
        raw = path.read_bytes()
        before = SceneCodec().decode_page(raw, path.name)
        edit = writer().append_strokes(raw, path.name, strokes=ink)
        after = SceneCodec().decode_page(edit.scene, path.name)

        assert edit.scene.startswith(raw), path.name
        assert len(edit.scene) > len(raw), path.name
        assert after.stroke_count == before.stroke_count + len(ink), path.name
        assert [defect.code for defect in after.defects] == [
            defect.code for defect in before.defects
        ], path.name
        written = after.layers[edit.layer_index].strokes[-len(ink) :]
        assert written[0].pen is PenType.FINELINER_2, path.name
        assert written[1].color_override == Rgba(r=190, g=234, b=254), path.name

        blocks = list(read_blocks(io.BytesIO(raw)))
        site = codec_module._append_site(blocks, path.name)
        minted = codec_module._line_blocks(
            ink, site=site, author_id=codec_module._fresh_author_id(blocks, path.name)
        )
        buffer = io.BytesIO()
        write_blocks(buffer, [*blocks, *minted])
        assert buffer.getvalue() == edit.scene, path.name
