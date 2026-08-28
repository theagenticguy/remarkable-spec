# remarkable-spec · Business logic

This file indexes the domain rules `remarkable-spec` enforces and points at the line that enforces
each one.

**What counts as business logic here.** This is a binary-file-format and device-protocol library, not
a CRUD application, so the domain rules are: v6 `.rm` format invariants, coordinate and DPI
conversion math, physical reMarkable device constants, the ten pen-physics formulas, document
resolution and change-detection policy, and cache-correctness rules. All of that is in scope.

**Out of scope.** Rich console formatting, cyclopts argument parsing and help text, and the wording of
LLM prompts — except where a prompt fixes a closed domain vocabulary, which is captured as a
validation. Database constraints are normally out of scope for an application-layer index, but they
are surfaced in Invariants here because the SQLite schema is the only place the uniqueness that makes
`rm_hash` a cache key is declared, and because it shapes what the application can and cannot cache.

**A theme worth reading first: silent defaults.** Most enforcement in this codebase is a fallback, not
a rejection. An unknown pen type becomes a fineliner, an unknown colour becomes black, an
undetectable screen becomes a reMarkable 2, an unparseable page becomes an empty page. Each such rule
carries its user-visible failure mode in the Failure mode column, because nothing raises and nothing
reports at exit status.

**No tests exist.** pytest is configured with `testpaths = ["tests"]` at `pyproject.toml:74-76`, and
the `tests/` directory holds exactly one file, a 0-byte `__init__.py`. No rule below is verified by a
test. Every claim traces to the source line named.

## Validations

