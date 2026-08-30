# remarkable-spec Development Guide

## Project

Python library + CLI for reMarkable Paper Pro tablets. Parses v6 `.rm` binary files, renders
handwritten pages, runs OCR, extracts Mermaid diagrams, and reads and writes documents over
USB and SSH.

**North star: humans and agents working on the same tablet while it is powered on, over USB.**
That sentence decides arguments. It is why USB is the default read path, why every command has
a `--json` envelope and a `--dense` mode, and why a write refuses rather than merges.

## Running

`mise.toml` is the **only** file in this repository that contains a command string. `lefthook.yml`
and CI both call `mise run <task>` and never spell one out — that is the mechanism that keeps the
three from drifting.

```bash
mise run install        # uv sync --all-packages --all-extras
uv run rmspec --help    # 14 invocations
uv run rmspec manifest --json   # the authoritative surface, machine-readable
mise run build          # the one wheel a user installs, out of all nine packages
```

Tasks: `install lint lint-check format format-check typecheck arch test-fast test test-cov
test-hardware agents-md agents-md-check bundle build check versions`.

`mise run check` is the definition of done: `lint-check`, `format-check`, `typecheck`, `arch`,
`agents-md-check`, `test-cov`.

## Project structure

Nine distributions in a uv workspace. The legacy single-package `src/remarkable_spec/` tree was
**deleted on 2026-08-30** — 10,321 lines, fully replaced. If you find a reference to it, it is
stale.

```
packages/
├── rmspec-domain/       models, ports (Protocols), the error tree, exit codes. Imports nothing.
├── rmspec-app/          use cases. May import rmspec.domain and NOTHING else.
├── rmspec-formats/      v6 .rm codec (rmscene), xochitl layout, page index
├── rmspec-render/       SVG renderer, ten pen-physics models, palette
├── rmspec-export/       raster + PDF composition (cairo, pymupdf, PIL)
├── rmspec-device/       USB web API (httpx) and SSH (paramiko), writeback
├── rmspec-persistence/  SQLite sync store, OCR/diagram caches, the device search index
├── rmspec-ocr/          recognizers and vision-language models
└── rmspec-cli/          cyclopts commands + the dishka composition root
```

`probes/` sits beside `packages/`: hand-run scripts that call a real paid service and print what
it answered. Outside `packages/`, and so outside the coverage floor, the architecture invariants
and the no-billable-calls rule — all three of which exist to keep the suite runnable offline, and
none of which a measurement of the real thing can honour.

Direction is machine-checked by `tests/architecture/test_dependency_direction.py`, which also
holds `OWNED_THIRD_PARTY`: each third-party module has exactly one package allowed to import it
(`rmscene`→formats, `sqlite3`→persistence, `boto3`/`Vision`→ocr, `httpx`/`paramiko`→device,
`cairocffi`/`PIL`/`fitz`→export, `cyclopts`/`rich`/`dishka`/`markdown`/`weasyprint`→cli).
Everything else goes through a port.

`packages/rmspec-cli/src/rmspec/cli/_container.py` is **the only module in the workspace that
names an adapter**, and a test asserts it stays that way. Every other `cli/*.py` reaches it
through `importlib.import_module`, because an AST scan forbids a static adapter import there —
including under `if TYPE_CHECKING`.

## Key architecture decisions

- **Nine distributions develop, one ships.** `rmspec-cli`'s wheel requires eight `rmspec-*`
  distributions that exist on no index, so it installs nowhere but this workspace.
  `mise run build` stages a tenth distribution, `rmspec`, out of `rmspec.cli._bundle`: all nine
  subpackages under one PEP 420 namespace, the **union** of their third-party requirements, the
  `rmspec` script. Staged into gitignored `build/` and derived from the nine manifests every
  time, so there is no second dependency list to go stale — and two members asking for one
  requirement with different specifiers is an error, not a silent choice. `rmspec --version`
  asks about `rmspec` then `rmspec-cli`, because only one of the two is ever installed.
- **USB is the default read path.** `GET /download/{id}/rmdoc` is served *by* xochitl, so it is
  a consistent snapshot by construction; reading `.rm` off disk over SSH is a torn read. But
  `SearchIndexSource` has **no USB binding and never will** — the firmware's route table is
  closed at six families and none serves a file from the xochitl tree — so a USB run still opens
  SSH for the search index and for tier-0 OCR.
- **`POST /upload` is create-only, root-only, and irreversible.** The import re-keys both
  document and page uuids, and no HTTP route deletes. `UsbUploader` sends `GET /documents/`
  immediately before the POST, because the route targets the last-listed folder.
- **v6 `.rm`**: CRDT-based, parsed by `rmscene` 0.7.0. X origin is the page centre, so the SVG
  renderer applies `x_shift = vw / 2`. Tombstones are normal (`item.value is None`), and
  `parent_id` is **not stable** across a xochitl re-save.
