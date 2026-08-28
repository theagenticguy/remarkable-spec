# remarkable-spec · Processes

Every process in this codebase is initiated the same way: a `rmspec` subcommand dispatched by cyclopts. There are no HTTP routes, no RPC tool registrations, no cron or scheduler declarations, and no queue or topic consumers — the single console-script entry point is declared at `pyproject.toml:21` and resolves to the `cyclopts.App` built at `src/remarkable_spec/cli/__init__.py:40`. Eleven top-level commands are mounted onto it by literal-name registrations at `src/remarkable_spec/cli/__init__.py:48`, two of which (`sync`, `device`) are groups rather than leaves, giving 18 distinct entry points.

The eight processes below get full treatment. The remaining ten are listed under `## Minor flows`.

## rmspec ocr — handwriting transcription

Entry point: `src/remarkable_spec/cli/ocr_cmd.py:50`

1. Branch on the source argument: a `.rm` suffix goes straight to the pipeline, anything else is treated as a document name to look up `src/remarkable_spec/cli/ocr_cmd.py:90`.
2. Resolve the xochitl directory in order — the `--xochitl` flag, then `RMSPEC_XOCHITL` via pydantic-settings, then `~/.remarkable-spec/xochitl` if it exists `src/remarkable_spec/cli/_util.py:85`.
3. Resolve the name, UUID, or UUID prefix into a `ResolvedDocument` carrying ordered `.rm` paths, the backing PDF path, and a PDF page index per page `src/remarkable_spec/cli/_resolve.py:234`.
4. Choose target pages: `--page` selects one, `--all` selects every page, and the default selects only the last page `src/remarkable_spec/cli/ocr_cmd.py:123`.
5. For a PDF-backed page, rasterize the matching PDF page to a base64 PNG sized from the screen detected out of the stroke extents `src/remarkable_spec/cli/ocr_cmd.py:179`.
6. Render the page to a high-DPI PNG: parse the `.rm`, wrap the layers in a `Page`, detect the screen, write an intermediate SVG, then rasterize it with cairosvg and delete the SVG `src/remarkable_spec/ocr/pipeline.py:25`.
7. Run Apple Vision and AWS Textract concurrently on that PNG in a two-worker thread pool — the only concurrency in the codebase `src/remarkable_spec/ocr/postprocess.py:131`.
8. Send both OCR texts plus the original PNG to Claude Opus 4.6 through Bedrock `invoke_model`, with extended thinking enabled, and return the last text block of the response `src/remarkable_spec/ocr/postprocess.py:228`.

### Related

- `src/remarkable_spec/ocr/pipeline.py:86`
- `src/remarkable_spec/ocr/postprocess.py:110`
- `src/remarkable_spec/ocr/vision.py:63`
- `src/remarkable_spec/ocr/textract.py:23`
- `src/remarkable_spec/cli/ocr_cmd.py:207`
- `src/remarkable_spec/formats/rm_file.py:46`

## rmspec sync pull — incremental device pull

Entry point: `src/remarkable_spec/cli/sync_cmd.py:171`

1. Resolve the local xochitl mirror directory, exiting non-zero when none is configured `src/remarkable_spec/cli/sync_cmd.py:193`.
2. Open the SQLite sync database lazily, creating its parent directory, setting `journal_mode=WAL` and `foreign_keys=ON`, then applying the hand-written schema `src/remarkable_spec/sync/db.py:48`.
3. Build a device connection that prefers the key at `~/.ssh/id_ed25519_remarkable` and falls back to a password `src/remarkable_spec/cli/sync_cmd.py:81`, then open the paramiko SSH session and an SFTP channel `src/remarkable_spec/device/connection.py:81`.
4. Diff device against database: list every `.metadata` entry over SFTP, download each to a temp file, parse it, and compare its `lastModified` against the tracked value; documents tracked locally but absent on device are marked deleted `src/remarkable_spec/device/sync.py:233`.
5. For each changed document, download its metadata, content, pagedata, PDF or EPUB, its per-document page directory, and its thumbnails directory `src/remarkable_spec/device/sync.py:92`.
6. Hash the pulled files with SHA-256 and upsert the document row, reading page UUIDs from either the CRDT `cPages` shape or the flat `pages` list `src/remarkable_spec/device/sync.py:342`.
7. Upsert one page row per page UUID, recording the `.rm` SHA-256 that later serves as the cache-invalidation key for OCR and diagram results `src/remarkable_spec/device/sync.py:397`.
8. Append a `pull` row to the sync log. A per-document exception is caught, logged as an `error` row, and added to the skipped list rather than aborting the run `src/remarkable_spec/device/sync.py:420`.

