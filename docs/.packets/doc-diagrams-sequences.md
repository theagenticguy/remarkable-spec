---
role: doc-diagrams-sequences
model: opus
output: "docs/diagrams/behavioral/sequences.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · diagrams/behavioral/sequences.md

> **Conditional packet.** Only seed if `remarkable-spec` has ≥ 1 process with ≥ 3 verifiable steps. If no qualifying process exists, the packet is skipped at seed time and no file is produced.

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

When every section has real content and every Success criterion is checked off, change `status: COMPLETE` in the packet frontmatter to `status: COMPLETE`.
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

Produce `docs/diagrams/behavioral/sequences.md`: up to three Mermaid `sequenceDiagram` blocks, one per top process, each showing the outbound call order across 4–8 participants.

This file is the diagram-only companion to `behavior/processes.md` and `architecture/data-flow.md`. Where those are prose + steps, this is "diagrams at a glance" — same processes, no surrounding text.

## 2. Scope

- Create: `docs/diagrams/behavioral/sequences.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: the same processes identified by `doc-behavior-processes` (read `processes.md` if it exists; otherwise re-derive top processes from entry-point analysis).
- The call chain for each top process — the outbound dispatch order across participants.

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
2. Identify the top 3 candidate processes. If `docs/behavior/processes.md` exists, take its top 3 content H2s. Otherwise enumerate processes per `doc-behavior-processes`'s Process section and rank.
3. For each chosen process, walk the outbound call chain from the entry point. Record the ordered call sequence and the receiving participant for each call.
4. Group step targets into 4–8 participant lifelines. Participants are the logical actors (User / API / Workers / Storage / external services). Use names that match identifiers from `architecture/module-map.md` or `architecture/system-overview.md`.
5. For each process, draft a Mermaid `sequenceDiagram`: 4–8 participants in dispatch order at the top, solid arrows (`->>`) for synchronous calls, dashed arrows (`-->>`) for returns. Each message label ≤ 15 chars.
6. If any single diagram exceeds 20 elements (participants + labeled messages), keep the top 20 and add a `## Legend (overflow)` table below that block.
7. Write `docs/diagrams/behavioral/sequences.md` with H1 = `# remarkable-spec · Sequences`, one content H2 per process, one Mermaid block per content H2.

## 5. Output format rules

- H1 = `# remarkable-spec · Sequences`.
- One content H2 per process — `## <process-name>` — with the `sequenceDiagram` immediately beneath. Maximum 3 content H2/diagram pairs.
- No YAML frontmatter on the output file.
- Each Mermaid fence contains exactly one `sequenceDiagram`.
- 4–8 participants per diagram. Solid arrows for calls, dashed for returns.
- Every participant must trace to a real module/actor (no invented names).
- Participant labels ≤ 20 chars; edge labels ≤ 15 chars.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** entry-point files and the call chain.
- **Grep** for call patterns from each step's target (function names, decorator usages).
- **Glob** to enumerate handler/dispatcher locations.
- **Bash** for ad-hoc scripts; `jq` over `docs/.repomix/codebase.json` if present.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Fewer than 3 qualifying processes** (only 1 or 2 have ≥ 3 verifiable steps): emit only the qualifying count. Do not pad with sub-3-step processes.
- **A process's call chain cannot be fully resolved:** draw the diagram over the verified subset and note the truncation in the Work log.
- **Participant count would exceed 8:** group adjacent step targets into a single band (e.g., collapse `Parser` + `Lexer` into `Parsing`). Note the grouping in the Work log.

## 8. Success criteria

