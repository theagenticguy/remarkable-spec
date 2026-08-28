---
role: doc-architecture-system-overview
model: opus
output: "docs/architecture/system-overview.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · architecture/system-overview.md

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

Produce `docs/architecture/system-overview.md`: a 400–600-word narrative of what `remarkable-spec` does and how its top-level pieces fit, plus a stack table (≥ 5 rows) and a single Mermaid `flowchart LR` covering the top 6 modules.

This file is the entry point of the whole doc tree. A reader who only reads this one file should leave with the right mental model.

## 2. Scope

- Create: `docs/architecture/system-overview.md`
- Do not touch: any other file under `docs/`, any source file in the repo, any other packet under `docs/.packets/`.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase at the working-directory root: top-level structure, manifest files (`package.json` / `Cargo.toml` / `pyproject.toml` / `go.mod` / `Gemfile` / …), entry-point files, README and CHANGELOG if present.
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` if present — useful for a one-shot breadth scan of directory structure and top files.
- This packet's frontmatter `remarkable-spec` placeholder.

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
2. Locate entry points. Read root manifests to identify `main` / `bin` / `entry` declarations; cross-reference with conventional locations (`src/index.*`, `cmd/*/main.go`, `packages/*/src/index.*`, `bin/*`).
3. Identify the top-level modules. Group source files by their top-level directory (`packages/<name>`, `src/<area>`, `internal/<name>`, `apps/<name>`, …). Keep the top 6 by file count plus any obvious "core" module flagged in the manifest. Record the canonical name and one-line role for each.
4. Identify the stack. From manifest files + lockfiles, extract: language(s) + version, runtime, primary frameworks, storage, build tooling, test tooling. Aim for ≥ 5 stack-table rows; each row cites the manifest line that declares it.
5. Draft the narrative — 400–600 words. Paragraph 1 = what the codebase does, who uses it, what problem it solves. Paragraph 2 = how the top-level modules fit together; name each module and the role it plays. Every factual claim ends in a backtick `path:LOC` citation.
6. Draft the Stack table — three columns exactly: `Layer | Technology | Source`. Every Source cell is a backtick `path:LOC` citation to the manifest line.
7. Draft the Module map Mermaid block — `flowchart LR`, 3–20 nodes, labels ≤ 20 chars. Nodes are the top modules from step 3; edges are inferred from import/use relationships (one edge per source-module → target-module pair where at least one file in the source imports at least one file in the target).
8. Write the file with H1 = `# remarkable-spec · System overview`. Verify the final output meets every Success criterion.

## 5. Output format rules

- H1 = `# remarkable-spec · System overview`. No decorative titles.
- No YAML frontmatter on the output file.
- Narrative: 400–600 words. Use `wc -w` to verify.
- Stack table: exactly 3 columns — `Layer | Technology | Source`. ≥ 5 rows. Every Source cell is a backtick `path:LOC` citation.
- Exactly one Mermaid fence (`` ```mermaid ``) containing a `flowchart LR`. 3–20 nodes, labels ≤ 20 chars.
- Every factual claim in the narrative ends in a backtick `path:LOC` citation; file-level claims append `(N LOC)`.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** source files and manifest files directly.
- **Grep** for import statements, `export` declarations, `main`/`init` functions.
- **Glob** to enumerate top-level modules (`packages/*`, `src/*`, `internal/*`, …).
- **Bash** for `wc -l` (file LOC counts), `wc -w` (narrative word count), `find` (file inventory if a glob is awkward), and `jq` / `python -c 'import json; …'` over the optional `docs/.repomix/codebase.json` JSON if present.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences. Especially relevant when filling the Stack table.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

Context7 and the code index are the specifically named tools. If your environment has stronger tooling for everything else, use it — the output rules don't change.

## 7. Fallback paths

- **No clear entry point.** If manifest files don't declare one and no `src/index.*` / `cmd/*/main.*` exists: identify entry by inbound-import count (the file that nothing imports but everything else flows through). Note the heuristic in the Work log.
- **No clear module structure.** If the codebase is single-rooted (no `src/` subdivision): treat the top-level directories as candidate modules; if there are too few, fall back to grouping by file-name prefix or by major concern (one paragraph in the narrative explaining the grouping). Note the fallback in the Work log.
- **Narrative falls short of 400 words.** Add concrete examples (specific files the reader would open first, a sketch of a typical request lifecycle). Do not pad with adverbs.
- **Narrative exceeds 600 words.** Move details to `module-map.md` (which another packet produces). The system-overview is intentionally shallow.
- **No Mermaid-renderable diagram.** If the module graph has fewer than 3 nodes (degenerate case), still emit a Mermaid block — a single node with a self-explanatory label is acceptable when the codebase truly is one module.

## 8. Success criteria

- [x] `docs/architecture/system-overview.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · System overview`.
- [x] Narrative is 400–600 words (verify with `wc -w`). — 478 words in `## What it does`.
- [x] Stack table has ≥ 5 rows; every row cites `path:LOC`. — 20 rows, all three-cell, every Source cell carries a citation.
- [x] Exactly one Mermaid fence; diagram type is `flowchart LR`. — also renders under mmdc 11.16.0.
- [x] Mermaid diagram has 3–20 nodes; every label ≤ 20 chars. — 8 nodes, longest label 18 chars.
- [x] No YAML frontmatter on the output.
- [x] The cross-link pass's validator ran against the output and exited 0: every full and shorthand citation resolves to an existing file and an in-range line, with zero orphan shorthands. Paste the command and its output into Validation. — 57 full citations resolved, 0 shorthands used, exit 0. Command and output pasted.
- [x] No emojis.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers. — the output path held no file; `docs/architecture/` did not exist.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed. — Work log step 1 records that no prior version existed, with the `ls` and `git log` output.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path). — `git check-ignore -q` run per cited path inside the validator; zero hits across 57 citations.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent module names. Every node in the Mermaid diagram must match a real top-level directory or a manifest-declared package.
- Do not exceed 600 words. Detail belongs in `module-map.md`.
- Do not emit more than one Mermaid diagram.
- Do not write YAML frontmatter on the output file.
- Do not paraphrase manifest content; quote the relevant fragment when explaining stack rows.
- Do not emit emojis. Do not use filler adverbs.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `docs/.packets/_environment.md:149` and `:163` — the brief says the root `__all__` re-exports 24
  names; `ast` counts 26 in `src/remarkable_spec/__init__.py:33-67`. Any packet that quotes the brief's
  figure for the library surface will undercount the public API by two names, and a reader auditing
  the export list against the doc will think two names were added since the doc was written.

