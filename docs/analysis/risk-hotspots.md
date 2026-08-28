# remarkable-spec · Risk hotspots

Churn and activity history is unavailable for this repository, so no part of this ranking derives from
it: `git log --oneline` returns exactly one commit (`4bb899d`, dated 2026-03-06) by exactly one author,
which makes churn, co-change frequency, recency, and bus factor uncomputable rather than low. Ranking
therefore rests on a static signal set, all of it recomputable from source: file length in tracked
lines; count of broad or bare `except` handlers; the subset of those handlers whose body discards the
failure (`pass`, `continue`, or a bare `return`); `contextlib.suppress(Exception)` sites, which are the
same defect in context-manager form; the number of distinct optional-dependency extras a file imports
directly, out of the five real groups declared at `pyproject.toml:23-46` (`render`, `device`, `push`,
`aws`, `ocr`; the sixth entry, `all`, is their union at `:40-42`); invocation of an external binary
through `subprocess.run`; trust boundaries crossed (SSH to the device, HTTP to the device web
interface, AWS Textract and Bedrock, and parsing of untrusted v6 binary data through `rmscene`); and
inbound dependent count from the CodeGraph index as a blast-radius multiplier. The composition is
`2·silent_broad_except + 1·reporting_broad_except + 1·suppress_exception + 1.5·trust_boundary +
1·extras_group + 1·external_binary + LOC/100 + dependents/4`, ties broken on line count descending.

Two limitations bound what this file can tell you. First, there is no static-analysis *artifact* to
read: no committed `findings.json`, and the `.ruff_cache` and `.pytest_cache` directories that exist
locally are gitignored, so the `Open findings` counts below are recomputed from source by script rather
than lifted from a scanner's own severity labels — `error` means a finding that silently changes
observable behavior or sits on a trust boundary, `warn` means one that degrades availability or reports
before giving up. Second, and larger: this repository has **no tests at all**. `tests/` holds a single
0-byte file, `tests/__init__.py`, while pytest is configured and wired at `pyproject.toml:74-76`, so the
harness passes trivially against an empty suite. Zero test coverage is a multiplier on every row here
rather than a row of its own, because each silent handler and each trust boundary below is unguarded by
anything except reading the code. The marker-density fallback is also unavailable: there are zero
`TODO`, `FIXME`, `HACK`, or `XXX` comments anywhere in `src/`, which reflects unmarked structural debt
rather than an absence of debt.

| File | Open findings | Top owner | Citation |
| --- | --- | --- | --- |
| `src/remarkable_spec/device/sync.py` | 3 warn, 6 error | — | `:276` (573 LOC) |
| `src/remarkable_spec/cli/device_cmd.py` | 5 warn, 2 error | — | `:436` (504 LOC) |
| `src/remarkable_spec/cli/_resolve.py` | 0 warn, 3 error | — | `:166` (290 LOC) |
| `src/remarkable_spec/device/connection.py` | 4 warn, 1 error | — | `:94` (231 LOC) |
| `src/remarkable_spec/cli/search_cmd.py` | 2 warn, 2 error | — | `:88` (250 LOC) |
| `src/remarkable_spec/device/web_api.py` | 1 warn, 2 error | — | `:106` (227 LOC) |
| `src/remarkable_spec/ocr/diagram.py` | 2 warn, 1 error | — | `:231` (332 LOC) |
| `src/remarkable_spec/cli/diagram_cmd.py` | 3 warn, 0 error | — | `:288` (376 LOC) |
| `src/remarkable_spec/cli/ls_cmd.py` | 1 warn, 1 error | — | `:167` (309 LOC) |
| `src/remarkable_spec/cli/tree_cmd.py` | 1 warn, 1 error | — | `:127` (234 LOC) |
| `src/remarkable_spec/cli/sync_cmd.py` | 0 warn, 1 error | — | `:102` (425 LOC) |
| `src/remarkable_spec/formats/rm_file.py` | 0 warn, 1 error | — | `:31` (212 LOC) |

