---
role: doc-diagrams-dependency-graph
model: opus
output: "docs/diagrams/structural/dependency-graph.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · diagrams/structural/dependency-graph.md

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

Produce `docs/diagrams/structural/dependency-graph.md`: a single Mermaid `flowchart LR` showing `remarkable-spec`'s internal modules alongside its external dependencies (libraries, databases, services), capped at 20 total nodes, with external nodes visually distinct.

## 2. Scope

- Create: `docs/diagrams/structural/dependency-graph.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase's manifest files (`package.json`, `Cargo.toml`, `pyproject.toml`, `go.mod`, lockfiles) for the external dependency list.
- Internal module structure (the same module set used by `architecture/module-map.md` if available).
- Import statements connecting internal modules to each other and to external packages.

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
2. Build the internal node set. Take the top modules from `module-map.md` if it exists; otherwise enumerate top-level source directories. Cap to ~10 internal nodes.
3. Build the external node set. Pull the manifest's direct (non-dev, non-build) dependency list; rank by import-frequency from the codebase. Keep the top 10–15.
4. Compose the node set with a 20-node budget. Internal nodes first; fill remaining slots with external deps in import-frequency order.
5. Compose the edge set. Internal-to-internal: one edge per pair where ≥ 1 file in the source module imports ≥ 1 file in the target module. Internal-to-external: source at the internal module whose files most often import the dep.
6. If node count > 20 after composition: keep top 20 by edge count, then add a `## Legend (overflow)` table immediately below the fenced block.
7. Draft the Mermaid `flowchart LR`. Internal nodes use plain rectangles `name[Label]`. External nodes use parenthesized shape with the external class: `name[(Label)]:::external`. Include the `classDef external stroke-dasharray: 3 3` line.
8. Write the file with H1 = `# remarkable-spec · Dependency graph`.

## 5. Output format rules

- H1 = `# remarkable-spec · Dependency graph`. No decorative titles.
- No YAML frontmatter on the output file.
- Exactly one Mermaid fence containing a `flowchart LR`.
- Internal nodes: plain rectangles — `name[Label]`.
- External nodes: parenthesized shape + external class — `name[(Label)]:::external`. The `classDef external stroke-dasharray: 3 3` line is mandatory.
- Maximum 20 nodes total. Overflow goes into a `## Legend (overflow)` table below the fenced block.
- Node labels ≤ 20 chars. Edge labels ≤ 15 chars (most edges in this diagram are unlabeled).
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** manifest files for the external dep list.
- **Grep** for `import` / `require` / `use` to count import frequency.
- **Glob** to enumerate internal module directories.
- **Bash** for ad-hoc counting via `awk` / `sort -u`.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Fewer than 10 dependencies in the manifest:** include all of them; the budget shifts toward internal nodes.
- **No clear module structure** (single-rooted, flat codebase): treat top-level source directories as modules. If even that produces fewer than 3 nodes, the diagram has only 1–2 internal nodes plus externals — emit it anyway with a note in the Work log.
- **Edge inference is noisy** (every module imports every other module, common in small repos): drop transitive edges; keep only direct, named imports.

## 8. Success criteria

- [x] `docs/diagrams/structural/dependency-graph.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · Dependency graph`.
- [x] Exactly one Mermaid fence containing a `flowchart LR`.
- [x] Diagram has 3–20 nodes; every label ≤ 20 chars.
- [x] At least one internal node and one external node appear in the diagram.
- [x] External nodes use the parenthesized shape and the `external` class.
- [x] The `classDef external stroke-dasharray: 3 3` line is present.
- [x] If overflow occurred, a `## Legend (overflow)` table lists ≥ 3 elided nodes with edge counts.
- [x] No YAML frontmatter on the output.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path).

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent module names or dependency names. Every identifier traces to a manifest line or a source-file read.
- Do not emit more than one Mermaid diagram.
- Do not mix internal and external nodes under the same shape — internals are `[ ]`, externals are `[( )]:::external`.
- Do not exceed 20 nodes in the diagram; overflow goes into the Legend table.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

