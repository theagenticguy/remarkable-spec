---
role: doc-insights-business-logic
model: opus
output: "docs/insights/business-logic.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · insights/business-logic.md

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

Produce `docs/insights/business-logic.md`: an index of the domain rules baked into the codebase — validations, invariants, calculations, and policy/gate logic. Captures behavior that isn't obvious from interface shapes alone.

The reader's question this file answers: *"What rules does this codebase enforce, and where?"*

## 2. Scope

- Create: `docs/insights/business-logic.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: validator functions, schema definitions, guard clauses, calculation/derivation functions, policy-check sites, authorization middlewares, feature-flag guards, rate-limit configuration.
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` for breadth-first scans.

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

### Where the domain rules actually live

This is a file-format and device-protocol library, so "business logic" means format invariants,
coordinate math, physical-device constants, and cache-correctness rules. The richest seams, all to be
verified at their current line numbers:

- **Coordinate-system rule.** `CLAUDE.md` states the v6 `.rm` X origin is at the page centre, not
  the top-left, and that the SVG renderer applies `x_shift = vw / 2` to compensate. Find that in
  `src/remarkable_spec/render/engine.py` and cite the line — this is the single most
  consequential invariant in the codebase, and a reader who misses it renders every page offset by
  half a width.
- **Pen physics.** `src/remarkable_spec/render/pens.py` (480 LOC, 47 indexed symbols) encodes ten
  pen formulas over pressure, tilt, speed, and direction, plus a thickness multiplier defaulting to
  1.5 (`src/remarkable_spec/cli/_util.py:44-48`) that exists to reconcile on-screen weight with
  export weight. Each formula is a rule; the multiplier is a policy.
- **Screen auto-detection.** `detect_screen` in `src/remarkable_spec/models/screen.py` infers
  reMarkable 2 versus Paper Pro from stroke coordinate extents rather than from configuration
  (`README.md:104-106`). The thresholds are the rule and the fallback is the policy.
- **Colour and pen mapping.** `PenColor` 0-13 and `RM_PALETTE`
  (`src/remarkable_spec/models/color.py:17`ff) map device enum values to RGB; unknown values fall
  back silently (`src/remarkable_spec/formats/rm_file.py:162,169`). A silent fallback is a business
  rule with a failure mode.
- **Cache invalidation.** `rm_hash`, the SHA-256 of the `.rm` bytes
  (`src/remarkable_spec/sync/hasher.py`, `src/remarkable_spec/sync/models.py:52-56`), is the sole
  invalidation key for both OCR and diagram results. State the consequence: editing a page produces
  a new key rather than updating the old row.
- **Change detection.** A document is considered changed when the device's `lastModified` epoch-ms
  value exceeds the recorded one (`src/remarkable_spec/device/sync.py:291`), with a malformed value
  coerced to 0 (`:283-286`). Both the comparison and the coercion are rules.
- **Document resolution and tie-break.** `src/remarkable_spec/cli/_resolve.py:27` and `:234` accept
  a name substring, a full UUID, or a UUID prefix, and on duplicates pick by page count descending
  then `lastModified` descending. Cite the comparator.
- **Templates.** `BUILTIN_TEMPLATES` (`src/remarkable_spec/models/template.py:139`ff) is a
  curated list of the templates most often seen in `.pagedata`, which makes it a heuristic with an
  unlisted-template fallback.
- **Markdown-push pipeline policy.** `README.md:72-91` states three transformation steps
  (Mermaid code blocks to inline PNG via `mmdc`, image paths to base64 data URIs, then WeasyPrint to
  PDF with e-ink styling). Verify each against `src/remarkable_spec/device/push.py` and treat any
  divergence as a finding.