### Related

- `src/remarkable_spec/sync/hasher.py:15`
- `src/remarkable_spec/sync/hasher.py:27`
- `src/remarkable_spec/sync/migrations.py:99`
- `src/remarkable_spec/sync/models.py:18`
- `src/remarkable_spec/device/connection.py:166`
- `src/remarkable_spec/cli/_util.py:75`

## rmspec sync push — file push to device

Entry point: `src/remarkable_spec/cli/sync_cmd.py:228`

1. Verify the file exists and that its suffix is one of `.pdf`, `.epub`, `.md`, `.mmd`, `.txt`, exiting on anything else `src/remarkable_spec/cli/sync_cmd.py:321`.
2. Resolve `--folder` to a parent UUID by listing documents over the device's HTTP web API and matching `CollectionType` entries case-insensitively, taking the first on a tie `src/remarkable_spec/cli/sync_cmd.py:291`.
3. Render a non-native type to a temporary PDF through a suffix-keyed renderer table — markdown plus weasyprint, the external `mmdc` binary, or monospace weasyprint `src/remarkable_spec/device/push.py:30`.
4. Count the PDF's pages with PyMuPDF, falling back to a regex scan for page objects when that fails `src/remarkable_spec/device/sync.py:438`.
5. Generate one page UUID per PDF page so the device can map PDF pages to future `.rm` overlay files `src/remarkable_spec/device/sync.py:515`.
6. Upload the file over SFTP, upload the generated `.metadata` and `.content` JSON from temp files, create the per-document page directory, and touch an empty `.rm` stub for every page UUID `src/remarkable_spec/device/sync.py:532`.
7. Restart the device's `xochitl` service so the new document appears in the library `src/remarkable_spec/device/sync.py:552`.
8. Record the document and a `push` row in the sync database, then move any rendered PDF into `~/.remarkable-spec/cache` for later annotation compositing `src/remarkable_spec/cli/sync_cmd.py:363`.

### Related

- `src/remarkable_spec/device/sync.py:456`
- `src/remarkable_spec/device/push.py:58`
- `src/remarkable_spec/device/push.py:116`
- `src/remarkable_spec/device/push.py:189`
- `src/remarkable_spec/device/web_api.py:90`
- `src/remarkable_spec/device/connection.py:183`

## rmspec diagram — Mermaid extraction from handwriting

Entry point: `src/remarkable_spec/cli/diagram_cmd.py:54`

1. Branch on the source: a `.rm` suffix extracts a single page directly, anything else resolves as a document name `src/remarkable_spec/cli/diagram_cmd.py:101`.
2. Choose target pages: `--page`, `--all`, or the last page by default `src/remarkable_spec/cli/diagram_cmd.py:140`.
3. Hash the `.rm` file and look the digest up in the sync database's diagram cache; a hit carrying Mermaid code, or classified `TEXT`, returns without any model call `src/remarkable_spec/cli/diagram_cmd.py:221`.
4. On a miss, render the page — with the PDF background composited when the document is PDF-backed — to a high-DPI PNG in a temp directory `src/remarkable_spec/ocr/diagram.py:201`.
5. Send the PNG to Claude Opus 4.6 through Bedrock `invoke_model` with the diagram-extraction prompt at temperature 0 `src/remarkable_spec/ocr/diagram.py:330`.
6. Parse `CONTENT_TYPE`, `DIAGRAM_TYPE`, and the fenced Mermaid block out of the response with three regexes, defaulting the classification to `TEXT` when the marker is missing `src/remarkable_spec/ocr/diagram.py:261`.
7. Write the result back to the diagram cache keyed by the `.rm` hash; a cache-write failure is swallowed and does not fail the command `src/remarkable_spec/cli/diagram_cmd.py:246`.
8. Optionally shell out to the external `mmdc` binary with a 30-second timeout to render the Mermaid to PNG, and optionally validate its syntax `src/remarkable_spec/cli/diagram_cmd.py:288`.