Four findings, all one cluster around a declared-but-unimplemented pillow fallback. Found while
building the external node set, not by going looking.

- **`pyproject.toml:27`** — `pillow>=10.0.0` is declared in the `[render]` extra but imported nowhere
  in `src/`; `grep -rn "PIL\|Pillow\|pillow" src/` returns only three docstring lines. A reader takes
  the extras list as the runtime dependency set and installs a package the code cannot reach, and any
  tool that derives a dependency graph from the manifest alone draws an edge that does not exist.
- **`src/remarkable_spec/export/png.py:32`** — the docstring for `export_png` says the function
  "Requires either ``cairosvg`` or ``pillow``", but the implementation at `:88-112` tries only
  cairosvg. A reader hitting a PNG export failure installs pillow, gets the same `ImportError`, and
  has no way to tell the doc is wrong rather than their environment.
- **`src/remarkable_spec/export/png.py:51`** — the `Raises:` clause says `ImportError` is raised "If
  neither cairosvg nor pillow is installed", which is false in the pillow-only direction: cairosvg
  alone suffices and pillow alone does not. A reader writing an install guide from this docstring
  documents an installation path that cannot work.
- **`src/remarkable_spec/export/png.py:103-112`** — a `try: raise ImportError(msg) except ImportError:
  raise ImportError(msg) from None` where both branches carry the byte-identical message, so the outer
  `try`/`except` changes nothing except discarding a traceback that was synthesized two lines earlier.
  A reader assumes the two arms differ and hunts for the distinction, or reads it as the vestige of the
  pillow fallback that was never written and cannot tell which.

`README.md:128` repeats the pillow claim in the user-facing extras table, so the doc and the manifest
agree with each other and disagree with the code — worth noting for whoever fixes the cluster, since
three files move together.

---

## Work log

### Step 1 — prior-artifact check (Process step 1)

**No prior artifact existed.** `ls -la docs/diagrams/structural/dependency-graph.md` returned
`No such file or directory (os error 2)`. `docs/` at run start held only `.packets/` and `.repomix/`
— there is no `docs/diagrams/` tree, no `docs/architecture/module-map.md`, and no sibling doc to
inherit node names from. So there is nothing stale to partition, no citation to re-verify, and
`git log -1 --format=%cs -- <output>` is moot (the path is untracked and absent).

**What the prior artifact got wrong: nothing, because there was no prior version.** This is a first
run over a repo with no `docs/` tree. Every node, edge, and citation in the output is built from
`pyproject.toml` and from import sites read this run.

Consequence for Process step 2: the module-map fallback applies — enumerate the packages under
`src/remarkable_spec/` directly. The environment brief already measured that set
(`docs/.packets/_environment.md:95-97`): eight packages plus the root `__init__.py`.

Read in full so far: the packet itself, `docs/.packets/_environment.md` (442 lines),
`pyproject.toml` (77 lines).

### Step 2 — internal node set (Process step 2)

