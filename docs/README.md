# remarkable-spec · Documentation tree

Prose is generated; structure is mechanical. Cross-references are deterministic.

Generated 2026-08-28 against commit `4bb899d` on `main`. Every factual claim in every file below
carries a backtick `path:LOC` citation into tracked source, and the whole tree passed a citation
validator that resolves each one to a real file and an in-range line: **1,563 full citations plus 735
shorthands across 60 distinct source files, zero unresolved.**

## Where to start

New to the codebase, in this order: [system overview](architecture/system-overview.md) for what it
is, [module map](architecture/module-map.md) for where things live,
[CLI reference](reference/cli.md) for what you can run, then
[debugging guide](insights/debugging-guide.md) the first time something breaks.

Changing something: [impact analysis](insights/impact-analysis.md) tells you what a change reaches,
[contract map](insights/contract-map.md) tells you what the other side assumes, and
[business logic](insights/business-logic.md) tells you which rule you are about to break.

## Architecture

| File | What it answers |
| --- | --- |
| [system-overview.md](architecture/system-overview.md) | What is this and how do the eight packages fit together? Narrative, 20-row stack table, one `flowchart LR`. |
| [module-map.md](architecture/module-map.md) | What is in each package? One H2 per package, ordered by LOC descending, with every file and its line count. |
| [data-flow.md](architecture/data-flow.md) | How does data move? Three flows — `render`, `ocr`, `sync pull` — each with eight numbered steps and a `sequenceDiagram`. |

## Reference

| File | What it answers |
| --- | --- |
| [public-api.md](reference/public-api.md) | What can I import and call? 30 symbol entries with verbatim signatures, split into the 26 root exports and the second-tier subpackage surface. |
| [cli.md](reference/cli.md) | What commands exist and what flags? All 19 `rmspec` command paths, plus the seven `RMSPEC_` environment settings. |

## Behavior

| File | What it answers |
| --- | --- |
| [processes.md](behavior/processes.md) | When the system runs X, what happens? Eight processes with numbered steps, plus ten minor flows. |

`behavior/state-machines.md` is **deliberately absent.** The conditional gate for that file requires
at least two state machines with three or more states and two or more transitions traceable to
source. Exactly one qualifies — the tracked-document sync lifecycle. The `rm_hash`-keyed cache is
insert-only with no delete, no TTL, and no cascade, which makes it two states and one transition. The
adjudication, including the two further candidates that were examined and rejected, is recorded in
`.packets/doc-behavior-state-machines.md`.

## Analysis

| File | What it answers |
| --- | --- |
| [risk-hotspots.md](analysis/risk-hotspots.md) | Where do bugs cluster? 12-row ranking plus a five-file drill-down. Ranked on static signals only — the repo has one commit, so churn is uncomputable, and the file says so in its first paragraph. |
| [dead-code.md](analysis/dead-code.md) | What can I safely delete? Three buckets, with published-API-at-zero-consumers separated from genuinely deletable code. |

`analysis/ownership.md` is **deliberately absent.** One commit, one author: a per-person table would
be noise dressed as analysis.

## Diagrams

| File | What it answers |
| --- | --- |
| [components.md](diagrams/architecture/components.md) | What is a component and how do they relate? One `classDiagram`, 8 components, 36 methods, citations carried as diagram notes. |
| [dependency-graph.md](diagrams/structural/dependency-graph.md) | Internal modules and external dependencies on one page. One `flowchart LR` at the 20-node budget — 9 internal, 11 external — with an overflow legend. |
| [sequences.md](diagrams/behavioral/sequences.md) | What is the call order for the top processes? Three `sequenceDiagram` blocks, each with participant and edge-call-site tables. |

## Insights

The category that answers what the codebase assumes, fails like, and resists when you change it.

| File | What it answers |
| --- | --- |
| [impact-analysis.md](insights/impact-analysis.md) | If I change X, what breaks? Eight surfaces ranked by distinct inbound imports, 78 downstream-effect rows. |
| [debugging-guide.md](insights/debugging-guide.md) | When something breaks, where do I look? An 18-row failure-mode index, a log-and-error-surface map, and a ten-step first-checks ladder whose first eight steps are offline and free. |
| [contract-map.md](insights/contract-map.md) | What does module A assume about module B? Eleven contracts with producer, consumers, verbatim shape, and the assumptions each consumer makes. |
| [business-logic.md](insights/business-logic.md) | What rules does this codebase enforce? Validations, invariants, calculations, and policy — with the theme that nearly every rule here is a silent default rather than a rejection. |
| [tech-debt.md](insights/tech-debt.md) | Where is the rot, and what would I pay to fix it? A 24-row ranked register with cost-of-removal, over a codebase containing zero `TODO` markers. |

## How this tree was produced

Two stages. `npx repomix` flattened the repository into one machine-readable pack
(`.repomix/codebase.json`, 67 files, 95,134 tokens) as the shared breadth-scan input. Then 17
parallel Opus agents each took sole ownership of one output file, working from a self-contained
context packet under `.packets/` and a single shared environment brief
(`.packets/_environment.md`) carrying verified toolchain facts and a binding list of stale-prior
traps. Agents never messaged each other; the filesystem was the shared memory. A final mechanical
pass validated every citation and wrote the `## See also` footers, which rank siblings by shared
source citations.

`.packets/` is kept rather than deleted: each packet holds its agent's work log, the validation
commands it ran with output, and an out-of-scope-findings section recording defects it tripped over
in files it was not allowed to touch. That is where to look when a claim in this tree seems wrong, or
when you want to regenerate one file — re-dispatching a single packet is the refresh unit.
