---
role: doc-insights-impact-analysis
model: opus
output: "docs/insights/impact-analysis.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · insights/impact-analysis.md

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

Produce `docs/insights/impact-analysis.md`: for the top 8 high-impact surfaces in `remarkable-spec`, a downstream-effect table that names every module / file / test that needs to be touched (or carefully validated) when that surface changes.

The reader's question this file answers: *"If I touch X, what else do I have to think about?"* — the blast-radius map that usually only lives in the heads of senior engineers.

## 2. Scope

- Create: `docs/insights/impact-analysis.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase's import graph (built from grep over `import` / `require` / `use` statements, or read from a graph index if one exists).
- The test suite — directory structure and tests' import patterns.
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

### Ranking guidance for this packet

`codegraph impact <symbol> -d 3 --json` and `codegraph callers <symbol> -l 50 --json` are the right
primary tools; the absolute binary path is in the environment brief. Two cautions specific to this
repo:

- **Name collisions will inflate counts.** `app` is a distinct `cyclopts.App` object in each of 12
  CLI modules; `push`, `pull`, and `ls` name both subcommand functions and module functions;
  `_invoke_bedrock_vision` is three different functions; `DEFAULT_MODEL` is two. Confirm every edge
  at an import or call site before you report a blast radius.
- **Lazy imports hide real edges.** Function-local imports are used throughout to keep optional
  extras out of CLI startup (`src/remarkable_spec/cli/_util.py:79`,
  `src/remarkable_spec/device/sync.py:325-326`, `src/remarkable_spec/ocr/pipeline.py:48-51`), so an
  index-derived count can under-report. Cross-check high-value symbols with a text grep.

The surfaces most worth an impact section, from the measured import graph: `models` (imported by
every other module and by the root `__init__`, so a field rename is the widest possible change),
`Page` and `Layer` and `Stroke` specifically, `parse_rm_file`, the `ScreenSpec` and `detect_screen`
pair, `RmspecSettings` (imported as a singleton), the `SyncDB` method surface plus the SQL schema it
mirrors, and `export_svg` as the chokepoint every raster and PDF path funnels through.

**There are no tests, so "N tests would need updating" is not an available downstream effect.** The
honest downstream-effect columns here are: internal call sites, CLI commands whose output changes,
the published `__all__` surface that external importers depend on, on-disk artifacts whose shape
changes (the SQLite schema, exported SVG/PNG/PDF), and cached rows that become wrong rather than
merely stale. That last one is the interesting case: because both caches are keyed only on
`rm_hash`, a change to render or prompt behaviour leaves previously cached OCR and diagram rows
valid-looking and silently wrong. Say so where it applies.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Define what "high-impact surface" means for this codebase. State the definition in the intro. Default: top 8 modules by inbound reference count. Alternatives: every public export, the contract surface, the entry-point handlers. Pick one and stick with it; the reader needs the rule to interpret the table.
3. Pick the top 8 surfaces by the chosen criterion. Record each as `name → defined at path:LOC`.
4. For each surface, enumerate downstream consumers in four buckets:
   - **direct import** — files that import the surface name directly.
   - **indirect** — files that import a module that re-exports the surface, or that consume a struct passed back from the surface.
   - **runtime dispatch** — places where the surface is reached through framework dispatch, dependency injection, plugin loading, or string-keyed lookup.
   - **test** — test files that exercise the surface (often by importing it directly with mocks/fixtures).
5. For each consumer, judge "Touch on change": `yes` (consumer must be touched), `likely` (probably needs review even if no signature change), `no` (only behavioral change would affect it). The judgment is the load-bearing part of the file.
6. Capture implicit contracts under "Blast-radius notes" for each surface — assumptions consumers make that the type signature doesn't express. Examples: "always returns sorted", "throws never; returns Result", "first call must complete before second call." Cite each note.
7. Draft each content H2 per the document-templates contract. Each content H2 starts with `Defined at: <backtick path:LOC>` and contains a downstream table + 1–3 blast-radius notes.
8. If more than 8 surfaces matter, append a trailing `## Other notable surfaces` H2 with one-line entries.
9. Write `docs/insights/impact-analysis.md` with H1 = `# remarkable-spec · Impact analysis`.