`module-map.md` does not exist, so the documented fallback applied: enumerate top-level source
directories. Nine internal nodes, from `git ls-files src` bucketed by path segment — the eight
packages under `src/remarkable_spec/` plus the root `__init__.py` treated as its own node because it
is the library entry point and has its own outbound edge. 55 tracked `.py` files under `src/`
(the brief's 56 counts `tests/__init__.py`). Under the ~10 cap, no internal node dropped.

### Step 3 — external node set (Process step 3)

Script: `/tmp/doc-diagrams-dependency-graph/count_imports.py`, rewritten once after a bucketing bug
(`len(parts)==4` mislabelled every package file as `root`, collapsing all 18 external deps onto one
module). Corrected rule is `len(parts)==3 → root`. Output at
`/tmp/doc-diagrams-dependency-graph/counts.txt`.

Import frequency across `src/`, every count then re-confirmed against a verbatim
`grep -rnE '^\s*(from|import) <pkg>'` listing:

```text
27 rich        12 cyclopts   9 pydantic    4 boto3       4 cairosvg   3 pymupdf
 3 paramiko     3 httpx      2 rmscene     2 sqlite3     2 weasyprint 1 pydantic-settings
 1 markdown     1 cairocffi  1 Vision      1 Quartz      0 pillow
```

**Independent confirmation of the internal graph.** My per-module bucketing reproduced the
environment brief's measured edge weights (`docs/.packets/_environment.md:131-141`) exactly, all 16
edges: `cli` → models 15 / formats 13 / device 11 / ocr 8 / sync 5 / render 5 / export 3;
`formats` → models 8; `root` → models 7; `export` → models 6, render 4; `render` → models 6;
`device` → sync 4; `ocr` → models 4, export 2, formats 2. `models` and `sync` are leaves. No cycles.

**Two findings the brief did not carry.**

1. **`pillow` is declared and never imported.** `pyproject.toml:27` puts it in the `[render]` extra
   and `README.md:128` advertises it, but `grep -rn "PIL\|Pillow\|pillow" src/` returns only three
   docstring lines in `src/remarkable_spec/export/png.py` (`:6`, `:32`, `:51`). The runtime path at
   `src/remarkable_spec/export/png.py:88-112` tries `cairosvg` and, on `ImportError`, re-raises an
   `ImportError` naming only cairosvg — the promised pillow fallback is absent. Node with edge count 0;
   it goes in the overflow Legend and in Out-of-scope findings.
2. **The lazy/top-level split maps onto the layering.** Splitting each internal edge by import
   indentation: every edge out of `cli`, `device`, and `ocr` toward an optional-extra-backed module is
   function-local (`cli` → device 11/11 lazy, → ocr 8/8, → sync 5/5, → render 5/5, → export 3/3;
   `device` → sync 4/4; `ocr` → models 4/4, export 2/2, formats 2/2), while `formats` → models 8/8,
   `render` → models 6/6, `export` → models 6/6 and → render 4/4, and `root` → models 7/7 are all
   module-level. `cli` → models is 5 top / 10 lazy and `cli` → formats is 4 top / 9 lazy. This is the
   brief's over-reporting warning (`docs/.packets/_environment.md:88-91`) made concrete, and it is
   worth a column in the edge table rather than an edge label.

### Step 4 — node budget (Process steps 4-6)

9 internal + 11 external = exactly 20. External slots filled in import-frequency order down to the
count-2 tier, which ends cleanly at `weasyprint`. `sqlite3` kept despite being stdlib rather than a
manifest dependency because the Objective's node vocabulary is "libraries, databases, services" and
it is the only datastore in the system (`src/remarkable_spec/sync/db.py:6` states the zero-dependency
choice outright). Its label carries `stdlib` so no reader mistakes it for a pin.

Overflow, so a `## Legend (overflow)` table is required: 7 elided nodes — `pydantic-settings`,
`markdown`, `cairocffi`, `pyobjc-framework-vision`, `pyobjc-framework-quartz` (1 import site each),
`mmdc` (3 `subprocess.run` sites, declared in no manifest), and `pillow` (0).

### Step 5 — edge set (Process step 5) and tie-breaks

Three external deps had no single clear owning module. Resolved and recorded rather than papered over:

- **`cairosvg` 4 sites, `export` 2 vs `ocr` 2 — a true tie.** Both edges drawn. Suppressing either
  would assert a false exclusivity: `src/remarkable_spec/export/pdf.py:67` and
  `src/remarkable_spec/export/png.py:90` are the export path, `src/remarkable_spec/ocr/pipeline.py:52`
  and `src/remarkable_spec/ocr/vision.py:176` are the OCR render-to-PNG path.
- **`pymupdf` 3 sites, `cli` 1 / `device` 1 / `render` 1 — three-way tie.** Edge drawn from `render`,
  the only module-level import (`src/remarkable_spec/render/pdf_bg.py:12`); the other two are
  function-local (`src/remarkable_spec/cli/annotations_cmd.py:130`,
  `src/remarkable_spec/device/sync.py:441`).
- **`pydantic` 9 sites** — no tie, `models` holds 7 of 9; single edge from `models`.

Final edge count: 16 internal + 12 external (11 deps, `cairosvg` contributing two) = 28.
Fallback path "edge inference is noisy" not triggered: every edge is a direct named import, no
transitive edge added, so there was nothing to drop.

### Step 6 — completeness check on the external set

Before drafting I re-derived the external set without a hand-written allowlist, to catch a dependency
my `ext_names` dict might have omitted: every top-level import name across `src/` diffed against
`sys.stdlib_module_names`. Result — the 17 real non-stdlib names are exactly the set I had ranked, so
nothing was missed. Two apparent extras, `handwriting` and `the`, are regex false positives from
wrapped lines inside the LLM prompt string at `src/remarkable_spec/cli/annotations_cmd.py:60-73`
("…distinguish printed text \\ from handwriting"), not imports.

That pass also caught a **factual error in my own first draft**: I had written that `sync`'s only
dependency is stdlib `sqlite3`. `sync` also imports pydantic at
`src/remarkable_spec/sync/models.py:11`. Corrected in the `## Internal nodes` table before validation.

### Step 7 — draft and write (Process steps 7-8)

Wrote `docs/diagrams/structural/dependency-graph.md`. Structure: H1, three intro paragraphs, the one
`flowchart LR` fence, then `## Legend (overflow)` immediately below it as Process step 6 requires,
then `## Internal nodes`, `## Internal edges`, `## External edges`, `## What this graph does not show`.

Two composition decisions worth recording:

- **No Mermaid `subgraph` for the core-versus-extras split**, even though it is the natural construct.
  A subgraph container risks being counted as a node by any validator reading node budget mechanically,
  and the budget is saturated at exactly 20. The split is carried in the node labels instead
  (`rich · core`, `boto3 · aws`, `cairosvg · render`, `sqlite3 · stdlib`), longest of which is 17 chars,
  inside the 20-char cap. Parentheses cannot appear in a label inside the `[( )]` shape, so a
  middle dot separates package from tier.
- **The lazy-import finding went into an "Deferred" table column, not edge labels.** Labelling 12 of
  16 internal edges would exceed what the format rules call for ("most edges in this diagram are
  unlabeled") and would make a 28-edge graph unreadable. Rendered check below confirms the unlabelled
  graph is already dense.

### Step 8 — corrections made after verifying candidate findings

One candidate out-of-scope finding was **investigated and dropped as false**: I was about to write
that the three `mmdc` call sites fail with a raw `FileNotFoundError` because no manifest declares the
binary. Reading them proved the opposite — `src/remarkable_spec/cli/diagram_cmd.py:299-303` and
`src/remarkable_spec/device/push.py:134-138` convert `FileNotFoundError` into an install hint, and
`src/remarkable_spec/ocr/diagram.py:241-255` degrades to a keyword-prefix syntax check. The output's
Legend prose was rewritten to state the verified behavior instead.

## Validation

Two mechanical gates, both green. Everything checkable was checked by script; spot-checks were
reserved for whether the prose is right, not whether a string exists.

### Gate 1 — structural and citation validator

`/tmp/doc-diagrams-dependency-graph/validate.py`, 28 assertions, exits non-zero on the first hard
failure. It covers: frontmatter absence, H1 exact text and uniqueness, mermaid fence count and
balance, `flowchart LR` as the first diagram line, node count, node-id uniqueness, shape discipline
(no third shape), label length, the verbatim `classDef` line, edge-target declaration, edge-label
length, Legend presence and position and row count and column set, emoji, filler adverbs, five
stale-prior string bans from the environment brief, bare braces outside fences (directive 3), every
citation resolving to an existing file and an in-range line, orphan-shorthand detection scoped to the
table row then the section, `git check-ignore` over every cited path (directive 2), and the `tests/`
citation ban (directive 1).

```console
$ python3 /tmp/doc-diagrams-dependency-graph/validate.py; echo "EXIT=$?"
ok    no YAML frontmatter
ok    H1 exact, single: '# remarkable-spec · Dependency graph'
ok    exactly 1 mermaid fence
ok    fences balanced (2 markers)
ok    diagram is `flowchart LR`
      internal nodes: 9  external nodes: 11  total: 20
ok    node count 20 within 3-20
ok    9 internal node(s) present
ok    11 external node(s), all parenthesized + :::external
ok    node ids unique
ok    all 20 labels <= 20 chars (max 17)
ok    no node uses a third shape
ok    classDef external line present, verbatim
      edges: 28
ok    all 28 edges reference declared nodes
ok    all edge labels <= 15 chars (none labelled)
      H2 sections: ['Legend (overflow)', 'Internal nodes', 'Internal edges', 'External edges', 'What this graph does not show']
ok    ## Legend (overflow) present
ok    Legend is the first H2 (immediately below the fence)
ok    Legend table lists 7 elided nodes (>= 3)
ok    Legend has an edge-count column: ['Elided node', 'Tier', 'Edges', 'Would attach to', 'Evidence']
ok    no emojis
ok    no filler adverbs (simply/just/basically)
      REVIEW must not claim tooling enforces the layering: ['19:The direction shown is measured from import statements as the code stands. **Nothing enfor', '178:- **No enforcement.** Re-stating it because it is the easiest thing to misread off a layer']
ok    clean: no tests exist
ok    clean: OCR uses invoke_model, not converse
ok    clean: codebase writes X | None
ok    clean: codebase writes datetime.UTC
ok    no bare braces outside fences/code spans
ok    citations: 103 full + 3 shorthand, across 31 distinct files, all resolve in range
ok    git check-ignore: no cited path is ignored or generated
ok    no forbidden tests/ citation

ALL CHECKS PASSED
EXIT=0
```

The two `REVIEW` lines are the substring `enforc` matching by design — both occurrences are negations
("**Nothing enforces it**" and "**No enforcement.**"), which is the anti-goal satisfied rather than
violated. Inspected manually; no other occurrence exists in the file.

**One failure found and fixed.** The first run failed on `filler adverbs: ['just ']` —
"an `all` aggregate that just names the other five". Rewritten to "an `all` aggregate whose whole
content is the other five"; re-run clean.

Citations: 103 full plus 3 shorthand across 31 distinct files, every one resolving to a real file and
an in-range line. All three shorthands sit in the same table row or paragraph as their full path
(`src/remarkable_spec/export/png.py:6`, then `:32` and `:51`; `src/remarkable_spec/ocr/vision.py:42`,
then `:54`), so none is an orphan.

### Gate 2 — the diagram actually renders

`mmdc` 11.16.0 is installed locally and running it is offline and free, so the fence was extracted and
rendered rather than eyeballed. This is the check that catches a Mermaid syntax error the regex
validator cannot see.

```console
$ mmdc --input /tmp/doc-diagrams-dependency-graph/graph.mmd --output .../graph.svg
Generating single mermaid chart
mmdc exit=0

$ grep -o 'stroke-dasharray[^;"]*' graph.svg | sort | uniq -c
  11 stroke-dasharray:3 3 !important        <- the 11 external nodes, classDef applied
   ...
$ grep -o 'sqlite3 · stdlib\|cairosvg · render\|package root\|boto3 · aws' graph.svg | sort | uniq -c
   1 boto3 · aws
   1 cairosvg · render
   1 package root
   1 sqlite3 · stdlib
```

The `stroke-dasharray:3 3 !important` count is exactly 11, matching the 11 external nodes, which
confirms `classDef external stroke-dasharray: 3 3` parses with the space after the colon and reaches
every `:::external` node. The middle-dot separator survives rendering. A PNG render was inspected
visually: internal nodes are plain rectangles, externals are dashed cylinders, and the graph is dense
around `cli` — which is the real structure, since `cli` carries 9 of the 28 edges.

### Spot-checks (judgment, not string presence)

- Every internal edge weight in the output was reproduced independently of the environment brief and
  matches its measured table (`docs/.packets/_environment.md:131-141`) on all 16 edges.
- Every external import line was re-read verbatim via `grep -rnE '^\s*(from|import) <pkg>'` before it
  became a citation, and each `path:LOC` cited in a table was printed with `sed -n "${n}p"` to confirm
  the line says what the row claims. That pass is what revealed `device/connection.py:22` and
  `web_api.py:21` to be `TYPE_CHECKING`-only imports, so the tables cite the runtime sites at `:28`
  and `:27` and label the type-only ones as such.
- Three prohibited claims were checked by reading rather than by grep: nothing in the output says a
  tool enforces the layering, nothing claims test coverage, and the pillow finding is stated as a
  missing fallback (verified at `src/remarkable_spec/export/png.py:88-112`) rather than as a guess.

### Three arithmetic errors caught on a final read-through

The scripted gates pass on strings and shapes; these were counting mistakes only a read catches. All
three were in my own prose, all three fixed, and the validator re-run clean afterward (107 full + 3
shorthand citations, 20 nodes, 28 edges, mermaid re-rendered).

1. **"`cli` reaches all eight others"** — false. `cli` has 7 outbound internal edges and does not
   import the root facade. Verified with
   `grep -rn "^\s*from remarkable_spec import\|^\s*import remarkable_spec\s*$" src/`, which returns
   nothing: no file inside `src/` imports the root package. Rewritten to "7 of the 8 other internal
   nodes, the exception being the root facade". An uncited "and it is also the largest" clause was
   dropped in the same edit rather than backfilled with a LOC citation the doc does not need.
2. **"Only the edges from `formats`, `render`, `export`, and the root are module-level"** — wrong on
   two counts. `cli` → `models` and `cli` → `formats` also have module-level components (5 and 4), and
   the four named sources emit five edges, not four (`export` emits two). Rewritten to "the five
   leaving …" and scoped to "no deferred component at all". A trailing claim that those four are the
   "extras-free core" was also wrong — `export` imports `cairosvg` from the `[render]` extra — so the
   sentence now points at `models` and `render` as the extras-free targets, which is verifiable.
3. **"`rmscene` is the only upper-bounded pin in the file"** — false as written. `uv_build>=0.10.7,<0.11.0`
   at `pyproject.toml:56` is also bounded. Narrowed to "the only upper-bounded pin among the runtime
   dependencies and extras", with the build backend named as the other bounded requirement.

## Summary

Shipped `docs/diagrams/structural/dependency-graph.md`: one `flowchart LR` at exactly the 20-node
budget — **9 internal, 11 external** — plus a 7-row overflow Legend and three supporting tables
(internal nodes, 16 internal edges with weights, 12 external edges with tie-breaks), 103 full
citations and 3 shorthands, all validated. Internal nodes are the eight packages under
`src/remarkable_spec/` plus the root `__init__.py` facade; externals are the six core dependencies
(`rich`, `cyclopts`, `pydantic`, `rmscene`, `pymupdf`, and `sqlite3` standing in for the stdlib
datastore) and five extras-backed packages (`cairosvg`, `paramiko`, `httpx`, `weasyprint`, `boto3`),
each labelled with its tier because the core-versus-extras split is what decides whether an import can
fail at runtime. Seven external candidates were dropped to fit the budget, cut at the count-2
frequency tier: `mmdc` (3 sites, declared in no manifest — the costliest elision), `pydantic-settings`,
`cairocffi`, `markdown`, and the two `pyobjc` frameworks (1 site each), and `pillow`, which lost on
merit at **0 import sites** despite being declared at `pyproject.toml:27` and advertised at
`README.md:128`. No prior artifact existed, so nothing was inherited; the two findings the graph adds
beyond the environment brief are the unused `pillow` declaration and the fact that every edge out of
`cli`, `device`, and `ocr` toward extras-backed code is a deferred function-local import, which is why
`rmspec` starts with no extras installed.
