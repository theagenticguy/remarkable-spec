---
role: doc-reference-public-api
model: opus
output: "docs/reference/public-api.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · reference/public-api.md

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

Produce `docs/reference/public-api.md`: one H3 per exported symbol for the top 30 public exports of `remarkable-spec`, each with a fenced code block quoting the signature **verbatim** from source, a one-sentence description, and a `` `path:LOC` `` citation. When `remarkable-spec` does not expose a CLI, append an `## HTTP` H2 listing routes.

## 2. Scope

- Create: `docs/reference/public-api.md`
- Do not touch: `docs/reference/cli.md`, any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially barrel/entry files (`packages/*/src/index.*`, `src/index.*`, `mod.rs`, `__init__.py`, `lib.rs`, or whatever convention applies).
- HTTP route declarations if the codebase is a server (route decorators, `app.get(...)` / `router.post(...)` / `@route` patterns).
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` for fast `jq`-based barrel scanning.

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

### Surface definition for this packet

Two surfaces exist and only one of them is the library API. Keep them separate.

- **The library API is `models`-only by construction.** The root
  `src/remarkable_spec/__init__.py:33-67` declares 24 names in `__all__`, and every one of them comes
  from a `remarkable_spec.models.*` import (`src/remarkable_spec/__init__.py:7-31`). Nothing from
  `formats`, `render`, `ocr`, `device`, `sync`, or `export` is re-exported at the root.
- **Each subpackage publishes its own `__all__`** — `formats/__init__.py:22-35`,
  and the equivalents in `models`, `render`, `device`, `export`, `sync`, `ocr`, `cli`. These are the
  second tier of the public surface: importable as `remarkable_spec.formats.parse_rm_file` but not
  as `remarkable_spec.parse_rm_file`. Say which tier each symbol is in, because an importer who
  guesses wrong gets an `ImportError`.
- **`src/remarkable_spec/py.typed` is present and tracked**, so the distribution ships inline types
  and every published signature is part of the contract a type checker enforces on consumers.
- Signatures must be **verbatim** from source, including `from __future__ import annotations`
  consequences: the annotations are strings at runtime. Quote them as written — `Path | None`, not
  `Optional[Path]`.
- The CLI is documented by a sibling file, `docs/reference/cli.md`. Do not duplicate command or flag
  documentation here; a one-line pointer is enough.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Identify the public surface. Locate barrel files and treat their `export` declarations as the canonical public surface. For languages without explicit barrels (Go, Python single-package), use symbols whose name does not start with `_` and whose file has ≥ 1 inbound import from outside the module.
3. Filter to the top 30 by significance. Rank by inbound-reference count if available; fall back to "declared in the barrel root" + "non-underscore name" + "has a docstring or comment header." Tie-break alphabetically.
4. For each shortlisted export, locate the source declaration site and read the surrounding context (typically `start_line` through `start_line + 20`). Extract the signature **verbatim** — function declaration, class declaration, or type declaration as it appears in source.
5. Write a one-sentence description for each export. The first sentence of the symbol's docstring is the preferred source; otherwise derive from the function name + signature.
6. Determine HTTP rendering. If the codebase exposes a CLI (per `architecture/system-overview.md`'s entry-point analysis or per direct manifest inspection), skip the HTTP section — `reference/cli.md` handles route-like surfaces. Otherwise, enumerate HTTP routes and prepare an `## HTTP` H2.
7. Draft one H3 per export. Format: `### <symbol-name>`, then a fenced code block (language tag matching the source file extension) quoting the verbatim signature, then a one-sentence description, then a backtick `path:LOC` citation on its own line.
8. If rendering HTTP, append `## HTTP` as the final content H2. One H3 per route: `### <METHOD> <path>`, a one-sentence description, a backtick `path:LOC` citation. Sort by `path` ascending, then by `method`.
9. Write `docs/reference/public-api.md` with H1 = `# remarkable-spec · Public API`.

## 5. Output format rules

- H1 = `# remarkable-spec · Public API`. No decorative titles.
- No YAML frontmatter on the output file.
- Each exported symbol is an H3 (`### <symbol-name>`), followed by:
  1. A fenced code block (language tag matches source extension: `ts`, `py`, `rs`, `go`, `rb`, `java`, …) quoting the signature **verbatim**. Never paraphrase, never retype from memory.
  2. A one-sentence description.
  3. A backtick `path:LOC` citation on its own line.
