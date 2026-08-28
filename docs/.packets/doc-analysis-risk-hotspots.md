---
role: doc-analysis-risk-hotspots
model: opus
output: "docs/analysis/risk-hotspots.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · analysis/risk-hotspots.md

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

Produce `docs/analysis/risk-hotspots.md`: a ranked table of the top 12 files in `remarkable-spec` by combined risk score, followed by a `## Per-file drill-down` H2 covering the top 5 hotspots — what's there, recent activity, ownership, and any open findings.

"Risk score" is whatever signal the environment can supply. The default composition is **30-day activity trend + finding severity**. If activity history or findings are unavailable, the file can rank by one signal alone (state the limitation in the intro).

## 2. Scope

- Create: `docs/analysis/risk-hotspots.md`
- Do not touch: `docs/analysis/ownership.md`, `docs/analysis/dead-code.md`, any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- Git history if available — `git log --since=30.days.ago --name-only` for activity trend per file.
- Static-analysis findings if available — output from whatever linter/scanner the repo runs (severity-labeled per file).
- Ownership signals — `git log --pretty=format:%an` per path.
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

### Signal availability for this packet

**There is no activity signal.** The repository has exactly one commit (`4bb899d`, 2026-03-06) and
one author, so churn, co-change frequency, recency, and bus factor are all uncomputable, not merely
low. State this in the file's **first paragraph** and do not emit a churn column, a "last touched"
column, or any ranking that implies one. Fabricating a churn number here is the single most likely
way this file ships wrong.

Rank on **static finding signals only**, and name the signal set you used so a reader can audit the
ranking. Signals genuinely available here:

- File length and function length. The five longest files are `src/remarkable_spec/device/sync.py`
  (573 LOC), `src/remarkable_spec/cli/device_cmd.py` (504), `src/remarkable_spec/render/pens.py`
  (480), `src/remarkable_spec/cli/sync_cmd.py` (425), `src/remarkable_spec/cli/render_cmd.py` (408).
- The 27 broad or bare `except` sites across `src/`
  (`grep -rn -E 'except (Exception|BaseException|OSError)?\s*:|except Exception' src/ --include='*.py'`).
  `src/remarkable_spec/device/sync.py:276-277` is the representative case: a bare
  `except Exception: continue` that turns a per-document metadata fetch failure into a silently
  skipped document.
- Count of distinct optional extras a file needs in order to work at all. A file that requires
  cairo plus pyobjc plus boto3 is fragile in a way a pure-`models` file is not.
- `subprocess.run` calls on an external binary that may not be installed — `mmdc` at
  `src/remarkable_spec/ocr/diagram.py:231`, `src/remarkable_spec/cli/diagram_cmd.py:288`,
  `src/remarkable_spec/device/push.py:128`.
- Trust boundaries: SSH to a hardcoded default host, HTTP to the device web API, AWS API calls,
  binary parsing of untrusted `.rm` files through `rmscene`.
- Constants duplicated across files, which make a fix land in one place and not the others.
- The total absence of tests, which is a **multiplier** on every signal above rather than a row of
  its own — a 573-LOC file with 6 broad excepts and no test is the top of this ranking.

`codegraph impact <symbol> -d 3 --json` and `codegraph callers <symbol> --json` give blast radius,
which is a legitimate risk multiplier — use them. The `affected` and untested-symbol capabilities in
the brief's command table return nothing useful here because there are no tests; **do not report
their empty output as evidence of low risk.**

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Determine what risk signals are available. Try `git log` over the last 30 days; try reading any committed `.eslintcache` / `ruff_cache` / `findings.json` / similar artifacts; try invoking the project's lint/typecheck/test in dry-run if cheap. Record signal availability in the Work log before proceeding.
3. Compute per-file activity trend over a 30-day window. Slope is `↑ rising` (commit count > median + 1σ), `→ flat` (within σ), `↓ falling` (commit count < median − 1σ).
4. Compute per-file finding counts from any static-analysis signal available. Group by `warn` / `error` severity.
5. Compose a per-file risk score. Default: `2 × error_count + 0.5 × warn_count + 1 × (slope == "rising" ? 1 : 0)`. Adapt if signals are missing; document the composition in the file's intro.
6. Rank files. Keep the top 12 for the ranking table; the top 5 feed the drill-down.
7. For each top-12 file: resolve the top owner (highest commit share over 30 days). Capture share percentage.
8. Draft the intro — 2 paragraphs: paragraph 1 defines "risk" as composed here; paragraph 2 names signal limitations (e.g., insufficient git history).
9. Draft the ranking table with columns: `File | Trend | Open findings | Top owner | Citation`. Every Citation cell is a backtick path with optional `(N LOC)`. Every Open findings cell is `N warn, M error` derived from the static-analysis signal.
10. Draft the `## Per-file drill-down` section. One H3 per top-5 hotspot. Each H3 covers: a 2-sentence "What's there" summary (cite source), "Recent activity" (commit count, trend), "Owners" (top 1-2 with percentage), "Findings" (counts by severity, cited).
11. Write `docs/analysis/risk-hotspots.md` with H1 = `# remarkable-spec · Risk hotspots`.

