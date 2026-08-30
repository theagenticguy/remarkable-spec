"""The v6 scene seam, both directions: bytes to page content, and ink back onto bytes.

Two adapters, one parser. :class:`SceneCodec` implements
``rmspec.domain.ports.formats.PageCodec`` and :class:`AppendOnlySceneWriter` implements
``rmspec.domain.ports.formats.SceneAppender``. They share this module rather than sitting
in two files so that "exactly one module in the workspace imports ``rmscene``" stays a
one-element fact -- ``tests/test_formats_containment.py`` asserts that number -- and so the
writer reuses :func:`_reject_lossy_rewrite`, :func:`_quiet_parser` and
:func:`_parser_detail` in place instead of importing three private names across a module
boundary. The lossless precondition was already here before the writer was: it is a write
concern, and the section below has always said so.

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

Seven changes, all forced:

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
6. **A highlighter's real colour is read out of the bytes the parser left behind.** Measured
   against firmware 3.27.3.0: every highlighter stroke reports ``PenColor`` id 9 whatever
   colour it was drawn in, and carries its true colour in an optional tagged field --
   index 8, ``Byte4`` -- that ``rmscene`` 0.7.0 does not decode. Two strokes drawn in two
   visibly different colours therefore reached the renderer identical, so every non-yellow
   highlight this project drew was the wrong colour. :func:`_read_color_overrides` decodes
   that one field and :attr:`Stroke.color_override` carries it; the enum is untouched,
   because a pen stroke has no such field and its colour id is correct. Independently of
   the colour, leftover bytes *nothing here understood* now record
   ``PageDefectCode.BLOCK_BYTES_UNREAD`` with those bytes in the detail, which is what the
   suppressed warning used to say and could not be acted on. A tail this codec does decode
   is not reported: after change 6 those bytes are read, so calling them unread would be
   false.
7. **The page's own typed text is read at last.** ``_collect_item`` handles a text item, but it
   is only ever reached from the scene-tree walk, and a page's typed text is not in the tree:
   it arrives in one page-scoped block that ``rmscene`` hands over as ``SceneTree.root_text``
   and never adds to any group. So every page a human typed on decoded to zero text blocks.
   Pre-existing and not a regression -- legacy ``formats/rm_file.py`` had the identical shape,
   so typed text has never reached a render from this project. :func:`_convert_root_text` reads
   it, and the domain carries it page-level on ``PageContent.text_blocks`` rather than on layer
   0: the block names no layer, so stapling it to one would state a provenance the bytes do not
   support, and the first caller to draw layers separately would find it. ``Layer.text_blocks``
   is left in place and left empty -- ``rmscene`` 0.7.0 decodes the format's *layer-owned* text
   item to nothing, so no file can fill it today -- and its own docstring records that.

The lossless rewrite precondition
---------------------------------
:meth:`SceneCodec.check_rewritable` is the other half of this module, and it belongs to
writing rather than reading. Measured: ``write_blocks(read_blocks(raw))`` reproduces ``raw``
byte for byte on all 30 non-empty artifacts of the reference corpus and on the live page,
*even though* ``rmscene`` 0.7.0 warns *"Some data has not been read"* on most of them --
per-block ``extra_data`` carries what it does not model. That is a fact about one firmware and
one parser version rather than a guarantee, so it is checked per call instead of assumed once.
A silently lossy rewrite of a page is the worst outcome available here: the page is the only
copy of something a human made by hand.

``rmscene``'s logger is quieted per call by a restoring, *reference-counted* context
manager, never at import time and never in ``__init__``. Importing a parser must not
reconfigure the host application's logging, and a global ``setLevel`` leaks across a
parallel, randomised test run. Reference counting is what makes the restore correct when
two decodes overlap: save-and-restore alone has the inner decode save the outer decode's
*suppressed* level and put it back, which leaks the suppression for the life of the
process -- the very failure scoping was chosen to avoid. A filter would not do, because
``rmscene`` logs through per-module child loggers and a filter on the parent is never
consulted for them, while an effective level is.

Suppressing that logger no longer discards what it was saying. Its
*"Some data has not been read"* warning is the one that found change 6 above, and the two
facts behind it -- which block stopped short, and what the leftover bytes were -- are
values on the returned content now rather than a log line nobody sees.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError
from rmscene import scene_items as si
from rmscene.crdt_sequence import END_MARKER, CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import (
    Block,
    PageInfoBlock,
    SceneItemBlock,
    SceneLineItemBlock,
    UnreadableBlock,
    build_tree,
    read_blocks,
    write_blocks,
)
from rmscene.scene_tree import SceneTree
from rmscene.tagged_block_common import CrdtId

from rmspec.domain.errors import (
    CorruptPageData,
    RmspecError,
    SceneRewriteUnsafe,
    UnsupportedPageFormat,
    UsageError,
)
from rmspec.domain.models import (
    Layer,
    PageContent,
    PageDefect,
    PageDefectCode,
    PenColor,
    PenType,
    Point,
    Rgba,
    Stroke,
    TextBlock,
    pen_from_wire,
)
from rmspec.domain.ports.formats import SceneEdit

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

__all__ = ["AppendOnlySceneWriter", "SceneCodec"]

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

_COLOR_OVERRIDE_INDEX: Final = 8
"""Tagged-field index a highlighter's true colour is written at, on a scene line item."""

_TAG_BYTE4: Final = 0x4
r"""``rmscene``'s own ``TagType.Byte4``, restated because its enum is not imported for this.

The measurement that found the field read its header as one byte and therefore as index 8,
tag ``0x1``. The header is ``84 01``, and ``0x84`` has its continuation bit set, so it is a
two-byte varuint worth 132: index ``132 >> 4 == 8``, tag ``132 & 0xF == 0x4``. Reading it as
a byte makes the 4-byte payload look like 5 bytes and sends a reader hunting a field that is
not there, which is why the width is derived and never assumed.
"""

_TAG_INDEX_SHIFT: Final = 4
"""Bits a tagged-field header's index sits above its 4-bit tag type."""

_TAG_TYPE_MASK: Final = 0xF
"""Low nibble of a tagged-field header, which is its tag type."""

_ARGB_LENGTH: Final = 4
"""Payload width of a ``Byte4`` field: one little-endian ``uint32``, read as ARGB."""

_VARUINT_MAX_BYTES: Final = 5
"""Longest varuint that can encode a 32-bit value, so a malformed tail cannot spin."""

_VARUINT_CONTINUATION: Final = 0x80
"""Bit that marks a varuint byte as having a successor."""

