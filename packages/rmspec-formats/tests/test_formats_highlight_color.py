"""The per-stroke colour ``rmscene`` 0.7.0 does not decode, over the real measured bytes.

Firmware 3.27.3.0 writes a highlighter's true colour as an optional tagged field -- index 8,
``Byte4`` -- on the scene line item, and reports ``PenColor`` id 9 for every highlight whatever
colour it was drawn in. The parser stops before that field and warns; the codec reads the bytes
it left behind. Two of the four strokes drawn for the measurement had been flushed to disk, and
both of their tails are fixtures here, so every assertion below is against bytes a tablet
actually wrote rather than a shape invented to match the code.
"""

from __future__ import annotations

import formats_fixtures as ff
import pytest
from rmscene import scene_items as si
from rmscene.scene_stream import SceneLineItemBlock

from rmspec.domain.models import PageContent, PageDefectCode, PenColor, PenType, Rgba, Stroke
from rmspec.formats import SceneCodec
from rmspec.formats import scene_codec as codec_module

YELLOW = Rgba(r=0xFF, g=0xED, b=0x75, a=255)
"""``#ffed75``, the first measured stroke: ``84 01 75 ed ff ff`` -> ``0xFFFFED75``."""

PALE_BLUE = Rgba(r=0xBE, g=0xEA, b=0xFE, a=255)
"""``#beeafe``, the second measured stroke: ``84 01 fe ea be ff`` -> ``0xFFBEEAFE``."""


def strokes(content: PageContent) -> tuple[Stroke, ...]:
    """Return every stroke of a single-layer decode, in draw order."""
    assert len(content.layers) == 1
    return content.layers[0].strokes


def codes(content: PageContent) -> list[PageDefectCode]:
    """Return the defect codes a decode reported, in order."""
    return [defect.code for defect in content.defects]


def scene_with_block_level_tail(tail: bytes) -> bytes:
    """Build a page whose line item leaves bytes unread at *block* level, not in its value.

    The measured field lives inside value subblock 6, so ``extra_value_data`` is where it
    lands. A tail outside that subblock is the same signal about a different part of the
    format, and the defect covers both; this is the second case.
    """
    blocks = list(ff.layer_blocks(node=11, items=(ff.stroke_item(),)))
    for block in blocks:
        if isinstance(block, SceneLineItemBlock):
            block.extra_data = tail
    return ff.scene_bytes(*blocks)


# ─────────────────────────── the defect, in one assertion ───────────────────────────


def test_two_highlighter_strokes_with_the_same_colour_id_decode_to_different_colours():
    """The whole rendering defect, stated once.

    Both strokes are tool 18, colour 9. Before this, both rendered ``EXPORT_PALETTE``'s
    yellow, so a pale blue highlight came out yellow and nothing anywhere said so.
    """
    raw = ff.tailed_scene(ff.MEASURED_YELLOW_TAIL, ff.MEASURED_BLUE_TAIL)

    first, second = strokes(SceneCodec().decode_page(raw, "page-1"))

    assert first.color is second.color is PenColor.HIGHLIGHT, "the wire says one colour"
    assert first.pen is PenType.HIGHLIGHTER_2
    assert first.color_override == YELLOW
    assert second.color_override == PALE_BLUE
    assert first.color_override != second.color_override, "and the bytes say two"


def test_the_first_measured_stroke_decodes_to_hash_ffed75_at_full_alpha():
    raw = ff.tailed_scene(ff.MEASURED_YELLOW_TAIL)

    override = strokes(SceneCodec().decode_page(raw, "page-1"))[0].color_override

    assert override is not None
    assert override.as_rgb().as_hex() == "#ffed75"
    assert override.a == 255


def test_the_second_measured_stroke_decodes_to_hash_beeafe_at_full_alpha():
    raw = ff.tailed_scene(ff.MEASURED_BLUE_TAIL)

    override = strokes(SceneCodec().decode_page(raw, "page-1"))[0].color_override

    assert override is not None
    assert override.as_rgb().as_hex() == "#beeafe"
    assert override.a == 255


