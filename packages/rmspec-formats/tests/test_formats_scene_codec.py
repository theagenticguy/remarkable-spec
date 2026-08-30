"""The ``PageCodec`` adapter, over real v6 bytes written by ``rmscene``'s own writer.

No committed binary fixture and no device: every case here is bytes this module built,
which is also what makes the branches a real file cannot reach -- an out-of-enum wire
tool id, a zero-width text block, a negative thickness -- reachable at all.
"""

from __future__ import annotations

import contextlib
import logging
import struct

import formats_fixtures as ff
import pytest
from hypothesis import given
from hypothesis import strategies as st
from rmscene import scene_items as si
from rmscene.scene_tree import SceneTree
from rmscene.tagged_block_common import CrdtId

from rmspec.domain.errors import (
    CorruptPageData,
    RmspecError,
    SceneRewriteUnsafe,
    UnsupportedPageFormat,
)
from rmspec.domain.models import PageContent, PageDefectCode, PenColor, PenType
from rmspec.formats import SceneCodec
from rmspec.formats import scene_codec as codec_module


def codes(content: PageContent) -> list[PageDefectCode]:
    """Return the defect codes a decode reported, in order."""
    return [defect.code for defect in content.defects]


# ─────────────────────────── the happy path ───────────────────────────


def test_one_stroke_page_decodes_to_one_visible_layer():
    content = SceneCodec().decode_page(ff.inked_scene(), "page-1")

    assert content.defects == ()
    assert len(content.layers) == 1
    layer = content.layers[0]
    assert layer.name == "Layer 1"
    assert layer.visible is True
    assert len(layer.strokes) == 1


def test_every_stroke_field_survives_the_round_trip():
    raw = ff.scene_bytes(
        *ff.layer_blocks(
            node=11,
            items=(
                ff.stroke_item(
                    tool=int(si.Pen.PENCIL_1),
                    color=int(si.PenColor.RED),
                    thickness=3.25,
                    starting_length=1.5,
                    points=(si.Point(-4.5, 7.25, 1234, 200, 4321, 250),),
                ),
            ),
        )
    )

    stroke = SceneCodec().decode_page(raw, "page-1").layers[0].strokes[0]

    assert stroke.pen is PenType.PENCIL_1
    assert stroke.color is PenColor.RED
    assert stroke.thickness_scale == pytest.approx(3.25)
    assert stroke.starting_length == pytest.approx(1.5)
    assert len(stroke.points) == 1
    point = stroke.points[0]
    assert (point.x, point.y) == pytest.approx((-4.5, 7.25))
    assert (point.speed, point.direction, point.width, point.pressure) == (1234, 200, 4321, 250)


def test_layers_keep_their_file_order_and_visibility():
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, name="first", items=(ff.stroke_item(),)),
        *ff.layer_blocks(node=12, name="second", visible=False, items=(ff.stroke_item(),)),
    )

    content = SceneCodec().decode_page(raw, "page-1")

    assert [(layer.name, layer.visible) for layer in content.layers] == [
        ("first", True),
        ("second", False),
    ]
    assert content.stroke_count == 1, "a hidden layer's strokes are not drawn"


def test_a_nested_group_is_flattened_into_the_layer_that_owns_it():
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, items=(ff.stroke_item(),)),
        *ff.layer_blocks(node=12, parent=CrdtId(0, 11), items=(ff.stroke_item(),)),
    )

    content = SceneCodec().decode_page(raw, "page-1")

    assert len(content.layers) == 1, "a nested group is not a layer of its own"
    assert len(content.layers[0].strokes) == 2


def test_a_nested_group_that_will_not_resolve_falls_back_to_its_own_children():
    """The relocated ``except (KeyError, AttributeError)`` arm, which no real file reaches.

    ``SceneGroupItemBlock`` stores the already-resolved node as the child, so the lookup
    always succeeds today and the fallback is unreachable through bytes. It is kept
    verbatim because running *both* paths would emit every nested stroke twice, and it
    is pinned here so a later tidy-up cannot delete it unnoticed.
    """

    class _UnresolvableTree(SceneTree):
        """A tree that has forgotten every node, so the lookup cannot succeed."""

        def __getitem__(self, node_id: CrdtId) -> si.SceneItem:
            """Fail every lookup.

            Parameters
            ----------
            node_id
                The node asked for.

            Raises
            ------
            KeyError
                Always.
            """
            raise KeyError(node_id)

    nested = si.Group(node_id=CrdtId(0, 12))
    nested.children.add(ff._sequence_item(1, ff.stroke_item()))
    outer = si.Group(node_id=CrdtId(0, 11))
    outer.children.add(ff._sequence_item(2, nested))

    layer = codec_module._convert_group(outer, _UnresolvableTree(), codec_module._Defects(), {})

    assert len(layer.strokes) == 1


