<!-- Generated from `rmspec manifest`. Do not edit; run `mise run agents-md`. -->

# rmspec 0.2.0 -- agent interface

Humans and agents working on the same reMarkable tablet while it is on, over USB.
Every fact below is generated from `rmspec manifest --json`, which is the
authoritative surface; when this file and that command disagree, the command wins
and someone forgot to run `mise run agents-md`.

```bash
rmspec manifest --json                          # this document's source, machine-readable
rmspec doctor --dense                           # what the composed transport can and cannot do
rmspec ls --json | jq '.data.documents[].uuid'
```

## Two conventions, before anything else

- **stdout is the machine's, stderr is the human's.** `--json` and `--dense` write to
  stdout and nothing else does; the default human rendering, every table, every
  degradation notice and every error line go to stderr. So `rmspec ls --json | jq` is
  clean by construction and `rmspec ls 2>/dev/null` is correctly silent.
- **`--pages` is 0-based**, matching `page_index` in every payload. A spec is a
  comma-separated list of indices and inclusive `A-B` ranges: `0`, `2-5`, `0,3,7-9`.
  A descending range, an empty spec, `--limit 0`, `--max-pages 0`, and `--pages`
  together with `--limit` are all refused as `UsageError` before any work is paid for.
  A human typing `--pages 1` gets the *second* page; that cost is deliberate, because
  the primary caller has just read `page_index` out of a previous payload.

## Output modes

| mode | flag | stream | for |
| --- | --- | --- | --- |
| `human` | *(default)* | stderr | a person |
| `json` | `--json` | stdout | an agent that parses |
| `dense` | `--dense` | stdout | an agent that greps, or a bounded context |

Passing both flags is a `UsageError`: a run has exactly one output mode. `--dense`
writes tab-separated records, header line first, and a tab or newline inside a cell
becomes a space -- lossy on purpose, so that `cut -f2` and `wc -l` both work. Reach
for `--json` when the exact bytes matter; reach for `--dense` when `rmspec ocr` on a
432-page document would otherwise be megabytes of JSON.

In both examples below every value is the field's **type**, not sample data.

### Success envelope

`api_version` and `type` are always present -- branch on `type`
before touching `data`. `degradations` is always present and `[]` when
there were none; it is hoisted to the top level because every result carries one, and
it is order-preserving and never deduplicated. `next` appears only when
there is an obvious next command.

```json
{
  "api_version": "rmspec/v1",
  "type": "<one of the command's response types>",
  "data": {
    "...": "the use case's result, snake_case throughout"
  },
  "degradations": [
    {
      "kind": "DegradationKind",
      "subject": "str",
      "detail": "str",
      "substituted": "str | None"
    }
  ],
  "next": {
    "command": "str",
    "purpose": "str"
  }
}
```

`data` carries its own `degradations` as well, and the difference is
deliberate: the top-level tuple is everything that happened during the invocation,
document resolution included, while `data.degradations` is what the use
case itself recorded. The top level is a superset, not a duplicate.

### Failure envelope

`error.type` is the error class name from the closed set below, and
`error.exit_code` is the status the process also exits with -- carried inside
the document so a caller needs one channel rather than two. `remediation` is always
present and often `null`; `candidates` is dropped entirely unless the failure really
searched, which today is `AmbiguousDocument` alone.

```json
{
  "api_version": "rmspec/v1",
  "type": "error",
  "error": {
    "type": "str",
    "message": "str",
    "remediation": "str | None",
    "exit_code": "int",
    "candidates": "tuple[DocumentCandidate, ...] | None"
  },
  "next": {
    "command": "str",
    "purpose": "str"
  }
}
```

## Commands (14)

`response types` is every `type` discriminator the command's success envelope can
carry, so the parser for `data` can be chosen before the call. Where a command lists
two, a flag selects between them and that flag's row says which.

