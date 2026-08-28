---
role: doc-behavior-state-machines
model: opus
output: "docs/behavior/state-machines.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · behavior/state-machines.md

> **Conditional packet.** Only seed if `remarkable-spec` contains ≥ 2 state machines — entity types with an explicit `status`/`state` field driven by enumerated transitions, an XState/Stateful-style declaration, or any equivalent state-driven pattern. Verify before spawning.

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

Produce `docs/behavior/state-machines.md`: one content H2 per state machine in `remarkable-spec`, alphabetically ordered, each containing exactly one Mermaid `stateDiagram-v2` block plus a `Defined at: <backtick path:LOC>` citation. State names and transition labels match source identifiers verbatim.

## 2. Scope

- Create: `docs/behavior/state-machines.md`
- Do not touch: `docs/behavior/processes.md`, any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The codebase. Especially: enum/literal-union declarations for `status` / `state` fields, state-machine library declarations (XState, Stateful, custom), transition functions named `transition` / `setStatus` / `advance`.
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

### Conditional gate for this packet

This packet was seeded speculatively and **omitting its output is a legitimate success case**. This
is a Python CLI with six `enum` classes and no state-machine library, and several of those enums are
classifications rather than machines: `PageContentType` (`src/remarkable_spec/ocr/diagram.py:31`),
`DocumentType` and `FileType` (`src/remarkable_spec/models/document.py:27,39`), `PenColor` and
`HighlightColor` (`src/remarkable_spec/models/color.py:17,44`), `PenType`
(`src/remarkable_spec/models/pen.py:17`). A closed value set that code only reads is not a state
machine.

Emit `docs/behavior/state-machines.md` **only if you can document at least two machines where each
has at least 3 states and at least 2 transitions traced to cited source.** A transition means code
that moves an entity from one state to another, not a value that is merely branched on.

Two candidates, to be verified rather than assumed:

- **Tracked-document sync lifecycle.** `SyncManager.sync_status` classifies each document as
  `new_on_device`, `modified_on_device`, or `deleted_on_device`
  (`src/remarkable_spec/device/sync.py:233-301`), and `sync_pull` acts on that classification
  (`src/remarkable_spec/device/sync.py:303`ff) by upserting or deleting rows through
  `src/remarkable_spec/sync/db.py`. The persisted row is the state; the classification plus the
  action is the transition. Check whether an untracked document, a tracked-and-current document, and
  a tracked-then-deleted document really are distinguishable states in the DB.
- **The `rm_hash`-keyed cache lifecycle.** `OCRCacheEntry` and `DiagramCacheEntry`
  (`src/remarkable_spec/sync/models.py:63-107`) are keyed on the SHA-256 of the `.rm` bytes, so an
  entry moves miss to computed to cached, and is stranded when the page is edited and the hash
  changes. Confirm against the cache read/write methods in `src/remarkable_spec/sync/db.py` and the
  hasher at `src/remarkable_spec/sync/hasher.py`, and establish whether stale rows are ever deleted
  or merely orphaned — that answer is the interesting part either way.

If fewer than two candidates clear the bar: **do not write the file and do not write a placeholder.**
Delete the output path if you created it, record the omission and your reasoning in the Work log,
tick the Success criteria that still apply, note the ones voided by the omission, and set
`status: COMPLETE`. Report the omission in your Summary so the orchestrator does not re-dispatch you.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Enumerate state machines. Search for: type declarations of the form `type Status = 'pending' | 'running' | 'done'` (or equivalent), `enum Status` definitions, state-machine library calls (`createMachine`, `Statefulor`-style fluent builders), explicit transition tables.
3. For each candidate, locate the state and transition declarations. Read the definition file around `start_line..start_line + 60`. Confirm the machine has at least 2 states and at least 1 transition; otherwise discard.
4. Resolve every transition. A transition is a `from → to` pair triggered by an event name (function call, message type, condition). The event name is the Mermaid edge label, verbatim from source.
5. Identify entry state and terminal states. Most machines have one entry and one or two terminals; if source has no explicit terminal, note the absence rather than invent one.
6. Draft each content H2: `## <machine-name>`, then exactly one fenced Mermaid block containing `stateDiagram-v2`, then a `Defined at: <backtick path:LOC>` line on its own.
7. Order content H2s alphabetically by machine name.
8. Write `docs/behavior/state-machines.md` with H1 = `# remarkable-spec · State machines`.