## 5. Output format rules

- H1 = `# remarkable-spec · Risk hotspots`. No decorative titles.
- No YAML frontmatter on the output file.
- Intro = 2 paragraphs.
- Ranking table has exactly these columns: `File | Trend | Open findings | Top owner | Citation`. At least 10 rows (12 preferred).
- Trend arrows use only: `↑ rising` / `→ flat` / `↓ falling`. No other symbols.
- Drill-down uses H3s under a single `## Per-file drill-down` content H2 — not free-floating content H2s.
- Owner shares are whole-percent integers (`68%`, not `0.68`).
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Bash** for `git log` queries (activity trend, ownership share).
- **Read** for static-analysis output files if committed, and for source-file "what's there" summaries.
- **Grep** for known marker patterns (`TODO`, `FIXME`, error-class instantiation sites) as a fallback risk signal.
- **Glob** to enumerate the candidate file set.
- Run the project's lint/typecheck only if cheap and idempotent.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No git history available** (shallow clone, fresh repo): skip the Trend column from the ranking table, rank by finding severity alone, state the limitation in the intro.
- **No static-analysis signal available:** drop the Open findings column, rank by activity trend alone, state the limitation in the intro.
- **Neither activity nor findings available:** rank by `TODO`/`FIXME`/`HACK` marker density per file as a last-resort signal. State the heuristic in the intro. If even that fails, write the gap to the Work log and flip `status` to `BLOCKED` — this packet was seeded under wrong preconditions.
- **A file has no owner** (single-commit history, or `git log` returns nothing): mark Top owner as `—` and note in the Work log.

## 8. Success criteria

- [x] `docs/analysis/risk-hotspots.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · Risk hotspots`. Exact-match asserted by `validate.py`.
- [x] The ranking table has at least 10 rows. 12 rows.
- [x] Every ranking row has a backtick path citation in the Citation column. Each cell is a `:LOC`
      shorthand anchored to the full path in the same row's `File` column, plus that file's line count.
- [x] A single `## Per-file drill-down` content H2 exists, containing 5 H3s. Asserted by script:
      content H2 list is exactly `['## Per-file drill-down']`, H3 count is 5, all after the H2.
- [x] Every drill-down H3 cites at least 2 `path:LOC` references. Lowest is `_resolve.py` at 11;
      per-section counts are in the shorthand-anchor audit in Validation.
- [x] Every `Open findings` cell was recomputed from the static-analysis signal by script and matches
      the table cell exactly. `validate.py` re-runs `score.py` and diffs cell by cell; command and
      output pasted in Validation.
- [x] No YAML frontmatter on the output. First line is the H1.
- [x] Prior-artifact check ran: **the output path held no file.** `docs/analysis/` did not exist
      (`os error 2`), so nothing was carried over and nothing needed re-verification.
- [x] The Work log names what the prior artifact got wrong — recorded as "no prior version existed",
      with the `ls` evidence, in Work log step 1.
- [x] No citation resolves into a generated or gitignored path. `git check-ignore` run over all 21
      cited paths (clean), plus `git ls-files --error-unmatch` to confirm each is tracked — the second
      check added because `check-ignore` alone silently misses `.codegraph/`, which is untracked but
      unignored (see Out-of-scope findings).