- HTTP section (non-CLI repos only): one H2 `## HTTP`, one H3 per route `### <METHOD> <path>`, one-sentence description, backtick `path:LOC` citation.
- No Mermaid. No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** declaration sites to extract verbatim signatures and docstrings.
- **Grep** for `export` / `pub fn` / `def` / `func` declarations at barrel locations.
- **Glob** to enumerate barrel files across the repo.
- **Bash** for `jq` over `docs/.repomix/codebase.json` if present (e.g., `jq '.files | keys[] | select(endswith("/index.ts"))'`).
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No clear barrel pattern** (language without explicit re-exports): use `start_line == 1` AND `≥ 3 inbound import edges` as the public-surface heuristic. Note the heuristic in the Work log.
- **Fewer than 30 qualifying exports:** emit whatever is present. Do not pad with internal helpers.
- **A symbol has no docstring or comment:** derive a one-sentence description from the function name and signature. State the inference in the Work log if it was unusually inferential.
- **CLI determination is ambiguous:** if the repo has *both* a library API and a CLI binary, render the public API here AND render the CLI in `reference/cli.md` — they cover different surfaces. Skip the `## HTTP` section here only if the codebase is a CLI-only tool (no library exports).

## 8. Success criteria

- [x] `docs/reference/public-api.md` exists on disk. — 480 lines.
- [x] H1 line reads `# remarkable-spec · Public API`. — asserted by the validator.
- [x] At least 5 H3 symbol entries are present (count `^###` matches, excluding HTTP routes). — 30, exactly at the cap; no HTTP routes exist to exclude.
- [x] Every H3 symbol block has one fenced code block immediately after the heading. — validator checks 2 fence markers per block, fence is the first non-blank element, tag is `py`.
- [x] Every H3 block has exactly one backtick `path:LOC` citation. — one violation found and fixed (`RM2_SCREEN` carried a second `README.md:12`; caveat moved to the intro).
- [x] A script confirms every fenced signature's declaration line appears literally in the file named by that H3's citation; the command and its output are pasted into Validation. — `/tmp/doc-reference-public-api/validate.py`; strengthened beyond the criterion to check *every* non-blank fence line, plus that the cited line is itself one of the fence lines.
- [x] Three signatures are read end-to-end at their cited spans to confirm the declaration is complete and unaltered. — `export_svg` (8 params, defaults, `-> None`, no elided decorator), `PenColor` (14 members, 0–13 contiguous, list ends at `:41`), `Point` (frozen config, no decorator above the class). Spot-check 3 found a genuine omission: 23 `@computed_field` properties and 9 `@classmethod` helpers widen the contract; both families are now documented in the intro.
- [x] If the repo is a CLI-only tool: no `## HTTP` section exists. Otherwise: if HTTP routes were found, an `## HTTP` section exists with one H3 per route. — Not CLI-only (both surfaces exist), so Fallback path 4 applied; a route/server grep over `src/` returns nothing, the only HTTP is the `WebAPI` client, so no routes were found and no section is rendered. Validator asserts no `## HTTP` H2.
- [x] No YAML frontmatter on the output. — asserted by the validator.
- [x] No Mermaid fences in the output. — asserted by the validator.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers. — the path held no file; `docs/reference/` did not exist and the output was never committed.
- [x] The Work log names what the prior artifact got wrong — or records that no prior version existed. — Step 1 records that no prior version existed and that the Process § 1 git-partition step is therefore inapplicable.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path). — 0 of 22 cited paths ignored; additionally all 22 confirmed present in `git ls-files`.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent symbol names, signatures, route paths, or citations. Every identifier traces back to a source read.
- Do not paraphrase signatures. Quote them verbatim from source.
- Do not write to `docs/reference/cli.md` — the CLI packet owns that file.
- Do not write YAML frontmatter on the output file.
- Do not exceed 30 symbol H3 entries — overflow belongs to a paginated follow-up packet, not this one.
- Do not emit emojis. Do not use filler adverbs.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `docs/.packets/_environment.md:165` — still states "Library surface: **24 exported names** in the
  root `__all__`" after the mid-run correction fixed only the other instance at `:149`, so the brief
  now contradicts itself within 16 lines. An agent that reads the § 2 public-surface bullet rather
  than the § 2 entry-points bullet will document 24 of the 26 root exports and silently omit two
  names that importers can actually reach.
