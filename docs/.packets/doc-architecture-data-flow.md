---
role: doc-architecture-data-flow
model: opus
output: "docs/architecture/data-flow.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · architecture/data-flow.md

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

Produce `docs/architecture/data-flow.md`: a walk of the top 3 processes (or data flows) in `remarkable-spec`, each rendered as numbered steps plus one Mermaid `sequenceDiagram`. Every step cites the function that advances the flow.

The reader's question this file answers: "Show me a typical request lifecycle, end-to-end."

## 2. Scope

- Create: `docs/architecture/data-flow.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: entry-point files (HTTP route handlers, RPC tool registrations, CLI command dispatchers, scheduled-job entry points, message-queue consumers).
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` for fast call-chain inspection.

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
2. Enumerate candidate processes. A process is an end-to-end flow triggered by an external event — HTTP request, RPC call, CLI invocation, scheduled tick, queue message. List all you find; cite the entry point for each.
3. Rank processes by likely importance: load-bearing routes (most-called or named after the system's core verb), critical-path background jobs, primary CLI subcommands. Pick the top 3.
4. For each chosen process, walk the call chain outward from the entry point. Record the ordered call sequence: caller → callee → downstream participant. Cap at 8 steps per flow.
5. Resolve each step's target to a logical actor (e.g., CLI / API / Workers / Storage / external service). Actor names match identifiers used in `architecture/module-map.md` where possible.
6. For each step, read the source span at the cited line to confirm the function exists and to extract a one-line summary. Do not paraphrase beyond a one-line description.
7. Draft each flow as `## Flow N: <process-name>`, followed by a numbered step list (every step ends in a backtick `path:LOC` citation), then exactly one `` ```mermaid `` fence containing one `sequenceDiagram`.
8. Write `docs/architecture/data-flow.md` with H1 = `# remarkable-spec · Data flow`.

## 5. Output format rules

- H1 = `# remarkable-spec · Data flow`. No decorative titles.
- No YAML frontmatter on the output file.
- One content H2 per flow, in the form `## Flow N: <process-name>`. Maximum 3 flow H2 sections.
- Each flow body = a numbered step list (Markdown ordered list) + exactly one `sequenceDiagram` Mermaid fence.
- Every numbered step ends with a backtick `path:LOC` citation; the entry-point step cites the function, not the file.
- `sequenceDiagram` participants use short labels (≤ 20 chars); participant names match module/community identifiers from the rest of the tree where possible.
- Maximum 8 steps per flow. Solid arrows (`->>`) for synchronous calls, dashed (`-->>`) for returns.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** source files at entry points and along the call chain.
- **Grep** for handler-registration patterns, `route()` / `tool()` / `command()` decorators, queue-subscription sites.
- **Glob** to enumerate route or handler directories.
- **Bash** for ad-hoc scripts (`jq` over `docs/.repomix/codebase.json` to scan all entry points in one shot if available).
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No clear entry points** (library-style codebase with no top-level "request" concept): treat the top 3 public exports' call chains as the flows. State the substitution in the intro.
- **Fewer than 3 processes qualify** (e.g., a small CLI with one main command): emit only the qualifying count (1 or 2). Do not pad with synthetic flows.
- **A step's call site cannot be confirmed** (`Read` fails or the function moved): truncate the flow at the last verified step and append `> _flow truncated: downstream call site unresolved_` under the numbered list. Still emit the `sequenceDiagram` over the verified subset.
- **A participant has no obvious module alignment:** use the top-level folder name as the participant label and note the substitution in the Work log.

## 8. Success criteria

- [x] `docs/architecture/data-flow.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · Data flow`.
- [x] Between 1 and 3 content H2 sections match `^## Flow \d+:` (grep to verify).
- [x] Exactly one `` ```mermaid `` fence per flow; each fence's first non-empty line is `sequenceDiagram`.
- [x] Every numbered step has a backtick `path:LOC` citation.
- [x] The number of `mermaid` fences equals the number of `## Flow` content H2 sections.
- [x] No participant label exceeds 20 characters.
- [x] The cross-link pass's validator ran against the output and exited 0: every full and shorthand citation resolves to an existing file and an in-range line, with zero orphan shorthands. Paste the command and its output into Validation.
- [x] No YAML frontmatter on the output.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path).

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent process names, participants, or call edges. Every step must map to a verified `Read` span or a confirmed call graph relation.
- Do not write YAML frontmatter on the output file.
- Do not emit more than 3 `sequenceDiagram` blocks. Overflow is for `diagrams/behavioral/sequences.md` (another packet).
- Do not paraphrase step bodies beyond a one-line summary of the cited source span.
- Do not emit emojis. Do not use filler adverbs.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `docs/.packets/_environment.md:154` — the shared brief places the 11 `app.command(...)` registration
  calls at `src/remarkable_spec/cli/__init__.py:52-62`; on disk they occupy `:48-58`. A reader who
  copies the brief's range cites four lines that are not registrations (`:59-62` runs past the block
  into blank lines and `_get_version`) and misses the first four commands. The count of 11 is correct
  and the `sync`/`device` sub-function line numbers in the same bullet do match disk.
