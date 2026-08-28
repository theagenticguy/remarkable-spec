# remarkable-spec · Public API

`remarkable-spec` publishes two independent surfaces. This page documents the **library** surface —
what an importer gets from `import remarkable_spec`. The **CLI** surface is a separate contract with
its own reference page, `docs/reference/cli.md`; its eleven top-level commands are registered at
`src/remarkable_spec/cli/__init__.py:48-58` and are not repeated here.

The library surface has two tiers, and guessing the wrong one raises `ImportError` rather than
falling back:

- **Tier 1 — the package root.** `src/remarkable_spec/__init__.py:33-67` declares 26 names in
  `__all__`, and every import feeding that list comes from a `remarkable_spec.models.*` module
  (`src/remarkable_spec/__init__.py:6-31`). The root API is `models`-only by construction: nothing
  from `formats`, `render`, `ocr`, `device`, `sync`, or `export` is re-exported there. The `models`
  barrel mirrors the same 26 names (`src/remarkable_spec/models/__init__.py:47-81`), so
  `remarkable_spec.Page` and `remarkable_spec.models.Page` resolve identically.
- **Tier 2 — a subpackage only.** Each subpackage declares its own `__all__`: 8 names in
  `src/remarkable_spec/formats/__init__.py:21-35`, 13 in `src/remarkable_spec/render/__init__.py:37-54`,
  6 in `src/remarkable_spec/sync/__init__.py:22-29`, 4 in `src/remarkable_spec/device/__init__.py:20-25`,
  3 in `src/remarkable_spec/export/__init__.py:20-24`, 3 in `src/remarkable_spec/cli/__init__.py:38`,
  and 2 in `src/remarkable_spec/ocr/__init__.py:7`. These import as
  `remarkable_spec.formats.parse_rm_file`, never as `remarkable_spec.parse_rm_file`.

`src/remarkable_spec/py.typed` is present and tracked, so the distribution ships inline type
information and every signature below is part of the contract a type checker enforces on consumers.
All 55 tracked modules under `src/` open with `from __future__ import annotations` — for example
`src/remarkable_spec/models/stroke.py:14` — so annotations are strings at runtime and are quoted here
exactly as written (`str | None`, `dict[PenColor, RGB]`).

Signatures below are verbatim. Pydantic field declarations are not transcribed, because most span
several lines each; the citation on every entry points at the declaration line so the full field set
is one jump away.

Two decorator families widen the contract beyond the declared fields, and neither is visible in the
fences below:

- **`@computed_field` properties serialize.** Six models carry 23 of them, so `model_dump()` returns
  more keys than the field list implies: `Point` has `pressure_normalized`, `direction_radians`, and
  `tilt` (`src/remarkable_spec/models/stroke.py:61-81`); `Stroke` has `is_eraser`, `is_highlighter`,
  and `bounding_box` (`src/remarkable_spec/models/stroke.py:119-142`); `Layer` has `is_empty` and
  `bounding_box` (`src/remarkable_spec/models/page.py:74-96`); `Page` has `rm_filename`,
  `metadata_filename`, `thumbnail_filename`, and `all_strokes`
  (`src/remarkable_spec/models/page.py:124-146`); `Document` has `name`, `is_notebook`, `is_pdf`,
  `is_epub`, `is_folder`, and `is_trashed` (`src/remarkable_spec/models/document.py:343-377`); and
  `ScreenSpec` has `points_per_pixel`, `page_width_pt`, `page_height_pt`, `page_width_inches`, and
  `page_height_inches` (`src/remarkable_spec/models/screen.py:44-76`).
- **Nine `@classmethod` helpers sit alongside the plain constructors**, and the `from_*` ones are the
  intended entry point on the parse path: `PenType.is_highlighter`, `PenType.is_eraser`, and
  `PenType.canonical` (`src/remarkable_spec/models/pen.py:48-75`), `Pen.from_stroke`
  (`src/remarkable_spec/models/pen.py:131`), `DocumentMetadata.from_json` and
  `DocumentMetadata.from_path` (`src/remarkable_spec/models/document.py:107-130`),
  `ExtraMetadata.from_json` (`src/remarkable_spec/models/document.py:156`), and
  `ContentInfo.from_json` and `ContentInfo.from_path`
  (`src/remarkable_spec/models/document.py:259-304`).