**Signals marked unavailable rather than met**, each with its reason:

- *Activity trend / 30-day slope* — unavailable. One commit (`4bb899d`, 2026-03-06), one author;
  `git log --since=30.days.ago --name-only` returns nothing. `Trend` column dropped under fallback
  path 1 and its absence asserted by script.
- *Top-owner commit share* — unavailable. One author across one commit; every cell is `—` under
  fallback path 4, asserted uniform by script.
- *Test coverage / untested-symbol* — structurally absent. `tests/` holds one 0-byte `__init__.py`.
  Reported as a global multiplier in the intro, never as a per-row value, and the validator asserts the
  doc makes no "covered by tests" style claim.
- *Marker density (`TODO`/`FIXME`/`HACK`)* — zero occurrences in `src/`, so fallback path 3 was
  unusable as a last-resort signal.
- *Static-analysis artifact with vendor severity labels* — none committed. Severities are derived from
  scripted source counts under a stated mapping rather than lifted from a scanner.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent finding counts, owner names, or trend slopes. Every value must trace to a signal source.
- Do not write YAML frontmatter on the output file.
- Do not reorder the ranking columns; downstream readers expect the fixed schema.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

**Brief and directive defects (documentation, not source):**

- `docs/.packets/_environment.md:230-233` claims `_invoke_bedrock_vision` is "implemented three times"
  and names `src/remarkable_spec/cli/annotations_cmd.py:254` as the third site. Only two functions carry
  that name (`src/remarkable_spec/ocr/postprocess.py:187`, `src/remarkable_spec/ocr/diagram.py:286`);
  the third is `_invoke_annotation_analysis` at `src/remarkable_spec/cli/annotations_cmd.py:251`, and
  `:254` is that function's `model_id` default-argument line. A packet that greps for the shared symbol
  name will find two hits, conclude the brief is stale, and may drop the duplication finding entirely —
  when the duplication is real and only the name differs.
- `docs/.packets/doc-analysis-risk-hotspots.md:76-79` (directive 2, replicated in every packet) states
  that `.codegraph/` is gitignored and that `git check-ignore` will flag a citation into it. It will
  not: `git status --short` reports `?? .codegraph/` and `?? docs/`, both untracked but **unignored**,
  because the repo-root `.gitignore` (46 lines) never mentions `.codegraph/`, `.pytest_cache/`,
  `.ruff_cache/`, or `.erpaval/`. A Phase 6 validator that relies on `git check-ignore` alone will pass
  a citation into `.codegraph/` that the directive intends to fail. `.ruff_cache/` and `.pytest_cache/`
  *are* ignored, but through nested self-written files (`.ruff_cache/.gitignore:2`,
  `.pytest_cache/.gitignore:2`), not the root list — and `git check-ignore` only reports them when the
  argument carries a trailing slash, which is a silent false-negative for any validator that omits it.
  Adding a `git ls-files --error-unmatch` tracked-path check alongside `check-ignore` closes both gaps;
  my validator does that.

