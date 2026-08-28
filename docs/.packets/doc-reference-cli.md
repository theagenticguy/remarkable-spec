---
role: doc-reference-cli
model: opus
output: "docs/reference/cli.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · reference/cli.md

> **Conditional packet.** Only seed if `remarkable-spec` exposes a CLI binary. Verify by inspecting the root manifest (`package.json.bin`, `[[bin]]` in `Cargo.toml`, `[project.scripts]` in `pyproject.toml`, etc.) before spawning.

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

Produce `docs/reference/cli.md`: one content H2 per CLI subcommand — or per verb group, with subcommands as H3, when subcommand count exceeds 40 — each subcommand entry carrying a fenced usage block, a one-sentence description, a backtick `path:LOC` citation, and a bulleted flag list where every flag cites its definition site.

## 2. Scope

- Create: `docs/reference/cli.md`
- Do not touch: `docs/reference/public-api.md`, any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The root manifest declaring the CLI binary name(s).
- CLI command handlers — typically under `bin/`, `cmd/`, `src/cli/`, or wherever the manifest's `bin` entry points. Look for command-router patterns (a switch on `argv[2]`, a decorator-based dispatcher, a framework like Click/Cobra/Commander/Clap).
- Flag definition sites — usually colocated with the command handler.
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

### The authoritative CLI census

The command surface is fully literal and therefore fully enumerable — this is the one place the
index's decorator-extraction limit does not bite.

- **11 top-level commands**, registered by `app.command(<sub_app>, name="<literal>")` at
  `src/remarkable_spec/cli/__init__.py:52-62`: `inspect`, `ls`, `render`, `tree`, `ocr`, `diagram`,
  `search`, `sync`, `device`, `annotations`, `env`.
- **Two of those are groups, not leaves.** `sync` has five entry points — a `@app.default` plus
  `status`, `pull`, `push`, `log` at `src/remarkable_spec/cli/sync_cmd.py:47,98,171,228,368`.
  `device` has four — `info`, `ls`, `pull`, `push` at
  `src/remarkable_spec/cli/device_cmd.py:66,162,293,443`. Document group and leaf at the right
  level; `rmspec sync push` and `rmspec device push` are different commands with different
  behaviour, and `rmspec ls` and `rmspec device ls` likewise.
- **The other nine are single `@app.default` functions**, one per `*_cmd.py`.
- **Flags** come from the function signatures plus any `cyclopts.Parameter` annotation, and help
  text from numpydoc-style docstring `Parameters` sections; cyclopts 4.6.0 reads both. The installed
  cyclopts is a major version ahead of the `>=3.0.0` floor in `pyproject.toml:14` — describe the
  behaviour of 4.6.0, which is what runs.
- **`rmspec --help` and `rmspec <subcommand> --help` are offline and free.** Run them to confirm
  rendered flag spellings, defaults, and group structure rather than inferring from signatures
  alone, and cite the source line for each flag you document. Do not invoke any command that reaches
  AWS or the device.
- **Global settings that change command behaviour without appearing as flags**: the seven
  `RMSPEC_`-prefixed environment variables on `RmspecSettings`
  (`src/remarkable_spec/cli/_util.py:13-64`), plus a `.env` file in the working directory. Document
  these once, in their own section, since they apply across commands.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. Read the root manifest to confirm the CLI binary name. Record it for the intro line.
