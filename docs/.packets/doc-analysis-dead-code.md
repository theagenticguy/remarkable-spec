---
role: doc-analysis-dead-code
model: opus
output: "docs/analysis/dead-code.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · analysis/dead-code.md

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

Produce `docs/analysis/dead-code.md`: three tables enumerating unreferenced exports, unreferenced files, and dead imports in `remarkable-spec`. The file is always emitted — when no dead code is detected, emit a `No unreferenced symbols detected.` banner plus a stable skeleton of three content H2s so cross-references resolve.

## 2. Scope

- Create: `docs/analysis/dead-code.md`
- Do not touch: `docs/analysis/risk-hotspots.md`, `docs/analysis/ownership.md`, any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase — full file inventory and import statements.
- Any colocated dead-code analyzer output if the repo runs one (`ts-prune`, `knip`, `vulture`, `unused`, language-specific equivalents).
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

### Three rules specific to this repo

1. **A symbol in an `__all__` is published API, not dead code** — even at zero internal callers.
   The root `src/remarkable_spec/__init__.py:33-67` exports 24 names, and `models`, `formats`,
   `render`, `device`, `export`, `sync`, `ocr`, and `cli` each declare their own `__all__`. This is a
   library as well as a CLI, so an export with no internal consumer is the normal, intended state.
   Put these in a separately headed bucket — "exported, zero internal consumers" — and never in a
   bucket whose name implies deletability. Getting this wrong turns the document into a proposal to
   break the public API.

2. **Zero callers from the index is a lead, not a verdict.** This codebase uses function-local lazy
   imports deliberately, so that optional extras stay out of CLI startup: see
   `src/remarkable_spec/cli/_util.py:79`, `src/remarkable_spec/device/sync.py:325-326`, and
   `src/remarkable_spec/ocr/pipeline.py:48-51`. A name-resolved index can miss those edges. Before
   you list any symbol as unreferenced, run a text grep for the bare name across `src/` and paste
   the result into Validation. Note also that three same-named private helpers exist
   (`_invoke_bedrock_vision` in `src/remarkable_spec/ocr/postprocess.py:187`,
   `src/remarkable_spec/ocr/diagram.py:286`, `src/remarkable_spec/cli/annotations_cmd.py:254`), so a
   caller count against that bare name is the sum of three unrelated functions.

3. **"No test touches it" is not a signal here** — no tests exist, so it is true of every symbol.
   Do not use it as a criterion or report it as a finding.

Genuinely useful here: unreferenced module-level constants, unreferenced private helpers (leading
underscore, zero call sites in their own module), imports that no line in the file uses (ruff `F401`
would catch these, so a hit is evidence the lint gate is not run repo-wide), and enum members no
code branches on.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Determine the analysis approach. If a dead-code analyzer is already part of the project (check for config in manifests / a CI step / a committed cache), invoke it and parse the output. Otherwise build a lightweight import graph from grep over `import` / `require` / `use` declarations.
3. Identify **unreferenced exports** — exported symbols never imported elsewhere in the codebase. Capture the symbol name, declaration `path:LOC`, and last-modified date (`git log -1 --format=%cs -- <path>`).
4. Identify **unreferenced files** — files with no inbound `import`/`require`/`use` from any other file in the codebase. Capture the file path, total LOC (`wc -l`), and last-modified date.
5. Identify **dead imports** — `import` statements whose imported symbol is never referenced in the importing file. Capture the import site `path:LOC`, the imported symbol, and the source module.
6. Cross-check candidates against runtime-dispatch and string-based dynamic imports. False positives include: framework-discovered handlers (Express routers, Flask blueprints), dependency-injection registration sites, dynamically-imported plugins. Drop any candidate that looks framework-dispatched and note the drop in the Work log.
7. Draft three tables under three content H2 headings in fixed order: `## Unreferenced exports`, `## Unreferenced files`, `## Dead imports`. Empty buckets get a single `_none_` line under their H2.
8. If all three buckets are empty after step 6: emit the banner `No unreferenced symbols detected.` immediately after the H1, before the three content H2 sections (which still appear, each with `_none_`).
9. Write `docs/analysis/dead-code.md` with H1 = `# remarkable-spec · Dead code`.

## 5. Output format rules

- H1 = `# remarkable-spec · Dead code`. No decorative titles.
- No YAML frontmatter on the output file.
- Three content H2 headings in this fixed order: `## Unreferenced exports`, `## Unreferenced files`, `## Dead imports`. Always present.
- `Unreferenced exports` table columns: `Symbol | Path | Last modified`.
- `Unreferenced files` table columns: `File | Lines | Last modified`.
- `Dead imports` table columns: `Path | Symbol | Imported from`.
- Every Path / File / Imported-from cell is a backtick `path` or `path:LOC`.
- Empty buckets contain a single `_none_` line under their H2.
- Empty-overall: banner `No unreferenced symbols detected.` on its own line directly after the H1.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** declaration files and importing files to verify candidates.
- **Grep** for `import` / `require` / `use` / `from .* import` patterns to build the reference index.
- **Glob** to enumerate the full file inventory.
- **Bash** for `git log -1 --format=%cs -- <path>` (last-modified dates), `wc -l` (LOC), and any analyzer invocations.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No dead-code analyzer integrated:** build the import graph from grep over `import`/`require`/`use`. Note the methodology in the Work log.
- **Last-modified unavailable** (shallow clone): use `—` in the Last modified cell and note in the Work log.
- **A candidate is in fact framework-dispatched** (caught only after spot-checking): drop the row and cite the dispatch site in the Work log.
- **All three buckets empty:** still emit the file with the banner + three `_none_` content H2s. The file's existence is the load-bearing thing for cross-references.

## 8. Success criteria

