---
role: doc-insights-debugging-guide
model: opus
output: "docs/insights/debugging-guide.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · insights/debugging-guide.md

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

Produce `docs/insights/debugging-guide.md`: a failure-mode index (≥ 6 entries), a log/error-surface map, a first-checks ladder, and a known-incident-patterns list. Captures the operational knowledge that usually lives only in the oncall team's collective memory.

The reader's question this file answers: *"Something is broken. Where do I look first?"*

## 2. Scope

- Create: `docs/insights/debugging-guide.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: error-class definitions, retry/circuit-breaker sites, dead-letter-queue handlers, logging configuration, error-handler middleware, sentry/observability bootstrap files.
- Comments in source tagged `// INCIDENT:` / `// POSTMORTEM:` / `// FLAKY:` / `// SLOW:` if any.
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` to scan all error-related sites in one shot.

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

### What a debugger here actually has to work with

- **There are no tests to bisect with.** Every diagnosis path in this guide has to be a manual
  reproduction through the CLI or the library. Say so once, plainly, near the top.
- **The offline, free reproduction surface**: `rmspec inspect <file>` (`.rm`, `.metadata`,
  `.content`, `.pagedata`), `rmspec ls`, `rmspec tree`, `rmspec search`, `rmspec render`,
  `rmspec env`, and `--help` on anything. `rmspec env` (`src/remarkable_spec/cli/env_cmd.py:27`)
  exists specifically to print the resolved environment and is the natural first check.
- **Off-limits for reproduction**: `rmspec ocr`, `rmspec diagram`, `rmspec annotations` (billable
  Bedrock and Textract calls) and `rmspec sync pull|push`, `rmspec device *` (SSH to
  `10.11.99.1`). Document what their failures look like from source and log lines, not from running
  them.
- **The highest-value failure modes to index**, each traceable in source: a missing optional extra
  raising `ImportError` with an install hint (the `try: import cairosvg / except ImportError: raise
  ImportError("... install with: uv add 'remarkable-spec[render]'")` pattern, e.g.
  `src/remarkable_spec/ocr/pipeline.py:52-56`); cairo not found on macOS, which the import-time
  `DYLD_FALLBACK_LIBRARY_PATH` mutation at `src/remarkable_spec/cli/_util.py:68-72` is designed to
  prevent and which resurfaces the moment that variable is already set to something else; the
  xochitl directory being unset, resolved through three fallbacks at
  `src/remarkable_spec/cli/_util.py:85-111`; document-name resolution finding zero or several
  matches (`src/remarkable_spec/cli/_resolve.py:27`, with the tie-break at that function and
  `:234`); `mmdc` absent so Mermaid rendering fails through `subprocess.run`
  (`src/remarkable_spec/ocr/diagram.py:231`, `src/remarkable_spec/cli/diagram_cmd.py:288`,
  `src/remarkable_spec/device/push.py:128`, each with a `TimeoutExpired` branch); an unknown pen
  type or color in a `.rm` file silently defaulting to `FINELINER_1` / `BLACK` with only a warning
  (`src/remarkable_spec/formats/rm_file.py:162,169`); a sync document silently skipped by the bare
  `except Exception: continue` at `src/remarkable_spec/device/sync.py:276-277`; AWS credentials or
  region misconfigured on the four `boto3.client` call sites.
- **Silent-degradation is the dominant failure shape in this codebase**, and it is what makes a
  debugging guide valuable here: 27 broad or bare `except` sites mean the common symptom is missing
  or wrong output rather than a traceback. Structure the first-checks ladder around that.
- **Logging reality**: `logging` is used in `src/remarkable_spec/formats/rm_file.py:18,33` and
  little else; user-facing output goes through `rich` consoles in the CLI modules. There is no log
  file, no log level flag, and no structured logging. Confirm the extent with a grep before
  describing it, and if the answer is "almost none", say that — it is the actionable finding.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Enumerate error/failure surfaces. Grep for: error-class declarations, `throw` / `raise` / `panic` sites, custom error types, retry decorators, circuit-breaker wrappers, dead-letter-queue handlers, timeout configurations. For each, capture `path:LOC` and the symptom that signal would surface (e.g., 500, stuck job, slow response).
3. For each failure surface, identify the most likely root-cause module — the code path that, when broken, produces this symptom. The pairing is the load-bearing entry of the Failure-mode index.
4. For each pairing, draft a "First check" — the single shortest action a debugger would take to confirm whether this root cause applies. Examples: inspect the circuit-breaker state, check the dead-letter queue, read the last N requests for a given user, verify upstream availability.
5. Enumerate log and error surfaces. For each, capture: where it emits (stdout, file, observability platform, custom log channel), the format/shape, what field to grep for. Cite the configuration line.
6. Draft the First-checks ladder — a numbered 5–10 step "if something looks wrong, try this in order" list. Cheapest checks first; most invasive last. Cite where each signal lives in source.
7. Extract known incident patterns from the codebase itself — comments tagged `INCIDENT`/`POSTMORTEM`/`KNOWN BUG`, error-class names that hint at history (`LegacyRaceCondition`, `RetryAfterFlakyDep`), pointer files like `INCIDENTS.md` if any. Each becomes a bullet under `## Known incident patterns` with description, signal, and mitigation.
8. Write `docs/insights/debugging-guide.md` with H1 = `# remarkable-spec · Debugging guide`. Four content H2 sections in fixed order: `Failure-mode index`, `Log and error surfaces`, `First-checks ladder`, `Known incident patterns`.