- [x] `docs/diagrams/behavioral/sequences.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · Sequences`.
- [x] Between 1 and 3 Mermaid fences, each containing a `sequenceDiagram`.
- [x] Every diagram has 4–8 participants with labels ≤ 20 chars.
- [x] Every message edge has a label ≤ 15 chars.
- [x] One content H2 per diagram; H2 text matches a real process name.
- [x] Every participant name maps to a real module/actor. A scripted grep of each participant label against the module roster pre-filters the list; judge by hand only the labels grep cannot resolve.
- [x] No YAML frontmatter on the output.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path).

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent participants, step targets, or message labels. Every identifier traces to a source read.
- Do not emit more than three `sequenceDiagram` blocks.
- Do not emit a diagram for any process with fewer than 3 verifiable steps.
- Do not exceed 20 nodes in any single diagram.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `README.md:74-77` — the "Markdown Push with Diagrams" list claims the `.md` push pipeline
  "automatically" renders ` ```mermaid ` fences to inline PNGs via `mmdc` and resolves image paths to
  base64 data URIs; `_render_markdown` at `src/remarkable_spec/device/push.py:58-113` does neither,
  running only `markdown.markdown` at `:75` with the `tables`, `fenced_code`, and `codehilite`
  extensions and then `weasyprint.HTML(...).write_pdf` at `:112`. A reader following the README will
  push a `.md` file expecting rendered diagrams and get a verbatim fenced code block in the PDF
  instead, and will look for a `mmdc` step in the push path that does not exist.
- `README.md:57` — the AI-assistant workflow example invokes `rmspec sync proposal.md --folder
  "Projects"`, but the `sync` group's `@app.default` is `_default` at
  `src/remarkable_spec/cli/sync_cmd.py:47-70`, which is keyword-only, accepts no positional file, and
  forwards to `status` at `:72`. The push command is `rmspec sync push` at `:228`, which the sibling
  example at `README.md:69` uses correctly. A reader copying line 57 gets a CLI argument error rather
  than a push.
- `src/remarkable_spec/device/sync.py:276-277` — the bare `except Exception: continue` in the
  metadata-fetch loop of `sync_status` turns any per-document fetch or JSON-parse failure into a
  silently skipped document with no log line and no entry in the returned change list. A reader
  running `rmspec sync pull` sees "Already up to date" for a document that in fact failed to be
  examined, because the skip happens before the change list that drives `sync_pull` at `:328` is
  built.
- `src/remarkable_spec/cli/ocr_cmd.py:3-4` — the module docstring says the command "Uses Apple Vision
  framework for high-quality handwriting OCR", naming one engine; the code path it documents calls
  `transcribe_rm` at `:96`, which runs Apple Vision **and** AWS Textract and then Bedrock
  (`src/remarkable_spec/ocr/postprocess.py:131-145`). A reader trusting the docstring will not expect
  `rmspec ocr` to make billable AWS calls, which it does on every invocation.

---

## Work log

### Step 1 — inputs read

- Read the packet in full, then `docs/.packets/_environment.md` (442 lines) in full. Its
  stale-prior traps are treated as binding. Key constraints carried into the draft:
  - The only concurrency is `ThreadPoolExecutor(max_workers=2)` in `ocr/postprocess.py`, not
    `ocr/pipeline.py`.
  - Bedrock is reached via `invoke_model`, never `converse`.
  - `mmdc` (mermaid-cli) is an external binary invoked via `subprocess.run` at three sites.
  - No tests exist; nothing may be described as covered or verified by tests.
  - Never cite `dist/`, `.venv/`, `.codegraph/`, `.pytest_cache/`, `.ruff_cache/`, `.erpaval/`,
    `.claude/`, or `docs/.repomix/`.
- Scratch directory created at `/tmp/doc-diagrams-sequences/`.
- CodeGraph binary path taken from the brief's command table:
  `/Users/lalsaado/.local/share/mise/installs/npm-colbymchenry-codegraph/latest/node_modules/.bin/codegraph`.

### Step 2 — prior-artifact check (Process step 1)

**No prior artifact existed.** `docs/diagrams/behavioral/sequences.md` was absent from disk
(`ls` → `os. error 2`) and absent from git (`git ls-files docs/` returns nothing; `git log -1
--format=%cs -- docs/diagrams/behavioral/sequences.md` returns empty). The entire `docs/` tree is
untracked and holds only `docs/.packets/` and `docs/.repomix/`. So there were no stale line numbers,
no wrong counts, no fabricated edges, and no inverted rules to correct — nothing was inherited. Every
claim in the output was derived from a first-pass source read in this run.

`docs/behavior/processes.md` also does not exist (it is being written concurrently by a sibling
agent), so Process step 2's primary path was unavailable. Fell back to the documented alternative:
re-derived the top processes from entry-point analysis per `doc-behavior-processes`'s Process
section, using the 11-command CLI registration block the environment brief names as the
authoritative surface (`src/remarkable_spec/cli/__init__.py:52-62`).

### Step 3 — process selection and call-chain walks

Three processes chosen, each with far more than 3 verifiable steps, each spanning 6+ modules:

1. `rmspec ocr` — entry `src/remarkable_spec/cli/ocr_cmd.py:50`.
2. `rmspec sync pull` — entry `src/remarkable_spec/cli/sync_cmd.py:171`.
3. `rmspec sync push` on a `.md` file — entry `src/remarkable_spec/cli/sync_cmd.py:228`.

Files read in full to walk the chains: `src/remarkable_spec/ocr/pipeline.py` (128 lines),
`src/remarkable_spec/ocr/postprocess.py` (236), `src/remarkable_spec/ocr/textract.py` (72),
`src/remarkable_spec/cli/ocr_cmd.py` (241), `src/remarkable_spec/device/sync.py` (574),
`src/remarkable_spec/device/push.py` (194), `src/remarkable_spec/sync/hasher.py` (64), plus
targeted ranges of `src/remarkable_spec/cli/sync_cmd.py`, `src/remarkable_spec/cli/device_cmd.py`,
and `src/remarkable_spec/cli/_util.py`, and signature greps over
`src/remarkable_spec/ocr/vision.py`, `src/remarkable_spec/device/connection.py`,
`src/remarkable_spec/sync/db.py`, `src/remarkable_spec/export/svg.py`,
`src/remarkable_spec/formats/rm_file.py`.

**Correction to the assumed step list for process 3.** The orchestrator preamble described the
Markdown push as "Mermaid to PNG via an external binary, then image inlining, then WeasyPrint to
PDF, then upload", and `README.md:74-77` claims the same three steps. The source does not do this.
`_render_markdown` at `src/remarkable_spec/device/push.py:58-113` calls
`markdown.markdown(md_text, extensions=["tables", "fenced_code", "codehilite"])` at `:75` and then
`weasyprint.HTML(string=html).write_pdf(...)` at `:112` — there is no `mmdc` invocation and no
base64 data-URI resolution in that function or anywhere on the `.md` path. `grep -rn mmdc src/`
returns exactly three call sites and none is reachable from `_render_markdown`:
`src/remarkable_spec/ocr/diagram.py:232` (syntax validation),
`src/remarkable_spec/cli/diagram_cmd.py:289` (`rmspec diagram --render`), and
`src/remarkable_spec/device/push.py:129` — which is `_render_mermaid`, the `.mmd` branch, and it
produces a PDF directly via `--pdfFit`, never a PNG. `grep -rn 'data:image/png;base64' src/` has one
hit, `src/remarkable_spec/render/engine.py:379`, in the SVG background embed, unrelated to push.
The diagram was drawn over the verified `.md` chain and the README discrepancy is logged under
Out-of-scope findings.

### Step 4 — CodeGraph cross-check of the drawn edges

Ran `callees` on the three entry-chain hubs (absolute binary path). Two of the three confirmed my
source reads exactly; the third exposed a documented index limit.

- `callees sync_pull` returned exactly `sync_status` (`src/remarkable_spec/device/sync.py:233`),
  `delete_document` (`src/remarkable_spec/sync/db.py:125`), `pull_document`
  (`src/remarkable_spec/device/sync.py:92`), `hash_document_files`
  (`src/remarkable_spec/sync/hasher.py:27`), `hash_file` (`:15`), `upsert_document`
  (`src/remarkable_spec/sync/db.py:77`), `upsert_page` (`:132`), `log_sync` (`:278`) — a perfect
  match to the read of `src/remarkable_spec/device/sync.py:328-408`.
- `callees sync_push_file` returned `_count_pdf_pages`
  (`src/remarkable_spec/device/sync.py:438`), `put_file`
  (`src/remarkable_spec/device/connection.py:183`), `execute` (`:140`), `upsert_document`, and
  `log_sync` — matching the read of `src/remarkable_spec/device/sync.py:497-563`.
- **Two index limits hit, both anticipated by packet section 6.** `callees transcribe_page` returned
  only `merge_with_image` (`src/remarkable_spec/ocr/postprocess.py:148`) plus two module constants —
  it did **not** see `ocr_image` or `ocr_image_textract`, because those are dispatched through
  `pool.submit(...)` at `src/remarkable_spec/ocr/postprocess.py:132-133`. And `callees
  render_to_pdf` returned only the `_RENDERERS` variable
  (`src/remarkable_spec/device/push.py:189`), never `_render_markdown` — the exact table-driven
  dispatch failure the packet warns about, since the lookup at `:49` and the call at `:55` go through
  a dict literal. In both cases the source read at those line numbers is the authority for the edge,
  and the edges were drawn from the read, not the index.

### Step 5 — participant grouping and element-budget truncations

All three diagrams landed at exactly 20 elements, the cap. No diagram needed the
`## Legend (overflow)` fallback, because nothing exceeded 20. Participant counts: 8, 6, 7 — all
inside the 4–8 band, so the Fallback-paths grouping rule was not triggered and no two modules were
collapsed into a single band. What *was* trimmed, per Fallback path 2 (draw over the verified subset,
note the truncation):

