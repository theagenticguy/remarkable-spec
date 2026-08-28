"""Ports for the render slice: one parsed page in, one SVG document out.

The slice has exactly one port, :class:`PageRenderer`, plus the value objects it
exchanges. Everything else the render lens proposed -- pen-model registries, pen physics
factories, palette sources, template sources, background sources -- is deliberately absent;
:ref:`the section below <not-ports>` records each one and why.

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

Cache identity (defect 3)
-------------------------
:meth:`RenderStyle.digest` folds *everything* that changes a pixel -- thickness, padding,
renderer revision, the screen, the palette's name **and** contents, and the background's
markup and bytes -- into one hex string. That string is the render component of an
``OcrCacheKey`` or ``DiagramCacheKey`` digest; the caller still folds in the source-file
hash, the model id, the prompt version and the rasterization DPI, which travel with the
pixels on the export slice's raster value. A DPI, palette or pen-formula change therefore
mechanically misses instead of returning a valid-looking stale row.

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

Note for whoever writes ``ports/__init__.py``
---------------------------------------------
This module may not import a sibling ports module, so three value objects here are
deliberate twins: :class:`ImageMedia` (twin of the OCR and export copies) and
:class:`PhysicalSize` (twin of the export copy) are field-for-field identical, and
:class:`RenderedPage` is the export slice's ``SvgPage`` plus ``stroke_count`` and
``notices``. Hoist one definition of each into a shared ``rmspec.domain.values`` module
rather than re-exporting same-named classes -- and note that until that hoist happens, a
use case has to convert this port's output before handing it to ``SvgRasterizer`` or
``PdfComposer``, which is a conversion no type checker will ask for.

Domain models (``Page``, ``ScreenSpec``, ``Palette``) are imported for annotations only, as
in ``ports/formats.py``: nothing in this module needs them at runtime.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from rmspec.domain.models import Page, Palette, ScreenSpec

__all__ = [
    "ImageMedia",
    "PageBackground",
    "PageRenderer",
    "PageUnderlay",
    "PhysicalSize",
    "RenderNotice",
    "RenderNoticeCode",
    "RenderStyle",
    "RenderedPage",
]

_FIELD_SEPARATOR = b"\x1f"
"""Byte separating digest components, so concatenation cannot be ambiguous."""

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
"""First eight bytes of every PNG file."""

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
    supply bytes a real renderer would embed.

    Parameters
    ----------
    media
        Declared encoding.
    data
        Encoded image bytes.

    Raises
    ------
    ValueError
        If ``data`` does not begin with ``media``'s signature.
    """
    signature = _PNG_SIGNATURE if media is ImageMedia.PNG else _JPEG_SIGNATURE
    if not data.startswith(signature):
        msg = f"data does not start with a {media.value} signature"
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
    """A substitution the renderer made and survived, reported instead of hidden.

    Two members, because two things in the legacy renderer silently changed the output.
    Anything the renderer cannot survive raises instead, so this enum stays closed and
    small: it is not a log level and it is not a warning channel.
    """

    VIEWPORT_EXPANDED = "viewport_expanded"
    """Ink fell outside the page box, so the viewport was widened past the screen size."""

    UNDERLAY_RESCALED = "underlay_rescaled"
    """The underlay's native page size differed from the note page's and was fitted."""


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

    Attributes
    ----------
    template_svg
        SVG markup to draw beneath the ink, or ``None``.
    underlay
        Rasterized pixels to draw beneath the ink, or ``None``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    template_svg: str | None = None
    underlay: PageUnderlay | None = None

    @model_validator(mode="after")
    def _check_something_present(self) -> Self:
        """Reject an empty background and markup that is not SVG.

        Returns
        -------
        PageBackground
            The validated model.

        Raises
        ------
        ValueError
            If both fields are ``None``, or if ``template_svg`` has no ``<svg>`` root.
        """
        if self.template_svg is None and self.underlay is None:
            msg = "a background must carry template markup, an underlay, or both"
            raise ValueError(msg)
        if self.template_svg is not None and _SVG_ROOT_TAG not in self.template_svg:
            msg = "template_svg must contain an <svg> root element"
            raise ValueError(msg)
        return self

    def digest(self) -> str:
        """Return a stable content digest of this background.

        Returns
        -------
        str
            Lowercase hex SHA-256 over the markup, the underlay encoding and its bytes.
        """
        hasher = hashlib.sha256()
        hasher.update(b"rmspec.render.background.v1")
        hasher.update(_FIELD_SEPARATOR)
        hasher.update((self.template_svg or "").encode())
        hasher.update(_FIELD_SEPARATOR)
        if self.underlay is not None:
            hasher.update(self.underlay.media.value.encode())
            hasher.update(_FIELD_SEPARATOR)
            hasher.update(self.underlay.data)
        return hasher.hexdigest()


