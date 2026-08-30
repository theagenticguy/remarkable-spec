"""Ports for the render slice: a parsed page in, an SVG document or ink strokes out.

The slice has two ports and they run in opposite directions. :class:`PageRenderer` turns a
page into markup a human looks at; :class:`TextEngraver` turns a string into strokes a human
reads *on the tablet*. Both are "make this legible", which is why they share a slice and a
technology; neither knows where its output goes. Everything else the render lens proposed --
pen-model registries, pen physics factories, palette sources, template sources, background
sources -- is deliberately absent; :ref:`the section below <not-ports>` records each one and
why.

Why a port at all when there is one adapter
-------------------------------------------
Because ``tests/architecture/test_dependency_direction.py`` fails the build if
``rmspec-app`` imports anything but ``rmspec.domain``. Page rendering is pure stdlib
``xml.etree`` arithmetic with one plausible implementation, so on swappability alone it
would be a module-level function -- but a use case may not import it, and moving the
orchestration into the CLI to dodge that is defect 1's shape (application logic in command
bodies). The seam is therefore mandated by the target architecture rather than speculated
from a second backend, and it is placed as coarsely as possible: one page-level call, with
no pen, palette, layout or template indirection underneath it.

The two steps in this neighbourhood that *do* carry a swappable technology -- SVG to PNG
(cairo today) and one page of an existing PDF to pixels (pymupdf today) -- are ports of the
export slice, not of this one. This module names neither library and neither concept.

.. _not-ports:

Ports deliberately not defined here
-----------------------------------
- ``PenModel`` / ``PenModelProvider`` / ``PenModelRegistry`` / ``PenPhysicsFactory``: pen
  physics is pure float arithmetic with one caller inside the render adapter. A port would
  have re-exported the ``.rm`` v6 wire encoding (raw ``direction`` byte, quarter-unit
  widths) into the domain, and its only test double is one that makes every width and
  opacity assertion vacuous. Pen selection stays an exhaustive ``match`` inside
  ``rmspec-render``; an unknown pen id raises ``UnsupportedPenType`` from
  :meth:`PageRenderer.render` instead of silently rendering as a fineliner.
- ``PaletteSource`` / ``PaletteRegistry``: no caller resolves a palette by name, and a
  registry whose fake is a dict and whose adapter is the same dict is not a seam. ``Palette``
  is a frozen domain model whose validator makes ``rgb()`` total, which is what deletes the
  three silent black fallbacks; it arrives here as a required parameter, and an unknown
  ``--palette`` becomes CLI validation.
- ``TemplateSource``: nothing in the legacy tree resolves a template *name* to markup -- the
  background is a user-supplied path on the render command -- and firmware 3.27 templates are
  ``{UUID}.template`` JSON, which a ``markup: str`` port cannot represent at all. The file
  read stays in the CLI; the renderer receives already-read markup as
  :attr:`PageBackground.template_svg` and raises instead of swallowing a parse failure.
- ``BackgroundRasterizer`` / ``BackgroundSource``: the PDF underlay is produced by the export
  slice's PDF reader port, whose adapter owns pymupdf. This module only *consumes* the
  result, as :class:`PageUnderlay`, so no port here opens a file, holds a document handle,
  or needs a ``close()`` that duplicates the container's ``Scope.REQUEST`` finalizer.

Layout is a value, not a port method
------------------------------------
No ``viewport_for`` member exists. The centre-origin correction (``x_shift`` equal to half
the viewport width) and the stroke-extent widening are pure arithmetic over a page, a screen
and a padding, so they belong to a frozen layout value in ``rmspec.domain.models`` that one
implementation computes and every adapter is handed. Behind a Protocol, every test double
would re-derive that geometry and be asserted against its own wrong numbers. It also cannot
be a method here without a cycle: the underlay's target pixel box comes from the screen, and
the viewport comes from the underlay.

Typed text is a policy, and skipping it is observable
-----------------------------------------------------
``Layer.text_blocks`` is page content, and a page whose only content is one typed block used
to render as a perfectly valid, blank-looking ``<svg>``: ``stroke_count`` was ``0``,
``notices`` was empty, and the PNG, the PDF and both OCR paths downstream saw the words
simply gone. ``TextBlock`` carries a position, a wrap width and a string but no typeface, and
SVG has no auto-wrap, so *which* font lays it out is a decision -- the unowned-constant shape
of the bare ``1.5`` thickness, one field over. Three members close it:

- :class:`TextStyle`, a required member of :class:`RenderStyle`, owns the family, size and
  line height at composition time, so the choice is explicit and :meth:`RenderStyle.digest`
  covers it: changing the font misses the OCR cache instead of serving a stale row.
- :attr:`RenderedPage.text_block_count` counts the blocks actually committed to the markup,
  the way ``stroke_count`` counts strokes, so "drew the text" and "dropped the text" are
  different values instead of the same one.
- :attr:`RenderNoticeCode.TEXT_OMITTED` is what an adapter that will not lay out text has to
  report. A font-metric-free SVG writer is a legitimate second adapter; one that drops a
  block in silence is not, and :meth:`PageRenderer.render` states the obligation the shared
  contract suite pins on every adapter and every double.

Cache identity (defect 3)
-------------------------
:meth:`RenderStyle.digest` folds *everything* that changes a pixel -- thickness, padding,
text family, size and line height, renderer revision, the screen, the palette's name **and**
contents, and the background's markup and bytes -- into one hex string. Every component is a
required parameter or field, including ``background``, so there is no way to spell a render
identity that omits one. That string is the render component of an ``OcrCacheKey`` or
``DiagramCacheKey`` digest; the caller still folds in the source-file hash, the model id, the
prompt version and the rasterization DPI, which travel with the pixels on the export slice's
raster value. A DPI, palette, font or pen-formula change therefore mechanically misses
instead of returning a valid-looking stale row.

:class:`RenderedPage` deliberately carries no digest of its own configuration. An identity
the adapter echoes back is unfalsifiable -- every double echoes it correctly -- so key
construction stays with the caller that chose the configuration.

Availability is not a port error (defect 4)
-------------------------------------------
No method here raises "renderer unavailable" or "dependency missing". A missing optional
package is a composition failure that names the package and the extra providing it, during
the container's eager resolution pass -- never a function-local ``ImportError`` inside a
command body.

Errors
------
:meth:`PageRenderer.render` raises exactly two error types, both from
``rmspec.domain.errors``: ``UnsupportedPenType`` and ``BackgroundUnreadable``. There is no
"template not found" error (this port never touches a filesystem), no "unknown ink" error
(palette totality is a model invariant), and no degradation error (substitutions the render
survives are reported as :attr:`RenderedPage.notices`, always as data, never gated by an
``on_degradation`` mode flag).

Malformed template markup has exactly *one* exit channel, and it is
``BackgroundUnreadable``. :class:`PageBackground` deliberately does not pre-screen
``template_svg`` for a root tag: a substring validator would have made a CLI reading
``--background`` receive a pydantic ``ValidationError`` for a file with no ``<svg`` in it and
a typed ``BackgroundUnreadable`` for a file that has one but does not parse -- the same user
mistake arriving at the same edge as two unrelated types, chosen by accident of which
half-measure noticed first. The field's ``min_length=1`` is not that half-measure: "no bytes
at all" is not a claim about well-formedness, and refusing it is what keeps ``None`` the only
spelling of "no template" for :meth:`PageBackground.digest`, which folds the field in as
``(template_svg or "")``.

Why text becomes ink, and why that is a second port here
--------------------------------------------------------
A page-scoped typed-text block written into a real page by a foreign author was **preserved**
by firmware 3.27.3.0 across the tablet's own re-save -- read back at the exact position set,
with the foreign author id intact -- and was **never drawn**. Strokes are what the tablet
renders. So a message a human can read has to be ink, and :class:`TextEngraver` is where a
string becomes some.

It is a *separate* port from :class:`PageRenderer` for the mechanical reason
``ports/formats.py`` gives for splitting :class:`~rmspec.domain.ports.formats.SceneAppender`
off :class:`~rmspec.domain.ports.formats.PageCodec`: ``PageRenderer`` has exactly one method
so a conforming double is one canned :class:`RenderedPage`, and every double in the workspace
is annotated against it. A second method there would stop all of them satisfying the port
they were written against, in order to publish something only the write path calls. The split
also says something true -- rendering a page is available everywhere and produces an artifact
nobody's handwriting depends on, while engraving text exists to feed a transport that rewrites
a page a human is holding.

The font is a **single-stroke engraving** face, and that is not a stylistic preference: a
traced outline is a closed contour meant to be *filled*, and a stroke cannot fill, so an
outline font engraved as strokes draws every letter as a hollow double line. The port is named
for what the technique is, so nobody replaces it with an outline tracer and reports success.

The one deliberate absence is a coverage set. :class:`TextEngraver` publishes no
"characters I can draw" collection and the domain holds no copy of one, because the answer is
a property of a font this module has never seen: a domain copy would be a claim nothing here
can check and a second source of truth that drifts the first time the face is replaced.
:attr:`InkText.substituted` is the answer instead -- reported per call, about the exact string
that was asked for -- which is also the only shape a caller can act on before spending a
write.

Note for whoever writes ``ports/__init__.py``
---------------------------------------------
This module may not import a sibling ports module, so three value objects here are
deliberate twins: :class:`ImageMedia` (twin of the OCR and export copies) and
:class:`PhysicalSize` (twin of the export copy) are field-for-field identical, and
:class:`RenderedPage` is the export slice's ``SvgPage`` plus ``stroke_count``,
``text_block_count`` and ``notices``. Hoist one definition of each into a shared
``rmspec.domain.values`` module rather than re-exporting same-named classes: while the twins
stay separate, every export and OCR use case re-copies :attr:`RenderedPage.size` field by
field into an ``SvgPage``, and a transposed ``width_mm``/``height_mm`` in that re-wrap type
checks clean and silently rotates the page. The twins have already drifted once -- the export
copy's PNG check enforced a 24-byte minimum header where the copy here checked only the
8-byte signature, so bytes it rejected were accepted here; :func:`_check_image_signature`
now enforces the same minimum, which is a fix that has to be made twice until the hoist
lands.

Domain models split two ways here, and the line is pydantic's rather than a preference.
``Page``, ``ScreenSpec`` and ``Palette`` appear only in method signatures, so they are
imported under ``TYPE_CHECKING`` as in ``ports/formats.py``. ``PenColor`` and ``Stroke``
appear in *field* annotations on :class:`InkTextStyle` and :class:`InkText`, and pydantic
resolves those when the model class is built -- so they must be imported at run time or the
models raise ``PydanticUserError: not fully defined``. ``runtime-evaluated-base-classes`` in
the root ``pyproject.toml`` is what stops ruff demanding the move that breaks them; the same
note is on :mod:`rmspec.domain.models`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rmspec.domain._digest import digest_of
from rmspec.domain.models import PenColor, Stroke

if TYPE_CHECKING:
    from rmspec.domain.models import Page, Palette, ScreenSpec

__all__ = [
    "ImageMedia",
    "InkText",
    "InkTextStyle",
    "PageBackground",
    "PageRenderer",
    "PageUnderlay",
    "PhysicalSize",
    "RenderNotice",
    "RenderNoticeCode",
    "RenderStyle",
    "RenderedPage",
    "TextEngraver",
    "TextStyle",
]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
"""First eight bytes of every PNG file."""

_PNG_HEADER_LENGTH = 24
"""Bytes in a PNG signature plus its mandatory ``IHDR`` chunk, header and CRC included."""

_JPEG_SIGNATURE = b"\xff\xd8\xff"
"""Start-of-image marker of every JPEG file."""

_SVG_ROOT_TAG = "<svg"
"""Substring every SVG document contains and no other artifact does."""


def _canonical_json(value: BaseModel) -> str:
    """Serialize ``value`` to JSON with object keys sorted.

    Sorted keys because a palette is a mapping: two palettes with identical colours in a
    different insertion order must produce one digest, or a cache would miss for a reason
    that changes no pixel.

    Parameters
    ----------
    value
        Any frozen domain value object.

    Returns
    -------
    str
        Compact JSON with deterministic key order.
    """
    return json.dumps(
        json.loads(value.model_dump_json()),
        sort_keys=True,
        separators=(",", ":"),
    )


def _check_image_signature(media: ImageMedia, data: bytes) -> None:
    """Reject bytes that are not in ``media``'s encoding.

    A header check rather than a decode, but it is what stops a test double from passing
    ``b"AA=="`` as an underlay: any double that wants to exercise the placement path has to
    supply bytes a real renderer would embed. PNG additionally requires the signature *plus*
    an ``IHDR``-sized remainder, matching the export slice's twin of this check byte for byte
    -- an eight-byte-only test accepted underlays that port rejected.

    Parameters
    ----------
    media
        Declared encoding.
    data
        Encoded image bytes.

    Raises
    ------
    ValueError
        If ``data`` does not begin with ``media``'s signature, or is too short to carry a
        PNG header.
    """
    if media is ImageMedia.PNG:
        if len(data) < _PNG_HEADER_LENGTH or not data.startswith(_PNG_SIGNATURE):
            msg = "data does not start with a png signature and header"
            raise ValueError(msg)
    elif not data.startswith(_JPEG_SIGNATURE):
        msg = "data does not start with a jpeg signature"
        raise ValueError(msg)


class ImageMedia(StrEnum):
    """Encoding of a raster image's bytes, as a domain fact rather than a MIME token.

    A domain enum instead of a content-type string because an HTTP content type is a wire
    label that can lie: the tablet's thumbnail route advertises JPEG and returns PNG bytes.
    Both members exist so this enum stays a field-for-field twin of the OCR and export
    slices' copies.
    """

    PNG = "png"
    JPEG = "jpeg"


class RenderNoticeCode(StrEnum):
    """A substitution or omission the renderer made and survived, reported instead of hidden.

    Three members, because three things in the legacy renderer silently changed the output.
    Anything the renderer cannot survive raises instead, so this enum stays closed and
    small: it is not a log level and it is not a warning channel.
    """

    VIEWPORT_EXPANDED = "viewport_expanded"
    """Ink fell outside the page box, so the viewport was widened past the screen size."""

    UNDERLAY_RESCALED = "underlay_rescaled"
    """The underlay's native page size differed from the note page's and was fitted."""

    TEXT_OMITTED = "text_omitted"
    """A visible, non-empty typed text block was left out of the markup.

    Mandatory for an adapter that does not lay out text, and the reason a text-only page can
    never come back as a valid blank one. ``detail`` should say how many blocks were dropped
    and why (no font metrics, unwrappable width), because the person reading it is deciding
    whether to re-run with a different renderer before trusting the OCR.
    """


class PhysicalSize(BaseModel):
    """A page's real-world size, in millimetres.

    Millimetres, not PostScript points: points are a PDF/PostScript unit, and a domain that
    speaks them has adopted one adapter's coordinate system. Adapters convert on their own
    boundary (``pt = mm * 72 / 25.4``).

    Attributes
    ----------
    width_mm
        Width in millimetres.
    height_mm
        Height in millimetres.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)


