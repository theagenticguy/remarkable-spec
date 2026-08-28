"""Behavioural tests for the OCR ports' value objects.

The module under test carries no I/O, so every test here is pure: no network, no
AWS, no device, no subprocess, no filesystem. What is worth testing is what the
value objects *refuse* and what they *derive* -- the two mechanisms the design
uses to make defect 3 (a cache key that misses a component and serves a stale
row) unrepresentable rather than merely discouraged.

Three groups do the load-bearing work:

``digest``/``canonical``
    Golden hex values pin the digest bodies. The module docstring requires
    ``RasterImage``'s digest to stay byte-identical to its twin in
    ``ports/export.py`` forever; a hex constant is what makes accidental drift a
    test failure instead of a silent cache split.
``answering``/``attributed``
    The echoed cache-key components must be derived from the request, the
    binding and the raster -- never hand-copied. Tests assert a double *cannot*
    fill them wrongly through the sanctioned constructor.
cache-key composition
    The app composes ``(rm_hash, request.digest(), fingerprint,
    sorted(provider_ids))``. Those tests vary one component at a time -- prompt
    text, render DPI, fingerprint, surviving recognizer set -- and assert the key
    moves, which is the whole point of the design.
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import get_protocol_members

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from rmspec.domain.ports.ocr import (
    _FIELD_SEPARATOR,
    _MAGIC_BY_MEDIA,
    Decoding,
    ImageMedia,
    PageRasterLike,
    RasterImage,
    ReasoningEffort,
    Recognition,
    StopReason,
    TextRecognizer,
    TokenUsage,
    VisionCompletion,
    VisionLanguageModel,
    VisionRequest,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_BYTES = PNG_MAGIC + b"pixels"
JPEG_BYTES = JPEG_MAGIC + b"pixels"
HEX_64 = re.compile(r"\A[0-9a-f]{64}\Z")


# ───────────────────────────── builders ─────────────────────────────


def make_raster(
    *,
    page_ref: str = "doc-1/page-1",
    media: ImageMedia = ImageMedia.PNG,
    data: bytes | None = None,
    width: int = 1620,
    height: int = 2160,
    render_dpi: int = 229,
) -> RasterImage:
    if data is None:
        data = PNG_BYTES if media is ImageMedia.PNG else JPEG_BYTES
    return RasterImage(
        page_ref=page_ref,
        media=media,
        data=data,
        width=width,
        height=height,
        render_dpi=render_dpi,
    )


def make_decoding(
    *,
    max_output_tokens: int = 4096,
    temperature: float = 0.0,
    reasoning: ReasoningEffort = ReasoningEffort.NONE,
) -> Decoding:
    return Decoding(
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        reasoning=reasoning,
    )


def make_request(
    *,
    prompt: str = "Transcribe this page.",
    system: str | None = None,
    decoding: Decoding | None = None,
    images: tuple[RasterImage, ...] = (),
) -> VisionRequest:
    return VisionRequest(
        prompt=prompt,
        decoding=decoding if decoding is not None else make_decoding(),
        system=system,
        images=images,
    )


def cache_key(
    *,
    rm_hash: str,
    request: VisionRequest,
    fingerprint: str,
    provider_ids: list[str],
) -> tuple[str, str, str, tuple[str, ...]]:
    """Compose the cache key exactly as the module docstring specifies."""
    return (rm_hash, request.digest(), fingerprint, tuple(sorted(provider_ids)))


# ───────────────────── doubles and structural twins ─────────────────────


class TwinMedia(StrEnum):
    """Another slice's encoding enum, to prove ``media: str`` really is covariant."""

    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class ExportRasterTwin:
    """A field-for-field twin of ``RasterImage`` owned by a different slice."""

    def __init__(
        self,
        *,
        page_ref: str,
        media: TwinMedia,
        data: bytes,
        width: int,
        height: int,
        render_dpi: int,
    ) -> None:
        self._page_ref = page_ref
        self._media = media
        self._data = data
        self._width = width
        self._height = height
        self._render_dpi = render_dpi

    @property
    def page_ref(self) -> str:
        """Return the page identity."""
        return self._page_ref

    @property
    def media(self) -> TwinMedia:
        """Return this slice's own encoding enum member."""
        return self._media

    @property
    def data(self) -> bytes:
        """Return the encoded bytes."""
        return self._data

    @property
    def width(self) -> int:
        """Return the pixel width."""
        return self._width

    @property
    def height(self) -> int:
        """Return the pixel height."""
        return self._height

    @property
    def render_dpi(self) -> int:
        """Return the render DPI."""
        return self._render_dpi


