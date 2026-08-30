"""Every tier boundary of ``TranscribePages``, and every way it refuses to pay twice.

How the collaborators are bound here, and why
---------------------------------------------
With local in-memory fakes annotated against their Protocols -- the pattern
``test_app_resolve.py`` sets out and gives the argument for. Nothing here imports
``rmspec.device``, ``rmspec.formats``, ``rmspec.render``, ``rmspec.export``,
``rmspec.persistence`` or ``rmspec.ocr``: the architecture check only scans ``src/``, so
an adapter import in a test would pass the gate while breaking exactly the property the
gate exists to protect, and it would make a pure-policy suite need ``boto3``, ``pyobjc``
and ``cairocffi`` installed to test string matching. Conformance is still checked, by
``ty`` rather than by convention, because every fake below is passed to a
Protocol-annotated parameter.

:class:`_Pipeline` binds :class:`~rmspec.app.transcribe.PageRenderPipeline` over
already-rendered pages rather than wrapping a real
:class:`~rmspec.app.render.RenderPages`. The sibling use case has its own suite; what
this one needs to pin is that the pixels, the ``page_hash`` and the ``render_digest``
reach the cache key unchanged, and a fake that hands them over directly is the only
binding that can also produce the page-with-no-pixels state.

Every fake counts its calls, because most of this module's promises are about calls that
must **not** happen: a blank page must not reach a model, a cache hit must not reach a
recognizer, and a truncated completion must not reach the cache.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from rmspec.app import PageSelection
from rmspec.app.render import (
    RenderedPageArtifact,
    RenderPagesRequest,
    RenderPagesResult,
)
from rmspec.app.transcribe import (
    _PRIOR_BLANK,
    _PRIOR_UNAVAILABLE,
    _PRIOR_UNBOUND,
    _PRIOR_UNINDEXED,
    _READER_PREFIX,
    TranscribedPage,
    TranscribePages,
    TranscribePagesRequest,
    TranscribePagesResult,
)
from rmspec.domain.errors import (
    AllRecognizersFailed,
    DegradationKind,
    NoTextRecognized,
    RasterizationFailed,
    RecognitionFailed,
    StoreSchemaMismatchError,
    StoreUnavailableError,
)
from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    OcrArtifact,
    OcrCacheKey,
)
from rmspec.domain.ports.export import ImageMedia, RasterImage
from rmspec.domain.ports.ocr import (
    IndexedHandwriting,
    Recognition,
    StopReason,
    VisionCompletion,
    VisionRequest,
)
from rmspec.domain.ports.ocr import (
    RasterImage as OcrRasterImage,
)
from rmspec.domain.ports.render import (
    PhysicalSize as RenderPhysicalSize,
)
from rmspec.domain.ports.render import (
    RenderedPage,
    RenderStyle,
    TextStyle,
)

DOC = "d3b38661-1111-4111-8111-111111111111"
PAGE_A = "aaaaaaaa-1111-4111-8111-111111111111"
PAGE_B = "bbbbbbbb-2222-4222-8222-222222222222"

HASH_A = "a" * 64
HASH_B = "b" * 64
RENDER_DIGEST = "r" * 64

VISION = "apple-vision@2"
TEXTRACT = "aws-textract@1"
INDEX = "device-index@1"
READER = "reader-fingerprint-1"
ADJUDICATOR = "adjudicator-fingerprint-1"

DPI = 300
NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)
EARLIER = datetime.datetime(2026, 8, 1, 9, 30, tzinfo=datetime.UTC)

INK = "the quick brown fox jumps over the lazy dog"
OTHER = "entirely unrelated words about a completely different subject"

STYLE = RenderStyle(
    thickness_scale=1.5,
    min_padding_mm=10.6,
    text=TextStyle(family="Noto Sans, sans-serif", size_px=32.0, line_height=1.25),
    renderer_revision="render-r1",
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IEND = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"


def _png(width: int, height: int) -> bytes:
    """Build the smallest byte stream ``RasterImage``'s validators accept."""
    return (
        _PNG_SIGNATURE
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + _IEND
    )