- **`rmspec ocr` (8 participants, 12 messages).** `cairosvg.svg2png` is drawn as a self-message on
  `ocr.pipeline` rather than a ninth lifeline, because it is called inline at
  `src/remarkable_spec/ocr/pipeline.py:75` from `render_rm_to_png`, not delegated to
  `export.svg`. `detect_screen` (`:60`) and the intermediate `layers` / `.svg written` returns were
  dropped. The two-worker pool is drawn as a mermaid `par` block labelled `max_workers=2`, which
  matches `src/remarkable_spec/ocr/postprocess.py:131` exactly and keeps the parallelism attributed to
  `postprocess`, not `pipeline`.
- **`rmspec sync pull` (6 participants, 14 messages).** The `deleted_on_device` branch
  (`src/remarkable_spec/device/sync.py:334`) and the per-document `except Exception` skip path
  (`:420-432`) were dropped from the drawing and named in a trailing note under the diagram instead.
  The two loops are drawn as mermaid `loop` blocks matching the metadata loop at `:263` and the change
  loop at `:332`.
- **`rmspec sync push` (7 participants, 13 messages).** `device.web_api` was dropped as an eighth
  lifeline because the `--folder` lookup at `src/remarkable_spec/cli/sync_cmd.py:291` is an optional
  branch; it and `_count_pdf_pages` (`src/remarkable_spec/device/sync.py:497`) are named in the
  trailing note. The `.rm` stub creation at `:531-532` is folded into the single `execute` edge.