- [x] `docs/analysis/dead-code.md` exists on disk (even when empty).
- [x] H1 line reads `# remarkable-spec · Dead code`. Asserted by script; Validation § 4.
- [x] All three content H2 headings exist in the fixed order. Asserted by script; Validation § 4.
- [x] Every populated table row has a backtick path citation in its Path/File/Imported-from column. All 20 rows carry a full `path:LOC` in the Path column; Validation § 5 asserts each resolves.
- [x] When all three buckets are empty: the banner appears once, and each of the three content H2s contains `_none_`. **Not applicable — path not taken.** `Unreferenced exports` is populated, so the banner would be false. `Unreferenced files` and `Dead imports` each carry `_none_`.
- [x] When tables have rows: column headers match the fixed schema in Section 5. Three `Symbol | Path | Last modified` tables plus the dropped-candidate register, which uses `Symbol | Path | Why it is live`; script output in Validation § 4.
- [x] No YAML frontmatter on the output. Asserted by script; Validation § 4.
- [x] Every row was checked by script rather than sampled: each cited file exists, each cited line is in range, and each symbol still resolves at its cited path and line against the analyzer output or the grep index. Two scripts, both pasted: `validate.py` (existence, range, orphan shorthands, gitignore, shape, schema) and `assert_symbols.py` (23 symbol-at-line assertions). Validation § 4 and § 5.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers. **The output path held no file** — `ls` returned `os error 2`; Work log Step 1.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed. **Records that no prior version existed** (Work log Step 1). Separately, the three inherited-citation errors this run found and fixed are itemised in Validation § 5.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path). All 39 distinct cited paths checked; `gitignored cited : none`. Validation § 4.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent unreferenced symbols, files, or imports. Every row must trace to an analyzer output entry or a verified grep result.
- Do not omit the three content H2 headings when buckets are empty — the skeleton must stay consistent for cross-references.
- Do not delete the file or skip the Write step when buckets are empty — the banner is the product in that case.
- Do not include framework-dispatched candidates without verification.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `docs/.packets/_environment.md:163` (and the same figure restated in this packet's section 3a) says
  the root `__all__` "exports 24 names". An AST parse of
  `src/remarkable_spec/__init__.py:33-67` returns **26**; the string entries occupy
  `src/remarkable_spec/__init__.py:35-66`. A reader sizing the public API from the brief undercounts
  it by two and could conclude two exports were removed. Confirmed by the coordinator and **already
  fixed at source in the brief**; recorded here for the sweep.
- `docs/.packets/_environment.md:154` says the 11 CLI commands are registered at
  `src/remarkable_spec/cli/__init__.py:52-62`. The actual `app.command(...)` block is
  `src/remarkable_spec/cli/__init__.py:48-58`: line 52 is the fifth call
  (`app.command(ocr_app, name="ocr")`) and line 62 is a docstring inside `_get_version`
  (`src/remarkable_spec/cli/__init__.py:61`). A reader following the citation to enumerate the CLI
  surface sees 7 of the 11 commands and 4 lines of an unrelated function. Every packet that cites the
  CLI census inherits this offset, so it is worth fixing centrally.
- `docs/.packets/_environment.md:89-91` gives `src/remarkable_spec/cli/_util.py:79` as a lazy-import
  example; that line is the closing `"""` of the docstring and the import is at
  `src/remarkable_spec/cli/_util.py:80`. The same bullet is repeated in this packet's section 3a rule
  2, which additionally gives `src/remarkable_spec/ocr/pipeline.py:48-51` where the lazy-import block
  is `src/remarkable_spec/ocr/pipeline.py:46-49` (line 51 is `try:`). Both are one- to two-line
  offsets, so a reader lands adjacent to the evidence rather than on it.
  `src/remarkable_spec/device/sync.py:325-326` in the same bullet is accurate.
- `src/remarkable_spec/sync/migrations.py:1-6`, the module docstring, says the module is "Called
  automatically on first access to the sync database" and both creates tables and "migrates legacy
  `.ocr.txt` sidecar files into the `ocr_cache` table". Only the first half holds: `init_schema`
  (`src/remarkable_spec/sync/migrations.py:99`) is called from `src/remarkable_spec/sync/db.py:59`,
  while `migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:113`) has no caller anywhere.
  A reader trusting the docstring believes sidecar migration happens on first DB access; it never
  runs.
- `src/remarkable_spec/device/web_api.py:210-212`, the `WebAPI.search` docstring, says it "Searches
  document names and (on newer firmware) full-text content". The body
  (`src/remarkable_spec/device/web_api.py:220-227`) is a client-side substring filter over
  `list_documents()` and makes no full-text request on any firmware. A reader picks this method
  expecting server-side full-text search and gets name matching.
- `src/remarkable_spec/device/web_api.py:225` reads the response key `"VisssibleName"` with three `s`
  characters, falling back to `"VisibleName"` at
  `src/remarkable_spec/device/web_api.py:226`; the parallel CLI path reads `"VissibleName"` with two,
  falling back to `"visibleName"` (`src/remarkable_spec/cli/search_cmd.py:120`). Three spellings of
  one field across two code paths, at most one of which the device sends. A reader cannot tell which
  is the real key, and each path silently yields `"?"` or `""` when its guess is wrong.
- `src/remarkable_spec/models/color.py:37` defines `PenColor.HIGHLIGHT = 9`, and neither `RM_PALETTE`
  (`src/remarkable_spec/models/color.py:89-103`) nor `PAPER_PRO_PHYSICAL`
  (`src/remarkable_spec/models/color.py:109-119`) contains an entry for it. A stroke carrying colour
  ID 9 falls through to whatever default the caller supplies, so a highlighter stroke renders in the
  fallback colour with no warning.