## 5. Output format rules

- H1 = `# remarkable-spec · State machines`. No decorative titles.
- No YAML frontmatter on the output file.
- One content H2 per state machine. Content H2s in alphabetical order.
- Each content H2 contains exactly one Mermaid fence with `stateDiagram-v2`. Not `stateDiagram` (v1), not `flowchart`.
- Each content H2 ends with `Defined at: <backtick path:LOC>` on its own line.
- Mermaid state names match source identifiers **verbatim**; transition labels match source event names **verbatim**.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** definition files and any transition-handler files.
- **Grep** for: `type \w+ =.*\|.*\|` (union types), `enum \w+\s*{`, `createMachine\(`, `Statefulor`-style patterns, function names `transition`/`setStatus`/`advance`/`fire`.
- **Glob** to enumerate types/state-machine declaration locations.
- **Bash** for `jq` over `docs/.repomix/codebase.json`.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Fewer than 2 machines found** (the conditional precondition fails): write the gap to the Work log, flip `status` to `BLOCKED`, and stop. The orchestrator will prune this packet from the tree.
- **A machine has no explicit terminal state in source:** draw the diagram without `--> [*]` and note the absence in the Work log. Do not invent a terminal.
- **Transitions are scattered** (state-change calls in many handler files): cite the definition site for the type/enum and the *first* transition site you find; list additional transition sites as bullets under the diagram if useful. Do not fabricate a centralized transition table that doesn't exist in source.
- **A machine is implicit** (no type union, but a `status` field has well-known values via runtime checks): include it only if the values can be confirmed from ≥ 2 transition sites. Otherwise skip and note in the Work log.

## 8. Success criteria

**Six criteria are VOIDED by the conditional gate in section 3a not being met** (one machine clears
the bar, two are required). Each void carries its one-line reason. The remaining four are ticked.

- [ ] ~~`docs/behavior/state-machines.md` exists on disk.~~ — **VOIDED:** gate not met, output
      deliberately omitted; verified absent in Validation V2.
- [ ] ~~H1 line reads `# remarkable-spec · State machines`.~~ — **VOIDED:** no output file to carry an H1.
- [ ] ~~At least 2 content H2 entries exist (matches the conditional precondition).~~ — **VOIDED:**
      this is the precondition that failed; exactly 1 machine clears the ≥3-states/≥2-transitions bar.
- [ ] ~~Every content H2 contains exactly one `stateDiagram-v2` Mermaid fence.~~ — **VOIDED:** no
      content H2 exists.
- [ ] ~~No content H2 contains a second Mermaid block or a non-Mermaid diagram.~~ — **VOIDED:** no
      content H2 exists.
- [ ] ~~Every content H2 ends with a `Defined at: <backtick path:LOC>` line.~~ — **VOIDED:** no
      content H2 exists.
- [ ] ~~A script confirms every state name and every transition label in every diagram appears
      literally in the file named by that H2's `Defined at:` citation.~~ — **VOIDED:** no diagram was
      drawn. The equivalent check was still run for the one qualifying candidate and pasted in
      Validation V4 (all 9 identifiers verbatim in `src/remarkable_spec/device/sync.py`), which is
      what establishes the machine was excluded on the count and not on a naming technicality.
- [x] One machine is read end-to-end at its cited span to confirm the transitions themselves —
      direction, trigger, terminal — match source. **Done:** `src/remarkable_spec/device/sync.py`
      read in full, transitions traced by hand in Validation V4, including the two findings a string
      check cannot reach (FK cascade on delete; `deleted_on_device` reported in neither return bucket).
- [x] No YAML frontmatter on the output. **Satisfied:** no output file was written, so no frontmatter
      was written.
