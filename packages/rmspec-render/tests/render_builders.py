"""Builders shared by this package's suites.

A plain module rather than a ``conftest.py``, for two reasons. Per-package test directories are
deliberately not importable packages -- ``tests/architecture/test_test_module_names.py`` fails
the build if one grows an ``__init__.py`` -- so every non-package ``conftest.py`` in the
workspace would import under the bare name ``conftest`` and the second one collected would
collide with the first. A uniquely named module sidesteps that entirely, and it also lets these
builders be imported by name instead of arriving as invisible fixture magic.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import cast
from uuid import UUID

from rmspec.domain.models import (
    Layer,
    Page,
    PageContent,
    PageDefect,
    PageDefectCode,
    PageId,
    PenColor,
    PenType,
    Point,
    Rgba,
    Stroke,
    TextBlock,
)
from rmspec.domain.ports.render import (
    ImageMedia,
    PageUnderlay,
    PhysicalSize,
    RenderStyle,
    TextStyle,
)
from rmspec.render import (
    LEGACY_MIN_PADDING_MM,
    LEGACY_THICKNESS_SCALE,
    SVG_RENDERER_REVISION,
)

#: The page identity the differential manifest's ``render_params`` names.
ZERO_PAGE_UUID = str(UUID(int=0))

#: Text policy used wherever a test does not care about typed text. Every field is required
#: by ``RenderStyle``, so there is no way to omit it and no default to drift.
DEFAULT_TEXT_STYLE = TextStyle(family="sans-serif", size_px=32.0, line_height=1.2)

#: Exactly the parameter set the manifest records: thickness 1.5, the legacy 30 pt margin.
LEGACY_STYLE = RenderStyle(
    thickness_scale=LEGACY_THICKNESS_SCALE,
    min_padding_mm=LEGACY_MIN_PADDING_MM,
    text=DEFAULT_TEXT_STYLE,
    renderer_revision=SVG_RENDERER_REVISION,
)

#: A PNG header long enough for ``PageUnderlay``'s validator, which requires the signature
#: plus an IHDR-sized remainder -- 24 bytes, not the 8 of the signature alone.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(16))

#: A JPEG start-of-image marker plus filler, for the non-PNG media path.
JPEG_BYTES = b"\xff\xd8\xff" + bytes(range(21))


def style(
    *,
    thickness_scale: float = LEGACY_THICKNESS_SCALE,
    min_padding_mm: float = LEGACY_MIN_PADDING_MM,
    text: TextStyle = DEFAULT_TEXT_STYLE,
) -> RenderStyle:
    """Build a render policy, varying only what a test cares about."""
    return RenderStyle(
        thickness_scale=thickness_scale,
        min_padding_mm=min_padding_mm,
        text=text,
        renderer_revision=SVG_RENDERER_REVISION,
    )


def point(
    x: float,
    y: float,
    *,
    speed: int = 0,
    direction: int = 0,
    width: int = 0,
    pressure: int = 0,
) -> Point:
    """Build one stylus sample with every raw channel spelled out."""
    return Point(x=x, y=y, speed=speed, direction=direction, width=width, pressure=pressure)


def stroke(
    *points: Point,
    pen: PenType = PenType.FINELINER_1,
    color: PenColor = PenColor.BLACK,
    thickness_scale: float = 2.0,
    color_override: Rgba | None = None,
) -> Stroke:
    """Build one stroke from samples."""
    return Stroke(
        pen=pen,
        color=color,
        color_override=color_override,
        thickness_scale=thickness_scale,
        points=points,
    )


def layer(
    *strokes: Stroke,
    name: str = "Layer 1",
    visible: bool = True,
    text_blocks: tuple[TextBlock, ...] = (),
) -> Layer:
    """Build one layer."""
    return Layer(name=name, visible=visible, strokes=strokes, text_blocks=text_blocks)


def page(
    *layers: Layer,
    text_blocks: tuple[TextBlock, ...] = (),
    defects: tuple[PageDefect, ...] = (),
) -> Page:
    """Build a readable page from layers, at the manifest's page identity.

    ``text_blocks`` is the page's *own* typed text -- ``PageContent.text_blocks``, the
    page-scoped block naming no layer -- and is a different field from the identically named
    keyword on :func:`layer`. The two are not alternatives and a renderer owes both.
    """
    return Page(
        page_id=PageId(uuid=ZERO_PAGE_UUID),
        index=0,
        content=PageContent(layers=layers, text_blocks=text_blocks, defects=defects),
    )


def unreadable_page(code: PageDefectCode) -> Page:
    """Build a page whose scene bytes could not be turned into content."""
    return Page(
        page_id=PageId(uuid=ZERO_PAGE_UUID),
        index=0,
        content=None,
        defects=(PageDefect(code=code, detail="pinned by a test"),),
    )


def parse_svg(markup: str) -> ET.Element:
    """Parse rendered markup back into a tree.

    Uses the same pull parser the adapter does, because the repository's lint gate refuses
    ``ElementTree.fromstring`` everywhere, tests included.
    """
    parser = ET.XMLPullParser(events=("end",))
    parser.feed(markup)
    parser.close()
    closed = cast("list[tuple[str, ET.Element]]", list(parser.read_events()))
    return closed[-1][1]


def underlay(
    *,
    media: ImageMedia = ImageMedia.PNG,
    width_mm: float = 210.0,
    height_mm: float = 297.0,
) -> PageUnderlay:
    """Build a rasterized underlay with real header bytes."""
    return PageUnderlay(
        media=media,
        data=PNG_BYTES if media is ImageMedia.PNG else JPEG_BYTES,
        source_size=PhysicalSize(width_mm=width_mm, height_mm=height_mm),
    )
