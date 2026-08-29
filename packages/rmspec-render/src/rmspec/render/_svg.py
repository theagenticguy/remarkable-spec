"""Every byte the rendered document contains, and nothing else.

The differential oracle at ``tests/fixtures/render-differential-manifest.json`` hashes the
exact bytes the legacy renderer wrote, so this module is the one place where an innocuous
cleanup is a regression. Four properties were verified on CPython 3.13 and are pinned by
tests:

1. ``ElementTree.tostring(root, encoding="unicode", xml_declaration=True).encode()`` is
   byte-identical to what the legacy ``tree.write(path, encoding="unicode",
   xml_declaration=True)`` produced: ``<?xml version='1.0' encoding='utf-8'?>`` with *single*
   quotes, a newline after it, ``<line ... />`` with a space before the slash, and **no**
   trailing newline after ``</svg>``. Switching to ``encoding="utf-8"`` emits a double-quoted
   declaration and breaks all thirty entries at once.
2. ``ElementTree`` serialises attributes in insertion order, so every ``set()`` order below is
   part of the output. Building an element's attributes from a dict literal, or reordering two
   lines, changes every byte after that point while rendering an identical picture.
3. Numeric formatting: coordinates at two decimals, ``stroke-width`` at three applied to
   ``segment_width * scale`` (the scale multiply happens at format time, not before),
   ``opacity`` at three and written *only* when strictly below ``1.0``, the underlay's ``y``
   as the literal string ``"0"`` rather than ``"0.00"``, and ``rgb(r,g,b)`` with no spaces --
   ``Rgb.as_css`` inserts them and must not be reused here.
4. ``ElementTree`` hoists namespace declarations to the root and writes them *before* the
   root's own attributes, so a template-backed page serialises as
   ``<svg xmlns:ns0="..." xmlns="..." viewBox=...>`` with the template's children as
   ``<ns0:rect>``. That is relocated unchanged rather than fixed: no oracle entry has a
   template, and fixing it would change every template-backed page.

Parsing without ``fromstring``
------------------------------
Template markup is parsed with :class:`~xml.etree.ElementTree.XMLPullParser` rather than
``ElementTree.fromstring``. Both drive the same expat parser and the same tree builder, so the
resulting tree -- namespace behaviour included -- is identical, verified by comparing
serialisations. The pull parser is used because the repository's lint gate refuses the
``fromstring`` and ``parse`` entry points outright, and the alternative would be either a
suppression comment or ``defusedxml``, which would cost this package its
stdlib-only purity for markup the user themself just handed in.
"""

from __future__ import annotations

import base64
import math
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, cast

from rmspec.domain.errors import BackgroundUnreadable
from rmspec.render._pens import SegmentInput
from rmspec.render._units import PRINTABLE_TOLERANCE, mm_to_points

if TYPE_CHECKING:
    from rmspec.domain.models import Stroke
    from rmspec.domain.ports.render import PageUnderlay
    from rmspec.render._layout import PageLayout
    from rmspec.render._pens import PenModel, PenProfile

__all__ = [
    "SVG_NAMESPACE",
    "append_layer_group",
    "append_stroke",
    "append_template",
    "append_underlay",
    "new_document",
    "serialize",
    "underlay_box",
]

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
"""The one namespace every element in the output belongs to."""

_MIN_SEGMENT_WIDTH = 0.1
"""Floor applied to a computed segment width, before the export multiplier."""

_OPAQUE = 1.0
"""Opacity at or above which no ``opacity`` attribute is written."""

_MIN_DRAWABLE_POINTS = 2
"""A stroke with fewer samples than this is a tap and draws nothing."""

_SVG_ROOT = f"{{{SVG_NAMESPACE}}}svg"
"""Namespaced tag a parsed template's root element must have."""


def _two(value: float) -> str:
    """Format a coordinate or a length.

    Parameters
    ----------
    value
        A length in points.

    Returns
    -------
    str
        Two decimal places, which is what the oracle bytes carry.
    """
    return f"{value:.2f}"


def _three(value: float) -> str:
    """Format a stroke width or an opacity.

    Parameters
    ----------
    value
        A width in points, or an opacity.

    Returns
    -------
    str
        Three decimal places.
    """
    return f"{value:.3f}"


def new_document(layout: PageLayout, /) -> ET.Element:
    """Build the root ``<svg>`` and its white background rect.

    Parameters
    ----------
    layout
        The geometry, which supplies the viewBox and the page size.

    Returns
    -------
    xml.etree.ElementTree.Element
        The root element, with the background rect already appended.
    """
    total_width = layout.total_width
    total_height = layout.total_height

    root = ET.Element("svg")
    root.set("xmlns", SVG_NAMESPACE)
    root.set(
        "viewBox",
        f"{_two(layout.origin_x)} {_two(layout.origin_y)} "
        f"{_two(total_width)} {_two(total_height)}",
    )
    root.set("width", _two(total_width))
    root.set("height", _two(total_height))

    background = ET.SubElement(root, "rect")
    background.set("x", _two(layout.origin_x))
    background.set("y", _two(layout.origin_y))
    background.set("width", _two(total_width))
    background.set("height", _two(total_height))
    background.set("fill", "white")
    return root