## 5. Output format rules

- H1 = `# remarkable-spec · Impact analysis`. No decorative titles.
- No YAML frontmatter on the output file.
- Intro defines "high-impact surface" explicitly.
- One content H2 per surface (max 8). Each content H2 starts with `Defined at: <backtick path:LOC>`.
- Downstream table columns exactly: `Downstream | Type | Touch on change | Citation`.
- `Type` is one of: `direct import` / `indirect` / `runtime dispatch` / `test` / `config`.
- `Touch on change` is one of: `yes` / `likely` / `no`.
- Each content H2 includes a 1–3 bullet `Blast-radius notes` subsection capturing implicit contracts.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Grep** for import statements referencing each surface's name.
- **Read** consumer files to verify the kind of usage and assess "Touch on change."
- **Glob** to enumerate the test directory.
- **Bash** for ad-hoc graph queries; `jq` over `docs/.repomix/codebase.json` if present (e.g., to list every file that contains the surface name in one shot).
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Surface has zero downstream consumers:** include it in `## Other notable surfaces` rather than the main set — a zero-impact surface isn't load-bearing for the reader.
- **Runtime dispatch is unverifiable** (string-keyed plugin loader, etc.): mark Type as `runtime dispatch` and Touch on change as `likely`. Note the uncertainty in Blast-radius notes.
- **Too many direct-import consumers to enumerate** (a surface used by 30+ files): list the top 10 by file size or call count, then add a row `(N more direct imports, see [path glob])` summarizing the remainder.
- **No clear inbound-reference signal** (e.g., heavy runtime dispatch): rank surfaces by the manifest-declared public exports + entry-point handlers instead. State the substitution in the intro.

## 8. Success criteria

- [x] `docs/insights/impact-analysis.md` exists on disk. (450 lines)
- [x] H1 line reads `# remarkable-spec · Impact analysis`. (script-checked by exact string equality)
- [x] Intro defines "high-impact surface" explicitly. (distinct inbound import statements across
      `src/`, AST-measured, plus two named cohort merges and a stated tie-break; ranking table included)
- [x] Between 3 and 8 content H2 surface sections. (8, script-counted, `## Other notable surfaces`
      excluded from the count)
