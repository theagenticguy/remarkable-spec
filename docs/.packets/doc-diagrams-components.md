---
role: doc-diagrams-components
model: opus
output: "docs/diagrams/architecture/components.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · diagrams/architecture/components.md

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

Produce `docs/diagrams/architecture/components.md`: a single Mermaid `classDiagram` showing the top components of `remarkable-spec` (max 8) with their key methods and has-a / uses relationships.

## 2. Scope

- Create: `docs/diagrams/architecture/components.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: class/struct declarations, module-level functions, the same module roster used by `architecture/module-map.md` (read it if it exists).
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
2. Pick component candidates. A component is a "thing" the system thinks in: a service, a router, a worker pool, a storage layer, a domain-model facade. In OO codebases these are usually classes/structs; in functional codebases they are sets of cohesive functions under one module.
3. Limit to the top 8. Use inbound-reference count (preferred) or LOC / fan-out as the ranking signal.
4. For each component, identify 3–5 methods that the rest of the system actually calls (inbound call count, not "every public method"). Mark methods with `+` prefix in the Mermaid class block.
5. Identify relationships. For each pair of components, walk for usage edges: A calls B's method → `A --> B : <verb>`. Use one-word verbs (`invokes`, `dispatches`, `reads`, `writes`, `consumes`).
6. Draft the Mermaid `classDiagram` block. Cap at 8 classes total. Method labels ≤ 30 chars; class names ≤ 20 chars.
7. Write the file with H1 = `# remarkable-spec · Components`. Body = one Mermaid fence and no prose around it, above the `## See also` footer the cross-link pass writes.

## 5. Output format rules