- [x] Prior-artifact check ran: the output path held no file. At check time `docs/behavior/` did not
      exist at all, and `git log -1 --format=%cs -- docs/behavior/state-machines.md` is empty (never
      tracked). Nothing was inherited, so no claim needed re-verification. Recorded in Work log § 1.
      The directory exists now only because a sibling packet wrote `docs/behavior/processes.md` into
      it during the same fan-out; the state-machines path is still empty.
- [x] The Work log records that **no prior version existed** — see Work log § 1.
- [x] No citation resolves into a generated or gitignored path. `git check-ignore --stdin` over all
      15 distinct cited paths returned 0 hits; command and output in Validation V1. All 29 full and 24
      shorthand citations resolve to a real file and an in-range line, exit 0.

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent state names or transition labels. Every identifier must come from a source read.
- Do not emit more than one Mermaid block per content H2.
- Do not use `stateDiagram` (v1); use `stateDiagram-v2` only.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `src/remarkable_spec/device/sync.py:251-253` — the `sync_status` docstring says the returned
  `change_type` is one of `new_on_device`, `modified_on_device`, `deleted_local`, but the code emits
  `deleted_on_device` (`:298`) and `sync_pull` matches on `deleted_on_device` (`:333`); the CLI's
  label map agrees with the code (`src/remarkable_spec/cli/sync_cmd.py:160`). `deleted_local` appears
  nowhere else in `src/`. A reader trusting the docstring writes a consumer that branches on
  `deleted_local`, silently never matches, and treats device-side deletions as no-ops.
- `src/remarkable_spec/sync/db.py:174-233` — `get_ocr`, `put_ocr`, and `get_all_ocr` have **zero
  callers** anywhere in `src/` (confirmed by grep and by CodeGraph `callers`, which returns `[]` for
  all three). The live OCR cache is a filesystem sidecar instead:
  `src/remarkable_spec/cli/search_cmd.py:196-197` reads `rm_path.with_suffix(".ocr.txt")` and `:206`
  writes it. A reader assumes the documented `rm_hash`-keyed OCR cache in the sync DB is the one in
  use, and will not find their cached text there; the only writer of `ocr_cache` is the one-shot
  legacy importer `migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:141-145`).
- `src/remarkable_spec/sync/db.py:319` — `find_changed_pages` also has zero callers, so the
  `rm_hash`-diff change detection it implements is never exercised by any command. A reader
  reasonably infers per-page incremental sync exists; `sync_pull` in fact re-pulls every page of any
  changed document (`src/remarkable_spec/device/sync.py:388-405`).
- `src/remarkable_spec/sync/models.py:115-117` — `SyncLogEntry.status` documents four values
  (`'ok'`, `'conflict'`, `'error'`, `'skipped'`) but only `"ok"` and `"error"` are ever assigned
  (`src/remarkable_spec/device/sync.py:414,428,568`,
  `src/remarkable_spec/cli/device_cmd.py:404,432`). A reader building a report or filter on
  `status == 'conflict'` or `status == 'skipped'` gets permanently empty results. Note that
  `sync_pull` does maintain a `skipped` list in memory (`src/remarkable_spec/device/sync.py:330,432`)
  and logs those rows as `"error"`, so the concept exists but the label never reaches the DB.
- `src/remarkable_spec/sync/migrations.py:49-74` — neither `ocr_cache` nor `diagram_cache` declares a
  foreign key or `ON DELETE CASCADE` on `rm_hash`, and no code path deletes from either table, so
  cache rows outlive the pages that produced them forever. A reader who deletes a document expects
  its derived caches to go with it (they do not) and will not find the unbounded-growth behavior
  documented anywhere.
