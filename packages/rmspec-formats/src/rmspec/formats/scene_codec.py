"""The ``PageCodec`` adapter: v6 scene bytes in, domain page content out.

The only module in the workspace that imports ``rmscene``, which
``tests/architecture/test_dependency_direction.py`` asserts by walking every source
file in every package. No ``rmscene`` type appears in a signature, a return value, or
a raised exception here: a parser type crossing this edge is the defect the port
exists to prevent, and ``tests/`` asserts that too.

What was relocated, and what changed
------------------------------------
``parse_rm_bytes`` and its whole private walk -- ``_convert_tree``, ``_convert_group``,
``_collect_item``, ``_convert_line``, ``_convert_point``, ``_convert_text`` -- move
here with their traversal order, their nested-group resolution and their
``FINELINER_1`` / ``BLACK`` fallbacks intact. Stroke order *is* SVG draw order, so the
order of these appends is a differential-hash concern and not a style one; in
particular the ``tree[child.node_id]`` lookup and its
``except (KeyError, AttributeError)`` fallback stay exactly as they were.

Five changes, all forced:

1. **Strokes and text accumulate per layer, defects per page.** The domain's ``Layer``
   and ``PageContent`` are frozen with tuple fields, so ``layer.strokes.append(...)``
   cannot survive literally. Each layer therefore gets a mutable builder and the frozen
   model is built once at the end. The builder is *per layer* rather than per page,
   because ``_convert_group`` owned one ``Layer`` and every path -- the resolved nested
   group, the unresolved fallback, the direct child -- appended into that layer. A
   page-flat accumulator would merge every layer into one, which loses layer
   visibility and changes what a renderer groups.
2. **Every degradation is a value, not a log line.** The two ``logger.warning`` calls
   for an unknown pen and an unknown colour, the ``logger.debug`` for an unhandled
   item, the silent ``pass`` for a highlight and the invented ``"Layer 1"`` all become
   ``PageDefect`` entries on the returned content. There is no module-level logger, so
   there is no path by which a degradation reaches a log instead of the caller. That
   also covers one degradation the legacy reader could not see: ``Block.read`` swallows
   a per-block parse failure into an ``UnreadableBlock`` and a warning, so the item is
   absent from the tree the walk below is handed. ``read_blocks`` plus ``build_tree`` is
   therefore called in place of ``read_tree`` -- the same two steps ``read_tree`` is,
   with the block list kept -- so those failures are counted rather than silenced.
3. **A zero-byte artifact is a blank page, decided before ``rmscene`` is touched.** 62
   of the 92 ``.rm`` files in the reference corpus are zero bytes -- the stubs the
   firmware writes for the unannotated pages of a PDF-backed document. ``read_tree``
   raises a bare, message-less ``EOFError`` on every one of them, which is what made
   two thirds of a real corpus crash a naive page loop. ``PageContent``'s own docstring
   names empty layers with empty defects as meaning "the page really is blank", which
   is precisely what a stub records, so that is what is returned. It is not folded into
   ``ARTIFACT_ABSENT``: those files exist, and the distinction between "no file" and
   "an empty file" is one a caller can act on.

   The corollary is a *partial write*, and it is the one input that could otherwise
   have returned the stub's own value. ``rmscene``'s block reader treats an
   end-of-file inside the 4-byte block-length field as a clean end of iteration, so a
   file cut exactly on -- or within three bytes of -- a block boundary parses without
   an exception and without an ``UnreadableBlock``, and produces the same empty tree a
   stub does. "Blank" and "damaged" collapsing into one value is exactly what this
   codec exists to prevent, so :func:`_reject_partial_write` checks three things the
   bytes really do settle before any content is returned: that there is at least one
   block, that block framing consumes the file to the byte, and that the preamble the
   firmware always writes is present. A cut that survives all three keeps a complete
   preamble and exact framing and is *not* decidable from the bytes; those get a
   defect instead, because a scene file that declares blocks and yields no layer at
   all is not something the device writes -- every one of the 30 renderable corpus
   entries records at least one layer.
4. **v1 point channels are rounded.** ``rmscene`` decodes a v1 ``SceneLineItemBlock``'s
   speed, direction and pressure as *floats* (``read_float32() * 4`` and friends),
   while ``Point`` constrains them to the uint8 and uint16 ranges the v6 wire format
   uses. Rounding and clamping is what ``rmscene``'s own commented-out code did. The
   alternative is a ``ValidationError`` that costs a whole page the legacy reader also
   lost -- but silently, at whole-page granularity: legacy ``Point`` was a pydantic
   model with ``speed: int``, so a v1 line block raised ``ValidationError`` there too
   and the loader's ``except Exception`` turned it into an empty layer list. The loss
   here is sub-unit on three raw sensor channels of pre-3.0 content only.
5. **A version refusal is a header sniff.** ``UnsupportedPageFormat`` requires
   ``observed_version``, and ``rmscene`` cannot supply it: its header check raises
   ``ValueError("Wrong header: ...")`` against a fixed 43-byte constant and never parses
   the number. So the version is read here, and a refusal is raised only when the sniff
   *succeeds* and names something other than 6 -- never on a failed sniff, which falls
   through to the parser and becomes ``CorruptPageData``.

``rmscene``'s logger is quieted per call by a restoring, *reference-counted* context
manager, never at import time and never in ``__init__``. Importing a parser must not
reconfigure the host application's logging, and a global ``setLevel`` leaks across a
parallel, randomised test run. Reference counting is what makes the restore correct when
two decodes overlap: save-and-restore alone has the inner decode save the outer decode's
*suppressed* level and put it back, which leaks the suppression for the life of the
process -- the very failure scoping was chosen to avoid. A filter would not do, because
``rmscene`` logs through per-module child loggers and a filter on the parent is never
consulted for them, while an effective level is.
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError
from rmscene import scene_items as si
from rmscene.scene_stream import (
    Block,
    PageInfoBlock,
    UnreadableBlock,
    build_tree,
    read_blocks,
)
from rmscene.scene_tree import SceneTree

from rmspec.domain.errors import CorruptPageData, RmspecError, UnsupportedPageFormat
from rmspec.domain.models import (
    Layer,
    PageContent,
    PageDefect,
    PageDefectCode,
    PenColor,
    PenType,
    Point,
    Stroke,
    TextBlock,
    pen_from_wire,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["SceneCodec"]

_SUPPORTED_SCENE_VERSIONS: Final = ("6",)
"""Scene versions this codec decodes. Private on purpose.

