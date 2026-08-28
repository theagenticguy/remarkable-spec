# remarkable-spec · Contract map

This file answers one question: when module A hands something to module B, what does B assume about
it?

**"Contract" here means** any type, schema, function signature, module-level singleton, or external
wire format that is declared in one file and depended on by at least one other file, where the
depender's correctness rests on properties the declaration does not state. That is broader than "a
shared type", deliberately: two of the highest-consequence boundaries in this codebase are not
types at all — the SQLite schema lives in SQL string literals, and importing `cli/_util.py` mutates
the process environment.

Two facts frame everything below.

**Nothing enforces any of it.** There is no import-linter configuration, no dependency test, no
lint rule constraining module direction, and no schema generator. Ruff's selected rules are
`E, F, I, N, UP, B, SIM, RUF` (`pyproject.toml:63-68`) — style and correctness lints, not
architectural ones. The layering below is what the code currently does, not what the build permits.

**The measured import direction** (import-statement counts, no cycles):

```text
models   leaf, zero outbound internal edges
sync     leaf, zero outbound internal edges
formats  -> models (8)
render   -> models (6)
export   -> models (6), render (4)
device   -> sync (4)
ocr      -> models (4), export (2), formats (2)
cli      -> models (15), formats (13), device (11), ocr (8), sync (5), render (5), export (3)
```

Consumer counts used for ranking come from an AST walk over every `ImportFrom` in the 56 source
files, cross-checked against the CodeGraph index. Counts below are distinct consuming *files*
outside the producer's own package.

---

## Contract 1 — `Page`: the render payload

Nine files outside `models` depend on this shape. It is the widest contract in the codebase.

**Producer:** `src/remarkable_spec/models/page.py:99`

**Consumer(s):**

- `src/remarkable_spec/render/engine.py:21` — imported, then consumed as the first parameter of
  `render_page` at `:91` and walked at `:145` and `:207`
- `src/remarkable_spec/export/svg.py:12` — first parameter of `export_svg` at `:19`
- `src/remarkable_spec/export/png.py:14` — first parameter of `export_png` at `:20`
- `src/remarkable_spec/export/pdf.py:14` — `export_pdf` takes `pages: list[Page]` at `:20`
- `src/remarkable_spec/formats/document_loader.py:27` — constructs instances at `:104-108`
- `src/remarkable_spec/ocr/pipeline.py:48` — constructs at `:59`
- `src/remarkable_spec/ocr/vision.py:160` — constructs at `:165`
- `src/remarkable_spec/cli/render_cmd.py:51` — plus lazy re-imports at `:151`, `:236`, `:273`,
  `:305`
- `src/remarkable_spec/__init__.py:22` — re-exported on the public library surface

**Shape:** verbatim from `src/remarkable_spec/models/page.py:110-146`

```python
    uuid: UUID = Field(
        description="Unique identifier for this page. Used as the filename stem "
        "for the .rm file, metadata JSON, and thumbnail JPEG.",
    )
    layers: list[Layer] = Field(
        default_factory=list,
        description="Ordered list of drawing layers. Rendered bottom-to-top.",
    )
    template_name: str = Field(
        default="",
        description="Name of the background template (e.g. 'Blank', 'Lined', 'Grid_small'). "
        "Empty string means no template / blank background.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rm_filename(self) -> str:
        """The .rm filename for this page, e.g. '{uuid}.rm'."""
        return f"{self.uuid}.rm"
```

**Assumptions consumers make:**

- **`uuid` is required, so consumers that have no real UUID invent one.**
  `src/remarkable_spec/ocr/pipeline.py:59` and `src/remarkable_spec/ocr/vision.py:165` both write
  `Page(uuid=uuid4(), layers=layers)`, and `src/remarkable_spec/cli/render_cmd.py:274-275` does the
  same for unannotated PDF pages. The resulting `uuid` matches no file on disk, so `rm_filename`
  (`src/remarkable_spec/models/page.py:126-128`), `metadata_filename` (`:132-134`), and
  `thumbnail_filename` (`:138-140`) are all fabricated for those pages. Nothing marks the value as
  synthetic.
- **The render path ignores `template_name`.** `SVGRenderer.render_page` takes a separate
  `template_svg: Path | None` argument (`src/remarkable_spec/render/engine.py:97`, threaded from
  `src/remarkable_spec/export/svg.py:23`) and never reads `page.template_name`. So the template
  name that `src/remarkable_spec/formats/document_loader.py:107` carefully resolves from
  `.pagedata` has no effect on rendering.
- **"Visible layers only" is implemented twice.** `Page.all_strokes` filters on `layer.visible`
  (`src/remarkable_spec/models/page.py:146`), but the renderer re-walks `page.layers` and applies
  its own `if not layer.visible: continue` at `src/remarkable_spec/render/engine.py:146` and again
  at `:208`. A change to the visibility rule has to be made in three places.
- **Strokes with fewer than two points render as nothing.**
  `src/remarkable_spec/render/engine.py:258-259` returns early on `len(points) < 2`, while
  `src/remarkable_spec/models/stroke.py:110-112` documents empty strokes as valid and names
  single-tap dots as the example. A tap therefore round-trips through the parser and vanishes at
  export.
- **`export_pdf` assumes `background_images_b64` is index-aligned with `pages`.** It indexes
  positionally at `src/remarkable_spec/export/pdf.py:113` with no length check, though the
  docstring at `:41-43` states the requirement.

**Drift risk:** adding a field to `Page` without a default breaks
`src/remarkable_spec/formats/document_loader.py:104`, `src/remarkable_spec/ocr/pipeline.py:59`,
`src/remarkable_spec/ocr/vision.py:165`, and `src/remarkable_spec/cli/render_cmd.py:274` at once,
at runtime, with no static signal. Give every new field a default, or add a `Page.synthetic(...)`
classmethod that the three synthesizing call sites share.

---

## Contract 2 — `parse_rm_file` returns a bare `list[Layer]`

The load-bearing parse boundary. Seven files outside `formats` call it.

**Producer:** `src/remarkable_spec/formats/rm_file.py:46`

**Consumer(s):**

- `src/remarkable_spec/cli/annotations_cmd.py:222`
- `src/remarkable_spec/cli/diagram_cmd.py:362`
- `src/remarkable_spec/cli/inspect_cmd.py:105`
- `src/remarkable_spec/cli/ocr_cmd.py:190`
- `src/remarkable_spec/cli/render_cmd.py:150` and `:304`
- `src/remarkable_spec/ocr/pipeline.py:47` — called at `:58`
- `src/remarkable_spec/ocr/vision.py:159` — called at `:164`
- `src/remarkable_spec/formats/document_loader.py:25` — called at `:94`, same module
- `src/remarkable_spec/formats/__init__.py:19` — re-exported

**Shape:** verbatim from `src/remarkable_spec/formats/rm_file.py:46-67`

```python
def parse_rm_file(path: Path) -> list[Layer]:
    """Parse a v6 ``.rm`` binary file into a list of :class:`Layer` objects.

    Parameters
    ----------
    path:
        Filesystem path to a ``.rm`` file.

    Returns
    -------
    list[Layer]
        Ordered list of layers, each containing strokes and text blocks.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    rmscene.UnexpectedBlockError
        If the file is not a valid v6 ``.rm`` file.
    """
    data = path.read_bytes()
    return parse_rm_bytes(data)
```

**Assumptions consumers make:**

- **The return value is not a `Page`, so every consumer wraps it.**
  `src/remarkable_spec/ocr/pipeline.py:59` and `src/remarkable_spec/ocr/vision.py:165` each pair
  the layer list with a fresh `uuid4()`. The parser deliberately does not know the page UUID; the
  filename stem is the only source, and only `src/remarkable_spec/cli/render_cmd.py:269-270`
  recovers it via `UUID(rm_path.stem)`.
- **The declared `Raises` block is incomplete in the direction that matters.** An unknown pen ID
  becomes `PenType.FINELINER_1` with a `logger.warning` at
  `src/remarkable_spec/formats/rm_file.py:160-163`, and an unknown color becomes `PenColor.BLACK`
  at `:167-170`. Both are silent successes to any caller that only guards against exceptions — and
  the warnings themselves land on a logger whose sibling `rmscene` logger was raised to `ERROR` at
  import time (`:31`).
- **A parse failure yields an empty page, not an error, in the document path.**
  `src/remarkable_spec/formats/document_loader.py:92-101` wraps the call in `except Exception` and
  leaves `layers = []`, so `len(Document.pages)` always equals `len(content.page_refs)` even when
  the strokes never loaded. A consumer counting pages cannot distinguish "blank page" from
  "unparseable page".