3. Locate the command-router. Identify the dispatcher pattern in use; enumerate subcommands by reading the router file.
4. For each subcommand, locate the handler. Read the handler file at the dispatch span to extract: the canonical usage line (function signature or framework-decorator usage), the full flag list, the one-sentence purpose (docstring's first sentence or comment header).
5. For each flag, find its definition site. Frameworks usually centralize flag declarations near the handler signature; ad-hoc CLIs scatter them through the handler body. Cite the exact line where the flag is parsed.
6. Group by verb if there are more than 40 subcommands. A verb group is the top-level command word (`get`, `set`, `list`, `analyze`, `query`). Each verb becomes a content H2, with subcommands nested as H3.
7. Draft each subcommand entry: `## <subcommand>`, then a fenced usage block (plain triple-backticks, no language tag), a one-sentence description, a backtick `path:LOC` citation on its own line, then a `Flags:` label followed by a bullet list where each flag is ``- `--flag` — description. `path:LOC`.``
8. Order subcommands as they appear in the router; within a verb group, order alphabetically.
9. Write `docs/reference/cli.md` with H1 = `# remarkable-spec · CLI` and a one-sentence intro line naming the CLI binary in backticks before the first content H2.

## 5. Output format rules

- H1 = `# remarkable-spec · CLI`. No decorative titles.
- No YAML frontmatter on the output file.
- A single one-sentence intro after the H1 names the CLI binary (e.g., ``The `mybin` CLI has the following subcommands.``).
- Each subcommand is a content H2 (`## <subcommand>`) — or H3 when verb-grouped — followed in this order by:
  1. A fenced usage block with **no language tag** (plain `` ``` ``` ``).
  2. A one-sentence description.
  3. A backtick `path:LOC` citation on its own line.
  4. A `Flags:` label followed by a bullet list. Each bullet has the form ``- `--flag` — description. `path:LOC`.``
- If subcommand count > 40: one content H2 per verb group, one H3 per subcommand beneath. Otherwise flat content H2s.
- No Mermaid. No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Read** the router and per-command handler files.
- **Grep** for command-registration patterns: `.command(` / `@app.command` / `subparser.add_parser` / `Cobra` `Command{}` / `Clap` `arg!()`.
- **Glob** to enumerate handler files under `bin/`, `cmd/`, `src/cli/`, or `src/commands/`.
- **Bash** for `<binary> --help` invocation **only if** the binary is safely runnable; otherwise rely on source reads. Cite the source either way.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **No clear router** (each subcommand is its own binary file): each binary file becomes one content H2. Cite the file's main entry.
- **Subcommand has no extractable flags:** emit the subcommand heading with usage block, description, and citation only — omit the `Flags:` bullet list rather than emit an empty one.
- **A handler file cannot be `Read`** (missing or binary): emit the subcommand heading with the usage block and description, mark the citation `*handler unavailable*`, and log the skip in the Work log.
- **Flag definitions are scattered across multiple files:** cite the canonical definition site (where the flag's name first appears as a string literal or constant), then note the secondary parsing sites in the Work log.

## 8. Success criteria

- [x] `docs/reference/cli.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · CLI`.
- [x] H1 is followed by a single one-sentence intro containing the CLI binary name in backticks.
- [x] At least one content H2 subcommand (or verb group) exists; count matches the router's subcommand count.
- [x] Every subcommand heading — a content H2 in flat mode, an H3 under a verb group — has exactly one fenced usage block immediately after it.
- [x] Every subcommand heading has exactly one backtick `path:LOC` citation for the handler.
- [x] Every `Flags:` bullet has a backtick `path:LOC` citation.
- [x] Subcommand count > 40 → verb-group content H2s with H3 subcommands; otherwise flat content H2s.
- [x] No YAML frontmatter on the output.
- [x] No Mermaid fences in the output.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path).

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent subcommand names, flag names, or `path:LOC` citations. Every identifier traces back to a source read or a router declaration.
- Do not paraphrase usage blocks or flag declarations. Quote them verbatim from source.
- Do not write to `docs/reference/public-api.md` — the public-api packet owns that file.
- Do not emit the output file when no CLI is detected — flip `status` to `BLOCKED` and write the gap to the Work log.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

- `docs/.packets/_environment.md:82` and `:156` — both cite the CLI registration block as
  `src/remarkable_spec/cli/__init__.py:52-62`; the block is actually at `:48-58`, four lines earlier,
  and the file is only 72 lines long. A downstream packet that copies the range without re-reading
  will cite lines that hold `_get_version` and the `app.version` assignment instead of the
  registrations, and its citation will still pass a naive in-range check. Same wrong range is repeated
  in this packet's own section 3a.
- `docs/.packets/doc-reference-cli.md:117-118` (section 3a) — states flag "help text [comes] from
  numpydoc-style docstring `Parameters` sections". No CLI function in this repo has a numpydoc
  `Parameters` section; all 98 documented flags get their help from an inline
  `cyclopts.Parameter(help="...")` in the `Annotated[...]` annotation. An agent trusting the directive
  would look in docstrings, find nothing, and conclude the flags are undocumented.
- `docs/.packets/doc-reference-cli.md:112-114` (section 3a) — states `rmspec sync push` and
  `rmspec device push` are "different commands with different behaviour". They are not:
  `src/remarkable_spec/cli/device_cmd.py:493-504` lazily imports `sync_cmd.push` and forwards all
  seven parameters, and the two signatures carry identical defaults. A reader told they differ will go
  looking for a behavioural difference that does not exist. The directive's parallel claim about
  `rmspec ls` versus `rmspec device ls` *is* correct — those are a local filesystem scan and an SSH
  read respectively.
- `src/remarkable_spec/cli/_util.py:47-55` — the `thickness` and `dpi` fields on `RmspecSettings` have
  zero readers anywhere in `src/`, so `RMSPEC_THICKNESS` and `RMSPEC_DPI` are settable and inert while
  looking live in `rmspec env`-adjacent documentation. The drift is invisible because
  `src/remarkable_spec/cli/render_cmd.py:85,89` hardcodes the same two values (`1.5`, `226`) as literal
  signature defaults, so `--help` prints exactly what the settings model would have supplied.
- `src/remarkable_spec/cli/env_cmd.py:29` — `shell: bool = True` is the only CLI parameter declared
  without a `cyclopts.Parameter` annotation, so `rmspec env --help` renders a flag with an empty
  description column. A user reading that help cannot tell what `--shell` versus `--no-shell` changes
  without opening `:58-63`.
- `src/remarkable_spec/cli/device_cmd.py:104,204,331` — `_check_device_deps()` guards `info`, `ls`, and
  `pull` but not `push`, whose handler at `:442` never calls it. A user without the `[device]` extra
  gets the clean "Device dependencies not installed" message from three subcommands and falls through
  to `sync_cmd`'s own guard from the fourth, so the failure mode is inconsistent across one group.

---

## Work log

### Step 1 — Inputs read

- `docs/.packets/_environment.md` (442 lines) read in full. Its **stale-prior traps** section treated
  as binding: cyclopts is **4.6.0** (not 3.x, not Click, not Typer, not argparse); no tests exist; no
  CI; `RmspecSettings` has exactly seven fields; OCR uses Bedrock `invoke_model`, never `converse`.
- `docs/.repomix/codebase.json` present (371 KB) and `docs/.repomix/token-tree.txt` (5.3 KB). Read as
  input only — never cited, per directive 2.

### Step 2 — Prior-artifact check (Process step 1)

**No prior artifact exists.** Verified three ways:

```
$ ls -la docs/reference/cli.md
"docs/reference/cli.md": No such file or directory (os error 2)
$ git ls-files docs/ | wc -l
0
$ git log -1 --format=%cs -- docs/reference/cli.md
(no output — path never committed)
```

`docs/` holds zero tracked files; only the untracked `.packets/` and `.repomix/` scratch directories
exist under it. There is therefore **no prior version of `docs/reference/cli.md` and nothing to
re-verify or inherit** — this is a first run over a repo with no `docs/` tree. Nothing "the prior
artifact got wrong" can be reported because no prior artifact was written. The whole file is built
from source reads at their current line numbers.

Repo total: 67 tracked files (`git ls-files | wc -l`), matching the brief.

### Step 3 — Binary name confirmed (Process step 2)

One console script: `rmspec = "remarkable_spec.cli:app"` at `pyproject.toml:20-21`. Distribution name
`remarkable-spec` at `pyproject.toml:2`. No other `[project.scripts]` entry, no `[project.gui-scripts]`.

### Step 4 — Router located (Process step 3)

Dispatcher pattern: **cyclopts 4.6.0 sub-app mounting**, not a switch or an argparse subparser. The
root `cyclopts.App` is built at `src/remarkable_spec/cli/__init__.py:40-44` (`name="rmspec"`,
`version_flags=("--version", "-V")`), and 11 child `App` objects are mounted by
`app.command(<sub_app>, name="<literal>")`.

**Line-number correction against the brief.** The environment brief and this packet's section 3a both
cite the registration block as `src/remarkable_spec/cli/__init__.py:52-62`. Read at current HEAD, the
block is at **`:48-58`** — off by four. The file is 72 lines total (`wc -l`), so `:62` is a real line
but holds `app.command(diagram_app, name="diagram")`-adjacent whitespace rather than the block start.
Every citation in the output uses the line numbers I read myself, not the brief's. Same check applied
to the other brief citations I reuse: `app = cyclopts.App(` at `:40-44` is correct, `__all__` at `:38`
is correct.

Registration order (the output's H2 order, per Process step 8): `inspect` `:48`, `ls` `:49`,
`render` `:50`, `tree` `:51`, `ocr` `:52`, `diagram` `:53`, `search` `:54`, `sync` `:55`,
`device` `:56`, `annotations` `:57`, `env` `:58`.

Handler files enumerated (`ls src/remarkable_spec/cli/` + `wc -l`): 14 `.py` files, 3,899 LOC, one
`*_cmd.py` per top-level command plus `__init__.py`, `_util.py` (112), `_resolve.py` (290).

### Step 5 — Rendered help captured (Process steps 4-5, directive 4)

Ran `rmspec --help` plus `--help` for all 11 top-level commands and all 9 group leaves via
`uv run rmspec ... --help`. **Nothing billable and nothing device-touching was invoked** — no `ocr`,
`diagram`, `annotations`, `sync pull|push`, or `device *` execution. Output saved to
`/tmp/doc-reference-cli/help.txt` (580 lines) and `/tmp/doc-reference-cli/help-nested.txt` (116 lines).

**Shell artifact caught and corrected.** The first pass invoked nested leaves as `uv run rmspec $c
--help` with `c="sync status"`. zsh does not word-split unquoted parameters, so cyclopts received the
single token `sync status`, failed to match it, and fell back to printing the **root** `rmspec --help`
for all eight nested calls. Re-run with `${=c}` to force splitting produced the real per-leaf help.
Recorded because the wrong output looked plausible: eight identical root-help dumps could have been
mistaken for "nested leaves have no help of their own", which is false.

Command surface confirmed against the router, count reconciled:

- 11 top-level registrations at `src/remarkable_spec/cli/__init__.py:48-58`.
- `sync` group: `@app.default` at `src/remarkable_spec/cli/sync_cmd.py:47` plus four `@app.command`
  leaves at `:97` (`status`), `:170` (`pull`), `:227` (`push`), `:367` (`log`). The brief's decorated
  *function* lines `47,98,171,228,368` are the `def` lines; the decorator sits one line above each
  except `:47`, which is itself the decorator with `def _default` at `:48`. Both resolve; I cite the
  decorator line.
- `device` group: four `@app.command` leaves at `src/remarkable_spec/cli/device_cmd.py:65` (`info`),
  `:161` (`ls`), `:292` (`pull`), `:442` (`push`). **`device` has no `@app.default`** — confirmed by
  `rmspec device --help` printing a Commands panel and **no** Parameters panel, so the group itself
  takes no flags. `sync` does have a default and does show a Parameters panel.
- **19 documentable command paths** = 11 top-level (2 of which are groups) + 9 group leaves. 19 is
  well under the 40 threshold in Output format rules, so the output uses **flat content H2s**, each
  H2 being the full command path (`## sync push`, `## device push`) rather than a nested verb group.
  This satisfies directive 3a's "document group and leaf at the right level" — `sync push` and
  `device push` get separate H2s, separate usage lines, and separate citations — while honouring the
  flat-mode rule.

### Step 6 — Flag extraction (Process step 5)

Wrote `/tmp/doc-reference-cli/sigdump.py` and `/tmp/doc-reference-cli/defdump.py`, two PEP-723-style
`ast` walkers run under `uv run python`, to pull every parameter's **exact source line**, its
`Annotated[...]` source segment, and its default expression. This beats eyeballing because the
signatures are multi-line `Annotated[...]` blocks where the parameter name and its `cyclopts.Parameter`
sit 2-7 lines apart.

**Where help text actually comes from — correction to directive 3a.** Section 3a says help text comes
"from numpydoc-style docstring `Parameters` sections". In this codebase it does not: every documented
flag carries an inline `cyclopts.Parameter(help="...")` inside its `Annotated[...]` annotation, and
the rendered help string matches that literal verbatim in all 78 cases. The function docstrings here
hold prose bodies and `Examples` sections, not numpydoc `Parameters` blocks. The one exception is
`env`, whose single parameter is a bare `shell: bool = True` with no `cyclopts.Parameter`
(`src/remarkable_spec/cli/env_cmd.py:29`) — and its help panel correspondingly renders a description
column that is empty. So the flag citations point at the signature line, which is the definition site.

**Defaults resolve from two different places, and this matters.** Nine flags default to
`settings.<field>` read off the `RmspecSettings` singleton (`src/remarkable_spec/cli/_util.py:64`) —
all `--host`/`--user` on `sync` and `device`. The rest are literals in the signature. Consequence
worth stating in the output: the `[default: 10.11.99.1]` cyclopts prints for `--host` is the value of
`RMSPEC_DEVICE_HOST` at import time, not a constant.

**Verified finding — two declared settings have zero consumers.** `RmspecSettings.thickness`
(`src/remarkable_spec/cli/_util.py:47-51`) and `RmspecSettings.dpi` (`:52-55`) are read nowhere in
`src/`:

```
$ grep -rn "settings.thickness\|settings.dpi" src/ --include='*.py'
(zero consumers)
$ grep -rn "getattr(settings\|settings\[\|model_dump\|dict(settings" src/ --include='*.py'
(none)
```

`rmspec render` hardcodes `1.5` and `226` as literal signature defaults at
`src/remarkable_spec/cli/render_cmd.py:85,89` — the same numbers as the settings fields, which is why
the drift is invisible in `--help` output. The second grep rules out dynamic attribute access as an
escape hatch, so `RMSPEC_THICKNESS` and `RMSPEC_DPI` are settable and inert. Documented as such in the
output's Environment settings section rather than filed as out-of-scope, because it is a fact about
CLI behaviour and this file owns that.

### Step 7 — Environment settings section (directive 3a bullet 5)

Directive 3a requires the seven `RMSPEC_` variables documented "once, in their own section". The
Output format rules describe only command H2s, and section 8's exemption clause covers only the
cross-link pass's `## See also`. Resolution: the output carries one final `## Environment settings`
H2, deliberately shaped like a command entry — fenced block, one-sentence description, citation on
its own line, then a bullet list where every item carries its own `path:LOC` — so every mechanical
check in section 8 (fenced block per H2, citation per H2, citation per bullet) still passes over it.
Declaring the deviation here rather than silently emitting an odd section.

Seven fields verified by reading `src/remarkable_spec/cli/_util.py:13-60` in full: `xochitl`,
`device_host`, `device_user`, `device_password`, `thickness`, `dpi`, `sync_db`. Matches the brief's
stale-prior trap exactly — no port, no timeout, no region field. `env_prefix="RMSPEC_"` and
`env_file=".env"` at `:23-24`.

### Step 8 — Brace safety (directive 3)

One brace hazard in the CLI surface, in `rmspec search`'s help text: the device backend posts to a
path template with a keyword placeholder in braces (`src/remarkable_spec/cli/search_cmd.py:1`
docstring, rendered at `/tmp/doc-reference-cli/help.txt:180-181`). In the output that string is
wrapped in an inline code span, never left bare in prose. No `{uuid}`-style document filename appears
anywhere in the CLI docs, so `models/document.py` and `device/paths.py` never got quoted.

## Validation

Three scripts, all under `/tmp/doc-reference-cli/`, each exiting non-zero on any failure. Everything
mechanically checkable was checked by script; only the prose descriptions were judged by eye.

### 1. `validate.py` — structure, citations, gitignore, braces

Checks: no YAML frontmatter; exact H1 string; exactly one H1; intro is exactly one line and contains
the binary in backticks; no mermaid fence; no emoji; every full `path:LOC` resolves to a real file and
an in-range line; every `:LOC` shorthand resolves against the last full path **in the same section**
and is in range for *that* file; no orphan shorthand; `git check-ignore` and `git ls-files
--error-unmatch` on every cited path; per-H2 exactly one fenced block, that fence is the first content
after the heading, exactly one standalone handler-citation line, and every bullet carries a citation;
no bare brace outside a fence or code span.

```
$ uv run python /tmp/doc-reference-cli/validate.py
doc lines          : 383
content H2 sections: 20
full citations     : 25
shorthand (:LOC)   : 113
distinct paths     : 14
   pyproject.toml
   src/remarkable_spec/cli/__init__.py
   src/remarkable_spec/cli/_util.py
   src/remarkable_spec/cli/annotations_cmd.py
   src/remarkable_spec/cli/device_cmd.py
   src/remarkable_spec/cli/diagram_cmd.py
   src/remarkable_spec/cli/env_cmd.py
   src/remarkable_spec/cli/inspect_cmd.py
   src/remarkable_spec/cli/ls_cmd.py
   src/remarkable_spec/cli/ocr_cmd.py
   src/remarkable_spec/cli/render_cmd.py
   src/remarkable_spec/cli/search_cmd.py
   src/remarkable_spec/cli/sync_cmd.py
   src/remarkable_spec/cli/tree_cmd.py
H2 list            : ['inspect', 'ls', 'render', 'tree', 'ocr', 'diagram', 'search', 'sync',
                      'sync status', 'sync pull', 'sync push', 'sync log', 'device', 'device info',
                      'device ls', 'device pull', 'device push', 'annotations', 'env',
                      'Environment settings']

ALL CHECKS PASSED
$ echo $?
0
```

138 citations total (25 full + 113 shorthand) over 14 files, all tracked, none gitignored.

**Defect this script caught on its first useful run, after a fix.** The first version resolved
shorthands but only range-checked them, so it passed a citation that resolved to the **wrong file**.
In the `## Environment settings` section the `RMSPEC_DPI` bullet carries an inline full citation to
`src/remarkable_spec/cli/render_cmd.py:86-89`, which reset the section's "last full path". The two
following shorthands `:52-55` and `:56-60`, intended as `_util.py`, therefore resolved to
`render_cmd.py` — a 408-line file where both ranges exist, so the range check passed and a reader would
have been sent to the wrong lines. Fixed by spelling both citations out in full
(`src/remarkable_spec/cli/_util.py:52-55` and `:56-60`), which is why the full-citation count is 25
rather than 23. This is the exact class of failure the packet's shorthand rule exists to prevent, and a
range-only validator does not see it.

### 2. `xcheck.py` — every flag citation lands on the parameter it names

Parses each CLI module with `ast`, indexes every `ast.arg` and `ast.AnnAssign` by line number together
with any `name="..."` override inside its `Annotated[...]`, then asserts that each flag bullet's cited
line actually defines a parameter whose name matches the documented spelling (accounting for
underscore-to-dash conversion, cyclopts `name=` overrides, positional upper-casing, `--no-` negations,
and the `RMSPEC_` env-var prefix from `env_prefix`).

```
$ uv run python /tmp/doc-reference-cli/xcheck.py
flag bullets cross-checked against AST param lines: 98 matched

NO MISMATCHES
$ echo $?
0
```

98 of 98. No flag bullet cites a line that is not the definition site of the flag it documents.

### 3. `helpdiff.py` — no missing and no invented flags

Parses the Parameters panel of every captured `--help` invocation and diffs it against the doc's
per-command flag set in both directions, collapsing cyclopts' auto-generated `--no-X` negations.

```
$ uv run python /tmp/doc-reference-cli/helpdiff.py
OK  rmspec annotations    help=7  doc=7
OK  rmspec device         help=0  doc=0
OK  rmspec device info    help=4  doc=4
OK  rmspec device ls      help=5  doc=5
OK  rmspec device pull    help=7  doc=7
OK  rmspec device push    help=8  doc=8
OK  rmspec diagram        help=11 doc=11
OK  rmspec env            help=1  doc=1
OK  rmspec inspect        help=3  doc=3
OK  rmspec ls             help=5  doc=5
OK  rmspec ocr            help=9  doc=9
OK  rmspec render         help=11 doc=11
OK  rmspec search         help=6  doc=6
OK  rmspec sync           help=5  doc=5
OK  rmspec sync log       help=2  doc=2
OK  rmspec sync pull      help=4  doc=4
OK  rmspec sync push      help=8  doc=8
OK  rmspec sync status    help=5  doc=5
OK  rmspec tree           help=3  doc=3

Every documented flag exists in --help; every --help flag is documented. 19/19 command H2s reconciled.
$ echo $?
0
```

The first run of this script reported 16 failures, all of them parser artifacts rather than doc
defects — the help-panel parser had swept all-caps words out of description text (`SSH`, `OCR`, `DPI`)
and the doc parser had captured only the first backtick span per bullet (losing the `--source` half of
`` `SOURCE` / `--source` ``) and had unconditionally discarded anything starting with `--no-` (losing
the legitimate `--no-pdf-bg`). Fixed by splitting the help first column on runs of two-plus spaces and
by reading every backtick span before the em-dash. Recorded because "16 failures" on a doc that was
correct is a good way to talk yourself into editing correct prose.

### Judgment spot-checks (not scriptable)

- **`device push` description.** Draft said it was "a separate handler from `rmspec sync push`" that
  "does not consult the sync database the same way". Reading
  `src/remarkable_spec/cli/device_cmd.py:493-504` disproved it: the body lazily imports
  `sync_cmd.push` and forwards all seven arguments. Rewritten to say the two share one implementation,
  and a Note added. This was an inherited assumption from the packet's own section 3a, not from source.
- **`device` group's dependency guard.** Draft said the `[device]` check runs "before any subcommand
  connects". `grep -n "_check_device_deps"` returned call sites at `:104`, `:204`, `:331` only —
  `push` at `:442` never calls it. Corrected to name the three that do and note the one that does not.
- **Test claims.** None made. Per directive 1 the signal is structurally absent (`tests/` holds one
  0-byte `__init__.py`), so no sentence in the output says anything is tested, covered, or verified by
  tests, and no path under `tests/` is cited.
- **Brace safety.** `validate.py` proves zero bare braces outside fences and code spans. The one
  hazard, the search backend's keyword-placeholder URL path, sits inside an inline code span in the
  `## search` section.
- **cyclopts version.** Every framework statement describes 4.6.0 behaviour observed in the live venv
  via actual `--help` output, not inferred from the `>=3.0.0` floor. No sentence mentions Click, Typer,
  argparse, or cyclopts 3.x.

## Summary

Shipped `docs/reference/cli.md`, 383 lines, built from scratch — the output path held no file and
`docs/` had zero tracked files, so nothing was inherited or patched. It documents all 19 command paths
of the `rmspec` console script as **flat content H2s**: 11 top-level commands from the registration
block at `src/remarkable_spec/cli/__init__.py:48-58`, with the two groups (`sync`, `device`) expanded
into their 9 leaves under full-path headings (`## sync push`, `## device push`) so group and leaf are
documented at the right level without nesting. **Verb grouping was not applied** — 19 subcommands is
well under the 40 threshold, and the format rules mandate flat H2s below it. One additional H2,
`## Environment settings`, documents the seven `RMSPEC_` variables that directive 3a requires in their
own section; it is deliberately shaped like a command entry so every mechanical check applies to it
too, and the deviation is declared in the Work log. Each entry carries a verbatim usage line quoted
from live `--help` output, a one-sentence description, a handler citation, and a flag bullet per
parameter citing its exact signature line — 138 citations over 14 tracked files, all validated by
script.

**No router gaps.** The command surface is fully literal, so the registration block is a complete
census and nothing had to be inferred or marked unavailable; every handler file read cleanly and no
fallback path in section 7 was needed. Three corrections worth carrying forward: the environment
brief's registration-block line range is off by four (`:48-58`, not `:52-62`); flag help text in this
codebase comes from inline `cyclopts.Parameter(help=...)`, not numpydoc docstring sections as
section 3a states; and `rmspec device push` is a thin forwarder to `rmspec sync push` with identical
defaults, not a behaviourally distinct command as section 3a states. Two source findings surfaced while
verifying citations and are documented in the output rather than buried: `RMSPEC_THICKNESS` and
`RMSPEC_DPI` have zero readers in `src/` and are inert, hidden by `rmspec render` hardcoding the same
two values; and the `[device]` dependency guard covers three of the four `device` subcommands. No
billable or device-touching command was ever invoked — only `--help`.
