---
role: doc-behavior-processes
model: opus
output: "docs/behavior/processes.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · behavior/processes.md

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

Produce `docs/behavior/processes.md`: one H2 per top process in `remarkable-spec` (max 8), each with an entry-point line, a numbered step list citing `path:LOC` on every line, and a `### Related` subsection of handler/helper citations. Processes with fewer than 3 concrete steps collapse into a trailing `## Minor flows` H2.

This file is the inventory of "what runs when." Where `data-flow.md` shows three flows in diagram form, `processes.md` is the index of every meaningful flow.

## 2. Scope

- Create: `docs/behavior/processes.md`
- Do not touch: `docs/behavior/state-machines.md`, any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: route handlers, RPC tool handlers, CLI command handlers, scheduled-job entry points, queue/topic consumers, message handlers.
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

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Enumerate process initiators. Walk the codebase for entry-point patterns: HTTP route declarations, RPC tool registrations, CLI command handlers, `@scheduled` / cron declarations, queue/topic subscription sites, message-handler decorators. Group hits by initiator type.
3. For each candidate process, identify the entry point — the first function executed when the initiator fires. Record `process-name → entry-point path:LOC`.
4. For each entry point, walk the call chain outward to identify the ordered step list. A step is a function call that materially advances the flow (skip trivial getters, log lines, error converters). Cap step count per process at a reasonable upper bound (~8); anything beyond is detail belonging to module-map or data-flow.
5. Rank processes by importance: load-bearing routes / RPC tools used by production traffic / primary CLI commands / scheduled jobs critical to system function. Keep the top 8 for full H2 treatment; everything else goes to `## Minor flows`.
6. For each top-8 process, draft the H2 block: `## <process-name>`, an `Entry point:` line citing `path:LOC`, a numbered step list where every step ends in a backtick `path:LOC` citation, then a `### Related` subsection with 3–6 backtick citations to handler/helper files.
7. For `## Minor flows`, draft one bullet per remaining process: name, entry-point citation, 1-line summary.
8. Write `docs/behavior/processes.md` with H1 = `# remarkable-spec · Processes`.

## 5. Output format rules

- H1 = `# remarkable-spec · Processes`. No decorative titles.
- No YAML frontmatter on the output file.
- One content H2 per process. Maximum 8 full-treatment H2s; everything else goes under `## Minor flows`.
- Each full-treatment H2 opens with `Entry point: <backtick path:LOC>` on its own line.
- Step list is numbered. Every numbered step ends with a backtick `path:LOC` citation.
- `### Related` subsection lives at the end of each full-treatment H2; bullets are backtick citations only (no prose). 3–6 bullets.
- `## Minor flows` is a flat bullet list. Each bullet: ``- <name> — entry at `path:LOC`. <one-line summary>.``
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** entry-point files and the step targets.
- **Grep** for initiator patterns: route decorators, command-registration calls, `@schedule`/`@cron`, `on_message`, queue-subscription wrappers.
- **Glob** to enumerate route/handler directories.
- **Bash** for `jq` over `docs/.repomix/codebase.json` to bulk-enumerate entry-point files.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No clear initiators:** if the codebase is a library (no entry-point patterns), treat the top exports' immediate call chains as the processes. State the substitution in the intro of the output file.
- **A process has fewer than 3 verifiable steps:** collapse it into `## Minor flows` rather than emit a tiny H2 section.
- **A step's call target cannot be confirmed:** truncate the step list at the last verified step; append `> _step list truncated: downstream target unresolved_` and move on.
- **More than 8 processes deserve full treatment:** keep the top 8; expand `## Minor flows` to capture the rest.

## 8. Success criteria

