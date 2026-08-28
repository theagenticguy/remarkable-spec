# remarkable-spec · Impact analysis

If you touch one of the definitions below, this page tells you what else has to move with it.

**What counts as a high-impact surface here.** A surface is one defining module plus the symbols it
exports. Surfaces are ranked by **distinct inbound import statements across `src/`** — every
`from remarkable_spec.<module> import <name>` counted once per statement. The count comes from an AST
walk over all 56 tracked `.py` files rather than a grep, for two reasons specific to this codebase:
multiline `from ... import (` blocks are common (`src/remarkable_spec/models/__init__.py:20-45`), and
function-local lazy imports are used deliberately to keep optional extras out of CLI startup
(`src/remarkable_spec/cli/_util.py:80`, `src/remarkable_spec/ocr/pipeline.py:46-49`) — a line-oriented
grep undercounts the first and a naive top-of-file scan misses the second entirely.

Two adjustments, both stated so you can re-derive the ranking:

- **Two cohort merges.** `src/remarkable_spec/sync/db.py:26` and `src/remarkable_spec/sync/models.py:1`
  count as one surface, because the models module declares itself "the typed API surface used by
  ``SyncDB`` methods" (`src/remarkable_spec/sync/models.py:3-4`) and `db.py` imports all five of them
  (`src/remarkable_spec/sync/db.py:15-21`). `src/remarkable_spec/export/svg.py:18` and
  `src/remarkable_spec/render/engine.py:91` count as one, because `export_svg` is a ten-line wrapper
  that constructs an `SVGRenderer` and forwards every argument unchanged
  (`src/remarkable_spec/export/svg.py:58-68`).
- **Tie-break at equal count**: a surface whose consumers span more than one package outranks one
  whose consumers all live inside a single package. That rule is what moves `cli/_resolve.py` out of
  the main set and into the overflow section.

**There are no tests.** pytest is configured to collect from `tests/` (`pyproject.toml:74-76`) and that
directory holds exactly one file, a 0-byte `tests/__init__.py`. So no row below carries Type `test`,
"N tests would need updating" is not an available downstream effect, and every "Touch on change"
judgment is a static read of source rather than a coverage claim. The honest downstream effects in
this repo are: internal call sites, CLI output shape, the published `__all__` surface that library
importers depend on, on-disk artifacts (the SQLite file, exported SVG/PNG/PDF, `.ocr.txt` sidecars),
and cached rows that become *wrong* rather than merely stale.

**The measured ranking.**

| # | Surface | Defining file | Inbound import statements |
| --- | --- | --- | --- |
| 1 | `Page`, `Layer`, `TextBlock` | `src/remarkable_spec/models/page.py:99` | 16 |
| 2 | `ScreenSpec`, `detect_screen` | `src/remarkable_spec/models/screen.py:14` | 14 |
| 3 | `RmspecSettings`, `settings` | `src/remarkable_spec/cli/_util.py:13` | 14 |
| 4 | `parse_rm_file`, `parse_rm_bytes` | `src/remarkable_spec/formats/rm_file.py:46` | 10 |
| 5 | `SyncDB` + sync models + SQL schema | `src/remarkable_spec/sync/db.py:26` | 10 (cohort) |
| 6 | `export_svg` + `SVGRenderer.render_page` | `src/remarkable_spec/export/svg.py:18` | 8 (cohort) |
| 7 | `DocumentType`, `DocumentMetadata`, `ContentInfo`, `Document` | `src/remarkable_spec/models/document.py:27` | 7 |
| 8 | `PenType`, `Pen` | `src/remarkable_spec/models/pen.py:17` | 7 |

The module layering these counts imply — `models` and `sync` as leaves, `cli` reaching everything — is
a convention the code currently honors, not a gate. Nothing in `pyproject.toml` or `lefthook.yml`
enforces it (`pyproject.toml:63-68`, `lefthook.yml:1-14`).

## `Page`, `Layer`, and `TextBlock`

Defined at: `src/remarkable_spec/models/page.py:99` (`Layer` at `:45`, `TextBlock` at `:18`)