| command | response types | modes | summary |
| --- | --- | --- | --- |
| `rmspec doctor` | `capabilities` | `human`, `json`, `dense` | Report what this binding can do, and what it is missing. |
| `rmspec env` | `settings` | `human`, `json`, `dense` | Print the resolved settings as shell assignments you can `eval`. |
| `rmspec device info` | `facts` | `human`, `json`, `dense` | Report what the attached tablet is, and name every fact it cannot answer. |
| `rmspec ls` | `catalog` | `human`, `json`, `dense` | List the documents and folders the tablet holds. |
| `rmspec read` | `document` | `human`, `json`, `dense` | Resolve one document selector and report what the catalog knows about it. |
| `rmspec render` | `render` | `human`, `json`, `dense` | Render pages of one document into SVG, PNG or PDF files in a directory. |
| `rmspec ocr` | `transcription` | `human`, `json`, `dense` | Transcribe handwriting on pages of one document, paying only for the tiers needed. |
| `rmspec diagram` | `diagrams` | `human`, `json`, `dense` | Extract the Mermaid of any diagram drawn on a document's pages. |
| `rmspec annotations` | `annotations` | `human`, `json`, `dense` | Read the handwritten annotations on a PDF-backed document, page by page. |
| `rmspec search` | `matches` | `human`, `json`, `dense` | Find a term in transcribed page text, saying of every hit which source read it. |
| `rmspec sync` | `sync`, `history` | `human`, `json`, `dense` | Pull the tablet's library into the local mirror, predict a pull, or read past pulls. |
| `rmspec push` | `created` | `human`, `json`, `dense` | Place one new document on the attached tablet, visible immediately. |
| `rmspec reply` | `reply` | `human`, `json`, `dense` | Write an ink reply onto one page of a document on the attached tablet. |
| `rmspec manifest` | `manifest` | `human`, `json`, `dense` | Describe every command, error, degradation and setting this CLI has. |

### `rmspec device info`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `--resources`, `--no-resources` | `bool` | no | `true` |  | Read the memory and storage gauges as well as the fixed facts, at the cost of a second round trip. `--no-resources` reports the fixed facts alone and says of every gauge that this run did not ask for it, rather than leaving it looking unanswered. |

### `rmspec ls`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `PATH` | `str` | no | `null` |  | Limit the listing to one folder's subtree, as a `/`-separated path from the library root; each segment is matched against a folder name case-insensitively and exactly. A path naming no folder is refused rather than answered with an empty listing, since an empty answer cannot be told apart from an empty folder. |
| `--tree` | `bool` | no | `false` |  | Indent the hierarchy for a person instead of listing documents flat. A **rendering** flag: `--json` emits the same whole result either way, because it already carries both views, and `--dense` emits the same records -- so no agent has to know which flags a human typed before it can pick a parser. |
| `--source` | `Source` | no | `null` | `"device"`, `"mirror"` | Read the tablet (`device`, over USB) or a local copy of the document tree (`mirror`), overriding `RMSPEC_TRANSPORT` for this run only. Leave it off to keep the configured transport, which is the only way to read the tablet over SSH. `mirror` has no implementation yet and refuses with the transports that do serve a listing rather than quietly reading the tablet instead. |
| `--include-trashed` | `bool` | no | `false` |  | Report entries the user deleted on the tablet as well as the library. Trashed entries appear at the root because the firmware overwrites a deleted entry's parent, so its original folder is not recoverable. A no-op over USB, whose listings never contain a trashed entry at all. |

### `rmspec read`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `DOC` | `str` | yes | `null` |  | Which document: a case-insensitive substring of its name, its full uuid, or a uuid prefix of eight or more characters. The stages are tried in that order and the first that matches anything wins. A selector matching only a document in the trash is not found, because the trash is not the library. |
| `--strict` | `bool` | no | `false` |  | Refuse an ambiguous selector instead of accepting the ranked winner. The default accepts it and reports `ambiguous_auto_resolved` plus every other match, so a caller always learns that a choice was made for it; `--strict` turns the same situation into an `AmbiguousDocument` failure carrying the candidates. |

