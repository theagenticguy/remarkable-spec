"""Template models for reMarkable page backgrounds.

Templates are SVG/PNG files that provide the background grid or pattern
for notebook pages. Two template systems exist:

Legacy templates (pre-3.x):
  Stored at /usr/share/remarkable/templates/ with a templates.json manifest.
  Reset on every OS upgrade.

Methods templates (3.x+):
  Stored in xochitl/ directory as {UUID}.template JSON files.
  Persist across OS upgrades. Support dynamic sizing via constants.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BuiltinTemplate(BaseModel):
    """A built-in template from the legacy template system (pre-3.x firmware).

    Built-in templates are shipped with the firmware at
    ``/usr/share/remarkable/templates/`` and are defined in a central
    ``templates.json`` manifest. They are reset on every OS upgrade, so
    custom templates added to this directory will be lost.

    Each template has a display name and a filename (without extension)
    that maps to both a .png preview and an .svg rendering file.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        description="Display name shown in the template picker UI, "
        "e.g. 'Blank', 'Lined', 'Grid (small)'.",
    )
    filename: str = Field(
        description="Filename stem (without extension) for the template files. "
        "Maps to {filename}.svg and {filename}.png in the templates directory.",
    )
    icon_code: str = Field(
        default="",
        description="Icon code for the template picker. Empty string for default icon.",
    )
    landscape: bool = Field(
        default=False,
        description="Whether this template is designed for landscape orientation.",
    )
    categories: tuple[str, ...] = Field(
        default=(),
        description="Category tags for organizing templates in the picker, "
        "e.g. ('Lines',), ('Grids',).",
    )


class TemplateItem(BaseModel):
    """A geometric item within a methods template (line, rect, circle, etc.).

    Methods templates (firmware 3.x+) define page backgrounds as a collection
    of geometric primitives rather than raster images. Each item has a type
    and a properties dict that varies by type (e.g. x1/y1/x2/y2 for lines,
    cx/cy/r for circles).
    """

    item_id: str = Field(
        description="Unique identifier for this item within the template.",
    )
    item_type: str = Field(
        description="Geometric primitive type: 'line', 'rect', 'circle', 'text', etc.",
    )
    properties: dict[str, str | float | int] = Field(
        default_factory=dict,
        description="Type-specific properties. For 'line': x1, y1, x2, y2, strokeWidth, color. "
        "For 'rect': x, y, width, height. Values may reference template constants.",
    )


class Template(BaseModel):
    """A methods-style template (firmware 3.x+).

    These templates define page backgrounds using geometric primitives
    and support dynamic sizing via template constants (templateWidth,
    templateHeight). They are stored as {UUID}.template JSON files in the
    xochitl directory and persist across OS upgrades, unlike legacy templates.

    Methods templates support multiple screen sizes (rM2 and Paper Pro)
    by using constants that resolve to device-specific values at render time.
    """

    name: str = Field(
        description="Display name for the template, shown in the template picker.",
    )
    author: str = Field(
        default="",
        description="Author name or identifier for the template.",
    )
    template_version: str = Field(
        default="0.0.1",
        description="Semantic version string for this template definition.",
    )
    format_version: int = Field(
        default=1,
        description="Template format version. Currently 1 for methods templates.",
    )
    orientation: str = Field(
        default="portrait",
        description="Page orientation: 'portrait' or 'landscape'.",
    )
    categories: list[str] = Field(
        default_factory=list,
        description="Category tags for the template picker, e.g. ['Lines', 'Creative'].",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Additional label tags for search and filtering.",
    )
    supported_screens: list[str] = Field(
        default_factory=lambda: ["rm2", "rmPP"],
        description="Device screen identifiers this template supports. "
        "'rm2' = reMarkable 2, 'rmPP' = Paper Pro.",
    )
    constants: dict[str, str | float] = Field(
        default_factory=dict,
        description="Named constants used by template items for dynamic sizing. "
        "Common constants: 'templateWidth', 'templateHeight', 'marginLeft'.",
    )
    items: list[TemplateItem] = Field(
        default_factory=list,
        description="Ordered list of geometric items that compose the template background.",
    )
    icon_data: str = Field(
        default="",
        description="Base64-encoded SVG data for the template icon in the picker.",
    )


# Common built-in templates from the legacy template system.
# These are the most frequently encountered templates in .pagedata files.
BUILTIN_TEMPLATES = [
    BuiltinTemplate(name="Blank", filename="Blank"),
    BuiltinTemplate(name="Lined", filename="Lined", categories=("Lines",)),
    BuiltinTemplate(name="Lined (small)", filename="Lined_small", categories=("Lines",)),
    BuiltinTemplate(name="Lined (medium)", filename="Lined_medium", categories=("Lines",)),
    BuiltinTemplate(name="Dotted", filename="Dots_S", categories=("Grids",)),
    BuiltinTemplate(name="Grid (small)", filename="Grid_small", categories=("Grids",)),
    BuiltinTemplate(name="Grid (medium)", filename="Grid_medium", categories=("Grids",)),
    BuiltinTemplate(name="Margin (small)", filename="Margin_small", categories=("Lines",)),
    BuiltinTemplate(name="Margin (medium)", filename="Margin_medium", categories=("Lines",)),
]
