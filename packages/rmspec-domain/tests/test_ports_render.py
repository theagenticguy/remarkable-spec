"""Behavioural tests for the render slice's port module.

Every test here is stdlib plus pydantic: no filesystem, no network, no image library. The
values under test are the ones the render seam exchanges, and the assertions are the three
things a double could otherwise lie about -- what a background may be, what a rendered page
may claim, and what a render identity folds in (defect 3).
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from rmspec.domain.models import (
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    Layer,
    Page,
    PageContent,
    PageId,
    Palette,
    PenColor,
    Rgb,
    ScreenSpec,
    TextBlock,
)
from rmspec.domain.ports import render as render_port
from rmspec.domain.ports.render import (
    _FIELD_SEPARATOR,
    _JPEG_SIGNATURE,
    _PNG_HEADER_LENGTH,
    _PNG_SIGNATURE,
    ImageMedia,
    PageBackground,
    PageRenderer,
    PageUnderlay,
    PhysicalSize,
    RenderedPage,
    RenderNotice,
    RenderNoticeCode,
    RenderStyle,
    TextStyle,
    _canonical_json,
    _check_image_signature,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# ─────────────────────────────── fixtures as plain data ───────────────────────────────

PNG_BYTES = _PNG_SIGNATURE + bytes(_PNG_HEADER_LENGTH - len(_PNG_SIGNATURE))
"""The shortest byte string that is a structurally plausible PNG: signature + IHDR span."""

JPEG_BYTES = _JPEG_SIGNATURE + b"\xe0\x00\x10JFIF\x00"

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>'

positive = st.floats(
    min_value=0.0,
    max_value=1e6,
    exclude_min=True,
    allow_nan=False,
    allow_infinity=False,
)
non_positive = st.floats(max_value=0.0, allow_nan=False, allow_infinity=False)


def make_palette(name: str = "export", *, tint: int = 0) -> Palette:
    return Palette(
        name=name,
        inks={colour: Rgb(r=tint, g=colour.value, b=0) for colour in PenColor},
    )


def make_size(width_mm: float = 210.0, height_mm: float = 297.0) -> PhysicalSize:
    return PhysicalSize(width_mm=width_mm, height_mm=height_mm)


def make_underlay(
    *,
    media: ImageMedia = ImageMedia.PNG,
    data: bytes | None = None,
    source_size: PhysicalSize | None = None,
) -> PageUnderlay:
    payload = data if data is not None else (PNG_BYTES if media is ImageMedia.PNG else JPEG_BYTES)
    return PageUnderlay(
        media=media,
        data=payload,
        source_size=source_size if source_size is not None else make_size(),
    )


def make_text_style(
    family: str = "Noto Sans, sans-serif",
    size_px: float = 32.0,
    line_height: float = 1.25,
) -> TextStyle:
    return TextStyle(family=family, size_px=size_px, line_height=line_height)


def make_style(
    thickness_scale: float = 1.5,
    min_padding_mm: float = 5.0,
    text: TextStyle | None = None,
    renderer_revision: str = "render-2026.08.1",
) -> RenderStyle:
    return RenderStyle(
        thickness_scale=thickness_scale,
        min_padding_mm=min_padding_mm,
        text=text if text is not None else make_text_style(),
        renderer_revision=renderer_revision,
    )


def make_page(text: str = "hello", *, visible: bool = True) -> Page:
    return Page(
        page_id=PageId(uuid="page-0001"),
        index=0,
        content=PageContent(
            layers=(
                Layer(
                    name="Layer 1",
                    visible=visible,
                    text_blocks=(TextBlock(pos_x=0.0, pos_y=0.0, width=800.0, text=text),),
                ),
            ),
        ),
    )


def digest_of(style: RenderStyle, *, background: PageBackground | None = None) -> str:
    return style.digest(screen=PAPER_PRO_SCREEN, palette=make_palette(), background=background)


# ─────────────────────────────── enums stay closed ───────────────────────────────


def test_image_media_is_two_members_and_str_valued():
    assert [member.value for member in ImageMedia] == ["png", "jpeg"]
    assert ImageMedia.PNG == "png"
    assert ImageMedia("jpeg") is ImageMedia.JPEG


def test_render_notice_code_is_closed_at_three_survivable_substitutions():
    assert {member.value for member in RenderNoticeCode} == {
        "viewport_expanded",
        "underlay_rescaled",
        "text_omitted",
    }


def test_public_surface_is_exported_and_sorted():
    assert render_port.__all__ == sorted(render_port.__all__)
    for name in render_port.__all__:
        assert hasattr(render_port, name)


# ─────────────────────────────── canonical json ───────────────────────────────


def test_canonical_json_sorts_keys_and_stays_compact():
    text = _canonical_json(make_text_style(family="serif"))

    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    assert ", " not in text
    assert '": ' not in text
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_canonical_json_is_blind_to_mapping_insertion_order():
    forward = make_palette()
    reversed_inks = Palette(
        name=forward.name,
        inks=dict(reversed(list(forward.inks.items()))),
    )

    assert list(reversed_inks.inks) != list(forward.inks)
    assert _canonical_json(reversed_inks) == _canonical_json(forward)


# ─────────────────────────────── image signature gate ───────────────────────────────


def test_check_image_signature_accepts_bytes_in_the_declared_encoding():
    assert _check_image_signature(ImageMedia.PNG, PNG_BYTES) is None
    assert _check_image_signature(ImageMedia.JPEG, JPEG_BYTES) is None


def test_check_image_signature_rejects_png_below_the_header_length():
    truncated = PNG_BYTES[: _PNG_HEADER_LENGTH - 1]

    assert truncated.startswith(_PNG_SIGNATURE)
    with pytest.raises(ValueError, match="png signature and header"):
        _check_image_signature(ImageMedia.PNG, truncated)


def test_check_image_signature_accepts_png_exactly_at_the_header_length():
    # The twin-drift boundary: the export slice enforced 24 bytes while this copy checked 8,
    # so bytes that port rejected used to be accepted here.
    assert len(PNG_BYTES) == _PNG_HEADER_LENGTH
    assert _check_image_signature(ImageMedia.PNG, PNG_BYTES) is None


@pytest.mark.parametrize(
    ("media", "data", "message"),
    [
        (ImageMedia.PNG, b"AA==" * 8, "png signature and header"),
        (ImageMedia.PNG, JPEG_BYTES * 8, "png signature and header"),
        (ImageMedia.JPEG, b"AA==", "jpeg signature"),
        (ImageMedia.JPEG, PNG_BYTES, "jpeg signature"),
        (ImageMedia.JPEG, _JPEG_SIGNATURE[:2], "jpeg signature"),
    ],
)
def test_check_image_signature_rejects_foreign_bytes(
    media: ImageMedia,
    data: bytes,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        _check_image_signature(media, data)


# ─────────────────────────────── PhysicalSize ───────────────────────────────


@given(width=positive, height=positive)
def test_physical_size_accepts_any_positive_millimetre_pair(width: float, height: float):
    size = PhysicalSize(width_mm=width, height_mm=height)

    assert (size.width_mm, size.height_mm) == (width, height)


@given(bad=non_positive)
def test_physical_size_rejects_non_positive_width(bad: float):
    with pytest.raises(ValidationError):
        PhysicalSize(width_mm=bad, height_mm=297.0)


@given(bad=non_positive)
def test_physical_size_rejects_non_positive_height(bad: float):
    with pytest.raises(ValidationError):
        PhysicalSize(width_mm=210.0, height_mm=bad)


def test_physical_size_is_frozen_and_extra_free():
    size = make_size()

    with pytest.raises(ValidationError):
        size.width_mm = 1.0  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        PhysicalSize.model_validate({"width_mm": 1.0, "height_mm": 1.0, "depth_mm": 1.0})


def test_physical_size_equal_values_compare_and_hash_equal():
    assert make_size() == make_size()
    assert hash(make_size()) == hash(make_size())
    assert make_size(211.0) != make_size()


# ─────────────────────────────── RenderNotice ───────────────────────────────


def test_render_notice_requires_human_facing_detail():
    with pytest.raises(ValidationError):
        RenderNotice(code=RenderNoticeCode.TEXT_OMITTED, detail="")


def test_render_notice_round_trips_through_its_wire_form():
    notice = RenderNotice(
        code=RenderNoticeCode.VIEWPORT_EXPANDED,
        detail="ink 40px left of the page box",
    )

    assert RenderNotice.model_validate_json(notice.model_dump_json()) == notice
    assert (
        RenderNotice.model_validate(
            {"code": "viewport_expanded", "detail": "ink 40px left of the page box"},
        )
        == notice
    )


def test_render_notice_rejects_a_code_outside_the_closed_set():
    with pytest.raises(ValidationError):
        RenderNotice.model_validate({"code": "font_substituted", "detail": "x"})


# ─────────────────────────────── PageUnderlay ───────────────────────────────


def test_page_underlay_carries_the_source_page_size_not_the_target():
    underlay = make_underlay(source_size=make_size(215.9, 279.4))

    assert underlay.source_size == PhysicalSize(width_mm=215.9, height_mm=279.4)
    assert underlay.source_size != make_size()


def test_page_underlay_rejects_empty_bytes():
    with pytest.raises(ValidationError):
        PageUnderlay(media=ImageMedia.PNG, data=b"", source_size=make_size())


@pytest.mark.parametrize(
    ("media", "data"),
    [
        (ImageMedia.PNG, PNG_BYTES[:8]),
        (ImageMedia.PNG, JPEG_BYTES),
        (ImageMedia.JPEG, PNG_BYTES),
        (ImageMedia.JPEG, b"\x89PNG"),
    ],
)
def test_page_underlay_rejects_bytes_that_contradict_the_declared_media(
    media: ImageMedia,
    data: bytes,
):
    with pytest.raises(ValidationError):
        PageUnderlay(media=media, data=data, source_size=make_size())


def test_page_underlay_accepts_both_encodings_and_is_frozen():
    png = make_underlay(media=ImageMedia.PNG)
    jpeg = make_underlay(media=ImageMedia.JPEG)

    assert (png.media, jpeg.media) == (ImageMedia.PNG, ImageMedia.JPEG)
    with pytest.raises(ValidationError):
        png.data = JPEG_BYTES  # ty: ignore[invalid-assignment]


# ─────────────────────────────── PageBackground ───────────────────────────────


def test_page_background_rejects_carrying_nothing():
    with pytest.raises(ValidationError, match="template markup, an underlay, or both"):
        PageBackground()


@pytest.mark.parametrize(
    "background",
    [
        PageBackground(template_svg=SVG),
        PageBackground(underlay=make_underlay()),
        PageBackground(template_svg=SVG, underlay=make_underlay()),
    ],
)
def test_page_background_accepts_the_three_representable_states(background: PageBackground):
    assert background.template_svg is not None or background.underlay is not None


def test_page_background_does_not_pre_screen_template_markup():
    # One user mistake, one exit channel: well-formedness is BackgroundUnreadable from
    # PageRenderer.render, never a ValidationError here.
    background = PageBackground(template_svg="not markup at all")

    assert background.template_svg == "not markup at all"


def test_page_background_forbids_extra_fields():
    with pytest.raises(ValidationError):
        PageBackground.model_validate({"template_svg": SVG, "template_path": "background.svg"})


def test_page_background_digest_is_stable_lowercase_hex():
    background = PageBackground(template_svg=SVG, underlay=make_underlay())
    first = background.digest()

    assert first == background.digest()
    assert first == PageBackground(template_svg=SVG, underlay=make_underlay()).digest()
    assert len(first) == 64
    assert first == first.lower()
    assert set(first) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (PageBackground(template_svg=SVG), PageBackground(template_svg=SVG + "<g/>")),
        (PageBackground(template_svg=SVG), PageBackground(underlay=make_underlay())),
        (
            PageBackground(underlay=make_underlay()),
            PageBackground(underlay=make_underlay(data=PNG_BYTES + b"\x01")),
        ),
        (
            PageBackground(underlay=make_underlay(media=ImageMedia.PNG)),
            PageBackground(underlay=make_underlay(media=ImageMedia.JPEG)),
        ),
        (
            PageBackground(template_svg=SVG),
            PageBackground(template_svg=SVG, underlay=make_underlay()),
        ),
    ],
)
def test_page_background_digest_separates_every_pixel_changing_component(
    left: PageBackground,
    right: PageBackground,
):
    assert left.digest() != right.digest()


def test_page_background_digest_cannot_be_confused_by_separator_smuggling():
    # The only reason this cannot collide is that underlay bytes have min_length=1: the
    # left stream is the right one plus a trailing, non-empty data field.
    smuggled = _FIELD_SEPARATOR.decode() + ImageMedia.PNG.value
    with_underlay = PageBackground(template_svg=SVG, underlay=make_underlay())
    markup_only = PageBackground(template_svg=SVG + smuggled)

    assert with_underlay.digest() != markup_only.digest()


def test_page_background_digest_treats_empty_markup_as_no_markup():
    # Correct for a pixel identity: empty markup draws exactly what no markup draws.
    underlay = make_underlay()

    assert (
        PageBackground(template_svg="", underlay=underlay).digest()
        == PageBackground(underlay=underlay).digest()
    )


def test_page_background_digest_ignores_the_underlay_source_size():
    # Documented gap, reported to the reviewer rather than asserted as desirable: two
    # underlays with identical bytes but different native page sizes are placed differently
    # (that is what UNDERLAY_RESCALED reports), yet they share a background digest.
    letter = PageBackground(underlay=make_underlay(source_size=make_size(215.9, 279.4)))
    a4 = PageBackground(underlay=make_underlay(source_size=make_size(210.0, 297.0)))

    assert letter.digest() == a4.digest()


# ─────────────────────────────── TextStyle ───────────────────────────────


def test_text_style_keeps_the_css_family_list_verbatim():
    style = make_text_style(family="Noto Sans, Helvetica, sans-serif")

    assert style.family == "Noto Sans, Helvetica, sans-serif"


@pytest.mark.parametrize(
    ("family", "size_px", "line_height"),
    [
        ("", 32.0, 1.25),
        ("serif", 0.0, 1.25),
        ("serif", -1.0, 1.25),
        ("serif", 32.0, 0.0),
        ("serif", 32.0, -1.25),
    ],
)
def test_text_style_rejects_unusable_typography(
    family: str,
    size_px: float,
    line_height: float,
):
    with pytest.raises(ValidationError):
        TextStyle(family=family, size_px=size_px, line_height=line_height)


def test_text_style_is_frozen_and_extra_free():
    style = make_text_style()

    with pytest.raises(ValidationError):
        style.size_px = 12.0  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        TextStyle.model_validate({"family": "serif", "size_px": 8.0, "line_height": 1.0, "dpi": 1})


# ─────────────────────────────── RenderStyle construction ───────────────────────────────


def test_render_style_has_no_defaulted_field():
    for name, field in RenderStyle.model_fields.items():
        assert field.is_required(), f"{name} acquired a default"


def test_render_style_allows_zero_padding_but_not_zero_thickness():
    assert make_style(min_padding_mm=0.0).min_padding_mm == 0.0
    with pytest.raises(ValidationError):
        make_style(thickness_scale=0.0)


@pytest.mark.parametrize(
    ("thickness_scale", "min_padding_mm", "renderer_revision"),
    [
        (-1.5, 5.0, "r1"),
        (1.5, -0.1, "r1"),
        (1.5, 5.0, ""),
    ],
)
def test_render_style_rejects_out_of_range_policy(
    thickness_scale: float,
    min_padding_mm: float,
    renderer_revision: str,
):
    with pytest.raises(ValidationError):
        make_style(
            thickness_scale=thickness_scale,
            min_padding_mm=min_padding_mm,
            renderer_revision=renderer_revision,
        )


def test_render_style_holds_no_dpi_field():
    # Rasterization resolution travels with the pixels, so there is no second source of
    # truth for that cache-key component here.
    assert "dpi" not in RenderStyle.model_fields
    with pytest.raises(ValidationError):
        RenderStyle.model_validate(
            {
                "thickness_scale": 1.5,
                "min_padding_mm": 5.0,
                "text": make_text_style().model_dump(),
                "renderer_revision": "r1",
                "dpi": 229,
            },
        )


def test_render_style_is_frozen():
    style = make_style()

    with pytest.raises(ValidationError):
        style.thickness_scale = 2.0  # ty: ignore[invalid-assignment]


# ─────────────────────────────── RenderStyle.digest (defect 3) ───────────────────────────


def test_digest_requires_screen_palette_and_background_by_keyword():
    signature = inspect.signature(RenderStyle.digest)
    parameters = signature.parameters

    assert [name for name in parameters if name != "self"] == [
        "screen",
        "palette",
        "background",
    ]
    for name in ("screen", "palette", "background"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


def test_digest_is_deterministic_and_lowercase_hex():
    first = digest_of(make_style())

    assert first == digest_of(make_style())
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_digest_is_domain_separated_from_a_background_digest():
    background = PageBackground(template_svg=SVG)

    assert digest_of(make_style(), background=background) != background.digest()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda: make_style(thickness_scale=1.6), id="thickness_scale"),
        pytest.param(lambda: make_style(min_padding_mm=5.5), id="min_padding_mm"),
        pytest.param(lambda: make_style(renderer_revision="render-2026.09.1"), id="revision"),
        pytest.param(lambda: make_style(text=make_text_style(family="serif")), id="text_family"),
        pytest.param(lambda: make_style(text=make_text_style(size_px=33.0)), id="text_size"),
        pytest.param(
            lambda: make_style(text=make_text_style(line_height=1.5)),
            id="text_line_height",
        ),
    ],
)
def test_digest_changes_when_any_policy_component_changes(mutate: Callable[[], RenderStyle]):
    assert digest_of(mutate()) != digest_of(make_style())


def test_digest_changes_with_the_screen_that_sets_the_centre_origin_shift():
    style = make_style()
    baseline = style.digest(screen=PAPER_PRO_SCREEN, palette=make_palette(), background=None)
    other = style.digest(screen=RM2_SCREEN, palette=make_palette(), background=None)

    # x_shift is half the viewport width, so two screens of different widths place every
    # centre-origin stroke differently -- and must not share a render identity.
    assert PAPER_PRO_SCREEN.x_shift == PAPER_PRO_SCREEN.width / 2
    assert RM2_SCREEN.x_shift == RM2_SCREEN.width / 2
    assert PAPER_PRO_SCREEN.x_shift != RM2_SCREEN.x_shift
    assert baseline != other


def test_digest_changes_with_the_screen_dpi_alone():
    style = make_style()
    base = ScreenSpec(name="Paper Pro", width=1620, height=2160, dpi=229)
    rescaled = ScreenSpec(name="Paper Pro", width=1620, height=2160, dpi=226)

    assert style.digest(screen=base, palette=make_palette(), background=None) != style.digest(
        screen=rescaled,
        palette=make_palette(),
        background=None,
    )


def test_digest_changes_with_the_palette_name_alone():
    style = make_style()
    export = style.digest(screen=PAPER_PRO_SCREEN, palette=make_palette("export"), background=None)
    screen = style.digest(screen=PAPER_PRO_SCREEN, palette=make_palette("screen"), background=None)

    assert export != screen


def test_digest_changes_with_the_palette_contents_alone():
    style = make_style()
    original = make_palette("export")
    edited = make_palette("export", tint=1)

    assert original.name == edited.name
    assert original.inks != edited.inks
    assert style.digest(
        screen=PAPER_PRO_SCREEN,
        palette=original,
        background=None,
    ) != style.digest(screen=PAPER_PRO_SCREEN, palette=edited, background=None)


def test_digest_ignores_palette_ink_insertion_order():
    style = make_style()
    forward = make_palette()
    shuffled = Palette(name=forward.name, inks=dict(reversed(list(forward.inks.items()))))

    assert style.digest(
        screen=PAPER_PRO_SCREEN,
        palette=shuffled,
        background=None,
    ) == style.digest(screen=PAPER_PRO_SCREEN, palette=forward, background=None)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (None, PageBackground(template_svg=SVG)),
        (None, PageBackground(underlay=make_underlay())),
        (PageBackground(template_svg=SVG), PageBackground(template_svg=SVG + "<g/>")),
        (
            PageBackground(underlay=make_underlay()),
            PageBackground(underlay=make_underlay(data=PNG_BYTES + b"\x02")),
        ),
    ],
)
def test_digest_folds_the_background_in(
    left: PageBackground | None,
    right: PageBackground | None,
):
    assert digest_of(make_style(), background=left) != digest_of(make_style(), background=right)


def test_digest_distinguishes_a_string_that_moves_between_components():
    style = make_style()
    forwards = style.digest(
        screen=ScreenSpec(name="alpha", width=1620, height=2160, dpi=229),
        palette=make_palette("beta"),
        background=None,
    )
    swapped = style.digest(
        screen=ScreenSpec(name="beta", width=1620, height=2160, dpi=229),
        palette=make_palette("alpha"),
        background=None,
    )

    assert forwards != swapped


@given(revision=st.text(min_size=1, max_size=32), thickness=positive)
def test_digest_is_a_function_of_its_inputs_only(revision: str, thickness: float):
    left = make_style(thickness_scale=thickness, renderer_revision=revision)
    right = make_style(thickness_scale=thickness, renderer_revision=revision)

    assert left == right
    assert digest_of(left) == digest_of(right)


@given(revision=st.text(min_size=1, max_size=32))
def test_digest_separates_every_renderer_revision(revision: str):
    baseline = make_style(renderer_revision="render-2026.08.1")
    candidate = make_style(renderer_revision=revision)

    if candidate.renderer_revision == baseline.renderer_revision:
        assert digest_of(candidate) == digest_of(baseline)
    else:
        assert digest_of(candidate) != digest_of(baseline)


# ─────────────────────────────── RenderedPage ───────────────────────────────


def test_rendered_page_defaults_to_no_notices():
    page = RenderedPage(
        page_ref="page-0001",
        svg=SVG,
        size=make_size(),
        stroke_count=3,
        text_block_count=1,
    )

    assert page.notices == ()


def test_rendered_page_accepts_an_empty_page():
    # Zero of both counters is legal: an empty page renders, and that is not the failure
    # TEXT_OMITTED exists for.
    page = RenderedPage(
        page_ref="page-0001",
        svg="<svg/>",
        size=make_size(),
        stroke_count=0,
        text_block_count=0,
    )

    assert (page.stroke_count, page.text_block_count, page.notices) == (0, 0, ())


def test_rendered_page_root_check_is_one_substring_not_a_parse():
    # Deliberately shallow: well-formedness is the rasterizer's or the renderer's verdict, so
    # anything carrying the root tag is accepted and only "not markup at all" is refused.
    page = RenderedPage(
        page_ref="page-0001",
        svg="<svg><unclosed>",
        size=make_size(),
        stroke_count=1,
        text_block_count=0,
    )

    assert page.svg == "<svg><unclosed>"


@pytest.mark.parametrize(
    "svg",
    ["", "x", "not markup", "<svq/>", "<SVG/>"],
)
def test_rendered_page_rejects_markup_without_an_svg_root(svg: str):
    with pytest.raises(ValidationError):
        RenderedPage(
            page_ref="page-0001",
            svg=svg,
            size=make_size(),
            stroke_count=0,
            text_block_count=0,
        )


def test_rendered_page_svg_root_check_names_the_missing_element():
    with pytest.raises(ValidationError, match="<svg> root element"):
        RenderedPage(
            page_ref="page-0001",
            svg="x",
            size=make_size(),
            stroke_count=0,
            text_block_count=0,
        )


@pytest.mark.parametrize(
    ("page_ref", "stroke_count", "text_block_count"),
    [
        ("", 0, 0),
        ("page-0001", -1, 0),
        ("page-0001", 0, -1),
    ],
)
def test_rendered_page_rejects_unusable_bookkeeping(
    page_ref: str,
    stroke_count: int,
    text_block_count: int,
):
    with pytest.raises(ValidationError):
        RenderedPage(
            page_ref=page_ref,
            svg=SVG,
            size=make_size(),
            stroke_count=stroke_count,
            text_block_count=text_block_count,
        )


def test_rendered_page_notices_are_an_immutable_ordered_sequence():
    page = RenderedPage.model_validate(
        {
            "page_ref": "page-0001",
            "svg": SVG,
            "size": {"width_mm": 210.0, "height_mm": 297.0},
            "stroke_count": 2,
            "text_block_count": 0,
            "notices": [
                {"code": "viewport_expanded", "detail": "widened by 40px"},
                {"code": "text_omitted", "detail": "1 block, no font metrics"},
            ],
        },
    )

    assert isinstance(page.notices, tuple)
    assert [notice.code for notice in page.notices] == [
        RenderNoticeCode.VIEWPORT_EXPANDED,
        RenderNoticeCode.TEXT_OMITTED,
    ]
    with pytest.raises(ValidationError):
        page.notices = ()  # ty: ignore[invalid-assignment]


def test_rendered_page_round_trips_through_json():
    page = RenderedPage(
        page_ref="page-0001",
        svg=SVG,
        size=make_size(),
        stroke_count=7,
        text_block_count=2,
        notices=(RenderNotice(code=RenderNoticeCode.UNDERLAY_RESCALED, detail="letter into a4"),),
    )

    assert RenderedPage.model_validate_json(page.model_dump_json()) == page


def test_rendered_page_forbids_extra_fields():
    with pytest.raises(ValidationError):
        RenderedPage.model_validate(
            {
                "page_ref": "page-0001",
                "svg": SVG,
                "size": {"width_mm": 210.0, "height_mm": 297.0},
                "stroke_count": 0,
                "text_block_count": 0,
                "render_digest": "deadbeef",
            },
        )


def test_rendered_page_carries_no_self_reported_identity():
    # An identity the adapter echoes back is unfalsifiable, so key construction stays with
    # the caller that chose the configuration.
    assert "digest" not in RenderedPage.model_fields
    assert not hasattr(
        RenderedPage(
            page_ref="page-0001",
            svg=SVG,
            size=make_size(),
            stroke_count=0,
            text_block_count=0,
        ),
        "digest",
    )


# ─────────────────────────────── PageRenderer protocol ───────────────────────────────


class _TextDroppingRenderer:
    """A legitimate font-metric-free adapter: draws ink, reports the text it omitted."""

    def __init__(self) -> None:
        self.identity: str | None = None

    def render(
        self,
        page: Page,
        /,
        *,
        screen: ScreenSpec,
        palette: Palette,
        style: RenderStyle,
        background: PageBackground | None = None,
    ) -> RenderedPage:
        """Return markup with every typed block omitted and one notice saying so."""
        self.identity = style.digest(screen=screen, palette=palette, background=background)
        layers = page.content.layers if page.content is not None else ()
        omitted = sum(
            1 for layer in layers if layer.visible for block in layer.text_blocks if block.text
        )
        notices = (
            RenderNotice(
                code=RenderNoticeCode.TEXT_OMITTED,
                detail=f"{omitted} block(s) omitted: no metrics for {style.text.family}",
            ),
        )
        return RenderedPage(
            page_ref=page.page_id.uuid,
            svg=SVG,
            size=PhysicalSize(width_mm=screen.width_mm, height_mm=screen.height_mm),
            stroke_count=0,
            text_block_count=0,
            notices=notices if omitted else (),
        )


def test_page_renderer_has_exactly_one_member():
    members = {
        name
        for name in vars(PageRenderer)
        if not name.startswith("_") and callable(getattr(PageRenderer, name, None))
    }

    assert members == {"render"}


def test_page_renderer_render_takes_the_page_positionally_and_the_policy_by_keyword():
    parameters = inspect.signature(PageRenderer.render).parameters

    assert parameters["page"].kind is inspect.Parameter.POSITIONAL_ONLY
    for name in ("screen", "palette", "style"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty
    assert parameters["background"].default is None


def test_page_renderer_is_not_runtime_checkable():
    # A structural isinstance that passes on the method name while the semantics differ is
    # worse than no check; conformance is the shared contract suite's job.
    assert getattr(PageRenderer, "_is_runtime_protocol", False) is False


def test_a_font_metric_free_double_conforms_and_cannot_drop_text_silently():
    renderer: PageRenderer = _TextDroppingRenderer()
    style = make_style()
    palette = make_palette()

    result = renderer.render(
        make_page("hello"),
        screen=PAPER_PRO_SCREEN,
        palette=palette,
        style=style,
        background=PageBackground(template_svg=SVG),
    )

    assert result.page_ref == "page-0001"
    assert result.text_block_count == 0
    assert [notice.code for notice in result.notices] == [RenderNoticeCode.TEXT_OMITTED]
    assert result.size == PhysicalSize(
        width_mm=PAPER_PRO_SCREEN.width_mm,
        height_mm=PAPER_PRO_SCREEN.height_mm,
    )


def test_a_double_that_renders_nothing_reports_no_notice():
    renderer = _TextDroppingRenderer()

    result = renderer.render(
        make_page("hidden", visible=False),
        screen=PAPER_PRO_SCREEN,
        palette=make_palette(),
        style=make_style(),
    )

    assert result.notices == ()
    assert renderer.identity is not None
    assert len(renderer.identity) == 64