One caveat on the screen models: `RM2_SCREEN` being exported is not a statement that the reMarkable 2
is a supported device. `README.md:9-12` names the Paper Pro as the supported hardware and records
other models as untested.

## Tier 1 — exported from the package root

Import as `from remarkable_spec import <name>`. Listed in the order
`src/remarkable_spec/__init__.py:33-67` declares them, which groups by concern.

### PenColor

```py
class PenColor(enum.IntEnum):
    BLACK = 0
    GRAY = 1
    WHITE = 2
    YELLOW = 3
    GREEN = 4
    PINK = 5
    BLUE = 6
    RED = 7
    GRAY_OVERLAP = 8
    HIGHLIGHT = 9  # Shared ID; actual color from extra block data or extraMetadata
    GREEN_2 = 10
    CYAN = 11
    MAGENTA = 12
    YELLOW_2 = 13
```

Color index stored per stroke in `.rm` binary files, spanning IDs 0 through 13.

`src/remarkable_spec/models/color.py:17`

### HighlightColor

```py
class HighlightColor(enum.Enum):
    YELLOW = "HighlighterYellow"
    GREEN = "HighlighterGreen"
    PINK = "HighlighterPink"
    BLUE = "HighlighterBlue"
    ORANGE = "HighlighterOrange"
```

The five highlight colors that all share `PenColor.HIGHLIGHT` (ID 9), distinguished by string keys
matching reMarkable's internal config.

`src/remarkable_spec/models/color.py:44`

### RGB

```py
class RGB(BaseModel):
    model_config = ConfigDict(frozen=True)
```

Frozen 8-bit RGB color value used for color palette definitions, with `r`, `g`, and `b` channels in
the 0–255 range.

`src/remarkable_spec/models/color.py:59`

### RM_PALETTE

```py
RM_PALETTE: dict[PenColor, RGB] = {
```

The standard export palette — the RGB values used when rendering to PNG, SVG, or PDF, covering 13 of
the 14 `PenColor` members.

`src/remarkable_spec/models/color.py:89`

### PAPER_PRO_PHYSICAL

```py
PAPER_PRO_PHYSICAL: dict[PenColor, RGB] = {
```

Physical on-screen colors for the Paper Pro's color e-ink panel, measured by DSLR calibration and
much more muted than the export palette.

`src/remarkable_spec/models/color.py:109`

### PenType

```py
class PenType(enum.IntEnum):
    PAINTBRUSH_1 = 0
    PENCIL_1 = 1
    BALLPOINT_1 = 2
    MARKER_1 = 3
    FINELINER_1 = 4
    HIGHLIGHTER_1 = 5
    ERASER = 6
    MECHANICAL_PENCIL_1 = 7
    ERASER_AREA = 8
    PAINTBRUSH_2 = 12
    MECHANICAL_PENCIL_2 = 13
    PENCIL_2 = 14
    BALLPOINT_2 = 15
    MARKER_2 = 16
    FINELINER_2 = 17
    HIGHLIGHTER_2 = 18
    CALLIGRAPHY = 21
    SHADER = 23
```

Pen tool ID stored per stroke in `.rm` binary files, where the `_2` variants render identically to
their `_1` counterparts and exist only because the reMarkable UI has two toolbar rows.

`src/remarkable_spec/models/pen.py:17`

### Pen

```py
class Pen(BaseModel):
    model_config = ConfigDict(frozen=True)
```

Frozen pen configuration carrying the base width, opacity, line cap, segment length, and which
stylus input channels (pressure, tilt, speed) affect rendered output.

`src/remarkable_spec/models/pen.py:78`

### Point

```py
class Point(BaseModel):
    model_config = ConfigDict(frozen=True)
```

A single frozen stylus sample — position plus raw speed, direction, width, and pressure sensor
values — occupying 14 bytes in the v6 binary format.

`src/remarkable_spec/models/stroke.py:24`

### Stroke

```py
class Stroke(BaseModel):
```

One continuous pen-down-to-pen-up movement: the tool used, its color, the UI thickness scale, and the
ordered sequence of sampled points.

`src/remarkable_spec/models/stroke.py:84`

### TextBlock

```py
class TextBlock(BaseModel):
```

