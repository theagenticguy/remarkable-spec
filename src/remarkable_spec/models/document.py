"""Document metadata and content models.

Each document on the reMarkable is identified by a UUID and consists of
multiple files in the xochitl directory:

  {UUID}.metadata     -- JSON: visibleName, type, parent, sync fields
  {UUID}.content      -- JSON: page UUIDs, file type, extra metadata
  {UUID}.pagedata     -- Text: template name per page (one line each)
  {UUID}/             -- Directory with per-page .rm files
  {UUID}.thumbnails/  -- Directory with per-page .jpg thumbnails
  {UUID}.pdf          -- Original PDF (if uploaded)
  {UUID}.epub         -- Original EPUB (if uploaded)
"""

from __future__ import annotations

import enum
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from remarkable_spec.models.page import Page


class DocumentType(enum.Enum):
    """Document vs folder distinction in .metadata files.

    The reMarkable filesystem uses a flat directory structure where all
    documents and folders live in the same xochitl directory. The parent-child
    hierarchy is encoded via the 'parent' field in each .metadata file.
    """

    DOCUMENT = "DocumentType"
    COLLECTION = "CollectionType"


class FileType(enum.Enum):
    """The underlying file type of a document.

    Determines how the document was created and what original source file
    (if any) is associated with it. Notebooks are created on-device,
    while PDF and EPUB documents are uploaded from external sources.
    """

    NOTEBOOK = "notebook"
    PDF = "pdf"
    EPUB = "epub"


class DocumentMetadata(BaseModel):
    """Parsed contents of a {UUID}.metadata JSON file.

    This file controls how the document appears in the reMarkable UI and
    its position in the folder hierarchy. It tracks display name, folder
    membership, pin state, deletion status, and sync timestamps.

    The .metadata file is the primary index used by xochitl to build the
    document tree shown in the My Files view.
    """

    visible_name: str = Field(
        description="Display name shown in the reMarkable UI. This is the user-facing "
        "document title, separate from the UUID-based filename on disk.",
    )
    doc_type: DocumentType = Field(
        description="Whether this entry is a document or a folder (collection). "
        "Folders use CollectionType and contain no .rm files.",
    )
    parent: str = Field(
        default="",
        description="UUID string of the parent folder. Empty string means root level. "
        "The literal string 'trash' means the document is in the trash.",
    )
    deleted: bool = Field(
        default=False,
        description="Whether the document has been deleted. Deleted documents may "
        "persist on disk until the next cloud sync.",
    )
    pinned: bool = Field(
        default=False,
        description="Whether the document is pinned (favorited) in the UI.",
    )
    last_modified: int = Field(
        default=0,
        description="Last modification timestamp as Unix epoch in milliseconds. "
        "Updated when any page content changes.",
    )
    last_opened: int = Field(
        default=0,
        description="Last opened timestamp as Unix epoch in milliseconds.",
    )
    last_opened_page: int = Field(
        default=0,
        description="Zero-based index of the last page the user had open.",
    )
    version: int = Field(
        default=0,
        description="Metadata version counter, incremented on each sync.",
    )
    synced: bool = Field(
        default=False,
        description="Whether this document has been synced to the reMarkable cloud.",
    )

    @classmethod
    def from_json(cls, data: dict) -> DocumentMetadata:
        """Parse a DocumentMetadata from a raw .metadata JSON dict.

        Handles the reMarkable-specific field naming conventions (camelCase)
        and type coercions (e.g. lastModified stored as string).
        """
        return cls(
            visible_name=data.get("visibleName", ""),
            doc_type=DocumentType(data.get("type", "DocumentType")),
            parent=data.get("parent", ""),
            deleted=data.get("deleted", False),
            pinned=data.get("pinned", False),
            last_modified=int(data.get("lastModified", "0")),
            last_opened=int(data.get("lastOpened", "0")),
            last_opened_page=data.get("lastOpenedPage", 0),
            version=data.get("version", 0),
            synced=data.get("synced", False),
        )

    @classmethod
    def from_path(cls, path: Path) -> DocumentMetadata:
        """Load and parse a .metadata file from disk."""
        return cls.from_json(json.loads(path.read_text()))