### Related

- `src/remarkable_spec/ocr/diagram.py:174`
- `src/remarkable_spec/ocr/diagram.py:213`
- `src/remarkable_spec/cli/diagram_cmd.py:202`
- `src/remarkable_spec/sync/db.py:237`
- `src/remarkable_spec/sync/db.py:253`
- `src/remarkable_spec/sync/models.py:88`

## rmspec annotations — PDF annotation read-back

Entry point: `src/remarkable_spec/cli/annotations_cmd.py:83`

1. Resolve the document and reject it unless `.content` reports `fileType` of `pdf` and the backing PDF exists on disk, pointing the user at `rmspec ocr` for notebooks `src/remarkable_spec/cli/annotations_cmd.py:124`.
2. Choose target pages: `--page` selects one, otherwise every page whose `.rm` file exists and is larger than zero bytes `src/remarkable_spec/cli/annotations_cmd.py:147`.
3. Map each page to its PDF page index through the `redir` field parsed out of the CRDT `.content` file, falling back to page order when `redir` is absent `src/remarkable_spec/cli/_resolve.py:176`.
4. Extract the PDF page's digital text with PyMuPDF — treated as ground truth against which handwriting is diffed `src/remarkable_spec/cli/annotations_cmd.py:177`.
5. Rasterize that PDF page to a base64 PNG at the detected screen's point dimensions, at 2x scale `src/remarkable_spec/render/pdf_bg.py:15`.
6. Render the page's strokes over that raster background into a composite PNG in a temp directory `src/remarkable_spec/cli/annotations_cmd.py:238`.
7. Send the composite image plus the digital text to Claude Opus 4.6 through Bedrock `invoke_model` at temperature 0, and return the first text block `src/remarkable_spec/cli/annotations_cmd.py:297`.
8. Emit one rich panel per page, or a single JSON array when `--json` is passed `src/remarkable_spec/cli/annotations_cmd.py:203`.

### Related

- `src/remarkable_spec/cli/annotations_cmd.py:209`
- `src/remarkable_spec/cli/annotations_cmd.py:48`
- `src/remarkable_spec/ocr/pipeline.py:25`
- `src/remarkable_spec/cli/_resolve.py:234`
- `src/remarkable_spec/models/screen.py:86`

## rmspec render — page export to SVG, PNG, or PDF

Entry point: `src/remarkable_spec/cli/render_cmd.py:58`

1. Branch on the source: a path ending `.rm` renders one page, anything else resolves as a document name against the xochitl directory `src/remarkable_spec/cli/render_cmd.py:112`.
2. Resolve the document and exit when it reports no pages `src/remarkable_spec/cli/render_cmd.py:212`.
3. Parse each page's `.rm` into layers and detect the screen spec from stroke extents; a page with no `.rm` file on disk becomes an empty `Page` on the Paper Pro spec so PDF-only pages still render `src/remarkable_spec/cli/render_cmd.py:302`.
4. Rasterize the backing PDF page as a base64 background, skipped entirely when `--no-pdf-bg` is passed `src/remarkable_spec/cli/render_cmd.py:313`.
5. Dispatch on the output suffix — `.svg`, `.png`, or `.pdf` — exiting on anything else, and surfacing an install hint when the `[render]` extra is missing `src/remarkable_spec/cli/render_cmd.py:340`.
6. Build the SVG document: a viewBox in PDF points, per-side padding computed by scanning every visible stroke's extents, and an `x_shift` of half the page width to compensate for the v6 format's page-centered X origin `src/remarkable_spec/render/engine.py:134`.
7. Emit one `<g>` element per visible layer and one `<line>` element per stroke segment, with width, color, and opacity computed from the per-pen formulas `src/remarkable_spec/render/engine.py:232`.
8. When the target is a directory rather than a file, repeat for every page, writing zero-padded filenames such as `page-01.svg` in the format given by `--format` `src/remarkable_spec/cli/render_cmd.py:283`.