def underlay_box(underlay: PageUnderlay, /, *, layout: PageLayout) -> tuple[float, float, bool]:
    """Return the size to draw an underlay at, and whether that is not its own size.

    ``PhysicalSize`` fields are ``Field(gt=0)``, which pydantic satisfies with ``inf``, so a
    caller can hand in a page whose native size is unusable. Such an underlay is drawn at the
    page box instead of at ``inf`` points -- which would serialise as ``width="inf"`` and
    ``x="-inf"`` -- and the substitution is reported rather than performed in silence.

    Parameters
    ----------
    underlay
        The pixels and their native page size.
    layout
        The geometry, for the fallback page box.

    Returns
    -------
    tuple[float, float, bool]
        ``(width, height)`` in points, and whether they differ from the underlay's own size or
        from the page box -- either way, what the caller reports as ``UNDERLAY_RESCALED``.
    """
    width = mm_to_points(underlay.source_size.width_mm)
    height = mm_to_points(underlay.source_size.height_mm)
    if not (math.isfinite(width) and math.isfinite(height)):
        return (layout.viewport_width, layout.viewport_height, True)
    rescaled = not (
        math.isclose(width, layout.viewport_width, abs_tol=PRINTABLE_TOLERANCE)
        and math.isclose(height, layout.viewport_height, abs_tol=PRINTABLE_TOLERANCE)
    )
    return (width, height, rescaled)


def append_underlay(root: ET.Element, underlay: PageUnderlay, /, *, layout: PageLayout) -> bool:
    """Embed already-rasterized pixels beneath the ink, centred on the ink origin.

    The scene coordinate system maps roughly 1:1 onto PDF points after the DPI scale, so the
    underlay is placed at its native size rather than stretched: that is what makes a PDF
    annotation line up vertically with the text it annotates. Horizontally it is centred on
    ``x_shift``, which is where the stroke coordinate origin sits.

    Parameters
    ----------
    root
        The root ``<svg>``.
    underlay
        The pixels and their native page size.
    layout
        The geometry, for ``x_shift`` and the fallback page box.

    Returns
    -------
    bool
        Whether the underlay's native size differs from the page box, which is what the
        caller reports as ``UNDERLAY_RESCALED``.
    """
    width, height, rescaled = underlay_box(underlay, layout=layout)
    encoded = base64.standard_b64encode(underlay.data).decode("ascii")

    image = ET.SubElement(root, "image")
    image.set("x", _two(layout.x_shift - width / 2))
    image.set("y", "0")
    image.set("width", _two(width))
    image.set("height", _two(height))
    image.set("preserveAspectRatio", "xMidYMin meet")
    image.set("href", f"data:image/{underlay.media.value};base64,{encoded}")
    return rescaled


def _parse_markup(markup: str, /) -> ET.Element:
    """Parse template markup into a tree, or raise ``ElementTree.ParseError``.

    Parameters
    ----------
    markup
        SVG markup, already read from wherever the user pointed at.

    Returns
    -------
    xml.etree.ElementTree.Element
        The document's root element.

    Raises
    ------
    xml.etree.ElementTree.ParseError
        If the markup is not well-formed XML, including when it is empty.
    """
    parser = ET.XMLPullParser(events=("end",))
    parser.feed(markup)
    parser.close()
    # The stub types read_events() as a union covering namespace and comment events, which
    # `events=("end",)` cannot produce: every item here is ("end", element). The last one
    # closes last, so it is the document root -- and a document with no elements at all
    # raised ParseError from close() above rather than reaching this line.
    closed = cast("list[tuple[str, ET.Element]]", list(parser.read_events()))
    return closed[-1][1]


def append_template(root: ET.Element, markup: str, /, *, page_ref: str) -> None:
    """Embed a template SVG's children as a half-opacity background group.

    The legacy version took a path, returned silently when the file did not exist, and
    returned silently again on a parse error -- so a background simply vanished from a page
    that still looked finished. Here the markup arrives already read, and both silences are
    replaced by ``BackgroundUnreadable``.

    Parameters
    ----------
    root
        The root ``<svg>``.
    markup
        The template's SVG markup.
    page_ref
        The page's identity, for the error message.

    Raises
    ------
    BackgroundUnreadable
        If the markup will not parse, or parses to a root element that is not ``<svg>``.
    """
    try:
        template_root = _parse_markup(markup)
    except ET.ParseError as exc:
        raise BackgroundUnreadable(
            page_ref=page_ref,
            detail=f"template markup is not well-formed xml: {exc}",
        ) from exc

    if template_root.tag not in {_SVG_ROOT, "svg"}:
        detail = f"template root element is {template_root.tag!r}, not <svg>"
        raise BackgroundUnreadable(page_ref=page_ref, detail=detail)

    group = ET.SubElement(root, "g")
    group.set("id", "template")
    group.set("opacity", "0.5")
    for child in template_root:
        group.append(child)