class ExtraMetadata(BaseModel):
    """Per-tool last-used settings from the .content file's extraMetadata field.

    Tracks the last pen type, color, and thickness used for each tool so
    the UI can restore the user's previous tool configuration when they
    reopen the document. The keys and values are reMarkable-internal
    identifiers (e.g. 'Fineliner', 'FinelinerV2Size', etc.).
    """

    last_tool: str = Field(
        default="",
        description="Internal name of the last tool used, e.g. 'Fineliner' or 'Highlighter'.",
    )
    last_pen: str = Field(
        default="",
        description="Internal name of the last pen variant used.",
    )
    tool_settings: dict[str, str] = Field(
        default_factory=dict,
        description="Full dictionary of tool settings from the extraMetadata JSON. "
        "Keys are reMarkable-internal identifiers for tool properties.",
    )

    @classmethod
    def from_json(cls, data: dict) -> ExtraMetadata:
        """Parse an ExtraMetadata from the extraMetadata dict in a .content file."""
        return cls(
            last_tool=data.get("LastTool", ""),
            last_pen=data.get("LastPen", ""),
            tool_settings=data,
        )


class PageRef(BaseModel):
    """Reference to a page within a document's .content file.

    In newer firmware (3.x+), pages use cPages format with CRDT timestamps
    for conflict-free sync. Each page reference maps to a {uuid}.rm file
    in the document's directory on disk.
    """

    uuid: UUID = Field(
        description="Unique identifier for the referenced page. Corresponds to "
        "the .rm filename: {uuid}.rm.",
    )
    template: str = Field(
        default="Blank",
        description="Template name for this page's background. "
        "Defaults to 'Blank' if not specified.",
    )
    redirect: str | None = Field(
        default=None,
        description="Redirect UUID for pages that have been moved or merged. "
        "None for normal pages.",
    )


class ContentInfo(BaseModel):
    """Parsed contents of a {UUID}.content JSON file.

    Contains the document's page structure, file type, tool settings, and
    layout configuration. This is the primary structural file that defines
    how many pages exist, what templates they use, and how text/PDF content
    should be rendered.

    The .content file format has evolved across firmware versions. The cPages
    format (firmware 3.x+) uses CRDT structures for sync-safe page ordering.
    """

    file_type: FileType = Field(
        description="The underlying file type: notebook (created on device), "
        "pdf (uploaded PDF), or epub (uploaded EPUB).",
    )
    format_version: int = Field(
        default=2,
        description="Content format version. Version 2 is current for firmware 3.x.",
    )
    orientation: str = Field(
        default="portrait",
        description="Page orientation: 'portrait' or 'landscape'.",
    )
    page_count: int = Field(
        default=0,
        description="Total number of pages in the document.",
    )
    page_refs: list[PageRef] = Field(
        default_factory=list,
        description="Ordered list of page references. Defines page order and "
        "per-page template assignments.",
    )
    extra_metadata: ExtraMetadata = Field(
        default_factory=ExtraMetadata,
        description="Per-tool last-used settings (pen type, color, size). "
        "Restored when the user reopens the document.",
    )

    # Layout settings (primarily for PDF/EPUB rendering)
    margins: int = Field(
        default=125,
        description="Page margins in screen units for text reflow (PDF/EPUB).",
    )
    font_name: str = Field(
        default="",
        description="Font name for text rendering in EPUB documents.",
    )
    line_height: int = Field(
        default=-1,
        description="Line height for text rendering. -1 means auto/default.",
    )
    text_scale: float = Field(
        default=1.0,
        description="Text scaling factor for EPUB rendering.",
    )
    text_alignment: str = Field(
        default="justify",
        description="Text alignment for EPUB rendering: 'justify', 'left', 'center', 'right'.",
    )
    zoom_mode: str = Field(
        default="bestFit",
        description="PDF zoom mode: 'bestFit', 'fitWidth', 'fitPage', or 'custom'.",
    )
    custom_zoom_scale: float = Field(
        default=1.0,
        description="Custom zoom scale when zoom_mode is 'custom'.",
    )

    @classmethod
    def from_json(cls, data: dict) -> ContentInfo:
        """Parse a ContentInfo from a raw .content JSON dict.

        Handles both the legacy 'pages' array format and the newer 'cPages'
        CRDT format introduced in firmware 3.x.
        """
        file_type = FileType(data.get("fileType", "notebook"))

        # Parse page references (handles both old and new format)
        page_refs: list[PageRef] = []
        if "cPages" in data and "pages" in data["cPages"]:
            for p in data["cPages"]["pages"]:
                page_refs.append(
                    PageRef(
                        uuid=UUID(p["id"]),
                        template=p.get("template", {}).get("value", "Blank"),
                        redirect=p.get("redirect", {}).get("value"),
                    )
                )
        elif "pages" in data:
            for page_uuid in data["pages"]:
                page_refs.append(PageRef(uuid=UUID(page_uuid)))

        extra = ExtraMetadata.from_json(data.get("extraMetadata", {}))

        return cls(
            file_type=file_type,
            format_version=data.get("formatVersion", 2),
            orientation=data.get("orientation", "portrait"),
            page_count=data.get("pageCount", len(page_refs)),
            page_refs=page_refs,
            extra_metadata=extra,
            margins=data.get("margins", 125),
            font_name=data.get("fontName", ""),
            line_height=data.get("lineHeight", -1),
            text_scale=data.get("textScale", 1.0),
            text_alignment=data.get("textAlignment", "justify"),
            zoom_mode=data.get("zoomMode", "bestFit"),
            custom_zoom_scale=data.get("customZoomScale", 1.0),
        )

    @classmethod
    def from_path(cls, path: Path) -> ContentInfo:
        """Load and parse a .content file from disk."""
        return cls.from_json(json.loads(path.read_text()))


