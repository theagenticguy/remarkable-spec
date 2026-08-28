# remarkable-spec · Module map

Eight packages live under `src/remarkable_spec/`, declared as the single distribution
`remarkable-spec` (`pyproject.toml:2`). Modules below are ordered by total tracked LOC, descending.

## cli

`cli` holds the package's only console-script entry point, `rmspec = "remarkable_spec.cli:app"`
(`pyproject.toml:21`), which resolves to the `cyclopts.App` constructed at
`src/remarkable_spec/cli/__init__.py:40`. That root app mounts 11 child apps by literal name —
`inspect`, `ls`, `render`, `tree`, `ocr`, `diagram`, `search`, `sync`, `device`, `annotations`, `env`
— in one registration block starting at `:48`, and each `*_cmd.py` module builds exactly one of them
(`src/remarkable_spec/cli/render_cmd.py:53`). Two files are shared infrastructure rather than
commands: `src/remarkable_spec/cli/_util.py:13` defines the seven-field `RmspecSettings` and
instantiates it as a module-level singleton at `:64`, while
`src/remarkable_spec/cli/_resolve.py:234` resolves a document from a name substring, a full UUID, or
a UUID prefix, breaking ties among duplicates at `:121`. Six further command modules fall outside
the eight files listed below — `ls_cmd.py`, `annotations_cmd.py`, `search_cmd.py`, `ocr_cmd.py`,
`tree_cmd.py`, and `env_cmd.py`, the last of which builds its sub-app at
`src/remarkable_spec/cli/env_cmd.py:22`.

- `src/remarkable_spec/cli/device_cmd.py` (504 LOC)
- `src/remarkable_spec/cli/sync_cmd.py` (425 LOC)
- `src/remarkable_spec/cli/render_cmd.py` (408 LOC)
- `src/remarkable_spec/cli/diagram_cmd.py` (376 LOC)
- `src/remarkable_spec/cli/inspect_cmd.py` (317 LOC)
- `src/remarkable_spec/cli/_resolve.py` (290 LOC)
- `src/remarkable_spec/cli/_util.py` (112 LOC)
- `src/remarkable_spec/cli/__init__.py` (72 LOC)

## models

`models` holds the Pydantic v2 data layer and is a dependency leaf — its package `__init__` imports
only from its own submodules (`src/remarkable_spec/models/__init__.py:20`) and re-exports 26 names
at `:47`. The domain types are `Document` with its metadata siblings
(`src/remarkable_spec/models/document.py:307`), `Page` and `Layer`
(`src/remarkable_spec/models/page.py:99`), `Stroke` and `Point`
(`src/remarkable_spec/models/stroke.py:84`), and the `PenType`/`Pen` pair
(`src/remarkable_spec/models/pen.py:17`). Two device screens are declared as constants rather than
read from configuration — `RM2_SCREEN` at 1404x1872 and 226 DPI, `PAPER_PRO_SCREEN` at 1620x2160 and
229 DPI (`src/remarkable_spec/models/screen.py:80`) — and `detect_screen` picks between them at parse
time by assuming Paper Pro whenever a stroke point exceeds reMarkable 2 bounds (`:86`). Color is
table-driven too, with `RM_PALETTE` for export and `PAPER_PRO_PHYSICAL` for on-panel appearance
(`src/remarkable_spec/models/color.py:89`).

- `src/remarkable_spec/models/document.py` (388 LOC)
- `src/remarkable_spec/models/pen.py` (226 LOC)
- `src/remarkable_spec/models/page.py` (157 LOC)
- `src/remarkable_spec/models/template.py` (150 LOC)
- `src/remarkable_spec/models/stroke.py` (142 LOC)
- `src/remarkable_spec/models/color.py` (119 LOC)
- `src/remarkable_spec/models/screen.py` (104 LOC)
- `src/remarkable_spec/models/__init__.py` (81 LOC)

## device

`device` is the module that reaches the physical tablet, and it is the only non-`cli` module that
imports `sync` (`src/remarkable_spec/device/sync.py:28`). `SyncManager` at `:31` is the high-level
surface, exposing `pull_all` at `:52`, `sync_status` at `:233`, `sync_pull` at `:303`, and
`sync_push_file` at `:456`. Beneath it, `DeviceConnection` wraps paramiko SSH and SFTP
(`src/remarkable_spec/device/connection.py:38`) and `WebAPI` wraps the USB web interface over httpx
(`src/remarkable_spec/device/web_api.py:37`); both default to the USB address `10.11.99.1`, declared
once as `DevicePaths.USB_IP` (`src/remarkable_spec/device/paths.py:43`) alongside the on-tablet
xochitl data path where each document is stored as a set of files named by a per-document UUID
(`:35`). `src/remarkable_spec/device/push.py:189` maps three source extensions — `.md`, `.mmd`,
`.txt` — to renderer functions so those formats can be converted to PDF before upload.