The widest surface in the repo. Every render, export, and OCR path takes a `Page`; `Document` nests a
list of them.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `load_document` assembles one `Page` per page ref | direct import | yes | `src/remarkable_spec/formats/document_loader.py:27,103-109` |
| `SVGRenderer.render_page` walks `page.layers`, honours `layer.visible`, reads `layer.name` | direct import | yes | `src/remarkable_spec/render/engine.py:21,145-159,207-214` |
| `export_svg(page, ...)` | direct import | yes | `src/remarkable_spec/export/svg.py:12,19` |
| `export_png(page, ...)` | direct import | yes | `src/remarkable_spec/export/png.py:14,20` |
| `export_pdf(pages: list[Page], ...)` | direct import | yes | `src/remarkable_spec/export/pdf.py:14,20` |
| `Document.pages: list[Page]` | direct import | yes | `src/remarkable_spec/models/document.py:24,332` |
| `_convert_group` / `_collect_item` build `Layer` and `TextBlock` from rmscene items | direct import | yes | `src/remarkable_spec/formats/rm_file.py:25,119,142-144` |
| `render_rm_to_png` synthesizes a `Page` around parsed layers | direct import | yes | `src/remarkable_spec/ocr/pipeline.py:48,59` |
| `ocr_page` synthesizes a `Page` around parsed layers | direct import | yes | `src/remarkable_spec/ocr/vision.py:160,165` |
| `_render_single_rm` and `_load_page_from_rm` build pages for the render command | direct import | yes | `src/remarkable_spec/cli/render_cmd.py:151,158,305,310` |
| `detect_screen` iterates `layer.strokes` and `stroke.points` through an untyped `list` parameter | indirect | likely | `src/remarkable_spec/models/screen.py:86,98-100` |
| `models/__init__.py` re-export and `__all__` | direct import | yes | `src/remarkable_spec/models/__init__.py:36,61-63` |
| Root `__init__.py` re-export and `__all__` — the library contract | direct import | yes | `src/remarkable_spec/__init__.py:22,47-49` |

### Blast-radius notes

- **`Page.uuid` is a lie on three of the five construction paths.** `parse_rm_file` returns
  `list[Layer]`, never a `Page` (`src/remarkable_spec/formats/rm_file.py:46`), so each caller wraps the
  layers itself — and `src/remarkable_spec/ocr/pipeline.py:59`,
  `src/remarkable_spec/ocr/vision.py:165`, and `src/remarkable_spec/cli/render_cmd.py:158` all pass
  `uuid=uuid4()`. Everything derived from that field — `Page.rm_filename`,
  `Page.metadata_filename`, `Page.thumbnail_filename`, `Page.rm_path`
  (`src/remarkable_spec/models/page.py:126,132,138,148`) — is meaningless on those paths. Only
  `src/remarkable_spec/formats/document_loader.py:105` and
  `src/remarkable_spec/cli/render_cmd.py:310` pass the real page UUID. If you make `Page.uuid` load
  bearing, fix those three call sites first.
- **`Layer.bounding_box` is not the extent of what gets rendered.** It mins and maxes over
  `self.strokes` only, ignoring both `visible` and `text_blocks`, and its own docstring says so
  (`src/remarkable_spec/models/page.py:82-96`). `Page.all_strokes` *does* filter on visibility
  (`:146`), and so does the renderer (`src/remarkable_spec/render/engine.py:146-147,208-209`). A
  consumer that treats the bounding box as the render extent is wrong for any page with a hidden layer.
- **`detect_screen` couples to `Layer` and `Stroke` field names without a type to protect it.** Its
  parameter is annotated as a bare `list` (`src/remarkable_spec/models/screen.py:86`) and it reaches
  `layer.strokes`, `stroke.points`, `pt.x`, `pt.y` positionally (`:98-102`). Renaming any of those
  four attributes leaves this function type-clean and silently returning the wrong screen.

## `ScreenSpec` and `detect_screen`

Defined at: `src/remarkable_spec/models/screen.py:14` (`detect_screen` at `:86`, `RM2_SCREEN` at `:80`,
`PAPER_PRO_SCREEN` at `:83`)

`ScreenSpec.dpi` is the single scalar that sets output geometry: the renderer derives its coordinate
scale and the center-origin X shift from it.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `SVGRenderer.render_page` computes `scale = 72.0 / screen.dpi`, `vw`, `vh`, and `x_shift = vw / 2` | direct import | yes | `src/remarkable_spec/render/engine.py:23,128-134` |
| `export_svg` defaults a missing `screen` to `RM2_SCREEN` | direct import | yes | `src/remarkable_spec/export/svg.py:13,63` |
| `export_png` defaults a missing `screen` to `RM2_SCREEN` | direct import | yes | `src/remarkable_spec/export/png.py:15,64-65` |
| `export_pdf` uses `screen.dpi`/`width`/`height` to size the `cairocffi` PDF surface | direct import | yes | `src/remarkable_spec/export/pdf.py:15,79-80,131-135` |
| `render_rm_to_png` sizes the PNG from `screen.width`, `screen.height`, `screen.dpi` | direct import | yes | `src/remarkable_spec/ocr/pipeline.py:49,60,78-79` |
| `ocr_page` pins the screen to `RM2_SCREEN` regardless of content | direct import | yes | `src/remarkable_spec/ocr/vision.py:161,172,186-187` |
| `_export_page` defaults a missing `screen` to `PAPER_PRO_SCREEN` | direct import | yes | `src/remarkable_spec/cli/render_cmd.py:47,353-354` |
| `_get_pdf_bg_ocr`, `_get_pdf_bg_diagram`, `_analyze_page` convert `screen.page_width_pt` / `page_height_pt` into PDF raster dimensions | direct import | yes | `src/remarkable_spec/cli/ocr_cmd.py:191,201-202`, `src/remarkable_spec/cli/diagram_cmd.py:363`, `src/remarkable_spec/cli/annotations_cmd.py:223` |
| `models/__init__.py` and root `__init__.py` re-exports | direct import | yes | `src/remarkable_spec/models/__init__.py:38`, `src/remarkable_spec/__init__.py:24` |
| Every SVG, PNG, and PDF already on disk | indirect | no | `src/remarkable_spec/render/engine.py:174-179` |

