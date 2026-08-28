# remarkable-spec · Tech debt

**Zero `TODO`, `FIXME`, `HACK`, or `XXX` markers exist anywhere in this codebase.**
`grep -rn -E 'TODO|FIXME|HACK|XXX' src/ tests/ --include='*.py'` returns no output and exits 1. That
count is a fact about the team's commenting culture, not a fact about the code's health. Every item
below is unmarked debt, found by reading source rather than by searching for confessions.

This register was assembled from seven signals, in the order they were worked:

1. **Comment markers and deprecation decorators.** Zero of the four canonical keywords. The repo's
   actual marker vocabulary is suppression pragmas — 23 `# type: ignore[prop-decorator]` and one
   `# noqa: F401` — which is what the Explicit markers section reports.
2. **The three read-only quality gates, run against the committed tree at commit `4bb899d`.** Ruff
   lint and format pass. The type checker does not. That asymmetry produced several rows.
3. **Manifest and toolchain version pins**, compared against what is installed in the live `.venv`
   and against each other across `pyproject.toml`, `mise.toml`, and `.python-version`.
4. **Single-platform assumptions** in code the packaging metadata presents as portable.
5. **Asymmetric enforcement between sibling surfaces** — two call sites doing the same job where one
   validates or raises and its twin does not.
6. **Structural smells**: duplicated bodies, error-swallowing handlers, unimplemented exported
   interfaces, and classes carrying two generations of the same operation.
7. **Declared-but-unreached surface**, cross-checked with `codegraph callers` and grep: methods with
   zero callers, settings fields with zero readers, and a dependency declared in the manifest and
   never imported. Three rows come from this signal alone.

The register carries 24 rows. The category vocabulary is closed at eight values — `marker`,
`wrong abstraction`, `error handling`, `dead code adjacent`, `deprecated pattern`, `version pin`,
`duplicated logic`, `missing tests` — and every one of the eight is used at least once, which is a
consequence of the findings rather than an attempt to fill the set.

**Ordering rule.** Rows are ranked descending by consequence-of-leaving, with cost-to-fix as the
tiebreaker toward the cheaper repair — so the top of the table is what to do first, and the
`Cost to fix` column stays independently readable. The ordering deliberately does not flatten cost:
rank 1 is weeks of work and rank 3 is a two-line guard, and both sit near the top because leaving
either one costs more than fixing it.

**One limitation to declare.** There is no test suite, so no row below is expressed in terms of
coverage, and no claim in this document is backed by a test. There is also exactly one commit in
history (`4bb899d`), so nothing here is ranked by churn, age, or co-change. Every row traces to a
source read at the line cited. One fact resists citation by construction: `tests/` holds a single
0-byte `tests/__init__.py`, and a 0-byte file has no line to point at, so rank 1 cites the
configuration that wires the empty suite instead.

## Ranked register

