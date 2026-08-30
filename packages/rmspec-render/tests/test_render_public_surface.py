"""The package's public surface, pinned as an invariant rather than a convention.

Today ``rmspec.render`` exports two adapters' worth of surface -- the page renderer, the
text-to-ink writer and its two value objects, and three constants -- and everything else, the SVG
writer, the layout, the pen models, the font, the units, lives behind a leading underscore. That
is held in place by nothing but module naming and a hand-written ``__all__``, and the obvious next
change ("let ``rmspec-export`` compose a multi-page SVG without re-serialising") would re-export
:mod:`rmspec.render._svg` and put a mutable ``ElementTree.Element`` in the public surface with no
gate failing. It also already drifted once: ``MM_PER_INCH`` and ``POINTS_PER_INCH`` were
importable from here while absent from ``__all__``, because the module-level padding constant
needed them.

Three lines, and the cleanliness becomes an invariant.

Why the ink surface is public at all
------------------------------------
``text_to_ink`` is not an implementation detail of anything in this package: no method on
``PageRenderer`` calls it. It is public because the composition root is the only place allowed to
join it to a ``.rm`` encoder -- ``rmspec-formats`` may import ``rmspec.domain`` and nothing else,
so it cannot reach in here for the strokes and this package cannot reach out there to write them.
The four names below are exactly that seam, and no more of it.
"""

from __future__ import annotations

import rmspec.render

EXPECTED = [
    "INK_TEXT_CHARACTERS",
    "InkText",
    "InkTextStyle",
    "LEGACY_MIN_PADDING_MM",
    "LEGACY_THICKNESS_SCALE",
    "SVG_RENDERER_REVISION",
    "SvgPageRenderer",
    "text_to_ink",
]

#: ``from __future__ import annotations`` binds ``annotations`` as a module attribute. It is a
#: language artifact every module in the workspace carries, not something this package exports,
#: so it is named here rather than quietly filtered by a broader rule that would also hide a
#: real leak.
LANGUAGE_ARTIFACTS = {"annotations"}


def test_the_public_surface_is_exactly_what_all_declares() -> None:
    public = [
        name
        for name in dir(rmspec.render)
        if not name.startswith("_") and name not in LANGUAGE_ARTIFACTS
    ]
    assert sorted(public) == EXPECTED


def test_all_lists_every_name_and_no_others() -> None:
    assert sorted(rmspec.render.__all__) == EXPECTED


def test_every_exported_name_resolves() -> None:
    """``__all__`` entries that do not exist fail only on ``import *``, which nobody writes."""
    for name in rmspec.render.__all__:
        assert getattr(rmspec.render, name) is not None
