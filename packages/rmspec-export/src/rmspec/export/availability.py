"""The composition root's health check for this package's native backends.

Availability is not a port error. No method on any export port raises "backend unavailable"
or "dependency missing", because a use case writing ``except ExportError`` must not be able to
swallow a wiring bug. :func:`require_backends` is the single replacement for the legacy
function-local ``ImportError`` raises -- including ``export_png``'s
``try: raise ImportError(...) except ImportError: raise ImportError(...) from None``, which
raised the same message twice -- and it raises
:class:`~rmspec.domain.errors.MissingDependencyError`, a direct child of the root error so no
export ``except`` can swallow it.

It is called once, by the container's eager resolution pass, and never by an adapter method.

What it can and cannot catch
---------------------------
``cairosvg`` and ``pymupdf`` are hard dependencies of this distribution, not extras, so in a
synced environment they are present by construction and this module imports them at module
scope like everything else here. What is *not* guaranteed is that they work: ``cairocffi``
performs its ``dlopen`` during import and raises ``OSError`` -- not ``ImportError`` -- when
``libcairo`` is not on the loader's search path, and ``pymupdf`` imports cleanly and then
needs its MuPDF core to open a document. A backend that is genuinely absent or unloadable
therefore fails while ``rmspec.export`` is being imported, which is the composition root's
own import and where it belongs; this function covers the remaining case of a backend that
imported and cannot do the work, by rasterizing a 1x1 SVG and building a one-page PDF.
"""

from __future__ import annotations

from rmspec.domain.errors import MissingDependencyError
from rmspec.export import _cairo, _pymupdf

__all__ = ["EXTRA", "FEATURE", "require_backends"]

EXTRA = "render"
"""The optional dependency group that provides this package's native backends."""

FEATURE = "SVG rasterization and PDF export"
"""What the user was trying to do, for the message."""

_PROBE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1"></svg>'
)


def require_backends() -> None:
    """Prove that both native backends can do their work, or name the failing package.

    Raises
    ------
    MissingDependencyError
        A backend imported but is unusable, naming the import package and the extra that
        provides it. Deliberately the same error a genuinely missing package produces, because
        the user's next action -- reinstall the extra -- is the same.
    """
    try:
        _cairo.render_png(_PROBE_SVG, width_px=1, height_px=1)
    except (_cairo.CairoError, OSError) as exc:
        raise MissingDependencyError(package="cairosvg", extra=EXTRA, feature=FEATURE) from exc
    try:
        _pymupdf.probe_backend()
    except (_pymupdf.PdfBackendError, OSError) as exc:
        raise MissingDependencyError(package="pymupdf", extra=EXTRA, feature=FEATURE) from exc
