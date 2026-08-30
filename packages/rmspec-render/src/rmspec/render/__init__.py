"""Pure stroke rendering: ten pen-physics models and SVG generation. No native deps.

This package implements exactly one port, ``rmspec.domain.ports.render.PageRenderer``, and
imports nothing but the standard library and ``rmspec.domain``. That purity is not a stylistic
preference: it is what makes every path here unit-testable with no fixture file, no temporary
directory and no native library, and it is enforced by
``tests/architecture/test_dependency_direction.py``. Cairo, pymupdf and Pillow belong to
``rmspec-export``; palettes belong to ``rmspec.domain.models``; the ``.rm`` codec belongs to
``rmspec-formats``.

The load-bearing invariant
--------------------------
Scene ``x`` is measured from the **centre** of the page, so every rendered coordinate is
``x * scale + x_shift`` with ``x_shift`` equal to half the viewport width in points. It is
stated here because getting it wrong produces a page that still looks like handwriting and sits
half a page to one side. :mod:`rmspec.render._layout` owns it.

Byte-exactness
--------------
``tests/fixtures/render-differential-manifest.json`` records the SHA-256 of the SVG the legacy
renderer produced for thirty real pages. This package reproduces all thirty byte for byte, and
``packages/rmspec-render/tests/test_render_differential.py`` is the harness. Anything that
changes attribute order, float formatting, indentation or the serialisation envelope is a
regression even when the picture is identical -- :mod:`rmspec.render._svg` documents each of
those four traps.

Reading a page is half of it; writing one is the other half
-----------------------------------------------------------
:func:`~rmspec.render.text_to_ink` runs the other direction: a string in, domain strokes out. It
is here rather than in ``rmspec-formats`` because turning letters into geometry is rendering, and
because ``formats`` may import ``domain`` only -- so the composition root is what joins ink to an
encoder. It exists at all because of one measurement, recorded at the top of
:mod:`rmspec.render._ink_text`: xochitl *preserves but never displays* a foreign author's
``RootTextBlock``, so text a human is meant to read off the glass has to be strokes.

It changes no existing page's output and therefore does not bump ``SVG_RENDERER_REVISION``: it
adds a capability nothing in ``PageRenderer.render`` calls, and a render of any page that
existed before it serialises byte for byte as it did.

The declared surface is the real surface
---------------------------------------
``__all__`` below is eight names, and ``tests/test_render_public_surface.py`` asserts that
``dir(rmspec.render)`` holds no ninth public one. The unit constants are therefore imported
under private aliases: they were public here by accident, absent from ``__all__``, and the
mm-to-point boundary belongs to one module. An export-slice caller that needs it should import
:mod:`rmspec.render._units` deliberately, or be given a public re-export deliberately -- not
inherit one from an implementation detail of the constant below.

Constants, not defaults
-----------------------
The three legacy-calibrated numbers below are exported as *values for the composition root to
inject*, never as defaults in a signature. ``RenderStyle`` has no defaulted field, and
``PageRenderer.render`` requires the screen and the palette, precisely because the legacy
module-level ``RM2_SCREEN`` and ``EXPORT_PALETTE`` fallbacks rendered wrong-sized pages for
every caller that omitted an argument.
"""

from __future__ import annotations

from rmspec.render._ink_text import (
    INK_TEXT_CHARACTERS,
    InkText,
    InkTextStyle,
    text_to_ink,
)
from rmspec.render._renderer import SvgPageRenderer
from rmspec.render._units import MM_PER_INCH as _MM_PER_INCH
from rmspec.render._units import POINTS_PER_INCH as _POINTS_PER_INCH

__all__ = [
    "INK_TEXT_CHARACTERS",
    "LEGACY_MIN_PADDING_MM",
    "LEGACY_THICKNESS_SCALE",
    "SVG_RENDERER_REVISION",
    "InkText",
    "InkTextStyle",
    "SvgPageRenderer",
    "text_to_ink",
]

SVG_RENDERER_REVISION = "svg-v3"
"""Opaque revision of the rendering rules, for ``RenderStyle.renderer_revision``.

Bump it whenever a pen formula, the geometry, or the emitted element and attribute structure
changes. That is what makes a formula change *miss* the OCR cache instead of returning a row
computed under the old physics.

``svg-v1`` -> ``svg-v2``: a stroke's own colour, when it carries one, now wins over the
palette entry for its colour index. Every highlighter stroke on a Paper Pro carries one, so
every page holding a non-yellow highlight renders different bytes than it did -- and a cached
raster or OCR row keyed on ``svg-v1`` was produced from a page where all four highlighter
colours came out yellow. Nothing about the geometry or the emitted structure moved, so a page
with no highlighter stroke serialises byte for byte as before; the bump is for the pages that
do, which the cache cannot tell apart from the ones that do not.

``svg-v2`` -> ``svg-v3``: ``PageContent.text_blocks`` is now drawn, last and above every
layer. That is where a page's real typed text lives -- one page-scoped block naming no layer
-- so before this, a page a human had typed on rendered with every typed word missing, and any
raster or OCR row cached under ``svg-v2`` for such a page was computed from markup that did not
contain them. Serving that row again would keep returning a transcription of text the file has
and the picture did not, which is the exact failure a revision exists to stop.

Same shape as the bump above, and bumped for the same reason: the emitted structure did not
move for a page with no page-level text, and all thirty recorded SVG hashes reproduce
byte for byte -- verified, not assumed, because none of the 92 files in the reference corpus
carries a page-level block or a scene text item. The corpus fact is why no hash moved; it is
not evidence the change is inert, because the cache cannot tell a page that carries typed text
from one that does not, and the only pages this alters are the ones it does alter.
"""

LEGACY_MIN_PADDING_MM = 30.0 * _MM_PER_INCH / _POINTS_PER_INCH
"""The legacy 30 pt minimum margin, in millimetres.

``RenderStyle`` carries millimetres because the domain does not speak points. This spelling
round-trips back to exactly ``30.0`` pt -- asserted next to the constant's test -- so binding it
reproduces the oracle geometry bit for bit. Any other value, or converting in the other
operation order, perturbs all four pads and can flip a printed viewBox digit.
"""

LEGACY_THICKNESS_SCALE = 1.5
"""The legacy export stroke-width multiplier, for ``RenderStyle.thickness_scale``.

A calibration constant compensating on-screen against exported stroke weight. It was a bare
``1.5`` with no owner in the legacy renderer; it is a value here and a required field there, so
the choice is visible to ``RenderStyle.digest``.
"""