# ───────────────────────────────── the fakes ─────────────────────────────────


class _Pipeline:
    """A :class:`PageRenderPipeline` over pages that are already rendered."""

    def __init__(self, *pages: RenderedPageArtifact) -> None:
        self.requested: list[RenderPagesRequest] = []
        self._pages = pages

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        """Hand back the prepared pages under one render identity."""
        self.requested.append(request)
        return RenderPagesResult(
            document_uuid=request.document_uuid,
            pages=self._pages,
            render_digest=RENDER_DIGEST,
            degradations=(),
        )


class _Recognizer:
    """A :class:`TextRecognizer` with one canned reading and an optional outage."""

    def __init__(
        self,
        slug: str,
        *,
        text: str = "",
        confidence: float | None = None,
        failure: RecognitionFailed | None = None,
    ) -> None:
        self.calls = 0
        self._slug = slug
        self._text = text
        self._confidence = confidence
        self._failure = failure

    @property
    def provider_id(self) -> str:
        """Return the engine's stable identity slug."""
        return self._slug

    def recognize(self, image: OcrRasterImage, /) -> Recognition:
        """Return the canned reading, attributed to the raster that was read."""
        self.calls += 1
        if self._failure is not None:
            raise self._failure
        return Recognition.attributed(
            image,
            provider_id=self._slug,
            text=self._text,
            mean_confidence=self._confidence,
        )


class _Index:
    """A :class:`HandwrittenTextIndex` over one row, with a database that can be torn."""

    def __init__(
        self,
        row: IndexedHandwriting | None = None,
        *,
        failure: StoreUnavailableError | None = None,
    ) -> None:
        self.looked_up: list[str] = []
        self._row = row
        self._failure = failure

    @property
    def provider_id(self) -> str:
        """Return the index's stable identity slug."""
        return INDEX

    def lookup(self, page_ref: str, /) -> IndexedHandwriting | None:
        """Return the row for one page, or fail the way a torn read does."""
        self.looked_up.append(page_ref)
        if self._failure is not None:
            raise self._failure
        return self._row


class _Model:
    """A :class:`VisionLanguageModel` that records every request it was sent."""

    def __init__(
        self,
        fingerprint: str,
        *,
        text: str = "",
        stop_reason: StopReason = StopReason.COMPLETE,
    ) -> None:
        self.requests: list[VisionRequest] = []
        self._fingerprint = fingerprint
        self._text = text
        self._stop_reason = stop_reason

    @property
    def fingerprint(self) -> str:
        """The opaque identity of this binding."""
        return self._fingerprint

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Answer, with both cache-key echoes derived rather than copied."""
        self.requests.append(request)
        return VisionCompletion.answering(
            request,
            fingerprint=self._fingerprint,
            text=self._text,
            stop_reason=self._stop_reason,
        )


class _Cache:
    """An :class:`OcrCache` as a dict keyed by digest, total the way the port requires."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[OcrCacheKey, OcrArtifact]] = {}
        self.gets: list[OcrCacheKey] = []
        self.puts: list[tuple[OcrCacheKey, OcrArtifact]] = []

    def get(self, key: OcrCacheKey, /) -> OcrArtifact | None:
        """Return the artifact stored under this exact key, or ``None``."""
        self.gets.append(key)
        entry = self.rows.get(key.digest)
        return None if entry is None else entry[1]

    def put(self, key: OcrCacheKey, artifact: OcrArtifact, /) -> None:
        """Store an artifact under this key, overwriting any earlier entry."""
        self.puts.append((key, artifact))
        self.rows[key.digest] = (key, artifact)

    def superseded(self, key: OcrCacheKey, /) -> OcrCacheKey | None:
        """Return the greatest stored key for the same page under other inputs."""
        qualifying = [
            stored
            for stored, _ in self.rows.values()
            if stored.page_hash == key.page_hash and stored.digest != key.digest
        ]
        if not qualifying:
            return None
        return max(qualifying, key=lambda stored: stored.digest)