- **`detect_screen` consumes the same list positionally and untyped.** Its parameter is annotated
  `layers: list` with no element type (`src/remarkable_spec/models/screen.py:86`) and it
  triple-nests into `layer.strokes` then `stroke.points` at `:98-100`. An empty list returns
  `RM2_SCREEN` (`:104`).
- **Layer order is the render order.** `_convert_tree` appends in `root.children.values()` order
  (`src/remarkable_spec/formats/rm_file.py:98-100`), and `src/remarkable_spec/models/page.py:116`
  plus `:53` both promise bottom-to-top list order. Nothing sorts.

**Drift risk:** the fallback at `src/remarkable_spec/formats/rm_file.py:105-109` — when no
top-level `Group` children are found, build one synthetic `Layer(name="Layer 1")` from
`tree.walk()` — silently collapses a multi-layer page into one layer if rmscene changes how the
root group is exposed. Mitigation: log at `warning` rather than proceeding silently, so the
collapse is visible in the same place the unknown-pen warnings already appear.

---

## Contract 3 — `rmscene` scene items crossing into local models

The one external contract that is version-bounded on both sides, and the only place a dependency
upgrade can change parsed output without changing a line of this codebase.

**Producer:** the third-party library, imported at `src/remarkable_spec/formats/rm_file.py:21-22`
(`from rmscene import read_tree`, `from rmscene import scene_items as si`), pinned
`rmscene>=0.7.0,<0.8.0` at `pyproject.toml:13`.

**Consumer(s):** all inside the single conversion layer, which is the whole point —

- `src/remarkable_spec/formats/rm_file.py:92` — `_convert_tree`, reads `tree.root` at `:94` and
  `tree.walk()` at `:106`
- `src/remarkable_spec/formats/rm_file.py:114` — `_convert_group`, reads `group.label.value` and
  `group.visible.value` at `:116-117` and resolves nested nodes via `tree[child.node_id]` at `:126`
- `src/remarkable_spec/formats/rm_file.py:139` — `_collect_item`, dispatches on `si.Line`,
  `si.Text`, `si.Group`, `si.GlyphRange`
- `src/remarkable_spec/formats/rm_file.py:156` — `_convert_line`, reads `line.tool`, `line.color`,
  `line.points`, `line.thickness_scale`, `line.starting_length`
- `src/remarkable_spec/formats/rm_file.py:183` — `_convert_point`, reads `point.x`, `.y`, `.speed`,
  `.direction`, `.width`, `.pressure`
- `src/remarkable_spec/formats/rm_file.py:195` — `_convert_text`, reads `text.items`, `text.pos_x`,
  `text.pos_y`, `text.width`

**Shape:** the mapping is stated verbatim in the module docstring at
`src/remarkable_spec/formats/rm_file.py:7-12`

```text
Mapping
-------
rmscene ``Group``  -> ``Layer`` (each top-level group child of the root is a layer)
rmscene ``Line``   -> ``Stroke`` (pen type, color, thickness, points)
rmscene ``Point``  -> ``Point`` (x, y, speed, direction, width, pressure)
rmscene ``Text``   -> ``TextBlock`` (pos, width, extracted plain text)
```

**Assumptions consumers make:**

- **Four scene-item types are handled; everything else is dropped.** `_collect_item` ends in an
  `else` that only calls `logger.debug` (`src/remarkable_spec/formats/rm_file.py:152-153`), and
  `si.GlyphRange` is explicitly matched and discarded at `:149-151`. A new scene-item type in a
  future rmscene release is silently lost at `debug` level.
- **`line.tool` and `line.color` are `int`-convertible.** `int(line.tool)` at
  `src/remarkable_spec/formats/rm_file.py:160` and `int(line.color)` at `:167` assume the library's
  enums stay integer-valued. If either became a string enum, every stroke would take the
  `ValueError` branch and render as a black fineliner.
- **`group.label` and `group.visible` are optional wrapper objects with a `.value`.** Both are read
  through a truthiness guard (`src/remarkable_spec/formats/rm_file.py:116-117`), which treats a
  present-but-falsy wrapper the same as an absent one — a layer explicitly named `""` and a layer
  with no label are indistinguishable.
- **`text.items` is a mapping whose values are either `str` or integer format codes.**
  `src/remarkable_spec/formats/rm_file.py:202-205` (`_convert_text`) keeps only
  `isinstance(value, str)` and drops the rest, so paragraph formatting is discarded and the
  concatenation order is `dict` insertion order.
- **`nested_child` resolution has a silent second path.**
  `src/remarkable_spec/formats/rm_file.py:125-132` (`_convert_group`) tries `tree[child.node_id]`
  and, on `KeyError` or `AttributeError`, falls back to the unresolved group's own `children`. The
  two paths can yield different stroke sets and nothing records which ran.
- **The library's own warnings are suppressed process-wide.**
  `src/remarkable_spec/formats/rm_file.py:31` sets
  `logging.getLogger("rmscene").setLevel(logging.ERROR)` at import time, with the stated reason at
  `:29-30`. Any process that imports this module loses rmscene's "some data has not been read"
  signal, which is exactly the signal that a format change would produce.

**Drift risk:** the `<0.8.0` upper bound is the maintainer's own statement that 0.8 will break
something, and it will break inside the six functions above rather than at the import. Mitigation:
before relaxing the bound, diff `rmscene.scene_items` against the attribute list in the Consumers
bullets — that list is the complete surface this codebase touches.

---

## Contract 4 — `Stroke` and `Point`: the per-sample stylus record

**Producer:** `src/remarkable_spec/models/stroke.py:24` (`Point`) and `:84` (`Stroke`)

**Consumer(s):**

- `src/remarkable_spec/formats/rm_file.py:27` — constructs `Stroke` at `:174-180` and `Point` at
  `:185-192`
- `src/remarkable_spec/render/engine.py:24` — `_render_stroke` takes `stroke: Stroke` at `:234` and
  reads point fields at `:275-296`
- `src/remarkable_spec/models/page.py:15` — `Layer.strokes: list[Stroke]` at `:64`
- `src/remarkable_spec/__init__.py:25` — re-exported publicly

**Shape:** verbatim from `src/remarkable_spec/models/stroke.py:36-59` and `:96-117`

```python
    model_config = ConfigDict(frozen=True)

    x: float = Field(description="Horizontal position in screen units (0 = left edge).")
    y: float = Field(description="Vertical position in screen units (0 = top edge).")
    speed: int = Field(
        default=0,
        description="Stylus movement speed as a raw uint16 sensor value. "
        "Higher values indicate faster pen movement.",
    )
    direction: int = Field(
        default=0,
        description="Stylus angle as uint8 (0-255 maps to 0-360 degrees). "
        "Used by tilt-sensitive pens like marker and pencil.",
    )
    width: int = Field(
        default=0,
        description="Raw input width as uint16. Varies by pen type and is "
        "combined with pressure in rendering formulas.",
    )
    pressure: int = Field(
        default=0,
        description="Pen pressure as uint8 (0-255). 0 = no pressure, 255 = maximum. "
        "Used by pressure-sensitive pens like ballpoint and pencil.",
    )
```

```python
    pen_type: PenType = Field(
        description="The pen tool used for this stroke. Determines rendering "
        "behavior (line width formula, opacity, sensitivity to pressure/tilt/speed).",
    )
    color: PenColor = Field(
        description="The color index for this stroke. On monochrome devices "
        "(rM1/rM2) only BLACK, GRAY, WHITE are available. Paper Pro adds colors.",
    )
    thickness_scale: float = Field(
        description="Raw thickness scale from the UI thickness slider. This value "
        "is transformed by pen-specific formulas into the actual rendered width.",
    )
    points: list[Point] = Field(
        default_factory=list,
        description="Ordered sequence of stylus sample points from pen-down to "
        "pen-up. Empty strokes are valid (e.g. single-tap dots).",
    )
    starting_length: float = Field(
        default=0.0,
        description="Cumulative length offset for multi-segment rendering. "
        "Used when a stroke is split across multiple rendering passes.",
    )
```

**Assumptions consumers make:**

- **Only the trailing point of each segment influences rendering.**
  `src/remarkable_spec/render/engine.py:275-296` passes `p2.speed`, `p2.direction`, `p2.width`,
  `p2.pressure` to all three renderer methods and never reads `p1`'s sensor values. The first
  sample of every stroke contributes its coordinates at `:303` and nothing else.
- **`Point.direction_radians` is computed but the renderers recompute it.**
  `src/remarkable_spec/models/stroke.py:71` returns `self.direction * (math.pi * 2) / 255`, and
  `src/remarkable_spec/render/pens.py:38` returns `direction * (math.pi * 2) / 255` from a
  standalone `direction_to_tilt`. The four renderers that need tilt call the
  `src/remarkable_spec/render/pens.py` version (`:214`, `:240`, `:310`, `:330`), so the model's
  computed field and its `tilt` alias at
  `src/remarkable_spec/models/stroke.py:81` have no reader in `src/`.