class ScriptedVisionModel:
    """A ``VisionLanguageModel`` double keyed by request digest, never by call order."""

    def __init__(self, *, fingerprint: str, replies: dict[str, str]) -> None:
        self._fingerprint = fingerprint
        self._replies = dict(replies)

    @property
    def fingerprint(self) -> str:
        """Return this binding's identity."""
        return self._fingerprint

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Return the reply scripted for ``request``."""
        return VisionCompletion.answering(
            request,
            fingerprint=self._fingerprint,
            text=self._replies[request.digest()],
            stop_reason=StopReason.COMPLETE,
        )


class PageKeyedRecognizer:
    """A ``TextRecognizer`` double that looks its reading up by page, not by call order."""

    def __init__(self, *, provider_id: str, by_page: dict[str, str], claimed_page: str) -> None:
        self._provider_id = provider_id
        self._by_page = dict(by_page)
        self._claimed_page = claimed_page

    @property
    def provider_id(self) -> str:
        """Return this engine's slug."""
        return self._provider_id

    def recognize(self, image: RasterImage, /) -> Recognition:
        """Return the reading for ``image``, attributed through the sanctioned constructor."""
        # `claimed_page` is a deliberately wrong page slot: `attributed` must win.
        assert self._claimed_page
        return Recognition.attributed(
            image,
            provider_id=self._provider_id,
            text=self._by_page[image.page_ref],
        )


# ───────────────────────────── ImageMedia ─────────────────────────────


def test_image_media_is_an_encoding_fact_not_a_mime_token():
    assert ImageMedia("png") is ImageMedia.PNG
    assert ImageMedia("jpeg") is ImageMedia.JPEG
    with pytest.raises(ValueError, match="not a valid ImageMedia"):
        ImageMedia("image/png")


def test_every_image_media_member_has_magic_bytes_to_check_against():
    # A member added without magic bytes would KeyError inside the validator
    # instead of rejecting the lie the enum exists to stop.
    assert set(_MAGIC_BY_MEDIA) == set(ImageMedia)
    assert all(magic for magic in _MAGIC_BY_MEDIA.values())


# ─────────────────────────── ReasoningEffort ───────────────────────────


def test_reasoning_effort_is_intent_with_a_no_reasoning_option():
    assert [effort.value for effort in ReasoningEffort] == ["none", "low", "medium", "high"]
    assert make_decoding().reasoning is ReasoningEffort.NONE


def test_every_reasoning_effort_changes_the_decoding_canonical_form():
    canonicals = {make_decoding(reasoning=effort).canonical() for effort in ReasoningEffort}
    assert len(canonicals) == len(ReasoningEffort)


# ───────────────────────────── StopReason ─────────────────────────────


def test_stop_reason_names_the_four_domain_outcomes():
    assert [reason.value for reason in StopReason] == [
        "complete",
        "output_limit",
        "stop_sequence",
        "refusal",
    ]


@pytest.mark.parametrize("reason", list(StopReason))
def test_is_complete_is_true_only_for_a_model_that_finished_on_its_own_terms(
    reason: StopReason,
) -> None:
    completion = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="body",
        stop_reason=reason,
    )
    assert completion.is_complete == (reason is StopReason.COMPLETE)


def test_truncation_and_refusal_are_data_rather_than_exceptions():
    truncated = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="half a pa",
        stop_reason=StopReason.OUTPUT_LIMIT,
    )
    refused = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="",
        stop_reason=StopReason.REFUSAL,
    )
    assert not truncated.is_complete
    assert truncated.text == "half a pa"
    assert not refused.is_complete
    assert refused.text == ""


# ───────────────────────────── RasterImage ─────────────────────────────


def test_raster_accepts_matching_png_and_jpeg_bytes():
    assert make_raster(media=ImageMedia.PNG).media is ImageMedia.PNG
    assert make_raster(media=ImageMedia.JPEG).media is ImageMedia.JPEG


@pytest.mark.parametrize(
    ("media", "data"),
    [
        (ImageMedia.PNG, JPEG_BYTES),
        (ImageMedia.JPEG, PNG_BYTES),
        (ImageMedia.PNG, b"\x89PN"),
        (ImageMedia.JPEG, b"\xff\xd8"),
        (ImageMedia.PNG, b"GIF89a"),
    ],
)
def test_raster_rejects_bytes_that_contradict_the_declared_encoding(
    media: ImageMedia,
    data: bytes,
) -> None:
    with pytest.raises(ValidationError, match="declared media"):
        make_raster(media=media, data=data)


@given(data=st.binary(min_size=1, max_size=24))
@settings(deadline=None, max_examples=50)
def test_raster_rejects_any_body_not_beginning_with_the_declared_magic(data: bytes) -> None:
    assume(not data.startswith(PNG_MAGIC))
    with pytest.raises(ValidationError, match="declared media"):
        make_raster(media=ImageMedia.PNG, data=data)