``ports/formats.py`` states that ``PageCodec`` "publishes no supported-version set" and
that "the observed version is carried on the raised error, not returned for callers to
compare against integers". Exporting this tuple would hand a caller -- ``cli`` is allowed
to import this package -- exactly the set the port declined to publish, and invite the
version branching the port exists to prevent. Its only legitimate use is constructing
``UnsupportedPageFormat`` inside this module.
"""

_SYNTHESISED_LAYER_NAME: Final = "Layer 1"
"""Name the legacy reader invented when items arrived with no owning layer group."""

_HEADER_SNIFF_LENGTH: Final = 43
"""Bytes of a scene file the version declaration lives in, per ``rmscene.HEADER_V6``."""

_HEADER_VERSION: Final = re.compile(rb"version=(\d+)")
"""The one field of the header this codec reads. Anything else is the parser's business."""

_HEADER_PREFIX: Final = b"reMarkable"
"""What a scene file's header opens with, per ``rmscene.HEADER_V6``.

Required before the version is believed. Without it, any file that happens to carry
``version=N`` in its first 43 bytes -- a yaml front matter, a plist, a lockfile -- was
refused as an unsupported *scene* version rather than reported as not a scene file, which
is the wrong thing to tell someone who ran ``rmspec inspect rm`` on the wrong path.
"""

_BLOCK_LENGTH_BYTES: Final = 4
"""Width of the little-endian block-length field that opens every block header."""

_BLOCK_HEADER_LENGTH: Final = 8
"""Total block header: the 4-byte length, then unknown, min version, version, type."""