`Top owner` is `—` on every row by necessity, not omission: with one commit and one author there is no
per-file attribution to compute and any share figure would be the same constant on all 56 source files.
The `Citation` cell is a line-number shorthand against the full path in the same row, plus that file's
tracked line count. The three highest-scoring files that did not make the table are
`src/remarkable_spec/cli/annotations_cmd.py` (299 LOC), `src/remarkable_spec/device/push.py` (193), and
`src/remarkable_spec/models/document.py` (388); the last of those carries no exception-handling finding
and ranks on size plus seven inbound dependents alone.

## Per-file drill-down

### `src/remarkable_spec/device/sync.py`

**What's there.** A single `SyncManager` class (`src/remarkable_spec/device/sync.py:31`) holding six
operations over an SSH connection — `pull_all` (`:52`), `pull_document` (`:92`), `push_pdf` (`:149`),
`sync_status` (`:233`), `sync_pull` (`:303`), and `sync_push_file` (`:456`) — plus a static
`_count_pdf_pages` helper (`:438`). It is the longest file in the repository at 573 lines and the only
one that both moves data off a physical device and writes the local SQLite sync ledger, which is why it
tops the ranking on more than length.

**Recent activity.** Uncomputable. The file has one commit in its history, the repository-wide
`4bb899d`; a 30-day window (`git log --since=30.days.ago`) returns nothing at all because that commit
predates the window. No trend can be assigned.

**Owners.** No per-file owner signal exists. The repository has one author across its one commit, so an
ownership share here would be a constant rather than a measurement.

**Findings.** Six error-severity and three warn-severity. The error group is three silent broad
handlers plus one non-teardown `contextlib.suppress(Exception)` plus two trust boundaries. The three
silent handlers each convert a failure into a quieter, wrong success: `:276-277` is
`except Exception: continue` inside the device-metadata loop, so a document whose `.metadata` cannot be
fetched or parsed drops out of the change set and reads as "not present on device" rather than
"unknown"; `:132-133` and `:146-147` are `except OSError: pass` around the page-data and thumbnail
directory pulls, so a partial `pull_document` reports the same as a complete one. The suppress at `:422`
is the sharpest of the four — it discards a failure while writing the *error* record for an
already-failed pull, so the audit trail of a failed sync can itself vanish. `:448` is a broad
`except Exception` in `_count_pdf_pages` that swallows any pymupdf failure and falls back to a regex
scan for `/Type /Page` byte patterns at `:453`; a wrong page count there propagates into the
`page_uuids` list built at `:515` and therefore into the number of empty `.rm` stubs written to the
device at `:532`. On the trust boundary: `:226`, `:530`, and `:532` build remote shell commands by
unquoted f-string interpolation and hand them to `connection.execute`. Nothing attacker-controlled
reaches the shell today, because the interpolated values come from `uuid.uuid4()` at `:175-176`,
`:489-490`, and `:515` — but that safety is an unenforced invariant, and `XOCHITL_DIR` at `:48` is a
class attribute a caller can rebind. `:88-90` uses `except OSError` as type dispatch to decide whether
a remote entry is a directory or a file, which nests a second transfer inside the handler for the first.

### `src/remarkable_spec/cli/device_cmd.py`

**What's there.** The `rmspec device` command group, a `cyclopts.App` created at
`src/remarkable_spec/cli/device_cmd.py:47` with four subcommands — `info` (`:66`), `ls` (`:162`),
`pull` (`:293`), and `push` (`:443`). At 504 lines it is the second-longest file and carries the most
broad handlers after `src/remarkable_spec/device/sync.py`; `push` is a thin delegation to
`sync_cmd.push` at `src/remarkable_spec/cli/device_cmd.py:494-503`, which is the one place this file
avoids duplicating logic.

**Recent activity.** Uncomputable, for the same reason as every other file here: one commit in
repository history, nothing inside a 30-day window.

**Owners.** No per-file owner signal. One author, one commit.