| Rule | Domain | Citation | Failure mode |
| --- | --- | --- | --- |
| A stroke's pen tool ID must be a member of `PenType`; anything else becomes `FINELINER_1` | `.rm` parsing | `src/remarkable_spec/formats/rm_file.py:159-163` | Coerce, with a `logger.warning` — and that warning is invisible in practice because the module raises the `rmscene` logger to ERROR at import (`:31`) and never configures a handler for its own logger (`:33`); the stroke renders as a plain constant-width line |
| A stroke's colour ID must be a member of `PenColor`; anything else becomes `BLACK` | `.rm` parsing | `src/remarkable_spec/formats/rm_file.py:167-170` | Coerce with an unseen warning; the stroke renders black |
| A scene item that is neither `Line`, `Text`, `Group`, nor `GlyphRange` is dropped | `.rm` parsing | `src/remarkable_spec/formats/rm_file.py:149-153` | Silent drop at `logger.debug`; `GlyphRange` (text highlights) is discarded by design at `:149-151`, so highlighted typed text loses its highlight |
| `.metadata` `type` must be `DocumentType` or `CollectionType` | Document metadata | `src/remarkable_spec/models/document.py:116` with the enum at `:27-36` | Raises `ValueError` — this is one of the few strict validations in the codebase |
| `.content` `fileType` must be `notebook`, `pdf`, or `epub` | Document content | `src/remarkable_spec/models/document.py:266` with the enum at `:39-49` | Raises `ValueError` |
| `.metadata` `lastModified` and `lastOpened` must parse as integers | Document metadata | `src/remarkable_spec/models/document.py:120-121` | Raises `ValueError` — and this contradicts the sync path, which coerces the same field to 0 (see the Invariants table) |
| A document name argument resolves as full UUID, then 8-or-more-hex-char UUID prefix, then case-insensitive substring of `visibleName`, in that order | Document resolution | `src/remarkable_spec/cli/_resolve.py:82-102`, regexes at `:20-24` | No match prints an error and returns `None` at `:98-100`; every caller then exits 1, for example `src/remarkable_spec/cli/render_cmd.py:206-207` |
| A `.metadata` file whose `type` is `CollectionType` is excluded from document resolution candidates | Document resolution | `src/remarkable_spec/cli/_resolve.py:60-61` | Silent skip, so a folder can never be resolved as a document |
| A `.metadata` file that fails to parse as JSON is skipped during resolution | Document resolution | `src/remarkable_spec/cli/_resolve.py:55-58` | Silent `continue` on bare `except Exception`; the document becomes unaddressable with no message |
| A push file's extension must be in `{.pdf, .epub, .md, .mmd, .txt}` | Device push | `src/remarkable_spec/cli/sync_cmd.py:317-324` | Prints the supported list and exits 1 |
| A file handed to `SyncManager.sync_push_file` must be `.pdf` or `.epub` | Device push | `src/remarkable_spec/device/sync.py:484-486` | Raises `ValueError`; this is the second, library-level gate behind the CLI gate above |
| A file handed to `render_to_pdf` must have a registered renderer | Device push | `src/remarkable_spec/device/push.py:48-53`, renderer table at `:189-193` | Raises `ValueError` naming the supported extensions |
| A render output path's suffix must be `.png`, `.svg`, or `.pdf` | Render | `src/remarkable_spec/cli/render_cmd.py:141-144` and again at `:246-249` | Prints the supported list and exits 1 |
| An `--page` argument must satisfy `1 <= page <= len(rm_files)` | Render / OCR / diagram / annotations | `src/remarkable_spec/cli/render_cmd.py:223-228`, `src/remarkable_spec/cli/ocr_cmd.py:124-126`, `src/remarkable_spec/cli/diagram_cmd.py:141-143`, `src/remarkable_spec/cli/annotations_cmd.py:141-143` | Prints the valid range and exits 1; the same rule is written out four times |
| `rasterize_pdf_page` requires `0 <= page_index < len(doc)` | PDF background | `src/remarkable_spec/render/pdf_bg.py:41-42` | Raises `IndexError` naming the actual page count |
| `export_pdf` requires at least one page | Export | `src/remarkable_spec/export/pdf.py:54-55` | Raises `ValueError` |
| An inspect target's extension must be `.rm`, `.metadata`, `.content`, or `.pagedata` | Inspect | `src/remarkable_spec/cli/inspect_cmd.py:87-100` | Prints the supported list and exits 1 |
| `rmspec annotations` requires a PDF-backed document | Annotations | `src/remarkable_spec/cli/annotations_cmd.py:124-127` | Prints an error redirecting the user to `rmspec ocr` and exits 1 |
| An LLM page classification must be one of the closed vocabulary `TEXT` / `DIAGRAM` / `MIXED` | Diagram extraction | enum at `src/remarkable_spec/ocr/diagram.py:31-36`, prompt vocabulary at `:105-112` | Two different enforcement paths disagree: `classify_page` substring-scans and defaults to `TEXT` (`:138-142`), while `_parse_mermaid_response` calls `PageContentType(...)` directly on the captured group (`:267-270`) and therefore raises `ValueError` on any casing other than uppercase |
| Extracted Mermaid must start with a recognised diagram keyword when `mmdc` is unavailable | Diagram validation | `src/remarkable_spec/ocr/diagram.py:241-256` | Returns `(False, "…does not start with a recognized diagram type")`; the caller prints it and continues (`src/remarkable_spec/cli/diagram_cmd.py:196-199`) |
| A page is "annotated" only if its `.rm` file exists and is non-empty | Annotations | `src/remarkable_spec/cli/annotations_cmd.py:147-151`, re-checked at `:167-168` | Silent skip; a page with a 0-byte `.rm` stub — exactly what push creates at `src/remarkable_spec/device/sync.py:531-532` — is treated as unannotated |
| An SVG template that does not exist, or does not parse, is skipped | Render | `src/remarkable_spec/render/engine.py:336-343` | Silent `return`; the page renders on a plain white background with no message |
| A local search only considers documents whose `type` is `DocumentType` and which have a `.content` file | Search | `src/remarkable_spec/cli/search_cmd.py:155-165` | Silent `continue`. The `.metadata` read at `:150-153` is guarded but the `.content` read at `:167` is not, so one malformed `.content` file aborts the whole search with a traceback |

## Invariants