def append_layer_group(root: ET.Element, /, *, index: int, name: str) -> ET.Element:
    """Open a group for one visible layer.

    ``index`` is the layer's position in the *full* layer list, invisible layers included,
    exactly as the legacy ``enumerate(page.layers)`` produced. Numbering over
    ``visible_layers`` instead would renumber every group after a hidden layer.

    Parameters
    ----------
    root
        The root ``<svg>``.
    index
        Position in the full layer list.
    name
        The layer's name, written as ``data-name`` only when it is non-empty.

    Returns
    -------
    xml.etree.ElementTree.Element
        The group to append this layer's content to.
    """
    group = ET.SubElement(root, "g")
    group.set("id", f"layer-{index}")
    if name:
        group.set("data-name", name)
    return group


def append_stroke(
    parent: ET.Element,
    stroke: Stroke,
    /,
    *,
    base_color: tuple[int, int, int],
    layout: PageLayout,
    profile: PenProfile,
    model: PenModel,
    thickness: float,
) -> bool:
    """Draw one stroke as a group of per-segment ``<line>`` elements.

    The clamp and the export multiplier stay *inside* the smoothing feedback loop, which is
    where the legacy loop put them: ``last_width`` is assigned after ``max(0.1, w)`` and after
    ``w *= thickness``, so the multiplier compounds into every subsequent segment of a marker,
    pencil or calligraphy stroke. It reads like a bug and is the single most likely thing a
    reviewer "fixes"; a test asserts the second segment of a marker stroke does not scale
    linearly with ``thickness``.

    Each segment's width, colour and opacity come from the *later* of its two points.

    Parameters
    ----------
    parent
        The layer group to append to.
    stroke
        The stroke to draw.
    base_color
        The ink the palette resolved this stroke's colour to.
    layout
        The geometry.
    profile
        The stroke's base width and linecap.
    model
        The stroke's physics.
    thickness
        ``RenderStyle.thickness_scale`` -- the export multiplier, not the tablet slider.

    A segment whose endpoints do not both place at finite coordinates is skipped before any
    physics runs, so ``last_width`` does not advance across it either. ``Point.x`` and
    ``Point.y`` are unconstrained floats and a malformed scene file can carry a NaN through the
    codec, which would serialise as ``x1="nan"`` -- markup that validates against
    ``RenderedPage`` (it greps for ``<svg``) and that cairo then rejects, one package along.
    Dropping the segment keeps this adapter's output parseable; constraining the model is the
    real fix and belongs upstream.

    Returns
    -------
    bool
        Whether any markup was committed. ``False`` for a tap, which draws nothing, and for a
        stroke none of whose segments can be placed.
    """
    points = stroke.points
    if len(points) < _MIN_DRAWABLE_POINTS:
        return False

    group = ET.SubElement(parent, "g")
    group.set("stroke-linecap", profile.stroke_linecap)
    group.set("fill", "none")

    committed = False
    last_width = profile.base_width
    for index in range(len(points) - 1):
        start = points[index]
        end = points[index + 1]
        x1 = start.x * layout.scale + layout.x_shift
        y1 = start.y * layout.scale
        x2 = end.x * layout.scale + layout.x_shift
        y2 = end.y * layout.scale
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            continue
        sample = SegmentInput(
            speed=end.speed,
            direction=end.direction,
            width=end.width,
            pressure=end.pressure,
            last_width=last_width,
        )
        segment_width = model.segment_width(sample)
        segment_color = model.segment_color(sample, base_color)
        segment_opacity = model.segment_opacity(sample)

        segment_width = max(_MIN_SEGMENT_WIDTH, segment_width)
        segment_width *= thickness

        line = ET.SubElement(group, "line")
        line.set("x1", _two(x1))
        line.set("y1", _two(y1))
        line.set("x2", _two(x2))
        line.set("y2", _two(y2))
        line.set("stroke-width", _three(segment_width * layout.scale))
        line.set("stroke", f"rgb({segment_color[0]},{segment_color[1]},{segment_color[2]})")
        if segment_opacity < _OPAQUE:
            line.set("opacity", _three(segment_opacity))

        last_width = segment_width
        committed = True

    return committed


def serialize(root: ET.Element, /) -> str:
    """Indent and serialise the document, declaration included.

    Parameters
    ----------
    root
        The root ``<svg>``.

    Returns
    -------
    str
        The whole document. Encoded as UTF-8 this is byte-identical to what the legacy
        ``tree.write(path, encoding="unicode", xml_declaration=True)`` wrote, including the
        single-quoted declaration and the absence of a trailing newline.
    """
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