**Findings.** Two error-severity and five warn-severity. The single silent handler is the most
consequential finding in the file: `:436-437` is `except Exception: pass` with the comment "SyncDB
logging is best-effort", placed around the block at `:415-435` that records a *successful* pull into the
sync database. If that write fails, the pull succeeded on disk but the ledger never learns it, so the
next incremental sync re-pulls the same document and the user sees no error either time. `:409-410` is a
second `except Exception: pass`, nested inside the broad `except Exception as exc` at `:393` — the outer
handler catches a failed pull and the inner one discards the attempt to log that failure, so a pull can
fail with nothing written anywhere but the console. `:214` and `:343` are reporting broad handlers that
print and `sys.exit(1)`, which is the acceptable shape. The trust boundary is SSH: `info` issues five
separate shell commands at `:121-129` and parses their stdout by string manipulation, so a firmware
change to `free`, `df`, or the `/sys/devices/soc0` layout silently degrades the output. Two credential
details are worth naming. The warn count includes the `[device]` extra, gated at `:54` by an import probe whose failure
path prints an install hint and returns `False`. The key path is hardcoded to
`~/.ssh/id_ed25519_remarkable` at `src/remarkable_spec/cli/device_cmd.py:111` with no setting to
override it, and the password falls back to `settings.device_password` at
`src/remarkable_spec/cli/device_cmd.py:112`, which reads the `RMSPEC_DEVICE_PASSWORD` environment
variable declared at `src/remarkable_spec/cli/_util.py:43-46`.

### `src/remarkable_spec/cli/_resolve.py`

**What's there.** The shared document-resolution module every content command routes through —
`resolve_document` (`src/remarkable_spec/cli/_resolve.py:27`) and its superset
`resolve_document_full` (`:234`), which adds `file_type`, the backing PDF path, and the `redir`-derived
page-index mapping. Four CLI modules import it — `src/remarkable_spec/cli/diagram_cmd.py`,
`src/remarkable_spec/cli/ocr_cmd.py`, `src/remarkable_spec/cli/render_cmd.py`, and
`src/remarkable_spec/cli/annotations_cmd.py` — so a resolution defect here surfaces as four unrelated
command bugs.

**Recent activity.** Uncomputable. One commit, and nothing in a 30-day window.

**Owners.** No per-file owner signal. One author, one commit.

**Findings.** Three error-severity, zero warn-severity — this file scores third despite crossing no
trust boundary and importing no optional extra, because all three of its broad handlers are silent and
each one degrades a different stage of resolution. `src/remarkable_spec/cli/_resolve.py:57-58` skips any
document whose `.metadata` will not parse, so the "No document matching" message printed at `:99`
cannot be distinguished from "the
metadata file is corrupt". `:166-167` makes `_get_page_uuids` return an empty list on a `.content`
parse failure, so the document still resolves — with zero pages — and every downstream render, OCR, or
diagram run produces nothing without reporting a cause. `:206-207` makes `_parse_content_file` return an
empty dict, which sends `file_type` to its `"notebook"` default at `:259`; a PDF-backed document then
loses both its backing PDF (the `file_type == "pdf"` branch at `:263` never runs) and its `redir`
mapping, and the docstring at `:179-180` states the consequence directly — without that mapping stroke
overlays composite onto the wrong PDF page. Two further behaviors are not exception handling but belong
in the same read. `_pick_best` at `:132-138` resolves ambiguity without prompting: on multiple matches
it sorts by page count then last-modified, prints the candidate list and "Using best match (most
pages)", and proceeds, so a wrong document can be operated on after a warning the user may not read.
And `:277` calls `_get_page_uuids` inside the per-page loop, re-reading and re-parsing the same
`.content` file once for every unannotated page.

### `src/remarkable_spec/device/connection.py`

**What's there.** `DeviceConnection` (`src/remarkable_spec/device/connection.py:38`), the paramiko
wrapper that every SSH path in the codebase goes through: `execute` (`:140`), `get_file` (`:166`),
`put_file` (`:183`), `list_dir` (`:202`), and context-manager entry and exit at `:219` and `:224`.
paramiko is imported lazily by `_import_paramiko` (`:25-35`) so the module stays importable without the
`[device]` extra, and four other modules depend on it.