| Invariant | Where enforced | Citation |
| --- | --- | --- |
| **v6 `.rm` X coordinates are centre-origin, so every X must be shifted right by half the page width before emission.** This is the single most consequential invariant in the codebase; miss it and every page renders offset by half a width | Application code — the SVG renderer computes `x_shift = vw / 2` once and applies it to the padding scan and to both endpoints of every emitted segment | `src/remarkable_spec/render/engine.py:132-134`, applied at `:150` and `:303,305`; the same assumption is restated independently in the screen detector's comment at `src/remarkable_spec/models/screen.py:101` and in `CLAUDE.md:36` |
| A raster PDF background must be centred on the stroke origin, not on the viewport, or strokes land off the printed text | Application code | `src/remarkable_spec/render/engine.py:189-200`, offset computed at `:199` as `x_shift - bg_w / 2` |
| The viewBox must grow, per side, to contain any stroke that falls outside the screen rectangle, with a 30-point floor | Application code — a full scan of every point of every visible stroke precedes element emission | `src/remarkable_spec/render/engine.py:136-159`, floor at `:139`, viewBox composed at `:172-179` |
| Only visible layers are rendered; layers are painted bottom-to-top in list order | Application code, in two places | `src/remarkable_spec/render/engine.py:145-147` and `:207-209`; the ordering contract is stated at `src/remarkable_spec/models/page.py:53` and re-implemented in `Page.all_strokes` at `:144-146` |
| A stroke with fewer than two points emits nothing | Application code | `src/remarkable_spec/render/engine.py:257-259` — and `src/remarkable_spec/models/stroke.py:108-112` explicitly declares empty strokes valid, so single-tap dots are parsed and then silently dropped at render time |
| Every rendered segment width is clamped to a 0.1 floor before the global multiplier is applied | Application code | `src/remarkable_spec/render/engine.py:298-300` |
| `ScreenSpec`, `Pen`, `Point`, `RGB`, and `BuiltinTemplate` are immutable once constructed | Application code — Pydantic `ConfigDict(frozen=True)` | `src/remarkable_spec/models/screen.py:26`, `src/remarkable_spec/models/pen.py:90`, `src/remarkable_spec/models/stroke.py:36`, `src/remarkable_spec/models/color.py:67`, `src/remarkable_spec/models/template.py:32`; `Palette` is a frozen dataclass at `src/remarkable_spec/render/palette.py:25` |
| `rm_hash` — the SHA-256 of the `.rm` bytes — is the cache key, so an edited page produces a **new** row rather than updating the old one, and stale rows are never evicted | Both: the hash function in application code, the key uniqueness in the DB | `src/remarkable_spec/sync/hasher.py:15-24`, declared as "the cache invalidation key" at `src/remarkable_spec/sync/models.py:55-58`, enforced as `UNIQUE (rm_hash, engine)` at `src/remarkable_spec/sync/migrations.py:58` and `rm_hash TEXT NOT NULL UNIQUE` at `:66` |
| One OCR row per `(rm_hash, engine)` pair; one diagram row per `rm_hash` | DB constraint | `src/remarkable_spec/sync/migrations.py:58` and `:66`, upheld on write by `ON CONFLICT` clauses at `src/remarkable_spec/sync/db.py:198` and `:259` |
| A page row is unique on `(page_uuid, doc_uuid)`, and deleting a document cascades to its pages | DB constraint | `src/remarkable_spec/sync/migrations.py:42` and the foreign key at `:37`, with `PRAGMA foreign_keys=ON` set per connection at `src/remarkable_spec/sync/db.py:55` |
| The schema is created idempotently and the version row is written exactly once | Application code over DB DDL | `src/remarkable_spec/sync/migrations.py:99-110`, `SCHEMA_VERSION = 1` at `:13` |
| The same `lastModified` field is handled two incompatible ways: the sync path coerces a malformed value to 0, the model path raises | Application code, inconsistently | coercion at `src/remarkable_spec/device/sync.py:283-286` and again at `:367-370`, and at `src/remarkable_spec/cli/_resolve.py:66-69`; the raising path is `src/remarkable_spec/models/document.py:120-121` |
| "Trashed" means `parent == "trash"` **or** `deleted == true` — a two-clause rule implemented three separate times | Application code, triplicated | `src/remarkable_spec/models/document.py:377`, `src/remarkable_spec/cli/ls_cmd.py:194`, `src/remarkable_spec/cli/tree_cmd.py:131` |
| A page's template comes from the `.pagedata` line at that index if one exists, otherwise from the `.content` page reference | Application code | `src/remarkable_spec/formats/document_loader.py:83-88` |
| Page order is authoritative from `.content`: the `cPages.pages` array on firmware 3.x, falling back to the flat `pages` array | Application code — the same two-branch read is written out four times | `src/remarkable_spec/models/document.py:270-281`, `src/remarkable_spec/cli/_resolve.py:169-172`, `src/remarkable_spec/device/sync.py:361-364`, `src/remarkable_spec/cli/search_cmd.py:169-172` |
| A `.rm` file that fails to parse yields a page with zero layers rather than aborting the document load | Application code | `src/remarkable_spec/formats/document_loader.py:92-101` — logs a warning with a traceback and continues, so the page renders blank |
| Multi-page PDF export does not hold: after building one PDF per page, the function overwrites the output with the first page only | Application code — the invariant is stated in the docstring at `src/remarkable_spec/export/pdf.py:30-31` and broken by the body | `src/remarkable_spec/export/pdf.py:159-168`; the comment at `:166-167` concedes the merge is unimplemented. Callers are insulated today because `src/remarkable_spec/cli/render_cmd.py:395-401` always passes a single-page list, which takes the early return at `src/remarkable_spec/export/pdf.py:104` |
| The OCR cache is unreachable: `get_ocr`, `put_ocr`, `get_all_ocr`, `find_changed_pages`, and `migrate_ocr_sidecars` have no callers anywhere in `src/` | Nowhere — the invariant `CLAUDE.md:40` asserts is not upheld for OCR | definitions at `src/remarkable_spec/sync/db.py:174,192,216,319` and `src/remarkable_spec/sync/migrations.py:113`; `rmspec ocr` calls `transcribe_rm` unconditionally at `src/remarkable_spec/cli/ocr_cmd.py:96` and `:155`. `CLAUDE.md:40` says `rm_hash` "is the cache invalidation key for OCR and diagram results" — true only for diagrams (`src/remarkable_spec/cli/diagram_cmd.py:221-231,246-254`) |
| Screen auto-detection is not applied uniformly: the pipeline renderer detects, the Apple-Vision-only path hard-codes reMarkable 2 | Application code, inconsistently | detection at `src/remarkable_spec/ocr/pipeline.py:60` and `src/remarkable_spec/cli/render_cmd.py:159,309`; hard-coded `RM2_SCREEN` at `src/remarkable_spec/ocr/vision.py:161,172,186-187`, which is the path `rmspec search` uses via `src/remarkable_spec/cli/search_cmd.py:204` |
| The library and the CLI default to different screens when none is supplied | Application code, inconsistently | library default `RM2_SCREEN` at `src/remarkable_spec/render/engine.py:125-126`, `src/remarkable_spec/export/svg.py:63`, `src/remarkable_spec/export/png.py:64-65`, `src/remarkable_spec/export/pdf.py:79-80`; CLI default `PAPER_PRO_SCREEN` at `src/remarkable_spec/cli/render_cmd.py:353-354`, also used for unannotated pages at `:239` and `:276` |
| `RM_PALETTE` covers 13 of the 14 `PenColor` values; `PenColor.HIGHLIGHT` (ID 9) has no entry | Application code, by omission | enum of 14 at `src/remarkable_spec/models/color.py:17-41`, 13-entry table at `:89-103`; `Palette.get_rgb` returns black for a miss at `src/remarkable_spec/render/palette.py:57-60`. `CLAUDE.md:38` says "14 pen colors (PenColor enum 0-13)", which is true of the enum and not of the export palette |
| `PAPER_PRO_PHYSICAL` covers 9 of 14 values, so `PHYSICAL_PALETTE` renders `PINK`, `GRAY_OVERLAP`, `HIGHLIGHT`, `GREEN_2`, and `YELLOW_2` as black | Application code, by omission | table at `src/remarkable_spec/models/color.py:109-119`, palette bound at `src/remarkable_spec/render/palette.py:95` |
| `PenType` `_2` variants are aliases of their `_1` counterparts and must be canonicalised before any rendering decision | Application code | `PenType.canonical` at `src/remarkable_spec/models/pen.py:58-75`, called at `src/remarkable_spec/models/pen.py:146` and `src/remarkable_spec/render/pens.py:456` |