**Source defects tripped over while verifying citations (outside this packet's Scope, not fixed):**

- `src/remarkable_spec/cli/search_cmd.py:167` parses a document's `.content` file with no exception
  guard, eight lines below `:152-153` where the sibling `.metadata` read is wrapped in
  `except Exception: continue`. A corrupt `.content` therefore aborts the whole `rmspec search` run with
  an uncaught `JSONDecodeError` while a corrupt `.metadata` is skipped silently — two adjacent reads in
  one loop with opposite failure policies, so a user cannot predict which corruption is fatal.
- `src/remarkable_spec/cli/search_cmd.py:88` interpolates the raw user search term into a URL path
  (`f"http://{host}/search/{query}"`) with no percent-encoding before posting it at `:91`. A query
  containing a slash, `?`, `#`, or a space silently produces a different request than the user typed.
- `src/remarkable_spec/cli/search_cmd.py:196-207` writes OCR results to a `.ocr.txt` sidecar beside the
  source file, keyed on filename rather than content, so the cache never invalidates when the `.rm`
  changes — while `src/remarkable_spec/sync/migrations.py:128-145` exists to migrate exactly these
  sidecars into the content-addressed `ocr_cache` table keyed on `rm_hash`. New writes keep recreating
  the legacy artifact the migration was written to retire.
- `src/remarkable_spec/cli/device_cmd.py:436-437` swallows the sync-database write that records a
  *successful* pull (`except Exception: pass  # SyncDB logging is best-effort`). The pull succeeds on
  disk and the ledger never learns it, so the next incremental sync re-pulls the same document with no
  error surfaced on either run.
- `src/remarkable_spec/device/sync.py:448` swallows any pymupdf failure in `_count_pdf_pages` and falls
  back to a regex byte-scan at `:453`. A wrong page count propagates into the `page_uuids` list at
  `:515` and therefore into the number of empty `.rm` stubs written to the device at `:532`, so a
  mis-counted PDF lands on the tablet with a mismatched page mapping.
- `src/remarkable_spec/device/connection.py:94` installs `paramiko.AutoAddPolicy()`, accepting any host
  key with no `known_hosts` check and no setting to tighten it. Harmless over the USB default
  `10.11.99.1`, but the same class serves the Wi-Fi path documented at
  `src/remarkable_spec/cli/device_cmd.py:96-98`, where a reader would reasonably assume host
  verification applies and it does not.
- `src/remarkable_spec/cli/_resolve.py:277` calls `_get_page_uuids` inside the per-page loop,
  re-reading and re-parsing the same `.content` file once per unannotated page.

---

## Work log

### Step 1 — packet + environment brief read; prior-artifact check

- Read this packet in full, then `docs/.packets/_environment.md` (442 lines) in full. Its
  stale-prior traps subsection and § 2 topology are treated as binding.
- **Prior-artifact check: no prior artifact exists.** `ls -la docs/analysis/` returns
  `No such file or directory (os error 2)`. `docs/` currently contains only `.packets/` and
  `.repomix/`. So there is nothing stale to partition, no citation ranges to re-verify against an
  older doc, and **no claim is inherited**. Nothing carried over; the file is built from source.
  Recorded here to satisfy the success criterion that names what the prior artifact got wrong: there
  was no prior version, so the answer is "nothing — first run over a repo with no `docs/` tree".
- Scratch directory created: `/tmp/doc-analysis-risk-hotspots/`.

### Step 2 — signal availability (packet section 4.2)

| Signal | Available? | Evidence |
| --- | --- | --- |
| Git activity trend, 30-day | **No** | `git log --oneline \| wc -l` = 1. `git log --since=30.days.ago --name-only` returns empty (sole commit is 2026-03-06, ~175 days back). |
| Ownership share | **No** | `git log --pretty=format:'%an' \| sort \| uniq -c` = `1 Laith Al-Saadoon`. One author, 100% of one commit — a share column would be a constant. |
| Committed lint/scan artifact | **No** | No `findings.json`, no committed `.ruff_cache`/`.eslintcache`. `.ruff_cache/` and `.pytest_cache/` exist but are gitignored, so uncitable per directive 2. |
| `TODO`/`FIXME`/`HACK` markers | **No (zero)** | Brief § "Marker inventory": 0 hits. Fallback path 3 is therefore unusable as well. |
| Test coverage / untested-symbol | **No** | `tests/` = one 0-byte `__init__.py`. Recorded per directive 1; not reported as low risk. |
| File and function length | **Yes** | `git ls-files '*.py' \| xargs wc -l \| sort -rn` — 10,321 total, matching brief § 2. |
| Broad/bare `except` sites | **Yes** | 27 across `src/`, per-file counts computed. Matches the brief's count exactly. |
| Silent-swallow subset | **Yes** | 17 `except` handlers whose body is `pass`/`continue`/bare `return`, found with an AST-adjacent script; 13 of the 17 are also broad. |
| Optional-extra fan-in | **Yes** | Grep for imports of `cairocffi\|cairosvg\|PIL\|Vision\|Quartz\|boto3\|paramiko\|httpx\|weasyprint\|markdown\|pymupdf`. |
| External-binary `subprocess.run` | **Yes** | 3 sites, all `mmdc`. |
| Trust boundaries | **Yes** | SSH (`paramiko.AutoAddPolicy` at `device/connection.py:94`), HTTP device web API, `boto3` Textract + Bedrock, `rmscene` binary parse. |
| Duplicated constants / implementations | **Yes** | Model-ID literal x4; `_invoke_bedrock_vision` x2 plus a third near-copy under a different name. |
| Blast radius | **Yes** | CodeGraph `node -f <path> --symbols-only` header gives a per-file dependents count for all 45 non-`__init__` source files; ran in one loop. |

Conclusion: **fallback path 1 applies** (no git history → no Trend column, no owner shares). Fallback path
2 does not apply in its literal form — there is no linter *artifact*, but there are reproducible static
finding counts, so the `Open findings` column is retained and populated from scripted counts rather than
from a scanner's severity labels. Fallback path 3 (marker density) is unusable: 0 markers.

Ranking composition adopted, since the packet's default formula needs a `slope` term that does not
exist here:

```text
risk = 2*silent_broad_except + 1*(broad_except - silent_broad_except)
     + 1.5*trust_boundary_count + 1*extra_count + 1*external_binary
     + LOC/100 + dependents/4
```

Zero tests is applied as a stated global multiplier in prose, not as a per-row term, per directive
section 3a ("a multiplier on every signal above rather than a row of its own").

### Step 3 — CodeGraph blast radius, and one misleading edge caught

Ran the absolute binary
`/Users/lalsaado/.local/share/mise/installs/npm-colbymchenry-codegraph/latest/node_modules/.bin/codegraph`
(shim not trusted, per the brief). `node -f <path> --symbols-only` over all 45 non-`__init__` source
files gave dependents counts. Highest: `models/page.py` 12, `cli/_util.py` 11, `models/screen.py` 11,
`formats/rm_file.py` 9, `models/document.py` 7, `sync/db.py` 7.

**Name-collision artifact confirmed and rejected.** CodeGraph reports `device/connection.py` as
"used by ... `src/remarkable_spec/sync/db.py`, `src/remarkable_spec/sync/migrations.py`". That edge
does not exist: `grep -rn 'from remarkable_spec' src/remarkable_spec/sync/` shows `sync/` imports only
`sync.models`, `sync.migrations`, `sync.db`, `sync.hasher` — nothing from `device`. It is the
`connect`/`_connect` name collision the brief warned about. I ranked `device/connection.py` with 4
confirmed dependents, not 6, and did not draw a `sync -> device` edge anywhere.

**One edge that looked like a collision but is real:** `ocr/textract.py:11` genuinely does
`from remarkable_spec.ocr.vision import OCRLine, OCRResult`. Confirmed at the import site before use.

**`formats/rm_file.py` fan-in confirmed at 9 real import sites**, not inferred: `formats/__init__.py:19`,
`formats/document_loader.py:25`, `ocr/vision.py:159`, `ocr/pipeline.py:47`, `cli/ocr_cmd.py:190`,
`cli/diagram_cmd.py:362`, `cli/render_cmd.py:150` and `:304`, `cli/inspect_cmd.py:105`,
`cli/annotations_cmd.py:222`.

### Step 4 — a brief claim that does not hold, and one it understates

- The brief (`docs/.packets/_environment.md:230-233`) says `_invoke_bedrock_vision` is "implemented
  three times", listing `src/remarkable_spec/cli/annotations_cmd.py:254` as the third.
  `grep -rn 'def _invoke' src/` returns only two functions with that name
  (`ocr/postprocess.py:187`, `ocr/diagram.py:286`); the third is named `_invoke_annotation_analysis`
  and starts at `src/remarkable_spec/cli/annotations_cmd.py:251`, with `:254` being its `model_id`
  default-argument line. The *duplication* finding is real — three near-identical Bedrock
  `invoke_model` wrappers — but the symbol name is not shared, so I wrote it as "two under one name
  plus a third renamed copy" and cited the real definition lines. Logged as an out-of-scope finding.
- The brief lists the SSH trust boundary but not this part of it: `device/sync.py:226`, `:530`, and
  `:532` build remote shell commands by unquoted f-string interpolation and run them through
  `connection.execute`.

  **Self-correction, made before drafting.** My first pass called this caller-controlled and therefore
  injectable. Reading the assignment sites disproves that: the interpolated `remote_base` is built from
  `uuid.uuid4()` at `src/remarkable_spec/device/sync.py:175-176` and `:489-490`, and `page_uuid_str`
  comes from the `uuid.uuid4()` list comprehension at `:515`. Both are internally generated, so nothing
  attacker-controlled reaches the shell today. I wrote it up as an unquoted-interpolation pattern whose
  safety rests on an unenforced invariant, not as an exploitable injection. Filed the wrong version
  nowhere.

### Step 5 — a fourth silent-handler flavor my first grep missed

`grep -rn 'contextlib.suppress' src/` finds **3** `contextlib.suppress(Exception)` sites that the
`except`-clause regex cannot see, because they are context managers rather than clauses:
`src/remarkable_spec/device/sync.py:422`, `src/remarkable_spec/device/connection.py:125` and `:129`.
Functionally these are `except Exception: pass`. I added a `suppress` term at weight 1 (not the
weight-2 used for silent broad `except`) because two of the three are in
`DeviceConnection.disconnect` (`src/remarkable_spec/device/connection.py:119-131`), where suppressing
a close() failure during teardown is defensible. The third, at `src/remarkable_spec/device/sync.py:422`,
is not teardown — it discards a sync-log write failure inside an error path, so the audit trail of a
failed pull can itself vanish. Adding the term moved `device/connection.py` from rank 8 to rank 4;
the change is recorded here so the reordering is auditable rather than unexplained.

Final composition, reproducible from `/tmp/doc-analysis-risk-hotspots/score.py`:

```text
risk = 2*silent_broad_except + 1*loud_broad_except + 1*suppress_exception
     + 1.5*trust_boundary + 1*extras_group + 1*external_binary
     + LOC/100 + dependents/4
```

Ties break on LOC descending (this decides rank 9 vs 10: `cli/ls_cmd.py` 309 LOC over
`cli/tree_cmd.py` 234, both scoring 6.34).

## Validation

Two scripts, both under `/tmp/doc-analysis-risk-hotspots/`, both re-runnable:

- `score.py` — recomputes every per-file signal from source and emits the ranking table rows verbatim.
  The doc's `Open findings` cells were pasted from its output, so the two cannot drift by hand.
- `validate.py` — 24 mechanical checks over the output file: structure, closed vocabulary, citation
  resolution, gitignore/tracked status, prose bans. Exits non-zero on the first failure.

### Signal totals cross-checked against the environment brief

```text
$ uv run --no-project /tmp/doc-analysis-risk-hotspots/score.py | tail -5
total broad except sites across src/: 27      # brief § Marker inventory says 27 — match
total silent-broad sites: 13
total LOC: 10321                              # brief § 2 says 10,321 — match
total contextlib.suppress(Exception) sites: 3  # not in the brief; found by this packet
```

### Ranking table regenerated by script (pasted into the doc unmodified)

```text
| File | Open findings | Top owner | Citation |
| --- | --- | --- | --- |
| `src/remarkable_spec/device/sync.py` | 3 warn, 6 error | — | `:276` (573 LOC) |
| `src/remarkable_spec/cli/device_cmd.py` | 5 warn, 2 error | — | `:436` (504 LOC) |
| `src/remarkable_spec/cli/_resolve.py` | 0 warn, 3 error | — | `:166` (290 LOC) |
| `src/remarkable_spec/device/connection.py` | 4 warn, 1 error | — | `:94` (231 LOC) |
| `src/remarkable_spec/cli/search_cmd.py` | 2 warn, 2 error | — | `:88` (250 LOC) |
| `src/remarkable_spec/device/web_api.py` | 1 warn, 2 error | — | `:106` (227 LOC) |
| `src/remarkable_spec/ocr/diagram.py` | 2 warn, 1 error | — | `:231` (332 LOC) |
| `src/remarkable_spec/cli/diagram_cmd.py` | 3 warn, 0 error | — | `:288` (376 LOC) |
| `src/remarkable_spec/cli/ls_cmd.py` | 1 warn, 1 error | — | `:167` (309 LOC) |
| `src/remarkable_spec/cli/tree_cmd.py` | 1 warn, 1 error | — | `:127` (234 LOC) |
| `src/remarkable_spec/cli/sync_cmd.py` | 0 warn, 1 error | — | `:102` (425 LOC) |
| `src/remarkable_spec/formats/rm_file.py` | 0 warn, 1 error | — | `:31` (212 LOC) |
```

Severity derivation, printed per file by the same script so each cell is auditable:
`error = silent_broad_except + non_teardown_suppress + trust_boundary`;
`warn = reporting_broad_except + teardown_suppress + extras_groups + external_binary`.

### Full validator run

```text
$ uv run --no-project /tmp/doc-analysis-risk-hotspots/validate.py ; echo "exit=$?"
ok    no YAML frontmatter
ok    H1 exact match: '# remarkable-spec · Risk hotspots'
ok    exactly one H1
ok    exactly one content H2, '## Per-file drill-down'
ok    5 H3s under the drill-down: ['`src/remarkable_spec/device/sync.py`', '`src/remarkable_spec/cli/device_cmd.py`', '`src/remarkable_spec/cli/_resolve.py`', '`src/remarkable_spec/device/connection.py`', '`src/remarkable_spec/cli/search_cmd.py`']
ok    all H3s sit after the '## Per-file drill-down' H2 (no free-floating content H2s)
ok    intro is exactly 2 paragraphs
ok    first paragraph states one commit / one author (churn unavailable)
ok    table columns fixed and in order: ['File', 'Open findings', 'Top owner', 'Citation']
ok    ranking table has 12 rows (>= 10 required, 12 preferred)
ok    no churn / trend / last-modified column present
ok    no trend arrows anywhere (Trend column dropped under fallback path 1)
ok    every Top owner cell is '—' (single-author repo, fallback path 4)
ok    no fractional owner shares
ok    all 12 Open findings cells match score.py exactly
ok    parsed 18 full and 79 shorthand citations across 21 distinct paths
ok    every citation resolves to an existing file and an in-range line
ok    git check-ignore: none of the 21 cited paths are ignored
ok    every cited path is tracked by git ls-files
ok    no emoji-class (So) characters
ok    no filler adverbs (simply / just / basically / obviously / clearly)
ok    no bare braces outside fenced blocks or inline code spans
ok    no claim that anything is tested, covered, or regression-protected
ok    no tests/ path cited other than the empty __init__.py (cited: ['tests/__init__.py'])

PASSED: 0 failure(s)
exit=0
```

### Shorthand-anchor audit (semantic, not just range)

Range-checking a shorthand is not enough — a `:125` that resolves to the wrong file can still land
in range. `dump_cites.py` prints, per section, which file every shorthand actually anchors to. Result:

```text
### src/remarkable_spec/device/sync.py
    src/remarkable_spec/device/sync.py       24 cites  31, 52, 92, 149, 233, 303, 456, 438, 276-277,
                                                       132-133, 146-147, 422, 448, 453, 515, 532, 226,
                                                       530, 532, 175-176, 489-490, 515, 48, 88-90
### src/remarkable_spec/cli/device_cmd.py
    src/remarkable_spec/cli/device_cmd.py    16 cites  47, 66, 162, 293, 443, 494-503, 436-437,
                                                       415-435, 409-410, 393, 214, 343, 121-129,
                                                       54, 111, 112
    src/remarkable_spec/cli/_util.py          1 cites  43-46
### src/remarkable_spec/cli/_resolve.py
    src/remarkable_spec/cli/_resolve.py      11 cites  27, 234, 57-58, 99, 166-167, 206-207, 259,
                                                       263, 179-180, 132-138, 277
### src/remarkable_spec/device/connection.py
    src/remarkable_spec/device/connection.py 15 cites  38, 140, 166, 183, 202, 219, 224, 25-35, 94,
                                                       125, 129, 119-131, 112-117, 140-164, 53-55
    src/remarkable_spec/cli/_util.py          1 cites  34-35
    src/remarkable_spec/cli/device_cmd.py     1 cites  96-98
    src/remarkable_spec/device/sync.py        1 cites  226
### src/remarkable_spec/cli/search_cmd.py
    src/remarkable_spec/cli/search_cmd.py    11 cites  70-73, 76, 129, 88, 91, 152-153, 167, 196,
                                                       207, 203-210, 79
    src/remarkable_spec/sync/migrations.py    1 cites  128-145
```

Every shorthand anchors to its intended file; no cross-attribution.

### Fixes applied during validation

1. **Out-of-range citation caught and fixed.** The draft cited `tests/__init__.py:1`. The file is
   0 bytes, so line 1 does not exist and the range check failed it. Rewritten to name the path with no
   line number and describe it as a 0-byte file.
2. **Four shorthand-anchor ambiguities caught and fixed.** The first draft placed foreign-file full
   paths mid-section, after which subject-file shorthands re-anchored to the wrong file. Worst case:
   in the `search_cmd.py` section, `:203-210` followed `src/remarkable_spec/sync/migrations.py:128-145`
   and would have resolved into a 151-line file — an out-of-range failure that also happened to be
   semantically wrong. Fixed by re-anchoring with a full path after every context switch, in the
   `device_cmd.py`, `_resolve.py`, `connection.py`, and `search_cmd.py` sections.
3. **A near-miss on the `.ruff_cache` claim.** `git check-ignore .ruff_cache` (no trailing slash)
   returned nothing, which read as "not ignored" and would have made the intro's statement false.
   `git check-ignore -v .ruff_cache/` exits 0 and reports `.ruff_cache/.gitignore:2:*` — ignored via a
   nested self-written file, not the repo-root list. Claim verified, doc unchanged; the trailing-slash
   trap is filed as an out-of-scope finding because it is a live false-negative for the Phase 6
   validator.
4. **One imprecision corrected.** The intro said extras are counted "from the five groups declared at
   `pyproject.toml:23-46`", but that range holds six entries. Rewritten to name the five real groups and
   note that `all` at `:40-42` is their union.

### What was deliberately not run

Per directive 4, no billable or device command was invoked: no `rmspec ocr`, `diagram`, `annotations`,
`sync pull|push`, or `device *`. Every finding traces to reading source, `git`, `grep`, or CodeGraph.
`mise lint` / `typecheck` were not run either — they are free, but ruff and pyright are configured for
py312 against a py313.11 interpreter and their output would have added a toolchain-drift signal this
packet is not scoped to report.

## Summary

Shipped `docs/analysis/risk-hotspots.md`: a 12-row static risk ranking over the 56 tracked source files
plus a five-file drill-down, built from scratch because no prior artifact existed — `docs/analysis/`
did not exist before this run, so nothing was inherited and nothing needed re-verifying against an older
version. Both activity-derived signals the packet's default formula wants were unavailable and routed to
fallbacks: fallback path 1 for git history (one commit, one author, so the `Trend` column is absent
rather than fabricated) and fallback path 4 for ownership (every `Top owner` cell is `—`). Fallback
path 3 was unusable too — zero `TODO`/`FIXME`/`HACK` markers exist — so ranking rests on eight static
signals instead: file length, broad-`except` count, the silent-swallow subset, `contextlib.suppress`
sites, optional-extras fan-in, external-binary `subprocess` calls, trust boundaries, and CodeGraph
dependent counts, with the total absence of tests applied as a stated multiplier rather than a row.
Two corrections mattered more than the ranking itself: a first-pass claim that
`src/remarkable_spec/device/sync.py:226` was shell-injectable was disproved by reading the `uuid.uuid4()`
assignment sites at `:175-176` and `:489-490` and rewritten as an unenforced-invariant finding, and
CodeGraph's report that `sync/db.py` depends on `device/connection.py` was confirmed as a name-collision
artifact and dropped rather than drawn as an edge. The top five, in order:
`src/remarkable_spec/device/sync.py` (573 LOC, 6 error findings, three silent handlers plus unquoted
remote-shell interpolation), `src/remarkable_spec/cli/device_cmd.py` (504, a swallowed
successful-pull ledger write), `src/remarkable_spec/cli/_resolve.py` (290, three silent handlers that
each degrade a different resolution stage across four consuming commands),
`src/remarkable_spec/device/connection.py` (231, `paramiko.AutoAddPolicy` on the Wi-Fi path), and
`src/remarkable_spec/cli/search_cmd.py` (250, an unencoded user query in a URL path and two adjacent
reads with opposite failure policies). All 24 mechanical checks pass with exit 0.