_PARSER_LOGGER_NAME: Final = "rmscene"
"""Logger whose per-block warnings a decode suppresses, by name rather than by import."""

_UINT8_MAX: Final = 255
"""Full-scale value of the ``direction`` and ``pressure`` channels."""

_UINT16_MAX: Final = 65535
"""Full-scale value of the ``speed`` and ``width`` channels."""


@dataclass(slots=True)
class _Defects:
    """Page-scoped accumulator for the degradations a decode survived."""

    entries: list[PageDefect] = field(default_factory=list)

    def add(self, code: PageDefectCode, detail: str) -> None:
        """Record one degradation.

        Parameters
        ----------
        code
            The closed-set code a use case may branch on.
        detail
            Free text for a person reading a warning. Never parsed.
        """
        self.entries.append(PageDefect(code=code, detail=detail))


@dataclass(slots=True)
class _LayerBuilder:
    """Mutable stand-in for one frozen :class:`Layer` while its items are collected."""

    name: str
    visible: bool
    strokes: list[Stroke] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Whether this layer collected nothing at all.

        Returns
        -------
        bool
            ``True`` when the layer has neither strokes nor text.
        """
        return not self.strokes and not self.text_blocks

    def build(self) -> Layer:
        """Freeze the collected items into a domain layer.

        Returns
        -------
        Layer
            The layer, with strokes and text in collection order.
        """
        return Layer(
            name=self.name,
            visible=self.visible,
            strokes=tuple(self.strokes),
            text_blocks=tuple(self.text_blocks),
        )


class SceneCodec:
    """Decode one page's v6 scene bytes into :class:`PageContent`.

    Implements ``rmspec.domain.ports.formats.PageCodec``. Stateless and cheap to
    construct: the composition root can bind it at any scope, and a test needs no
    fixture to build one.
    """

    def decode_page(self, raw: bytes, page_ref: str, /) -> PageContent:
        """Decode complete scene bytes into layers, strokes, and text.

        Parameters
        ----------
        raw
            The entire, uninterpreted contents of one page's scene file.
        page_ref
            What to call these bytes when reporting a failure -- the page uuid when the
            caller holds one, the path the user typed when it does not. Never resolved,
            never validated, and never reflected in the return value.

        Returns
        -------
        PageContent
            Layers in render order plus the defects the decode accepted. Empty layers
            with empty defects is certain evidence of a genuinely blank page *for a
            zero-byte artifact*, which is what the firmware's stubs are, and only for
            that: a non-empty artifact that yields no layer carries a defect saying so,
            because a scene file the device wrote always declares at least one layer.

        Raises
        ------
        UnsupportedPageFormat
            The bytes declare a scene version this codec does not decode.
        CorruptPageData
            The bytes are not a decodable scene file: the parser refused them, or they
            are a partial write -- no blocks, block framing that does not consume the
            file exactly, or a missing preamble. A parser exception is chained through
            ``__cause__`` and never re-exported, and the byte offset reached before the
            failure is carried on the error.
        """
        if not raw:
            return PageContent()
        declared = _declared_version(raw)
        if declared is not None and declared not in _SUPPORTED_SCENE_VERSIONS:
            raise UnsupportedPageFormat(
                page_uuid=page_ref,
                observed_version=declared,
                supported_versions=_SUPPORTED_SCENE_VERSIONS,
            )
        stream = io.BytesIO(raw)
        defects = _Defects()
        try:
            with _quiet_parser():
                blocks = list(read_blocks(stream))
                tree = SceneTree()
                build_tree(tree, blocks)
            _record_unreadable(blocks, defects)
            layers = _convert_tree(tree, defects)
        except RmspecError:
            raise
        except Exception as err:
            reason = str(err).strip() or "the parser reported no detail"
            raise CorruptPageData(
                page_uuid=page_ref,
                detail=f"{type(err).__name__}: {reason}",
                offset=stream.tell(),
            ) from err
        # After the parser, not before: a mid-block cut must still surface the parser's
        # own exception through `__cause__`, and these three checks only catch what the
        # parser accepted in silence.
        _reject_partial_write(raw, blocks, page_ref)
        if not layers:
            defects.add(
                PageDefectCode.ITEM_DROPPED,
                f"{len(blocks)} block(s) decoded to no layer at all, so any ink this "
                f"artifact holds is missing; a scene file the device wrote declares at "
                f"least one layer",
            )
        return PageContent(layers=layers, defects=tuple(defects.entries))


def _record_unreadable(blocks: list[Block], defects: _Defects, /) -> None:
    """Turn each block the parser could not read into a defect.

    ``rmscene`` contains its own per-block failures: ``Block.read`` catches every
    exception, logs a warning and yields an ``UnreadableBlock`` in place of the block, so
    ``read_tree`` returns a tree with that item simply missing. That is how a stroke
    whose wire tool id the parser's own ``Pen`` enum does not know disappears -- the
    substitution defects below never see it, because it never reaches the tree.

    The legacy reader lost that fact to a log line it had explicitly silenced. Counting
    it here does not change a single layer or stroke, so it cannot move a differential
    hash; it only stops "some ink is missing from this page" from being invisible.

    Parameters
    ----------
    blocks
        Every block read from the file, in file order.
    defects
        Page-scoped accumulator for degradations.
    """
    for block in blocks:
        if isinstance(block, UnreadableBlock):
            defects.add(
                PageDefectCode.ITEM_DROPPED,
                f"the parser could not read a block of type {block.info.block_type}: "
                f"{block.error}",
            )


def _reject_partial_write(raw: bytes, blocks: list[Block], page_ref: str, /) -> None:
    """Refuse an artifact the parser accepted but that the bytes show to be incomplete.

    ``rmscene``'s ``TaggedBlockReader`` returns ``None`` -- a clean end of iteration --
    when the 4-byte block-length read hits end of file, so a file truncated on or within
    three bytes of a block boundary raises nothing, yields no ``UnreadableBlock``, and
    decodes to the same empty ``PageContent`` a zero-byte stub does. That collapse is
    what this function prevents. Every check here is decidable from the bytes alone:

    1. **No blocks.** A v6 header and nothing else. A page the device wrote always
       carries ``AuthorIdsBlock`` + ``MigrationInfoBlock`` + ``PageInfoBlock``, so the
       smallest real file is 160 bytes, not 43. Zero blocks is a flushed header whose
       writer died, not a blank page.
    2. **Framing does not consume the file exactly.** Walking the length-prefixed
       frames from the end of the header must land on the last byte. A remainder too
       short to hold a block header, or a declared length that runs off the end, is a
       truncated tail.
    3. **No ``PageInfoBlock``.** The firmware writes the preamble first and always;
       its absence in a file that has blocks means the file stops inside the preamble.

    A cut that passes all three keeps a complete preamble and exact framing and cannot
    be told from a short-but-valid file by inspection; :meth:`SceneCodec.decode_page`
    records a defect for those instead of guessing.

    Parameters
    ----------
    raw
        The artifact's bytes, non-empty.
    blocks
        Every block the parser produced, in file order.
    page_ref
        What to call these bytes on the error.

    Raises
    ------
    CorruptPageData
        The artifact is a partial write. No parser exception is chained, because the
        parser did not object; the offset is where the evidence is.
    """
    if not blocks:
        raise CorruptPageData(
            page_uuid=page_ref,
            detail="a v6 header with no blocks: the artifact is a partial write, not a blank page",
            offset=_HEADER_SNIFF_LENGTH,
        )
    end = _framing_end(raw)
    if end != len(raw):
        raise CorruptPageData(
            page_uuid=page_ref,
            detail=(
                f"block framing ends at byte {end} of {len(raw)}: the artifact's tail is "
                f"truncated, and the parser reads a short block length as end of file"
            ),
            offset=min(end, len(raw)),
        )
    if not any(isinstance(block, PageInfoBlock) for block in blocks):
        raise CorruptPageData(
            page_uuid=page_ref,
            detail=(
                f"{len(blocks)} block(s) and no page info block: the artifact stops "
                f"inside the preamble every scene file the device writes begins with"
            ),
            offset=len(raw),
        )


def _framing_end(raw: bytes, /) -> int:
    """Walk the length-prefixed block frames and report where they end.

    Parameters
    ----------
    raw
        The artifact's bytes.

    Returns
    -------
    int
        The offset the walk lands on. Equal to ``len(raw)`` for a file whose frames
        consume it exactly; less, when a remainder is too short to hold a block header;
        greater, when a declared block length runs off the end.
    """
    position = _HEADER_SNIFF_LENGTH
    while position < len(raw):
        if position + _BLOCK_HEADER_LENGTH > len(raw):
            return position
        declared = raw[position : position + _BLOCK_LENGTH_BYTES]
        position += _BLOCK_HEADER_LENGTH + int.from_bytes(declared, "little")
    return position


@dataclass(slots=True)
class _QuietParser:
    """Reference-counted suppression of the parser's logger, shared by every decode.

    Module-level state, and deliberately: two overlapping decodes must not each save
    and restore the logger's level independently. The inner one would save the level
    the outer one had already lowered and put *that* back, leaving the suppression in
    place for the life of the process. Counting entries means the level is saved once,
    on the outermost entry, and restored once, when the last decode leaves.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    depth: int = 0
    restore: int = logging.NOTSET

    def acquire(self) -> None:
        """Suppress the parser's logger, saving the level if nothing else has."""
        logger = logging.getLogger(_PARSER_LOGGER_NAME)
        with self.lock:
            if self.depth == 0:
                self.restore = logger.level
                logger.setLevel(logging.ERROR)
            self.depth += 1

    def release(self) -> None:
        """Restore the parser's logger once the last decode has left."""
        logger = logging.getLogger(_PARSER_LOGGER_NAME)
        with self.lock:
            self.depth -= 1
            if self.depth == 0:
                logger.setLevel(self.restore)