- **`x` is not what its own description says.** `src/remarkable_spec/models/stroke.py:38` documents
  `"0 = left edge"`, but `src/remarkable_spec/render/engine.py:132-134` states and compensates for
  the v6 origin being the page centre, and `src/remarkable_spec/models/screen.py:101-102` repeats
  the correction. So `x` ranges roughly `[-width/2, +width/2]`, and the field description is the
  odd one out.
- **`Point` is frozen, `Stroke` is not.** `src/remarkable_spec/models/stroke.py:36` sets
  `frozen=True` on `Point`; the `Stroke` model at `:84` has no `model_config`, and
  `src/remarkable_spec/formats/rm_file.py:142` and `:144` mutate `layer.strokes` and
  `layer.text_blocks` in place after construction.
- **A stroke with no points still contributes to bounding boxes.** `Stroke.bounding_box` returns
  `(0.0, 0.0, 0.0, 0.0)` for an empty point list (`src/remarkable_spec/models/stroke.py:138-139`),
  and `Layer.bounding_box` folds that into a `min`/`max` over all strokes
  (`src/remarkable_spec/models/page.py:90-96`). Because `x` is centre-origin and therefore often
  negative, one point-less stroke silently stretches the layer box to include the origin.
- **`starting_length` has no reader.** It is parsed from rmscene at
  `src/remarkable_spec/formats/rm_file.py:179` and never consulted by the renderer, whose segment
  loop starts from `pen.base_width` (`src/remarkable_spec/render/engine.py:269`).

**Drift risk:** the renderer reaches four `Point` fields by keyword
(`src/remarkable_spec/render/engine.py:276-279`), so renaming any one of them is a `TypeError` at
the first segment rather than a static error. Mitigation: keep the keyword names stable, or have
`Point` expose a single `sensor_kwargs` mapping the renderers splat.

---

## Contract 5 — `ScreenSpec`, its two constants, and `detect_screen`

Six files outside `models` depend on this, and it is the contract most often satisfied by accident.

**Producer:** `src/remarkable_spec/models/screen.py:14` (`ScreenSpec`), `:80` (`RM2_SCREEN`), `:83`
(`PAPER_PRO_SCREEN`), `:86` (`detect_screen`)

**Consumer(s):**

- `src/remarkable_spec/render/engine.py:23` — defaults to `RM2_SCREEN` at `:125-126`, derives
  `scale`, `vw`, `vh` at `:128-130`
- `src/remarkable_spec/export/svg.py:13` — defaults at `:63`
- `src/remarkable_spec/export/png.py:15` — defaults at `:64-65`, derives `scale_factor` at `:86`
- `src/remarkable_spec/export/pdf.py:15` — defaults at `:79-80`, derives page size at `:131-133`
- `src/remarkable_spec/ocr/pipeline.py:49` — calls `detect_screen(layers)` at `:60` and sizes the
  raster at `:78-79`
- `src/remarkable_spec/ocr/vision.py:161` — hardcodes `RM2_SCREEN` at `:172`, `:186-187`
- `src/remarkable_spec/cli/render_cmd.py:47` — defaults to `PAPER_PRO_SCREEN` at `:353-354`
- `src/remarkable_spec/__init__.py:24` — re-exported publicly

**Shape:** verbatim from `src/remarkable_spec/models/screen.py:26-52` and `:79-83`

```python
    model_config = ConfigDict(frozen=True)

    width: int = Field(
        description="Screen width in pixels (portrait orientation). "
        "1404 for rM1/rM2, 1620 for Paper Pro.",
    )
    height: int = Field(
        description="Screen height in pixels (portrait orientation). "
        "1872 for rM1/rM2, 2160 for Paper Pro.",
    )
    dpi: int = Field(
        description="Display dots per inch. 226 for rM1/rM2, 229 for Paper Pro. "
        "Used to convert between screen units and physical measurements.",
    )
    name: str = Field(
        description="Human-readable device name, e.g. 'reMarkable 2' or 'Paper Pro'.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def points_per_pixel(self) -> float:
        """Convert screen units to PDF points (1/72 inch).

        This ratio is used when rendering .rm content into PDF pages
        to ensure strokes appear at the correct physical size.
        """
        return 72.0 / self.dpi
```

```python
# reMarkable 2 (and reMarkable 1) -- 1404x1872 @ 226 DPI
RM2_SCREEN = ScreenSpec(width=1404, height=1872, dpi=226, name="reMarkable 2")

# reMarkable Paper Pro (portrait orientation) -- 1620x2160 @ 229 DPI
PAPER_PRO_SCREEN = ScreenSpec(width=1620, height=2160, dpi=229, name="Paper Pro")
```

**Assumptions consumers make:**

- **Omitting `screen` means rM2 in the library and Paper Pro in the CLI.** The library defaults are
  `RM2_SCREEN` at `src/remarkable_spec/render/engine.py:125-126`,
  `src/remarkable_spec/export/svg.py:63`, `src/remarkable_spec/export/png.py:64-65`, and
  `src/remarkable_spec/export/pdf.py:79-80`; the CLI's `_export_page` defaults to
  `PAPER_PRO_SCREEN` at `src/remarkable_spec/cli/render_cmd.py:353-354`. A library caller and a CLI
  caller rendering the same page get different viewBox dimensions and a different points-per-pixel
  scale.
- **The two OCR entry points disagree about screen detection.**
  `src/remarkable_spec/ocr/pipeline.py:60` calls `detect_screen(layers)`;
  `src/remarkable_spec/ocr/vision.py:172` passes `screen=RM2_SCREEN` unconditionally and sizes the
  PNG from `RM2_SCREEN` at `:186-187`. So `ocr_page` rasterizes a Paper Pro page at rM2 dimensions
  while `render_rm_to_png` does not.
- **`detect_screen` uses centre-origin X but top-origin Y.**
  `src/remarkable_spec/models/screen.py:102` tests
  `abs(pt.x) > RM2_SCREEN.width / 2 or pt.y > RM2_SCREEN.height` — the X test is halved for the v6
  centre origin, the Y test is not. The asymmetry is correct for the format, and the docstring at
  `:101` says so, but it means the two axes have different detection sensitivity.
- **Detection is one-directional and content-dependent.** A genuine Paper Pro page whose strokes
  all fall inside rM2 bounds returns `RM2_SCREEN` (`src/remarkable_spec/models/screen.py:104`), so
  the reported device depends on how far toward the margins the user wrote.
- **`ScreenSpec` is frozen, so DPI cannot be patched at the call site.**
  `src/remarkable_spec/models/screen.py:26` sets `frozen=True`, and the CLI carries an independent
  DPI default of 226 in `src/remarkable_spec/cli/_util.py:52-55` rather than deriving it from a
  `ScreenSpec`.
- **A second hardcoded copy of the rM2 numbers exists.**
  `src/remarkable_spec/render/engine.py:29-32` defines `SCREEN_WIDTH = 1404`,
  `SCREEN_HEIGHT = 1872`, `SCREEN_DPI = 226`, `SCALE = 72.0 / SCREEN_DPI`, and
  `src/remarkable_spec/render/__init__.py:18-21` re-exports all four. No other file in `src/` reads
  them, so they are a public surface that duplicates `RM2_SCREEN` without being wired to it.

**Drift risk:** adding a third device constant does not extend `detect_screen`, whose two-way
`if`/fallback at `src/remarkable_spec/models/screen.py:98-104` can only ever return one of two
values. Mitigation: turn the detector into an ordered list of `(bounds, spec)` pairs so a new
device is one entry rather than a new branch.

---

## Contract 6 — `PenType` and `PenColor`: the two stroke enums the renderer resolves

Both enums live in `models`, are written by the parser, and are turned into pixels by `render`.
They are grouped here because they share one failure mode: an unrecognised member falls back to a
default instead of raising.

**Producer:** `src/remarkable_spec/models/pen.py:17` (`PenType`) and
`src/remarkable_spec/models/color.py:17` (`PenColor`), with the palette dicts `RM_PALETTE` at
`src/remarkable_spec/models/color.py:89` and `PAPER_PRO_PHYSICAL` at `:109`

**Consumer(s):** of `PenType` —

- `src/remarkable_spec/render/pens.py:23` — `get_pen_renderer` matches on it at `:456-480`
- `src/remarkable_spec/models/pen.py:132` — `Pen.from_stroke` matches on it at `:146-226`, same
  file, different function