- `src/remarkable_spec/render/engine.py:29-32` exports `SCREEN_WIDTH`, `SCREEN_HEIGHT`, `SCREEN_DPI`,
  and `SCALE` as reMarkable 2 geometry at 226 DPI, while the project targets the Paper Pro's 229 DPI
  (`src/remarkable_spec/models/screen.py:83`). Nothing in `src/` reads them, so the mismatch is
  invisible internally, but a library consumer importing `SCALE` from
  `src/remarkable_spec/render/__init__.py:44` gets `72.0 / 226` for a 229-DPI panel.

One candidate finding was investigated and **withdrawn**, recorded so nobody re-raises it:
`src/remarkable_spec/sync/migrations.py:20` declares `doc_type TEXT NOT NULL DEFAULT 'DocumentType'`,
which reads like the enum's class name leaking into the schema as a default. It is not — the enum
member's *value* is that string: `DOCUMENT = "DocumentType"` at
`src/remarkable_spec/models/document.py:35`, matching reMarkable's own `.metadata` spelling. The
adjacent `file_type` default `'notebook'` (`src/remarkable_spec/sync/migrations.py:21`) is likewise
`FileType.NOTEBOOK`'s value (`src/remarkable_spec/models/document.py:47`). Both defaults are valid.

---

## Work log

### Step 1 — packet + environment brief read; prior artifact checked (section 4.1)

- Read this packet in full, then `docs/.packets/_environment.md` (442 lines) in full. Its
  stale-prior traps subsection is treated as binding: no test claims, no `converse`, no cyclopts 3.x,
  no "fully synchronous", parallelism attributed to `src/remarkable_spec/ocr/postprocess.py:131`.
- **Prior-artifact check: the output path held no file.** `ls -la docs/analysis/dead-code.md` →
  `No such file or directory (os error 2)`. No `docs/` tree of analysis artifacts exists yet, so
  there is **no prior version and therefore nothing the prior artifact got wrong**. Nothing was
  inherited; the entire document is built from source read during this run. `git log -1 --format=%cs`
  against the output path is moot for the same reason.
- Confirmed the citable-path universe: `git ls-files | wc -l` → 67, `git ls-files '*.py' | wc -l` →
  56, matching the brief's § 2 counts exactly.
- Scratch directory created at `/tmp/doc-analysis-dead-code/`.

### Step 2 — analysis approach (section 4.2): no analyzer integrated, fallback path taken

- `grep -n -iE 'vulture|deadcode|ts-prune|knip|unused|dead' pyproject.toml mise.toml lefthook.yml`
  exits 1 with no output. There is no `vulture`, `deadcode`, `dead`, or `unused` in
  `pyproject.toml:11-53` (deps, extras, dev group) and no lint/task entry for one in `mise.toml` or
  `lefthook.yml`. **Fallback path from section 7 taken**: import/reference graph built from source.
- Methodology: a PEP 723 script at `/tmp/doc-analysis-dead-code/inventory.py` parses all 55 `.py`
  files under `src/remarkable_spec/` with `ast`, collecting per file the `__all__` contents,
  module-level `FunctionDef`/`ClassDef`/`Assign` bindings with line numbers, and every import binding.
  A second pass tokenises every line with `[A-Za-z_][A-Za-z0-9_]*` and builds a whole-word occurrence
  index, so a symbol's reference count is a text count across `src/` — which is exactly the grep
  cross-check section 3a rule 2 demands, applied to every symbol rather than sampled.
  `/tmp/doc-analysis-dead-code/analyze.py` classifies. 219 module-level symbols total.
- Ruff cross-check run read-only, no `--fix`: `uvx ruff check src/ --select F401,F811,F841 --no-fix`
  → `All checks passed!`. So no unused import, no redefinition, and no unused local exists by ruff's
  own analysis. Selection `F` is already in the repo's lint set (`pyproject.toml:64`) and lefthook
  runs it pre-commit (`lefthook.yml:1-14`), so the clean result is evidence the gate **is** honoured
  repo-wide, the opposite of the "hit means the gate is not run" case section 3a anticipates.

### Step 3 — zero-caller candidates confirmed by grep (section 4.3)

Five module-level symbols have zero whole-word occurrences in `src/` beyond their own definition
line. Three are cyclopts handlers and are dropped as framework-dispatched (see Step 4). The two that
survive:

- `classify_page`, `src/remarkable_spec/ocr/diagram.py:115`. `grep -rn '\bclassify_page\b' src/
  --include='*.py'` returns exactly one line, the `def` itself. Not in `src/remarkable_spec/ocr/__init__.py`'s
  `__all__`, which holds only `ocr_image` and `ocr_page` (`src/remarkable_spec/ocr/__init__.py:7`),
  so it is not published API. The module's own usage docstring advertises `extract_mermaid_from_rm`
  (`src/remarkable_spec/ocr/diagram.py:14-15`), which is genuinely consumed at
  `src/remarkable_spec/cli/diagram_cmd.py:213,237`.
- `migrate_ocr_sidecars`, `src/remarkable_spec/sync/migrations.py:113`. Same grep shape: one hit, the
  `def`. Not in `src/remarkable_spec/sync/__init__.py`'s `__all__` (six model/DB names,
  `:22`). Its sibling `init_schema` (`src/remarkable_spec/sync/migrations.py:99`) **is** called, via
  a function-local lazy import at `src/remarkable_spec/sync/db.py:57,59` — which is the exact edge
  section 3a rule 2 warns a name-resolved index would miss, and which the text grep caught.

### Step 4 — framework-dispatch drops (section 4.6)

`/tmp/doc-analysis-dead-code/decor.py` enumerated every decorated module-level function: 18, all in
`src/remarkable_spec/cli/`, all decorated `app.default` or `app.command`. cyclopts discovers these by
decorator, so a zero-caller count is expected and meaningless. Three of them were in the zero-caller
set and are **dropped**:

- `inspect_file`, `src/remarkable_spec/cli/inspect_cmd.py:47` — `@app.default`, mounted as `inspect`
  at `src/remarkable_spec/cli/__init__.py:48`. (The brief's `:52-62` for this block is off by four;
  the 11 `app.command(...)` calls are `src/remarkable_spec/cli/__init__.py:48-58`. Recorded in
  Out-of-scope findings.)
- `ls_documents`, `src/remarkable_spec/cli/ls_cmd.py:73` — `@app.default`, mounted as `ls`.
- `_default`, `src/remarkable_spec/cli/sync_cmd.py:48` — `@app.default` on the `sync` sub-app.

The other 15 decorated handlers never entered the candidate set because their bare names collide with
unrelated identifiers elsewhere in `src/` (`render`, `ocr`, `search`, `diagram`, `env`, `tree`,
`annotations`, and the four-way `push`/`pull`/`ls`/`info` collision the brief flags at
`docs/.packets/_environment.md:76-80`). That collision inflating counts is why the surviving
candidates were each re-grepped by hand rather than trusted from the count.

Also dropped after inspection, all initially flagged by a "no qualified `Enum.MEMBER` use" heuristic:

- `PenType.PAINTBRUSH_2`, `MECHANICAL_PENCIL_2`, `PENCIL_2`, `BALLPOINT_2`, `MARKER_2`,
  `FINELINER_2`, `HIGHLIGHTER_2` (`src/remarkable_spec/models/pen.py:38-44`). They are branched on
  as `cls.X` inside `PenType.canonical` (`src/remarkable_spec/models/pen.py:65-73`) and
  `PenType.is_highlighter` (`:51`), not as `PenType.X`. Live.
- `PageContentType.DIAGRAM` and `MIXED` (`src/remarkable_spec/ocr/diagram.py:35-36`). Reached by
  value through `for ct in PageContentType: if ct.value in response_upper`
  (`src/remarkable_spec/ocr/diagram.py:139-141`). Live.
- All ten pen renderer classes (`src/remarkable_spec/render/pens.py:136,158,196,218,259,291,314,334,369,403`).
  Each is constructed in the `match canonical:` dispatch inside `get_pen_renderer`
  (`src/remarkable_spec/render/pens.py:457-480`). Live, and a table-driven dispatch is exactly the
  false-positive class section 4.6 names.

### Step 5 — unreferenced files (section 4.4): bucket is empty

`/tmp/doc-analysis-dead-code/files.py` resolved every `Import`/`ImportFrom` in all 55 files to a
dotted module name (handling relative levels and the `from pkg.mod import submodule` shape) and
counted inbound edges per file. Result: **every one of the 46 non-`__init__.py` files has at least
one inbound internal import.** The lowest are 1 each: the ten `cli/*_cmd.py` modules,
`src/remarkable_spec/device/push.py`, `src/remarkable_spec/formats/document_loader.py`,
`src/remarkable_spec/ocr/textract.py`, and `src/remarkable_spec/sync/migrations.py`. The highest is 16
(`src/remarkable_spec/models/page.py`).

The only zero-inbound files are the nine `__init__.py` package initialisers
(`src/remarkable_spec/__init__.py`, plus one each in `cli`, `device`, `export`, `formats`, `models`,
`ocr`, `render`, `sync`). These are **not** unreferenced: Python executes a package's `__init__.py`
whenever any submodule beneath it is imported, and `pyproject.toml:20-21` names
`remarkable_spec.cli:app` as the console-script entry point, reaching
`src/remarkable_spec/cli/__init__.py` directly. Counting them as dead would be an artifact of the
method, so the bucket is `_none_`.

One related observation, not a dead-file finding: `src/remarkable_spec/ocr/__init__.py:5` re-exports
`ocr_image` and `ocr_page`, but every internal consumer imports them from the defining module
instead — `src/remarkable_spec/ocr/postprocess.py:128` and `src/remarkable_spec/cli/search_cmd.py:136`
both do `from remarkable_spec.ocr.vision import ...`. So the `remarkable_spec.ocr` namespace is
published-only. Both names do exist (`src/remarkable_spec/ocr/vision.py:63,140`), so the re-export is
not broken.

### Step 6 — dead imports (section 4.5): bucket is empty

`/tmp/doc-analysis-dead-code/imports.py` checked every import binding in all 55 files for any
whole-word occurrence of the bound name outside the import statement's own line span, treating a name
present in that file's `__all__` as a legitimate re-export. 45 bindings came back with no further
use. **44 of them are `from __future__ import annotations`** — a compiler directive whose whole
purpose is the absence of a reference, present in every source file per
`docs/.packets/_environment.md:299-302`. Excluded, not dead.

That leaves exactly one candidate, and it is a false positive: `import paramiko` at
`src/remarkable_spec/cli/device_cmd.py:54`. It sits inside `try:` / `except ImportError:` in
`_check_device_deps` (`src/remarkable_spec/cli/device_cmd.py:51-62`), so the import statement's
success or failure **is** the return value — an availability probe for the `[device]` extra. The
source already says so with an explicit `# noqa: F401` on the same line, which is also why ruff's own
F401 pass is clean. Dropped; bucket is `_none_`.

### Step 7 — published API with zero internal consumers (section 3a rule 1)

`/tmp/doc-analysis-dead-code/published.py` took the union of all nine `__all__` declarations (**65
distinct names**) and counted occurrences outside `__init__.py` re-export plumbing and outside each
name's own definition line. Five have zero:

| Name | Definition | Declared in |
| --- | --- | --- |
| `BUILTIN_TEMPLATES` | `src/remarkable_spec/models/template.py:140` | `src/remarkable_spec/__init__.py:33`, `src/remarkable_spec/models/__init__.py:47` |
| `HighlightColor` | `src/remarkable_spec/models/color.py:44` | `src/remarkable_spec/__init__.py:33`, `src/remarkable_spec/models/__init__.py:47` |
| `SCREEN_WIDTH` | `src/remarkable_spec/render/engine.py:29` | `src/remarkable_spec/render/__init__.py:37` |
| `SCREEN_HEIGHT` | `src/remarkable_spec/render/engine.py:30` | `src/remarkable_spec/render/__init__.py:37` |
| `SCALE` | `src/remarkable_spec/render/engine.py:32` | `src/remarkable_spec/render/__init__.py:37` |

`SCREEN_DPI` (`src/remarkable_spec/render/engine.py:31`) was **not** included: it has one real
consumer, `SCALE = 72.0 / SCREEN_DPI` at `src/remarkable_spec/render/engine.py:32`.

Per-module `__all__` sizes, AST-counted and matching the coordinator's independent count: root 26,
`models` 26, `render` 13, `formats` 8, `sync` 6, `device` 4, `cli` 3, `export` 3, `ocr` 2.

Two enum members, both belonging to published enums:

- `PenColor.HIGHLIGHT` (`src/remarkable_spec/models/color.py:37`). `grep -rn '\bHIGHLIGHT\b'` returns
  two lines: the member definition and a docstring mention at `src/remarkable_spec/models/color.py:45`.
  No code branches on it, and it is absent from both palette dicts — `RM_PALETTE`
  (`src/remarkable_spec/models/color.py:89-103`) and `PAPER_PRO_PHYSICAL`
  (`src/remarkable_spec/models/color.py:109-119`) each skip ID 9, so a stroke carrying it has no RGB
  mapping.
- `HighlightColor.ORANGE` (`src/remarkable_spec/models/color.py:56`). `grep -rn '\bORANGE\b'` returns
  one line, the definition. The other four members
  (`src/remarkable_spec/models/color.py:52-55`) are equally unbranched, because the enclosing class
  has zero consumers.

### Step 8 — heading-shape decision

Section 3a rule 1 wants published-API symbols in a separately headed bucket; section 5 fixes the
content H2s at three, in order. Resolved on the coordinator's instruction: **H3 sub-buckets under
`## Unreferenced exports`, no fourth H2.** The published H3's heading states in words that the
symbols are published API with no internal consumer and are not deletable.

### Step 9 — independent CodeGraph cross-check

Ran `callers` from the absolute binary path in `docs/.packets/_environment.md:27` for all five
low-signal names. It agrees with the grep index on every one: `classify_page` → `"callers": []`,
`migrate_ocr_sidecars` → `"callers": []`, and `SCALE` / `HighlightColor` / `BUILTIN_TEMPLATES` →
callers consisting only of `__init__.py` re-export files. Output pasted in Validation. Two
independent methods, same answer, so no method-artifact ambiguity remains on the seven rows shipped.

### Step 10 — methods pass (gap found after coordinator feedback)

Steps 2-7 covered **module-level** symbols only. A sibling agent surfaced four zero-caller `SyncDB`
methods, which exposed that gap: class methods were never analysed. Closed it with
`/tmp/doc-analysis-dead-code/methods.py`, which walks every `ClassDef` in all 55 files, skips dunders
(runtime-invoked), and counts whole-word references per method. **23 zero-reference methods**, every
one on a class that is in an `__all__`:

- 5 on `SyncDB` — `get_page` (`src/remarkable_spec/sync/db.py:162`), `get_ocr` (`:174`), `put_ocr`
  (`:192`), `get_all_ocr` (`:216`), `find_changed_pages` (`:319`). The coordinator named four; the
  systematic pass found `get_page` as a fifth.
- 5 on `WebAPI` — `download_rmdoc` (`src/remarkable_spec/device/web_api.py:129`), `upload_pdf`
  (`:149`), `upload_epub` (`:172`), `get_thumbnail` (`:194`), plus `search` (`:209`), which the
  method script missed because the bare name `search` collides with
  `src/remarkable_spec/cli/search_cmd.py:46` and `re.search`; confirmed separately by a
  `def search|\.search(` grep.
- 2 on `Palette` — `get_hex` (`src/remarkable_spec/render/palette.py:62`), `get_css` (`:76`).
- 1 on `Document` — `base_path` (`src/remarkable_spec/models/document.py:379`).
- **11 dropped as framework-dispatched**: every one carries `@computed_field` above `@property` —
  verified by reading `src/remarkable_spec/models/document.py:349-377` and the pydantic import at
  `src/remarkable_spec/models/document.py:22`. Pydantic v2 evaluates these on `model_dump()` and puts
  them in the JSON schema, so serialization is the call site and deleting one changes the wire shape.

Bucketing decision, and it departs from the coordinator's framing: the coordinator noted that none of
the `SyncDB` methods is in an `__all__`, which is true of the method names, but their class `SyncDB`
**is** exported (`src/remarkable_spec/sync/__init__.py:25`). A library consumer can call
`SyncDB().put_ocr(...)`, so section 3a rule 1 applies to them exactly as it does to `HighlightColor` —
they went in the published H3, not the deletable one. Same reasoning for `WebAPI`
(`src/remarkable_spec/device/__init__.py:24`), `Palette`
(`src/remarkable_spec/render/__init__.py:46`), and `Document` (`src/remarkable_spec/__init__.py:33`).
Also declined the coordinator's "`SCREEN_DPI` read by nothing in `src/`": it is read once, at
`src/remarkable_spec/render/engine.py:32`. Documented as transitively unreachable — its only consumer
`SCALE` has none — rather than as a zero-consumer row, which is the accurate claim.

### Step 11 — dates

