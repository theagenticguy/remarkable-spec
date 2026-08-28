# remarkable-spec · Dead code

`remarkable-spec` ships two surfaces from one distribution: a `rmspec` console script
(`pyproject.toml:20-21`) and an importable library whose root `__all__` re-exports 26 names
(`src/remarkable_spec/__init__.py:33-67`). That shapes every finding below. A symbol published in an
`__all__` with no internal caller is the normal, intended state for the library surface, so those
symbols are partitioned into their own subsection and are not deletion candidates.

No dead-code analyzer is wired into this project — `pyproject.toml`, `mise.toml`, and `lefthook.yml`
contain no `vulture`, `deadcode`, or equivalent. The reference index behind this page was built by
parsing all 55 `.py` files under `src/remarkable_spec/` with Python's `ast` module and cross-checking
every candidate with a whole-word text grep over `src/`, then a third time against the CodeGraph
`callers` index. The grep step is load-bearing rather than belt-and-braces: this codebase uses
function-local lazy imports to keep optional extras out of CLI startup
(`src/remarkable_spec/cli/_util.py:80`, `src/remarkable_spec/device/sync.py:325-326`,
`src/remarkable_spec/ocr/pipeline.py:46-49`), and a name-resolved index can miss those edges. One
symbol below survived candidacy only because grep caught its sibling's lazy-import call site.

The headline finding is not a single symbol. The `ocr_cache` SQLite table has a schema
(`src/remarkable_spec/sync/migrations.py:49-61`), a typed accessor API
(`src/remarkable_spec/sync/db.py:174`, `:192`, `:216`), and a legacy importer
(`src/remarkable_spec/sync/migrations.py:113`), and no live code path reaches any of them — the
shipping OCR cache is a `.ocr.txt` file on disk (`src/remarkable_spec/cli/search_cmd.py:196`,
`:204-207`). Read one at a time each symbol looks like an unused helper; read together they are an
unreachable subsystem.

Last-modified dates are all `2026-03-06`: the repository has a single commit
(`git log --format=%cs | sort -u`), so the column carries no recency signal and no ranking is implied
by it.

## Unreferenced exports

### Internal symbols with zero references — deletion candidates

Module-level definitions that appear in no `__all__` and have exactly one whole-word occurrence in
`src/` — their own `def` line. Both are public-named (no leading underscore) but unpublished, so
nothing outside the repository can reach them either.

| Symbol | Path | Last modified |
| --- | --- | --- |
| `classify_page` | `src/remarkable_spec/ocr/diagram.py:115` | 2026-03-06 |
| `migrate_ocr_sidecars` | `src/remarkable_spec/sync/migrations.py:113` | 2026-03-06 |

`classify_page` is a standalone Bedrock classification call that returns a `PageContentType`
(`src/remarkable_spec/ocr/diagram.py:115-142`). Its work is duplicated inside `extract_mermaid`, whose
combined prompt classifies and extracts in one request (`src/remarkable_spec/ocr/diagram.py:73-112`),
and it is `extract_mermaid_from_rm` (`src/remarkable_spec/ocr/diagram.py:174`) that the CLI actually
calls (`src/remarkable_spec/cli/diagram_cmd.py:213`, `:237`). The module's own usage docstring
advertises `extract_mermaid_from_rm` and never mentions `classify_page`
(`src/remarkable_spec/ocr/diagram.py:12-16`). It is absent from `ocr`'s `__all__`, which holds only
`ocr_image` and `ocr_page` (`src/remarkable_spec/ocr/__init__.py:7`). Deleting it removes one of the
four hardcoded copies of the `global.anthropic.claude-opus-4-6-v1` model ID
(`src/remarkable_spec/ocr/diagram.py:57`).

`migrate_ocr_sidecars` imports legacy `.ocr.txt` sidecar files into the `ocr_cache` table
(`src/remarkable_spec/sync/migrations.py:113-151`). Its module docstring states the module is "called
automatically on first access to the sync database" and both creates tables and "migrates legacy
`.ocr.txt` sidecar files" (`src/remarkable_spec/sync/migrations.py:1-6`) — but only half of that is
true. The sibling `init_schema` (`src/remarkable_spec/sync/migrations.py:99`) is genuinely invoked on
first connection, through a function-local lazy import at `src/remarkable_spec/sync/db.py:57` and a
call at `:59`. `migrate_ocr_sidecars` has no such call site anywhere. Either the migration never runs
and the docstring is wrong, or it was meant to be wired into the same block in
`src/remarkable_spec/sync/db.py:57-59` and was not. Resolve the docstring before deleting the
function, since the docstring is the only statement of intent.

### Published API with zero internal consumers — reachable by library consumers, not deletion candidates