- [x] Every content H2 starts with an `Defined at:` line citing `path:LOC`. (script-checked: first
      non-blank line of each content H2 must begin ``Defined at: ` ``)
- [x] Every content H2's downstream table has the exact 4-column schema. (script-checked against the
      literal header string and a 4-cell count per row)
- [x] `Type` cells use only the closed vocabulary; `Touch on change` cells use only `yes` / `likely` /
      `no`. (script-checked against both sets; 78 rows total)
- [x] Every content H2 has a `Blast-radius notes` subsection with 1–3 cited bullets. (script-checked
      for the `### Blast-radius notes` heading and a 1..3 bullet count; every section has 3)
- [x] No YAML frontmatter on the output. (script-checked: line 1 is not `---`)
- [x] Prior-artifact check ran: the output path held no file. `docs/insights/` did not exist and
      `git log -1 --format=%cs -- docs/insights/impact-analysis.md` returned empty. Nothing inherited.
- [x] The Work log names what the prior artifact got wrong — recorded as **no prior version existed**
      (Step 1). Instead of stale-claim triage, the Validation section lists the six defects the
      validator found in this run's own first draft and how each was fixed.
- [x] No citation resolves into a generated or gitignored path. (`git check-ignore --stdin` over all 47
      distinct cited paths: 0 hits; plus a `git ls-files` membership check, 0 untracked)

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent consumers. Every row traces to a verified grep hit or read.
- Do not soften the "Touch on change" judgment. The reader will rely on it — overcautious "yes" labels are noise; missed "yes" labels are bugs.
- Do not exceed 8 main content H2 surfaces; overflow goes to `## Other notable surfaces`.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `src/remarkable_spec/export/pdf.py:168` — the multi-page branch ends with
  `output.write_bytes(page_pdfs[0])`, overwriting the composited surface with page 1 only, while the
  docstring at `:31` promises "Pages are combined into a single PDF document in the order provided".
  A reader exporting a multi-page notebook to PDF gets a one-page file and no error.
- `src/remarkable_spec/export/pdf.py:138-157` — the `cairocffi` loop opens a `PDFSurface` per page,
  paints white, calls `show_page`, and never draws the page bytes it just rendered, so the whole loop
  is unreachable work whose only effect is temp-file churn. A reader tracing PDF output will assume
  compositing happens here.
- `src/remarkable_spec/export/png.py:6-7` — the module docstring says PNG export rasterizes "using
  CairoSVG (preferred) or Pillow as a fallback", but the fallback branch at `:102-112` raises
  `ImportError` twice and never touches Pillow. A reader who installs only Pillow will believe PNG
  export is available.
- `src/remarkable_spec/export/png.py:36-37` — the docstring claims "At 300 DPI with the default RM2
  screen, the output is approximately 1863x2484 pixels", a figure computed from unpadded screen
  dimensions; `SVGRenderer.render_page` writes a viewBox padded by at least 30 points per side
  (`src/remarkable_spec/render/engine.py:139-179`), so the real output is larger. A reader sizing
  downstream buffers from that number under-allocates.
- `src/remarkable_spec/export/svg.py:38-52` — the `Args` block documents seven parameters and omits
  `background_page_size`, which is in the signature at `:26` and forwarded at `:67`. A reader relying
  on the docstring will not know the parameter that controls PDF-background alignment exists.
- `src/remarkable_spec/sync/migrations.py:1-6` — the module docstring says it "migrates legacy
  ``.ocr.txt`` sidecar files into the ``ocr_cache`` table" automatically on first access, but
  `src/remarkable_spec/sync/db.py:57-59` calls only `init_schema`, and `migrate_ocr_sidecars` at `:113`
  has zero call sites. A reader will assume their existing sidecars are in the database.
- `src/remarkable_spec/sync/migrations.py:99-110` — `init_schema` reads `schema_version` only to
  decide whether to insert a row and never compares it to `SCHEMA_VERSION` at `:13`, so the version
  constant is decorative and no upgrade path exists. A reader who bumps it expects a migration to run.
- `CLAUDE.md:40` — "SHA-256 of .rm files (`rm_hash`) is the cache invalidation key for OCR and diagram
  results" is true for diagrams (`src/remarkable_spec/cli/diagram_cmd.py:223`) and false for OCR, which
  caches to `.ocr.txt` sidecars keyed on the page filename with no hash check
  (`src/remarkable_spec/cli/ocr_cmd.py:165-166`, `src/remarkable_spec/cli/search_cmd.py:196-198`). A
  reader will believe editing a page invalidates its OCR text; `rmspec search` keeps serving the old
  transcription.
- `src/remarkable_spec/models/stroke.py:110-111` — the `points` field documents empty strokes as valid
  "e.g. single-tap dots", but `src/remarkable_spec/render/engine.py:258-259` returns early for any
  stroke with fewer than two points. A reader debugging a missing dot will look in the parser rather
  than the renderer.
- `src/remarkable_spec/cli/_util.py:47-55` — `RmspecSettings.thickness` and `.dpi` have zero consumers
  anywhere in `src/`, so `RMSPEC_THICKNESS` and `RMSPEC_DPI` are inert while being documented as
  working env vars in their own `Field` descriptions. A reader who sets them sees no change and no
  warning.
- `src/remarkable_spec/render/engine.py:29-32` — `SCREEN_WIDTH`, `SCREEN_HEIGHT`, `SCREEN_DPI`, and
  `SCALE` hardcode rM2 values, are exported through `src/remarkable_spec/render/__init__.py:41-44`, and
  are read by nothing; `render_page` derives its own scale from `screen.dpi` at `:128`. A reader who
  imports them believes they govern rendering.

---

## Work log

### Step 0 — read inputs

- Read the packet in full, then `docs/.packets/_environment.md` (442 lines) in full. Its stale-prior
  traps subsection is treated as binding: no test claims, no `converse`, no cyclopts 3.x, no
  "fully synchronous", parallelism lives in `ocr/postprocess.py` not `ocr/pipeline.py`, layering is a
  measured convention and not an enforced gate.
- Confirmed the flattened pack exists: `docs/.repomix/codebase.json` (371 KB) plus
  `docs/.repomix/token-tree.txt`. Read-only input; never cited.
- Scratch dir created at `/tmp/doc-insights-impact-analysis/`.

### Step 1 — prior-artifact check (Process step 1)

- `ls docs/insights/` → `No such file or directory (os error 2)`. The `docs/insights/` directory does
  not exist, so `docs/insights/impact-analysis.md` holds no file.
- `git log -1 --format=%cs -- docs/insights/impact-analysis.md` → empty output (path never tracked).
- **There is no prior artifact.** Nothing to re-verify, nothing inherited, no stale citations to
  partition. The whole file is drafted from source on this run. Recorded here to satisfy the two
  Success criteria that ask what the prior version got wrong: it got nothing wrong because it never
  existed. Only `docs/.packets/` and `docs/.repomix/` exist under `docs/` — this is a first run over a
  repo with no docs tree.

### Step 2 — define "high-impact surface" (Process step 2)

Built the import graph mechanically rather than by grep, because grep misses multiline
`from ... import (` blocks and the packet's own caution #2 warns that lazy imports hide edges. Script:
`/tmp/doc-insights-impact-analysis/` (inline `python3` heredoc, `ast.ImportFrom` walk over every
`*.py` under `src/`, filtered to `node.module.startswith('remarkable_spec')`, keyed by
`{module}.{name}` and by `{module}`, deduplicated to distinct `file:lineno` statement sites).

- **192** raw grep hits for `from remarkable_spec`; **288** `import` nodes in the CodeGraph index.
  The AST scan resolves these into per-symbol and per-module distinct-statement counts.
- Criterion chosen: **distinct inbound import statements across `src/`, counted per defining module.**
  Two documented cohort merges (`sync/db.py` + `sync/models.py`; `export/svg.py` +
  `render/engine.py`), and a stated tie-break at equal count. Recorded in the output intro with the
  full ranking table so the reader can apply the rule themselves.

Measured per-module counts (top rows): `models/page.py` 16, `models/screen.py` 14, `cli/_util.py` 14,
`formats/rm_file.py` 10, `models/document.py` 7, `models/pen.py` 7, `cli/_resolve.py` 7,
`sync/models.py` 7, `models/color.py` 6, `export/svg.py` 6, `models/stroke.py` 5, `render/pdf_bg.py` 5,
`device/connection.py` 5, `device/sync.py` 5, `formats/metadata.py` 5, `formats/content.py` 5,
`ocr/vision.py` 5, `render/palette.py` 5, `device/web_api.py` 4, `ocr/diagram.py` 4,
`device/paths.py` 4, `sync/db.py` 3, `ocr/pipeline.py` 3, `sync/hasher.py` 3, `formats/pagedata.py` 3.

### Step 3 — the 8 surfaces (Process step 3)

1. `Page` / `Layer` / `TextBlock` → `src/remarkable_spec/models/page.py:99`, `:45`, `:18` — 16
2. `ScreenSpec` / `detect_screen` → `src/remarkable_spec/models/screen.py:14`, `:86` — 14
3. `RmspecSettings` / `settings` / `get_xochitl_dir` / `get_sync_db` → `src/remarkable_spec/cli/_util.py:13`, `:64`, `:85`, `:75` — 14
4. `parse_rm_file` / `parse_rm_bytes` → `src/remarkable_spec/formats/rm_file.py:46`, `:70` — 10
5. `SyncDB` + the five sync models + the SQL schema → `src/remarkable_spec/sync/db.py:26` — 10 (cohort)
6. `export_svg` + `SVGRenderer.render_page` → `src/remarkable_spec/export/svg.py:18` — 8 (cohort)
7. `DocumentType` / `DocumentMetadata` / `PageRef` / `ContentInfo` / `Document` → `src/remarkable_spec/models/document.py:27,52,166,190,307` — 7
8. `PenType` / `Pen` → `src/remarkable_spec/models/pen.py:17`, `:78` — 7

Overflow (Process step 8): `cli/_resolve.py` (7, demoted by the stated tie-break — all consumers
inside `cli`), `models/color.py` (6), `models/stroke.py` (5), `render/pdf_bg.py` (5),
`render/palette.py` (5), `device/connection.py` (5), `device/sync.py` (5), `ocr/vision.py` (5),
`sync/hasher.py` (3).

### Step 4 — source reads and cross-checks (Process steps 4–6)

Read in full: `models/page.py`, `models/stroke.py`, `models/screen.py`, `formats/rm_file.py`,
`cli/_util.py`, `export/svg.py`, `export/png.py`, `export/pdf.py`, `render/engine.py`,
`ocr/pipeline.py`, `sync/db.py`, `sync/migrations.py`, `sync/models.py`,
`formats/document_loader.py`. Read in part: `ocr/vision.py:140-190`, `cli/render_cmd.py:340-408`,
`cli/ocr_cmd.py:150-215`, `cli/search_cmd.py:180-215`, `models/pen.py:40-75,195-226`,
`render/pens.py:437-480`, `render/palette.py:41-61`, `models/document.py:1-30`.

CodeGraph cross-check (`callers <sym> -l 50 --json`, invoked by absolute path per the brief) agreed
with the AST counts and added function-level call sites for `parse_rm_file`, `export_svg`, and
`detect_screen`. `callers RmspecSettings` returned **zero** — a false negative, since
`cli/_util.py:64` instantiates it one line below the class; this is the index's name-resolution limit
biting, and the reason every count in the output comes from the AST scan rather than the index.

Nine load-bearing findings that came out of the reads, each verified at its cited line:

1. `export_png` (`export/png.py:19-28`) and `export_pdf` (`export/pdf.py:19-26`) have **no
   `thickness` parameter**, so `--thickness` reaches SVG output only (`cli/render_cmd.py:359-401`).
2. `export_pdf`'s multi-page branch ends with `output.write_bytes(page_pdfs[0])`
   (`export/pdf.py:168`) — pages 2..N are dropped despite the docstring at `:31`.
3. `render/engine.py:258-259` drops any stroke with fewer than two points, contradicting
   `models/stroke.py:110-111` ("Empty strokes are valid (e.g. single-tap dots)").
4. `ocr/vision.py:172` hardcodes `screen=RM2_SCREEN`, while `ocr/pipeline.py:60` calls
   `detect_screen`. Two OCR entry points, two screen policies.
5. `_export_page` defaults a missing screen to `PAPER_PRO_SCREEN` (`cli/render_cmd.py:353-354`);
   `export_svg` defaults it to `RM2_SCREEN` (`export/svg.py:63`).
6. The `ocr_cache` table and `SyncDB.get_ocr`/`put_ocr`/`get_all_ocr` (`sync/db.py:174,192,216`) have
   **zero call sites**. `rmspec ocr --save` writes `.ocr.txt` sidecars (`cli/ocr_cmd.py:165-166`) and
   `rmspec search` reads them with no hash check (`cli/search_cmd.py:196-198`).
7. `init_schema` never compares the stored `schema_version` against `SCHEMA_VERSION`
   (`sync/migrations.py:99-110`), and every DDL is `CREATE TABLE IF NOT EXISTS` — so a schema change
   is not applied to an existing database.
8. `migrate_ocr_sidecars` (`sync/migrations.py:113`) has zero call sites.
9. `PenType.canonical`'s alias map (`models/pen.py:65-73`) plus the `case _` fallback at
   `render/pens.py:478-480` means a new pen enum member renders as a fineliner silently.

## Validation

Everything mechanically checkable is checked by one script,
`/tmp/doc-insights-impact-analysis/validate.py`, which exits non-zero on the first problem. It
covers: no YAML frontmatter, exact H1 text, content-H2 count and the 3..8 bound, a `Defined at:` line
opening every content H2, exactly one `| Downstream | Type | Touch on change | Citation |` header per
content H2 with 4 cells per row, the closed `Type` and `Touch on change` vocabularies, every citation
resolving to a real tracked file and an in-range line, shorthand resolution, `git check-ignore` over
every cited path, no emoji or non-ASCII beyond a small typographic allowlist, no filler adverbs, and
no bare brace outside a code span or fence.

Shorthand resolution rule the script enforces: a `` `:LOC` `` resolves against the **nearest preceding
full citation inside the same block**, where a block is a run of contiguous non-blank lines and never
spans an H2 boundary (so each bullet, paragraph, and table row is its own block). That is stricter
than the packet's minimum ("a full path earlier in the same section, or in the same table row") and it
is what caught the one real mis-resolution below.

Final run:

```text
$ python3 /tmp/doc-insights-impact-analysis/validate.py
content H2 count: 8
  L52: `Page`, `Layer`, and `TextBlock`
  L97: `ScreenSpec` and `detect_screen`
  L140: `RmspecSettings` and the `settings` singleton
  L184: `parse_rm_file` and `parse_rm_bytes`
  L229: `SyncDB`, the sync models, and the SQLite schema
  L279: `export_svg` and `SVGRenderer.render_page`
  L329: `DocumentType`, `DocumentMetadata`, `ContentInfo`, and `Document`
  L373: `PenType` and `Pen`
  table rows in '`Page`, `Layer`, and `TextBlock`': 13
  table rows in '`ScreenSpec` and `detect_screen`': 10
  table rows in '`RmspecSettings` and the `settings` singleton': 9
  table rows in '`parse_rm_file` and `parse_rm_bytes`': 11
  table rows in '`SyncDB`, the sync models, and the SQLite schema': 10
  table rows in '`export_svg` and `SVGRenderer.render_page`': 9
  table rows in '`DocumentType`, `DocumentMetadata`, `ContentInfo`, and `Document`': 9
  table rows in '`PenType` and `Pen`': 7

full citations: 229   shorthand citations: 37
distinct cited paths: 47
git check-ignore hits: 0

PASS: all mechanical checks green
```

```text
$ git check-ignore -v docs/insights/impact-analysis.md ; echo "exit=$?"
exit=1
```

```text
$ wc -l -w docs/insights/impact-analysis.md
     450    4479 docs/insights/impact-analysis.md
```

### Failures the script found, and the fixes

The first run reported 28 problems. Each was real:

1. **Two relative paths.** `formats/metadata.py:73` and `formats/content.py:77` were written without
   the `src/remarkable_spec/` prefix, so they resolved to nothing and were untracked. Expanded to full
   paths.
2. **A mis-resolving shorthand.** In the `export_svg` blast-radius notes, `` `:139-179` `` sat after a
   full citation to `src/remarkable_spec/models/stroke.py`, so it read as a stroke-model range when it
   meant `src/remarkable_spec/render/engine.py:139-179`. Written out in full.
3. **Three shorthands hidden behind a wrapped code span.** `` `rmspec render --thickness` `` was split
   across a line break, which left an unbalanced backtick on both lines and hid the
   `src/remarkable_spec/cli/render_cmd.py:82-85` antecedent from the parser — so `` `:359-367` ``,
   `` `:374-382` ``, and `` `:395-401` `` resolved to `export/svg.py`, a 68-line file, and the range
   check fired. Reflowed the sentence so no code span crosses a newline, and wrote the first of the
   three in full. Added a standing guard: zero lines in the output may have an odd backtick count.
4. **Two off-by-a-few `__all__` ranges.** The `Page`/`Layer`/`TextBlock` entries in
   `src/remarkable_spec/models/__init__.py` are at `:61-63`, not `:60-63`; in
   `src/remarkable_spec/__init__.py` they are at `:47-49`, not `:49-50`. Both corrected against the
   file.
5. **A wrong `CLAUDE.md` line.** The document-resolution tie-break is `CLAUDE.md:41`; `:42` is the
   DYLD auto-config bullet. Corrected.
6. **A malformed citation.** The `DeviceConnection` overflow entry cited an import line and then
   `src/remarkable_spec/device/connection.py:1` as "class at". The class is at `:38`. Replaced.

### Judgment spot-checks

Ranges the script can only bound, verified by reading the exact lines:
`src/remarkable_spec/export/pdf.py:166-168` (the "simplified merge" comment immediately above
`output.write_bytes(page_pdfs[0])`), `src/remarkable_spec/render/engine.py:95-100` (`thickness: float
= 1.5` is at `:98`), `src/remarkable_spec/cli/ocr_cmd.py:199-203` (`screen.page_width_pt` /
`page_height_pt` at `:201-202`), `src/remarkable_spec/export/svg.py:36-53` (the `Args` block ends at
`:51`, with no entry for `background_page_size`), `src/remarkable_spec/render/__init__.py:40-45`
(`SCREEN_WIDTH` through `SCALE` in `__all__` at `:41-44`),
`src/remarkable_spec/models/pen.py:40-75` (`is_highlighter` `:49`, `is_eraser` `:54`, `canonical`
`:59`, alias dict `:65-73`), `src/remarkable_spec/render/pens.py:437-480` (canonicalise at `:456`,
eraser case `:476-477`, fineliner fallthrough `:478-480`).

Counted claims re-derived rather than carried: the root `__all__` holds **26** elements by AST read of
`src/remarkable_spec/__init__.py:33-67`. Note for the orchestrator: `docs/.packets/_environment.md`
was revised while this packet was in flight — the copy read at Step 0 said "24 exported names" in two
places, and the copy on disk now says **26** at `:150` and `:166` and explicitly warns not to trust a
"24" (`:151-152`). The output file says 26, which matches both source and the current brief. The
brief's CLI registration citation also moved from `:52-62` to `:48-58`; no citation in the output
depends on it.

### Checks that were not available

- **Test impact.** Structurally absent. pytest is configured over `tests/` (`pyproject.toml:74-76`)
  and that directory holds one 0-byte `__init__.py`, so no `Type: test` row exists in the output and
  none could. Recorded here per orchestrator directive #1 rather than reported as a coverage number.
- **Prior-artifact diff.** No prior version existed (see Work log Step 1), so there was nothing to
  diff, re-verify, or partition by `git log` date.

## Summary

Shipped `docs/insights/impact-analysis.md` — 450 lines, 229 full citations and 37 shorthands across 47
distinct tracked files, all validated by script. The surface-selection criterion is **distinct inbound
import statements across `src/`**, measured by an AST walk over all 56 tracked `.py` files rather than
grep (multiline import blocks and function-local lazy imports both count), with two documented cohort
merges and a stated tie-break at equal count; the full ranking table is in the intro so a reader can
re-derive the set. The eight sections are `Page`/`Layer`/`TextBlock` (16), `ScreenSpec`/`detect_screen`
(14), `RmspecSettings`/`settings` (14), `parse_rm_file` (10), `SyncDB` plus the sync models plus the
SQL schema (10, cohort), `export_svg` plus `SVGRenderer.render_page` (8, cohort), the
`models/document.py` contract (7), and `PenType`/`Pen` (7). Nine surfaces went to
`## Other notable surfaces`, including `cli/_resolve.py` — which tied for 7th on the primary count and
was demoted by the stated tie-break because all four of its consumers are CLI commands. The
highest-value content is not the row counts but the implicit contracts: `--thickness` reaches SVG
output and nothing else because neither raster exporter accepts the parameter; multi-page PDF export
writes page 1 and drops the rest; both caches are keyed on `rm_hash` alone, so a render or prompt
change leaves cached rows valid-looking and wrong; the `ocr_cache` table is dead code while
`rmspec search` serves `.ocr.txt` sidecars with no hash check at all; and a schema change never
reaches an existing `sync.db` because every DDL is `CREATE TABLE IF NOT EXISTS` and the version
constant is never compared. No test-impact column appears anywhere, because no tests exist.