State each rule as a rule, cite where it is enforced, and where the enforcement is a silent default
rather than an error, say what a user sees when it fires.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. State the scope explicitly in the intro: what counts as "business logic" here (e.g., "application-layer rules; database constraints are out of scope" / "domain calculations and validations; UI-level form-validation is out of scope"). The reader needs this to interpret the file.
3. Enumerate **Validations**. Grep for: schema declarations (`z.object`, `joi.object`, `pydantic.BaseModel`, `marshmallow.Schema`), explicit `if (!valid) throw …` guards, assertion sites, regex pattern matches against incoming data. Capture the rule statement, domain it applies to, citation, and failure mode (reject with 400 / silent drop / coerce / etc.).
4. Enumerate **Invariants**. Look for `assert` statements, comments labeled `Invariant:`, post-call sanity checks, database-uniqueness checks, transactional consistency guards. For each, name the invariant, identify where it is enforced (application code, DB constraint, or both), cite.
5. Enumerate **Calculations**. Find functions whose body composes a derived value from inputs (totals, rates, scores, prices, durations). Capture formula in plain prose, inputs, output, citation.
6. Enumerate **Policy and gates**. Find authorization checks, role/permission guards, feature flags, rate limits, opt-in toggles. For each, write a one-sentence policy + citation.
7. Within each section, group by domain — the bounded-context name (User / Order / Billing / …). Reuse domain names from `architecture/module-map.md` if possible.
8. Draft the file with four content H2 sections in fixed order: `## Validations`, `## Invariants`, `## Calculations`, `## Policy and gates`. Empty content H2s carry a single `_none found_` line.
9. Write `docs/insights/business-logic.md` with H1 = `# remarkable-spec · Business logic`.

## 5. Output format rules

- H1 = `# remarkable-spec · Business logic`. No decorative titles.
- No YAML frontmatter on the output file.
- Intro states scope explicitly.
- Four content H2 sections in fixed order: `Validations`, `Invariants`, `Calculations`, `Policy and gates`. Always present.
- `Validations` table columns: `Rule | Domain | Citation | Failure mode`.
- `Invariants` table columns: `Invariant | Where enforced | Citation`.
- `Calculations` table columns: `Calculation | Inputs | Output | Citation`. When a formula is non-trivial, write the formula in plain prose under the table.
- `Policy and gates` is a bullet list. Each bullet: ``- **<policy-name>:** one-sentence rule + where enforced. `path:LOC`.``
- Empty sections show `_none found_`.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Grep** for validator-library calls, `assert(`, `Invariant:` comments, `@require_permission`, feature-flag check patterns, rate-limit decorators.
- **Read** validator/calculator/policy implementations.
- **Glob** to enumerate `validators/`, `policies/`, `schemas/`, `calc/`, `pricing/` directories if present.
- **Bash** for `jq` over `docs/.repomix/codebase.json` to bulk-list validator/policy sites.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Validation is purely schema-library-driven** (no explicit guards): treat schema declarations as the validations. Cite each field-level constraint rather than the whole schema as a single row.
- **Codebase is a thin CRUD app with very little domain logic:** the Calculations and Policy sections may be very small or empty. Emit the content H2s with `_none found_` rather than skip.
- **A calculation is too complex to describe in one row** (multi-step pipeline): name the pipeline in the table, then write a paragraph beneath the table walking through the steps with citations.
- **Invariants are mostly DB-side** (UNIQUE constraints, foreign keys): capture them in the Invariants table with `Where enforced: DB constraint` and cite the migration / schema file. Note in the intro that DB-only invariants are surfaced here despite the "application-layer" scope, because they shape application behavior.

## 8. Success criteria

- [x] `docs/insights/business-logic.md` exists on disk. 262 lines.
- [x] H1 line reads `# remarkable-spec · Business logic`. Checked by script; exactly one H1.
- [x] Intro states scope explicitly. Four paragraphs: what counts, what does not, why DB constraints
      are surfaced anyway, and the silent-defaults theme.
- [x] All four content H2 sections exist in the fixed order, even if empty. Script output:
      `['Validations', 'Invariants', 'Calculations', 'Policy and gates']`.