_QUIET_PARSER: Final = _QuietParser()
"""The one suppression counter. Never reassigned, so no ``global`` statement exists."""


@contextlib.contextmanager
def _quiet_parser() -> Iterator[None]:
    """Silence ``rmscene``'s per-block warnings for the duration of one decode.

    The parser logs a warning for every block it cannot read and for every trailing
    byte it did not consume, and the v6 format evolves faster than the parser does, so
    a real page produces several on a decode that succeeded. The suppression is scoped
    and restored rather than applied at import: a library that reconfigures another
    library's logger for the life of the process is a library that breaks its host's
    logging, and under a parallel randomised test run it breaks unrelated tests.

    Yields
    ------
    None
        With the ``rmscene`` logger raised to ``ERROR``, restored on the way out even
        when the decode raises, and even when another decode is running concurrently.
    """
    _QUIET_PARSER.acquire()
    try:
        yield
    finally:
        _QUIET_PARSER.release()


def _declared_version(raw: bytes, /) -> str | None:
    """Read the scene version out of the file header, without parsing the file.

    Parameters
    ----------
    raw
        The scene file's bytes.

    Returns
    -------
    str | None
        The declared version as it was written, or ``None`` when the first bytes are not
        a scene header carrying a version declaration. ``None`` means "let the parser
        decide": these bytes may be a truncated v6 file, which is ``CorruptPageData``,
        and refusing on a failed sniff would turn every one of those into a version
        complaint. A version is only believed inside a header that opens the way a scene
        file's does, so an unrelated file mentioning ``version=`` is not misreported as
        a scene of an unsupported version.
    """
    head = raw[:_HEADER_SNIFF_LENGTH]
    if not head.startswith(_HEADER_PREFIX):
        return None
    match = _HEADER_VERSION.search(head)
    if match is None:
        return None
    return match.group(1).decode()