# ─────────────────────────── degradations as values ───────────────────────────


def test_items_with_no_owning_layer_get_one_invented_and_say_so():
    content = SceneCodec().decode_page(ff.rootless_scene(ff.stroke_item()), "page-1")

    assert [layer.name for layer in content.layers] == ["Layer 1"]
    assert codes(content) == [PageDefectCode.LAYER_SYNTHESISED]


def test_an_unknown_wire_tool_id_substitutes_a_fineliner_and_records_it():
    """The substitution the port documents, at the level where it is reachable.

    It cannot be driven through bytes with this parser: ``line_from_stream`` constructs
    ``si.Pen(tool_id)`` while reading, so an id outside its own enum raises inside
    ``Block.read``, which turns the whole block into an ``UnreadableBlock``. The stroke
    never reaches the tree -- see the dropped-block test below, which is what makes that
    loss visible at all.
    """
    defects = codec_module._Defects()

    stroke = codec_module._convert_line(ff.stroke_item(tool=99), defects, {})

    assert stroke is not None
    assert stroke.pen is PenType.FINELINER_1
    assert [defect.code for defect in defects.entries] == [PageDefectCode.UNKNOWN_PEN_SUBSTITUTED]
    assert "99" in defects.entries[0].detail


def test_an_unknown_colour_index_substitutes_black_and_records_it():
    defects = codec_module._Defects()

    stroke = codec_module._convert_line(ff.stroke_item(color=42), defects, {})

    assert stroke is not None
    assert stroke.color is PenColor.BLACK
    assert [defect.code for defect in defects.entries] == [
        PageDefectCode.UNKNOWN_COLOR_SUBSTITUTED
    ]
    assert "42" in defects.entries[0].detail