- [x] Every populated table row has a backtick `path:LOC` citation. Enforced by the strict
      row-scoped shorthand rule in the validator; six rows were rewritten from shorthand to a full
      path to satisfy it.
- [x] Empty content H2s carry a single `_none found_` line. **Not applicable — no section is empty**,
      so the marker count is 0 by design.
- [x] No YAML frontmatter on the output. Line 1 is the H1.
- [x] Prior-artifact check ran: the output path held no file. `docs/insights/` did not exist,
      `git log -1 -- docs/insights/business-logic.md` was empty. Nothing was inherited; every claim
      was built from a source read in this session.
- [x] The Work log names what the prior artifact got wrong — recorded as **no prior version existed**
      (Work log step 1), with the evidence for that conclusion.
- [x] No citation resolves into a generated or gitignored path. `git check-ignore --stdin` over all
      45 distinct cited paths returned nothing.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent rules, invariants, calculations, or policies. Every entry traces to a source read.
- Do not paraphrase formulas beyond a faithful prose summary; cite the source line for the formula.
- Do not skip a content H2 just because it's empty — emit `_none found_`.
- Do not include UI-level form validation in the application-layer scope unless it duplicates server-side logic; cite the server-side site, not the UI.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

### Sibling docs written by other packets in this fan-out

- **`docs/insights/contract-map.md:84`ff — citations use package-relative paths, not repo-relative
  ones.** 174 citations across the tree name files as `ocr/pipeline.py:59`, `models/page.py:126-128`,
  `cli/render_cmd.py:274-275` and so on, omitting the `src/remarkable_spec/` prefix; 168 of those are in
  `contract-map.md` and 6 in `docs/insights/impact-analysis.md:360-361`. A reader cannot open any of
  them from the repo root, and Phase 6's file-existence check will fail on every one.
- **`docs/insights/impact-analysis.md:307` — four shorthand citations point past the end of the file
  they resolve to.** `:359-367`, `:374-382`, and `:395-401` follow a full citation of
  `src/remarkable_spec/export/svg.py`, which has 68 lines; the intended target is almost certainly
  `cli/render_cmd.py`. A reader following the shorthand lands nowhere. Same class at `:322`
  (`:139-179` against a 142-line `models/stroke.py`).
- **`docs/analysis/risk-hotspots.md:32-43` — twelve table rows carry a bare `:LOC` shorthand with no
  full path in the row.** Under the write protocol's same-table-row scope these are orphans; under a
  section-wide scope they resolve to `pyproject.toml`, which is the wrong file and puts every one of
  them out of range. A reader of that table cannot tell which file any row refers to.
- **`docs/diagrams/behavioral/sequences.md:169` — shorthand `:456` resolves to
  `src/remarkable_spec/device/push.py`, which has 193 lines.** The number matches nothing in that file.
- **The orchestrator must fix one ambiguity before Phase 6 runs:** whether shorthand scope is the H2
  section or the table row. My run of the strict (row-scoped) reading flags 34 orphans in sibling docs
  that the lenient (section-scoped) reading accepts. `docs/insights/business-logic.md` passes both.

### The shared environment brief

- **`docs/.packets/_environment.md:154` cites the CLI registration block as
  `src/remarkable_spec/cli/__init__.py:52-62`; the actual block is `:48-58`.** A reader following the
  brief's range sees only 7 of the 11 `app.command(...)` calls and overruns into `_get_version`,
  so a census built from that citation undercounts the CLI surface.
- **`docs/.packets/_environment.md:247` cites `RmspecSettings.dpi = 226` as
  `src/remarkable_spec/cli/_util.py:53-56`; the field is at `:52-55`.** Off by one at both ends, so the
  range starts on `default=226` and spills into the `sync_db` field. My packet's own section 3a has
  the same class of drift for the thickness multiplier (`:44-48` for a field at `:47-51`); both are
  corrected in the Work log above and in the output.

