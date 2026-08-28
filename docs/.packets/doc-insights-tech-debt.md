---
role: doc-insights-tech-debt
model: opus
output: "docs/insights/tech-debt.md"
depends_on: []
status: COMPLETE
---

# Packet · remarkable-spec · insights/tech-debt.md

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

Produce `docs/insights/tech-debt.md`: a ranked register of debt items in `remarkable-spec` (≥ 10 rows), a verbatim dump of explicit markers (TODO/HACK/FIXME/etc.), and a Pattern-level smells section (top 5) with citations and a cost assessment. Markers are one signal among several and often the weakest one — many repos forbid them by convention, and their debt lives in declined-scope documents, version pins, single-platform assumptions, asymmetric enforcement between sibling surfaces, and prose that no longer describes the code beneath it.

The reader's question this file answers: *"Where is the rot, and what would I pay to fix it?"*

## 2. Scope

- Create: `docs/insights/tech-debt.md`
- Do not touch: any other file under `docs/`, any source file, any other packet.

## 3. Inputs

- **READ FIRST — the shared environment brief**, `_environment.md` in this packet's own directory (`docs/.packets/_environment.md` in single-repo mode): verified-current language, framework, and toolchain facts, repo topology, and the code-index command table with absolute binary paths. Its **stale-prior traps** subsections are BINDING — an API the brief names as removed or renamed does not exist in this codebase, whatever your priors suggest, and citing it is an invention. If the file is absent, say so in the Work log and confirm every framework fact against the dependency manifest or Context7 before citing it.
- The repo's own statements of intent, each of which is a claim to difference against the code beneath it: declined-scope and non-goals documents, architecture-decision records, strategy and threat-model docs, compounded-lesson and postmortem directories (`LESSONS.md`, `docs/lessons/`, `.erpaval/solutions/`, `postmortems/`), and the acceptance prose in specs and requirement files.
- Manifests and CI configuration: version pins across every dependency manifest, lockfile, workflow file, tool-version file, and container base image. A pin is debt when it names a version the ecosystem has moved past, when two manifests pin one dependency differently, or when a comment justifies the pin with a condition that no longer holds.
- Single-platform and single-environment assumptions: one hard-coded target triple, architecture, region, OS, shell, or path separator inside code the docs describe as portable.
- Asymmetric enforcement between sibling surfaces: two adjacent modules, bindings, endpoints, or CI jobs where one carries a guard, a test, or a validation gate and its twin does not.
- Comment markers (TODO, FIXME, HACK, XXX, NOTE, REFACTOR) and deprecation decorators (`@deprecated`, `// DEPRECATED`), where the repo's culture uses them. A zero count is a methodology signal, not a clean bill of health — see Fallback paths.
- Structural smells: suspicious naming (`legacy*`, `*Wrapper`, `*Helper`, `*Old`, `*V1`), duplicated/copy-pasted code blocks, error-swallowing patterns (`catch {}` / bare `except:` / `_ = error`), missing-tests signals.
- Optional flattened-codebase JSON at `docs/.repomix/codebase.json` for fast comment-pattern scanning.

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

### Zero markers is a methodology trigger, not a finding

`grep -rn -E 'TODO|FIXME|HACK|XXX' src/ tests/ --include='*.py'` returns **nothing** (exit 1). Say
so, then do the real work: the debt here is unmarked and structural. Verified leads, each of which
you must re-verify at its current line number before publishing:

- **Zero tests against 10,321 LOC.** `tests/` holds one 0-byte `__init__.py` while
  `pyproject.toml:74-76` configures pytest and `mise.toml:11` defines a `test` task, so the harness
  is wired to an empty suite and `mise test` passes vacuously. This belongs at or near the top of
  the register.
- **`_invoke_bedrock_vision` implemented three times** — `src/remarkable_spec/ocr/postprocess.py:187`,
  `src/remarkable_spec/ocr/diagram.py:286`, `src/remarkable_spec/cli/annotations_cmd.py:254` — with
  the second carrying a docstring at `src/remarkable_spec/ocr/diagram.py:295` that says it uses "the
  same pattern as" the first. Self-documented duplication.
- **The model ID `global.anthropic.claude-opus-4-6-v1` hardcoded in four places**:
  `src/remarkable_spec/ocr/postprocess.py:23`, `src/remarkable_spec/ocr/diagram.py:57`,
  `src/remarkable_spec/ocr/pipeline.py:90`, `src/remarkable_spec/cli/annotations_cmd.py:254`. No
  settings field exists for it.
- **27 broad or bare `except` sites in `src/`**, with
  `src/remarkable_spec/device/sync.py:276-277` the representative case — a bare
  `except Exception: continue` that converts a metadata-fetch failure into a silently dropped
  document during sync.