class Document(BaseModel):
    """A complete reMarkable document with metadata, content info, and pages.

    This is the top-level container that combines all the separate files
    on the device filesystem into a single coherent object. It unifies:

    - {UUID}.metadata -- display name, folder, sync state (DocumentMetadata)
    - {UUID}.content  -- page list, file type, tool settings (ContentInfo)
    - {UUID}/*.rm     -- per-page stroke data (list of Page)
    - {UUID}.pagedata -- per-page template names (list of str)

    Use this model to represent a fully loaded document for rendering,
    export, or analysis.
    """

    uuid: UUID = Field(
        description="Unique identifier for the document. All associated files on "
        "disk use this UUID as their filename stem.",
    )
    metadata: DocumentMetadata = Field(
        description="Parsed .metadata file: display name, folder hierarchy, sync state.",
    )
    content: ContentInfo = Field(
        description="Parsed .content file: page structure, file type, layout settings.",
    )
    pages: list[Page] = Field(
        default_factory=list,
        description="Loaded page data with layers and strokes. May be empty if "
        "only metadata was loaded without parsing .rm files.",
    )
    templates: list[str] = Field(
        default_factory=list,
        description="Per-page template names from the .pagedata file. "
        "One entry per page, in page order.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def name(self) -> str:
        """The user-visible document name from metadata."""
        return self.metadata.visible_name

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_notebook(self) -> bool:
        """Whether this document is a native notebook (created on device)."""
        return self.content.file_type == FileType.NOTEBOOK

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_pdf(self) -> bool:
        """Whether this document is an uploaded/annotated PDF."""
        return self.content.file_type == FileType.PDF

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_epub(self) -> bool:
        """Whether this document is an uploaded/annotated EPUB."""
        return self.content.file_type == FileType.EPUB

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_folder(self) -> bool:
        """Whether this entry is a folder (collection), not a document."""
        return self.metadata.doc_type == DocumentType.COLLECTION

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_trashed(self) -> bool:
        """Whether this document is in the trash (by parent or deleted flag)."""
        return self.metadata.parent == "trash" or self.metadata.deleted

    def base_path(self, xochitl_dir: Path) -> Path:
        """Base path for all this document's files within the xochitl directory.

        Args:
            xochitl_dir: Path to the xochitl directory on the device.

        Returns:
            Path like ``/home/root/.local/share/remarkable/xochitl/{uuid}``.
        """
        return xochitl_dir / str(self.uuid)
