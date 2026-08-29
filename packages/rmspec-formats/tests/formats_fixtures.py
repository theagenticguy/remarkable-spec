"""Builders shared by this package's test modules.

Not a ``conftest.py`` on purpose. ``packages/*/tests/`` directories are deliberately
not importable packages (``tests/architecture/test_test_module_names.py`` asserts it),
so two sibling packages each shipping a bare ``conftest.py`` would both resolve to the
module name ``conftest`` and the second would fail collection with
``ImportPathMismatchError``. A uniquely named helper module cannot collide, and pytest
puts each test module's own directory on ``sys.path`` before importing it, so a plain
``import formats_fixtures`` works from any test module in this directory.

Two things live here:

1. **Scene byte builders.** Real v6 bytes, written by ``rmscene``'s own writer, so no
   binary fixture is committed and no device or network is involved. That also makes
   every branch of the codec reachable: an unknown wire tool id, a zero-width text
   block and a negative thickness cannot be produced by hand-editing a committed file
   but are one argument away here.
2. **A store spec plus two builders over it.** :func:`build_xochitl` writes a real
   xochitl directory and returns the real adapter; :class:`FakeDocumentRepository`
   answers the same spec from prebuilt models. The contract tests run one assertion
   table against both, which is what makes them a port contract rather than an adapter
   test. :data:`CANNED_CONTENT` is deliberately shaped like what :func:`inked_scene`
   decodes to -- one visible layer, one stroke -- so the table can assert a stroke count
   without knowing which implementation it is talking to.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final, cast
from uuid import UUID

from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import (
    AuthorIdsBlock,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGlyphItemBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
    write_blocks,
)
from rmscene.tagged_block_common import CrdtId, LwwValue

from rmspec.domain.errors import (
    CorruptPageData,
    DocumentNotFound,
    DocumentStoreUnavailable,
    MalformedDocument,
    PageNotFound,
    UnsupportedPageFormat,
)
from rmspec.domain.models import (
    Document,
    DocumentId,
    DocumentKind,
    DocumentMetadata,
    DocumentSummary,
    Layer,
    Page,
    PageContent,
    PageDefect,
    PageDefectCode,
    PageId,
    PenColor,
    PenType,
    SourceKind,
    Stroke,
)
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT
from rmspec.formats import SceneCodec, XochitlDocumentRepository
from rmspec.formats.page_index import decode_pagedata

if TYPE_CHECKING:
    from pathlib import Path

    from rmscene.scene_stream import Block

ROOT_ID: Final = CrdtId(0, 1)
"""Node id ``rmscene`` gives the scene tree's root group."""

VALID_HEADER: Final = b"reMarkable .lines file, version=6          "
"""The 43-byte v6 header, spelled here so a test can truncate or re-version it."""

TRUNCATED_SCENE: Final = VALID_HEADER + b"\x40\x00\x00\x00\x01\x01"
"""A v6 header followed by a block length that runs off the end of the file."""

V5_SCENE: Final = b"reMarkable .lines file, version=5          " + b"\x00" * 8
"""A scene file declaring a version this codec does not decode."""


