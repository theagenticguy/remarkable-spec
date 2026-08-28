---
role: doc-insights-contract-map
model: opus
output: "docs/insights/contract-map.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · insights/contract-map.md

<write_protocol>
Your task packet file is the single source of truth for what you've done, decided, and verified. Edit it after every meaningful step, before starting the next one. Partial progress written to disk survives timeouts, interrupts, and orchestrator context pressure; state held in working memory does not.

The rhythm is: one action → edit the packet with the outcome → next action. One exchange at a time.

Work through your sections in numbered order. For each section:

1. Do one unit of work — read a source file, search for a pattern, draft a Markdown block, write the output file.
2. Edit your packet file under that section with what happened — the exact files read, the search query, the decision made, any surprises.
3. If the section needs more depth, do another unit and edit again.
4. Move to the next section only after the current one has real content.

If a check fails (empty search result, schema mismatch, missing file): write the failure to the packet's Fallback paths or Work log, then execute the documented fallback, then edit again with the outcome. Keep the file ahead of your working memory at all times.

**Cite every factual claim with a backtick `path:LOC` reference** (`repo:path:LOC` in multi-repo mode). A further reference to a file already cited in full may shorten to `` `:LOC` ``; a shorthand with no full path before it in the same section — or in the same table row — is an error, because nobody can resolve it. Citations let the orchestrator and future reviewers trace every line back to source.

When every section has real content and every Success criterion is checked off, change `status: IN_PROGRESS` in the packet frontmatter to `status: COMPLETE`.
</write_protocol>

## Contents

- Objective
- Scope
- Inputs
- Process
- Output format rules
- Discovery toolkit
- Fallback paths
- Success criteria
- Anti-goals
- Out-of-scope findings
- Work log
- Validation
- Summary

## 1. Objective

Produce `docs/insights/contract-map.md`: one content H2 per inter-module contract in `remarkable-spec`, ordered by consumer count, capped at 12 content H2s. Each content H2 captures producer, consumer(s), the verbatim shape, the assumptions consumers make, and any drift risk.

The reader's question this file answers: *"When module A passes something to module B, what is B really expecting?"*

## 2. Scope

- Create: `docs/insights/contract-map.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: type declarations crossing module boundaries (shared types files, `types.ts`, `schemas/`, `models/`), event-payload shapes, RPC envelopes, function signatures at module boundaries.
- The import graph (to identify which consumers reach which types).
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json`.

## 3a. Orchestrator directives (BINDING for this run)

These are repo-specific and override any general habit. The shared environment brief covers all four
in more depth; they are repeated here because they are cheap to forget mid-draft.

1. **No tests exist.** `tests/` holds exactly one file, a 0-byte `__init__.py`. Never write that
   anything is tested, covered, verified by tests, or regression-protected. Never cite a path under
   `tests/` other than that empty file. If a template section or success criterion asks you to
   report test coverage, record in the Work log that the signal is structurally absent and move on.

2. **Never cite a gitignored or generated path.** Phase 6 runs `git check-ignore` over every
   citation and a single hit fails the whole run. Off-limits: `dist/`, `.venv/`, `.codegraph/`,
   `.pytest_cache/`, `.ruff_cache/`, `.erpaval/`, `.claude/`, and `docs/.repomix/` — you read the
   flattened pack, you never cite it. `git ls-files` is the definitive list of citable paths; there
   are 67 of them and 56 are `.py`.

3. **Braces.** This codebase writes reMarkable filenames as a UUID placeholder in braces — you will
   encounter that spelling in docstrings at `src/remarkable_spec/models/document.py:8` and
   `src/remarkable_spec/device/paths.py:22` and will be tempted to reproduce it. A bare brace
   outside a fenced block or an inline code span breaks the downstream MDX publisher with an error
   that names neither the page nor the field. So in every heading, table cell, and paragraph, either
   wrap the whole value in backticks as inline code, or write it in prose as "the per-document
   UUID". Braces inside a fenced block, including a mermaid fence, are safe and need nothing.

4. **Do not run billable or device commands.** Off-limits: `rmspec ocr`, `rmspec diagram`,
   `rmspec annotations`, `rmspec sync pull`, `rmspec sync push`, `rmspec device *` — these reach AWS
   Textract and Bedrock `invoke_model`, or SSH to `10.11.99.1` where no device is attached.
   `rmspec --help` and `rmspec <subcommand> --help` are offline, free, and encouraged. Reading any
   source file is always free.