@pytest.mark.parametrize(
    "overrides",
    [
        {"page_ref": ""},
        {"data": b""},
        {"width": 0},
        {"width": -1},
        {"height": 0},
        {"render_dpi": 0},
        {"render_dpi": -229},
    ],
)
def test_raster_rejects_degenerate_identity_and_geometry(overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "page_ref": "doc-1/page-1",
        "media": ImageMedia.PNG,
        "data": PNG_BYTES,
        "width": 1620,
        "height": 2160,
        "render_dpi": 229,
    }
    with pytest.raises(ValidationError):
        RasterImage(**(fields | overrides))  # ty: ignore[invalid-argument-type]


def test_raster_rejects_an_unknown_encoding():
    with pytest.raises(ValidationError):
        RasterImage(
            page_ref="doc-1/page-1",
            media="webp",
            data=PNG_BYTES,
            width=1,
            height=1,
            render_dpi=229,
        )


def test_raster_is_frozen_and_forbids_unknown_fields():
    raster = make_raster()
    with pytest.raises(ValidationError):
        raster.width = 1  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        RasterImage(
            page_ref="p",
            media=ImageMedia.PNG,
            data=PNG_BYTES,
            width=1,
            height=1,
            render_dpi=229,
            scale=2.0,  # ty: ignore[unknown-argument]
        )


def test_raster_digest_is_a_lowercase_sha256_pinned_against_drift():
    # The export slice defines a field-for-field twin whose digest body must stay
    # byte-identical to this one forever, or identical pixels hash to two cache
    # keys. This constant is what turns drift in either body into a failure.
    raster = make_raster(
        page_ref="p1",
        media=ImageMedia.PNG,
        data=PNG_MAGIC + b"body",
        width=10,
        height=20,
        render_dpi=229,
    )
    assert HEX_64.match(raster.digest())
    assert raster.digest() == "b00f243efbed6ed476e592e2120e80dee63291d7cfb1edb2b408a398f3539b8a"


def test_raster_digest_excludes_page_ref_so_identical_pixels_share_one_row():
    assert (
        make_raster(page_ref="doc-1/page-3").digest()
        == make_raster(page_ref="doc-9/page-7").digest()
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 1621},
        {"height": 2161},
        {"render_dpi": 300},
        {"data": PNG_MAGIC + b"other"},
        {"media": ImageMedia.JPEG},
    ],
)
def test_raster_digest_moves_when_pixels_or_scale_move(overrides: dict[str, object]) -> None:
    baseline = make_raster()
    assert make_raster(**overrides).digest() != baseline.digest()  # ty: ignore[invalid-argument-type]


def test_raster_digest_survives_a_python_mode_round_trip():
    raster = make_raster()
    restored = RasterImage.model_validate(raster.model_dump())
    assert restored == raster
    assert restored.digest() == raster.digest()


def test_raster_cannot_be_serialised_to_json_because_pixels_are_not_text():
    # Persistence stores `digest()`, never the pixels: real PNG bytes are not
    # valid UTF-8, so a JSON row of this value object is not a design option.
    with pytest.raises(PydanticSerializationError):
        make_raster().model_dump_json()


@given(
    first=st.tuples(
        st.integers(min_value=1, max_value=4000),
        st.integers(min_value=1, max_value=4000),
        st.integers(min_value=1, max_value=1200),
        st.binary(max_size=16),
    ),
    second=st.tuples(
        st.integers(min_value=1, max_value=4000),
        st.integers(min_value=1, max_value=4000),
        st.integers(min_value=1, max_value=1200),
        st.binary(max_size=16),
    ),
)
@settings(deadline=None, max_examples=75)
def test_raster_digest_separates_distinct_content_and_scale(
    first: tuple[int, int, int, bytes],
    second: tuple[int, int, int, bytes],
) -> None:
    def digest_of(fields: tuple[int, int, int, bytes]) -> str:
        width, height, dpi, tail = fields
        return make_raster(
            data=PNG_MAGIC + tail,
            width=width,
            height=height,
            render_dpi=dpi,
        ).digest()

    if first == second:
        assert digest_of(first) == digest_of(second)
    else:
        assert digest_of(first) != digest_of(second)


def test_from_raster_adopts_another_slices_twin_without_a_field_splat():
    twin = ExportRasterTwin(
        page_ref="doc-1/page-4",
        media=TwinMedia.PNG,
        data=PNG_BYTES,
        width=1620,
        height=2160,
        render_dpi=229,
    )
    source: PageRasterLike = twin
    adopted = RasterImage.from_raster(source)
    assert adopted.page_ref == "doc-1/page-4"
    assert adopted.media is ImageMedia.PNG
    assert adopted.digest() == make_raster(page_ref="doc-1/page-4").digest()


def test_from_raster_is_idempotent_on_this_slices_own_value():
    raster = make_raster(media=ImageMedia.JPEG)
    assert RasterImage.from_raster(raster) == raster
    assert RasterImage.from_raster(raster).digest() == raster.digest()


