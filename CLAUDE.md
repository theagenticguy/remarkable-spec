# remarkable-spec Development Guide

## Project

Python library + CLI for reMarkable Paper Pro tablets. Parses v6 `.rm` binary files, renders handwritten pages, runs OCR via Apple Vision + Textract + Opus 4.6, extracts Mermaid diagrams, and syncs over USB/SSH.

## Running

```bash
uv sync --all-extras          # install all optional dependencies
uv run rmspec --help           # CLI entry point
export RMSPEC_XOCHITL=/tmp/remarkable-data/xochitl  # local data dir
```

For OCR/diagram features, also need `boto3`:
```bash
uv add boto3    # not in extras — optional AWS dep
```

## Project Structure

```
src/remarkable_spec/
├── models/         # Pydantic v2: stroke, page, document, color, pen, screen
├── formats/        # Parsers: rm_file.py (rmscene), metadata, content, pagedata
├── render/         # SVG renderer: engine.py (core), pens.py (10 pen formulas), palette.py
├── ocr/            # pipeline.py, vision.py (Apple), textract.py (AWS), postprocess.py (LLM merge), diagram.py (Mermaid)
├── device/         # connection.py (SSH), web_api.py (HTTP), sync.py, push.py, paths.py
├── sync/           # db.py (SQLite), models.py, hasher.py, migrations.py
├── export/         # svg.py, png.py, pdf.py
└── cli/            # cyclopts commands, _util.py (settings), _resolve.py (doc lookup)
```

## Key Architecture Decisions

- **v6 .rm format**: CRDT-based, parsed by `rmscene` (v0.7.0). X origin is at center of page (not top-left). SVG renderer applies `x_shift = vw / 2` to compensate.
- **Pen physics**: 10 pen types with pressure/tilt/speed/direction formulas ported from rmc. Thickness multiplier (default 1.5) compensates for on-screen vs export weight difference.
- **Paper Pro screen**: 1620x2160 @ 229 DPI, 14 pen colors (PenColor enum 0-13).
- **OCR pipeline**: Render → parallel Vision + Textract → both texts + image → Opus 4.6 via Bedrock `invoke_model` (not `converse`). Model: `global.anthropic.claude-opus-4-6-v1`.
- **Sync DB**: SQLite at `~/.remarkable-spec/sync.db`. SHA-256 of .rm files (`rm_hash`) is the cache invalidation key for OCR and diagram results.
- **Document resolution**: Shared `cli/_resolve.py` — supports name substring, UUID, UUID prefix. On duplicates, picks by page count desc then lastModified desc.
- **DYLD auto-config**: `cli/_util.py` auto-sets `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` on macOS if not already set.
- **rmscene warnings suppressed**: `formats/rm_file.py` sets rmscene logger to ERROR level.

## Dependencies

Core: pydantic, rmscene, cyclopts, rich, pydantic-settings
Optional extras: `[render]` cairocffi/cairosvg/pillow, `[device]` paramiko/httpx, `[ocr]` pyobjc, `[push]` weasyprint/markdown, `[aws]` boto3

## Linting

```bash
uvx ruff check src/ --fix && uvx ruff format src/
```

Config: ruff line-length 99, python 3.12, select E/F/I/UP.