def _convert_tree(tree: SceneTree, defects: _Defects, /) -> tuple[Layer, ...]:
    """Walk the scene tree root and convert each top-level group into a layer.

    An ``rmscene`` type appears in this signature and in the two below because they are
    module-private. The rule the port sets, and the one ``tests/`` asserts, is that no
    *public* symbol of this distribution names a parser type.

    Parameters
    ----------
    tree
        The parsed scene tree.
    defects
        Page-scoped accumulator for degradations.

    Returns
    -------
    tuple[Layer, ...]
        Layers in render order. Empty when the page holds nothing, which is the one
        state that means "blank" rather than "damaged".
    """
    layers = [
        _convert_group(child, tree, defects)
        for child in tree.root.children.values()
        if isinstance(child, si.Group)
    ]
    if layers:
        return tuple(layers)

    # No layer group at root level: collect every leaf item into one invented layer,
    # exactly as the legacy reader did, and say so rather than logging it.
    builder = _LayerBuilder(name=_SYNTHESISED_LAYER_NAME, visible=True)
    for item in tree.walk():
        _collect_item(item, builder, defects)
    if builder.is_empty:
        return ()
    defects.add(
        PageDefectCode.LAYER_SYNTHESISED,
        f"items arrived with no owning layer group, so {_SYNTHESISED_LAYER_NAME!r} was "
        f"invented to hold them",
    )
    return (builder.build(),)


