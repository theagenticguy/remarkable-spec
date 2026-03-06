"""Export module for converting reMarkable pages to standard file formats.

This module provides:
  - **export_svg** — Pure-Python SVG export (no external dependencies).
  - **export_png** — PNG rasterization (requires ``remarkable-spec[render]``).
  - **export_pdf** — Multi-page PDF export (requires ``remarkable-spec[render]``).

The SVG exporter works out of the box. PNG and PDF exporters require
the ``render`` optional dependency group::

    pip install remarkable-spec[render]
"""

from __future__ import annotations

from remarkable_spec.export.pdf import export_pdf
from remarkable_spec.export.png import export_png
from remarkable_spec.export.svg import export_svg

__all__ = [
    "export_pdf",
    "export_png",
    "export_svg",
]