def test_a_block_the_parser_cannot_read_is_recorded_rather_than_silently_missing():
    """A wire tool id ``rmscene``'s own enum rejects costs the stroke, not the page.

    The legacy reader lost this to a log line it had silenced at import time: the block
    is dropped by the parser before the walk sees it, so the page came back with one
    fewer stroke and nothing to say about it.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, items=(ff.stroke_item(tool=99),)))

    content = SceneCodec().decode_page(raw, "page-1")

    assert len(content.layers) == 1, "the declared layer survives; only its stroke is gone"
    assert content.layers[0].strokes == ()
    assert codes(content) == [PageDefectCode.ITEM_DROPPED]
    assert "could not read a block" in content.defects[0].detail


def test_a_stroke_the_domain_refuses_is_dropped_rather_than_failing_the_page():
    raw = ff.scene_bytes(
        *ff.layer_blocks(
            node=11, items=(ff.stroke_item(thickness=-1.0), ff.stroke_item(thickness=1.0))
        )
    )

    content = SceneCodec().decode_page(raw, "page-1")

    assert len(content.layers[0].strokes) == 1, "the good stroke survives"
    assert codes(content) == [PageDefectCode.ITEM_DROPPED]


def test_a_text_highlight_is_skipped_and_recorded():
    highlight = si.GlyphRange(
        start=0,
        length=3,
        text="abc",
        color=si.PenColor.YELLOW,
        rectangles=[si.Rectangle(1.0, 2.0, 3.0, 4.0)],
    )
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, items=(highlight, ff.stroke_item())))

    content = SceneCodec().decode_page(raw, "page-1")

    assert len(content.layers[0].strokes) == 1
    assert codes(content) == [PageDefectCode.ITEM_DROPPED]


def test_a_scene_item_type_this_codec_does_not_know_is_dropped_by_name():
    defects = codec_module._Defects()
    builder = codec_module._LayerBuilder(name="", visible=True)

    codec_module._collect_item(si.SceneItem(), builder, defects, {})

    assert builder.is_empty
    assert [defect.code for defect in defects.entries] == [PageDefectCode.ITEM_DROPPED]
    assert "SceneItem" in defects.entries[0].detail


# ─────────────────────────── typed text ───────────────────────────
#
#  Two sources, and only one of them is reachable from a real file.
#
#  A page's typed text is page-scoped: it arrives as ``SceneTree.root_text``, which the
#  scene-tree walk never visits, so ``_collect_item``'s text arm never ran against real
#  bytes and every typed page decoded to zero text blocks -- the same shape the legacy
#  reader had, which is why typed text has never worked here. ``_convert_root_text`` reads
#  it onto ``PageContent.text_blocks``, page-level, because the block names no layer.
#
#  ``Layer.text_blocks`` stays reachable only below this line: ``SceneTextItemBlock`` --
#  the format's *layer-owned* text item -- decodes its value to nothing in ``rmscene``
#  0.7.0 and is never added to the tree, and no artifact in the reference corpus carries
#  one. So the layer-owned arm is exercised directly, and the page-level one through bytes.


def test_the_pages_own_typed_text_reaches_the_domain():
    """The defect, at the level a caller sees it. Previously ``text_blocks`` was empty."""
    content = SceneCodec().decode_page(ff.texted_scene(), "page-1")

    assert [block.text for block in content.text_blocks] == ["hello world"]
    assert content.text_blocks[0].width == pytest.approx(400.0)
    assert content.defects == ()


def test_the_pages_own_typed_text_is_not_stapled_to_a_layer():
    """Layer 0 was rejected: the block names no layer, so claiming one would be a lie.

    A renderer that draws layers separately, or hides the first one, would move or lose
    text that never belonged to any layer -- which is why this lands page-level instead.
    """
    content = SceneCodec().decode_page(ff.texted_scene(), "page-1")

    assert [layer.text_blocks for layer in content.layers] == [()]
    assert len(content.layers[0].strokes) == 1, "the layer keeps its own ink"


def test_a_page_with_no_typed_text_reports_none_rather_than_an_empty_block():
    content = SceneCodec().decode_page(ff.inked_scene(), "page-1")

    assert content.text_blocks == ()


def test_a_page_whose_only_content_is_typed_text_is_not_blank():
    """And it still reports the layerless defect: the device always writes a layer."""
    content = SceneCodec().decode_page(ff.scene_bytes(ff.root_text_block()), "page-1")

    assert [block.text for block in content.text_blocks] == ["hello world"]
    assert content.is_blank is False, "typed text is content, so the page is not blank"
    assert content.layers == ()
    assert codes(content) == [PageDefectCode.ITEM_DROPPED], "no layer is still an anomaly"


def test_a_page_text_block_the_domain_refuses_is_dropped_and_recorded():
    """Through real bytes, which is what makes the width guard more than a unit test."""
    content = SceneCodec().decode_page(ff.texted_scene(width=0.0), "page-1")

    assert content.text_blocks == ()
    assert codes(content) == [PageDefectCode.ITEM_DROPPED]
    assert "non-positive width" in content.defects[0].detail


def test_a_text_block_flattens_its_crdt_sequence_and_drops_formatting_codes():
    block = codec_module._convert_text(ff.text_item(), codec_module._Defects())

    assert block is not None
    assert block.text == "hello world"
    assert block.width == pytest.approx(400.0)


@pytest.mark.parametrize("width", [0.0, -12.5])
def test_a_text_block_with_a_non_positive_width_is_dropped(width: float):
    defects = codec_module._Defects()

    block = codec_module._convert_text(ff.text_item(width=width), defects)

    assert block is None
    assert [defect.code for defect in defects.entries] == [PageDefectCode.ITEM_DROPPED]


def test_a_text_block_lands_on_the_layer_that_owns_it():
    defects = codec_module._Defects()
    builder = codec_module._LayerBuilder(name="", visible=True)

    codec_module._collect_item(ff.text_item(), builder, defects, {})

    assert len(builder.text_blocks) == 1
    assert defects.entries == []


# ─────────────────────────── blank, absent, corrupt ───────────────────────────


def test_a_zero_byte_artifact_is_a_blank_page_and_not_a_failure():
    content = SceneCodec().decode_page(b"", "page-1")

    assert content == PageContent()
    assert content.is_blank
    assert content.defects == ()


def test_a_valid_file_with_no_items_is_layerless_and_says_so():
    """No layer is not the same claim as no ink, and only a stub may make the latter.

    A preamble-only file parses cleanly and produces no layer, which is byte-for-byte the
    value a zero-byte stub produces. Since a scene file the device wrote always declares
    at least one layer -- all 30 renderable corpus entries record ``layers >= 1`` -- the
    layerless outcome is reported rather than presented as a blank page. This is what
    stops a truncated artifact from being indistinguishable from a stub on the offsets
    that keep a complete preamble and exact framing.
    """
    content = SceneCodec().decode_page(ff.scene_bytes(), "page-1")

    assert content.layers == (), "still no synthesised layer: there was nothing to hold"
    assert [defect.code for defect in content.defects] == [PageDefectCode.ITEM_DROPPED]
    assert content != PageContent(), "the stub's value, and this is not a stub"


def test_truncated_bytes_are_corrupt_page_data_against_the_supplied_ref():
    with pytest.raises(CorruptPageData) as caught:
        SceneCodec().decode_page(ff.TRUNCATED_SCENE, "page-1")

    error = caught.value
    assert error.page_uuid == "page-1"
    assert error.detail, "the legacy EOFError carried an empty message; this one may not"
    assert error.offset is not None
    assert error.offset > 0
    assert isinstance(error.__cause__, EOFError)


def test_no_parser_type_escapes_the_codec():
    with pytest.raises(RmspecError) as caught:
        SceneCodec().decode_page(b"this is not a scene file at all", "page-1")

    mro = type(caught.value).__mro__
    assert not any(cls.__module__.startswith("rmscene") for cls in mro)


def test_a_scene_version_this_codec_refuses_is_carried_on_the_error():
    with pytest.raises(UnsupportedPageFormat) as caught:
        SceneCodec().decode_page(ff.V5_SCENE, "page-1")

    assert caught.value.observed_version == "5"
    assert caught.value.supported_versions == ("6",)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\x00\x01\x02\x03", id="nothing header-shaped at all"),
        pytest.param(b"reMarkable .lines file, unversioned    ", id="the prefix but no version"),
    ],
)
def test_bytes_with_no_readable_header_are_corrupt_rather_than_unsupported(raw: bytes):
    """A failed sniff must fall through to the parser, never become a version refusal."""
    with pytest.raises(CorruptPageData):
        SceneCodec().decode_page(raw, "page-1")


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"---\nversion=9\nname: something\n---\n", id="yaml front matter"),
        pytest.param(b"# lockfile\nversion=3\n[[package]]\n", id="a toml lockfile"),
    ],
)
def test_a_non_scene_file_that_mentions_a_version_is_corrupt_not_unsupported(raw: bytes):
    """A version refusal is the wrong thing to say about a file that is not a scene.

    The sniff searched the first 43 bytes for a ``version=`` field and believed whatever
    it found, so a file that merely mentions a version was refused as a scene of an
    unsupported version -- which sends someone who ran ``rmspec inspect rm`` on the wrong
    path looking for a firmware problem. A version is now believed only inside a header
    that opens the way a scene file's does.
    """
    with pytest.raises(CorruptPageData):
        SceneCodec().decode_page(raw, "page-1")


def test_the_page_ref_is_a_label_and_never_changes_what_is_decoded():
    raw = ff.inked_scene()

    by_uuid = SceneCodec().decode_page(raw, "5a2c-page")
    by_path = SceneCodec().decode_page(raw, "notes/page two.rm")

    assert by_uuid == by_path


# ─────────────────────────── the parser's logger ───────────────────────────


@pytest.mark.parametrize("raw", [b"", ff.inked_scene(), ff.TRUNCATED_SCENE])
def test_decoding_leaves_the_parser_logger_exactly_as_it_found_it(raw: bytes):
    logger = logging.getLogger("rmscene")
    before = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        with contextlib.suppress(RmspecError):
            SceneCodec().decode_page(raw, "page-1")
        assert logger.level == logging.DEBUG, "the suppression must be restored, not left on"
    finally:
        logger.setLevel(before)


def test_neither_importing_nor_constructing_touches_the_parser_logger():
    """The legacy module raised the ``rmscene`` level at import time, process-wide."""
    logger = logging.getLogger("rmscene")
    before = logger.level

    SceneCodec()

    assert logger.level == before
    assert before == logging.NOTSET, "importing this package configured another library's logger"


def test_two_overlapping_decodes_do_not_leak_the_suppression():
    """Save-and-restore alone leaks permanently; the counter is what fixes it.

    Interleaved: A saves ``NOTSET`` and lowers, B saves the *lowered* level and lowers
    again, A restores ``NOTSET``, B restores ``ERROR``. The process is left suppressed
    for good -- which is the exact failure the docstring sells scoping as the cure for,
    and it bites hardest under the parallel randomised runs this suite is built for.
    """
    logger = logging.getLogger("rmscene")
    before = logger.level
    assert before == logging.NOTSET

    outer = codec_module._quiet_parser()
    inner = codec_module._quiet_parser()
    outer.__enter__()
    inner.__enter__()
    assert logger.level == logging.ERROR, "both are inside, so the parser stays quiet"

    outer.__exit__(None, None, None)
    assert logger.level == logging.ERROR, "the inner decode is still running"

    inner.__exit__(None, None, None)
    assert logger.level == before, "the last one out restores what the first one found"


# ─────────────────────────── v1 point channels ───────────────────────────


def test_v1_float_encoded_point_channels_round_back_to_their_wire_values():
    """A pre-3.0 line block encodes speed, direction and pressure as floats.

    ``Point`` constrains all three to integers in the ranges the v6 format uses, so
    without rounding one such block would refuse a whole page the legacy reader
    rendered. Rounding recovers the original channel exactly.
    """
    sample = si.Point(1.0, 2.0, 1, 30, 3, 200)
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, items=(ff.stroke_item(points=(sample,)),)), version="3.0"
    )

    point = SceneCodec().decode_page(raw, "page-1").layers[0].strokes[0].points[0]

    assert (point.speed, point.direction, point.width, point.pressure) == (1, 30, 3, 200)


@given(
    value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
    limit=st.sampled_from([255, 65535]),
)
def test_a_channel_is_always_an_int_inside_its_wire_range(value: float, limit: int):
    channel = codec_module._channel(value, limit)

    assert isinstance(channel, int)
    assert 0 <= channel <= limit


@given(value=st.integers(min_value=0, max_value=255))
def test_an_in_range_integer_channel_is_left_alone(value: int):
    assert codec_module._channel(value, 255) == value


# ─────────────────────────── the translation block ───────────────────────────


def test_a_group_inside_a_group_is_collected_into_the_same_layer():
    """``_collect_item``'s own recursion, which a doubly-nested group reaches."""
    inner = si.Group(node_id=CrdtId(0, 13))
    inner.children.add(ff._sequence_item(1, ff.stroke_item()))
    outer = si.Group(node_id=CrdtId(0, 12))
    outer.children.add(ff._sequence_item(2, inner))
    builder = codec_module._LayerBuilder(name="", visible=True)

    codec_module._collect_item(outer, builder, codec_module._Defects(), {})

    assert len(builder.strokes) == 1