## Calculations

| Calculation | Inputs | Output | Citation |
| --- | --- | --- | --- |
| Points-per-pixel scale | `screen.dpi` | `72.0 / dpi` — 0.3186 for reMarkable 2, 0.3144 for Paper Pro | `src/remarkable_spec/models/screen.py:44-52`, recomputed inline at `src/remarkable_spec/render/engine.py:128` and `src/remarkable_spec/export/pdf.py:131` |
| Page size in PDF points | `screen.width`, `screen.height`, points-per-pixel | 447.29 x 596.39 pt for reMarkable 2; 509.34 x 679.13 pt for Paper Pro | `src/remarkable_spec/models/screen.py:54-64` |
| Page size in inches | `screen.width`, `screen.height`, `screen.dpi` | width / dpi, height / dpi | `src/remarkable_spec/models/screen.py:66-76` |
| Centre-origin X shift | viewport width in points | `vw / 2` — 223.65 pt for reMarkable 2, 254.67 pt for Paper Pro | `src/remarkable_spec/render/engine.py:134` |
| Per-side viewBox padding | every point of every visible stroke, PDF background size | four padding values, each at least 30 pt | `src/remarkable_spec/render/engine.py:136-167` |
| Raster output dimensions, pipeline path | `screen.width`, `screen.height`, `screen.dpi`, target `dpi` | `int(screen.width * dpi / screen.dpi)` by the same for height | `src/remarkable_spec/ocr/pipeline.py:78-79`, duplicated at `src/remarkable_spec/ocr/vision.py:186-187` |
| Raster scale, export path | target `dpi` | `dpi / 72.0` applied to a point-space SVG — algebraically the same ratio, but it also scales the padding, so the two paths do not produce identical pixel dimensions | `src/remarkable_spec/export/png.py:86,93-97` |
| PDF background rasterisation scale | target width/height in points, PDF page's native rect | `min(width_pt / page_w * 2, height_pt / page_h * 2)` — aspect-preserving, 2x oversampled | `src/remarkable_spec/render/pdf_bg.py:49-54` |
| Direction byte to tilt radians | `direction` (uint8, 0-255) | `direction * 2π / 255` | `src/remarkable_spec/render/pens.py:26-38`, duplicated as a model property at `src/remarkable_spec/models/stroke.py:67-81` |
| Normalised pressure | `pressure` (uint8, 0-255) | `pressure / 255.0` | `src/remarkable_spec/models/stroke.py:61-65` |
| Base width per pen type | `pen_type`, `thickness_scale` from the `.rm` header | one of eleven branch results plus a passthrough default | `src/remarkable_spec/models/pen.py:132-226` |
| Ten per-segment pen width formulas | `speed`, `direction`, `width`, `pressure`, `last_width` | segment width in screen units | `src/remarkable_spec/render/pens.py:136-435`, dispatched at `:437-480` |
| Per-segment opacity | `pressure` for pencil and mechanical pencil, constants for highlighter and shader, `1.0` otherwise | 0.0-1.0 | `src/remarkable_spec/render/pens.py:247-256`, `:279-288`, `:357-366`, `:391-400`, default at `:124-133` |
| Ballpoint ink saturation | `pressure`, base RGB | RGB darkened by up to 20 percent, clamped per channel to 0-255 | `src/remarkable_spec/render/pens.py:178-193` |
| Final SVG stroke width | segment width, global thickness multiplier, points-per-pixel scale | `max(0.1, seg_width) * thickness * scale` | `src/remarkable_spec/render/engine.py:298-307` |
| Stroke and layer bounding boxes | all point X/Y values | `(x_min, y_min, x_max, y_max)`, `(0,0,0,0)` when empty | `src/remarkable_spec/models/stroke.py:131-142`, `src/remarkable_spec/models/page.py:80-96` |
| Screen detection threshold | every point of every stroke | Paper Pro if `abs(x) > 702` or `y > 1872`, else reMarkable 2 | `src/remarkable_spec/models/screen.py:98-104` |
| Duplicate-document tie-break key | page count, `lastModified` | sort key `(len(page_uuids), last_modified)` descending | `src/remarkable_spec/cli/_resolve.py:134` |
| PDF page count for push | PDF bytes | PyMuPDF page count, else a regex count of `/Type /Page` occurrences, floored at 1 | `src/remarkable_spec/device/sync.py:437-454` |
| Average OCR confidence | per-line confidences | mean, or 0.0 when no lines were recognised | `src/remarkable_spec/ocr/vision.py:131`, `src/remarkable_spec/ocr/textract.py:69` — Textract's 0-100 score is divided by 100 first at `src/remarkable_spec/ocr/textract.py:51` to match Vision's 0-1 scale |
| Displayed page count | `.content` `pageCount`, page-reference list | `content.page_count or len(content.page_refs)` — the `or` means a genuine `pageCount` of 0 falls through to the derived count | `src/remarkable_spec/cli/ls_cmd.py:179`, `src/remarkable_spec/cli/tree_cmd.py:143`, and independently at `src/remarkable_spec/models/document.py:289` |
| PDF page index per annotated page | the `redir` value in `.content` `cPages`, positional index | 0-based PDF page index, falling back to position | `src/remarkable_spec/cli/_resolve.py:176-196`, consumed at `:270-281` |