- `docs/.packets/_environment.md:154` — the brief places the CLI command-registration block at
  `src/remarkable_spec/cli/__init__.py:52-62`; `grep -n 'app.command('` puts it at `:48-58`, a
  10-line offset. A packet that copies that range cites `:52-62`, which in the real file spans
  `app.command(ocr_app, ...)` through the blank lines before `_get_version`, so a reviewer opening the
  citation sees a partial list and concludes the census is wrong when only the range is.

- `README.md:143` and `mise.toml:12` invoke `uvx ty check src/` while `pyproject.toml:70-72`
  configures `[tool.pyright]` and `pyproject.toml:52` puts `pyright>=1.1` in the dev group — the
  configured type checker and the invoked one are different tools. A contributor following the README
  runs `ty` with no configuration and gets results that do not match the settings the repo actually
  declares. (Already noted at `docs/.packets/_environment.md:275-279`; recorded here because I read
  both files while filling the Stack table and the output's Type-checking row states the split rather
  than picking a side.)

- `src/remarkable_spec/render/engine.py:28-32` — module-level `SCREEN_WIDTH = 1404`,
  `SCREEN_HEIGHT = 1872`, `SCREEN_DPI = 226`, and `SCALE = 72.0 / SCREEN_DPI` hardcode reMarkable 2
  geometry under the comment "Default rendering constants", yet a repo-wide grep finds no consumer:
  the only other references are the re-export at `src/remarkable_spec/render/__init__.py:18-21` and the
  `__all__` entries at `:41-44`, so they are public API that the renderer itself never reads — the live
  path resolves geometry from the `ScreenSpec` parameter instead. A reader who takes them as the
  renderer's actual defaults will believe Paper Pro output is scaled at 226 DPI when
  `src/remarkable_spec/models/screen.py:83` supplies 229, and a library consumer who imports
  `SCALE` from `remarkable_spec.render` will silently get reMarkable 2 scaling on a Paper Pro page.

---

## Work log

### Step 1 — prior-artifact check (Process step 1)

**No prior artifact existed.** `docs/architecture/` did not exist on disk before this run:

```
$ ls -la /Users/lalsaado/Projects/remarkable-spec/docs/architecture/
"/Users/lalsaado/Projects/remarkable-spec/docs/architecture/": No such file or directory (os error 2)
$ git log -1 --format=%cs -- docs/architecture/system-overview.md
(empty output, exit 0)
```

