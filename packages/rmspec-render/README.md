# rmspec-render

Pure stroke rendering: ten pen-physics models and SVG generation. No native deps.

- Import path: `rmspec.render`
- Workspace dependencies: `rmspec-domain`
- Third-party dependencies: none

Part of the [remarkable-spec](../../README.md) workspace. The dependency
direction is asserted by `tests/architecture/test_dependency_direction.py`,
not merely documented here.

## Public surface

```python
from rmspec.render import (
    SvgPageRenderer,  # the one PageRenderer adapter
    SVG_RENDERER_REVISION,  # bind into RenderStyle.renderer_revision
    LEGACY_MIN_PADDING_MM,  # the legacy 30 pt margin, in mm
    LEGACY_THICKNESS_SCALE,  # the legacy 1.5 export multiplier
)
```

Four names, and the three constants are *values for the composition root to
inject* rather than defaults in a signature. `RenderStyle` has no defaulted
field and `PageRenderer.render` requires the screen and the palette, because the
legacy renderer's module-level `RM2_SCREEN` and `EXPORT_PALETTE` fallbacks
rendered wrong-sized pages for every caller that omitted an argument.

`SvgPageRenderer` is a zero-field frozen dataclass, so binding it is
`provide(SvgPageRenderer, scope=Scope.APP, provides=PageRenderer)` and there is
nothing to close.

## The load-bearing invariant

Scene `x` is measured from the **centre** of the page, so every rendered
coordinate is `x * scale + x_shift`, with `x_shift` equal to half the viewport
width in points. Getting it wrong produces a page that still looks like
handwriting and sits half a page to one side. `_layout.py` owns it.

The padding scan in the same module is a **gated recurrence**, not a maximum over
stroke extents: each of the four updates compares against the *running* pad, so
the result depends on point order. `PageContent.bounding_box` looks like the same
computation and is not — substituting it moves the viewBox of real pages while
rendering an identical picture, which is the single easiest way to break all
thirty differential hashes. `test_render_layout.py` pins the counterexample.

## Byte-exactness

`tests/fixtures/render-differential-manifest.json` holds the SHA-256 of the SVG
the legacy renderer produced for thirty real pages. This package reproduces all
thirty byte for byte.

```bash
# The corpus is a personal backup, outside the repository.
RMSPEC_DIFFERENTIAL_CORPUS=~/remarkable \
  uv run pytest packages/rmspec-render/tests/test_render_differential.py -q
```

The harness is skipped when the corpus or the legacy tree is absent — but three
of the thirty hashes are also reproduced from hand-built pages with no fixture at
all, in `test_render_svg_document.py`, so a clean CI machine is not silently
testing nothing.

On a mismatch the failure reports the byte-length delta first. A constant `+39`
names the XML declaration, `+1` names a trailing newline, anything larger names
geometry. `_svg.py`'s module docstring lists the four traps: the serialisation
envelope, attribute insertion order, numeric formatting, and ElementTree's
namespace hoisting.

## What does not live here

| Concern | Where it lives | Why |
| --- | --- | --- |
| SVG to PNG, PDF composition, PDF page rasterization | `rmspec-export` | cairo and pymupdf are native; keeping them out is what makes this package fully unit-testable |
| Palettes (`Palette`, `EXPORT_PALETTE`) | `rmspec.domain.models` | one source of truth for ink, and its validator is what deleted three silent black fallbacks |
| Parsing `.rm` scene files | `rmspec-formats` | this package never opens a file |
| `detect_screen` | nowhere — deliberately deleted | which device produced a document is a fact the device slice reports and the composition root binds, not one a renderer guesses from ink |