class RenderNotice(BaseModel):
    """One reported substitution: a closed code plus human-facing detail.

    Attributes
    ----------
    code
        What was substituted. This is the part callers may branch on.
    detail
        Free text for a person reading a log or a CLI warning. Never parsed.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    code: RenderNoticeCode
    detail: str = Field(min_length=1)


class PageUnderlay(BaseModel):
    """Already-rasterized pixels to sit behind a page's ink, plus their native page size.

    The two facts a renderer needs to place an underlay, and nothing else. Pixels arrive as
    bytes rather than a path, so this package and ``rmspec-app`` hold zero image fixtures,
    and the encoding is a domain enum rather than a MIME string. ``source_size`` is the
    *source* page's real-world size, not the target's: the two routinely disagree (a Letter
    PDF behind a Paper Pro page), which is exactly the case the legacy letterboxing
    arithmetic got to decide unobserved. Producing this value is the export slice's PDF
    reader port; consuming it is all this slice does.

    Attributes
    ----------
    media
        Encoding of ``data``.
    data
        Encoded image bytes.
    source_size
        The source page's native real-world size, with any page rotation already applied.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    media: ImageMedia
    data: bytes = Field(min_length=1)
    source_size: PhysicalSize

    @model_validator(mode="after")
    def _check_image_bytes(self) -> Self:
        """Reject bytes that are not in the declared encoding.

        Returns
        -------
        PageUnderlay
            The validated model.

        Raises
        ------
        ValueError
            If ``data`` does not begin with ``media``'s signature.
        """
        _check_image_signature(self.media, self.data)
        return self


