# remarkable-spec · Debugging guide

Something is broken. Where do you look first?

Two structural facts govern every answer below, and both are unusual enough to state up front.

**There are no tests to bisect with.** pytest is configured with `testpaths = ["tests"]`
(`pyproject.toml:75`) and `mise.toml:9` defines a `test` task, but `tests/` holds exactly one file, a
0-byte `tests/__init__.py` — verify with `wc -c tests/__init__.py`. The harness runs and validates
nothing. Every diagnosis path here is therefore a manual reproduction through the CLI or the library:
there is no failing test to point at a regression, and no coverage signal to tell you which paths have
ever executed.

**Silent degradation is the dominant failure shape.** The codebase declares zero custom exception
types — `grep -rn -E 'class \w*(Error|Exception)\b' src/` exits 1 — and carries 27 broad or bare
`except` handlers, 24 of them catching `Exception` outright
(`grep -rcn -E '^\s*except (Exception|BaseException)\s*(as \w+)?\s*:' src/`). The representative case
is a bare `except Exception: continue` in the device metadata loop
(`src/remarkable_spec/device/sync.py:276-277`). Three further failure paths are
`contextlib.suppress(Exception)` (`src/remarkable_spec/device/sync.py:422`,
`src/remarkable_spec/device/connection.py:125`, `src/remarkable_spec/device/connection.py:129`). The
consequence: the common symptom is **missing or wrong output, not a traceback**. A document that
should appear does not; a page renders blank; a sync transfers fewer files than expected. The
first-checks ladder below is ordered around that, not around reading stack traces.

## Failure-mode index

