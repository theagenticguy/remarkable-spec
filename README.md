# remarkable-spec

**Humans and agents working on the same reMarkable tablet while it's on over USB.**

A Python workspace and a CLI, `rmspec`, for the [reMarkable Paper Pro](https://remarkable.com/).
It reads the tablet's library while xochitl is running, renders v6 `.rm` strokes to SVG, PNG and
PDF, transcribes handwriting in cost-ordered tiers, extracts Mermaid from drawings, reads the
annotations a human left on a PDF, and pushes a new document back for them to read.

The CLI is built to be driven by a coding agent as much as by a person. Every command answers in
three modes — a table for a human on **stderr**, one JSON envelope for a parser on **stdout**, or
tab-separated records for a bounded context — and `rmspec manifest --json` describes the whole
surface so nothing has to scrape `--help`. [`AGENTS.md`](AGENTS.md) is generated from that
manifest and is the file to hand an agent.

## Why USB is the default read path

`RMSPEC_TRANSPORT` defaults to `usb`. That is not a convenience choice.

Reading the tablet over USB means asking xochitl — the tablet's own document application — for a
document through `GET /download/{id}/rmdoc`. Because xochitl serves the bytes itself, the archive
it hands back is a consistent snapshot **by construction**: there is no window in which a page is
half-written to disk while the host is copying it.

The alternative is to copy the document tree off the filesystem. reMarkable's own documentation
says of that directory:

> It is possible to copy this directory, but note that Xochitl should not be running when
> accessing and/or changing the stored documents.

**Our inference from that sentence, not reMarkable's claim:** a filesystem copy taken while the
tablet is in use can catch a document mid-write, so the transport that goes through xochitl is
the one that cannot. Everything else here follows from wanting the tablet to stay on and in the
user's hands while a host reads it.

### What USB cannot do, and says so out loud

One port has no USB binding and never will. The firmware's HTTP route table is closed at six
families and none of them serves a file out of the document tree, so there is no route that
returns the tablet's own handwriting-search index. A `usb` run that wants `rmspec search` — or
OCR tier 0, which is the tablet's own reading of a page — therefore opens an SSH session *as
well*. `rmspec doctor` reports that rather than hiding it:

```text
$ rmspec doctor --dense
port	state	detail
DeviceCatalog	served
RawBundleSource	served
SearchIndexSource	limited	read the on-device search index without opening an SSH session is not possible over usb_web_api; it needs ssh
HandwrittenTextIndex	limited	read the on-device search index without opening an SSH session is not possible over usb_web_api; it needs ssh
DeviceFactsSource	limited	read the firmware version, the board model and the free space is not possible over usb_web_api; it needs ssh
DeviceFactsSource	limited	read the serial the tablet UI shows is not possible over usb_web_api; it needs no transport
DocumentUploader	limited	create a document inside a folder is not possible over usb_web_api; it needs ssh
DocumentUploader	limited	update a document already on the tablet is not possible over usb_web_api; it needs no transport
DocumentUploader	limited	delete a document the host just created is not possible over usb_web_api; it needs no transport
```

`it needs no transport` means no transport can do it at all — not that SSH could. `POST /upload`
is **create-only, root-only and irreversible**: no HTTP route deletes, so a document pushed by
mistake costs a manual delete on the tablet. `rmspec push` therefore refuses everything
checkable before the first byte goes out, including a name the library root already holds.

## Install

```bash
mise run install       # the whole workspace, every extra, from the lockfile
uv run rmspec --help
```

Every command in this repository lives in `mise.toml` and nowhere else, so `mise run install` is
the install rather than a wrapper around one. Without `mise`, its body is
`uv sync --all-packages --all-extras`.

That is the *development* install. To use the CLI without the workspace, build the single
distribution and install that:

```bash
mise run build                          # dist/rmspec-0.2.0-py3-none-any.whl, and an sdist
uv tool install "dist/rmspec-0.2.0-py3-none-any.whl[push,vision]"
rmspec --version                        # 0.2.0
```

Nine distributions is a development-time boundary, not a shipping decision: `rmspec-cli`'s own
wheel requires eight `rmspec-*` distributions that exist on no index, so it is installable
nowhere but here. `mise run build` therefore stages a tenth distribution, `rmspec`, whose
`src/rmspec/` holds all nine subpackages and whose metadata is the **union** of their
third-party requirements — one wheel, one `rmspec` command, no workspace. The staged tree is
derived from the nine manifests on every build and never committed, so it cannot drift from
them; two members asking for one requirement with different specifiers fails the build rather
than being silently reconciled.

Two optional extras, and nothing else is optional:

| extra | packages | for |
| --- | --- | --- |
| `rmspec-cli[push]` | `markdown`, `weasyprint` | converting a `.md` to PDF before upload |
| `rmspec-ocr[vision]` | `pyobjc-framework-vision`, `pyobjc-framework-quartz` | Apple Vision, macOS only |

`cairosvg`, `pillow` and `pymupdf` are ordinary dependencies of `rmspec-export`, not an extra.
(The old `[render]` extra advertised `pillow` while nothing imported `PIL`; an architecture test
now fails the build when a package declares a distribution it imports nowhere, so that cannot
recur.) `weasyprint` and `cairo` link against native libraries — `rmspec doctor` names any that
will not load, and a missing one is a `MissingDependencyError` naming the install, never an
`OSError` from inside a conversion already begun.

## The command surface

Fourteen invocations. This is `rmspec manifest --dense`, verbatim:

```text
command	response_types	help
doctor	capabilities	Report what this binding can do, and what it is missing.
env	settings	Print the resolved settings as shell assignments you can ``eval``.
device info	facts	Report what the attached tablet is, and name every fact it cannot answer.
ls	catalog	List the documents and folders the tablet holds.
read	document	Resolve one document selector and report what the catalog knows about it.
render	render	Render pages of one document into SVG, PNG or PDF files in a directory.
ocr	transcription	Transcribe handwriting on pages of one document, paying only for the tiers needed.
diagram	diagrams	Extract the Mermaid of any diagram drawn on a document's pages.
annotations	annotations	Read the handwritten annotations on a PDF-backed document, page by page.
search	matches	Find a term in transcribed page text, saying of every hit which source read it.
sync	sync,history	Pull the tablet's library into the local mirror, predict a pull, or read past pulls.
push	created	Place one new document on the attached tablet, visible immediately.
reply	reply	Write an ink reply onto one page of a document on the attached tablet.
manifest	manifest	Describe every command, error, degradation and setting this CLI has.
```

`response_types` is every `type` discriminator the command's envelope can carry, so a caller
picks its parser before it calls. It is a list because `sync --history` answers `history` rather
than `sync` — `rmspec manifest --json` reports `["sync", "history"]` for that one row.

Alongside the commands the manifest publishes **49 error identities** with their exit codes, the
**9 closed degradation kinds**, and the **15 `RMSPEC_*` settings**. Those four sections are the
whole contract; `AGENTS.md` is them rendered as Markdown.

## Three output modes

| mode | flag | stream | for |
| --- | --- | --- | --- |
| human | *(default)* | stderr | a person |
| json | `--json` | stdout | an agent that parses |
| dense | `--dense` | stdout | an agent that greps, or a bounded context |

**stdout is the machine's, stderr is the human's.** Tables, degradation notices and error lines
all go to stderr, which is what makes `rmspec ls --json | jq` clean by construction and
`rmspec ls 2>/dev/null` correctly silent. Passing both flags is a `UsageError`: a run has exactly
one output mode.

A document selector is a name substring, a full uuid, or a uuid prefix of eight or more
characters, tried in that order.

```text
$ rmspec ls Test --dense
kind	uuid	name	page_count	parent_uuid	unrooted
document	31833079-193f-40cd-86fc-fc78b4f26cfd	TestNb		9f62566c-c916-43e3-9f32-f63b4fe88d86	false
folder	9f62566c-c916-43e3-9f32-f63b4fe88d86	Test			false
```

```text
$ rmspec render TestNb /tmp/out --pages 0 --format png --dense
name	media	uri	byte_count	committed
page-0000	png	file:///private/tmp/out/page-0000.png	176581	true
```

`--pages` is **0-based**, matching `page_index` in every payload: a comma-separated list of
indices and inclusive `A-B` ranges, as in `0`, `2-5` or `0,3,7-9`. A human typing `--pages 1`
gets the second page. That cost is deliberate — the primary caller has just read `page_index` out
of a previous payload, and making it add one is the off-by-one this project keeps finding.

Failures are the same envelope with `type: "error"`, and the exit status is carried inside the
document as well as returned to the shell:

```text
$ rmspec read "no-such-document-anywhere" --json; echo "exit=$?"
{
  "api_version": "rmspec/v1",
  "type": "error",
  "error": {
    "type": "DocumentNotFound",
    "message": "no document in device catalog matches 'no-such-document-anywhere'",
    "remediation": null,
    "exit_code": 66
  }
}
exit=66
```

Nothing a run had to substitute or skip is ever swallowed. A success envelope always carries a
top-level `degradations` array — `[]` when there were none — drawn from a closed set of nine
kinds, so a caller decides once per kind instead of matching strings.

## The agent loop

The loop the project exists for: an agent writes Markdown, a human annotates it with a pen, the
agent reads the annotations back.

```bash
rmspec push proposal.md --json          # Markdown -> PDF -> the tablet, visible immediately
# the human marks it up on the tablet
rmspec sync                             # pull the library into the local mirror
rmspec annotations proposal --json      # read the marks back, page by page
```

`rmspec push` accepts `.md`, `.markdown`, `.pdf` and `.rmdoc`. A `.md` is converted to HTML with
`markdown` and then to PDF with WeasyPrint, at the size of the tablet's own page rather than A4,
so the reader does no scaling. The page count comes from the renderer that laid the pages out, so
a Markdown file with no renderable content produces a document with no pages and is **refused**
rather than uploaded as a valid, empty PDF.

`--parent` is handed to the transport verbatim and never quietly turned into a placement at the
root: the USB import route has no destination parameter, so it refuses and names SSH as the
transport that can.

## OCR pays for the cheapest tier that answers

`rmspec ocr` runs four tiers and stops as soon as it has an answer it trusts. It reports
`tier_reached` and `short_circuited`, so the cost of a run is visible in the payload.

| tier | who reads | notes |
| --- | --- | --- |
| 0 | the tablet's own handwriting index | needs SSH, even on a `usb` run |
| 1 | a recogniser — AWS Textract, or Apple Vision on macOS | `RMSPEC_OCR_ENGINES`, default `textract` |
| 2 | `global.openai.gpt-5.6-luna` reads the raster itself | `RMSPEC_READ_MODEL` |
| 3 | `global.openai.gpt-5.6-terra` adjudicates tiers 0–2 | `RMSPEC_MERGE_MODEL` |

When tier 0 and tier 1 agree at or above `RMSPEC_AGREEMENT_THRESHOLD` (0.90), tiers 2 and 3 are
skipped and never billed. Both models are called through Bedrock in `RMSPEC_AWS_REGION`. One
threshold is not right for every hand, which is why it is a setting and why `--threshold`
overrides it per run.

`rmspec diagram` extracts the Mermaid of a drawn diagram and reports a skip as data, never as an
error. It does **not** check that the Mermaid parses: that would need a Node toolchain, so
validity is reported as *not attempted* rather than silently as *valid*.

Every model call and every render is capped at the entry boundary by `RMSPEC_MAX_PAGES`
(default 64), enforced before the first page is decoded. One 432-page document cannot silently
become 432 model calls.

## Settings

Fifteen variables, all `RMSPEC_`-prefixed. A typo in one fails the run at startup and names the
closest match. `rmspec env` prints the resolved values as assignments a shell can `eval`:

```text
$ rmspec env
export RMSPEC_DEVICE_HOST=10.11.99.1
export RMSPEC_DEVICE_USER=root
export RMSPEC_SSH_KEY=~/.ssh/id_ed25519_remarkable
export RMSPEC_SYNC_DB=~/.remarkable-spec/sync.db
export RMSPEC_RENDER_DPI=229
export RMSPEC_OCR_DPI=300
export RMSPEC_THICKNESS=1.5
export RMSPEC_MAX_PAGES=64
export RMSPEC_TRANSPORT=usb
export RMSPEC_AWS_REGION=us-west-2
export RMSPEC_READ_MODEL=global.openai.gpt-5.6-luna
export RMSPEC_MERGE_MODEL=global.openai.gpt-5.6-terra
export RMSPEC_OCR_ENGINES=textract
export RMSPEC_AGREEMENT_THRESHOLD=0.9
```

Two of those defaults are absolute paths under the running user's home; they are shown here as
`~` and the command prints them in full.

`RMSPEC_RENDER_DPI` is 229 because that is the Paper Pro panel's own density — 2700 pixels of
diagonal over 11.8 inches — so a render is 1:1. It read 226 until 2026-08-30, which is the
reMarkable 2's density, inherited from a legacy setting nothing read.
`RMSPEC_THICKNESS` is 1.5, a stroke-weight multiplier compensating export weight against
on-screen weight. `RMSPEC_TRANSPORT` takes `usb`, `ssh` or `mirror`; `mirror` — reading a copied
document tree named by `RMSPEC_XOCHITL` — is declared but not yet implemented, and refuses while
naming the transports that do serve a listing rather than quietly reading the tablet instead.

The full table, with every type and default, is in [`AGENTS.md`](AGENTS.md) or in
`rmspec manifest --json`.

## Architecture

Nine packages in a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/), with
the dependency direction enforced by tests rather than by convention. `rmspec.domain` is pure and
imports nothing of ours; `rmspec.app` imports only the domain; the adapters import only the
domain; the CLI is the one place that composes them, through
[dishka](https://github.com/reagento/dishka).

| package | contents |
| --- | --- |
| `rmspec-domain` | Models, ports and the error tree. Depends on `pydantic` alone. |
| `rmspec-app` | Use cases. Imports `rmspec.domain` and nothing else. |
| `rmspec-formats` | Parsers for v6 `.rm`, `.metadata`, `.content`, `.pagedata`, via `rmscene`. |
| `rmspec-render` | Ten pen-physics models and SVG generation. No native dependencies. |
| `rmspec-export` | SVG, PNG and PDF writers, plus PDF page rasterization. |
| `rmspec-device` | USB web API, SSH, and the read-only spec probe harness. |
| `rmspec-ocr` | Apple Vision, AWS Textract, Bedrock. |
| `rmspec-persistence` | SQLite adapters. The only package permitted to import `sqlite3`. |
| `rmspec-cli` | The `rmspec` commands and the composition root. |

The boundaries are load-bearing and checked: `tests/architecture/` asserts the import direction,
that no CLI command module imports an adapter at all (it reaches them by string through the
container), that a declared dependency is imported somewhere, that a package with a banned import
is the one package allowed it, and that no credential-shaped literal is committed anywhere.

The legacy single-distribution tree at `src/remarkable_spec/` **was deleted on 2026-08-30** —
10,321 lines across 55 modules — after the nine packages above replaced all of it.

It was worth keeping until then for one reason: two differential suites parsed each corpus page
with *both* implementations and compared the rendered SVG byte for byte, so the rewrite had an
independent oracle rather than a self-check. The oracle's last word was the strongest one it ever
gave: re-pointed at `rmspec.formats`, all thirty recorded SVG hashes reproduced **byte-identically**,
which means the two codecs agreed on every point rather than merely on stroke counts.

Those thirty hashes are still asserted, but the claim has changed and the suites say so in their
own docstrings: nothing re-derives them any more, so they are a **regression pin** on this
project's own output, not evidence that a second implementation agrees. A green run there should
not be read as more than that.

## Development

`mise.toml` is the single source of every command in this repository. `lefthook.yml` and CI both
call `mise run <task>` and never spell a command out themselves — that is the mechanism, not a
convention, and it is why the three cannot drift.

```bash
mise run check     # everything that must pass before a push
```

| task | what it runs |
| --- | --- |
| `install` | sync the workspace venv from the lockfile |
| `lint` / `lint-check` | ruff, autofix or the CI form |
| `format` / `format-check` | ruff format, write or check |
| `typecheck` | `ty check --error-on-warning` — every diagnostic is an error |
| `arch` | the architecture invariants |
| `agents-md` / `agents-md-check` | regenerate `AGENTS.md`, or fail if it has drifted |
| `test-fast` | only the tests reaching changed code, via testmon |
| `test` / `test-cov` | the full suite, parallel; `test-cov` adds the coverage gate |
| `test-hardware` | the tests that need the tablet attached. Never runs in CI. |
| `bundle` / `build` | stage the single-distribution tree, then build the one wheel and sdist |
| `check` | `lint-check`, `format-check`, `typecheck`, `arch`, `agents-md-check`, `test-cov` |
| `versions` | the resolved tool versions, to prove local and CI agree |

Ruff runs at `select = ["ALL"]` with eleven ignores, numpydoc docstrings, and a ban on relative
imports and on function-local imports. `ty` runs with every diagnostic promoted to an error.
Coverage is measured with `branch = true` against a 90% floor, and CI additionally requires 100%
coverage of changed lines via `diff-cover`. The run behind this paragraph reported **99.99%**:
every statement in every package covered, with a single partially-taken branch in
`rmspec-export/_cairo.py`.

`AGENTS.md` is generated, not written. `mise run agents-md-check` regenerates it into memory and
fails on any difference, so a command that ships without regenerating breaks the build instead of
misleading a caller.

## Tested on

- **macOS Tahoe** on Apple Silicon, Python 3.13.
- **reMarkable Paper Pro** on firmware **3.27.3.0**, attached over USB. Every device claim in
  this file was measured against that tablet.

The firmware's own device code names are `rm1`, `rm2`, `ferrari` (Paper Pro), `chiappa` (Paper
Pro Move) and `tatsu` (Paper Pure). Only `ferrari` has been exercised here; the others are named
because the firmware names them, not because they are supported.

## License

MIT
