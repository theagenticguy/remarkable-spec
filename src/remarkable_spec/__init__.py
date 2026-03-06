"""remarkable-spec: Python library for reMarkable tablet file formats,
device access, and rendering."""

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
