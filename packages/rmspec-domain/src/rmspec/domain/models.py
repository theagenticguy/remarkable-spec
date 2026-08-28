"""The domain model: what a reMarkable document *is*, independent of how it is stored.

Every Protocol in :mod:`rmspec.domain.ports` exchanges these types and imports them from here
-- ``from rmspec.domain.models import Document, DocumentId`` -- so this module is the stable
address for all of them.

One module, like every ports slice
----------------------------------
Not a package of ten small modules, for a mechanical reason worth recording so nobody splits
it back up: pydantic resolves a field's annotation at run time, so a model referenced across
module boundaries must be imported at run time too, and ruff's ``TC001`` then demands that
annotation-only first-party import move into a ``TYPE_CHECKING`` block -- where pydantic can no
longer see it. ``errors.py`` and each ``ports/*.py`` are single self-contained modules for the
same reason. Section banners below stand in for what would otherwise be file boundaries; the
order is dependency order, so nothing refers forward.

Conventions every model here follows
------------------------------------
Frozen and ``extra="forbid"``, so a typo'd field name is a construction error rather than a
silently ignored one. Constrained rather than merely annotated: ranges, minimum lengths and
totality are pydantic validators, which is what lets the port modules declare no error for a
state a constraint already makes unconstructible. Ordered collections are tuples, because
order is part of several contracts here and a list invites in-place reordering of a value.

Third-party dependency: pydantic only. No ``rmscene``, no ``sqlite3``, no ``boto3``, no
``pathlib.Path`` in any field -- a filesystem location is an adapter's identity for a resource,
carried as an opaque ``str`` that is displayed and never reopened -- and no clock: every
timestamp is a required, timezone-aware field the caller supplies, so nothing here has to be
frozen in a test.

Not modelled here, on purpose
-----------------------------
- **Pen physics.** Widths and opacities are float arithmetic with one caller inside
  ``rmspec-render``; ``ports/render.py`` records why they get no port and no registry.
  :class:`PenType` is the wire enum and stops there.
- **Scene wire spellings.** The v6 format's CRDT blocks, tagged items and sequence ids stay in
  the formats adapter; what crosses into the domain is a :class:`Page` of :class:`Stroke`.
  The *sidecar* spellings do live here, in exactly two places:
  :meth:`DocumentMetadata.decode` and :meth:`DocumentLayout.decode` read ``visibleName``,
  ``lastModified`` as a millisecond epoch and ``type`` as ``"DocumentType"``.
  ``ports/formats.py`` declines a sidecar codec port on the grounds that this mapping needs
  nothing but ``json`` and pydantic, both already legal here -- and one decoder in the domain
  is the whole point: the legacy tree hand-mirrored the same field knowledge into
  ``formats/metadata.py``, ``formats/content.py`` and two CLI bodies, which is how they came
  to disagree.
- **Errors.** They live in :mod:`rmspec.domain.errors` as one tree for the whole workspace.
  Nothing here imports them and nothing here raises one; a model refuses a bad value with
  ``ValueError`` from a validator, which the CLI maps once at its boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

__all__ = [
    "EXPORT_PALETTE",
    "PAPER_PRO_PHYSICAL_PALETTE",
    "PAPER_PRO_SCREEN",
    "RM2_SCREEN",
    "DiagramArtifact",
    "DiagramCacheKey",
    "Document",
    "DocumentId",
    "DocumentKind",
    "DocumentLayout",
    "DocumentMetadata",
    "DocumentSummary",
    "ExtraMetadata",
    "Layer",
    "OcrArtifact",
    "OcrCacheKey",
    "Page",
    "PageContent",
    "PageContentKind",
    "PageDefect",
    "PageDefectCode",
    "PageId",
    "PageOrientation",
    "PageText",
    "Palette",
    "PenColor",
    "PenType",
    "Point",
    "RecordedSyncAuditEntry",
    "Rgb",
    "ScreenSpec",
    "SourceKind",
    "Stroke",
    "SyncAuditEntry",
    "SyncOperation",
    "SyncOutcome",
    "SyncedDocument",
    "SyncedPage",
    "TextBlock",
    "TextProvenance",
    "pen_from_wire",
]


_MM_PER_INCH = 25.4

_UINT8_MAX = 255
_UINT16_MAX = 65535
_FULL_TURN = math.pi * 2

_FIELD_SEPARATOR = b"\x1f"
"""Byte separating digest components, so concatenation cannot be ambiguous."""

_ITEM_SEPARATOR = "\x1e"
"""Character separating the items of a component that is itself a sequence."""

_MS_PER_SECOND = 1000
"""Divisor turning the store's millisecond epoch into the seconds ``datetime`` wants."""

_TRASH_PARENT = "trash"
"""The value the store writes into ``parent`` for a trashed entry, in place of a folder."""

_BOOLEAN_WORDS = {"true": True, "false": False}
"""Spellings of a boolean this decoder accepts as text, since the store is not consistent."""

_IDENTIFIER_PATTERN = r"^[0-9A-Za-z._-]+$"
"""Characters a store identifier may use. No separator, so no identifier can traverse."""

_IDENTIFIER_MAX_LENGTH = 64
"""Longest identifier accepted. A uuid is 36; this leaves room without leaving a path."""


def _digest(tag: bytes, parts: tuple[str, ...]) -> str:
    """Fold a domain tag and an ordered set of components into one hex digest.

    Parameters
    ----------
    tag
        Domain-and-version label, so two different key types with identical components cannot
        collide and so a future change of scheme is a mechanical miss rather than a silent
        reinterpretation.
    parts
        The components, in a fixed order.

    Returns
    -------
    str
        Lowercase hex SHA-256.
    """
    hasher = hashlib.sha256()
    hasher.update(tag)
    for part in parts:
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(part.encode())
    return hasher.hexdigest()


# ──────────────────────── Sidecar json readers ────────────────────────
#
#  The narrowing readers the two ``decode`` classmethods are built from.
#
#  ``json.loads`` hands back ``Any``, and a model field is the wrong place to discover that a
#  key held a list where a string belonged. Each reader below takes one untyped json value and
#  returns one narrow domain value: a missing key (``None``) becomes the caller's stated
#  default, a value of the wrong json type raises ``TypeError``, and a value of the right type
#  that cannot be read raises ``ValueError``. That split is the same one ``int`` makes --
#  ``int(None)`` is a ``TypeError`` and ``int("x")`` is a ``ValueError`` -- and it is what lets
#  ``decode`` state both in one ``Raises`` section instead of leaking a pydantic
#  ``ValidationError`` shaped by whichever field happened to be validated first.


def _json_object(raw: bytes, /) -> dict[str, object]:
    """Parse sidecar bytes into the members of one json object.

    Parameters
    ----------
    raw
        The sidecar's bytes, exactly as the store holds them.

    Returns
    -------
    dict[str, object]
        The object's members. Values stay untyped; the readers below narrow them.

    Raises
    ------
    TypeError
        If the payload is valid json that is not an object.
    ValueError
        If the payload is not valid json at all.
    """
    decoded: object = json.loads(raw)
    if not isinstance(decoded, dict):
        msg = f"expected a json object, got {type(decoded).__name__}"
        raise TypeError(msg)
    return {str(key): value for key, value in decoded.items()}


def _string(value: object, /, *, default: str = "") -> str:
    """Read a json string.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.
    default
        What an absent key means.

    Returns
    -------
    str
        The string, or ``default``.

    Raises
    ------
    TypeError
        If the value is present and is not a json string.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    msg = f"expected a json string, got {type(value).__name__}"
    raise TypeError(msg)


def _flag(value: object, /, *, default: bool = False) -> bool:
    """Read a json boolean, tolerating the store's ``"true"`` spelling.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.
    default
        What an absent key means.

    Returns
    -------
    bool
        The flag, or ``default``.

    Raises
    ------
    TypeError
        If the value is present and is neither a json boolean nor a boolean word.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = _BOOLEAN_WORDS.get(value.strip().lower())
        if word is not None:
            return word
    msg = f"expected a json boolean, got {type(value).__name__}"
    raise TypeError(msg)


def _whole(value: object, /, *, default: int) -> int:
    """Read a json integer, tolerating the store's quoted numbers.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.
    default
        What an absent key means.

    Returns
    -------
    int
        The integer, or ``default``.

    Raises
    ------
    TypeError
        If the value is present and is not a json number or numeric string.
    ValueError
        If the value is a string that is not a number.
    """
    if value is None:
        return default
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        return int(value.strip())
    msg = f"expected a json integer, got {type(value).__name__}"
    raise TypeError(msg)


def _fraction(value: object, /, *, default: float) -> float:
    """Read a json number as a float.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.
    default
        What an absent key means.

    Returns
    -------
    float
        The number, or ``default``.

    Raises
    ------
    TypeError
        If the value is present and is not a json number or numeric string.
    ValueError
        If the value is a string that is not a number.
    """
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return float(value.strip())
    msg = f"expected a json number, got {type(value).__name__}"
    raise TypeError(msg)


def _moment(value: object, /) -> datetime | None:
    """Read a millisecond epoch, which the store writes as a quoted number.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.

    Returns
    -------
    datetime | None
        An aware UTC datetime, or ``None`` when the key was absent, null, or an empty string.
        Zero is a real instant and is returned as one: the store writing ``"0"`` is different
        from the store writing nothing.

    Raises
    ------
    TypeError
        If the value is present and is not a json number or numeric string.
    ValueError
        If the value is a string that is not a number.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        milliseconds = float(text)
    elif isinstance(value, int | float):
        milliseconds = float(value)
    else:
        msg = (
            f"expected a millisecond epoch as a json string or number, got {type(value).__name__}"
        )
        raise TypeError(msg)
    return datetime.fromtimestamp(milliseconds / _MS_PER_SECOND, tz=UTC)


def _settings(value: object, /) -> dict[str, str]:
    """Read the store's tool-settings object, which is opaque display data.

    Every value is rendered with ``str`` rather than type-checked. These settings are
    reMarkable-internal identifiers this workspace never branches on, so a firmware that writes
    a number where it used to write a string should change what ``rmspec inspect content``
    prints, not whether the document decodes.

    Parameters
    ----------
    value
        The raw json value, or ``None`` when the key was absent.

    Returns
    -------
    dict[str, str]
        The settings, or an empty mapping.

    Raises
    ------
    TypeError
        If the value is present and is not a json object.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    msg = f"expected a json object of tool settings, got {type(value).__name__}"
    raise TypeError(msg)