class PageBackground(BaseModel):
    """Everything behind the ink, already resolved: template markup and/or an underlay.

    Absence is expressed by passing no background at all, so ``None`` for both fields is
    unrepresentable and the four-state product the rejected background ports carried
    collapses to three real ones. Markup, never a path: the file read belongs to whichever
    edge accepted the ``--background`` argument, which is what keeps the filesystem, and
    therefore a "template not found" error, out of this port entirely.

    ``template_svg`` is *not* pre-screened for a root tag. Well-formedness is decided in one
    place, by the parser inside :meth:`PageRenderer.render`, which raises
    ``BackgroundUnreadable``. A substring check here would have added a second exit channel
    for one user mistake -- a pydantic ``ValidationError`` for markup with no ``<svg`` in it
    and ``BackgroundUnreadable`` for markup that has one but still will not parse -- and the
    edge reading ``--background`` would get whichever the accident of ordering produced.

    It is nonetheless constrained to be non-empty, which is a different rule for a different
    reason: :meth:`digest` folds it in as ``(self.template_svg or "")``, so ``""`` and
    ``None`` would be one byte stream. Length is a fact about the field, not a verdict on the
    markup, so checking it here creates no second exit channel for a malformed template --
    ``--background`` pointing at an empty file is not markup this port can carry, and saying
    so is honest.

    Attributes
    ----------
    template_svg
        SVG markup to draw beneath the ink, or ``None``. Validity is the renderer's verdict;
        emptiness is refused here, so "no template" has exactly one spelling.
    underlay
        Rasterized pixels to draw beneath the ink, or ``None``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    template_svg: str | None = Field(default=None, min_length=1)
    underlay: PageUnderlay | None = None

    @model_validator(mode="after")
    def _check_something_present(self) -> Self:
        """Reject a background that carries nothing.

        Returns
        -------
        PageBackground
            The validated model.

        Raises
        ------
        ValueError
            If both fields are ``None``.
        """
        if self.template_svg is None and self.underlay is None:
            msg = "a background must carry template markup, an underlay, or both"
            raise ValueError(msg)
        return self

    def digest(self) -> str:
        """Return a stable content digest of this background.

        Returns
        -------
        str
            Lowercase hex SHA-256 over the markup, the underlay encoding and its bytes. Three
            components always, so an absent underlay is two empty components rather than two
            missing ones -- and ``template_svg`` cannot be ``""``, so ``(x or "")`` maps
            exactly one input to the empty component.
        """
        underlay = self.underlay
        return digest_of(
            b"rmspec.render.background.v2",
            (self.template_svg or "").encode(),
            b"" if underlay is None else underlay.media.value.encode(),
            b"" if underlay is None else underlay.data,
        )


class TextStyle(BaseModel):
    """How typed text is set: the three numbers SVG cannot infer for itself.

    ``TextBlock`` gives a corner, a wrap width and a string. Turning that into glyphs needs a
    typeface, a size and a line height, and SVG has no auto-wrap, so an adapter that is not
    handed them either invents them or drops the block. Both were happening: this value makes
    the choice a composition-time decision :meth:`RenderStyle.digest` can see, the same
    correction the ownerless ``1.5`` thickness constant got.

    Pixels, not millimetres, unlike :class:`PhysicalSize`. ``TextBlock.pos_x``, ``pos_y`` and
    ``width`` are in screen units, which are the SVG user units the ink is drawn in, so a size
    in millimetres would have to be converted against the screen before it could be compared
    with the box it has to fit -- an arithmetic step in the adapter that the unit exists to
    remove. Millimetres remain correct for a page, which is a physical object.

    ``family`` is a CSS font-family list, not a resolved font file: which concrete face a
    generic name lands on is the rasterizer's business, and a domain that named a file would
    be asserting a filesystem it cannot see. It is still digest-covered, so switching the list
    invalidates a cached OCR row.

    Attributes
    ----------
    family
        CSS font-family list for typed text, most specific first.
    size_px
        Em size in screen units, the same units ``TextBlock`` positions are given in.
    line_height
        Baseline-to-baseline distance as a multiple of ``size_px``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    family: str = Field(min_length=1)
    size_px: float = Field(gt=0)
    line_height: float = Field(gt=0)