5. **Attribute the OCR concurrency to the right file.** The only concurrency in the entire codebase
   is `ThreadPoolExecutor(max_workers=2)` at `src/remarkable_spec/ocr/postprocess.py:131`. The
   module docstring of `src/remarkable_spec/ocr/pipeline.py:1` describes a "parallel OCR" step, but
   `pipeline.py` itself is straight-line. Do not place the parallelism in `pipeline.py`, and do not
   describe the codebase as fully synchronous either.

### Boundaries worth mapping in this repo

The layering is clean and acyclic, which makes the contracts unusually legible. Measured import
direction (counts are import-statement counts, from the environment brief):

```text
models   leaf, zero outbound internal edges
sync     leaf, zero outbound internal edges
formats  -> models (8)
render   -> models (6)
export   -> models (6), render (4)
device   -> sync (4)
ocr      -> models (4), export (2), formats (2)
cli      -> models (15), formats (13), device (11), ocr (8), sync (5), render (5), export (3)
```

**Nothing enforces this.** There is no import-linter config, no dependency test, no lint rule. Say
"the code currently imports in this direction", never "the build enforces it" — that distinction is
the whole value of the sentence.

The highest-value contracts to document, beyond the module-to-module ones:

- **`formats` to `models`**: parsers produce `Stroke`, `Layer`, `Page`, `Document` instances, so the
  Pydantic model definitions are the schema every parser must satisfy. `parse_rm_file` returning
  `list[Layer]` (`src/remarkable_spec/formats/rm_file.py`) is the load-bearing shape.
- **`rmscene` to `formats`**: the one external contract that is version-pinned on both sides
  (`rmscene>=0.7.0,<0.8.0`, `pyproject.toml:13`). Name which rmscene types cross the boundary and
  where they are converted into local models, because that conversion layer is what an rmscene
  upgrade breaks.
- **`sync/models.py` to the SQLite schema**: two hand-maintained mirrors of the same shape. The
  Pydantic models are at `src/remarkable_spec/sync/models.py`; the actual `CREATE TABLE` statements
  are SQL string literals in `src/remarkable_spec/sync/migrations.py`. Nothing generates one from
  the other and nothing tests that they agree. Document the columns from the migration file, not
  from the models, and flag any field present in one and absent from the other as a finding.
- **`device` to the reMarkable device**: SSH via paramiko (`src/remarkable_spec/device/connection.py`)
  and HTTP via httpx (`src/remarkable_spec/device/web_api.py`), against a default host of
  `10.11.99.1` (`src/remarkable_spec/cli/_util.py:36-39`). The remote filesystem layout is an
  undocumented contract with the vendor's `xochitl` process; `src/remarkable_spec/device/paths.py`
  is where that assumption is written down.
- **`ocr` to AWS**: Bedrock `invoke_model` with a raw
  `anthropic_version: "bedrock-2023-05-31"` body and a hardcoded Opus model ID, at three
  independent call sites (`src/remarkable_spec/ocr/postprocess.py:200-228`,
  `src/remarkable_spec/ocr/diagram.py:304-330`, `src/remarkable_spec/cli/annotations_cmd.py:272-297`),
  plus Textract at `src/remarkable_spec/ocr/textract.py:37`. The prompt and the expected response
  shape are the contract, and each of the three sites owns its own copy of it.
- **`cli/_util.py` to the whole process**: importing it mutates `os.environ`
  (`src/remarkable_spec/cli/_util.py:68-72`) and instantiates a module-level settings singleton
  (`:62`). Any importer inherits both. That is a contract even though it has no signature.
- **The external `mmdc` binary**: invoked through `subprocess.run` at three sites with a timeout
  branch each. The contract is a CLI invocation and an exit code, with absence of the binary as an
  expected state.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Define "contract" for this codebase. State the definition in the intro. Default: any interface, type alias, or schema declared in one module and referenced by ≥ 1 other module. Adapt as needed (e.g., for codebases with explicit event-bus payloads, include those).