| Rank | Debt item | Category | Cost to fix | Citation |
| --- | --- | --- | --- | --- |
| 1 | No test suite at all against 10,321 lines of source, while the harness is fully wired: pytest is configured with `testpaths = ["tests"]` and a `test` task exists, so `mise test` exits 0 against an empty `tests/` directory holding one 0-byte `tests/__init__.py`. The gate reports success and verifies nothing. | `missing tests` | L | `pyproject.toml:74-76`, `mise.toml:9` |
| 2 | The documented type-check gate fails on the committed tree. `ty check src/` exits 1 with 8 errors and 1 warning at commit `4bb899d`. `mise.toml:12` and the README's development block both invoke it, but lefthook runs only ruff and no CI exists, so nothing blocks the failure from being committed. | `missing tests` | M | `mise.toml:12`, `README.md:142`, `lefthook.yml:1-11` |
| 3 | `Path \| None` flows unchecked into four positions declared `Path`. The last one calls `rm_path.with_suffix(".mmd")` on a value the type checker proves can be `None`, which raises `AttributeError` at runtime for any resolved document without a backing `.rm` file. | `error handling` | S | `src/remarkable_spec/cli/diagram_cmd.py:164`, `:156`, `:159`, `:161` |
| 4 | Broad handlers silently drop user documents. 27 `except` sites in `src/` catch `Exception`, `BaseException`, or `OSError`; ten of them recover with a bare `continue` or `pass` that removes a document from a listing or a sync run with no log line and no counter. The device-sync case turns a metadata fetch failure into a document that never appears in the sync report. | `error handling` | M | `src/remarkable_spec/device/sync.py:276`, `src/remarkable_spec/cli/_resolve.py:57`, `src/remarkable_spec/cli/search_cmd.py:152`, `src/remarkable_spec/cli/ls_cmd.py:167`, `src/remarkable_spec/cli/tree_cmd.py:127` |
| 5 | No CI, no dependency advisory scanner, no license check, no secret scanner, no SBOM. `.github/` does not exist. The entire automated safety net is lefthook running ruff on staged files plus a Conventional Commits regex, both of which a `--no-verify` push bypasses. `uv.lock` is committed, so resolution is reproducible, but nothing audits what it resolves to. | `missing tests` | M | `lefthook.yml:1-11`, `:13-24` |
| 6 | The Bedrock model ID `global.anthropic.claude-opus-4-6-v1` is a hardcoded string literal in four files, twice as a module constant and twice as a function parameter default. `RmspecSettings` has seven fields and none of them is a model ID, so changing models means editing four files. | `duplicated logic` | S | `src/remarkable_spec/ocr/postprocess.py:23`, `src/remarkable_spec/ocr/diagram.py:57`, `src/remarkable_spec/ocr/pipeline.py:90`, `src/remarkable_spec/cli/annotations_cmd.py:254`, `src/remarkable_spec/cli/_util.py:52-55` |
| 7 | The Bedrock vision invocation is written three times. Two of the three are literally named `_invoke_bedrock_vision`, and the second one's docstring says it "Uses the same pattern as" the first. The third is the same body inlined under a different name. All three build an `anthropic_version` payload and call `invoke_model`; their `max_tokens` and `temperature` already diverge. | `duplicated logic` | M | `src/remarkable_spec/ocr/postprocess.py:187`, `src/remarkable_spec/ocr/diagram.py:286`, `:295`, `src/remarkable_spec/cli/annotations_cmd.py:251` |
| 8 | Four near-identical loops scan the xochitl directory for `*.metadata`, and two of them bypass the typed parser. `ls` and `tree` call `parse_metadata`, which returns a validated `DocumentMetadata`; document resolution and `search` call raw `json.loads` and then read untyped dict keys. Same input, two different validation contracts, four places to change when the format moves. | `duplicated logic` | M | `src/remarkable_spec/cli/ls_cmd.py:162`, `:166`, `src/remarkable_spec/cli/tree_cmd.py:122`, `src/remarkable_spec/cli/_resolve.py:54`, `src/remarkable_spec/formats/metadata.py:36` |
| 9 | `_render_mermaid` is defined twice with opposite failure contracts: the CLI copy prints a red message and returns `None`, the device copy raises `RuntimeError`. A third site shells out to `mmdc` directly for validation. Timeouts are 30s, 30s, and 10s. No site guards with `shutil.which`, so each one discovers a missing `mmdc` through its own `FileNotFoundError` handler. | `duplicated logic` | M | `src/remarkable_spec/cli/diagram_cmd.py:281`, `src/remarkable_spec/device/push.py:116`, `src/remarkable_spec/ocr/diagram.py:231` |
| 10 | Three unrelated DPI defaults, none of which is the supported panel's DPI. The settings default is 226, which is exactly the reMarkable 2 DPI, while the Paper Pro this project supports is 229 and the OCR paths default to 300 in two more places. A user who renders through settings gets a different scale than one who renders through the OCR pipeline. | `wrong abstraction` | S | `src/remarkable_spec/cli/_util.py:52-55`, `src/remarkable_spec/models/screen.py:80`, `:83`, `src/remarkable_spec/sync/models.py:82`, `src/remarkable_spec/ocr/pipeline.py:28` |
| 11 | The typed SQLite OCR cache and the OCR cache the product actually uses are two different systems, and the typed one is dead. `SyncDB.get_ocr`, `put_ocr`, `get_all_ocr`, and `find_changed_pages` have zero callers by grep and by `codegraph callers`. The shipping cache is a `.ocr.txt` filesystem sidecar written beside each page. The only writer of the `ocr_cache` table is the one-shot legacy importer `migrate_ocr_sidecars`, which itself has zero callers — so the table the schema documents is never populated in a normal run, and `CLAUDE.md:40` describes `rm_hash` as the cache-invalidation key for a cache that is keyed by file path. | `dead code adjacent` | M | `src/remarkable_spec/sync/db.py:174`, `:192`, `:216`, `src/remarkable_spec/sync/migrations.py:113`, `src/remarkable_spec/cli/search_cmd.py:196` |
| 12 | `SyncManager` carries two generations of pull and push in one 573-line module. `pull_all` and `pull_document` sit beside `sync_pull`; `push_pdf` sits beside `sync_push_file`. Nothing marks either pair as superseded, so a caller has to read all four to learn which one honours the sync database. | `wrong abstraction` | L | `src/remarkable_spec/device/sync.py:31`, `:52`, `:92`, `:303`, `:456` |
| 13 | Two global side effects fire at import time from library code. Importing the CLI settings module mutates `os.environ["DYLD_FALLBACK_LIBRARY_PATH"]` on macOS; importing the `.rm` parser sets the third-party `rmscene` logger to `ERROR` process-wide. Both change behaviour for every consumer of the process, including one that imports `remarkable_spec` as a library and never touches the CLI. | `wrong abstraction` | M | `src/remarkable_spec/cli/_util.py:69-72`, `src/remarkable_spec/formats/rm_file.py:31` |
| 14 | `RMSPEC_THICKNESS` and `RMSPEC_DPI` are settable and inert. Both are declared on `RmspecSettings` with a description telling the user which env var sets them, and neither field is read anywhere in `src/`. The render command hardcodes the same `1.5` and `226` as literal parameter defaults, so `rmspec render --help` prints the values the settings model would have supplied while the env vars do nothing. `rmspec env` emits three of the seven settings and neither of these two, so there is no surface where the failure becomes visible. | `dead code adjacent` | S | `src/remarkable_spec/cli/_util.py:47-51`, `:52-55`, `src/remarkable_spec/cli/render_cmd.py:85`, `:89`, `src/remarkable_spec/cli/env_cmd.py:41` |
| 15 | The Python version is declared four different ways and none of them matches the interpreter in use. `.python-version` says 3.13, `mise.toml` asks for 3.12, `requires-python` floors at 3.12, and ruff and pyright both target 3.12 — while the `.venv` interpreter is 3.13.11. The code runs on 3.13 and is linted as 3.12. | `version pin` | S | `.python-version:1`, `mise.toml:2`, `pyproject.toml:10`, `:60`, `:71` |
| 16 | The configured type checker is not the invoked one. `[tool.pyright]` and a `pyright>=1.1` dev dependency are both present and neither is ever run; the gate and the README both invoke `ty`. The pyright block is dead configuration that a reader will mistake for the active contract. | `dead code adjacent` | S | `pyproject.toml:70-72`, `:52`, `mise.toml:12`, `README.md:142` |
| 17 | Two floating lower bounds have been crossed by an installed major. `cyclopts>=3.0.0` resolves to 4.6.0 and `paramiko>=3.4.0` resolves to 4.0.0, so a fresh resolve can pick up a breaking change with no manifest signal. `rmscene>=0.7.0,<0.8.0` is the only upper-bounded pin in the file, which flags that dependency as the one expected to break and leaves every other one unguarded. | `version pin` | S | `pyproject.toml:14`, `:30`, `:13` |
| 18 | `pip install remarkable-spec[render]` appears with the extras bracket unquoted in 15 places across 7 files — six of them runtime `ImportError` messages the user actually sees, the rest module and function docstrings. In zsh — macOS's default shell, and macOS is the only platform this project claims — that command fails before pip starts: `zsh: no matches found: remarkable-spec[render]`. The other 31 install hints in `src/` use the correctly quoted `uv add 'remarkable-spec[...]'` form, so the failing variant is a leftover, not a convention. | `deprecated pattern` | S | `src/remarkable_spec/export/png.py:106`, `:111`, `src/remarkable_spec/export/pdf.py:62`, `src/remarkable_spec/device/connection.py:34`, `src/remarkable_spec/device/web_api.py:33` |
| 19 | `pillow` is a declared, advertised, never-imported dependency, and the code path that was supposed to use it is a tautology. The `[render]` extra pins it and the README extras table lists it, but no line in `src/` imports `PIL`. The PNG exporter's docstring promises "either `cairosvg` or `pillow`", while the runtime path tries only cairosvg and then executes a `try: raise ImportError(msg)` / `except ImportError: raise ImportError(msg) from None` in which both arms carry the identical message naming only cairosvg. Users install a package the code cannot use and read a docstring that names a fallback that does not exist. | `dead code adjacent` | S | `pyproject.toml:27`, `README.md:127`, `src/remarkable_spec/export/png.py:32`, `:99`, `:103-112` |
| 20 | The `ocr` extra pins two macOS-only pyobjc packages with no `sys_platform` marker, and the `all` extra pulls `ocr` in. `uv sync --all-extras` — the project's own documented install task — therefore attempts a macOS-only dependency on every platform. | `version pin` | S | `pyproject.toml:43-46`, `:40-42`, `mise.toml:8` |
| 21 | The `PenRenderer` Protocol is exported from the render package and implemented by nothing. It declares the same three methods as the `BasePenRenderer` ABC that sits 50 lines below it; all ten concrete renderers subclass the ABC, and the factory returns the ABC. `codegraph callers PenRenderer` reports one caller, the re-export itself. A published interface with zero implementers invites the next contributor to implement the wrong one. | `dead code adjacent` | S | `src/remarkable_spec/render/pens.py:42`, `:92`, `:437`, `src/remarkable_spec/render/__init__.py:32` |
| 22 | `tempfile.mktemp` is used to name the intermediate PNG in the OCR pipeline. The function has been deprecated since Python 2.3 for a race between naming and creating the file; the type checker flags it as the run's only warning. It is the single `mktemp` call in the repo — the other 14 temp-file sites already use the safe `NamedTemporaryFile` and `mkstemp` forms, so this one is an outlier rather than a house style. | `deprecated pattern` | S | `src/remarkable_spec/ocr/pipeline.py:63`, `src/remarkable_spec/ocr/diagram.py:228`, `src/remarkable_spec/device/push.py:123` |
| 23 | The Bedrock response parser ends in a fallback that cannot succeed. After a loop that already scans every block in reverse for the first `text` block, the function returns `result["content"][0]["text"]`. Extended thinking is enabled in the same request body, so block 0 is a thinking block with no `text` key — the only path that reaches the fallback raises `KeyError` instead of the intended graceful read. | `dead code adjacent` | S | `src/remarkable_spec/ocr/postprocess.py:235`, `:232`, `:207` |
| 24 | 23 `# type: ignore[prop-decorator]` suppressions across four of the eight `models/` files, one per `@computed_field` property, plus one `# noqa: F401`. Each is a deliberate record that a checker disagrees with the code. They are the codebase's only markers, and none carries a ticket, an owner, or a condition for removal. | `marker` | S | `src/remarkable_spec/models/screen.py:44`, `src/remarkable_spec/models/page.py:74`, `src/remarkable_spec/models/document.py:343`, `src/remarkable_spec/models/stroke.py:61`, `src/remarkable_spec/cli/device_cmd.py:54` |