def test_from_raster_rejects_an_encoding_this_slice_does_not_know():
    twin = ExportRasterTwin(
        page_ref="doc-1/page-4",
        media=TwinMedia.WEBP,
        data=b"RIFF....WEBP",
        width=1,
        height=1,
        render_dpi=229,
    )
    with pytest.raises(ValueError, match="webp"):
        RasterImage.from_raster(twin)


def test_from_raster_runs_the_destinations_validators_rather_than_copying_fields():
    liar = ExportRasterTwin(
        page_ref="doc-1/page-4",
        media=TwinMedia.PNG,
        data=JPEG_BYTES,
        width=1,
        height=1,
        render_dpi=229,
    )
    with pytest.raises(ValidationError, match="declared media"):
        RasterImage.from_raster(liar)


def test_from_raster_takes_its_source_positionally_only():
    with pytest.raises(TypeError, match="positional-only"):
        RasterImage.from_raster(source=make_raster())  # ty: ignore[positional-only-parameter-as-kwarg]


# ─────────────────────────────── Decoding ───────────────────────────────


def test_decoding_canonical_is_fixed_precision():
    assert (
        make_decoding(
            max_output_tokens=1024, temperature=0.5, reasoning=ReasoningEffort.HIGH
        ).canonical()
        == "max_output_tokens=1024;temperature=0.500000;reasoning=high"
    )


def test_decoding_canonical_separates_values_that_differ_within_its_precision():
    assert (
        make_decoding(temperature=0.5).canonical()
        != make_decoding(temperature=0.500001).canonical()
    )


def test_decoding_canonical_collapses_differences_below_its_precision():
    # Documented trade-off: six decimal places, so a float repr change cannot
    # invalidate every cache row. Sub-microscopic temperature deltas share a row.
    assert (
        make_decoding(temperature=0.5).canonical()
        == make_decoding(temperature=0.5 + 1e-12).canonical()
    )


@pytest.mark.parametrize("temperature", [0.0, 1.0, 0.5])
def test_decoding_accepts_the_closed_unit_interval(temperature: float) -> None:
    assert make_decoding(temperature=temperature).temperature == temperature


@pytest.mark.parametrize(
    "temperature",
    [-0.000001, 1.000001, -1.0, 2.0, math.nan, math.inf, -math.inf],
)
def test_decoding_rejects_temperatures_outside_the_unit_interval(temperature: float) -> None:
    with pytest.raises(ValidationError):
        make_decoding(temperature=temperature)


@pytest.mark.parametrize("tokens", [0, -1])
def test_decoding_rejects_a_non_positive_output_budget(tokens: int) -> None:
    with pytest.raises(ValidationError):
        make_decoding(max_output_tokens=tokens)


def test_decoding_is_frozen_and_forbids_unknown_fields():
    decoding = make_decoding()
    with pytest.raises(ValidationError):
        decoding.temperature = 0.9  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        Decoding(
            max_output_tokens=1,
            temperature=0.0,
            top_p=0.9,  # ty: ignore[unknown-argument]
        )


@given(
    tokens=st.integers(min_value=1, max_value=1_000_000),
    temperature=st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    effort=st.sampled_from(ReasoningEffort),
)
@settings(deadline=None, max_examples=75)
def test_decoding_canonical_is_parseable_and_stable(
    tokens: int,
    temperature: float,
    effort: ReasoningEffort,
) -> None:
    canonical = make_decoding(
        max_output_tokens=tokens,
        temperature=temperature,
        reasoning=effort,
    ).canonical()
    parsed = dict(pair.split("=", 1) for pair in canonical.split(";"))
    assert int(parsed["max_output_tokens"]) == tokens
    assert parsed["reasoning"] == effort.value
    assert parsed["temperature"] == f"{temperature:.6f}"
    rebuilt = make_decoding(
        max_output_tokens=tokens,
        temperature=float(parsed["temperature"]),
        reasoning=effort,
    )
    assert rebuilt.canonical() == canonical


# ───────────────────────────── VisionRequest ─────────────────────────────


def test_request_defaults_to_no_system_turn_and_no_images():
    request = make_request()
    assert request.system is None
    assert request.images == ()


def test_request_normalises_an_image_sequence_to_a_tuple():
    request = VisionRequest(
        prompt="p",
        decoding=make_decoding(),
        images=[make_raster()],
    )
    assert isinstance(request.images, tuple)


def test_request_rejects_an_empty_prompt():
    with pytest.raises(ValidationError):
        make_request(prompt="")


def test_request_is_frozen_hashable_and_usable_as_a_dictionary_key():
    first = make_request(prompt="a", images=(make_raster(),))
    same = make_request(prompt="a", images=(make_raster(),))
    other = make_request(prompt="b", images=(make_raster(),))
    scripted = {first: "answer-a", other: "answer-b"}
    assert scripted[same] == "answer-a"
    assert hash(first) == hash(same)
    with pytest.raises(ValidationError):
        first.prompt = "b"  # ty: ignore[invalid-assignment]