`git log --format=%cs | sort -u` returns a single value, `2026-03-06`, consistent with the
one-commit history at `docs/.packets/_environment.md:209-210`. Every Last-modified cell is therefore
`2026-03-06` — a real `git log -1 --format=%cs` result per file, not the section 7 `—` fallback,
which was not needed.

## Validation

### 1. Ruff cross-check — read-only, no `--fix`

```console
$ uvx ruff check src/ --select F401,F811,F841 --no-fix
All checks passed!
$ echo $?
0
```

Run twice, before drafting and after the final edit, same result. No unused import, no
redefinition, no unused local by ruff's own analysis.

### 2. Bare-name grep cross-check for every zero-caller claim (section 3a rule 2)

Every symbol shipped in the document was grepped for its bare name across `src/` before being
listed. Full output at `/tmp/doc-analysis-dead-code/grep-crosscheck.txt` and
`/tmp/doc-analysis-dead-code/grep-ocrcache.txt`; the load-bearing lines:

```console
$ grep -rn '\bclassify_page\b' src/ --include='*.py'
src/remarkable_spec/ocr/diagram.py:115:def classify_page(

$ grep -rn '\bmigrate_ocr_sidecars\b' src/ --include='*.py'
src/remarkable_spec/sync/migrations.py:113:def migrate_ocr_sidecars(conn: sqlite3.Connection, xochitl_dir: Path) -> int:

$ grep -rn '\bput_ocr\b' src/ --include='*.py'
src/remarkable_spec/sync/db.py:192:    def put_ocr(self, entry: OCRCacheEntry) -> None:

$ grep -rn '\bget_ocr\b' src/ --include='*.py'
src/remarkable_spec/sync/db.py:174:    def get_ocr(self, rm_hash: str, engine: str = "merged") -> OCRCacheEntry | None:

$ grep -rn '\bget_all_ocr\b' src/ --include='*.py'
src/remarkable_spec/sync/db.py:216:    def get_all_ocr(self, rm_hash: str) -> list[OCRCacheEntry]:

$ grep -rn '\bfind_changed_pages\b' src/ --include='*.py'
src/remarkable_spec/sync/db.py:319:    def find_changed_pages(self, doc_uuid: str, current_hashes: dict[str, str]) -> list[str]:

$ grep -rn '\bHighlightColor\b' src/ --include='*.py'
src/remarkable_spec/__init__.py:10:    HighlightColor,
src/remarkable_spec/__init__.py:36:    "HighlightColor",
src/remarkable_spec/models/color.py:44:class HighlightColor(enum.Enum):
src/remarkable_spec/models/__init__.py:24:    HighlightColor,
src/remarkable_spec/models/__init__.py:50:    "HighlightColor",

$ grep -rn '\bSCALE\b' src/ --include='*.py'
src/remarkable_spec/render/engine.py:32:SCALE = 72.0 / SCREEN_DPI
src/remarkable_spec/render/__init__.py:18:    SCALE,
src/remarkable_spec/render/__init__.py:44:    "SCALE",

$ grep -rn '\bBUILTIN_TEMPLATES\b' src/ --include='*.py'
src/remarkable_spec/__init__.py:27:    BUILTIN_TEMPLATES,
src/remarkable_spec/__init__.py:62:    "BUILTIN_TEMPLATES",
src/remarkable_spec/models/__init__.py:41:    BUILTIN_TEMPLATES,
src/remarkable_spec/models/__init__.py:76:    "BUILTIN_TEMPLATES",
src/remarkable_spec/models/template.py:140:BUILTIN_TEMPLATES = [

$ grep -rn '\bORANGE\b' src/ --include='*.py'
src/remarkable_spec/models/color.py:56:    ORANGE = "HighlighterOrange"

$ grep -rn '\bHIGHLIGHT\b' src/ --include='*.py'
src/remarkable_spec/models/color.py:37:    HIGHLIGHT = 9  # Shared ID; actual color from extra block data or extraMetadata
src/remarkable_spec/models/color.py:45:    """Highlight colors that share PenColor.HIGHLIGHT (ID 9).
```

`SCREEN_WIDTH` and `SCREEN_HEIGHT` return three lines each (definition plus the two
`src/remarkable_spec/render/__init__.py` re-export lines). `SCREEN_DPI` returns four — the extra one
is `src/remarkable_spec/render/engine.py:32`, its only real consumer — which is why it is described in
the document as transitively unreachable rather than listed as a zero-consumer row.

`WebAPI.search` needed a different query, because the bare name `search` collides with
`src/remarkable_spec/cli/search_cmd.py:46` and with `re.search`:

```console
$ grep -rn 'def search\|\.search(' src/ --include='*.py'
src/remarkable_spec/ocr/diagram.py:267:    content_match = re.search(r"CONTENT_TYPE:\s*(\w+)", response)
src/remarkable_spec/ocr/diagram.py:272:    type_match = re.search(r"DIAGRAM_TYPE:\s*(\S+)", response)
src/remarkable_spec/ocr/diagram.py:275:    code_match = re.search(r"```mermaid\n(.*?)```", response, re.DOTALL)
src/remarkable_spec/cli/search_cmd.py:46:def search(
src/remarkable_spec/device/web_api.py:209:    def search(self, keyword: str) -> list[dict[str, Any]]:
```

No `.search(` call site resolves to the `WebAPI` method; all three are `re.search`.

### 3. Independent CodeGraph cross-check