- `src/remarkable_spec/device/sync.py` (573 LOC)
- `src/remarkable_spec/device/connection.py` (231 LOC)
- `src/remarkable_spec/device/web_api.py` (227 LOC)
- `src/remarkable_spec/device/push.py` (193 LOC)
- `src/remarkable_spec/device/paths.py` (46 LOC)
- `src/remarkable_spec/device/__init__.py` (25 LOC)

## render

`render` turns a `Page` model into an SVG document, and the SVG path carries no third-party rendering
dependency (`src/remarkable_spec/render/__init__.py:11`).
`RenderEngine` is an abstract base declaring a single `render_page` method
(`src/remarkable_spec/render/engine.py:35`) and `SVGRenderer` is its only implementation (`:75`),
which compensates for the v6 format's center-origin X axis by offsetting every point by
`x_shift = vw / 2` (`:134`). Stroke appearance comes from `src/remarkable_spec/render/pens.py:437`,
where `get_pen_renderer` dispatches a `PenType` to one of ten concrete renderer classes that each
compute per-segment width, color, and opacity from pressure, tilt, speed, and direction. Color
lookup goes through two `Palette` constants, `EXPORT_PALETTE` and `PHYSICAL_PALETTE`
(`src/remarkable_spec/render/palette.py:92`), and `src/remarkable_spec/render/pdf_bg.py:15`
rasterizes a single PDF page through PyMuPDF so annotations can be composited over their original
background.

- `src/remarkable_spec/render/pens.py` (480 LOC)
- `src/remarkable_spec/render/engine.py` (379 LOC)
- `src/remarkable_spec/render/palette.py` (95 LOC)
- `src/remarkable_spec/render/pdf_bg.py` (61 LOC)
- `src/remarkable_spec/render/__init__.py` (54 LOC)

## ocr

`ocr` reads a rendered page three ways — Apple Vision, AWS Textract, and a Bedrock vision model
pointed straight at the image (`src/remarkable_spec/ocr/postprocess.py:110`) — then reconciles the
results. `:131` is the only concurrency site in the codebase, a
`ThreadPoolExecutor(max_workers=2)` that runs Apple Vision (`src/remarkable_spec/ocr/vision.py:63`)
and AWS Textract (`src/remarkable_spec/ocr/textract.py:23`) against the same rendered PNG before
handing both transcripts and the image to `merge_with_image`
(`src/remarkable_spec/ocr/postprocess.py:148`). That merge reaches Bedrock through a raw
`invoke_model` call rather than `converse`, wrapped in a private `_invoke_bedrock_vision` helper at
`:187` and pointed at a model ID hardcoded as a module constant at `:23`.
`src/remarkable_spec/ocr/pipeline.py:25` is a straight-line orchestrator holding no executor of its
own — `render_rm_to_png`, then `transcribe_rm` at `:86` — and
`src/remarkable_spec/ocr/diagram.py:145` reuses the same request shape to classify a page and extract
Mermaid source from it.

- `src/remarkable_spec/ocr/diagram.py` (332 LOC)
- `src/remarkable_spec/ocr/postprocess.py` (235 LOC)
- `src/remarkable_spec/ocr/vision.py` (190 LOC)
- `src/remarkable_spec/ocr/pipeline.py` (127 LOC)
- `src/remarkable_spec/ocr/textract.py` (71 LOC)
- `src/remarkable_spec/ocr/__init__.py` (7 LOC)

## sync

`sync` is the codebase's second dependency leaf: a stdlib `sqlite3` state store whose own imports
stay inside the package (`src/remarkable_spec/sync/__init__.py:13`), with no ORM and no migration
framework. `SyncDB` at `src/remarkable_spec/sync/db.py:26` connects lazily, sets
`PRAGMA journal_mode=WAL` at `:54` and `PRAGMA foreign_keys=ON` at `:55`, then calls `init_schema` on
first access at `:59`. The schema is a single SQL string literal at
`src/remarkable_spec/sync/migrations.py:15` creating six tables — `documents`, `pages`, `ocr_cache`,
`diagram_cache`, `sync_log`, and `schema_version` at `:17`, `:35`, `:49`, `:64`, `:77`, and `:93`
respectively — against a `SCHEMA_VERSION` of 1 at `:13`. `src/remarkable_spec/sync/hasher.py:15`
produces the SHA-256 digest used as `rm_hash`, the key on both cache tables, and
`src/remarkable_spec/sync/models.py:18` mirrors each table as a hand-maintained Pydantic model.