Everything in this subsection is reachable from outside the distribution: either the name itself is in
an `__all__`, or it is a member or method of a class that is. Zero internal consumers is the expected,
intended state for a library surface, and removing any of these is a breaking API change. They are
listed because the pattern *within* this set is informative — one whole subsystem appears here — not
because they should be deleted.

#### Module-level exported names

| Symbol | Path | Last modified |
| --- | --- | --- |
| `BUILTIN_TEMPLATES` | `src/remarkable_spec/models/template.py:140` | 2026-03-06 |
| `HighlightColor` | `src/remarkable_spec/models/color.py:44` | 2026-03-06 |
| `SCREEN_WIDTH` | `src/remarkable_spec/render/engine.py:29` | 2026-03-06 |
| `SCREEN_HEIGHT` | `src/remarkable_spec/render/engine.py:30` | 2026-03-06 |
| `SCALE` | `src/remarkable_spec/render/engine.py:32` | 2026-03-06 |

Five of the 65 distinct names across the nine `__all__` declarations. `BUILTIN_TEMPLATES` and
`HighlightColor` are exported twice over, from both `src/remarkable_spec/models/__init__.py:47` and
the root `src/remarkable_spec/__init__.py:33`.

The `render` constants are one block of four (`src/remarkable_spec/render/engine.py:28-32`), imported
together at `src/remarkable_spec/render/__init__.py:18-21` and exported at
`src/remarkable_spec/render/__init__.py:41-44`. The fourth, `SCREEN_DPI`
(`src/remarkable_spec/render/engine.py:31`), is kept out of the table because it has one real
consumer — but that consumer is `SCALE = 72.0 / SCREEN_DPI` at
`src/remarkable_spec/render/engine.py:32`, which itself has none. So all four are collectively
unreachable from live code, and the group is only reachable by an external importer.

That matters more than it looks, because the values are reMarkable 2 geometry, labelled as such by
the comment at `src/remarkable_spec/render/engine.py:28`. A library consumer who imports `SCALE`
gets `72.0 / 226`, and 226 DPI is the rM2 panel (`src/remarkable_spec/models/screen.py:80`), not the
Paper Pro's 229 (`src/remarkable_spec/models/screen.py:83`) that this project targets. Live rendering
code never hits that mismatch because it does not read these constants: it takes a `ScreenSpec` and
defaults to `RM2_SCREEN` (`src/remarkable_spec/render/engine.py:126`). The exported constants are a
second, staler representation of the same geometry.

#### Methods of exported classes

Plain methods (no decorator) on classes that are in an `__all__`, with zero whole-word occurrences in
`src/` beyond their own `def` line.

| Symbol | Path | Last modified |
| --- | --- | --- |
| `SyncDB.get_ocr` | `src/remarkable_spec/sync/db.py:174` | 2026-03-06 |
| `SyncDB.put_ocr` | `src/remarkable_spec/sync/db.py:192` | 2026-03-06 |
| `SyncDB.get_all_ocr` | `src/remarkable_spec/sync/db.py:216` | 2026-03-06 |
| `SyncDB.get_page` | `src/remarkable_spec/sync/db.py:162` | 2026-03-06 |
| `SyncDB.find_changed_pages` | `src/remarkable_spec/sync/db.py:319` | 2026-03-06 |
| `WebAPI.search` | `src/remarkable_spec/device/web_api.py:209` | 2026-03-06 |
| `WebAPI.download_rmdoc` | `src/remarkable_spec/device/web_api.py:129` | 2026-03-06 |
| `WebAPI.upload_pdf` | `src/remarkable_spec/device/web_api.py:149` | 2026-03-06 |
| `WebAPI.upload_epub` | `src/remarkable_spec/device/web_api.py:172` | 2026-03-06 |
| `WebAPI.get_thumbnail` | `src/remarkable_spec/device/web_api.py:194` | 2026-03-06 |
| `Palette.get_hex` | `src/remarkable_spec/render/palette.py:62` | 2026-03-06 |
| `Palette.get_css` | `src/remarkable_spec/render/palette.py:76` | 2026-03-06 |
| `Document.base_path` | `src/remarkable_spec/models/document.py:379` | 2026-03-06 |

`SyncDB` is exported at `src/remarkable_spec/sync/__init__.py:25`, `WebAPI` at
`src/remarkable_spec/device/__init__.py:24`, `Palette` at `src/remarkable_spec/render/__init__.py:46`,
`Document` at `src/remarkable_spec/__init__.py:33`.