- **Floating lower bounds crossed by installed majors**: `cyclopts>=3.0.0` with 4.6.0 installed and
  `paramiko>=3.4.0` with 4.0.0 installed (`pyproject.toml:14,30`). `rmscene` is the only
  upper-bounded pin (`pyproject.toml:13`), which is itself a signal about that dependency.
- **Python version declared four different ways**: `.python-version:1` says 3.13, `mise.toml:2` says
  3.12, `pyproject.toml:10` floors at 3.12, ruff targets py312 (`pyproject.toml:60`) and pyright
  targets 3.12 (`pyproject.toml:71`) — while the venv interpreter is 3.13.11.
- **The configured type checker is not the invoked one**: `[tool.pyright]` at
  `pyproject.toml:70-72` with `pyright>=1.1` in the dev group (`pyproject.toml:52`), against
  `uvx ty check src/` in `mise.toml:12` and `README.md:143`.
- **Three unrelated DPI defaults**: `RmspecSettings.dpi = 226`
  (`src/remarkable_spec/cli/_util.py:53-56`), `OCRCacheEntry.render_dpi = 300`
  (`src/remarkable_spec/sync/models.py:82`), and `dpi: int = 300` on both `pipeline.py` entry points
  (`src/remarkable_spec/ocr/pipeline.py:28,88`) — against a panel `CLAUDE.md` documents as 229 DPI.
- **macOS-only behaviour compiled into library code, at import time**:
  `src/remarkable_spec/cli/_util.py:68-72` mutates `os.environ["DYLD_FALLBACK_LIBRARY_PATH"]` as a
  module-level side effect, and `src/remarkable_spec/ocr/vision.py` binds the Apple Vision
  framework with no non-Apple fallback.
- **No CI, no advisory scanner, no license check, no secret scanner, no SBOM.** `.github/` does not
  exist; the only automation is lefthook's pre-commit ruff pass and commit-msg check
  (`lefthook.yml:1-27`).
- **`src/remarkable_spec/formats/rm_file.py:31` silences a third party globally** —
  `logging.getLogger("rmscene").setLevel(logging.ERROR)` at import time, affecting any process that
  imports the module, while the same file downgrades unknown pen types and unknown colors to
  warnings with silent defaults (`:162`, `:169`).

Give each entry a cost-of-removal that is honest about ordering: several of these are one-line
config fixes and one of them (the test suite) is weeks of work. Do not flatten that.

## 4. Process

1. **Check for a prior artifact before drafting anything.** The output path usually already exists, and the expected finding is that it is stale in ways that change meaning: citations off by hundreds of lines (a handler cited at `:541` now living at `:1092`), LOC figures matching neither `wc -l` nor the flattened pack, a diagram edge the codebase's own dependency test forbids, a rule whose polarity the source has since inverted, citations pointing into a gitignored build artifact a reviewer cannot open. Inherit no claim you have not re-verified. Partition the old citations in one pass: `git log -1 --format=%cs -- <output file>` gives the doc's own last write, `git log -1 --format=%cs -- <cited path>` gives each cited file's; every citation whose file is newer than the doc must be re-read, the rest need only the scripted range check. Rebuild the file from source rather than patching prose around surviving sentences — a stale paragraph that still parses is the failure mode here — and record in the Work log what the prior artifact got wrong.
2. State the assembly methodology in the intro. The register combines: explicit comment markers, deprecation decorators, manifest version pins to known-old versions, and pattern-level smells the reviewer chose to flag. Be transparent about the closed vocabulary used for categories.
3. Extract **Explicit markers**. Grep for `\bTODO\b` / `\bFIXME\b` / `\bHACK\b` / `\bXXX\b` / `// REFACTOR` / `# DEPRECATED` and any equivalent the repo uses. Capture the comment text verbatim and the citation. No filtering — the whole list is the deliverable here.
4. Identify **Pattern-level smells**. Look for: error-swallowing (`catch (_) {}`, `except: pass`), wrong-abstraction signals (a class named `*Manager` doing 1000 LOC), duplicated logic (two functions with near-identical bodies), missing-tests signals (a complex module with no colocated test file), version pins to known-old packages, dead-code-adjacent debt (commented-out code blocks > 5 lines).
5. For each smell, identify 2–5 representative sites. The smell is the pattern; the sites are evidence.
6. Compose the **Ranked register**. Each row is a discrete debt item — either a single marker, a single smell instance, or a single bad abstraction. Category vocabulary is closed: `marker`, `wrong abstraction`, `error handling`, `dead code adjacent`, `deprecated pattern`, `version pin`, `duplicated logic`, `missing tests`. Cost vocabulary is closed: `S`, `M`, `L`. Rank by `cost-to-fix × consequence-of-leaving` — the reviewer's judgment shows up in the rank.
7. Write `docs/insights/tech-debt.md` with H1 = `# remarkable-spec · Tech debt`. Three content H2 sections: `## Ranked register`, `## Explicit markers`, `## Pattern-level smells`.