### Blast-radius notes

- **Three different answers to "which screen when the caller does not say".** `export_svg`,
  `export_png`, and `export_pdf` all fall back to `RM2_SCREEN` (`src/remarkable_spec/export/svg.py:63`,
  `src/remarkable_spec/export/png.py:64-65`, `src/remarkable_spec/export/pdf.py:79-80`), while the CLI's
  own `_export_page` falls back to `PAPER_PRO_SCREEN` (`src/remarkable_spec/cli/render_cmd.py:353-354`).
  Changing either default changes output dimensions for callers who never passed the argument, and the
  two defaults will diverge further unless changed together.
- **`ocr_page` does not use `detect_screen`, and `render_rm_to_png` does.** `ocr_page` hardcodes
  `screen=RM2_SCREEN` and derives its raster size from RM2 constants
  (`src/remarkable_spec/ocr/vision.py:172,186-187`); `render_rm_to_png` calls `detect_screen(layers)`
  first (`src/remarkable_spec/ocr/pipeline.py:60`). `rmspec search` goes through `ocr_page`
  (`src/remarkable_spec/cli/search_cmd.py:136,204`) and `rmspec ocr` goes through `render_rm_to_png`
  (`src/remarkable_spec/cli/ocr_cmd.py:86,155`), so the two commands rasterize Paper Pro pages at
  different sizes today. Any change to `detect_screen` reaches only one of them.
- **`detect_screen` reports rM2 for a Paper Pro page whose strokes happen to fit.** The heuristic is a
  pure extents test — `abs(pt.x) > RM2_SCREEN.width / 2 or pt.y > RM2_SCREEN.height`
  (`src/remarkable_spec/models/screen.py:102`) — with `RM2_SCREEN` as the fallthrough (`:104`). A
  lightly-annotated Paper Pro page is therefore exported at 1404x1872 and 226 DPI. Widening or
  tightening that test changes the geometry of previously-exported artifacts without changing any
  signature.

## `RmspecSettings` and the `settings` singleton

Defined at: `src/remarkable_spec/cli/_util.py:13` (`settings` at `:64`, `get_sync_db` at `:75`,
`get_xochitl_dir` at `:85`)

The only configuration surface. It is a `pydantic_settings.BaseSettings` subclass instantiated once at
import time, so the environment is read exactly once per process.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `sync` command group: `settings.device_host`, `device_user`, `device_password` as parameter defaults | config | yes | `src/remarkable_spec/cli/sync_cmd.py:39,53,57,92,103,107,176,180,249,253` |
| `device` command group: same three fields, four subcommands | config | yes | `src/remarkable_spec/cli/device_cmd.py:45,71,75,112,167,171,306,310,378,464,468` |
| `search` command reads `settings.device_host` | config | yes | `src/remarkable_spec/cli/search_cmd.py:39,87` |
| `env` command prints `RMSPEC_XOCHITL` and `RMSPEC_DEVICE_HOST` only | config | yes | `src/remarkable_spec/cli/env_cmd.py:20,39-44` |
| `get_xochitl_dir` — the resolution chain every filesystem-reading command calls | direct import | yes | `src/remarkable_spec/cli/_util.py:85,104-112` |
| `ls`, `tree`, `render`, `ocr`, `diagram`, `annotations` each import `get_xochitl_dir` | direct import | likely | `src/remarkable_spec/cli/ls_cmd.py:47`, `src/remarkable_spec/cli/tree_cmd.py:37`, `src/remarkable_spec/cli/render_cmd.py:46`, `src/remarkable_spec/cli/ocr_cmd.py:40`, `src/remarkable_spec/cli/diagram_cmd.py:43`, `src/remarkable_spec/cli/annotations_cmd.py:42` |
| `get_sync_db()` constructs `SyncDB(settings.sync_db)` | direct import | yes | `src/remarkable_spec/cli/_util.py:75-82` |
| `cli/__init__.py` imports both helpers at the top-level app module | direct import | yes | `src/remarkable_spec/cli/__init__.py:25` |
| `DYLD_FALLBACK_LIBRARY_PATH` in the ambient process environment | runtime dispatch | likely | `src/remarkable_spec/cli/_util.py:69-72` |

### Blast-radius notes

- **`thickness` and `dpi` are declared and never read.** `RmspecSettings.thickness`
  (`src/remarkable_spec/cli/_util.py:47-51`) and `RmspecSettings.dpi` (`:52-55`) have zero consumers;
  every command that needs those values declares its own literal default instead —
  `src/remarkable_spec/cli/render_cmd.py:85,89` (1.5 and 226) and
  `src/remarkable_spec/cli/ocr_cmd.py:75,79` (300 and 1.5). So `RMSPEC_THICKNESS` and `RMSPEC_DPI` have
  no effect, and the three DPI numbers in play — 226 in settings, 226 in the render command, 300 in the
  OCR command — are independent constants that will not move together. Wiring either field up is a
  behavior change for anyone who has already set the env var expecting it to work.