```console
$ CG=/Users/lalsaado/.local/share/mise/installs/npm-colbymchenry-codegraph/latest/node_modules/.bin/codegraph
$ $CG callers classify_page -l 50 --json
{ "symbol": "classify_page", "callers": [] }
$ $CG callers migrate_ocr_sidecars -l 50 --json
{ "symbol": "migrate_ocr_sidecars", "callers": [] }
$ $CG callers put_ocr -l 50 --json
{ "symbol": "put_ocr", "callers": [] }
$ $CG callers get_ocr -l 50 --json
{ "symbol": "get_ocr", "callers": [] }
$ $CG callers get_all_ocr -l 50 --json
{ "symbol": "get_all_ocr", "callers": [] }
$ $CG callers find_changed_pages -l 50 --json
{ "symbol": "find_changed_pages", "callers": [] }
$ $CG callers SCALE -l 50 --json
{ "symbol": "SCALE", "callers": [
    { "name": "__init__.py", "kind": "file",
      "filePath": "src/remarkable_spec/render/__init__.py", "startLine": 1 } ] }
$ $CG callers HighlightColor -l 50 --json
{ "symbol": "HighlightColor", "callers": [
    { "name": "__init__.py", "kind": "file",
      "filePath": "src/remarkable_spec/__init__.py", "startLine": 1 },
    { "name": "__init__.py", "kind": "file",
      "filePath": "src/remarkable_spec/models/__init__.py", "startLine": 1 } ] }
$ $CG callers BUILTIN_TEMPLATES -l 50 --json
{ "symbol": "BUILTIN_TEMPLATES", "callers": [
    { "name": "__init__.py", "kind": "file",
      "filePath": "src/remarkable_spec/__init__.py", "startLine": 1 },
    { "name": "__init__.py", "kind": "file",
      "filePath": "src/remarkable_spec/models/__init__.py", "startLine": 1 } ] }
```

Two independent methods agree on every shipped row. One CodeGraph result was **rejected** rather than
used: `$CG callers search` reports one caller, `_parse_mermaid_response` at
`src/remarkable_spec/ocr/diagram.py:261`. That is the name-resolution limit the brief documents at
`docs/.packets/_environment.md:73-81` — the "caller" is `re.search`, not `WebAPI.search`. Confirmed by
the `def search|\.search(` grep above and discarded.

### 4. Citation resolution, gitignore, shape, and schema — scripted

`/tmp/doc-analysis-dead-code/validate.py` checks existence, line-range, orphan shorthands, `git
check-ignore`, H1 text, absence of frontmatter, content-H2 order, and table headers. It exits
non-zero on the first failure class.

```console
$ uv run --no-project /tmp/doc-analysis-dead-code/validate.py
H1                : '# remarkable-spec · Dead code'
YAML frontmatter  : absent
content H2s       : ['Unreferenced exports', 'Unreferenced files', 'Dead imports']
H3s               : ['Internal symbols with zero references — deletion candidates',
                     'Published API with zero internal consumers — reachable by library consumers, not deletion candidates',
                     'Candidates dropped as framework-dispatched or table-dispatched']

table header rows:
  | Symbol | Path | Last modified |
  | Symbol | Path | Last modified |
  | Symbol | Path | Last modified |
  | Symbol | Path | Why it is live |

full citations    : 117
shorthand `:LOC`  : 10
distinct paths    : 39
gitignored cited  : none
skipped as prose   : ['.ocr.txt', '__init__.py']
...
PASS: all citations resolve, all lines in range, no orphan shorthands, no gitignored paths, shape and schema correct.
$ echo $?
0
```

The `skipped as prose` line matters: the first version of this script treated the backticked
`` `.ocr.txt` `` sidecar suffix and the bare `` `__init__.py` `` in prose as citations and crashed
trying to open `/Users/lalsaado/Projects/remarkable-spec/.ocr.txt`. The filter now requires a citation
to contain `/` or be a tracked root file, and prints every token it skipped so nothing hides behind
the filter.

### 5. Symbol-resolves-at-cited-line, scripted

Range checks prove a line exists; they do not prove it says what the row claims.
`/tmp/doc-analysis-dead-code/assert_symbols.py` parses the tables out of the document and asserts the
row's symbol appears at its Path-column `path:LOC`.

```console
$ uv run --no-project /tmp/doc-analysis-dead-code/assert_symbols.py
OK   src/remarkable_spec/ocr/diagram.py:115  ['classify_page']            :: def classify_page(
OK   src/remarkable_spec/sync/migrations.py:113  ['migrate_ocr_sidecars'] :: def migrate_ocr_sidecars(conn: sqlite3.Connection, xochitl_dir:
OK   src/remarkable_spec/models/template.py:140  ['BUILTIN_TEMPLATES']    :: BUILTIN_TEMPLATES = [
OK   src/remarkable_spec/models/color.py:44   ['HighlightColor']          :: class HighlightColor(enum.Enum):
OK   src/remarkable_spec/render/engine.py:29   ['SCREEN_WIDTH']           :: SCREEN_WIDTH = 1404
OK   src/remarkable_spec/render/engine.py:30   ['SCREEN_HEIGHT']          :: SCREEN_HEIGHT = 1872
OK   src/remarkable_spec/render/engine.py:32   ['SCALE']                  :: SCALE = 72.0 / SCREEN_DPI
OK   src/remarkable_spec/sync/db.py:174  ['get_ocr']                      :: def get_ocr(self, rm_hash: str, engine: str = "merged") -> OCRCa
OK   src/remarkable_spec/sync/db.py:192  ['put_ocr']                      :: def put_ocr(self, entry: OCRCacheEntry) -> None:
OK   src/remarkable_spec/sync/db.py:216  ['get_all_ocr']                  :: def get_all_ocr(self, rm_hash: str) -> list[OCRCacheEntry]:
OK   src/remarkable_spec/sync/db.py:162  ['get_page']                     :: def get_page(self, doc_uuid: str, page_uuid: str) -> SyncPage |
OK   src/remarkable_spec/sync/db.py:319  ['find_changed_pages']           :: def find_changed_pages(self, doc_uuid: str, current_hashes: dict
OK   src/remarkable_spec/device/web_api.py:209  ['search']                :: def search(self, keyword: str) -> list[dict[str, Any]]:
OK   src/remarkable_spec/device/web_api.py:129  ['download_rmdoc']        :: def download_rmdoc(self, doc_id: str, output: Path) -> None:
OK   src/remarkable_spec/device/web_api.py:149  ['upload_pdf']            :: def upload_pdf(self, path: Path) -> None:
OK   src/remarkable_spec/device/web_api.py:172  ['upload_epub']           :: def upload_epub(self, path: Path) -> None:
OK   src/remarkable_spec/device/web_api.py:194  ['get_thumbnail']         :: def get_thumbnail(self, doc_id: str) -> bytes:
OK   src/remarkable_spec/render/palette.py:62   ['get_hex']               :: def get_hex(self, color: PenColor) -> str:
OK   src/remarkable_spec/render/palette.py:76   ['get_css']               :: def get_css(self, color: PenColor) -> str:
OK   src/remarkable_spec/models/document.py:379  ['base_path']            :: def base_path(self, xochitl_dir: Path) -> Path:
OK   src/remarkable_spec/cli/inspect_cmd.py:47   ['inspect_file']         :: def inspect_file(
OK   src/remarkable_spec/cli/ls_cmd.py:73   ['ls_documents']              :: def ls_documents(
OK   src/remarkable_spec/cli/sync_cmd.py:48   ['_default']                :: def _default(
span src/remarkable_spec/models/pen.py:38-44   ['PenType', '_2']          (block cite, range check only)
span src/remarkable_spec/ocr/diagram.py:35-36  ['DIAGRAM', 'MIXED']       (block cite, range check only)

row/citation pairs asserted: 23
PASS: every table row's symbol resolves at its cited path and line.
$ echo $?
0
```