| Symptom | Likely surface | First check | Citation |
| --- | --- | --- | --- |
| `Error: No xochitl directory. Set RMSPEC_XOCHITL or pass --xochitl.` and exit 1 | The three-fallback resolver returned `None`: no `--xochitl` flag, no `RMSPEC_XOCHITL` in env or `.env`, and `~/.remarkable-spec/xochitl` is not a directory | Run `rmspec env`. If no `export RMSPEC_XOCHITL=...` line appears, none of the three fallbacks resolved — the emitter only prints that line when the resolver returns non-`None` | `src/remarkable_spec/cli/_util.py:104-112`; guard at `src/remarkable_spec/cli/ocr_cmd.py:104-109`; `env` emitter at `src/remarkable_spec/cli/env_cmd.py:39-41` |
| `ImportError` naming a package plus an install hint, for example `PNG rendering requires cairosvg. Install with: uv add 'remarkable-spec[render]'` | One of 16 lazy-import guards. Optional extras are imported inside functions, not at module top, so the failure lands at call time rather than at CLI startup | Read the hint — it names the extra verbatim. Cross-check against the extras table in `pyproject.toml:23` | `src/remarkable_spec/ocr/pipeline.py:51-56`; `src/remarkable_spec/export/pdf.py:58-71`; `src/remarkable_spec/device/connection.py:27-35` |
| `[render]` extra is installed, yet cairo still fails to load on macOS | The import-time `DYLD_FALLBACK_LIBRARY_PATH` mutation is guarded by `"DYLD_FALLBACK_LIBRARY_PATH" not in os.environ`, so it is skipped entirely when the variable is already set to something that lacks `libcairo` | `echo $DYLD_FALLBACK_LIBRARY_PATH`. If it is set and does not contain `/opt/homebrew/lib`, the auto-config never ran; `rmspec env` reports the same value it would have used | `src/remarkable_spec/cli/_util.py:69-72`; the `env` mirror at `src/remarkable_spec/cli/env_cmd.py:47-56` |
| The wrong document was rendered or transcribed, preceded by `Multiple matches for '<name>'` in yellow | Substring name matching hit several documents. The tie-break sorts by page count descending then `lastModified` descending, prints the candidates, and **continues with the winner** rather than stopping | Re-run with the full 36-character UUID, which takes the exact-match branch before substring matching is reached | `src/remarkable_spec/cli/_resolve.py:132-138`; exact-UUID branch at `src/remarkable_spec/cli/_resolve.py:83-87` |
| `Error: No document matching '<name>'.` for a document you can see on the device | The candidate list is built only from `*.metadata` files in the top level of the xochitl directory, and any file whose JSON fails to parse is dropped by a bare `except Exception: continue` with no message | `rmspec ls` — it applies the same silent-skip rule (`src/remarkable_spec/cli/ls_cmd.py:165-168`), so a document absent from both listings has unparseable metadata rather than a name mismatch | `src/remarkable_spec/cli/_resolve.py:54-58`; page list from `.content` at `src/remarkable_spec/cli/_resolve.py:164-167` |
| Strokes render in the wrong colour or at a uniform weight | An unrecognised pen type or colour value in the `.rm` file fell back to `FINELINER_1` / `BLACK`. Each fallback emits a `logger.warning` and nothing else | `rmspec inspect <page>.rm --json` and read the `pen_types` and `colors` arrays. `FINELINER_1` or `BLACK` where you expect another value is the fingerprint | `src/remarkable_spec/formats/rm_file.py:159-170`; the JSON report at `src/remarkable_spec/cli/inspect_cmd.py:128-129` |
| Rendered page is the wrong physical size, or content sits off-centre | `detect_screen` infers the device purely from stroke extents: it returns Paper Pro only if some point exceeds half the rM2 width or the full rM2 height, otherwise rM2. A sparsely annotated Paper Pro page whose strokes all fit inside rM2 bounds is misclassified | Render to `.svg` (no extra dependencies needed) and read the root dimensions. They derive from the detected spec as `screen.width * 72 / screen.dpi` — 447.4 x 596.6 pt for rM2 1404x1872 @226, 509.4 x 679.0 pt for Paper Pro 1620x2160 @229 | `src/remarkable_spec/models/screen.py:98-104`; specs at `src/remarkable_spec/models/screen.py:80-83`; viewport maths at `src/remarkable_spec/render/engine.py:128-134` |
| `rmspec sync pull` reports fewer documents than the device holds, with no error line for the missing ones | The device metadata loop wraps `get_file` plus `json.loads` in `except Exception: continue`. A single SFTP or parse failure removes that document from the change set with no log entry, no `sync_log` row, and no exit code change | Compare the count of `.metadata` entries the device reports against the number of rows `rmspec sync log --json` shows for the run. Documents dropped here appear in neither | `src/remarkable_spec/device/sync.py:271-279`; the change-set return at `src/remarkable_spec/device/sync.py:301` |
| `rmspec sync pull` prints `Skipping <name>` and continues | The per-document `except Exception` inside `sync_pull` — unlike the metadata loop above, this one does record an `error` row in `sync_log` and appends to the `skipped` list | `rmspec sync log --json` and read the `details` field, which carries `str(exc)` | `src/remarkable_spec/device/sync.py:420-433`; reader at `src/remarkable_spec/cli/sync_cmd.py:379-381` |
| `Error: mmdc not found. Install mermaid-cli: npm install -g @mermaid-js/mermaid-cli`, or a rendering timeout | One of three `subprocess.run(["mmdc", ...])` call sites. Each has both a `FileNotFoundError` and a `subprocess.TimeoutExpired` branch; timeouts are 10 s for validation and 30 s for rendering | `command -v mmdc`. `mmdc` is an external npm binary and is in no Python extra, so a fully-installed `[all]` environment still lacks it | `src/remarkable_spec/cli/diagram_cmd.py:288-304`; `src/remarkable_spec/ocr/diagram.py:231-258`; `src/remarkable_spec/device/push.py:128-141` |
| `validate_mermaid` reported valid code that `mmdc` then rejects | When `mmdc` is absent, validation degrades to a prefix check against nine diagram-type keywords and returns `(True, "")` on a match. Nothing in the return value distinguishes a real `mmdc` pass from the keyword fallback | `command -v mmdc` before trusting a validation result | `src/remarkable_spec/ocr/diagram.py:241-256` |
| An unhandled `botocore` traceback — `NoCredentialsError`, `AccessDenied`, `ExpiredTokenException` | None of the four `boto3.client` call sites wrap the client construction or the API call. AWS failures propagate raw. The region is a function-parameter default, not a setting: `RmspecSettings` has no region field | `aws sts get-caller-identity`, then confirm the region default matches where you have Bedrock and Textract access. `region` reaches Bedrock as `region_name` with no environment override in this codebase | `src/remarkable_spec/ocr/textract.py:36-40`; `src/remarkable_spec/ocr/postprocess.py:200`; `src/remarkable_spec/ocr/diagram.py:304`; `src/remarkable_spec/cli/annotations_cmd.py:272`; settings shape at `src/remarkable_spec/cli/_util.py:29-60` |
| Repeating `rmspec ocr` on an unchanged page bills Textract and Bedrock again | The `ocr_cache` table and its three accessors exist but have zero callers. `rmspec ocr` writes an `.ocr.txt` sidecar only under `--save` and never reads one back. Only `rmspec search` reads those sidecars | `sqlite3 ~/.remarkable-spec/sync.db "select count(*) from ocr_cache"` before and after a repeat run — the count does not move, and no read occurs. Contrast with `rmspec diagram`, which does check its cache | `src/remarkable_spec/sync/db.py:174`; `src/remarkable_spec/sync/db.py:192`; sidecar write at `src/remarkable_spec/cli/ocr_cmd.py:164-167`; the only sidecar reader at `src/remarkable_spec/cli/search_cmd.py:196-198` |
| `Error: Failed to connect to reMarkable at 10.11.99.1: <cause>` | `DeviceConnection.connect` catches every paramiko exception — auth failure, refused connection, host unreachable — and re-raises one `ConnectionError` with the original as `__cause__`. The CLI then prints only the message and exits 1, so the discriminating exception type is lost from the output | Read the `<cause>` tail of the message; that substring is the paramiko exception. Then confirm the target: the default host is a settings field, so `RMSPEC_DEVICE_HOST` may be pointing elsewhere | `src/remarkable_spec/device/connection.py:112-117`; CLI handler at `src/remarkable_spec/cli/sync_cmd.py:137-139`; host default at `src/remarkable_spec/cli/_util.py:34-38` |
| `Error: Failed to connect to device web API: <exc>` from `rmspec device ls` or `rmspec device pull` | The `WebAPI` path is HTTP to the tablet's USB web interface, which is a separate switch from SSH and can be off while SSH works. This handler is a broad `except Exception`, so a JSON-decode failure and a refused connection look identical | The error text itself names the remedy path: the USB web interface toggle. SSH-based commands are unaffected | `src/remarkable_spec/cli/device_cmd.py:211-222`; second site at `src/remarkable_spec/cli/device_cmd.py:340-345` |
| Background template silently absent from a rendered SVG | `_add_template` returns early and without a message on both a missing file and an XML parse error | Confirm the path exists and parses as XML. Neither condition produces output | `src/remarkable_spec/render/engine.py:336-343` |
| A document lists pages but every page renders blank | `document_loader` catches all `.rm` parse failures per page and continues with an empty layer list, emitting a `logger.warning` with `exc_info=True` | Watch stderr — that warning is the only signal, and it carries the traceback | `src/remarkable_spec/formats/document_loader.py:92-101` |
| `rmspec ls` or `rmspec tree` shows a file type of `unknown` | The `.content` parse failed, or the file is absent. Both collapse to the same `unknown` string through a broad `except` | `rmspec inspect <uuid>.content` — it runs the same parser without the swallowing handler | `src/remarkable_spec/cli/ls_cmd.py:174-183`; `src/remarkable_spec/cli/tree_cmd.py:138-147` |