- [x] `docs/behavior/processes.md` exists on disk. — scripted check `output exists on disk`.
- [x] H1 line reads `# remarkable-spec · Processes`. — scripted exact-string check.
- [x] At least 3 full-treatment H2 entries exist (excluding `## Minor flows`). — 8.
- [x] At most 8 full-treatment H2s exist. — exactly 8; `## Minor flows` excluded from the count as the template directs.
- [x] Every full-treatment H2 opens with an `Entry point:` line citing `path:LOC`. — 8/8, shape asserted by regex.
- [x] Every numbered step in every process has a backtick `path:LOC` citation. — 62 numbered steps, all ending in a citation; asserted by regex per step.
- [x] Every full-treatment H2 contains a `### Related` subsection with at least 3 backtick citations. — 8/8, between 5 and 6 bullets each, every bullet a bare citation with no prose.
- [x] No content H2 corresponds to a process name not traced to an entry-point file. — all 8 names are `rmspec` subcommands from the registration block at `src/remarkable_spec/cli/__init__.py:48`, each with a verified `def` line (Work log step 4).
- [x] No YAML frontmatter on the output. — file opens `# remarkable-spec · Processes`.
- [x] Prior-artifact check ran: the output path held no file. `ls` returned `No such file or directory`, `git log -1 -- docs/behavior/processes.md` returned empty, `git ls-files docs/` returned empty. Nothing was carried over, so every claim is first-hand.
- [x] The Work log names what the prior artifact got wrong — recorded as "no prior version existed" in Work log step 1, with the three commands that establish it.
- [x] No citation resolves into a generated or gitignored path. — `git check-ignore` run over all 38 cited paths, zero hits; all 38 also present in `git ls-files`. `docs/.repomix/codebase.json` was read but not cited.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent process names. Every content H2 must trace to a verified entry point in source.
- Do not document more than 8 full-treatment processes; overflow goes to `## Minor flows`.
- Do not duplicate handler-file contents in prose form — cite them on numbered lines.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `README.md:57` — The headline AI-assistant workflow tells the user to run `rmspec sync proposal.md --folder "Projects"`, but the `rmspec sync` default handler at `src/remarkable_spec/cli/sync_cmd.py:48` declares only keyword-only parameters (`host`, `user`, `password`, `xochitl`, `--json`) and no positional argument and no `--folder`, so that invocation cannot parse. A reader following the README's primary use case fails on its first step and has no signal that the working form is `rmspec sync push proposal.md --folder "Projects"`, which the same code block shows twelve lines later at `README.md:69`.
- `src/remarkable_spec/ocr/vision.py:161` — `ocr_page` hardcodes `RM2_SCREEN` for both the SVG export and the cairosvg output dimensions (`src/remarkable_spec/ocr/vision.py:172`, `:186`), while every other render path detects the screen from stroke extents (`src/remarkable_spec/ocr/pipeline.py:60`). A reader assumes `rmspec search`'s local OCR rasterizes at the same geometry as `rmspec ocr`; on a Paper Pro it silently renders at reMarkable 2 geometry instead, so the two commands' cached text can disagree for the same page.
- `src/remarkable_spec/device/web_api.py:209` — `WebAPI.search` exists but has zero call sites; `rmspec search --device` hand-rolls the same POST with `httpx` at `src/remarkable_spec/cli/search_cmd.py:91`. Confirmed by grep: the three `WebAPI(...)` construction sites are `src/remarkable_spec/cli/sync_cmd.py:290`, `src/remarkable_spec/cli/device_cmd.py:212`, and `src/remarkable_spec/cli/device_cmd.py:341`, none of which calls `.search`. A reader tracing the device-search path from the `WebAPI` class lands on a method that never runs and misses the timeout and error handling that actually governs the feature.
- `docs/.packets/_environment.md:79` — Names the third `_invoke_bedrock_vision` definition as living at `cli/annotations_cmd.py:254`, repeated at `docs/.packets/_environment.md:231`. The function at that location is `_invoke_annotation_analysis`, defined at `src/remarkable_spec/cli/annotations_cmd.py:251`; line 254 is its `model_id` default. A packet trusting the brief verbatim would cite a symbol name that does not exist in that file, and would also miss that the third copy differs from the other two — it takes `pdf_text` rather than an `OCRResult` pair, omits the `thinking` block, and returns `content[0]` rather than the last text block.
- `docs/.packets/_environment.md:154` — Gives the CLI registration block as `cli/__init__.py:52-62`, repeated at `docs/.packets/_environment.md:82`. Measured at commit `4bb899d` it is `src/remarkable_spec/cli/__init__.py:48-58`. A doc inheriting the range points a reader four lines low: 59 is blank and 61 is `def _get_version`, so the tail of the cited range lands outside the registration block entirely.
- `docs/.packets/_environment.md:39` — The command table promises `codegraph callers <symbol>` for ranking by consumer count, and § 1 warns that lookup is name-resolved. That warning is understated for one-word symbol names: `codegraph callers search` returned a single caller, `_parse_mermaid_response` at `src/remarkable_spec/ocr/diagram.py:261`, which are `re.search` calls and have nothing to do with `WebAPI.search`. A reader ranking by that output would conclude the device-search method has one consumer when it has none.

