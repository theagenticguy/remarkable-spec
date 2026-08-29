# rmspec-export

Adapters for the export slice: rasterize SVG, compose PDF, read PDF, commit bytes.

Depends on `rmspec-domain` only. Owns `cairosvg` (and, transitively, `cairocffi`), `pymupdf`
and Pillow — no other package in the workspace may import them. This package **generates no
SVG**; `rmspec-render` does. Export rasterizes, composes, reads and commits.

## Ports and adapters

| Port | Adapter | Scope |
| --- | --- | --- |
| `SvgRasterizer` | `rasterizer.CairoSvgRasterizer` | `APP` |
| `PdfComposer` | `composer.CairoSvgPdfComposer` | `APP` |
| `PdfPageReader` | `pdf_reader.PyMuPdfPageReader` | `APP`, with a `REQUEST` registry |
| `ArtifactSink` | `sink.FilesystemArtifactSink` | `REQUEST` |

Two supporting pieces are not ports: `sources.PdfSourceRegistry` mints and resolves the
opaque `PdfSourceRef` tokens the reader takes, and `availability.require_backends()` is the
composition root's health check.

## Wiring

```python
from pathlib import Path

from rmspec.export import (
    CairoSvgPdfComposer,
    CairoSvgRasterizer,
    FilesystemArtifactSink,
    PdfSourceRegistry,
    PyMuPdfPageReader,
    require_backends,
)

require_backends()  # eager pass; raises MissingDependencyError

rasterizer = CairoSvgRasterizer()  # APP: stateless
composer = CairoSvgPdfComposer()  # APP: stateless

with PdfSourceRegistry() as registry:  # REQUEST: owns any spooled temporary
    reader = PyMuPdfPageReader(registry=registry)
    sink = FilesystemArtifactSink(  # REQUEST: holds the invocation's flags
        destination=Path("out"),
        overwrite=False,
        dry_run=False,
    )
```

A `PdfSourceRef` can only be minted by the registry — `registry.for_path(path)` for a document
in the store, `registry.for_bytes(data)` for one just pulled over SSH. Both return an opaque
`uuid4` hex token; nothing above may parse, split or build one, and every token stops resolving
when the registry closes. That is the port's bounded-memoisation rule with an owner.

A missing or unloadable native backend is a **wiring** failure, not a port error. It surfaces
as `MissingDependencyError`, which hangs directly off the root error, so a use case's
`except ExportError` cannot swallow it. No method on any adapter here raises `ImportError`, and
a test asserts that structurally.

## Two legacy defects fixed, not relocated

**`export_pdf` wrote page 1 for an N-page input.** Its multi-page branch converted every page,
painted an unrelated blank white `cairocffi` surface over the output, then ended with
`output.write_bytes(page_pdfs[0])` under a comment claiming it appended the rest. The composer
now has no page-count branch at all — one page and two hundred run the identical convert,
verify and merge pipeline, asserted by counting conversions — and it reopens its own output to
read back the page count and every page size before returning.

**`export_png` documented a Pillow fallback that did not exist.** The docstring promised
"CairoSVG (preferred) or Pillow as a fallback"; the fallback branch was
`try: raise ImportError(...) except ImportError: raise ImportError(...) from None`, the same
message twice, and Pillow was imported nowhere in the repository. The fallback is deleted.
Pillow is given the one job the ports genuinely require and no other installed library
performs: resampling an encoded PNG to an exact pixel size, in `_pillow.py`, exercised by a
test that forces the mismatch.

## Facts that are easy to lose

- **Three private shims, one library each.** `_cairo.py` (cairosvg), `_pymupdf.py` (pymupdf),
  `_pillow.py` (Pillow). No backend type appears in a port signature and no backend exception is
  *raised* across a port; each shim raises a private error the public adapter translates.
  Asserted by an AST test, not a convention. A backend exception does still travel as
  `__cause__` — `PdfSourceUnreadable.__cause__.__cause__` is a `pymupdf.FileDataError` — because
  every translation chains with `raise ... from`. That is deliberate: the chain is what makes a
  wrong verdict debuggable, and only a caller that goes looking for it ever sees a backend type.
- **MuPDF's C core does not write to a file descriptor.** It printed
  `MuPDF error: format error: object is not a stream` below Python — uncapturable by `capsys` or
  by a `rich` console — while the call *succeeded*. The device is off, and the diagnostics MuPDF
  keeps in its own store are drained onto the `detail` of every `PdfBackendError`, so the text
  ends up on the typed error the port already carries instead of interleaved with CLI output.
  Drained at entry too, so one document's repair notes cannot be attached to the next one's
  failure.
- **The sink's temporary has a constant name.** Deriving it from the artifact's
  (`prefix=f".{name.value}-"`) imposed a `NAME_MAX - 15` ceiling that the filesystem does not
  have and `ArtifactName` does not either: a 241-character stem failed while its 245-character
  target was legal, and the legacy `output.write_bytes` accepted every stem up to 251. The
  temporary is now a fixed 21 characters and the only surviving limit is
  `len(stem) + len(suffix) <= NAME_MAX`, asserted at both boundaries. `Path.exists` is wrapped
  for the same reason: it does not swallow `ENAMETOOLONG` on 3.13, so the overwrite probe used
  to raise a raw `OSError` out of a port whose contract is `ArtifactWriteFailed`.
- **The `pymupdf` import must stay inside `warnings.catch_warnings()`.** Its SWIG bindings emit
  a `DeprecationWarning` during C type initialisation, and escalating it *segfaults* the
  interpreter — `python -W error -c "import pymupdf"` exits 139 here. The workspace runs pytest
  with `filterwarnings = ["error"]`, so an unguarded import kills collection, which reports a
  bogus coverage number instead of a red build.