## Log and error surfaces

There is no log file, no log-level flag, no structured logging, and no observability platform in this
codebase. That is a measured result, not an assumption:
`grep -rn -iE 'structlog|loguru|FileHandler|StreamHandler|--verbose|--debug|log_level|LOG_LEVEL|logfile|log_file|sentry|datadog|opentelemetry' src/ --include='*.py'`
exits 1 with zero hits. `logging.basicConfig` appears nowhere either. The section below is therefore
mostly stdout, stderr, and one SQLite table — which is itself the actionable finding: **there is no
retrospective record of a failed render, parse, or OCR run, only of a failed sync.**

| Surface | Where it emits | What to grep for | Citation |
| --- | --- | --- | --- |
| Rich console, user-facing errors | **stdout**, not stderr. All 11 `Console()` instances across the CLI modules are default-constructed; `grep -rn 'stderr' src/` finds no `Console(stderr=True)`. Error text is interleaved with normal output and cannot be separated by stream | `Error:` — the literal prefix on every guard clause, emitted as `[red]Error:[/red]`. 55 `sys.exit` calls follow these | `src/remarkable_spec/cli/env_cmd.py:23`; representative guard at `src/remarkable_spec/cli/render_cmd.py:191-197` |
| `logging` module loggers | **stderr**, through `logging.lastResort`. Root logger has no handlers, so `WARNING` and above reach stderr as a bare message with no timestamp, level, or module prefix. Anything below `WARNING` is discarded | `Unknown pen type`, `Unknown color`, `Invalid metadata JSON`, `Invalid content JSON`, `Skipping`, `Failed to parse .rm file` | `src/remarkable_spec/formats/rm_file.py:33`; `src/remarkable_spec/formats/document_loader.py:29`; `src/remarkable_spec/device/sync.py:25` |
| Suppressed third-party logger | Nothing is emitted. `rmscene` is pinned to `ERROR` at import time of the parser module, process-wide, for any process that imports it — including a library consumer that never wanted the suppression | `getLogger("rmscene")` — one site, and no flag reverses it | `src/remarkable_spec/formats/rm_file.py:31` |
| Unreachable debug channel | Nothing is emitted. `logger.debug` for unknown scene-item types is below the `lastResort` threshold and there is no flag or environment variable in this codebase that installs a handler | `Skipping unknown scene item type` — present in source, unreachable at runtime | `src/remarkable_spec/formats/rm_file.py:153` |
| `sync_log` SQLite table | `~/.remarkable-spec/sync.db`, or the path in `RMSPEC_SYNC_DB`. The only durable, queryable error record in the system. Columns: `direction`, `doc_uuid`, `doc_name`, `pages_transferred`, `status`, `details`, `device_host`, `timestamp` | `status = 'error'`, then read `details`, which holds `str(exc)` from the raising site | `src/remarkable_spec/sync/migrations.py:77-87`; writer at `src/remarkable_spec/sync/db.py:281`; reader at `src/remarkable_spec/sync/db.py:298-301` |
| `rmspec sync log --json` | stdout as JSON, newest first, default 20 rows. The intended read path for the table above | `"status": "error"` and the adjacent `"details"` field | `src/remarkable_spec/cli/sync_cmd.py:367-398` |
| `rmspec env` | stdout as shell `export` statements. Reports the resolved xochitl directory, device host, and macOS DYLD path — and **omits** the xochitl line entirely when resolution fails, which is the diagnostic | An absent `RMSPEC_XOCHITL` line; a `DYLD_FALLBACK_LIBRARY_PATH` value that lacks `/opt/homebrew/lib` | `src/remarkable_spec/cli/env_cmd.py:36-63` |
| `--json` flags on read commands | stdout as JSON, via `console.print_json`. Nine of the eleven CLI modules declare a `--json` parameter — every one except `render` and `env`. The machine-readable diagnostic surface | Field names rather than rendered table text — for example `pen_types`, `colors`, `layer_count` from `inspect` | `src/remarkable_spec/cli/inspect_cmd.py:121-141`; `src/remarkable_spec/cli/sync_cmd.py:374-377` |
| `mmdc` subprocess stderr | Captured by `capture_output=True` and re-emitted through the Rich console or wrapped into a `RuntimeError` message. Never written to a file | `Render failed:` in CLI output; `Mermaid rendering failed:` in an exception message | `src/remarkable_spec/cli/diagram_cmd.py:297`; `src/remarkable_spec/device/push.py:141` |
| Remote command stderr over SSH | Interpolated into a `RuntimeError` message alongside the exit code. Discarded if the caller swallows the exception | `Command failed (exit ` — the literal prefix, followed by the command and its stderr | `src/remarkable_spec/device/connection.py:160-162` |