---

## Work log

### 1. Prior-artifact check — no prior artifact existed

- `ls docs/behavior/processes.md` → `No such file or directory`. `git log -1 --format=%cs -- docs/behavior/processes.md` → empty. `git ls-files docs/` → empty (nothing under `docs/` is tracked; only `docs/.packets/` and `docs/.repomix/` exist, both untracked).
- **Nothing was inherited, so nothing carried over could be stale.** This is a first run over a repo with no `docs/` tree. The success criterion asking what the prior artifact got wrong is satisfied by this finding: there was no prior version, so there are no stale line numbers, wrong counts, fabricated edges, or inverted rules to report.
- Consequence for the drafting rule in Process step 1: every citation in the output is read fresh from source in this run. No sentence is patched around.

### 2. Inputs read

- `docs/.packets/_environment.md` (442 lines) — read in full. Its stale-prior traps are treated as binding.
- `docs/.packets/doc-behavior-processes.md` — this packet.
- `git ls-files` → 67 tracked files, 56 `.py`. Citable-path universe confirmed.

### 3. Two off-by-a-few offsets in the shared brief (recorded, not propagated)

The brief's line numbers for the CLI registration block and the subcommand definitions are each a few lines off from what `grep -n` returns at commit `4bb899d`. I cite my own measurements throughout.

- Brief § 2 says the 11 registrations live at `src/remarkable_spec/cli/__init__.py:52-62`. Measured: `src/remarkable_spec/cli/__init__.py:48-58` (`grep -n 'app.command('`).
- Brief § 2 gives sync subcommands at `sync_cmd.py:47,98,171,228,368` and device at `device_cmd.py:66,162,293,443` — those are the `def` lines. The decorator lines are one earlier in each case (`sync_cmd.py:47,97,170,227,367`; `device_cmd.py:65,161,292,442`). Both are defensible; I cite the `def` line for an entry point so the cited line names the function.

### 4. Entry-point census (Process steps 2–3)

Sole initiator type in this repo: **CLI command dispatch**. No HTTP routes, no RPC tool registrations, no cron/`@scheduled` declarations, no queue or topic consumers, no message handlers. Confirmed by the brief (one console script, `pyproject.toml:20-21`) and by the grep above returning only `@app.default` / `@app.command` / `app.command(<sub_app>, ...)` hits.

19 entry points total: 11 top-level registrations, of which 9 are single-`@app.default` leaves and 2 are groups (`sync` with 5, `device` with 4).

| Process | Entry point |
| --- | --- |
| `rmspec ocr` | `src/remarkable_spec/cli/ocr_cmd.py:50` |
| `rmspec diagram` | `src/remarkable_spec/cli/diagram_cmd.py:54` |
| `rmspec render` | `src/remarkable_spec/cli/render_cmd.py:58` |
| `rmspec annotations` | `src/remarkable_spec/cli/annotations_cmd.py:83` |
| `rmspec sync pull` | `src/remarkable_spec/cli/sync_cmd.py:171` |
| `rmspec sync push` | `src/remarkable_spec/cli/sync_cmd.py:228` |
| `rmspec sync` (default scan) | `src/remarkable_spec/cli/sync_cmd.py:48` |
| `rmspec sync status` | `src/remarkable_spec/cli/sync_cmd.py:98` |
| `rmspec sync log` | `src/remarkable_spec/cli/sync_cmd.py:368` |
| `rmspec device info` | `src/remarkable_spec/cli/device_cmd.py:66` |
| `rmspec device ls` | `src/remarkable_spec/cli/device_cmd.py:162` |
| `rmspec device pull` | `src/remarkable_spec/cli/device_cmd.py:293` |
| `rmspec device push` | `src/remarkable_spec/cli/device_cmd.py:443` |
| `rmspec inspect` | `src/remarkable_spec/cli/inspect_cmd.py:47` |
| `rmspec ls` | `src/remarkable_spec/cli/ls_cmd.py:73` |
| `rmspec tree` | `src/remarkable_spec/cli/tree_cmd.py:60` |
| `rmspec search` | `src/remarkable_spec/cli/search_cmd.py:46` |
| `rmspec env` | `src/remarkable_spec/cli/env_cmd.py:27` |

Every `def` line above was confirmed by reading the file (see step 5), not inferred from the decorator offset.

### 5. Files read in full