- `docs/.packets/doc-reference-public-api.md:103` — this packet's own § 3a states the root `__all__`
  "declares 24 names"; it declares 26. Left unedited because § 3a is orchestrator-owned input, not my
  Scope, but any packet quoting § 3a rather than counting the source inherits the error.
- `docs/.packets/doc-reference-public-api.md:104` — § 3a cites the root barrel's model imports as
  `src/remarkable_spec/__init__.py:7-31`; the import block starts at line **6**
  (`from remarkable_spec.models.color import (`), with line 7 being the first imported name inside
  those parentheses. A reader following the citation lands one line inside the first import statement
  and could conclude the block is shorter than it is.
- `docs/.packets/_environment.md:82` and `:156` — both cite the CLI command-registration block as
  `src/remarkable_spec/cli/__init__.py:52-62`; the eleven `app.command(...)` calls are actually at
  `:48-58`, a uniform 4-line offset. Lines 52-62 do contain 7 of the 11 registrations plus 4 unrelated
  lines, so a reader spot-checking the citation sees plausible-looking code and may not notice four
  commands are outside the cited range — the failure mode the packet's Process § 1 warns about.
- `src/remarkable_spec/export/svg.py:38-52` — the `Args:` docstring block documents 7 of the
  function's 8 parameters; `background_page_size`, declared at `:26` and passed straight through at
  `:67`, has no entry. A caller reading the docstring will not learn that the parameter exists or that
  it pairs with `background_image_b64` to size an embedded raster background.
- `src/remarkable_spec/models/color.py:89` — `RM_PALETTE` maps 13 of the 14 `PenColor` members,
  omitting `HIGHLIGHT` (ID 9), and nothing at the definition site says the omission is deliberate.
  Two different wrong outcomes follow, and I verified both rather than assuming one: through the
  library's own renderer, `Palette.get_rgb` does a guarded `self.colors.get(color)` and returns black
  `(0, 0, 0)` for a miss (`src/remarkable_spec/render/palette.py:57-60`, documented at `:31` and
  `:49`), so every highlighter stroke silently renders black instead of yellow; whereas an external
  consumer subscripting the exported dict directly as `RM_PALETTE[stroke.color]` gets a `KeyError`.
  `RM_PALETTE` is a tier-1 root export, so the second path is a supported one.
- `src/remarkable_spec/render/palette.py:49` — the documented black fallback in `get_rgb` is
  indistinguishable from a genuine `PenColor.BLACK` stroke, and the method emits no warning on a miss.
  A reader debugging a page whose highlighter marks came out black has no signal pointing at the
  palette gap above.

---

## Work log

### Step 1 — inputs read, prior-artifact check

Read in full: `docs/.packets/_environment.md` (442 lines, brief-write commit `4bb899d`) and this
packet. Both stale-prior-trap subsections absorbed; the four orchestrator directives in § 3a are in
force.

**Prior artifact: none existed.** `ls docs/reference/` returned `No such file or directory (os error
2)`, and `git log -1 --format=%cs -- docs/reference/public-api.md` returned empty (never committed).
`docs/` on disk holds only `.packets/` and `.repomix/`. So there is **no stale prose to inherit and
nothing the prior artifact got wrong** — this is a first run over a repo with no `docs/` tree. The
partition step from Process #1 (`git log` per cited path vs. per doc) is inapplicable: every citation
in this run is read fresh from source at its current line number.

Confirmed the citable-path universe with `git ls-files`: **67 tracked files, 56 of them `.py`**,
matching the brief's § 2 count. No `docs/` file is tracked yet, so every citation I emit points into
`src/` or a root manifest.

### Step 2 — tier-1 surface (root `__all__`)

Read `src/remarkable_spec/__init__.py` end to end (67 lines). Confirms the brief and § 3a:
`__all__` at `:33-67` declares exactly **24 names**, and every import feeding it (`:6-31`) is from a
`remarkable_spec.models.*` module — `models.color` (`:6-12`), `models.document` (`:13-21`),
`models.page` (`:22`), `models.pen` (`:23`), `models.screen` (`:24`), `models.stroke` (`:25`),
`models.template` (`:26-31`). Nothing from `formats`, `render`, `ocr`, `device`, `sync`, or `export`
appears. The library API really is `models`-only at the root.

