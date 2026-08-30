"""Behavioural tests for the domain model.

The module under test holds no I/O at all -- no clock, no filesystem, no store -- so every
test here is pure: bytes in, values out. What is worth asserting is therefore not that a field
exists but what the models *refuse*, what they *derive*, and what they *round trip*.

Four groups carry the weight:

sidecar readers and ``decode``
    The store's vocabulary meets this one in exactly two classmethods, so the ``TypeError`` /
    ``ValueError`` split those readers promise is tested directly: a missing key becomes the
    stated default, a wrong json *type* is a ``TypeError``, and a right-typed value that cannot
    be read is a ``ValueError``. Defect 1's shape -- the same field knowledge hand-mirrored into
    four readers -- is prevented by there being one decoder, so the tests exercise that one.

constraints and validators
    Six model validators encode facts the type system cannot: a contentless page must say why,
    a document's pages must be in document order, blank text may not claim a merging model, a
    palette must be total, a Mermaid-bearing kind must carry Mermaid, and an unhappy audit
    outcome must carry a detail. Each is tested from both sides -- the state it permits and the
    state it makes unconstructible.

cache-key identity (defect 3, as an executable invariant)
    ``OcrCacheKey`` and ``DiagramCacheKey`` are property-tested with hypothesis: two keys that
    differ in *any* field are neither equal nor equal-digested. That is the legacy defect --
    keying on ``rm_hash`` alone while the model id and render DPI sat in non-key columns -- as a
    test that fails the moment a component stops being folded in. Golden hex constants pin the
    digest bodies so a change of scheme is a mechanical failure rather than a silent cache
    split.

derived geometry and classification
    ``bounding_box`` at three altitudes, ``x_shift``, the millimetre conversions, and the pen
    fold. The legacy defects these replaced are asserted as behaviour: a point-less stroke
    reports ``None`` rather than a zero box at the origin, and hidden layers are excluded from
    every aggregate.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from rmspec.domain import models as domain_models
from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_PHYSICAL_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    DiagramArtifact,
    DiagramCacheKey,
    Document,
    DocumentId,
    DocumentKind,
    DocumentLayout,
    DocumentMetadata,
    DocumentSummary,
    ExtraMetadata,
    Layer,
    OcrArtifact,
    OcrCacheKey,
    OcrProvenance,
    Page,
    PageContent,
    PageContentKind,
    PageDefect,
    PageDefectCode,
    PageId,
    PageOrientation,
    PageText,
    Palette,
    PenColor,
    PenType,
    Point,
    RecordedSyncAuditEntry,
    Rgb,
    Rgba,
    ScreenSpec,
    SourceKind,
    Stroke,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
    TextBlock,
    TextProvenance,
    pen_from_wire,
)

# ──────────────────────── fixtures-as-builders ────────────────────────
#
#  Plain functions rather than pytest fixtures: hypothesis cannot take a function-scoped
#  fixture, and half of these are used from inside a `@given` body.

MOMENT = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
NAIVE_MOMENT = MOMENT.replace(tzinfo=None)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: Pinned so a change to the digest scheme is a failing assertion, not a silent cache split.
#: Both moved once, when length framing replaced the 0x1f separator and the tags went v1 -> v2.
OCR_DIGEST_GOLDEN = "e1678848127f7f7a36cd71b2314c29f1e27287b10d19bee661a2dbc770f60022"
DIAGRAM_DIGEST_GOLDEN = "6d0f22509bbd372a6baa8e3a949451463cfb439f61bdc7cf97ad6d0632a4fb69"

OCR_KEY_PARTS: dict[str, Any] = {
    "page_hash": "ph",
    "render_digest": "rd",
    "raster_digest": "rad",
    "recognizers": ("vision", "textract"),
    "model_fingerprint": "mf",
    "request_digest": "rq",
}
DIAGRAM_KEY_PARTS: dict[str, Any] = {
    "page_hash": "ph",
    "render_digest": "rd",
    "raster_digest": "rad",
    "model_fingerprint": "mf",
    "request_digest": "rq",
}


def ocr_parts(**overrides: object) -> dict[str, Any]:
    parts: dict[str, Any] = dict(OCR_KEY_PARTS)
    parts.update(overrides)
    return parts


def diagram_parts(**overrides: object) -> dict[str, Any]:
    parts: dict[str, Any] = dict(DIAGRAM_KEY_PARTS)
    parts.update(overrides)
    return parts


def make_stroke(*, points: tuple[Point, ...] = (), pen: PenType = PenType.FINELINER_1) -> Stroke:
    return Stroke(pen=pen, color=PenColor.BLACK, thickness_scale=2.0, points=points)


def make_point(x: float, y: float) -> Point:
    return Point(x=x, y=y)


def make_page(
    *,
    uuid: str = "page-1",
    index: int = 0,
    content: PageContent | None = None,
    defects: tuple[PageDefect, ...] = (),
) -> Page:
    return Page(
        page_id=PageId(uuid=uuid),
        index=index,
        content=PageContent() if content is None and not defects else content,
        defects=defects,
    )


def make_metadata(**overrides: object) -> DocumentMetadata:
    fields: dict[str, Any] = {"visible_name": "Notes", "kind": DocumentKind.DOCUMENT}
    fields.update(overrides)
    return DocumentMetadata(**fields)


def make_provenance(**overrides: object) -> TextProvenance:
    fields: dict[str, Any] = {"extracted_at": MOMENT}
    fields.update(overrides)
    return TextProvenance(**fields)


def make_audit_entry(**overrides: object) -> SyncAuditEntry:
    fields: dict[str, Any] = {
        "operation": SyncOperation.PULL,
        "outcome": SyncOutcome.SUCCEEDED,
        "occurred_at": MOMENT,
    }
    fields.update(overrides)
    return SyncAuditEntry(**fields)


# ──────────────────────── module surface ────────────────────────


def test_public_surface_is_exported_without_duplicates():
    assert len(set(domain_models.__all__)) == len(domain_models.__all__)
    for name in domain_models.__all__:
        assert hasattr(domain_models, name)


def test_nothing_defined_here_is_public_without_being_exported():
    # The ports slices import from this module by name, so a model that exists but is not
    # exported is a model half the workspace cannot reach.
    defined_here = {
        name
        for name, value in vars(domain_models).items()
        if not name.startswith("_")
        and type(value).__name__ != "module"
        and getattr(value, "__module__", "") == domain_models.__name__
    }

    assert defined_here == set(domain_models.__all__)


def test_every_exported_model_is_frozen_and_forbids_unknown_fields():
    exported = [getattr(domain_models, name) for name in domain_models.__all__]
    models = [
        obj
        for obj in exported
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    ]

    assert len(models) >= 20
    for model in models:
        assert model.model_config.get("frozen") is True, model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_frozen_models_refuse_assignment():
    doc_id = DocumentId(uuid="abc")

    with pytest.raises(ValidationError):
        doc_id.uuid = "def"  # ty: ignore[invalid-assignment]


def test_models_refuse_an_unknown_field():
    with pytest.raises(ValidationError):
        DocumentId.model_validate({"uuid": "abc", "uid": "abc"})


def test_domain_imports_nothing_outside_stdlib_and_pydantic():
    # The dependency rule, as a test rather than a comment: an adapter import sneaking into the
    # domain would show up here before it showed up in an architecture test.
    banned = {"rmscene", "httpx", "sqlite3", "boto3", "cairocffi", "cairosvg", "fitz", "paramiko"}
    imported = {
        value.__name__.split(".")[0]
        for value in vars(domain_models).values()
        if getattr(value, "__name__", None) and type(value).__name__ == "module"
    }

    assert not (imported & banned)


# ──────────────────────── sidecar json readers ────────────────────────


def test_json_object_reads_the_members_of_an_object():
    assert domain_models._json_object(b'{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}


def test_json_object_reads_an_empty_object():
    assert domain_models._json_object(b"{}") == {}


@pytest.mark.parametrize("payload", [b"[]", b'"text"', b"3", b"null", b"true"])
def test_json_object_refuses_valid_json_that_is_not_an_object(payload: bytes):
    with pytest.raises(TypeError, match="expected a json object"):
        domain_models._json_object(payload)


@pytest.mark.parametrize("payload", [b"{", b"", b"not json"])
def test_json_object_refuses_payloads_that_are_not_json(payload: bytes):
    with pytest.raises(ValueError, match="Expecting"):
        domain_models._json_object(payload)


def test_string_reader_returns_the_default_for_an_absent_key():
    assert domain_models._string(None) == ""
    assert domain_models._string(None, default="justify") == "justify"


def test_string_reader_passes_a_string_through_unchanged():
    assert domain_models._string("  spaced  ") == "  spaced  "


@pytest.mark.parametrize("value", [1, 1.5, [], {}])
def test_string_reader_refuses_a_non_string(value: object):
    with pytest.raises(TypeError, match="expected a json string"):
        domain_models._string(value)


def test_flag_reader_returns_the_default_for_an_absent_key():
    assert domain_models._flag(None) is False
    assert domain_models._flag(None, default=True) is True


def test_flag_reader_passes_a_real_boolean_through():
    truthy: object = True
    falsy: object = False

    assert domain_models._flag(truthy) is True
    assert domain_models._flag(falsy) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("false", False), ("TRUE", True), ("  False  ", False)],
)
def test_flag_reader_tolerates_the_stores_boolean_words(value: str, *, expected: bool):
    assert domain_models._flag(value) is expected


@pytest.mark.parametrize("value", ["yes", "", "1", 1, [], {}])
def test_flag_reader_refuses_anything_else(value: object):
    with pytest.raises(TypeError, match="expected a json boolean"):
        domain_models._flag(value)


def test_whole_reader_returns_the_default_for_an_absent_key():
    assert domain_models._whole(None, default=125) == 125


@pytest.mark.parametrize(
    ("value", "expected"),
    [(7, 7), (7.9, 7), (-3, -3), ("7", 7), ("  -12  ", -12)],
)
def test_whole_reader_narrows_numbers_and_quoted_numbers(value: object, expected: int):
    assert domain_models._whole(value, default=0) == expected


def test_whole_reader_refuses_a_string_that_is_not_a_number():
    with pytest.raises(ValueError, match="invalid literal for int"):
        domain_models._whole("later", default=0)


@pytest.mark.parametrize("value", [[], {}])
def test_whole_reader_refuses_a_non_number(value: object):
    with pytest.raises(TypeError, match="expected a json integer"):
        domain_models._whole(value, default=0)


def test_fraction_reader_returns_the_default_for_an_absent_key():
    assert domain_models._fraction(None, default=1.0) == 1.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2, 2.0), (0.5, 0.5), ("0.25", 0.25), ("  -1.5 ", -1.5)],
)
def test_fraction_reader_narrows_numbers_and_quoted_numbers(value: object, expected: float):
    assert domain_models._fraction(value, default=1.0) == expected


def test_fraction_reader_refuses_a_string_that_is_not_a_number():
    with pytest.raises(ValueError, match="could not convert string to float"):
        domain_models._fraction("big", default=1.0)


@pytest.mark.parametrize("value", [[], {}])
def test_fraction_reader_refuses_a_non_number(value: object):
    with pytest.raises(TypeError, match="expected a json number"):
        domain_models._fraction(value, default=1.0)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_moment_reader_reports_no_instant_for_an_absent_or_blank_value(value: object):
    assert domain_models._moment(value) is None


def test_moment_reader_treats_zero_as_a_real_instant():
    # The distinction the docstring insists on: the store writing "0" is not the store writing
    # nothing.
    assert domain_models._moment("0") == EPOCH
    assert domain_models._moment(0) == EPOCH


def test_moment_reader_converts_the_stores_millisecond_epoch():
    from_text = domain_models._moment("1700000000000")
    from_number = domain_models._moment(1_700_000_000_000)

    assert from_text == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert from_number == from_text
    assert from_number is not None
    assert from_number.tzinfo is UTC


def test_moment_reader_keeps_sub_second_precision():
    moment = domain_models._moment(1500)

    assert moment is not None
    assert moment.microsecond == 500_000


def test_moment_reader_refuses_a_string_that_is_not_a_number():
    with pytest.raises(ValueError, match="could not convert string to float"):
        domain_models._moment("yesterday")


@pytest.mark.parametrize("value", [[], {}])
def test_moment_reader_refuses_a_non_number(value: object):
    with pytest.raises(TypeError, match="expected a millisecond epoch"):
        domain_models._moment(value)


def test_settings_reader_returns_an_empty_mapping_for_an_absent_key():
    assert domain_models._settings(None) == {}


def test_settings_reader_renders_every_value_as_text():
    # Opaque display data: a firmware that writes a number where it wrote a string should change
    # what `rmspec inspect content` prints, not whether the document decodes.
    assert domain_models._settings({"FinelinerV2Size": 2, "LastTool": "Fineliner"}) == {
        "FinelinerV2Size": "2",
        "LastTool": "Fineliner",
    }


@pytest.mark.parametrize("value", ["{}", 1, []])
def test_settings_reader_refuses_a_non_object(value: object):
    with pytest.raises(TypeError, match="expected a json object of tool settings"):
        domain_models._settings(value)


# ──────────────────────── identity ────────────────────────


@pytest.mark.parametrize("model", [DocumentId, PageId])
def test_identifiers_are_carried_verbatim(model: type[DocumentId | PageId]):
    # No UUID round trip, so case and grouping survive: an identity read from the device still
    # compares equal to the same identity read from a cache row.
    spelling = "9F8E7D6C-1234-ABCD-ef01-000000000000"
    identifier = model(uuid=spelling)

    assert identifier.uuid == spelling
    assert str(identifier) == spelling


@pytest.mark.parametrize("model", [DocumentId, PageId])
@pytest.mark.parametrize("uuid", ["a/b", "a b", "..%2f", "a\\b", "café", "a:b", "a\nb"])
def test_identifiers_refuse_a_spelling_that_could_traverse(
    model: type[DocumentId | PageId], uuid: str
):
    with pytest.raises(ValidationError):
        model(uuid=uuid)


@pytest.mark.parametrize("model", [DocumentId, PageId])
@pytest.mark.parametrize("uuid", [".", "..", "...", "...."])
def test_identifiers_refuse_a_dot_segment(model: type[DocumentId | PageId], uuid: str):
    with pytest.raises(ValidationError, match="path segment, not an identity"):
        model(uuid=uuid)


@pytest.mark.parametrize("model", [DocumentId, PageId])
def test_identifiers_accept_a_dot_that_is_not_the_whole_spelling(
    model: type[DocumentId | PageId],
):
    assert model(uuid="a.b").uuid == "a.b"
    assert model(uuid=".hidden").uuid == ".hidden"


@pytest.mark.parametrize("model", [DocumentId, PageId])
def test_identifiers_are_bounded_at_both_ends(model: type[DocumentId | PageId]):
    assert model(uuid="a" * 64).uuid == "a" * 64

    with pytest.raises(ValidationError):
        model(uuid="a" * 65)

    with pytest.raises(ValidationError):
        model(uuid="")


def test_identifiers_are_hashable_so_a_repository_double_can_be_a_dict():
    store = {DocumentId(uuid="doc"): "value"}

    assert store[DocumentId(uuid="doc")] == "value"


def test_document_and_page_identity_cannot_be_swapped():
    # The whole reason there are two single-field models rather than two bare strings.
    assert DocumentId(uuid="same") != PageId(uuid="same")


# ──────────────────────── colour and palettes ────────────────────────


def test_pen_colour_wire_values_are_pinned():
    assert [(member.name, member.value) for member in PenColor] == [
        ("BLACK", 0),
        ("GRAY", 1),
        ("WHITE", 2),
        ("YELLOW", 3),
        ("GREEN", 4),
        ("PINK", 5),
        ("BLUE", 6),
        ("RED", 7),
        ("GRAY_OVERLAP", 8),
        ("HIGHLIGHT", 9),
        ("GREEN_2", 10),
        ("CYAN", 11),
        ("MAGENTA", 12),
        ("YELLOW_2", 13),
    ]


def test_rgb_renders_its_three_spellings():
    colour = Rgb(r=78, g=105, b=201)

    assert colour.as_tuple() == (78, 105, 201)
    assert colour.as_hex() == "#4e69c9"
    assert colour.as_css() == "rgb(78, 105, 201)"


def test_rgb_hex_is_zero_padded_and_lowercase():
    assert Rgb(r=0, g=10, b=255).as_hex() == "#000aff"
    assert Rgb(r=171, g=205, b=239).as_hex() == "#abcdef"


@given(
    r=st.integers(min_value=0, max_value=255),
    g=st.integers(min_value=0, max_value=255),
    b=st.integers(min_value=0, max_value=255),
)
def test_rgb_hex_round_trips_through_its_channels(r: int, g: int, b: int):
    text = Rgb(r=r, g=g, b=b).as_hex()

    assert len(text) == 7
    assert text == text.lower()
    assert (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)) == (r, g, b)


@pytest.mark.parametrize("channel", ["r", "g", "b"])
@pytest.mark.parametrize("value", [-1, 256, 300])
def test_rgb_refuses_a_channel_outside_eight_bits(channel: str, value: int):
    # The legacy RGB accepted r=300 and rendered a malformed eight-character hex string.
    channels: dict[str, Any] = {"r": 0, "g": 0, "b": 0}
    channels[channel] = value

    with pytest.raises(ValidationError):
        Rgb(**channels)


def test_rgb_accepts_both_bounds():
    assert Rgb(r=0, g=0, b=0).as_hex() == "#000000"
    assert Rgb(r=255, g=255, b=255).as_hex() == "#ffffff"


def test_rgba_drops_its_alpha_to_reach_the_type_every_ink_consumer_takes():
    # The measured highlighter colour: little-endian 0xFFFFED75, i.e. a=255 and #ffed75.
    colour = Rgba(r=0xFF, g=0xED, b=0x75, a=255)

    assert colour.as_rgb() == Rgb(r=0xFF, g=0xED, b=0x75)
    assert colour.as_rgb().as_hex() == "#ffed75"


def test_rgba_alpha_defaults_to_opaque_which_is_what_the_firmware_writes():
    assert Rgba(r=1, g=2, b=3).a == 255


@pytest.mark.parametrize(("alpha", "expected"), [(0, 0.0), (255, 1.0)])
def test_rgba_normalizes_its_alpha_onto_the_scale_compositing_wants(alpha: int, expected: float):
    assert Rgba(r=0, g=0, b=0, a=alpha).alpha_normalized == pytest.approx(expected)


@pytest.mark.parametrize("channel", ["r", "g", "b", "a"])
@pytest.mark.parametrize("value", [-1, 256])
def test_rgba_refuses_a_channel_outside_eight_bits(channel: str, value: int):
    channels: dict[str, Any] = {"r": 0, "g": 0, "b": 0, "a": 0}
    channels[channel] = value

    with pytest.raises(ValidationError):
        Rgba(**channels)


def test_rgba_is_a_separate_type_from_rgb_and_not_interchangeable_with_it():
    """Both are frozen values, and a palette ink is deliberately still the three-channel one.

    A palette says what a colour *index* means and has no opinion about coverage; this says
    what one *stroke* carried. Folding the two would put an alpha on every palette entry that
    nothing writes and nothing reads.
    """
    assert Rgba(r=1, g=2, b=3, a=255) != Rgb(r=1, g=2, b=3)
    assert Rgba(r=1, g=2, b=3) == Rgba(r=1, g=2, b=3)


@pytest.mark.parametrize("palette", [EXPORT_PALETTE, PAPER_PRO_PHYSICAL_PALETTE])
def test_shipped_palettes_resolve_every_pen_colour(palette: Palette):
    # Totality is what deletes the three silent `return (0, 0, 0)` fallbacks: `rgb` cannot miss.
    for colour in PenColor:
        assert isinstance(palette.rgb(colour), Rgb)


def test_palette_refuses_an_incomplete_mapping_and_names_what_is_missing():
    partial = {colour: Rgb(r=1, g=2, b=3) for colour in PenColor if colour is not PenColor.MAGENTA}

    with pytest.raises(ValidationError, match="has no ink for: MAGENTA"):
        Palette(name="partial", inks=partial)


def test_palette_names_every_missing_colour_in_sorted_order():
    with pytest.raises(ValidationError, match="BLACK, BLUE"):
        Palette(name="empty", inks={})


def test_palette_requires_a_name():
    with pytest.raises(ValidationError):
        Palette(name="", inks=dict(EXPORT_PALETTE.inks))


def test_palette_compares_by_value_but_is_not_hashable():
    twin = Palette(name=EXPORT_PALETTE.name, inks=dict(EXPORT_PALETTE.inks))

    assert twin == EXPORT_PALETTE
    with pytest.raises(TypeError):
        hash(EXPORT_PALETTE)


def test_a_renamed_palette_is_a_different_value():
    # The name is digested by RenderStyle.digest, so it is part of the render identity.
    renamed = Palette(name="export-2", inks=dict(EXPORT_PALETTE.inks))

    assert renamed != EXPORT_PALETTE


def test_export_palette_shares_yellows_ink_with_the_highlighter():
    assert EXPORT_PALETTE.rgb(PenColor.HIGHLIGHT) == EXPORT_PALETTE.rgb(PenColor.YELLOW)


def test_physical_palette_overrides_the_nine_measured_colours():
    measured = {
        PenColor.BLACK,
        PenColor.GRAY,
        PenColor.WHITE,
        PenColor.YELLOW,
        PenColor.GREEN,
        PenColor.BLUE,
        PenColor.RED,
        PenColor.CYAN,
        PenColor.MAGENTA,
    }

    for colour in PenColor:
        differs = PAPER_PRO_PHYSICAL_PALETTE.rgb(colour) != EXPORT_PALETTE.rgb(colour)
        assert differs is (colour in measured), colour.name


def test_physical_palette_black_is_the_measured_ink():
    assert PAPER_PRO_PHYSICAL_PALETTE.rgb(PenColor.BLACK) == Rgb(r=0x3A, g=0x48, b=0x61)


# ──────────────────────── pen tools ────────────────────────


def test_pen_type_wire_values_are_pinned():
    assert {member.name: member.value for member in PenType} == {
        "PAINTBRUSH_1": 0,
        "PENCIL_1": 1,
        "BALLPOINT_1": 2,
        "MARKER_1": 3,
        "FINELINER_1": 4,
        "HIGHLIGHTER_1": 5,
        "ERASER": 6,
        "MECHANICAL_PENCIL_1": 7,
        "ERASER_AREA": 8,
        "PAINTBRUSH_2": 12,
        "MECHANICAL_PENCIL_2": 13,
        "PENCIL_2": 14,
        "BALLPOINT_2": 15,
        "MARKER_2": 16,
        "FINELINER_2": 17,
        "HIGHLIGHTER_2": 18,
        "CALLIGRAPHY": 21,
        "SHADER": 23,
    }


@pytest.mark.parametrize(
    ("row_two", "row_one"),
    [
        (PenType.PAINTBRUSH_2, PenType.PAINTBRUSH_1),
        (PenType.MECHANICAL_PENCIL_2, PenType.MECHANICAL_PENCIL_1),
        (PenType.PENCIL_2, PenType.PENCIL_1),
        (PenType.BALLPOINT_2, PenType.BALLPOINT_1),
        (PenType.MARKER_2, PenType.MARKER_1),
        (PenType.FINELINER_2, PenType.FINELINER_1),
        (PenType.HIGHLIGHTER_2, PenType.HIGHLIGHTER_1),
    ],
)
def test_canonical_folds_the_second_toolbar_row_onto_the_first(row_two: PenType, row_one: PenType):
    assert row_two.canonical is row_one


def test_canonical_is_the_identity_for_a_tool_with_no_twin():
    for pen in (PenType.ERASER, PenType.ERASER_AREA, PenType.CALLIGRAPHY, PenType.SHADER):
        assert pen.canonical is pen


def test_canonical_is_idempotent():
    for pen in PenType:
        assert pen.canonical.canonical is pen.canonical


def test_only_the_two_erasers_remove_ink():
    erasers = {pen for pen in PenType if pen.is_eraser}

    assert erasers == {PenType.ERASER, PenType.ERASER_AREA}


def test_both_toolbar_rows_highlighters_are_highlighters_and_nothing_else_is():
    highlighters = {pen for pen in PenType if pen.is_highlighter}

    assert highlighters == {PenType.HIGHLIGHTER_1, PenType.HIGHLIGHTER_2}


def test_pen_from_wire_knows_every_member():
    for pen in PenType:
        assert pen_from_wire(pen.value) is pen


@pytest.mark.parametrize("value", [9, 10, 11, 19, 20, 22, 24, 99, -1, 0xFFFF])
def test_pen_from_wire_reports_an_unknown_tool_id_rather_than_raising(value: int):
    # A codec must not have to catch ValueError from PenType(value) to learn whether it may
    # build a Stroke; on None it substitutes and records UNKNOWN_PEN_SUBSTITUTED.
    assert pen_from_wire(value) is None


# ──────────────────────── screen geometry ────────────────────────


def test_shipped_screens_carry_the_geometry_the_devices_have():
    assert (RM2_SCREEN.width, RM2_SCREEN.height, RM2_SCREEN.dpi) == (1404, 1872, 226)
    assert (PAPER_PRO_SCREEN.width, PAPER_PRO_SCREEN.height, PAPER_PRO_SCREEN.dpi) == (
        1620,
        2160,
        229,
    )


def test_screen_converts_pixels_to_millimetres():
    screen = ScreenSpec(name="test", width=229, height=458, dpi=229)

    assert screen.width_mm == pytest.approx(25.4)
    assert screen.height_mm == pytest.approx(50.8)


def test_screen_reports_the_shift_that_moves_centre_origin_coordinates_into_the_page():
    assert PAPER_PRO_SCREEN.x_shift == 810.0
    assert RM2_SCREEN.x_shift == 702.0


@given(
    width=st.integers(min_value=1, max_value=10_000),
    height=st.integers(min_value=1, max_value=10_000),
    dpi=st.integers(min_value=1, max_value=2_000),
)
def test_screen_millimetres_scale_with_pixels_at_a_fixed_density(
    width: int, height: int, dpi: int
):
    screen = ScreenSpec(name="s", width=width, height=height, dpi=dpi)

    assert screen.width_mm == pytest.approx(width / dpi * 25.4)
    assert screen.x_shift * 2 == width


@pytest.mark.parametrize("field", ["width", "height", "dpi"])
@pytest.mark.parametrize("value", [0, -1])
def test_screen_refuses_a_non_positive_dimension(field: str, value: int):
    fields: dict[str, Any] = {"name": "s", "width": 10, "height": 10, "dpi": 10}
    fields[field] = value

    with pytest.raises(ValidationError):
        ScreenSpec(**fields)


def test_screen_requires_a_name():
    with pytest.raises(ValidationError):
        ScreenSpec(name="", width=10, height=10, dpi=10)


# ──────────────────────── strokes and stylus samples ────────────────────────


def test_point_defaults_every_sensor_channel_to_zero():
    point = Point(x=1.5, y=-2.5)

    assert (point.speed, point.direction, point.width, point.pressure) == (0, 0, 0, 0)
    assert point.pressure_normalized == 0.0
    assert point.direction_radians == 0.0


def test_point_normalises_pressure_onto_a_unit_scale():
    assert Point(x=0, y=0, pressure=255).pressure_normalized == 1.0
    assert Point(x=0, y=0, pressure=128).pressure_normalized == pytest.approx(128 / 255)


def test_point_maps_the_direction_byte_onto_a_full_turn():
    # Full scale is 255, not 256: the top of the uint8 range is a whole turn exactly, and a
    # quarter turn therefore lands between two representable bytes.
    assert Point(x=0, y=0, direction=64).direction_radians == pytest.approx(64 * math.tau / 255)
    assert Point(x=0, y=0, direction=255).direction_radians == pytest.approx(math.tau)
    assert Point(x=0, y=0, direction=128).direction_radians > math.pi


@given(
    pressure=st.integers(min_value=0, max_value=255),
    direction=st.integers(min_value=0, max_value=255),
)
def test_point_normalisations_stay_inside_their_declared_ranges(pressure: int, direction: int):
    point = Point(x=0.0, y=0.0, pressure=pressure, direction=direction)

    assert 0.0 <= point.pressure_normalized <= 1.0
    assert 0.0 <= point.direction_radians <= math.tau


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speed", 65536),
        ("speed", -1),
        ("direction", 256),
        ("direction", -1),
        ("width", 65536),
        ("width", -1),
        ("pressure", 256),
        ("pressure", -1),
    ],
)
def test_point_refuses_a_sample_the_wire_format_cannot_encode(field: str, value: int):
    with pytest.raises(ValidationError):
        Point(x=0, y=0, **{field: value})


@pytest.mark.parametrize(("field", "value"), [("speed", 65535), ("pressure", 255)])
def test_point_accepts_a_sample_at_full_scale(field: str, value: int):
    assert getattr(Point(x=0, y=0, **{field: value}), field) == value


def test_a_stroke_with_no_samples_is_a_tap_and_reports_no_extent():
    # None rather than a zero box: (0, 0, 0, 0) is a real extent for a tap at the origin, and
    # folding the two together is what pinned every legacy page's extent to the origin.
    assert make_stroke().bounding_box is None


def test_stroke_bounding_box_spans_its_samples():
    stroke = make_stroke(points=(make_point(3, -1), make_point(-2, 7), make_point(1, 1)))

    assert stroke.bounding_box == (-2.0, -1.0, 3.0, 7.0)


def test_a_single_sample_stroke_reports_a_degenerate_box():
    assert make_stroke(points=(make_point(5, 6),)).bounding_box == (5.0, 6.0, 5.0, 6.0)


def test_stroke_stores_its_samples_as_a_tuple():
    stroke = Stroke.model_validate(
        {
            "pen": PenType.PENCIL_1,
            "color": PenColor.RED,
            "thickness_scale": 1.0,
            "points": [make_point(0, 0)],
        }
    )

    assert isinstance(stroke.points, tuple)


@pytest.mark.parametrize("field", ["thickness_scale", "starting_length"])
def test_stroke_refuses_a_negative_length(field: str):
    fields: dict[str, Any] = {
        "pen": PenType.MARKER_1,
        "color": PenColor.BLUE,
        "thickness_scale": 1.0,
    }
    fields[field] = -0.1

    with pytest.raises(ValidationError):
        Stroke(**fields)


def test_stroke_classification_is_read_off_its_pen():
    assert make_stroke(pen=PenType.ERASER_AREA).pen.is_eraser is True
    assert make_stroke(pen=PenType.HIGHLIGHTER_2).pen.is_highlighter is True


def test_a_stroke_carries_no_colour_override_unless_the_wire_gave_it_one():
    # None is not "black": it is "the colour index is the whole truth", which is every pen.
    assert make_stroke().color_override is None


def test_two_strokes_of_one_colour_index_can_carry_two_different_colours():
    """The state that makes the highlighter renderable, expressed in the domain alone.

    Both strokes report ``PenColor.HIGHLIGHT``, because that is what the firmware writes for
    every highlight whatever colour it was drawn in. The override is what keeps them apart,
    and it is additional to the index rather than a replacement for it.
    """
    yellow = Stroke(
        pen=PenType.HIGHLIGHTER_2,
        color=PenColor.HIGHLIGHT,
        color_override=Rgba(r=0xFF, g=0xED, b=0x75),
        thickness_scale=2.0,
    )
    blue = yellow.model_copy(update={"color_override": Rgba(r=0xBE, g=0xEA, b=0xFE)})

    assert yellow.color is blue.color is PenColor.HIGHLIGHT
    assert yellow.color_override != blue.color_override
    assert yellow != blue


# ──────────────────────── pages ────────────────────────


def test_page_defect_codes_are_the_closed_survivable_set():
    assert {member.value for member in PageDefectCode} == {
        "artifact_absent",
        "block_bytes_unread",
        "content_undecodable",
        "item_dropped",
        "layer_synthesised",
        "unknown_pen_substituted",
        "unknown_color_substituted",
    }


def test_a_defect_must_carry_human_detail():
    with pytest.raises(ValidationError):
        PageDefect(code=PageDefectCode.ITEM_DROPPED, detail="")


def test_text_block_defaults_to_empty_text():
    block = TextBlock(pos_x=1.0, pos_y=2.0, width=100.0)

    assert block.text == ""


@pytest.mark.parametrize("width", [0.0, -1.0])
def test_text_block_refuses_a_width_that_draws_nothing(width: float):
    # A codec meeting such an item must skip it and record ITEM_DROPPED, not let this escape.
    with pytest.raises(ValidationError):
        TextBlock(pos_x=0.0, pos_y=0.0, width=width)


def test_a_layer_defaults_to_visible_and_empty():
    layer = Layer()

    assert layer.visible is True
    assert layer.name == ""
    assert layer.is_empty is True


@pytest.mark.parametrize(
    "layer",
    [
        Layer(strokes=(make_stroke(),)),
        Layer(text_blocks=(TextBlock(pos_x=0.0, pos_y=0.0, width=10.0),)),
    ],
)
def test_a_layer_holding_anything_is_not_empty(layer: Layer):
    assert layer.is_empty is False


def test_page_content_with_no_layers_and_no_defects_means_a_genuinely_blank_page():
    content = PageContent()

    assert content.is_blank is True
    assert content.stroke_count == 0
    assert content.visible_layers == ()
    assert content.bounding_box is None
    assert content.defects == ()


def test_page_content_skips_hidden_layers_in_every_aggregate():
    hidden = Layer(
        name="hidden", visible=False, strokes=(make_stroke(points=(make_point(99, 99),)),)
    )
    shown = Layer(name="shown", strokes=(make_stroke(points=(make_point(1, 2),)),))
    content = PageContent(layers=(hidden, shown))

    assert content.visible_layers == (shown,)
    assert content.stroke_count == 1
    assert content.is_blank is False
    assert content.bounding_box == (1.0, 2.0, 1.0, 2.0)


def test_a_page_whose_only_content_is_hidden_reads_as_blank():
    hidden = Layer(visible=False, strokes=(make_stroke(points=(make_point(0, 0),)),))
    content = PageContent(layers=(hidden,))

    assert content.is_blank is True
    assert content.stroke_count == 0
    assert content.bounding_box is None


def test_a_page_whose_only_content_is_typed_text_is_not_blank():
    # The page-level tuple, which a codec fills from the one text block a page owns. It is
    # not on any layer, so no layer aggregate can see it and `is_blank` has to.
    content = PageContent(text_blocks=(TextBlock(pos_x=0.0, pos_y=0.0, width=400.0, text="hi"),))

    assert content.is_blank is False
    assert content.layers == ()
    assert content.stroke_count == 0, "typed text is not ink"
    assert content.bounding_box is None, "how tall text draws depends on the font"


def test_page_level_text_is_not_gated_by_layer_visibility():
    # The block carrying it has no visible flag, so hiding every layer cannot hide it.
    hidden = Layer(visible=False, strokes=(make_stroke(points=(make_point(0, 0),)),))
    content = PageContent(
        layers=(hidden,),
        text_blocks=(TextBlock(pos_x=0.0, pos_y=0.0, width=400.0),),
    )

    assert content.visible_layers == ()
    assert content.is_blank is False


def test_page_content_bounding_box_folds_every_visible_stroke():
    content = PageContent(
        layers=(
            Layer(strokes=(make_stroke(points=(make_point(-5, 0), make_point(0, 3))),)),
            Layer(strokes=(make_stroke(points=(make_point(10, -7),)), make_stroke())),
        )
    )

    assert content.bounding_box == (-5.0, -7.0, 10.0, 3.0)


def test_page_content_ignores_point_less_strokes_when_folding_an_extent():
    # The legacy defect: a tap-only stroke returned (0, 0, 0, 0), which pinned the extent to the
    # origin even when no ink was near it.
    content = PageContent(
        layers=(Layer(strokes=(make_stroke(), make_stroke(points=(make_point(50, 60),)))),)
    )

    assert content.bounding_box == (50.0, 60.0, 50.0, 60.0)


def test_page_content_counts_strokes_across_visible_layers():
    layer = Layer(strokes=(make_stroke(), make_stroke()))
    content = PageContent(layers=(layer, layer))

    assert content.stroke_count == 4


def test_a_readable_page_reports_its_content():
    page = make_page(content=PageContent(layers=(Layer(strokes=(make_stroke(),)),)))

    assert page.is_readable is True
    assert page.stroke_count == 1
    assert page.ref == "page-1"
    assert page.all_defects == ()


def test_a_contentless_page_must_say_why():
    # "unreadable" can never be mistaken for "blank": the state is unconstructible.
    with pytest.raises(ValidationError, match="no content and no defect explaining it"):
        Page(page_id=PageId(uuid="p"), index=0, content=None)


def test_the_rejection_message_names_the_two_codes_that_would_explain_it():
    with pytest.raises(ValidationError, match="artifact_absent"):
        Page(page_id=PageId(uuid="p"), index=0)


@pytest.mark.parametrize(
    "code", [PageDefectCode.ARTIFACT_ABSENT, PageDefectCode.CONTENT_UNDECODABLE]
)
def test_a_contentless_page_with_a_defect_is_constructible(code: PageDefectCode):
    page = make_page(content=None, defects=(PageDefect(code=code, detail="no scene file"),))

    assert page.is_readable is False
    assert page.stroke_count == 0
    assert len(page.all_defects) == 1


def test_page_defects_are_reported_page_level_first():
    page_level = PageDefect(code=PageDefectCode.ITEM_DROPPED, detail="page")
    content_level = PageDefect(code=PageDefectCode.LAYER_SYNTHESISED, detail="content")
    page = make_page(content=PageContent(defects=(content_level,)), defects=(page_level,))

    assert page.all_defects == (page_level, content_level)


def test_page_defaults_leave_the_template_and_the_pdf_index_unrecorded():
    page = make_page()

    assert page.template_name is None
    assert page.pdf_page_index is None


def test_a_template_named_blank_is_a_real_template():
    # One spelling of "no template": None. "Blank" means the store really recorded that name.
    page = Page(page_id=PageId(uuid="p"), index=0, template_name="Blank", content=PageContent())

    assert page.template_name == "Blank"


@pytest.mark.parametrize(("field", "value"), [("index", -1), ("pdf_page_index", -1)])
def test_page_refuses_a_negative_position(field: str, value: int):
    fields: dict[str, Any] = {
        "page_id": PageId(uuid="p"),
        "index": 0,
        "content": PageContent(),
    }
    fields[field] = value

    with pytest.raises(ValidationError):
        Page(**fields)


def test_page_accepts_a_pdf_page_index_of_zero():
    page = Page(page_id=PageId(uuid="p"), index=0, pdf_page_index=0, content=PageContent())

    assert page.pdf_page_index == 0


# ──────────────────────── document enums and layout ────────────────────────


def test_document_kind_tells_folders_from_documents():
    assert {member.value for member in DocumentKind} == {"document", "collection"}


def test_source_kind_covers_the_three_things_a_document_is_made_from():
    assert {member.value for member in SourceKind} == {"notebook", "pdf", "epub"}


def test_page_orientation_is_closed_so_a_renderer_can_exhaustively_match():
    assert {member.value for member in PageOrientation} == {"portrait", "landscape"}


def test_extra_metadata_defaults_to_nothing_recorded():
    extra = ExtraMetadata()

    assert (extra.last_tool, extra.last_pen, extra.tool_settings) == ("", "", {})


def test_extra_metadata_lifts_the_two_named_tools_and_keeps_the_whole_bag():
    extra = ExtraMetadata.from_json(
        {"LastTool": "Fineliner", "LastPen": "Fineliner", "FinelinerV2Size": "2"}
    )

    assert extra.last_tool == "Fineliner"
    assert extra.last_pen == "Fineliner"
    assert extra.tool_settings["FinelinerV2Size"] == "2"
    assert "LastTool" in extra.tool_settings


def test_extra_metadata_from_an_empty_object_equals_the_default():
    assert ExtraMetadata.from_json({}) == ExtraMetadata()


def test_layout_decode_supplies_every_default_from_an_empty_sidecar():
    layout = DocumentLayout.decode(b"{}")

    assert layout.format_version == 2
    assert layout.orientation is PageOrientation.PORTRAIT
    assert layout.margins == 125
    assert layout.font_name == ""
    assert layout.line_height == -1
    assert layout.text_scale == 1.0
    assert layout.text_alignment == "justify"
    assert layout.zoom_mode == "bestFit"
    assert layout.custom_zoom_scale == 1.0
    assert layout.extra_metadata == ExtraMetadata()


def test_layout_decode_reads_the_stores_spellings():
    layout = DocumentLayout.decode(
        b"""{
            "formatVersion": 2,
            "orientation": "landscape",
            "margins": 180,
            "fontName": "EBGaramond",
            "lineHeight": 150,
            "textScale": 1.25,
            "textAlignment": "left",
            "zoomMode": "customFit",
            "customZoomScale": 2.5,
            "extraMetadata": {"LastTool": "Ballpointv2", "LastPen": "Ballpointv2"}
        }"""
    )

    assert layout.orientation is PageOrientation.LANDSCAPE
    assert layout.margins == 180
    assert layout.font_name == "EBGaramond"
    assert layout.line_height == 150
    assert layout.text_scale == 1.25
    assert layout.text_alignment == "left"
    assert layout.zoom_mode == "customFit"
    assert layout.custom_zoom_scale == 2.5
    assert layout.extra_metadata.last_tool == "Ballpointv2"


def test_layout_tolerates_the_stores_quoted_numbers():
    layout = DocumentLayout.from_json({"margins": "180", "textScale": "1.5", "lineHeight": "-1"})

    assert (layout.margins, layout.text_scale, layout.line_height) == (180, 1.5, -1)


def test_layout_keeps_the_stores_automatic_line_height_sentinel():
    # Unbounded below on purpose: -1 is the store's spelling of "automatic".
    assert DocumentLayout.from_json({"lineHeight": -1}).line_height == -1


def test_layout_refuses_an_orientation_this_domain_does_not_know():
    with pytest.raises(ValueError, match="unknown orientation 'sideways'"):
        DocumentLayout.from_json({"orientation": "sideways"})


def test_layout_refuses_a_negative_format_version():
    with pytest.raises(ValidationError):
        DocumentLayout.from_json({"formatVersion": -1})


def test_layout_refuses_a_negative_margin():
    with pytest.raises(ValidationError):
        DocumentLayout.from_json({"margins": -1})


def test_layout_decode_refuses_a_payload_that_is_not_a_json_object():
    with pytest.raises(TypeError, match="expected a json object"):
        DocumentLayout.decode(b"[]")


def test_layout_decode_refuses_a_payload_that_is_not_json():
    with pytest.raises(ValueError, match="Expecting value"):
        DocumentLayout.decode(b"<xml/>")


def test_layout_refuses_tool_settings_that_are_not_an_object():
    with pytest.raises(TypeError, match="tool settings"):
        DocumentLayout.from_json({"extraMetadata": ["LastTool"]})


def test_layout_ignores_keys_it_does_not_know():
    # A firmware that adds a cosmetic key must not turn into a decode failure.
    assert DocumentLayout.from_json({"someNewKey": {"nested": True}}) == DocumentLayout()


# ──────────────────────── wire spelling readers ────────────────────────


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [("DocumentType", DocumentKind.DOCUMENT), ("CollectionType", DocumentKind.COLLECTION)],
)
def test_kind_reader_maps_the_two_spellings_the_store_writes(
    spelling: str, expected: DocumentKind
):
    assert domain_models._kind_from_wire(spelling) is expected


def test_kind_reader_defaults_an_absent_type_to_a_document():
    assert domain_models._kind_from_wire(None) is DocumentKind.DOCUMENT


def test_kind_reader_refuses_an_unknown_spelling():
    with pytest.raises(ValueError, match="unknown document type 'TemplateType'"):
        domain_models._kind_from_wire("TemplateType")


def test_source_reader_reports_nothing_when_the_sidecar_did_not_say():
    assert domain_models._source_from_wire(None) is None


@pytest.mark.parametrize("member", list(SourceKind))
def test_source_reader_maps_every_spelling_the_store_writes(member: SourceKind):
    assert domain_models._source_from_wire(member.value) is member


@pytest.mark.parametrize("spelling", ["", "PDF", "doc"])
def test_source_reader_refuses_an_unknown_spelling(spelling: str):
    with pytest.raises(ValueError, match="unknown file type"):
        domain_models._source_from_wire(spelling)


def test_orientation_reader_defaults_an_absent_orientation_to_portrait():
    assert domain_models._orientation_from_wire(None) is PageOrientation.PORTRAIT


@pytest.mark.parametrize("member", list(PageOrientation))
def test_orientation_reader_maps_every_spelling_the_store_writes(member: PageOrientation):
    assert domain_models._orientation_from_wire(member.value) is member


# ──────────────────────── document metadata ────────────────────────


def test_metadata_decode_reads_a_metadata_sidecar_alone():
    metadata = DocumentMetadata.decode(
        b"""{
            "visibleName": "Design notes",
            "type": "DocumentType",
            "parent": "3f2a",
            "deleted": false,
            "pinned": true,
            "lastModified": "1700000000000",
            "lastOpened": "1700000001000",
            "lastOpenedPage": 3,
            "version": 12,
            "synced": true
        }"""
    )

    assert metadata.visible_name == "Design notes"
    assert metadata.kind is DocumentKind.DOCUMENT
    assert metadata.parent_uuid == "3f2a"
    assert metadata.trashed is False
    assert metadata.pinned is True
    assert metadata.last_modified == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert metadata.last_opened == datetime.fromtimestamp(1_700_000_001, tz=UTC)
    assert metadata.last_opened_page == 3
    assert metadata.version == 12
    assert metadata.synced is True


def test_metadata_without_a_content_sidecar_admits_it_does_not_know_the_source():
    # Not defaulted to NOTEBOOK: a pdf silently reported as a notebook is an export with no
    # background and no defect recorded anywhere.
    metadata = DocumentMetadata.decode(b'{"visibleName": "x", "type": "DocumentType"}')

    assert metadata.source is None
    assert metadata.layout is None


def test_metadata_decode_reads_the_content_sidecar_when_it_is_handed_one():
    metadata = DocumentMetadata.decode(
        b'{"visibleName": "Paper", "type": "DocumentType"}',
        content=b'{"fileType": "pdf", "orientation": "landscape"}',
    )

    assert metadata.source is SourceKind.PDF
    assert metadata.layout is not None
    assert metadata.layout.orientation is PageOrientation.LANDSCAPE


def test_metadata_defaults_every_unrecorded_field():
    metadata = DocumentMetadata.decode(b"{}")

    assert metadata.visible_name == ""
    assert metadata.kind is DocumentKind.DOCUMENT
    assert metadata.parent_uuid is None
    assert metadata.trashed is False
    assert metadata.pinned is False
    assert metadata.last_modified is None
    assert metadata.last_opened is None
    assert metadata.last_opened_page == 0
    assert metadata.version == 0
    assert metadata.synced is False


def test_the_root_is_no_parent_rather_than_an_empty_string():
    assert DocumentMetadata.from_json({"parent": ""}).parent_uuid is None


@pytest.mark.parametrize(
    "data",
    [
        {"parent": "trash"},
        {"deleted": True},
        {"deleted": "true"},
        {"parent": "trash", "deleted": False},
    ],
)
def test_either_legacy_trash_spelling_alone_means_trashed(data: dict[str, object]):
    # The or of both spellings, defined once here so no two adapters can choose differently.
    assert DocumentMetadata.from_json(data).trashed is True


def test_a_trashed_entry_reports_no_parent_folder():
    metadata = DocumentMetadata.from_json({"parent": "trash"})

    assert metadata.trashed is True
    assert metadata.parent_uuid is None


def test_a_live_entry_in_a_folder_is_not_trashed():
    metadata = DocumentMetadata.from_json({"parent": "3f2a", "deleted": False})

    assert metadata.trashed is False
    assert metadata.parent_uuid == "3f2a"


def test_metadata_refuses_an_unknown_document_type():
    with pytest.raises(ValueError, match="unknown document type"):
        DocumentMetadata.from_json({"type": "TemplateType"})


def test_metadata_refuses_an_unknown_file_type_in_the_content_sidecar():
    with pytest.raises(ValueError, match="unknown file type"):
        DocumentMetadata.from_json({}, content={"fileType": "djvu"})


def test_metadata_refuses_a_visible_name_that_is_not_a_string():
    with pytest.raises(TypeError, match="expected a json string"):
        DocumentMetadata.from_json({"visibleName": ["Notes"]})


def test_metadata_refuses_a_negative_version():
    with pytest.raises(ValidationError):
        DocumentMetadata.from_json({"version": -1})


def test_metadata_refuses_a_negative_last_opened_page():
    with pytest.raises(ValidationError):
        DocumentMetadata.from_json({"lastOpenedPage": -1})


def test_metadata_decode_refuses_a_content_payload_that_is_not_a_json_object():
    with pytest.raises(TypeError, match="expected a json object"):
        DocumentMetadata.decode(b"{}", content=b"[]")


def test_metadata_timestamps_are_timezone_aware():
    # A naive datetime compares wrongly against anything read from another store.
    metadata = DocumentMetadata.from_json({"lastModified": "1700000000000"})

    assert metadata.last_modified is not None
    assert metadata.last_modified.utcoffset() is not None


def test_metadata_refuses_a_naive_timestamp_supplied_directly():
    with pytest.raises(ValidationError):
        make_metadata(last_modified=NAIVE_MOMENT)


def test_metadata_treats_an_empty_timestamp_string_as_unrecorded():
    assert DocumentMetadata.from_json({"lastModified": ""}).last_modified is None


# ──────────────────────── documents ────────────────────────


def test_a_summary_counts_the_pages_it_lists():
    summary = DocumentSummary(
        doc_id=DocumentId(uuid="doc"),
        metadata=make_metadata(),
        pages=(PageId(uuid="p1"), PageId(uuid="p2")),
    )

    assert summary.page_count == 2


def test_a_folder_summary_lists_no_pages():
    summary = DocumentSummary(
        doc_id=DocumentId(uuid="folder"),
        metadata=make_metadata(kind=DocumentKind.COLLECTION),
        pages=(),
    )

    assert summary.page_count == 0


def test_a_document_holds_its_pages_in_document_order():
    document = Document(
        doc_id=DocumentId(uuid="doc"),
        metadata=make_metadata(),
        pages=(make_page(uuid="p0", index=0), make_page(uuid="p1", index=1)),
    )

    assert [page.index for page in document.pages] == [0, 1]


def test_a_document_refuses_pages_whose_index_disagrees_with_their_position():
    # The order *is* the addressing scheme: page_index in the sync store, last_opened_page in the
    # metadata and the pdf background index are all positions in this tuple.
    with pytest.raises(ValidationError, match="claims index 1 at position 0"):
        Document(
            doc_id=DocumentId(uuid="doc"),
            metadata=make_metadata(),
            pages=(make_page(uuid="p1", index=1),),
        )


def test_a_document_refuses_transposed_pages_and_names_every_one():
    with pytest.raises(ValidationError, match="not in document order"):
        Document(
            doc_id=DocumentId(uuid="doc"),
            metadata=make_metadata(),
            pages=(make_page(uuid="p1", index=1), make_page(uuid="p0", index=0)),
        )


def test_an_empty_document_is_in_order():
    document = Document(doc_id=DocumentId(uuid="doc"), metadata=make_metadata())

    assert document.pages == ()
    assert document.defective_pages == ()


def test_a_document_can_produce_the_summary_a_listing_would_show():
    pages = (make_page(uuid="p0", index=0), make_page(uuid="p1", index=1))
    document = Document(doc_id=DocumentId(uuid="doc"), metadata=make_metadata(), pages=pages)

    assert document.summary == DocumentSummary(
        doc_id=document.doc_id,
        metadata=document.metadata,
        pages=(PageId(uuid="p0"), PageId(uuid="p1")),
    )


def test_a_document_reports_only_the_pages_that_came_back_wrong():
    absent = PageDefect(code=PageDefectCode.ARTIFACT_ABSENT, detail="gone")
    clean = make_page(uuid="p0", index=0)
    broken = make_page(uuid="p1", index=1, content=None, defects=(absent,))
    substituted = make_page(
        uuid="p2",
        index=2,
        content=PageContent(
            defects=(PageDefect(code=PageDefectCode.UNKNOWN_PEN_SUBSTITUTED, detail="id 99"),)
        ),
    )
    document = Document(
        doc_id=DocumentId(uuid="doc"),
        metadata=make_metadata(),
        pages=(clean, broken, substituted),
    )

    assert document.defective_pages == (broken, substituted)


def test_the_serialized_document_no_longer_duplicates_facts_from_its_metadata():
    # The six legacy computed fields are gone; every fact they restated is still dumped.
    document = Document(doc_id=DocumentId(uuid="doc"), metadata=make_metadata())
    dumped = document.model_dump()

    assert set(dumped) == {"doc_id", "metadata", "pages"}
    assert not {"name", "is_notebook", "is_pdf", "is_epub", "is_folder", "is_trashed"} & set(
        dumped
    )
    assert dumped["metadata"]["visible_name"] == "Notes"


def test_a_document_round_trips_through_its_serialized_form():
    document = Document(
        doc_id=DocumentId(uuid="doc"),
        metadata=make_metadata(source=SourceKind.PDF, last_modified=MOMENT),
        pages=(make_page(uuid="p0", index=0),),
    )

    assert Document.model_validate(document.model_dump()) == document


# ──────────────────────── the tracked mirror ────────────────────────


def test_a_mirror_row_records_when_it_was_written():
    row = SyncedDocument(
        uuid="doc",
        visible_name="Notes",
        kind=DocumentKind.DOCUMENT,
        page_count=3,
        synced_at=MOMENT,
    )

    assert row.synced_at == MOMENT
    assert row.source is SourceKind.NOTEBOOK
    assert row.parent_uuid is None
    assert row.metadata_hash is None
    assert row.content_hash is None
    assert row.device_last_modified is None


def test_a_mirror_row_cannot_be_written_without_an_age():
    with pytest.raises(ValidationError):
        SyncedDocument.model_validate(
            {
                "uuid": "doc",
                "visible_name": "Notes",
                "kind": DocumentKind.DOCUMENT,
                "page_count": 0,
            }
        )


def test_a_mirror_row_refuses_a_naive_timestamp():
    with pytest.raises(ValidationError):
        SyncedDocument(
            uuid="doc",
            visible_name="Notes",
            kind=DocumentKind.DOCUMENT,
            page_count=0,
            synced_at=NAIVE_MOMENT,
        )


@pytest.mark.parametrize(("field", "value"), [("uuid", ""), ("page_count", -1)])
def test_a_mirror_row_refuses_an_unusable_component(field: str, value: object):
    fields: dict[str, Any] = {
        "uuid": "doc",
        "visible_name": "Notes",
        "kind": DocumentKind.DOCUMENT,
        "page_count": 0,
        "synced_at": MOMENT,
    }
    fields[field] = value

    with pytest.raises(ValidationError):
        SyncedDocument(**fields)


def test_a_mirror_page_carries_the_fingerprint_that_decides_change():
    page = SyncedPage(
        doc_uuid="doc",
        page_uuid="page",
        page_index=0,
        rm_hash="a" * 64,
        rm_size_bytes=1024,
        synced_at=MOMENT,
    )

    assert page.rm_hash == "a" * 64
    assert page.rm_size_bytes == 1024


def test_a_mirror_page_with_no_scene_artifact_has_nothing_to_hash():
    page = SyncedPage(doc_uuid="doc", page_uuid="page", page_index=0, synced_at=MOMENT)

    assert page.rm_hash is None
    assert page.rm_size_bytes is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("doc_uuid", ""), ("page_uuid", ""), ("page_index", -1), ("rm_size_bytes", -1)],
)
def test_a_mirror_page_refuses_an_unusable_component(field: str, value: object):
    fields: dict[str, Any] = {
        "doc_uuid": "doc",
        "page_uuid": "page",
        "page_index": 0,
        "synced_at": MOMENT,
    }
    fields[field] = value

    with pytest.raises(ValidationError):
        SyncedPage(**fields)


# ──────────────────────── extracted text ────────────────────────


def test_provenance_from_a_legacy_sidecar_import_is_representable():
    # An honest state: the .ocr.txt migration genuinely does not know how its text was produced.
    provenance = make_provenance()

    assert provenance.recognizers == ()
    assert provenance.model_fingerprint is None
    assert provenance.render_dpi is None


def test_provenance_records_which_engines_read_the_page_in_fold_order():
    provenance = make_provenance(
        recognizers=("apple-vision", "aws-textract"), model_fingerprint="opus", render_dpi=300
    )

    assert provenance.recognizers == ("apple-vision", "aws-textract")
    assert provenance.render_dpi == 300


@pytest.mark.parametrize("dpi", [0, -1])
def test_provenance_refuses_a_non_positive_render_dpi(dpi: int):
    with pytest.raises(ValidationError):
        make_provenance(render_dpi=dpi)


def test_provenance_requires_an_aware_extraction_time():
    with pytest.raises(ValidationError):
        make_provenance(extracted_at=NAIVE_MOMENT)


def test_page_text_carries_the_reading_and_its_provenance():
    text = PageText(
        doc_uuid="doc",
        page_uuid="page",
        page_index=2,
        text="hello",
        provenance=make_provenance(model_fingerprint="opus"),
    )

    assert text.text == "hello"
    assert text.provenance.model_fingerprint == "opus"


def test_a_blank_page_is_recorded_with_a_provenance_that_names_no_model():
    text = PageText(
        doc_uuid="doc", page_uuid="page", page_index=0, text="", provenance=make_provenance()
    )

    assert text.text == ""


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_may_not_claim_a_merging_model(text: str):
    # A merged reading with no text means the merge failed and was written down as a success.
    with pytest.raises(ValidationError, match="claims model"):
        PageText(
            doc_uuid="doc",
            page_uuid="page",
            page_index=0,
            text=text,
            provenance=make_provenance(model_fingerprint="opus"),
        )


@pytest.mark.parametrize(
    ("field", "value"), [("doc_uuid", ""), ("page_uuid", ""), ("page_index", -1)]
)
def test_page_text_refuses_an_unusable_component(field: str, value: object):
    fields: dict[str, Any] = {
        "doc_uuid": "doc",
        "page_uuid": "page",
        "page_index": 0,
        "text": "x",
        "provenance": make_provenance(),
    }
    fields[field] = value

    with pytest.raises(ValidationError):
        PageText(**fields)


# ──────────────────────── cache keys: defect 3 as an invariant ────────────────────────

#: Printable ASCII *plus* the two bytes the old digest scheme folded components on. Under
#: separators this alphabet would have made the properties below flaky-by-construction, which is
#: why it used to exclude them; under length framing a component may contain anything, and
#: including them is what makes these properties assert that.
UNIT_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x1e"
COMPONENT = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126)
    | st.sampled_from([UNIT_SEPARATOR, RECORD_SEPARATOR]),
    min_size=1,
    max_size=12,
)
RECOGNIZER_LIST = st.lists(COMPONENT, max_size=3).map(tuple)


@st.composite
def ocr_key_parts(draw: st.DrawFn) -> dict[str, Any]:
    return {
        "page_hash": draw(COMPONENT),
        "render_digest": draw(COMPONENT),
        "raster_digest": draw(COMPONENT),
        "recognizers": draw(RECOGNIZER_LIST),
        "model_fingerprint": draw(COMPONENT),
        "request_digest": draw(COMPONENT),
    }


@st.composite
def diagram_key_parts(draw: st.DrawFn) -> dict[str, Any]:
    return {
        "page_hash": draw(COMPONENT),
        "render_digest": draw(COMPONENT),
        "raster_digest": draw(COMPONENT),
        "model_fingerprint": draw(COMPONENT),
        "request_digest": draw(COMPONENT),
    }


def test_ocr_cache_key_digest_body_is_pinned():
    assert OcrCacheKey(**ocr_parts()).digest == OCR_DIGEST_GOLDEN


def test_diagram_cache_key_digest_body_is_pinned():
    assert DiagramCacheKey(**diagram_parts()).digest == DIAGRAM_DIGEST_GOLDEN


@pytest.mark.parametrize("key", [OcrCacheKey(**ocr_parts()), DiagramCacheKey(**diagram_parts())])
def test_a_cache_key_digest_is_lowercase_hex_sha256(key: OcrCacheKey | DiagramCacheKey):
    digest = key.digest

    assert len(digest) == 64
    assert digest == digest.lower()
    assert set(digest) <= set("0123456789abcdef")


@pytest.mark.parametrize("key", [OcrCacheKey(**ocr_parts()), DiagramCacheKey(**diagram_parts())])
def test_a_cache_key_digest_is_a_pure_function_of_its_components(
    key: OcrCacheKey | DiagramCacheKey,
):
    # `digest` is a property, not a field: a fake cache and the SQLite cache agree by
    # construction rather than because a caller passed the same string to both.
    recomputed = key.digest
    rebuilt = type(key).model_validate(key.model_dump())

    assert recomputed == key.digest
    assert rebuilt.digest == key.digest
    assert "digest" not in key.model_dump()


def test_the_two_key_types_do_not_collide_on_shared_components():
    ocr = OcrCacheKey(**diagram_parts(recognizers=()))
    diagram = DiagramCacheKey(**diagram_parts())

    assert ocr.digest != diagram.digest


def test_raster_identity_ignores_the_page_hash_and_nothing_else():
    # The measured case: the tablet rewrote one page from 18,813 to 24,534 bytes with the ink
    # unchanged, which moves `page_hash` and must leave the identity of the work alone.
    rewritten = OcrCacheKey(**ocr_parts(page_hash="after-the-rewrite"))
    original = OcrCacheKey(**ocr_parts())

    assert original.digest != rewritten.digest
    assert original.raster_identity == rewritten.raster_identity


@given(parts=ocr_key_parts(), field=st.sampled_from(sorted(set(OCR_KEY_PARTS) - {"page_hash"})))
def test_every_component_but_the_page_hash_moves_the_raster_identity(
    parts: dict[str, Any], field: str
):
    mutated: dict[str, Any] = dict(parts)
    mutated[field] = ("a wholly other slug",) if field == "recognizers" else "a wholly other value"
    assume(mutated[field] != parts[field])

    assert OcrCacheKey(**parts).raster_identity != OcrCacheKey(**mutated).raster_identity


def test_raster_identity_is_a_derived_property_and_never_a_stored_field():
    key = OcrCacheKey(**ocr_parts())

    assert "raster_identity" not in key.model_dump()
    assert OcrCacheKey.model_validate(key.model_dump()).raster_identity == key.raster_identity


def test_raster_identity_cannot_collide_with_the_key_s_own_digest():
    # Two digests over overlapping component lists, so they carry different domain tags:
    # a collision would let a fallback lookup match a row it has no business matching.
    key = OcrCacheKey(**ocr_parts())

    assert key.raster_identity != key.digest
    assert len(key.raster_identity) == 64
    assert set(key.raster_identity) <= set("0123456789abcdef")


@given(parts=ocr_key_parts(), field=st.sampled_from(sorted(OCR_KEY_PARTS)), data=st.data())
def test_two_ocr_keys_differing_in_any_field_are_different_keys(
    parts: dict[str, Any], field: str, data: st.DataObject
):
    # Defect 3, executable: the legacy tables keyed on the page hash alone while the model id and
    # the render DPI sat in ordinary columns, so a prompt or DPI change served a stale row as
    # valid. Every component must move the identity.
    if field == "recognizers":
        replacement: object = data.draw(
            RECOGNIZER_LIST.filter(lambda value: value != parts["recognizers"])
        )
    else:
        replacement = data.draw(COMPONENT.filter(lambda value: value != parts[field]))
    mutated: dict[str, Any] = dict(parts)
    mutated[field] = replacement
    base = OcrCacheKey(**parts)
    other = OcrCacheKey(**mutated)

    assert other != base
    assert other.digest != base.digest


@given(parts=diagram_key_parts(), field=st.sampled_from(sorted(DIAGRAM_KEY_PARTS)), data=st.data())
def test_two_diagram_keys_differing_in_any_field_are_different_keys(
    parts: dict[str, Any], field: str, data: st.DataObject
):
    replacement = data.draw(COMPONENT.filter(lambda value: value != parts[field]))
    mutated: dict[str, Any] = dict(parts)
    mutated[field] = replacement
    base = DiagramCacheKey(**parts)
    other = DiagramCacheKey(**mutated)

    assert other != base
    assert other.digest != base.digest


@given(parts=ocr_key_parts())
def test_equal_ocr_components_produce_one_identity(parts: dict[str, Any]):
    assert OcrCacheKey(**parts).digest == OcrCacheKey(**parts).digest


@given(parts=diagram_key_parts())
def test_equal_diagram_components_produce_one_identity(parts: dict[str, Any]):
    assert DiagramCacheKey(**parts).digest == DiagramCacheKey(**parts).digest


def test_the_recognizer_fold_order_is_part_of_the_ocr_identity():
    # Which engines survived, and in what order they were folded, changes the answer.
    forward = OcrCacheKey(**ocr_parts(recognizers=("vision", "textract")))
    backward = OcrCacheKey(**ocr_parts(recognizers=("textract", "vision")))
    partial = OcrCacheKey(**ocr_parts(recognizers=("vision",)))

    assert len({forward.digest, backward.digest, partial.digest}) == 3


def test_a_component_boundary_cannot_be_shifted_between_two_fields():
    # Length framing is what stops ("ab", "c") and ("a", "bc") folding to one digest.
    left = OcrCacheKey(**ocr_parts(page_hash="ab", render_digest="c"))
    right = OcrCacheKey(**ocr_parts(page_hash="a", render_digest="bc"))

    assert left.digest != right.digest


@pytest.mark.parametrize(
    ("left_parts", "right_parts"),
    [
        pytest.param(
            {"model_fingerprint": "a", "request_digest": f"b{UNIT_SEPARATOR}c"},
            {"model_fingerprint": f"a{UNIT_SEPARATOR}b", "request_digest": "c"},
            id="fingerprint_and_request",
        ),
        pytest.param(
            {"page_hash": "a", "render_digest": f"b{UNIT_SEPARATOR}c"},
            {"page_hash": f"a{UNIT_SEPARATOR}b", "render_digest": "c"},
            id="page_hash_and_render",
        ),
    ],
)
def test_a_separator_inside_a_component_cannot_move_a_field_boundary(
    left_parts: dict[str, Any],
    right_parts: dict[str, Any],
):
    # This is the reachable case that made framing a defect fix rather than a tidy-up:
    # `model_fingerprint` is contractually opaque and adapter-authored, so it is an open string
    # that may contain any byte. Under the old separator scheme these two keys folded to one
    # digest, so a row computed for one binding could be served for another.
    left = OcrCacheKey(**ocr_parts(**left_parts))
    right = OcrCacheKey(**ocr_parts(**right_parts))

    assert left != right
    assert left.digest != right.digest


def test_a_recognizer_slug_carrying_the_join_byte_cannot_forge_another_engine_set():
    # The app used to join surviving slugs on 0x1e and hash one string, so a slug containing
    # 0x1e could impersonate two engines: ("a\x1eb",) and ("a", "b") were one cache key, and a
    # Textract-only degraded run could reuse a Vision-plus-Textract row. Each slug is now its
    # own framed component, and the component *count* is folded in, so the split is recoverable.
    smuggled = OcrCacheKey(**ocr_parts(recognizers=(f"a{RECORD_SEPARATOR}b",)))
    genuine = OcrCacheKey(**ocr_parts(recognizers=("a", "b")))

    assert smuggled.digest != genuine.digest


def test_the_recognizer_count_is_recoverable_so_a_slug_cannot_absorb_a_neighbour():
    # `OcrCacheKey` has 3 fixed components, then N slugs, then 2 more, so N = count - 5 is
    # determined by the framed count. Without that, an empty slug or a slug spanning what were
    # two would leave two distinct component lists sharing one byte stream.
    digests = {
        OcrCacheKey(**ocr_parts(recognizers=recognizers)).digest
        for recognizers in [(), ("a",), ("a", "b"), ("ab",), ("a", "b", "c"), ("", "ab")]
    }

    assert len(digests) == 6


@pytest.mark.parametrize("field", sorted(set(OCR_KEY_PARTS) - {"recognizers"}))
def test_every_ocr_string_component_is_required_and_non_empty(field: str):
    with pytest.raises(ValidationError):
        OcrCacheKey(**ocr_parts(**{field: ""}))


@pytest.mark.parametrize("field", sorted(DIAGRAM_KEY_PARTS))
def test_every_diagram_component_is_required_and_non_empty(field: str):
    with pytest.raises(ValidationError):
        DiagramCacheKey(**diagram_parts(**{field: ""}))


@pytest.mark.parametrize("field", sorted(OCR_KEY_PARTS))
def test_no_ocr_component_may_be_omitted(field: str):
    with pytest.raises(ValidationError):
        OcrCacheKey(**{name: value for name, value in OCR_KEY_PARTS.items() if name != field})


@pytest.mark.parametrize("field", sorted(DIAGRAM_KEY_PARTS))
def test_no_diagram_component_may_be_omitted(field: str):
    with pytest.raises(ValidationError):
        DiagramCacheKey(
            **{name: value for name, value in DIAGRAM_KEY_PARTS.items() if name != field}
        )


def test_a_cache_key_can_be_a_dict_key_in_an_in_memory_double():
    cache = {OcrCacheKey(**ocr_parts()): "text"}

    assert cache[OcrCacheKey(**ocr_parts())] == "text"


def test_an_ocr_key_with_no_surviving_recognizers_is_still_constructible():
    # Partial failure is tolerated; a run where every engine failed is a representable key.
    key = OcrCacheKey(**ocr_parts(recognizers=()))

    assert key.recognizers == ()
    assert len(key.digest) == 64


# ──────────────────────── cached artifacts ────────────────────────


def test_page_content_kind_is_the_closed_set_the_cli_branches_on():
    assert {member.value for member in PageContentKind} == {"text", "diagram", "mixed"}


def test_an_ocr_artifact_defaults_to_a_complete_untruncated_reading():
    artifact = OcrArtifact(text="hello", created_at=MOMENT)

    assert artifact.truncated is False
    assert artifact.mean_confidence is None


def test_an_ocr_artifact_records_that_generation_was_cut_short():
    # Caching a half page without recording it is how the legacy pipeline served half pages
    # indefinitely.
    artifact = OcrArtifact(text="half", truncated=True, created_at=MOMENT)

    assert artifact.truncated is True


def test_an_empty_transcription_is_legal():
    assert OcrArtifact(text="", created_at=MOMENT).text == ""


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_an_ocr_artifact_accepts_a_confidence_on_the_unit_scale(confidence: float):
    artifact = OcrArtifact(text="x", mean_confidence=confidence, created_at=MOMENT)

    assert artifact.mean_confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 100.0])
def test_an_ocr_artifact_refuses_a_confidence_off_the_unit_scale(confidence: float):
    with pytest.raises(ValidationError):
        OcrArtifact(text="x", mean_confidence=confidence, created_at=MOMENT)


def test_an_ocr_artifact_cannot_be_cached_without_a_creation_time():
    with pytest.raises(ValidationError):
        OcrArtifact.model_validate({"text": "x"})


def test_an_ocr_artifact_refuses_a_naive_creation_time():
    with pytest.raises(ValidationError):
        OcrArtifact(text="x", created_at=NAIVE_MOMENT)


def test_an_artifact_built_without_provenance_reads_as_the_tier_three_merge_it_was():
    # The default is not a placeholder: before this field existed the tier-3 merge was the
    # only thing a writer would store, so this is what every such row actually means.
    artifact = OcrArtifact(text="x", created_at=MOMENT)

    assert artifact.provenance == OcrProvenance()
    assert artifact.provenance.tier_reached == 3
    assert artifact.provenance.short_circuited is False
    assert artifact.provenance.contributors == ()
    assert artifact.provenance.agreement is None


def test_a_row_written_before_provenance_existed_still_validates():
    # The forward direction of the compatibility bargain. The backward one -- an older build
    # reading a newer row -- fails as StoredRecordUnreadableError, which the class docstring
    # states rather than papers over, and no default can prevent.
    rehydrated = OcrArtifact.model_validate(
        {"text": "x", "mean_confidence": 0.5, "truncated": False, "created_at": MOMENT}
    )

    assert rehydrated.provenance == OcrProvenance()


def test_provenance_is_not_a_component_of_the_cache_key():
    # Adding one would change every digest and mass-invalidate every cached row to record
    # something the artifact carries for free.
    assert "provenance" not in OcrCacheKey.model_fields


def test_a_short_circuited_provenance_must_say_how_closely_the_readings_agreed():
    with pytest.raises(ValidationError, match="not a cache-key component"):
        OcrProvenance(tier_reached=1, short_circuited=True, contributors=("device-index@1",))


def test_a_merged_provenance_needs_no_agreement():
    provenance = OcrProvenance(tier_reached=3, contributors=("textract", "vision-read:m"))

    assert provenance.agreement is None
    assert provenance.short_circuited is False


@pytest.mark.parametrize("tier", [-1, 4])
def test_a_provenance_outside_the_four_tiers_is_unconstructible(tier: int):
    with pytest.raises(ValidationError):
        OcrProvenance(tier_reached=tier)


@pytest.mark.parametrize("agreement", [-0.1, 1.1])
def test_a_provenance_agreement_outside_the_ratio_is_unconstructible(agreement: float):
    with pytest.raises(ValidationError):
        OcrProvenance(short_circuited=True, agreement=agreement)


def test_a_provenance_forbids_an_unknown_field():
    # Frozen-ness is covered for every exported model by the module-surface check above;
    # what is worth stating here is that a newer build's extra component is a refusal and
    # not a silent drop, which is the forward-compatibility cost `OcrArtifact` documents.
    with pytest.raises(ValidationError):
        OcrProvenance.model_validate({"tier_reached": 1, "surprise": True})


def test_a_text_only_page_carries_no_mermaid():
    artifact = DiagramArtifact(content_kind=PageContentKind.TEXT, created_at=MOMENT)

    assert artifact.mermaid is None
    assert artifact.diagram_kind is None


@pytest.mark.parametrize("kind", [PageContentKind.DIAGRAM, PageContentKind.MIXED])
def test_a_diagram_bearing_page_carries_mermaid(kind: PageContentKind):
    artifact = DiagramArtifact(
        content_kind=kind,
        mermaid="flowchart TD\n A-->B",
        diagram_kind="flowchart",
        created_at=MOMENT,
    )

    assert artifact.mermaid is not None
    assert artifact.diagram_kind == "flowchart"


@pytest.mark.parametrize("kind", [PageContentKind.DIAGRAM, PageContentKind.MIXED])
@pytest.mark.parametrize("mermaid", [None, "", "   ", "\n"])
def test_a_diagram_bearing_page_without_mermaid_is_unconstructible(
    kind: PageContentKind, mermaid: str | None
):
    with pytest.raises(ValidationError, match="requires mermaid source"):
        DiagramArtifact(content_kind=kind, mermaid=mermaid, created_at=MOMENT)


@pytest.mark.parametrize("mermaid", ["", "flowchart TD\n A-->B"])
def test_a_text_only_page_that_carries_mermaid_is_unconstructible(mermaid: str):
    # "this page is a diagram" and "here is no diagram" cannot both be true of one cached row.
    with pytest.raises(ValidationError, match="mermaid must be None"):
        DiagramArtifact(content_kind=PageContentKind.TEXT, mermaid=mermaid, created_at=MOMENT)


def test_a_diagram_artifact_cannot_be_cached_without_a_creation_time():
    with pytest.raises(ValidationError):
        DiagramArtifact.model_validate({"content_kind": PageContentKind.TEXT})


def test_the_mermaid_diagram_type_stays_an_open_string():
    # Mermaid's set of diagram types is Mermaid's to grow; a closed enum would make a new one a
    # domain edit.
    artifact = DiagramArtifact(
        content_kind=PageContentKind.DIAGRAM,
        mermaid="quadrantChart",
        diagram_kind="somethingMermaidAddedLastWeek",
        created_at=MOMENT,
    )

    assert artifact.diagram_kind == "somethingMermaidAddedLastWeek"


# ──────────────────────── sync history ────────────────────────


def test_sync_operation_covers_the_commands_that_change_state_or_spend_money():
    assert {member.value for member in SyncOperation} == {
        "pull",
        "push",
        "ocr",
        "diagram",
        "export",
    }


def test_sync_outcome_can_express_the_partial_ending_the_legacy_code_could_not():
    assert {member.value for member in SyncOutcome} == {
        "succeeded",
        "partial",
        "failed",
        "skipped",
    }


def test_an_audit_entry_defaults_to_a_library_wide_operation_with_nothing_to_say():
    entry = make_audit_entry()

    assert entry.doc_uuid is None
    assert entry.doc_name == ""
    assert entry.pages_affected == 0
    assert entry.detail == ""


@pytest.mark.parametrize("outcome", [SyncOutcome.SUCCEEDED, SyncOutcome.SKIPPED])
def test_a_clean_outcome_needs_no_detail(outcome: SyncOutcome):
    assert make_audit_entry(outcome=outcome).detail == ""


@pytest.mark.parametrize("outcome", [SyncOutcome.FAILED, SyncOutcome.PARTIAL])
@pytest.mark.parametrize("detail", ["", "   ", "\n"])
def test_an_unhappy_outcome_must_say_what_went_wrong(outcome: SyncOutcome, detail: str):
    # "something went wrong" with no detail is the log line this design exists to replace.
    with pytest.raises(ValidationError, match="requires a detail"):
        make_audit_entry(outcome=outcome, detail=detail)


@pytest.mark.parametrize("outcome", [SyncOutcome.FAILED, SyncOutcome.PARTIAL])
def test_an_unhappy_outcome_with_a_detail_is_constructible(outcome: SyncOutcome):
    entry = make_audit_entry(outcome=outcome, detail="380 of 400 documents copied")

    assert entry.detail == "380 of 400 documents copied"


def test_an_audit_entry_records_the_document_name_at_the_time():
    entry = make_audit_entry(doc_uuid="doc", doc_name="Old name", pages_affected=12)

    assert (entry.doc_uuid, entry.doc_name, entry.pages_affected) == ("doc", "Old name", 12)


def test_an_audit_entry_refuses_a_negative_page_count():
    with pytest.raises(ValidationError):
        make_audit_entry(pages_affected=-1)


def test_an_audit_entry_requires_an_aware_occurrence_time():
    with pytest.raises(ValidationError):
        make_audit_entry(occurred_at=NAIVE_MOMENT)


def test_a_recorded_entry_adds_the_sequence_the_store_assigned():
    entry = make_audit_entry()
    recorded = RecordedSyncAuditEntry(sequence=1, entry=entry)

    assert recorded.sequence == 1
    assert recorded.entry == entry


@pytest.mark.parametrize("sequence", [0, -1])
def test_a_recorded_sequence_starts_at_one(sequence: int):
    with pytest.raises(ValidationError):
        RecordedSyncAuditEntry(sequence=sequence, entry=make_audit_entry())


def test_an_unsequenced_entry_cannot_be_mistaken_for_a_recorded_one():
    # Composition rather than a subclass with one extra field, so an entry cannot smuggle a
    # sequence of its own.
    entry = make_audit_entry()

    with pytest.raises(ValidationError):
        RecordedSyncAuditEntry(sequence=1, entry=entry.model_dump() | {"sequence": 1})