class InkTextStyle(BaseModel):
    """How a string is set as ink: the four decisions a stroke font cannot make for itself.

    Millimetres, unlike :class:`TextStyle`'s ``size_px``, and the unit follows the caller
    rather than the format. Typed text is positioned in screen units because
    ``TextBlock.pos_x`` is, so a size in millimetres there would need converting against the
    screen before it could be compared with the box it must fit. Ink placed on a page is
    positioned in millimetres from the page's top-left, because the person choosing where a
    reply goes is looking at a physical sheet and measuring from its corner -- and because
    :attr:`InkText.extent_mm` has to be comparable with a page size, which is physical.

    A pen is deliberately absent. :class:`~rmspec.domain.models.Stroke` carries a
    :class:`~rmspec.domain.models.PenType`, and which one an engraved glyph uses is a property
    of the technique rather than of the message: a single-stroke face needs a tool whose width
    does not vary with the pressure and speed a synthesised sample has to invent. Offering the
    choice here would offer a way to draw a reply with a highlighter, or with an eraser.

    Attributes
    ----------
    em_mm
        Height of one em in millimetres: the size knob, in the unit the placement uses.
    line_height
        Baseline-to-baseline distance as a multiple of ``em_mm``.
    color
        Ink colour, from the enum the tablet stores per stroke, so a reply is as visible as
        the human's own ink and can be told from it at a glance.
    thickness_scale
        The tablet's thickness-slider value the strokes are minted with, before any per-pen
        formula. The same calibration decision :attr:`RenderStyle.thickness_scale` is, one
        layer along: exported weight and on-screen weight differ, and the value that
        compensates has an owner at composition rather than being a bare constant.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    em_mm: float = Field(gt=0)
    line_height: float = Field(gt=0)
    color: PenColor
    thickness_scale: float = Field(gt=0)


class InkText(BaseModel):
    """One string, engraved: the strokes to draw it and everything a caller checks first.

    Every field answers a question that has to be answerable **before** the strokes reach a
    page, because that is the only point at which the answer is free. :attr:`substituted` is
    what stops a caller discovering on the tablet that its prose was drawn as boxes;
    :attr:`extent_mm` is what stops it discovering that half the reply is below the bottom
    edge; :attr:`lines` is the reply as it will actually read once wrapped. A value that only
    described the strokes would leave all three to be found out by looking at the tablet.

    There is no field echoing the input back. A receipt that restates its own request invites
    a caller to check the request instead of the result -- the rule
    :class:`~rmspec.domain.ports.formats.SceneEdit` states about itself.

    Attributes
    ----------
    strokes
        The ink, in draw order, with samples already in screen units and x measured from the
        centre of the page -- what :class:`~rmspec.domain.models.Point` means everywhere else,
        and what :meth:`~rmspec.domain.ports.formats.SceneAppender.append_strokes` accepts. So
        the placement millimetres are converted here, once, by the implementation that owns the
        font metrics; a port that returned normalised coordinates would mean the ink written is
        not the ink previewed.
    lines
        The text as wrapped, one entry per drawn line, in order. Non-empty. Present because
        wrapping is a decision the implementation makes and the caller did not, and an
        unreported decision is one nobody can preview or disagree with.
    substituted
        The distinct characters the font could not draw, in first-appearance order, each
        rendered as a struck box. Empty when every character was drawable. Never a raise: an
        undrawable character is a fact about the string, and the caller is the only layer that
        knows whether a box on the page is acceptable or whether the words must be changed.
        Deduplicated because a reader acts per character, not per occurrence.
    extent_mm
        The real-world size of the box the strokes actually occupy, so a caller can check that
        the reply fits on the page before writing it. This is the check that replaces a
        coordinate-range validator on the scene itself: the reference corpus has 13 of 30 pages
        with ink outside the declared x range and 17 outside y, so out-of-bounds ink is normal
        on pages the tablet wrote and refusing it would refuse the tablet's own documents. Ink
        *this* program is about to place is a different question, and it is answered here.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    strokes: tuple[Stroke, ...] = Field(min_length=1)
    lines: tuple[str, ...] = Field(min_length=1)
    substituted: tuple[str, ...]
    extent_mm: PhysicalSize

    @model_validator(mode="after")
    def _check_substituted_is_distinct_characters(self) -> Self:
        """Reject a substitution report that is not distinct single characters.

        Returns
        -------
        InkText
            The validated model.

        Raises
        ------
        ValueError
            An entry is not exactly one character, or a character is reported twice. Both
            would make a caller's "which characters must I change" list wrong -- a multi-
            character entry is a substring nobody can look up, and a repeat turns "three
            characters to fix" into "three occurrences of one".
        """
        wrong_length = [entry for entry in self.substituted if len(entry) != 1]
        if wrong_length:
            msg = f"substituted must hold single characters, not {wrong_length!r}"
            raise ValueError(msg)
        if len(set(self.substituted)) != len(self.substituted):
            msg = f"substituted repeats a character: {self.substituted!r}"
            raise ValueError(msg)
        return self