- `src/remarkable_spec/formats/rm_file.py:26` — maps rmscene's tool ID into it at `:160-163`
- `src/remarkable_spec/models/stroke.py:21` — `Stroke.pen_type` at `:96`, and the
  `is_eraser`/`is_highlighter` computed fields at `:123` and `:129`
- `src/remarkable_spec/cli/inspect_cmd.py:40`
- `src/remarkable_spec/__init__.py:23` — re-exported publicly

and of `PenColor` —

- `src/remarkable_spec/render/palette.py:17` — wraps the dicts as `EXPORT_PALETTE` at `:92` and
  `PHYSICAL_PALETTE` at `:95`; `Palette.get_rgb` at `:41`, `get_hex` at `:62`, `get_css` at `:76`
- `src/remarkable_spec/render/engine.py:263` — `base_rgb = palette.get_rgb(stroke.color)`
- `src/remarkable_spec/formats/rm_file.py:24` — maps rmscene's color ID into it at `:167-170`
- `src/remarkable_spec/models/stroke.py:20` — `Stroke.color` at `:100`
- `src/remarkable_spec/cli/inspect_cmd.py:39`
- `src/remarkable_spec/__init__.py:6` — re-exported publicly

**Shape:** `PenType` members and the alias table, verbatim from
`src/remarkable_spec/models/pen.py:29-46` and `:58-75`

```python
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

```python
    @classmethod
    def canonical(cls, value: int) -> PenType:
        """Return the canonical (_1) variant for any pen type.

        Maps _2 toolbar-row variants back to their _1 equivalents so that
        rendering logic only needs to handle one variant per pen type.
        """
        _aliases: dict[int, PenType] = {
            cls.PAINTBRUSH_2: cls.PAINTBRUSH_1,
            cls.MECHANICAL_PENCIL_2: cls.MECHANICAL_PENCIL_1,
            cls.PENCIL_2: cls.PENCIL_1,
            cls.BALLPOINT_2: cls.BALLPOINT_1,
            cls.MARKER_2: cls.MARKER_1,
            cls.FINELINER_2: cls.FINELINER_1,
            cls.HIGHLIGHTER_2: cls.HIGHLIGHTER_1,
        }
        pen = cls(value)
        return _aliases.get(pen, pen)
```

`PenColor` members and the export palette, verbatim from
`src/remarkable_spec/models/color.py:28-41` and `:89-103`

```python
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

```python
RM_PALETTE: dict[PenColor, RGB] = {
    PenColor.BLACK: RGB(r=0, g=0, b=0),
    PenColor.GRAY: RGB(r=144, g=144, b=144),
    PenColor.WHITE: RGB(r=255, g=255, b=255),
    PenColor.YELLOW: RGB(r=251, g=247, b=25),
    PenColor.GREEN: RGB(r=0, g=255, b=0),
    PenColor.PINK: RGB(r=255, g=192, b=203),
    PenColor.BLUE: RGB(r=78, g=105, b=201),
    PenColor.RED: RGB(r=179, g=62, b=57),
    PenColor.GRAY_OVERLAP: RGB(r=125, g=125, b=125),
    PenColor.GREEN_2: RGB(r=161, g=216, b=125),
    PenColor.CYAN: RGB(r=139, g=208, b=229),
    PenColor.MAGENTA: RGB(r=183, g=130, b=205),
    PenColor.YELLOW_2: RGB(r=247, g=232, b=81),
}
```

**Assumptions consumers make:**

- **18 members collapse to 11 canonical values, and both dispatch tables end in a catch-all.**
  `Pen.from_stroke` closes with `case _: return cls(pen_type=pen_type, base_width=thickness_scale)`
  at `src/remarkable_spec/models/pen.py:225-226`, and `get_pen_renderer` closes with a comment
  saying "Fallback to fineliner for unknown pen types" at
  `src/remarkable_spec/render/pens.py:478-480`. A new pen ID therefore renders as a plain
  constant-width line with no error.
- **`canonical()` is called twice per stroke, independently.**
  `src/remarkable_spec/render/engine.py:261-262` passes the **raw** `stroke.pen_type` to both
  `Pen.from_stroke` and `get_pen_renderer`, each of which canonicalizes internally
  (`src/remarkable_spec/models/pen.py:146`, `src/remarkable_spec/render/pens.py:456`). The two must
  agree, and nothing ties them together.
- **`base_width` is computed in one function and consumed in another.**
  `src/remarkable_spec/render/engine.py:262` feeds `pen.base_width` into `get_pen_renderer`, but
  only four renderers store it — `src/remarkable_spec/render/pens.py:143` (`FinelineRenderer`),
  `:228` (`PencilRenderer`), `:265` (`MechanicalPencilRenderer`), and `:410` (`EraserRenderer`).
  `BallpointRenderer`, `MarkerRenderer`, `PaintbrushRenderer`, and `CalligraphyRenderer` take no
  constructor argument (`src/remarkable_spec/render/pens.py:461`, `:463`, `:469`, `:471`), so for
  those four the `thickness_scale` transforms in `Pen.from_stroke` are computed and discarded.
- **The mechanical pencil's squared width is discarded for the same reason it is computed.**
  `src/remarkable_spec/models/pen.py:180` sets `base_width=thickness_scale**2`, and
  `MechanicalPencilRenderer.segment_width` returns that stored value verbatim
  (`src/remarkable_spec/render/pens.py:268-277`) — so this is one of the four where it does
  survive. The contrast with `src/remarkable_spec/render/pens.py:167-176` (`BallpointRenderer`),
  which ignores `base_width` entirely in favour of its own formula, is the asymmetry a reader trips
  over.
- **`PenRenderer` is a `runtime_checkable` `Protocol` that nothing checks at runtime.**
  `src/remarkable_spec/render/pens.py:41-42` declares it; `get_pen_renderer` is annotated to return
  the ABC `BasePenRenderer` instead (`:437`), and no `isinstance` against the protocol appears in
  `src/`.
- **The parse path pre-normalizes, making the catch-alls near-unreachable from real files.**
  `src/remarkable_spec/formats/rm_file.py:159-163` already coerces any out-of-range rmscene tool to
  `FINELINER_1`, so the `case _` arms only fire for pen types that exist in the enum but were
  forgotten in a match.
- **A missing palette entry becomes black, silently, and one entry is missing.** `Palette.get_rgb`
  returns `(0, 0, 0)` when the color is absent (`src/remarkable_spec/render/palette.py:57-60`).
  `PenColor` has 14 members but the block quoted above has 13 keys — `HIGHLIGHT` (ID 9) is the one
  omitted. So every stroke carrying color ID 9 resolves to pure black at
  `src/remarkable_spec/render/engine.py:263`. Confirmed by executing
  `EXPORT_PALETTE.get_rgb(PenColor.HIGHLIGHT)`, which returns `(0, 0, 0)`.
  `src/remarkable_spec/models/color.py:37` explains why the ID is special — the real color lives in
  extra block data — but no consumer reads that extra data.
- **`PHYSICAL_PALETTE` covers 9 of 14 members.** `src/remarkable_spec/models/color.py:109-119`
  omits `PINK`, `GRAY_OVERLAP`, `HIGHLIGHT`, `GREEN_2`, and `YELLOW_2`, so a page rendered with
  `src/remarkable_spec/render/palette.py:95` shows those five as black. `get_hex` and `get_css`
  have the same fallback (`:74`, `:88`).
- **`EraserRenderer` discards the palette result.** `src/remarkable_spec/render/pens.py:424-434`
  returns `(255, 255, 255)` regardless of `base_color`. That is only correct against a white page,
  which `src/remarkable_spec/render/engine.py:182-187` does draw across the full padded viewBox —
  so the eraser contract depends on a background rect drawn 80 lines earlier.
- **`BallpointRenderer` assumes 0-255 integers come back.**
  `src/remarkable_spec/render/pens.py:188-193` multiplies each channel by a pressure-derived
  intensity and clamps with `max(0, min(255, int(...)))`. `RGB` declares `r`, `g`, `b` as plain
  `int` with no range constraint (`src/remarkable_spec/models/color.py:69-71`), so the clamp in the
  renderer is the only range enforcement in the chain.
- **`Palette` is a frozen dataclass, not a Pydantic model.**
  `src/remarkable_spec/render/palette.py:25-26` uses `@dataclass(frozen=True)` while its `colors`
  values are Pydantic `RGB` instances — the only place in the codebase where the two systems are
  mixed inside one container.

**Drift risk:** adding a member to `PenType` and forgetting one of the two `match` statements is
invisible — the stroke still renders, wrongly, as a fineliner. Mitigation: derive the renderer
table from a module-level `dict[PenType, ...]` so a missing key is a `KeyError` at first use rather
than a silent substitution, or add the new member to `_aliases` at
`src/remarkable_spec/models/pen.py:65-73` when it is a toolbar-row duplicate.