- **The singleton is constructed at import time, so runtime mutation of `os.environ` is invisible.**
  `settings = RmspecSettings()` runs at module import (`src/remarkable_spec/cli/_util.py:64`), and
  cyclopts evaluates `settings.device_host` when it builds each parameter default
  (`src/remarkable_spec/cli/sync_cmd.py:53`). Anything that needs to re-read the environment has to
  re-instantiate; there is no reload path.
- **The same module mutates the process environment as a side effect of being imported.** On macOS
  with `DYLD_FALLBACK_LIBRARY_PATH` unset and `/opt/homebrew/lib` present, importing `cli/_util.py`
  writes that variable into `os.environ` (`src/remarkable_spec/cli/_util.py:69-72`). Every CLI module
  imports this file, so the side effect is unavoidable in the CLI, and it leaks into any library
  consumer that imports anything under `remarkable_spec.cli`. `rmspec env` reports the value it would
  set (`src/remarkable_spec/cli/env_cmd.py:47-56`) but not the four `RMSPEC_` variables it never
  prints — `RMSPEC_DEVICE_USER`, `RMSPEC_DEVICE_PASSWORD`, `RMSPEC_THICKNESS`, `RMSPEC_DPI`,
  `RMSPEC_SYNC_DB` are all absent from the emitted export list (`:36-56`).

## `parse_rm_file` and `parse_rm_bytes`

Defined at: `src/remarkable_spec/formats/rm_file.py:46` (`parse_rm_bytes` at `:70`)

The single entry point from the v6 binary format into the model layer. Ten import statements reach it
and every one of them is on a path that ends in a rendered artifact or printed output.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `load_document` parses each page's `.rm`, swallowing failures into an empty layer list | direct import | yes | `src/remarkable_spec/formats/document_loader.py:25,91-101` |
| `render_rm_to_png` — the OCR render step | direct import | yes | `src/remarkable_spec/ocr/pipeline.py:47,58` |
| `ocr_page` — the `rmspec search` render step | direct import | yes | `src/remarkable_spec/ocr/vision.py:159,164` |
| `_render_single_rm` and `_load_page_from_rm` | direct import | yes | `src/remarkable_spec/cli/render_cmd.py:150,154,304,308` |
| `_inspect_rm` — `rmspec inspect` layer and stroke dump | direct import | yes | `src/remarkable_spec/cli/inspect_cmd.py:105` |
| `_get_pdf_bg_ocr` — PDF background sizing for `rmspec ocr` | direct import | yes | `src/remarkable_spec/cli/ocr_cmd.py:190,194` |
| `_get_pdf_bg_diagram` — same for `rmspec diagram` | direct import | yes | `src/remarkable_spec/cli/diagram_cmd.py:362` |
| `_analyze_page` — same for `rmspec annotations` | direct import | yes | `src/remarkable_spec/cli/annotations_cmd.py:222` |
| `formats/__init__.py` re-export and `__all__` | direct import | yes | `src/remarkable_spec/formats/__init__.py:19` |
| `rmscene` 0.7.0 `read_tree` and `scene_items`, the upstream it wraps | indirect | likely | `src/remarkable_spec/formats/rm_file.py:21-22,83` |
| Every `.ocr.txt` sidecar already written next to a `.rm` file | indirect | no | `src/remarkable_spec/cli/ocr_cmd.py:165-166` |

### Blast-radius notes

- **Importing this module reconfigures logging for the whole process.**
  `logging.getLogger("rmscene").setLevel(logging.ERROR)` runs at import time
  (`src/remarkable_spec/formats/rm_file.py:31`), so any process that imports the parser — including a
  library consumer that only wanted `parse_rm_bytes` — loses rmscene's own warnings globally. The
  module keeps a separate logger of its own (`:33`) and does emit warnings for unknown pen types
  (`:162`) and unknown colors (`:169`).
- **Unrecognised enum values are absorbed, not raised.** An unknown `line.tool` becomes
  `PenType.FINELINER_1` (`src/remarkable_spec/formats/rm_file.py:159-163`) and an unknown `line.color`
  becomes `PenColor.BLACK` (`:166-170`). The consequence runs the other way too: **adding a member to
  `PenType` or `PenColor` changes the parse result for files that previously fell back**, so any
  cached derivative of those files silently disagrees with a fresh parse. Because the diagram cache is
  keyed only on the `.rm` file's SHA-256 (`src/remarkable_spec/sync/migrations.py:66`) and the
  `.ocr.txt` sidecar is keyed only on the page filename
  (`src/remarkable_spec/cli/search_cmd.py:196-198`), neither cache notices.
- **The return type is `list[Layer]`, not `Page`, and the docstring's `Raises` contract is not
  enforced by any caller except one.** The declared failures are `FileNotFoundError` and
  `rmscene.UnexpectedBlockError` (`src/remarkable_spec/formats/rm_file.py:59-64`). `load_document` is
  the only caller that guards the call, and it catches bare `Exception` and continues with zero layers
  (`src/remarkable_spec/formats/document_loader.py:93-101`) — so a corrupt page renders as a blank
  page rather than an error. The other nine call sites let the exception propagate. Narrowing or
  widening the raised set changes which of those two behaviors a user sees.