## 5. Output format rules

- H1 = `# remarkable-spec · Debugging guide`. No decorative titles.
- No YAML frontmatter on the output file.
- Four content H2 sections in fixed order: `Failure-mode index`, `Log and error surfaces`, `First-checks ladder`, `Known incident patterns`.
- `Failure-mode index` table columns: `Symptom | Likely surface | First check | Citation`. ≥ 6 rows.
- `Log and error surfaces` table columns: `Surface | Where it emits | What to grep for | Citation`.
- `First-checks ladder` is a numbered Markdown list (5–10 steps). Each step ends with a backtick `path:LOC` citation.
- `Known incident patterns` is a bullet list. Each bullet has the form `` - **<class>:** description, signal, mitigation. `path:LOC` ``.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Grep** for `throw new \w+Error`, `raise \w+Error`, `panic!`, error-class declarations, `@retry`, `circuit_breaker`, `dlq`, log-config sites.
- **Read** error-handler middleware and logging configuration.
- **Glob** to enumerate observability/logging directories.
- **Bash** for `jq` over `docs/.repomix/codebase.json` to bulk-scan error surfaces.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Fewer than 6 failure modes found:** broaden the search — any explicit error type, any retry/timeout site, any guard clause that produces a user-facing error. If still under 6, state it explicitly in the intro ("This codebase has unusually few explicit failure surfaces; cataloged: …") and emit what you have.
- **No structured logging:** the Log and error surfaces section becomes mostly stdout / stderr entries. Note the limitation; the section still appears.
- **No incident-tagged comments and no POSTMORTEM-style pointer files:** the `Known incident patterns` section gets a single line `No tagged incident history found in source.` It still appears as an H2 — readers benefit from knowing the absence.
- **Codebase uses an observability platform** (sentry, datadog, etc.) but no API access is in scope: cite the bootstrap line in source. Do not invent dashboard URLs or runbook locations.

## 8. Success criteria