# ──────────────────────────────── the builders ────────────────────────────────


def _indexed(text: str, *, page_ref: str = PAGE_A) -> IndexedHandwriting:
    return IndexedHandwriting(page_ref=page_ref, entry_ref=DOC, text=text, generation=7)


def _artifact(
    *,
    page_ref: str = PAGE_A,
    page_index: int = 0,
    page_hash: str = HASH_A,
    pixels: bool = True,
) -> RenderedPageArtifact:
    size = RenderPhysicalSize(
        width_mm=PAPER_PRO_SCREEN.width_mm,
        height_mm=PAPER_PRO_SCREEN.height_mm,
    )
    return RenderedPageArtifact(
        page_ref=page_ref,
        page_index=page_index,
        page_hash=page_hash,
        rendered=RenderedPage(
            page_ref=page_ref,
            svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            size=size,
            stroke_count=12,
            text_block_count=0,
            notices=(),
        ),
        raster=(
            RasterImage(
                page_ref=page_ref,
                media=ImageMedia.PNG,
                data=_png(1620, 2160),
                width=1620,
                height=2160,
                render_dpi=DPI,
            )
            if pixels
            else None
        ),
    )


def _request(
    *,
    render: dict[str, object] | None = None,
    **overrides: object,
) -> TranscribePagesRequest:
    rendering: dict[str, object] = {
        "document_uuid": DOC,
        "selection": PageSelection.all(),
        "max_pages": 50,
        "screen": PAPER_PRO_SCREEN,
        "palette": EXPORT_PALETTE,
        "style": STYLE,
        "raster_dpi": DPI,
    }
    if render is not None:
        rendering.update(render)
    fields: dict[str, object] = {"render": rendering, "now": NOW}
    fields.update(overrides)
    return TranscribePagesRequest.model_validate(fields)


class _Bench:
    """One assembled use case and every fake it was built from."""

    def __init__(
        self,
        *,
        recognizers: tuple[_Recognizer, ...],
        index: _Index | None,
        reader: _Model,
        adjudicator: _Model,
        cache: _Cache,
        pipeline: _Pipeline,
    ) -> None:
        self.recognizers = recognizers
        self.index = index
        self.reader = reader
        self.adjudicator = adjudicator
        self.cache = cache
        self.pipeline = pipeline
        self.use_case = TranscribePages(
            pipeline=pipeline,
            recognizers=recognizers,
            index=index,
            reader=reader,
            adjudicator=adjudicator,
            cache=cache,
        )

    def run(self, request: TranscribePagesRequest | None = None) -> TranscribePagesResult:
        """Transcribe, with the default request unless one is given."""
        return self.use_case.transcribe(_request() if request is None else request)


def _bench(
    *,
    recognizers: tuple[_Recognizer, ...] | None = None,
    index: _Index | None = None,
    reader: _Model | None = None,
    adjudicator: _Model | None = None,
    cache: _Cache | None = None,
    pipeline: _Pipeline | None = None,
) -> _Bench:
    return _Bench(
        recognizers=(_Recognizer(TEXTRACT, text=INK),) if recognizers is None else recognizers,
        index=index,
        reader=_Model(READER, text=INK) if reader is None else reader,
        adjudicator=(_Model(ADJUDICATOR, text=INK) if adjudicator is None else adjudicator),
        cache=_Cache() if cache is None else cache,
        pipeline=_Pipeline(_artifact()) if pipeline is None else pipeline,
    )