## `SyncDB`, the sync models, and the SQLite schema

Defined at: `src/remarkable_spec/sync/db.py:26` (schema SQL at
`src/remarkable_spec/sync/migrations.py:15-96`, models at `src/remarkable_spec/sync/models.py:18,49,65,88,108`)

The only durable state in the repo. Everything else in this document is in-process; this surface writes
a file at `~/.remarkable-spec/sync.db` (`src/remarkable_spec/sync/db.py:23`) that survives every run.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `SyncManager.pull_document` writes documents, pages, and log rows | direct import | yes | `src/remarkable_spec/device/sync.py:326,384,397,408,423` |
| `SyncManager` push path writes a document and a log row | direct import | yes | `src/remarkable_spec/device/sync.py:479,555,563` |
| `SyncManager` change detection reads `get_document` and `list_documents`, deletes with cascade | direct import | yes | `src/remarkable_spec/device/sync.py:288,295,334` |
| `rmspec device push` writes a document and two log rows through `get_sync_db()` | direct import | yes | `src/remarkable_spec/cli/device_cmd.py:337,396,399,416,419,427` |
| `rmspec sync log` renders `get_sync_log(limit=...)` output | direct import | yes | `src/remarkable_spec/cli/sync_cmd.py:39,381` |
| `rmspec diagram` reads and writes the diagram cache keyed on `rm_hash` | direct import | yes | `src/remarkable_spec/cli/diagram_cmd.py:217-219,223,244,246` |
| `hash_file` / `hash_document_files` produce the `rm_hash` values every cache row is keyed on | direct import | yes | `src/remarkable_spec/sync/hasher.py:15,27`, `src/remarkable_spec/device/sync.py:325` |
| `get_sync_db()` in the CLI settings helper | direct import | yes | `src/remarkable_spec/cli/_util.py:80-82` |
| `sync/__init__.py` re-export and `__all__` | direct import | yes | `src/remarkable_spec/sync/__init__.py:13-14` |
| Any `sync.db` file already on a user's disk | indirect | yes | `src/remarkable_spec/sync/migrations.py:99-104` |

### Blast-radius notes

- **A schema change does not reach an existing database, and nothing detects that.** Every DDL
  statement is `CREATE TABLE IF NOT EXISTS` (`src/remarkable_spec/sync/migrations.py:17,35,49,64,77,93`),
  so adding or widening a column is a no-op against a file that already exists. `init_schema` reads
  `schema_version` only to decide whether to insert a row, never to compare it against
  `SCHEMA_VERSION` and never to upgrade (`src/remarkable_spec/sync/migrations.py:99-110`, constant at
  `:13`). Bumping that constant therefore changes nothing. **If you change the schema, the only
  working migration is for the user to delete `~/.remarkable-spec/sync.db`, and you have to tell them
  so** — `src/remarkable_spec/sync/db.py:51-52` recreates the file and its parent directory silently.
- **Both caches are keyed on `rm_hash` alone, so a render or prompt change makes existing rows wrong
  rather than stale.** `diagram_cache.rm_hash` is the unique key
  (`src/remarkable_spec/sync/migrations.py:66`) and `ocr_cache` is unique on `(rm_hash, engine)`
  (`:58`). `model_id` and `render_dpi` are stored on the row (`:55-56`) but are not part of either key,
  and neither is a render-behavior version. Change the SVG geometry, the DPI, the pen formulas, or the
  Bedrock prompt, and `rmspec diagram` returns the pre-change answer for an unchanged page and reports
  it as a cache hit (`src/remarkable_spec/cli/diagram_cmd.py:223`). There is no invalidation lever
  short of deleting rows.
- **The OCR cache is wired at the database and dead at the call site.** `SyncDB.get_ocr`,
  `put_ocr`, and `get_all_ocr` (`src/remarkable_spec/sync/db.py:174,192,216`) have no callers anywhere
  in `src/`, and `migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:113`) has none either
  — so the `ocr_cache` table is never written. The OCR path caches to `.ocr.txt` sidecar files instead
  (`src/remarkable_spec/cli/ocr_cmd.py:165-166`), and `rmspec search` reads a sidecar whenever it
  exists with **no hash comparison at all** (`src/remarkable_spec/cli/search_cmd.py:196-198`). Editing
  a page on the device therefore leaves `rmspec search` serving the old transcription indefinitely.
  `CLAUDE.md:40` describes `rm_hash` as the cache-invalidation key "for OCR and diagram results";
  that is true for diagrams and false for OCR today. Wiring `put_ocr` up is a two-sided change — the
  writer in `ocr_cmd` and the reader in `search_cmd` both have to move, or the sidecar keeps winning.

## `export_svg` and `SVGRenderer.render_page`

Defined at: `src/remarkable_spec/export/svg.py:18` (renderer at
`src/remarkable_spec/render/engine.py:91`, abstract signature at `:44`)