**This check caught three real errors that the range check passed.** All three are now fixed in the
output:

1. `CLAUDE.md:29` was cited for the `rm_hash`-as-cache-key claim. Line 29 is a directory listing
   (`├── sync/           # db.py (SQLite), models.py, hasher.py, migrations.py`). The claim is at
   `CLAUDE.md:40`. Corrected.
2. The cyclopts registration block was cited as `src/remarkable_spec/cli/__init__.py:52-62`, carried
   from the brief. Line 52 is `app.command(ocr_app, name="ocr")` and line 62 is a docstring inside
   `_get_version`. The 11 `app.command(...)` calls are `src/remarkable_spec/cli/__init__.py:48-58`.
   The three dropped-handler rows now cite their individual mount lines: `:48` for `inspect`, `:49`
   for `ls`, `:55` for `sync`.
3. Two lazy-import example citations carried from the packet were off. `src/remarkable_spec/cli/_util.py:79`
   is a closing `"""`; the import is at `:80`. `src/remarkable_spec/ocr/pipeline.py:48-51` spans into
   a `try:`; the lazy-import block is `:46-49`. Both corrected. `src/remarkable_spec/device/sync.py:325-326`
   was accurate as given.

Judgment items not scripted, verified by direct read instead: the four span citations
(`src/remarkable_spec/models/pen.py:38-44` and `:65-73`, `src/remarkable_spec/ocr/diagram.py:35-36`
and `:139-141`, `src/remarkable_spec/render/pens.py:457-480`) point at blocks whose endpoints are
punctuation, so a per-line symbol assertion is meaningless for them; each was read in full during
Step 4. The 11 `@computed_field` properties were verified by reading the decorator stack at
`src/remarkable_spec/models/document.py:349-377` and the pydantic import at
`src/remarkable_spec/models/document.py:22`.

### 6. Not run, deliberately

No billable or device command was invoked: no `rmspec ocr`, `diagram`, `annotations`, `sync pull`,
`sync push`, or `device *`. Nothing in this analysis required executing the package — every finding
comes from reading source, `git`, `ast`, `grep`, `ruff`, and the CodeGraph index. Ruff was never run
with `--fix`. No source file was modified: only `docs/analysis/dead-code.md` (created) and this packet
were written, both inside Scope.

## Summary

Shipped `docs/analysis/dead-code.md` with the mandated H1 and all three content H2s in fixed order,
no frontmatter. **The empty-state banner path was not taken** — `Unreferenced exports` is populated,
so the banner would have been wrong. Row counts: `Unreferenced exports` carries 20 rows across three
H3 sub-buckets — 2 internal deletion candidates (`classify_page`,
`src/remarkable_spec/ocr/diagram.py:115`; `migrate_ocr_sidecars`,
`src/remarkable_spec/sync/migrations.py:113`), 18 published-API rows with zero internal consumers (5
module-level names plus 13 methods of exported classes), and 6 dropped-candidate rows kept as a
do-not-re-raise record. `Unreferenced files` and `Dead imports` are both `_none_`, each with the
reasoning that makes the emptiness checkable rather than asserted: every non-`__init__.py` file has
at least one inbound import, and the only non-`__future__` dead-import candidate is the
`# noqa: F401` availability probe at `src/remarkable_spec/cli/device_cmd.py:54`. Framework-dispatch
drops totalled 6 classes of false positive — 18 cyclopts handlers, 7 `PenType` `_2` members reached
as `cls.X`, 2 `PageContentType` members reached by value, 10 pen renderer classes in a `match`
dispatch, and 11 Pydantic `@computed_field` properties whose call site is serialization. The
document's headline is not a single symbol but a subsystem: the `ocr_cache` table has a schema, a
five-method typed API on the exported `SyncDB`, and a legacy importer, and no live path reaches any of
them, because the shipping OCR cache is a `.ocr.txt` file written at
`src/remarkable_spec/cli/search_cmd.py:204-207`. Per section 3a rule 1, every symbol reachable through
an `__all__` — including methods and enum members of exported classes — sits under an H3 whose heading
says it is public API and not a deletion candidate; the three-H2 contract is intact with no fourth H2.