- [x] `docs/insights/debugging-guide.md` exists on disk. — 168 lines.
- [x] H1 line reads `# remarkable-spec · Debugging guide`. — byte-exact string compare in check [2].
- [x] All four content H2 sections exist in the fixed order. — check [3] asserts list equality against the fixed order and that no other H2 exists.
- [x] `Failure-mode index` table has ≥ 6 rows. — **18 rows.**
- [x] Every row in `Failure-mode index` and `Log and error surfaces` has a backtick `path:LOC` citation in the Citation column. — checks [4]–[6]; 18 + 10 rows, column sets byte-exact, every Citation cell matched.
- [x] `First-checks ladder` is numbered, 5–10 steps, each citing `path:LOC`. — **10 steps**, numbering asserted sequential 1..10, each step's block asserted to end with a citation.
- [x] `Known incident patterns` is either populated or carries the empty-state line. — both: the empty-state line plus 8 bullets in the required `- **<class>:** …` form.
- [x] No YAML frontmatter on the output. — check [1].
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers. — **the output path held no file**; `docs/insights/` did not exist. Nothing was carried over.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed. — Work log Step 1 records that no prior version existed and why the mtime-partition procedure is inapplicable.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path). — check [11]: 31 distinct paths, `git check-ignore` returns 1 (no hits), and all 31 appear in `git ls-files`.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent failure modes, log surfaces, or incident patterns. Every row traces to a source read.
- Do not invent dashboard URLs, runbook links, or pager destinations — only what's in source.
- Do not skip an H2 just because it's empty; emit the H2 with the empty-state line.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `CLAUDE.md:40` — states that the SHA-256 `rm_hash` "is the cache invalidation key for OCR and diagram
  results." True for diagrams, false for OCR: `SyncDB.get_ocr`, `put_ocr`, and `get_all_ocr`
  (`src/remarkable_spec/sync/db.py:174`, `:192`, `:216`) have zero callers anywhere in `src/`,
  confirmed by both grep and `codegraph callers`. A reader assumes a repeat `rmspec ocr` on an
  unchanged page is served from cache, and is instead billed for Textract plus a Bedrock Opus
  invocation every single time.
- `docs/.packets/_environment.md:154` — cites the CLI subcommand registration block as
  `src/remarkable_spec/cli/__init__.py:52-62`. The actual `app.command(...)` calls are at `:48-58`;
  `:52-62` lands mid-block and runs past it into `_get_version`. A reader trusting the range gets 7 of
  the 11 registrations plus three unrelated lines, and could undercount the CLI surface.
- `docs/.packets/_environment.md:231` and `:236` — list `_invoke_bedrock_vision` as the third
  duplicate implementation at `src/remarkable_spec/cli/annotations_cmd.py:254`. No function of that
  name exists in that file; the third implementation is `_invoke_annotation_analysis`
  (`src/remarkable_spec/cli/annotations_cmd.py:251`), and `:254` is that function's `model_id`
  default — the same line the brief separately and correctly cites for the hardcoded model ID. A
  reader grepping `annotations_cmd.py` for `_invoke_bedrock_vision` finds nothing and may conclude the
  triplication finding is wrong when it is real.
- `src/remarkable_spec/export/png.py:102-112` — dead branch: an `ImportError` is raised inside a `try`
  and immediately re-raised with identical text from its own `except ImportError` handler, so the
  inner raise can never escape. A reader debugging PNG export looks for a second, distinct failure
  condition that does not exist.
- `src/remarkable_spec/formats/rm_file.py:153` — `logger.debug("Skipping unknown scene item type…")`
  is unreachable at runtime. No `logging.basicConfig` or handler is installed anywhere in `src/`, and
  no CLI flag or environment variable in this codebase installs one, so the message sits below
  `logging.lastResort`'s WARNING threshold. A reader treats it as a diagnostic they can switch on.
- `src/remarkable_spec/formats/rm_file.py:31` — an import-time side effect sets the third-party
  `rmscene` logger to `ERROR` process-wide. A library consumer that imports anything touching this
  module silently loses `rmscene`'s warnings for the life of the process, with no way to opt out short
  of resetting the level afterwards.

---

## Work log

### Step 1 — Inputs read, prior-artifact check (Process step 1)