def _convert_group(group: si.Group, tree: SceneTree, defects: _Defects, /) -> Layer:
    """Convert one scene group -- a layer in the tablet's UI -- into a layer.

    Parameters
    ----------
    group
        The group to convert.
    tree
        The parsed scene tree, for resolving a nested group by node id.
    defects
        Page-scoped accumulator for degradations.

    Returns
    -------
    Layer
        The layer, with every item collected in traversal order.
    """
    builder = _LayerBuilder(
        name=group.label.value if group.label else "",
        visible=group.visible.value if group.visible else True,
    )
    for child in group.children.values():
        if isinstance(child, si.Group):
            # A nested group is flattened into this layer. The tree lookup and its
            # fallback are relocated verbatim: `SceneGroupItemBlock` already stores the
            # resolved node as the child, so the two branches see the same object
            # today, and running both would emit every nested stroke twice on a page
            # that would still look plausible. Do not tidy this into one path.
            try:
                resolved = tree[child.node_id]
                nested = resolved.children.values()
            except (KeyError, AttributeError):
                nested = child.children.values()
            for nested_child in nested:
                _collect_item(nested_child, builder, defects)
        else:
            _collect_item(child, builder, defects)
    return builder.build()


def _collect_item(item: object, builder: _LayerBuilder, defects: _Defects, /) -> None:
    """Add one scene item to the layer being built.

    Parameters
    ----------
    item
        The scene item to add, or ``None`` for a member the CRDT sequence records as
        deleted.
    builder
        The layer collecting it.
    defects
        Page-scoped accumulator for degradations.
    """
    if item is None:
        # A CRDT tombstone, not an item of an unknown type. An item block whose value
        # subblock is absent records that the member was *deleted* -- the parser reads its
        # `deleted_length` and yields `None` in its place -- so this is a stroke the user
        # erased. It is skipped and not counted: `ITEM_DROPPED` says "some ink or text is
        # missing from an otherwise good page", which would be a lie about ink the user
        # deliberately removed. The reference corpus makes the cost concrete: 1237 of these
        # across 24 of its 30 renderable pages, i.e. `--strict` failing on almost every
        # real document while reporting nothing a reader could act on. Skipping changes no
        # layer and no stroke, so the recorded legacy counts are untouched.
        return
    if isinstance(item, si.Line):
        stroke = _convert_line(item, defects)
        if stroke is not None:
            builder.strokes.append(stroke)
    elif isinstance(item, si.Text):
        block = _convert_text(item, defects)
        if block is not None:
            builder.text_blocks.append(block)
    elif isinstance(item, si.Group):
        for child in item.children.values():
            _collect_item(child, builder, defects)
    elif isinstance(item, si.GlyphRange):
        # A highlight over a pdf's own text. The legacy reader skipped it silently;
        # skipping is kept, and saying so is the change -- the ink really is missing
        # from the render, which is what ITEM_DROPPED names.
        defects.add(
            PageDefectCode.ITEM_DROPPED,
            f"text highlight of {len(item.rectangles)} rectangle(s) is not rendered",
        )
    else:
        defects.add(
            PageDefectCode.ITEM_DROPPED,
            f"scene item of type {type(item).__name__} is not understood",
        )