**Recent activity.** Uncomputable. One commit; a 30-day window is empty.

**Owners.** No per-file owner signal. One author, one commit.

**Findings.** One error-severity and four warn-severity, and the single error is the security finding
that lifts this file from rank eight to rank four. `:94` sets
`self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())`, which accepts any host key it is
offered and records it, with no `known_hosts` check and no setting to tighten it. Over the USB link to
`10.11.99.1` — the default from `src/remarkable_spec/cli/_util.py:34-35` — that is close to harmless,
but the same class serves the Wi-Fi path documented at `src/remarkable_spec/cli/device_cmd.py:96-98`,
where it removes the only protection against a substituted host. The two
`contextlib.suppress(Exception)` sites at `src/remarkable_spec/device/connection.py:125` and `:129` are
counted as warn rather than error because they sit inside `disconnect` (`:119-131`), where swallowing a
`close()` failure during teardown is defensible; they are recorded because a text search for `except`
will not find them. `:112-117` is a reporting broad handler that re-raises as `ConnectionError` with the
host interpolated, which is the correct shape. Two design details raise the blast radius of a change
here. `execute` (`:140-164`) takes a raw command string with no quoting or escaping helper, which is
what makes the f-string call sites in `src/remarkable_spec/device/sync.py:226` reachable in the shape
they have. And the docstring at `src/remarkable_spec/device/connection.py:53-55` instructs the user to
read the device root password out of the tablet's own settings screen, so password auth is the
documented default rather than key auth.

### `src/remarkable_spec/cli/search_cmd.py`

**What's there.** The `rmspec search` command, which branches at
`src/remarkable_spec/cli/search_cmd.py:70-73` into `_search_device` (`:76`), an HTTP call to the
device's USB web interface, and `_search_local` (`:129`), which OCRs every page of every matching
notebook through Apple Vision. It is the only command that reaches the device over plain HTTP and the
only one that writes an OCR cache outside the sync database.

**Recent activity.** Uncomputable. One commit, empty 30-day window.

**Owners.** No per-file owner signal. One author, one commit.

**Findings.** Two error-severity and two warn-severity, and one of the errors is the clearest
user-input defect located anywhere in this analysis. `:88` builds
`url = f"http://{host}/search/{query}"` from the raw search term and posts it at `:91` with no
percent-encoding — a query containing a slash, a question mark, a `#`, or a space produces a wrong or
malformed request path, and the transport is unencrypted HTTP. The silent handler is `:152-153`, which
skips any document whose `.metadata` will not parse. Directly below it, `:167` parses the `.content`
file with **no** guard at all, so a corrupt `.content` raises an uncaught `JSONDecodeError` and aborts
the entire search, while a corrupt `.metadata` eight lines earlier is skipped without a word: two
adjacent reads in the same loop with opposite failure policies. The OCR cache is the other finding
worth acting on. `:196` derives a sidecar path with `rm_path.with_suffix(".ocr.txt")` and `:207` writes
the transcription there, next to the source file inside the xochitl mirror. That cache is keyed on
filename, not content, so it is never invalidated when the underlying `.rm` changes — and it bypasses
the content-addressed `ocr_cache` table keyed on `rm_hash` that
`src/remarkable_spec/sync/migrations.py:128-145` exists specifically to migrate these sidecars into.
The per-page handler at `src/remarkable_spec/cli/search_cmd.py:203-210` reports each OCR failure and
continues, so a partial result set is presented with the same summary line as a complete one. The
remaining warn is the `[device]` extra, probed at `:79`.

## See also

- [processes](../behavior/processes.md) — 15 shared source citations
- [business logic](../insights/business-logic.md) — 15 shared source citations
- [contract map](../insights/contract-map.md) — 15 shared source citations
- [debugging guide](../insights/debugging-guide.md) — 14 shared source citations
- [tech debt](../insights/tech-debt.md) — 14 shared source citations