Read in order: this packet in full, then `docs/.packets/_environment.md` (442 lines, read whole). The
brief's stale-prior traps section (`docs/.packets/_environment.md:396-442`) is treated as binding.

**Prior-artifact check result: no prior artifact exists.** `ls -la docs/insights/` returns
`No such file or directory (os error 2)`; `docs/` contains only `.packets/` and `.repomix/`. So there
is **no stale doc to re-verify and nothing the prior artifact got wrong** — this is a first run over a
repo with no `docs/` tree. Every claim in the output is therefore built from a source read in this
session, and the partition-by-mtime procedure in Process step 1 (`git log -1 --format=%cs -- <path>`)
is not applicable.

Confirmed inputs available: `docs/.repomix/codebase.json` (371 KB) and `docs/.repomix/token-tree.txt`
exist and are readable, and per directive 3a.2 are read-only — never cited.

Scratch dir created: `/tmp/doc-insights-debugging-guide/`.

### Step 2 — Error/failure surface census (Process step 2)

Ran three greps over `src/ --include='*.py'`:

| Query | Count | Note |
| --- | --- | --- |
| `^\s*raise ` | 34 sites | 17 of them `ImportError` with an install hint |
| `^\s*except` | 73 sites | |
| brief's broad-handler regex `except (Exception\|BaseException\|OSError)?\s*:\|except Exception` | **27** | matches `_environment.md:237-238` exactly |
| `^\s*except (Exception\|BaseException)\s*(as \w+)?\s*:` | 24 | the truly broad subset |
| `contextlib.suppress` | 3 (`device/sync.py:422`, `device/connection.py:125,129`) | |
| `sys.exit` | 55 | every CLI guard clause exits 1 |
| `class \w*(Error\|Exception)` | **0** | zero custom exception types; everything is a stdlib builtin |

Files read whole: `src/remarkable_spec/cli/_util.py` (113), `src/remarkable_spec/cli/_resolve.py` (291),
`src/remarkable_spec/formats/rm_file.py` (213), `src/remarkable_spec/cli/env_cmd.py` (64),
`src/remarkable_spec/ocr/pipeline.py` (128), `src/remarkable_spec/device/connection.py` (232),
`src/remarkable_spec/cli/__init__.py` (73). Read in ranges: `ocr/diagram.py:200-333`,
`ocr/postprocess.py:100-209`, `device/sync.py:240-459`, `cli/annotations_cmd.py:120-145,245-299`,
`ocr/textract.py:1-60`, `ocr/vision.py:35-105,165-190`, `cli/diagram_cmd.py:225-315`,
`device/push.py:40-80,110-165`, `export/png.py:90-112`, `export/pdf.py:48-80`,
`cli/sync_cmd.py:75-145,195-215,300-425`, `cli/render_cmd.py:190-232,375-408`,
`render/engine.py:330-352`, `sync/migrations.py:70-95,130-151`, `formats/document_loader.py:80-105`,
`cli/search_cmd.py:70-115,140-215`, `device/web_api.py:20-45,95-120`, `sync/db.py:40-60`,
`cli/ls_cmd.py:160-186`, `cli/tree_cmd.py:120-150`, `cli/ocr_cmd.py:100-189`,
`models/screen.py:80-105`.

### Step 3 — Logging reality, measured not assumed (Process step 5)

Directive 3a and the anti-goals forbid describing a log facility I have not verified, so I measured it.

- Three module loggers exist and nothing else: `formats/rm_file.py:33`, `formats/document_loader.py:29`,
  `device/sync.py:25`. Six emit sites total (`rm_file.py:153,162,169`, `document_loader.py:96`,
  `sync.py:352,360,421`).
- `grep -rn -iE 'structlog|loguru|FileHandler|StreamHandler|--verbose|--debug|log_level|LOG_LEVEL|logfile|log_file|sentry|datadog|opentelemetry' src/ --include='*.py'` → **exit 1, zero hits.**
  So: no log file, no log-level flag, no structured logging, no observability platform. Confirmed by
  measurement, per the anti-goal.