Each H2 carries a `### Participants` table and a `### Edge call sites` table so every lifeline and
every arrow has a `path:LOC` citation; the diagram itself sits immediately beneath its H2 as the
output format rules require.

### Step 6 — signals structurally absent

Per orchestrator directive 1, no statement anywhere in the output claims any of these flows is
tested, covered, or regression-protected. `tests/` holds one 0-byte `tests/__init__.py`, so no
coverage signal exists for any participant or edge drawn. Directive 4 was honoured: no `rmspec ocr`,
`rmspec diagram`, `rmspec annotations`, `rmspec sync pull|push`, or `rmspec device *` command was
run. The only binary executed against the work product was `mmdc` on the extracted diagram blocks,
which is offline and free. Directive 3 was honoured: the validator asserts there is no bare brace
outside a code span anywhere in the output, and it passes.

## Validation

Two mechanical gates, both green. Nothing in the output rests on a spot-check where a script could
decide it.

### Gate 1 — structural and citation validator

Script at `/tmp/doc-diagrams-sequences/validate.py` (PEP 723, stdlib only). It checks, in one pass:
no YAML frontmatter and exact H1; no emoji and no filler adverbs (`simply`, `just`, `basically`,
`essentially`); content-H2 count 1–3 with `## See also` exempted; mermaid fence count 1–3, one
`sequenceDiagram` per fence, fence count equal to content-H2 count; per fence 4–8 participants,
participant labels ≤ 20 chars, edge labels ≤ 15 chars, both solid and dashed arrow kinds present,
every message endpoint a declared participant, and total elements (participants + labeled messages)
≤ 20; every `path:LOC` citation resolving to an existing file and an in-range line; every shorthand
`:LOC` resolving against the last full path in the same table row, or the last full path in the same
section for prose, with orphans failing; `git check-ignore --stdin` over every distinct cited path and
a `git ls-files` membership check; every participant label resolvable either to a module path under
`src/remarkable_spec/` or to a literal grep hit in `src/`; and no bare brace outside a code span.