class RenderStyle(BaseModel):
    """The render policy chosen at composition, with no field defaulted.

    No defaults anywhere, deliberately. The legacy renderer imported ``RM2_SCREEN`` and
    ``EXPORT_PALETTE`` as in-module fallbacks on a Paper-Pro-only product, so every caller
    that omitted them rendered 1404x1872 geometry and every test that omitted them asserted
    the wrong ``x_shift``. Screen and palette are now required parameters of
    :meth:`PageRenderer.render`, and every field here is required, so "forgot to pass it" is
    a construction error rather than a silently different picture.

    ``thickness_scale`` in particular was a bare ``1.5`` with no owner: it is a calibration
    constant compensating on-screen against exported stroke weight, and it belongs to a
    composition-time decision that :meth:`digest` can see. ``text`` is the same correction
    applied to the typeface, which had no owner at all because no adapter drew typed text.

    There is no ``dpi`` field. Rasterization resolution belongs to the port that rasterizes
    and travels with the pixels it produced; a second copy here would be a second source of
    truth for a cache-key component.

    Attributes
    ----------
    thickness_scale
        Multiplier applied to every stroke width, compensating on-screen versus exported
        weight.
    min_padding_mm
        Minimum margin kept around ink that falls outside the page box, before the viewport
        is widened.
    text
        Typeface, size and line height for ``Layer.text_blocks``. Required, because an
        adapter handed no text policy is an adapter that invents one or drops the block.
    renderer_revision
        Opaque revision of the rendering rules in force -- bumped when a pen formula or the
        SVG structure changes. Data set in the composition root rather than a property on
        the port, because a value a double can echo cannot invalidate anything, and this one
        is what makes a pen-formula change miss the OCR cache instead of hitting it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    thickness_scale: float = Field(gt=0)
    min_padding_mm: float = Field(ge=0)
    text: TextStyle
    renderer_revision: str = Field(min_length=1)

    def digest(
        self,
        *,
        screen: ScreenSpec,
        palette: Palette,
        background: PageBackground | None,
    ) -> str:
        """Return a digest over every input that changes a rendered pixel.

        The screen and the palette are parameters rather than fields because they are domain
        models this module only annotates. Requiring them here is what makes the digest
        total: there is no way to compute a render identity that omits the geometry or the
        ink, which is how a palette or DPI change stops leaving a cached row valid-looking
        and wrong.

        ``background`` has no default for the same reason, and it is the component that most
        needed one removed. With ``background=None`` defaulted, ``digest(screen=s,
        palette=p)`` type-checked and returned the bare-ink identity for a render that had a
        template or an underlay drawn under it -- one silently omittable component inside a
        totality claim, which is a stale row served as valid, i.e. defect 3 exactly. Pass
        ``None`` explicitly to mean "no background"; there is no way to mean it by omission.

        Parameters
        ----------
        screen
            The screen geometry the page was rendered for.
        palette
            The palette the ink was resolved through, name and contents.
        background
            The background that was drawn beneath the ink, or ``None`` if there was none.
            Required, stated rather than defaulted.

        Returns
        -------
        str
            Lowercase hex SHA-256 over this policy, the screen, the palette and the
            background. Compose it with the source-file hash, the model id, the prompt
            version and the rasterization DPI to key an OCR or diagram cache row.
        """
        return digest_of(
            b"rmspec.render.style.v2",
            _canonical_json(self).encode(),
            _canonical_json(screen).encode(),
            _canonical_json(palette).encode(),
            b"none" if background is None else background.digest().encode(),
        )


class RenderedPage(BaseModel):
    """One finished SVG page: its markup, the physical page it fills, and what it cost.

    Markup as ``str``, not ``bytes`` with a media type. A media type would be runtime
    content negotiation in a port whose output type is statically known, and every consumer
    of this value -- SVG export, PNG rasterization, PDF composition, both OCR paths -- wants
    SVG text; a byte-plus-label pair type-checks clean and fails at run time. It also costs
    the tests the one assertable artifact: ``ElementTree.fromstring(page.svg)`` is the whole
    setup, with no temporary directory and no ``.rm`` fixture.

    ``stroke_count`` and ``text_block_count`` exist so a test can assert a page is not blank
    without parsing XML, which is the assertion the legacy silent-return background path most
    needed. They count *content of two kinds* because a page can hold either, and a single
    stroke counter reported a text-only page as blank.

    Nothing here is self-certifying, and the three counters plus ``size`` are the fields a
    lying double would use if it could. They are constrained by arithmetic the caller already
    knows, not by a validator this model can run, so the shared contract suite pins them
    against the input page every adapter and every double is run over:

    - ``size`` equals ``screen.width_mm``/``height_mm`` unless a ``VIEWPORT_EXPANDED`` notice
      is present, in which case it is strictly greater in at least one dimension. Without
      that pin, ``PhysicalSize(width_mm=1.0, height_mm=1.0)`` validates, which is the "fakes
      lie" hole ``viewport_for`` was deleted for, one type along.
    - ``stroke_count`` is at most ``page.stroke_count``, and is zero if and only if the page
      has no strokes in any visible layer.
    - ``text_block_count`` equals the number of visible non-empty text blocks on the page
      unless a ``TEXT_OMITTED`` notice is present, in which case it is strictly fewer.

    Attributes
    ----------
    page_ref
        Stable identity of the page this markup was rendered from, echoed from the page the
        renderer was given. Distinct values are what let a multi-page export detect a
        dropped or duplicated page.
    svg
        The SVG document text.
    size
        The real-world size of the page this markup fills.
    stroke_count
        Number of strokes committed to the markup. Zero is legal: an empty page renders.
    text_block_count
        Number of typed text blocks committed to the markup. Zero on a page that has text
        blocks is legal only alongside a ``TEXT_OMITTED`` notice, which is what stops a
        text-only page from returning as a valid-looking blank one.
    notices
        Substitutions and omissions the render survived, in the order they occurred. Always
        data: there is no mode flag that turns a notice into an exception, because strictness
        is a use-case decision and a flag would restore the silence notices exist to remove.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_ref: str = Field(min_length=1)
    svg: str = Field(min_length=1)
    size: PhysicalSize
    stroke_count: int = Field(ge=0)
    text_block_count: int = Field(ge=0)
    notices: tuple[RenderNotice, ...] = ()

    @model_validator(mode="after")
    def _check_svg_root(self) -> Self:
        """Reject text that is not an SVG document.

        A one-substring check, but it is what stops a test double from returning ``"x"`` as
        a page: a fake has to produce markup a real rasterizer would accept.

        Returns
        -------
        RenderedPage
            The validated model.

        Raises
        ------
        ValueError
            If ``svg`` contains no ``<svg>`` root element.
        """
        if _SVG_ROOT_TAG not in self.svg:
            msg = "svg must contain an <svg> root element"
            raise ValueError(msg)
        return self