### Source defects tripped over while verifying citations

- **`src/remarkable_spec/export/pdf.py:159-168` — the multi-page PDF merge is unimplemented.** After
  rendering one PDF per page and painting blank pages onto a cairo surface, the function overwrites
  the output with `page_pdfs[0]`, so a caller passing N pages gets a one-page PDF with no error. Today
  no caller is exposed because `src/remarkable_spec/cli/render_cmd.py:395-401` always passes a
  single-page list, but a library consumer following the docstring at `:30-31` will silently lose pages.
- **`src/remarkable_spec/cli/ocr_cmd.py:225-227` — the comment and the values disagree.** The comment
  reads "Use default Paper Pro screen dimensions for the rasterize" while the constants are `1404.0`
  and `1872.0`, which are reMarkable 2 *pixel* dimensions being passed into a parameter documented as
  PDF points (`src/remarkable_spec/render/pdf_bg.py:30-31`). A bare PDF page is therefore rasterised at
  a scale derived from the wrong device and the wrong unit.
- **`src/remarkable_spec/models/document.py:277` reads the cPages redirect as `redirect` while
  `src/remarkable_spec/cli/_resolve.py:189` reads the same field as `redir`.** Only one spelling can be
  right, so `PageRef.redirect` is likely always `None`; a reader of `rmspec inspect <doc>.content`
  sees an empty Redirect column (`src/remarkable_spec/cli/inspect_cmd.py:246`) and concludes no page
  has been redirected.
- **`src/remarkable_spec/cli/search_cmd.py:167` parses `.content` without the guard the adjacent
  `.metadata` read has at `:150-153`.** One malformed `.content` file aborts the whole local search
  with a traceback instead of skipping that document, which is what the guarded read above it does.
- **`src/remarkable_spec/device/web_api.py:209-227` — `WebAPI.search` has no caller.**
  `rmspec search --device` posts to `/search/{keyword}` directly
  (`src/remarkable_spec/cli/search_cmd.py:88-91`), so the method's root-only scan and its two-spelling
  name matching are dead surface a reader may mistake for the search implementation.

---

## Work log

### Step 1 — inputs read, prior-artifact check

Read in full: this packet, `docs/.packets/_environment.md` (442 lines, measured at commit `4bb899d`).
Confirmed the binding constraints I will honor: no tests exist (`tests/__init__.py` is 0 bytes), no
citation may land in a gitignored path, braces around a UUID placeholder must always sit inside
backticks or be rewritten as prose, no billable/device command may be run, and the single
`ThreadPoolExecutor(max_workers=2)` belongs to `ocr/postprocess.py` and not to `ocr/pipeline.py`.

**Prior-artifact check: no prior version exists.** `docs/insights/` does not exist as a directory
(`ls docs/insights/` → `No such file or directory`), `test -f docs/insights/business-logic.md` →
ABSENT, and `git log -1 --format=%cs -- docs/insights/business-logic.md` returns empty (never
committed). `docs/` holds only `.packets/` and `.repomix/`. So there is **nothing the prior artifact
got wrong** — this is a first run over a repo with no `docs/` tree, and every claim in the output is
built from a source read in this session rather than inherited. The staleness-partition step in
Process #1 (`git log -1 --format=%cs` per cited path) is therefore inapplicable and was not run.

`git ls-files` returns 67 paths, 56 of them `.py`, matching the brief. Scratch directory
`/tmp/doc-insights-business-logic/` created.

### Step 2 — read the format / render / cache seams

Files read in full: `src/remarkable_spec/render/engine.py` (380), `models/screen.py` (105),
`render/pens.py` (481), `models/color.py` (120), `render/palette.py` (96), `formats/rm_file.py` (213),
`models/pen.py` (227), `models/stroke.py` (143), `models/template.py` (151), `models/page.py` (158),
`sync/hasher.py` (64), `sync/models.py` (121), `sync/migrations.py` (152), `sync/db.py` (366).