Separately for color: adding a `PenColor` member without adding it to `RM_PALETTE` produces black
strokes with no warning, which is exactly the state `HIGHLIGHT` is in today. Mitigation: have
`Palette.get_rgb` log at `warning` on a miss, or assert at import that
`set(RM_PALETTE) == set(PenColor)`.

---

## Contract 7 — `sync/models.py` Pydantic rows against the SQLite DDL

Two hand-maintained mirrors of one shape. Nothing generates either from the other. The columns
below are taken from the migration file, which is the authority on what exists on disk.

**Producer:** `src/remarkable_spec/sync/migrations.py:15` — the `_SCHEMA_SQL` string literal,
executed by `init_schema` at `:99-110`

**Consumer(s):**

- `src/remarkable_spec/sync/db.py:15` — imports all five models; writes at `:79-108`, `:134-151`,
  `:194-213`, `:255-273`, `:280-295`; reads at `:182-190`, `:223-231`, `:244-250`, `:304-313`,
  `:337-352`, `:355-365`
- `src/remarkable_spec/device/sync.py:326` — constructs `SyncDocument` at `:372-383`, `SyncPage` at
  `:398-404`, `SyncLogEntry` at `:409-417`; a second lazy import at `:479` feeds `:556-562` and
  `:564`
- `src/remarkable_spec/cli/device_cmd.py:337`
- `src/remarkable_spec/cli/diagram_cmd.py:219` and `:244` — `DiagramCacheEntry`
- `src/remarkable_spec/sync/__init__.py:14` — re-exported

**Shape:** verbatim from `src/remarkable_spec/sync/migrations.py:17-29` and `:49-59` and `:93-95`

```sql
CREATE TABLE IF NOT EXISTS documents (
    doc_uuid            TEXT PRIMARY KEY,
    visible_name        TEXT NOT NULL,
    doc_type            TEXT NOT NULL DEFAULT 'DocumentType',
    file_type           TEXT NOT NULL DEFAULT 'notebook',
    parent              TEXT NOT NULL DEFAULT '',
    page_count          INTEGER NOT NULL DEFAULT 0,
    metadata_hash       TEXT,
    content_hash        TEXT,
    device_last_modified INTEGER NOT NULL DEFAULT 0,
    last_synced_at      TEXT,
    local_path          TEXT
);
```

```sql
CREATE TABLE IF NOT EXISTS ocr_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rm_hash     TEXT NOT NULL,
    engine      TEXT NOT NULL,
    ocr_text    TEXT NOT NULL,
    confidence  REAL,
    model_id    TEXT,
    render_dpi  INTEGER NOT NULL DEFAULT 300,
    created_at  TEXT NOT NULL,
    UNIQUE (rm_hash, engine)
);
```

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
```

Column-by-column against `src/remarkable_spec/sync/models.py`:

| DDL table | Pydantic model | Divergence |
| --- | --- | --- |
| `documents` `src/remarkable_spec/sync/migrations.py:17-29`, 11 columns | `SyncDocument` `src/remarkable_spec/sync/models.py:18-46`, 11 fields | names align; `last_synced_at TEXT` is nullable in SQL, non-optional `datetime` in the model (`src/remarkable_spec/sync/models.py:41-43`) |
| `pages` `src/remarkable_spec/sync/migrations.py:35-43`, 6 columns | `SyncPage` `src/remarkable_spec/sync/models.py:49-62`, 6 fields | same nullable-versus-required split on `last_synced_at` (`src/remarkable_spec/sync/models.py:60-62`) |
| `ocr_cache` `src/remarkable_spec/sync/migrations.py:49-59`, 8 columns | `OCRCacheEntry` `src/remarkable_spec/sync/models.py:65-85`, 7 fields | `id INTEGER PRIMARY KEY AUTOINCREMENT` has no model field |
| `diagram_cache` `src/remarkable_spec/sync/migrations.py:64-72`, 7 columns | `DiagramCacheEntry` `src/remarkable_spec/sync/models.py:88-105`, 6 fields | same missing `id` |
| `sync_log` `src/remarkable_spec/sync/migrations.py:77-87`, 9 columns | `SyncLogEntry` `src/remarkable_spec/sync/models.py:108-120`, 8 fields | missing `id`; `pages_transferred` and `details` are nullable in SQL (`sync/migrations.py:82,84`), required in the model (`sync/models.py:114,118`) |
| `schema_version` `src/remarkable_spec/sync/migrations.py:93-95`, 1 column | none | no Pydantic mirror exists |

**Assumptions consumers make:**

- **Every reader assumes row identity is not needed, because the models cannot carry it.** Three
  tables have an `AUTOINCREMENT` surrogate key with no corresponding field, so
  `SyncDB.get_sync_log` orders only by `timestamp DESC` (`src/remarkable_spec/sync/db.py:301`) and
  cannot break ties or paginate stably. `get_all_ocr` orders by `engine` (`:219`) for the same
  reason.
- **A NULL `last_synced_at` reads back as "synced right now".** `_row_to_sync_document` substitutes
  `datetime.now(UTC)` when the column is NULL (`src/remarkable_spec/sync/db.py:350`), and
  `_row_to_sync_page` does the same at `:364`. So "never synced" and "synced this instant" are
  indistinguishable downstream. Both writers always supply a value (`:105`, `:149`), which is why
  the substitution has not surfaced.
- **The same `created_at` column can hold two incomparable datetime kinds.** `migrate_ocr_sidecars`
  inserts SQLite's `datetime('now')` (`src/remarkable_spec/sync/migrations.py:143`), which is a
  naive `'YYYY-MM-DD HH:MM:SS'`; `put_ocr` inserts `entry.created_at.isoformat()`
  (`src/remarkable_spec/sync/db.py:211`) from the timezone-aware `_utcnow` default
  (`src/remarkable_spec/sync/models.py:14-15`, `:83-85`). Both come back through
  `datetime.fromisoformat` at `src/remarkable_spec/sync/db.py:189`, so comparing a migrated row's
  timestamp with a freshly written one raises.
- **Sidecar migration hard-codes values the model treats as configurable.**
  `src/remarkable_spec/sync/migrations.py:143` writes `engine='vision'`, `confidence=NULL`,
  `model_id=NULL`, and `render_dpi=300` as SQL literals, duplicating the model's own default of 300
  (`src/remarkable_spec/sync/models.py:82`).
- **Foreign-key cascade is relied on for page cleanup.** `delete_document` deletes only from
  `documents` (`src/remarkable_spec/sync/db.py:127`) and depends on `ON DELETE CASCADE` in the
  `pages` DDL (`src/remarkable_spec/sync/migrations.py:37`) plus `PRAGMA foreign_keys=ON` set per
  connection at `src/remarkable_spec/sync/db.py:55`. A connection opened without that pragma
  orphans page rows.
- **`schema_version` is written once and never read.** `init_schema` inserts `SCHEMA_VERSION`
  (`src/remarkable_spec/sync/migrations.py:13`, inserted at `:109`) only when the table is empty,
  and no code reads the value back — so the version cannot gate a future migration.
- **Cache invalidation rests entirely on `rm_hash`.** `src/remarkable_spec/sync/hasher.py:3-6`
  states it, `get_ocr` keys on it (`src/remarkable_spec/sync/db.py:177`), `get_diagram` keys on it
  (`:240`), and `find_changed_pages` compares it (`:332`). A page edited and reverted to
  byte-identical content is a cache hit by design.

**Drift risk:** adding a column to `_SCHEMA_SQL` without adding the field is invisible until
someone needs the value, and adding a required field to a model without the column raises a
`KeyError` inside `sqlite3.Row` indexing at the first read — for example
`src/remarkable_spec/sync/db.py:183-189`, which names every column explicitly. Mitigation: add one
startup assertion comparing `PRAGMA table_info(<table>)` column names against each model's
`model_fields` keys, which makes both directions of drift fail loudly at first connection.

---

## Contract 8 — the Bedrock `invoke_model` envelope, owned three times

There is no shared client. Three files each construct their own request body and each parse the
response differently, and the second one's docstring admits the duplication.

**Producer:** `src/remarkable_spec/ocr/postprocess.py:187` — the reference implementation, which
`src/remarkable_spec/ocr/diagram.py:295` cites by name as the pattern it copies.

**Consumer(s):** each site is its own consumer and its own producer —

- `src/remarkable_spec/ocr/postprocess.py:200` — `boto3.client("bedrock-runtime")`, body at
  `:202-226`, call at `:228`, response parsed at `:229-235`
- `src/remarkable_spec/ocr/diagram.py:304` — client, body at `:306-328`, call at `:330`, response
  parsed at `:331-332`
- `src/remarkable_spec/cli/annotations_cmd.py:272` — client, body at `:273-295`, call at `:297`,
  response parsed at `:298-299`

**Shape:** verbatim from `src/remarkable_spec/ocr/postprocess.py:202-226`

```python
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "temperature": 1,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
    )
