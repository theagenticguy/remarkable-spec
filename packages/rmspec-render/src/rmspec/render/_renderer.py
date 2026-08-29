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

    from rmspec.domain.models import Layer, Page, Palette, ScreenSpec, Stroke
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
            text_extents=self._text_extents(layers, text=style.text),
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
            for block in layer.text_blocks:
                if block.text.strip():
                    drawable += 1
                if append_text_block(group, block, style=style.text, layout=layout):
                    drawn += 1

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
        /,
        *,
        text: TextStyle,
    ) -> tuple[tuple[float, float, float, float], ...]:
        """Estimate where every visible block will land, before the layout exists.

        The padding scan needs the boxes up front, which is why they are measured in screen
        units and transformed inside ``layout_for`` rather than here: the alternative is to
        recompute ``scale`` and ``x_shift`` in this method, and a second spelling of the
        centre-origin correction is exactly the duplication that puts ink half a page to one
        side.

        Parameters
        ----------
        layers
            The page's layers in render order.
        text
            The text policy, which fixes the size and line height the estimate uses.

        Returns
        -------
        tuple[tuple[float, float, float, float], ...]
            One ``(min_x, min_y, max_x, max_y)`` box per visible block, in screen units.
        """
        return tuple(
            block_extent(block, style=text)
            for layer in layers
            if layer.visible
            for block in layer.text_blocks
        )

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
            Palette resolving the stroke's colour. Total, so there is no unknown-ink path.
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
            base_color=palette.rgb(stroke.color).as_tuple(),
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