## Explicit markers

**There are zero `TODO`, `FIXME`, `HACK`, and `XXX` markers in `src/` and `tests/`.** The grep exits
1 with no output. What follows is the complete set of what this repo writes instead: machine-readable
suppression pragmas, and the free-text comments that carry a marker's information — an accepted
deficiency, a fallback, or a deliberate global override — without a marker keyword. Every line is
quoted verbatim.

Suppression pragmas, the closest thing here to a marker (24 total, 23 of one code):

- `# type: ignore[prop-decorator]` — `src/remarkable_spec/models/screen.py:44` (also `:54`, `:60`, `:66`, `:72`)
- `# type: ignore[prop-decorator]` — `src/remarkable_spec/models/page.py:74` (also `:80`, `:124`, `:130`, `:136`, `:142`)
- `# type: ignore[prop-decorator]` — `src/remarkable_spec/models/document.py:343` (also `:349`, `:355`, `:361`, `:367`, `:373`)
- `# type: ignore[prop-decorator]` — `src/remarkable_spec/models/stroke.py:61` (also `:67`, `:73`, `:119`, `:125`, `:131`)
- `# noqa: F401` — `src/remarkable_spec/cli/device_cmd.py:54`

Rule suppressions in the lint configuration, each with its own justification comment:

- `# __all__ sorted — we group by category instead` — `pyproject.toml:66`
- `# Vision/Quartz are module-level imports, not variables` — `pyproject.toml:67`