def _reject_dot_segment(uuid: str, /) -> None:
    """Reject an identifier that is a filesystem dot segment rather than an identity.

    Parameters
    ----------
    uuid
        The identifier to check.

    Raises
    ------
    ValueError
        If every character is a dot -- ``"."``, ``".."``, and every longer run.
    """
    if set(uuid) <= {"."}:
        msg = f"identifier {uuid!r} is a path segment, not an identity"
        raise ValueError(msg)


# ──────────────────────── Identity ────────────────────────
#
#  Document and page identity, as two types that cannot be swapped for one another.
#
#  ``DocumentRepository.load_page(doc_id, page_id)`` takes two uuid-shaped arguments in a
#  fixed order. As bare ``str`` they are interchangeable, and transposing them is a call that
#  type-checks, runs, and raises ``DocumentNotFound`` from inside an adapter. Two frozen
#  single-field models make the transposition a type error instead.
#
#  The field is a ``str`` rather than :class:`uuid.UUID`. Xochitl writes uuids into filenames
#  and json, and the legacy tree parsed them into ``UUID`` and formatted them back on every
#  boundary crossing -- a round trip that normalises case and grouping, so an identity read
#  from the device no longer compares equal to the same identity read from a cache row. The
#  domain therefore carries the identifier exactly as the store spelled it and never
#  reformats it.
#
#  Opaque, but not unconstrained
#  -----------------------------
#  ``UUID`` parsing was also the legacy tree's de-facto sanitizer. A persistence adapter joins
#  these identifiers into ``{doc}/{page}.rm``, so accepting ``".."`` or ``"a/b"`` as an identity
#  would put path traversal in a value object. Both fields therefore carry a charset pattern, a
#  length bound and a validator refusing dot segments -- enough that an identifier cannot name
#  anything but a leaf, without pinning the domain to one identifier *format* the way ``UUID``
#  did. ``DocumentMetadata.parent_uuid`` is deliberately *not* constrained this way: the store
#  writes ``"trash"`` there, and a parent is compared and displayed, never joined into a path.


class DocumentId(BaseModel, frozen=True, extra="forbid"):
    """Identity of one document in a store.

    Frozen, so it is hashable and usable as a mapping key: an in-memory
    ``DocumentRepository`` double is a dict keyed by this type.
    """

    uuid: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH, pattern=_IDENTIFIER_PATTERN)
    """The document's identifier, verbatim as the store spelled it -- no separators, no dot
    segments, so a store may join it into a path without sanitising it first."""

    def __str__(self) -> str:
        return self.uuid

    @model_validator(mode="after")
    def _check_not_a_dot_segment(self) -> Self:
        """Reject an identifier made only of dots.

        Returns
        -------
        DocumentId
            The validated model.

        Raises
        ------
        ValueError
            If the identifier is ``"."``, ``".."``, or any longer run of dots.
        """
        _reject_dot_segment(self.uuid)
        return self


class PageId(BaseModel, frozen=True, extra="forbid"):
    """Identity of one page within a document.

    Distinct from :class:`DocumentId` on purpose. Nothing about the two is structurally
    different; the difference is which argument position each belongs in.
    """

    uuid: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH, pattern=_IDENTIFIER_PATTERN)
    """The page's identifier, verbatim as the store spelled it -- constrained exactly as
    :attr:`DocumentId.uuid` is, and for the same reason."""

    def __str__(self) -> str:
        return self.uuid

    @model_validator(mode="after")
    def _check_not_a_dot_segment(self) -> Self:
        """Reject an identifier made only of dots.

        Returns
        -------
        PageId
            The validated model.

        Raises
        ------
        ValueError
            If the identifier is ``"."``, ``".."``, or any longer run of dots.
        """
        _reject_dot_segment(self.uuid)
        return self


# ──────────────────────── Colour and palettes ────────────────────────
#
#  Pen colours and the palettes that resolve them to ink.
#
#  The tablet stores a colour as a small integer per stroke. Turning that integer into an RGB
#  triplet is a policy decision -- the export palette is what a screen should show, the
#  physical palette approximates what colour e-ink actually shows -- so it belongs to a value
#  the caller supplies, not to a module-level default inside a renderer.
#
#  Totality is the point
#  ---------------------
#  :class:`Palette` validates that it carries an ink for every member of :class:`PenColor`,
#  which makes :meth:`Palette.rgb` total and therefore deletes the three silent
#  ``return (0, 0, 0)`` fallbacks the legacy palette had. A colour the palette does not cover
#  is now unconstructible rather than rendered black, so ``rmspec render`` cannot quietly turn
#  a magenta stroke into a black one.
#
#  That constraint is also why there is no partial palette in this module. The measured
#  Paper Pro inks cover nine of the fourteen colours; :data:`PAPER_PRO_PHYSICAL_PALETTE` is
#  therefore the export palette with those nine overridden, and the five unmeasured colours
#  keep their export inks. Stating the fallback here, once, is better than a ``dict.get`` at
#  every read site that cannot say what it fell back to.


class PenColor(IntEnum):
    """The colour index stored per stroke in a scene file.

    Values 0-2 exist on every reMarkable. Values 3-7 arrived with the Paper Pro's colour
    display and 8-13 extend that palette. The index is written as a uint32 on the wire, so
    this enum is the domain's account of a wire encoding and its members' values may not be
    renumbered.
    """

    BLACK = 0
    GRAY = 1
    WHITE = 2
    YELLOW = 3
    GREEN = 4
    PINK = 5
    BLUE = 6
    RED = 7
    GRAY_OVERLAP = 8
    HIGHLIGHT = 9
    GREEN_2 = 10
    CYAN = 11
    MAGENTA = 12
    YELLOW_2 = 13


class Rgb(BaseModel, frozen=True, extra="forbid"):
    """One 8-bit-per-channel colour.

    Constrained rather than merely annotated: a channel outside 0-255 is refused at
    construction, so no renderer has to clamp and no export has to explain a wrapped
    channel.

    Obligation this puts on adapters
    --------------------------------
    The bounds are new -- the legacy ``RGB`` accepted ``r=300`` and rendered it as a malformed
    eight-character hex string. Every field, default and unit is otherwise unchanged, so the
    tightening is the whole delta and it is kept. An adapter that builds a colour from parsed
    bytes or from user input therefore owes a clamp, or a mapping to a typed domain error:
    letting pydantic's ``ValidationError`` escape mid-render turns one bad palette entry into a
    stack trace rather than a message.
    """

    r: int = Field(ge=0, le=255)
    """Red channel."""

    g: int = Field(ge=0, le=255)
    """Green channel."""

    b: int = Field(ge=0, le=255)
    """Blue channel."""

    def as_tuple(self) -> tuple[int, int, int]:
        """Return the channels as an ``(r, g, b)`` tuple.

        Returns
        -------
        tuple[int, int, int]
            The three channels in order.
        """
        return (self.r, self.g, self.b)

    def as_hex(self) -> str:
        """Return the colour as a lowercase ``#rrggbb`` string.

        Returns
        -------
        str
            Seven characters, always lowercase, so two equal colours have one spelling.
        """
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def as_css(self) -> str:
        """Return the colour as a CSS ``rgb()`` function.

        Returns
        -------
        str
            For example ``rgb(78, 105, 201)``.
        """
        return f"rgb({self.r}, {self.g}, {self.b})"


class Palette(BaseModel, frozen=True, extra="forbid"):
    """A named, total mapping from every :class:`PenColor` to one :class:`Rgb`.

    Both fields are digested by ``RenderStyle.digest``: the name identifies the choice a
    human made and the inks identify what that choice currently means, so editing a
    palette's contents without renaming it still misses a cache row that was produced with
    the old inks.

    Frozen but not hashable -- it carries a mapping. Compare palettes by value, and use
    ``RenderStyle.digest`` when a cache key is what is wanted.
    """

    name: str = Field(min_length=1)
    """Which palette this is, e.g. ``"export"``. Part of the render cache identity."""

    inks: dict[PenColor, Rgb]
    """One ink per pen colour. Validated total, which is what makes :meth:`rgb` total."""

    @model_validator(mode="after")
    def _check_total(self) -> Self:
        """Reject a palette that does not cover every pen colour.

        Returns
        -------
        Palette
            The validated model.

        Raises
        ------
        ValueError
            If any :class:`PenColor` member has no ink.
        """
        missing = sorted(colour.name for colour in PenColor if colour not in self.inks)
        if missing:
            msg = f"palette {self.name!r} has no ink for: {', '.join(missing)}"
            raise ValueError(msg)
        return self

    def rgb(self, colour: PenColor, /) -> Rgb:
        """Return the ink this palette resolves ``colour`` to.

        Total: every pen colour has an ink, checked at construction, so there is no
        fallback and no unknown-colour error anywhere downstream.

        Parameters
        ----------
        colour
            The stroke colour index to resolve.

        Returns
        -------
        Rgb
            The ink to draw with.
        """
        return self.inks[colour]


#: Saturated inks for viewing an export on a backlit screen.
#:
#: Thirteen of the fourteen are the values community exporters agree on (rmc's
#: ``writing_tools``). :attr:`PenColor.HIGHLIGHT` is the fourteenth: the tablet resolves a
#: highlighter's actual colour from scene extra data, and yellow is the tool's default, so
#: it shares :attr:`PenColor.YELLOW`'s ink here. A highlight in another colour reaches the
#: renderer as that colour's own index, not as ``HIGHLIGHT``.
EXPORT_PALETTE = Palette(
    name="export",
    inks={
        PenColor.BLACK: Rgb(r=0, g=0, b=0),
        PenColor.GRAY: Rgb(r=144, g=144, b=144),
        PenColor.WHITE: Rgb(r=255, g=255, b=255),
        PenColor.YELLOW: Rgb(r=251, g=247, b=25),
        PenColor.GREEN: Rgb(r=0, g=255, b=0),
        PenColor.PINK: Rgb(r=255, g=192, b=203),
        PenColor.BLUE: Rgb(r=78, g=105, b=201),
        PenColor.RED: Rgb(r=179, g=62, b=57),
        PenColor.GRAY_OVERLAP: Rgb(r=125, g=125, b=125),
        PenColor.HIGHLIGHT: Rgb(r=251, g=247, b=25),
        PenColor.GREEN_2: Rgb(r=161, g=216, b=125),
        PenColor.CYAN: Rgb(r=139, g=208, b=229),
        PenColor.MAGENTA: Rgb(r=183, g=130, b=205),
        PenColor.YELLOW_2: Rgb(r=247, g=232, b=81),
    },
)