- `src/remarkable_spec/sync/db.py` (365 LOC)
- `src/remarkable_spec/sync/migrations.py` (151 LOC)
- `src/remarkable_spec/sync/models.py` (120 LOC)
- `src/remarkable_spec/sync/hasher.py` (63 LOC)
- `src/remarkable_spec/sync/__init__.py` (29 LOC)

## formats

`formats` parses the four file types found in a xochitl directory and assembles them into one
`Document` (`src/remarkable_spec/formats/__init__.py:10`).
`src/remarkable_spec/formats/rm_file.py:46` wraps the third-party `rmscene` v6 binary parser and
converts its scene tree into this package's own `Layer`, `Stroke`, and `Point` models; at import time
it silences rmscene process-wide with `logging.getLogger("rmscene").setLevel(logging.ERROR)` (`:31`),
and it degrades an unrecognized pen to `FINELINER_1` at `:162` and an unrecognized color to `BLACK`
at `:169`, logging a warning in each case. The two JSON sidecars each get a path-taking and a
dict-taking parser — `parse_content` and `parse_content_json`
(`src/remarkable_spec/formats/content.py:40`), `parse_metadata` and `parse_metadata_json`
(`src/remarkable_spec/formats/metadata.py:36`) — while the plain-text `.pagedata` has only the
path form, `parse_pagedata` (`src/remarkable_spec/formats/pagedata.py:26`). `load_document` at
`src/remarkable_spec/formats/document_loader.py:36` is the composition point that reads all of them
plus every per-page `.rm` file for one document UUID.

- `src/remarkable_spec/formats/rm_file.py` (212 LOC)
- `src/remarkable_spec/formats/document_loader.py` (117 LOC)
- `src/remarkable_spec/formats/content.py` (77 LOC)
- `src/remarkable_spec/formats/metadata.py` (73 LOC)
- `src/remarkable_spec/formats/pagedata.py` (48 LOC)
- `src/remarkable_spec/formats/__init__.py` (35 LOC)

## export

`export` is a thin adapter layer of three functions, one per output format, each delegating into
`render` (`src/remarkable_spec/export/__init__.py:16`). `export_svg` at
`src/remarkable_spec/export/svg.py:18` drives `SVGRenderer` directly and is the most widely consumed
of the three, imported at six sites spanning `ocr` (`src/remarkable_spec/ocr/pipeline.py:46`), `cli`
(`src/remarkable_spec/cli/render_cmd.py:357`), the other two exporters, and the package `__init__`.
`export_png` at `src/remarkable_spec/export/png.py:19` and `export_pdf` at
`src/remarkable_spec/export/pdf.py:19`
both produce SVG first and then rasterize or concatenate it — the delegating imports are at
`src/remarkable_spec/export/png.py:60` and `src/remarkable_spec/export/pdf.py:75` — which is why the
module docstring gates both behind the `[render]` optional-dependency extra while leaving SVG
dependency-free (`src/remarkable_spec/export/__init__.py:8`).

- `src/remarkable_spec/export/pdf.py` (168 LOC)
- `src/remarkable_spec/export/png.py` (112 LOC)
- `src/remarkable_spec/export/svg.py` (68 LOC)
- `src/remarkable_spec/export/__init__.py` (24 LOC)

## Supporting code

Two tracked files sit at the package root rather than inside any of the eight modules, and the root
`__init__.py` re-exports 26 names drawn from `models` and nothing else
(`src/remarkable_spec/__init__.py:33`), so the importable library surface is the data layer alone.

- `src/remarkable_spec/__init__.py` (67 LOC)
- `src/remarkable_spec/py.typed` (0 LOC)

## See also

- [contract map](../insights/contract-map.md) — 41 shared source citations
- [impact analysis](../insights/impact-analysis.md) — 34 shared source citations
- [business logic](../insights/business-logic.md) — 33 shared source citations
- [processes](../behavior/processes.md) — 29 shared source citations
- [tech debt](../insights/tech-debt.md) — 29 shared source citations