def test_a_stroke_that_leaves_nothing_unread_carries_no_override_and_no_defect():
    """162 of the measured page's 164 lines. Every pen stroke, on every page ever drawn."""
    raw = ff.tailed_scene(ff.NO_TAIL, tool=int(si.Pen.FINELINER_2), color=int(si.PenColor.BLACK))

    content = SceneCodec().decode_page(raw, "page-1")

    assert strokes(content)[0].color_override is None
    assert content.defects == ()


def test_an_ordinary_page_of_pen_strokes_is_byte_for_byte_the_value_it_always_was():
    """The override cannot perturb a page that carries no such field.

    ``inked_scene`` is the fixture every other suite in this package decodes, so pinning its
    whole value here is what says the new field is additive rather than a change of default.
    """
    content = SceneCodec().decode_page(ff.inked_scene(), "page-1")

    assert content.defects == ()
    assert strokes(content)[0].color_override is None


# ─────────────────────────── the unread-bytes defect ───────────────────────────


def test_a_tail_this_codec_decodes_is_not_reported_as_unread():
    """Reporting a decoded tail as unread would be a false statement about the page.

    ``rmscene`` stopped before those six bytes; this codec did not. ``BLOCK_BYTES_UNREAD`` is
    a claim about the artifact a caller acts on, not a note about a third-party parser's
    internals, so a page whose every leftover byte has been read and used is not degraded --
    and every highlighter stroke a Paper Pro writes leaves exactly this tail, so the
    alternative was the code firing forever on every page holding a highlight.
    """
    raw = ff.tailed_scene(ff.MEASURED_YELLOW_TAIL, ff.MEASURED_BLUE_TAIL)

    content = SceneCodec().decode_page(raw, "page-1")

    assert [stroke.color_override for stroke in strokes(content)] == [YELLOW, PALE_BLUE]
    assert content.defects == (), "the bytes were read, so nothing about them went unread"


def test_a_remainder_after_a_recognised_field_is_reported_and_the_field_still_decoded():
    """Two facts in one tail, and the defect narrows to the half that is really unread.

    Not something the firmware has been seen to write -- both measured tails were the field
    and nothing else -- but appending a second field is how this format has grown before, and
    the header is a varuint, so the boundary is data rather than a constant two bytes. The
    remainder alone appears in the detail; the six bytes that were understood do not.
    """
    raw = ff.tailed_scene(ff.MEASURED_YELLOW_TAIL + bytes.fromhex("1c07"))

    content = SceneCodec().decode_page(raw, "page-1")

    assert strokes(content)[0].color_override == YELLOW, "the field was still read"
    assert codes(content) == [PageDefectCode.BLOCK_BYTES_UNREAD]
    detail = content.defects[0].detail
    assert "1c 07" in detail
    assert "2 byte(s)" in detail
    assert "75 ed ff ff" not in detail, "the decoded field is not reported as unread"


def test_a_tail_that_is_not_this_field_is_left_alone_and_still_reported():
    """``8f 01`` is index 8 with tag ``ID``, not ``Byte4``: the right index, the wrong field.

    Guessing at it would colour a stroke from four bytes of something else. The defect is
    what a reader gets instead, and it carries the bytes so the next measurement can start
    from them.
    """
    raw = ff.tailed_scene(bytes.fromhex("8f0101020304"))

    content = SceneCodec().decode_page(raw, "page-1")

    assert strokes(content)[0].color_override is None
    assert codes(content) == [PageDefectCode.BLOCK_BYTES_UNREAD]
    assert "8f 01 01 02 03 04" in content.defects[0].detail


def test_bytes_left_unread_outside_the_value_subblock_are_reported_too():
    content = SceneCodec().decode_page(scene_with_block_level_tail(b"\x99\x88"), "page-1")

    assert strokes(content)[0].color_override is None
    assert codes(content) == [PageDefectCode.BLOCK_BYTES_UNREAD]
    assert "99 88" in content.defects[0].detail