_VARUINT_PAYLOAD: Final = 0x7F
"""Bits of a varuint byte that carry value."""

_VARUINT_SHIFT: Final = 7
"""Value bits each varuint byte contributes."""

_BYTE_MASK: Final = 0xFF
"""One channel's worth of a packed colour."""

_MAX_AUTHOR_ID: Final = 0xFF
"""Widest author component a ``CrdtId`` can carry: ``rmscene`` writes ``part1`` as a uint8.

Not cosmetic. A fresh author id is one past the highest already in the artifact, so a page
that already uses 255 leaves none free -- and minting 256 anyway would raise out of the
writer after the caller had been told the append was going ahead. It is refused by name
instead.
"""


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

    :meth:`check_rewritable` is deliberately *not* on that port. ``PageCodec`` has exactly one
    method so that a fake is one canned return value, every fake in the workspace is annotated
    against it, and the scene *write* path has no port of its own yet. A second method here
    would make all of those stop satisfying the port they were written for, to publish a
    precondition only a writer calls; a composition root that needs it reaches this adapter.
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
            Layers in render order, the page's own typed text, and the defects the decode
            accepted. Empty layers with empty text and empty defects is certain evidence of
            a genuinely blank page *for a zero-byte artifact*, which is what the firmware's
            stubs are, and only for that: a non-empty artifact that yields no layer carries
            a defect saying so, because a scene file the device wrote always declares at
            least one layer.

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
            overrides = _read_color_overrides(blocks, defects)
            layers = _convert_tree(tree, defects, overrides)
            page_text = _convert_root_text(tree, defects)
        except RmspecError:
            raise
        except Exception as err:
            raise CorruptPageData(
                page_uuid=page_ref,
                detail=_parser_detail(err),
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
        return PageContent(layers=layers, text_blocks=page_text, defects=tuple(defects.entries))

    def check_rewritable(self, raw: bytes, page_ref: str, /) -> None:
        """Refuse these bytes unless this build can re-encode them byte for byte.

        The precondition an additive scene rewrite must pass *before* its bytes are handed
        back, and it is checked on every call rather than established once. The check is the
        whole claim: re-read the artifact, re-encode the block list **unmodified**, and
        compare. Anything but equality means the round trip drops something this parser did
        not model, and the thing dropped would be part of a page that is the only copy of
        something a human made by hand.

        Cheap enough to be unconditional. A page is tens of kilobytes, and this costs one
        extra parse and one extra serialise to convert an assumption about a firmware and a
        parser version into a checked fact about the artifact in hand.

        Parameters
        ----------
        raw
            The entire, uninterpreted contents of one page's scene file, read immediately
            before the rewrite this guards. Checking a stale copy checks nothing: the bytes
            passed here must be the bytes the rewrite is derived from.
        page_ref
            What to call these bytes when reporting a failure -- the page uuid when the caller
            holds one, the path the user typed when it does not. Never resolved and never
            validated.

        Raises
        ------
        CorruptPageData
            The bytes are not a decodable scene file, so there is nothing to rewrite. The
            parser's exception is chained through ``__cause__`` and never re-exported.
        SceneRewriteUnsafe
            The bytes decode and this build cannot reproduce them: a zero-byte artifact, a
            writer that raised, or a re-encode that differs from the input.
        """
        _reject_lossy_rewrite(raw, page_ref)


class AppendOnlySceneWriter:
    r"""Add ink to a page by concatenating new blocks onto its byte-for-byte untouched bytes.

    Implements ``rmspec.domain.ports.formats.SceneAppender``. Stateless and cheap to
    construct, like :class:`SceneCodec`: the composition root can bind it at any scope, and
    it performs no I/O.

    The design decision this class is named for
    ------------------------------------------
    Two shapes were available and this is (b).

    **(a) Full re-encode.** Decode, add the new blocks to the block list, hand all of it to
    ``write_blocks``. Measured here, and it is not a weak measurement:
    ``write_blocks(read_blocks(raw)) == raw`` byte for byte on all 30 non-empty artifacts of
    the reference corpus and on the live 18,355-byte page, with no writer options at all,
    *while* ``rmscene`` 0.7.0 reports that it did not read all of most of them. Per-block
    ``extra_data`` is what carries the unmodelled bytes through.

    **(b) Append-only.** Parse read-only to find the layer and the author ids, serialise
    *only the new blocks*, strip the writer's own header from that output, and concatenate
    onto the original bytes, which are never re-encoded and never touched.

    The argument for (b) is not that (a) is unsafe here. It is that **the two produce the
    same bytes, so (b) is free.** A block's serialisation is self-contained: the writer emits
    its header and then each block behind its own length prefix, with no back-references and
    no shared table. So once :func:`_reject_lossy_rewrite` has passed -- every original block
    re-encodes to its original bytes, in order -- writing ``original + new`` produces exactly
    ``raw + tail``, which is what this class assembles directly. Same output, two different
    reasons to believe the human's existing ink survived:

    * under (a) it survived **because a check was run, was correct, and was run against
      these bytes**;
    * under (b) it survived because the original is a literal prefix of the result, which is
      a property of the code rather than of a check.

    The costs are asymmetric in one direction only. A maintainer who someday relaxes the
    precondition -- skips it for a 200-page notebook, caches its verdict, or checks a copy
    read a moment earlier -- turns (a) from "refuse" into "silently corrupt", and turns (b)
    into "lose the fidelity-of-read guarantee below while still being unable to damage a
    stroke". A page is the only copy of something a human made by hand, so the failure that
    is *structurally* impossible is worth having even where the measured failure rate of the
    alternative is zero.

    Prior art disagrees with the measurement rather than with the choice.
    ``remarkable-mcp`` also chose (b), and states its reason: *"On newer firmware a full
    read_blocks -> write_blocks round-trip is lossy: rmscene does not understand every field
    of the SceneInfo block and silently drops a few bytes, which can corrupt the page."* That
    is false on firmware 3.27.3.0 with ``rmscene`` 0.7.0, measured on 30 artifacts. Both
    claims can be true of different firmware and different pages, and the disagreement is
    itself the argument: a losslessness claim that two projects measure differently is not a
    claim to hang a page-destroying default on.

    Why the precondition is still run, given the prefix is structural
    ----------------------------------------------------------------
    Because it guards the **read**, not the write. Concatenation protects the existing bytes
    on its own; what it cannot protect is the facts this class reads *out* of those bytes and
    derives the new blocks from -- which layer to attach to, which author ids are taken. If
    this build's model of the artifact is incomplete enough that it cannot reproduce the
    bytes, then everything it thinks it read is equally suspect, and an append derived from a
    misread scene attaches ink to the wrong node or mints under a colliding id. So
    :func:`_reject_lossy_rewrite` is a precondition on the parse, and the two halves are
    complementary rather than redundant. It also supplies the two refusals by name that this
    class would otherwise have to restate: a zero-byte artifact, and bytes that do not
    decode.

    What that means for the regression test, which is the interesting half
    ---------------------------------------------------------------------
    Prior art's strongest available assertion is a pair: that the original bytes survive as
    an exact prefix, **and** that a naive round trip does *not* byte-match -- the second half
    being what proves the first is measuring the property rather than passing by luck. **The
    second half is not expressible here.** Our round trip *is* byte-identical, so there is no
    lossy naive rewrite for the prefix to differ from. ``tests`` therefore pins the same fact
    from the other side: an append's output is asserted **equal** to a full re-encode of the
    same block list. The day a firmware or parser change makes (a) lossy, that assertion
    moves and says so out loud, instead of (b)'s insurance being quietly cashed with nobody
    told. And it records honestly that with the precondition in front, (a) and (b) *cannot*
    differ today -- so the check is what protects the page now, and the shape is what
    protects it if the check ever stops being run.

    What is appended, and what is deliberately not touched
    -----------------------------------------------------
    One ``SceneLineItemBlock`` per stroke, and nothing else. That ink needs nothing else is
    what makes append-only viable for it at all, and each of the three candidates was checked
    rather than assumed:

    * **The page info block** counts loads, merges, text characters and text lines. None of
      those is a stroke count, so ink does not move any of them. The live spike had to update
      two of them because it wrote *text*.
    * **The scene info block** names a ``current_layer``, which looks like the layer a write
      should target. It is ``CrdtId(0, 0)`` -- the unset end marker -- on all 30 corpus
      artifacts, so it is not a layer selector, and this class does not read it.
    * **The author id block** is the page's author *directory*, mapping id to uuid. It stays
      byte-frozen, and the port therefore takes no author uuid; see below.

    Tombstones are untouched by construction rather than by care. A tombstone is a
    ``SceneLineItemBlock`` whose value subblock is absent -- the CRDT record of a stroke the
    user erased, and 6 of the live page's 61 item blocks -- so it lives in the prefix that is
    never re-encoded. It does keep its place in the sequence, and this class reads the layer's
    last member *including* tombstones when it chains the new ink on, because a tombstone
    occupies an ordering position whether or not it holds a stroke.

    The fresh author id, and the one place the design draft was wrong
    ----------------------------------------------------------------
    Every ``CrdtId`` minted here carries an author component one past the highest the
    artifact already uses, which makes the minted ids collision-free by construction rather
    than by hunting for an unused sequence number. Measured on firmware 3.27.3.0: writing
    under a fresh author id into a page owned by another author was accepted, and xochitl
    **kept the foreign author id through its own re-save** of that page.

    The draft said to take that maximum over "the scene's own ``AuthorIdsBlock``". That is
    **wrong, and the corpus shows it**: every one of the 30 artifacts uses author 0 in its
    ``CrdtId``s while its directory declares only author 1, so the directory is already not
    the set of authors in use. Worse, it is wrong in the exact way that matters -- after one
    append by this class the page holds ids under author *n* that the directory still does not
    mention, so a second append that consulted only the directory would mint author *n*
    again and collide with the first one's ink. The maximum is therefore taken over every
    author component reachable in the parsed blocks; :func:`_author_ids` is that walk.

    Why the port takes no author uuid
    ---------------------------------
    The draft had the caller supply a uuid it owns. A uuid's only home in this format is the
    ``AuthorIdsBlock``, and under append-only that block is byte-frozen -- so recording one
    means appending a *second* author id block, whose merge semantics on this firmware are
    **unmeasured**. Union would register us, first-wins would leave us unregistered, and
    last-wins would erase the page's real author. That is a guess with an unbounded downside,
    and the measurement does not need it: the live write's foreign author id was never
    registered and survived anyway, because a ``CrdtId`` carries its author inline and
    nothing in the render path consults the directory. A parameter whose value cannot reach
    the bytes is worse than no parameter, because it tells a caller their identity was
    recorded when nothing recorded it. ``SceneEdit.author_id`` is the receipt for what *is* in
    the bytes.

    Where the ink lands, and why a synthesised layer is not a target
    ---------------------------------------------------------------
    The last **visible** root-level group, indexed exactly as :meth:`SceneCodec.decode_page`
    indexes layers, so the caller can render the layer named on the receipt. Last because
    layers draw bottom to top in that order and a reply should sit above what it replies to;
    visible because ink on a hidden layer is ink the human cannot see, which is the same
    defect as the typed-text block the tablet preserves and never draws.

    A page whose items hang off the root with no layer group at all is **refused**, and that
    is a deliberate asymmetry with the reader: :func:`_convert_tree` invents a layer to hold
    those items, because a reader has to report the ink somehow. Inventing one here would
    mean writing tree and group blocks -- creating structure in someone else's page rather
    than adding to it -- which is a different operation with different failure modes.

    No bounds check, and that is measured too
    -----------------------------------------
    Nothing here compares a sample against the page's declared paper size. It is tempting,
    since ink off the page is invisible; the corpus refutes it, with 13 of its 30 non-empty
    artifacts carrying strokes outside the declared x range and 17 outside y -- one reaching
    y 81,159 on a page declaring 2,160. A coordinate range is not a validity test on this
    format. Rendering the proposed page with this project's own renderer is, and it is what
    the live spike did before writing.
    """

    def append_strokes(
        self, raw: bytes, page_ref: str, /, *, strokes: tuple[Stroke, ...]
    ) -> SceneEdit:
        """Append strokes to a page's scene bytes, leaving the original bytes a literal prefix.

        Parameters
        ----------
        raw
            The entire, uninterpreted contents of the page's scene file, read immediately
            before this call.
        page_ref
            What to call these bytes when reporting a failure -- the page uuid when the
            caller holds one, the path the user typed when it does not. Never resolved, never
            validated, and never reflected in the returned bytes.
        strokes
            The ink to add, in draw order, with samples already in screen units. Must not be
            empty.

        Returns
        -------
        SceneEdit
            The whole page's new bytes -- ``raw`` followed by the serialised new blocks --
            the author id the ink was minted under, and the index of the layer it landed on.

        Raises
        ------
        UsageError
            ``strokes`` is empty.
        CorruptPageData
            The bytes are not a decodable scene file, so there is nothing to append to.
        SceneRewriteUnsafe
            The bytes decode and this build will not write them: a zero-byte artifact, a
            round trip it cannot reproduce, a block it could not read, no visible layer to
            attach to, no author id left to mint under, or new blocks the writer refused.
        """
        if not strokes:
            raise UsageError(
                subject=f"an append of no strokes to page {page_ref}",
                requirement="at least one stroke",
            )
        blocks = _reject_lossy_rewrite(raw, page_ref)
        site = _append_site(blocks, page_ref)
        author_id = _fresh_author_id(blocks, page_ref)
        minted = _line_blocks(strokes, site=site, author_id=author_id)
        return SceneEdit(
            scene=raw + _serialise_tail(minted, page_ref),
            author_id=author_id,
            layer_index=site.layer_index,
        )


def _reject_lossy_rewrite(raw: bytes, page_ref: str, /) -> list[Block]:
    """Refuse a scene this build cannot reproduce from its own parse of it.

    Three refusals, and each one is a shape that has been produced rather than imagined:

    1. **Nothing to preserve.** A zero-byte artifact -- the stub the firmware writes for an
       unannotated page of a pdf, which is 62 of the reference corpus's 92 files -- carries no
       author ids to allocate a fresh one against, no preamble, and no layer to attach
       anything to. Writing one means *creating* a scene, which is a different operation with
       different failure modes, so it is refused by name here instead of being reported as a
       byte-count mismatch against a 43-byte header.
    2. **The writer raised.** A page whose line blocks use the pre-3.0 v1 encoding reads its
       speed, direction and pressure channels as floats, and the writer packs them as
       integers, so re-encoding raises out of ``struct`` before it produces anything. The
       exception is translated, never re-exported: no third-party or stdlib type crosses this
       seam.
    3. **The bytes differ.** Block *header* versions are not carried across a round trip --
       ``rmscene`` recomputes them from the firmware version handed to the writer -- so an
       artifact written by a firmware whose header versions differ from the writer's defaults
       re-encodes to a different file. Both lengths go in the detail because the length is the
       cheapest description of how it differs, while the comparison itself is over every byte.

    Measured: all 30 non-empty artifacts of the reference corpus, and the live 18,355-byte
    page, pass this with no writer options at all -- byte for byte, and *while* ``rmscene``
    0.7.0 reports that it did not read all of some of them. Per-block ``extra_data`` is what
    makes that true, and it is a property of one firmware and one parser version, which is
    exactly why this function exists instead of a comment claiming the round trip is safe.

    Parameters
    ----------
    raw
        The artifact's bytes.
    page_ref
        What to call these bytes on the error.

    Returns
    -------
    list[Block]
        The parsed blocks, in file order, so a caller that needs both the check and the
        parse pays for one. Returned rather than discarded because the alternative is the
        writer re-reading the same twenty kilobytes it just proved it could reproduce, and a
        second parse is also a second chance for the two to disagree.
        :meth:`SceneCodec.check_rewritable` ignores it, which is what makes the check
        callable on its own.

    Raises
    ------
    CorruptPageData
        The bytes are not a decodable scene file.
    SceneRewriteUnsafe
        The bytes decode and cannot be re-encoded to themselves.
    """
    if not raw:
        raise SceneRewriteUnsafe(
            page_uuid=page_ref,
            detail=(
                "a zero-byte artifact holds no author ids, no preamble and no layer to attach "
                "to, so writing one creates a scene rather than rewriting one"
            ),
        )
    stream = io.BytesIO(raw)
    try:
        with _quiet_parser():
            blocks = list(read_blocks(stream))
    except Exception as err:
        raise CorruptPageData(
            page_uuid=page_ref,
            detail=_parser_detail(err),
            offset=stream.tell(),
        ) from err
    buffer = io.BytesIO()
    try:
        with _quiet_parser():
            write_blocks(buffer, blocks)
    except Exception as err:
        raise SceneRewriteUnsafe(
            page_uuid=page_ref,
            detail=(
                f"re-encoding the unmodified scene raised {_parser_detail(err)}, so this "
                f"build cannot write back what it just read"
            ),
        ) from err
    if buffer.getvalue() != raw:
        raise SceneRewriteUnsafe(
            page_uuid=page_ref,
            detail=(
                f"re-encoding the unmodified scene produced {len(buffer.getvalue())} byte(s) "
                f"from {len(raw)}, so a rewrite would not preserve what this build cannot "
                f"model"
            ),
        )
    return blocks


@dataclass(frozen=True, slots=True)
class _AppendSite:
    """Where new ink attaches, resolved from one read-only parse of the artifact.

    Every field is read out of the bytes on every call and none of them is cached. The
    tablet renumbers scene ids -- a measured page's layer moved from ``CrdtId(0, 11)`` to
    ``CrdtId(1, 334)`` across xochitl's own re-save -- so a remembered ``parent_id`` would
    attach ink to a node that no longer exists, or to a different one.
    """

    layer_index: int
    """Position of the target layer in the tuple :meth:`SceneCodec.decode_page` returns."""

    parent_id: CrdtId
    """Node id of the target layer group, which every new item block names as its parent."""

    left_id: CrdtId
    """Sequence member the first new stroke follows, or the end marker for an empty layer."""


def _append_site(blocks: list[Block], page_ref: str, /) -> _AppendSite:
    """Resolve the layer new ink attaches to, or refuse the page.

    Builds the same scene tree :meth:`SceneCodec.decode_page` builds, from the same block
    list, so the reported ``layer_index`` indexes the same tuple a decode of the result
    produces. Reading the CRDT sequence's key order is what performs the topological sort, so
    it happens inside the guarded block: a page whose left/right ids form a cycle raises from
    there and not from the caller's own iteration later.

    Parameters
    ----------
    blocks
        Every block read from the artifact, in file order.
    page_ref
        What to call the artifact on an error.

    Returns
    -------
    _AppendSite
        The last visible root-level group, with the id of its last existing member.

    Raises
    ------
    CorruptPageData
        The blocks do not assemble into a scene tree, or its sequences do not order.
    SceneRewriteUnsafe
        The scene declares no visible layer, so there is nowhere ink would be seen. Includes
        the page whose items hang off the root with no layer group: a decode invents one to
        report them, and inventing one here would mean creating structure rather than adding
        to it.
    """
    tree = SceneTree()
    try:
        with _quiet_parser():
            build_tree(tree, blocks)
        layers = [child for child in tree.root.children.values() if isinstance(child, si.Group)]
        members = [list(layer.children.keys()) for layer in layers]
    except Exception as err:
        raise CorruptPageData(page_uuid=page_ref, detail=_parser_detail(err)) from err
    visible = [index for index, layer in enumerate(layers) if layer.visible.value]
    if not visible:
        raise SceneRewriteUnsafe(
            page_uuid=page_ref,
            detail=(
                f"{len(layers)} layer group(s) and none of them visible, so there is nowhere "
                f"to attach ink a human would see"
            ),
        )
    chosen = visible[-1]
    existing = members[chosen]
    return _AppendSite(
        layer_index=chosen,
        parent_id=layers[chosen].node_id,
        # Tombstones count. An erased stroke still occupies an ordering position, so the last
        # member is the last member whether or not it holds a value.
        left_id=existing[-1] if existing else END_MARKER,
    )


def _fresh_author_id(blocks: list[Block], page_ref: str, /) -> int:
    """Allocate an author id no ``CrdtId`` in this artifact already uses.

    One past the highest in use, which makes every id minted under it collision-free without
    searching a sequence space. Taken over the whole parse rather than over the author
    directory: the directory is measurably incomplete -- all 30 corpus artifacts carry ids
    under author 0 while declaring only author 1 -- and after one append by this module the
    page holds ids under an author the directory still does not name, so a directory-only
    maximum would hand out the same id twice.

    Parameters
    ----------
    blocks
        Every block read from the artifact, in file order.
    page_ref
        What to call the artifact on an error.

    Returns
    -------
    int
        The fresh author id, at least 1 and at most :data:`_MAX_AUTHOR_ID`.

    Raises
    ------
    SceneRewriteUnsafe
        A block did not read, so its ids are opaque and no id can be shown to be free; or
        the artifact already uses the highest author id the wire format can encode.
    """
    for block in blocks:
        if isinstance(block, UnreadableBlock):
            raise SceneRewriteUnsafe(
                page_uuid=page_ref,
                detail=(
                    f"a block of type {block.get_block_type()} did not read, so the ids "
                    f"inside it are opaque and no fresh author id can be shown to be free"
                ),
            )
    highest = max(_author_ids(blocks), default=0)
    if highest >= _MAX_AUTHOR_ID:
        raise SceneRewriteUnsafe(
            page_uuid=page_ref,
            detail=(
                f"the artifact already uses author id {highest}, and the wire format encodes "
                f"an author as one byte, so no fresh id is available"
            ),
        )
    return highest + 1


def _author_ids(value: object, /) -> Iterator[int]:
    """Yield the author component of every ``CrdtId`` reachable from a parsed value.

    A structural walk rather than a per-block-type enumeration, because an enumeration is a
    list that goes stale silently: a field this module forgot is an author it cannot see, and
    an author it cannot see is a collision it hands to the caller. ``rmscene``'s blocks and
    scene items are all dataclasses, so recursing on :func:`dataclasses.fields` reaches every
    declared field including the ones this module never names -- lww timestamps, anchors,
    migration ids.

    ``si.Point`` is skipped by name, and that is a measured optimisation rather than an
    assumption: its six fields are numbers, none of them an id, and the largest corpus
    artifact holds enough samples that walking them costs 0.45s against 0.034s for the same
    answer. ``tests`` pins that its fields stay numeric.

    Parameters
    ----------
    value
        Anything a parse produced: the block list, a block, a scene item, a container.

    Yields
    ------
    int
        One author component per ``CrdtId`` found, with duplicates. Anything that is not a
        ``CrdtId``, a handled container or a dataclass contributes nothing, which is correct
        for every leaf ``rmscene`` declares -- ``int``, ``float``, ``bool``, ``str``,
        ``bytes``, ``UUID``, ``None`` -- and is why a block that did not read is refused
        before this runs rather than walked into.
    """
    if isinstance(value, CrdtId):
        yield value.part1
        return
    for child in _walkable(value):
        yield from _author_ids(child)


def _walkable(value: object, /) -> Iterator[object]:
    """Yield what one parsed value holds, so :func:`_author_ids` recurses on one thing.

    Split out rather than inlined into the walk above so that "which containers are
    understood" is one small list a reader can check against ``rmscene``'s dataclasses,
    instead of six arms tangled with the recursion.

    Parameters
    ----------
    value
        Anything a parse produced.

    Yields
    ------
    object
        Its members: a CRDT sequence's items, both halves of a mapping's entries, a
        sequence's elements, or a dataclass's declared field values. Nothing for a stylus
        sample, which is six numbers and the one shape numerous enough for walking it to
        cost; nothing for a leaf of any other kind.
    """
    if isinstance(value, si.Point):
        return
    if isinstance(value, CrdtSequence):
        yield from value.sequence_items()
    elif isinstance(value, dict):
        for key, member in value.items():
            yield key
            yield member
    elif isinstance(value, list | tuple):
        yield from value
    elif dataclasses.is_dataclass(value):
        for declared in dataclasses.fields(value):
            yield getattr(value, declared.name)


def _line_blocks(
    strokes: tuple[Stroke, ...], /, *, site: _AppendSite, author_id: int
) -> list[Block]:
    """Build one scene line item block per stroke, chained to draw last and in order.

    Two independent mechanisms put the new ink last, and they agree. Each block's
    ``left_id`` is the previous member -- the layer's last existing one for the first stroke
    -- and its ``right_id`` is the end marker, so the topological sort places them after
    everything already there. Separately, the sort's tie-break is ``CrdtId`` order, and the
    fresh author id is the highest in the artifact, so an id minted here sorts after every
    existing one anyway.

    Parameters
    ----------
    strokes
        The ink to add, in draw order.
    site
        Where it attaches.
    author_id
        The fresh author component every minted id carries.

    Returns
    -------
    list[Block]
        One block per stroke, in the order they will draw.
    """
    blocks: list[Block] = []
    left_id = site.left_id
    for offset, stroke in enumerate(strokes):
        # The sequence component starts at 1 under a fresh author, so nothing needs to be
        # scanned for a free one: the author is what makes the pair unique.
        item_id = CrdtId(author_id, offset + 1)
        blocks.append(
            SceneLineItemBlock(
                site.parent_id,
                CrdtSequenceItem(item_id, left_id, END_MARKER, 0, _scene_line(stroke)),
                extra_value_data=_encode_color_override(stroke.color_override),
            )
        )
        left_id = item_id
    return blocks


def _scene_line(stroke: Stroke, /) -> si.Line:
    """Convert one domain stroke into the parser's line item.

    The exact inverse of :func:`_convert_line` for every field that survives a round trip.
    Both wire enums are constructed rather than cast, so a divergence between the domain's
    account of the format and the parser's would raise here instead of writing a value
    neither agrees on; ``tests`` pins that the two enums carry identical value sets, which is
    what makes that construction total today.

    Parameters
    ----------
    stroke
        The stroke to convert. Its samples are already in the screen units the wire uses.

    Returns
    -------
    si.Line
        The line, with no ``move_id`` -- that field marks a stroke the tablet moved, and new
        ink has not been moved.
    """
    return si.Line(
        color=si.PenColor(int(stroke.color)),
        tool=si.Pen(int(stroke.pen)),
        points=[
            si.Point(
                point.x,
                point.y,
                point.speed,
                point.direction,
                point.width,
                point.pressure,
            )
            for point in stroke.points
        ],
        thickness_scale=stroke.thickness_scale,
        starting_length=stroke.starting_length,
    )


def _encode_color_override(colour: Rgba | None, /) -> bytes:
    r"""Encode a stroke's own colour as the tagged field the firmware writes it in.

    The inverse of :func:`_decode_color_override`, and the reason a highlighted page keeps
    its colours through a decode-then-append round trip. Without it, every highlighter stroke
    on a page written back through here would report ``PenColor`` id 9 and render as one
    yellow, because the index is the same for all four highlighter colours and the true
    colour lives only in this field.

    Written whenever the model carries one, without checking the tool. The field belongs to
    the highlighter -- a pen stroke has no such field and its colour index is the whole truth
    -- but deciding that here would silently drop a colour the decode read, and echoing what
    the model holds is what makes the round trip an identity on this field. The caller decides
    which tool gets one; this decides how it is spelled.

    Parameters
    ----------
    colour
        The stroke's own colour, or ``None`` for almost every stroke.

    Returns
    -------
    bytes
        Empty for ``None``, so the value subblock ends exactly where the parser expects.
        Otherwise the two-byte varuint header ``84 01`` -- index 8, tag ``Byte4`` -- followed
        by the colour as a little-endian ``uint32`` in ARGB order.

    Examples
    --------
    The two measured strokes, both of which reported ``PenColor`` id 9:

    >>> _encode_color_override(Rgba(r=255, g=237, b=117, a=255)).hex()
    '840175edffff'
    >>> _encode_color_override(Rgba(r=190, g=234, b=254, a=255)).hex()
    '8401feeabeff'
    >>> _encode_color_override(None)
    b''
    """
    if colour is None:
        return b""
    # Channel positions restated from `_decode_color_override`, which reads them off the same
    # little-endian uint32 in the other direction.
    packed = (colour.a << 24) | (colour.r << 16) | (colour.g << 8) | colour.b
    header = _write_tag(_COLOR_OVERRIDE_INDEX, _TAG_BYTE4)
    return header + packed.to_bytes(_ARGB_LENGTH, "little")


def _write_tag(index: int, tag_type: int, /) -> bytes:
    """Assemble the varuint header that opens a tagged field.

    The inverse of :func:`_read_tag`, and general for the same reason that one is: the header
    is a varuint, so its width is data. Emitting a hardcoded ``84 01`` would be correct for
    index 8 and wrong for the first field index above 15 the format grows.

    Parameters
    ----------
    index
        The field index.
    tag_type
        The 4-bit tag type that sizes the payload.

    Returns
    -------
    bytes
        The header, one byte per seven value bits, least significant group first.
    """
    header = (index << _TAG_INDEX_SHIFT) | tag_type
    encoded = bytearray()
    while header >= _VARUINT_CONTINUATION:
        encoded.append((header & _VARUINT_PAYLOAD) | _VARUINT_CONTINUATION)
        header >>= _VARUINT_SHIFT
    encoded.append(header)
    return bytes(encoded)


def _serialise_tail(minted: list[Block], page_ref: str, /) -> bytes:
    """Serialise the new blocks alone and strip the writer's own file header from them.

    The strip width is measured from the same writer in the same call -- the length of what
    it produces for an empty block list, which is the header and nothing else -- rather than
    from a 43-byte constant. There is then no assumption left for production code to check:
    the only way the width is wrong is if ``write_blocks`` stopped writing its header first,
    which is the definition of the function, and ``tests`` pins the header's bytes and its
    length against a real artifact's own opening bytes.

    Parameters
    ----------
    minted
        The new blocks, in draw order.
    page_ref
        What to call the artifact on an error.

    Returns
    -------
    bytes
        The blocks' serialisation with no header, ready to concatenate.

    Raises
    ------
    SceneRewriteUnsafe
        The writer refused the new blocks. Reachable from the caller's own values rather
        than from the artifact: a sample coordinate too large for a 32-bit float is a
        constructible :class:`~rmspec.domain.models.Point` and raises out of ``struct``. The
        third-party exception is translated and never re-exported.
    """
    try:
        with _quiet_parser():
            return _serialise(minted)[len(_serialise(())) :]
    except Exception as err:
        raise SceneRewriteUnsafe(
            page_uuid=page_ref,
            detail=(
                f"serialising {len(minted)} new block(s) raised {_parser_detail(err)}, so the "
                f"ink this caller supplied cannot be written"
            ),
        ) from err


def _serialise(blocks: Iterable[Block], /) -> bytes:
    """Write blocks to bytes with the writer's default options.

    Default options on purpose, and it is the same call :func:`_reject_lossy_rewrite` makes:
    that check passing is what establishes the artifact's own line blocks re-encode
    identically under these defaults, so new blocks written under them are encoded the way
    the rest of the page already is.

    Parameters
    ----------
    blocks
        The blocks to write, in file order. Empty yields the file header alone.

    Returns
    -------
    bytes
        The v6 file header followed by each block.
    """
    buffer = io.BytesIO()
    write_blocks(buffer, blocks)
    return buffer.getvalue()


def _parser_detail(err: Exception, /) -> str:
    """Describe a third-party exception for a caller, without re-exporting its type.

    Parameters
    ----------
    err
        The exception a parser or writer raised.

    Returns
    -------
    str
        Its class name and its message, or a stand-in when it carried none -- which the
        message-less ``EOFError`` a truncated artifact raises does.
    """
    reason = str(err).strip() or "the parser reported no detail"
    return f"{type(err).__name__}: {reason}"


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


def _read_color_overrides(blocks: list[Block], defects: _Defects, /) -> dict[int, Rgba]:
    r"""Read the per-stroke colour ``rmscene`` leaves behind, and record every unread tail.

    Two facts come off the same bytes, so they are gathered in one pass over the blocks.

    How the unparsed tail is obtained, and why this route rather than the other two
    ---------------------------------------------------------------------------------
    ``rmscene`` already hands it over. ``TaggedBlockReader._check_position`` -- the hook that
    logs *"Some data has not been read"* -- reads the bytes between where the parser stopped
    and the end of the block and assigns them to ``BlockInfo.extra_data``;
    ``Block.read`` copies that onto every block as the public field ``Block.extra_data``, and
    ``SceneItemBlock.from_stream`` copies the *value subblock's* own leftover onto
    ``SceneItemBlock.extra_value_data``. Both are declared fields of ``rmscene``'s public
    dataclasses, both are written back out by its writer, and they are byte-identical to what
    ``_check_position`` saw, because they *are* what ``_check_position`` saw.

    The two alternatives were considered and are worse. Monkeypatching ``_check_position`` to
    capture ``self.data.tell()`` reaches into a library's private method from production
    code, and this repo's own suppression context manager exists precisely because reaching
    into another library's globals does not stay contained. Re-walking the raw block frames
    and re-decoding each line item's tagged fields to find where the parser *would have*
    stopped duplicates the parser inside its own adapter: it is a second implementation that
    can disagree with the first, and every disagreement is a stroke coloured from the wrong
    offset. Byte-scanning for ``0x84`` is not an option at all -- that byte occurs constantly
    inside point data, so it produces false positives.

    A field index 8 is on the *line*, which the parser reads inside value subblock 6, so the
    override is read from ``extra_value_data``. The block-level ``extra_data`` is still
    inspected for the defect, because a tail there is the same signal about a different part
    of the format.

    What the defect is recorded for, and what it is not
    --------------------------------------------------
    ``BLOCK_BYTES_UNREAD`` is recorded for the bytes *nothing here understood*, which after
    this function means: a recognised index-8 ``Byte4`` field is decoded and reported as
    nothing, and every other leftover byte -- a different index, a different tag type, a glyph
    item's own field, or a remainder sitting after a field that was recognised -- is reported
    with its bytes in the detail. Recording a decoded tail as unread would be false: the
    parser did not read those four bytes, but this codec did, and "unread" is a claim about
    the page a caller acts on rather than a note about ``rmscene``'s internals.

    That is the opposite outcome to the tombstone case :func:`_collect_item` documents, and
    for the opposite reason. A tombstone is normal, expected content, so reporting it made
    ``ITEM_DROPPED`` fire on almost every real document and say nothing actionable; a
    highlighter's colour field is normal too, but it is now *handled*, so this code goes quiet
    on every page for which the answer is already known and stays loud on exactly the pages a
    next measurement should start from.

    The claimed sibling field, and why it is not implemented
    -------------------------------------------------------
    The same source that named index 8 on the line item names index 10 on the *glyph* item --
    a highlight over a pdf's own text. Nothing in the measured corpus carries one: no glyph
    item in it leaves a single byte unread, so there is no evidence to write against and a
    speculative decode would be four bytes of something read as a colour. A glyph item that
    does leave bytes behind records the defect below with those bytes in its detail -- it is
    not a ``SceneLineItemBlock``, so no decode is attempted on it at all -- which is exactly
    what a future measurement needs and all this function can honestly claim.

    How a tail is correlated back to a stroke
    ----------------------------------------
    By object identity, not by order. ``build_tree`` places the very ``si.Line`` instance a
    block parsed into the tree -- it copies no values -- so ``id(block.item.value)`` is the
    key the walk below can look up when it reaches that line. Block order and yield order do
    match, and the measurement checked that they do, but an ordinal correlation would go
    silently wrong the first time a block yields no item or the walk visits one twice, and
    identity cannot.

    Parameters
    ----------
    blocks
        Every block read from the file, in file order. Must stay referenced for as long as
        the returned mapping is used: its keys are ``id()`` values of objects these blocks
        own, and a freed object's id may be handed to another.
    defects
        Page-scoped accumulator for degradations.

    Returns
    -------
    dict[int, Rgba]
        The decoded colour for each line that carried one, keyed by ``id()`` of the parsed
        line. Empty for a page of ordinary pen strokes, which is almost every page.
    """
    overrides: dict[int, Rgba] = {}
    for block in blocks:
        value_tail = block.extra_value_data if isinstance(block, SceneItemBlock) else b""
        if isinstance(block, SceneLineItemBlock):
            decoded = _decode_color_override(value_tail)
            if decoded is not None:
                colour, consumed = decoded
                overrides[id(block.item.value)] = colour
                # Only what the field did not cover is still unread. A remainder here is not
                # something the firmware has been seen to write -- both measured tails were
                # the field and nothing else -- but a second field appended after this one is
                # how the format has grown before, and dropping it would hide the next one.
                value_tail = value_tail[consumed:]
        unread = block.extra_data + value_tail
        if unread:
            defects.add(
                PageDefectCode.BLOCK_BYTES_UNREAD,
                f"nothing in this codec understood {len(unread)} byte(s) of a block of type "
                f"{block.get_block_type()}: {unread.hex(' ')}",
            )
    return overrides


def _decode_color_override(tail: bytes, /) -> tuple[Rgba, int] | None:
    r"""Decode one unparsed tail as a stroke's own colour, or decline to.

    The tail is a tagged field like any other: a varuint header carrying an index and a tag
    type, then a payload the tag type sizes. Only index 8 with tag ``Byte4`` is this field.
    Anything else is a field of the format this codec does not know, and is left alone rather
    than guessed at -- the caller records those bytes as a defect.

    The consumed width is returned alongside the colour rather than recomputed by the caller,
    because the header is a *varuint* and its width is therefore data, not a constant. A
    caller that assumed two bytes would mis-slice the remainder the day a field index above 15
    appears, and would then report the wrong bytes as unread.

    Parameters
    ----------
    tail
        The bytes the parser did not consume from a line item's value subblock.

    Returns
    -------
    tuple[Rgba, int] | None
        The colour and how many bytes it occupied -- the header's own width plus the 4-byte
        payload -- when the tail opens with index 8 / ``Byte4`` and holds that payload whole.
        ``None`` for an empty tail, an incomplete header, any other field, or a payload cut
        short.

    Examples
    --------
    The two measured strokes, both of which reported ``PenColor`` id 9, and both of which are
    the field and nothing else -- six bytes consumed of six:

    >>> _decode_color_override(bytes.fromhex("840175edffff"))
    (Rgba(r=255, g=237, b=117, a=255), 6)
    >>> _decode_color_override(bytes.fromhex("8401feeabeff"))
    (Rgba(r=190, g=234, b=254, a=255), 6)
    """
    header = _read_tag(tail)
    if header is None:
        return None
    index, tag_type, width = header
    if index != _COLOR_OVERRIDE_INDEX or tag_type != _TAG_BYTE4:
        return None
    payload = tail[width : width + _ARGB_LENGTH]
    if len(payload) != _ARGB_LENGTH:
        return None
    # Little-endian uint32, then ARGB. Stated in that order because it is unambiguous: the
    # same bytes described from the other end read "BGRA", which is how the claim was worded
    # and which invites reading the channels off in file order.
    packed = int.from_bytes(payload, "little")
    colour = Rgba(
        r=(packed >> 16) & _BYTE_MASK,
        g=(packed >> 8) & _BYTE_MASK,
        b=packed & _BYTE_MASK,
        a=(packed >> 24) & _BYTE_MASK,
    )
    return colour, width + _ARGB_LENGTH


def _read_tag(tail: bytes, /) -> tuple[int, int, int] | None:
    r"""Split the varuint header that opens a tagged field.

    Parameters
    ----------
    tail
        Bytes beginning at a tagged-field header.

    Returns
    -------
    tuple[int, int, int] | None
        ``(index, tag_type, width)``, where ``width`` is how many bytes the header occupied
        and therefore where its payload begins. ``None`` when no complete varuint is present:
        an empty tail, or one whose every byte sets the continuation bit.
    """
    header = 0
    for width, byte in enumerate(tail[:_VARUINT_MAX_BYTES]):
        header |= (byte & _VARUINT_PAYLOAD) << (_VARUINT_SHIFT * width)
        if not byte & _VARUINT_CONTINUATION:
            return header >> _TAG_INDEX_SHIFT, header & _TAG_TYPE_MASK, width + 1
    return None


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


def _convert_tree(
    tree: SceneTree,
    defects: _Defects,
    overrides: Mapping[int, Rgba],
    /,
) -> tuple[Layer, ...]:
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
    overrides
        Per-line colours from :func:`_read_color_overrides`, keyed by ``id()`` of the
        parsed line. Threaded down rather than looked up here, because the only consumer
        is :func:`_convert_line` at the bottom of the walk.

    Returns
    -------
    tuple[Layer, ...]
        Layers in render order. Empty when the page holds nothing, which is the one
        state that means "blank" rather than "damaged".
    """
    layers = [
        _convert_group(child, tree, defects, overrides)
        for child in tree.root.children.values()
        if isinstance(child, si.Group)
    ]
    if layers:
        return tuple(layers)

    # No layer group at root level: collect every leaf item into one invented layer,
    # exactly as the legacy reader did, and say so rather than logging it.
    builder = _LayerBuilder(name=_SYNTHESISED_LAYER_NAME, visible=True)
    for item in tree.walk():
        _collect_item(item, builder, defects, overrides)
    if builder.is_empty:
        return ()
    defects.add(
        PageDefectCode.LAYER_SYNTHESISED,
        f"items arrived with no owning layer group, so {_SYNTHESISED_LAYER_NAME!r} was "
        f"invented to hold them",
    )
    return (builder.build(),)


def _convert_group(
    group: si.Group,
    tree: SceneTree,
    defects: _Defects,
    overrides: Mapping[int, Rgba],
    /,
) -> Layer:
    """Convert one scene group -- a layer in the tablet's UI -- into a layer.

    Parameters
    ----------
    group
        The group to convert.
    tree
        The parsed scene tree, for resolving a nested group by node id.
    defects
        Page-scoped accumulator for degradations.
    overrides
        Per-line colours, keyed by ``id()`` of the parsed line.

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
                _collect_item(nested_child, builder, defects, overrides)
        else:
            _collect_item(child, builder, defects, overrides)
    return builder.build()


def _collect_item(
    item: object,
    builder: _LayerBuilder,
    defects: _Defects,
    overrides: Mapping[int, Rgba],
    /,
) -> None:
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
    overrides
        Per-line colours, keyed by ``id()`` of the parsed line.
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
        stroke = _convert_line(item, defects, overrides)
        if stroke is not None:
            builder.strokes.append(stroke)
    elif isinstance(item, si.Text):
        block = _convert_text(item, defects)
        if block is not None:
            builder.text_blocks.append(block)
    elif isinstance(item, si.Group):
        for child in item.children.values():
            _collect_item(child, builder, defects, overrides)
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


def _convert_line(
    line: si.Line,
    defects: _Defects,
    overrides: Mapping[int, Rgba],
    /,
) -> Stroke | None:
    """Convert one scene line into a stroke.

    Parameters
    ----------
    line
        The line to convert.
    defects
        Page-scoped accumulator for degradations.
    overrides
        Per-line colours, keyed by ``id()`` of the parsed line. A miss is the ordinary
        case and means the colour index is the whole truth for this stroke.

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
            color_override=overrides.get(id(line)),
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


def _convert_root_text(tree: SceneTree, defects: _Defects, /) -> tuple[TextBlock, ...]:
    """Read the page's own typed text, which no layer owns and the walk never visits.

    The whole of defect 4.3. ``rmscene`` parses the page-scoped text block into
    ``SceneTree.root_text`` and adds it to no group, so :func:`_convert_tree` -- which walks
    groups -- cannot see it, and ``_collect_item``'s text arm has therefore never run against
    a real file. Confirmed both ways on the live page: ``root_text`` was a text item carrying
    the typed words while ``decode_page`` reported no text block at all.

    A tuple of at most one, because the format carries at most one page-scoped text block and
    ``rmscene`` overwrites the first if a second appears. It is a tuple rather than an
    ``Optional`` because that is the shape the layer-owned collection already has, and a
    renderer drawing "all the text on this page" should not need two spellings.

    What this text is good for, and what it is not
    ---------------------------------------------
    Reading. It is how a caller finds out what a human typed on a page, which was previously
    invisible to every consumer of this codec.

    It is **not** a channel for writing text a human will see. Measured on firmware 3.27.3.0:
    a page-scoped text block written into a real page by a foreign author was **preserved** by
    the tablet -- it read back at the exact position set, with the foreign author id intact,
    across the tablet's own re-save -- and was **never drawn**. Strokes are what the tablet
    renders. So a reply feature built on writing one of these puts text into the file that the
    person holding the tablet cannot see; a reply the human can read has to be ink.

    Parameters
    ----------
    tree
        The parsed scene tree.
    defects
        Page-scoped accumulator for degradations.

    Returns
    -------
    tuple[TextBlock, ...]
        The page's typed text, or empty -- for a page with none, and for one whose text item
        the domain refuses, which :func:`_convert_text` records as a dropped item.
    """
    if tree.root_text is None:
        return ()
    block = _convert_text(tree.root_text, defects)
    return () if block is None else (block,)


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