`cli/__init__.py`, `cli/ocr_cmd.py`, `cli/diagram_cmd.py`, `cli/render_cmd.py`, `cli/annotations_cmd.py`, `cli/sync_cmd.py`, `cli/device_cmd.py`, `cli/search_cmd.py`, `cli/ls_cmd.py`, `cli/tree_cmd.py`, `cli/inspect_cmd.py`, `cli/env_cmd.py`, `cli/_resolve.py`, `cli/_util.py`, `ocr/pipeline.py`, `ocr/postprocess.py`, `ocr/diagram.py`, `ocr/textract.py`, `device/sync.py`, `device/push.py`, `formats/rm_file.py`, `export/svg.py` — all under `src/remarkable_spec/`. Read in part: `render/engine.py:75-249`, `ocr/vision.py:130-190`, `sync/db.py:40-79`, `device/connection.py:81-140`, `render/pdf_bg.py:1-25`. Symbol maps taken by grep for `export/png.py`, `export/pdf.py`, `sync/migrations.py`, `sync/models.py`, `sync/hasher.py`, `device/web_api.py`, `device/paths.py`, `models/screen.py`, `models/document.py`, `formats/metadata.py`, `formats/content.py`, `formats/pagedata.py`.

### 6. CodeGraph used for edge confirmation, not for the census

The binary at the brief's absolute path works. Three `callers` queries confirmed the fan-in that ranks the top-8:

- `callers render_rm_to_png` → 3 callers: `cli/annotations_cmd.py:209`, `ocr/diagram.py:174`, `ocr/pipeline.py:86`. So the PNG-render step is genuinely shared by the annotations, diagram, and OCR processes rather than duplicated.
- `callers resolve_document_full` → 4 callers: `cli/annotations_cmd.py:83`, `cli/diagram_cmd.py:54`, `cli/ocr_cmd.py:50`, `cli/render_cmd.py:176`. Exactly the four document-name-taking processes.
- `callers transcribe_page` → 2 callers: `cli/ocr_cmd.py:207`, `ocr/pipeline.py:86`.

The CLI census itself came from `grep -n` over the decorators, per the brief's warning that decorator extraction sees literal arguments only. That limit does not bite here — the 11 registrations are literal strings.

### 7. Ranking decision (Process step 5) — top 8 and the overflow

18 candidate processes, cap of 8. Ranked by depth of verifiable call chain and by whether the process is one the `README.md` headline workflows name. Full treatment:

1. `rmspec ocr` — deepest chain in the repo; the only process that touches both AWS services and the one thread pool.
2. `rmspec sync pull` — the incremental-sync engine; the only writer of the `documents` and `pages` tables.
3. `rmspec sync push` — the only write path to the device; `rmspec device push` forwards to it, so documenting it once covers both.
4. `rmspec diagram` — the only process with a read-through/write-back cache around the LLM call.
5. `rmspec annotations` — the annotate-and-read-back workflow `README.md:51-70` calls out.
6. `rmspec render` — the export surface; the only process that reaches all three exporters.
7. `rmspec search` — two independent backends behind one flag; the only user of the `.ocr.txt` sidecar cache.
8. `rmspec ls` — included deliberately over `rmspec sync status` and `rmspec device pull`: it is the only top-8 process that exercises the `formats` metadata/content parsers, so omitting it would leave that layer undocumented anywhere in the file.

Rejected from full treatment, sent to `## Minor flows` (10 bullets): `rmspec sync` (default), `rmspec sync status`, `rmspec sync log`, `rmspec device info`, `rmspec device ls`, `rmspec device pull`, `rmspec device push`, `rmspec inspect`, `rmspec tree`, `rmspec env`. `sync status` in particular has 5 real steps and would qualify on depth, but its step list is a strict subset of `sync pull` step 4 (`sync_pull` calls `sync_status` at `src/remarkable_spec/device/sync.py:328`), so a full H2 would restate rather than add.

### 8. Citation pre-verification

Before drafting, every planned citation was checked by printing its exact source line with `sed -n '<L>p'`. 57 lines checked, 56 landed as intended. One correction applied: `device/sync.py:437` is the `@staticmethod` decorator, so the `_count_pdf_pages` citation moved to `:438`. Output of the check is in Validation.

### 9. Output written

`docs/behavior/processes.md` created. H1 `# remarkable-spec · Processes`, no YAML frontmatter, 8 full-treatment content H2s plus `## Minor flows`. Braces were kept out of every heading, paragraph, and bullet per directive 3 — the per-document UUID directory is described in prose, never spelled with braces.

