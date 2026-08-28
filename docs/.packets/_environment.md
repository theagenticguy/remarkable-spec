# Shared environment brief — remarkable-spec

Every packet's section 3 (Inputs) opens with a READ FIRST bullet naming this file. It is the one
input the orchestrator wrote and every agent reads, and it exists because the alternative is 17
agents each re-deriving the same facts and each landing on a different answer.

Measured 2026-08-28 at commit `4bb899d` on branch `main`.

## Contents

- [1. Code index — query the graph before you grep](#1-code-index--query-the-graph-before-you-grep)
- [2. Verified topology](#2-verified-topology)
- [3. Verified-current language and framework facts](#3-verified-current-language-and-framework-facts)

---

## 1. Code index — query the graph before you grep

A CodeGraph index covers this workspace: **57 files, 702 nodes, 1,377 edges**, 1.99 MB SQLite at
`.codegraph/codegraph.db`. It resolves dynamic-dispatch hops grep cannot follow and reports
caller/callee trails.

Binary, absolute — **a version-manager shim is not reliably on a subshell's `PATH`, so the
absolute path is the invocable one**:

```text
/Users/lalsaado/.local/share/mise/installs/npm-colbymchenry-codegraph/latest/node_modules/.bin/codegraph
```

Invoke from `/Users/lalsaado/Projects/remarkable-spec`. The index lags writes by about a second
through a file watcher, so it reflects current source. Verified working at brief-write time.

An MCP tool `mcp__codegraph__codegraph_explore` may also be available to you; if a `ToolSearch` for
it succeeds, it returns the same output as `codegraph explore` with less shell overhead. Either is
fine. Do not spend more than one attempt discovering it — fall back to the absolute binary.

| Need                                                      | Command                                 |
| --------------------------------------------------------- | --------------------------------------- |
| Verbatim source, call paths, and blast radius for an area | `<cmd> explore "<symbols or question>"` |
| One symbol's source and caller/callee trail               | `<cmd> node <symbol>`                   |
| A file with line numbers plus its dependents              | `<cmd> node -f <path>`                  |
| Symbol map for a file, no bodies                          | `<cmd> node -f <path> --symbols-only`   |
| Everything a change to a symbol reaches                   | `<cmd> impact <symbol> -d 3 --json`     |
| Inbound callers, for ranking by consumer count            | `<cmd> callers <symbol> -l 50 --json`   |
| Outbound calls from a symbol                              | `<cmd> callees <symbol> --json`         |
| Symbol search, filterable by kind                         | `<cmd> query <term> -k <kind> --json`   |
| Indexed file tree with per-file symbol counts             | `<cmd> files --format grouped`          |

`explore` output is line-numbered and safe to cite as `path:LOC` directly. Do not re-read a file it
has already printed.

Which packets lean hardest on which command: impact-analysis on `impact` and `callers`; dead-code on
`callers` returning zero, cross-checked against `query` and a text grep; contract-map on `callers` to
rank by consumer count; module-map, dependency-graph, and components on `files` and
`node --symbols-only`.

**Kinds this index actually holds, with counts** — a `-k` value outside this list returns nothing:

```text
import      288
method      121
function    113
variable     69
file         56
class        55
```

Note the absence of any `interface`, `type`, `enum`, `struct`, or `trait` kind. This is a Python
repo; the six `enum` classes in source (see § 2) are indexed as `class`, and the module-level
constants (`RM_PALETTE`, `PAPER_PRO_SCREEN`, `BUILTIN_TEMPLATES`) are indexed as `variable`.

**Three edges where the index misleads.** All measured, not hypothetical:

- **Symbol lookup is name-resolved, not namespace-qualified.** This repo has real collisions that
  will cross-attribute callers and coverage if you trust a bare name: `app` is a distinct
  `cyclopts.App` in each of 12 CLI modules; `push` and `pull` and `ls` exist both as
  `device_cmd`/`sync_cmd` subcommands and as `device/push.py` module functions; the Bedrock
  vision helper is duplicated three times under two names (`_invoke_bedrock_vision` at
  `ocr/postprocess.py:187` and `ocr/diagram.py:286`, and `_invoke_annotation_analysis` at
  `cli/annotations_cmd.py:251`); `DEFAULT_MODEL` twice. Rank with the counts, then confirm every edge
  from an import or a call site.
- **Route and decorator extraction only sees literal arguments.** The CLI surface here is registered
  by 11 `app.command(<sub_app>, name="<literal>")` calls in `src/remarkable_spec/cli/__init__.py:48-58`
  — those literals *are* the authoritative census, so this limit does not bite for the CLI. It does
  bite for the SQLite schema: the tables are created from SQL string literals inside
  `src/remarkable_spec/sync/migrations.py`, which the index sees as opaque strings. Read that file for
  the schema; do not infer it from the Pydantic models, which are a separate hand-maintained mirror.
- **`import` is the most numerous kind (288 of 702 nodes).** Many are function-local lazy imports
  used deliberately to keep optional extras out of CLI startup (for example
  `src/remarkable_spec/cli/_util.py:79`, `src/remarkable_spec/device/sync.py:325-326`). A dependency
  edge derived from imports alone therefore over-reports coupling relative to the module layering in
  § 2. Cite the import site.

## 2. Verified topology

- **Modules**: eight packages under `src/remarkable_spec/` — `models`, `formats`, `render`, `ocr`,
  `device`, `sync`, `export`, `cli` — plus the root `__init__.py`. Declared as a single distribution
  named `remarkable-spec` at `pyproject.toml:2`, built by `uv_build` (`pyproject.toml:55-57`).
  There is no workspace or monorepo; one package, one distribution.

- **Size per module** (source-only: `*.py` under the module, `wc -l`, excluding `__init__.py` from
  neither — `__init__.py` files are counted):

  | Module    | LOC   | Files |
  | --------- | ----- | ----- |
  | `cli`     | 3,899 | 14    |
  | `models`  | 1,367 | 8     |
  | `device`  | 1,295 | 6     |
  | `render`  | 1,069 | 5     |
  | `ocr`     | 962   | 6     |
  | `sync`    | 728   | 5     |
  | `formats` | 562   | 6     |
  | `export`  | 372   | 4     |
  | root      | 67    | 1     |

  These nine rows sum to exactly 10,321, which is the total below — use them as-is rather than
  recounting with a different rule.

  Total tracked source: **10,321 LOC across 56 `.py` files** (`find src tests -name '*.py' | xargs wc -l`).
  Largest single files: `device/sync.py` (573), `cli/device_cmd.py` (504), `render/pens.py` (480),
  `cli/sync_cmd.py` (425), `cli/render_cmd.py` (408).

- **File inventory**: the flattened pack at `docs/.repomix/codebase.json` holds **67 files,
  95,134 tokens, 369,670 chars**. 56 are `.py`; the rest are `pyproject.toml`, `uv.lock`, `README.md`,
  `CLAUDE.md`, `LICENSE`, `mise.toml`, `lefthook.yml`, `.czrc`, `.gitignore`, `.python-version`,
  `src/remarkable_spec/py.typed`.

- **Dependency direction — measured, not asserted by any test.** Derived by scanning every
  `from remarkable_spec.<module>` import and bucketing by containing package. Edge weights are import
  counts:

  ```text
  models   (leaf, zero outbound internal edges)
  sync     (leaf, zero outbound internal edges)
  formats  -> models (8)
  render   -> models (6)
  export   -> models (6), render (4)
  device   -> sync (4)
  ocr      -> models (4), export (2), formats (2)
  cli      -> models (15), formats (13), device (11), ocr (8), sync (5), render (5), export (3)
  root     -> models (7)
  ```

  No cycles. `cli` is the only module that reaches every other. **There is no test, lint rule, or
  import-linter config enforcing this direction** — it is a convention the code currently honors,
  not a gate. Say "the code currently imports in this direction", never "the build enforces".

- **Entry points**: one console script, `rmspec = "remarkable_spec.cli:app"` (`pyproject.toml:20-21`),
  resolving to the `cyclopts.App` built at `src/remarkable_spec/cli/__init__.py:40-44`. The package is
  also importable as a library: the root `src/remarkable_spec/__init__.py:33-67` re-exports **26**
  names in `__all__` — counted with an AST parse, not by eye; an earlier revision of this brief said
  24 and was wrong, so do not trust a "24" you may have read here or in a packet's section 3a. Both
  surfaces are in scope; the library surface is `models`-only by construction — the root `__init__`
  imports from `models` and nothing else.

- **Public or wire surface**: **11 top-level CLI commands**, registered at
  `src/remarkable_spec/cli/__init__.py:48-58`: `inspect`, `ls`, `render`, `tree`, `ocr`, `diagram`,
  `search`, `sync`, `device`, `annotations`, `env`. Two of those are groups rather than leaves:
  `sync` has 5 subcommands (a `@app.default` plus `status`, `pull`, `push`, `log` at
  `src/remarkable_spec/cli/sync_cmd.py:47,98,171,228,368`) and `device` has 4 (`info`, `ls`, `pull`,
  `push` at `src/remarkable_spec/cli/device_cmd.py:66,162,293,443`). The remaining nine are single
  `@app.default` functions. **There is no generated CLI census artifact** — the registration block
  and the `@app.default`/`@app.command` decorators are the authority. `rmspec --help` runs offline
  and is safe to invoke for verification.

  Library surface: **26 exported names** in the root `__all__` (`src/remarkable_spec/__init__.py:33-67`),
  plus per-module `__all__` in `models/__init__.py`, `formats/__init__.py`, `render/__init__.py`,
  `device/__init__.py`, `export/__init__.py`, `sync/__init__.py`, `ocr/__init__.py`,
  `cli/__init__.py:38`.

- **Test tiers**: **none exist.** `tests/` contains exactly one file, `tests/__init__.py`, at
  **0 bytes**. `pyproject.toml:74-76` configures pytest (`testpaths = ["tests"]`,
  `addopts = "-v --tb=short"`) and `mise.toml` defines `test = "uv run pytest"`, so the harness is
  wired and the suite is empty. `.pytest_cache/` exists at the repo root, so pytest has been run.
  **This is binding on every packet**: never write that a behavior is "covered", "verified by tests",
  or "regression-protected". Never cite a path under `tests/` other than the empty `__init__.py`.
  Any claim about runtime behavior must trace to source or to the docstring at that source line. The
  risk-hotspots, tech-debt, and debugging-guide packets should each treat the zero-test state as a
  first-class finding rather than an aside.

- **Toolchain**: pinned versions disagree across four files, and this is a real finding, not a
  transcription error — state the disagreement, do not pick a winner silently:

  | Declaration                                | Value        |
  | ------------------------------------------ | ------------ |
  | `.python-version:1`                        | `3.13`       |
  | `mise.toml:2` `[tools] python`             | `3.12`       |
  | `pyproject.toml:10` `requires-python`      | `>=3.12`     |
  | `pyproject.toml:60` ruff `target-version`  | `py312`      |
  | `pyproject.toml:71` pyright `pythonVersion`| `3.12`       |
  | **Actual interpreter in `.venv`**          | **3.13.11**  |

  The venv follows `.python-version`, so linters and the type checker are configured for a language
  level one minor behind the interpreter that runs the code.

- **Gates**: `mise.toml:8-13` defines `install` and `lint` (`uvx ruff check src/ --fix`), `format`
  (`uvx ruff format src/`), `typecheck` (`uvx ty check src/`), and `check` = all three
  (`mise.toml:13`). All three are offline and free — but **`ty check src/` exits non-zero on this
  commit**, reporting 8 errors and 1 deprecation warning. The documented type gate does not pass on
  the tree it documents; nothing in `lefthook.yml` runs it and there is no CI. Ruff lint and format
  both do pass. `lefthook.yml:1-14` runs ruff check and format on staged `*.py`/`*.pyi` at
  pre-commit and enforces Conventional Commits at commit-msg (`lefthook.yml:16-24`; the file is
  24 lines long, so do not cite past `:24`). **`mise test` (`mise.toml:9`)
  exists and passes trivially against an empty suite** — do not describe it as a quality gate.
  Ruff lint selection is `E, F, I, N, UP, B, SIM, RUF` with `RUF022` and `N806` ignored
  (`pyproject.toml:63-68`), line length 99 (`pyproject.toml:61`).

  **Billable commands that must never be wired to a hook or run for verification**: anything that
  reaches AWS. `ocr/textract.py:37` calls `boto3.client("textract")`; `ocr/postprocess.py:200`,
  `ocr/diagram.py:304`, and `cli/annotations_cmd.py:272` each call
  `boto3.client("bedrock-runtime")` and then `invoke_model` with an Opus-class model. Reading these
  files is free; running `rmspec ocr`, `rmspec diagram`, or `rmspec annotations` is not. Also do not
  attempt device commands (`rmspec device *`, `rmspec sync pull|push`) — they SSH to `10.11.99.1`
  and no device is attached.

- **Git history**: **1 commit**, `4bb899d` dated 2026-03-06, subject
  `feat: initial release of remarkable-spec`, **1 human author** (Laith Al-Saadoon), 0 bots.

  **Consequence, binding on the analysis packets**: there is no churn signal, no co-change signal,
  no bus-factor signal, and no "recently touched" signal. `git log --pretty=format: --name-only`
  returns every tracked file exactly once. The `doc-analysis-ownership` packet was **deliberately not
  seeded** for this run for exactly this reason — a per-person table over one author is noise dressed
  as analysis. `doc-analysis-risk-hotspots` **was** seeded but must rank on static finding signals
  only (see its packet) and must state in its first paragraph that churn history is unavailable.
  (Aside for anyone re-running this later: `git log --pretty=format: --name-only` silently skips
  merge commits, so per-file churn disagrees with `git log --oneline -- <path>` unless you pass
  `--no-merges`. Moot here at one commit.)

- **Most-churned paths**: not computable. One commit; every tracked file has exactly one touch.

- **Marker inventory**: **0** `TODO` / `FIXME` / `HACK` / `XXX` across all of `src/` and `tests/`
  (`grep -rn -E 'TODO|FIXME|HACK|XXX' src/ tests/ --include='*.py'` returns nothing, exit 1).

  **This is a methodology trigger for the tech-debt packet, never a finding of no debt.** Debt in
  this repo is unmarked and structural. Verified locations where it actually lives:
  - Zero tests against 10,321 LOC (see Test tiers above).
  - The Bedrock vision helper is implemented three times under two names:
    `_invoke_bedrock_vision` at `src/remarkable_spec/ocr/postprocess.py:187` and
    `src/remarkable_spec/ocr/diagram.py:286`, and `_invoke_annotation_analysis` at
    `src/remarkable_spec/cli/annotations_cmd.py:251`. The second carries a docstring at
    `src/remarkable_spec/ocr/diagram.py:295` explicitly saying it uses "the same pattern as" the
    first. The differing third name is why a bare-name caller count under-reports the duplication.
  - The model ID string `global.anthropic.claude-opus-4-6-v1` hardcoded in four places:
    `src/remarkable_spec/ocr/postprocess.py:23`, `src/remarkable_spec/ocr/diagram.py:57`,
    `src/remarkable_spec/ocr/pipeline.py:90`, `src/remarkable_spec/cli/annotations_cmd.py:254`
    (a parameter default on `_invoke_annotation_analysis`, declared at `:251`).
  - **27** `except`-clause sites in `src/` matching broad or bare handlers
    (`grep -rn -E 'except (Exception|BaseException|OSError)?\s*:|except Exception' src/ --include='*.py' | wc -l`).
    `src/remarkable_spec/device/sync.py:276-277` is the representative case: a bare
    `except Exception: continue` inside the device-metadata loop, which turns a fetch failure into a
    silently skipped document.
  - Floating lower-bound pins crossed by installed majors: `cyclopts>=3.0.0` with **4.6.0**
    installed, `paramiko>=3.4.0` with **4.0.0** installed (`pyproject.toml:14,30`).
  - Python-version drift across four declarations (see Toolchain above).
  - Three DPI defaults that are neither the physical panel DPI nor each other:
    `RmspecSettings.dpi = 226` (`src/remarkable_spec/cli/_util.py:53-56`) against the 229-DPI panel
    `CLAUDE.md` documents, `OCRCacheEntry.render_dpi = 300`
    (`src/remarkable_spec/sync/models.py:82`), and the `dpi: int = 300` parameter defaults on
    `render_rm_to_png` and `transcribe_rm` (`src/remarkable_spec/ocr/pipeline.py:28,88`). Verify each
    line number at its current position before citing.
  - macOS-only paths compiled into library code, not gated behind a plugin seam:
    `src/remarkable_spec/cli/_util.py:68-72` mutates `os.environ["DYLD_FALLBACK_LIBRARY_PATH"]` at
    import time, and `src/remarkable_spec/ocr/vision.py` binds the Apple Vision framework.

  Each bullet above is a lead with a citation, not a finished finding. Re-verify the line number at
  its current position before you put it in a doc; several of these files are long enough that a
  stale offset is plausible.

- **Prior lessons already recorded**: `.erpaval/sessions/` exists (a compounded-lessons directory
  from the `erpaval` skill) but holds **no tracked content** (it is untracked rather than gitignored). Do not cite anything
  under it — an untracked path fails the Phase 6 trackedness gate.

- **Hand-written documents that outrank your inference**: two, both tracked.
  - `README.md` (148 lines) — the user-facing contract. Governs: supported hardware
    (`README.md:9-12`), the AI-assistant annotate-and-read-back workflow (`:51-70`), the
    Markdown-push pipeline steps (`:72-91`), PDF background compositing (`:93-102`), the dependency
    extras table (`:122-135`).
  - `CLAUDE.md` (2.9 KB) — the agent-facing development guide. Governs the stated architecture
    decisions: v6 `.rm` X-origin-at-center and the `x_shift = vw / 2` compensation, the 1.5 thickness
    multiplier, `PenColor` 0–13, the OCR pipeline's use of Bedrock `invoke_model` rather than
    `converse`, `rm_hash` as the cache-invalidation key, and the document-resolution tie-break
    (page count desc, then `lastModified` desc).

  **One checkable staleness, confirmed:** `README.md:142` and `mise.toml:12` say the type checker is
  `ty` (`uvx ty check src/`), while `pyproject.toml:70-72` configures `[tool.pyright]` and
  `pyproject.toml:52` puts `pyright>=1.1` in the dev group — pyright 1.1.408 is what is actually
  installed. Both documents' architecture blocks (`README.md:111-120`, `CLAUDE.md:22-31`) were
  spot-checked against the filesystem and match it, including `formats/pagedata.py`, so do not go
  hunting for drift there. Where your reading of the source contradicts either document, **cite both
  and flag it in your Work log** rather than silently picking a side.

- **Formal specifications**: none. No OpenAPI, JSON Schema, protobuf, or `.rm`-format spec file in
  the repo. The binary-format knowledge lives in the third-party `rmscene` dependency and in prose
  in `CLAUDE.md`. There is no command that verifies a spec, because there is no spec.

---

## 3. Verified-current language and framework facts

### Python (verified 2026-08-28)

- **Interpreter actually in `.venv`: 3.13.11** (`uv run python -V`). Requested floor is `>=3.12`
  (`pyproject.toml:10`); `.python-version:1` pins `3.13`; `mise.toml:2` asks for `3.12`. The venv
  won. Do not claim "this project runs on 3.12" — it runs on 3.13.11 here while being *linted and
  type-checked as* 3.12.
- **Language level as configured: 3.12.** Ruff `target-version = "py312"` (`pyproject.toml:60`) and
  pyright `pythonVersion = "3.12"` (`pyproject.toml:71`). So a 3.13-only syntax feature would run but
  be flagged.
- **Every source file opens with `from __future__ import annotations`.** Verified across the files
  read at brief time. Consequence: all annotations are strings at runtime, so a doc claim about
  runtime type introspection must account for `typing.get_type_hints` being required. Pydantic
  handles this itself.
- **Modern idiom is used consistently** and ruff's `UP` rules enforce it: `X | None` rather than
  `Optional[X]`, `list[T]`/`dict[K, V]` rather than `typing.List`, `datetime.UTC` rather than
  `timezone.utc` (`src/remarkable_spec/sync/models.py:9,15`). Do not write `Optional[...]`,
  `typing.List`, or `timezone.utc` when quoting or paraphrasing this codebase's style.
- **Concurrency model: synchronous everywhere except one thread pool.** There is no `async def` and
  no `asyncio` anywhere in `src/`. The single concurrency site in the whole codebase is
  `src/remarkable_spec/ocr/postprocess.py:18` (`from concurrent.futures import ThreadPoolExecutor`),
  used at `:131` as `with ThreadPoolExecutor(max_workers=2) as pool:` to run Apple Vision and AWS
  Textract concurrently. So the "parallel OCR" wording in `README.md:20` and `CLAUDE.md` is accurate,
  but it lives in `postprocess.py`, **not** in `src/remarkable_spec/ocr/pipeline.py` — `pipeline.py`
  is a straight-line orchestrator (`render_rm_to_png` then `transcribe_page`) with no concurrency of
  its own. Attribute the parallelism to the right file.

### Frameworks and libraries — installed versus pinned

Installed versions read from the live `.venv` via `importlib.metadata`; pins from `pyproject.toml`.
Where they differ by a major, the installed one is what the code runs against.

| Package                     | Pin (`pyproject.toml`) | Installed | Note                                       |
| --------------------------- | ---------------------- | --------- | ------------------------------------------ |
| `pydantic`                  | `>=2.10.0` (`:12`)     | 2.12.5    | v2 API only                                |
| `rmscene`                   | `>=0.7.0,<0.8.0` (`:13`)| 0.7.0    | the only upper-bounded pin in the file     |
| `cyclopts`                  | `>=3.0.0` (`:14`)      | **4.6.0** | major ahead of the floor                   |
| `rich`                      | `>=13.0.0` (`:15`)     | 14.3.3    | major ahead of the floor                   |
| `pydantic-settings`         | `>=2.13.1` (`:16`)     | 2.13.1    | exact                                      |
| `pymupdf`                   | `>=1.24.0` (`:17`)     | 1.27.1    |                                            |
| `cairocffi` / `cairosvg`    | `[render]` (`:24-28`)  | 1.7.1 / 2.8.2 |                                        |
| `pillow`                    | `[render]` (`:27`)     | 12.1.1    |                                            |
| `paramiko`                  | `[device]` (`:30`)     | **4.0.0** | major ahead of the `>=3.4.0` floor         |
| `httpx`                     | `[device]` (`:31`)     | 0.28.1    |                                            |
| `weasyprint` / `markdown`   | `[push]` (`:33-36`)    | 68.1 / 3.10.2 |                                        |
| `boto3`                     | `[aws]` (`:37-39`)     | 1.42.59   |                                            |
| `pyobjc-framework-vision`   | `[ocr]` (`:43-46`)     | 12.1      | also `pyobjc-framework-quartz` 12.1        |
| `pytest` / `ruff` / `pyright`| dev group (`:48-53`)  | 9.0.2 / 0.15.4 / 1.1.408 |                         |

- **Pydantic is v2 and v2 only.** All models subclass `pydantic.BaseModel` with `Field(...)`;
  settings use `pydantic_settings.BaseSettings` + `SettingsConfigDict`
  (`src/remarkable_spec/cli/_util.py:9,22-27`).
- **cyclopts is the CLI framework, at 4.6.0.** The idioms actually present in this repo, verified
  against current cyclopts docs via Context7 on 2026-08-28 and still correct in v4:
  `app = cyclopts.App(name=..., help=..., version_flags=(...))`; `@app.default` for a single-command
  app; `@app.command` for a subcommand function; `app.command(<sub_app>, name="<literal>")` to mount
  a child `App`. **Flag help text comes from inline `cyclopts.Parameter(help="...")` annotations**
  — for example `src/remarkable_spec/cli/render_cmd.py:80,84` — not from numpydoc docstring
  `Parameters` sections. No CLI command function in this repo has a numpydoc `Parameters` block; the
  only one anywhere in `cli/` is on the non-command helper `get_xochitl_dir`
  (`src/remarkable_spec/cli/_util.py:94-95`). Do not write that cyclopts parses docstring parameter
  sections here.
- **`rmscene` 0.7.0 is the v6 `.rm` binary parser** and the only upper-bounded dependency, which
  is itself a signal: `<0.8.0` says the maintainer expects breaking changes.
  `src/remarkable_spec/formats/rm_file.py` wraps it and, at import time, sets
  `logging.getLogger("rmscene").setLevel(logging.ERROR)` (`src/remarkable_spec/formats/rm_file.py:31`)
  to suppress rmscene's warnings process-wide. That module keeps its own logger at `:33` and does emit
  warnings for unknown pen types (`:162`) and unknown colors (`:169`), each of which silently falls
  back to a default (`FINELINER_1`, `BLACK`).
- **PyMuPDF (`pymupdf` 1.27.1) rasterizes PDF backgrounds** — see `src/remarkable_spec/render/pdf_bg.py`
  (61 LOC) and `src/remarkable_spec/export/pdf.py`.
- **`sqlite3` is stdlib, not a dependency.** `src/remarkable_spec/sync/db.py:6-7` says so explicitly.
  WAL journal mode and `foreign_keys=ON` are set per connection at
  `src/remarkable_spec/sync/db.py:54-55` (`:53` is `row_factory`, not a pragma). There is no ORM, no SQLAlchemy, no Alembic; migrations are
  hand-written SQL in `src/remarkable_spec/sync/migrations.py`.

### Test tiers this repo uses

**pytest 9.0.2**, maintained, configured at `pyproject.toml:74-76`. **It validates nothing today** —
`tests/` holds one empty `__init__.py`. There is no `pytest-cov`, no `hypothesis`, no `tox`, no
`nox`, no CI workflow directory (`.github/` is absent). Do not name a runner or a tier this repo
does not have.

### Supply chain and lints

- **ruff 0.15.4** — lint (`E, F, I, N, UP, B, SIM, RUF` minus `RUF022`, `N806`) and format, line
  length 99. Runs at pre-commit via lefthook on staged files only, and repo-wide via `mise lint`.
- **pyright 1.1.408** is the installed and configured type checker (`pyproject.toml:70-72`,
  `typeCheckingMode = "standard"`), while `mise typecheck` (`mise.toml:12`) and `README.md:142`
  invoke **`ty`** — which fails on this commit with 8 errors. The
  configured checker and the invoked checker are different tools. Flag, do not resolve.
- **What nothing covers**: no advisory scanner (`pip-audit`, `osv-scanner`, `safety`), no license
  check, no secret scanner in the pre-commit chain, no SBOM, no CI. `uv.lock` (181 KB) is committed,
  so dependency resolution is reproducible even though nothing audits it. Repomix's own scan
  reported no suspicious files at pack time, which is a one-off observation, not a gate.

### Build and packaging

Backend is `uv_build>=0.10.7,<0.11.0` (`pyproject.toml:55-57`) — src-layout, `src/remarkable_spec/`.
`src/remarkable_spec/py.typed` is present and tracked, so the distribution ships inline type
information. There is no compiled extension and no cross-compilation; `dist/` exists locally but is
gitignored (`.gitignore:5-6`) — **never cite a path under `dist/`, `.venv/`, `.codegraph/`,
`.pytest_cache/`, `.ruff_cache/`, `.erpaval/`, `.claude/`, or `docs/.repomix/`. The reason is
trackedness, not `.gitignore`:** only `dist/` and `.venv/` are actually ignored at the repo root, so
`git check-ignore` returns non-zero for the other five and is a false-negative gate on its own. The
reliable test is `git ls-files --error-unmatch <path>` — 67 paths are tracked and those are the only
citable ones. The one exception is `docs/.repomix/codebase.json`, which you read but must
not cite.

Native dependencies that are the real portability constraint: `libcairo` (via cairocffi/cairosvg,
auto-pointed at `/opt/homebrew/lib`), the Apple Vision and Quartz frameworks (macOS-only, via
pyobjc), and the optional external binary `mmdc` (mermaid-cli) invoked through `subprocess.run` at
`src/remarkable_spec/ocr/diagram.py:231`, `src/remarkable_spec/cli/diagram_cmd.py:288`, and
`src/remarkable_spec/device/push.py:128`.

### Stale-prior traps — do not write these

**This subsection is BINDING.** A statement listed here is one you might be about to write and which
is false for this codebase. Citing it is an invention rather than an inaccuracy. Each bullet is
phrased as **the wrong sentence**, so you recognise it if you start to type it.

- *"Covered by tests in `tests/test_render.py`."* — There are no test files. `tests/` contains one
  0-byte `__init__.py`. No test module of any name exists.
- *"`mise check` runs the test suite."* — `check` depends on `lint`, `format`, `typecheck` only
  (`mise.toml:13`). `test` is a separate task (`mise.toml:9`) and the suite is empty.
- *"Type checking is done by pyright as part of the standard workflow."* — The configured checker is
  pyright; the **invoked** one in both `mise.toml:12` and `README.md:142` is `ty`. Naming only one is
  wrong either way. And *"the type gate passes"* is wrong outright: `ty check src/` exits non-zero
  here with 8 errors.
- *"This project targets Python 3.12."* — It is *linted and type-checked* as 3.12 and *runs on*
  3.13.11. Four declarations disagree; do not collapse them.
- *"cyclopts 3.x"* / *"built with Click"* / *"built with Typer"* / *"uses `argparse`"* — none are
  present. The framework is cyclopts **4.6.0**. There is no `@click.option`, no `typer.Option`, no
  `@app.callback`, no `add_typer`, no `ArgumentParser` anywhere in `src/`.
- *"`pipeline.py` runs Vision and Textract in parallel."* — The parallelism is a
  `ThreadPoolExecutor(max_workers=2)` inside `src/remarkable_spec/ocr/postprocess.py:131`.
  `pipeline.py` calls `render_rm_to_png` then `transcribe_page` in sequence and contains no executor.
  Also do not write *"the codebase is fully synchronous"* — that one thread pool is the exception.
- *"The `.rm` parser logs at default level."* — `src/remarkable_spec/formats/rm_file.py:31` sets
  `logging.getLogger("rmscene").setLevel(logging.ERROR)` at import time, suppressing rmscene's
  warnings globally for any process that imports the module.
- *"OCR calls Bedrock via the `converse` API."* — It calls `client.invoke_model(...)` with a raw
  `anthropic_version: "bedrock-2023-05-31"` body (`src/remarkable_spec/ocr/postprocess.py:200-228`,
  `src/remarkable_spec/ocr/diagram.py:304-330`, `src/remarkable_spec/cli/annotations_cmd.py:272-297`).
  `converse` appears nowhere.
- *"Model IDs are read from configuration."* — They are hardcoded string literals in four files
  (listed in § 2). `RmspecSettings` has no model field; grep it before claiming otherwise.
- *"The sync database uses SQLAlchemy / an ORM / Alembic migrations."* — Stdlib `sqlite3` and
  hand-written SQL in `src/remarkable_spec/sync/migrations.py`. No ORM in the dependency tree.
- *"`Optional[Path]`" / "`typing.List[str]`" / "`timezone.utc`"* — the codebase writes `Path | None`,
  `list[str]`, `datetime.UTC`. Quoting it the old way misrepresents the source.
- *"An import-linter / dependency-cruiser rule enforces the layering."* — Nothing enforces it. The
  layering in § 2 is measured from imports, and is a convention only.
- *"There is a CI pipeline / GitHub Actions workflow."* — `.github/` does not exist. The only
  automation is lefthook pre-commit and commit-msg hooks.
- *"reMarkable 2 is supported."* — `README.md:12` says other models are **untested**; screen
  detection is automatic from stroke extents so rM2 "may work but is not verified".
  `RM2_SCREEN` existing in `src/remarkable_spec/models/screen.py` is not the same fact as support.
- *"`RMSPEC_` settings include a device port / timeout / region."* — `RmspecSettings`
  (`src/remarkable_spec/cli/_util.py:13-64`) has exactly seven fields: `xochitl`, `device_host`,
  `device_user`, `device_password`, `thickness`, `dpi`, `sync_db`. AWS region is a function argument
  with its own default, not a setting.