- `logging.basicConfig` appears nowhere. Ran an experiment to establish what that means in practice:

  ```
  uv run python -c "... print(logging.getLogger().handlers); print(logging.lastResort) ..."
  root handlers: []
  lastResort: <_StderrHandler <stderr> (WARNING)> level WARNING
  rmscene level: ERROR
  module logger effective: WARNING
  SAMPLE WARNING LINE          <- printed, bare, on stderr
  (SAMPLE DEBUG LINE)          <- not printed
  ```

  Finding: the three `logger.warning` families reach stderr through `logging.lastResort` as a bare
  message with no timestamp, level, or module prefix, and `logger.debug` at `rm_file.py:153` is
  unreachable with no flag to enable it.
- 11 `rich.console.Console()` instantiations, one per CLI module, **all default-constructed** — `grep -rn 'stderr' src/` shows no `Console(stderr=True)`. So every user-facing `[red]Error:[/red]`
  goes to **stdout**, interleaved with normal output.
- The one durable, queryable error record is the `sync_log` SQLite table (`sync/migrations.py:77-90`),
  written by `SyncDB.log_sync` and read by `rmspec sync log` (`cli/sync_cmd.py:367-381`).

### Step 4 — Incident-history search (Process step 7)

- `grep -rn -E 'INCIDENT|POSTMORTEM|FLAKY|SLOW:|KNOWN BUG|WORKAROUND|XXX|TODO|FIXME|HACK' src/ tests/ --include='*.py'` → **exit 1, zero hits.** Matches `_environment.md:224-225`.
- `git ls-files | grep -iE 'incident|postmortem|runbook|oncall|troubleshoot'` → **exit 1, zero hits.**
  No pointer file of any kind.
- Zero custom exception classes, so the "error-class name that hints at history" avenue in Process
  step 7 is also empty.

Decision: emit the fallback empty-state line **and** the defensive-code patterns that encode learned
failures, each with a citation. The empty-state line satisfies section 7; the bullets satisfy the more
useful reading of Process step 7 without inventing an incident. The strongest such marker is
`cli/device_cmd.py:348`, which reads three different spellings of the same device field
(`VissibleName`, `VisibleName`, `VisssibleName`) — nobody writes that speculatively.

### Step 5 — A finding the packet did not seed: the OCR cache is write-only

Verified two ways because it contradicts a governing document.

- `grep -rn -E 'ocr_cache|put_ocr|get_ocr|OCRCacheEntry' src/` shows `SyncDB.get_ocr`
  (`sync/db.py:174`), `put_ocr` (`:192`), `get_all_ocr` (`:216`) defined — and **no call site anywhere
  in `src/`**. The only writer into `ocr_cache` is the one-shot sidecar importer at
  `sync/migrations.py:139-148`.
- CodeGraph agrees: `codegraph callers get_ocr|put_ocr|get_all_ocr --json` each return
  `"callers": []`, while `get_diagram`/`put_diagram` both return `_extract_with_cache`
  (`cli/diagram_cmd.py:202`).
- `cli/ocr_cmd.py` contains no `except`, no `get_sync_db`, and no `rm_hash`; it writes an `.ocr.txt`
  sidecar only under `--save` (`:164-167`) and never reads one back. `cli/search_cmd.py:196-198` is
  the only reader of those sidecars.
- **Consequence, and it is billable:** `CLAUDE.md:40` states `rm_hash` "is the cache invalidation key
  for OCR and diagram results." That is true for `rmspec diagram` and false for `rmspec ocr` — a
  repeat `rmspec ocr` on a byte-identical page re-invokes Textract and Bedrock every time. Flagged in
  Out-of-scope findings per the brief's instruction to cite both and flag rather than pick a side.

## Validation