def test_a_deleted_sequence_member_is_skipped_without_claiming_ink_went_missing():
    """Found by running the differential oracle against the real 92-file backup.

    An item block with no value subblock is a CRDT *tombstone*: the parser reads its
    ``deleted_length`` and yields ``None`` in place of a value, so it records a stroke the
    user erased. Falling through to the unknown-type arm reported ``ITEM_DROPPED`` -- "some
    ink or text is missing from an otherwise good page" -- 1237 times across 24 of the
    corpus's 30 renderable pages, which put almost every real document into
    ``Document.defective_pages`` and would fail ``--strict`` on all of them, over ink the
    user deliberately removed. No layer and no stroke changes, so the recorded counts are
    untouched.
    """
    builder = codec_module._LayerBuilder(name="", visible=True)
    defects = codec_module._Defects()

    codec_module._collect_item(None, builder, defects, {})

    assert builder.is_empty
    assert defects.entries == [], "a deletion the user made is not a degradation we survived"


def test_a_scene_item_of_a_type_this_codec_does_not_know_is_still_reported():
    """The other arm: an unrecognised *type* really is ink that will not be drawn."""
    builder = codec_module._LayerBuilder(name="", visible=True)
    defects = codec_module._Defects()

    codec_module._collect_item(object(), builder, defects, {})

    assert builder.is_empty
    assert [defect.code for defect in defects.entries] == [PageDefectCode.ITEM_DROPPED]
    assert "not understood" in defects.entries[0].detail