- **Highlighter colour lives in a field rmscene does not read.** Tag index 8, `Byte4`, a
  little-endian uint32 read as ARGB, on `SceneLineItem`. The header is a **two-byte varuint** —
  `(8<<4)|4 == 0x84` has its continuation bit set, so a one-byte header is impossible.
  `PenColor` collapses every highlighter to id 9, so without this the colour is always yellow.
  Measured: `#FFED75` and `#BEEAFE` on one real page.
- **rmscene's unread-data warning is scoped, not silenced.** `scene_codec.py` uses a
  reference-counted context manager that restores the prior level, so it cannot leak across a
  parallel randomised test run. Bytes nothing understands are recorded as
  `PageDefectCode.BLOCK_BYTES_UNREAD`; bytes we *do* decode are not, because calling them unread
  would be false.
- **OCR is tiered and all-OpenAI.** Tier 0 is the tablet's own handwriting index (free), tier 1
  Bedrock Data Automation by default (then Textract, then optionally Apple Vision), tier 2
  `global.openai.gpt-5.6-luna` reading the raster, tier 3 `global.openai.gpt-5.6-terra`
  adjudicating. Tier 0 and tier 1 agreeing above `RMSPEC_AGREEMENT_THRESHOLD` short-circuits
  tiers 2 and 3, and tier 0's text wins because it read the strokes rather than pixels.
- **BDA is the default tier-1 engine, and it needs configuration rather than only credentials.**
  Sync `InvokeDataAutomation`: bytes in, inline output, no S3 and no polling. Three things the AWS
  user guide does not say, all found by calling it — `dataAutomationConfiguration` is optional in
  the API model and mandatory in fact, the project must be `projectType: SYNC` (the console makes
  `ASYNC`), and a SYNC project accepts exactly one document text format. `RMSPEC_BDA_PROJECT_ARN`
  unset is refused at composition naming that setting, never on the first page.
  **Read `text_words`, never `text_lines`**: the line-level confidence came back as a constant
  `0.01` on every line of a measured page while its words ran 0.869 to 1.0, and the lowest word
  was exactly the one token the service misread. `probes/bda_sync_document.py` is where that was
  measured and the only place a billable call lives.
- **Cache key**: SHA-256 of the `.rm` bytes, plus render and raster digests, recognizers, model
  fingerprint and request digest. `OcrCache.equivalent_raster` exists because xochitl rewrites a
  page's bytes without changing the ink.
- **Writes refuse rather than merge.** `SshSceneWriter` captures the artifact's digest at read
  time, re-checks immediately before writing, and refuses on mismatch; writes go to a temp path
  and arrive by `mv -f` (SFTP's `SSH_FXP_RENAME` fails when the destination exists). A window of
  two round trips remains and the docstring says so.
- **Never restart xochitl casually.** Four starts per ten minutes routes to `emergency.target`,
  whose handler reboots the tablet. The one restart path checks `is-active`, arms a boot-id
  fence, runs `reset-failed` immediately before `restart`, and never retries.
- **stdout is the machine's, stderr is the human's.** `--json` emits
  `{api_version, type, data, degradations, next}`; `--dense` emits TSV; the default renders to
  stderr. `--pages` is **0-based**, matching `page_index` in every payload.

## Testing and gates

- Coverage floor is 90%; every package is at 100% *statement* coverage
  (`export/_cairo.py` has one partial branch, hence 99.99% overall). CI additionally requires
  **100% on changed lines** via `diff-cover`.
- `select = ["ALL"]` with eleven ignores, line length 99, numpydoc docstrings.
  **No `noqa`, no `type: ignore`, no threshold change** — never fix a gate by lowering it.
- `ty check --error-on-warning`: every diagnostic is an error.
- pytest runs with `filterwarnings = ["error"]`, `-n auto` and `pytest-randomly`.
- `mise run test-hardware` needs the tablet attached and never runs in CI.
- **No billable calls in tests.** Nothing may construct a `bedrock-runtime`, `textract` or
  `bedrock-data-automation-runtime` client, and **nothing may issue `POST /upload`** — it creates
  a document no route can delete. Every adapter takes its client factory as an injected argument
  precisely so a double reaches every branch. A measurement that genuinely needs the real service
  goes in `probes/`, which is outside `packages/` and therefore outside the coverage floor, the
  architecture invariants and this rule; it is run by hand and never by CI.
- **Never open the device config file.** `tests/architecture/test_secret_containment.py` fails
  the build if any file under `packages/` so much as names it or its credential keys, and it does
  not skip comments or docstrings.
- Do not run two coverage runs at once; they share the repo-root `.coverage` and corrupt each
  other. See the note in `mise.toml`.