## 5. Output format rules

- H1 = `# remarkable-spec · Tech debt`. No decorative titles.
- No YAML frontmatter on the output file.
- Intro states the assembly methodology.
- Three content H2 sections: `## Ranked register`, `## Explicit markers`, `## Pattern-level smells`. Always in that order.
- `Ranked register` table columns: `Rank | Debt item | Category | Cost to fix | Citation`. ≥ 10 rows.
- `Category` uses the closed vocabulary: `marker` / `wrong abstraction` / `error handling` / `dead code adjacent` / `deprecated pattern` / `version pin` / `duplicated logic` / `missing tests`. No improvised category names.
- `Cost to fix` is `S` / `M` / `L`.
- `Explicit markers` is a bullet list. Each bullet quotes the comment verbatim + citation: `` - `<verbatim comment text>` — `path:LOC` ``.
- `Pattern-level smells` uses H3 per smell (top 5). Each H3 has: a one-paragraph description, a "Shows up in:" bullet list of 2–5 citations, and a final "Cost:" line.
- No emojis. No filler adverbs.

## 6. Discovery toolkit

- **Grep** with `-i` for marker patterns; case-sensitive for `\bTODO\b` style.
- **Read** representative smell sites to verify.
- **Glob** to enumerate test directories (for the "missing tests" smell).
- **Bash** for `jq` over `docs/.repomix/codebase.json` to bulk-scan all comment markers in one pass.
- **Context7 MCP** for framework / 3p-library documentation (`resolve-library-id` → `query-docs`) — use when the codebase references a framework or library whose semantics you want to confirm before drawing inferences.
- **Code index / symbol graph** — a precomputed index of symbols, references, and call edges (codegraph, an LSP index, an AST analyzer). It answers what grep cannot: consumer counts per symbol, blast radius of a change, reachability, and which symbols no test touches. Three limits it will not warn you about:
  - Symbol lookup is name-resolved, not namespace-qualified. One type name living in two modules cross-attributes its callers and its coverage, and can imply a dependency edge the codebase's own tests forbid. Rank with the counts; confirm every edge at an import or call site before drawing it.
  - Route and decorator extraction sees literal arguments only. A schema-driven or table-driven router yields test fixtures instead of the real surface — a route query returning 3 nodes, all from one test file, for an 18-endpoint service is that failure. Enumerate the surface from the registration table and cite it.
  - Pass subagents the absolute path to the binary. A version-manager shim is not reliably on a subshell's `PATH`.

## 7. Fallback paths