def test_request_forbids_unknown_fields_so_a_new_input_cannot_bypass_the_digest():
    with pytest.raises(ValidationError):
        VisionRequest(
            prompt="p",
            decoding=make_decoding(),
            prompt_revision=3,  # ty: ignore[unknown-argument]
        )


def test_request_digest_is_a_lowercase_sha256_pinned_against_drift():
    request = make_request(
        prompt="p",
        system="s",
        decoding=make_decoding(
            max_output_tokens=1024,
            temperature=0.0,
            reasoning=ReasoningEffort.HIGH,
        ),
        images=(
            make_raster(
                page_ref="p1",
                data=PNG_MAGIC + b"body",
                width=10,
                height=20,
                render_dpi=229,
            ),
        ),
    )
    assert HEX_64.match(request.digest())
    assert request.digest() == "2299741b3db530fc9f16b494363efe716060d61e50c54fb2cb9fb1f98fac6c3c"


def test_request_digest_hashes_the_prompt_text_so_no_version_field_is_needed():
    assert (
        make_request(prompt="Transcribe this page.").digest()
        != make_request(prompt="Transcribe this page!").digest()
    )


def test_request_digest_moves_when_the_system_turn_changes():
    assert make_request(system=None).digest() != make_request(system="Be terse.").digest()
    assert make_request(system="Be terse.").digest() != make_request(system="Be verbose.").digest()


def test_request_digest_treats_an_empty_system_turn_as_no_system_turn():
    # Documented consequence of `(self.system or "")`: an empty string carries no
    # instruction, so it must not fork the cache from `None`.
    assert make_request(system=None).digest() == make_request(system="").digest()


@pytest.mark.parametrize(
    "decoding",
    [
        Decoding(max_output_tokens=8192, temperature=0.0),
        Decoding(max_output_tokens=4096, temperature=1.0),
        Decoding(max_output_tokens=4096, temperature=0.0, reasoning=ReasoningEffort.HIGH),
    ],
)
def test_request_digest_moves_when_any_decoding_setting_moves(decoding: Decoding) -> None:
    assert make_request(decoding=decoding).digest() != make_request().digest()


def test_request_digest_moves_when_an_images_pixels_or_dpi_move():
    baseline = make_request(images=(make_raster(),))
    other_dpi = make_request(images=(make_raster(render_dpi=300),))
    other_pixels = make_request(images=(make_raster(data=PNG_MAGIC + b"other"),))
    assert other_dpi.digest() != baseline.digest()
    assert other_pixels.digest() != baseline.digest()


def test_request_digest_ignores_which_page_slot_an_image_was_rendered_for():
    assert (
        make_request(images=(make_raster(page_ref="page-1"),)).digest()
        == make_request(images=(make_raster(page_ref="page-2"),)).digest()
    )


def test_request_digest_depends_on_image_order_and_count():
    front = make_raster(page_ref="a", data=PNG_MAGIC + b"front")
    back = make_raster(page_ref="b", data=PNG_MAGIC + b"back")
    assert (
        make_request(images=(front, back)).digest() != make_request(images=(back, front)).digest()
    )
    assert make_request(images=(front,)).digest() != make_request(images=(front, front)).digest()
    assert make_request(images=()).digest() != make_request(images=(front,)).digest()


def test_request_digest_frames_the_system_and_prompt_boundary():
    assert (
        make_request(system="a", prompt="bc").digest()
        != make_request(system="ab", prompt="c").digest()
    )


@pytest.mark.xfail(
    reason=(
        "framing defect: the separator is not escaped, so a field that itself contains "
        "0x1f makes the concatenation ambiguous and two distinct requests collide on one "
        "cache key. Reachable because prompts embed recognizer text, which is arbitrary."
    ),
    strict=True,
)
def test_request_digest_frames_fields_even_when_a_field_contains_the_separator():
    separator = _FIELD_SEPARATOR.decode()
    assert (
        make_request(system="a", prompt=f"b{separator}c").digest()
        != make_request(system=f"a{separator}b", prompt="c").digest()
    )


# ────────────────────────────── TokenUsage ──────────────────────────────


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(0, 0), (1, 0), (0, 1), (99, 1024)])
def test_token_usage_accepts_zero_and_positive_counts(
    input_tokens: int,
    output_tokens: int,
) -> None:
    usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    assert (usage.input_tokens, usage.output_tokens) == (input_tokens, output_tokens)


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(-1, 0), (0, -1)])
def test_token_usage_rejects_negative_counts(input_tokens: int, output_tokens: int) -> None:
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def test_token_usage_is_frozen_and_forbids_unknown_fields():
    usage = TokenUsage(input_tokens=1, output_tokens=1)
    with pytest.raises(ValidationError):
        usage.input_tokens = 2  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        TokenUsage(
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=1,  # ty: ignore[unknown-argument]
        )