Seams verified at current line numbers:

- **`x_shift = vw / 2`** is at `src/remarkable_spec/render/engine.py:134`, with the center-origin
  comment at `:132-133`. Applied to points at `:150` (padding scan) and `:303,305` (emitted
  `<line>` coordinates). Matches `CLAUDE.md`.
- **Pen physics**: ten concrete renderers in `render/pens.py`; the factory `get_pen_renderer` at
  `:437` with an explicit unknown-pen fallback to `FinelineRenderer` at `:478-480`. Base widths come
  from `Pen.from_stroke` (`models/pen.py:132-226`), a second layer of per-pen math.
- **`detect_screen`** at `models/screen.py:86`, threshold at `:102`, RM2 fallback at `:104`.
- **Silent enum fallbacks** confirmed at `formats/rm_file.py:161-163` (pen → `FINELINER_1`) and
  `:168-170` (colour → `BLACK`), both `logger.warning` at a logger the module itself defines at `:33`.
- **Palette gap found (new, not in the packet's seam list)**: `RM_PALETTE`
  (`models/color.py:89-103`) has 13 entries for a 14-value `PenColor` enum (`:17-41`) — `HIGHLIGHT`
  (ID 9) is absent, and `Palette.get_rgb` (`render/palette.py:41`) falls back to black at `:60`. So a
  highlighter stroke carrying colour ID 9 renders black at 0.3 opacity rather than in a highlight
  colour. This is a silent-default failure mode worth a row.
- **Cache key**: `hash_file` at `sync/hasher.py:15`, `rm_hash` described as "the cache invalidation
  key" at `sync/models.py:55-58`, and the DB uniqueness that makes it the key — `UNIQUE (rm_hash,
  engine)` at `sync/migrations.py:58` and `rm_hash TEXT NOT NULL UNIQUE` at `:66`. Change detection
  by hash inequality at `sync/db.py:332`.
- **DB-side invariants** are real here and belong in the Invariants table per Fallback path 4:
  `PRIMARY KEY` and `REFERENCES ... ON DELETE CASCADE` at `sync/migrations.py:37,42`, with
  `foreign_keys=ON` set per connection at `sync/db.py:55`.

### Step 3 — device, resolution, and push seams; one README divergence found

Files read in full: `device/sync.py` (574), `cli/_resolve.py` (291), `device/push.py` (194),
`cli/_util.py` (113), `README.md` (148), `cli/device_cmd.py` (505), `cli/sync_cmd.py` (426).

Verified:

- **Change detection** at `src/remarkable_spec/device/sync.py:291` (`device_last_modified >
  tracked.device_last_modified`), coercion of a malformed `lastModified` to 0 at `:283-286` and again
  on the pull path at `:367-370`. The bare `except Exception: continue` that silently drops a
  document whose metadata cannot be fetched or parsed is at `:276-277`.
- **Resolution and tie-break** at `src/remarkable_spec/cli/_resolve.py:27` (three-tier match, regexes
  at `:20-24`) and `:234` (`resolve_document_full`); comparator at `:134`, sorting on
  `(len(page_uuids), last_modified)` with `reverse=True` — page count desc then `lastModified` desc,
  matching `CLAUDE.md`.
- **Thickness multiplier** default 1.5 at `src/remarkable_spec/cli/_util.py:47-51` (the packet's
  hint said `:44-48`; the field is at `:47-51` in the current file — corrected). Same 1.5 default
  appears as a parameter default at `src/remarkable_spec/render/engine.py:51,98,239`, applied to the
  segment width at `:300`.
- **DPI setting is 226**, not the 229 the Paper Pro panel uses (`src/remarkable_spec/cli/_util.py:52-55`
  vs `src/remarkable_spec/models/screen.py:83`). Recorded as a calculation input, not editorialised.

**Divergence found — `README.md:74-77` overstates the Markdown push pipeline.** The README lists
three automatic steps: mermaid code blocks to inline PNG via `mmdc`, image paths to base64 data URIs,
then WeasyPrint. Only the third exists. `render_to_pdf`
(`src/remarkable_spec/device/push.py:30-55`) dispatches on file extension through the `_RENDERERS`
table at `:189-193`; `.md` goes to `_render_markdown` at `:58`, which calls
`markdown.markdown(..., extensions=["tables", "fenced_code", "codehilite"])` at `:75` and hands the
HTML straight to `weasyprint` at `:112`. `mmdc` is reachable only through `_render_mermaid` at `:116`,
which is registered for `.mmd` at `:191` and is never called from the `.md` path. Grep confirms it:
`grep -rn "data:image" src/ --include='*.py'` returns exactly one hit, `render/engine.py:379`, in the
SVG raster-background code — nothing in `device/push.py`. `README.md:23` and `:130` repeat the same
claim. Both sides cited in the output; also logged under Out-of-scope findings.

### Step 4 — OCR, export, and CLI sweep; the OCR cache is dead

Files read in full: `ocr/postprocess.py` (236), `ocr/pipeline.py` (128), `ocr/diagram.py` (333),
`ocr/vision.py` (191), `ocr/textract.py` (72), `formats/metadata.py` (74), `formats/content.py` (78),
`formats/pagedata.py` (49), `formats/document_loader.py` (118), `models/document.py` (389),
`render/pdf_bg.py` (62), `export/pdf.py` (169), `export/svg.py` (69), `export/png.py` (113),
`device/paths.py` (47), `device/connection.py` (232), `device/web_api.py` (228),
`cli/render_cmd.py` (409), `cli/ocr_cmd.py` (241), `cli/diagram_cmd.py` (377),
`cli/annotations_cmd.py` (300), `cli/search_cmd.py` (251), `cli/inspect_cmd.py` (318),
`cli/ls_cmd.py` (310), `cli/tree_cmd.py` (235), `cli/env_cmd.py` (64), `cli/__init__.py` (73),
`CLAUDE.md` (57).

**The OCR half of the documented cache does not exist as a live path.** `CLAUDE.md:40` states
`rm_hash` "is the cache invalidation key for OCR and diagram results". The diagram half is real
(`src/remarkable_spec/cli/diagram_cmd.py:221-231` reads, `:246-254` writes). The OCR half is not:
`grep -rn "get_ocr\|put_ocr\|get_all_ocr" src/ --include='*.py'` returns only the three definitions in
`src/remarkable_spec/sync/db.py:174,192,216` and no call sites. `find_changed_pages`
(`:319`) and `migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:113`) likewise have
zero callers. `rmspec ocr` calls `transcribe_rm` unconditionally
(`src/remarkable_spec/cli/ocr_cmd.py:96,155`), so every invocation re-bills Textract and Bedrock.
The only OCR reuse that works is a `.ocr.txt` sidecar written by `--save`
(`src/remarkable_spec/cli/ocr_cmd.py:165-166`) and read by `rmspec search`
(`src/remarkable_spec/cli/search_cmd.py:196-198`).

I attempted `codegraph callers` to corroborate the zero-caller result; **the command was declined at
the permission prompt**, so the grep above is the evidence of record. The symbol names are unique to
`sync/db.py` in this repo, so a name-resolution collision cannot be masking a caller.

Counts verified by running the models in the project venv (offline, free), output saved to the scratch
directory: `PenType` has 18 members collapsing to 11 canonical values
(`src/remarkable_spec/models/pen.py:29-46,58-75`), `get_pen_renderer` maps those 11 onto **10**
concrete renderers because `ERASER` and `ERASER_AREA` share one
(`src/remarkable_spec/render/pens.py:476-477`) — so `CLAUDE.md:37`'s "10 pen types" is right about
renderers and understates the enum. `PenColor` has 14 members, `RM_PALETTE` 13,
`PAPER_PRO_PHYSICAL` 9. `BUILTIN_TEMPLATES` has 9 entries.

### Step 5 — draft and write the output

Section plan settled. Scope sentence: format invariants, coordinate/DPI math, device constants,
pen-physics formulas, and cache-correctness rules count as business logic; Rich console formatting,
cyclopts argument parsing, and prompt wording do not, except where a prompt encodes a domain
classification vocabulary. DB constraints surfaced in Invariants per Fallback path 4, because the
SQLite schema is where the uniqueness that makes `rm_hash` a cache key actually lives.

## Validation

Two scripts, both under `/tmp/doc-insights-business-logic/`. Everything mechanically checkable was
checked by script; only the prose judgment calls were spot-checked by eye.

### Citation resolution — `check_citations.py`

Checks four properties and exits non-zero on any: (1) every full `path:LOC` names a file that exists,
(2) every line in every LOC spec is in range for that file, (3) every shorthand `:LOC` resolves to a
full path cited earlier **in the same table row** when it sits in a table row, or earlier in the same
H2 section otherwise, (4) no cited path is gitignored (`git check-ignore --stdin` over the distinct
path set).

```
$ uv run --no-project /tmp/doc-insights-business-logic/check_citations.py docs/insights/business-logic.md
documents checked      : 1
full citations         : 191
shorthand citations    : 66
distinct cited paths   : 45
gitignored cited paths : 0

