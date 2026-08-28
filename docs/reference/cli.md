# remarkable-spec · CLI

The `rmspec` console script (`pyproject.toml:20-21`) is a cyclopts application (`src/remarkable_spec/cli/__init__.py:40-44`) that mounts 11 top-level commands (`:48-58`), two of which — `sync` and `device` — are groups rather than leaves, for 19 command paths in all.

## inspect

```
rmspec inspect [OPTIONS] PATH
```

Auto-detects a reMarkable file's type by extension and prints a summary of the parsed structure, accepting `.rm`, `.metadata`, `.content`, and `.pagedata`.

`src/remarkable_spec/cli/inspect_cmd.py:46`

Flags:

- `PATH` / `--path` — Path to the file to inspect (.rm, .metadata, .content, or .pagedata); required positional. `:48`.
- `--json` / `--no-json` — Output machine-readable JSON instead of rich formatting; defaults to false. `:55`.

## ls

```
rmspec ls [OPTIONS] [ARGS]
```

Scans a xochitl directory for `.metadata` files and lists every document with its name, type, page count, and last-modified timestamp.

`src/remarkable_spec/cli/ls_cmd.py:72`

Flags:

- `XOCHITL-DIR` / `--xochitl-dir` — Path to the xochitl directory (defaults to RMSPEC_XOCHITL env var); optional positional. `:74`.
- `--tree` / `--no-tree` — Display documents in a folder hierarchy tree; defaults to false. `:81`.
- `--json` / `--no-json` — Output machine-readable JSON instead of rich formatting; defaults to false. `:85`.
- `--deleted` / `--no-deleted` — Include deleted/trashed documents in output; defaults to false. `:92`.

## render

```
rmspec render [OPTIONS] SOURCE OUTPUT
```

Converts reMarkable stroke data to SVG, PNG, or PDF, taking either a `.rm` file path for a single page or a document name for a whole notebook.

`src/remarkable_spec/cli/render_cmd.py:57`

Flags:

- `SOURCE` / `--source` — Path to a .rm file, or a document name to look up in the xochitl directory; required positional. `:59`.
- `OUTPUT` / `--output` — Output file path (.svg/.png/.pdf) or directory (for batch rendering); required positional. `:65`.
- `--xochitl` — Path to the xochitl directory (defaults to RMSPEC_XOCHITL env var). `:72`.
- `--page` — Render a specific page (1-indexed). `:78`.
- `--thickness` — Stroke-width multiplier; literal default `1.5` in the signature. `:82`.
- `--dpi` — DPI for raster output; literal default `226` in the signature. `:86`.
- `--background` — Path to a background template SVG file. `:90`.
- `--no-pdf-bg` — Disable automatic PDF background compositing for PDF-backed documents; because the flag name already begins with `no-`, cyclopts generates the negation as `--no-no-pdf-bg`. `:94`.
- `--format` — Output format for batch rendering to a directory (svg, png, pdf); defaults to `svg`. `:101`.

Requires: `.png` and `.pdf` output need the `[render]` extra, `.svg` does not. `:12-14`.

## tree

```
rmspec tree [OPTIONS] [ARGS]
```

Displays all documents as a folder-hierarchy tree, matching the device's own "My Files" view.

`src/remarkable_spec/cli/tree_cmd.py:59`

Flags:

- `XOCHITL-DIR` / `--xochitl-dir` — Path to the xochitl directory (defaults to RMSPEC_XOCHITL env var); optional positional. `:61`.
- `--json` / `--no-json` — Output machine-readable JSON instead of rich formatting; defaults to false. `:68`.

## ocr

```
rmspec ocr [OPTIONS] SOURCE
```

Runs Apple Vision handwriting recognition over rendered reMarkable pages, for one page or an entire notebook.

`src/remarkable_spec/cli/ocr_cmd.py:49`

Flags:

- `SOURCE` / `--source` — Path to a .rm file, or a document name to look up; required positional. `:51`.
- `--xochitl` — Path to xochitl directory (defaults to RMSPEC_XOCHITL). `:56`.
- `--page` — Specific page number (1-indexed). `:60`.
- `--all` / `--no-all` — OCR all pages (not just the last); defaults to false. `:64`.
- `--save` / `--no-save` — Save recognized text as .txt sidecar files; defaults to false. `:68`.
- `--dpi` — Render DPI for OCR; literal default `300` in the signature, which is not the `226` that `render` defaults to. `:72`.
- `--thickness` — Stroke thickness multiplier; literal default `1.5`. `:76`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:80`.

Requires: the `[ocr]` and `[render]` extras. `:5-8`.

## diagram

```
rmspec diagram [OPTIONS] SOURCE
```

Sends rendered pages to Claude Opus 4.6 on Amazon Bedrock to detect handwritten diagrams and convert them to Mermaid syntax.

`src/remarkable_spec/cli/diagram_cmd.py:53`

Flags:

- `SOURCE` / `--source` — Path to a .rm file, or a document name to look up; required positional. `:55`.
- `--xochitl` — Path to xochitl directory (defaults to RMSPEC_XOCHITL). `:60`.
- `--page` — Specific page number (1-indexed). `:64`.
- `--all` / `--no-all` — Extract diagrams from all pages; defaults to false. `:68`.
- `--render` — Render extracted Mermaid to PNG via mmdc; takes an output path, so there is no `--no-render` negation. `:72`.
- `--validate` / `--no-validate` — Validate extracted Mermaid syntax; defaults to false. `:76`.
- `--save` / `--no-save` — Save .mmd sidecar files next to .rm files; defaults to false. `:80`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:84`.
- `--dpi` — Render DPI for extraction; literal default `300`. `:88`.
- `--thickness` — Stroke thickness multiplier; literal default `1.5`. `:92`.

Requires: the `[ocr]` and `[render]` extras, plus `boto3` for the Bedrock call. `:5-8`.

## search

```
rmspec search [OPTIONS] QUERY
```

Searches handwritten notes through one of two backends — the device's built-in handwriting search over its USB web interface, or local Apple Vision OCR followed by a text match.

`src/remarkable_spec/cli/search_cmd.py:45`

Flags:

- `QUERY` / `--query` — Text to search for in handwritten notes; required positional. `:47`.
- `--device` / `--no-device` — Search via device USB web interface instead of local OCR; defaults to false, so local OCR is the default backend. `:52`.
- `--doc` — Limit search to a specific document name. `:56`.
- `--xochitl` — Path to xochitl directory (defaults to RMSPEC_XOCHITL). `:60`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:64`.

Requires: `--device` needs the `[device]` extra and posts to `POST /search/{keyword}` on the tablet; the default local backend needs the `[ocr,render]` extras. `:5-11`.

## sync

```
rmspec sync COMMAND [OPTIONS]
```

Invoked with no subcommand, the group's own default handler reports sync status; it also hosts the four `status`, `pull`, `push`, and `log` subcommands documented below.

`src/remarkable_spec/cli/sync_cmd.py:47`

Flags:

- `--host` — Device hostname or IP; defaults to `settings.device_host`, so the value cyclopts prints is whatever `RMSPEC_DEVICE_HOST` resolved to at import time. `:50`.
- `--user` — SSH username; defaults to `settings.device_user`. `:54`.
- `--password` — SSH password. `:58`.
- `--xochitl` — Path to local xochitl directory. `:62`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:66`.

Requires: the `[device]` extra. `:5-8`.

## sync status

```
rmspec sync status [OPTIONS]
```

Compares the device against the local sync database and reports what changed since the last sync, without transferring anything.

`src/remarkable_spec/cli/sync_cmd.py:97`

Flags:

- `--host` — Device hostname or IP (default: 10.11.99.1 for USB). `:100`.
- `--user` — SSH username (default: root). `:104`.
- `--password` — SSH password (if not using key auth); falls back to `settings.device_password`. `:108`.
- `--xochitl` — Path to local xochitl directory. `:112`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:116`.

## sync pull

```
rmspec sync pull [OPTIONS]
```

Transfers only the documents whose content hash changed since the last sync, which is the incremental half of the sync pair.

`src/remarkable_spec/cli/sync_cmd.py:170`

Flags:

- `--host` — Device hostname or IP. `:173`.
- `--user` — SSH username. `:177`.
- `--password` — SSH password. `:181`.
- `--xochitl` — Path to local xochitl directory. `:185`.

Note: this leaf takes no `--json` flag, unlike `status` and `log`. `:171-188`.

## sync push

```
rmspec sync push [OPTIONS] FILE
```

Uploads a file to the device, accepting PDF and EPUB directly and converting Markdown, Mermaid, and plain text to PDF first.

`src/remarkable_spec/cli/sync_cmd.py:227`

Flags:

- `FILE` / `--file` — Path to the file to upload (PDF, EPUB, .md, .mmd, .txt); required positional. `:229`.
- `--name` — Display name on the device (default: filename stem). `:234`.
- `--folder` — Target folder name on device (resolves to UUID). `:238`.
- `--parent` — Parent folder UUID (use --folder for name lookup); defaults to the empty string. `:242`.
- `--host` — Device hostname or IP. `:246`.
- `--user` — SSH username. `:250`.
- `--password` — SSH password. `:254`.

Requires: Markdown, Mermaid, and text conversion needs the `[push]` extra on top of `[device]`. `:334`.

## sync log

```
rmspec sync log [OPTIONS]
```

Prints the sync history recorded in the local SQLite database.

`src/remarkable_spec/cli/sync_cmd.py:367`

Flags:

- `--limit` — Maximum number of log entries to show; defaults to `20`. `:370`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:374`.

## device

```
rmspec device COMMAND
```

Group for direct SSH access to a connected tablet, hosting the `info`, `ls`, `pull`, and `push` subcommands; it has no default handler and takes no flags of its own.

`src/remarkable_spec/cli/device_cmd.py:47`

Requires: the `[device]` extra, imported through a `paramiko` probe that `info`, `ls`, and `pull` each call before connecting at `:104`, `:204`, and `:331`; `push` does not call it, because it delegates. `:51-62`.

## device info

```
rmspec device info [OPTIONS]
```

Connects over SSH and reports the device's model, firmware version, and storage, read from system files and the xochitl configuration.

`src/remarkable_spec/cli/device_cmd.py:65`

Flags:

- `--host` — Device hostname or IP (default: 10.11.99.1 for USB). `:68`.
- `--user` — SSH username (default: root). `:72`.
- `--password` — SSH password (if not using key auth); falls back to `settings.device_password`. `:76`.
- `--json` / `--no-json` — Output machine-readable JSON; defaults to false. `:80`.

## device ls

```
rmspec device ls [OPTIONS]
```

Reads the xochitl directory over SSH and lists every document on the device with its name, type, and page count — the on-device counterpart to the local `rmspec ls`.

`src/remarkable_spec/cli/device_cmd.py:161`

Flags:

- `--host` — Device hostname or IP (default: 10.11.99.1 for USB). `:164`.
- `--user` — SSH username (default: root). `:168`.
- `--password` — SSH password (if not using key auth). `:172`.
- `--tree` / `--no-tree` — Display documents in a folder hierarchy tree; defaults to false. `:176`.
- `--json` / `--no-json` — Output machine-readable JSON; defaults to false. `:180`.

## device pull

```
rmspec device pull [OPTIONS] DOC-NAME DEST
```

Copies every file belonging to one named document — metadata, content, pages, thumbnails — from the device to a local directory.

`src/remarkable_spec/cli/device_cmd.py:292`

Flags:

- `DOC-NAME` / `--doc-name` — Name of the document to download (or UUID); required positional. `:294`.
- `DEST` / `--dest` — Local destination directory for the downloaded files; required positional. `:298`.
- `--host` — Device hostname or IP (default: 10.11.99.1 for USB). `:303`.
- `--user` — SSH username (default: root). `:307`.
- `--password` — SSH password (if not using key auth). `:311`.

## device push

```
rmspec device push [OPTIONS] FILE
```

Uploads a file to the device, rendering non-native types to PDF first; the handler is a thin alias that forwards all seven arguments to `rmspec sync push`, so the two commands share one implementation.

`src/remarkable_spec/cli/device_cmd.py:442`

Flags:

- `FILE` / `--file` — File to upload (PDF, EPUB, .md, .mmd, .txt); required positional. `:444`.
- `--name` — Display name on the device (default: filename stem). `:449`.
- `--folder` — Target folder name on device (resolves to UUID). `:453`.
- `--parent` — Parent folder UUID (use --folder for name lookup); defaults to the empty string. `:457`.
- `--host` — Device hostname or IP. `:461`.
- `--user` — SSH username. `:465`.
- `--password` — SSH password. `:469`.