### Step 3 — corrected the tier-1 count: it is 26, not 24

Both the shared brief (`docs/.packets/_environment.md:150-151` and `:163`) and this packet's § 3a
(`docs/.packets/doc-reference-public-api.md:102`) state the root `__all__` declares **24** names. It
declares **26**. I enumerated the list by hand from `src/remarkable_spec/__init__.py:33-67` and got
26, then confirmed with an AST pass rather than trusting my count:

```text
src/remarkable_spec/__init__.py: 26 names, __all__ at line 33-67
src/remarkable_spec/models/__init__.py: 26 names, __all__ at line 47-81
src/remarkable_spec/formats/__init__.py: 8 names, __all__ at line 21-35
src/remarkable_spec/render/__init__.py: 13 names, __all__ at line 37-54
src/remarkable_spec/ocr/__init__.py: 2 names, __all__ at line 7-7
src/remarkable_spec/device/__init__.py: 4 names, __all__ at line 20-25
src/remarkable_spec/sync/__init__.py: 6 names, __all__ at line 22-29
src/remarkable_spec/export/__init__.py: 3 names, __all__ at line 20-24
src/remarkable_spec/cli/__init__.py: 3 names, __all__ at line 38-38
```

Script: `/tmp/doc-reference-public-api/` session, `ast.parse` over each barrel, matching the
`__all__` assignment target and counting `node.value.elts`. **I use 26**, because the source is the
authority over any brief. Logged as an out-of-scope finding against the brief and against § 3a.

The `models` barrel re-exports the identical 26 names (`src/remarkable_spec/models/__init__.py:47-81`
mirrors the root list exactly), so `remarkable_spec.X` and `remarkable_spec.models.X` are the same
surface for all 26. Total declared public surface across all nine barrels: 26 root + 8 `formats` +
13 `render` + 2 `ocr` + 4 `device` + 6 `sync` + 3 `export` + 3 `cli` = 65 name slots, 26 of which are
the duplicated `models` mirror.

26 tier-1 names leaves exactly **4 slots** under the 30 cap for tier-2 symbols.

**Coordinator correction received mid-task, and it agrees with what I had already derived.** The
orchestrator messaged to say the root `__all__` holds 26, not 24, verified by AST parse, and that the
brief had been corrected while this packet's § 3a could not be. No rework followed: I had already
caught the discrepancy independently in this step and had written 26 throughout. Confirmed by grep
that no stale `24` count reached the output — the four `24` substrings in
`docs/reference/public-api.md` are all line numbers inside citations (`export/__init__.py:20-24`,
`models/page.py:124-146`, `models/stroke.py:24`), not counts. The coordinator's per-module tier-2
figures (`models` 26, `render` 13, `formats` 8, `sync` 6, `device` 4, `cli` 3, `export` 3, `ocr` 2)
match my AST census in this section line for line.

**The brief's correction is only half-applied**, which matters for the packets still running.
`docs/.packets/_environment.md:149` now reads "re-exports **26** names" with an explicit warning not
to trust a 24, but `docs/.packets/_environment.md:165` still reads "Library surface: **24 exported
names** in the root `__all__`". The brief now contradicts itself, and an agent that reads only the
§ 2 public-surface bullet gets the wrong number. Recorded under Out-of-scope findings.

### Step 4 — ranked the surface by inbound-reference count

Ran `codegraph callers <symbol> -l 300 --json` over all 63 distinct public names (26 tier-1 + 37
tier-2, treating the `models` mirror as one set), via `/tmp/doc-reference-public-api/census.sh`, and
counted `.callers | length`, the non-`file`-kind subset (real call/reference sites as opposed to
import edges), and distinct `filePath`s. Full table at `/tmp/doc-reference-public-api/callers.tsv`.
Invoked by absolute path per the brief's § 1 — and note a zsh gotcha: the first attempt looped once
over the whole space-separated string because zsh does not word-split unquoted expansions, so the
census reran under `bash`.

Top of the ranking (total callers / non-file callers / distinct files):