- **Very few markers found** (codebase has been aggressively cleaned, or the team's culture is "no TODO in code"): the Explicit markers section may be small. State it explicitly. Still emit the section.
- **Hard to find ≥ 10 register rows:** dig deeper into smells. If a codebase is genuinely clean (rare), state it explicitly and emit whatever count you reached. Note the limitation in the intro.
- **A smell is borderline judgment-call:** include it, but mark the description with `*judgment-call*` and explain in one sentence why it's worth flagging.
- **Duplicated-logic detection misfires** (false positives from boilerplate): drop the row and note in the Work log. Boilerplate is not debt.

## 8. Success criteria

- [x] `docs/insights/tech-debt.md` exists on disk.
- [x] H1 line reads `# remarkable-spec · Tech debt`.
- [x] Intro states the assembly methodology.
- [x] Three content H2 sections in fixed order.
- [x] `Ranked register` has ≥ 10 rows.
- [x] Every register row uses the closed category vocabulary.
- [x] Every register row uses the closed cost vocabulary (`S` / `M` / `L`).
- [x] Every Explicit-markers bullet quotes the comment verbatim and cites `path:LOC`.
- [x] `Pattern-level smells` has up to 5 H3 entries; each lists 2–5 representative citations.
- [x] No YAML frontmatter on the output.
- [x] Prior-artifact check ran: either the output path held no file, or every claim carried over from the previous version was re-verified against source at its current line numbers.
- [x] The Work log names what the prior artifact got wrong — stale line numbers, wrong counts, fabricated edges, inverted rules — or records that no prior version existed.
- [x] No citation resolves into a generated or gitignored path (`git check-ignore` every cited path).

When a property is mechanically checkable — a citation resolving to a real file and an in-range line, a count, a heading shape, a table's column set, a closed vocabulary — check all of it with a script and paste the command and its output into Validation; reserve spot-checks for judgment properties, where the question is whether the prose is right rather than whether the string exists.

Every criterion above that counts, orders, or constrains H2 sections applies to content H2s only; the trailing `## See also` footer written by the cross-link pass is exempt from H2 counts, from alphabetical or fixed ordering, and from per-H2 required elements.

## 9. Anti-goals

- Do not invent debt. Every register row, marker, and smell instance traces to a source read.
- Do not paraphrase comment text. Quote it verbatim.
- Do not use improvised category names. Stick to the closed vocabulary.
- Do not write YAML frontmatter on the output file.
- Do not emit emojis.

## 10. Out-of-scope findings

- `docs/.packets/_environment.md:196` — cites `mise.toml:10-14` for the gates table, but `mise.toml`
  is 13 lines (`awk 'END{print NR}'`). A reader who opens the range finds it truncated at `check` and
  cannot tell whether a fifth task was meant to exist.
- `docs/.packets/_environment.md:198-199` — cites `lefthook.yml:1-14` for the pre-commit block and
  `lefthook.yml:16-27` for commit-msg. The file is 24 lines, so `:16-27` runs past its end, and the
  pre-commit block actually ends at `:11`. A reader validating citations against this file gets an
  out-of-range failure that looks like their own error.
- `docs/.packets/_environment.md:280`, `:383`, `:417` — all three say `README.md:143` invokes
  `uvx ty check src/`. The line is `README.md:142`; `:143` is the closing code fence. Three agents
  inheriting this will each publish the same off-by-one, and the finding it supports (configured
  checker is not the invoked one) is correct, so the error is invisible unless someone opens the file.
- `docs/.packets/_environment.md:266` — describes `.erpaval/sessions/` as "gitignored".
  `git check-ignore .erpaval` exits 1: it is not ignored. It stays out of `git status` only because it
  contains zero files, which git does not report.
- `docs/.packets/_environment.md:395` — instructs agents never to cite paths under `.codegraph/`,
  `.pytest_cache/`, `.ruff_cache/`, or `.erpaval/` "because the Phase 6 validator runs
  `git check-ignore`". None of those four is gitignored; only `dist/` and `.venv/` are, at
  `.gitignore:5` and `:10`. The three cache dirs stay invisible because pytest, ruff, and codegraph
  each write a self-ignoring `.gitignore` inside their own directory. The don't-cite instruction is
  still correct advice; the stated reason is not, so a reader concludes `.gitignore` covers tool
  caches when it covers none of them.
- `CLAUDE.md:40` — states that the SHA-256 `rm_hash` is the cache-invalidation key for OCR and
  diagram results. The OCR cache the CLI actually uses is a `.ocr.txt` filesystem sidecar keyed by
  file path (`src/remarkable_spec/cli/search_cmd.py:196`); the `rm_hash`-keyed `ocr_cache` table has
  no live writer. A reader trusting this line will look for a hash-keyed cache and not find the one
  that runs. Captured as register row 11 in the output.
- `README.md:127` — lists `pillow` in the `[render]` extra. Nothing in `src/` imports `PIL`. A reader
  installing the extra pays for a dependency the code cannot reach. Captured as register row 19.

Defects you notice in files outside section 2 (Scope) are recorded here and nowhere else. Do not edit those files, do not fix them in passing, and do not widen your Scope to cover them. One bullet each: the `path:LOC`, one sentence on what is wrong, one sentence on what a reader gets wrong because of it. In range: stale or contradicted claims in other docs you read, disagreements between two docs, and source defects you tripped over while verifying a citation. Leave the section reading `_none_` when you found nothing, so an empty finding list is distinguishable from a skipped section. The orchestrator sweeps this section from every packet once the fan-out completes.

---

## Work log

### Step 1 — inputs read, prior-artifact check (§4.1)

Read in full before any drafting: this packet, then `docs/.packets/_environment.md` (442 lines,
measured at commit `4bb899d`). Its stale-prior traps subsection (`docs/.packets/_environment.md:396-442`)
treated as binding.

**Prior artifact: none.** `docs/insights/` does not exist on disk (`ls docs/insights/` →
"No such file or directory"), and `git log -1 --format=%cs -- docs/insights/tech-debt.md` returns
empty, so the path was never committed either. **There is nothing the prior artifact got wrong,
because there is no prior artifact** — this is a first run over a repo whose `docs/` tree holds only
`.packets/` and `.repomix/`. No claim is inherited; every citation in the output is verified against
source in this run. The scripted range check in Validation therefore stands in for the
newer-than-doc partition described in §4.1.

**Marker scan re-run, confirming the packet's premise:**
`grep -rn -E 'TODO|FIXME|HACK|XXX' src/ tests/ --include='*.py'` → no output, exit 1. Zero markers.
Handled as a methodology trigger per §3a, not as a finding of cleanliness.

`git ls-files` → 67 tracked files, 56 of them `.py`. `tests/` holds exactly `tests/__init__.py` at
0 bytes (`wc -c`), matching the §3a directive: no test-coverage claims anywhere in the output.

Scratch dir created at `/tmp/doc-insights-tech-debt/`.

### Step 2 — re-verified all eleven §3a leads at current line numbers

Each lead re-grepped or re-read. Result: **eight confirmed exactly, three carried a wrong offset or a
wrong identifier.** Corrections adopted in the output:

| §3a lead | Verdict |
| --- | --- |
| Zero tests vs 10,321 LOC | Confirmed. `tests/__init__.py` 0 bytes; `pyproject.toml:74-76` pytest config; `mise.toml:9` `test` task. **Packet said `mise.toml:11`; the `test` task is at `:9`** (`:11` is `format`). |
| `_invoke_bedrock_vision` x3 | **Partly wrong.** Only two functions carry that name: `src/remarkable_spec/ocr/postprocess.py:187` and `src/remarkable_spec/ocr/diagram.py:286`. The third site is named `_invoke_annotation_analysis`, defined at `src/remarkable_spec/cli/annotations_cmd.py:251` — `:254` is its `model_id` parameter line, not the `def`. The body is still a third copy of the same Bedrock call. Docstring cross-reference confirmed at `src/remarkable_spec/ocr/diagram.py:295`. |
| Model ID in four places | Confirmed exactly: `src/remarkable_spec/ocr/postprocess.py:23`, `src/remarkable_spec/ocr/diagram.py:57`, `src/remarkable_spec/ocr/pipeline.py:90`, `src/remarkable_spec/cli/annotations_cmd.py:254`. No `model` field in `RmspecSettings` (`src/remarkable_spec/cli/_util.py:13-60`, seven fields, read in full). |
| 27 broad `except` sites | Confirmed, count 27. `src/remarkable_spec/device/sync.py:276-277` verified verbatim. Additional fact the lead omitted: **zero truly bare `except:`** — `grep -rnE '^\s*except\s*:'` exits 1. All 27 name a class. |
| Floating lower bounds | Confirmed: `pyproject.toml:14` `cyclopts>=3.0.0`, `:30` `paramiko>=3.4.0`, `:13` `rmscene>=0.7.0,<0.8.0` the only upper bound. |
| Python version four ways | Confirmed: `.python-version:1` = 3.13, `mise.toml:2` = 3.12, `pyproject.toml:10` `>=3.12`, `:60` `py312`, `:71` `3.12`. |
| Configured checker ≠ invoked | Confirmed and **escalated** — see Step 3. `pyproject.toml:70-72` pyright, `:52` `pyright>=1.1`, against `mise.toml:12` and `README.md:142` (**packet and brief both said `README.md:143`; the line is `:142`**, file is 147 lines). |
| Three DPI defaults | Confirmed, and sharper than stated: `src/remarkable_spec/cli/_util.py:52-55` `dpi=226` is exactly `RM2_SCREEN`'s DPI (`src/remarkable_spec/models/screen.py:80`), not the Paper Pro's 229 (`:83`). `src/remarkable_spec/sync/models.py:82` = 300; `src/remarkable_spec/ocr/pipeline.py:28,88` = 300. |
| macOS-only at import time | Confirmed: `src/remarkable_spec/cli/_util.py:69-72` (guard at `:69`, mutation at `:72`; the comment is `:67-68`). |
| No CI / scanners | Confirmed: `.github/` absent. **`lefthook.yml` is 24 lines, not 27** — pre-commit `:1-11`, commit-msg `:13-24`. |
| `rm_file.py` silences rmscene | Confirmed exactly: `src/remarkable_spec/formats/rm_file.py:31`, warnings at `:162` and `:169`, file is 212 lines. |

### Step 3 — ran the read-only lint, format, and typecheck gates

Never with `--fix`; no source edited.

- `uvx ruff check src/ --no-fix` → `All checks passed!`, exit 0.
- `uvx ruff format --check src/` → `55 files already formatted`, exit 0. (55 not 56: the 56th `.py` is `tests/__init__.py`, outside `src/`.)
- `/Users/lalsaado/.local/bin/ty check src/` → **exit 1, 9 diagnostics (8 errors, 1 warning)**. Full
  output saved to `/tmp/doc-insights-tech-debt/ty.txt`.

**This is the run's biggest unlisted finding: the type-check gate fails on the committed tree.**
`mise typecheck` (`mise.toml:12`) and the documented dev loop (`README.md:142`) both invoke `ty`, and
`ty` exits non-zero at commit `4bb899d`. Because lefthook only runs ruff (`lefthook.yml:1-11`) and
`mise check` (`mise.toml:13`) is not wired to any hook or CI, nothing blocks the failure. Diagnostics:
four `Path | None` propagation errors in `src/remarkable_spec/cli/diagram_cmd.py:156,159,161,164`, two
`hashes.get(...)` union errors at `src/remarkable_spec/device/sync.py:379-380`, two
module-as-return-annotation errors at `src/remarkable_spec/device/connection.py:25` and
`src/remarkable_spec/device/web_api.py:24`, and a deprecated `tempfile.mktemp` at
`src/remarkable_spec/ocr/pipeline.py:63`.

### Step 4 — structural smell scan beyond the eleven leads

Seven additional verified debt items the leads did not name:

1. **Four near-identical `*.metadata` scan loops**, two of which bypass the typed parser. Loop heads
   at `src/remarkable_spec/cli/_resolve.py:54`, `src/remarkable_spec/cli/search_cmd.py:149`,
   `src/remarkable_spec/cli/ls_cmd.py:162`, `src/remarkable_spec/cli/tree_cmd.py:122`. The first two
   call raw `json.loads` (`src/remarkable_spec/cli/_resolve.py:56` and
   `src/remarkable_spec/cli/search_cmd.py:151`); the last two call `parse_metadata`
   (`src/remarkable_spec/formats/metadata.py:36`) at `src/remarkable_spec/cli/ls_cmd.py:166` and
   `src/remarkable_spec/cli/tree_cmd.py:126`. Same input, validated in two consumers and not in the
   other two.
2. **`_render_mermaid` defined twice with opposite failure contracts** —
   `src/remarkable_spec/cli/diagram_cmd.py:281` prints to the console and returns `None`;
   `src/remarkable_spec/device/push.py:116` raises `RuntimeError`. A third raw `mmdc` shell-out lives
   at `src/remarkable_spec/ocr/diagram.py:231`. Timeouts differ (10s vs 30s vs 30s) and no site
   guards with `shutil.which`.
3. **`PenRenderer` Protocol is exported and unimplemented.** Declared at
   `src/remarkable_spec/render/pens.py:42` with the same three methods as the `BasePenRenderer` ABC
   at `:92`. `codegraph callers PenRenderer -l 50 --json` returns a single caller,
   `src/remarkable_spec/render/__init__.py`, i.e. the re-export at `:32` and `:50`. All ten concrete
   renderers subclass `BasePenRenderer` and `get_pen_renderer` (`:437`) returns `BasePenRenderer`.
4. **`SyncManager` carries two generations of pull and push in one 573-line file.**
   `src/remarkable_spec/device/sync.py:31` with `pull_all` (`:52`), `pull_document` (`:92`),
   `push_pdf` (`:149`), then `sync_pull` (`:303`) and `sync_push_file` (`:456`).
5. **15 error messages tell the user to run a command that fails in zsh.**
   `pip install remarkable-spec[render]` appears unquoted across 7 files (`src/remarkable_spec/export/png.py:106`,
   `src/remarkable_spec/export/pdf.py:62`, `src/remarkable_spec/device/connection.py:34`,
   `src/remarkable_spec/device/web_api.py:33`, plus three `__init__` docstrings). Verified:
   `zsh -c 'echo remarkable-spec[render]'` → `zsh:1: no matches found: remarkable-spec[render]`. The
   other 31 hints use the correctly quoted `uv add 'remarkable-spec[...]'` form.
6. **The `ocr` extra has no platform marker.** `pyproject.toml:43-46` lists pyobjc with no
   `sys_platform == 'darwin'` marker, and `pyproject.toml:40-42` folds it into `[all]`.
7. **`_invoke_bedrock_vision`'s fallback return is unreachable-or-broken.**
   `src/remarkable_spec/ocr/postprocess.py:235` returns `result["content"][0]["text"]` after a loop
   that already scans for a text block; with `thinking` enabled at `:207` the first block is a
   thinking block, so the only path that reaches `:235` raises `KeyError`.

**The repo's real marker vocabulary is suppression pragmas, not TODOs.** `grep -c` over `src/`:
**23** `# type: ignore[prop-decorator]` (all four `models/` files) and **1** `# noqa: F401`
(`src/remarkable_spec/cli/device_cmd.py:54`). Zero other ignore codes. These are the Explicit-markers
section's content.

**Dropped as boilerplate (§7 fallback):** the `@computed_field  # type: ignore[prop-decorator]`
repetition itself — it is a known pydantic/decorator-ordering interaction, not a defect; and the
missing `.gitignore` entries for `.pytest_cache/` and `.ruff_cache/`, since both tools write their own
self-ignoring `.gitignore` inside the cache dir. Also verified no commented-out code blocks of five or
more consecutive lines anywhere in `src/` (awk scan over `git ls-files 'src/**/*.py'`, zero hits), so
no `dead code adjacent` row rests on commented-out code.

**Output path is citable:** `git check-ignore -v docs/insights/tech-debt.md` exits 1 (not ignored).

### Step 5 — wrote `docs/insights/tech-debt.md`, 21 rows

H1 `# remarkable-spec · Tech debt`, no frontmatter, three content H2s in the fixed order. Ranked
register with 21 rows at first write, all eight closed categories used, cost in `S`/`M`/`L` only.

### Step 6 — coordinator corrections and three new findings, all re-verified

Mid-task message from the orchestrator. Verified each at its current line number before adopting:

| Item | Verdict |
| --- | --- |
| Root `__all__` is 26, not 24 | **Confirmed** by AST count: 26 names, `src/remarkable_spec/__init__.py:33-67`. I had already independently caught this while drafting and had written 24 from the brief; corrected in the output. |
| CLI registration is `:48-58`, not `:52-62` | **Confirmed**: 11 `app.command(...)` calls at `src/remarkable_spec/cli/__init__.py:48` through `:58`. I do not cite this range in the output, so no edit needed. |
| Third Bedrock copy is `_invoke_annotation_analysis` at `src/remarkable_spec/cli/annotations_cmd.py:251` | **Confirmed** — this matches my own Step 2 correction, reached independently. |
| SQLite pragmas at `src/remarkable_spec/sync/db.py:54-55` | **Confirmed**, `:53` is `row_factory`. Not cited in the output. |
| `pillow` declared, advertised, never imported | **Confirmed.** `grep -rniE '^\s*(import\|from)\s+(PIL\|pillow)' src/` exits 1. `pyproject.toml:27` pins it; the README extras table lists it at **`README.md:127`, not `:128`** as the message said. `src/remarkable_spec/export/png.py:32` promises "either `cairosvg` or `pillow`"; the runtime path at `:88-100` tries cairosvg only; `:103-112` is a `try: raise ImportError(msg)` / `except ImportError: raise ImportError(msg) from None` with byte-identical messages in both arms. |
| Typed OCR cache API is dead | **Confirmed by two methods.** grep finds only the definitions — `src/remarkable_spec/sync/db.py:174` `get_ocr`, `:192` `put_ocr`, `:216` `get_all_ocr`, `:319` `find_changed_pages` — and `codegraph callers` returns 0 for all four plus `migrate_ocr_sidecars` (`src/remarkable_spec/sync/migrations.py:113`). Live cache is the `.ocr.txt` sidecar at `src/remarkable_spec/cli/search_cmd.py:196` and `src/remarkable_spec/cli/ocr_cmd.py:165`. Sharpened with one fact the message did not carry: `CLAUDE.md:40` documents `rm_hash` as the cache-invalidation key, while the shipping cache is keyed by file path. |
| `RMSPEC_THICKNESS` / `RMSPEC_DPI` inert | **Confirmed.** `grep -rn 'settings\.thickness\|settings\.dpi' src/` exits 1 — zero readers, against 23 reader sites for the other five fields. Fields at `src/remarkable_spec/cli/_util.py:47-51` and `:52-55` (the message's `:44-56` also spans the tail of `device_password`). Literals hardcoded at `src/remarkable_spec/cli/render_cmd.py:85` (`1.5`) and `:89` (`226`). Sharpened: `rmspec env` emits only `RMSPEC_XOCHITL`, `RMSPEC_DEVICE_HOST`, and `DYLD_FALLBACK_LIBRARY_PATH` (`src/remarkable_spec/cli/env_cmd.py:41,44,56`), so neither inert var has any surface where the failure becomes visible. |

Ranked the three new rows myself rather than appending them: dead OCR cache at 11 (two parallel cache
systems, the documented one dead, M), inert env vars at 14 (user-facing silent no-op, S), phantom
`pillow` at 19 (misleading manifest plus a tautological raise, S). Renumbered the register to 24 rows
with a script rather than by hand to avoid off-by-one collisions.

## Validation

Everything mechanically checkable was checked with a script, not by eye. The script is at
`/tmp/doc-insights-tech-debt/validate.py`; it exits non-zero on the first missing file, out-of-range
line, orphan shorthand, ignored path, or structural violation.

**Citation and structure validator — final run:**

```text
$ python3 /tmp/doc-insights-tech-debt/validate.py; echo "EXIT=$?"
document          : docs/insights/tech-debt.md
register rows     : 24
smell H3 entries  : 5
distinct cited files: 40
citations         : 109 full + 60 shorthand = 169

PASS - all citations resolve, all lines in range, no orphan shorthand, no ignored paths.
EXIT=0
```

What it enforces: every full `` `path:LOC` `` resolves to a file on disk and to a line inside it
(range ends checked too); every shorthand `` `:LOC` `` has a full path earlier in the same H2 section
**and**, inside a table row, earlier in the same row; `git check-ignore` run over all 40 distinct cited
paths with zero hits; H1 string exact; no leading `---`; content H2 list equal to
`["Ranked register", "Explicit markers", "Pattern-level smells"]` in that order; 24 five-cell register
rows numbered 1..24 with no gaps; every category inside the closed eight and every cost inside
`S`/`M`/`L`; every Explicit-markers bullet carrying both a quote separator and a citation; five H3
smells each with a `Shows up in:` line, a `Cost:` line, and 2–5 citation bullets.

One validator bug found and fixed during the run: the row-cell splitter counted `Path \| None` in row 3
as a cell boundary and reported a 6-cell row. The escape is correct Markdown; the splitter now honours
`\|`. That was the only failure across the whole pass.

**Closed-vocabulary distribution — all eight categories used, cost not flattened:**

```text
  dead code adjacent   6        cost: L=2, M=8, S=14
  deprecated pattern   2
  duplicated logic     4
  error handling       2
  marker               1
  missing tests        3
  version pin          3
  wrong abstraction    3
```

**Read-only gate runs** (never with `--fix`, no source edited):

```text
$ uvx ruff check src/ --no-fix          -> All checks passed!            exit 0
$ uvx ruff format --check src/          -> 55 files already formatted    exit 0
$ /Users/lalsaado/.local/bin/ty check src/ -> Found 9 diagnostics        exit 1
$ grep -rn -E 'TODO|FIXME|HACK|XXX' src/ tests/ --include='*.py'  -> no output, exit 1
$ git check-ignore -v docs/insights/tech-debt.md                  -> no output, exit 1
```

**Prose checks:** zero emoji or `So`-category symbols (`unicodedata` scan); the only non-ASCII
characters in the file are `·` in the required H1 and the em-dash. Filler-adverb grep
(`simply|just|basically|seamlessly|leverage|robust`) exits 1 after one rewrite — "catches just as
broadly" became "catches equally broadly".

**Spot-checks reserved for judgment, not string existence.** Re-read at source rather than scripted:
the three `mmdc` timeout literals (`src/remarkable_spec/cli/diagram_cmd.py:292` = 30,
`src/remarkable_spec/device/push.py:132` = 30, `src/remarkable_spec/ocr/diagram.py:236` = 10); the
three divergent Bedrock payloads (`max_tokens` 16384 at `src/remarkable_spec/ocr/postprocess.py:205`
vs 4096 at `src/remarkable_spec/ocr/diagram.py:309` and
`src/remarkable_spec/cli/annotations_cmd.py:276`); the ten concrete pen renderers between
`src/remarkable_spec/render/pens.py:136` and `:403`; and the zsh glob failure, run for real:
`zsh -c 'echo remarkable-spec[render]'` → `zsh:1: no matches found: remarkable-spec[render]`.

**Three imprecise sentences corrected after spot-check**, each a case where the string was real but the
sentence overstated it: "15 error messages" became "15 places, six of them runtime `ImportError`
messages, the rest docstrings" (only 6 of the 15 `pip install` occurrences are in a `raise`);
"573-line class" became "573-line module" (the count is `wc -l` on the file, not the class body); and
"the four `models/` files" became "four of the eight `models/` files" (`models/` has 8 tracked `.py`).

**Not checkable, recorded as absent:** test coverage. Per §3a directive 1 the signal is structurally
absent — `tests/` holds one 0-byte file — so no criterion, row, or smell in the output is expressed in
terms of coverage, and rank 1 cites `pyproject.toml:74-76` and `mise.toml:9` rather than a line inside
a zero-line file.

## Summary

Shipped `docs/insights/tech-debt.md`: a 24-row ranked register, an Explicit-markers section, and five
pattern-level smells, carrying 169 citations across 40 files, all script-verified to resolve to a real
file and an in-range line with no gitignored path among them. **The marker count is zero** — no `TODO`,
`FIXME`, `HACK`, or `XXX` anywhere in `src/` or `tests/` — and the document says so in its first line
while treating it as a statement about commenting culture rather than about code health; the repo's real
marker vocabulary turns out to be 23 `# type: ignore[prop-decorator]` pragmas and one `# noqa: F401`.
The top smell is the error-handling one: 27 broad `except` clauses, none of them a bare `except:`, of
which ten recover with a wordless `continue` or `pass` that removes a user's document from a listing or
a sync run with no log line — and the same codebase contains the correct shape
(`logger.warning(..., exc_info=True)`) in `src/remarkable_spec/formats/document_loader.py:96`, so the
silence is a habit rather than a constraint. The most surprising item is rank 2, which the packet did
not list and which no amount of reading source would have surfaced: the documented type-check gate
**fails on the committed tree**. `ty check src/` exits 1 with eight errors at commit `4bb899d`, lefthook
runs only ruff (`lefthook.yml:1-11`), and `.github/` does not exist — so a repository that presents four
quality tasks enforces two of them, and the two it does not enforce are the two that would have caught
something. Running the gates read-only rather than trusting `mise.toml` is what found it. Runner-up for
surprise: rank 11, where the SQLite `ocr_cache` schema and the `.ocr.txt` sidecar the CLI actually
writes are two unrelated cache systems, and `CLAUDE.md:40` documents the dead one.