def test_one_defect_is_recorded_per_block_with_bytes_left_over_and_not_per_page():
    """Three lines, two of them carrying a field nothing understands, one carrying nothing.

    Per block rather than per page, and per *block* rather than per byte: ``rmscene`` warns
    once per reader instance, which is precisely why the measurement had to rebuild a
    one-block file per block to attribute the warning at all.
    """
    unknown = bytes.fromhex("8f0101020304")
    raw = ff.tailed_scene(unknown, ff.NO_TAIL, unknown)

    content = SceneCodec().decode_page(raw, "page-1")

    assert codes(content) == [PageDefectCode.BLOCK_BYTES_UNREAD] * 2


# ─────────────────────────── the field decoder itself ───────────────────────────


@pytest.mark.parametrize(
    ("tail", "expected", "consumed"),
    [
        pytest.param(ff.MEASURED_YELLOW_TAIL, YELLOW, 6, id="the first measured stroke"),
        pytest.param(ff.MEASURED_BLUE_TAIL, PALE_BLUE, 6, id="the second measured stroke"),
        pytest.param(
            bytes.fromhex("84010000000000"),
            Rgba(r=0, g=0, b=0, a=0),
            6,
            id="a payload of zeroes, plus a trailing byte the field does not own",
        ),
        pytest.param(
            bytes.fromhex("8401ffffffff"),
            Rgba(r=255, g=255, b=255, a=255),
            6,
            id="every channel at full scale",
        ),
    ],
)
def test_the_field_decoder_reads_a_little_endian_uint32_as_argb(
    tail: bytes,
    expected: Rgba,
    consumed: int,
):
    assert codec_module._decode_color_override(tail) == (expected, consumed)


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(b"", id="nothing unread at all"),
        pytest.param(bytes.fromhex("84"), id="a header whose continuation byte never arrives"),
        pytest.param(bytes.fromhex("8080808080"), id="five continuation bytes and no terminator"),
        pytest.param(bytes.fromhex("14deadbeef"), id="index 1, Byte4 -- a different field"),
        pytest.param(bytes.fromhex("8f01deadbeef"), id="index 8, ID -- the wrong tag type"),
        pytest.param(bytes.fromhex("840175ed"), id="index 8, Byte4, and a payload cut short"),
    ],
)
def test_the_field_decoder_declines_anything_that_is_not_this_field(tail: bytes):
    assert codec_module._decode_color_override(tail) is None


def test_the_two_byte_header_is_read_as_a_varuint_and_not_as_a_byte():
    """The trap the measurement recorded, pinned so a later tidy-up cannot re-enter it.

    Read as one byte, ``0x84`` gives index 8 and tag ``0x4`` by accident and a payload width
    of 5 by mistake, which is how a reader ends up hunting a field that is not there. Read as
    a varuint, ``84 01`` is 132: the same index and tag, and a header two bytes wide.
    """
    assert codec_module._read_tag(ff.MEASURED_YELLOW_TAIL) == (8, 0x4, 2)
    assert codec_module._read_tag(bytes.fromhex("14")) == (1, 0x4, 1)
    assert codec_module._read_tag(bytes.fromhex("84")) is None


def test_this_field_can_never_have_a_one_byte_header():
    """Why the consumed width is derived anyway, and why it is always six here.

    ``(8 << 4) | 0x4`` is ``0x84``, whose continuation bit is set, so index 8 / ``Byte4``
    cannot be spelled in one byte -- the two-byte header is forced by the encoding rather than
    a choice the firmware made. The width is still returned by the decoder rather than assumed
    by its caller, because that stops being true the moment an index above 15 appears.
    """
    header = (8 << 4) | 0x4

    assert header == 0x84
    assert header & 0x80, "so a single byte cannot terminate the varuint"
    assert codec_module._read_tag(bytes([header, 0x01])) == (8, 0x4, 2)