```text
T2  get_xochitl_dir  20 / 10 / 10
T1  Page             14 /  5 / 11
T2  parse_rm_file    11 /  9 /  9
T2  parse_content     8 /  4 /  5
T2  parse_metadata    8 /  4 /  5
T1  PenColor          7 /  1 /  6
T1  PenType           7 /  1 /  6
T1  RM2_SCREEN        7 /  1 /  7
T1  ScreenSpec        7 /  0 /  7
T2  export_svg        6 /  5 /  6
T2  SyncDocument      6 /  4 /  4
T2  SyncLogEntry      6 /  4 /  4
```

**Shortlist rule applied.** All 26 tier-1 names are included — they are the complete, deliberate
`import remarkable_spec` surface, and a reference that covers it partially is worse than one that
covers it whole. The 4 remaining slots go to the highest-ranked tier-2 symbols, excluding the three
`cli` barrel names (`app`, `get_xochitl_dir`, `settings`): `get_xochitl_dir` tops the entire ranking
at 20 callers, but it is CLI plumbing and the sibling `docs/reference/cli.md` owns that surface, so
featuring it here would duplicate another packet's file. That yields `parse_rm_file` (11),
`parse_content` (8), `parse_metadata` (8), and then a three-way tie at 6 broken alphabetically
(case-insensitive) in favour of `export_svg` over `SyncDocument` and `SyncLogEntry`. Total: **30
H3 entries, exactly at the cap.**

**Two index results I cross-checked rather than trusted.** `load_document` came back with 1 caller,
which looked like an under-report for a function named "load the document". `grep -rn load_document
src/ --include='*.py'` returns exactly four lines — the barrel import
(`src/remarkable_spec/formats/__init__.py:16`), the barrel `__all__` entry (`:34`), the module's own
`__all__` entry (`src/remarkable_spec/formats/document_loader.py:32`), and the definition (`:36`).
The index was right: it has no internal consumer and exists for external importers. Not dead code
(it is a declared export), and the dead-code packet owns that judgment, so I record it here and drew
no conclusion in the output. Second: `parse_content` grep-counts 12 lines against the index's 8
callers, because grep also matches `parse_content_json` and the barrel/`__all__` lines; the index
count is the one I ranked on.

### Step 5 — declaration sites read verbatim

Read end to end, no ranges: `src/remarkable_spec/models/color.py` (119 lines),
`models/pen.py` (227), `models/stroke.py` (143), `models/page.py` (158),
`models/document.py` (389), `models/screen.py` (105), `models/template.py` (151),
`formats/rm_file.py` (212), `formats/content.py` (77), `formats/metadata.py` (73),
`export/svg.py` (68), `sync/models.py` (120). Every one of the 30 shortlisted signatures is
transcribed from these reads, not from memory.

Confirmed the brief's Python-idiom facts hold at the sites I quote: `from __future__ import
annotations` opens all twelve; `str | None` and `Path | None` and `dict[PenColor, RGB]` appear,
`Optional[...]` and `typing.Dict` appear nowhere; `datetime.now(UTC)` at
`src/remarkable_spec/sync/models.py:15`. So the fences reproduce modern-idiom annotations as written.

**Brace hazard confirmed live**, exactly where § 3a said: `src/remarkable_spec/models/document.py:8`
carries the UUID-in-braces filename spelling, and so do the docstrings of `DocumentMetadata` (`:53`),
`ContentInfo` (`:191`), `PageRef` (`:176`), `Page` (`src/remarkable_spec/models/page.py:105`),
`Template` (`src/remarkable_spec/models/template.py:84`), and both format parsers
(`src/remarkable_spec/formats/metadata.py:3`, `formats/content.py:3`). Every place I carry that
spelling into prose it is wrapped in backticks as inline code; nowhere does a bare brace appear
outside a fence or a code span.

### Step 6 — HTTP determination: no section

The repo ships a CLI (`rmspec = "remarkable_spec.cli:app"`, `pyproject.toml:20-21`) **and** a library
surface, so Fallback path 4 applies rather than Process step 6's CLI shortcut: render the public API
here, let `docs/reference/cli.md` render the CLI, and decide the HTTP section on whether routes
actually exist. They do not. This grep over all of `src/` returns nothing (exit 1):

```text
grep -rnE "@(app|router|api|bp)\.(get|post|put|patch|delete|route)|add_url_rule|APIRouter|FastAPI\(|Flask\(|Litestar|starlette|aiohttp\.web|http\.server|BaseHTTPRequestHandler" src/ --include='*.py'
```

The only HTTP in the codebase is a **client**: `WebAPI` at `src/remarkable_spec/device/web_api.py:37`
issues requests against the tablet's own USB web interface through `httpx`
(`src/remarkable_spec/device/web_api.py:65`), with methods `list_documents` (`:70`),
`list_all_documents` (`:90`), `download_pdf` (`:112`), `download_rmdoc` (`:129`), `upload_pdf`
(`:149`), `upload_epub` (`:172`), `get_thumbnail` (`:194`), and `search` (`:209`). A client that calls
someone else's endpoints publishes no routes of its own, so **no `## HTTP` section is rendered** and
the corresponding success criterion is satisfied by its absence. No cyclopts `App` is a router; the
CLI census stays with the sibling file.