Every mechanically checkable property is checked by script, per the packet's instruction. Script at
`/tmp/doc-insights-debugging-guide/validate.py` (PEP 723, no dependencies). It covers 13 properties:
existence, absent frontmatter, exact H1, the four content H2s in fixed order with no extras, both
tables' exact column sets and row counts, a citation in every Citation cell, the ladder's step count
and sequential numbering with a trailing citation per step, the incident section's bullet form or
empty-state line, every citation resolving to a real file and an in-range line, shorthand-citation
scoping, `git check-ignore` plus `git ls-files` over every cited path, emoji and filler-adverb
absence, and bare braces outside code spans.

```
$ uv run --no-project /tmp/doc-insights-debugging-guide/validate.py
[1] exists, 168 lines, no YAML frontmatter
[2] H1 ok: # remarkable-spec · Debugging guide
[3] content H2s in fixed order: ['Failure-mode index', 'Log and error surfaces', 'First-checks ladder', 'Known incident patterns']
[4] Failure-mode index: 18 rows (>= 6), columns exact
[5] Log and error surfaces: 10 rows, columns exact
[6] every row of both tables carries a citation in its Citation cell
[7] First-checks ladder: 10 numbered steps, each ending in a citation
[8] Known incident patterns: 8 bullets in required form; empty-state line present=True
[9] 90 full citations, all resolving to a real file and an in-range line
[10] 0 shorthand citations, all with a full path in scope
[11] 31 distinct cited paths, none gitignored, all tracked by git ls-files
[12] no emojis, no filler adverbs
[13] no bare braces outside fenced blocks or inline code spans

ALL CHECKS PASSED
$ echo $?
0
```

**A bug the first validator run had, and the fix.** The first version scoped citation checking to
H2-delimited sections and therefore skipped the pre-H2 preamble entirely: it reported 84 citations
while a whole-file regex found 90. The six intro citations — `pyproject.toml:75`, `mise.toml:9`,
`src/remarkable_spec/device/sync.py:276-277`, `:422`, and the two `connection.py` suppress sites — were
going unvalidated. Added a `("(preamble)", 0, <first H2>)` pseudo-section; the count went 84 → 90 and
the run still passes. Recording this because a validator that silently under-counts is worse than no
validator.

Independent second implementation, written separately to avoid a shared-bug blind spot:

```
$ python3 <inline cross-check>
checked 90 citations across 31 files; failures = 0
git check-ignore rc = 1 (1 = no cited path is ignored)
cross-check exit: 0
```

**Citation shorthand:** the output uses **zero** shorthand `:LOC` citations. Every citation carries its
full path, so the orphan-shorthand failure mode cannot occur, in a table row or anywhere else.

**Judgment spot-checks** — reserved, per the packet, for whether the prose is right rather than
whether the string exists. Read the exact cited lines for five of the least-obvious claims and
confirmed each:

| Claim in the doc | Cited lines read | Verdict |
| --- | --- | --- |
| `sync_log` is written by `SyncDB.log_sync` | `src/remarkable_spec/sync/db.py:279-285` | Confirmed — `"""INSERT INTO sync_log` begins at `:281` inside `log_sync` |
| Device listing reads three spellings of the title field | `src/remarkable_spec/cli/device_cmd.py:346-349` | Confirmed — `d.get("VissibleName", d.get("VisibleName", d.get("VisssibleName", "")))` at `:348` |
| Sync bookkeeping is best-effort and swallows its own failure | `src/remarkable_spec/cli/device_cmd.py:434-438` | Confirmed — `except Exception: pass  # SyncDB logging is best-effort` at `:436-437` |
| The PNG exporter's second raise is a dead branch | `src/remarkable_spec/export/png.py:100-112` | Confirmed — identical `ImportError` raised inside `try` at `:104` and re-raised at `:109` |
| The "No xochitl directory" guard exits 1 | `src/remarkable_spec/cli/render_cmd.py:190-198` | Confirmed — print at `:191-196`, `sys.exit(1)` at `:197` |