def _only(result: TranscribePagesResult) -> TranscribedPage:
    assert len(result.pages) == 1
    return result.pages[0]


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error."""
    setattr(target, field, value)


# ─────────────────── tier 0: three states, and none of them two ───────────────────


def test_an_unindexed_page_is_read_and_paid_for():
    """The normal case: the page was written after the last index build, so it has no row."""
    bench = _bench(index=_Index(None))
    page = _only(bench.run())
    assert bench.index is not None
    assert bench.index.looked_up == [PAGE_A]
    assert page.tier_reached == 3
    assert page.short_circuited is False
    assert len(bench.adjudicator.requests) == 1


def test_an_indexed_blank_row_does_not_suppress_the_paid_read():
    """``text == ""`` is "indexed and found nothing", which must not stand in for a reading."""
    bench = _bench(index=_Index(_indexed("")))
    page = _only(bench.run())
    assert page.tier_reached == 3
    assert page.page.text == INK


@pytest.mark.parametrize(
    ("index", "account"),
    [
        (None, _PRIOR_UNBOUND),
        (_Index(None), _PRIOR_UNINDEXED),
        (_Index(_indexed("")), _PRIOR_BLANK),
        (
            _Index(failure=StoreUnavailableError(store="xochitl", detail="torn")),
            _PRIOR_UNAVAILABLE,
        ),
    ],
)
def test_the_adjudicator_is_told_which_silence_tier_zero_is_in(index: _Index | None, account: str):
    """Four silences reach the model as four prompts, because they are four facts."""
    bench = _bench(index=index)
    bench.run()
    assert account in bench.adjudicator.requests[0].prompt


def test_tier_zero_text_reaches_the_adjudicator_when_it_disagrees():
    bench = _bench(index=_Index(_indexed(OTHER)))
    bench.run()
    assert OTHER in bench.adjudicator.requests[0].prompt


# ──────────────────── tier 0 degrades, and never fails the run ────────────────────


@pytest.mark.parametrize(
    "failure",
    [
        StoreUnavailableError(store="xochitl.db", detail="the image failed its integrity check"),
        StoreSchemaMismatchError(store="xochitl.db", found=3, expected=4),
    ],
)
def test_a_torn_index_costs_the_prior_and_nothing_else(failure: StoreUnavailableError):
    bench = _bench(index=_Index(failure=failure))
    result = bench.run()
    assert len(result.degradations) == 1
    degradation = result.degradations[0]
    assert degradation.kind is DegradationKind.DEVICE_INDEX_UNAVAILABLE
    assert degradation.subject == PAGE_A
    assert failure.store in degradation.detail
    assert _only(result).page.text == INK


def test_a_torn_index_is_left_out_of_the_cache_key():
    """A run with no prior available must be able to reuse a row written by another."""
    torn = _bench(index=_Index(failure=StoreUnavailableError(store="db", detail="torn")))
    torn.run()
    unbound = _bench(index=None)
    unbound.run()
    assert torn.cache.gets[0].recognizers == unbound.cache.gets[0].recognizers


# ─────────────────────────── tier 1: partial and total failure ───────────────────────────


def test_a_partial_failure_travels_back_and_the_survivor_is_used():
    down = _Recognizer(
        VISION,
        failure=RecognitionFailed(provider_id=VISION, detail="no handler", retryable=False),
    )
    up = _Recognizer(TEXTRACT, text=INK)
    bench = _bench(recognizers=(down, up), index=_Index(None))
    page = _only(bench.run())
    assert page.recognizer_failures == {VISION: "no handler"}
    assert page.tier_reached == 3
    assert INK in bench.adjudicator.requests[0].prompt


def test_every_recognizer_failing_raises_and_pays_for_no_model():
    bench = _bench(
        recognizers=(
            _Recognizer(
                VISION,
                failure=RecognitionFailed(provider_id=VISION, detail="a", retryable=True),
            ),
            _Recognizer(
                TEXTRACT,
                failure=RecognitionFailed(provider_id=TEXTRACT, detail="b", retryable=False),
            ),
        ),
        index=_Index(_indexed(INK)),
    )
    with pytest.raises(AllRecognizersFailed) as raised:
        bench.run()
    assert raised.value.failures == {VISION: "a", TEXTRACT: "b"}
    assert bench.reader.requests == []
    assert bench.adjudicator.requests == []


def test_an_empty_ensemble_is_not_a_total_failure():
    """Zero bound engines is a visible binding, not an outage, so tier 0 alone may carry it."""
    bench = _bench(recognizers=(), index=_Index(_indexed(INK)))
    page = _only(bench.run())
    assert page.tier_reached == 3
    assert page.recognizer_failures == {}


# ───────────────── a blank page cannot cost a token ─────────────────


@pytest.mark.parametrize("index", [None, _Index(None), _Index(_indexed(""))])
def test_a_blank_page_raises_before_any_model_call(index: _Index | None):
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=""),), index=index)
    with pytest.raises(NoTextRecognized) as raised:
        bench.run()
    assert raised.value.page_ref == PAGE_A
    assert raised.value.providers == (TEXTRACT,)
    assert bench.reader.requests == []
    assert bench.adjudicator.requests == []
    assert bench.cache.puts == []


# ───────────────────── the short-circuit, measured ─────────────────────


def test_tier_zero_wins_when_a_recognizer_agrees():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=INK),), index=_Index(_indexed(INK)))
    page = _only(bench.run())
    assert page.short_circuited is True
    assert page.tier_reached == 1
    assert page.page.text == INK
    assert page.page.provenance.recognizers == (INDEX, TEXTRACT)
    assert page.page.provenance.model_fingerprint is None
    assert bench.reader.requests == []
    assert bench.adjudicator.requests == []


def test_a_short_circuit_writes_no_cache_row():
    """A row cannot say which tier produced it, so only a merged reading may be stored."""
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=INK),), index=_Index(_indexed(INK)))
    bench.run()
    assert bench.cache.puts == []


def test_tier_zero_text_wins_even_where_the_recognizer_differs():
    """The tablet read the strokes; the recognizer read pixels made after discarding them."""
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text="the quick brown fox jumps over the lazy dogs"),),
        index=_Index(_indexed(INK)),
    )
    page = _only(bench.run())
    assert page.short_circuited is True
    assert page.page.text == INK


def test_case_and_punctuation_do_not_defeat_agreement():
    bench = _bench(
        recognizers=(
            _Recognizer(TEXTRACT, text="The quick, brown FOX  jumps over the lazy dog."),
        ),
        index=_Index(_indexed(INK)),
    )
    assert _only(bench.run()).short_circuited is True


def test_disagreement_pays_for_both_tiers():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(_indexed(INK)))
    page = _only(bench.run())
    assert page.short_circuited is False
    assert page.tier_reached == 3
    assert len(bench.reader.requests) == 1
    assert len(bench.adjudicator.requests) == 1


def test_the_threshold_is_a_request_field_and_flips_the_outcome():
    """One threshold cannot be right for every hand, so it is a field and not a constant."""
    readings = (_Recognizer(TEXTRACT, text="the quick brown fox jumped over a lazy dog"),)
    lenient = _bench(recognizers=readings, index=_Index(_indexed(INK)))
    assert _only(lenient.run(_request(agreement_threshold=0.5))).short_circuited is True
    strict = _bench(
        recognizers=(_Recognizer(TEXTRACT, text="the quick brown fox jumped over a lazy dog"),),
        index=_Index(_indexed(INK)),
    )
    assert _only(strict.run(_request(agreement_threshold=0.99))).short_circuited is False


def test_a_blank_reading_cannot_agree_with_tier_zero():
    """An empty string agrees with nothing, however good the ratio against an empty prior."""
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=""),), index=_Index(_indexed(INK)))
    page = _only(bench.run())
    assert page.short_circuited is False
    assert page.tier_reached == 3


def test_a_blank_reading_is_skipped_while_a_later_one_agrees():
    bench = _bench(
        recognizers=(_Recognizer(VISION, text=""), _Recognizer(TEXTRACT, text=INK)),
        index=_Index(_indexed(INK)),
    )
    page = _only(bench.run())
    assert page.short_circuited is True
    assert page.page.provenance.recognizers == (INDEX, TEXTRACT)


def test_a_tie_goes_to_the_earliest_binding():
    bench = _bench(
        recognizers=(_Recognizer(VISION, text=INK), _Recognizer(TEXTRACT, text=INK)),
        index=_Index(_indexed(INK)),
    )
    assert _only(bench.run()).page.provenance.recognizers == (INDEX, VISION)


# ────────────────────────── tiers 2 and 3, and the merge ──────────────────────────


def test_the_merge_sees_every_reading_and_the_page():
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(_indexed(INK)),
        reader=_Model(READER, text="a third reading"),
    )
    bench.run()
    merge = bench.adjudicator.requests[0]
    assert INK in merge.prompt
    assert OTHER in merge.prompt
    assert "a third reading" in merge.prompt
    assert TEXTRACT in merge.prompt
    assert len(merge.images) == 1
    assert merge.images[0].render_dpi == DPI


def test_the_read_is_sent_the_pixels_and_nothing_else_varies():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    bench.run()
    read = bench.reader.requests[0]
    assert len(read.images) == 1
    assert OTHER not in read.prompt


def test_provenance_names_the_fold_order_and_the_merging_model():
    bench = _bench(
        recognizers=(_Recognizer(VISION, text=OTHER), _Recognizer(TEXTRACT, text=OTHER)),
        index=_Index(_indexed(INK)),
    )
    provenance = _only(bench.run()).page.provenance
    assert provenance.recognizers == (INDEX, VISION, TEXTRACT, f"{_READER_PREFIX}{READER}")
    assert provenance.model_fingerprint == ADJUDICATOR
    assert provenance.render_dpi == DPI
    assert provenance.extracted_at == NOW


def test_an_absent_prior_is_left_out_of_the_fold_order():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    provenance = _only(bench.run()).page.provenance
    assert provenance.recognizers == (TEXTRACT, f"{_READER_PREFIX}{READER}")


def test_a_blank_merge_names_no_model():
    """The domain's rule: a blank page's provenance names no model, or the merge failed."""
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(None),
        adjudicator=_Model(ADJUDICATOR, text=""),
    )
    page = _only(bench.run())
    assert page.page.text == ""
    assert page.page.provenance.model_fingerprint is None


@pytest.mark.parametrize(
    ("confidences", "expected"),
    [((0.8, 0.6), 0.7), ((None, 0.6), 0.6), ((None, None), None)],
)
def test_confidence_averages_only_what_was_reported(
    confidences: tuple[float | None, float | None],
    expected: float | None,
):
    bench = _bench(
        recognizers=(
            _Recognizer(VISION, text=OTHER, confidence=confidences[0]),
            _Recognizer(TEXTRACT, text=OTHER, confidence=confidences[1]),
        ),
        index=_Index(None),
    )
    assert _only(bench.run()).mean_confidence == expected


# ───────────────────── truncation is data, and never a cache row ─────────────────────


def test_a_complete_merge_is_written_under_the_surviving_engines():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(_indexed(INK)))
    page = _only(bench.run())
    assert page.truncated is False
    assert len(bench.cache.puts) == 1
    key, artifact = bench.cache.puts[0]
    assert key.recognizers == tuple(sorted((TEXTRACT, f"{_READER_PREFIX}{READER}", INDEX)))
    assert key.page_hash == HASH_A
    assert key.render_digest == RENDER_DIGEST
    assert key.model_fingerprint == ADJUDICATOR
    assert artifact.text == page.page.text
    assert artifact.truncated is False
    assert artifact.created_at == NOW


@pytest.mark.parametrize("stopped", ["read", "merge"])
def test_a_truncated_completion_is_never_cached(stopped: str):
    limit = StopReason.OUTPUT_LIMIT
    done = StopReason.COMPLETE
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(None),
        reader=_Model(READER, text=OTHER, stop_reason=limit if stopped == "read" else done),
        adjudicator=_Model(
            ADJUDICATOR,
            text=INK,
            stop_reason=limit if stopped == "merge" else done,
        ),
    )
    page = _only(bench.run())
    assert page.truncated is True
    assert bench.cache.puts == []
    assert page.page.text == INK


def test_the_write_key_names_only_the_surviving_engines():
    down = _Recognizer(
        VISION,
        failure=RecognitionFailed(provider_id=VISION, detail="down", retryable=True),
    )
    bench = _bench(
        recognizers=(down, _Recognizer(TEXTRACT, text=OTHER)),
        index=_Index(None),
    )
    bench.run()
    looked_up = bench.cache.gets[0].recognizers
    written = bench.cache.puts[0][0].recognizers
    assert VISION in looked_up
    assert VISION not in written
    assert written == tuple(sorted((TEXTRACT, f"{_READER_PREFIX}{READER}", INDEX)))


# ────────────────────────────── the cache, and the key ──────────────────────────────


def test_a_cache_hit_skips_every_paid_tier():
    cache = _Cache()
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None), cache=cache
    )
    bench.run()
    replay = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(None),
        cache=cache,
    )
    page = _only(replay.run())
    assert page.cached is True
    assert page.tier_reached == 0
    assert page.short_circuited is False
    assert page.truncated is False
    assert page.recognizer_failures == {}
    assert page.page.text == INK
    assert page.page.provenance.extracted_at == NOW
    assert replay.recognizers[0].calls == 0
    assert replay.reader.requests == []
    assert replay.adjudicator.requests == []


def test_a_hit_reports_the_row_s_own_extraction_time():
    cache = _Cache()
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None), cache=cache
    )
    bench.run()
    stored_key = cache.puts[0][0]
    cache.rows[stored_key.digest] = (
        stored_key,
        OcrArtifact(text=INK, mean_confidence=0.5, truncated=False, created_at=EARLIER),
    )
    replay = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(None),
        cache=cache,
    )
    page = _only(replay.run())
    assert page.page.provenance.extracted_at == EARLIER
    assert page.mean_confidence == 0.5


def test_a_truncated_row_is_read_as_a_miss_and_recomputed():
    cache = _Cache()
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None), cache=cache
    )
    bench.run()
    key = cache.puts[0][0]
    cache.rows[key.digest] = (
        key,
        OcrArtifact(text="half a p", mean_confidence=None, truncated=True, created_at=EARLIER),
    )
    replay = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(None),
        cache=cache,
    )
    page = _only(replay.run())
    assert page.cached is False
    assert page.tier_reached == 3
    assert replay.recognizers[0].calls == 1


def test_a_plain_miss_reports_no_degradation():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    assert bench.run().degradations == ()


def test_a_row_under_other_inputs_is_reported_rather_than_served():
    cache = _Cache()
    first = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None), cache=cache
    )
    first.run()
    moved = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER),),
        index=_Index(None),
        cache=cache,
        adjudicator=_Model("adjudicator-fingerprint-2", text=INK),
    )
    result = moved.run()
    assert len(result.degradations) == 1
    degradation = result.degradations[0]
    assert degradation.kind is DegradationKind.CACHE_MISS_KEY_CHANGED
    assert degradation.subject == PAGE_A
    assert cache.puts[0][0].digest in degradation.detail
    assert _only(result).tier_reached == 3


def test_consulting_tier_zero_changes_the_recognizer_component():
    """A run that had the free prior cannot reuse a row written by one that did not."""
    informed = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    informed.run()
    blind = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=None)
    blind.run()
    assert INDEX in informed.cache.gets[0].recognizers
    assert INDEX not in blind.cache.gets[0].recognizers
    assert informed.cache.gets[0].digest != blind.cache.gets[0].digest


def test_the_reader_binding_is_folded_into_the_key():
    """The key has one model field and this use case has two bindings; both must count."""
    one = _bench(index=_Index(None), reader=_Model(READER, text=OTHER))
    one.run()
    two = _bench(index=_Index(None), reader=_Model("reader-fingerprint-2", text=OTHER))
    two.run()
    assert one.cache.gets[0].digest != two.cache.gets[0].digest


def test_the_recognizer_component_is_sorted_not_bound_in_order():
    """A fan-out that finishes in another sequence must not digest to another key."""
    forwards = _bench(
        recognizers=(_Recognizer(VISION, text=OTHER), _Recognizer(TEXTRACT, text=OTHER)),
        index=_Index(None),
    )
    forwards.run()
    backwards = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=OTHER), _Recognizer(VISION, text=OTHER)),
        index=_Index(None),
    )
    backwards.run()
    assert forwards.cache.gets[0].recognizers == backwards.cache.gets[0].recognizers


def test_the_pixels_own_digest_is_the_raster_component():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    bench.run()
    artifact = _artifact()
    assert artifact.raster is not None
    assert bench.cache.gets[0].raster_digest == artifact.raster.digest()


# ─────────────────────────── the request, and the pipeline ───────────────────────────


def test_the_render_request_reaches_the_pipeline_unchanged():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    request = _request()
    bench.run(request)
    assert bench.pipeline.requested == [request.render]


def test_every_rendered_page_is_transcribed_in_order():
    bench = _bench(
        recognizers=(_Recognizer(TEXTRACT, text=INK),),
        index=_Index(_indexed(INK, page_ref=PAGE_A)),
        pipeline=_Pipeline(
            _artifact(page_ref=PAGE_A, page_index=0, page_hash=HASH_A),
            _artifact(page_ref=PAGE_B, page_index=1, page_hash=HASH_B),
        ),
    )
    result = bench.run()
    assert [page.page.page_uuid for page in result.pages] == [PAGE_A, PAGE_B]
    assert [page.page.page_index for page in result.pages] == [0, 1]
    assert result.document_uuid == DOC
    assert result.render_digest == RENDER_DIGEST
    assert bench.index is not None
    assert bench.index.looked_up == [PAGE_A, PAGE_B]


def test_a_page_with_no_pixels_is_a_rasterization_failure():
    bench = _bench(pipeline=_Pipeline(_artifact(pixels=False)))
    with pytest.raises(RasterizationFailed) as raised:
        bench.run()
    assert raised.value.page_ref == PAGE_A


def test_a_render_that_produces_no_pixels_is_unconstructible():
    with pytest.raises(ValidationError, match="raster_dpi"):
        _request(render={"raster_dpi": None})


@pytest.mark.parametrize("threshold", [-0.1, 1.1])
def test_a_threshold_outside_the_ratio_is_refused(threshold: float):
    with pytest.raises(ValidationError):
        _request(agreement_threshold=threshold)


def test_the_default_threshold_is_the_measured_one():
    assert _request().agreement_threshold == 0.90


def test_the_result_and_its_pages_are_frozen():
    bench = _bench(recognizers=(_Recognizer(TEXTRACT, text=OTHER),), index=_Index(None))
    result = bench.run()
    with pytest.raises(ValidationError):
        _assign(result, "render_digest", "x")
    with pytest.raises(ValidationError):
        _assign(result.pages[0], "tier_reached", 0)


def test_a_result_forbids_an_unknown_field():
    with pytest.raises(ValidationError):
        TranscribePagesResult.model_validate(
            {
                "document_uuid": DOC,
                "pages": (),
                "render_digest": RENDER_DIGEST,
                "degradations": (),
                "surprise": 1,
            }
        )