# ──────────────────────────── VisionCompletion ────────────────────────────


def test_answering_derives_both_echoed_cache_key_components():
    request = make_request(prompt="Merge these readings.", images=(make_raster(),))
    completion = VisionCompletion.answering(
        request,
        fingerprint="bedrock/opus@3",
        text="merged",
        stop_reason=StopReason.COMPLETE,
    )
    assert completion.request_digest == request.digest()
    assert completion.model_fingerprint == "bedrock/opus@3"
    assert completion.reasoning is None
    assert completion.usage is None


def test_answering_gives_an_adapter_no_opportunity_to_echo_a_stale_request():
    first = make_request(prompt="page one")
    second = make_request(prompt="page two")
    model = ScriptedVisionModel(
        fingerprint="fp-1",
        replies={first.digest(): "one", second.digest(): "two"},
    )
    binding: VisionLanguageModel = model
    # Out of binding order on purpose: a FIFO double would answer "one" here.
    later = binding.complete(second)
    earlier = binding.complete(first)
    assert (later.text, later.request_digest) == ("two", second.digest())
    assert (earlier.text, earlier.request_digest) == ("one", first.digest())


def test_answering_carries_reasoning_and_usage_through_untouched():
    usage = TokenUsage(input_tokens=1200, output_tokens=340)
    completion = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="body",
        stop_reason=StopReason.COMPLETE,
        reasoning="let me look at the strokes",
        usage=usage,
    )
    assert completion.reasoning == "let me look at the strokes"
    assert completion.usage == usage


def test_answering_reports_an_ignored_reasoning_request_as_absent_reasoning():
    request = make_request(decoding=make_decoding(reasoning=ReasoningEffort.HIGH))
    completion = VisionCompletion.answering(
        request,
        fingerprint="fp-1",
        text="body",
        stop_reason=StopReason.COMPLETE,
    )
    assert completion.reasoning is None


def test_answering_takes_its_request_positionally_and_the_rest_by_keyword():
    request = make_request()
    with pytest.raises(TypeError, match="positional-only"):
        VisionCompletion.answering(
            request=request,  # ty: ignore[positional-only-parameter-as-kwarg]
            fingerprint="fp-1",
            text="t",
            stop_reason=StopReason.COMPLETE,
        )
    with pytest.raises(TypeError, match="positional argument"):
        VisionCompletion.answering(request, "fp-1", "t", StopReason.COMPLETE)  # ty: ignore[too-many-positional-arguments, missing-argument]


def test_answering_rejects_an_anonymous_binding():
    with pytest.raises(ValidationError):
        VisionCompletion.answering(
            make_request(),
            fingerprint="",
            text="body",
            stop_reason=StopReason.COMPLETE,
        )


def test_completion_treats_empty_text_as_data_not_failure():
    completion = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="",
        stop_reason=StopReason.COMPLETE,
    )
    assert completion.text == ""
    assert completion.is_complete


@pytest.mark.parametrize(
    "overrides",
    [{"request_digest": ""}, {"model_fingerprint": ""}],
)
def test_completion_rejects_a_blank_cache_key_echo(overrides: dict[str, str]) -> None:
    fields: dict[str, object] = {
        "text": "body",
        "stop_reason": StopReason.COMPLETE,
        "request_digest": "d" * 64,
        "model_fingerprint": "fp-1",
    }
    with pytest.raises(ValidationError):
        VisionCompletion(**(fields | overrides))  # ty: ignore[invalid-argument-type]


def test_completion_is_frozen_and_forbids_unknown_fields():
    completion = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="body",
        stop_reason=StopReason.COMPLETE,
    )
    with pytest.raises(ValidationError):
        completion.text = "tampered"  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        VisionCompletion(
            text="body",
            stop_reason=StopReason.COMPLETE,
            request_digest="d" * 64,
            model_fingerprint="fp-1",
            region="us-west-2",  # ty: ignore[unknown-argument]
        )


def test_completion_round_trips_through_json_with_its_nested_usage():
    completion = VisionCompletion.answering(
        make_request(),
        fingerprint="fp-1",
        text="body",
        stop_reason=StopReason.OUTPUT_LIMIT,
        reasoning="thinking",
        usage=TokenUsage(input_tokens=7, output_tokens=8),
    )
    assert VisionCompletion.model_validate_json(completion.model_dump_json()) == completion


# ────────────────────────────── Recognition ──────────────────────────────