**The pen formulas, in prose.** All ten take the same five inputs — raw `speed` (uint16), `direction`
(uint8, converted to radians as tilt), raw input `width` (uint16), `pressure` (uint8), and the
previous segment's computed `last_width` — and are ported from the rmc project as documented at
`src/remarkable_spec/render/pens.py:12-14`.

- **Fineliner** and **mechanical pencil** ignore all stylus input and return the base width
  (`:146-155`, `:268-277`). Mechanical pencil's base width is the *square* of `thickness_scale`
  (`src/remarkable_spec/models/pen.py:180`), so its slider response is quadratic, not linear.
- **Ballpoint**: `(0.5 + pressure/255) + width/4 - 0.5 * (speed/4/50)` — harder is wider, faster is
  thinner (`src/remarkable_spec/render/pens.py:167-176`).
- **Marker**: `0.9 * (width/4 - 0.4 * tilt) + 0.1 * last_width` — tilt widens, with 10 percent
  smoothing against the previous segment (`:205-215`).
- **Pencil**: `0.7 * (((0.8 * base_width) + 0.5 * pressure/255) * width/4 - 0.5 * sqrt(tilt) + 0.5 *
  last_width)` — the only formula taking a square root, and the only one carrying 50 percent
  smoothing (`:231-245`).
