"""Parse reMarkable .rm binary files (v6 format) into structured data.

The v6 format uses rmscene's scene-tree representation internally. This module
wraps ``rmscene.read_tree`` and converts the resulting scene items into the
Pydantic / dataclass models defined in ``remarkable_spec.models``.

Mapping
-------
rmscene ``Group``  -> ``Layer`` (each top-level group child of the root is a layer)
rmscene ``Line``   -> ``Stroke`` (pen type, color, thickness, points)
rmscene ``Point``  -> ``Point`` (x, y, speed, direction, width, pressure)
rmscene ``Text``   -> ``TextBlock`` (pos, width, extracted plain text)
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from rmscene import read_tree
from rmscene import scene_items as si

from remarkable_spec.models.color import PenColor
from remarkable_spec.models.page import Layer, TextBlock
from remarkable_spec.models.pen import PenType
from remarkable_spec.models.stroke import Point, Stroke

# Suppress rmscene "Some data has not been read" warnings — the v6 format
# evolves faster than the parser, and the missing fields are non-critical.
logging.getLogger("rmscene").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

__all__ = [
    "parse_rm_bytes",
    "parse_rm_file",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_rm_file(path: Path) -> list[Layer]:
    """Parse a v6 ``.rm`` binary file into a list of :class:`Layer` objects.

    Parameters
    ----------
    path:
        Filesystem path to a ``.rm`` file.

    Returns
    -------
    list[Layer]
        Ordered list of layers, each containing strokes and text blocks.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    rmscene.UnexpectedBlockError
        If the file is not a valid v6 ``.rm`` file.
    """
    data = path.read_bytes()
    return parse_rm_bytes(data)


def parse_rm_bytes(data: bytes) -> list[Layer]:
    """Parse raw ``.rm`` bytes into a list of :class:`Layer` objects.

    Parameters
    ----------
    data:
        Raw bytes of a v6 ``.rm`` file.

    Returns
    -------
    list[Layer]
        Ordered list of layers, each containing strokes and text blocks.
    """
    tree = read_tree(io.BytesIO(data))
    return _convert_tree(tree)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _convert_tree(tree) -> list[Layer]:
    """Walk the scene tree root and convert each top-level Group into a Layer."""
    root: si.Group = tree.root
    layers: list[Layer] = []

    # The root Group's children are the top-level layer groups.
    for child in root.children.values():
        if isinstance(child, si.Group):
            layers.append(_convert_group(child, tree))
        # Non-group items at root level are unusual but we handle gracefully.

    # If no layer groups found, create a single layer from all items
    if not layers:
        layer = Layer(name="Layer 1")
        for item in tree.walk():
            _collect_item(item, layer)
        if not layer.is_empty:
            layers.append(layer)

    return layers


def _convert_group(group: si.Group, tree) -> Layer:
    """Convert an rmscene Group (layer) into a Layer model."""
    label = group.label.value if group.label else ""
    visible = group.visible.value if group.visible else True

    layer = Layer(name=label, visible=visible)

    for child in group.children.values():
        if isinstance(child, si.Group):
            # Nested groups: look up the actual node in the tree and
            # collect its children into this layer.
            try:
                resolved = tree[child.node_id]
                for nested_child in resolved.children.values():
                    _collect_item(nested_child, layer)
            except (KeyError, AttributeError):
                # If resolution fails, try iterating the group's own children
                for nested_child in child.children.values():
                    _collect_item(nested_child, layer)
        else:
            _collect_item(child, layer)

    return layer


def _collect_item(item, layer: Layer) -> None:
    """Add an rmscene item (Line, Text, etc.) to the given layer."""
    if isinstance(item, si.Line):
        layer.strokes.append(_convert_line(item))
    elif isinstance(item, si.Text):
        layer.text_blocks.append(_convert_text(item))
    elif isinstance(item, si.Group):
        # Recursively collect nested group items into the same layer
        for child in item.children.values():
            _collect_item(child, layer)
    elif isinstance(item, si.GlyphRange):
        # GlyphRange is a text highlight; skip for stroke extraction
        pass
    else:
        logger.debug("Skipping unknown scene item type: %s", type(item).__name__)


def _convert_line(line: si.Line) -> Stroke:
    """Convert an rmscene Line into a Stroke model."""
    # Map rmscene Pen enum to our PenType enum
    try:
        pen_type = PenType(int(line.tool))
    except ValueError:
        logger.warning("Unknown pen type %s, defaulting to FINELINER_1", line.tool)
        pen_type = PenType.FINELINER_1

    # Map rmscene PenColor to our PenColor enum
    try:
        color = PenColor(int(line.color))
    except ValueError:
        logger.warning("Unknown color %s, defaulting to BLACK", line.color)
        color = PenColor.BLACK

    points = [_convert_point(p) for p in line.points]

    return Stroke(
        pen_type=pen_type,
        color=color,
        thickness_scale=line.thickness_scale,
        points=points,
        starting_length=line.starting_length,
    )


def _convert_point(point: si.Point) -> Point:
    """Convert an rmscene Point into a Point model."""
    return Point(
        x=point.x,
        y=point.y,
        speed=point.speed,
        direction=point.direction,
        width=point.width,
        pressure=point.pressure,
    )


def _convert_text(text: si.Text) -> TextBlock:
    """Convert an rmscene Text block into a TextBlock model.

    Extracts the plain text from the CRDT sequence by concatenating
    all string items in order.
    """
    plain_parts: list[str] = []
    for value in text.items.values():
        if isinstance(value, str):
            plain_parts.append(value)
        # Integer values are formatting codes; skip them for plain text

    return TextBlock(
        pos_x=text.pos_x,
        pos_y=text.pos_y,
        width=text.width,
        text="".join(plain_parts),
    )