ALL CITATIONS RESOLVE
```

```
$ git check-ignore docs/insights/business-logic.md
exit=1 (1 = not ignored, good)
```

**First run failed with 8 findings in this file, all fixed:**

- 6 orphan shorthands in table rows — `:167-170`, `:149-153`, `:266`, `:39-49`, `:120-121`, `:55-58`,
  plus `:66-76` in the Calculations table — each rewritten to the full path
  (`src/remarkable_spec/formats/rm_file.py`, `src/remarkable_spec/models/document.py`,
  `src/remarkable_spec/cli/_resolve.py`, `src/remarkable_spec/models/screen.py`).
- 1 out-of-range citation: I wrote `src/remarkable_spec/export/pdf.py:159-169`; the file has 168 lines.
  Corrected to `:159-168`.
- 1 out-of-range citation I caught while drafting rather than after: `tests/__init__.py:1` is
  unresolvable because the file is 0 bytes and has no line 1. The no-tests claim now cites
  `pyproject.toml:74-76` and names the empty file in prose without a line number.

### Structure and style — `check_structure.py`

```
$ uv run --no-project /tmp/doc-insights-business-logic/check_structure.py
frontmatter            : none (line 1 is not '---')
H1                     : '# remarkable-spec · Business logic' OK
content H2s            : ['Validations', 'Invariants', 'Calculations', 'Policy and gates']
table in 'Validations'         : ['Rule', 'Domain', 'Citation', 'Failure mode']
table in 'Invariants'          : ['Invariant', 'Where enforced', 'Citation']
table in 'Calculations'        : ['Calculation', 'Inputs', 'Output', 'Citation']
bare braces            : none outside fences/code spans
emoji                  : none
filler adverbs         : none of simply/just/basically/obviously/merely/actually
'_none found_' markers : 0