#: Inks measured off a Paper Pro display, for reproducing its muted appearance.
#:
#: Source: the rmpro-v0 ICC profile derived by DSLR calibration (thregr.org/wavexx). Only
#: nine colours were measured, so the remaining five -- ``PINK``, ``GRAY_OVERLAP``,
#: ``HIGHLIGHT``, ``GREEN_2`` and ``YELLOW_2`` -- keep their :data:`EXPORT_PALETTE` inks.
#: Overriding rather than starting from an incomplete mapping is what keeps this palette
#: total without any invented measurement.
PAPER_PRO_PHYSICAL_PALETTE = Palette(
    name="paper-pro-physical",
    inks={
        **EXPORT_PALETTE.inks,
        PenColor.BLACK: Rgb(r=0x3A, g=0x48, b=0x61),
        PenColor.GRAY: Rgb(r=0x7F, g=0x7E, b=0x82),
        PenColor.WHITE: Rgb(r=0xA8, g=0xAA, b=0xA7),
        PenColor.YELLOW: Rgb(r=0xA0, g=0x9E, b=0x66),
        PenColor.GREEN: Rgb(r=0x6E, g=0x78, b=0x60),
        PenColor.BLUE: Rgb(r=0x3C, g=0x54, b=0x83),
        PenColor.RED: Rgb(r=0x86, g=0x63, b=0x69),
        PenColor.CYAN: Rgb(r=0x5F, g=0x6D, b=0x80),
        PenColor.MAGENTA: Rgb(r=0x7F, g=0x62, b=0x7B),
    },
)


# ──────────────────────── Pen tools ────────────────────────
#
#  The pen tool a stroke was drawn with.
#
#  An enum and nothing else. The rendering formulas that turn a pen plus stylus samples into a
#  width and an opacity live in ``rmspec-render``, behind ``PageRenderer``: they are float
#  arithmetic with one caller, their only test double is one that makes every width assertion
#  vacuous, and ``ports/render.py`` records the decision not to give them a port.
#
#  What stays here is the wire enum, because a stroke cannot be modelled without it, plus the
#  two classifications and the alias fold that every consumer would otherwise re-derive.


class PenType(IntEnum):
    """The pen tool id stored per stroke in a scene file.

    The ``_1`` and ``_2`` suffixed members are the same tool on the tablet's two toolbar
    rows and render identically; :attr:`canonical` folds the second onto the first so
    rendering rules handle one member per tool. Values are the wire encoding and may not be
    renumbered.
    """

    PAINTBRUSH_1 = 0
    PENCIL_1 = 1
    BALLPOINT_1 = 2
    MARKER_1 = 3
    FINELINER_1 = 4
    HIGHLIGHTER_1 = 5
    ERASER = 6
    MECHANICAL_PENCIL_1 = 7
    ERASER_AREA = 8
    PAINTBRUSH_2 = 12
    MECHANICAL_PENCIL_2 = 13
    PENCIL_2 = 14
    BALLPOINT_2 = 15
    MARKER_2 = 16
    FINELINER_2 = 17
    HIGHLIGHTER_2 = 18
    CALLIGRAPHY = 21
    SHADER = 23

    @property
    def canonical(self) -> PenType:
        """Return the toolbar-row-1 member for this tool.

        Returns
        -------
        PenType
            ``self`` for every member that has no ``_2`` twin.
        """
        return _CANONICAL.get(self, self)

    @property
    def is_eraser(self) -> bool:
        """Whether this tool removes ink rather than laying it down.

        Returns
        -------
        bool
            ``True`` for the point and area erasers.
        """
        return self in {PenType.ERASER, PenType.ERASER_AREA}

    @property
    def is_highlighter(self) -> bool:
        """Whether this tool is a highlighter variant.

        Returns
        -------
        bool
            ``True`` for either toolbar row's highlighter.
        """
        return self.canonical is PenType.HIGHLIGHTER_1


#: Toolbar-row-2 members and the row-1 member each is the same tool as.
_CANONICAL: dict[PenType, PenType] = {
    PenType.PAINTBRUSH_2: PenType.PAINTBRUSH_1,
    PenType.MECHANICAL_PENCIL_2: PenType.MECHANICAL_PENCIL_1,
    PenType.PENCIL_2: PenType.PENCIL_1,
    PenType.BALLPOINT_2: PenType.BALLPOINT_1,
    PenType.MARKER_2: PenType.MARKER_1,
    PenType.FINELINER_2: PenType.FINELINER_1,
    PenType.HIGHLIGHTER_2: PenType.HIGHLIGHTER_1,
}

#: Every wire tool id and the member it names, for the total lookup below.
_WIRE_PENS: dict[int, PenType] = {member.value: member for member in PenType}


def pen_from_wire(value: int, /) -> PenType | None:
    """Return the pen a scene file's tool id names, or ``None`` when no member has that id.

    The int-tolerant entry point the legacy ``PenType.is_eraser(6)`` and
    ``PenType.is_highlighter(99)`` classmethods provided, as one total function rather than
    three int-taking classmethods on the enum. Those are gone on purpose -- classification
    belongs to a member, and ``PenType(99).is_highlighter`` cannot be reached -- but a codec
    still meets raw bytes, and it must not have to catch ``ValueError`` from ``PenType(value)``
    to find out whether it may construct a :class:`Stroke`. On ``None`` a codec substitutes a
    known tool and records :attr:`PageDefectCode.UNKNOWN_PEN_SUBSTITUTED`, so an unknown wire id
    is a defect on one page rather than a failed decode of the document.

    Parameters
    ----------
    value
        The tool id exactly as the scene file wrote it.

    Returns
    -------
    PenType | None
        The member with that id, or ``None`` when this domain does not know it.
    """
    return _WIRE_PENS.get(value)


# ──────────────────────── Screen geometry ────────────────────────
#
#  Device screen geometry, and the two screens this project has seen.
#
#  Scene coordinates are screen units, so nothing can be exported at a correct physical size
#  without knowing which screen produced it. ``PageRenderer.render`` takes a
#  :class:`ScreenSpec` as a required keyword argument for exactly that reason: the legacy
#  renderer imported ``RM2_SCREEN`` as an in-module default on a Paper-Pro-only product, so
#  every caller that omitted it rendered 1404x1872 geometry with correctly placed ink, which
#  looks right until it is measured.
#
#  Millimetres, not points
#  -----------------------
#  The derived sizes here are millimetres. PostScript points are a PDF unit, and a domain that
#  speaks them has adopted one adapter's coordinate system; ``ports/export.py`` makes the same
#  choice for ``PhysicalSize``. Adapters convert on their own boundary
#  (``pt = mm * 72 / 25.4``).
#
#  No auto-detection
#  -----------------
#  There is no ``detect_screen``. The legacy version inferred Paper Pro from any stroke point
#  outside reMarkable 2 bounds, which silently made every page whose ink stayed inside those
#  bounds a reMarkable 2 page -- including Paper Pro notes with a narrow margin. Which device a
#  document came from is a fact the device slice reports and the composition root binds, not
#  one the renderer guesses from ink.


class ScreenSpec(BaseModel, frozen=True, extra="forbid"):
    """The pixel geometry and pixel density of one device screen, in portrait.

    Every field is digested by ``RenderStyle.digest``, including :attr:`name`: renaming a
    screen changes the render identity, which is correct, because the name is what a cached
    row records about the geometry it was produced with.
    """

    name: str = Field(min_length=1)
    """Human-readable device name, e.g. ``"Paper Pro"``."""

    width: int = Field(gt=0)
    """Screen width in pixels, portrait."""

    height: int = Field(gt=0)
    """Screen height in pixels, portrait."""

    dpi: int = Field(gt=0)
    """Pixels per inch, which is what converts screen units to physical ones."""

    @property
    def width_mm(self) -> float:
        """Physical width of the screen.

        Returns
        -------
        float
            Millimetres.
        """
        return self.width / self.dpi * _MM_PER_INCH

    @property
    def height_mm(self) -> float:
        """Physical height of the screen.

        Returns
        -------
        float
            Millimetres.
        """
        return self.height / self.dpi * _MM_PER_INCH

    @property
    def x_shift(self) -> float:
        """The offset that moves centre-origin scene coordinates into the page box.

        Scene ``x`` is measured from the centre of the page, so it spans
        ``[-width / 2, +width / 2]`` while ``y`` starts at the top edge. Half the width is
        therefore what every renderer must add to ``x``, and it lives here rather than in an
        adapter because two adapters computing it independently is two chances to compute it
        differently.

        Returns
        -------
        float
            Half the screen width, in screen units.
        """
        return self.width / 2


#: reMarkable 1 and 2: 1404x1872 at 226 DPI.
RM2_SCREEN = ScreenSpec(name="reMarkable 2", width=1404, height=1872, dpi=226)

#: reMarkable Paper Pro, portrait: 1620x2160 at 229 DPI.
PAPER_PRO_SCREEN = ScreenSpec(name="Paper Pro", width=1620, height=2160, dpi=229)


# ──────────────────────── Strokes and stylus samples ────────────────────────
#
#  One pen-down-to-pen-up movement, and the stylus samples that make it up.
#
#  These two models are the closest the domain comes to the wire format, and deliberately so:
#  ``ports/formats.py`` records that its ports isolate *the parser*, not *the format*, so a
#  second codec omits fields rather than fabricating them. Every per-sample field below is one
#  the v6 scene format carries, at the range that format encodes, which is why the constraints
#  are ``le=255`` and ``le=65535`` rather than free integers -- a value outside them did not
#  come off a stylus.


class Point(BaseModel, frozen=True, extra="forbid"):
    """One stylus sample: where the pen was and how it was being held.

    Fourteen bytes on the wire. The raw sensor scales are kept as-is and normalised by the
    properties below, so a rendering formula reads ``pressure_normalized`` and no caller has
    to remember which channel is a uint8 and which is a uint16.
    """

    x: float
    """Horizontal position in screen units, measured from the centre of the page."""

    y: float
    """Vertical position in screen units, measured from the top edge."""

    speed: int = Field(default=0, ge=0, le=_UINT16_MAX)
    """Raw stylus speed. Higher is faster."""

    direction: int = Field(default=0, ge=0, le=_UINT8_MAX)
    """Raw stylus angle, a full turn across the uint8 range."""

    width: int = Field(default=0, ge=0, le=_UINT16_MAX)
    """Raw input width, combined with pressure by the per-pen formulas."""

    pressure: int = Field(default=0, ge=0, le=_UINT8_MAX)
    """Raw nib pressure, ``0`` for none."""

    @property
    def pressure_normalized(self) -> float:
        """Pressure on a 0-1 scale.

        Returns
        -------
        float
            ``pressure`` divided by its full-scale value.
        """
        return self.pressure / _UINT8_MAX

    @property
    def direction_radians(self) -> float:
        """Stylus angle in radians.

        Returns
        -------
        float
            ``direction`` mapped from the uint8 range onto ``[0, 2*pi)``.
        """
        return self.direction * _FULL_TURN / _UINT8_MAX