def _convert_line(line: si.Line, defects: _Defects, /) -> Stroke | None:
    """Convert one scene line into a stroke.

    Parameters
    ----------
    line
        The line to convert.
    defects
        Page-scoped accumulator for degradations.

    Returns
    -------
    Stroke | None
        The stroke, or ``None`` when the wire values are outside what the domain can
        represent at all -- a negative thickness or starting length. One unusable
        stroke is a dropped item on an otherwise good page, never a failed decode.
    """
    tool = int(line.tool)
    pen = pen_from_wire(tool)
    if pen is None:
        defects.add(
            PageDefectCode.UNKNOWN_PEN_SUBSTITUTED,
            f"tool id {tool} is not a known pen, so {PenType.FINELINER_1.name} was used",
        )
        pen = PenType.FINELINER_1
    try:
        color = PenColor(int(line.color))
    except ValueError:
        defects.add(
            PageDefectCode.UNKNOWN_COLOR_SUBSTITUTED,
            f"colour index {int(line.color)} is not known, so {PenColor.BLACK.name} was used",
        )
        color = PenColor.BLACK
    try:
        return Stroke(
            pen=pen,
            color=color,
            thickness_scale=line.thickness_scale,
            points=tuple(_convert_point(point) for point in line.points),
            starting_length=line.starting_length,
        )
    except ValidationError as err:
        defects.add(
            PageDefectCode.ITEM_DROPPED,
            f"stroke carries a value the domain refuses: {err.error_count()} field(s)",
        )
        return None


def _convert_point(point: si.Point, /) -> Point:
    """Convert one stylus sample.

    Parameters
    ----------
    point
        The sample to convert.

    Returns
    -------
    Point
        The sample, with its three float-encoded v1 channels rounded and clamped into
        the ranges the v6 wire format -- and therefore the domain -- uses.
    """
    return Point(
        x=point.x,
        y=point.y,
        speed=_channel(point.speed, _UINT16_MAX),
        direction=_channel(point.direction, _UINT8_MAX),
        width=_channel(point.width, _UINT16_MAX),
        pressure=_channel(point.pressure, _UINT8_MAX),
    )


def _channel(value: float, limit: int, /) -> int:
    """Round one raw stylus channel into its wire range.

    Parameters
    ----------
    value
        The channel as the parser read it: an ``int`` for a v2 line block, a ``float``
        for a v1 one.
    limit
        Full-scale value of the channel, ``255`` or ``65535``.

    Returns
    -------
    int
        The channel, rounded to the nearest integer and clamped to ``0..limit``.
    """
    return min(limit, max(0, round(value)))


def _convert_text(text: si.Text, defects: _Defects, /) -> TextBlock | None:
    """Convert one typed-text block, flattening its CRDT sequence.

    Parameters
    ----------
    text
        The text item to convert.
    defects
        Page-scoped accumulator for degradations.

    Returns
    -------
    TextBlock | None
        The block, or ``None`` when its width is not positive. ``TextBlock.width`` is
        constrained ``> 0`` and its docstring makes skipping such an item the codec's
        obligation: the legacy model accepted a zero width and drew nothing from it.
    """
    if not text.width > 0:
        defects.add(
            PageDefectCode.ITEM_DROPPED,
            f"text block has a non-positive width of {text.width}, so it draws nothing",
        )
        return None
    return TextBlock(
        pos_x=text.pos_x,
        pos_y=text.pos_y,
        width=text.width,
        # Integer members of the sequence are paragraph formatting codes, not text.
        text="".join(value for value in text.items.values() if isinstance(value, str)),
    )