3. Enumerate candidate contracts. Find every shared type declared in one module and imported elsewhere. Use grep for type-name references across modules.
4. For each candidate, identify producer (declaration site, `path:LOC`) and consumer(s) (every site that imports or matches the shape, with `path:LOC`).
5. Rank by consumer count descending — the code index's per-symbol consumer counts produce the ranking directly, and every edge it reports is confirmed at an import or call site before it earns a row. Cap at 12 content H2 contracts; overflow goes to `## Other contracts` as one-liners.
6. For each top-12 contract, extract the verbatim shape from the producer file. Fence it as a code block with the appropriate language tag.
7. Identify assumptions consumers make beyond what the type signature expresses. Read each consumer site for: switch/match exhaustiveness, null/empty handling, ordering assumptions, range assumptions, side-effect expectations. Cite each assumption at the consumer site.
8. Assess drift risk. If the producer adds a new field, a new variant, or changes a base type, what breaks silently? Write one sentence + a one-line mitigation. Skip if no plausible drift.
9. Write `docs/insights/contract-map.md` with H1 = `# remarkable-spec · Contract map`.

## 5. Output format rules

- H1 = `# remarkable-spec · Contract map`. No decorative titles.
- No YAML frontmatter on the output file.
- Intro defines "contract" for this codebase.
- One content H2 per contract (max 12). Each content H2 contains:
  - `**Producer:** <backtick path:LOC>` — declaration site.
  - `**Consumer(s):**` followed by a bullet list of citations.
  - `**Shape:**` followed by a fenced code block with the shape verbatim.
  - `**Assumptions consumers make:**` followed by a bullet list (each bullet cites the consumer site).
  - `**Drift risk:**` one sentence + a one-line mitigation, or skipped if no plausible drift.
- The final content H2, `## Other contracts`, carries overflow as one-liners.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** type-declaration files and consumer sites.
- **Grep** for type-name references across the codebase to confirm each consumer the code index ranks.
- **Glob** to enumerate shared-types directories (`types/`, `schemas/`, `models/`, `shared/`).
- **Bash** for `jq` over `docs/.repomix/codebase.json` to bulk-list consumer sites in one shot.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Codebase is untyped** (no static type declarations): infer contracts from function signatures + inline validation. State the methodology in the intro. The Shape block becomes a prose description with cited validator code.
- **Too few cross-module types** (codebase is small or single-module): include same-module type contracts as long as the producer file and the consumer file differ. Note the looser scope in the intro.
- **A contract has only 1 consumer:** include it only if the consumer site is structurally important (an entry point, a public boundary). Otherwise drop or route to `## Other contracts`.
- **Drift risk is hard to assess for a contract** (e.g., the shape has been stable for years): write a single-line assessment and explicit "no current drift risk" if that's the honest answer.

## 8. Success criteria

- [x] `docs/insights/contract-map.md` exists on disk. 1,219 lines, 66,981 bytes.
- [x] H1 line reads `# remarkable-spec · Contract map`. Scripted equality check, and exactly one H1
      once fenced blocks are stripped.
- [x] Intro defines "contract" explicitly — the `**"Contract" here means**` paragraph, deliberately
      broader than "cross-module type" so the SQLite DDL and the `cli/_util.py` import side effects
      are in scope.
- [x] Between 3 and 12 content H2 contract sections — **11 contract H2s plus `## Other contracts`
      = 12 content H2s.** Deliberately reduced from 12 + overflow = 13: the criteria block exempts
      only the `## See also` footer from H2 counts, so `## Other contracts` counts, and 13 would
      breach the cap under that reading. `PenType` and `PenColor` were merged into one H2 (they
      share a producer package, a consumer set, and a failure mode) to land at 12 under every
      reading.
- [x] Every content H2 has Producer, Consumer(s), Shape, Assumptions, and Drift risk fields —
      scripted per-section presence check over all 11; zero Drift risk fields skipped.
- [x] Every Shape block is fenced code, quoted verbatim from the producer file — **21 fences
      checked by string-equality against the exact cited line range**, zero mismatches. The one
      remaining fence is the measured import-direction `text` block, which is derived data, not a
      source quotation, and the prose above it says so.
- [x] Every Consumer bullet and every Assumption bullet has a backtick `path:LOC` citation —
      541 citations total, all resolving. Also verified there are no `Identifier:LOC`
      pseudo-citations (13 were found and converted to real paths).
- [x] No YAML frontmatter on the output — file begins with the H1.
- [x] Prior-artifact check ran: **the output path held no file.** `docs/` contained only `.packets/`
      and `.repomix/` at start, so nothing was inherited and nothing needed re-verification.