class Stroke(BaseModel, frozen=True, extra="forbid"):
    """One continuous mark: the tool, its colour and thickness, and its samples.

    A stroke with no points is legal and means a single tap. It is not the same thing as an
    absent stroke, which is why :attr:`pen` and :attr:`color` are required even here.

    Renamed and narrowed from the legacy model, deliberately
    -------------------------------------------------------
    The field is :attr:`pen`, not ``pen_type``: its type is :class:`PenType`, so the suffix
    restated the annotation. The legacy ``is_eraser`` and ``is_highlighter`` methods are gone
    from here and live on :class:`PenType` instead -- one classification per tool rather than
    one per stroke -- and :attr:`Stroke.pen.is_eraser <PenType.is_eraser>` reproduces the old
    truth table for every one of the eighteen members. The model is also frozen where the legacy
    one was mutable, and ``points`` is a tuple: nothing mutated a stroke in place, and a rendered
    stroke that can be edited afterwards is a cache key that stops meaning anything.
    """

    pen: PenType
    """The tool used, which selects the rendering formula."""

    color: PenColor
    """The colour index, resolved to ink by a ``Palette`` at render time."""

    thickness_scale: float = Field(ge=0)
    """The tablet's thickness-slider value, before any per-pen formula is applied."""

    points: tuple[Point, ...] = ()
    """Samples in the order they were taken. Empty means a tap."""

    starting_length: float = Field(default=0.0, ge=0)
    """Cumulative length offset, for a stroke rendered across more than one pass."""

    @property
    def bounding_box(self) -> tuple[float, float, float, float] | None:
        """Return the extent of this stroke's samples.

        ``None`` rather than a zero box for a stroke with no points: a zero box at the
        origin is a real extent for a tap at the origin, and folding the two together is
        what let the legacy viewport arithmetic silently include the origin in every page's
        extent.

        Returns
        -------
        tuple[float, float, float, float] | None
            ``(x_min, y_min, x_max, y_max)`` in screen units, or ``None`` when the stroke
            has no samples.
        """
        if not self.points:
            return None
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


# ──────────────────────── Pages ────────────────────────
#
#  One page: its layers, its decoded content, and the defects accepted while reading it.
#
#  Degradation is data here, not a log line. ``ports/formats.py`` states the rule the models
#  below implement: "file absent", "page blank" and "page unparsable" are three distinct
#  states rather than one silently empty layer list, and a page that is present but degraded is
#  a *value* carrying its defects. That is what makes ``--strict`` one check at the CLI boundary
#  over :attr:`Page.defects` instead of a policy argument threaded through a port.
#
#  The split between :class:`PageContent` and :class:`Page`
#  -------------------------------------------------------
#  ``PageCodec.decode_page`` returns content, because it is handed bytes and knows nothing about
#  identity, ordering or templates. ``DocumentRepository.load_page`` returns a page, which adds
#  the identity the store addressed, the position the document listed it at, and the template
#  name the store's sidecar recorded. Keeping them apart is what lets ``rmspec inspect rm
#  <path>`` decode a file that has no document identity at all.


class PageDefectCode(StrEnum):
    """The closed set of things that can be wrong with one page and not raise.

    Closed, and small, for the same reason ``DegradationKind`` is: adding a way for a page to
    come back damaged should be a reviewed change to the domain, not something a codec can
    introduce by widening a string. Anything a reader cannot survive at all raises
    ``CorruptPageData`` or ``UnsupportedPageFormat`` instead of appearing here.
    """

    ARTIFACT_ABSENT = "artifact_absent"
    """The document lists this page but the store holds no scene file for it."""

    CONTENT_UNDECODABLE = "content_undecodable"
    """The scene file exists and could not be decoded, so the page has no content."""

    ITEM_DROPPED = "item_dropped"
    """A scene item was skipped, so some ink or text is missing from an otherwise good page."""

    LAYER_SYNTHESISED = "layer_synthesised"
    """Items arrived with no owning layer, so one was invented to hold them."""

    UNKNOWN_PEN_SUBSTITUTED = "unknown_pen_substituted"
    """A stroke named a tool this codec does not know, and a known tool was used instead."""

    UNKNOWN_COLOR_SUBSTITUTED = "unknown_color_substituted"
    """A stroke named a colour index this codec does not know, and a known one was used."""


class PageDefect(BaseModel, frozen=True, extra="forbid"):
    """One thing that was wrong with a page, as a branchable code plus human detail."""

    code: PageDefectCode
    """What was wrong. This is the part a use case may branch on."""

    detail: str = Field(min_length=1)
    """Free text for a person reading a warning or a log. Never parsed."""


class TextBlock(BaseModel, frozen=True, extra="forbid"):
    """A block of typed text placed on a page.

    The tablet stores text as a CRDT sequence; this is its flattened reading, with paragraph
    breaks as newlines. Nothing above the formats adapter has a use for the CRDT structure,
    and carrying it would put a sync algorithm in the domain.

    Obligation this puts on a codec
    -------------------------------
    :attr:`width` is constrained ``> 0`` where the legacy model accepted zero and negative
    widths and drew nothing from them. The tightening is kept, so a codec that meets a scene
    text item whose width is not positive must skip the item and record
    :attr:`PageDefectCode.ITEM_DROPPED` on the page it is assembling. It must never let the
    resulting ``ValidationError`` escape mid-decode: one unreadable text box is a defect on one
    page, not a failed document.
    """

    pos_x: float
    """Horizontal position of the block's top-left corner, in screen units."""

    pos_y: float
    """Vertical position of the block's top-left corner, in screen units."""

    width: float = Field(gt=0)
    """Width of the text box in screen units. Text wraps inside it."""

    text: str = ""
    """The flattened text. Empty is legal: an empty box is a real thing to draw."""


class Layer(BaseModel, frozen=True, extra="forbid"):
    """One drawing layer: ordered strokes, then text drawn over them.

    Layers render bottom to top in list order, and strokes render in list order within a
    layer, so both collections are ordered tuples rather than sets.
    """

    name: str = ""
    """The layer's name in the tablet UI. Empty when it was never named."""

    visible: bool = True
    """Whether the layer is shown. A hidden layer is skipped when rendering."""

    strokes: tuple[Stroke, ...] = ()
    """Strokes in draw order."""

    text_blocks: tuple[TextBlock, ...] = ()
    """Typed text, drawn above this layer's strokes."""

    @property
    def is_empty(self) -> bool:
        """Whether this layer holds nothing at all.

        Returns
        -------
        bool
            ``True`` when the layer has neither strokes nor text.
        """
        return not self.strokes and not self.text_blocks


class PageContent(BaseModel, frozen=True, extra="forbid"):
    """What a codec read out of one page's scene bytes.

    Carries its own defects, so a codec that had to substitute a pen or drop an item reports
    that as part of its result. An empty ``layers`` with an empty ``defects`` therefore means
    one specific thing -- the page really is blank -- which is the distinction the legacy
    "return an empty layer list on any problem" behaviour destroyed.
    """

    layers: tuple[Layer, ...] = ()
    """Layers in render order, bottom first."""

    defects: tuple[PageDefect, ...] = ()
    """Substitutions and omissions the decode survived, in the order they occurred."""

    @property
    def visible_layers(self) -> tuple[Layer, ...]:
        """Layers that should be drawn.

        Returns
        -------
        tuple[Layer, ...]
            The visible layers, in render order.
        """
        return tuple(layer for layer in self.layers if layer.visible)

    @property
    def stroke_count(self) -> int:
        """How many strokes this page would draw.

        Counts visible layers only, because that is the number a caller checks a page
        against when deciding whether it is worth rendering or sending to OCR.

        Returns
        -------
        int
            Zero for a blank page.
        """
        return sum(len(layer.strokes) for layer in self.visible_layers)

    @property
    def is_blank(self) -> bool:
        """Whether there is nothing to draw.

        Returns
        -------
        bool
            ``True`` when every visible layer is empty.
        """
        return all(layer.is_empty for layer in self.visible_layers)

    @property
    def bounding_box(self) -> tuple[float, float, float, float] | None:
        """Return the extent of every stroke this page would draw.

        The replacement for the legacy ``Layer.bounding_box``, aggregated one level up so the
        render adapter's viewport crop and the export adapter's page fit read the same number
        instead of each folding per-stroke boxes their own way. Two legacy defects are fixed
        rather than carried: that method ignored layer visibility, and it folded in the
        ``(0, 0, 0, 0)`` a point-less stroke used to return, which pinned every page's extent to
        the origin. This reads :attr:`visible_layers` and skips strokes with no samples, which
        :attr:`Stroke.bounding_box` now reports as ``None``.

        Returns
        -------
        tuple[float, float, float, float] | None
            ``(x_min, y_min, x_max, y_max)`` in screen units, or ``None`` when no visible stroke
            has a sample. Text blocks are not folded in: how tall one draws depends on the font
            the export adapter picks, so including them would make this a guess.
        """
        boxes = [
            box
            for layer in self.visible_layers
            for stroke in layer.strokes
            if (box := stroke.bounding_box) is not None
        ]
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )


class Page(BaseModel, frozen=True, extra="forbid"):
    """One page of a document, as the store was able to produce it.

    ``content`` is ``None`` when the page could not be read at all, and a validator requires a
    defect explaining why -- so "unreadable" is never mistaken for "blank", and a page cannot
    be constructed that fails silently. ``DocumentRepository.load_page`` raises
    ``PageNotFound`` instead of returning such a page; it is ``load`` on a whole document that
    needs to hand back the pages it could not read alongside the ones it could.
    """

    page_id: PageId
    """Identity of this page, as the document listed it."""

    index: int = Field(ge=0)
    """Zero-based position in the document, which is how a caller names "page 3"."""

    template_name: str | None = None
    """The background template the store recorded, or ``None`` when it recorded none.

    ``None`` is the *only* spelling of "no template". The legacy tree had two more -- ``Page``
    defaulted to ``""`` while ``PageRef`` defaulted to ``"Blank"`` -- so a caller had three
    values to test, and two of them were strings no store ever wrote. Here ``"Blank"`` means the
    store really did record a template named ``Blank``."""

    pdf_page_index: int | None = Field(default=None, ge=0)
    """Zero-based page of the source pdf this page annotates, or ``None`` when the store's
    redirection map named none.

    The legacy ``PageRef.redirect``, restored because without it the degradation is the only
    reachable path: ``errors.py`` declares ``PDF_PAGE_INDEX_FALLBACK`` for "no entry in the
    redirection map, so the page's position was used", and a background reader with nothing but
    :attr:`index` can never *not* report it. Typed ``int``, not the legacy ``str | None``:
    firmware 3.x writes the ``cPages`` entry's ``redir`` as an object whose ``value`` is an
    integer page index, so the legacy annotation could only ever read ``None`` or raise."""

    content: PageContent | None = None
    """The decoded page, or ``None`` when it could not be decoded or was not there."""

    defects: tuple[PageDefect, ...] = ()
    """Page-level problems -- absent artifact, failed decode. Decode-level substitutions
    live on :attr:`PageContent.defects`; :attr:`all_defects` is the union."""

    @model_validator(mode="after")
    def _check_absent_content_is_explained(self) -> Self:
        """Reject a contentless page that does not say why it has no content.

        Returns
        -------
        Page
            The validated model.

        Raises
        ------
        ValueError
            If ``content`` is ``None`` and ``defects`` is empty.
        """
        if self.content is None and not self.defects:
            msg = (
                f"page {self.page_id.uuid} has no content and no defect explaining it; "
                f"use {PageDefectCode.ARTIFACT_ABSENT.value!r} or "
                f"{PageDefectCode.CONTENT_UNDECODABLE.value!r}"
            )
            raise ValueError(msg)
        return self

    @property
    def ref(self) -> str:
        """A stable display identity for this page.

        The value adapters echo back as ``RenderedPage.page_ref`` and ``RasterImage.page_ref``,
        so a raster or a reading cannot be attributed to the wrong page. Opaque to those
        ports: they compare it and never parse it.

        Returns
        -------
        str
            The page's uuid.
        """
        return self.page_id.uuid

    @property
    def is_readable(self) -> bool:
        """Whether this page has content to work with.

        Returns
        -------
        bool
            ``False`` when the scene file was absent or would not decode.
        """
        return self.content is not None

    @property
    def stroke_count(self) -> int:
        """How many strokes this page would draw.

        Returns
        -------
        int
            Zero for a blank page and for an unreadable one; use :attr:`is_readable` to tell
            those apart.
        """
        return 0 if self.content is None else self.content.stroke_count

    @property
    def all_defects(self) -> tuple[PageDefect, ...]:
        """Every defect recorded about this page, page-level first.

        Returns
        -------
        tuple[PageDefect, ...]
            :attr:`defects` followed by the content's own, or just :attr:`defects` when there
            is no content.
        """
        if self.content is None:
            return self.defects
        return (*self.defects, *self.content.defects)


# ──────────────────────── Documents ────────────────────────
#
#  A document: what the tablet shows about it, and the pages it holds.
#
#  Two shapes, because ``DocumentRepository`` has two altitudes.
#  :class:`DocumentSummary` is what ``list_documents`` returns -- everything a listing or a tree
#  render needs, and nothing that costs a scene decode. :class:`Document` is what ``load``
#  returns, and it is the same thing with the pages decoded.
#
#  The summary carries page *identities* in document order rather than a page count, which is
#  what lets a caller address "page 3" for ``load_page`` without decoding pages 1 and 2. A count
#  would have forced either a second call or an index-to-uuid guess in the app layer.
#
#  Field naming is domain-facing, not wire-facing. The store writes ``visibleName``,
#  ``lastModified`` as a millisecond epoch in a json string, and ``type`` as ``"DocumentType"``;
#  the models below are spelled for their readers instead, and :meth:`DocumentMetadata.decode`
#  is the single place the two vocabularies meet. That classmethod is what ``ports/formats.py``
#  chose in place of a sidecar codec port -- "a decode classmethod over bytes" -- and it is a
#  domain concern rather than an adapter one for the reason the module docstring gives: a mapping
#  that needs only ``json`` and pydantic, written once, cannot drift the way the legacy tree's
#  four hand-mirrored copies of it did.


class DocumentKind(StrEnum):
    """Whether an entry is a document or a folder.

    The store is a flat directory; the tree a user sees is built from each entry's parent, so
    folders are entries too and this is what tells them apart.
    """

    DOCUMENT = "document"
    COLLECTION = "collection"


class SourceKind(StrEnum):
    """What a document was made from.

    A notebook was drawn on the tablet and has no source file. A pdf or epub was uploaded, so
    it has one, and its pages render over a background the export slice's PDF reader produces.
    """

    NOTEBOOK = "notebook"
    PDF = "pdf"
    EPUB = "epub"


class PageOrientation(StrEnum):
    """Which way up the store says a document's pages are.

    A closed enum rather than the legacy free ``str``, because a renderer must branch on it.
    Landscape rendering is unimplemented in this workspace; carrying the fact as a string is how
    it would have stayed unimplementable without re-plumbing a model field, since a value nothing
    can exhaustively match is a value nobody writes a branch for.
    """

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class ExtraMetadata(BaseModel, frozen=True, extra="forbid"):
    """The tool state the store recorded for a document, restored when it is reopened.

    Read from the ``.content`` sidecar's ``extraMetadata`` object: which tool was last used,
    which pen variant, and the full bag of per-tool sizes and colours. The keys and values are
    reMarkable-internal identifiers such as ``"Fineliner"`` and ``"FinelinerV2Size"``, and this
    workspace never branches on any of them -- ``rmspec inspect content`` prints them, which is
    the whole reason the model survives the move to the domain.

    ``dict``, not ``Mapping``
    -------------------------
    :attr:`tool_settings` would read better as a ``Mapping``, but a ``collections.abc`` import
    used only in annotations is one ruff moves into a ``TYPE_CHECKING`` block, where pydantic can
    no longer resolve it -- the same mechanical constraint the module docstring records for
    cross-module model imports. ``dict`` on a frozen model is what :attr:`Palette.inks` does for
    the same reason: the value is never mutated, and nothing hands one of these out to be edited.
    """

    last_tool: str = ""
    """Internal name of the last tool used, e.g. ``"Fineliner"``. Empty when unrecorded."""

    last_pen: str = ""
    """Internal name of the last pen variant used. Empty when unrecorded."""

    tool_settings: dict[str, str] = Field(default_factory=dict)
    """Every setting the sidecar carried, verbatim. Opaque: displayed, never parsed."""

    @classmethod
    def from_json(cls, data: dict[str, str], /) -> Self:
        """Read one ``extraMetadata`` object.

        Parameters
        ----------
        data
            The ``extraMetadata`` members of a ``.content`` sidecar, with every value already
            rendered as text -- see the sidecar readers at the top of this module.

        Returns
        -------
        ExtraMetadata
            The tool state, with every unrecorded field at its default and the whole bag kept.
        """
        return cls(
            last_tool=_string(data.get("LastTool")),
            last_pen=_string(data.get("LastPen")),
            tool_settings=dict(data),
        )


class DocumentLayout(BaseModel, frozen=True, extra="forbid"):
    """How the store says a document should be laid out, plus the tool state it recorded.

    The half of the legacy ``ContentInfo`` that was not structure. Its structural half folded
    correctly into the models around it -- ``file_type`` became
    :attr:`DocumentMetadata.source`, ``page_refs`` became :attr:`Document.pages` plus
    :attr:`Page.template_name`, ``page_count`` became :attr:`DocumentSummary.page_count` -- and
    these fields are what was left with nowhere to go, every one of them printed by
    ``rmspec inspect content``.

    Closed where the domain branches, opaque where it echoes
    -------------------------------------------------------
    :attr:`orientation` is a closed enum because rendering must branch on it. The rest stay
    strings and numbers: nothing in this workspace branches on a zoom mode or a font name, so
    closing them would turn a firmware that adds one into a decode failure instead of a slightly
    stale line of output. For the same reason :attr:`text_scale` and :attr:`custom_zoom_scale`
    are unconstrained where most fields in this module are bounded -- refusing to open a document
    because a cosmetic scale is odd trades a real capability for a validation that buys nothing.
    """

    format_version: int = Field(default=2, ge=0)
    """The sidecar's own format version. ``2`` is current for firmware 3.x."""

    orientation: PageOrientation = PageOrientation.PORTRAIT
    """Which way up the pages are."""

    margins: int = Field(default=125, ge=0)
    """Margin in screen units used when reflowing pdf or epub text."""

    font_name: str = ""
    """Font the tablet renders epub text with. Empty means the tablet's default."""

    line_height: int = -1
    """Line height for reflowed text. ``-1`` is the store's spelling of "automatic", which is
    why this field is not bounded below."""

    text_scale: float = 1.0
    """Scale factor for reflowed epub text."""

    text_alignment: str = "justify"
    """Alignment for reflowed epub text, e.g. ``"justify"``. Opaque: displayed, never matched."""

    zoom_mode: str = "bestFit"
    """Pdf zoom mode, e.g. ``"bestFit"``. Opaque: displayed, never matched."""

    custom_zoom_scale: float = 1.0
    """Scale used when :attr:`zoom_mode` is the store's custom setting."""

    extra_metadata: ExtraMetadata = Field(default_factory=ExtraMetadata)
    """The tool state the store recorded for this document."""

    @classmethod
    def decode(cls, raw: bytes, /) -> Self:
        """Read the layout facts out of one ``.content`` sidecar.

        Parameters
        ----------
        raw
            The sidecar's bytes, exactly as the store holds them.

        Returns
        -------
        DocumentLayout
            The layout, with every unrecorded field at its default.

        Raises
        ------
        TypeError
            If the payload is not a json object, or a field carries a json type this decoder
            does not accept.
        ValueError
            If the payload is not valid json, a numeric field is not a number, or
            ``orientation`` is a spelling this domain does not know.
        """
        return cls.from_json(_json_object(raw))

    @classmethod
    def from_json(cls, data: dict[str, object], /) -> Self:
        """Read the layout facts out of an already-parsed ``.content`` object.

        Present so :meth:`DocumentMetadata.decode` can read one sidecar once and hand the same
        members to both readers, instead of parsing the payload twice.

        Parameters
        ----------
        data
            The members of a ``.content`` sidecar.

        Returns
        -------
        DocumentLayout
            The layout, with every unrecorded field at its default.

        Raises
        ------
        TypeError
            If a field carries a json type this decoder does not accept.
        ValueError
            If a numeric field is not a number, or ``orientation`` is a spelling this domain
            does not know.
        """
        settings = _settings(data.get("extraMetadata"))
        return cls(
            format_version=_whole(data.get("formatVersion"), default=2),
            orientation=_orientation_from_wire(data.get("orientation")),
            margins=_whole(data.get("margins"), default=125),
            font_name=_string(data.get("fontName")),
            line_height=_whole(data.get("lineHeight"), default=-1),
            text_scale=_fraction(data.get("textScale"), default=1.0),
            text_alignment=_string(data.get("textAlignment"), default="justify"),
            zoom_mode=_string(data.get("zoomMode"), default="bestFit"),
            custom_zoom_scale=_fraction(data.get("customZoomScale"), default=1.0),
            extra_metadata=ExtraMetadata.from_json(settings),
        )