## First-checks ladder

Cheapest and most local first. Steps 1 through 8 are offline and free. Step 9 touches an external
binary; step 10 is the only one that can bill an account, and it is deliberately last.

1. Run `rmspec env`. It prints exactly the resolved configuration the commands will use, and its
   omissions are as informative as its output — a missing `RMSPEC_XOCHITL` line means all three
   xochitl fallbacks failed. `src/remarkable_spec/cli/env_cmd.py:36-63`
2. Run `rmspec --help`, then `rmspec <subcommand> --help`. Eleven top-level commands are registered
   from literal names, and `sync` and `device` are groups rather than leaves, so a "command not found"
   is often a missing subcommand rather than a broken install. `src/remarkable_spec/cli/__init__.py:48-58`
3. Re-run the failing command with the streams split: `rmspec ... > /tmp/out.txt 2> /tmp/err.txt`.
   Errors go to stdout via Rich while the three module loggers go to stderr via `logging.lastResort`,
   so splitting the streams separates guard-clause messages from parser warnings — the only way to see
   that distinction without a log level flag, because none exists.
   `src/remarkable_spec/formats/rm_file.py:33`
4. Run `rmspec ls` (or `rmspec tree`) against the same xochitl directory. Both walk `*.metadata` with
   the same silent-skip rule the resolver uses, so a document missing from these listings has
   unparseable metadata rather than a name that failed to match.
   `src/remarkable_spec/cli/ls_cmd.py:162-168`
