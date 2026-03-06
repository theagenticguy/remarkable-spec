"""Page and layer data structures.

A reMarkable notebook page consists of one or more layers, each containing
strokes and optionally text. Pages are stored as individual .rm files
identified by their UUID within the document's directory on the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from remarkable_spec.models.stroke import Stroke


class TextBlock(BaseModel):
    """A block of typed text on a page.

    Corresponds to the Text scene item in v6 format. Text is stored as a
    CRDT (Conflict-free Replicated Data Type) sequence of strings with
    paragraph styles, enabling real-time sync between devices.

    The position and width define the text box in screen coordinates.
    The text content is the plain-text rendering of the CRDT sequence.
    """

    pos_x: float = Field(
        description="Horizontal position of the text block's top-left corner in screen units.",
    )
    pos_y: float = Field(
        description="Vertical position of the text block's top-left corner in screen units.",
    )
    width: float = Field(
        description="Width of the text block in screen units. Text wraps within this width.",
    )
    text: str = Field(
        default="",
        description="Plain-text content of the CRDT text sequence. "
        "Paragraph breaks are represented as newlines.",
    )


class Layer(BaseModel):
    """A single drawing layer within a page.

    Layers contain strokes (pen drawings) and optionally text blocks.
    They can be shown/hidden and named in the UI. The reMarkable supports
    multiple layers per page, allowing users to organize content
    (e.g. sketch on one layer, annotations on another).

    Layers are rendered bottom-to-top in list order.
    """

    name: str = Field(
        default="",
        description="Optional layer name shown in the reMarkable UI layer panel.",
    )
    visible: bool = Field(
        default=True,
        description="Whether the layer is visible. Hidden layers are skipped during rendering.",
    )
    strokes: list[Stroke] = Field(
        default_factory=list,
        description="Ordered list of strokes (pen drawings) in this layer. "
        "Strokes are rendered in list order (earlier strokes appear behind later ones).",
    )
    text_blocks: list[TextBlock] = Field(
        default_factory=list,
        description="Typed text blocks in this layer. Text is rendered on top of strokes.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_empty(self) -> bool:
        """Whether the layer has no content (no strokes and no text blocks)."""
        return not self.strokes and not self.text_blocks

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """Return (x_min, y_min, x_max, y_max) bounding box of all strokes.

        Returns (0, 0, 0, 0) if the layer has no strokes.
        Text blocks are not included in the bounding box calculation.
        """
        if not self.strokes:
            return (0.0, 0.0, 0.0, 0.0)
        boxes = [s.bounding_box for s in self.strokes]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


class Page(BaseModel):
    """A single page in a notebook or annotated document.

    Each page is stored as a separate .rm file on the device filesystem
    at ``{document_uuid}/{page_uuid}.rm``. Pages are identified by UUID
    and referenced from the document's .content JSON file.

    A page contains one or more layers and an optional template reference
    that defines the background pattern (lines, grid, dots, etc.).
    """

    uuid: UUID = Field(
        description="Unique identifier for this page. Used as the filename stem "
        "for the .rm file, metadata JSON, and thumbnail JPEG.",
    )
    layers: list[Layer] = Field(
        default_factory=list,
        description="Ordered list of drawing layers. Rendered bottom-to-top.",
    )
    template_name: str = Field(
        default="",
        description="Name of the background template (e.g. 'Blank', 'Lined', 'Grid_small'). "
        "Empty string means no template / blank background.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rm_filename(self) -> str:
        """The .rm filename for this page, e.g. '{uuid}.rm'."""
        return f"{self.uuid}.rm"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def metadata_filename(self) -> str:
        """The per-page metadata JSON filename, e.g. '{uuid}-metadata.json'."""
        return f"{self.uuid}-metadata.json"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_filename(self) -> str:
        """The thumbnail JPEG filename, e.g. '{uuid}.jpg'."""
        return f"{self.uuid}.jpg"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_strokes(self) -> list[Stroke]:
        """All strokes across all visible layers, in render order."""
        return [s for layer in self.layers if layer.visible for s in layer.strokes]

    def rm_path(self, document_uuid: UUID) -> Path:
        """Full relative path to the .rm file within the xochitl directory.

        Args:
            document_uuid: The parent document's UUID.

        Returns:
            Path like ``{document_uuid}/{page_uuid}.rm``.
        """
        return Path(str(document_uuid)) / self.rm_filename