#: The store's ``type`` spellings, and the kind each one means.
_WIRE_KINDS: dict[str, DocumentKind] = {
    "DocumentType": DocumentKind.DOCUMENT,
    "CollectionType": DocumentKind.COLLECTION,
}

#: What an absent ``type`` means, matching the legacy reader's default.
_WIRE_KIND_DEFAULT = "DocumentType"

#: The store's ``fileType`` spellings, and the source each one means.
_WIRE_SOURCES: dict[str, SourceKind] = {member.value: member for member in SourceKind}

#: The store's ``orientation`` spellings, and the orientation each one means.
_WIRE_ORIENTATIONS: dict[str, PageOrientation] = {
    member.value: member for member in PageOrientation
}


def _kind_from_wire(value: object, /) -> DocumentKind:
    """Map the store's ``type`` field onto :class:`DocumentKind`.

    Parameters
    ----------
    value
        The raw ``type`` value, or ``None`` when the key was absent.

    Returns
    -------
    DocumentKind
        The kind that spelling means, or :attr:`DocumentKind.DOCUMENT` when absent.

    Raises
    ------
    TypeError
        If the value is present and is not a json string.
    ValueError
        If the spelling is neither of the two the store writes.
    """
    spelling = _string(value, default=_WIRE_KIND_DEFAULT)
    kind = _WIRE_KINDS.get(spelling)
    if kind is None:
        msg = f"unknown document type {spelling!r}; expected one of {sorted(_WIRE_KINDS)}"
        raise ValueError(msg)
    return kind


def _source_from_wire(value: object, /) -> SourceKind | None:
    """Map the store's ``fileType`` field onto :class:`SourceKind`.

    Parameters
    ----------
    value
        The raw ``fileType`` value, or ``None`` when the key was absent.

    Returns
    -------
    SourceKind | None
        The source that spelling means, or ``None`` when the sidecar did not say.

    Raises
    ------
    TypeError
        If the value is present and is not a json string.
    ValueError
        If the spelling is none of the three the store writes.
    """
    if value is None:
        return None
    spelling = _string(value)
    source = _WIRE_SOURCES.get(spelling)
    if source is None:
        msg = f"unknown file type {spelling!r}; expected one of {sorted(_WIRE_SOURCES)}"
        raise ValueError(msg)
    return source


def _orientation_from_wire(value: object, /) -> PageOrientation:
    """Map the store's ``orientation`` field onto :class:`PageOrientation`.

    Parameters
    ----------
    value
        The raw ``orientation`` value, or ``None`` when the key was absent.

    Returns
    -------
    PageOrientation
        The orientation that spelling means, or portrait when absent.

    Raises
    ------
    TypeError
        If the value is present and is not a json string.
    ValueError
        If the spelling is neither of the two the store writes.
    """
    spelling = _string(value, default=PageOrientation.PORTRAIT.value)
    orientation = _WIRE_ORIENTATIONS.get(spelling)
    if orientation is None:
        msg = f"unknown orientation {spelling!r}; expected one of {sorted(_WIRE_ORIENTATIONS)}"
        raise ValueError(msg)
    return orientation


class DocumentMetadata(BaseModel, frozen=True, extra="forbid"):
    """What the tablet UI knows about a document.

    ``last_modified`` is an aware datetime, not the store's millisecond epoch: a naive
    datetime compares wrongly against anything read from another store, and the legacy tree
    carried the epoch integer as far as the sync database, where two rows written by two
    different code paths meant two different things.

    One sentinel replaced by two fields
    -----------------------------------
    The legacy ``parent`` was one overloaded string: ``""`` meant the root, a uuid meant a
    folder, and ``"trash"`` meant trashed -- while a separate ``deleted`` flag meant trashed
    too. Here :attr:`parent_uuid` is a folder or ``None`` and :attr:`trashed` is a fact of its
    own, defined as the *or* of both legacy spellings. That definition lives here, in the field
    docstring and in :meth:`decode`, precisely so no adapter has to choose one of them and no
    two adapters can choose differently.
    """

    visible_name: str
    """The name shown on the tablet. Empty is legal; the tablet allows an unnamed document."""

    kind: DocumentKind
    """Document or folder."""

    source: SourceKind | None = None
    """What the document was made from, or ``None`` when the ``.content`` sidecar was not read or
    did not say.

    Not defaulted to :attr:`SourceKind.NOTEBOOK`, which is what the first pass at this model did:
    the legacy ``ContentInfo.file_type`` was a *required* field, so defaulting it here turned an
    unreadable content sidecar into a document that reads as a notebook -- and a pdf silently
    reported as a notebook is an export with no background and no defect recorded anywhere.
    ``None`` says "unknown", which is the truth in that case and is a state a caller can test."""

    parent_uuid: str | None = None
    """Folder this entry sits in, or ``None`` at the root and for a trashed entry.

    An opaque identifier, and deliberately not constrained the way :class:`DocumentId` is: it is
    compared and displayed, never joined into a path."""

    trashed: bool = False
    """Whether the entry is in the trash. Trashed entries persist on the store.

    The *or* of the legacy ``parent == "trash"`` sentinel and the legacy ``deleted`` flag. Both
    meant this, so reading either one alone -- as the legacy tree's two readers each did -- made
    a trashed document look live."""

    pinned: bool = False
    """Whether the entry is pinned in the UI."""

    last_modified: AwareDatetime | None = None
    """When the document last changed, or ``None`` when the store recorded nothing."""

    last_opened: AwareDatetime | None = None
    """When the document was last opened on the tablet, or ``None`` when the store recorded
    nothing. Restored from the legacy model and re-typed like :attr:`last_modified`, because
    ``rmspec inspect metadata`` prints it and did its own ``/1000`` epoch conversion to do so."""

    last_opened_page: int = Field(default=0, ge=0)
    """Zero-based index of the page the user last had open."""

    version: int = Field(default=0, ge=0)
    """The store's own metadata version counter, incremented on each sync. Printed by
    ``rmspec inspect metadata``; never a cache key -- see :class:`OcrCacheKey` for why."""

    synced: bool = False
    """Whether the store believes this document has reached the reMarkable cloud."""

    layout: DocumentLayout | None = None
    """The ``.content`` sidecar's layout and tool facts, or ``None`` when it was not read."""

    @classmethod
    def decode(cls, raw: bytes, /, *, content: bytes | None = None) -> Self:
        """Read a ``.metadata`` sidecar, and its ``.content`` sibling when the caller has it.

        The one place the store's vocabulary becomes this one: ``visibleName``, ``lastModified``
        as a millisecond epoch in a json *string*, ``type`` as ``"DocumentType"``, and the two
        trash spellings. ``ports/formats.py`` declines a sidecar codec port on the grounds that
        this belongs "on the domain models themselves (a decode classmethod over bytes)"; this is
        that method, and it is testable without a filesystem because it takes ``bytes``.

        Parameters
        ----------
        raw
            The bytes of the document's ``.metadata`` sidecar.
        content
            The bytes of the document's ``.content`` sidecar, when the caller has them. Without
            them :attr:`source` and :attr:`layout` stay ``None``, which is honest: those are the
            facts that file owns.

        Returns
        -------
        DocumentMetadata
            The metadata, with every unrecorded field at its default.

        Raises
        ------
        TypeError
            If either payload is not a json object, or a field carries a json type this decoder
            does not accept.
        ValueError
            If either payload is not valid json, a timestamp or number cannot be read, a
            spelling is one this domain does not know, or a field constraint refuses a value.
        """
        return cls.from_json(
            _json_object(raw),
            content=None if content is None else _json_object(content),
        )

    @classmethod
    def from_json(
        cls, data: dict[str, object], /, *, content: dict[str, object] | None = None
    ) -> Self:
        """Read already-parsed sidecar members.

        Parameters
        ----------
        data
            The members of the document's ``.metadata`` sidecar.
        content
            The members of the document's ``.content`` sidecar, when the caller has them.

        Returns
        -------
        DocumentMetadata
            The metadata, with every unrecorded field at its default.

        Raises
        ------
        TypeError
            If a field carries a json type this decoder does not accept.
        ValueError
            If a timestamp or number cannot be read, a spelling is one this domain does not
            know, or a field constraint refuses a value.
        """
        parent = _string(data.get("parent"))
        return cls(
            visible_name=_string(data.get("visibleName")),
            kind=_kind_from_wire(data.get("type")),
            source=None if content is None else _source_from_wire(content.get("fileType")),
            parent_uuid=None if parent in {"", _TRASH_PARENT} else parent,
            trashed=parent == _TRASH_PARENT or _flag(data.get("deleted")),
            pinned=_flag(data.get("pinned")),
            last_modified=_moment(data.get("lastModified")),
            last_opened=_moment(data.get("lastOpened")),
            last_opened_page=_whole(data.get("lastOpenedPage"), default=0),
            version=_whole(data.get("version"), default=0),
            synced=_flag(data.get("synced")),
            layout=None if content is None else DocumentLayout.from_json(content),
        )


class DocumentSummary(BaseModel, frozen=True, extra="forbid"):
    """A document as a listing sees it: identity, metadata, and its page identities.

    Cheap by construction -- there is nowhere in this model for a decoded page to go, so a
    ``list_documents`` implementation cannot accidentally pay to decode one.
    """

    doc_id: DocumentId
    """Identity of the document."""

    metadata: DocumentMetadata
    """What the tablet UI knows about it."""

    pages: tuple[PageId, ...] = ()
    """Page identities in document order. Empty for a folder, and for an empty notebook."""

    @property
    def page_count(self) -> int:
        """How many pages the document lists.

        Returns
        -------
        int
            The length of :attr:`pages`.
        """
        return len(self.pages)


