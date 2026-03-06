"""Data models for reMarkable tablet file formats and data structures.

This package provides Pydantic v2 models for the reMarkable ecosystem:

- **Colors**: Pen colors, highlight colors, RGB palettes for export and physical display
- **Pens**: Pen tool types and rendering configurations
- **Strokes**: Point samples and stroke data from .rm binary files
- **Screens**: Device screen specifications (rM2, Paper Pro)
- **Pages**: Page, layer, and text block structures
- **Documents**: Complete document metadata, content info, and page collections
- **Templates**: Legacy and methods-style page background templates

All models use ``from __future__ import annotations`` for forward-reference support
and are designed for both human readability and AI agent consumption via rich
Field descriptions and docstrings.
"""

from __future__ import annotations

from remarkable_spec.models.color import (
    PAPER_PRO_PHYSICAL,
    RGB,
    RM_PALETTE,
    HighlightColor,
    PenColor,
)
from remarkable_spec.models.document import (
    ContentInfo,
    Document,
    DocumentMetadata,
    DocumentType,
    ExtraMetadata,
    FileType,
    PageRef,
)
from remarkable_spec.models.page import Layer, Page, TextBlock
from remarkable_spec.models.pen import Pen, PenType
from remarkable_spec.models.screen import PAPER_PRO_SCREEN, RM2_SCREEN, ScreenSpec
from remarkable_spec.models.stroke import Point, Stroke
from remarkable_spec.models.template import (
    BUILTIN_TEMPLATES,
    BuiltinTemplate,
    Template,
    TemplateItem,
)

__all__ = [
    # Colors
    "PenColor",
    "HighlightColor",
    "RGB",
    "RM_PALETTE",
    "PAPER_PRO_PHYSICAL",
    # Pens
    "PenType",
    "Pen",
    # Strokes
    "Point",
    "Stroke",
    # Pages & Layers
    "TextBlock",
    "Layer",
    "Page",
    # Documents
    "Document",
    "DocumentMetadata",
    "ContentInfo",
    "ExtraMetadata",
    "DocumentType",
    "FileType",
    "PageRef",
    # Templates
    "Template",
    "TemplateItem",
    "BuiltinTemplate",
    "BUILTIN_TEMPLATES",
    # Screen specs
    "ScreenSpec",
    "RM2_SCREEN",
    "PAPER_PRO_SCREEN",
]