Note: the body imports `sync_cmd.push` lazily and calls it with all seven parameters unchanged, and the two signatures carry identical defaults, so `rmspec device push` and `rmspec sync push` behave the same and differ only in help wording. `:493-504`.

## annotations

```
rmspec annotations [OPTIONS] SOURCE
```

For PDF-backed documents, reports per page what was added, crossed out, or marked up by comparing handwritten strokes against the original PDF text.

`src/remarkable_spec/cli/annotations_cmd.py:82`

Flags:

- `SOURCE` / `--source` — Document name or UUID to analyze; required positional. `:84`.
- `--xochitl` — Path to xochitl directory (defaults to RMSPEC_XOCHITL). `:89`.
- `--page` — Specific page number (1-indexed). `:93`.
- `--dpi` — Render DPI for analysis; literal default `300`. `:97`.
- `--thickness` — Stroke thickness multiplier; literal default `1.5`. `:101`.
- `--json` / `--no-json` — Output as JSON; defaults to false. `:105`.

Requires: the `[render]` and `[ocr]` extras plus `boto3`. `:12-15`.

## env

```
rmspec env [OPTIONS]
```

Prints the environment variables needed to run cairo- and weasyprint-backed tools outside the CLI, in a form suitable for `eval "$(rmspec env)"`.

`src/remarkable_spec/cli/env_cmd.py:26`

Flags:

- `--shell` / `--no-shell` — Emit `export KEY='value'` lines when true, bare `KEY=value` lines when false; defaults to true, and is the only parameter in the CLI declared without a `cyclopts.Parameter` annotation, so its help panel shows no description. `:29`.

Note: the variables emitted are `RMSPEC_XOCHITL` when a xochitl directory resolves, `RMSPEC_DEVICE_HOST` unconditionally, and `DYLD_FALLBACK_LIBRARY_PATH` on macOS when Homebrew's `lib` directory exists. `:39-56`.

## Environment settings

```
export RMSPEC_XOCHITL=~/remarkable-backup/xochitl
rmspec ls
```

Seven `RMSPEC_`-prefixed environment variables change command behaviour without appearing as flags, resolved by a pydantic-settings model that also reads a `.env` file from the working directory.

`src/remarkable_spec/cli/_util.py:13-60`

Flags:

- `RMSPEC_XOCHITL` — Path to the xochitl data directory; consulted after an explicit `--xochitl` flag and before the `~/.remarkable-spec/xochitl/` fallback. `:29-33`.
- `RMSPEC_DEVICE_HOST` — Device IP for SSH and HTTP access, default `10.11.99.1`; supplies the `--host` default on every `sync` and `device` command. `:34-38`.
- `RMSPEC_DEVICE_USER` — SSH username, default `root`; supplies the `--user` default. `:39-42`.
- `RMSPEC_DEVICE_PASSWORD` — SSH password, used only when `--password` is absent. `:43-46`.
- `RMSPEC_THICKNESS` — Declared stroke-thickness default of `1.5`, but read by no command: `rmspec render` hardcodes `1.5` in its own signature and nothing in `src/` references this field, so setting the variable has no effect. `:47-51`.
- `RMSPEC_DPI` — Declared raster-export default of `226`, and inert for the same reason: `rmspec render` hardcodes `226` at `src/remarkable_spec/cli/render_cmd.py:86-89` and no code reads this field. `src/remarkable_spec/cli/_util.py:52-55`.
- `RMSPEC_SYNC_DB` — Path to the SQLite sync database, defaulting to `~/.remarkable-spec/sync.db`. `src/remarkable_spec/cli/_util.py:56-60`.

Note: the prefix and `.env` filename are set by `SettingsConfigDict`, and the singleton is constructed once at import time, so a variable changed after the process starts is not re-read. `:22-27`, `:64`.

## See also

- [processes](../behavior/processes.md) — 14 shared source citations
- [contract map](../insights/contract-map.md) — 14 shared source citations
- [debugging guide](../insights/debugging-guide.md) — 14 shared source citations
- [impact analysis](../insights/impact-analysis.md) — 14 shared source citations
- [business logic](../insights/business-logic.md) — 13 shared source citations