5. Re-run with the full 36-character UUID instead of a name. That takes the exact-match branch and
   bypasses both prefix and substring matching, eliminating the tie-break as a variable.
   `src/remarkable_spec/cli/_resolve.py:83-87`
6. Run `rmspec inspect <page>.rm --json`. It parses the binary with no rendering, no network, and no
   optional extras, so a failure here isolates the fault to `rmscene` and the format layer. The
   `pen_types` and `colors` arrays also expose silent pen and colour fallbacks.
   `src/remarkable_spec/cli/inspect_cmd.py:121-141`
7. Render one page to `.svg`. SVG export is pure Python and needs no extra, so it exercises parse,
   model, palette, and geometry without cairo, PyMuPDF, or AWS. The root dimensions also reveal which
   screen spec `detect_screen` chose. `src/remarkable_spec/render/engine.py:128-134`
8. For any sync symptom, read `rmspec sync log --json` and filter on `status = 'error'`. This is the
   only persisted failure record in the system, and the `details` column carries the original
   exception string. `src/remarkable_spec/cli/sync_cmd.py:367-398`
9. Check the two native dependencies the Python extras do not cover: `command -v mmdc` for Mermaid,
   and `echo $DYLD_FALLBACK_LIBRARY_PATH` for cairo on macOS. A pre-set DYLD variable suppresses the
   auto-configuration entirely, so a full `[all]` install can still fail to load `libcairo`.
   `src/remarkable_spec/cli/_util.py:69-72`
10. Only now, before running `rmspec ocr`, `rmspec diagram`, or `rmspec annotations`, confirm AWS
    identity and region with `aws sts get-caller-identity`. These commands invoke Textract and Bedrock
    with no cache read on the OCR path and no exception handling around the client, so a credential or
    region mistake surfaces as a raw traceback after the render work is already done.
    `src/remarkable_spec/ocr/pipeline.py:88-91`

## Known incident patterns

No tagged incident history found in source. `grep -rn -E 'INCIDENT|POSTMORTEM|FLAKY|SLOW:|KNOWN BUG|WORKAROUND|XXX|TODO|FIXME|HACK' src/ tests/ --include='*.py'`
exits 1 with zero hits, `git ls-files | grep -iE 'incident|postmortem|runbook|oncall|troubleshoot'`
returns nothing, and the repository has a single commit, so there is no history to mine. The
codebase also declares zero custom exception classes, closing the "error-class name that hints at
history" avenue.

What follows is the next-best evidence: defensive code whose shape only makes sense as a response to
a failure someone already hit. Each is a live constraint a debugger should know about.