- **`libcairo` is located before `cairosvg` is imported.** `_dyld.py` seeds
  `DYLD_FALLBACK_LIBRARY_PATH` on macOS when it is unset. Contrary to the usual claim this does
  work in-process: `cairocffi` resolves through `ctypes.util.find_library`, whose Darwin
  implementation is pure Python and re-reads the environment on every call. Measured — before
  the assignment `find_library("cairo")` returns `None`, after it returns
  `/opt/homebrew/lib/libcairo.dylib`.
- **PDF page size is measured, never assumed.** `cairosvg` reads a unitless SVG `width` as CSS
  pixels at 96/inch, so the legacy renderer's points-valued box comes out 0.75x too small,
  while `mm`-declared markup needs no correction at all. Both were measured. The composer
  converts once, measures the page box, and re-converts at `target / measured` — exact under
  both conventions, and it needs no edit if the renderer's units change. Tests pin both.
- **Background raster size is the domain's figure, hit with an anisotropic zoom.**
  `PixelSize.fit_within` rounds and MuPDF's pixmap sizing ceils; an isotropic zoom recomputed
  beside the domain formula disagreed in 4 of 16 measured page/box/oversample combinations, and
  a per-axis zoom derived from the target agreed in 16 of 16.
- **`ArtifactName` is a stem.** `ArtifactMedia` is the sole source of the suffix, so
  `write(ArtifactName(value="out.pdf"), ..., media=PDF)` lands `out.pdf.pdf`. A CLI turning
  `--output /tmp/out.pdf` into a destination plus a name must pass `Path.stem`.
- **The sink writes bytes verbatim.** No encode, no newline translation, no appended byte, no
  BOM. `sha256(file) == sha256(payload)` is asserted directly, because it is the one byte-level
  promise the differential oracle rests on now that SVG generation lives in the render slice.

## What is not byte-comparable to the legacy output, deliberately

- **PNG.** Legacy passed `scale = dpi / 72` and reported whatever surface size `cairosvg`
  produced; the port requires `PixelSize.from_dpi` exactly, so the adapter forces the domain
  figure with `output_width`/`output_height`. The two agree on real page geometry at 72, 150 and
  300 dpi, and the equality is what is asserted. Nothing in the manifest covers PNG.
- **PDF.** Every page now goes through convert → verify → merge, and `pymupdf`'s `insert_pdf`
  rewrites the file structure. Two identical runs of the *legacy* code already produced
  different bytes, so there is no reproducible baseline to match. Assert page count and per-page
  size in millimetres — which is exactly what `PdfDocument.pages` makes assertable — and never
  PDF bytes, and never fold PDF bytes into a cache key.

## Recorded seams, deliberately not fixed here

- `RasterImage`, `ImageMedia` and `PhysicalSize` are nominal twins of definitions in
  `rmspec.domain.ports.ocr`, so the raster this package produces is not the type the OCR port
  accepts. The fix is the hoist into a shared `rmspec.domain.values` the export port module
  already describes. Until it lands, the app layer must rebuild the OCR twin field by field —
  and that copy has no bytes-versus-declared-size validator while its `digest` keys the OCR
  cache.
- The `RenderedPage` → `SvgPage` bridge is not written here. It is the same nominal-twin remap,
  it belongs above both slices, and doing it in this package would need a page size derived from
  the markup's own declared box, which is the render slice's fact. The `rmspec-render` entry in
  `pyproject.toml` is reserved for that bridge and is unused today — nothing under `src/` imports
  `rmspec.render`. The dependency table permits the edge, so it is declared rather than dropped
  and named here so the absence of imports is not read as a mistake.
- **A PDF that MuPDF had to repair is not reported as such.** `Document.is_repaired` is read
  nowhere, because acting on it over-rejects: a document truncated to 90 % is repaired and still
  yields its text perfectly, while one truncated to 50 % yields `('',)` — indistinguishable from
  a genuinely text-free scanned page, which the port permits. Surfacing it needs a field on the
  reader's result or a degradation channel, which is the domain's decision. It matters for the
  SSH/USB pull path, where a half-transferred PDF is real and the OCR cache would key an empty
  answer under a digest.
- **The contract suites (`check_rasterizer`, `check_composer`, `check_reader`, `check_sink`) live
  in this package's `tests/`,** so no other package can import them and grade its own doubles
  against the same rules — even though the domain's port module cites them as the enforcement
  mechanism. The only address every package may import is `rmspec.domain`, so the fix is
  `rmspec.domain.testing`, a domain change. `MemoryArtifactSink` now carries an overwrite policy
  so at least the `ALREADY_PRESENT` clause is port-tested rather than adapter-tested, and
  `solid_png` is built from `zlib` and `struct` rather than through MuPDF so the doubles owe
  nothing to a backend mapped to this package alone.
- Background box choice is an app-layer decision no adapter test can catch. Legacy
  `render/pdf_bg.py` computed `min(viewport_pt / page_pt) * 2.0` — twice the viewport measured
  in *points*. `PixelSize.fit_within` takes a box in *pixels*. To reproduce the legacy scale the
  app must pass `box = PixelSize.from_dpi(page_size, 72)`; a device-pixel box is defensible but
  different, and it inflates a 200-page render's background memory roughly tenfold.

## Gates

```bash
uv run ruff check --fix packages/rmspec-export
uv run ruff format packages/rmspec-export
uv run ty check --error-on-warning packages/rmspec-export
uv run pytest packages/rmspec-export -q \
  --cov=packages/rmspec-export/src/rmspec/export --cov-report=term-missing
```
