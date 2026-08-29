# rmspec-formats

Parsers for reMarkable on-disk formats: v6 .rm, .metadata, .content, .pagedata.

- Import path: `rmspec.formats`
- Workspace dependencies: `rmspec-domain`
- Third-party dependencies: `rmscene`

Part of the [remarkable-spec](../../README.md) workspace. The dependency
direction is asserted by `tests/architecture/test_dependency_direction.py`,
not merely documented here.

## What it binds

| Port (`rmspec.domain.ports.formats`) | Adapter |
| --- | --- |
| `PageCodec` | `rmspec.formats.SceneCodec` — no arguments |
| `DocumentRepository` | `rmspec.formats.XochitlDocumentRepository(root=Path, codec=PageCodec)` |

`rmspec.formats.fingerprint_bytes` is exported as well: it is the unsalted
SHA-256 already persisted as `SyncedPage.rm_hash` and folded into every cached
OCR and diagram key, so it is a compatibility surface rather than a helper.

`layout.py` (on-disk names) and `page_index.py` (the `.content` page walk) stay
addressed by module path. They are the adapter's own vocabulary; a use case
reaches this package through a port.

This is the only distribution that imports `rmscene`, and `SceneCodec` is the
only module inside it that does — asserted by
`tests/test_formats_containment.py`, together with the other half of the rule:
no `rmscene` type appears in any exported signature.

## Three page states, all values

`ports/formats.py` requires a page the store can only partly produce to be a
value rather than an exception or a silently empty layer list:

| On disk | `Page.content` | Defect |
| --- | --- | --- |
| decodable artifact | the decoded content | whatever the decode substituted or dropped |
| zero-byte artifact | blank `PageContent()` | none — the page really has no ink |
| no artifact file | `None` | `ARTIFACT_ABSENT` |
| artifact that will not decode | `None` | `CONTENT_UNDECODABLE` |

The second row is 62 of the 92 `.rm` files in the reference corpus, and the
reason `load` completes on a two-thirds-unannotated PDF where the legacy loader
raised a message-less `EOFError` once per page.

## Tests

```bash
uv run pytest packages/rmspec-formats -q \
  --cov=packages/rmspec-formats/src/rmspec/formats --cov-report=term-missing
```

No binary fixture is committed: scene bytes come from `rmscene`'s own writer.
The differential assertions against the recorded legacy counts in
`tests/fixtures/render-differential-manifest.json` are gated on `RMSPEC_CORPUS`
pointing at a xochitl backup, and skip loudly without it.