class PageRenderer(Protocol):
    """Turn one parsed page into one in-memory SVG document.

    Scope: ``APP``. Stateless, deterministic and thread-safe; every per-invocation input
    arrives as an argument, so there is nothing to open, cache or close.

    One page per call, deliberately. A 200-page export streams as a generator in the use
    case rather than materializing 200 SVG strings, and multi-page composition -- which is
    inherently plural and belongs to the PDF composer -- stays out of a signature that
    could only fake it.

    Not ``runtime_checkable``: nothing needs ``isinstance`` against it, and a structural
    check that passes on the method name while the semantics differ is worse than no check.
    Conformance is asserted by the shared contract suite that both the real adapter and its
    double are run through.

    Notes
    -----
    The rejected variants of this port were refuted on five points, each answered by
    something present in or absent from the signature above rather than by argument:

    - "``render`` cannot draw ``Layer.text_blocks``, and the signature gives no adapter a way
      to say it didn't, so a text-only page comes back as a validator-passing blank ``<svg>``
      and the words vanish from the PNG, the PDF and both OCR paths." Answered by three
      additions: :class:`TextStyle` on :class:`RenderStyle` owns the font policy that was
      unowned, :attr:`RenderedPage.text_block_count` makes drawing and dropping different
      values, and :attr:`RenderNoticeCode.TEXT_OMITTED` makes dropping reportable. A
      font-metric-free adapter is still implementable; a silent one is not.

    - "``svg: str`` names one adapter's serialization; a future PDF or Skia backend would
      write binary into a field called ``svg``." Answered by scope: the backends that carry
      a native library are export-slice ports over an already-rendered page, and both
      judging lenses ranked ``svg: str`` above ``bytes`` plus a media type for exactly the
      run-time-failure reason recorded on :class:`RenderedPage`.
    - "A template-malformed error contradicts touching no filesystem." Answered by
      :class:`PageBackground`: markup arrives already read, so the only background error
      left is the parse failure this port genuinely observes.
    - "``media_type`` and ``palette_name`` are stringly typed adapter concepts." Answered by
      deletion: there is no media type here, and the palette arrives as a value object.
    - "Fakes lie, because ``viewport_for`` lets a double define the geometry the app is
      asserted against." Answered by deletion: layout is a domain value, computed once.
    """

    def render(
        self,
        page: Page,
        /,
        *,
        screen: ScreenSpec,
        palette: Palette,
        style: RenderStyle,
        background: PageBackground | None = None,
    ) -> RenderedPage:
        """Render ``page`` to SVG markup.

        Ink and typed text are both content, and both have to be accounted for. For every
        visible layer, an implementation draws the strokes, then draws ``layer.text_blocks``
        over them using ``style.text``, and reports what it committed as
        :attr:`RenderedPage.stroke_count` and :attr:`RenderedPage.text_block_count`. An
        implementation that will not lay out text -- a writer with no font metrics is a
        legitimate one -- must emit a single :attr:`RenderNoticeCode.TEXT_OMITTED` notice
        naming how many blocks it left out. Returning ``text_block_count`` below the page's
        visible non-empty block count with no such notice is a contract violation, not a
        degraded render, and it is the one the shared contract suite exists to catch: it is
        how a page of typed words became an indistinguishably blank one.

        Parameters
        ----------
        page
            The parsed page: its layers, strokes and pen data.
        screen
            Screen geometry to render for. Required and never defaulted, because a wrong
            screen silently produces a wrong-sized page with correctly placed ink.
        palette
            Palette resolving each pen colour to ink. Total over its colour enum by its own
            validator, so no ink can silently render black and this method has no
            unknown-colour error.
        style
            Thickness, padding, text policy and renderer revision.
        background
            Template markup and/or a rasterized underlay to draw beneath the ink. ``None``
            means no background, which is the only way to express absence.

        Returns
        -------
        RenderedPage
            The markup, the page's physical size, the stroke and text-block counts, and any
            notices. Its ``page_ref`` must identify ``page``.

        Raises
        ------
        UnsupportedPenType
            If a stroke carries a pen id these rules do not implement. Loud, because the
            legacy fallback substituted a fineliner: a plausible-looking page rendered with
            the wrong physics is indistinguishable from a correct one.
        BackgroundUnreadable
            If ``background.template_svg`` will not parse -- including markup with no
            ``<svg>`` root, which :class:`PageBackground` deliberately does not pre-screen, so
            that one bad file produces one error type. The legacy renderer returned silently
            on a parse failure, so a background simply vanished from the output.
        """
        ...