The chokepoint. PNG, PDF, and both OCR render paths all produce an intermediate SVG through this one
function, so every pixel this project emits passes through `render_page`.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `export_png` renders to a temp SVG then rasterizes with cairosvg | direct import | yes | `src/remarkable_spec/export/png.py:60,72-80,93-97` |
| `export_pdf` renders each page to a temp SVG then converts with cairosvg | direct import | yes | `src/remarkable_spec/export/pdf.py:75,91-98,115-122` |
| `render_rm_to_png` — the `rmspec ocr` and `rmspec annotations` render step | direct import | yes | `src/remarkable_spec/ocr/pipeline.py:46,66-73` |
| `ocr_page` — the `rmspec search` render step | direct import | yes | `src/remarkable_spec/ocr/vision.py:158,172` |
| `_export_page` dispatches on output suffix and is the only caller that forwards `thickness` | direct import | yes | `src/remarkable_spec/cli/render_cmd.py:357,359-367` |
| `export/__init__.py` re-export | direct import | yes | `src/remarkable_spec/export/__init__.py:18` |
| `get_pen_renderer` and `Pen.from_stroke`, reached once per stroke | runtime dispatch | likely | `src/remarkable_spec/render/engine.py:26,261-262` |
| `Palette.get_rgb`, reached once per stroke | direct import | likely | `src/remarkable_spec/render/engine.py:25,263`, `src/remarkable_spec/render/palette.py:41,57-60` |
| Every SVG, PNG, and PDF this project has already written | indirect | no | `src/remarkable_spec/render/engine.py:226-230` |

### Blast-radius notes

- **`thickness` reaches SVG output and nothing else.** `export_png` has no `thickness` parameter
  (`src/remarkable_spec/export/png.py:19-28`) and neither does `export_pdf`
  (`src/remarkable_spec/export/pdf.py:19-26`), so both call `export_svg` without it
  (`src/remarkable_spec/export/png.py:72-80`, `src/remarkable_spec/export/pdf.py:91-98,115-122`) and
  get the parameter default of 1.5 (`src/remarkable_spec/export/svg.py:24`).
  The `--thickness` flag on `rmspec render` is accepted for every suffix
  (`src/remarkable_spec/cli/render_cmd.py:82-85`) but only the `.svg` branch forwards it
  (`src/remarkable_spec/cli/render_cmd.py:359-367` versus `:374-382` and `:395-401`).
  Changing the default in
  `export_svg` silently changes raster and PDF output; changing it in `SVGRenderer.render_page`
  (`src/remarkable_spec/render/engine.py:98`) changes nothing, because `export_svg` always passes an
  explicit value (`src/remarkable_spec/export/svg.py:65`).
- **Multi-page PDF export writes page 1 and drops the rest.** The multi-page branch builds one PDF per
  page (`src/remarkable_spec/export/pdf.py:107-128`), runs a `cairocffi` loop that paints white and
  calls `show_page` without ever compositing the page bytes (`:138-157`), and then ends with
  `output.write_bytes(page_pdfs[0])` (`:168`) — overwriting the surface output with the first page
  only. The docstring at `:31` promises "Pages are combined into a single PDF document in the order
  provided" and the inline comment at `:166-167` admits the merge is simplified. Any change to
  `export_svg`'s output that a reader validates through a multi-page PDF will look like it did nothing.
- **`render_page` drops any stroke with fewer than two points, and the model says the opposite.**
  `if len(points) < 2: return` (`src/remarkable_spec/render/engine.py:258-259`) means a single-tap dot
  never appears in output, while `Stroke.points` documents empty strokes as valid "e.g. single-tap
  dots" (`src/remarkable_spec/models/stroke.py:110-111`). Separately, the viewBox is padded by at
  least 30 points per side and grows to fit overflowing strokes
  (`src/remarkable_spec/render/engine.py:139-179`), but
  `render_rm_to_png` sizes its PNG from `screen.width` and `screen.height` alone
  (`src/remarkable_spec/ocr/pipeline.py:78-79`) — so the raster dimensions and the SVG's own aspect
  ratio stop agreeing as soon as any stroke overflows the page. Touching the padding logic changes
  what the OCR model sees.

## `DocumentType`, `DocumentMetadata`, `ContentInfo`, and `Document`

Defined at: `src/remarkable_spec/models/document.py:27` (`DocumentMetadata` at `:52`, `PageRef` at
`:166`, `ContentInfo` at `:190`, `Document` at `:307`)

