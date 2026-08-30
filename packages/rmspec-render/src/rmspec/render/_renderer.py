"""The one ``PageRenderer`` adapter: a page in, an SVG document out.

Structurally the legacy ``SVGRenderer.render_page`` with the file sink amputated and the port's
value objects wired at the edges. Three things the legacy signature had are gone on purpose and
recorded in :class:`SvgPageRenderer`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rmspec.domain.errors import UnsupportedPenType
from rmspec.domain.ports.render import (
    PhysicalSize,
    RenderedPage,
    RenderNotice,
    RenderNoticeCode,
)
from rmspec.render._layout import layout_for
from rmspec.render._pens import model_for, profile_for
from rmspec.render._svg import (
    append_layer_group,
    append_stroke,
    append_template,
    append_underlay,
    new_document,
    serialize,
)
from rmspec.render._text import append_text_block, block_extent
from rmspec.render._units import mm_to_points, points_to_mm

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

    from rmspec.domain.models import Layer, Page, Palette, ScreenSpec, Stroke, TextBlock
    from rmspec.domain.ports.render import PageBackground, RenderStyle, TextStyle
    from rmspec.render._layout import PageLayout

__all__ = ["SvgPageRenderer"]


@dataclass(frozen=True, slots=True)
class SvgPageRenderer:
    """Render one parsed page to an in-memory SVG document.

    Stateless, deterministic, thread-safe and constructed with no arguments, so the
    composition root binds it once at ``Scope.APP`` and there is nothing to open or close.

    What the legacy renderer had and this does not
    ---------------------------------------------
    - **No ``output: Path``.** The markup is returned as ``RenderedPage.svg``; writing bytes
      belongs to the export slice's artifact writer and to the CLI. That is also what lets
      every test here assert on a string with no temporary directory and no ``.rm`` fixture.
    - **No ``palette=None`` and no ``screen=None`` defaults.** Those imported
      ``EXPORT_PALETTE`` and ``RM2_SCREEN`` as module-level fallbacks on a Paper-Pro-only
      product, so every caller that omitted them rendered 1404x1872 geometry with correctly
      placed ink -- which looks right until it is measured.
    - **No silent pen fallback and no silent background failure.** An unmodelled pen raises
      ``UnsupportedPenType`` and unparsable template markup raises ``BackgroundUnreadable``.

    Notes
    -----
    ``UnsupportedPenType`` is unreachable through this method for any page a parser can
    produce: every ``PenType`` member folds to one of the eleven canonical pens, all eleven
    have physics, and an unknown wire id is substituted upstream with
    ``PageDefectCode.UNKNOWN_PEN_SUBSTITUTED``. The raise exists so that adding a ``PenType``
    without a model fails loudly rather than drawing with the wrong physics, and the
    exhaustiveness test over the enum is what actually guards that. Do not read this
    package's coverage as evidence the path is live.

    ``RenderNoticeCode.TEXT_OMITTED`` is emitted for one reason only: a block whose ``pos_x``
    or ``pos_y`` is not a finite number, which cannot be placed at any coordinate and would
    otherwise serialise as ``x="nan"``. It is never emitted for a block that is merely far off
    the page, because such a block widens the viewport instead of being clipped by it -- see
    ``text_extents`` in :func:`rmspec.render._layout.layout_for`. The port also blesses a
    font-metric-free adapter that reports every block as omitted; this is not that adapter.
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

        A page whose ``content`` is ``None`` renders as an empty document rather than raising.
        The port declares exactly two exceptions, and inventing a third would break the
        contract; the readable/unreadable distinction stays where the domain put it, on
        ``Page.is_readable`` and ``Page.all_defects``, and ``--strict`` is the use case's
        decision over those. A zero-byte scene stub -- two thirds of a real PDF-backed
        document -- therefore renders as what it is: a page with no ink.

        Two sources of typed text, and the order they are drawn in
        ---------------------------------------------------------
        A page carries typed text in two places and both are drawn: ``Layer.text_blocks``,
        inside that layer's group and above that layer's ink, and ``PageContent.text_blocks``,
        which the file scopes to the whole page and which is drawn **last** -- after every
        layer group, as a direct child of the root ``<svg>``. Reading one and not the other is
        how words go missing, which is the defect this ordering exists to close: until this
        method read the page-level tuple, typed text was decoded by the codec and drawn by
        nobody.

        Last, and outside every group, for one reason: a page-level block carries no
        visibility flag, because the block behind it in the file has none -- which
        ``PageContent.text_blocks``' own docstring records. So it cannot be gated by
        ``Layer.visible`` the way a layer's blocks are, and it cannot be appended inside a
        layer's group without inheriting a group whose membership it does not have. A page
        whose every layer is hidden still has this text to draw. Document order is what fixes
        SVG paint order, so being last in the document is the whole of "on top"; no wrapper
        group is opened for these, because one would be empty for a page whose page-level text
        is all whitespace and the grouping buys nothing the ordering does not already give.

        Drawing this text is a deliberate divergence from the tablet
        -----------------------------------------------------------
        Measured on firmware 3.27.3.0: xochitl **preserves** a page-scoped text block written
        by a foreign author -- read back at the exact position set, with the foreign author id
        intact, across the tablet's own re-save -- and **never draws it**. Strokes are what it
        renders. This renderer draws it anyway, on purpose: the job of a tool whose whole
        purpose is reading a file is to show a caller what the file contains, not to reproduce
        one reader's blind spot. Do not "fix" this to match the device, and do not read it as a
        licence to build a reply feature by writing one of these blocks back -- the device will
        keep it and show nobody. Text a human is meant to see has to be ink.

        Parameters
        ----------
        page
            The parsed page.
        screen
            Screen geometry to render for.
        palette
            Palette resolving each pen colour to ink. Total by its own validator.
        style
            Thickness, padding, text policy and renderer revision.
        background
            Template markup and/or a rasterized underlay to draw beneath the ink.

        Returns
        -------
        RenderedPage
            The markup, the page's physical size, the counts and any notices.

        Raises
        ------
        UnsupportedPenType
            If a drawable stroke carries a pen these rules do not implement.
        BackgroundUnreadable
            If ``background.template_svg`` will not parse, including markup whose root
            element is not ``<svg>``.
        """
        layers: tuple[Layer, ...] = () if page.content is None else page.content.layers
        page_blocks: tuple[TextBlock, ...] = (
            () if page.content is None else page.content.text_blocks
        )
        underlay = None if background is None else background.underlay
        underlay_size_pt = (
            None
            if underlay is None
            else (
                mm_to_points(underlay.source_size.width_mm),
                mm_to_points(underlay.source_size.height_mm),
            )
        )
        layout = layout_for(
            layers,
            screen=screen,
            min_padding_pt=mm_to_points(style.min_padding_mm),
            text_extents=self._text_extents(layers, page_blocks, text=style.text),
            underlay_size_pt=underlay_size_pt,
        )

        root = new_document(layout)
        rescaled = False
        if underlay is not None:
            rescaled = append_underlay(root, underlay, layout=layout)
        if background is not None and background.template_svg is not None:
            append_template(root, background.template_svg, page_ref=page.ref)

        drawn = 0
        drawable = 0
        for index, layer in enumerate(layers):
            if not layer.visible:
                continue
            group = append_layer_group(root, index=index, name=layer.name)
            for stroke in layer.strokes:
                self._draw_stroke(
                    group,
                    stroke,
                    palette=palette,
                    layout=layout,
                    thickness=style.thickness_scale,
                    page_ref=page.ref,
                )
            offered, committed = self._draw_text(
                group,
                layer.text_blocks,
                style=style.text,
                layout=layout,
            )
            drawable += offered
            drawn += committed

        offered, committed = self._draw_text(root, page_blocks, style=style.text, layout=layout)
        drawable += offered
        drawn += committed

        return RenderedPage(
            page_ref=page.ref,
            svg=serialize(root),
            size=self._size_of(layout, screen=screen),
            stroke_count=page.stroke_count,
            text_block_count=drawn,
            notices=self._notices(layout, rescaled=rescaled, omitted=drawable - drawn),
        )

    @staticmethod
    def _text_extents(
        layers: tuple[Layer, ...],
        page_blocks: tuple[TextBlock, ...],
        /,
        *,
        text: TextStyle,
    ) -> tuple[tuple[float, float, float, float], ...]:
        """Estimate where every block that will be drawn lands, before the layout exists.

        The padding scan needs the boxes up front, which is why they are measured in screen
        units and transformed inside ``layout_for`` rather than here: the alternative is to
        recompute ``scale`` and ``x_shift`` in this method, and a second spelling of the
        centre-origin correction is exactly the duplication that puts ink half a page to one
        side.

        Both sources are measured, and the set measured here has to be exactly the set
        :meth:`render` draws: a block that is drawn but not measured is laid out outside the
        ``viewBox`` and clipped by every rasterizer, which is the silent vanishing act
        ``TEXT_OMITTED`` exists to make loud. Hence the layer filter applies to layer blocks
        only -- a page-level block has no visibility flag to filter on, and it is drawn
        whatever the layers do.

        Parameters
        ----------
        layers
            The page's layers in render order.
        page_blocks
            Typed text the page itself owns, drawn above every layer.
        text
            The text policy, which fixes the size and line height the estimate uses.

        Returns
        -------
        tuple[tuple[float, float, float, float], ...]
            One ``(min_x, min_y, max_x, max_y)`` box per block that will be drawn, in screen
            units, layer blocks first and in draw order.
        """
        return (
            *(
                block_extent(block, style=text)
                for layer in layers
                if layer.visible
                for block in layer.text_blocks
            ),
            *(block_extent(block, style=text) for block in page_blocks),
        )

    @staticmethod
    def _draw_text(
        parent: ET.Element,
        blocks: tuple[TextBlock, ...],
        /,
        *,
        style: TextStyle,
        layout: PageLayout,
    ) -> tuple[int, int]:
        """Draw one tuple of typed text blocks under ``parent``, and count both halves.

        One path for both of a page's text sources, and the parameter that tells them apart is
        ``parent``: a layer's group for ``Layer.text_blocks``, the root ``<svg>`` for
        ``PageContent.text_blocks``. Two spellings of this loop is how the two sources would
        drift into different placement, different escaping or -- the original defect -- one of
        them not being drawn at all.

        Parameters
        ----------
        parent
            The element the lines are appended to.
        blocks
            The blocks to draw, in order.
        style
            Family, size and line height.
        layout
            The geometry.

        Returns
        -------
        tuple[int, int]
            ``(offered, committed)``: how many blocks held something to draw, and how many
            were actually placed. The difference is what the caller reports as
            ``TEXT_OMITTED``, and it is non-zero only for a block whose corner is not a finite
            number.
        """
        offered = 0
        committed = 0
        for block in blocks:
            if block.text.strip():
                offered += 1
            if append_text_block(parent, block, style=style, layout=layout):
                committed += 1
        return (offered, committed)

    @staticmethod
    def _draw_stroke(
        group: ET.Element,
        stroke: Stroke,
        /,
        *,
        palette: Palette,
        layout: PageLayout,
        thickness: float,
        page_ref: str,
    ) -> None:
        """Resolve one stroke's pen and draw it.

        The stroke's own ``thickness_scale`` selects the base width; ``thickness`` is the
        export multiplier from ``RenderStyle``. They are different numbers and the parameter
        names keep them apart.

        Parameters
        ----------
        group
            The layer group element.
        stroke
            The stroke to draw.
        palette
            Palette resolving the stroke's colour index. Total, so there is no unknown-ink
            path -- but consulted only when the stroke carries no colour of its own; see
            :func:`_ink_for`.
        layout
            The geometry.
        thickness
            The export multiplier.
        page_ref
            The page's identity, for the error message.

        Raises
        ------
        UnsupportedPenType
            If no rule or model covers the stroke's pen.
        """
        profile = profile_for(stroke.pen, stroke_thickness=stroke.thickness_scale)
        model = None if profile is None else model_for(stroke.pen, base_width=profile.base_width)
        if profile is None or model is None:
            raise UnsupportedPenType(pen=stroke.pen.name, page_ref=page_ref)
        append_stroke(
            group,
            stroke,
            base_color=_ink_for(stroke, palette=palette),
            layout=layout,
            profile=profile,
            model=model,
            thickness=thickness,
        )

    @staticmethod
    def _size_of(layout: PageLayout, /, *, screen: ScreenSpec) -> PhysicalSize:
        """Return the real-world size of the document that was just written.

        Always the box the markup's own ``width`` and ``height`` describe. Reporting the
        screen's size instead would understate an ordinary page by twice the minimum margin --
        13% on a reMarkable 2 -- and the export slice sizes PNG and PDF pages from this value.
        The screen's own millimetres are used verbatim when no margin was applied at all, so
        an unpadded render compares exactly equal to ``screen.width_mm`` rather than within an
        ulp of it.

        Parameters
        ----------
        layout
            The geometry.
        screen
            The screen, for the unpadded case.

        Returns
        -------
        PhysicalSize
            The document's size in millimetres.
        """
        if not layout.expanded:
            return PhysicalSize(width_mm=screen.width_mm, height_mm=screen.height_mm)
        return PhysicalSize(
            width_mm=points_to_mm(layout.total_width),
            height_mm=points_to_mm(layout.total_height),
        )

    @staticmethod
    def _notices(
        layout: PageLayout,
        /,
        *,
        rescaled: bool,
        omitted: int,
    ) -> tuple[RenderNotice, ...]:
        """Assemble the substitutions this render survived.

        Parameters
        ----------
        layout
            The geometry.
        rescaled
            Whether an underlay's native size differed from the page box.
        omitted
            How many visible non-empty text blocks were not committed. Non-zero only for a
            block whose corner is not a finite number, which cannot be placed at all.

        Returns
        -------
        tuple[RenderNotice, ...]
            In the order they occurred.
        """
        notices: list[RenderNotice] = []
        if layout.expanded:
            detail = (
                f"content fell outside the page box, so the viewport grew to "
                f"{points_to_mm(layout.total_width):.1f}x"
                f"{points_to_mm(layout.total_height):.1f} mm"
                if layout.content_overflowed
                else f"a uniform {points_to_mm(layout.min_padding):.1f} mm margin was kept "
                f"around the page box"
            )
            notices.append(RenderNotice(code=RenderNoticeCode.VIEWPORT_EXPANDED, detail=detail))
        if omitted:
            notices.append(
                RenderNotice(
                    code=RenderNoticeCode.TEXT_OMITTED,
                    detail=(
                        f"{omitted} typed text block(s) carry a position that is not a finite "
                        f"number and could not be placed"
                    ),
                )
            )
        if rescaled:
            notices.append(
                RenderNotice(
                    code=RenderNoticeCode.UNDERLAY_RESCALED,
                    detail=(
                        "the underlay's native page size differs from this page's, so it was "
                        "placed at its own size and centred on the ink origin"
                    ),
                )
            )
        return tuple(notices)


def _ink_for(stroke: Stroke, /, *, palette: Palette) -> tuple[int, int, int]:
    """Resolve one stroke's ink, preferring the colour the stroke carried itself.

    The palette is a mapping from a colour *index* to an ink, and for thirteen of the
    fourteen indices the index is the whole truth. It is not for the highlighter: the
    firmware writes ``PenColor.HIGHLIGHT`` for every highlight whatever colour it was drawn
    in, and puts the real colour in a per-stroke field the formats adapter now reads into
    ``Stroke.color_override``. Without this preference, four highlighter colours all resolve
    to the palette's one yellow, which is what they did.

    The override's alpha is deliberately not consulted. Coverage is the pen's business --
    ``HighlighterModel.segment_opacity`` returns a calibrated ``0.3`` -- and both measured
    strokes carried a fully opaque ``a=255``, so honouring it here would replace a
    translucent highlight with an opaque block that hides the writing underneath it.

    Parameters
    ----------
    stroke
        The stroke being drawn.
    palette
        Palette resolving the stroke's colour index. Total, so the fallback cannot miss.

    Returns
    -------
    tuple[int, int, int]
        Channels in ``0``-``255``, in the shape the pen models take.
    """
    override = stroke.color_override
    ink = palette.rgb(stroke.color) if override is None else override.as_rgb()
    return ink.as_tuple()