def test_a_dropped_text_block_leaves_the_layer_alone():
    builder = codec_module._LayerBuilder(name="", visible=True)
    defects = codec_module._Defects()

    codec_module._collect_item(ff.text_item(width=0.0), builder, defects, {})

    assert builder.text_blocks == []
    assert [defect.code for defect in defects.entries] == [PageDefectCode.ITEM_DROPPED]


def test_an_erased_stroke_is_normal_content_and_not_a_defect_when_it_arrives_in_bytes():
    """The tombstone trap, through the writer rather than through ``_collect_item`` directly.

    Six of the live page's 61 item blocks are tombstones. Reporting one would put almost
    every real document into ``Document.defective_pages`` over ink the user deliberately
    erased, and dereferencing one is how code that samples "an existing stroke" for its pen
    and colour meets ``None``.
    """
    raw = ff.scene_bytes(
        *ff.layer_blocks(node=11, items=(ff.stroke_item(),)),
        ff.tombstone_block(node=11, index=1150),
    )

    content = SceneCodec().decode_page(raw, "page-1")

    assert len(content.layers) == 1
    assert len(content.layers[0].strokes) == 1, "the surviving stroke, and nothing invented"
    assert content.defects == (), "an erasure the user made is not a degradation"


def test_nothing_in_a_decode_keys_on_the_scene_ids_that_a_re_save_renumbers():
    """Measured: a re-save moved a page's layer from ``CrdtId(0, 11)`` to ``CrdtId(1, 334)``.

    Both components moved, so an id read from one parse is worthless against the next one.
    Two files identical but for their ids must therefore decode to the same value -- which is
    also what makes a decode cacheable across the tablet rewriting a page it did not change.
    """
    before = ff.scene_bytes(*ff.layer_blocks(node=11, items=(ff.stroke_item(),)))
    after = ff.scene_bytes(*ff.layer_blocks(node=334, author=1, items=(ff.stroke_item(),)))

    assert before != after, "the two files really are different bytes"
    assert SceneCodec().decode_page(before, "page-1") == SceneCodec().decode_page(after, "page-1")