A block of typed text on a page, holding the position and width of the text box plus the plain-text
rendering of the underlying CRDT sequence.

`src/remarkable_spec/models/page.py:18`

### Layer

```py
class Layer(BaseModel):
```

A single drawing layer within a page, containing strokes and optional text blocks, rendered
bottom-to-top in list order and skipped entirely when not visible.

`src/remarkable_spec/models/page.py:45`

### Page

```py
class Page(BaseModel):
```

A single page in a notebook or annotated document, identified by UUID and holding one or more layers
plus an optional background template name.

`src/remarkable_spec/models/page.py:99`

### Document

```py
class Document(BaseModel):
```

The top-level container that unifies a document's separate on-disk files — `.metadata`, `.content`,
the per-page `.rm` files, and `.pagedata` — into one object.

`src/remarkable_spec/models/document.py:307`

### DocumentMetadata

```py
class DocumentMetadata(BaseModel):
```

Parsed contents of a `{UUID}.metadata` JSON file: display name, folder membership, pin state,
deletion status, and sync timestamps.

`src/remarkable_spec/models/document.py:52`

### ContentInfo

```py
class ContentInfo(BaseModel):
```

Parsed contents of a `{UUID}.content` JSON file, carrying page structure, file type, tool settings,
and the layout parameters used for reflowable documents.

`src/remarkable_spec/models/document.py:190`

### ExtraMetadata

```py
class ExtraMetadata(BaseModel):
```

Per-tool last-used settings read from the `.content` file's `extraMetadata` field, so the UI can
restore the user's previous tool configuration.

`src/remarkable_spec/models/document.py:133`

### DocumentType

```py
class DocumentType(enum.Enum):
    DOCUMENT = "DocumentType"
    COLLECTION = "CollectionType"
```

The document-versus-folder distinction recorded in `.metadata` files, which is how hierarchy is
encoded in an otherwise flat directory.

`src/remarkable_spec/models/document.py:27`

### FileType

```py
class FileType(enum.Enum):
    NOTEBOOK = "notebook"
    PDF = "pdf"
    EPUB = "epub"
```

The underlying file type of a document, distinguishing on-device notebooks from uploaded PDF and
EPUB sources.

`src/remarkable_spec/models/document.py:39`

### PageRef

```py
class PageRef(BaseModel):
```

A reference to a page from within a document's `.content` file, carrying the page UUID, its template
name, and an optional redirect for pages that were moved or merged.

`src/remarkable_spec/models/document.py:166`

### Template

```py
class Template(BaseModel):
```

A methods-style template (firmware 3.x and later) that defines a page background from geometric
primitives and named constants rather than a raster image.

`src/remarkable_spec/models/template.py:79`

### TemplateItem

```py
class TemplateItem(BaseModel):
```

One geometric item inside a methods template — line, rect, circle, text — with a type-specific
properties dict whose values may reference template constants.

`src/remarkable_spec/models/template.py:57`

### BuiltinTemplate

```py
class BuiltinTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)
```

A frozen record for one legacy built-in template (pre-3.x firmware), shipped with the firmware and
reset on every OS upgrade.

`src/remarkable_spec/models/template.py:20`

### BUILTIN_TEMPLATES

```py
BUILTIN_TEMPLATES = [
```

The nine most frequently encountered legacy built-in templates, as they appear in `.pagedata` files.

`src/remarkable_spec/models/template.py:140`

### ScreenSpec

```py
class ScreenSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
```

Frozen physical screen specification — pixel dimensions, DPI, and device name — needed to convert
`.rm` screen units into PDF points or inches.

`src/remarkable_spec/models/screen.py:14`

### RM2_SCREEN

```py
RM2_SCREEN = ScreenSpec(width=1404, height=1872, dpi=226, name="reMarkable 2")
```

The reMarkable 2 screen specification, 1404x1872 at 226 DPI.

`src/remarkable_spec/models/screen.py:80`

### PAPER_PRO_SCREEN

```py
PAPER_PRO_SCREEN = ScreenSpec(width=1620, height=2160, dpi=229, name="Paper Pro")
```

The reMarkable Paper Pro screen specification in portrait orientation, 1620x2160 at 229 DPI.

`src/remarkable_spec/models/screen.py:83`

## Tier 2 — exported from a subpackage only