The on-disk document contract: what the reMarkable's per-document JSON files mean. The docstring at
`src/remarkable_spec/models/document.py:1-13` lists the sidecar files by extension against a
per-document UUID stem.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `parse_metadata` / `parse_metadata_json` return `DocumentMetadata` via `from_json` | direct import | yes | `src/remarkable_spec/formats/metadata.py:28,36,60,73`, `src/remarkable_spec/models/document.py:108` |
| `parse_content` / `parse_content_json` return `ContentInfo` via `from_json` | direct import | yes | `src/remarkable_spec/formats/content.py:32,40,64,77`, `src/remarkable_spec/models/document.py:260` |
| `load_document` iterates `content.page_refs` and builds the `Document` | direct import | yes | `src/remarkable_spec/formats/document_loader.py:26,79-88,111-117` |
| `rmspec ls` compares `meta.doc_type` against `DocumentType.DOCUMENT` | direct import | yes | `src/remarkable_spec/cli/ls_cmd.py:50,173` |
| `rmspec tree` does the same | direct import | yes | `src/remarkable_spec/cli/tree_cmd.py:40,137` |
| `rmspec inspect` prints parsed metadata and content | direct import | likely | `src/remarkable_spec/cli/inspect_cmd.py:176,230` |
| `models/__init__.py` and root `__init__.py` re-exports — four of the 26 names in the root `__all__` | direct import | yes | `src/remarkable_spec/models/__init__.py:27-35`, `src/remarkable_spec/__init__.py:13-21,33-67` |
| `documents.doc_type` column, default `'DocumentType'` | indirect | likely | `src/remarkable_spec/sync/migrations.py:20`, `src/remarkable_spec/sync/models.py:23-26` |
| `rmspec search` and `rmspec device ls` compare the raw JSON string `"DocumentType"` instead of the enum | runtime dispatch | likely | `src/remarkable_spec/cli/search_cmd.py:155`, `src/remarkable_spec/cli/device_cmd.py:284` |

### Blast-radius notes

- **The enum has a parallel string universe that does not import it.** `DocumentType` is an
  `enum.Enum` (`src/remarkable_spec/models/document.py:27`) used by `ls` and `tree`
  (`src/remarkable_spec/cli/ls_cmd.py:173`, `src/remarkable_spec/cli/tree_cmd.py:137`), while
  `src/remarkable_spec/cli/search_cmd.py:155`, `src/remarkable_spec/cli/device_cmd.py:284,423`,
  `src/remarkable_spec/device/sync.py:189,375,509`, `src/remarkable_spec/sync/models.py:24`, and
  `src/remarkable_spec/sync/migrations.py:20` all compare or store the bare literal `"DocumentType"`.
  Renaming an enum member is a rename in nine string literals that no type checker will point you at.
- **`ContentInfo` and `DocumentMetadata` parse defensively, so a field you add is silently absent
  rather than an error.** Both are constructed through `from_json` classmethods
  (`src/remarkable_spec/models/document.py:108,260`) reached from
  `src/remarkable_spec/formats/metadata.py:73` and `src/remarkable_spec/formats/content.py:77`.
  Adding a field with no default breaks parsing of every real file on disk;
  adding one with a default parses but reads as the default forever, including for documents that do
  carry the value under a different key.
- **`Document` nests `list[Page]`, so it inherits the whole page surface.**
  `src/remarkable_spec/models/document.py:332` holds the pages and `:24` imports `Page`, so a `Page`
  field change is a `Document` field change. `Document` also carries six boolean computed properties
  (`:345-375`) whose only construction site is `load_document`
  (`src/remarkable_spec/formats/document_loader.py:111-117`) — nothing else in `src/` builds one, so
  the reach of a `Document`-only change is narrower than its import count suggests.

## `PenType` and `Pen`

Defined at: `src/remarkable_spec/models/pen.py:17` (`Pen` at `:78`, `PenType.canonical` at `:59`)

The pen enum is the key for two separate dispatch tables — physical pen parameters and per-segment
render formulas — and the two are keyed differently.

| Downstream | Type | Touch on change | Citation |
| --- | --- | --- | --- |
| `_convert_line` maps `rmscene`'s tool int onto `PenType`, falling back to `FINELINER_1` | direct import | yes | `src/remarkable_spec/formats/rm_file.py:26,159-163` |
| `Stroke.pen_type` field plus `is_eraser` / `is_highlighter` computed fields | direct import | yes | `src/remarkable_spec/models/stroke.py:21,96,121-129` |
| `get_pen_renderer` matches on `PenType.canonical(pen_type)` with a fineliner fallthrough | direct import | yes | `src/remarkable_spec/render/pens.py:23,437,456-457,476-480` |
| `Pen.from_stroke` matches on the raw pen type for base width, opacity, and linecap | direct import | yes | `src/remarkable_spec/models/pen.py:78`, `src/remarkable_spec/render/engine.py:22,261` |
| `rmspec inspect` prints per-stroke pen names | direct import | likely | `src/remarkable_spec/cli/inspect_cmd.py:40` |
| `models/__init__.py` and root `__init__.py` re-exports | direct import | yes | `src/remarkable_spec/models/__init__.py:37`, `src/remarkable_spec/__init__.py:23` |
| Cached diagram rows and `.ocr.txt` sidecars produced by earlier renders | indirect | no | `src/remarkable_spec/sync/migrations.py:66`, `src/remarkable_spec/cli/search_cmd.py:196-198` |

### Blast-radius notes