Free-text comments that record an accepted deficiency or a deliberate global override:

- `# Suppress rmscene "Some data has not been read" warnings — the v6 format` — `src/remarkable_spec/formats/rm_file.py:29`
- `# evolves faster than the parser, and the missing fields are non-critical.` — `:30`
- `# Auto-configure macOS Homebrew cairo library path so cairosvg/cairocffi` — `src/remarkable_spec/cli/_util.py:67`
- `# can find libcairo without the user exporting DYLD_FALLBACK_LIBRARY_PATH.` — `:68`
- `# Singleton — instantiated once, reads env vars + .env on import` — `:63`
- `# mmdc not installed — basic syntax check` — `src/remarkable_spec/ocr/diagram.py:242`
- `# With extended thinking enabled, response has thinking + text blocks.` — `src/remarkable_spec/ocr/postprocess.py:230`
- `# Extract the last text block (the actual transcription).` — `:231`
- `# Fallback: inform the user about requirements` — `src/remarkable_spec/export/png.py:102`
- `# Fallback: default sync location` — `src/remarkable_spec/cli/_util.py:108`

One docstring line does the work a marker would do, and is the strongest single piece of evidence for
the duplication smell below:

- ``Uses the same pattern as :func:`remarkable_spec.ocr.postprocess._invoke_bedrock_vision`.`` — `src/remarkable_spec/ocr/diagram.py:295`