- **Paintbrush**: `0.7 * ((1 + 1.4 * pressure/255) * width/4 - 0.5 * tilt - speed/4/50)` — the
  strongest pressure coefficient of any pen (`:301-311`).
- **Calligraphy**: `0.5 * ((0.5 + pressure/255) * width/4 - 0.5 * tilt + 0.5 * last_width)`
  (`:321-331`).
- **Highlighter** and **shader** return constants — 15.0 units at 0.3 opacity and 12.0 units at 0.1
  opacity respectively (`:342-366`, `:376-400`), the same constants `Pen.from_stroke` assigns at
  `src/remarkable_spec/models/pen.py:200-212`.
- **Eraser** returns a constant width and overrides colour to pure white regardless of the stroke's
  recorded colour (`src/remarkable_spec/render/pens.py:413-434`). The eraser is therefore a painted
  white stroke, not a subtractive operation — erased content is covered, not removed, and remains in
  the parsed model.

**Formula-to-declaration drift.** `Pen` carries `pressure_sensitive`, `tilt_sensitive`, and
`speed_sensitive` booleans (`src/remarkable_spec/models/pen.py:115-129`) plus `base_opacity` and
`segment_length` (`:100-114`), all set per pen type at `:132-226`. The renderers never read any of
them: `SVGRenderer._render_stroke` consumes only `pen.base_width` and `pen.stroke_linecap`
(`src/remarkable_spec/render/engine.py:261-269`). Sensitivity is therefore encoded twice — once
declaratively on the model, once implicitly in each formula — and only the formula runs. Highlighter
and shader opacity happen to agree across both encodings; nothing enforces that they stay in
agreement.

## Policy and gates

- **Default page selection is the last page, not all pages:** `rmspec ocr` and `rmspec diagram`
  transcribe only the final page unless `--page` or `--all` is given, on the stated assumption that
  the newest notes are at the end. `src/remarkable_spec/cli/ocr_cmd.py:130-132`,
  `src/remarkable_spec/cli/diagram_cmd.py:147-149`. `rmspec annotations` inverts this and defaults to
  every annotated page: `src/remarkable_spec/cli/annotations_cmd.py:145-151`.
- **Diagram cache admission requires usable content:** a cached diagram row is accepted only if it
  carries `mermaid_code` or classifies the page as `TEXT`; a `DIAGRAM` row with a null
  `mermaid_code` is treated as a miss and the billable extraction re-runs.
  `src/remarkable_spec/cli/diagram_cmd.py:224`.
- **Cache failure is never fatal:** both the read and the write are wrapped so that a missing or
  broken sync database degrades to an uncached, billable run rather than an error.
  `src/remarkable_spec/cli/diagram_cmd.py:232-235` and `:255-256`.
- **PDF background compositing is opt-out, not opt-in:** a PDF-backed document composites its
  rasterised page beneath the strokes by default; `--no-pdf-bg` suppresses it.
  `src/remarkable_spec/cli/render_cmd.py:94-100`, honoured at `:216`, `:241`, and `:278`. `README.md:102`
  documents the flag.
- **Deleted and trashed documents are hidden by default:** `rmspec ls` filters them out unless
  `--deleted` is passed (`src/remarkable_spec/cli/ls_cmd.py:92-98,147-148`), while `rmspec tree` has no
  such flag and always excludes them (`src/remarkable_spec/cli/tree_cmd.py:130-132`).
- **SSH host keys are accepted without verification:** the client installs
  `paramiko.AutoAddPolicy()`, so any host answering at the configured address is trusted. Defensible
  for a USB link to `10.11.99.1`, unsafe over Wi-Fi, which `--host` permits.
  `src/remarkable_spec/device/connection.py:94`; the Wi-Fi path is advertised at
  `src/remarkable_spec/cli/device_cmd.py:96-98`.
