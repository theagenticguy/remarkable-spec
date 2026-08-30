# rmspec

Read and write a reMarkable Paper Pro from this host while a person is holding it.

The tablet stays on. Nothing here asks anyone to put it down, close a document, or stop
writing. That is the whole design, and it is why you can work on a page a person is still
using.

You do not need to know anything about `.rm` files, stroke formats, OCR engines, transports
or caching to use this well. If you find yourself reading about any of those, you have gone
the wrong way.

> This document is written by hand, not generated. `rmspec manifest --json` is the
> authoritative surface; a test asserts that every command and flag named below exists in
> it, so the instructions cannot tell you to run something that is not there. The judgement
> — who does what, and what to confirm first — is prose, and only prose can carry it.

## The contract

- **stdout is yours. stderr is the human's.** `--json` puts one envelope on stdout; the
  default puts a table on stderr. `cmd --json > f.json` is always clean.
- **Every command has three modes.** Default (human table), `--json` (one envelope),
  `--dense` (tab-separated, for a small context window). Never scrape the default.
- **The envelope is always the same shape**: `{api_version, type, data, degradations, next}`.
  Branch on `type` before you touch `data`. Read `next` — it says what to do next.
- **`degradations` is the tool admitting what it did badly.** If it guessed, skipped or
  substituted something, it is in there. Surface it; do not swallow it.
- **Exit codes are `sysexits.h`, not 0/1.** Branch on the number, not the message.
- **`--pages` is 0-based**, matching `page_index` in every payload.
- **`--pages` and `--limit` are mutually exclusive.** Pick one.

## Start here, in this order

```
rmspec manifest --json   # every command, flag, error and setting. Never scrape --help.
rmspec doctor --json     # what works right now, what does not, and why not
```

`doctor` is not decoration. It says which ports are served and which are limited, with the
reason in plain language. If a command later refuses, `doctor` already knew. Run it once at
the start of a session and believe it.

## Who can do what

The asymmetry is the point. A person can change anything; you can add but almost never
alter.

| The person, with a stylus | You, from this host |
| --- | --- |
| write or draw on any page, any time | read the library — `ls`, `read` |
| annotate a PDF you placed | read those annotations — `annotations` |
| draw a diagram | get it back as Mermaid — `diagram` |
| read an ink reply you wrote | write ink onto one page — `reply` |
| move, rename, delete, reorganise | **none of these. You cannot delete anything.** |
| keep working while you read | render pages to SVG, PNG or PDF — `render` |
| — | transcribe handwriting — `ocr` |
| — | search transcribed text — `search` |
| — | place a new document — `push` |
| — | mirror the library locally — `sync` |

Two commands change what the person sees. Everything else is read-only.

- **`push`** places a *new* document. It is create-only and **irreversible** — no route
  deletes, so a mistake costs the person a manual delete on the tablet. Confirm with the
  human before you push.
- **`reply`** draws real ink on a real page of a real document. Confirm before you write. It
  defaults to blue, so the person can tell your hand from theirs.

There is no update, no rename, no move and no delete. If a task needs one, the answer is
"ask the person to do it", not a workaround.

## Recipes

**Read what someone wrote.**

```
rmspec ls --json
rmspec ocr "Sprint notes" --json
```

`ocr` pays for the cheapest thing that answers and reports what it paid. Blank pages cost
nothing.

**Answer in ink — the loop this tool exists for.**

```
rmspec ocr "Sprint notes" --json
echo "Shipped. Costs are in the doc." | rmspec reply "Sprint notes" --page PAGE_ID
```

`--page` takes the **`page_id`** that the `ocr` and `render` payloads report for every page
they touch, *not* an index. Pipe prose on stdin rather than passing it as an argument —
shell quoting mangles paragraphs. The ink face is the 95 printable ASCII characters, so an
em dash, a curly quote or an ellipsis is refused unless you pass
`--allow-substituted-characters`, and the substitution is then reported as a degradation.
Write plain ASCII and the question never comes up.

**Hand someone something to mark up, then read their marks.**

```
rmspec push briefing.md
rmspec annotations "briefing" --json
```

A `.md` file becomes a PDF on the way in, diagrams and images included. `--parent` places it
inside a folder and needs more than USB; `doctor` says so before you try.

**Get a drawing as code.**

```
rmspec diagram "Architecture" --json
```

It reports "not checked" rather than claiming the Mermaid parses. It cannot verify that, so
do not read the absence of an error as validity.

**Find something.**

```
rmspec sync --json
rmspec search "agreement threshold" --json
```

Every hit says which source read it, so you can tell your transcription from the tablet's
own reading. `search` reads more than USB serves; `doctor` says so.

**Show a person a page.**

```
rmspec render "Sprint notes" out/ --format pdf --json
```

## Costs and caps

- `RMSPEC_MAX_PAGES` (default 64) caps every render, raster and model call **before** the
  first page is touched. A 432-page document cannot silently become 432 calls. `--max-pages`
  overrides it for one run.
- `sync --dry-run` and `render --dry-run` predict without doing.
- Repeat reads of unchanged pages are free.
- `ocr`, `diagram` and `annotations` can cost money per page. `ls`, `read`, `render`, `sync`
  and `search` do not.

## When it refuses

| exit | what happened | do this |
| --- | --- | --- |
| 0 | fine | — |
| 2 | your invocation is wrong, or a selector matched several documents | read `next`; pass `--strict` to refuse an ambiguous match instead of accepting the ranked winner |
| 65 | a document or page is malformed | report it; do not retry |
| 66 | not found | list first, then select |
| 69 | the tablet is unreachable, or a transfer broke | check the cable, then `doctor` |
| 70, 73 | render or export failed | `doctor` names any native library that will not load |
| 77 | authentication failed | `doctor`, then the SSH key setting |
| 78 | the environment is wrong | the error names the exact setting and what it must be. Fix that; do not work around it |

Every error carries a remediation when one exists. Use it. Retry only what the error says is
retryable — everything else gives the same answer forever.

## Do not

- **Never restart the tablet's software.** Not as a fix, not "to be safe". Repeated restarts
  reboot the device out from under the person holding it. Nothing you need requires it.
- **Never push or reply without confirming.** Both are visible to a person immediately and
  neither can be undone from here.
- **Never invent a page, a document id or a confidence.** A `null` means unmeasured. Pass it
  along as unmeasured.
- **Never scrape `--help`.** `rmspec manifest --json` is the surface, and it is generated
  from the code.

## Not your problem

Stroke formats, pen physics, which recogniser ran, cache keys, USB versus SSH, firmware
routes. All of it is handled, and `doctor` will tell you if any of it is unavailable. If you
are about to reason about one of these, run `rmspec doctor --json` instead. It already knows.