- **Adding an enum member without updating the alias map renders it as a fineliner, silently.**
  `PenType.canonical` collapses the `_2` toolbar variants through a hand-written dict
  (`src/remarkable_spec/models/pen.py:65-73`), and `get_pen_renderer` ends with
  `case _: return FinelineRenderer(base_width)` (`src/remarkable_spec/render/pens.py:478-480`). A new
  member that is in neither place produces output with no warning at all — the only warning in the
  chain fires earlier, for values the enum cannot construct
  (`src/remarkable_spec/formats/rm_file.py:161-163`).
- **The two dispatch tables disagree about aliasing.** `get_pen_renderer` canonicalises first
  (`src/remarkable_spec/render/pens.py:456`); `Pen.from_stroke` matches the raw value and falls through
  to `case _: return cls(pen_type=pen_type, base_width=thickness_scale)`
  (`src/remarkable_spec/models/pen.py:225-226`). So a `_2` variant gets the correct segment formulas
  but the generic base width, opacity, and linecap — not the ones its `_1` twin gets at
  `src/remarkable_spec/models/pen.py:200-206`. Adding a member to `_aliases` fixes render dispatch and
  does not fix `Pen`.
- **`Stroke.is_eraser` and `is_highlighter` have no consumers, so eraser strokes are drawn.** Both are
  computed fields (`src/remarkable_spec/models/stroke.py:121-129`) delegating to the classmethods at
  `src/remarkable_spec/models/pen.py:49,54`, and nothing outside `models` reads either one — the
  renderer never checks them (`src/remarkable_spec/render/engine.py:257-263`) and instead maps eraser
  types to `EraserRenderer` (`src/remarkable_spec/render/pens.py:476-477`). Erasure is rendered as a
  visible stroke rather than subtracted from what it erased. If you make the renderer honour
  `is_eraser`, every previously-exported artifact for a page with an eraser stroke changes.

## Other notable surfaces

- `resolve_document_full` and `ResolvedDocument` (`src/remarkable_spec/cli/_resolve.py:234,211`) — 7
  import statements, tied for 7th on the primary count and demoted by the stated tie-break because all
  four consumers are CLI commands (`src/remarkable_spec/cli/ocr_cmd.py:112`,
  `src/remarkable_spec/cli/diagram_cmd.py:129`, `src/remarkable_spec/cli/render_cmd.py:203`,
  `src/remarkable_spec/cli/annotations_cmd.py:118`). It owns the name/UUID/prefix lookup and the
  duplicate tie-break `CLAUDE.md:41` documents.
- `PenColor` (`src/remarkable_spec/models/color.py:17`) — 6 statements. Keys `Palette.get_rgb`, which
  returns black for any color missing from the palette (`src/remarkable_spec/render/palette.py:57-60`),
  so an added member renders black until the palette maps it.
- `Stroke` and `Point` (`src/remarkable_spec/models/stroke.py:84,24`) — 5 statements. `Point` is
  `frozen=True` (`:36`), so no consumer can mutate coordinates in place; the renderer reads
  `speed`, `direction`, `width`, `pressure` off the *second* point of each segment
  (`src/remarkable_spec/render/engine.py:275-296`), never the first.
- `Palette` and `EXPORT_PALETTE` (`src/remarkable_spec/render/palette.py:26`) — 5 statements, imported
  by all three exporters and the renderer as the default color source
  (`src/remarkable_spec/export/svg.py:15,62`).
- `rasterize_pdf_page` (`src/remarkable_spec/render/pdf_bg.py:15`) — 5 statements, all in CLI
  commands; the only PyMuPDF entry point and the source of every base64 background.
- `DeviceConnection` (`src/remarkable_spec/device/connection.py:38`) and `SyncManager`
  (`src/remarkable_spec/device/sync.py:31`) — 5 statements each. `SyncManager` is the largest single
  file in the repo at 573 lines and swallows per-document failures with a bare
  `except Exception: continue` (`src/remarkable_spec/device/sync.py:276-277`), which turns a fetch
  error into a silently skipped document.
- `ocr_page` and `ocr_image` (`src/remarkable_spec/ocr/vision.py:140,63`) — 5 statements. macOS-only;
  binds the Apple Vision framework, so this surface has no non-Darwin implementation.
- `hash_file` and `hash_document_files` (`src/remarkable_spec/sync/hasher.py:15,27`) — 3 statements,
  but it produces the `rm_hash` every cache row in the sync database is keyed on
  (`src/remarkable_spec/sync/models.py:55-58`). Changing the digest algorithm invalidates every cached
  row at once without deleting any of them.
- `migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:113`) — zero import statements and
  zero call sites. Routed here per the zero-consumer fallback: it is not load-bearing today, but the
  module docstring at `src/remarkable_spec/sync/migrations.py:1-6` advertises it as running
  automatically, so a reader will expect the sidecar import to have happened.

## See also

- [contract map](contract-map.md) — 41 shared source citations
- [business logic](business-logic.md) — 36 shared source citations
- [module map](../architecture/module-map.md) — 34 shared source citations
- [processes](../behavior/processes.md) — 31 shared source citations
- [tech debt](tech-debt.md) — 31 shared source citations