- [x] The Work log names what the prior artifact got wrong — records that **no prior version
      existed**, with the `ls` result and the empty `git log` for the path as evidence.
- [x] No citation resolves into a generated or gitignored path — `git check-ignore` run on every one
      of the 541 cited paths, zero hits.

Additional self-imposed checks, all passing (`/tmp/doc-insights-contract-map/final_check.py`):
fences balanced; no triple backticks in prose; no bare braces outside fences or inline code
(directive 3); no emojis; no filler adverbs; no test/coverage claim, no `tests/` citation
(directive 1); no `Optional[`, `typing.List`, `timezone.utc`, `.converse(`, `cyclopts 3`,
`GitHub Actions`, or "import-linter enforces" (stale-prior traps); contracts numbered 1..11 with
`## Other contracts` last.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent shapes. Quote verbatim from the producer file or describe a runtime validator with cited code.
- Do not invent consumers. Every consumer cites a verified import or shape-match site.
- Do not exceed 12 content H2 contracts. Overflow belongs in `## Other contracts`.
- Do not skip the Assumptions field — it's the load-bearing entry of this file. If a contract has no implicit assumptions, write `_no implicit assumptions identified_` rather than omit the field.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

- `src/remarkable_spec/export/pdf.py:159-168` — for a multi-page input the function builds a
  `cairocffi.PDFSurface` at `:135-157`, discards it, and writes only `page_pdfs[0]`, while its own
  docstring at `:30-31` promises the pages "combined into a single PDF document in the order
  provided". A reader trusts `export_pdf` to produce an N-page PDF and gets a 1-page file; the CLI
  hides it by only ever passing a single-element list (`src/remarkable_spec/cli/render_cmd.py:394-400`).
- `src/remarkable_spec/models/document.py:276` reads the `.content` page key `"redirect"` while
  `src/remarkable_spec/cli/_resolve.py:189` reads `"redir"` and its docstring at `:177-181` states
  the field is named `redir`. Only one spelling can match the device, so one reader always yields
  nothing. A reader trusts `PageRef.redirect`, which `src/remarkable_spec/cli/inspect_cmd.py:245`
  prints, and may be reading a permanently `None` field.
- `src/remarkable_spec/models/color.py:89-103` defines 13 of the 14 `PenColor` members, omitting
  `HIGHLIGHT` (ID 9, declared at `:37`), and `src/remarkable_spec/render/palette.py:57-60` falls
  back to black on a miss. A reader expects highlighter strokes to export in colour and gets pure
  black. Verified by executing `EXPORT_PALETTE.get_rgb(PenColor.HIGHLIGHT)` → `(0, 0, 0)`.
- `src/remarkable_spec/export/png.py:19-28` and `src/remarkable_spec/export/pdf.py:19-26` declare no
  `thickness` parameter, and `src/remarkable_spec/cli/render_cmd.py:373-381` and `:394-400` do not
  pass one. A reader sets `--thickness` or `RMSPEC_THICKNESS`
  (`src/remarkable_spec/cli/_util.py:47-51`) and sees it change SVG output only, with no warning
  that PNG and PDF ignored it.
- `src/remarkable_spec/ocr/vision.py:172` hardcodes `screen=RM2_SCREEN` and sizes the raster from it
  at `:186-187`, while `src/remarkable_spec/ocr/pipeline.py:60` calls `detect_screen`. A reader
  assumes both OCR entry points auto-detect the device and gets Paper Pro pages rasterized at rM2
  dimensions through `ocr_page`.
- `src/remarkable_spec/sync/migrations.py:143` writes `created_at` with SQLite `datetime('now')`
  (naive) while `src/remarkable_spec/sync/db.py:211` writes a timezone-aware ISO string; both are
  read back through `datetime.fromisoformat` at `:189`. A reader comparing two cache entries'
  timestamps gets a `TypeError` when one row came from sidecar migration.
- `src/remarkable_spec/models/stroke.py:38` documents `x` as "0 = left edge", but
  `src/remarkable_spec/render/engine.py:132-134` and `src/remarkable_spec/models/screen.py:101-102`
  both state and compensate for a centre origin. A reader trusting the field description computes
  every horizontal position half a page off.