class TextEngraver(Protocol):
    """Draw a string as strokes, placed on a page, in the only form the tablet renders.

    Scope: ``APP``. Stateless, deterministic and thread-safe; every per-invocation input
    arrives as an argument, so there is nothing to open, cache or close. One string per call,
    because a reply is one message and a batch would have to invent a layout relationship
    between two of them.

    Not ``runtime_checkable``: nothing needs ``isinstance`` against it, and a structural check
    that passes on a method name while the semantics differ is worse than none.

    Notes
    -----
    **Substitution is data and never a raise.** A character the face cannot draw is reported
    on :attr:`InkText.substituted` and drawn as a struck box, and an implementation may not
    drop it, may not silently fold it onto a lookalike, and may not refuse the call over it.
    Dropping is how a sentence loses a word without anyone noticing. Folding is a silent edit
    to somebody's words, and the layer that knows what a character *meant* is never this one.
    Refusing would move a caller's decision into a font.

    **No coverage query.** There is no ``supports``, no ``characters`` set, and no
    ``can_draw``. A caller cannot usefully branch on one -- it would have to reimplement the
    implementation's own segmentation to apply it -- and a set published here is a claim about
    a font file the domain has never opened. The report on the returned value is both cheaper
    and true of the exact string that was asked about.

    **The placement is millimetres from the page's top-left, and the conversion happens
    inside.** ``x_shift`` -- the centre-origin correction every renderer applies, half the
    screen width -- is applied by the implementation, so a caller never adds it and never
    adds it twice. That the returned strokes are in centre-origin screen units is the whole
    reason they can be handed straight to
    :meth:`~rmspec.domain.ports.formats.SceneAppender.append_strokes`.
    """

    def engrave(
        self,
        text: str,
        /,
        *,
        screen: ScreenSpec,
        style: InkTextStyle,
        left_mm: float,
        top_mm: float,
        width_mm: float,
    ) -> InkText:
        """Engrave ``text`` as ink laid out inside a box on the page.

        Parameters
        ----------
        text
            The message to draw, already exactly as the caller wants it read. Must contain at
            least one non-whitespace character.
        screen
            Screen geometry the ink is placed against. Required and never defaulted, for the
            reason :meth:`PageRenderer.render` gives: a wrong screen silently produces
            correctly-shaped ink in the wrong place, and here that means a reply the human
            cannot see.
        style
            Em size, line height, colour and thickness.
        left_mm
            Left edge of the text box, in millimetres from the page's left edge.
        top_mm
            Top edge of the text box, in millimetres from the page's top edge.
        width_mm
            Width of the text box, in millimetres. Lines wrap inside it; the box does not
            grow sideways, and its height is whatever the wrapped text needs -- reported as
            :attr:`InkText.extent_mm` rather than clipped, so a caller can see that a reply
            overran and decide, instead of writing ink that falls off the page.

        Returns
        -------
        InkText
            The strokes, the wrapped lines, the characters that became struck boxes, and the
            extent the ink occupies.

        Raises
        ------
        UsageError
            ``text`` holds nothing but whitespace, so there is no message to draw; or
            ``width_mm`` is too narrow to fit one character at ``style.em_mm``, so no wrap can
            succeed. Both are refusals about the request rather than about the string's
            contents, which is why an undrawable character is not among them.
        """
        ...