```

and the response contract, verbatim from `src/remarkable_spec/ocr/postprocess.py:229-235`

```python
    result = json.loads(response["body"].read())
    # With extended thinking enabled, response has thinking + text blocks.
    # Extract the last text block (the actual transcription).
    for block in reversed(result["content"]):
        if block["type"] == "text":
            return block["text"].strip()
    return result["content"][0]["text"].strip()
```

**Assumptions consumers make:**

- **The three bodies are not the same request.** `src/remarkable_spec/ocr/postprocess.py:205-208`
  sends `max_tokens: 16384`, `temperature: 1`, extended thinking with a 10,000-token budget, and a
  `system` prompt. `src/remarkable_spec/ocr/diagram.py:309-310` and
  `src/remarkable_spec/cli/annotations_cmd.py:276-277` both send `max_tokens: 4096`,
  `temperature: 0.0`, no `thinking` block, and no `system` prompt. Only the first can emit thinking
  blocks, which is why only the first scans for the trailing text block.
- **Two of the three would break if thinking were ever enabled on them.**
  `src/remarkable_spec/ocr/diagram.py:332` and `src/remarkable_spec/cli/annotations_cmd.py:299`
  both read `result["content"][0]["text"]` unconditionally. With a thinking block at index 0 that
  raises `KeyError: 'text'`. The safe reader exists at
  `src/remarkable_spec/ocr/postprocess.py:232-234` and was not shared.
- **The model ID is a literal, not configuration.** `src/remarkable_spec/ocr/postprocess.py:23` and
  `src/remarkable_spec/ocr/diagram.py:57` each define
  `DEFAULT_MODEL = "global.anthropic.claude-opus-4-6-v1"`; `src/remarkable_spec/ocr/pipeline.py:90`
  and `src/remarkable_spec/cli/annotations_cmd.py:254` repeat the same string as a parameter
  default. `RmspecSettings` (`src/remarkable_spec/cli/_util.py:13-60`) has no model field, so there
  is no way to override all four together.
- **Region is a parameter default, also repeated.** `src/remarkable_spec/ocr/postprocess.py:24` and
  `src/remarkable_spec/ocr/diagram.py:58` define `DEFAULT_REGION = "us-east-1"`;
  `src/remarkable_spec/ocr/pipeline.py:91` and `src/remarkable_spec/cli/annotations_cmd.py:255`
  inline the same literal.
- **`boto3` absence is the only failure mode handled.** All three wrap the import in
  `except ImportError` with a bespoke message (`src/remarkable_spec/ocr/postprocess.py:195-198`,
  `src/remarkable_spec/ocr/diagram.py:298-301`,
  `src/remarkable_spec/cli/annotations_cmd.py:260-263`). Throttling, an invalid model ID, and a
  `stop_reason` of `max_tokens` are unhandled at every site — a truncated transcription returns as
  if complete.
- **`media_type` is caller-supplied at two sites and hardcoded at the third.**
  `src/remarkable_spec/ocr/postprocess.py:181` passes `"image/png"` through the `media_type`
  parameter; `src/remarkable_spec/cli/annotations_cmd.py:286` writes `"media_type": "image/png"`
  inline with no parameter.
- **The response text is parsed with regex at the diagram site.**
  `src/remarkable_spec/ocr/diagram.py:272` extracts `DIAGRAM_TYPE:` and `:275` extracts a fenced
  `mermaid` block, both driven by the exact "Output format (follow EXACTLY)" instruction in
  `EXTRACTION_PROMPT` (`src/remarkable_spec/ocr/diagram.py:104-110`). The prompt text and the regex
  are a contract with no shared constant.

**Drift risk:** a change in the Anthropic message-body schema, or enabling thinking at one of the
two naive sites, breaks response parsing at two of three call sites while the third keeps working —
producing a partial outage that looks like a per-command bug. Mitigation: promote
`postprocess._invoke_bedrock_vision` to a shared `ocr` helper and have the other two call it, so
there is one body builder and one response reader.

---

## Contract 9 — `DevicePaths` and the remote xochitl layout

An undocumented contract with the vendor's `xochitl` process. This class is where the assumption is
written down.

**Producer:** `src/remarkable_spec/device/paths.py:13`

**Consumer(s):**

- `src/remarkable_spec/device/sync.py:23` — re-aliases three of them as class attributes at
  `:48-50`, then builds remote paths by string concatenation at `:72`, `:75`, `:113`, `:122`,
  `:136`, `:176`, `:260`, `:270`, `:490`
- `src/remarkable_spec/device/web_api.py:18` — `base_url` default is
  `f"http://{DevicePaths.USB_IP}"` at `:59`
- `src/remarkable_spec/device/connection.py:19`
- `src/remarkable_spec/device/__init__.py:16` — re-exported

**Shape:** verbatim from `src/remarkable_spec/device/paths.py:35-46`

```python
    XOCHITL_DATA: str = "/home/root/.local/share/remarkable/xochitl"
    TEMPLATES_BUILTIN: str = "/usr/share/remarkable/templates"
    TEMPLATES_CUSTOM: str = "/home/root/.local/share/remarkable/templates"
    CONFIG_FILE: str = "/home/root/.config/remarkable/xochitl.conf"
    UPDATE_CONF: str = "/usr/share/remarkable/update.conf"
    SPLASH_DIR: str = "/usr/share/remarkable"
    SSH_KEYS: str = "/home/root/.ssh/authorized_keys"

    USB_IP: str = "10.11.99.1"
    USB_SUBNET: str = "10.11.99.0/29"
    WEB_API_PORT: int = 80
    SSH_PORT: int = 22