- **SSH authentication precedence is key, then password, then agent:** a key path wins if supplied,
  otherwise a password, otherwise agent authentication is attempted.
  `src/remarkable_spec/device/connection.py:102-108`. The CLI supplies the key only if
  `~/.ssh/id_ed25519_remarkable` exists (`src/remarkable_spec/cli/device_cmd.py:111,119`,
  `src/remarkable_spec/cli/sync_cmd.py:44,93`), and a `--password` flag outranks the
  `RMSPEC_DEVICE_PASSWORD` setting (`src/remarkable_spec/cli/sync_cmd.py:92`).
- **Optional dependencies are gated at the call site, not at import:** every optional extra is
  imported lazily inside the function that needs it and converted into an install instruction on
  `ImportError` — `paramiko` at `src/remarkable_spec/device/connection.py:25-35`, `httpx` at
  `src/remarkable_spec/device/web_api.py:24-34`, Apple Vision and Quartz at
  `src/remarkable_spec/ocr/vision.py:39-60`, `boto3` at `src/remarkable_spec/ocr/textract.py:14-20`,
  `cairocffi` and `cairosvg` at `src/remarkable_spec/export/pdf.py:58-71`, `weasyprint` and `markdown`
  at `src/remarkable_spec/device/push.py:65-72`. This is what keeps `rmspec --help` startup free of
  optional imports.
- **Configuration precedence is explicit argument, then `RMSPEC_`-prefixed environment variable or
  `.env`, then default:** stated at `src/remarkable_spec/cli/_util.py:16-19`, with the settings model
  at `:13-60` and the xochitl-directory resolver implementing the three-step order at `:85-112`,
  including a final fallback to `~/.remarkable-spec/xochitl` only if that directory exists (`:108-111`).
- **`DYLD_FALLBACK_LIBRARY_PATH` is mutated at import time on macOS:** importing
  `remarkable_spec.cli._util` sets the variable to `/opt/homebrew/lib` when it is unset and that
  directory exists, so libcairo resolves without user setup. This is a process-wide side effect of an
  import. `src/remarkable_spec/cli/_util.py:67-72`, matching `CLAUDE.md:42`; `rmspec env` exports the
  same value for other tools at `src/remarkable_spec/cli/env_cmd.py:46-56`.
- **`rmscene` warnings are silenced process-wide at import time:** importing
  `remarkable_spec.formats.rm_file` raises the third-party `rmscene` logger to ERROR for the whole
  process, on the stated rationale that the v6 format outpaces the parser.
  `src/remarkable_spec/formats/rm_file.py:29-31`, matching `CLAUDE.md:43`.
- **Bedrock is called through `invoke_model` with a raw Anthropic body, never `converse`:** all three
  call sites build `{"anthropic_version": "bedrock-2023-05-31", …}` by hand.
  `src/remarkable_spec/ocr/postprocess.py:200-228`, `src/remarkable_spec/ocr/diagram.py:304-330`,
  `src/remarkable_spec/cli/annotations_cmd.py:272-297`, as `CLAUDE.md:39` states.
- **Model IDs, region, and inference parameters are hardcoded, not configurable:** the model string
  `global.anthropic.claude-opus-4-6-v1` appears as a literal in four files and `RmspecSettings` has no
  field for it. `src/remarkable_spec/ocr/postprocess.py:23`, `src/remarkable_spec/ocr/diagram.py:57`,
  `src/remarkable_spec/ocr/pipeline.py:90`, `src/remarkable_spec/cli/annotations_cmd.py:254`; settings
  fields enumerated at `src/remarkable_spec/cli/_util.py:29-60`.
- **Only the transcription call enables extended thinking:** OCR merge runs with
  `temperature: 1`, `max_tokens: 16384`, and a 10,000-token thinking budget
  (`src/remarkable_spec/ocr/postprocess.py:205-207`), then extracts the **last** text block to skip the
  thinking blocks (`:230-235`). Diagram extraction and annotation analysis run deterministically at
  `temperature: 0.0` with `max_tokens: 4096` and take the first block
  (`src/remarkable_spec/ocr/diagram.py:309-310,332`,
  `src/remarkable_spec/cli/annotations_cmd.py:276-277,299`).
- **Pushing a document restarts the device UI:** both push paths issue
  `systemctl restart xochitl` over SSH so the new document appears, which interrupts whatever the user
  is doing on the tablet. `src/remarkable_spec/device/sync.py:229` and `:552`.