### `rmspec render`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `DOC` | `str` | yes | `null` |  | Which document: a name substring, a full uuid, or a uuid prefix. |
| `OUT` | `Path` | yes | `null` |  | The directory the artifacts are written into, created if it does not exist. Every format writes here; per-page artifacts are `page-NNNN` with the 0-based page index, and a PDF is named for the document's uuid. |
| `--pages` | `str` | no | `null` |  | Which pages to render: a comma-separated list of 0-based page indices and inclusive A-B ranges, as in 0 or 2-5 or 0,3,7-9. Mutually exclusive with `--limit`. |
| `--limit` | `int` | no | `null` |  | Render at most this many leading pages. Mutually exclusive with `--pages`. |
| `--max-pages` | `int` | no | `null` |  | Override `RMSPEC_MAX_PAGES` for this run only. The cap is enforced before any page is decoded, so one 432-page document cannot silently become 432 renders. |
| `--strict` | `bool` | no | `false` |  | Refuse an ambiguous selector instead of accepting the ranked winner and reporting the substitution. |
| `--format` | `Literal['svg', 'png', 'pdf']` | no | `"svg"` | `"svg"`, `"png"`, `"pdf"` | Which artifact to commit: `svg` for the markup, `png` for pixels, `pdf` for every selected page composed into one document. |
| `--dpi` | `int` | no | `null` |  | Raster density, overriding `RMSPEC_RENDER_DPI` (229, the Paper Pro panel's own density, so the default is a 1:1 render). Refused with `--format svg`, which carries no resolution. |
| `--thickness` | `float` | no | `null` |  | Stroke-width multiplier, overriding `RMSPEC_THICKNESS` (1.5). It reaches all three formats, because it changes the markup every one of them is derived from. |
| `--overwrite` | `bool` | no | `false` |  | Replace an artifact that is already present instead of refusing it. |
| `--dry-run` | `bool` | no | `false` |  | Report where the bytes would land and how many there are, and write none. |

### `rmspec ocr`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `DOC` | `str` | yes | `null` |  | Which document: a name substring, a full uuid, or a uuid prefix. |
| `--pages` | `str` | no | `null` |  | Which pages to transcribe: a comma-separated list of 0-based page indices and inclusive A-B ranges, as in 0 or 2-5 or 0,3,7-9. Mutually exclusive with `--limit`. |
| `--limit` | `int` | no | `null` |  | Transcribe at most this many leading pages. Mutually exclusive with `--pages`. |
| `--max-pages` | `int` | no | `null` |  | Override `RMSPEC_MAX_PAGES` for this run only. The cap is enforced before any render, raster or model call, so one 432-page document cannot silently become 432 model calls. |
| `--strict` | `bool` | no | `false` |  | Refuse an ambiguous selector instead of accepting the ranked winner and reporting the substitution. |
| `--threshold` | `float` | no | `null` |  | Agreement at or above which the tablet's own reading and a recogniser's are held to agree, which skips tiers 2 and 3. Overrides `RMSPEC_AGREEMENT_THRESHOLD` (0.90); one threshold is not right for every hand, which is why it is a setting. |

### `rmspec diagram`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `DOC` | `str` | yes | `null` |  | Which document: a name substring, a full uuid, or a uuid prefix. Several matches are ranked and the winner is used, with the choice reported as a degradation, unless `--strict` was passed. |
| `--pages` | `str` | no | `null` |  | Which pages to examine, as a comma-separated list of 0-based page indices and inclusive A-B ranges, as in 0 or 2-5 or 0,3,7-9. Every page when omitted. Mutually exclusive with `--limit`. |
| `--limit` | `int` | no | `null` |  | Examine at most this many leading pages. Mutually exclusive with `--pages`. |
| `--max-pages` | `int` | no | `null` |  | Override `RMSPEC_MAX_PAGES` (64) for this run. The cap is enforced before the first render, so one 432-page document cannot quietly become 432 model calls. |
| `--strict` | `bool` | no | `false` |  | Refuse an ambiguous selector instead of accepting the ranked winner. |

### `rmspec annotations`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `DOC` | `str` | yes | `null` |  | Which document: a name substring, a full uuid, or a uuid prefix. It must be PDF-backed -- a notebook has no printed page for a mark to sit on, so one is refused as a usage error. Several matches are ranked and the winner used, with the choice reported as a degradation, unless `--strict` was passed. |
| `--pages` | `str` | no | `null` |  | Which pages to read, as a comma-separated list of 0-based page indices and inclusive A-B ranges, as in 0 or 2-5 or 0,3,7-9. Every page when omitted. Mutually exclusive with `--limit`. |
| `--limit` | `int` | no | `null` |  | Read at most this many leading pages. Mutually exclusive with `--pages`. |
| `--max-pages` | `int` | no | `null` |  | Override `RMSPEC_MAX_PAGES` (64) for this run. The cap is enforced before the first rasterization, because this pass costs one PDF render and one model call per annotated page. |
| `--strict` | `bool` | no | `false` |  | Refuse an ambiguous selector instead of accepting the ranked winner. |

### `rmspec search`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `QUERY` | `str` | yes | `null` |  | The term to look for. Surrounding whitespace is stripped; a blank term is refused. |
| `--doc` | `str` | no | `null` |  | The uuid of one recorded document to search in, or omitted to search every recorded document. A uuid the local mirror does not track is an error rather than an empty result. |

### `rmspec sync`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `--dry-run` | `bool` | no | `false` |  | Report what a pull would do and write nothing at all -- no transfer, no mirror row, no history entry, no deletion. Cannot be combined with `--history`. |
| `--history` | `bool` | no | `false` |  | Read the recorded history of previous runs, newest first, instead of touching the tablet. Answers with no tablet attached, and emits the `history` response type rather than `sync`. |
| `--limit` | `int` | no | `null` |  | How many history entries to return. Requires `--history`. Defaults to 20, and a page above the use case's ceiling is refused rather than trimmed, so a page shorter than the limit asked for always means a short log. |

### `rmspec push`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `FILE` | `Path` | yes | `null` |  | The document to place. One of `.md`, `.markdown`, `.pdf` or `.rmdoc`. |
| `--name` | `str` | no | `null` |  | What the document should be called, defaulting to the file's own name. For a `.pdf` this becomes the name the tablet shows, verbatim and extension included; for an `.rmdoc` the archive's own metadata name wins and this is ignored by the firmware. |
| `--parent` | `str` | no | `null` |  | The uuid of a destination folder, or nothing for the library root. |
| `--allow-duplicate-name` | `bool` | no | `false` |  | Create the document even though the library root already holds the name. Off by default, because the recovery from a duplicate is a manual delete on the tablet. |

### `rmspec reply`

| flags | type | required | default | choices | help |
| --- | --- | --- | --- | --- | --- |
| `DOC` | `str` | yes | `null` |  | The document to write into: a name substring, a uuid, or a uuid prefix. |
| `TEXT` | `str` | no | `null` |  | The message, as you want it read. Omit it to read the whole of stdin instead, which is the path a paragraph should take -- shell quoting mangles prose, and the substitution report would then name characters you never typed. |
| `--page` | `str` | yes | `null` |  | The page to write on, as the device identifies it. `rmspec render DOC OUT --json` and `rmspec ocr DOC --json` both report `page_id` for every page they touch. |
| `--left-mm` | `float` | no | `15.0` |  | Left edge of the text box, in millimetres from the page's left edge. |
| `--top-mm` | `float` | no | `15.0` |  | Top edge of the text box, in millimetres from the page's top edge. The first baseline sits one em below it. |
| `--width-mm` | `float` | no | `150.0` |  | Wrap width in millimetres. Lines wrap inside it and the box does not grow sideways; its height is whatever the wrapped text needs, and a reply that would run off the page is refused before the tablet is touched. |
| `--em-mm` | `float` | no | `5.0` |  | Height of one em in millimetres, which is the size knob. |
| `--line-height` | `float` | no | `1.4` |  | Baseline-to-baseline distance as a multiple of `em_mm`. |
| `--colour`, `--color` | `PenColor` | no | `null` | `"black"`, `"gray"`, `"white"`, `"yellow"`, `"green"`, `"pink"`, `"blue"`, `"red"`, `"gray-overlap"`, `"highlight"`, `"green-2"`, `"cyan"`, `"magenta"`, `"yellow-2"` | Ink colour, from the tablet's own fourteen, named rather than numbered -- `blue`, `gray-overlap`. Blue when unset, so the reply can be told apart from black handwriting at a glance. |
| `--thickness` | `float` | no | `null` |  | The tablet's thickness-slider value the strokes are minted with. Defaults to `RMSPEC_THICKNESS`, so one setting calibrates rendering and replying together. |
| `--allow-substituted-characters` | `bool` | no | `false` |  | Write a reply the engraving face cannot fully draw. The face has the 95 printable ASCII characters and nothing else, so an em dash, a curly quote, an ellipsis or a typographic apostrophe -- all of which model-written prose is full of -- is drawn as a **struck box** on the page. Nothing is folded onto a lookalike and nothing is dropped: this flag accepts a visible box wherever each character appeared, and records one degradation per distinct character. Without it, such a reply is refused before the tablet is touched, with every offending character named by code point. |
| `--strict` | `bool` | no | `false` |  | Refuse anything the default would merely record: an ambiguous `DOC` becomes `AmbiguousDocument` instead of the ranked winner. Cannot be combined with `--allow-substituted-characters`, because one asks to proceed and record while the other asks to refuse rather than record, and picking a winner silently is how a page ends up full of boxes the caller thought it had forbidden. |

## Errors (50 identities)

Closed set. `abstract` marks a class with subclasses of its own: a caller normally
meets one of its leaves, and the leaf's exit status may have been inherited from it,
because the status is resolved by walking the class's own MRO.

| exit | meaning |
| --- | --- |
| `1` | unspecified failure -- the root error class, for a class the table never names |
| `2` | the command line was wrong |
| `65` | input data was malformed |
| `66` | the named input does not exist |
| `69` | a required service was unavailable |
| `70` | an internal error |
| `73` | an output file could not be created |
| `74` | an I/O error |
| `75` | a temporary failure -- retrying may work |
| `77` | permission was denied |
| `78` | the configuration is wrong |

| type | exit | abstract |
| --- | --- | --- |
| `RmspecError` | `1` | yes |
| `MissingDependencyError` | `78` |  |
| `ConfigurationError` | `78` | yes |
| `XochitlDirNotConfigured` | `78` |  |
| `InvalidSettingError` | `78` |  |
| `UsageError` | `2` |  |
| `DocumentSourceError` | `66` | yes |
| `DocumentStoreUnavailable` | `69` |  |
| `DocumentNotFound` | `66` |  |
| `AmbiguousDocument` | `2` |  |
| `PageNotFound` | `66` |  |
| `FormatError` | `65` | yes |
| `MalformedDocument` | `65` |  |
| `CorruptPageData` | `65` |  |
| `UnsupportedPageFormat` | `65` |  |
| `SceneRewriteUnsafe` | `70` |  |
| `RenderError` | `70` | yes |
| `UnsupportedPenType` | `70` |  |
| `BackgroundUnreadable` | `70` |  |
| `ExportError` | `73` | yes |
| `RasterizationFailed` | `73` |  |
| `PdfCompositionFailed` | `73` |  |
| `PdfSourceUnreadable` | `65` |  |
| `PdfPageOutOfRange` | `66` |  |
| `ArtifactWriteFailed` | `73` |  |
| `DeviceError` | `69` | yes |
| `DeviceUnreachable` | `69` |  |
| `DeviceAuthFailed` | `77` |  |
| `DeviceProtocolError` | `69` |  |
| `DeviceDocumentNotFound` | `66` |  |
| `MalformedDeviceMetadata` | `65` |  |
| `DeviceTransferInterrupted` | `69` |  |
| `DeviceStateMismatchError` | `69` |  |
| `DeviceUploadRejected` | `69` |  |
| `DeviceOperationUnsupported` | `78` |  |
| `OcrError` | `69` | yes |
| `RecognitionFailed` | `69` |  |
| `AllRecognizersFailed` | `69` |  |
| `NoTextRecognized` | `69` |  |
| `ModelError` | `69` | yes |
| `ModelUnavailable` | `69` |  |
| `ModelAccessDenied` | `77` |  |
| `ModelThrottled` | `75` |  |
| `ModelRejectedRequest` | `65` |  |
| `ModelResponseMalformed` | `65` |  |
| `PersistenceError` | `74` | yes |
| `StoreUnavailableError` | `74` | yes |
| `StoreSchemaMismatchError` | `78` |  |
| `StoredRecordUnreadableError` | `65` |  |
| `AuditWriteFailedError` | `74` |  |

## Degradations (10 kinds)

A degradation is a thing the run did anyway, having substituted or skipped something --
not a failure, and never swallowed. Closed set, so a caller can decide once per kind
rather than matching strings. Each item carries
`kind`, `subject`, `detail`, `substituted`.

- `catalog_entry_skipped`
- `page_not_annotated`
- `ambiguous_auto_resolved`
- `pdf_page_index_fallback`
- `pdf_page_count_estimated`
- `cache_miss_key_changed`
- `audit_not_recorded`
- `device_index_unavailable`
- `ink_character_substituted`
- `cache_hit_raster_equivalent`

## Settings (15 variables)

Read from the environment at startup. An unknown `RMSPEC_`-prefixed variable fails the
run and names the closest match, so a typo cannot silently do nothing. `rmspec env`
prints the resolved values as assignments a shell can `eval`; a default shown as a `~`
path is resolved against the running user's home.

| variable | type | default | help |
| --- | --- | --- | --- |
| `RMSPEC_XOCHITL` | `Path \| None` | `null` | Local mirror of a xochitl document tree, or `None` to require a device. |
| `RMSPEC_DEVICE_HOST` | `str` | `"10.11.99.1"` | `RMSPEC_DEVICE_HOST` -- the tablet's USB-ethernet address, fixed by firmware. |
| `RMSPEC_DEVICE_USER` | `str` | `"root"` | `RMSPEC_DEVICE_USER` -- the only account the tablet's SSH daemon offers. |
| `RMSPEC_SSH_KEY` | `Path` | `"~/.ssh/id_ed25519_remarkable"` | `RMSPEC_SSH_KEY` -- private key for SSH, because paramiko ignores `~/.ssh/config`. |
| `RMSPEC_SYNC_DB` | `Path` | `"~/.remarkable-spec/sync.db"` | `RMSPEC_SYNC_DB` -- SQLite file holding the mirror, hashes and cached results. |
| `RMSPEC_RENDER_DPI` | `int` | `229` | `RMSPEC_RENDER_DPI` -- raster density for `rmspec render`: the Paper Pro's panel. |
| `RMSPEC_OCR_DPI` | `int` | `300` | `RMSPEC_OCR_DPI` -- the raster density the recognisers were tuned against. |
| `RMSPEC_THICKNESS` | `float` | `1.5` | `RMSPEC_THICKNESS` -- stroke weight multiplier compensating export versus screen. |
| `RMSPEC_MAX_PAGES` | `int` | `64` | `RMSPEC_MAX_PAGES` -- the entry-boundary work cap, and the reason it is a setting. |
| `RMSPEC_TRANSPORT` | `Transport` | `"usb"` | `RMSPEC_TRANSPORT` -- `usb`, `ssh` or `mirror`. USB is the default read path. |
| `RMSPEC_AWS_REGION` | `str` | `"us-west-2"` | `RMSPEC_AWS_REGION` -- the region Textract and Bedrock are called in. |
| `RMSPEC_READ_MODEL` | `str` | `"global.openai.gpt-5.6-luna"` | `RMSPEC_READ_MODEL` -- OCR tier 2, the vision read of the raster itself. |
| `RMSPEC_MERGE_MODEL` | `str` | `"global.openai.gpt-5.6-terra"` | `RMSPEC_MERGE_MODEL` -- OCR tier 3, which adjudicates tiers 0-2. |
| `RMSPEC_OCR_ENGINES` | `frozenset[OcrEngineName]` | `["textract"]` | `RMSPEC_OCR_ENGINES` -- comma-separated; `apple_vision` is macOS-only. |
| `RMSPEC_AGREEMENT_THRESHOLD` | `float` | `0.9` | `RMSPEC_AGREEMENT_THRESHOLD` -- the tier-0/tier-1 short-circuit. |