```text
$ uv run --no-project /tmp/doc-diagrams-sequences/validate.py; echo "exit=$?"
=== NOTES ===
  H1 = '# remarkable-spec · Sequences'
  content H2s (3): ['## rmspec ocr — handwriting transcription', '## rmspec sync pull — incremental device pull', '## rmspec sync push — Markdown render and upload']
  mermaid fences: 3
  fence 1: 8 participants, 12 messages, 20 elements
  fence 2: 6 participants, 14 messages, 20 elements
  fence 3: 7 participants, 13 messages, 20 elements
  citations: 76 full, 69 shorthand
  distinct cited paths: 17
  git check-ignore: 0 hits over all cited paths
  all 17 cited paths appear in git ls-files
  participant 'bedrock-runtime' resolved by literal grep: src/remarkable_spec/ocr/diagram.py
  participant 'device.connection' resolved to a module path
  participant 'device.push' resolved to a module path
  participant 'device.sync' resolved to a module path
  participant 'export.svg' resolved to a module path
  participant 'formats.rm_file' resolved to a module path
  participant 'ocr.pipeline' resolved to a module path
  participant 'ocr.postprocess' resolved to a module path
  participant 'ocr.textract' resolved to a module path
  participant 'ocr.vision' resolved to a module path
  participant 'reMarkable device' resolved by literal grep: src/remarkable_spec/models/screen.py
  participant 'rmspec ocr' resolved by literal grep: src/remarkable_spec/cli/ocr_cmd.py
  participant 'rmspec sync pull' resolved by literal grep: src/remarkable_spec/cli/sync_cmd.py
  participant 'rmspec sync push' resolved by literal grep: src/remarkable_spec/cli/sync_cmd.py
  participant 'sync.db' resolved to a module path
  participant 'sync.hasher' resolved to a module path
  participant 'weasyprint' resolved by literal grep: src/remarkable_spec/cli/env_cmd.py
=== RESULT ===
  ALL CHECKS PASSED
exit=0
```

**Fixes applied after the first run.** The validator failed twice on its first pass, both in the
intro paragraph, and both were real defects rather than validator noise:

1. The intro named `behavior/processes.md` and `architecture/data-flow.md` in backticks. Neither file
   exists — they are being written concurrently by sibling agents — so both were flagged as citations
   to missing files. The intro was rewritten to describe this file on its own terms and no longer
   points at a sibling that may not land.
2. Two citations were bare paths with no line number (`src/remarkable_spec/device/paths.py` and
   `src/remarkable_spec/device/web_api.py`). Both were resolved to `path:LOC` form —
   `src/remarkable_spec/device/paths.py:35` for the `XOCHITL_DATA` constant and
   `src/remarkable_spec/device/web_api.py:90` for `list_all_documents`.