Import as `from remarkable_spec.<subpackage> import <name>`; these names are not reachable from the
package root. The four below are the highest-consumer tier-2 symbols by inbound-reference count, with
CLI-barrel names excluded because `docs/reference/cli.md` owns that surface.

### parse_rm_file

```py
def parse_rm_file(path: Path) -> list[Layer]:
```

Parses a v6 `.rm` binary file into a list of `Layer` objects, delegating to `rmscene.read_tree` and
raising `FileNotFoundError` or `rmscene.UnexpectedBlockError` on bad input.

`src/remarkable_spec/formats/rm_file.py:46`

### parse_content

```py
def parse_content(path: Path) -> ContentInfo:
```

Parses a `{UUID}.content` JSON file from disk into a `ContentInfo`, handling both the legacy `pages`
array and the newer `cPages` CRDT format.

`src/remarkable_spec/formats/content.py:40`

### parse_metadata

```py
def parse_metadata(path: Path) -> DocumentMetadata:
```

Parses a `{UUID}.metadata` JSON file from disk into a `DocumentMetadata`, translating reMarkable's
camelCase field names and string-encoded timestamps.

`src/remarkable_spec/formats/metadata.py:36`

### export_svg

```py
def export_svg(
    page: Page,
    output: Path,
    palette: Palette | None = None,
    screen: ScreenSpec | None = None,
    template_svg: Path | None = None,
    thickness: float = 1.5,
    background_image_b64: str | None = None,
    background_page_size: tuple[float, float] | None = None,
) -> None:
```

Exports one page to a self-contained SVG file with no external dependencies, defaulting to the export
palette, the reMarkable 2 screen, and a 1.5x stroke-width multiplier that matches on-device visual
weight.

`src/remarkable_spec/export/svg.py:18`

## Public names not documented here

Thirty-five further names are declared public by a subpackage `__all__` but fall below this page's
30-symbol cap. They are real exports, not internal helpers, and each subpackage barrel is the
authoritative list:

- `formats` — `parse_rm_bytes`, `parse_metadata_json`, `parse_content_json`, `parse_pagedata`,
  `load_document` (`src/remarkable_spec/formats/__init__.py:21-35`). `load_document` has no consumer
  inside `src/`; it exists for external importers.
- `render` — `RenderEngine`, `SVGRenderer`, `SCREEN_WIDTH`, `SCREEN_HEIGHT`, `SCREEN_DPI`, `SCALE`,
  `Palette`, `EXPORT_PALETTE`, `PHYSICAL_PALETTE`, `PenRenderer`, `BasePenRenderer`,
  `get_pen_renderer`, `direction_to_tilt` (`src/remarkable_spec/render/__init__.py:37-54`).
- `sync` — `SyncDB`, `SyncDocument`, `SyncPage`, `SyncLogEntry`, `OCRCacheEntry`,
  `DiagramCacheEntry` (`src/remarkable_spec/sync/__init__.py:22-29`).
- `device` — `DeviceConnection`, `DevicePaths`, `SyncManager`, `WebAPI`
  (`src/remarkable_spec/device/__init__.py:20-25`). All four require the `device` extra.
- `export` — `export_png`, `export_pdf` (`src/remarkable_spec/export/__init__.py:20-24`); both
  require the `render` extra.
- `ocr` — `ocr_image`, `ocr_page` (`src/remarkable_spec/ocr/__init__.py:7`); both bind the macOS-only
  Apple Vision framework.
- `cli` — `app`, `get_xochitl_dir`, `settings` (`src/remarkable_spec/cli/__init__.py:38`). Covered by
  `docs/reference/cli.md`.

There is no HTTP route surface to document. A grep over `src/` for route decorators and server
constructors returns nothing; the only HTTP in the codebase is the client `WebAPI`
(`src/remarkable_spec/device/web_api.py:37`), which calls the tablet's own USB web interface through
`httpx` (`src/remarkable_spec/device/web_api.py:65`) and publishes no endpoints of its own.

## See also

- [module map](../architecture/module-map.md) — 18 shared source citations
- [contract map](../insights/contract-map.md) — 18 shared source citations
- [impact analysis](../insights/impact-analysis.md) — 16 shared source citations
- [dead code](../analysis/dead-code.md) — 15 shared source citations
- [business logic](../insights/business-logic.md) — 11 shared source citations