**The `ocr_cache` table has a typed API, a schema, and an importer, and no live code path reaches any
of them.** This is the strongest single finding on this page, and it only becomes visible when the
symbols are read together rather than one at a time. The three `ocr_cache` accessors —
`SyncDB.get_ocr` (`src/remarkable_spec/sync/db.py:174`), `put_ocr` (`:192`), `get_all_ocr` (`:216`) —
are the only code that reads or writes the table, whose `SELECT`/`INSERT` statements sit at
`src/remarkable_spec/sync/db.py:177`, `:195`, and `:219`. The table itself is created unconditionally
at `src/remarkable_spec/sync/migrations.py:49-61`. Its only other writer is
`migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:141`), which is listed above as an
unreferenced internal symbol. Nothing calls any of them.

What ships instead is a filesystem sidecar. `_search_local` computes
`cache_path = rm_path.with_suffix(".ocr.txt")` at `src/remarkable_spec/cli/search_cmd.py:196`, reads
it when present (`src/remarkable_spec/cli/search_cmd.py:197-198`), and writes the OCR text straight
back to it after calling `ocr_page` (`src/remarkable_spec/cli/search_cmd.py:204-207`) — never
touching `SyncDB`. So the SQLite cache and its one-shot legacy importer are unreachable together: the
importer that would populate the table has no caller, and the accessors that would read it have no
caller. `CLAUDE.md:40` describes `rm_hash` as "the cache invalidation key for OCR and diagram
results", which is true of the schema (`src/remarkable_spec/sync/migrations.py:51`) and not of the
running code, where the cache key is the `.rm` file's own path.

`WebAPI.search` (`src/remarkable_spec/device/web_api.py:209`) is unreached for a different reason: the
`search --device` path reimplements it rather than calling it. `_search_device` builds
`url = f"http://{host}/search/{query}"` at `src/remarkable_spec/cli/search_cmd.py:88` and issues its
own httpx request. The two are not equivalent — `WebAPI.search` performs a client-side substring
filter over `list_documents()` (`src/remarkable_spec/device/web_api.py:220-227`) while the CLI hits a
server-side `/search/` endpoint, so the method's docstring claim of "(on newer firmware) full-text
content" (`src/remarkable_spec/device/web_api.py:212`) does not match its body. The divergence has
also let the response key drift three ways: `WebAPI.search` reads `"VisssibleName"` with three `s`
characters and falls back to `"VisibleName"` (`src/remarkable_spec/device/web_api.py:225-226`), while
`_search_device` reads `"VissibleName"` with two and falls back to `"visibleName"`
(`src/remarkable_spec/cli/search_cmd.py:120`). At most one spelling can be the device's.

Two enum members, both on published enums:

- `PenColor.HIGHLIGHT` (`src/remarkable_spec/models/color.py:37`) is an enum member no code branches
  on. Its only other occurrence in `src/` is a docstring reference at
  `src/remarkable_spec/models/color.py:45`. It is also the one `PenColor` value absent from both
  palette dicts, `RM_PALETTE` (`src/remarkable_spec/models/color.py:89-103`) and `PAPER_PRO_PHYSICAL`
  (`src/remarkable_spec/models/color.py:109-119`), so a stroke carrying colour ID 9 has no RGB
  mapping in either palette. The comment at `src/remarkable_spec/models/color.py:37` explains why —
  the real colour comes from extra block data — but no code reads that path.
- `HighlightColor.ORANGE` (`src/remarkable_spec/models/color.py:56`) has exactly one whole-word
  occurrence in `src/`, its own definition. The other four members
  (`src/remarkable_spec/models/color.py:52-55`) are equally unbranched, for the same reason the
  enclosing class is in the table above: nothing consumes it.

### Candidates dropped as framework-dispatched or table-dispatched

Recorded so a later reader does not re-raise them. Each has zero or near-zero callers by name and is
nonetheless live.

| Symbol | Path | Why it is live |
| --- | --- | --- |
| `inspect_file` | `src/remarkable_spec/cli/inspect_cmd.py:47` | `@app.default`; cyclopts dispatches it, mounted as `inspect` at `src/remarkable_spec/cli/__init__.py:48` |
| `ls_documents` | `src/remarkable_spec/cli/ls_cmd.py:73` | `@app.default`; mounted as `ls` at `src/remarkable_spec/cli/__init__.py:49` |
| `_default` | `src/remarkable_spec/cli/sync_cmd.py:48` | `@app.default` on the `sync` sub-app, mounted at `src/remarkable_spec/cli/__init__.py:55` |
| `PenType` `_2` variants | `src/remarkable_spec/models/pen.py:38-44` | branched on as `cls.X`, not `PenType.X`, in `canonical` (`src/remarkable_spec/models/pen.py:65-73`) and `is_highlighter` (`src/remarkable_spec/models/pen.py:51`) |
| `PageContentType.DIAGRAM`, `.MIXED` | `src/remarkable_spec/ocr/diagram.py:35-36` | reached by value via `for ct in PageContentType` (`src/remarkable_spec/ocr/diagram.py:139-141`) |
| Ten pen renderer classes | `src/remarkable_spec/render/pens.py:136,158,196,218,259,291,314,334,369,403` | constructed in the `match canonical:` dispatch in `get_pen_renderer` (`src/remarkable_spec/render/pens.py:457-480`) |
| 11 `@computed_field` properties | `src/remarkable_spec/models/document.py:351,357,363,369,375`, `src/remarkable_spec/models/page.py:132,138,144`, `src/remarkable_spec/models/screen.py:68,74`, `src/remarkable_spec/models/stroke.py:63` | Pydantic v2 evaluates `@computed_field` on `model_dump()` and includes it in the JSON schema, so serialization is the call site |