- `src/remarkable_spec/render/engine.py:29-32` defines `SCREEN_WIDTH`, `SCREEN_HEIGHT`,
  `SCREEN_DPI`, `SCALE` as a second hardcoded copy of the rM2 numbers, re-exported publicly at
  `src/remarkable_spec/render/__init__.py:18-21` with no reader anywhere in `src/`. A reader imports
  them expecting them to track the active `ScreenSpec` and silently gets rM2 constants.
- `src/remarkable_spec/cli/_util.py:75` declares `def get_sync_db():` with no return annotation in a
  package that ships `py.typed`. A reader gets an untyped `SyncDB` at all three call sites.
- `src/remarkable_spec/sync/hasher.py:27` returns `dict[str, str | dict[str, str]]`, and
  `src/remarkable_spec/device/sync.py:379-380` feeds `hashes.get("metadata")` into
  `SyncDocument.metadata_hash`, declared `str | None` at
  `src/remarkable_spec/sync/models.py:36`. A reader assumes the type checker guards that hand-off;
  the union widens at the boundary and only the caller's key choice keeps it sound.
- **Process observation, not a defect claim:** 15 sibling docs under `docs/` appeared on disk during
  this run from the concurrent fan-out. A tree-wide version of my citation validator exits non-zero
  against several of them, but they were mid-write when I scanned, so I am recording only that the
  tree-wide check is not yet green rather than attributing errors to finished artifacts. The
  validator is reusable at `/tmp/doc-insights-contract-map/validate.py` (whole tree) and
  `/tmp/doc-insights-contract-map/validate_one.py` (single file).

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

---

## Work log

### Step 1 — inputs read, prior-artifact check

- Read this packet in full, then `docs/.packets/_environment.md` (442 lines) in full. Its stale-prior
  traps subsection is treated as binding: no test claims, no `converse`, no cyclopts 3.x, no
  import-linter enforcement, no `Optional[...]` spellings, parallelism attributed to
  `src/remarkable_spec/ocr/postprocess.py:131` and never to `ocr/pipeline.py`.
- **Prior-artifact check: no prior artifact exists.** `ls docs/insights` returns
  `No such file or directory (os error 2)`; `docs/` holds only `.packets/` and `.repomix/`. So there
  are no stale line numbers, no fabricated edges, and no inverted rules carried over — nothing was
  inherited and nothing needed re-verification. `git log -1 --format=%cs -- docs/insights/contract-map.md`
  is therefore empty. The file is being built from source in one pass. **What the prior artifact got
  wrong: not applicable — this is a first run over a repo with no `docs/` tree.**
- `git ls-files` confirms 67 tracked paths, 56 of them `.py`, matching the brief. Scratch dir
  `/tmp/doc-insights-contract-map/` created.

### Step 2 — the highest-yield check: `sync/migrations.py` DDL versus `sync/models.py` Pydantic

Read `src/remarkable_spec/sync/migrations.py` (152 lines) and `src/remarkable_spec/sync/models.py`
(121 lines) in full, then `src/remarkable_spec/sync/db.py` (366 lines) to see where the two meet.
Column-by-column diff of six tables against five models:

| Table (`migrations.py`) | Model (`models.py`) | Divergence |
| --- | --- | --- |
| `documents` `:17-29` (11 cols) | `SyncDocument` `:18-46` (11 fields) | names match; `last_synced_at TEXT` is nullable in DDL, `datetime` non-optional in model |
| `pages` `:35-43` (6 cols) | `SyncPage` `:49-62` (6 fields) | same nullable-vs-required split on `last_synced_at` |
| `ocr_cache` `:49-59` (8 cols) | `OCRCacheEntry` `:65-85` (7 fields) | **`id INTEGER PRIMARY KEY AUTOINCREMENT` has no model field** |
| `diagram_cache` `:64-72` (7 cols) | `DiagramCacheEntry` `:88-105` (6 fields) | **same missing `id`** |
| `sync_log` `:77-87` (9 cols) | `SyncLogEntry` `:108-120` (8 fields) | **same missing `id`**; `pages_transferred` and `details` are nullable in DDL, required in model |
| `schema_version` `:93-95` (1 col) | — | **no Pydantic mirror at all** |

Real findings, all confirmed at the reader in `db.py`:
- Three surrogate `id` columns are unrepresentable in the models, so `db.py` cannot return a row
  identity and `get_sync_log` `db.py:298-315` has no stable cursor — it orders by `timestamp DESC`
  only, and ties are resolution-dependent.