- `src/remarkable_spec/cli/ocr_cmd.py:225` — the comment says "Use default Paper Pro screen
  dimensions for the rasterize", but the two lines under it hardcode `1404.0` and `1872.0` (`:226`,
  `:227`), which are the **reMarkable 2** dimensions (`src/remarkable_spec/models/screen.py:80`), not
  Paper Pro's 1620x2160 (`:83`). A reader trusting the comment expects Paper Pro page geometry on the
  bare-PDF OCR path and gets rM2 geometry.
- `src/remarkable_spec/device/sync.py:253` — the `sync_status` docstring documents the third change
  type as `deleted_local`, but the code emits `deleted_on_device` (`:298`), which is also the key
  `src/remarkable_spec/cli/sync_cmd.py:160` styles. A reader building on the docstring matches a
  string the function never returns.
- `src/remarkable_spec/cli/render_cmd.py:85` — `thickness` and `dpi` default to the literals `1.5`
  and `226` (`:89`) rather than to `RmspecSettings.thickness` / `RmspecSettings.dpi`
  (`src/remarkable_spec/cli/_util.py:47`, `:52`). `grep -rn 'settings\.dpi|settings\.thickness' src/`
  returns nothing, so `RMSPEC_DPI` and `RMSPEC_THICKNESS` are declared, documented as env-var
  configurable in their own `Field` descriptions, and read by no code. A reader who exports either
  variable sees no change in output.
- `src/remarkable_spec/export/svg.py:63` — `export_svg` falls back to `RM2_SCREEN` when `screen` is
  omitted, and its docstring at `:45` states that default explicitly, even though the package's
  headline target is Paper Pro. The CLI always passes a `detect_screen` result so the CLI path is
  unaffected; a library caller who omits `screen` silently gets rM2 page geometry.

---

## Work log

### Step 1 — inputs read, prior-artifact check (section 3 + section 4.1)

- Read the packet in full, then `docs/.packets/_environment.md` (442 lines) in full. Its
  stale-prior traps subsection is treated as binding; the four orchestrator directives in section 3a
  were re-read before drafting.
- **Prior-artifact check result: no prior artifact exists.** `ls docs/architecture/data-flow.md`
  returned `No such file or directory (os error 2)`. `git log -1 --format=%cs --
  docs/architecture/data-flow.md` returned empty. `git ls-files docs/` returned empty — nothing under
  `docs/` is tracked at all. `docs/` on disk holds only `docs/.repomix` and `docs/.packets`, both of
  which are inputs, not outputs. `docs/architecture/` does not exist and will be created.