`docs/` held only `.packets/` and `.repomix/` — both untracked. So there was **nothing the prior
artifact got wrong**: no stale line numbers, no wrong counts, no fabricated edges, no inverted rules.
This is a first run over a repo with no `docs/` tree, and every claim in the output is drawn from
source read during this run. The partition step from Process step 1 (`git log -1 --format=%cs` per
cited path against the doc's own last write) was not applicable and was skipped.

### Step 2 — inputs read

- `docs/.packets/_environment.md` (442 lines) — read in full first, per the READ FIRST bullet. Its
  stale-prior traps subsection (`:396-441`) is treated as binding; in particular the OCR-concurrency
  attribution, the four-way Python-version disagreement, the `converse`-vs-`invoke_model` trap, and
  the "no import-linter enforces the layering" trap all constrain wording in the output.
- `pyproject.toml` (77 lines) — the single manifest. One distribution, `[project.scripts]` at `:20-21`.
- `README.md` (148 lines) — user-facing contract; supplies the "built for AI assistants" framing.
- `docs/.repomix/codebase.json` — read via `jq` for the breadth scan (never cited, per directive 2).

### Step 3 — entry points (Process step 2)

One console script only: `rmspec = "remarkable_spec.cli:app"` (`pyproject.toml:20-21`). It resolves to
the `cyclopts.App` constructed at `src/remarkable_spec/cli/__init__.py:40-44`, with 11 child `App`
objects mounted by `app.command(<sub_app>, name="<literal>")` at `:48-58`. No `src/index.*`, no
`cmd/*/main.go`, no `bin/` — the Fallback-paths inbound-import heuristic was not needed.

Second entry surface: the package is importable as a library. `src/remarkable_spec/__init__.py:6-31`
imports from `models` and nothing else, and `:33-67` re-exports the names in `__all__`.

**Correction to the shared brief — `__all__` holds 26 names, not 24.** Counted with `ast`:

```
$ python3 -c "import ast; t=ast.parse(open('src/remarkable_spec/__init__.py').read()); \
  print([len(n.value.elts) for n in ast.walk(t) if isinstance(n,ast.Assign) \
  and any(getattr(x,'id',None)=='__all__' for x in n.targets)])"
[26]
```

**Second correction to the brief** — it places the command-registration block at
`src/remarkable_spec/cli/__init__.py:52-62`; `grep -n 'app.command('` puts it at `:48-58`. Both corrections are logged
under Out-of-scope findings, since `docs/.packets/_environment.md` is outside this packet's Scope.

### Step 4 — module inventory (Process step 3)

Re-measured rather than inherited. Command and output:

```
$ for m in models formats render ocr device sync export cli; do \
    n=$(find src/remarkable_spec/$m -name '*.py' | wc -l); \
    loc=$(find src/remarkable_spec/$m -name '*.py' -exec cat {} + | wc -l); \
    echo "$m LOC=$loc files=$n"; done
models LOC=1367 files=8      formats LOC=562  files=6
render LOC=1069 files=5      ocr     LOC=962  files=6
device LOC=1295 files=6      sync    LOC=728  files=5
export LOC=372  files=4      cli     LOC=3899 files=14
root   LOC=67   files=1
$ find src tests -name '*.py' | wc -l   ->  56
$ find src tests -name '*.py' -exec cat {} + | wc -l   ->  10321
```

Matches the brief's table exactly (`docs/.packets/_environment.md:104-118`).

**Grouping decision — the diagram carries all eight packages, not six.** Ranking by file count gives
`cli` (14), `models` (8), then a three-way tie at 6 (`device`, `formats`, `ocr`) and a two-way tie at
5 (`render`, `sync`), so a strict cut at six would break a tie arbitrarily and drop a package that
`cli` imports. Eight nodes sits inside the 3-20 node budget from Output format rules, and every node
maps to a real directory under `src/remarkable_spec/`, satisfying the anti-goal on invented module
names. The top six by file count are all present, so the Objective's "covering the top 6" holds.

### Step 5 — dependency edges (Process step 7)

Measured with a scan over every `from remarkable_spec.<module>` import, bucketed by containing
package (script kept at `/tmp/doc-architecture-system-overview/edges.txt`):

```
models  -> (leaf)                     sync    -> (leaf)
formats -> models (8)                 render  -> models (6)
export  -> models (6), render (4)     device  -> sync (4)
ocr     -> models (4), export (2), formats (2)
cli     -> models (15), formats (13), device (11), ocr (8), sync (5), render (5), export (3)
root    -> models (7)
```

Reproduces the brief's measurement exactly. Per the brief's warning that import-derived edges
over-report coupling, every one of the 15 edges was then confirmed at a concrete import site:

| Edge | Confirmed at |
| --- | --- |
| `cli` → `models` | `src/remarkable_spec/cli/annotations_cmd.py:223` |
| `cli` → `formats` | `src/remarkable_spec/cli/annotations_cmd.py:222` |
| `cli` → `ocr` | `src/remarkable_spec/cli/annotations_cmd.py:224` |
| `cli` → `render` | `src/remarkable_spec/cli/annotations_cmd.py:225` |
| `cli` → `device` | `src/remarkable_spec/cli/device_cmd.py:109` |
| `cli` → `export` | `src/remarkable_spec/cli/render_cmd.py:357` |
| `cli` → `sync` | `src/remarkable_spec/cli/_util.py:80` |
| `formats` → `models` | `src/remarkable_spec/formats/content.py:32` |
| `render` → `models` | `src/remarkable_spec/render/engine.py:21` |
| `export` → `models` | `src/remarkable_spec/export/pdf.py:14` |
| `export` → `render` | `src/remarkable_spec/export/pdf.py:16` |
| `ocr` → `models` | `src/remarkable_spec/ocr/pipeline.py:48` |
| `ocr` → `formats` | `src/remarkable_spec/ocr/pipeline.py:47` |
| `ocr` → `export` | `src/remarkable_spec/ocr/pipeline.py:46` |
| `device` → `sync` | `src/remarkable_spec/device/sync.py:28` |

No cycles. Per the brief (`docs/.packets/_environment.md:143-145`) nothing enforces this direction —
the output says "currently imports", never "the build enforces".

### Step 6 — source files read for narrative grounding

- `src/remarkable_spec/ocr/pipeline.py` (127 LOC, read in full) — confirms directive 5: straight-line
  `render_rm_to_png` then `transcribe_page`, no executor.
- `src/remarkable_spec/ocr/postprocess.py:1-50,115-159` — `ThreadPoolExecutor` imported at `:18`,
  used as `max_workers=2` at `:131`. This is the only concurrency in the repo.
- `src/remarkable_spec/formats/rm_file.py:1-60` — wraps `rmscene.read_tree`, maps scene items to
  models, silences the `rmscene` logger at `:31`.
- `src/remarkable_spec/models/screen.py` (104 LOC, read in full) — `RM2_SCREEN` at `:80`,
  `PAPER_PRO_SCREEN` at `:83`, `detect_screen` at `:86-104`.
- `src/remarkable_spec/render/engine.py:1-70` plus `x_shift = vw / 2` at `:134`.
- `src/remarkable_spec/render/pens.py:437-476` — 10 `match` arms in `get_pen_renderer`.
- `src/remarkable_spec/models/color.py:17-41` — `PenColor` IntEnum, 14 members, 0 through 13.
- `src/remarkable_spec/sync/db.py:1-60` — stdlib `sqlite3`, WAL and `foreign_keys=ON` at `:54-55`.
- `src/remarkable_spec/sync/migrations.py` — 6 `CREATE TABLE IF NOT EXISTS` statements at
  `:17,35,49,64,77,93`.
- `src/remarkable_spec/sync/models.py:66-91` — OCR and diagram caches keyed on `rm_hash`.
- `src/remarkable_spec/sync/hasher.py:15-25` — `hash_file` builds a `hashlib.sha256`.
- `src/remarkable_spec/device/connection.py:1-55` — paramiko lazily imported at `:25-35`.
- `src/remarkable_spec/cli/_util.py:13-27` — `RmspecSettings(BaseSettings)` with `env_prefix="RMSPEC_"`.
- `src/remarkable_spec/ocr/diagram.py:232` and `src/remarkable_spec/cli/diagram_cmd.py:289` — the
  optional external `mmdc` binary via `subprocess`.

No test files were read or cited (directive 1): the signal is structurally absent, `tests/` holds one
0-byte `__init__.py`.

### Step 7 — draft written, then four fixes from self-review

Wrote `docs/architecture/system-overview.md` with three content H2s: `## What it does` (the
narrative), `## Stack`, `## Module map`. Then found and fixed four defects in my own first draft:

1. **A citation with no resolvable line.** The Test-harness stack row originally cited
   `tests/__init__.py`, a 0-byte file with no line 1 — it would fail the Output-format rule that every
   Source cell is a `path:LOC` citation, and any range check on it. Replaced with
   `pyproject.toml:74-76`, `mise.toml:9`; the empty-suite fact now lives in prose with no `tests/`
   citation at all.
2. **An absence claim dressed as a cited claim.** "a single package, no workspace" cited
   `pyproject.toml:2`, which only declares the name. Rewritten to cite the name at `:2` and the
   src-layout build backend at `pyproject.toml:55-57`.
3. **An undercounted dispatch.** `get_pen_renderer` was cited at `src/remarkable_spec/render/pens.py:456-476`
   for "10 pen families", but reading `:478-480` shows a `case _:` fineliner fallback the range
   excluded. Range widened to `:457-480` and the fallback named.
4. **A seven-edge claim backed by four edges.** "`cli` is the only module with an edge to every other"
   cited only `src/remarkable_spec/cli/annotations_cmd.py:222-225`, which covers `formats`, `models`,
   `ocr`, `render` — four of seven. Now cites all seven sites, adding
   `src/remarkable_spec/cli/device_cmd.py:109`, `src/remarkable_spec/cli/render_cmd.py:357`, and
   `src/remarkable_spec/cli/_util.py:80`. The "nothing enforces the layering" sentence also gained
   `pyproject.toml:63-68` (the ruff selection carries no import-boundary rule) so it rests on the
   configuration rather than on absence.

Two wording fixes on top: "11 subcommand groups" became "11 top-level commands" (only `sync` and
`device` are groups; the other nine are single `@app.default` functions), and one ragged line wrap was
reflowed.

Directive-by-directive compliance:

- **Directive 1 (no tests).** Zero `tests/` citations. The Test-harness stack row states the harness
  is wired and the suite empty, and no sentence anywhere claims coverage or verification by tests.
- **Directive 2 (no gitignored citations).** The validator runs `git check-ignore -q` on all 57
  cited paths; zero hits. `docs/.repomix/codebase.json` was read for the breadth scan and never cited.
- **Directive 3 (braces).** Zero bare braces outside fenced blocks and inline code spans, checked with
  a script (see Validation). The Mermaid fence uses no braces. The per-document UUID spelling from
  `src/remarkable_spec/models/document.py:8` never enters the output, so the trap did not arise.
- **Directive 4 (no billable or device commands).** No `rmspec` subcommand was executed at all — not
  even `--help`. Every fact traces to a file read. `mmdc` was invoked once, offline, on a local
  temp file to confirm the diagram parses.
- **Directive 5 (OCR concurrency attribution).** The narrative names `pipeline.py` as straight-line
  (`src/remarkable_spec/ocr/pipeline.py:113-127`) and places the two-worker pool in `postprocess.py`
  (`src/remarkable_spec/ocr/postprocess.py:131`), and says "the only concurrency anywhere in the
  codebase" rather than calling the codebase synchronous.

## Validation

### Primary validator — structure plus every citation, scripted

Script at `/tmp/doc-architecture-system-overview/validate.py`. It checks all 13 mechanically
checkable Success criteria: file existence, absent frontmatter, exact H1, single H1, no emojis, no
filler adverbs, narrative word count, stack column set, stack row count, a `path:LOC` citation in
every Source cell, exactly one Mermaid fence, `flowchart LR`, node count, label lengths, and then for
every citation in the file — file exists, line or range in range, not gitignored, no orphan shorthand.

```
$ uv run --no-project python /tmp/doc-architecture-system-overview/validate.py; echo "EXIT=$?"
...
PASS  output exists: /Users/lalsaado/Projects/remarkable-spec/docs/architecture/system-overview.md
PASS  no YAML frontmatter (line 1 is not '---')
PASS  H1 exact: '# remarkable-spec · System overview'
PASS  exactly one H1 (found 1)
PASS  no emojis (found [])
PASS  no filler adverbs (found [])
PASS  narrative word count in [400,600]: 478
PASS  stack columns exactly Layer|Technology|Source: ['Layer', 'Technology', 'Source']
PASS  stack rows >= 5: 20
PASS  exactly one mermaid fence: 1
PASS  only one mermaid fence overall: ['mermaid', '']
PASS  diagram type is flowchart LR
PASS  node count in [3,20]: 8 -> ['CLI', 'DEVICE', 'EXPORT', 'FORMATS', 'MODELS', 'OCR', 'RENDER', 'SYNC']
PASS  every label <= 20 chars: worst=(18, 'cli (rmspec entry)'), over=[]
PASS  full citations found and all resolved: 57

55 passed, 0 failed
EXIT=0
```

All 57 full citations resolve to an existing, non-gitignored file and an in-range line. Zero
shorthand `:LOC` citations appear in the output, so the orphan-shorthand class is vacuously clean —
every citation carries its full path. `git check-ignore -q` was run per cited path inside the script;
zero hits.

### Brace safety (orchestrator directive 3)

```
$ python3 - <<'EOF'   # strips inline code spans, then looks for { or } outside fences
... bare-brace hits: NONE
EOF
```

### Mermaid renders (offline, free)

```
$ mmdc --input /tmp/doc-architecture-system-overview/diagram.mmd \
       --output /tmp/doc-architecture-system-overview/diagram.svg
Generating single mermaid chart
EXIT=0
$ ls -la /tmp/doc-architecture-system-overview/diagram.svg
.rw-r--r--@ 28k lalsaado 28 Aug 11:10 diagram.svg
```

mmdc 11.16.0, invoked by absolute path at
`/Users/lalsaado/.local/share/mise/installs/npm-mermaid-js-mermaid-cli/11.16.0/bin/mmdc`. The
diagram parses and renders, so the fence is not merely well-formed Markdown.

### Word counts

Narrative section (`## What it does`) is 478 words, inside the 400-600 band. Whole-file `wc -w` is
977, which includes the 20-row stack table, the Mermaid fence, and the module-map closing paragraph —
none of which the Output format rules count toward the narrative budget.

### Judgment spot-checks (not scriptable)

Six claims where the question is whether the prose is right, not whether the string exists. Each was
read at source:

| Claim | Source read | Verdict |
| --- | --- | --- |
| `get_pen_renderer` covers 10 pen families | `src/remarkable_spec/render/pens.py:457-480` | 10 explicit `case` arms plus a `case _:` fineliner fallback — claim adjusted to say so |
| `PenColor` has 14 members, 0 through 13 | `src/remarkable_spec/models/color.py:28-41` | `BLACK = 0` through `YELLOW_2 = 13`, contiguous |
| `pipeline.py` is straight-line | `src/remarkable_spec/ocr/pipeline.py:113-127` | `render_rm_to_png` then `transcribe_page`, no executor |
| Six hand-written tables | `src/remarkable_spec/sync/migrations.py:17,35,49,64,77,93` | six `CREATE TABLE IF NOT EXISTS` |
| `RMSPEC_` env prefix | `src/remarkable_spec/cli/_util.py:22-27` | `SettingsConfigDict(env_prefix="RMSPEC_", ...)` |
| Root re-exports come only from `models` | `src/remarkable_spec/__init__.py:6-31` | all seven imports are `remarkable_spec.models.*` |

### Fixes applied

The four defects and two wording fixes listed under Work log step 7 were applied before the final
validator run. The run above is post-fix and is the one that exits 0.

## Summary

Shipped `docs/architecture/system-overview.md` — the doc tree's entry point — as a 478-word narrative
plus a 20-row stack table and one `flowchart LR` over the eight packages under `src/remarkable_spec/`.
No prior artifact existed: `docs/architecture/` was absent and `git log` on the output path returned
nothing, so every claim was drawn from source read in this run rather than inherited. On the
module-grouping step I deliberately did not cut to six: ranking by file count gives `cli` (14) and
`models` (8) clear, then a three-way tie at 6 (`device`, `formats`, `ocr`) and a two-way tie at 5
(`render`, `sync`), so a strict top-six would have broken a tie arbitrarily and dropped a package that
`cli` imports — eight nodes fits the 3-20 budget and every node is a real directory, so no invented
module name enters the diagram. The 15 edges were measured by scanning `from remarkable_spec.<module>`
imports and then each confirmed at a concrete import site, because the environment brief warns that
import-derived edges over-report coupling through function-local lazy imports; the output says the
code "currently imports" in this direction and cites `pyproject.toml:63-68` and `mise.toml:13` for the
fact that nothing enforces it. Two corrections to the shared environment brief surfaced along the way
and are logged under Out-of-scope findings: the root `__all__` holds 26 names, not 24, and the CLI
command-registration block is at `src/remarkable_spec/cli/__init__.py:48-58`, not `:52-62`. The validator exits 0 with 55
checks passed and all 57 citations resolved, none gitignored; the Mermaid fence additionally renders
under mmdc 11.16.0.