class Document(BaseModel, frozen=True, extra="forbid"):
    """A whole document with every page decoded as far as it could be.

    A validator holds ``pages`` to document order, because the order *is* the addressing
    scheme: ``page_index`` in the sync store, ``last_opened_page`` in the metadata and the
    background page index in a PDF export are all positions in this tuple. An adapter that
    assembled pages out of order would otherwise produce a document that reads correctly and
    exports the wrong page.

    The serialized shape changed, on purpose
    ----------------------------------------
    The legacy model exposed ``name``, ``is_notebook``, ``is_pdf``, ``is_epub``, ``is_folder`` and
    ``is_trashed`` as ``@computed_field`` properties, so ``model_dump()`` emitted six keys this
    one does not. They are not re-added, because every one was a one-line read of
    :attr:`metadata` -- ``metadata.visible_name``, ``metadata.source is SourceKind.PDF``,
    ``metadata.kind is DocumentKind.COLLECTION``, ``metadata.trashed`` -- and all of those fields
    *are* dumped. No fact left the payload; only its duplicate spelling did, and with it the
    chance of the two disagreeing. A ``--json`` command that owes callers the old keys derives
    them at its own boundary, which is where an output contract belongs. :attr:`summary` and
    :attr:`defective_pages` are plain properties and have never serialized.

    Two legacy fields are folded rather than dropped: ``content`` became
    :attr:`DocumentMetadata.layout` plus :attr:`DocumentMetadata.source`, and ``templates`` --
    a parallel list indexed by page position -- became :attr:`Page.template_name`, so a template
    can no longer be attributed to the wrong page by a list that is one element short.
    """

    doc_id: DocumentId
    """Identity of the document."""

    metadata: DocumentMetadata
    """What the tablet UI knows about it."""

    pages: tuple[Page, ...] = ()
    """The decoded pages, in document order. Pages that could not be read are present and
    carry the defect saying so, rather than being omitted."""

    @model_validator(mode="after")
    def _check_pages_are_in_document_order(self) -> Self:
        """Reject pages whose recorded index disagrees with their position.

        Returns
        -------
        Document
            The validated model.

        Raises
        ------
        ValueError
            If any page's ``index`` is not its position in ``pages``.
        """
        misplaced = [
            f"{page.page_id.uuid} claims index {page.index} at position {position}"
            for position, page in enumerate(self.pages)
            if page.index != position
        ]
        if misplaced:
            msg = f"document {self.doc_id.uuid} pages are not in document order: " + "; ".join(
                misplaced
            )
            raise ValueError(msg)
        return self

    @property
    def summary(self) -> DocumentSummary:
        """Return this document as a listing would show it.

        Present so a fake ``DocumentRepository`` can implement ``list_documents`` from the same
        prebuilt aggregates it serves ``load`` from, instead of holding two copies of the same
        facts that can disagree.

        Returns
        -------
        DocumentSummary
            The identity, the metadata and the page identities in document order.
        """
        return DocumentSummary(
            doc_id=self.doc_id,
            metadata=self.metadata,
            pages=tuple(page.page_id for page in self.pages),
        )

    @property
    def defective_pages(self) -> tuple[Page, ...]:
        """Pages that came back with something wrong.

        Returns
        -------
        tuple[Page, ...]
            Pages carrying at least one defect, in document order. Empty when the whole
            document decoded cleanly, which is what ``--strict`` checks.
        """
        return tuple(page for page in self.pages if page.all_defects)


# ──────────────────────── The tracked mirror, and extracted text ────────────────────────
#
#  The tracked mirror of the tablet, and the text extracted from its pages.
#
#  ``DocumentSyncStore`` exchanges these four models. They describe what was pulled and when,
#  which is a different question from what a document *is*. :class:`Document` answers that, and
#  a mirror row must survive without it, because the whole point of the mirror
#  is to answer "has this page changed?" without decoding anything.
#
#  Hashes, not timestamps, decide change
#  -------------------------------------
#  :attr:`SyncedPage.rm_hash` is the lowercase hex SHA-256 of the page's stored bytes, which is
#  the same value ``DocumentRepository.page_fingerprint`` returns. Comparing it is what detects a
#  changed page; the tablet's own ``lastModified`` is per document, not per page, and it moves
#  when metadata changes with no ink touched.
#
#  Provenance travels with text
#  ----------------------------
#  :class:`PageText` carries :class:`TextProvenance`, so ``rmspec search`` can say how the text it
#  matched was produced. That is deliberately *not* a cache key: text stays readable after the
#  key that produced it stops being current, which is why extracted text lives on the page in
#  this store rather than in the OCR cache. A cache is an exact-key lookup and must never double
#  as a browse.


class SyncedDocument(BaseModel, frozen=True, extra="forbid"):
    """One tablet document as the local mirror last saw it.

    Uuids are plain strings here rather than
    :class:`DocumentId`, because that is the vocabulary
    ``DocumentSyncStore`` declares: its methods take ``doc_uuid: str``. The two spellings of
    identity in the port layer are a known seam, recorded rather than papered over.
    """

    uuid: str = Field(min_length=1)
    """The document's identifier on the tablet."""

    visible_name: str
    """The document's name at the time it was recorded. Denormalised on purpose: a history
    entry naming a document that has since been renamed should still read correctly."""

    kind: DocumentKind
    """Document or folder."""

    source: SourceKind = SourceKind.NOTEBOOK
    """What the document was made from."""

    parent_uuid: str | None = None
    """Folder the document sat in, or ``None`` at the root."""

    page_count: int = Field(ge=0)
    """How many pages the document listed when it was recorded."""

    metadata_hash: str | None = None
    """Lowercase hex SHA-256 of the stored metadata artifact, or ``None`` if not hashed."""

    content_hash: str | None = None
    """Lowercase hex SHA-256 of the stored content artifact, or ``None`` if not hashed."""

    device_last_modified: AwareDatetime | None = None
    """The tablet's own last-modified time, when it reported one."""

    synced_at: AwareDatetime
    """When this row was written. Required: a mirror row whose age is unknown cannot be
    reasoned about, and a default clock in the domain is a clock tests have to freeze."""


class SyncedPage(BaseModel, frozen=True, extra="forbid"):
    """One page of a tracked document, and the fingerprint that decides whether it changed."""

    doc_uuid: str = Field(min_length=1)
    """Identifier of the owning document."""

    page_uuid: str = Field(min_length=1)
    """The page's own identifier."""

    page_index: int = Field(ge=0)
    """Zero-based position in the document, and the store's declared sort order."""

    rm_hash: str | None = None
    """Lowercase hex SHA-256 of the page's stored scene bytes, or ``None`` when the page had
    no scene artifact to hash. Not a cache key on its own -- see
    :class:`OcrCacheKey`."""

    rm_size_bytes: int | None = Field(default=None, ge=0)
    """Size of the page's stored scene bytes, or ``None`` when there were none."""

    synced_at: AwareDatetime
    """When this row was written."""


class TextProvenance(BaseModel, frozen=True, extra="forbid"):
    """How one page's text came to exist.

    Every field is a fact about the production, so a search result can explain itself: which
    engines read the page, which model binding merged their readings, and at what resolution
    the page was rasterised. Text imported from a legacy ``.ocr.txt`` sidecar has an empty
    ``recognizers`` and no ``model_fingerprint``, which is a representable and honest state --
    that migration genuinely does not know how its text was produced.
    """

    recognizers: tuple[str, ...] = ()
    """``TextRecognizer.provider_id`` slugs that contributed, in the order they were folded.
    Empty means the text did not come from a recognizer."""

    model_fingerprint: str | None = None
    """``VisionLanguageModel.fingerprint`` of the binding that merged the readings, or ``None``
    when no model was involved."""

    render_dpi: int | None = Field(default=None, gt=0)
    """Resolution the page was rasterised at, or ``None`` when it was never rasterised."""

    extracted_at: AwareDatetime
    """When the extraction ran."""


class PageText(BaseModel, frozen=True, extra="forbid"):
    """The text of one page, with the provenance of its extraction.

    Keyed by ``(doc_uuid, page_uuid)``: re-running OCR replaces the row rather than
    accumulating, which is why ``record_page_text`` takes the whole model instead of a text
    argument plus separate identity arguments that a caller could mismatch.
    """

    doc_uuid: str = Field(min_length=1)
    """Identifier of the owning document."""

    page_uuid: str = Field(min_length=1)
    """The page's own identifier."""

    page_index: int = Field(ge=0)
    """Zero-based position in the document, and part of the store's declared sort order."""

    text: str
    """The extracted text. Empty is legal and means the page was read and held nothing."""

    provenance: TextProvenance
    """How the text was produced."""

    @model_validator(mode="after")
    def _check_empty_text_has_no_confidence_claim(self) -> Self:
        """Reject text that claims a merging model but carries nothing.

        A merged reading with no text means the merge failed and the failure was written down
        as a success. Blank pages are recorded with a provenance that names no model.

        Returns
        -------
        PageText
            The validated model.

        Raises
        ------
        ValueError
            If ``text`` is blank while ``provenance.model_fingerprint`` is set.
        """
        if not self.text.strip() and self.provenance.model_fingerprint is not None:
            msg = (
                f"page {self.page_uuid} has empty text but claims model "
                f"{self.provenance.model_fingerprint!r}; a blank page's provenance names no "
                f"model"
            )
            raise ValueError(msg)
        return self


# ──────────────────────── Cache keys and cached artifacts ────────────────────────
#
#  Cache keys and cached artifacts for the two paid pipelines.
#
#  The defect these models exist to remove: the legacy tables keyed on the source file's hash
#  alone while storing the model id and the render DPI as ordinary columns. Changing the prompt,
#  the model, the pen formulas or the DPI therefore produced a *hit* on a row computed under the
#  old settings -- a stale answer that looked valid and cost nothing to believe.
#
#  Every key here folds each of those inputs into :attr:`OcrCacheKey.digest` /
#  :attr:`DiagramCacheKey.digest`, and every component is a required field, so a key of unknown
#  provenance is unconstructible rather than merely unlikely. ``OcrCache.get`` matches on the
#  digest and nothing else, so a changed input is mechanically a miss.
#
#  ``digest`` is a property, not a field
#  -------------------------------------
#  An identity a caller can pass in is an identity a caller can get wrong, and an identity an
#  adapter echoes back is unfalsifiable -- every test double echoes it correctly. Computing it
#  here from the components means a fake cache and the SQLite cache agree by construction.
#
#  Where the components come from
#  ------------------------------
#  ``page_hash`` is ``DocumentRepository.page_fingerprint``. ``render_digest`` is
#  ``RenderStyle.digest``, which folds thickness, padding, renderer revision, screen, palette and
#  background. ``raster_digest`` is ``RasterImage.digest``, which folds encoding, pixel
#  dimensions, DPI and the bytes. ``request_digest`` is ``VisionRequest.digest``, which folds
#  system text, prompt text and decoding settings -- stronger than a ``prompt_version`` integer
#  somebody has to remember to bump. ``model_fingerprint`` is ``VisionLanguageModel.fingerprint``.