- **What the prior artifact got wrong: not applicable — there was no prior version.** This is a first
  run over a repo with no `docs/` tree, so nothing was inherited, nothing was patched, and every claim
  in the output is drafted from source read in this session. The partition step in Process 4.1
  (`git log -1 --format=%cs` per cited path versus the doc's own last write) has no input to partition.
- `git ls-files` confirms the citable surface: **67 tracked paths**, 56 of them `.py`, matching the
  brief's count at `docs/.packets/_environment.md:122-125`. Every citation in the output will be
  checked against this list plus `git check-ignore` in the Validation section.
- Scratch directory created: `/tmp/doc-architecture-data-flow/`.
- Note on Process step 5: `docs/architecture/module-map.md` does not exist yet (parallel fan-out), so
  sequenceDiagram participant labels are drawn from the eight package names verified at
  `docs/.packets/_environment.md:95-97` (`models`, `formats`, `render`, `ocr`, `device`, `sync`,
  `export`, `cli`) plus external-service labels. This substitution is per Fallback path 4.

### Step 2 — candidate process enumeration (Process 4.2)

Read `src/remarkable_spec/cli/__init__.py` in full (73 lines) and grepped every
`@app.default` / `@app.command` in `src/remarkable_spec/cli/*.py`. This repo has exactly one external
trigger class: a CLI invocation of the `rmspec` console script
(`pyproject.toml:20-21` per `docs/.packets/_environment.md:147`). No HTTP server, no queue consumer,
no scheduler. So every candidate process is a CLI subcommand. Fifteen leaf entry points:

| Entry point                | Registered as        | Function site                                        |
| -------------------------- | -------------------- | ---------------------------------------------------- |
| `inspect`                  | `src/remarkable_spec/cli/__init__.py:48` | `inspect_file` `src/remarkable_spec/cli/inspect_cmd.py:47` |
| `ls`                       | `:49`                | `ls_documents` `src/remarkable_spec/cli/ls_cmd.py:73`      |
| `render`                   | `:50`                | `render` `src/remarkable_spec/cli/render_cmd.py:58`        |
| `tree`                     | `:51`                | `tree` `src/remarkable_spec/cli/tree_cmd.py:60`            |
| `ocr`                      | `:52`                | `ocr` `src/remarkable_spec/cli/ocr_cmd.py:50`              |
| `diagram`                  | `:53`                | `diagram` `src/remarkable_spec/cli/diagram_cmd.py:54`      |
| `search`                   | `:54`                | `search` `src/remarkable_spec/cli/search_cmd.py:46`        |
| `sync` (default/status/pull/push/log) | `:55`     | `src/remarkable_spec/cli/sync_cmd.py:48,98,171,228,368`    |
| `device` (info/ls/pull/push)| `:56`               | `src/remarkable_spec/cli/device_cmd.py:66,162,293,443`     |
| `annotations`              | `:57`                | `annotations` `src/remarkable_spec/cli/annotations_cmd.py:83` |
| `env`                      | `:58`                | `env` `src/remarkable_spec/cli/env_cmd.py:27`              |

**Discrepancy against the brief, recorded in Out-of-scope findings:** the brief puts the
registration block at `docs/.packets/_environment.md:154` lines "52-62"; the file on disk has the 11
`app.command(...)` calls at `src/remarkable_spec/cli/__init__.py:48-58`. The count (11) is right, the
line range is off by four. The `sync` and `device` sub-function line numbers in the brief do match
disk exactly.

### Step 3 — ranking, top 3 picked (Process 4.3)

Ranked by span of the internal module graph each flow traverses (the brief's measured import
direction at `docs/.packets/_environment.md:131-141`) and by whether the flow is named after a core
verb in the package's own quick-start docstring (`src/remarkable_spec/cli/__init__.py:5-10`):

1. **`rmspec render`** — the shared substrate. `cli` → `formats` → `models` → `render` → `export`,
   the longest purely-local chain, and the step every other content flow reuses.
2. **`rmspec ocr`** — the widest flow: it calls the render path as a subroutine, then fans out to two
   external OCR services and one Bedrock model. Contains the codebase's only concurrency site.
3. **`rmspec sync pull`** — the only flow that crosses a network boundary to the device and the only
   one that writes persistent state (SQLite).

Not chosen: `inspect`, `ls`, `tree`, `env` are single-module read-and-print with no downstream fan-out;
`search` and `diagram` are variations that reuse the resolve and render steps already covered by
flows 1 and 2; `device pull` / `sync push` share the transport of flow 3.

### Step 4 — Flow 1 (`rmspec render`) call chain walked and confirmed

Files read in full: `src/remarkable_spec/cli/render_cmd.py` (409 lines),
`src/remarkable_spec/cli/_resolve.py` (291), `src/remarkable_spec/formats/rm_file.py` (213),
`src/remarkable_spec/export/svg.py` (69). Read in part:
`src/remarkable_spec/render/engine.py:75-314`, `src/remarkable_spec/models/screen.py:80-104`.

Confirmed chain, every hop at a read call site:

- `render` `src/remarkable_spec/cli/render_cmd.py:58` branches on the `.rm` suffix at `:112`; a document name goes to
  `_render_document_by_name` `:176`.
- `:189` calls `get_xochitl_dir` (`src/remarkable_spec/cli/_util.py:85`).
- `src/remarkable_spec/cli/render_cmd.py:205` calls `resolve_document_full` (`src/remarkable_spec/cli/_resolve.py:234`), which delegates to `resolve_document`
  `src/remarkable_spec/cli/_resolve.py:27`, which globs `*.metadata` at `:54` and ranks duplicates in `_pick_best` `:121`
  by page count then `lastModified` at `:134` — matching the tie-break `CLAUDE.md` documents.
- `src/remarkable_spec/cli/render_cmd.py:233` / `:271` call `_load_page_from_rm` `:302`, which calls `parse_rm_file`
  (`src/remarkable_spec/formats/rm_file.py:46`) and `detect_screen` (`src/remarkable_spec/models/screen.py:86`).
- `parse_rm_file` reads bytes at `src/remarkable_spec/formats/rm_file.py:66` and hands them to `parse_rm_bytes` `:70`,
  which calls `rmscene.read_tree` at `:83`.
- `src/remarkable_spec/cli/render_cmd.py:241` / `:278` call `_get_pdf_bg` `:313`, which calls `rasterize_pdf_page`
  (`src/remarkable_spec/render/pdf_bg.py:15`) only when `result.pdf_path` is set
  (`src/remarkable_spec/cli/render_cmd.py:323`).
- `src/remarkable_spec/cli/render_cmd.py:251` / `:284` call `_export_page` `:340`, which dispatches on suffix to `export_svg`
  (`src/remarkable_spec/export/svg.py:18`), `export_png`, or `export_pdf`.
- `export_svg` constructs `SVGRenderer` at `src/remarkable_spec/export/svg.py:58` and calls `render_page`
  (`src/remarkable_spec/render/engine.py:91`), which applies `x_shift = vw / 2` at `src/remarkable_spec/render/engine.py:134` and writes the
  file at `:230`.

Surprise worth noting in the output: `export_svg` defaults `screen` to `RM2_SCREEN`
(`src/remarkable_spec/export/svg.py:63`), but the CLI always passes a `detect_screen` result, so the default is dead on
the CLI path.

### Step 5 — Flow 2 (`rmspec ocr`) call chain walked and confirmed

Read in full: `src/remarkable_spec/cli/ocr_cmd.py` (241 lines),
`src/remarkable_spec/ocr/pipeline.py` (128), `src/remarkable_spec/ocr/postprocess.py` (236),
`src/remarkable_spec/ocr/textract.py` (72). Read in part: `src/remarkable_spec/ocr/vision.py:60-139`.
No `rmspec ocr` command was executed — orchestrator directive 4 forbids it, and every claim below is
read from source, not from a run.

Confirmed chain:

- `ocr` `src/remarkable_spec/cli/ocr_cmd.py:50`; lazy-imports `transcribe_rm` at `:86`, resolves the document at `:114`,
  picks target pages at `:123-132` (default is the last page, `:132`).
- `:155` calls `transcribe_rm` (`src/remarkable_spec/ocr/pipeline.py:86`), whose `model_id` default is the hardcoded
  literal `global.anthropic.claude-opus-4-6-v1` at `src/remarkable_spec/ocr/pipeline.py:90`.
- `src/remarkable_spec/ocr/pipeline.py:115` calls `render_rm_to_png` `:25`, which reuses flow 1's parse and SVG export
  (`parse_rm_file` at `:58`, `export_svg` at `:66`) and then rasterizes with `cairosvg.svg2png` at
  `:75`. The intermediate SVG is deleted at `:82`.
- `src/remarkable_spec/ocr/pipeline.py:123` calls `transcribe_page` (`src/remarkable_spec/ocr/postprocess.py:110`).
- **Concurrency, attributed per orchestrator directive 5:** the only executor in the codebase is
  `ThreadPoolExecutor(max_workers=2)` at `src/remarkable_spec/ocr/postprocess.py:131`, submitting `ocr_image`
  (`src/remarkable_spec/ocr/vision.py:63`) and `ocr_image_textract`
  (`src/remarkable_spec/ocr/textract.py:23`) and joining at
  `src/remarkable_spec/ocr/postprocess.py:135-136`. `pipeline.py` is straight-line
  and contains no executor, matching the brief at `docs/.packets/_environment.md:308-314`.
- `src/remarkable_spec/ocr/postprocess.py:139` calls `merge_with_image` `:148`, which base64-encodes the PNG at `:171` and
  formats both OCR texts into `PIPELINE_PROMPT` at `:173`.
- `merge_with_image` calls `_invoke_bedrock_vision` `:187`, which builds a raw
  `anthropic_version: "bedrock-2023-05-31"` body at `:204` and calls `client.invoke_model` at `:228`
  — `invoke_model`, not `converse`, confirming the brief's trap at
  `docs/.packets/_environment.md:421-424`. Extended thinking is on (`:207`), so the reply is scanned
  in reverse for the last text block at `:232-234`.
- External calls confirmed at their client construction sites: Apple Vision at `src/remarkable_spec/ocr/vision.py:89`
  (`VNRecognizeTextRequest`), AWS Textract at `src/remarkable_spec/ocr/textract.py:37` +
  `client.detect_document_text` at `:40`, Bedrock at `src/remarkable_spec/ocr/postprocess.py:200`.

### Step 6 — Flow 3 (`rmspec sync pull`) call chain walked and confirmed

Read in full: `src/remarkable_spec/cli/sync_cmd.py` (426 lines),
`src/remarkable_spec/device/sync.py` (574), `src/remarkable_spec/cli/_util.py` (113),
`src/remarkable_spec/sync/hasher.py` (64). Read in part:
`src/remarkable_spec/device/connection.py:81-220`, `src/remarkable_spec/sync/db.py:26-87`,
`src/remarkable_spec/sync/migrations.py:95-112`. No device command was run — orchestrator
directive 4, and no device is attached.

Confirmed chain:

- `pull` `src/remarkable_spec/cli/sync_cmd.py:171` resolves the local mirror at `:193`, opens the sync DB at `:200`, builds a
  `SyncManager` at `:201`, and enters the SSH connection at `:204`.
- `get_sync_db` (`src/remarkable_spec/cli/_util.py:75`) constructs `SyncDB`
  (`src/remarkable_spec/sync/db.py:26`). The connection is lazy: the `conn` property at `src/remarkable_spec/sync/db.py:48`
  creates the parent directory `:51`, sets `PRAGMA journal_mode=WAL` `:54` and
  `PRAGMA foreign_keys=ON` `:55`, and runs `init_schema`
  (`src/remarkable_spec/sync/migrations.py:99`, `conn.executescript(_SCHEMA_SQL)` at `:104`).
- `_get_connection` `src/remarkable_spec/cli/sync_cmd.py:81` builds `DeviceConnection`
  (`src/remarkable_spec/device/connection.py:38`) with the host default `10.11.99.1` from
  `RmspecSettings.device_host` (`src/remarkable_spec/cli/_util.py:34-38`); `__enter__` `src/remarkable_spec/device/connection.py:219` calls
  `connect` `:81`, which opens a paramiko `SSHClient` `:93` and an SFTP channel `:111`, wrapping any
  failure as `ConnectionError` `:115`.
- `sync.sync_pull` `src/remarkable_spec/cli/sync_cmd.py:206` → `src/remarkable_spec/device/sync.py:303`.
- `sync_pull` calls `sync_status` at `src/remarkable_spec/device/sync.py:328` → `:233`, which lists the remote xochitl
  data directory at `:260` (`DevicePaths.XOCHITL_DATA` = `/home/root/.local/share/remarkable/xochitl`,
  `src/remarkable_spec/device/paths.py:35`) via `connection.list_dir` (`src/remarkable_spec/device/connection.py:202`), fetches
  each `.metadata` to a temp file at `src/remarkable_spec/device/sync.py:274`, and classifies against
  `db.get_document` `src/remarkable_spec/device/sync.py:288` (`src/remarkable_spec/sync/db.py:111`) as
  `new_on_device` `src/remarkable_spec/device/sync.py:290` or `modified_on_device` `:292`; tracked docs
  absent from the device become `deleted_on_device` `:298`.
- For each change, `pull_document` `src/remarkable_spec/device/sync.py:339` → `:92` pulls the five sidecar extensions at
  `:110-119` and the per-document page directory at `:122-131`, each hop through
  `connection.get_file` (`src/remarkable_spec/device/connection.py:166`, SFTP `get` at `:181`).
- `hash_document_files` `src/remarkable_spec/device/sync.py:342` → `src/remarkable_spec/sync/hasher.py:27` SHA-256s the
  `.metadata`, `.content`, and every `.rm` via `hash_file` `:15`.
- State is written at `src/remarkable_spec/device/sync.py:384` (`db.upsert_document`,
  `src/remarkable_spec/sync/db.py:77`), `src/remarkable_spec/device/sync.py:397` (`db.upsert_page`,
  `src/remarkable_spec/sync/db.py:132`) carrying the per-page `rm_hash` computed at
  `src/remarkable_spec/device/sync.py:393`, and `src/remarkable_spec/device/sync.py:408`
  (`db.log_sync`, `src/remarkable_spec/sync/db.py:278`).
- Failure handling worth naming: the per-document `except Exception` at `src/remarkable_spec/device/sync.py:420` demotes
  any error to a skipped entry `:432` and continues, so a partial pull reports success for the rest.
  The bare `except Exception: continue` inside the metadata loop at `:276-277` is the same shape.

## Validation

### Check 1 — scripted validator over the output

Script at `/tmp/doc-architecture-data-flow/validate.py`. It checks the H1 string, absence of YAML
frontmatter, the `^## Flow \d+:` count against the total content-H2 count, one `mermaid` fence per
flow with `sequenceDiagram` as its first non-empty line, every `participant`/`actor` alias and display
label at or under 20 characters, every numbered step ending in a citation, the 8-steps-per-flow cap,
and every citation — full and shorthand — resolving to an existing file and an in-range line with the
shorthand resolved against the last full path in the same H2 section. It also runs
`git check-ignore` and `git ls-files` over every distinct cited path, scans for filler adverbs and
emoji-range characters, and flags bare braces outside fenced blocks and inline code spans
(orchestrator directive 3).

```console
$ python3 /tmp/doc-architecture-data-flow/validate.py; echo "EXIT=$?"
PASSED CHECKS
  ok   H1 OK: # remarkable-spec · Data flow
  ok   no YAML frontmatter
  ok   flow H2 sections: 3 (content H2 total 3)
  ok   mermaid fences: 3
  ok   participant/actor labels checked: 42, all <= 20 chars
  ok   citations: 35 full + 38 shorthand = 73 total
  ok   distinct cited paths: 21
  ok   numbered steps ending in a citation: 24
  ok   git check-ignore: 0 cited paths ignored
  ok   git ls-files: every cited path is tracked
  ok   no filler adverbs (simply / just / basically)
  ok   no emoji-range characters
  ok   no bare braces outside fenced blocks or inline code

All checks passed.
EXIT=0
```

### Check 2 — independent grep cross-checks of the counted properties

```console
$ grep -cE '^## Flow [0-9]+:' docs/architecture/data-flow.md
3
$ grep -c '^```mermaid$' docs/architecture/data-flow.md
3
$ head -1 docs/architecture/data-flow.md
# remarkable-spec · Data flow
$ wc -l docs/architecture/data-flow.md
     177 docs/architecture/data-flow.md
```

24 numbered steps across 3 flows = 8 per flow, exactly at the cap and not over it.

### Check 3 — every cited line printed and read back

Dumped the source text at all 73 citation targets and read each one against the sentence it
supports. Every line matched. Representative samples, the ones a reviewer is most likely to doubt:

- `src/remarkable_spec/render/engine.py:134` → `x_shift = vw / 2`
- `src/remarkable_spec/ocr/postprocess.py:131` → `with ThreadPoolExecutor(max_workers=2) as pool:`
- `src/remarkable_spec/ocr/postprocess.py:228` → `response = client.invoke_model(modelId=model_id, body=body)`
- `src/remarkable_spec/ocr/pipeline.py:90` → `model_id: str = "global.anthropic.claude-opus-4-6-v1",`
- `src/remarkable_spec/cli/_resolve.py:134` → `matches.sort(key=lambda c: (len(c.page_uuids), c.last_modified), reverse=True)`
- `src/remarkable_spec/device/sync.py:110` → `extensions = [".metadata", ".content", ".pagedata", ".pdf", ".epub"]`
- `src/remarkable_spec/sync/hasher.py:22` → `while chunk := f.read(65536):`

### Check 4 — shorthand resolution audit

Printed every shorthand citation next to the full path the section-scoped rule resolves it to, and
confirmed each landed on the file intended when it was written. All 38 shorthands resolve correctly;
zero orphans. Every shorthand also has its full path earlier in the same numbered step, which is
stricter than the packet's same-section rule, so the output survives a per-step or per-row resolver
as well as a per-section one.

### Check 5 — Mermaid renders

`mmdc` 11.16.0 is installed at
`/Users/lalsaado/.local/share/mise/installs/npm-mermaid-js-mermaid-cli/11.16.0/bin/mmdc`. Extracted
each fence to `/tmp/doc-architecture-data-flow/flow{1,2,3}.mmd` and rendered each to SVG. All three
exited 0. Grepping the rendered SVGs back confirms the display labels are the module identifiers
(`cli/render_cmd`, `formats/rm_file`, `render/engine`, `ocr/postprocess`, `device/connection`,
`sync/hasher`, `sync/db`, `reMarkable device`, `Apple Vision`, `AWS Textract`, `AWS Bedrock`), so the
alias form did not silently swap in the short alias as the visible label.

```console
$ for f in /tmp/doc-architecture-data-flow/flow*.mmd; do mmdc -i "$f" -o "${f%.mmd}.svg"; echo "$(basename $f) exit=$?"; done
flow1.mmd exit=0
flow2.mmd exit=0
flow3.mmd exit=0
```

### Check 6 — this packet's own citations

Ran the same resolver over this packet file, extended to accept the range and comma forms the Work log
uses (`path:N-M`, `path:N,M`). 229 line targets, 83 shorthands, all resolving to an existing file and
an in-range line. The only two remaining flags are `:541` and `:1092` in Process step 1 — the
template's own illustration of what a stale citation looks like, not citations, and not mine to edit.

```console
packet: 229 line targets across citations, 83 shorthands
101: orphan shorthand :541
101: orphan shorthand :1092
```

### Fixes applied during validation

- Flow 3 step 6 of the output originally claimed the thumbnails directory but cited only
  `src/remarkable_spec/device/sync.py:110` and `:122`; added `:136`, the actual `thumb_dir` line.
- Flow 3 step 2 of the output claimed parent-directory creation and foreign keys while citing only
  `src/remarkable_spec/sync/db.py:48` and `:54`; added `:51` and `:55`.
- Work log hygiene, found by check 6 and fixed: 39 citations were written with a shortened relative
  path (`render_cmd.py:176` rather than the full `src/remarkable_spec/cli/render_cmd.py:176`) and were
  expanded to full paths. Nine bare `:LOC` shorthands in the Work log pointed at a file that was no
  longer the last full path in their section — for example `:205`, `:233`, `:241`, and `:251` all meant
  `src/remarkable_spec/cli/render_cmd.py` but sat after a full citation of
  `src/remarkable_spec/cli/_util.py`, `_resolve.py`, or `formats/rm_file.py` — and were rewritten with
  the full path. One range was genuinely out of bounds:
  `src/remarkable_spec/models/screen.py:80-105` against a 104-line file, corrected to `:80-104`.
  The output file had no comparable defects; every shorthand there already had its full path within
  the same numbered step.

### Signals that are structurally absent

Test coverage: not reportable. `tests/` holds one 0-byte `__init__.py`
(`docs/.packets/_environment.md:168-173`), so no step in any flow is test-covered and the output makes
no coverage claim. Orchestrator directive 1. Runtime verification of the flows: not performed —
`rmspec ocr`, `rmspec diagram`, `rmspec annotations`, and every `rmspec sync`/`rmspec device`
subcommand are billable or need an attached device (orchestrator directive 4), so every claim traces
to a read source span rather than an observed run.

## Summary

`docs/architecture/data-flow.md` ships three end-to-end flows over the only external trigger this
codebase has — a `rmspec` CLI invocation — since there is no HTTP server, queue consumer, or
scheduler. The three were picked for module-graph span rather than for line count: `rmspec render`
walks `cli` → `formats` → `models` → `render` → `export` and is the substrate the other two reuse;
`rmspec ocr` calls that render path as a subroutine and then fans out through the codebase's only
`ThreadPoolExecutor(max_workers=2)` to Apple Vision and AWS Textract before merging both transcripts
plus the source image through Bedrock `invoke_model`; `rmspec sync pull` is the only flow that crosses
a network boundary, opening paramiko SSH plus SFTP to the tablet and writing document, page, and log
rows into SQLite. Each flow is 8 numbered steps — the cap — with one `sequenceDiagram` apiece, and all
three chains terminate at a real edge rather than trailing off: flow 1 at
`src/remarkable_spec/render/engine.py:230` writing the SVG to disk, flow 2 at
`src/remarkable_spec/ocr/postprocess.py:228` returning the model's transcription, flow 3 at
`src/remarkable_spec/device/sync.py:408` committing the sync log entry. No flow needed the truncation
fallback; the only substitution taken was Fallback path 4, using package folder names as
sequenceDiagram participant labels because `docs/architecture/module-map.md` does not exist yet.