### Gate 2 — the diagrams actually parse and render

Every mermaid block was extracted to `/tmp/doc-diagrams-sequences/d{1,2,3}.mmd` and rendered with
`mmdc` 11.16.0 at the absolute path
`/Users/lalsaado/.local/share/mise/installs/npm-mermaid-js-mermaid-cli/11.16.0/bin/mmdc`. This is a
free offline binary, not one of the billable or device commands directive 4 forbids.

```text
$ for i in 1 2 3; do mmdc --input d$i.mmd --output d$i.svg; echo "exit=$?"; done
--- d1 --- Generating single mermaid chart   exit=0
--- d2 --- Generating single mermaid chart   exit=0
--- d3 --- Generating single mermaid chart   exit=0
$ ls -la *.svg
33k d1.svg   32k d2.svg   32k d3.svg
```

All three were also rendered to PNG at 1400 px and inspected visually. Confirmed by eye: the `par`
block in diagram 1 renders as a labelled `max_workers=2` parallel region with the Vision and Textract
branches side by side; the two `loop` blocks in diagram 2 render as nested labelled regions
(`per .metadata`, `per change`); the self-messages (`svg2png`, `sync_status`, `md to HTML`) render as
loop-back arrows on the correct lifeline; and no label is clipped or overlapped in any of the three.

### Judgement properties checked by hand

The four things a script cannot decide, each confirmed against a source read rather than an
assumption: the parallelism sits on `ocr.postprocess`, not `ocr.pipeline`
(`src/remarkable_spec/ocr/postprocess.py:131` versus the straight-line
`src/remarkable_spec/ocr/pipeline.py:113-127`); the Bedrock call is `invoke_model` with a raw
`anthropic_version` body, never `converse` (`src/remarkable_spec/ocr/postprocess.py:204,228`); the
`.md` push branch performs no Mermaid rendering, contradicting `README.md:74-77`
(`src/remarkable_spec/device/push.py:58-113`); and `rmspec device push` is not an independent flow but
a delegation to the same `sync push` function
(`src/remarkable_spec/cli/device_cmd.py:494-504`), which is why it did not consume one of the three
diagram slots.

## Summary

Shipped `docs/diagrams/behavioral/sequences.md` — a new file; the output path held nothing before this
run and the whole `docs/` tree is untracked, so no prior artifact's claims were inherited or patched.
Three `sequenceDiagram` blocks, one per process, all derived from first-pass source reads:
`rmspec ocr` handwriting transcription (8 lifelines — CLI, `ocr.pipeline`, `formats.rm_file`,
`export.svg`, `ocr.postprocess`, `ocr.vision`, `ocr.textract`, `bedrock-runtime` — with the
`ThreadPoolExecutor(max_workers=2)` at `src/remarkable_spec/ocr/postprocess.py:131` drawn as a `par`
region so the parallelism is attributed to `postprocess` rather than `pipeline`);
`rmspec sync pull` incremental device pull (6 lifelines, two `loop` regions covering the metadata
compare and the per-change download, hash, and SQLite upsert); and `rmspec sync push` Markdown render
and upload (7 lifelines, markdown to HTML to WeasyPrint to SFTP to `systemctl restart xochitl` to the
sync DB). No participant grouping was needed — every diagram landed inside the 4–8 band on its own —
and no diagram needed the overflow legend, since all three sit at exactly the 20-element cap with the
trimmed branches named in a trailing note under each. The one substantive correction to the assumed
step list: the orchestrator brief and `README.md:74-77` both describe the Markdown push as rendering
Mermaid fences to inline PNGs before WeasyPrint, and the source does not do it —
`src/remarkable_spec/device/push.py:58-113` runs `markdown.markdown` then `write_pdf` with no `mmdc`
call and no data-URI resolution on that path. 145 citations (76 full, 69 shorthand) over 17 distinct
files, all resolving to in-range lines, none gitignored, all tracked by git.