def test_a_domain_error_raised_inside_the_walk_is_re_raised_untouched(
    monkeypatch: pytest.MonkeyPatch,
):
    """The translation block turns parser failures into ``CorruptPageData``, not our own.

    Nothing in the walk raises a domain error today, so this guard is unreachable through
    bytes. It is kept because the alternative -- a domain error being re-wrapped as
    "these bytes are not a decodable scene file" -- would report the wrong thing about
    the wrong subject, and that is worth pinning before someone adds a raise.
    """
    intended = UnsupportedPageFormat(
        page_uuid="page-1", observed_version="7", supported_versions=("6",)
    )

    def raise_domain_error(*_: object) -> tuple[object, ...]:
        raise intended

    monkeypatch.setattr(codec_module, "_convert_tree", raise_domain_error)

    with pytest.raises(UnsupportedPageFormat) as caught:
        SceneCodec().decode_page(ff.inked_scene(), "page-1")

    assert caught.value is intended


# ──────────────────── the lossless rewrite precondition ────────────────────
#
#  The check that stands between an additive rewrite and silently dropping a page of
#  handwriting. It is verified per call because the fact behind it -- that this parser
#  reproduces a real page byte for byte even while reporting that it did not read all of
#  it -- is a measurement of one firmware and one parser version, not a guarantee.


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(ff.inked_scene(), id="one stroke"),
        pytest.param(ff.texted_scene(), id="typed text the page owns"),
        pytest.param(
            ff.tailed_scene(ff.MEASURED_YELLOW_TAIL, ff.NO_TAIL),
            id="a tail the parser leaves behind",
        ),
        pytest.param(
            ff.scene_bytes(
                *ff.layer_blocks(node=11, items=(ff.stroke_item(),)),
                ff.tombstone_block(node=11, index=1150),
            ),
            id="an erased stroke",
        ),
        pytest.param(
            ff.scene_bytes(
                *ff.layer_blocks(node=11, name="first", items=(ff.stroke_item(),)),
                *ff.layer_blocks(node=12, name="second", visible=False),
            ),
            id="two layers, one hidden",
        ),
    ],
)
def test_every_shape_this_codec_decodes_also_re_encodes_to_itself(raw: bytes):
    """Including the two the write path cares about most: a tombstone and an unread tail.

    A tail is the case that could only be assumed before it was checked -- the parser stops
    short of those bytes and hands them over as ``extra_data``, and it is the writer putting
    them back that makes the round trip lossless at all.
    """
    SceneCodec().check_rewritable(raw, "page-1")