The 11 `@computed_field` properties deserve a note, because they are the largest single group of
zero-call-site symbols in the codebase and the easiest to mistake for dead code. Each carries
`@computed_field` above `@property` — for example `Document.is_pdf` at
`src/remarkable_spec/models/document.py:355-359` — and `computed_field` is imported from pydantic in
each of the four model modules (`src/remarkable_spec/models/document.py:22`,
`src/remarkable_spec/models/page.py:13`, `src/remarkable_spec/models/screen.py:11`). Pydantic v2
evaluates them whenever the model is dumped, so they are part of the serialized output of every
`Document`, `Page`, `ScreenSpec`, and `Point`. Deleting one silently changes the JSON shape.

The 18 decorated CLI handlers are all `app.default` or `app.command` in `src/remarkable_spec/cli/`.
Only the three above surfaced as zero-caller candidates; the other 15 have bare names that collide
with unrelated identifiers elsewhere in `src/` (`render`, `ocr`, `search`, `diagram`, `env`, `tree`,
`annotations`, and a four-way `push` / `pull` / `ls` / `info` collision), which inflates their counts
rather than deflating them. Name collision cutting both ways is why each surviving candidate was
re-grepped individually.

## Unreferenced files

_none_

Every one of the 46 non-`__init__.py` files under `src/remarkable_spec/` has at least one inbound
internal import. The thinnest at one inbound edge each are the ten `cli/*_cmd.py` command modules,
`src/remarkable_spec/device/push.py`, `src/remarkable_spec/formats/document_loader.py`,
`src/remarkable_spec/ocr/textract.py`, and `src/remarkable_spec/sync/migrations.py`; the most
depended-on is `src/remarkable_spec/models/page.py` at 16.

The nine `__init__.py` package initialisers have no inbound import statement naming them, and are
nonetheless reachable: Python executes a package's `__init__.py` whenever any module beneath it is
imported, and `pyproject.toml:20-21` names `remarkable_spec.cli:app` as the console-script entry
point, reaching `src/remarkable_spec/cli/__init__.py` directly. Listing them would be an artifact of
counting import statements rather than a finding.

One adjacent observation: `src/remarkable_spec/ocr/__init__.py:5` re-exports `ocr_image` and
`ocr_page`, but no internal caller goes through it — `src/remarkable_spec/ocr/postprocess.py:128` and
`src/remarkable_spec/cli/search_cmd.py:136` both import from the defining module,
`src/remarkable_spec/ocr/vision.py:63` and `:140`. The `remarkable_spec.ocr` namespace therefore
exists for external consumers only. The re-export resolves correctly; this is a routing note, not a
defect.

## Dead imports

_none_

Every import binding in all 55 source files was checked for a whole-word occurrence of its bound name
elsewhere in the same file. 45 bindings had none. 44 are `from __future__ import annotations`, a
compiler directive present in every source file whose entire purpose is that no reference follows it;
it is not a dead import.

The single remaining candidate is a false positive: `import paramiko` at
`src/remarkable_spec/cli/device_cmd.py:54`, inside the `try:` / `except ImportError:` body of
`_check_device_deps` (`src/remarkable_spec/cli/device_cmd.py:51-62`). Whether the import raises *is*
the function's return value — it probes for the `[device]` extra (`pyproject.toml:29-32`) and prints
an install hint when absent. The source marks the intent inline with `# noqa: F401` on
`src/remarkable_spec/cli/device_cmd.py:54`.

This bucket being empty is corroborated rather than merely asserted:
`uvx ruff check src/ --select F401,F811,F841 --no-fix` reports `All checks passed!`. `F` is already in
this repo's committed lint selection (`pyproject.toml:64`) and lefthook runs ruff on staged Python at
pre-commit (`lefthook.yml:1-11`), so the clean result indicates the gate is honoured repo-wide.

## See also

- [contract map](../insights/contract-map.md) — 31 shared source citations
- [impact analysis](../insights/impact-analysis.md) — 28 shared source citations
- [business logic](../insights/business-logic.md) — 27 shared source citations
- [module map](../architecture/module-map.md) — 24 shared source citations
- [tech debt](../insights/tech-debt.md) — 24 shared source citations