### Step 7 — fence policy, stated so it is reviewable

Every fence is `py` and strictly verbatim — each non-blank line inside it appears literally in the
file named by that H3's citation, which is what the Validation script proves. What goes in a fence
varies by symbol kind, deliberately:

- **Functions** — the complete `def` line, including multi-line parameter lists quoted in full
  (`export_svg` spans nine lines).
- **Enums** — the `class` line plus every member line. The members are the contract here: the
  0–13 `PenColor` range is a documented architecture decision, and a fence showing only
  `class PenColor(enum.IntEnum):` would hide the thing a caller needs.
- **Pydantic models** — the `class` line, plus the `model_config = ConfigDict(frozen=True)` line for
  the five frozen models (`RGB`, `Pen`, `Point`, `BuiltinTemplate`, `ScreenSpec`), because
  immutability is caller-visible contract. Field declarations are **not** transcribed: most span
  three or four lines each with long `description=` strings, and a partial field list reads as a
  complete one. The citation points at the declaration line so the reader lands on the full field
  set.
- **Module constants** — the assignment line as written, including its annotation
  (`RM_PALETTE: dict[PenColor, RGB] = {`).

One citation per H3, pointing at the declaration line, never a range — a single line number is
unambiguous and the validator checks it against the file's real length.

## Validation

### Scripted pass — `/tmp/doc-reference-public-api/validate.py`

One script checks every mechanically checkable property and exits non-zero on the first failure
class. It parses the output fence-aware (so a `#` or a brace inside a `py` block is never mistaken
for a heading or a hazard), then per H3 block asserts: exactly two fence markers, the fence is the
first element after the heading, the fence tag is `py`, exactly one `path:LOC` citation, the cited
file exists, the cited line is within the file's real length, **every non-blank fence line appears
literally in the cited file**, and the line the citation points at is itself one of the fence lines.
It then resolves all citations document-wide (single lines and ranges), rejects orphan `:LOC`
shorthands, runs `git check-ignore` over every cited path, and confirms every cited path appears in
`git ls-files`.

Command and final output:

```text
$ python3 /tmp/doc-reference-public-api/validate.py
========================================================================
  PASS  H1 OK: '# remarkable-spec · Public API'
  PASS  no YAML frontmatter
  PASS  no mermaid fences
  PASS  no emojis
  PASS  no filler adverbs
  PASS  no bare braces outside fences or inline code
  PASS  content H2 count = 3: ['## Tier 1 — exported from the package root', '## Tier 2 — exported from a subpackage only', '## Public names not documented here']
  PASS  H3 symbol count = 30
  PASS  no ## HTTP section (repo publishes no routes)
  PASS  validated 30 H3 blocks: fence shape, py tag, single citation, verbatim lines
  PASS  resolved 64 total citations across 22 distinct files
  PASS  no orphan shorthand citations (this doc uses full paths throughout)
  PASS  git check-ignore: 0 of 22 cited paths are ignored
  PASS  every cited path is tracked by git
========================================================================
ALL CHECKS PASSED
```

Output is 480 lines. `git check-ignore` returned non-zero for all 22 cited paths, so none resolves
into `dist/`, `.venv/`, `.codegraph/`, `.pytest_cache/`, `.ruff_cache/`, `.erpaval/`, `.claude/`, or
`docs/.repomix/`. The flattened pack was read for the inventory question and cited nowhere.