### Related

- `src/remarkable_spec/export/svg.py:18`
- `src/remarkable_spec/export/png.py:19`
- `src/remarkable_spec/export/pdf.py:19`
- `src/remarkable_spec/render/pdf_bg.py:15`
- `src/remarkable_spec/models/screen.py:86`
- `src/remarkable_spec/formats/rm_file.py:46`

## rmspec search — notebook text search

Entry point: `src/remarkable_spec/cli/search_cmd.py:46`

1. Branch on `--device`: the device backend queries the tablet's USB web interface, the default backend runs local OCR over the xochitl mirror `src/remarkable_spec/cli/search_cmd.py:70`.
2. Device backend: POST the query to the tablet's `/search/` endpoint with a 10-second timeout, distinguishing a connection failure from a non-2xx status in the error message `src/remarkable_spec/cli/search_cmd.py:91`.
3. Local backend: enumerate every `DocumentType` metadata file in sorted order, skipping any whose JSON fails to parse and any excluded by `--doc` `src/remarkable_spec/cli/search_cmd.py:149`.
4. Read ordered page UUIDs from each `.content`, handling both the CRDT `cPages` shape and the flat `pages` list, and keep only pages whose `.rm` file exists `src/remarkable_spec/cli/search_cmd.py:169`.
5. For each page, reuse an existing `.ocr.txt` sidecar when one sits next to the `.rm` file — this is the cache that makes a repeat search cheap `src/remarkable_spec/cli/search_cmd.py:196`.
6. Otherwise render the page and run Apple Vision recognition on it, then write the recognized text back as the sidecar; a recognition failure skips that page with a warning `src/remarkable_spec/cli/search_cmd.py:204`.
7. Case-insensitive substring match the query against the recognized text, collecting document name, page number, UUID, and full text per hit `src/remarkable_spec/cli/search_cmd.py:212`.
8. Print a table with up to 30 characters of context on either side of the match, ellipsised where truncated `src/remarkable_spec/cli/search_cmd.py:238`.

### Related

- `src/remarkable_spec/cli/search_cmd.py:76`
- `src/remarkable_spec/cli/search_cmd.py:129`
- `src/remarkable_spec/ocr/vision.py:140`
- `src/remarkable_spec/ocr/vision.py:63`
- `src/remarkable_spec/cli/_util.py:85`

## rmspec ls — document inventory

Entry point: `src/remarkable_spec/cli/ls_cmd.py:73`

1. Resolve the xochitl directory from the positional argument, then the environment, then the default location, exiting with an export hint when none resolves and again when the path is not a directory `src/remarkable_spec/cli/ls_cmd.py:131`.
2. Glob `*.metadata` in sorted order and parse each into a `DocumentMetadata`; a parse failure skips that document silently `src/remarkable_spec/cli/ls_cmd.py:162`.
3. For `DocumentType` entries only, parse the sibling `.content` to read `fileType` and page count, recording `unknown` when the file is missing or unparseable `src/remarkable_spec/cli/ls_cmd.py:177`.
4. Treat an entry as deleted when either the `deleted` flag is set or its parent is the literal string `trash` `src/remarkable_spec/cli/ls_cmd.py:194`.
5. Drop deleted entries from the result set unless `--deleted` was passed `src/remarkable_spec/cli/ls_cmd.py:147`.
6. Render one of three outputs: JSON, a folder tree built by linking each entry to its parent UUID, or a table sorted folders-first then by lowercased name `src/remarkable_spec/cli/ls_cmd.py:150`.