## Pattern-level smells

### Handlers that convert a failure into a missing row

27 `except` clauses in `src/` catch `Exception`, `BaseException`, or `OSError`. None is a truly bare
`except:` — every one names a class, which is why ruff's `B` and `E` rules pass. The problem is what
the bodies do: five recover with a bare `continue` that removes an item from a result set with no log
line, no warning, and no count of what was skipped. In device sync this means a document whose
metadata fetch failed never appears in the sync report, so the user reads a successful run that
silently omitted their file. The codebase already contains the correct shape: the document loader
catches equally broadly but calls `logger.warning(..., exc_info=True)` and continues with an empty
layer list, so the failure reaches the user. The debt is not the breadth of the
handlers — it is that two adjacent handlers with the same breadth have opposite visibility, and the
silent one is the majority.

Shows up in:

- `src/remarkable_spec/device/sync.py:276` — bare `continue` inside the device metadata loop
- `src/remarkable_spec/cli/_resolve.py:57` — bare `continue`, drops the document from resolution candidates
- `src/remarkable_spec/cli/ls_cmd.py:167` — bare `continue`, drops the document from `rmspec ls`
- `src/remarkable_spec/formats/document_loader.py:95` — the correct counterexample: logs with `exc_info=True` at `:96`
- `src/remarkable_spec/cli/__init__.py:67` — swallows to a fabricated `"0.0.0-dev"` version at `:68`

Cost: M — mechanical per site, but 27 sites and each needs a judgment call about whether to log, count, or re-raise.

### One Bedrock call, written three times, already drifting

The AWS Bedrock `invoke_model` request is constructed from scratch in three files. Two of the three
functions carry the identical name `_invoke_bedrock_vision`; the third is the same body inlined into a
CLI helper under a different name. All three build the raw `anthropic_version: "bedrock-2023-05-31"`
payload by hand, base64-encode an image into a content block, call `invoke_model`, and read
`result["content"][0]["text"]`. The copies have already diverged in ways that matter: `max_tokens` is
16384 in one and 4096 in the other two, `temperature` is 1 versus 0.0, and only the first enables
extended thinking. The duplication is self-documented — the second copy's docstring points at the
first — which means it was a known shortcut rather than an accident, and it is the reason the response
parser in register row 20 is correct in one copy and broken in the others.

Shows up in:

- `src/remarkable_spec/ocr/postprocess.py:187` — the original, with `thinking` enabled at `:207`
- `src/remarkable_spec/ocr/diagram.py:286` — the acknowledged copy, docstring at `:295`
- `src/remarkable_spec/cli/annotations_cmd.py:251` — the renamed third copy, `invoke_model` at `:297`

Cost: M — one shared client helper plus three call-site rewrites, and the divergent parameters have to be reconciled deliberately rather than merged.

### Configuration constants copied instead of centralized

Values that should have one home have several, and the copies disagree. The Bedrock model ID is a
literal in four files. The render DPI has three defaults — 226 in settings, 300 in the sync cache
model, 300 on both OCR entry points — and 226 is the reMarkable 2 panel DPI, not the 229 of the Paper
Pro that the README names as the supported device. The AWS region is a parameter default in each
module rather than a setting. `RmspecSettings` exists and is the obvious home for all of this; it has
seven fields and holds none of them. The consequence is that behaviour depends on which entry point
the user reaches, and the settings object no longer describes the system it is named for.