class PageContentKind(StrEnum):
    """What a page turned out to hold, as decided by the diagram pass.

    Closed, because the CLI branches on it: a ``TEXT`` page has no Mermaid to write out, and a
    ``MIXED`` page has both a transcription and a diagram worth keeping.
    """

    TEXT = "text"
    DIAGRAM = "diagram"
    MIXED = "mixed"


class OcrCacheKey(BaseModel, frozen=True, extra="forbid"):
    """Everything that changes the transcription of one page.

    Frozen and hashable, so an in-memory ``OcrCache`` double can be a dict keyed by this model
    directly as well as by :attr:`digest`.
    """

    page_hash: str = Field(min_length=1)
    """Lowercase hex SHA-256 of the page's stored scene bytes."""

    render_digest: str = Field(min_length=1)
    """``RenderStyle.digest`` for the render that produced the pixels."""

    raster_digest: str = Field(min_length=1)
    """``RasterImage.digest`` for the pixels themselves, which carries the DPI."""

    recognizers: tuple[str, ...]
    """``provider_id`` slugs of the engines whose readings were folded in, in fold order.
    Required: partial failure is tolerated, so which engines survived changes the answer."""

    model_fingerprint: str = Field(min_length=1)
    """``VisionLanguageModel.fingerprint`` of the binding that merged the readings."""

    request_digest: str = Field(min_length=1)
    """``VisionRequest.digest``, which folds the system text, the prompt and the decoding."""

    @property
    def digest(self) -> str:
        """Return the single string this key is matched on.

        Returns
        -------
        str
            Lowercase hex SHA-256 over every component, in declaration order.
        """
        return _digest(
            b"rmspec.cache.ocr.v1",
            (
                self.page_hash,
                self.render_digest,
                self.raster_digest,
                _ITEM_SEPARATOR.join(self.recognizers),
                self.model_fingerprint,
                self.request_digest,
            ),
        )


class OcrArtifact(BaseModel, frozen=True, extra="forbid"):
    """One page's transcription, as it was cached.

    :attr:`truncated` exists because ``StopReason`` is data on a completion rather than an
    exception: a half-transcribed page is a wrong page, and caching it without recording that
    it was cut short is how the legacy pipeline served half pages indefinitely. The flag is
    stored so a use case can decide to recompute rather than trust the row.
    """

    text: str
    """The transcription. Empty is legal and means the page held no text."""

    mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    """Mean recognizer confidence on a 0-1 scale, or ``None`` when none was reported."""

    truncated: bool = False
    """Whether generation stopped at a limit rather than on the model's own terms."""

    created_at: AwareDatetime
    """When the transcription was produced. Required, so no clock lives in the domain."""


class DiagramCacheKey(BaseModel, frozen=True, extra="forbid"):
    """Everything that changes the diagram extracted from one page.

    The same components as :class:`OcrCacheKey` minus ``recognizers``: diagram extraction reads
    the pixels with a multimodal model and never runs an OCR engine, so there is no surviving-
    engine set to fold in. A separate type rather than one optional field, because a key whose
    meaning depends on whether a field is set is a key two adapters can read differently.
    """

    page_hash: str = Field(min_length=1)
    """Lowercase hex SHA-256 of the page's stored scene bytes."""

    render_digest: str = Field(min_length=1)
    """``RenderStyle.digest`` for the render that produced the pixels."""

    raster_digest: str = Field(min_length=1)
    """``RasterImage.digest`` for the pixels themselves, which carries the DPI."""

    model_fingerprint: str = Field(min_length=1)
    """``VisionLanguageModel.fingerprint`` of the binding that did the extraction."""

    request_digest: str = Field(min_length=1)
    """``VisionRequest.digest``, which folds the system text, the prompt and the decoding."""

    @property
    def digest(self) -> str:
        """Return the single string this key is matched on.

        Returns
        -------
        str
            Lowercase hex SHA-256 over every component, in declaration order.
        """
        return _digest(
            b"rmspec.cache.diagram.v1",
            (
                self.page_hash,
                self.render_digest,
                self.raster_digest,
                self.model_fingerprint,
                self.request_digest,
            ),
        )


class DiagramArtifact(BaseModel, frozen=True, extra="forbid"):
    """What the diagram pass made of one page.

    A validator ties :attr:`mermaid` to :attr:`content_kind`, so "this page is a diagram" and
    "here is no diagram" cannot both be true of one cached row. That pairing was previously
    two nullable columns with nothing relating them.
    """

    content_kind: PageContentKind
    """What the page turned out to hold."""

    mermaid: str | None = None
    """Mermaid source for the diagram, or ``None`` for a text-only page."""

    diagram_kind: str | None = None
    """The Mermaid diagram type, e.g. ``"flowchart"``. An open string: the set of diagram types
    is Mermaid's to grow, and a closed enum here would make a new one a domain edit."""

    created_at: AwareDatetime
    """When the extraction ran. Required, so no clock lives in the domain."""

    @model_validator(mode="after")
    def _check_mermaid_matches_content_kind(self) -> Self:
        """Reject a row whose Mermaid presence contradicts its content kind.

        Returns
        -------
        DiagramArtifact
            The validated model.

        Raises
        ------
        ValueError
            If a diagram-bearing kind has no Mermaid, or a text-only page has some.
        """
        has_diagram = self.content_kind is not PageContentKind.TEXT
        if has_diagram and not (self.mermaid or "").strip():
            msg = f"content_kind {self.content_kind.value!r} requires mermaid source"
            raise ValueError(msg)
        if not has_diagram and self.mermaid is not None:
            msg = (
                f"content_kind {PageContentKind.TEXT.value!r} carries no diagram, so mermaid "
                f"must be None"
            )
            raise ValueError(msg)
        return self


# ──────────────────────── Sync history ────────────────────────
#
#  The append-only history of what this tool did, including what it failed to do.
#
#  ``SyncAuditLog`` exchanges these two models. :class:`SyncAuditEntry` is what a caller appends;
#  :class:`RecordedSyncAuditEntry` is what the log returns, and it adds the sequence number the
#  store assigned. Composition rather than a subclass with one extra field, so there is exactly
#  one definition of an entry's contents and no way for an unsequenced entry to be mistaken for a
#  recorded one.
#
#  Order is the store's, not the clock's
#  -------------------------------------
#  There is no ordering promise attached to :attr:`SyncAuditEntry.occurred_at`. Entries written
#  inside one pull share a timestamp, tests freeze the clock, and a file, a list and SQLite each
#  broke that tie differently. ``SyncAuditLog.append`` returns a strictly increasing sequence and
#  ``recent`` orders by it, so the contract test is "``recent()`` equals the appends reversed" and
#  needs no clock at all. The timestamp stays because a human reading history wants it.
#
#  Failures are entries, not exceptions
#  ------------------------------------
#  :attr:`SyncAuditEntry.outcome` includes the unhappy endings. An append survives the failure it
#  describes -- that is what the port's durability rule is for -- so a pull that dies at document
#  400 still has the entry recording the death. A validator requires a failed or partial entry to
#  carry a detail, because "something went wrong" with no detail is the log line this design
#  exists to replace.


class SyncOperation(StrEnum):
    """What kind of work an entry describes.

    Closed, and one member per command that changes state or spends money. Reading -- listing,
    inspecting, rendering to stdout -- is not audited, because a history that records reads is a
    history nobody reads.
    """

    PULL = "pull"
    """Documents copied from the tablet into the local mirror."""

    PUSH = "push"
    """A file uploaded to the tablet."""

    OCR = "ocr"
    """Pages transcribed, which spends recognizer and model budget."""

    DIAGRAM = "diagram"
    """Pages examined for diagrams, which spends model budget."""

    EXPORT = "export"
    """Pages written out as SVG, PNG or PDF."""


class SyncOutcome(StrEnum):
    """How an operation ended.

    Four members, because the CLI phrases four different summaries. ``PARTIAL`` is the one the
    legacy code could not express: a pull that copied 380 of 400 documents was reported as a
    success with twenty warnings scrolled off the top of the terminal.
    """

    SUCCEEDED = "succeeded"
    """Every unit of work landed."""

    PARTIAL = "partial"
    """Some units landed and some did not."""

    FAILED = "failed"
    """Nothing landed."""

    SKIPPED = "skipped"
    """Nothing needed doing -- already current, or excluded by the caller."""


class SyncAuditEntry(BaseModel, frozen=True, extra="forbid"):
    """One operation, as the caller appending it describes it."""

    operation: SyncOperation
    """What kind of work this was."""

    outcome: SyncOutcome
    """How it ended."""

    doc_uuid: str | None = None
    """The document worked on, or ``None`` for an operation spanning the whole library."""

    doc_name: str = ""
    """The document's name at the time. Denormalised on purpose: history about a document that
    has since been renamed should still read correctly."""

    pages_affected: int = Field(default=0, ge=0)
    """How many pages were transferred, transcribed or written."""

    detail: str = ""
    """Why, for a human reading the history. Required when the outcome was not clean."""

    occurred_at: AwareDatetime
    """When the operation happened. Required, so no clock lives in the domain."""

    @model_validator(mode="after")
    def _check_unhappy_outcomes_are_explained(self) -> Self:
        """Reject a failed or partial entry that says nothing about why.

        Returns
        -------
        SyncAuditEntry
            The validated model.

        Raises
        ------
        ValueError
            If ``outcome`` is ``FAILED`` or ``PARTIAL`` and ``detail`` is blank.
        """
        needs_detail = self.outcome in {SyncOutcome.FAILED, SyncOutcome.PARTIAL}
        if needs_detail and not self.detail.strip():
            msg = f"outcome {self.outcome.value!r} requires a detail saying what went wrong"
            raise ValueError(msg)
        return self


class RecordedSyncAuditEntry(BaseModel, frozen=True, extra="forbid"):
    """An entry the log has accepted, carrying the order the store put it in."""

    sequence: int = Field(ge=1)
    """The store-assigned sequence number: strictly increasing, starting at 1."""

    entry: SyncAuditEntry
    """What was appended."""