- **Device field-name misspellings:** `rmspec device` reads the same document-title field under three
  different spellings — `VissibleName`, `VisibleName`, and `VisssibleName` — falling through in that
  order. Signal: a device listing shows an empty document name, meaning a fourth spelling arrived.
  Mitigation: add the new spelling to the fallback chain; the local `.metadata` path uses the correct
  `visibleName` and is unaffected. `src/remarkable_spec/cli/device_cmd.py:347-348`
- **Parser lags the format:** the `.rm` parser suppresses `rmscene`'s own warnings to `ERROR` at
  import time with the comment that "the v6 format evolves faster than the parser, and the missing
  fields are non-critical". `rmscene` is also the only upper-bounded dependency in the project
  (`pyproject.toml:13`), which says breaking changes are expected. Signal: strokes present on the
  tablet but absent from a render, with no warning, because the warning was suppressed process-wide.
  Mitigation: temporarily raise that logger's level in a REPL to see what `rmscene` is dropping.
  `src/remarkable_spec/formats/rm_file.py:29-31`
- **PDF page misalignment:** `_get_redir_map` exists solely to read the `redir` field from `.content`,
  with the docstring "Without this mapping, stroke overlays are composited onto the wrong PDF page."
  It falls back to positional order when `redir` is missing or not an integer. Signal: annotations
  land on a neighbouring page of a PDF-backed document. Mitigation: inspect the `.content` `redir`
  values for the affected pages. `src/remarkable_spec/cli/_resolve.py:176-196`
- **Nested-group resolution failure:** the scene-tree walker resolves nested groups through the tree
  index and, on `KeyError` or `AttributeError`, retries against the group's own children. Signal:
  strokes from a nested group are missing or duplicated across layers. Mitigation: compare per-layer
  stroke counts from `rmspec inspect --json` against the tablet.
  `src/remarkable_spec/formats/rm_file.py:125-132`
- **PyMuPDF-optional page counting:** PDF page counts fall back to a regex scan for `/Type /Page`
  entries when the `pymupdf` import or open fails, and return at least 1 regardless. Signal: a pushed
  PDF is tracked with a wrong page count in the sync database. Mitigation: confirm `pymupdf` is
  importable in the environment that ran the push. `src/remarkable_spec/device/sync.py:437-454`
- **Best-effort sync bookkeeping:** three sites treat sync-database writes as optional and swallow
  their failures — a nested `except Exception: pass` around the error-logging call itself, an
  unconditional `except Exception: pass` around the success record, and a `contextlib.suppress` around
  the pull error record. Signal: `rmspec sync log` shows no row for an operation you know ran.
  Mitigation: check the database file is writable before trusting the log as a record of what
  happened. `src/remarkable_spec/cli/device_cmd.py:409-410`; `src/remarkable_spec/cli/device_cmd.py:436-437`;
  `src/remarkable_spec/device/sync.py:422`
- **Cache treated as expendable:** the diagram cache lookup is wrapped in an `except Exception` that
  sets both the database handle and the content hash to `None`, and the cache write is wrapped in
  `except Exception: pass` with the comment "Cache write failure is not fatal". Signal: `rmspec
  diagram` re-bills Bedrock on a page it already processed and prints no `(cached)` marker.
  Mitigation: verify the sync database is reachable and writable; the failure is otherwise invisible.
  `src/remarkable_spec/cli/diagram_cmd.py:232-235`; `src/remarkable_spec/cli/diagram_cmd.py:255-256`
- **Double-raise dead branch:** the PNG exporter's fallback path raises `ImportError` inside a `try`
  and immediately re-raises an identical `ImportError` from its own handler. The first raise can never
  escape. Signal: none at runtime — the observable message is correct. It matters only because a
  reader debugging this file will look for a second condition that does not exist.
  `src/remarkable_spec/export/png.py:102-112`

## See also

- [contract map](contract-map.md) — 30 shared source citations
- [processes](../behavior/processes.md) — 29 shared source citations
- [business logic](business-logic.md) — 29 shared source citations
- [tech debt](tech-debt.md) — 26 shared source citations
- [impact analysis](impact-analysis.md) — 25 shared source citations