def test_a_zero_byte_stub_is_refused_by_name_rather_than_as_a_byte_mismatch():
    """62 of the corpus's 92 artifacts are these, so the message has to be the right one.

    There is nothing to preserve and nothing to allocate a fresh author id against, so
    writing one is creating a scene. Reporting "produced 43 bytes from 0" would describe the
    symptom of that and not the thing a caller has to do differently.
    """
    with pytest.raises(SceneRewriteUnsafe) as caught:
        SceneCodec().check_rewritable(b"", "page-1")

    assert caught.value.page_uuid == "page-1"
    assert "zero-byte" in caught.value.detail
    assert caught.value.__cause__ is None, "nothing failed; the artifact is simply empty"


def test_a_page_whose_channels_the_writer_cannot_pack_is_refused_and_chains_the_cause():
    """A pre-3.0 page: v1 line blocks read their channels as floats, and the writer packs ints.

    The writer raises before producing anything, so there are no bytes to compare -- which is
    why the precondition catches a raise as well as a mismatch. The ``struct`` failure is
    chained and never re-exported.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11, items=(ff.stroke_item(),)), version="3.0")

    with pytest.raises(SceneRewriteUnsafe) as caught:
        SceneCodec().check_rewritable(raw, "page-1")

    assert isinstance(caught.value.__cause__, struct.error)
    assert "raised" in caught.value.detail
    assert not any(cls.__module__.startswith("rmscene") for cls in type(caught.value).__mro__)


def test_a_page_the_writer_re_encodes_differently_is_refused_with_both_lengths():
    """Block *header* versions are recomputed from the writer's options, not carried over.

    So a file written by a firmware whose header versions differ from this writer's defaults
    comes back as a different file with nothing raised, which is the quiet failure that would
    otherwise return bytes missing part of a page.
    """
    raw = ff.scene_bytes(*ff.layer_blocks(node=11), version="3.0")

    with pytest.raises(SceneRewriteUnsafe) as caught:
        SceneCodec().check_rewritable(raw, "page-1")

    assert str(len(raw)) in caught.value.detail
    assert caught.value.__cause__ is None, "nothing raised; the bytes simply differ"


def test_bytes_that_do_not_decode_are_corrupt_data_rather_than_an_unsafe_rewrite():
    """Two different things to tell a caller, and this is the one about their file.

    ``SceneRewriteUnsafe`` says the page is fine and this build cannot reproduce it. A
    truncated artifact is the other claim entirely, and it must not be dressed up as a
    writer problem.
    """
    with pytest.raises(CorruptPageData) as caught:
        SceneCodec().check_rewritable(ff.TRUNCATED_SCENE, "page-1")

    assert not isinstance(caught.value, SceneRewriteUnsafe)
    assert isinstance(caught.value.__cause__, EOFError)
    assert caught.value.offset is not None


def test_the_precondition_leaves_the_parser_logger_exactly_as_it_found_it():
    logger = logging.getLogger("rmscene")
    before = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        SceneCodec().check_rewritable(ff.inked_scene(), "page-1")
        assert logger.level == logging.DEBUG
    finally:
        logger.setLevel(before)