### One failure the script caught and I fixed

First run failed on exactly one block:

```text
  FAIL  RM2_SCREEN: expected exactly 1 path:LOC citation, got 2: [('README.md', '12', ''), ('src/remarkable_spec/models/screen.py', '80', '')]
```

The `RM2_SCREEN` entry carried a second citation because I had folded the "rM2 is untested hardware"
caveat into its description. That caveat is worth keeping and does not belong in a symbol entry, so I
moved it to the page intro (where no per-block citation cap applies) as a standalone paragraph citing
`README.md:9-12`, and cut the description back to the screen geometry. Re-ran clean.

### Judgment spot-checks — three signatures read end-to-end at their cited spans

The scripted pass proves a declaration line exists; these three prove nothing was dropped from it.
Chosen for maximum contract risk rather than convenience: the longest parameter list, the longest
closed vocabulary, and a model whose decorators change what callers receive.

1. **`export_svg`, `src/remarkable_spec/export/svg.py:18-27`.** All 8 parameters present in source
   order with their defaults intact (`palette`/`screen`/`template_svg` default `None`, `thickness`
   defaults `1.5`, `background_image_b64` and `background_page_size` default `None`), the `-> None`
   return annotation present, and line 17 is blank so no decorator was elided above the `def`. The
   fence reproduces the span exactly, including the trailing `) -> None:`. Note the docstring's `Args`
   block (`:38-52`) documents seven of the eight parameters — `background_page_size` has no `Args`
   entry — which is a source defect, recorded under Out-of-scope findings.
2. **`PenColor`, `src/remarkable_spec/models/color.py:17` plus members `:28-41`.** 14 members, values
   contiguous 0 through 13 with no gaps, `HIGHLIGHT = 9` carrying its inline comment verbatim, line 42
   blank so the member list genuinely ends at 41, and line 16 blank so no decorator was dropped. This
   confirms the `PenColor` 0–13 range `CLAUDE.md` documents.
3. **`Point`, `src/remarkable_spec/models/stroke.py:24-81.`** `class Point(BaseModel):` with line 23
   blank (no decorator above) and `model_config = ConfigDict(frozen=True)` at `:36`, both verbatim in
   the fence. **This spot-check found a real omission**: `Point` also carries three
   `@computed_field  # type: ignore[prop-decorator]` properties at `:61`, `:67`, and `:73`, and in
   Pydantic v2 a computed field serializes — so `model_dump()` returns keys the field list does not
   imply. That is precisely the "attribute that changes the caller's contract" the criterion asks
   about, and my fence policy hid it.

### Fix applied from spot-check 3

Rather than patch one entry, I censused both contract-widening decorator families across all model
modules with an `ast` pass over `decorator_list`:

```text
src/remarkable_spec/models/pen.py:17      class PenType   classmethod (3): is_highlighter 49, is_eraser 54, canonical 59
src/remarkable_spec/models/pen.py:78      class Pen       classmethod (1): from_stroke 132
src/remarkable_spec/models/stroke.py:24   class Point     computed_field (3): pressure_normalized 63, direction_radians 69, tilt 75
src/remarkable_spec/models/stroke.py:84   class Stroke    computed_field (3): is_eraser 121, is_highlighter 127, bounding_box 133
src/remarkable_spec/models/page.py:45     class Layer     computed_field (2): is_empty 76, bounding_box 82
src/remarkable_spec/models/page.py:99     class Page      computed_field (4): rm_filename 126, metadata_filename 132, thumbnail_filename 138, all_strokes 144
src/remarkable_spec/models/document.py:52 class DocumentMetadata classmethod (2): from_json 108, from_path 128
src/remarkable_spec/models/document.py:133 class ExtraMetadata   classmethod (1): from_json 157
src/remarkable_spec/models/document.py:190 class ContentInfo     classmethod (2): from_json 260, from_path 302
src/remarkable_spec/models/document.py:307 class Document  computed_field (6): name 345, is_notebook 351, is_pdf 357, is_epub 363, is_folder 369, is_trashed 375
src/remarkable_spec/models/screen.py:14   class ScreenSpec computed_field (5): points_per_pixel 46, page_width_pt 56, page_height_pt 62, page_width_inches 68, page_height_inches 74
```

