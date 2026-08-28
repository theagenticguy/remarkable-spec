---
role: doc-architecture-module-map
model: opus
output: "docs/architecture/module-map.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · architecture/module-map.md

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

Produce `docs/architecture/module-map.md`: one content H2 per top module in `remarkable-spec`, each with a one-paragraph description (≤ 4 sentences) and a bullet list of the module's top 8 files cited as `` `path` `` with a `(N LOC)` suffix.

Where `system-overview.md` paints the whole picture in 500 words, `module-map.md` is the index a reader uses to find which module owns which behavior.

## 2. Scope

- Create: `docs/architecture/module-map.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase's top-level directory structure (`packages/*`, `src/*`, `internal/*`, `apps/*`, or equivalent).
- Source files in each candidate module — enough to identify the top 8 by significance.
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` for fast `jq`-based file enumeration.

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

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Enumerate candidate modules — top-level directories that contain source code. Skip vendor, build, and tooling-only paths.
3. For each module, build a ranked file list. Significance score = inbound import count (preferred when a graph index is available) or, fallback, total LOC × file-name-as-anchor weight (`index.*`, `main.*`, `mod.rs`, `__init__.py`, `lib.rs` get a multiplier). Keep the top 8 files per module.
4. Compute LOC for each shortlisted file (`wc -l` or read + count). Record per file.
5. Draft the one-paragraph description per module. Anchor on the highest-weight file's name and what it exports. Every claim ends in a backtick `path:LOC` citation.
6. Order modules in the output by importance — same order as `system-overview.md`'s Mermaid diagram if possible (read it if it exists; otherwise rank by total inbound import count or total LOC, descending).
7. Collapse modules with < 3 shortlisted files into a `## Supporting code` H2 — the last content H2 — that lists those files as flat bullets without descriptions.
8. Write `docs/architecture/module-map.md` with H1 = `# remarkable-spec · Module map`.

## 5. Output format rules

- H1 = `# remarkable-spec · Module map`. No decorative titles.
- No YAML frontmatter on the output file.
- One content H2 per module. H2 text is the module's canonical name (directory name or manifest-declared package name).
- Each content H2 is followed by exactly one paragraph (≤ 4 sentences), then a bullet list of the top 8 files.
- Every file bullet has the form `` `path` (N LOC) `` — both the backtick path and the LOC suffix are mandatory.
- Every paragraph claim ends in a backtick `path:LOC` citation.
- A `## Supporting code` H2, last among the content H2s, collects modules with fewer than 3 shortlisted files as flat bullets without descriptions.
- No Mermaid in this file (the flowchart is in `system-overview.md`). No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** source files for paragraph drafting and LOC computation.
- **Grep** for `import`/`from`/`require`/`use` statements when ranking by inbound-reference count.
- **Glob** to enumerate files per module.
- **Bash** for `wc -l` and any ad-hoc scripting (e.g., `jq` over `docs/.repomix/codebase.json` to slice files by directory prefix).
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No multi-module structure.** If the codebase is flat (no `src/<name>/` subdivision), partition by file-name prefix or by major concern (one content H2 per partition). Note the partitioning rule in the Work log.
- **`Read` fails for a file** (missing or binary): drop it from the bullet list. Do not invent a LOC count. Log the skip in the Work log.
- **Module shortlist has more than 12 modules:** keep the top 8 as full content H2 sections and collapse the remainder under `## Supporting code` as one-liners.
- **A module has fewer than 3 files:** collapse it under `## Supporting code` rather than emit a content H2 with a single bullet.

## 8. Success criteria

- [x] `docs/architecture/module-map.md` exists on disk. — 187 lines.
- [x] H1 line reads `# remarkable-spec · Module map`. — exact-string checked, not regex.
- [x] At least 3 content H2 sections are present (excluding `## Supporting code`). — 8 present.
- [x] Every content H2 is followed by a paragraph with at least one `path:LOC` citation. — all 8; sentence counts 4,4,4,4,4,4,4,3, none over the 4-sentence cap.
- [x] Every file bullet has the form `` `path` (N LOC) `` — verify with a regex match for `\(\d+ LOC\)` on every bullet line. — 50 bullets, all matching `^- \`([^\`]+)\`\s*\((\d+) LOC\)$`, and each N re-checked against `wc -l` on the file.
- [x] The cross-link pass's validator ran against the output and exited 0: every file bullet's path exists on disk, every full and shorthand citation resolves to an in-range line, and no shorthand is orphaned. Paste the command and its output into Validation. — 51 full + 30 shorthand citations resolve; command and output in Validation, plus a 12-case negative test proving the validator is not vacuous.
- [x] No module content H2 lists more than 8 bulleted files (the `Supporting code` H2 is excluded). — `cli` is at exactly 8; the rest are at 4–8.
- [x] No YAML frontmatter on the output. — line 1 is the H1.
- [x] No Mermaid fences in the output. — zero fences of any language. The word "Mermaid" appears once in the `ocr` prose as a domain noun.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers. — the path held no file; there is no `docs/architecture/` directory in the repo before this run.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed. — Work log entry 2 records that no prior version existed and enumerates which failure modes therefore could not occur.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path). — `git check-ignore --stdin` over all 52 distinct cited paths returned nothing; separately, all 52 are present in `git ls-files`.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent module names, file paths, or LOC counts. Every identifier must trace back to a source file you read.
- Do not write YAML frontmatter on the output file.
- Do not emit Mermaid diagrams — the flowchart belongs to `architecture/system-overview.md`.
- Do not exceed 8 file bullets per module; overflow belongs in `## Supporting code`.
- Do not emit emojis. Do not use filler adverbs.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

Four findings, all in the shared environment brief. Three are wrong numbers that any packet copying
them would ship as invented citations, so they are worth fixing at the source before the rest of the
fan-out lands.

- `docs/.packets/_environment.md:149` and `:163` — both say the root `__all__` exports **24** names;
  an AST count of the list literal at `src/remarkable_spec/__init__.py:33` gives **26**, and the
  identical list at `src/remarkable_spec/models/__init__.py:47` also gives 26. Any packet that
  repeats the figure — `doc-reference-public-api` most of all, since the library surface is its whole
  subject — publishes a wrong count that no citation check catches, because 24 and 26 both "resolve"
  to a real line.

- `docs/.packets/_environment.md:154` — places the CLI command registration block at
  `src/remarkable_spec/cli/__init__.py:52-62`; the 11 `app.command(...)` calls are at `:48-58`, and
  `:52` is the fifth call (`app.command(ocr_app, name="ocr")`), not the first. A reader following the
  cited range sees 7 of the 11 registrations plus 4 lines of unrelated code, and would undercount the
  CLI surface by four commands if they trusted the range as the census.

- `docs/.packets/_environment.md:358` — places the SQLite pragmas at
  `src/remarkable_spec/sync/db.py:53-54`; `:53` is `self._conn.row_factory = sqlite3.Row`, WAL is at
  `:54`, and `foreign_keys=ON` is at `:55`. A reader checking the cited range finds only one of the
  two pragmas and could conclude foreign-key enforcement is not set, which would invert a real
  durability property of the sync database.

- `codegraph node -f src/remarkable_spec/sync/db.py --symbols-only` reports `db.py` as used by
  `src/remarkable_spec/device/connection.py` and `src/remarkable_spec/sync/migrations.py`; neither
  imports it (`connection.py` has no `remarkable_spec.sync` import at all, and `migrations.py:122`
  imports only `sync.hasher`). Not a defect in a tracked file — it is the name-resolution limit
  `docs/.packets/_environment.md:74` already warns about, recorded here because it manufactures two
  concrete false dependency edges that `doc-diagrams-dependency-graph` and `doc-insights-contract-map`
  would draw straight from the tool output if they rank on its `used by` line without confirming at
  an import site.

---

## Work log

### 1. Inputs read

- `docs/.packets/_environment.md` (442 lines) read in full before any other work. Its stale-prior
  traps subsection treated as binding; the four orchestrator directives in section 3a cross-checked
  against it and consistent.
- This packet read in full.

### 2. Prior-artifact check (Process step 1)

`ls -la docs/architecture/module-map.md` → `No such file or directory (os error 2)`. **No prior
artifact existed.** `find docs -type f` returns only `docs/.repomix/{token-tree.txt,codebase.json}`
and 17 files under `docs/.packets/` — there is no `docs/architecture/` directory at all, so this is a
first run over a repo with no `docs/` tree. Consequences:

- Nothing to re-verify, no stale citations to partition, no `git log -1 --format=%cs -- <output>`
  comparison to run (the file has no git history).
- **What the prior artifact got wrong: nothing, because there was no prior artifact.** The failure
  modes the packet anticipates — citations off by hundreds of lines, LOC figures matching neither
  `wc -l` nor the flattened pack, a forbidden diagram edge, an inverted rule, citations into
  gitignored build output — could not occur. Every claim in the output is drafted from source read
  in this session.
- `docs/architecture/system-overview.md` also does not exist, so Process step 6's preferred ordering
  key (match the system-overview Mermaid diagram order) is unavailable. Fallback ordering used — see
  entry 4.

### 3. Inventory

`git ls-files` → 67 tracked paths, 56 of them `.py`, matching `_environment.md:122-126`. That list is
the citable universe; no path outside it appears in the output.

`git ls-files '*.py' | xargs wc -l` → 10,321 total across 56 files, reproducing the total at
`_environment.md:118` exactly. Per-module sums recomputed from that output and matched against the
table at `_environment.md:100-113` row by row — all nine rows agree (`cli` 3,899/14, `models`
1,367/8, `device` 1,295/6, `render` 1,069/5, `ocr` 962/6, `sync` 728/5, `formats` 562/6, `export`
372/4, root 67/1). LOC figures in the output are taken from this single `wc -l` run, so no two
bullets can disagree.

Raw output kept at `/tmp/doc-architecture-module-map/loc.txt`.

`codegraph files --format grouped` (absolute binary path per `_environment.md:27`) returned 57 files
with per-file symbol counts, used as a second signal for intra-`cli` ranking. Its 56 Python files
match `git ls-files '*.py'` one-for-one.

### 4. Module enumeration and ordering

Candidate modules = the eight packages under `src/remarkable_spec/`. No vendor, build, or
tooling-only directory exists inside `src/`, so nothing was skipped there. The two root-package files
(`src/remarkable_spec/__init__.py`, `src/remarkable_spec/py.typed`) are not a package and number
fewer than three, so per Process step 7 / Fallback 4 they go under `## Supporting code` rather than
getting a content H2.

Eight modules is under the 12-module threshold in Fallback 3, so all eight get full content H2s.

**Two ordering keys computed, one chosen.** Process step 6 allows total inbound import count or total
LOC, descending.

- Inbound import counts, summed from the measured edge table at `_environment.md:131-141`:
  `models` 46, `formats` 15, `device` 11, `sync` 9, `render` 9, `ocr` 8, `export` 5, `cli` 0.
- Total LOC, from the `wc -l` run above: `cli` 3,899, `models` 1,367, `device` 1,295, `render` 1,069,
  `ocr` 962, `sync` 728, `formats` 562, `export` 372.

**Chose total LOC descending.** Reasons: (a) inbound-import ranking puts `cli` last, but `cli` holds
the only entry point (`pyproject.toml:20-21`) and is the module a reader of an index opens first;
(b) LOC descending reproduces the order of the size table at `_environment.md:100-113`, so this doc
and any sibling doc that leans on the brief present modules in the same sequence. Recorded here
because the alternative key produces a materially different order.

### 5. Per-module file shortlists

Seven of the eight modules hold 8 or fewer `.py` files, so their shortlist is the whole module and no
ranking was needed. Only `cli` (14 files) required a cut to 8. Ranked `cli` by inbound intra-package
reference count first, LOC as tiebreak:

`grep -rn "^from \.\|^from remarkable_spec.cli\|^    from \.\|^        from \." src/remarkable_spec/cli/*.py`
plus `grep -rn "_resolve" src/` gave the real edges:

- `_util.py` — 10 inbound importers (`__init__.py:25`, `annotations_cmd.py:42`, `device_cmd.py:45`,
  `diagram_cmd.py:43`, `env_cmd.py:20`, `ls_cmd.py:47`, `ocr_cmd.py:40`, `render_cmd.py:46`,
  `search_cmd.py:39`, `sync_cmd.py:39`, `tree_cmd.py:37`) — highest in the package despite 112 LOC.
- `_resolve.py` — 4 inbound importers (`ocr_cmd.py:43,112`, `diagram_cmd.py:46,129`,
  `render_cmd.py:50,203`, `annotations_cmd.py:118`), every one of them a **function-local lazy
  import**, not a module-level one. This is the pattern `_environment.md:88-91` warns about; the
  import sites are cited rather than inferred.
- `__init__.py` — 0 inbound but it is the entry point and mounts all 11 sub-apps, so it is kept as
  the anchor file (`__init__.py` anchor multiplier, Process step 3).
- Remaining 11 are leaf command modules with 0 inbound edges each, ranked by LOC:
  `device_cmd.py` 504, `sync_cmd.py` 425, `render_cmd.py` 408, `diagram_cmd.py` 376,
  `inspect_cmd.py` 317, `ls_cmd.py` 309, `annotations_cmd.py` 299, `search_cmd.py` 250,
  `ocr_cmd.py` 240, `tree_cmd.py` 234, `env_cmd.py` 63.

Shortlist kept for `cli`: `__init__.py`, `_util.py`, `_resolve.py`, `device_cmd.py`, `sync_cmd.py`,
`render_cmd.py`, `diagram_cmd.py`, `inspect_cmd.py`. Cut: `ls_cmd.py`, `annotations_cmd.py`,
`search_cmd.py`, `ocr_cmd.py`, `tree_cmd.py`, `env_cmd.py` — named in the `cli` paragraph as
overflow so a reader is not misled into thinking the module has only 8 files.

### 6. Source verification for the paragraph claims

Every claim in the draft was pinned to a line read in this session. Method: `codegraph node -f <path>
--symbols-only` for a structural map with line numbers (all 56 files), then `Read` or a line-numbered
`awk` window on any region whose exact text a claim depends on.

Files read in full: `src/remarkable_spec/__init__.py`, `src/remarkable_spec/cli/__init__.py`,
`src/remarkable_spec/cli/_util.py`, `src/remarkable_spec/device/paths.py`, and the seven other
package `__init__.py` files (`models`, `formats`, `render`, `ocr`, `device`, `sync`, `export`).

Line-numbered windows read for exact text: `src/remarkable_spec/sync/db.py:45-60`,
`src/remarkable_spec/formats/rm_file.py:25-45`, `src/remarkable_spec/ocr/postprocess.py:125-140`,
`pyproject.toml:1-25`, `src/remarkable_spec/sync/migrations.py:10-30`,
`src/remarkable_spec/render/engine.py:20-45`, `src/remarkable_spec/models/screen.py:76-104`.

Greps run: `grep -rn "from remarkable_spec.sync" src/` (edge verification),
`grep -n "CREATE TABLE\|CREATE INDEX" src/remarkable_spec/sync/migrations.py` (six tables:
`documents:17`, `pages:35`, `ocr_cache:49`, `diagram_cache:64`, `sync_log:77`, `schema_version:93`),
`grep -n "x_shift" src/remarkable_spec/render/engine.py` (`x_shift = vw / 2` at `:134`),
`grep -n "^app = cyclopts.App" src/remarkable_spec/cli/*.py` (12 hits — one root `App` plus 11
sub-apps, matching the 11 mount calls), and a per-file `@app.default` / `@app.command` count.

**Two off-by-N discrepancies found against the shared environment brief.** Both re-measured, both
resolved in favour of the source, and both recorded under Out-of-scope findings because
`_environment.md` is outside this packet's Scope:

- `_environment.md:154` places the CLI registration block at
  `src/remarkable_spec/cli/__init__.py:52-62`. The 11 `app.command(...)` calls are actually at
  `:48-58`; line 52 is `app.command(ocr_app, name="ocr")`, the fifth of the eleven, not the first.
  The output cites `:48`.
- `_environment.md:358` places the SQLite pragmas at `src/remarkable_spec/sync/db.py:53-54`. WAL is
  at `:54` and `foreign_keys=ON` at `:55`; `:53` is `self._conn.row_factory = sqlite3.Row`. The
  output cites `:54`.

**One codegraph false attribution confirmed**, exactly the name-resolution failure
`_environment.md:74-81` warns about: `codegraph node -f src/remarkable_spec/sync/db.py
--symbols-only` reports `db.py` as "used by 7 files" including `src/remarkable_spec/device/connection.py`
and `src/remarkable_spec/sync/migrations.py`. `grep -rn "from remarkable_spec.sync" src/` shows
neither file imports it — `connection.py` has no `remarkable_spec.sync` import at all, and
`migrations.py:122` imports only `sync.hasher`. The real importers are `sync/__init__.py:13`,
`cli/_util.py:80`, and `device/sync.py:28`. No paragraph in the output asserts an edge that a grep
of import sites did not confirm.

### 7. Directive compliance

- **Directive 1 (no tests).** Nothing in the output claims coverage, verification by tests, or
  regression protection. `tests/__init__.py` is not cited and not listed under `## Supporting code`,
  because `tests/` is not a module under `src/`. The signal is structurally absent; no template
  section here asked for it.
- **Directive 2 (no gitignored paths).** Every cited path is drawn from the 67-entry `git ls-files`
  output. `docs/.repomix/codebase.json` was available but the LOC and inventory questions were
  answered from `git ls-files` + `wc -l` + codegraph instead, so the flattened pack is neither read
  into a claim nor cited. Verified by script — see Validation.
- **Directive 3 (braces).** `src/remarkable_spec/device/paths.py:21-22` does contain the
  brace-wrapped UUID placeholder spelling in its docstring, as warned. The output never reproduces
  it: the `device` paragraph writes "a per-document UUID" in prose, and `models/document.py` is
  described without quoting its docstring. No bare brace appears anywhere in the output; verified by
  script.
- **Directive 4 (no billable or device commands).** None run. The only commands executed were
  `ls`, `find`, `git ls-files`, `git check-ignore`, `wc -l`, `grep`, `awk`, and `codegraph` — all
  offline and free. `rmspec --help` was not needed either, since
  `grep -n "^app = cyclopts.App" src/remarkable_spec/cli/*.py` and the mount block give the command
  census from source.
- **Directive 5 (OCR concurrency attribution).** The `ocr` paragraph places
  `ThreadPoolExecutor(max_workers=2)` at `src/remarkable_spec/ocr/postprocess.py:131`, read directly
  at that line, and describes `pipeline.py` as a straight-line orchestrator
  (`render_rm_to_png` at `src/remarkable_spec/ocr/pipeline.py:25`, `transcribe_rm` at `:86`). The
  output does not call the codebase synchronous.

### 8. Draft, then three corrections found on judgment review

The first draft passed the mechanical validator on the first run. A subsequent read-through for
correctness rather than string-existence found three substantive errors, all now fixed:

1. **Wrong export count, inherited from the brief without checking.** The draft said the root and
   `models` packages each re-export "24 names", taking the figure from `_environment.md:149` and
   `_environment.md:163`. An AST count of the `__all__` list literal gives **26** in both
   `src/remarkable_spec/__init__.py:33` and `src/remarkable_spec/models/__init__.py:47` — 5 color
   names, 2 pen, 2 stroke, 3 page/layer, 7 document, 4 template, 3 screen. This is the exact failure
   the packet's "inherit no claim you have not re-verified" rule targets, arriving through the shared
   brief rather than a prior artifact. Corrected to 26 in both places.

2. **Overgeneralised sidecar-parser claim.** The draft said the three sidecar formats "each get a
   small module with a path-taking and a dict-taking parser". True for `.content`
   (`parse_content` at `src/remarkable_spec/formats/content.py:40`, `parse_content_json` at `:64`)
   and `.metadata` (`parse_metadata` at `src/remarkable_spec/formats/metadata.py:36`,
   `parse_metadata_json` at `:60`), false for `.pagedata`, which exposes only `parse_pagedata`
   (`src/remarkable_spec/formats/pagedata.py:26`) and has no JSON variant. Rewritten to name the
   asymmetry.

3. **Two uncited opening sentences.** The `render` and `ocr` paragraphs each opened with an uncited
   claim, which satisfies the success criterion ("a paragraph with at least one citation") but
   violates the stricter format rule ("every paragraph claim ends in a citation"). The `render`
   opener now cites the "no external dependencies" statement at
   `src/remarkable_spec/render/__init__.py:11`; the `ocr` opener now cites `transcribe_page`, the
   image-only Bedrock path that makes "three ways" true, at `src/remarkable_spec/ocr/postprocess.py:110`.

One remaining sentence in the output carries no citation by design: the preamble's "Modules below are
ordered by total tracked LOC, descending." That is a statement about this document's own arrangement,
not a claim about the codebase, so there is no source line it could point at. The ordering key it
describes is derived in Work log entry 4.

### 9. Files not read in full

`Read` did not fail on any file, so Fallback 2 was never invoked and no bullet was dropped. The
larger command modules (`cli/device_cmd.py`, `cli/sync_cmd.py`, `cli/inspect_cmd.py`,
`cli/ls_cmd.py`, `cli/tree_cmd.py`, `cli/search_cmd.py`, `cli/ocr_cmd.py`,
`cli/annotations_cmd.py`) were characterised from their module docstrings plus their `cyclopts.App`
construction line and decorator counts rather than read end to end — sufficient for a one-paragraph
index entry, and every claim made about them cites a line that was read. No paragraph asserts
anything about the internals of a file whose relevant region was not opened.

## Validation

Everything mechanically checkable is checked by script. The script lives at
`/tmp/doc-architecture-module-map/validate.py` (a PEP 723 standalone, zero dependencies) and exits
non-zero listing every failure.

### Command and output

```text
$ uv run --no-project /tmp/doc-architecture-module-map/validate.py; echo "exit=$?"
PASS checks:
  ok   output exists: docs/architecture/module-map.md (187 lines)
  ok   no YAML frontmatter
  ok   H1 exact: '# remarkable-spec · Module map'
  ok   no mermaid fences
  ok   no emojis
  ok   no filler adverbs (simply / just / basically)
  ok   no bare braces outside fences or inline code
  ok   H2 sections: ['cli', 'models', 'device', 'render', 'ocr', 'sync', 'formats', 'export', 'Supporting code']
  ok   8 content H2 sections (>= 3)
  ok   50 file bullets: all match '- `path` (N LOC)', exist, LOC == wc -l
  ok   every content H2 paragraph carries >= 1 full citation and <= 4 sentences
  ok   51 full + 30 shorthand citations: all resolve in-range, no orphans
  ok   git check-ignore: 0 of 52 cited paths are ignored
  ok   all 52 cited paths are tracked by git
  ok   no citation under tests/

ALL CHECKS PASSED
exit=0
```

Shorthand resolution follows the packet's rule literally: within each section the script tracks the
nearest preceding full `path:LOC` citation and resolves each `` `:LOC` `` against that file, failing
on an orphan or an out-of-range line. All 30 shorthands resolve; 12 of them belong to
`src/remarkable_spec/device/sync.py` and `src/remarkable_spec/sync/migrations.py`, the two files with
the longest shorthand runs.

### Negative test — proof the validator is not vacuous

A passing validator is worthless if it cannot fail. Twelve defects were injected one at a time into a
copy of the output and the validator re-run on each; the original was restored afterwards and
re-confirmed passing.

| Injected defect                                | Expected | Result       |
| ---------------------------------------------- | -------- | ------------ |
| citation line number past end of file          | exit 1   | exit 1       |
| citation to a nonexistent file                 | exit 1   | exit 1       |
| orphan shorthand `` `:12` `` with no full path  | exit 1   | exit 1       |
| bullet LOC off by one from `wc -l`             | exit 1   | exit 1       |
| bullet missing its `(N LOC)` suffix            | exit 1   | exit 1       |
| a ```` ```mermaid ```` fence                    | exit 1   | exit 1       |
| YAML frontmatter prepended                     | exit 1   | exit 1       |
| a gitignored path cited (`docs/.repomix/...`)   | exit 1   | exit 1       |
| a bare brace in a prose sentence               | exit 1   | exit 1       |
| a 9th file bullet in a content H2              | exit 1   | exit 1       |
| a 5th sentence in a content H2 paragraph       | exit 1   | exit 1       |
| a path under `tests/` cited                    | exit 1   | exit 1       |

The sentence-cap case needed a second attempt worth recording: injecting one extra sentence into the
`export` paragraph correctly did **not** fail, because that paragraph has 3 sentences and 4 is the
cap. Injecting two produced `FAIL [export] paragraph has 5 sentences, max is 4`. The check is sound;
the first attempt was a bad test, not a false negative.

### Judgment spot-checks — where a script cannot help

The script proves every cited string exists at a real line. It cannot prove the sentence around the
citation is true, so the whole output was read once against source with that question in mind. That
pass found the three errors in Work log entry 8 — an inherited wrong export count, an
overgeneralised claim about the sidecar parsers, and two uncited opening sentences — none of which the
mechanical run flagged, because all three were made of strings that resolved. Both re-verified counts
were derived rather than eyeballed: `__all__` length by `ast.parse` on the list literal, and the ten
concrete pen renderer classes by enumerating `pens.py` subclasses from the symbol map
(`FinelineRenderer`, `BallpointRenderer`, `MarkerRenderer`, `PencilRenderer`,
`MechanicalPencilRenderer`, `PaintbrushRenderer`, `CalligraphyRenderer`, `HighlighterRenderer`,
`ShaderRenderer`, `EraserRenderer`, excluding the `PenRenderer` protocol and `BasePenRenderer` base).

### Not run, and why

No build, lint, format, or type-check gate was run: this packet's Scope creates one Markdown file and
touches no Python, so `mise lint`, `mise format`, and `mise typecheck` have nothing to say about the
change. `mise test` would pass trivially against an empty suite and is not a gate
(`docs/.packets/_environment.md:196`). No billable or device command was run at any point.

## Summary

Shipped `docs/architecture/module-map.md`, 187 lines: eight content H2s — one per package under
`src/remarkable_spec/` — each carrying a single paragraph of at most four sentences and a bullet list
of its files as `` `path` (N LOC) ``, followed by a `## Supporting code` H2 for the two root-package
files. Ordering is total tracked LOC descending (`cli` 3,899 → `export` 372), chosen over the
inbound-import ranking that the packet also allows: import counts would put `cli` last despite it
owning the only console-script entry point and being the module a reader of an index opens first, and
LOC descending additionally reproduces the size table in the shared environment brief so this doc and
its siblings order modules identically. No prior artifact existed — `docs/architecture/` was not in
the repo — so nothing was inherited and the whole file is drafted from source read this session. Three
fallbacks were available and only one was near-relevant: the module count is 8, under the 12-module
threshold, so every module got a full content H2; only `cli` (14 files) needed a cut to the top 8,
ranked by intra-package inbound imports (`_util.py` at 10 importers, `_resolve.py` at 4, all of the
latter being function-local lazy imports) with LOC as tiebreak, and the six cut command modules are
named in the `cli` paragraph so the list does not read as complete; `Read` never failed, so no bullet
was dropped. The one finding worth escalating is that the shared environment brief carries three wrong
numbers — a 24-vs-26 export count and two off-by-N line ranges — and the first of them made it into
the draft before an AST recount caught it, which is the strongest argument for the packet's rule that
no claim ships unverified regardless of how authoritative its source looks.
