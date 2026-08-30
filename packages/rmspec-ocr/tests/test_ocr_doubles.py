"""The shipped doubles, and the properties the conformance contract cannot reach through a port.

``test_ocr_conformance.py`` proves the doubles satisfy the same contract as the adapters. This file
covers what is left, and everything in it is here for one of four reasons.

**The keying itself.** Through a port, a dictionary keyed by the input and a queue served in call
order are indistinguishable as long as a test happens to ask in the order it scripted. The port
docstrings are explicit that this is the failure mode -- a FIFO fake "would happily return another
page's text" -- so the assertions that ask *out of order*, and that ask for something unscripted,
are the only ones that falsify ``script.pop(0)``. They cannot live in the shared contract, because
a real adapter's stub client legitimately answers anything it is sent.

**A seam only a double has.** ``retryable=True`` from a recognizer is unreachable in the shipped
on-device engine, ``reasoning`` text is unreachable in the shipped Bedrock envelope, and a
refusal to script a confident blank page is a rule no adapter needs because its fold cannot
produce one. Each is asserted here so the application-layer tests that will depend on it are not
depending on an accident.

**The fingerprint's framing.** The contract asserts that varying one identity argument moves the
fingerprint. It cannot assert *how*, and the how matters: two open strings joined on a separator
collide the moment one of them contains it, which is the digest-framing defect this workspace
already fixed once in its cache keys.

**A counter.** The doubles ship under ``src/`` and are measured by the coverage gate, so every
branch above is also a line this file is responsible for.

There is deliberately nothing here for :class:`~rmspec.domain.ports.ocr.HandwrittenTextIndex`.
``rmspec.persistence.testing.FakeHandwrittenTextIndex`` already is that double, and it is not
imported here even to point at: ``rmspec-ocr`` does not depend on ``rmspec-persistence``, and a
test that reached across would make the workspace venv the only place this package's suite runs.
What is asserted instead is that this package ships no second one.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from ocr_contracts import (
    ANSWER_TEXT,
    CONTRACT_PAGES,
    INKED_TEXT,
    MEAN_CONFIDENCE,
    OTHER_REQUEST,
    PAGE_ONE,
    PAGE_TWO,
    REFERENCE_REQUEST,
    a_raster,
    a_request,
)

import rmspec.ocr.testing
from rmspec.domain.errors import ModelThrottled, RecognitionFailed
from rmspec.domain.ports.ocr import StopReason, TokenUsage
from rmspec.ocr import vision_model
from rmspec.ocr.testing import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER,
    DEFAULT_REVISION,
    FINGERPRINT_TAG,
    ScriptedTextRecognizer,
    ScriptedVisionLanguageModel,
)

HEX_DIGEST_LENGTH = 64
UNSCRIPTED_PAGE = "44444444-0000-4000-8000-00000000000d"
OTHER_ANSWER = "A different answer, for the other request."
OTHER_READING = "A different reading, off the other page."
FAN_OUT_REPEATS = 3


def a_throttle() -> ModelThrottled:
    """Build one model failure, with the delay the provider would have supplied.

    Returns
    -------
    ModelThrottled
        The error. A throttle rather than any of the other four because it is the one whose
        ``retry_after_s`` a caller reads back, so a double that rebuilt it would be visible.
    """
    return ModelThrottled(model_id="scripted", retry_after_s=1.5)


def a_failure(*, provider_id: str, retryable: bool) -> RecognitionFailed:
    """Build one recognition failure.

    Parameters
    ----------
    provider_id
        The engine the failure names, which must be the one that raises it.
    retryable
        Whether retrying could produce a different answer.

    Returns
    -------
    RecognitionFailed
        The error.
    """
    return RecognitionFailed(
        provider_id=provider_id,
        detail="the test seeded a failing engine",
        retryable=retryable,
    )


# ─────────────────── ScriptedVisionLanguageModel: keyed by request ───────────────────


def test_replies_are_served_by_request_and_never_in_call_order() -> None:
    # The assertion that falsifies `script.pop(0)`. Scripted in one order and asked in the other,
    # so a queue-shaped double answers each call with the other request's text -- which is exactly
    # what `ports/ocr.py` says a FIFO fake would "happily" do, and why `VisionRequest` is frozen
    # and hashable in the first place.
    model = ScriptedVisionLanguageModel()
    model.answer(REFERENCE_REQUEST, text=ANSWER_TEXT)
    model.answer(OTHER_REQUEST, text=OTHER_ANSWER)

    assert model.complete(OTHER_REQUEST).text == OTHER_ANSWER
    assert model.complete(REFERENCE_REQUEST).text == ANSWER_TEXT
    assert model.complete(OTHER_REQUEST).text == OTHER_ANSWER


def test_the_script_is_keyed_by_value_so_an_equal_request_built_elsewhere_finds_it() -> None:
    # An application-layer test builds its request where it composes its prompt, not where it
    # scripts its double. Keying on identity would make that a KeyError for a request that is
    # equal in every field, and keying on nothing would make it a wrong answer.
    model = ScriptedVisionLanguageModel()
    model.answer(a_request(), text=ANSWER_TEXT)

    rebuilt = a_request()
    assert rebuilt is not REFERENCE_REQUEST
    assert rebuilt == REFERENCE_REQUEST
    assert model.complete(rebuilt).text == ANSWER_TEXT


def test_a_request_nothing_was_scripted_for_is_a_key_error_and_never_a_neighbour() -> None:
    model = ScriptedVisionLanguageModel()
    model.answer(REFERENCE_REQUEST, text=ANSWER_TEXT)

    with pytest.raises(KeyError, match="nothing is scripted"):
        model.complete(OTHER_REQUEST)
    # And the refused call is still recorded, so a caller can prove the double was consulted.
    assert model.requests == [OTHER_REQUEST]
    assert model.complete_calls == 1


def test_a_later_scripting_replaces_an_earlier_one_for_the_same_request() -> None:
    model = ScriptedVisionLanguageModel()
    model.answer(REFERENCE_REQUEST, text=ANSWER_TEXT)
    model.fail(REFERENCE_REQUEST, a_throttle())

    with pytest.raises(ModelThrottled):
        model.complete(REFERENCE_REQUEST)


def test_the_scripted_error_object_is_the_one_that_arrives() -> None:
    # Not a reconstruction of it. `retry_after_s` is what a retry policy reads, so a double that
    # rebuilt the error from a message would silently turn a measured delay into a guessed one.
    seeded = a_throttle()
    model = ScriptedVisionLanguageModel()
    model.fail(REFERENCE_REQUEST, seeded)

    with pytest.raises(ModelThrottled) as caught:
        model.complete(REFERENCE_REQUEST)
    assert caught.value is seeded
    assert caught.value.retry_after_s == 1.5


def test_reasoning_text_and_token_accounting_are_both_scriptable() -> None:
    # Neither is reachable through the shipped Bedrock binding: its envelope reports reasoning as a
    # token count and never as text, so `VisionCompletion.reasoning` is always None there. A use
    # case that reads either field needs this double to reach the other branch at all.
    usage = TokenUsage(input_tokens=177, output_tokens=64, reasoning_tokens=40)
    model = ScriptedVisionLanguageModel()
    model.answer(
        REFERENCE_REQUEST,
        text=ANSWER_TEXT,
        reasoning="The second line is a continuation, not a new bullet.",
        usage=usage,
    )

    completion = model.complete(REFERENCE_REQUEST)
    assert completion.reasoning is not None
    assert completion.usage is not None
    assert completion.usage == usage
    assert completion.usage.reasoning_tokens == 40


def test_every_stop_reason_is_scriptable_including_the_one_no_shipped_wire_reports() -> None:
    # `StopReason.STOP_SEQUENCE` is unreachable through the OpenAI envelope, which reports `stop`
    # for a natural end and a stop-sequence hit alike. A double that could not produce it would
    # leave a caller's fourth branch untestable for as long as that is the only binding.
    model = ScriptedVisionLanguageModel()
    for reason in StopReason:
        model.answer(REFERENCE_REQUEST, text=ANSWER_TEXT, stop_reason=reason)
        assert model.complete(REFERENCE_REQUEST).stop_reason is reason


# ─────────────────── ScriptedVisionLanguageModel: the fingerprint ───────────────────


def test_the_fingerprint_is_a_derived_digest_and_not_a_readable_setting() -> None:
    fingerprint = ScriptedVisionLanguageModel().fingerprint
    assert len(fingerprint) == HEX_DIGEST_LENGTH
    assert bytes.fromhex(fingerprint)
    assert DEFAULT_MODEL_ID not in fingerprint


def test_the_components_are_framed_so_a_boundary_cannot_shift_between_them() -> None:
    # The half the shared contract cannot express. Both components are open strings a caller
    # supplies, so a separator-joined stream would give these two bindings one fingerprint -- the
    # digest-framing defect this workspace already fixed once in its cache keys.
    left = ScriptedVisionLanguageModel(model_id="ab", revision="c")
    right = ScriptedVisionLanguageModel(model_id="a", revision="bc")
    assert left.fingerprint != right.fingerprint


def test_a_double_and_the_real_binding_never_share_a_fingerprint_scheme() -> None:
    # Two tags, so a cache row written under a double is unreachable by the adapter and the other
    # way round. That is the honest answer: they are not the same binding and never answer alike.
    assert FINGERPRINT_TAG != vision_model.FINGERPRINT_TAG


@pytest.mark.parametrize(
    ("model_id", "revision"),
    [("", DEFAULT_REVISION), (DEFAULT_MODEL_ID, "")],
)
def test_an_empty_identity_component_is_refused_before_it_can_reach_a_cache_key(
    model_id: str,
    revision: str,
) -> None:
    # Mirrors `BedrockOpenAiVisionModel`, and for the same reason: an empty component still
    # produces a perfectly valid-looking digest, so the one thing a reader could not tell from the
    # fingerprint is that the binding is broken.
    with pytest.raises(ValueError, match="component of this double's fingerprint"):
        ScriptedVisionLanguageModel(model_id=model_id, revision=revision)


def test_the_claimed_identity_is_readable_without_re_deriving_it() -> None:
    assert ScriptedVisionLanguageModel().model_id == DEFAULT_MODEL_ID
    assert ScriptedVisionLanguageModel(model_id="named").model_id == "named"


# ─────────────────── ScriptedTextRecognizer: keyed by page ───────────────────


def test_readings_are_served_by_page_and_never_in_call_order() -> None:
    # The assertion `RasterImage.page_ref` exists for: the app owns fan-out and per-page cache
    # hits, so calls are neither ordered nor one-to-one.
    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text=INKED_TEXT, mean_confidence=MEAN_CONFIDENCE)
    recognizer.read(PAGE_TWO, text=OTHER_READING, mean_confidence=MEAN_CONFIDENCE)

    assert recognizer.recognize(a_raster(PAGE_TWO)).text == OTHER_READING
    assert recognizer.recognize(a_raster(PAGE_ONE)).text == INKED_TEXT
    assert recognizer.recognize(a_raster(PAGE_TWO)).text == OTHER_READING


def test_a_page_nothing_was_scripted_for_is_a_key_error_and_never_a_blank_reading() -> None:
    # The distinction the tier-0 lookup exists to preserve, applied here: `text=""` means an
    # engine read the page and found nothing, and collapsing an unscripted page onto it would let
    # a tiering test silently suppress the paid read it was written to prove happens.
    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text=INKED_TEXT, mean_confidence=MEAN_CONFIDENCE)

    with pytest.raises(KeyError, match="nothing is scripted"):
        recognizer.recognize(a_raster(UNSCRIPTED_PAGE))
    assert recognizer.pages_read == [UNSCRIPTED_PAGE]
    assert recognizer.recognize_calls == 1


def test_a_blank_reading_is_a_positive_scripting_and_not_an_absence() -> None:
    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text="")

    reading = recognizer.recognize(a_raster(PAGE_ONE))
    assert reading.text == ""
    assert reading.mean_confidence is None
    assert reading.has_text is False


@pytest.mark.parametrize("text", ["", "\n", "\n\n"])
def test_a_reading_with_nothing_measured_cannot_be_scripted_with_a_confidence(text: str) -> None:
    # No engine in this workspace can produce one: both recognizers get `None` from the shared
    # character-weighted fold, which weights each line by `len(line.text)` and never sees the
    # separators the join puts between them. So two blank lines are `text="\n"` with no confidence
    # -- a non-empty string with nothing measured in it, which is why the double's check is not
    # `if not text`. A double that accepted a number for any of these three would be the only
    # reader able to report a confident blank page, and an application test could then assert a
    # reading that cannot happen.
    recognizer = ScriptedTextRecognizer()
    with pytest.raises(ValueError, match="mean_confidence=None"):
        recognizer.read(PAGE_ONE, text=text, mean_confidence=0.0)


@pytest.mark.parametrize("text", ["", "\n", "\n\n"])
def test_a_reading_with_nothing_measured_is_scriptable_without_one(text: str) -> None:
    # The other half: the same three readings are legitimate with `mean_confidence=None`, because
    # a page of blank regions is a page an engine read and found nothing on.
    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text=text)
    reading = recognizer.recognize(a_raster(PAGE_ONE))
    assert reading.text == text
    assert reading.mean_confidence is None
    assert reading.has_text is False


def test_a_later_scripting_replaces_an_earlier_one_for_the_same_page() -> None:
    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text=INKED_TEXT, mean_confidence=MEAN_CONFIDENCE)
    recognizer.fail(PAGE_ONE, a_failure(provider_id=recognizer.provider_id, retryable=True))

    with pytest.raises(RecognitionFailed):
        recognizer.recognize(a_raster(PAGE_ONE))


def test_the_scripted_failure_object_is_the_one_that_arrives() -> None:
    recognizer = ScriptedTextRecognizer()
    seeded = a_failure(provider_id=recognizer.provider_id, retryable=True)
    recognizer.fail(PAGE_ONE, seeded)

    with pytest.raises(RecognitionFailed) as caught:
        recognizer.recognize(a_raster(PAGE_ONE))
    assert caught.value is seeded
    assert caught.value.retryable is True


def test_one_page_may_fail_while_another_reads() -> None:
    # Partial failure is use-case policy, which means a use case has to be able to reach it. One
    # double, one failing page and one readable page is the smallest shape that does, and it is
    # unreachable through any single shipped adapter -- a stub client fails for every page or none.
    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text=INKED_TEXT, mean_confidence=MEAN_CONFIDENCE)
    recognizer.fail(PAGE_TWO, a_failure(provider_id=recognizer.provider_id, retryable=False))

    assert recognizer.recognize(a_raster(PAGE_ONE)).text == INKED_TEXT
    with pytest.raises(RecognitionFailed):
        recognizer.recognize(a_raster(PAGE_TWO))
    assert recognizer.pages_read == [PAGE_ONE, PAGE_TWO]


def test_the_slug_is_built_from_both_halves_the_adapters_build_theirs_from() -> None:
    # The engine half is an argument rather than a constant so one double can impersonate either
    # shipped slug. That is what a test of the app's "fold the surviving engines in sorted order"
    # cache-key rule needs: two recognizers whose slugs sort the way the real ones do.
    assert ScriptedTextRecognizer().provider_id == f"{DEFAULT_PROVIDER}@1"
    assert ScriptedTextRecognizer(provider="apple-vision", revision=2).provider_id == (
        "apple-vision@2"
    )


def test_an_empty_engine_name_is_refused_before_it_can_reach_a_cache_key() -> None:
    # `Recognition.provider_id` requires one character, so `"@1"` would validate and then travel
    # into a cache key as a slug naming no engine at all.
    with pytest.raises(ValueError, match="component of this double's provider slug"):
        ScriptedTextRecognizer(provider="")


# ─────────────────── the counters, and the concurrency they must survive ───────────────────


def test_the_call_records_are_the_counters_so_the_two_cannot_disagree() -> None:
    model = ScriptedVisionLanguageModel()
    model.answer(REFERENCE_REQUEST, text=ANSWER_TEXT)
    model.complete(REFERENCE_REQUEST)
    model.complete(REFERENCE_REQUEST)
    assert model.complete_calls == len(model.requests) == 2

    recognizer = ScriptedTextRecognizer()
    recognizer.read(PAGE_ONE, text=INKED_TEXT, mean_confidence=MEAN_CONFIDENCE)
    recognizer.recognize(a_raster(PAGE_ONE))
    assert recognizer.recognize_calls == len(recognizer.pages_read) == 1


def test_a_double_counts_every_call_correctly_under_a_fan_out() -> None:
    # Why the counters are derived from a list rather than kept as an `int`: `+=` on an attribute
    # is not one bytecode, so a counter incremented that way loses increments under exactly the
    # fan-out the port mandates one instance tolerate. `list.append` does not.
    recognizer = ScriptedTextRecognizer()
    for page_ref in CONTRACT_PAGES:
        recognizer.read(page_ref, text=INKED_TEXT, mean_confidence=MEAN_CONFIDENCE)

    pages = [page_ref for page_ref in CONTRACT_PAGES for _ in range(FAN_OUT_REPEATS)]
    with ThreadPoolExecutor(max_workers=len(pages)) as pool:
        readings = list(pool.map(lambda ref: recognizer.recognize(a_raster(ref)), pages))

    assert [reading.page_ref for reading in readings] == pages
    assert recognizer.recognize_calls == len(pages)
    assert sorted(recognizer.pages_read) == sorted(pages)


# ─────────────────── one double that is deliberately not here ───────────────────


def test_no_handwritten_text_index_double_is_shipped_by_this_package() -> None:
    """``rmspec.persistence.testing.FakeHandwrittenTextIndex`` is already one."""
    # Not imported here to prove it: `rmspec-ocr` does not depend on `rmspec-persistence`, so a
    # test that reached across would make the workspace venv the only place this suite runs. The
    # port lives in `ports/ocr.py` because that is where the tiering decision is documented; the
    # reader and its double live where `sqlite3` is allowed. What this asserts is the *absence* of
    # a second one, which is the thing a later reader could get wrong.
    lookups = [
        name
        for name in rmspec.ocr.testing.__all__
        if callable(getattr(getattr(rmspec.ocr.testing, name), "lookup", None))
    ]
    assert lookups == []