- **Push pre-creates one empty `.rm` stub per PDF page:** page UUIDs are generated locally and
  `touch`ed on the device so xochitl can map PDF pages to annotation overlays.
  `src/remarkable_spec/device/sync.py:514-515,528-532`. Those stubs are 0 bytes, which is exactly what
  the annotations command treats as unannotated (`src/remarkable_spec/cli/annotations_cmd.py:150`).
- **A rendered push artifact is retained under `~/.remarkable-spec/cache/`:** the intermediate PDF is
  moved rather than deleted, keyed by display name, so a later annotations read can composite against
  it. Two pushes with the same name overwrite each other.
  `src/remarkable_spec/cli/sync_cmd.py:356-364`.
- **Sync-log writes are best-effort:** a failure to record the operation is swallowed so it cannot
  mask the operation's own result. `src/remarkable_spec/device/sync.py:422-431`,
  `src/remarkable_spec/cli/device_cmd.py:409-410` and `:436-437`.
- **A document whose metadata cannot be fetched or parsed is dropped from the sync comparison:** the
  bare `except Exception: continue` inside the device-metadata loop turns a transport failure into a
  document that silently never appears as changed. `src/remarkable_spec/device/sync.py:276-277`.
- **`device pull` refuses ambiguity while `_resolve` embraces it:** the device-side resolver exits 1
  on multiple name matches and demands a full UUID
  (`src/remarkable_spec/cli/device_cmd.py:365-370`), whereas the local resolver prints the candidates
  and proceeds with its tie-break winner (`src/remarkable_spec/cli/_resolve.py:132-138`). Folder
  lookup on push takes a third position and silently uses the first match
  (`src/remarkable_spec/cli/sync_cmd.py:301-307`).
- **`BUILTIN_TEMPLATES` is a heuristic, not a registry:** nine entries described as "the most
  frequently encountered templates in `.pagedata` files", with no lookup function and no fallback for
  an unlisted name. `src/remarkable_spec/models/template.py:138-150`. A page carrying any other
  template name still renders — the renderer only ever consumes an explicit `--background` SVG path
  (`src/remarkable_spec/cli/render_cmd.py:90-93`, `src/remarkable_spec/render/engine.py:317-350`) — so
  the template name is metadata the render path never resolves.
- **The Markdown push pipeline documented in `README.md:74-77` is one step, not three.** The README
  states that pushing a `.md` file automatically (1) renders ` ```mermaid ` blocks to inline PNGs via
  `mmdc`, (2) resolves image paths to base64 data URIs, and (3) converts to PDF via WeasyPrint.
  Only step 3 exists. `render_to_pdf` dispatches on extension through the table at
  `src/remarkable_spec/device/push.py:189-193`; `.md` routes to `_render_markdown` at `:58`, which calls
  `markdown.markdown(..., extensions=["tables", "fenced_code", "codehilite"])` at `:75` and hands the
  HTML straight to WeasyPrint at `:112`. `mmdc` is reachable only via `_render_mermaid` at `:116`,
  registered for `.mmd` at `:191`, and never invoked from the `.md` path. No base64 image embedding
  exists in that module at all. `README.md:23` and `README.md:130` repeat the same claim.
- **Device-side search covers less than the CLI implies:** `WebAPI.search` filters only root-level
  documents, because it calls `list_documents` rather than `list_all_documents`
  (`src/remarkable_spec/device/web_api.py:220`, versus the BFS traversal at `:90-110`), and matches on
  `VisssibleName` and `VisibleName` (`:225-226`) while the rest of the codebase also accepts the
  `VissibleName` spelling (`src/remarkable_spec/cli/device_cmd.py:229`). The `rmspec search --device`
  path bypasses this method entirely and posts to `/search/{keyword}` itself
  (`src/remarkable_spec/cli/search_cmd.py:88-91`), so `WebAPI.search` has no caller in the CLI.
- **Device-side folder traversal swallows failures:** a folder whose children cannot be listed is
  skipped, so its contents vanish from every listing built on `list_all_documents`.
  `src/remarkable_spec/device/web_api.py:101-107`.

## See also

- [contract map](contract-map.md) — 43 shared source citations
- [impact analysis](impact-analysis.md) — 36 shared source citations
- [processes](../behavior/processes.md) — 35 shared source citations
- [module map](../architecture/module-map.md) — 33 shared source citations
- [tech debt](tech-debt.md) — 33 shared source citations