## Validation

### Mechanical check — scripted, all properties

Script at `/tmp/doc-behavior-processes/validate.py` (PEP 723, `uv run`). It checks every mechanically checkable property in section 8: file existence, exact H1, absence of frontmatter, content-H2 count and the `Minor flows` exemption, per-H2 `Entry point:` line and its citation shape, per-step trailing citation, `### Related` presence and its 3-6 citation-only bullet rule, `Minor flows` bullet shape, then resolves every citation to a real file and an in-range line, runs `git check-ignore` over every cited path, cross-checks each against `git ls-files`, rejects any citation under `tests/`, and finally scans for bare braces outside code spans and fences, emojis, and filler adverbs.

Command:

```text
uv run /tmp/doc-behavior-processes/validate.py
```

Output (71 checks; per-H2 rows elided for the six middle processes, all PASS):

```text
[PASS] output exists on disk -- /Users/lalsaado/Projects/remarkable-spec/docs/behavior/processes.md
[PASS] H1 is exact -- '# remarkable-spec · Processes'
[PASS] no YAML frontmatter -- '# r'
[PASS] at least 3 content H2 -- 8 found
[PASS] at most 8 content H2 -- 8: ['rmspec ocr — handwriting transcription', 'rmspec sync pull — incremental device pull', 'rmspec sync push — file push to device', 'rmspec diagram — Mermaid extraction from handwriting', 'rmspec annotations — PDF annotation read-back', 'rmspec render — page export to SVG, PNG, or PDF', 'rmspec search — notebook text search', 'rmspec ls — document inventory']
[PASS] Minor flows H2 present
[PASS] 'rmspec ocr — handwriting transcription': Entry point line -- Entry point: `src/remarkable_spec/cli/ocr_cmd.py:50`
[PASS] 'rmspec ocr — handwriting transcription': Entry point cites path:LOC
[PASS] 'rmspec ocr — handwriting transcription': has numbered steps -- 8 steps
[PASS] 'rmspec ocr — handwriting transcription': every step ends in a citation
[PASS] 'rmspec ocr — handwriting transcription': has ### Related
[PASS] 'rmspec ocr — handwriting transcription': Related has 3-6 bullets -- 6
[PASS] 'rmspec ocr — handwriting transcription': Related bullets are citations only
... (six processes elided, all PASS) ...
[PASS] 'rmspec ls — document inventory': has numbered steps -- 6 steps
[PASS] 'rmspec ls — document inventory': every step ends in a citation
[PASS] 'rmspec ls — document inventory': Related has 3-6 bullets -- 6
[PASS] Minor flows bullets exist -- 10 bullets
[PASS] every Minor flow bullet has an entry citation
[PASS] no orphan shorthand citations
[PASS] every cited file exists
[PASS] every cited line is in range
       143 citations resolved across 38 distinct files
[PASS] no cited path is gitignored
[PASS] every cited path is git-tracked
[PASS] no citation under tests/
[PASS] no bare braces outside code spans/fences -- 0 found
[PASS] no emojis
[PASS] no filler adverbs

ALL CHECKS PASSED
```

### Fix applied during validation

The first run reported 142 citations, one short of the 143 in the file. Cause: the intro carried the only range-style citation, `pyproject.toml:20-21`, which the validator's `path:digits` pattern skips rather than flags — so a range citation could have shipped unvalidated. Since `pyproject.toml:20` is the `[project.scripts]` table header and `:21` is the `rmspec = "remarkable_spec.cli:app"` declaration itself, the citation was narrowed to `pyproject.toml:21`. Re-run: 143 citations, all resolved. A follow-up grep confirms zero remaining range citations:

```text
$ grep -c '`[A-Za-z0-9_./-]*:[0-9]\+-[0-9]\+`' docs/behavior/processes.md
0
```

### Pre-draft line verification

Before drafting, all 57 planned citations were checked by printing the exact source line with `sed -n '<L>p'`. 56 landed as intended. One correction: `src/remarkable_spec/device/sync.py:437` is the `@staticmethod` decorator, so the `_count_pdf_pages` citation was moved to `:438`, which is the `def` line.

### Judgment spot-checks — is the prose right, not just resolvable

Ten claims the script cannot judge were re-read at source:

| Claim | Verified at |
| --- | --- |
| OCR's Bedrock call has extended thinking enabled | `src/remarkable_spec/ocr/postprocess.py:207` |
| OCR returns the *last* text block, not the first | `src/remarkable_spec/ocr/postprocess.py:232` |
| Diagram extraction runs at temperature 0 | `src/remarkable_spec/ocr/diagram.py:310` |
| Annotations runs at temperature 0 and returns `content[0]` | `src/remarkable_spec/cli/annotations_cmd.py:277`, `src/remarkable_spec/cli/annotations_cmd.py:299` |
| PDF background rasterizes at 2x the target scale | `src/remarkable_spec/render/pdf_bg.py:50` |
| Renderer's minimum padding is 30 points per side | `src/remarkable_spec/render/engine.py:139` |
| Folder-name collision takes the first match | `src/remarkable_spec/cli/sync_cmd.py:306` |
| `sync log` default limit is 20 | `src/remarkable_spec/cli/sync_cmd.py:373` |
| Search context window is 30 characters each side | `src/remarkable_spec/cli/search_cmd.py:239` |
| `sync` default forwards exactly five keyword arguments | `src/remarkable_spec/cli/sync_cmd.py:72` |

All ten claims hold. Two of the table's own line numbers were off by one on first write and were corrected after printing the source line: the diagram temperature moved from `:309` (`max_tokens`) to `src/remarkable_spec/ocr/diagram.py:310`, and the `sync log` limit from `:372` (the `cyclopts.Parameter` line) to `src/remarkable_spec/cli/sync_cmd.py:373` (the `] = 20` default). Neither citation appears in the output file; both were packet-only.

### Binding-directive compliance

- Directive 1 (no tests): the output contains no claim of coverage, verification by test, or regression protection, and cites no path under `tests/` — asserted by the scripted `no citation under tests/` check. Test coverage is structurally absent in this repo (`tests/` holds one 0-byte file), so the signal is unavailable rather than unreported.
- Directive 2 (no gitignored paths): `git check-ignore` run over all 38 cited paths, zero hits; all 38 also appear in `git ls-files`. `docs/.repomix/codebase.json` was available but is not cited.
- Directive 3 (braces): zero bare braces outside code spans and fences, asserted by script. The per-document UUID directory is described in prose in `rmspec sync pull` step 5 and `rmspec sync push` step 6 without brace notation.
- Directive 4 (no billable or device commands): none run. No `rmspec` invocation of any kind was made; the CLI surface was read from source. The only commands executed were `git`, `grep`, `sed`, `wc`, `codegraph`, and `uv run` on the local validation script.
- Directive 5 (concurrency attribution): the `ThreadPoolExecutor(max_workers=2)` is placed in `rmspec ocr` step 7 citing `src/remarkable_spec/ocr/postprocess.py:131`. `src/remarkable_spec/ocr/pipeline.py` is cited in step 6 for straight-line rendering only, and the output nowhere calls the codebase fully synchronous.

## Summary

`docs/behavior/processes.md` shipped: H1 `# remarkable-spec · Processes`, no frontmatter, 8 full-treatment content H2s plus a 10-bullet `## Minor flows`, and 143 citations across 38 tracked source files — every one resolving to a real file and an in-range line, none gitignored, none under `tests/`. No prior artifact existed (the repo has no tracked `docs/` tree), so nothing was inherited and nothing needed un-staling; the file is built from source read in this run. The single initiator type is CLI dispatch — 18 entry points across 11 cyclopts registrations, with no HTTP, RPC, scheduler, or queue surface anywhere in the repo — so the process inventory is the command inventory. Full treatment went to `rmspec ocr` (the deepest chain, and the only process touching both AWS services and the codebase's one thread pool), `rmspec sync pull`, `rmspec sync push`, `rmspec diagram`, `rmspec annotations`, `rmspec render`, `rmspec search`, and `rmspec ls`; the last was chosen over the deeper `rmspec sync status` specifically because it is the only top-8 process that exercises the `formats` metadata and content parsers. `rmspec sync` (default), `sync status`, `sync log`, the four `device` subcommands, `inspect`, `tree`, and `env` went to Minor flows — `sync status` because its step list is a strict subset of `sync pull` step 4, the rest on depth. Six out-of-scope defects were recorded and not touched: a README command form the CLI cannot parse, a hardcoded `RM2_SCREEN` in `ocr_page` that diverges from every other render path, a zero-caller `WebAPI.search`, and three inaccuracies in the shared environment brief (a wrong symbol name, a ten-line-stale registration range, and a `callers` query whose name collision inverts a consumer count).