Shows up in:

- `src/remarkable_spec/cli/_util.py:52-55` — `dpi` default 226, against the 229-DPI panel at `README.md:10` and `CLAUDE.md:38`
- `src/remarkable_spec/models/screen.py:80` — 226 is `RM2_SCREEN`; the Paper Pro is at `:83`
- `src/remarkable_spec/sync/models.py:82` — `render_dpi` default 300, which is what the cache key records
- `src/remarkable_spec/ocr/pipeline.py:28` — `dpi: int = 300`, and again at `:88`
- `src/remarkable_spec/ocr/postprocess.py:24` — `DEFAULT_REGION` as a module constant rather than a setting

Cost: S — the model ID and region are a settings field and four deletions; the DPI reconciliation needs one decision about which value is correct before the edits.

### Import-time global side effects in library code

Two modules change process-wide state as a side effect of being imported. The CLI settings module
mutates `os.environ["DYLD_FALLBACK_LIBRARY_PATH"]` when it detects macOS and the Homebrew lib
directory exists. The `.rm` parser sets the third-party `rmscene` logger to `ERROR`. Both are
defensible as CLI conveniences and neither is gated behind a function a caller can decline. The
package ships `py.typed` and re-exports 26 names for library use
(`src/remarkable_spec/__init__.py:33-67`), so a consumer who imports
`remarkable_spec` to parse a file inherits a mutated environment variable and a silenced third-party
logger it never asked for. The macOS branch is also the sharp end of a broader single-platform
assumption: the Apple Vision binding has no non-Apple fallback, and the extra that installs it carries
no platform marker.

*judgment-call* — the DYLD line is a genuine ergonomic win on the one platform this project supports,
so the smell is the placement and the missing opt-out, not the intent. Worth flagging because the fix
is small and the blast radius of leaving it is every library consumer.

Shows up in:

- `src/remarkable_spec/cli/_util.py:69-72` — the environment mutation, guarded only by `platform.system() == "Darwin"`
- `src/remarkable_spec/formats/rm_file.py:31` — `logging.getLogger("rmscene").setLevel(logging.ERROR)` at module scope
- `src/remarkable_spec/ocr/vision.py:39` — the Apple Vision binding, with `_import_quartz` at `:51` and no non-Apple path
- `pyproject.toml:43-46` — the `ocr` extra with no `sys_platform` marker

Cost: M — moving both into explicit setup functions is small, but every current caller relies on the implicit behaviour and has to be found and updated.

### Sibling gates enforced at three different strengths

The repo defines four quality tasks and enforces them at three different strengths. Ruff lint and
format run at pre-commit through lefthook on staged files, so they are effectively mandatory — and
they pass: `ruff check src/ --no-fix` reports all checks passed and `ruff format --check src/` reports
55 files already formatted. The type check is defined in the same task table and wired to nothing;
it exits 1 with 8 errors on the committed tree. The test task is defined, wired to nothing, and
exits 0 because the suite is empty. `mise check` bundles lint, format, and typecheck but excludes
test, and no hook or CI invokes `check` either. The result is a repository that looks gated, passes
the gates a reader is most likely to run, and carries eight type errors and zero tests behind the
two gates nobody runs.

Shows up in:

- `mise.toml:10` — `lint`, and `format` at `:11`, both enforced at pre-commit via `lefthook.yml:1-11`, and both passing
- `mise.toml:12` — `typecheck`, enforced nowhere, currently failing
- `mise.toml:9` — `test`, enforced nowhere, passing vacuously
- `mise.toml:13` — `check`, which omits `test` and is itself uninvoked
- `src/remarkable_spec/device/sync.py:379` — a representative unblocked type error, with its twin at `:380`

Cost: S to wire the gates, L to make them pass — adding `typecheck` to lefthook is one line, fixing the eight errors is a day, and building the test suite that would make `test` meaningful is the L in register row 1. Reporting this as a single cost would be the mistake.

## See also

- [contract map](contract-map.md) — 35 shared source citations
- [business logic](business-logic.md) — 33 shared source citations
- [impact analysis](impact-analysis.md) — 31 shared source citations
- [module map](../architecture/module-map.md) — 29 shared source citations
- [processes](../behavior/processes.md) — 29 shared source citations
