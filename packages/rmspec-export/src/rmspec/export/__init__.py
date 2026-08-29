"""Export adapters: rasterize SVG, compose PDF, read PDF, commit bytes.

Four adapters, one per port in :mod:`rmspec.domain.ports.export`, plus the request-scoped
registry that mints the PDF reader's opaque source tokens and the composition root's backend
health check. This package generates no SVG -- :mod:`rmspec.render` does -- and it holds no
orchestration and no rendering defaults.

+-----------------------------------+----------------------------------------------+
| Port                              | Adapter                                      |
+===================================+==============================================+
| :class:`SvgRasterizer`            | :class:`~rmspec.export.rasterizer            |
|                                   | .CairoSvgRasterizer`                         |
+-----------------------------------+----------------------------------------------+
| :class:`PdfComposer`              | :class:`~rmspec.export.composer              |
|                                   | .CairoSvgPdfComposer`                        |
+-----------------------------------+----------------------------------------------+
| :class:`PdfPageReader`            | :class:`~rmspec.export.pdf_reader            |
|                                   | .PyMuPdfPageReader`                          |
+-----------------------------------+----------------------------------------------+
| :class:`ArtifactSink`             | :class:`~rmspec.export.sink                  |
|                                   | .FilesystemArtifactSink`                     |
+-----------------------------------+----------------------------------------------+

Four facts a reader of this package needs
-----------------------------------------
1. **Third-party imports live in exactly three private modules.** :mod:`rmspec.export._cairo`
   is the only importer of ``cairosvg`` (which pulls ``cairocffi`` in itself),
   :mod:`rmspec.export._pymupdf` the only importer of ``pymupdf``, and
   :mod:`rmspec.export._pillow` the only importer of Pillow. No backend type appears in a port
   signature and no backend exception is *raised* across a port; each shim raises a private
   error the public adapter translates. A backend exception does still travel as
   ``__cause__`` -- measured, ``PdfSourceUnreadable.__cause__.__cause__`` is a
   ``pymupdf.FileDataError`` -- because every translation chains with ``raise ... from``, which
   is deliberate: the chain is what makes a wrong verdict debuggable, and it is reachable only
   by a caller that goes looking for it.
2. **``libcairo`` has to be findable before ``cairosvg`` is imported.**
   :mod:`rmspec.export._dyld` seeds ``DYLD_FALLBACK_LIBRARY_PATH`` on macOS when it is unset,
   which -- contrary to the usual claim -- does work in-process, because ``cairocffi`` resolves
   through :func:`ctypes.util.find_library`, whose Darwin implementation is pure Python and
   re-reads the environment on every call. That module records the measurement.
3. **Two legacy defects are fixed here, not relocated.** ``export_pdf`` answered an N-page
   request with page one (``output.write_bytes(page_pdfs[0])``, under a comment claiming it
   appended the rest); :class:`~rmspec.export.composer.CairoSvgPdfComposer` has no page-count
   branch at all and verifies its own output. ``export_png`` documented a Pillow fallback that
   did not exist -- its fallback branch raised the same ``ImportError`` message twice and
   Pillow was imported nowhere; Pillow is instead given the one job the ports require, exact
   resampling in :mod:`rmspec.export._pillow`.
4. **Availability is not an error of any port.** A missing or unloadable backend fails
   container composition, via :func:`rmspec.export.availability.require_backends` or the
   import of this package itself, and surfaces as
   :class:`~rmspec.domain.errors.MissingDependencyError` -- a direct child of the root error,
   so no ``except ExportError`` can swallow a wiring bug.

Recorded seams, not fixed here
------------------------------
* :class:`~rmspec.domain.ports.export.RasterImage`, ``ImageMedia`` and ``PhysicalSize`` are
  nominal twins of definitions in :mod:`rmspec.domain.ports.ocr`, so the raster this package
  produces is not the type the OCR port accepts. The fix is the hoist into a shared
  ``rmspec.domain.values`` that the export port module already describes; until it lands, the
  app layer must rebuild the OCR twin field by field, and that copy has no
  bytes-versus-declared-size validator while its ``digest`` keys the OCR cache.
* The bridge from :class:`RenderedPage` to :class:`SvgPage` is deliberately *not* written
  here. It is the same nominal-twin remap, it belongs above both slices, and writing it in this
  package would need a size derived from the markup's own declared box -- which is the render
  slice's fact, not this one's. The ``rmspec-render`` entry in this package's
  ``pyproject.toml`` is reserved for that bridge and is unused today: nothing under
  ``src/rmspec/export`` imports :mod:`rmspec.render`. The dependency table permits the edge, so
  it passes the architecture suite; it is declared rather than dropped so the bridge lands
  without a packaging change, and named here so the absence of imports is not read as a mistake.
* Minting a :class:`~rmspec.domain.ports.export.PdfSourceRef` is a capability of the
  composition root alone. :class:`~rmspec.export.sources.PdfSourceRegistry` is the only minter,
  ``rmspec-app`` may import only the domain, and the reader binds the narrow
  :class:`~rmspec.export.sources.SourceResolver` view -- so a use case receives a ref as a
  parameter and cannot make one. That is deliberate, and it means the sync path's "bytes just
  pulled over SSH, then a page count" hand-off is assembled in ``rmspec-cli``. If a use case
  ever needs to mint, the answer is a ``PdfSourceMinter`` protocol in the domain, not an
  ``app -> export`` import.
"""

from __future__ import annotations

from rmspec.export.availability import require_backends
from rmspec.export.composer import CairoSvgPdfComposer
from rmspec.export.pdf_reader import PyMuPdfPageReader
from rmspec.export.rasterizer import CairoSvgRasterizer
from rmspec.export.sink import FilesystemArtifactSink
from rmspec.export.sources import PdfSourceRegistry

__all__ = [
    "CairoSvgPdfComposer",
    "CairoSvgRasterizer",
    "FilesystemArtifactSink",
    "PdfSourceRegistry",
    "PyMuPdfPageReader",
    "require_backends",
]