- H1 = `# remarkable-spec · Components`. No decorative titles.
- No YAML frontmatter on the output file.
- Exactly one Mermaid fence (`` ```mermaid ``) containing a `classDiagram`.
- Maximum 8 classes.
- 3–5 methods per class, prefixed with `+`.
- Relationships labeled with one-word verbs.
- Class names ≤ 20 chars; method labels ≤ 30 chars.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** class/struct definitions and their call sites.
- **Grep** for `class \w+`, `struct \w+`, `interface \w+`, `trait \w+`, or language-equivalent declarations.
- **Glob** to enumerate type-declaration files.
- **Bash** for `jq` over `docs/.repomix/codebase.json` if present.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Codebase is functional, no classes:** pick the top 8 modules from `module-map.md` and treat each as a "class" with 3–5 module-level public functions as "methods." State the substitution in the Work log.
- **More than 8 components qualify:** rank harder. Drop components whose only relationships are to "support" components (logging, metrics, config).
- **A component has fewer than 3 inbound-called methods:** include it only if its presence in the diagram is structurally important; otherwise drop it from the component set.

## 8. Success criteria

- [x] `docs/diagrams/architecture/components.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · Components`.
- [x] Exactly one Mermaid fence containing a `classDiagram`.
- [x] Class count is 3–8.
- [x] Every class has 3–5 method entries, each prefixed with `+`.
- [x] Every relationship label is a one-word verb.
- [x] No class name exceeds 20 characters; no method label exceeds 30 characters.
- [x] No YAML frontmatter on the output.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path).

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

## 9. Anti-goals

- Do not invent classes, methods, or relationships. Every identifier traces to a source read.
- Do not exceed 8 classes; if more matter, the picker hasn't been ruthless enough.
- Do not emit more than one Mermaid block.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

Found while verifying inbound call counts for method selection. Every count below is
"references anywhere in `src/`, excluding the defining file", cross-checked with both
`grep -rn '\.<name>('` and `codegraph callers`.

- **`src/remarkable_spec/sync/db.py:174`** — `get_ocr` has zero call sites outside `db.py`, as do
  `put_ocr` (`:192`), `get_all_ocr` (`:216`), `get_pages` (`:154`), `get_page` (`:162`), and
  `find_changed_pages` (`:319`). `CLAUDE.md:29` states that `rm_hash` "is the cache invalidation key
  for OCR and diagram results", but only the diagram half has live callers
  (`src/remarkable_spec/cli/diagram_cmd.py:223,246`); a reader takes `CLAUDE.md` at face value and
  assumes an OCR result cache is in the request path when the OCR accessors are unreachable code.

- **`src/remarkable_spec/device/sync.py:52`** — `pull_all` has no call site outside `sync.py`, and
  neither does `push_pdf` (`:149`); the only references are the two docstring examples at `:43` and
  `:45`. A reader who follows the class docstring believes `pull_all` is the supported bulk path,
  when the CLI actually routes bulk pulls through `sync_pull` (`:303`) and single documents through
  `pull_document` (`:92`, called at `src/remarkable_spec/cli/device_cmd.py:389`).

- **`src/remarkable_spec/ocr/diagram.py:115`** — `classify_page` has zero references anywhere in
  `src/`, including lazy imports; `PageContentType` (`:31`) exists only to be its return type. The
  CLI's diagram path calls `extract_mermaid_from_rm` (`:174`) directly at
  `src/remarkable_spec/cli/diagram_cmd.py:237`, so a reader who expects a classify-then-extract
  two-step pipeline is reading a stage that never runs.

- **`src/remarkable_spec/models/page.py:126`** — `rm_filename` has zero call sites outside
  `page.py`, as do `metadata_filename` (`:132`), `thumbnail_filename` (`:138`), `all_strokes`
  (`:144`) and `rm_path` (`:148`); the same holds for `Document.name`
  (`src/remarkable_spec/models/document.py:345`), `.is_notebook` (`:351`), `.is_pdf` (`:357`),
  `.is_epub` (`:363`), `.is_folder` (`:369`), `.is_trashed` (`:375`) and `.base_path` (`:379`).
  Four of the `Page` entries are `@computed_field` properties (`src/remarkable_spec/models/page.py:124-146`)
  so they still reach consumers through `model_dump()`, but the `Document` predicates are plain
  properties with no consumer at all — a reader assumes callers branch on `doc.is_pdf` when every
  call site in `src/remarkable_spec/cli/` re-derives the type from raw metadata instead.

- **Grep-only counting under-reports callers, twice in this repo.**
  `src/remarkable_spec/ocr/textract.py:23` (`ocr_image_textract`) looks unreferenced to
  `grep '\bocr_image_textract('` because it is passed as a bare callable to
  `pool.submit(...)` at `src/remarkable_spec/ocr/postprocess.py:133`, inside the
  `ThreadPoolExecutor(max_workers=2)` opened at `:131`. Same shape for `ocr_image`
  (`src/remarkable_spec/ocr/vision.py:63`) at `src/remarkable_spec/ocr/postprocess.py:132`. Any
  downstream packet that ranks or prunes on a paren-form grep will call both dead and be wrong.

- **`src/remarkable_spec/render/engine.py:35`** — `RenderEngine` is an ABC whose single abstract
  method `render_page` (`:44`) has exactly one implementation, `SVGRenderer` (`:75`), and one
  external caller (`src/remarkable_spec/export/svg.py:59`). The abstraction carries no second
  implementor, so a reader plans a new renderer against a seam that has never been exercised.

---

## Work log

### Step 0 — inputs read

- `docs/.packets/_environment.md` read in full (442 lines). Its stale-prior traps taken as binding.
  Facts carried into this packet: eight packages under `src/remarkable_spec/`
  (`docs/.packets/_environment.md:95-97`), measured dependency direction with no cycles (`:131-141`), the
  concurrency-attribution rule (`:309-314`), and the no-tests rule (`:168-176`).
- `docs/.packets/doc-diagrams-components.md` (this packet) read in full before starting.
- Sibling `docs/architecture/module-map.md` does **not** exist yet (`docs/` holds only `.packets/`
  and `.repomix/`, and `git ls-files docs/` returns nothing). No module roster inherited; the module
  roster used here is derived from `codegraph files --format grouped` and the brief's § 2.
- Scratch directory created at `/tmp/doc-diagrams-components/`.

### Step 1 — prior-artifact check (Process step 1)

Ran `ls -la docs/diagrams/architecture/components.md` → `No such file or directory (os error 2)`.
Ran `git log -1 --format=%cs -- docs/diagrams/architecture/components.md` → empty output.
`git ls-files docs/` → empty; the repo has 67 tracked files and none are under `docs/`.

**Finding: there is no prior artifact.** This is a first run over a repo with no `docs/` tree, so
the whole staleness class the packet warns about (citations off by hundreds of lines, LOC figures
matching neither `wc -l` nor the flattened pack, fabricated diagram edges, inverted rule polarity,
citations into gitignored build artifacts) is **not applicable** — nothing was inherited and no
claim was carried over. Every citation in the output is read fresh from source in this run.

### Step 2 — component candidate ranking

Class inventory pulled with
`codegraph query "" -k class --json` → 51 classes, dumped to `/tmp/doc-diagrams-components/classes.json`.

Inbound-reference counts via `codegraph callers <sym> -l 300 --json | jq '.callers | length'`.
Note the shape trap: the payload is an object `{symbol, callers}`, so `jq 'length'` returns 2 for
every symbol; `.callers | length` is the correct expression. First attempt hit this and was redone.

Classes, by caller count:

```text
14 Page              5 Layer            3 SVGRenderer      2 Template
 7 ScreenSpec        5 DeviceConnection 3 Pen              1 RenderEngine
 7 PenColor          4 ResolvedDocument 3 MermaidResult    1 PenRenderer
 6 SyncDocument      4 Point            3 DocumentMetadata 1 BasePenRenderer
 6 Stroke            4 OCRResult        3 ContentInfo      0 RmspecSettings
 5 SyncManager       4 OCRCacheEntry
 5 Palette           4 Document
                     4 DevicePaths
                     3 SyncDB
```

Module-level functions, by caller count: `parse_rm_file` 11, `export_svg` 6, `render_rm_to_png` 3,
`transcribe_page` 2, `export_png` 2, `export_pdf` 2, `transcribe_rm` 1, `render_page` 1,
`load_document` 1, `extract_mermaid` 1.

Counts include `__init__.py` re-export nodes, so they over-report slightly and were used only for
ranking, never cited as a fact. Every edge in the diagram was then confirmed at an import or call
site, per the brief's warning at `docs/.packets/_environment.md:74-80`.

**Mixed OO/functional substitution.** This codebase is not purely OO: the `formats` and `ocr`
packages expose cohesive module-level function sets rather than classes, and `parse_rm_file` is the
single most-referenced symbol in the repo (11 callers, more than any class except `Page`). Fallback
path 1 in section 7 is written for a wholly functional codebase; the substitution applied here is
partial — six components are classes, two are module-level function sets (`RmParser` for
`formats/rm_file.py`, `OcrPipeline` for `ocr/pipeline.py` plus `ocr/postprocess.py`). Stated here
per that fallback's instruction.

### Step 3 — symbol maps read

`codegraph node -f <path> --symbols-only` for `device/sync.py`, `sync/db.py`,
`device/connection.py`, `render/engine.py`; then `Read` for the remaining files, listed under
Validation. Method signatures and line numbers in the diagram come from these reads.

### Step 4 — method selection, and the finding that reshaped the component set

Process step 4 requires methods "the rest of the system actually calls (inbound call count, not
every public method)". Enforcing that literally changed which components qualify.

**The `models` classes have almost no called methods.** A grep for each convenience method across
all of `src/`, excluding its own defining file, returned zero external call sites for
`Page.rm_filename` (`src/remarkable_spec/models/page.py:126`), `.metadata_filename` (`:132`),
`.thumbnail_filename` (`:138`), `.all_strokes` (`:144`), `.rm_path` (`:148`), and likewise for
`Document.is_notebook` (`src/remarkable_spec/models/document.py:351`), `.is_pdf` (`:357`),
`.is_epub` (`:363`), `.is_folder` (`:369`), `.is_trashed` (`:375`), `.base_path` (`:379`). The
domain models are consumed as *types and data*, not through their methods.

For `Page` the four filename/stroke accessors are `@computed_field` properties
(`src/remarkable_spec/models/page.py:124-146`), so Pydantic emits them into every `model_dump()`
rather than any call site invoking them — which is why the grep is empty and why they are still
real consumed surface. `rm_path` (`:148`) is a plain method.

Consequence:

- `Page` is kept under **fallback path 3** ("include it only if its presence in the diagram is
  structurally important"). It is the single most-referenced symbol in the repo — 14 inbound
  references, imported by 12 files per `codegraph node -f src/remarkable_spec/models/page.py
  --symbols-only` — and it is the object that flows through parse, render, export, and OCR. Its
  five method entries are public API (`src/remarkable_spec/models/page.py:126,132,138,144,148`),
  four of them serialized computed fields.
- `Document` is **dropped**. Same zero-called-method profile as `Page` but a third of the inbound
  references (4), and every edge it would carry duplicates one `Page` already carries.

**`SyncDB` methods were re-picked from call sites, not from the public surface.** `get_ocr`
(`src/remarkable_spec/sync/db.py:174`), `put_ocr` (`:192`), `get_all_ocr` (`:216`),
`find_changed_pages` (`:319`), `get_pages` (`:154`) and `get_page` (`:162`) have **zero** call
sites outside `db.py` — recorded under Out-of-scope findings. The five entries in the diagram are
the ones with confirmed external callers.

**`SVGRenderer` cannot stand alone as a class component.** Its only public method is `render_page`
(`src/remarkable_spec/render/engine.py:91`); `_render_stroke` (`:232`), `_embed_template` (`:317`),
and `_embed_raster_background` (`:352`) are private, so listing them with a `+` visibility marker
would misstate the source. Rather than drop rendering from a components diagram of a rendering
library, the `render` package is drawn as a subsystem component per **fallback path 1**, with the
four operations the rest of the system invokes on it: `render_page` (`:91`), `get_pen_renderer`
(`src/remarkable_spec/render/pens.py:437`), `get_rgb`
(`src/remarkable_spec/render/palette.py:41`), `rasterize_pdf_page`
(`src/remarkable_spec/render/pdf_bg.py:15`). Same treatment for `formats`, `export`, and `ocr`,
which expose module-level functions rather than classes.

**`WebAPI` was dropped under fallback path 2.** `src/remarkable_spec/device/web_api.py:37` has
eight externally-callable methods and would qualify on method count, but it has no edge to any
other component in the set — `codegraph node -f src/remarkable_spec/device/web_api.py
--symbols-only` reports its consumers as `cli/device_cmd.py`, `cli/sync_cmd.py`, and
`ocr/diagram.py`, none of which is a component here. An isolated node earns no slot.

Also dropped: `Palette` (`src/remarkable_spec/render/palette.py:26`) and `PenRenderer`
(`src/remarkable_spec/render/pens.py:42`), both folded into the `render` subsystem;
`DevicePaths` (`src/remarkable_spec/device/paths.py:13`), a constants holder;
`RmspecSettings` (`src/remarkable_spec/cli/_util.py:13`), zero inbound callers; the 12 concrete
pen-renderer subclasses in `src/remarkable_spec/render/pens.py:136-434`, all reached only through
`get_pen_renderer` (`:437`).

### Step 5 — final component set, 8 of 8 slots

| Component | Kind | Anchor |
| --- | --- | --- |
| `formats` | subsystem, module-level functions | `src/remarkable_spec/formats/__init__.py:21-35` |
| `Page` | class | `src/remarkable_spec/models/page.py:99` |
| `render` | subsystem, mixed | `src/remarkable_spec/render/__init__.py:37-54` |
| `export` | subsystem, module-level functions | `src/remarkable_spec/export/__init__.py:20-24` |
| `ocr` | subsystem, module-level functions | `src/remarkable_spec/ocr/pipeline.py:25` |
| `SyncManager` | class | `src/remarkable_spec/device/sync.py:31` |
| `DeviceConnection` | class | `src/remarkable_spec/device/connection.py:38` |
| `SyncDB` | class | `src/remarkable_spec/sync/db.py:26` |

### Step 6 — every edge confirmed at an import or a call site

Per `docs/.packets/_environment.md:74-80`, no edge was drawn from a name match alone. Confirmations:

| Edge | Import site | Call or construction site |
| --- | --- | --- |
| `formats --> Page` produces | `src/remarkable_spec/formats/document_loader.py:27` | `:104` constructs `Page(...)` |
| `render --> Page` reads | `src/remarkable_spec/render/engine.py:21` | `:91` `render_page(page: Page, ...)` |
| `export --> Page` reads | `src/remarkable_spec/export/svg.py:12` | `:19` `export_svg(page: Page, ...)` |
| `export --> render` invokes | `src/remarkable_spec/export/svg.py:14` | `:58-59` builds `SVGRenderer()` then `renderer.render_page(...)` |
| `ocr --> formats` invokes | `src/remarkable_spec/ocr/pipeline.py:47` | `:58` `layers = parse_rm_file(rm_path)` |
| `ocr --> export` invokes | `src/remarkable_spec/ocr/pipeline.py:46` | `:66` `export_svg(...)` |
| `ocr --> Page` produces | `src/remarkable_spec/ocr/pipeline.py:48` | `:59` `page = Page(uuid=uuid4(), layers=layers)` |
| `SyncManager --> DeviceConnection` invokes | `src/remarkable_spec/device/sync.py:22` | `:72` `connection.list_dir(...)`, `:84,90,116,128,142,274` `get_file`, `:204,219,220,526,546,547` `put_file`, `:226,229,530,532,552` `execute` |
| `SyncManager --> SyncDB` writes | `src/remarkable_spec/device/sync.py:28` (`TYPE_CHECKING`) | `:384,555` `db.upsert_document(...)`, `:397` `upsert_page`, `:408,423,563` `log_sync` |

**Two collision traps avoided.** `grep '\.execute('` returns 26 hits in
`src/remarkable_spec/sync/db.py` and `src/remarkable_spec/sync/migrations.py` — those are
`sqlite3.Cursor.execute`, not `DeviceConnection.execute`
(`src/remarkable_spec/device/connection.py:140`); `src/remarkable_spec/sync/db.py:52` is
`sqlite3.connect`, not `DeviceConnection.connect` (`src/remarkable_spec/device/connection.py:81`).
Likewise `list_documents` hits at `src/remarkable_spec/device/web_api.py:53,97,220` are
`WebAPI.list_documents` (`:70`), a different method from `SyncDB.list_documents`
(`src/remarkable_spec/sync/db.py:120`). No edge was drawn from either.

**Deliberately omitted edge:** `ocr --> SyncDB`. Nothing under `src/remarkable_spec/ocr/` imports
`sync`; the diagram cache is read and written from the CLI layer at
`src/remarkable_spec/cli/diagram_cmd.py:223,246`. Drawing `ocr --> SyncDB` would have been a
fabricated edge.

### Step 7 — citation placement inside the Mermaid fence

Section 5 of this packet requires the body to be one Mermaid fence with no prose around it, while
the orchestrator's global criteria require a `path:LOC` citation for every factual claim. Both are
satisfied by carrying the citations as Mermaid `note for <Class>` entries inside the single fence,
so the page has zero prose and every component still names its source. `mmdc` 11.16.0 is present at
`/Users/lalsaado/.local/share/mise/installs/npm-mermaid-js-mermaid-cli/11.16.0/bin/mmdc`, so the
syntax was compiled before shipping rather than assumed — see Validation.

## Validation

### 1. Mechanical check of every countable property

Script at `/tmp/doc-diagrams-components/validate.py` (PEP 723, stdlib only). It checks: no YAML
frontmatter, exact H1 string, single H1, zero content H2s, exactly one fence pair, fence is
`mermaid`, first fence line is `classDiagram`, class count in 3–8, class-name length cap, 3–5 method
entries per class, every entry `+`-prefixed, every entry ≤30 chars, every relationship parsing to
`src --> dst : verb` with both endpoints declared and the verb a single lowercase word, every
backticked citation resolving to a real file with an in-range line, every shorthand `:LOC` preceded
by a full path in the same row, `git check-ignore` and `git ls-files --error-unmatch` per cited
path, no emoji, no filler adverbs, and no bare brace outside a fence or inline code span.

```console
$ uv run --no-project /tmp/doc-diagrams-components/validate.py; echo "EXIT=$?"
PASS 221

0 failures, 43 citations checked, 8 classes
EXIT=0
```

43 citations = 23 full-path plus 20 shorthand `:LOC`.

**First run of the script counted only 23** because the citation regex was `` `([^`]+?):(\d+)` ``,
whose `+` requires at least one character before the colon, so it never matched a bare shorthand of
the colon-plus-digits form. Changed to `` `([^`]*?):(\d+)` `` and re-ran; the 20 shorthands then
resolved against the preceding full path and all passed. Recording this because a validator that
silently skips the class of citation it is meant to police reports green for the wrong reason.

### 2. Negative controls — proving the validator has teeth

Three mutations of the shipped file, each fed to the same script with only `DOC` repointed:

```console
$ sed 's|src/remarkable_spec/sync/db.py:26|src/remarkable_spec/sync/db.py:9999|' ... 
FAIL line 89: src/remarkable_spec/sync/db.py:9999- out of range (file has 365)
1 failures, 43 citations checked, 8 classes
NEG_EXIT=1

$ sed 's|src/remarkable_spec/sync/db.py:26|docs/.repomix/codebase.json:1|' ...
FAIL line 89: docs/.repomix/codebase.json is untracked
FAIL line 89: docs/.repomix/codebase.json:132- out of range (file has 79)
... 6 failures

$ sed 's|`src/remarkable_spec/device/connection.py:38`<br/>connect|connect|' ...
FAIL line 87: orphan shorthand `:81`
FAIL line 87: shorthand `:81` preceded by a full path in same row
... 10 failures
```

An invented line number, a citation into the flattened pack, and an orphaned shorthand each fail the
run. The clean file's PASS is therefore not vacuous.

### 3. Gitignore and tracking sweep over the cited-path set

```console
$ grep -o '`[a-z][a-zA-Z0-9_/.]*\.py:[0-9]*' docs/diagrams/architecture/components.md \
    | sed 's/^`//;s/:[0-9]*$//' | sort -u | wc -l
23
$ git check-ignore --stdin < /tmp/doc-diagrams-components/cited_paths.txt; echo "exit=$?"
exit=1
$ git ls-files --error-unmatch $(cat /tmp/doc-diagrams-components/cited_paths.txt) >/dev/null \
    && echo "all cited paths tracked"
all cited paths tracked
```

`git check-ignore` exit 1 means no path matched an ignore rule. Nothing under `dist/`, `.venv/`,
`.codegraph/`, `.pytest_cache/`, `.ruff_cache/`, `.erpaval/`, `.claude/`, or `docs/.repomix/` is
cited. No path under `tests/` is cited at all.

### 4. The Mermaid actually compiles

The fence was extracted to `/tmp/doc-diagrams-components/components.mmd` and compiled with
`mmdc` 11.16.0 at
`/Users/lalsaado/.local/share/mise/installs/npm-mermaid-js-mermaid-cli/11.16.0/bin/mmdc`:

```console
$ mmdc -i components.mmd -o components.svg
Generating single mermaid chart
MMDC_EXIT=0
```

A probe run first (`/tmp/doc-diagrams-components/probe.mmd`) confirmed that `note for <Class>` text
tolerates backticks, colons, forward slashes, `<br/>`, and an em-dash, and that the note bodies
reach the rendered output — `grep -o 'src/remarkable_spec/[a-z_/.]*:[0-9-]*' probe.svg` returned the
cited paths. Lowercase class names (`formats`, `render`, `export`, `ocr`) are not Mermaid reserved
words and compiled without complaint. A 2400px PNG was rendered and inspected visually: 8 class
boxes, 9 labeled edges, 8 note boxes, no overlap that hides a label, all citation text legible.

### 4a. Citations in this packet, checked the same way

The same resolver was pointed at this packet file, with the "last full path" reset at each H2 so it
matches the shorthand rule's section scope rather than a per-line scope:

```console
OK 136 resolved (65 shorthand)
FAIL 99: orphan shorthand `:541`
FAIL 99: orphan shorthand `:1092`
```

136 of 138 resolve to a tracked, non-ignored file at an in-range line. The two reports at line 99 are
**not defects**: that line is the orchestrator-authored Process step 1, which uses "a handler cited
at `:541` now living at `:1092`" as an illustration of what a stale citation looks like. They are
prose examples with no referent by design, and rewriting the orchestrator's instruction text is out
of scope. Flagged here so the tree-wide cross-link pass does not read them as a finding.

Three real citation defects were found by this pass and fixed: `_environment.md` was cited three
times by bare filename rather than by its repo-relative path
`docs/.packets/_environment.md:74-80,95-97`, which no validator or reader could resolve; and one
shorthand in Validation §5 followed a path given without a line number, now
`src/remarkable_spec/ocr/pipeline.py:25`.

### 5. Judgment spot-checks, not string checks

Reserved for the questions a script cannot answer:

- **Is each edge real?** All nine confirmed at both the import line and a call or construction site
  before being drawn — table in Work log step 6. The two name-collision traps the environment brief
  flags (`.execute`, `list_documents`) were hit during this pass and neither produced an edge.
- **Is the parallelism attributed correctly?** The diagram places no concurrency in
  `src/remarkable_spec/ocr/pipeline.py:25`. `render_rm_to_png` (`:25`) and `transcribe_rm` (`:86`) sit
  in the `ocr` component as straight-line entry points; the `ThreadPoolExecutor(max_workers=2)` lives
  at `src/remarkable_spec/ocr/postprocess.py:131` and `transcribe_page`
  (`src/remarkable_spec/ocr/postprocess.py:110`) is the entry point that owns it. Consistent with
  orchestrator directive 5.
- **Any test claim?** None. The page contains no prose, so no coverage or verification wording is
  possible; no `tests/` path is cited.
- **Braces?** Every brace in the output is inside the single Mermaid fence, which orchestrator
  directive 3 declares safe. The validator's brace check confirms zero bare braces outside it. No
  reMarkable UUID-placeholder filename spelling was reproduced anywhere.

## Summary

Shipped `docs/diagrams/architecture/components.md`: an H1, one Mermaid `classDiagram`, no prose, no
frontmatter. Eight components, 36 method entries, 9 labeled edges, and 43 verified citations carried
as Mermaid `note for` entries inside the fence so the page satisfies both the no-prose rule and the
cite-everything rule. The set is deliberately mixed: four classes with real inbound-called methods —
`SyncManager` (`src/remarkable_spec/device/sync.py:31`), `DeviceConnection`
(`src/remarkable_spec/device/connection.py:38`), `SyncDB` (`src/remarkable_spec/sync/db.py:26`), and
`Page` (`src/remarkable_spec/models/page.py:99`) — plus four subsystem components for the packages
that expose module-level functions rather than classes: `formats`
(`src/remarkable_spec/formats/__init__.py:21-35`), `render`
(`src/remarkable_spec/render/__init__.py:37-54`), `export`
(`src/remarkable_spec/export/__init__.py:20-24`), and `ocr`
(`src/remarkable_spec/ocr/pipeline.py:25`). The ranking pass forced two non-obvious calls. `Document`
(`src/remarkable_spec/models/document.py:307`) and the twelve concrete pen renderers
(`src/remarkable_spec/render/pens.py:136-434`) were dropped because their methods have no external
callers and their edges duplicate ones `Page` and `render` already carry. `WebAPI`
(`src/remarkable_spec/device/web_api.py:37`) was dropped under fallback path 2 despite eight
callable methods: its consumers are all CLI modules, so it would have been an isolated node.
`SVGRenderer` (`src/remarkable_spec/render/engine.py:75`) could not stand alone — `render_page`
(`:91`) is its only public method — which is why the render layer is drawn as a subsystem rather than
omitted from a components diagram of a rendering library. No prior artifact existed; this repo had no
`docs/` tree at all before this run, so every line traces to a source file read in this session.