def test_attributed_derives_the_page_from_the_raster_that_was_read():
    raster = make_raster(page_ref="doc-1/page-7")
    reading = Recognition.attributed(
        raster,
        provider_id="apple-vision@2",
        text="handwriting",
        mean_confidence=0.81,
    )
    assert reading.page_ref == "doc-1/page-7"
    assert reading.provider_id == "apple-vision@2"
    assert reading.text == "handwriting"
    assert reading.mean_confidence == 0.81


def test_attributed_accepts_another_slices_raster_structurally():
    twin = ExportRasterTwin(
        page_ref="doc-1/page-9",
        media=TwinMedia.JPEG,
        data=JPEG_BYTES,
        width=1,
        height=1,
        render_dpi=229,
    )
    source: PageRasterLike = twin
    assert Recognition.attributed(source, provider_id="p", text="t").page_ref == "doc-1/page-9"


def test_a_recognizer_cannot_attribute_a_reading_to_another_page():
    pages = {"doc-1/page-1": "first page text", "doc-1/page-2": "second page text"}
    engine = PageKeyedRecognizer(
        provider_id="aws-textract@1",
        by_page=pages,
        claimed_page="doc-1/page-99",
    )
    recognizer: TextRecognizer = engine
    second = recognizer.recognize(make_raster(page_ref="doc-1/page-2"))
    first = recognizer.recognize(make_raster(page_ref="doc-1/page-1"))
    assert (second.page_ref, second.text) == ("doc-1/page-2", "second page text")
    assert (first.page_ref, first.text) == ("doc-1/page-1", "first page text")
    assert {first.provider_id, second.provider_id} == {"aws-textract@1"}


def test_hydrating_a_stored_reading_re_stamps_the_page_it_is_read_for():
    # A cached row is legitimately shared by two pages with identical pixels, so a
    # stored page_ref may name the wrong slot. `attributed` is the re-stamp.
    stored = Recognition(
        provider_id="apple-vision@2",
        page_ref="doc-1/page-3",
        text="shared pixels",
        mean_confidence=0.5,
    )
    rehydrated = Recognition.attributed(
        make_raster(page_ref="doc-1/page-7"),
        provider_id=stored.provider_id,
        text=stored.text,
        mean_confidence=stored.mean_confidence,
    )
    assert rehydrated.page_ref == "doc-1/page-7"
    assert rehydrated.model_dump(exclude={"page_ref"}) == stored.model_dump(exclude={"page_ref"})


def test_attributed_takes_its_raster_positionally_only():
    with pytest.raises(TypeError, match="positional-only"):
        Recognition.attributed(image=make_raster(), provider_id="p", text="t")  # ty: ignore[positional-only-parameter-as-kwarg]


def test_a_blank_reading_is_a_success_with_no_confidence_to_report():
    blank = Recognition.attributed(make_raster(), provider_id="apple-vision@2", text="")
    assert blank.text == ""
    assert blank.mean_confidence is None
    assert not blank.has_text


@pytest.mark.parametrize("text", ["", " ", "   ", "\n\t \r"])
def test_has_text_reports_a_whitespace_only_reading_as_a_blank_page(text: str) -> None:
    assert not Recognition.attributed(make_raster(), provider_id="p", text=text).has_text


@pytest.mark.parametrize("text", ["0", "a", "  x  ", "\n.\n"])
def test_has_text_reports_any_non_whitespace_reading_as_text(text: str) -> None:
    assert Recognition.attributed(make_raster(), provider_id="p", text=text).has_text


@pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5, None])
def test_recognition_accepts_the_closed_unit_interval_and_no_signal(
    confidence: float | None,
) -> None:
    reading = Recognition.attributed(
        make_raster(),
        provider_id="p",
        text="t",
        mean_confidence=confidence,
    )
    assert reading.mean_confidence == confidence