def scene_bytes(*items: Block, version: str = "3.7") -> bytes:
    """Serialise scene blocks into a v6 ``.rm`` file.

    Parameters
    ----------
    *items
        The blocks to write, in file order. The four preamble blocks every real file
        carries are prepended.
    version
        Firmware version the writer should emit for. ``"3.7"`` writes v2 line blocks;
        ``"3.0"`` and below write the v1 float-encoded point channels.

    Returns
    -------
    bytes
        A complete scene file.
    """
    preamble: tuple[Block, ...] = (
        AuthorIdsBlock(author_uuids={1: UUID(int=1)}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        TreeNodeBlock(si.Group(node_id=ROOT_ID)),
    )
    buffer = io.BytesIO()
    write_blocks(buffer, [*preamble, *items], options={"version": version})
    return buffer.getvalue()


def layer_blocks(
    *,
    node: int,
    name: str = "Layer 1",
    visible: bool = True,
    items: tuple[object, ...] = (),
    parent: CrdtId = ROOT_ID,
) -> tuple[Block, ...]:
    """Build the blocks that declare one layer and attach its items.

    Parameters
    ----------
    node
        Second component of the layer's ``CrdtId``. Must be unique within a file.
    name
        The layer's name in the tablet UI.
    visible
        Whether the layer is shown.
    items
        Scene item values to place in the layer, in draw order. A ``si.GlyphRange``
        becomes a glyph block and anything else a line block, which is what makes a
        highlight round-trip through the writer instead of being asserted by hand.
    parent
        Node the layer hangs off. The root, unless a nested group is being built.

    Returns
    -------
    tuple[Block, ...]
        The tree block registering the node, the node block naming it, the group item
        block attaching it to its parent, and one item block per item.
    """
    layer = CrdtId(0, node)
    blocks: list[Block] = [
        SceneTreeBlock(tree_id=layer, node_id=CrdtId(0, 0), is_update=True, parent_id=parent),
        TreeNodeBlock(
            si.Group(
                node_id=layer,
                label=LwwValue(CrdtId(0, 0), name),
                visible=LwwValue(CrdtId(0, 0), visible),
            )
        ),
        SceneGroupItemBlock(parent, _sequence_item(node * 100, layer)),
    ]
    blocks.extend(
        _item_block(layer, node * 100 + offset + 1, item) for offset, item in enumerate(items)
    )
    return tuple(blocks)


def rootless_scene(*items: object) -> bytes:
    """Build a page whose items hang off the root with no layer group at all.

    The state that made the legacy reader invent a layer called ``"Layer 1"``.

    Parameters
    ----------
    *items
        Scene item values to attach directly to the root, in draw order.

    Returns
    -------
    bytes
        A complete v6 scene file with no layer group.
    """
    return scene_bytes(
        *(_item_block(ROOT_ID, index + 1, item) for index, item in enumerate(items))
    )


def _item_block(parent: CrdtId, index: int, value: object) -> Block:
    """Wrap one scene item value in the block type that carries it.

    Parameters
    ----------
    parent
        Node the item belongs to.
    index
        Second component of the item's own ``CrdtId``.
    value
        The scene item value.

    Returns
    -------
    Block
        A glyph block for a highlight, a line block for anything else.
    """
    item = _sequence_item(index, value)
    if isinstance(value, si.GlyphRange):
        return SceneGlyphItemBlock(parent, item)
    return SceneLineItemBlock(parent, item)


def _sequence_item[T](index: int, value: T) -> CrdtSequenceItem[T]:
    """Wrap one value as a CRDT sequence member.

    Generic so that a member built here can be added to a typed ``CrdtSequence``: the
    scene-tree tests attach one to a ``CrdtSequence[SceneItem]`` directly.

    Parameters
    ----------
    index
        Second component of the member's own ``CrdtId``.
    value
        The value to carry.

    Returns
    -------
    CrdtSequenceItem[T]
        The member, with no neighbours and nothing deleted.
    """
    return CrdtSequenceItem(CrdtId(0, index), CrdtId(0, 0), CrdtId(0, 0), 0, value)


def stroke_item(
    *,
    tool: int = int(si.Pen.FINELINER_2),
    color: int = int(si.PenColor.BLUE),
    thickness: float = 2.0,
    starting_length: float = 0.5,
    points: tuple[si.Point, ...] = (),
) -> si.Line:
    """Build one scene line.

    Wire ids are plain ``int`` and cast into the parser's enums, because a tool or
    colour id *outside* those enums is exactly the case the substitution defects exist
    for -- and ``rmscene``'s own reader refuses to produce one, since it constructs
    ``si.Pen(tool_id)`` while reading.

    Parameters
    ----------
    tool
        Wire tool id.
    color
        Wire colour index.
    thickness
        The tablet's thickness-slider value.
    starting_length
        Cumulative length offset.
    points
        Stylus samples, in order. Two samples by default.

    Returns
    -------
    si.Line
        The line, ready to place in a layer.
    """
    samples = points or (si.Point(1.5, 2.5, 100, 30, 40, 200), si.Point(3.0, 4.0, 0, 0, 0, 0))
    return si.Line(
        color=cast("si.PenColor", color),
        tool=cast("si.Pen", tool),
        points=list(samples),
        thickness_scale=thickness,
        starting_length=starting_length,
    )


def text_item(*, width: float = 400.0, pos_x: float = -10.0, pos_y: float = 20.0) -> si.Text:
    """Build one typed-text block with two string members and one formatting code.

    Parameters
    ----------
    width
        Width of the text box in screen units.
    pos_x
        Horizontal position of the box.
    pos_y
        Vertical position of the box.

    Returns
    -------
    si.Text
        The text item, ready to place in a layer.
    """
    items: CrdtSequence[str | int] = CrdtSequence()
    items.add(CrdtSequenceItem(CrdtId(0, 901), CrdtId(0, 0), CrdtId(0, 0), 0, "hello "))
    items.add(CrdtSequenceItem(CrdtId(0, 902), CrdtId(0, 901), CrdtId(0, 0), 0, 1))
    items.add(CrdtSequenceItem(CrdtId(0, 903), CrdtId(0, 902), CrdtId(0, 0), 0, "world"))
    return si.Text(items=items, styles={}, pos_x=pos_x, pos_y=pos_y, width=width)


def inked_scene() -> bytes:
    """Build a one-layer, one-stroke page.

    Returns
    -------
    bytes
        A complete v6 scene file whose single visible layer holds one stroke.
    """
    return scene_bytes(*layer_blocks(node=11, items=(stroke_item(),)))


# ─────────────────────────── store spec ───────────────────────────


class PageState(StrEnum):
    """What the store holds for one page, declared by a test rather than derived.

    The contract table asserts that both implementations report the declared state, so
    the real adapter has to work it out from the bytes while the fake is told -- which is
    what stops the table from being a mirror of one implementation.
    """

    ABSENT = "absent"
    """No artifact file at all."""

    STUB = "stub"
    """A zero-byte artifact: the unannotated page of a PDF the tablet stubbed."""

    INKED = "inked"
    """An artifact holding one visible layer with one stroke."""

    UNDECODABLE = "undecodable"
    """An artifact that is present, non-empty, and not a decodable scene file."""


@dataclass(frozen=True, slots=True)
class PageSpec:
    """One page of a document as a test declares it."""

    uuid: str
    state: PageState = PageState.INKED
    template: str | None = None
    pdf_page_index: int | None = None

    @property
    def artifact(self) -> bytes | None:
        """The bytes the store holds for this page.

        Returns
        -------
        bytes | None
            ``None`` when no artifact file exists, otherwise the bytes to write.
        """
        return _ARTIFACTS[self.state]

    @property
    def fingerprint(self) -> str:
        """The token the *fake* reports for this page. Deliberately opaque.

        ``ports/formats.py`` says a fingerprint is "not comparable across
        implementations, so a cache shared between stores keys on the store's identity
        as well". Returning the real adapter's SHA-256 here would have the shared
        contract table assert exactly the cross-implementation digest equality the port
        forbids relying on -- and it is what dragged 354 bytes of ``rmscene``-serialised
        v6 data into the in-memory path, since the fake could not answer
        ``page_fingerprint`` without running the writer. Every invariant the port really
        states is satisfiable by a literal: non-empty and opaque, the sentinel exactly
        when the state is ``ABSENT``, stable across calls, distinct for distinct
        artifacts, and ``page_fingerprints`` agreeing with ``page_fingerprint``.

        Returns
        -------
        str
            ``ABSENT_ARTIFACT_FINGERPRINT`` when there is no artifact, otherwise a
            per-page literal that is a hash of nothing.
        """
        return ABSENT_ARTIFACT_FINGERPRINT if self.artifact is None else f"fp-{self.uuid}"


_ARTIFACTS: Final[dict[PageState, bytes | None]] = {
    PageState.ABSENT: None,
    PageState.STUB: b"",
    PageState.INKED: inked_scene(),
    PageState.UNDECODABLE: TRUNCATED_SCENE,
}
"""The bytes each declared page state is written as."""


@dataclass(frozen=True, slots=True)
class DocumentSpec:
    """One document of a store as a test declares it."""

    uuid: str
    visible_name: str = "Notebook"
    kind: DocumentKind = DocumentKind.DOCUMENT
    source: SourceKind | None = SourceKind.NOTEBOOK
    pages: tuple[PageSpec, ...] = ()
    templates: tuple[str, ...] = ()
    with_content: bool = True

    @property
    def doc_id(self) -> DocumentId:
        """Identity of the document this spec describes.

        Returns
        -------
        DocumentId
            The identity both implementations are addressed by.
        """
        return DocumentId(uuid=self.uuid)

    @property
    def page_ids(self) -> tuple[PageId, ...]:
        """Every page identity, in document order.

        Returns
        -------
        tuple[PageId, ...]
            The identities the summary must carry.
        """
        return tuple(PageId(uuid=page.uuid) for page in self.pages)

    @property
    def pagedata_bytes(self) -> bytes:
        """The exact ``.pagedata`` bytes this spec means.

        One property, so :func:`write_store` and :func:`effective_template` cannot
        disagree about what the spec's ``templates`` tuple *is* on disk. They did: the
        writer joined the lines and the real adapter then ran them through
        ``decode_pagedata``, whose whole-text ``strip()`` deletes a leading blank line
        and renumbers every later one, while ``effective_template`` indexed the tuple
        positionally and modelled no strip. A spec whose first template line is empty
        therefore had the two implementations report different template columns, and no
        spec in the contract table exercised it.

        Returns
        -------
        bytes
            The lines, newline-joined, with no trailing newline -- what the tablet
            writes.
        """
        return "\n".join(self.templates).encode()


LAST_MODIFIED: Final = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
"""The instant every fixture document records, so no test needs a clock."""


def metadata_json(spec: DocumentSpec) -> bytes:
    """Render one document's ``.metadata`` sidecar.

    Parameters
    ----------
    spec
        The document to render.

    Returns
    -------
    bytes
        The sidecar's bytes, with ``lastModified`` as the quoted millisecond epoch the
        store really writes.
    """
    return json.dumps(
        {
            "visibleName": spec.visible_name,
            "type": "DocumentType" if spec.kind is DocumentKind.DOCUMENT else "CollectionType",
            "parent": "",
            "deleted": False,
            "pinned": False,
            "lastModified": str(int(LAST_MODIFIED.timestamp() * 1000)),
            "version": 3,
            "synced": True,
        }
    ).encode()


def content_json(spec: DocumentSpec) -> bytes:
    """Render one document's ``.content`` sidecar in the firmware-3.x ``cPages`` shape.

    A page names ``template`` and ``redir`` only when the spec gives it one, so an
    absent key is exercised as often as a present one.

    Parameters
    ----------
    spec
        The document to render.

    Returns
    -------
    bytes
        The sidecar's bytes.
    """
    pages: list[dict[str, object]] = []
    for page in spec.pages:
        claim: dict[str, object] = {"id": page.uuid}
        if page.template is not None:
            claim["template"] = {"value": page.template}
        if page.pdf_page_index is not None:
            claim["redir"] = {"value": page.pdf_page_index}
        pages.append(claim)
    body: dict[str, object] = {
        "formatVersion": 2,
        "orientation": "portrait",
        "cPages": {"pages": pages},
    }
    if spec.source is not None:
        body["fileType"] = spec.source.value
    return json.dumps(body).encode()


def write_store(root: Path, *specs: DocumentSpec) -> Path:
    """Write a real xochitl directory from a store spec.

    Parameters
    ----------
    root
        Directory to write into. Created if absent.
    *specs
        The documents to write.

    Returns
    -------
    Path
        *root*, for chaining into a repository constructor.
    """
    root.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        (root / f"{spec.uuid}.metadata").write_bytes(metadata_json(spec))
        if spec.with_content:
            (root / f"{spec.uuid}.content").write_bytes(content_json(spec))
        if spec.templates:
            (root / f"{spec.uuid}.pagedata").write_bytes(spec.pagedata_bytes)
        stored = [(page, raw) for page in spec.pages if (raw := page.artifact) is not None]
        if stored:
            (root / spec.uuid).mkdir(exist_ok=True)
        for page, raw in stored:
            (root / spec.uuid / f"{page.uuid}.rm").write_bytes(raw)
    return root


def build_xochitl(root: Path, *specs: DocumentSpec) -> XochitlDocumentRepository:
    """Write a store spec to disk and bind the real adapter over it.

    Parameters
    ----------
    root
        Directory to write into.
    *specs
        The documents to write.

    Returns
    -------
    XochitlDocumentRepository
        The real adapter, with the real codec bound.
    """
    return XochitlDocumentRepository(root=write_store(root, *specs), codec=SceneCodec())


# ─────────────────────────── in-memory doubles ───────────────────────────

CANNED_CONTENT: Final = PageContent(
    layers=(
        Layer(
            name="Layer 1",
            strokes=(Stroke(pen=PenType.FINELINER_2, color=PenColor.BLUE, thickness_scale=2.0),),
        ),
    )
)
"""What the fake codec returns, and what the fake repository holds for an inked page."""

CANNED_DEFECT: Final = PageDefect(
    code=PageDefectCode.UNKNOWN_PEN_SUBSTITUTED, detail="canned degradation"
)
"""A degradation the fake codec can be told to report, to prove defects are values."""


@dataclass(slots=True)
class FakePageCodec:
    """An in-memory ``PageCodec``: one canned return value and two ways to raise.

    Records every call, so a test can assert that a repository hands the codec the page
    uuid it already holds and the bytes it read, with no real parser in the way.
    """

    content: PageContent = CANNED_CONTENT
    corrupt: bool = False
    unsupported: bool = False
    calls: list[tuple[bytes, str]] = field(default_factory=list)

    def decode_page(self, raw: bytes, page_ref: str, /) -> PageContent:
        """Return the canned content, or raise the configured failure.

        Parameters
        ----------
        raw
            The bytes to decode. Recorded, never inspected.
        page_ref
            What to call the bytes in a failure. Recorded, never resolved.

        Returns
        -------
        PageContent
            The canned content.

        Raises
        ------
        CorruptPageData
            When ``corrupt`` is set.
        UnsupportedPageFormat
            When ``unsupported`` is set.
        """
        self.calls.append((raw, page_ref))
        if self.corrupt:
            raise CorruptPageData(page_uuid=page_ref, detail="canned failure", offset=7)
        if self.unsupported:
            raise UnsupportedPageFormat(
                page_uuid=page_ref, observed_version="3", supported_versions=("6",)
            )
        return self.content


class FakeDocumentRepository:
    """An in-memory ``DocumentRepository`` built from the same spec the real one is.

    Holds prebuilt models and one fingerprint per page, both independent of any codec,
    so the shared contract table cannot be satisfied by one implementation's quirks. Two
    knobs cover the branches a mapping alone cannot reach: ``unavailable`` makes every
    method fail as an unreachable store, and ``malformed`` makes one document's sidecars
    undecodable.
    """

    def __init__(
        self, *specs: DocumentSpec, unavailable: bool = False, malformed: str | None = None
    ) -> None:
        self._specs = {spec.uuid: spec for spec in specs}
        self._unavailable = unavailable
        self._malformed = malformed

    def _spec(self, doc_id: DocumentId, /) -> DocumentSpec:
        """Resolve one document's spec, or fail as the port documents.

        Parameters
        ----------
        doc_id
            Identity to resolve.

        Returns
        -------
        DocumentSpec
            The spec.

        Raises
        ------
        DocumentStoreUnavailable
            When the store was built unavailable.
        DocumentNotFound
            When no spec has that identity.
        MalformedDocument
            When this document was built malformed.
        """
        if self._unavailable:
            raise DocumentStoreUnavailable(store="fake", detail="built unavailable")
        spec = self._specs.get(doc_id.uuid)
        if spec is None:
            raise DocumentNotFound(query=doc_id.uuid, store="fake")
        if self._malformed == doc_id.uuid:
            raise MalformedDocument(
                document_uuid=doc_id.uuid, artifact=".content", detail="built malformed"
            )
        return spec

    @staticmethod
    def _metadata(spec: DocumentSpec, /) -> DocumentMetadata:
        """Build the metadata the spec declares.

        Parameters
        ----------
        spec
            The document.

        Returns
        -------
        DocumentMetadata
            Equivalent to what the real adapter decodes from the written sidecars.
        """
        return DocumentMetadata(
            visible_name=spec.visible_name,
            kind=spec.kind,
            source=spec.source if spec.with_content else None,
            last_modified=LAST_MODIFIED,
        )

    @staticmethod
    def _page(spec: DocumentSpec, position: int, /) -> Page:
        """Build one prebuilt page in the state the spec declares.

        Parameters
        ----------
        spec
            The owning document.
        position
            Zero-based position in document order.

        Returns
        -------
        Page
            The page, in the declared state.
        """
        page = spec.pages[position]
        content: PageContent | None = None
        defects: tuple[PageDefect, ...] = ()
        if page.state is PageState.ABSENT:
            defects = (PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail="none stored"),)
        elif page.state is PageState.UNDECODABLE:
            defects = (
                PageDefect(code=PageDefectCode.CONTENT_UNDECODABLE, detail="would not decode"),
            )
        elif page.state is PageState.STUB:
            content = PageContent()
        else:
            content = CANNED_CONTENT
        return Page(
            page_id=PageId(uuid=page.uuid),
            index=position,
            template_name=effective_template(spec, position),
            pdf_page_index=page.pdf_page_index,
            content=content,
            defects=defects,
        )

    def list_documents(self) -> tuple[DocumentSummary, ...]:
        """List every document the fake holds.

        Returns
        -------
        tuple[DocumentSummary, ...]
            One summary per document, ordered by identifier, omitting one built
            malformed -- the same omission the real adapter makes.

        Raises
        ------
        DocumentStoreUnavailable
            When the store was built unavailable.
        """
        if self._unavailable:
            raise DocumentStoreUnavailable(store="fake", detail="built unavailable")
        return tuple(
            self.summary(DocumentId(uuid=uuid))
            for uuid in sorted(self._specs)
            if uuid != self._malformed
        )

    def summary(self, doc_id: DocumentId, /) -> DocumentSummary:
        """Summarise one document.

        Parameters
        ----------
        doc_id
            Identity to summarise.

        Returns
        -------
        DocumentSummary
            Identity, metadata and page identities in document order.
        """
        spec = self._spec(doc_id)
        return DocumentSummary(doc_id=doc_id, metadata=self._metadata(spec), pages=spec.page_ids)

    def load(self, doc_id: DocumentId, /) -> Document:
        """Load one document with every page in its declared state.

        Parameters
        ----------
        doc_id
            Identity to load.

        Returns
        -------
        Document
            The aggregate, in document order.
        """
        spec = self._spec(doc_id)
        return Document(
            doc_id=doc_id,
            metadata=self._metadata(spec),
            pages=tuple(self._page(spec, position) for position in range(len(spec.pages))),
        )

    def load_page(self, doc_id: DocumentId, page_id: PageId, /) -> Page:
        """Load one page.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page.

        Returns
        -------
        Page
            Exactly what :meth:`load` places at that identity.

        Raises
        ------
        PageNotFound
            When the document claims no such page.
        """
        spec = self._spec(doc_id)
        for position, claimed in enumerate(spec.page_ids):
            if claimed == page_id:
                return self._page(spec, position)
        raise PageNotFound(
            document_uuid=doc_id.uuid, page=page_id.uuid, page_count=len(spec.pages)
        )

    def page_fingerprint(self, doc_id: DocumentId, page_id: PageId, /) -> str:
        """Fingerprint one page's stored bytes.

        Parameters
        ----------
        doc_id
            Identity of the owning document.
        page_id
            Identity of the page.

        Returns
        -------
        str
            The same token the real adapter reports for the same declared state.

        Raises
        ------
        PageNotFound
            When the document claims no such page.
        """
        spec = self._spec(doc_id)
        for page in spec.pages:
            if page.uuid == page_id.uuid:
                return page.fingerprint
        raise PageNotFound(
            document_uuid=doc_id.uuid, page=page_id.uuid, page_count=len(spec.pages)
        )

    def page_fingerprints(self, doc_id: DocumentId, /) -> dict[PageId, str]:
        """Fingerprint every claimed page in document order.

        Parameters
        ----------
        doc_id
            Identity of the document.

        Returns
        -------
        dict[PageId, str]
            One entry per claimed page.
        """
        spec = self._spec(doc_id)
        return {PageId(uuid=page.uuid): page.fingerprint for page in spec.pages}


def effective_template(spec: DocumentSpec, position: int, /) -> str | None:
    """Apply the legacy template precedence to one page of a spec.

    A ``.pagedata`` line at this position wins whenever one exists, *even when it is
    empty*; only then does the page's own template apply; and an empty name is ``None``.

    "This position" is a position in the *decoded* sidecar, not in the spec's tuple, and
    that distinction is the whole of the correction: the lines are run through the same
    ``decode_pagedata`` the real adapter uses, so the whole-text ``strip()`` that drops a
    leading blank line is modelled once instead of twice. One rule, one place, no drift.

    Parameters
    ----------
    spec
        The owning document.
    position
        Zero-based position in document order.

    Returns
    -------
    str | None
        The template name, or ``None`` for no template.
    """
    lines = decode_pagedata(spec.pagedata_bytes)
    if position < len(lines):
        return lines[position] or None
    return spec.pages[position].template or None