- `_row_to_sync_document` `db.py:350` and `_row_to_sync_page` `db.py:364` each paper over the
  nullable `last_synced_at` by substituting `datetime.now(UTC)`, so a NULL silently reads back as
  "synced right now" rather than "never synced".
- `migrate_ocr_sidecars` `migrations.py:143` writes `created_at` with SQLite `datetime('now')`, which
  yields a **naive** `'YYYY-MM-DD HH:MM:SS'` string, while `put_ocr` `db.py:211` writes
  `entry.created_at.isoformat()` from a `datetime.now(UTC)` default (`models.py:14-15`) and is
  therefore **aware**. Both parse through `datetime.fromisoformat` at `db.py:189`, so the same column
  yields two incomparable datetime kinds.

### Step 3 — consumer census (AST, not grep)

Bare grep missed parenthesized multi-line imports, so the census was rebuilt with an `ast` walk over
all 56 source files: `/tmp/doc-insights-contract-map/census.py` → `census.json`. It records every
`ImportFrom` whose module starts with `remarkable_spec`, giving importing file, line, and source
module. Ranking (distinct consuming files outside the producer's own package):
`Page` 9 · `parse_rm_file` 7 · `ScreenSpec` 6 · `RM2_SCREEN` 6 · `detect_screen` 6 ·
`PenType` 4 · `PenColor` 4 · `Stroke` 3 · `Palette`/`EXPORT_PALETTE` 3 · `export_svg` 3 ·
`SyncDocument`/`SyncLogEntry` 2 · `DeviceConnection` 2 · `SyncDB` 2.
`get_xochitl_dir` has 9 consumers but every one is inside `cli` — the packet's "too few cross-module
types" fallback covers it (producer file differs from consumer file).

Cross-checked against the code index (`codegraph callers <sym> -l 100 --json | jq length`):
`Page` 14, `parse_rm_file` 11, `PenType` 7, `PenColor` 7, `ScreenSpec` 7, `Stroke` 6, `Layer` 5.
The index counts call sites rather than files and cross-attributes the name-collision cases the
brief warns about, so the AST census is what the ranking uses and the index only corroborates order.

### Step 4 — contract definition and the 12 chosen sections

Definition adopted: **a contract is any type, schema, function signature, module-level singleton, or
external wire format declared in one file and depended on by at least one other file, where the
depender's correctness rests on properties the declaration does not state.** Broader than
"cross-module type" because two of the highest-consequence boundaries here are not types at all
(the SQLite DDL, and the import-time side effects in `cli/_util.py`).

Chosen, ranked by consumer count then structural importance: `Page`; `parse_rm_file`; the rmscene
`scene_items` boundary; `Stroke`/`Point`; `ScreenSpec` + `detect_screen`; `PenType` renderer
dispatch; `PenColor` palette lookup; the `sync/models.py` ↔ DDL mirror; the Bedrock `invoke_model`
envelope; `DevicePaths`; `cli/_util.py`; the xochitl `.metadata`/`.content` JSON wire format.
Overflow to `## Other contracts`: `Palette`/`EXPORT_PALETTE`, `export_svg`, `OCRResult`, Textract
`Blocks`, `DeviceConnection`/`WebAPI`, `SyncDB`, `hash_document_files`, the `mmdc` CLI,
`ResolvedDocument`, `RenderEngine`, `rasterize_pdf_page`, `Template`/`BUILTIN_TEMPLATES`.

### Step 5 — assumption hunting, with the executable checks that confirmed each

Files read in full: `models/{stroke,page,document,screen,pen,color}.py`, `formats/{rm_file,
document_loader,metadata,content}.py`, `render/{engine,pens,palette}.py`, `export/{svg,png,pdf}.py`,
`ocr/{pipeline,postprocess,vision,textract}.py`, `sync/{db,models,migrations,hasher}.py`,
`device/paths.py`, `cli/{_util,_resolve}.py`; targeted ranges in `ocr/diagram.py`,
`device/{sync,push,connection,web_api}.py`, `cli/{render_cmd,annotations_cmd,diagram_cmd}.py`,
`render/pdf_bg.py`, `__init__.py`, `models/__init__.py`.

Findings confirmed by running code rather than by reading (all offline, no AWS, no device):

```text
$ uv run python -c "...RM_PALETTE / PenColor / PenType introspection..."
PenColor members: 14
RM_PALETTE keys: 13
missing from RM_PALETTE: ['HIGHLIGHT']
missing from PAPER_PRO_PHYSICAL: ['PINK', 'GRAY_OVERLAP', 'HIGHLIGHT', 'GREEN_2', 'YELLOW_2']
HIGHLIGHT -> (0, 0, 0)
PenType members: 18
```

So `PenColor.HIGHLIGHT` (ID 9) has no palette entry and `Palette.get_rgb` returns black for it.

Other confirmed assumptions worth flagging:
- **`redirect` versus `redir`.** `models/document.py:276` reads `p.get("redirect", {})`;
  `cli/_resolve.py:189` reads `page.get("redir", {})` and its docstring at `:177-181` names the field
  `redir`. `cli/inspect_cmd.py:245` prints `pr.redirect` to users. Two spellings of one wire field.
- **`thickness` reaches only the SVG path.** `grep -n thickness` over `export/` returns hits in
  `export/svg.py` only; `export/png.py` and `export/pdf.py` have no such parameter, and
  `cli/render_cmd.py:373-381,394-400` omit it. `RMSPEC_THICKNESS` therefore does nothing for PNG/PDF.
- **Library default screen is rM2, CLI default is Paper Pro.** `render/engine.py:125-126` versus
  `cli/render_cmd.py:353-354`.
- **`ocr/vision.py:172` hardcodes `RM2_SCREEN`** while `ocr/pipeline.py:60` calls `detect_screen`.
- **`render/engine.py:29-32` duplicates the rM2 numbers** as module constants re-exported at
  `render/__init__.py:18-21`; `grep` finds no other reader in `src/`.
- **Four independent readers of the xochitl JSON**: `models/document.py:108,260` (canonical),
  `cli/_resolve.py:56,165,205`, `device/sync.py:275,346,357`.

### Step 6 — drafting

Wrote `docs/insights/contract-map.md` from source. No prior file existed, so nothing was patched;
every line traces to a file read in step 5. Braces in reMarkable filename patterns are kept inside
backticks or fenced blocks per directive 3. No test claim appears anywhere in the output
(directive 1); `tests/` was never cited.

## Validation

Everything mechanically checkable was checked with a script. Final consolidated run:

```text
$ uv run /tmp/doc-insights-contract-map/final_check.py
[PASS] no YAML frontmatter
[PASS] H1 exact — '# remarkable-spec · Contract map'
[PASS] exactly one H1 — ['# remarkable-spec · Contract map']
[PASS] content H2 count <= 12 — 12 H2s (11 contracts + overflow)
[PASS] contract H2s in 3..12 — 11
[PASS] last H2 is 'Other contracts' — Other contracts
[PASS] contracts numbered 1..N
[PASS] all 5 fields present in every contract H2
[PASS] fences balanced — 44 fence lines
[PASS] no odd-backtick prose lines — []
[PASS] no triple backticks in prose — []
[PASS] no bare braces outside fences/code spans — []
[PASS] fence state closed at EOF
[PASS] all Shape fences verbatim from cited lines — 21 checked, bad=[]
[PASS] every citation resolves, in range, not gitignored — 541 citations;
[PASS] no Identifier:LOC pseudo-citations
[PASS] no emojis — set()
[PASS] no filler adverbs (simply/basically) — []
[PASS] 'just' usage reviewed — 0 occurrence(s)
[PASS] no forbidden claims/spellings — []
[PASS] says nothing enforces layering

RESULT: ALL CHECKS PASS
```

### Failures found and fixed during validation

1. **147 partial paths.** The first draft wrote citations like `` `models/document.py:274` `` — neither
   a full path nor the sanctioned `` `:LOC` `` shorthand, so nothing could resolve them.
   `/tmp/doc-insights-contract-map/expand.py` rewrote each to
   `src/remarkable_spec/<rest>` only when the expanded path resolved on disk; 23 further bare
   filenames (`postprocess.py:205-208` and similar) were expanded by explicit module mapping.
2. **A literal triple backtick in prose.** The sentence describing the diagram extractor's regex
   contained ```` ```mermaid ````, which after reflow landed at the start of a line and opened a
   phantom fence. **This silently truncated my own validator**: it reported "ALL CITATIONS RESOLVE"
   over only 333 of 541 citations because everything after that line was treated as fenced. Found by
   asserting the fence-toggle state is closed at EOF, which is now a standing check. Rewritten as a
   fenced `mermaid` block reference with no literal backticks.
3. **One mis-binding shorthand.** `` `:138-139` `` in the `Stroke`/`Point` section sat after a
   `formats/rm_file.py` citation but meant `models/stroke.py:138-139`. In range against the wrong
   file, so only a binding audit caught it. Found by flagging every shorthand whose nearest
   preceding full path is in a different block, then reading all 22 hits; the rest bound correctly
   and were left as shorthand, except 16 promoted to full paths so each bullet resolves on its own.
4. **13 `Identifier:LOC` pseudo-citations** (`FinelineRenderer:143`, `execute:140`, and similar).
   These look like citations and resolve to nothing. All converted to `path:LOC`.
5. **Three out-of-range shorthands** in the `DevicePaths` section that bound to `device/paths.py`
   (46 lines) while meaning `device/sync.py`. Rewritten as full paths.
6. **A 13th content H2.** 12 contract sections plus `## Other contracts` breaches the cap under the
   reading that only `## See also` is exempt. Merged `PenType` and `PenColor` into one H2 to reach 11
   + overflow = 12.

### Spot checks reserved for judgment

- Read the merged Contract 6 end-to-end to confirm the two consumer lists are labelled ("of
  `PenType` —" / "and of `PenColor` —") so the pronouns in each bullet are unambiguous.
- Confirmed each Drift-risk mitigation is actionable rather than restating the risk.
- Confirmed no sentence claims a test, lint rule, or CI gate enforces any contract; the intro states
  the opposite explicitly and names ruff's rule selection as the counter-example.

### Executable evidence behind the sharpest claims

```text
$ uv run python -c "...RM_PALETTE / PenColor / PenType introspection..."
PenColor members: 14
RM_PALETTE keys: 13
missing from RM_PALETTE: ['HIGHLIGHT']
missing from PAPER_PRO_PHYSICAL: ['PINK', 'GRAY_OVERLAP', 'HIGHLIGHT', 'GREEN_2', 'YELLOW_2']
HIGHLIGHT -> (0, 0, 0)
PenType members: 18
```

No billable or device command was run. `boto3.client`, `invoke_model`, `detect_document_text`, SSH to
`10.11.99.1`, `rmspec ocr|diagram|annotations|sync|device` — none were invoked; every claim about
those paths is read from source.

## Summary

Shipped `docs/insights/contract-map.md`: 11 contract sections plus an `## Other contracts` overflow
carrying 14 one-liners, 541 verified citations, and 21 Shape blocks quoted verbatim from their cited
line ranges. "Contract" is defined more broadly than the packet's default — any type, schema,
signature, module-level singleton, or external wire format declared in one file and depended on by
another, **where the depender's correctness rests on properties the declaration does not state** —
because the two highest-consequence boundaries in this repo are not types: the SQLite schema is a SQL
string literal, and importing `cli/_util.py` mutates `os.environ`. Ranking came from an AST walk over
every internal `ImportFrom` in all 56 source files, with the CodeGraph `callers` counts used only to
corroborate the order, since the index cross-attributes the name collisions the environment brief
warns about.

The most surprising assumptions were the ones where two parts of the codebase disagree in writing.
`PenColor.HIGHLIGHT` (ID 9) is declared but absent from `RM_PALETTE`, and `Palette.get_rgb` falls back
to black, so every highlighter stroke exports as pure black — confirmed by execution, not inference.
`ContentInfo.from_json` reads the `.content` key `"redirect"` while `cli/_resolve.py` reads `"redir"`
and documents that spelling, so one of the two readers is permanently returning nothing. `--thickness`
and `RMSPEC_THICKNESS` reach only the SVG exporter; `export_png` and `export_pdf` have no such
parameter, so the setting silently does nothing for the two formats users most likely want. And the
highest-yield check the preamble pointed at paid off: the SQLite DDL and its Pydantic mirror agree on
column *names* everywhere, but three tables carry an `AUTOINCREMENT` `id` no model can represent,
`schema_version` has no mirror at all, `last_synced_at` is nullable in SQL and required in the model
so a NULL reads back as "synced right now", and the sidecar migration writes a naive `datetime('now')`
into the same column that `put_ocr` fills with timezone-aware ISO strings — two incomparable datetime
kinds in one column, with nothing generating or testing either side against the other.