@pytest.mark.parametrize("confidence", [-0.000001, 1.000001, -1.0, 2.0, math.nan, math.inf])
def test_recognition_rejects_confidence_outside_the_unit_interval(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Recognition.attributed(
            make_raster(),
            provider_id="p",
            text="t",
            mean_confidence=confidence,
        )


@given(confidence=st.floats(allow_nan=False, allow_infinity=False, width=32))
@settings(deadline=None, max_examples=75)
def test_recognition_confidence_acceptance_region_is_exactly_the_unit_interval(
    confidence: float,
) -> None:
    if 0.0 <= confidence <= 1.0:
        reading = Recognition.attributed(
            make_raster(),
            provider_id="p",
            text="t",
            mean_confidence=confidence,
        )
        assert reading.mean_confidence == confidence
    else:
        with pytest.raises(ValidationError):
            Recognition.attributed(
                make_raster(),
                provider_id="p",
                text="t",
                mean_confidence=confidence,
            )


@pytest.mark.parametrize("overrides", [{"provider_id": ""}, {"page_ref": ""}])
def test_recognition_rejects_an_unattributed_reading(overrides: dict[str, str]) -> None:
    fields: dict[str, object] = {
        "provider_id": "apple-vision@2",
        "page_ref": "doc-1/page-1",
        "text": "t",
    }
    with pytest.raises(ValidationError):
        Recognition(**(fields | overrides))  # ty: ignore[invalid-argument-type]


def test_recognition_is_frozen_and_forbids_unknown_fields():
    reading = Recognition.attributed(make_raster(), provider_id="p", text="t")
    with pytest.raises(ValidationError):
        reading.text = "tampered"  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        Recognition(
            provider_id="p",
            page_ref="page-1",
            text="t",
            lines=[],  # ty: ignore[unknown-argument]
        )


def test_recognition_round_trips_through_json():
    reading = Recognition.attributed(
        make_raster(),
        provider_id="apple-vision@2",
        text="handwriting",
        mean_confidence=0.25,
    )
    assert Recognition.model_validate_json(reading.model_dump_json()) == reading


# ─────────────────────── the ports' published surface ───────────────────────


def test_the_model_port_publishes_no_provider_deployment_axis():
    # No `model_id`, no `region`, no `timeout`: model identity for the app is
    # `fingerprint`, and anything else would be AWS imported by another name.
    assert get_protocol_members(VisionLanguageModel) == {"fingerprint", "complete"}


def test_the_recognizer_port_publishes_no_fingerprint_and_no_timeout():
    # Engine revision lives inside the `provider_id` slug; a `fingerprint` member
    # would be a constant in every double and leave defect 3 unfalsifiable.
    assert get_protocol_members(TextRecognizer) == {"provider_id", "recognize"}


def test_the_raster_view_publishes_exactly_the_six_members_a_twin_must_supply():
    assert get_protocol_members(PageRasterLike) == {
        "page_ref",
        "media",
        "data",
        "width",
        "height",
        "render_dpi",
    }


@pytest.mark.parametrize("port", [VisionLanguageModel, TextRecognizer, PageRasterLike])
def test_the_ports_are_not_runtime_checkable(port: type) -> None:
    with pytest.raises(TypeError, match="runtime_checkable"):
        isinstance(make_raster(), port)


# ─────────────────────── cache-key composition (defect 3) ───────────────────────


def test_the_composed_cache_key_is_stable_for_identical_work():
    request = make_request(prompt="Merge.", images=(make_raster(),))
    first = cache_key(
        rm_hash="a" * 64,
        request=request,
        fingerprint="fp-1",
        provider_ids=["apple-vision@2", "aws-textract@1"],
    )
    # Same work, different fan-out completion order.
    second = cache_key(
        rm_hash="a" * 64,
        request=make_request(prompt="Merge.", images=(make_raster(),)),
        fingerprint="fp-1",
        provider_ids=["aws-textract@1", "apple-vision@2"],
    )
    assert first == second


@pytest.mark.parametrize(
    "variant",
    ["prompt", "system", "render_dpi", "decoding", "fingerprint", "rm_hash", "recognizers"],
)
def test_changing_any_cache_key_component_moves_the_key(variant: str) -> None:
    baseline_kwargs: dict[str, object] = {
        "rm_hash": "a" * 64,
        "fingerprint": "fp-1",
        "provider_ids": ["apple-vision@2", "aws-textract@1"],
    }
    request_kwargs: dict[str, object] = {
        "prompt": "Merge.",
        "system": "Be terse.",
        "decoding": make_decoding(),
        "images": (make_raster(),),
    }
    baseline = cache_key(request=make_request(**request_kwargs), **baseline_kwargs)

    match variant:
        case "prompt":
            request_kwargs["prompt"] = "Merge, carefully."
        case "system":
            request_kwargs["system"] = "Be verbose."
        case "render_dpi":
            request_kwargs["images"] = (make_raster(render_dpi=300),)
        case "decoding":
            request_kwargs["decoding"] = make_decoding(reasoning=ReasoningEffort.HIGH)
        case "fingerprint":
            baseline_kwargs["fingerprint"] = "fp-2"
        case "rm_hash":
            baseline_kwargs["rm_hash"] = "b" * 64
        case _:
            baseline_kwargs["provider_ids"] = ["aws-textract@1"]

    variant_key = cache_key(request=make_request(**request_kwargs), **baseline_kwargs)
    assert variant_key != baseline


def test_a_row_whose_echo_disagrees_with_its_key_is_detectable():
    stored_for = make_request(prompt="Merge.")
    read_for = make_request(prompt="Merge, carefully.")
    completion = VisionCompletion.answering(
        stored_for,
        fingerprint="fp-1",
        text="merged",
        stop_reason=StopReason.COMPLETE,
    )
    key = cache_key(
        rm_hash="a" * 64,
        request=read_for,
        fingerprint="fp-1",
        provider_ids=["apple-vision@2"],
    )
    assert completion.request_digest != key[1]
    assert completion.model_fingerprint == key[2]