- `docs/.packets/doc-behavior-state-machines.md:169` (this packet's own section 7) — its first
  fallback path says to set `status: BLOCKED` when fewer than 2 machines are found, which directly
  contradicts its section 3a conditional gate (`:129-132`) instructing `status: COMPLETE` for the
  same condition. A future agent following section 7 marks a legitimate success as blocked and
  invites re-dispatch.

---

## Work log

### 1. Prior-artifact check (Process step 1)

- `docs/behavior/` does not exist. `ls -la docs/behavior/` returned `No such file or directory (os error 2)`.
  `test -f docs/behavior/state-machines.md` returned `NO`. `git log -1 --format=%cs -- docs/behavior/state-machines.md`
  returned empty output (path never tracked).
- **There is no prior artifact.** This is a first run over a repo with no `docs/` tree beyond
  `docs/.packets/` and `docs/.repomix/`. Nothing was inherited, so nothing could be stale; the
  "what the prior artifact got wrong" question has the answer **no prior version existed**.
- Read the shared environment brief `docs/.packets/_environment.md` in full first. Its stale-prior
  traps are loaded: no tests, no ORM/Alembic, `invoke_model` not `converse`, cyclopts 4.6.0, the one
  `ThreadPoolExecutor` lives in `ocr/postprocess.py` not `ocr/pipeline.py`.
- Read `src/remarkable_spec/sync/models.py` end-to-end (120 lines per `wc -l`). Noted a third possible machine
  candidate not named in the packet: `SyncLogEntry.status` with the documented closed set
  `'ok'`, `'conflict'`, `'error'`, `'skipped'` (`src/remarkable_spec/sync/models.py:115-117`).

### 2. Enumerating state machines (Process steps 2–3)

Searches run (all from the repo root, all offline and free):

```bash
grep -rn "Enum" src/ --include='*.py'
grep -rn "Literal\[" src/ --include='*.py'
grep -rn "def \(transition\|set_status\|setStatus\|advance\|fire\|next_state\|_state\)" src/ --include='*.py'
grep -rn "createMachine\|StateMachine\|state_machine" src/ --include='*.py'
grep -rn "status=\|status ==\|\"status\"\|'status'" src/ --include='*.py'
grep -rn "DELETE FROM\|DROP TABLE\|VACUUM" src/ --include='*.py'
grep -rn "ocr_cache\|diagram_cache" src/ --include='*.py'
```

Results:

- **Six `enum` classes, exactly the six the gate names**, no more: `PageContentType`
  (`src/remarkable_spec/ocr/diagram.py:31`), `PenColor` (`src/remarkable_spec/models/color.py:17`),
  `HighlightColor` (`:44`), `DocumentType` (`src/remarkable_spec/models/document.py:27`), `FileType`
  (`:39`), `PenType` (`src/remarkable_spec/models/pen.py:17`). All six are `enum.Enum`/`enum.IntEnum`
  classifications that code only reads and branches on. None has a transition.
- **Zero `Literal[...]` string-union declarations** anywhere in `src/`. The grep returns no lines.
- **No state-machine library and no transition function.** `createMachine`, `StateMachine`,
  `state_machine` return nothing (exit 1); no `def transition`, `def set_status`, `def advance`,
  `def fire`, or `def next_state` exists.
- **Only two `status` literals are ever written by code**: `status="ok"`
  (`src/remarkable_spec/device/sync.py:414,568`, `src/remarkable_spec/cli/device_cmd.py:432`) and
  `status="error"` (`src/remarkable_spec/device/sync.py:428`,
  `src/remarkable_spec/cli/device_cmd.py:404`). `SyncLogEntry.status` documents four values
  (`src/remarkable_spec/sync/models.py:115-117`) but `'conflict'` and `'skipped'` are never assigned.
- **The only `DELETE` in the whole codebase** is `DELETE FROM documents WHERE doc_uuid = ?`
  (`src/remarkable_spec/sync/db.py:127`). No `DROP TABLE`, no `VACUUM`.

### 3. Candidate adjudication against the conditional gate

The gate requires **≥ 2 machines, each with ≥ 3 states and ≥ 2 transitions traced to cited source**,
and output format rule 5 requires every state name and transition label to be **verbatim** from the
file named in that H2's `Defined at:` line.

**Candidate 1 — tracked-document sync lifecycle. CLEARS the bar.**
Read `src/remarkable_spec/device/sync.py` end-to-end (573 lines per `wc -l`) and
`src/remarkable_spec/sync/db.py` end-to-end (365 lines per `wc -l`).
`SyncManager.sync_status` (`src/remarkable_spec/device/sync.py:233-301`) emits one of three literal
change types — `new_on_device` (`:290`), `modified_on_device` (`:292`), `deleted_on_device` (`:298`) —
and `SyncManager.sync_pull` (`:303-435`) acts on each: `db.delete_document` for the deleted case
(`:334`), `pull_document` + `db.upsert_document` + `db.upsert_page` + `db.log_sync` for the other two
(`:339,384,397,408`), and on exception a `log_sync(status="error")` plus an append to `skipped`
(`:420-433`). Terminal buckets `pulled` (`:329`) and `skipped` (`:330`) are declared literally.
Every state name and edge label I need appears literally in that one file. ≥ 3 states, ≥ 2
transitions, single `Defined at:` file. **Verified, drawable.**

**Candidate 2 — the `rm_hash`-keyed cache lifecycle. FAILS the bar.** Two independent reasons, both
verified rather than assumed:

1. **There is no transition out of the cached state — ever.** `ocr_cache` and `diagram_cache` are
   insert-only. `put_ocr` (`src/remarkable_spec/sync/db.py:192-214`) and `put_diagram` (`:253-274`)
   are the only writers, both `INSERT ... ON CONFLICT ... DO UPDATE`. No `DELETE` targets either
   table (the single `DELETE` in the codebase hits `documents`, `:127`). Neither table declares a
   foreign key or `ON DELETE CASCADE`: `ocr_cache` (`src/remarkable_spec/sync/migrations.py:49-59`)
   and `diagram_cache` (`:64-72`) carry only a bare `rm_hash TEXT NOT NULL` column, while `pages` is
   the one table that cascades (`:37`, `doc_uuid ... REFERENCES documents(doc_uuid) ON DELETE
   CASCADE`). There is no TTL column and no eviction path. So the answer to the gate's open
   question — "are stale rows ever deleted or merely orphaned" — is **merely orphaned, permanently**.
   That yields 2 states (row absent, row present) and 1 transition (`put_*`), against a required 3
   and 2.
2. **No verbatim state vocabulary exists to name it with.** `src/remarkable_spec/sync/db.py` contains
   no `miss`, `hit`, `stale`, `orphaned`, or `evicted` identifier; the word "cached" appears only in
   docstrings (`src/remarkable_spec/sync/db.py:175,217,238`). Drawing `miss --> cached --> orphaned`
   would invent all three state
   names, violating anti-goal 1 and failing the scripted verbatim check.

**Candidate 3 (found by me, not in the gate) — `SyncLogEntry.status`. FAILS the bar.** The
`sync_log` table is append-only: `log_sync` issues a bare `INSERT` with no `ON CONFLICT` and no
`UPDATE` (`src/remarkable_spec/sync/db.py:278-296`), and no `DELETE` touches it. A row's `status` is
fixed at insert. Zero transitions.

**Candidate 4 (found by me) — `DeviceConnection`. FAILS the bar.** `_client`/`_sftp` are `None` or
set (`src/remarkable_spec/device/connection.py:78-79`), moved by `connect` (`:81`) and `disconnect`
(`:119`), with the failure path resetting both to `None` (`:113-114`). Two states, and no state
identifier to name them with. Same shape for `SyncDB._conn` (`src/remarkable_spec/sync/db.py:45,50,66`).

**Running count of machines clearing the gate: 1.** The gate requires 2.

### 4. Exhaustive second sweep before accepting the omission

Because the omission turns on a count, I swept for anything I might have missed rather than stopping
at four candidates.

- **State-vocabulary grep across all of `src/`** for `pending|queued|in_progress|running|succeeded|
  failed|aborted|cancelled|canceled|stale|orphan|evict|expired|terminal|lifecycle`. Twelve hits, all
  of them non-states: eleven are `"Failed to ..."` error-message strings (for example
  `src/remarkable_spec/device/connection.py:116`,
  `src/remarkable_spec/formats/document_loader.py:97`) and one is the word "stale" in a docstring
  (`src/remarkable_spec/sync/hasher.py:6`). No enumerated state vocabulary exists anywhere in this
  codebase.
- **CodeGraph `explore`** for "state machine, status field, lifecycle transitions, document state,
  pending running done" returned 8 symbols across **1 file** — `src/remarkable_spec/device/sync.py`,
  candidate 1. It also surfaced `status` at `src/remarkable_spec/cli/sync_cmd.py:98`, which I read:
  that is the `@app.command def status` **CLI subcommand name**, not a state field.
- **Trash/deleted as a candidate.** `Document.is_trashed` (`src/remarkable_spec/models/document.py:375-377`)
  is a read-only predicate over `metadata.parent == "trash" or metadata.deleted`. Nothing in `src/`
  ever assigns `parent = "trash"` or `deleted = True`; every write is `"deleted": False`
  (`src/remarkable_spec/device/sync.py:180,500`). Trash state is authored by the device firmware and
  only read here. Zero transitions in this codebase. **Fails.**

### 5. Candidate 2 collapses further than the gate anticipated

Cross-checked the cache call graph with grep and with CodeGraph `callers`, which agree exactly:

| Method | Callers (CodeGraph `callers <sym> -l 20 --json`) |
| --- | --- |
| `put_ocr` (`src/remarkable_spec/sync/db.py:192`) | `[]` |
| `get_ocr` (`:174`) | `[]` |
| `get_all_ocr` (`:216`) | `[]` |
| `find_changed_pages` (`:319`) | `[]` |
| `put_diagram` (`:253`) | `_extract_with_cache` (`src/remarkable_spec/cli/diagram_cmd.py:202`) |
| `get_diagram` (`:237`) | `_extract_with_cache` (`src/remarkable_spec/cli/diagram_cmd.py:202`) |

So the `ocr_cache` half of candidate 2 is **unreachable**: its typed reader and writer have no
callers at all. The only thing that ever writes that table is the one-shot legacy import
`migrate_ocr_sidecars`, via raw `INSERT OR IGNORE` SQL (`src/remarkable_spec/sync/migrations.py:141-145`).
Meanwhile the live OCR cache used by `rmspec search` is a **filesystem sidecar**, not the database:
`cache_path = rm_path.with_suffix(".ocr.txt")`, read at `src/remarkable_spec/cli/search_cmd.py:196-197`
and written at `:206`. The `diagram_cache` half is reachable but insert-only with no eviction, giving
2 states and 1 transition.

### 6. Gate decision — output omitted

**One machine clears the bar; the gate requires two. `docs/behavior/state-machines.md` is not
written and no placeholder is written.** The output path was never created, so there was nothing to
delete; `docs/behavior/` still does not exist.

Reasoning in one line: this is a Python CLI whose only enumerated vocabularies are six read-only
`enum` classifications, whose persistence layer is insert-only apart from a single
`DELETE FROM documents`, and whose one genuine state-driven entity — the tracked document in
`sync_pull` — is a single machine, not two. Writing a second diagram would have required inventing
`miss` / `stale` / `orphaned` as state names that appear nowhere in source, which anti-goal 1 and the
verbatim rule in section 5 both forbid.

**Note on a conflict between two sections of this packet.** Section 3a's conditional gate says to set
`status: COMPLETE` on omission; section 7's first fallback path says to flip `status: BLOCKED`.
Section 3a is declared BINDING and overriding for this run, and the orchestrator's own success
criteria call for `COMPLETE`, so **`COMPLETE` is what I set**. Flagging the contradiction so the
packet template can be fixed rather than rediscovered.

## Validation

Because the output file is legitimately omitted, the checks below validate two things instead: that
the omission is real and complete, and that every citation in this packet's own authored sections
resolves. The scripted citation check is the same class of check the cross-link pass will run.

### V1 — citation validator over this packet's authored sections

Script at `/tmp/doc-behavior-state-machines/validate_citations.py`. It parses every
`` `path:LOC` `` and shorthand `` `:LOC` ``, skips fenced code blocks, resolves each shorthand
against the nearest preceding full path in the same section, and checks: file exists, every line
number is in range, and `git check-ignore --stdin` flags nothing. Scoped to `## Work log` onward so
the orchestrator's own illustrative line-541 and line-1092 example in section 4 is not counted as one
of my citations.

```console
$ uv run --no-project /tmp/doc-behavior-state-machines/validate_citations.py \
    docs/.packets/doc-behavior-state-machines.md '## Work log'
scope             : from '## Work log' to EOF (agent-authored sections only)
target            : docs/.packets/doc-behavior-state-machines.md
full citations    : 29
shorthand (:LOC)  : 24
distinct files    : 15
gitignored files  : 0
failures          : 0
exit=0
```

**Three rounds of failures the validator caught in my own prose, all fixed:**

1. A misresolving shorthand. I first wrote the three docstring hits in
   `src/remarkable_spec/sync/db.py:175,217,238` as a bare shorthand, but the nearest preceding
   *numbered* citation in that section was `src/remarkable_spec/sync/migrations.py:49-59`, and
   `migrations.py` is only 151 lines, so the validator resolved the shorthand against the wrong file
   and rejected all three line numbers as out of range. Fixed by writing the full path inline. This
   is exactly the misresolved-shorthand failure mode the success criteria warn about, caught by
   script rather than shipped.
2. Two orphan shorthands in the packet's section 4 that turned out to be the template's own worked
   example about a handler moving from line 541 to line 1092, not citations of mine. Fixed by scoping
   the validator to `## Work log` onward rather than editing template prose.
3. Fifteen orphan shorthands and two out-of-range shorthands in the Validation section itself, from
   naming a file in prose without a line number and then relying on it as the anchor. Fixed by making
   the first citation in each subsection a full `path:LOC`.

### V2 — omission is real, and no placeholder was written

```console
$ test -e docs/behavior/state-machines.md && echo "FAIL: file exists" || echo "OK: absent"
OK: docs/behavior/state-machines.md absent
```

At the time of my first check `docs/behavior/` did not exist at all. By the time of the final check it
did, because sibling packets in the same fan-out had written their own files into it. I did not create
it and I wrote nothing into it. The claim that matters holds throughout: the path named in this
packet's `output:` field has no file at it, and no placeholder was written.

### V3 — the machine-count evidence, mechanically

```console
$ grep -rn "Literal\[" src/ --include='*.py' | wc -l
0
$ grep -rn "createMachine\|StateMachine\|state_machine" src/ --include='*.py' | wc -l
0
$ grep -rnE "def (transition|set_status|setStatus|advance|fire|next_state)\b" src/ --include='*.py' | wc -l
0
$ grep -rn "DELETE FROM" src/ --include='*.py' | wc -l
1
```

Six `enum` classes exist (`grep -rn "Enum" src/ --include='*.py'`), all classifications; zero string
literal unions; zero state-machine declarations; zero transition functions; one `DELETE` statement in
the entire codebase.

### V4 — spot-check: candidate 1 read end-to-end at its cited span

This is the judgment check the criteria reserve for a human read. I read
`src/remarkable_spec/device/sync.py` in full and confirmed the transitions by hand, in the direction
and with the trigger the source actually has:

- `SyncManager.sync_status` classifies, it does not mutate: `tracked = db.get_document(doc_uuid)`
  then `tracked is None` → append `new_on_device` (`src/remarkable_spec/device/sync.py:288-290`);
  `device_last_modified > tracked.device_last_modified` → append `modified_on_device` (`:291-292`);
  a tracked doc whose UUID is absent from `device_uuids` → append `deleted_on_device` (`:295-299`).
- `SyncManager.sync_pull` mutates. `deleted_on_device` → `db.delete_document(doc_uuid)` then
  `continue`, so the document leaves the DB (`src/remarkable_spec/device/sync.py:333-335`). The other
  two change types → `self.pull_document` (`:339`), `db.upsert_document(doc)` (`:384`), one
  `db.upsert_page` per page (`:397`), `db.log_sync(status="ok", details=change_type)` (`:408-417`),
  append to `pulled` (`:419`).
- The failure edge is real and lands the document in `skipped`, not `pulled`: the `except Exception`
  at `src/remarkable_spec/device/sync.py:420` logs `status="error"` inside a
  `contextlib.suppress(Exception)` (`:422-431`) and appends to `skipped` (`:432`).

Two things this hand-read established that a string check could not, and both are why I trusted the
candidate: `db.delete_document` cascades to `pages` through the FK at
`src/remarkable_spec/sync/migrations.py:37`, and a `deleted_on_device` document is reported in
**neither** return bucket, because `src/remarkable_spec/device/sync.py:335` `continue`s before either
`pulled` or `skipped` is touched — `sync_pull`'s `(pulled, skipped)` return
(`src/remarkable_spec/device/sync.py:435`) is silent about deletions.

Also verified that every state name and edge label candidate 1 would have needed is verbatim in that
one file, so the machine was drawable and was excluded on the count alone, not on a technicality:
`new_on_device` 2, `modified_on_device` 2, `deleted_on_device` 2, `pulled` 10, `skipped` 5,
`upsert_document` 2, `delete_document` 1, `pull_document` 3, `log_sync` 3 occurrences.

### V5 — no billable or device command was run

Nothing in this run invoked `rmspec` at all. Every check was `grep`, `jq`, `wc`, `git`, `sed`,
`codegraph`, or a local Python script. No AWS call, no SSH to `10.11.99.1`.

## Summary

**Nothing shipped, by design: `docs/behavior/state-machines.md` was deliberately not written because
the conditional gate in section 3a was not met.** The gate requires at least two state machines each
with ≥3 states and ≥2 transitions traced to cited source; exactly one clears that bar. The
qualifying machine is the **tracked-document sync lifecycle** — `SyncManager.sync_status` classifies
each document as `new_on_device` / `modified_on_device` / `deleted_on_device`
(`src/remarkable_spec/device/sync.py:288-299`) and `sync_pull` moves it, deleting the DB row for the
deleted case (`:333-334`) or upserting document and pages and logging `status="ok"` for the other two
(`:339,384,397,408`), with a failure edge into `skipped` (`:420-432`); every state name and edge label
it needs is verbatim in that one file, so it was drawable and was excluded purely on the count. The
gate's second candidate, the `rm_hash`-keyed cache lifecycle, **fails twice over**: `ocr_cache` and
`diagram_cache` are insert-only with no `DELETE`, no TTL, and no `ON DELETE CASCADE`
(`src/remarkable_spec/sync/migrations.py:49-74`; the codebase's single `DELETE` hits `documents` at
`src/remarkable_spec/sync/db.py:127`), so stale rows are **merely orphaned, permanently** — 2 states,
1 transition — and `src/remarkable_spec/sync/db.py` holds no `miss`/`stale`/`orphaned` identifier to
name states with, so drawing it would have meant inventing all three. It collapses further than the
gate anticipated: `put_ocr`, `get_ocr`, `get_all_ocr`, and `find_changed_pages` have **zero callers**
(CodeGraph `callers` returns `[]` for each), the live OCR cache being a `.ocr.txt` filesystem sidecar
at `src/remarkable_spec/cli/search_cmd.py:196-206` instead. Two further candidates I found and
rejected: `SyncLogEntry.status` (append-only `sync_log`, zero transitions,
`src/remarkable_spec/sync/db.py:278-296`) and `DeviceConnection` (two states, no state vocabulary,
`src/remarkable_spec/device/connection.py:78-79,81,119`). A repo-wide sweep confirms the structural
reason: zero `Literal[...]` unions, zero state-machine declarations, zero transition functions, and
six `enum` classes that are all read-only classifications. **The orchestrator should not re-dispatch
this packet.** The tracked-document lifecycle belongs in `docs/behavior/processes.md`, which is out of
my scope; six out-of-scope defects are recorded above, the sharpest being the dead OCR-cache API and
the `deleted_local` / `deleted_on_device` docstring drift at
`src/remarkable_spec/device/sync.py:251-253`.