STRUCTURE OK
```

The brace check is the one that matters for orchestrator directive #3: it strips inline code spans and
fenced blocks, then looks for a surviving `{` or `}`. The one place a brace appears in the output —
the push-extension set — is wrapped as inline code, so it passes. The filler-adverb check failed once
on "actually" in the intro scope paragraph; rewritten.

### Domain-fact verification, not string verification

Counts asserted in the output were produced by importing the models in the project venv rather than by
reading and counting by hand (offline, free, no AWS or device access):

```
PenType members: 18
canonical distinct: 11 ['BALLPOINT_1', 'CALLIGRAPHY', 'ERASER', 'ERASER_AREA', 'FINELINER_1',
 'HIGHLIGHTER_1', 'MARKER_1', 'MECHANICAL_PENCIL_1', 'PAINTBRUSH_1', 'PENCIL_1', 'SHADER']
PenColor members: 14
RM_PALETTE entries: 13
missing from RM_PALETTE: ['HIGHLIGHT']
PAPER_PRO_PHYSICAL entries: 9
missing from PHYSICAL: ['PINK', 'GRAY_OVERLAP', 'HIGHLIGHT', 'GREEN_2', 'YELLOW_2']
BUILTIN_TEMPLATES: 9
RM2 ppp/pw/ph: 0.3185840707964602 447.2920353982301 596.3893805309734
PP  ppp/pw/ph: 0.314410480349345 509.34497816593887 679.1266375545852
x_shift PP: 254.67248908296943 x_shift RM2: 223.64601769911505
```

Those numbers are what let the output state the `x_shift` and page-size figures concretely instead of
restating the formula, and they are what surfaced the two palette gaps.

Zero-caller claims were established with
`grep -rn "get_ocr\|put_ocr\|get_all_ocr\|find_changed_pages\|migrate_ocr_sidecars" src/ --include='*.py'`,
which returns only the definition lines. A `codegraph callers` cross-check was attempted and
**declined at the permission prompt**, so grep is the evidence of record; the five names are unique to
`src/remarkable_spec/sync/db.py` and `src/remarkable_spec/sync/migrations.py`, so the index's
name-resolution collision hazard could not be hiding a caller.

### Spot-checks reserved for judgment

Read every source file the output cites, in full, in this session — 45 distinct paths. The judgment
calls I re-read rather than scripted: whether `x_shift` is applied on every emitted coordinate (it is —
`src/remarkable_spec/render/engine.py:150,303,305`), whether the eraser is subtractive (it is not),
whether the `_2` pen variants really are aliases (they are, and only `canonical()` collapses them),
and whether the README's three-step Markdown pipeline exists (one step of three does).

**No billable or device command was run.** No `rmspec ocr`, `diagram`, `annotations`, `sync pull`,
`sync push`, or `device *`. The only executions were `git`, `grep`, `wc`, and three offline Python
scripts against the local venv.

## Summary

Shipped `docs/insights/business-logic.md`, 262 lines, 191 full citations and 66 shorthands over 45
distinct source paths, all resolving to a real file and an in-range line. The scope decision that
shaped everything: in a binary-format and device-protocol library there is no "business logic" in the
CRUD sense, so I defined it as format invariants, coordinate and DPI math, physical device constants,
the ten pen-physics formulas, resolution and change-detection policy, and cache-correctness rules —
and surfaced SQLite constraints in Invariants because the schema is the only place the uniqueness that
makes `rm_hash` a cache key is declared. The intro leads with the theme that makes the file worth
reading: nearly every rule here is a silent default rather than a rejection, so the Failure mode column
carries what a user sees when each one fires. **Policy and gates was the surprise in volume** — 22
bullets against an expectation of near-zero for a library with no authorization model, because in this
codebase "policy" turned out to mean opt-out defaults, hardcoded model IDs and inference parameters,
import-time environment mutation, lazy optional-dependency gates, and a `paramiko.AutoAddPolicy()` that
trusts any host. Three findings change what a reader would otherwise conclude: the OCR half of the
documented `rm_hash` cache has no caller anywhere in `src/`, so `rmspec ocr` re-bills Textract and
Bedrock on every run; the README's three-step Markdown push pipeline is one step in source; and
`RM_PALETTE` covers 13 of 14 `PenColor` values, so a highlighter stroke carrying colour ID 9 renders
black. Each is stated with both the document line and the source line, per the anti-goal against
restating a document without finding what enforces it.