class RenderStyle(BaseModel):
    """The render policy chosen at composition, with no field defaulted.

    No defaults anywhere, deliberately. The legacy renderer imported ``RM2_SCREEN`` and
    ``EXPORT_PALETTE`` as in-module fallbacks on a Paper-Pro-only product, so every caller
    that omitted them rendered 1404x1872 geometry and every test that omitted them asserted
    the wrong ``x_shift``. Screen and palette are now required parameters of
    :meth:`PageRenderer.render`, and the three policy numbers here are required fields, so
    "forgot to pass it" is a construction error rather than a silently different picture.

    ``thickness_scale`` in particular was a bare ``1.5`` with no owner: it is a calibration
    constant compensating on-screen against exported stroke weight, and it belongs to a
    composition-time decision that :meth:`digest` can see.

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
    renderer_revision
        Opaque revision of the rendering rules in force -- bumped when a pen formula or the
        SVG structure changes. Data set in the composition root rather than a property on
        the port, because a value a double can echo cannot invalidate anything, and this one
        is what makes a pen-formula change miss the OCR cache instead of hitting it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    thickness_scale: float = Field(gt=0)
    min_padding_mm: float = Field(ge=0)
    renderer_revision: str = Field(min_length=1)

    def digest(
        self,
        *,
        screen: ScreenSpec,
        palette: Palette,
        background: PageBackground | None = None,
    ) -> str:
        """Return a digest over every input that changes a rendered pixel.

        The screen and the palette are parameters rather than fields because they are domain
        models this module only annotates. Requiring them here is what makes the digest
        total: there is no way to compute a render identity that omits the geometry or the
        ink, which is how a palette or DPI change stops leaving a cached row valid-looking
        and wrong.

        Parameters
        ----------
        screen
            The screen geometry the page was rendered for.
        palette
            The palette the ink was resolved through, name and contents.
        background
            The background that was drawn beneath the ink, if any.

        Returns
        -------
        str
            Lowercase hex SHA-256 over this policy, the screen, the palette and the
            background. Compose it with the source-file hash, the model id, the prompt
            version and the rasterization DPI to key an OCR or diagram cache row.
        """
        hasher = hashlib.sha256()
        hasher.update(b"rmspec.render.style.v1")
        parts = (
            _canonical_json(self),
            _canonical_json(screen),
            _canonical_json(palette),
            "none" if background is None else background.digest(),
        )
        for part in parts:
            hasher.update(_FIELD_SEPARATOR)
            hasher.update(part.encode())
        return hasher.hexdigest()


class RenderedPage(BaseModel):
    """One finished SVG page: its markup, the physical page it fills, and what it cost.

    Markup as ``str``, not ``bytes`` with a media type. A media type would be runtime
    content negotiation in a port whose output type is statically known, and every consumer
    of this value -- SVG export, PNG rasterization, PDF composition, both OCR paths -- wants
    SVG text; a byte-plus-label pair type-checks clean and fails at run time. It also costs
    the tests the one assertable artifact: ``ElementTree.fromstring(page.svg)`` is the whole
    setup, with no temporary directory and no ``.rm`` fixture.

    ``stroke_count`` exists so a test can assert a page is not blank without parsing XML,
    which is the assertion the legacy silent-return background path most needed.

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
    notices
        Substitutions the render survived, in the order they occurred. Always data: there is
        no mode flag that turns a notice into an exception, because strictness is a
        use-case decision and a flag would restore the silence notices exist to remove.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_ref: str = Field(min_length=1)
    svg: str = Field(min_length=1)
    size: PhysicalSize
    stroke_count: int = Field(ge=0)
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
    The rejected variants of this port were refuted on four points, each answered by
    something absent from the signature above rather than by argument:

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
            Thickness, padding and renderer revision.
        background
            Template markup and/or a rasterized underlay to draw beneath the ink. ``None``
            means no background, which is the only way to express absence.

        Returns
        -------
        RenderedPage
            The markup, the page's physical size, the stroke count and any notices. Its
            ``page_ref`` must identify ``page``.

        Raises
        ------
        UnsupportedPenType
            If a stroke carries a pen id these rules do not implement. Loud, because the
            legacy fallback substituted a fineliner: a plausible-looking page rendered with
            the wrong physics is indistinguishable from a correct one.
        BackgroundUnreadable
            If ``background.template_svg`` will not parse. The legacy renderer returned
            silently on a parse failure, so a background simply vanished from the output.
        """
        ...