**Directive compliance, checked explicitly:**

- *No tests* (3a.1): the doc states the zero-test state in its first body paragraph and cites
  `pyproject.toml:75` and `mise.toml:9` for the wired-but-empty harness. No path under `tests/` is
  cited, and the words "covered", "tested", and "regression-protected" appear nowhere in the output.
- *No gitignored citations* (3a.2): check [11]. `docs/.repomix/codebase.json` was read for inventory
  and is cited nowhere.
- *Braces* (3a.3): check [13] passes — every brace in the output is inside an inline code span. The
  UUID-placeholder spelling from `src/remarkable_spec/models/document.py:8` was never reproduced; the
  doc writes `<uuid>.metadata` and `<page>.rm` in angle brackets instead.
- *No billable or device commands* (3a.4): the only commands executed were `uv run rmspec --help`,
  `rmspec render --help`, `rmspec inspect --help`, `rmspec sync log --help`, `uv run rmspec env`, and
  read-only `git`/`grep`/`awk`/`codegraph`. `ocr`, `diagram`, `annotations`, `sync pull|push`,
  `sync status`, and `device *` were never invoked — their failure modes are documented from source and
  from the literal message strings at the raise and print sites.
- *OCR concurrency attribution* (3a.5): the output makes no claim about where the parallelism lives, so
  it cannot misattribute it. The `ThreadPoolExecutor(max_workers=2)` at
  `src/remarkable_spec/ocr/postprocess.py:131` was read during discovery and is not needed for any
  failure-mode row.
- *Log-facility anti-goal*: nothing about a log level flag, log file, or structured logging is asserted
  except its **absence**, and that absence is established by a grep that exits 1 plus a runtime
  experiment on `logging.lastResort` (Work log Step 3).

## Summary

Shipped `docs/insights/debugging-guide.md` (168 lines) with the four required H2s in fixed order: an
18-row `Failure-mode index`, a 10-row `Log and error surfaces` map, a 10-step `First-checks ladder`,
and 8 `Known incident patterns` bullets. No prior artifact existed — `docs/insights/` had to be
created — so nothing was inherited and every one of the 90 citations traces to a file read in this
session. The failure-mode index was assembled by first censusing the error surfaces mechanically (34
`raise` sites, 73 `except` clauses of which 27 match the broad-handler pattern, 3
`contextlib.suppress`, 55 `sys.exit`, and **zero** custom exception classes), then pairing each surface
with the root-cause module and the shortest offline confirming action — which is why the ladder's first
eight steps cost nothing and the AWS check is deliberately step 10. The organising insight is that
with no custom exception types and 24 handlers catching `Exception` outright, this codebase's normal
failure signature is missing or wrong output rather than a traceback, so the guide indexes symptoms
rather than error classes. Logging was measured rather than assumed: three module loggers, six emit
sites, no `basicConfig`, and a runtime experiment showing warnings reach stderr bare through
`logging.lastResort` while `logger.debug` is unreachable — and all eleven Rich consoles write errors to
**stdout**, so the streams cannot be separated by severity. On incident patterns, the tagged-history
search came back empty on every avenue (zero marker comments, zero pointer files, zero custom
exception names, one commit), so the section carries the prescribed empty-state line **and** eight
defensive-code patterns whose shape only makes sense as a response to a real failure — the strongest
being the three-spelling device-title fallback at `src/remarkable_spec/cli/device_cmd.py:347-348`. One
finding the packet did not seed and which contradicts a governing document: the `ocr_cache` table is
write-only, its three accessors have zero callers by both grep and CodeGraph, so `CLAUDE.md:40`'s
cache-invalidation claim holds for `rmspec diagram` and not for `rmspec ocr` — a repeat OCR run
re-bills Textract and Bedrock. That plus five other defects in out-of-scope files are recorded in
Out-of-scope findings.