### Related

- `src/remarkable_spec/formats/metadata.py:36`
- `src/remarkable_spec/formats/content.py:40`
- `src/remarkable_spec/models/document.py:27`
- `src/remarkable_spec/models/document.py:52`
- `src/remarkable_spec/cli/ls_cmd.py:158`
- `src/remarkable_spec/cli/_util.py:85`

## Minor flows

- rmspec sync (default) — entry at `src/remarkable_spec/cli/sync_cmd.py:48`. Forwards all five of its keyword arguments to `status`, so a bare `rmspec sync` is a read-only change report `src/remarkable_spec/cli/sync_cmd.py:72`.
- rmspec sync status — entry at `src/remarkable_spec/cli/sync_cmd.py:98`. Opens SSH plus the sync database and tabulates the `new_on_device` / `modified_on_device` / `deleted_on_device` diff produced at `src/remarkable_spec/device/sync.py:233`; step 4 of `rmspec sync pull` is the same call.
- rmspec sync log — entry at `src/remarkable_spec/cli/sync_cmd.py:368`. Reads the newest sync-log rows, default limit 20, and tabulates them with a direction arrow; touches no device `src/remarkable_spec/sync/db.py:298`.
- rmspec device info — entry at `src/remarkable_spec/cli/device_cmd.py:66`. Runs five separate shell commands over one SSH session for firmware, machine name, serial, memory, and disk usage `src/remarkable_spec/cli/device_cmd.py:121`.
- rmspec device ls — entry at `src/remarkable_spec/cli/device_cmd.py:162`. Lists documents over the HTTP web API rather than SSH, then renders a table or a folder tree `src/remarkable_spec/device/web_api.py:90`; on failure it suggests the SSH-based `rmspec sync pull` instead `src/remarkable_spec/cli/device_cmd.py:216`.
- rmspec device pull — entry at `src/remarkable_spec/cli/device_cmd.py:293`. Resolves the name or UUID over the HTTP web API, refusing to guess when more than one document matches `src/remarkable_spec/cli/device_cmd.py:365`, transfers over SSH, then records the pull in the sync database best-effort `src/remarkable_spec/cli/device_cmd.py:419`.
- rmspec device push — entry at `src/remarkable_spec/cli/device_cmd.py:443`. Forwards every argument to `rmspec sync push`, so one implementation serves both command paths `src/remarkable_spec/cli/device_cmd.py:496`.
- rmspec inspect — entry at `src/remarkable_spec/cli/inspect_cmd.py:47`. Dispatches on file suffix to one of four format parsers — `.rm`, `.metadata`, `.content`, `.pagedata` — and prints a summary of the parsed structure `src/remarkable_spec/cli/inspect_cmd.py:89`.
- rmspec tree — entry at `src/remarkable_spec/cli/tree_cmd.py:60`. Runs the same metadata-and-content scan as `rmspec ls` but always hides deleted and trashed documents `src/remarkable_spec/cli/tree_cmd.py:131`, and emits only a tree or JSON `src/remarkable_spec/cli/tree_cmd.py:112`.
- rmspec env — entry at `src/remarkable_spec/cli/env_cmd.py:27`. Prints shell `export` lines for the resolved xochitl path and device host, adding the macOS Homebrew cairo library path when running on Darwin `src/remarkable_spec/cli/env_cmd.py:47`.

## See also

- [contract map](../insights/contract-map.md) — 38 shared source citations
- [business logic](../insights/business-logic.md) — 35 shared source citations
- [impact analysis](../insights/impact-analysis.md) — 31 shared source citations
- [module map](../architecture/module-map.md) — 29 shared source citations
- [debugging guide](../insights/debugging-guide.md) — 29 shared source citations