23 computed fields across 6 models, 9 classmethods across 5. Both are now documented in the output's
intro as a two-bullet block with per-model line-range citations, keeping the one-citation-per-H3 rule
intact. On review I also corrected my own first wording there: `PenType.is_highlighter` and
`PenType.is_eraser` return `bool` and are not constructors, so the bullet reads "helpers sit alongside
the plain constructors, and the `from_*` ones are the intended entry point on the parse path".

### Two claims I tightened rather than assert loosely

- I had written "Every module opens with `from __future__ import annotations`", carrying the brief's
  wording. Verified instead of assumed: 55 of 56 tracked `.py` files have it, and the sole exception
  is the 0-byte `tests/__init__.py`. Since 56 − 1 = 55 is exactly the `src/` count, the output now
  says "All 55 tracked modules under `src/`", which is true as stated rather than true-ish.
- The "Thirty-five further names" count in the output's last H2 is arithmetic I checked both ways:
  8 + 13 + 2 + 4 + 6 + 3 + 3 = 39 tier-2 names, minus the 4 documented here = 35, and the seven
  bullets that follow list 5 + 13 + 6 + 4 + 2 + 2 + 3 = 35. Both agree.

### Orchestrator directives — compliance check

1. **No tests.** The output makes no claim about tests, coverage, or regression protection, and cites
   no path under `tests/`. The script's `git ls-files` assertion would surface any such path; the 22
   cited paths are 20 files under `src/`, plus `README.md` and `pyproject.toml`. The test signal is
   structurally absent, as the brief records, so there is nothing to report.
2. **No gitignored citation.** Scripted, 0 of 22 hits.
3. **Braces.** Scripted: the validator strips inline code spans and fenced blocks, then fails on any
   remaining `{` or `}`. Zero. Every UUID-in-braces filename in the output — in `DocumentMetadata`,
   `ContentInfo`, `parse_content`, and `parse_metadata` — is wrapped as inline code.
4. **No billable or device commands.** Nothing was executed against AWS or `10.11.99.1`. Every command
   this run issued was a local read: `git`, `grep`, `wc`, `python3` on local files, and
   `codegraph callers`. I did not even need `rmspec --help`, since the CLI surface belongs to the
   sibling packet.
5. **OCR concurrency attribution.** Not applicable — the output documents the library surface and
   makes no claim about `ocr/pipeline.py` or `ocr/postprocess.py`. Neither file is cited.

## Summary

Shipped `docs/reference/public-api.md`, 480 lines, H1 `# remarkable-spec · Public API`, no
frontmatter, no Mermaid, 30 H3 symbol entries at exactly the packet's cap — each one a verbatim `py`
fence, a one-sentence description drawn from the symbol's own docstring where it has one, and a single
`path:LOC` citation on the declaration line. No prior artifact existed: `docs/reference/` was absent
and the output path had never been committed, so nothing was inherited and nothing carried over to
re-verify. The shortlist is the complete tier-1 surface plus the top of tier 2: all **26** names in
the root `__all__` (not the 24 the shared brief and this packet's § 3a both state — I enumerated by
hand, confirmed with an AST pass, and used the source), then the 4 remaining slots filled by inbound
caller count from `codegraph callers` over all 63 distinct public names, excluding the three `cli`
barrel names because `docs/reference/cli.md` owns that surface. That yielded `parse_rm_file` (11
callers), `parse_content` (8), `parse_metadata` (8), and `export_svg` (6, winning a three-way tie
alphabetically). The remaining 35 tier-2 exports are listed by subpackage in a closing H2 with barrel
citations rather than silently dropped. **No `## HTTP` section was rendered**: the repo ships both a
CLI and a library, so Fallback path 4 applied, and a grep for route decorators and server
constructors across `src/` returns nothing — the only HTTP is the `WebAPI` client calling the
tablet's own USB web interface. The scripted pass proves every fence line is literal in its cited
file and every citation resolves to a tracked, non-ignored path and an in-range line; the three
end-to-end spot-checks caught one genuine omission (23 `@computed_field` properties and 9
`@classmethod` helpers widen the Pydantic contract beyond the declared fields), which is now
documented in the intro.