```

**Assumptions consumers make:**

- **The per-document file layout is documented in a docstring, not in code.**
  `src/remarkable_spec/device/paths.py:20-22` states that each document is a set of files whose
  names derive from the document UUID — a `.metadata`, a `.content`, a `.pagedata`, and a directory
  of per-page `.rm` files. `src/remarkable_spec/device/sync.py:113` reconstructs those names by
  f-string, as do `:122` and `:136`; nothing validates that the remote layout still matches.
- **These are plain class attributes, not an enum or a frozen model.** Any importer can rebind
  `DevicePaths.XOCHITL_DATA` at runtime and every consumer picks up the change, because
  `src/remarkable_spec/device/sync.py:48` copies the value at class-definition time while `:72` and
  later read `self.XOCHITL_DIR`.
- **`WEB_API_PORT` is declared and never used.** `src/remarkable_spec/device/web_api.py:59` builds
  the base URL from `USB_IP` only, relying on the HTTP default rather than the declared `80`. The
  constant is documentation.
- **Path joining is string concatenation, not `PurePosixPath`.**
  `src/remarkable_spec/device/sync.py:75` and `:270` build `f"{self.XOCHITL_DIR}/{entry}"`, so a
  filename containing a slash or a space is not escaped before reaching `connection.execute`
  (`src/remarkable_spec/device/connection.py:140`).
- **The device is assumed reachable at a fixed address unless overridden.**
  `src/remarkable_spec/cli/_util.py:34-38` defaults `device_host` to the same `10.11.99.1`,
  independently of `DevicePaths.USB_IP` — two literals for one address.
- **Firmware coverage is a prose claim.** `src/remarkable_spec/device/paths.py:7` states the paths
  are valid for reMarkable 2 and Paper Pro on firmware 2.x and 3.x. There is no runtime check:
  `UPDATE_CONF` at `:39` names the file whose docstring at `:26` says it "contains firmware version
  info", and that constant has no reader anywhere in `src/` — only the declaration and the
  docstring mention it.

**Drift risk:** a firmware release that moves the xochitl data directory breaks every device
command at once, and the failure surfaces as an empty document list rather than an error, because
`src/remarkable_spec/device/sync.py:276-277` swallows per-entry exceptions with
`except Exception: continue`. Mitigation: have `sync_status` assert the remote directory is
non-empty before reporting zero changes.

---

## Contract 10 — importing `cli/_util.py` mutates the process

This contract has no signature. Its terms are what happens at import time.

**Producer:** `src/remarkable_spec/cli/_util.py:13` (`RmspecSettings`), `:64` (the singleton), and
`:69-72` (the environment mutation)

**Consumer(s):** nine files, all in `cli`, which is the whole surface —

- `src/remarkable_spec/cli/__init__.py:25`
- `src/remarkable_spec/cli/annotations_cmd.py:42`
- `src/remarkable_spec/cli/diagram_cmd.py:43`
- `src/remarkable_spec/cli/env_cmd.py:20`
- `src/remarkable_spec/cli/ls_cmd.py:47`
- `src/remarkable_spec/cli/ocr_cmd.py:40`
- `src/remarkable_spec/cli/render_cmd.py:46`
- `src/remarkable_spec/cli/search_cmd.py:39`
- `src/remarkable_spec/cli/sync_cmd.py:39`
- `src/remarkable_spec/cli/tree_cmd.py:37`

**Shape:** verbatim from `src/remarkable_spec/cli/_util.py:22-27` and `:62-72`

```python
    model_config = SettingsConfigDict(
        env_prefix="RMSPEC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

```python
# Singleton — instantiated once, reads env vars + .env on import
settings = RmspecSettings()


# Auto-configure macOS Homebrew cairo library path so cairosvg/cairocffi
# can find libcairo without the user exporting DYLD_FALLBACK_LIBRARY_PATH.
if platform.system() == "Darwin" and "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ:
    _brew_lib = Path("/opt/homebrew/lib")
    if _brew_lib.exists():
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = str(_brew_lib)
```

**Assumptions consumers make:**

- **Settings are frozen at first import, not read per call.** `settings` is a module-level instance
  (`src/remarkable_spec/cli/_util.py:64`), so a test or a caller that changes `RMSPEC_XOCHITL`
  after import sees the old value. `get_xochitl_dir` reads `settings.xochitl` on every call
  (`:106`) but the underlying object never re-reads the environment.
- **A `.env` in the current working directory is read implicitly.**
  `src/remarkable_spec/cli/_util.py:24` sets `env_file=".env"`, so the resolved configuration
  depends on where the process was launched, and `extra="ignore"` at `:26` means a misspelled
  `RMSPEC_` variable is discarded without complaint.
- **The environment mutation is unconditional for any importer.**
  `src/remarkable_spec/cli/_util.py:69-72` writes `os.environ["DYLD_FALLBACK_LIBRARY_PATH"]` on
  macOS whenever the variable is unset and `/opt/homebrew/lib` exists. Every one of the nine
  consumers inherits that, and so does any library user who imports a CLI module — this is the only
  place in `src/` that writes to `os.environ`.
- **Exactly seven settings exist.** `src/remarkable_spec/cli/_util.py:29-60` declares `xochitl`,
  `device_host`, `device_user`, `device_password`, `thickness`, `dpi`, `sync_db`. There is no model
  ID, no AWS region, and no timeout; region is a function parameter with its own literal default
  (`src/remarkable_spec/ocr/postprocess.py:24`).
- **`get_sync_db` has no return annotation.** `src/remarkable_spec/cli/_util.py:75` is declared
  `def get_sync_db():` and imports `SyncDB` lazily at `:80` to keep `sqlite3` off the startup path,
  so callers get an inferred type and a type checker cannot verify the three call sites.
- **`dpi` defaults to 226, which is the rM2 panel, not the Paper Pro panel.**
  `src/remarkable_spec/cli/_util.py:52-55` sets 226 while `src/remarkable_spec/models/screen.py:83`
  gives Paper Pro 229 — so the CLI's default raster DPI matches the older device even though
  `src/remarkable_spec/cli/render_cmd.py:353-354` defaults the *screen* to Paper Pro.
- **`thickness` reaches only the SVG path.** `src/remarkable_spec/cli/render_cmd.py:364` forwards
  it to `export_svg`, which accepts it (`src/remarkable_spec/export/svg.py:24`); the `.png` branch
  at `src/remarkable_spec/cli/render_cmd.py:373-381` and the `.pdf` branch at `:394-400` call
  `export_png` and `export_pdf`, neither of which declares a `thickness` parameter
  (`src/remarkable_spec/export/png.py:19-28`, `src/remarkable_spec/export/pdf.py:19-26`).
  `RMSPEC_THICKNESS` therefore has no effect on PNG or PDF output.

**Drift risk:** because the singleton is built at import, adding a required field with no default
turns any `from remarkable_spec.cli._util import ...` into a `ValidationError` at import time,
which surfaces as a broken CLI rather than a configuration error. Mitigation: keep every field
defaulted, and move the `DYLD_FALLBACK_LIBRARY_PATH` write into an explicit function the CLI entry
point calls so that importing a command module has no side effect.

---

## Contract 11 — the xochitl `.metadata` and `.content` JSON, read four separate ways

The vendor's on-disk JSON is the widest external contract in the codebase, and four independent
readers interpret it.

**Producer:** `src/remarkable_spec/models/document.py:108` (`DocumentMetadata.from_json`) and
`:260` (`ContentInfo.from_json`) are the canonical readers; the format itself belongs to the
device.

**Consumer(s):**

- `src/remarkable_spec/formats/metadata.py:73` — thin wrapper, delegates to `from_json`; file
  loader at `:56`
- `src/remarkable_spec/formats/content.py:77` — thin wrapper; file loader at `:60`
- `src/remarkable_spec/formats/document_loader.py:63` and `:67` — the only caller of both wrappers
- `src/remarkable_spec/cli/_resolve.py:56` — reads `.metadata` with raw `json.loads`; `:165` and
  `:205` read `.content` the same way, bypassing `formats/` entirely
- `src/remarkable_spec/device/sync.py:275` — raw `json.loads` of a device-fetched `.metadata`;
  `:346` and `:357` do the same during pull

**Shape:** the wire format is documented as an example in each parser. Verbatim from
`src/remarkable_spec/formats/metadata.py:9-20`

```json
    {
        "visibleName": "My Notebook",
        "type": "DocumentType",
        "parent": "",
        "deleted": false,
        "pinned": false,
        "lastModified": "1700000000000",
        "lastOpened": "1700000000000",
        "lastOpenedPage": 0,
        "version": 3,
        "synced": true
    }
```

and verbatim from `src/remarkable_spec/formats/content.py:9-24`

```json
    {
        "fileType": "notebook",
        "formatVersion": 2,
        "orientation": "portrait",
        "pageCount": 3,
        "cPages": {
            "pages": [
                {"id": "abc-123", "template": {"value": "Lined"}},
                {"id": "def-456", "template": {"value": "Blank"}}
            ]
        },
        "extraMetadata": {
            "LastTool": "Fineliner",
            "LastPen": "Finelinerv2"
        }
    }
```

**Assumptions consumers make:**

- **Two spellings of the PDF-redirect field are in use, and they disagree.**
  `src/remarkable_spec/models/document.py:276` reads `p.get("redirect", {}).get("value")` into
  `PageRef.redirect`, while `src/remarkable_spec/cli/_resolve.py:189` reads `page.get("redir", {})`
  and its own docstring at `:177-181` states the field is named `redir`.
  `src/remarkable_spec/cli/inspect_cmd.py:245` surfaces `pr.redirect` to the user. Only one
  spelling can match the device, so one of these two readers always yields nothing.
- **`lastModified` is a string on the wire and coerced three different ways.**
  `src/remarkable_spec/models/document.py:120` does `int(data.get("lastModified", "0"))` with no
  guard; `src/remarkable_spec/cli/_resolve.py:65-69` and
  `src/remarkable_spec/device/sync.py:276-279` and `:366-370` each wrap the same conversion in
  `except (ValueError, TypeError)` and fall back to `0`. The canonical model is the one that
  raises.
- **The default display name differs by reader.** `src/remarkable_spec/models/document.py:115`
  defaults `visibleName` to `""`; `src/remarkable_spec/device/sync.py:279` defaults it to
  `doc_uuid[:8]` and then `:374` re-reads it with that truncated UUID as the fallback. The same
  missing field produces an empty name through one path and a UUID fragment through another.
- **`cPages` entries are assumed to carry an `id` key.**
  `src/remarkable_spec/models/document.py:274` does `UUID(p["id"])`,
  `src/remarkable_spec/cli/_resolve.py:170` does `p["id"]`, and
  `src/remarkable_spec/device/sync.py:362` does `p["id"]` — all three raise `KeyError` on a
  malformed entry, and only the third is inside a broad `except Exception`
  (`src/remarkable_spec/device/sync.py:276-277`).
- **Both the legacy `pages` array and the newer `cPages` CRDT form must be handled, and all three
  readers implement the branch separately.** `src/remarkable_spec/models/document.py:270-281`,
  `src/remarkable_spec/cli/_resolve.py:169-173`, and `src/remarkable_spec/device/sync.py:361-364`
  are three copies of the same if/elif.
- **`.pagedata` positional order outranks the per-page template in `.content`.**
  `src/remarkable_spec/formats/document_loader.py:85-88` prefers `templates[idx]` and only falls
  back to `page_ref.template`, so the two files are assumed index-aligned with no check that
  `len(templates) == len(content.page_refs)`.
- **`ExtraMetadata` keeps the raw dict alongside two extracted keys.**
  `src/remarkable_spec/models/document.py:159-163` sets `tool_settings=data` — the whole
  `extraMetadata` object — while also pulling `LastTool` and `LastPen` out, so the two fields and
  the dict can be read inconsistently.

**Drift risk:** a firmware change to either JSON shape has to be found and fixed in four places,
and the two raw-`json.loads` readers will keep returning plausible defaults rather than failing.
Mitigation: route `cli/_resolve.py` and `device/sync.py` through `parse_metadata` and
`parse_content`, which is the reason `formats/metadata.py` and `formats/content.py` exist.

---

## Other contracts

- **`Palette` and `EXPORT_PALETTE`** — declared at `src/remarkable_spec/render/palette.py:26` and
  `:92`; consumed by `src/remarkable_spec/export/svg.py:15`,
  `src/remarkable_spec/export/png.py:16`, `src/remarkable_spec/export/pdf.py:16`, and
  `src/remarkable_spec/render/engine.py:25`. This is the one `render` type that `export` depends on
  besides `SVGRenderer`, and every exporter defaults to it rather than accepting `None` downstream
  (`src/remarkable_spec/export/svg.py:62`, `src/remarkable_spec/export/png.py:62-63`,
  `src/remarkable_spec/export/pdf.py:77-78`).
- **`export_svg` is the single rasterization funnel** — `src/remarkable_spec/export/svg.py:18`;
  called by `src/remarkable_spec/export/png.py:72-80`, `src/remarkable_spec/export/pdf.py:91-98`
  and `:115-122`, `src/remarkable_spec/ocr/pipeline.py:66-73`,
  `src/remarkable_spec/ocr/vision.py:172`, and `src/remarkable_spec/cli/render_cmd.py:359-367`.
  Every PNG and PDF in this codebase is an SVG first, so an SVG-level bug reaches all three
  formats.
- **`export_pdf` does not deliver multi-page output** — `src/remarkable_spec/export/pdf.py:19`
  promises pages "combined into a single PDF document in the order provided" (`:30-31`), builds a
  `cairocffi.PDFSurface` at `:135-157`, then at `:159-168` discards it and writes only
  `page_pdfs[0]`. The comment at `:166-167` acknowledges the simplification. Reached from the CLI
  only with a single-element list (`src/remarkable_spec/cli/render_cmd.py:394-400`), so the
  shortfall is latent there.
- **`OCRResult` and `OCRLine`** — plain dataclasses at `src/remarkable_spec/ocr/vision.py:19` and
  `:28`; produced by `ocr_image` at `:133-137` and by `ocr_image_textract` at
  `src/remarkable_spec/ocr/textract.py:67-71`; consumed by
  `src/remarkable_spec/ocr/postprocess.py:21`, which reads only `.text` (`:174-175`) and ignores
  `confidence` and `lines` entirely. The two producers normalize confidence differently — Vision
  passes Apple's value through (`src/remarkable_spec/ocr/vision.py:112`) while Textract divides by
  100 (`src/remarkable_spec/ocr/textract.py:51`) — and their bounding boxes use different origins,
  normalized bottom-left for Vision (`src/remarkable_spec/ocr/vision.py:114-115`) versus Textract's
  `Left`/`Top` (`src/remarkable_spec/ocr/textract.py:58-59`).
- **The Textract `Blocks` response** — `src/remarkable_spec/ocr/textract.py:40`; the reader assumes
  every block has `BlockType`, and that `LINE` blocks have `Text` and `Confidence`, indexing all
  three without `.get` at `:47`, `:50`, `:51` while treating `Geometry` as optional at `:52`.
- **`DeviceConnection`** — `src/remarkable_spec/device/connection.py:38`, a six-method SSH surface
  (`src/remarkable_spec/device/connection.py:140` `execute`, `:166` `get_file`, `:183` `put_file`,
  `:202` `list_dir`, plus `:81` `connect` and `:119` `disconnect`) consumed by
  `src/remarkable_spec/device/sync.py:22` and by `src/remarkable_spec/cli/device_cmd.py:109` and
  `:334` and `src/remarkable_spec/cli/sync_cmd.py:84`. `list_dir` returns `list[str]` of bare
  names, and `src/remarkable_spec/device/sync.py:261-263` filters them by `.endswith(".metadata")`.
- **`WebAPI`** — `src/remarkable_spec/device/web_api.py:37`, an HTTP surface against
  `http://10.11.99.1` (`:59`) returning `list[dict[str, Any]]` with no schema (`:70`, `:90`,
  `:209`). The docstring at `:47-48` and the usage example at `:55-56` name `"ID"` as the document
  key, and `:93-95` records that `/documents/` returns only root-level items, so
  `list_all_documents` walks folders itself.
- **`SyncDB`** — `src/remarkable_spec/sync/db.py:26`; consumed by
  `src/remarkable_spec/cli/_util.py:80` and `src/remarkable_spec/device/sync.py:28`. The connection
  is lazy (`src/remarkable_spec/sync/db.py:47-60`) and every write commits immediately (`:109`,
  `:152`, `:214`, `:274`, `:296`), so there is no transaction spanning a document plus its pages —
  an interrupted pull leaves a document row with partial page rows.
- **`hash_document_files` returns a heterogeneous dict** — `src/remarkable_spec/sync/hasher.py:27`
  is typed `dict[str, str | dict[str, str]]` because `"metadata"` and `"content"` map to strings
  while `"pages"` maps to a nested dict (`:47`, `:51`, `:61`).
  `src/remarkable_spec/device/sync.py:379-380` feeds `hashes.get("metadata")` straight into
  `SyncDocument.metadata_hash`, which is declared `str | None`
  (`src/remarkable_spec/sync/models.py:36`) — the union widens at the boundary and only the
  caller's key choice keeps it correct. Missing files are omitted rather than mapped to `None`
  (`src/remarkable_spec/sync/hasher.py:41`).
- **The external `mmdc` binary** — invoked at three sites with three different argument sets and no
  shared wrapper: `src/remarkable_spec/ocr/diagram.py:232` reads Mermaid from stdin with a
  10-second timeout, `src/remarkable_spec/cli/diagram_cmd.py:289` passes a temp file with a
  30-second timeout, and `src/remarkable_spec/device/push.py:129` adds `--pdfFit` with a 30-second
  timeout. All three treat `FileNotFoundError` as an expected state —
  `src/remarkable_spec/ocr/diagram.py:241-256` degrades to a starts-with-keyword check against a
  nine-entry allowlist, while `src/remarkable_spec/cli/diagram_cmd.py:298-302` prints an install
  hint. The contract is an exit code plus stderr (`src/remarkable_spec/ocr/diagram.py:238`).
- **`ResolvedDocument`** — `src/remarkable_spec/cli/_resolve.py:211`, produced by
  `resolve_document_full` at `:234` and consumed across the CLI. `rm_files` deliberately contains
  `None` for unannotated pages so indices stay aligned with PDF pages (`:217-218`, built at
  `:146-152`), and `page_indices` is built from the redirect map with a positional fallback
  (`:270-281`). The tie-break on duplicate names — most pages, then most recently modified — lives
  at `:134`.
- **`RenderEngine`** — the ABC at `src/remarkable_spec/render/engine.py:35`, with one
  implementation, `SVGRenderer` at `:75`. The eight-parameter `render_page` signature is declared
  twice, at `:44-54` and `:91-101`, and must be kept in step by hand;
  `src/remarkable_spec/export/svg.py:59-68` calls it by keyword for all eight.
- **`rasterize_pdf_page`** — `src/remarkable_spec/render/pdf_bg.py:15`, returning
  `tuple[str, float, float]` of base64 PNG plus native page dimensions; the only PyMuPDF boundary,
  consumed from `cli/` at four call sites. The returned dimensions are what
  `src/remarkable_spec/render/engine.py:162-167` and `:194-200` use to widen the viewBox and centre
  the background on `x_shift`.
- **`Template`, `TemplateItem`, `BuiltinTemplate`, `BUILTIN_TEMPLATES`** —
  `src/remarkable_spec/models/template.py:79`, `:57`, `:20`, `:140`. Re-exported on the public
  library surface at `src/remarkable_spec/__init__.py:26-31` and `:57-60`, but no other module
  under `src/` imports them, so this is a published contract with no internal consumer.

## See also

- [business logic](business-logic.md) — 43 shared source citations
- [module map](../architecture/module-map.md) — 41 shared source citations
- [impact analysis](impact-analysis.md) — 41 shared source citations
- [processes](../behavior/processes.md) — 38 shared source citations
- [tech debt](tech-debt.md) — 35 shared source citations
