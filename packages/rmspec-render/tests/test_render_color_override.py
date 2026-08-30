"""Palette resolution when a stroke carries a colour of its own.

The highlighter is the one tool that writes its colour twice. Its ``PenColor`` is always
``HIGHLIGHT`` -- id 9 -- whatever colour it was drawn in, and its real colour arrives as
``Stroke.color_override`` from the formats adapter, which reads it out of bytes ``rmscene``
0.7.0 leaves unparsed. Resolving against the palette alone therefore drew every highlight in
one yellow, and this suite is the assertion that says otherwise.
"""

from __future__ import annotations

import pytest
from render_builders import LEGACY_STYLE, layer, page, point, stroke

from rmspec.domain.models import (
    EXPORT_PALETTE,
    RM2_SCREEN,
    Palette,
    PenColor,
    PenType,
    Rgb,
    Rgba,
    Stroke,
)
from rmspec.render import SVG_RENDERER_REVISION, SvgPageRenderer
from rmspec.render._renderer import _ink_for

RENDERER = SvgPageRenderer()

MEASURED_YELLOW = Rgba(r=0xFF, g=0xED, b=0x75)
"""``#ffed75``, off the first measured highlighter stroke: little-endian ``0xFFFFED75``."""

MEASURED_PALE_BLUE = Rgba(r=0xBE, g=0xEA, b=0xFE)
"""``#beeafe``, off the second. Both strokes reported ``PenColor.HIGHLIGHT``."""

PALETTE_HIGHLIGHT = "rgb(251,247,25)"
"""What ``EXPORT_PALETTE`` resolves ``PenColor.HIGHLIGHT`` to, and drew for all four colours."""


def highlight(override: Rgba | None) -> Stroke:
    """Build one drawable highlighter stroke at the wire's one highlight colour index."""
    return stroke(
        point(0.0, 0.0, pressure=200, width=400),
        point(40.0, 0.0, pressure=200, width=400),
        pen=PenType.HIGHLIGHTER_2,
        color=PenColor.HIGHLIGHT,
        color_override=override,
    )


def render_svg(*strokes: Stroke, palette: Palette = EXPORT_PALETTE) -> str:
    """Render one visible layer of strokes and return the markup."""
    rendered = RENDERER.render(
        page(layer(*strokes)),
        screen=RM2_SCREEN,
        palette=palette,
        style=LEGACY_STYLE,
    )
    return rendered.svg


def inks(svg: str) -> list[str]:
    """Return every distinct ``stroke="..."`` value in the markup, in first-seen order."""
    found: list[str] = []
    for fragment in svg.split('stroke="')[1:]:
        ink = fragment.split('"', 1)[0]
        if ink not in found:
            found.append(ink)
    return found


# ─────────────────────────── the fix ───────────────────────────


def test_two_highlights_of_one_colour_index_render_as_two_colours():
    """The defect, and the assertion that closes it.

    One document, two strokes, both ``PenColor.HIGHLIGHT``. Before the override was read they
    serialised the same ink and a pale blue highlight came out yellow.
    """
    svg = render_svg(highlight(MEASURED_YELLOW), highlight(MEASURED_PALE_BLUE))

    assert inks(svg) == ["rgb(255,237,117)", "rgb(190,234,254)"]
    assert PALETTE_HIGHLIGHT not in svg, "the palette's one yellow is no longer what is drawn"


def test_a_stroke_with_no_override_still_resolves_through_the_palette():
    svg = render_svg(highlight(None))

    assert inks(svg) == [PALETTE_HIGHLIGHT]


def test_the_override_does_not_disturb_the_pens_own_opacity():
    """Coverage is the pen's business, not the colour's.

    ``HighlighterModel`` returns a calibrated ``0.3``, and both measured strokes carried a
    fully opaque ``a=255``. Honouring that alpha here would replace a translucent highlight
    with an opaque block over the writing it marks.
    """
    with_override = render_svg(highlight(MEASURED_YELLOW))
    without = render_svg(highlight(None))

    assert 'opacity="0.300"' in with_override
    assert with_override.count('opacity="0.300"') == without.count('opacity="0.300"')


def test_the_renderer_revision_moved_because_the_output_bytes_did():
    """A cached raster keyed on ``svg-v1`` was produced with every highlight yellow.

    The constant is opaque to every consumer, so the only thing that can make a stale row
    miss is the value changing. Pinned rather than merely documented, because a revert that
    kept the colour fix and dropped the bump would silently serve the old rasters.

    ``svg-v2`` -> ``svg-v3`` for the same reason one step along: a row cached under ``svg-v2``
    for a page carrying page-level typed text was produced from markup that did not contain
    the words. No recorded SVG hash moved -- no file in the reference corpus carries such a
    block -- so the bump is deliberate rather than a consequence of a failing hash, and this
    assertion is what stops it being dropped as unnecessary.
    """
    assert SVG_RENDERER_REVISION == "svg-v3"


# ─────────────────────────── the resolver itself ───────────────────────────


def test_ink_for_prefers_the_stroke_over_the_palette():
    assert _ink_for(highlight(MEASURED_PALE_BLUE), palette=EXPORT_PALETTE) == (190, 234, 254)


def test_ink_for_falls_back_to_the_palette_entry_for_the_colour_index():
    assert _ink_for(highlight(None), palette=EXPORT_PALETTE) == (251, 247, 25)


@pytest.mark.parametrize("colour", list(PenColor))
def test_ink_for_is_total_over_every_colour_index_when_no_override_is_carried(colour: PenColor):
    """The palette's totality is what deletes the unknown-ink path, and the override keeps it.

    A stroke that carries no override must still resolve for every one of the fourteen
    indices, which is the property ``Palette``'s own validator guarantees.
    """
    plain = stroke(pen=PenType.FINELINER_1, color=colour)

    assert _ink_for(plain, palette=EXPORT_PALETTE) == EXPORT_PALETTE.rgb(colour).as_tuple()


def test_an_override_is_used_verbatim_and_not_looked_up_anywhere():
    """A palette that maps every index to one ink cannot influence an overridden stroke."""
    uniform = Palette(name="uniform", inks=dict.fromkeys(PenColor, Rgb(r=1, g=2, b=3)))

    assert _ink_for(highlight(MEASURED_YELLOW), palette=uniform) == (255, 237, 117)
    assert _ink_for(highlight(None), palette=uniform) == (1, 2, 3)
