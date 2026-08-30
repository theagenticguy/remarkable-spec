"""The two OCR port contracts, written once and run against every implementation.

Each class here holds every assertion one Protocol in :mod:`rmspec.domain.ports.ocr` makes, and
declares its subject through seams annotated with the *Protocol* rather than with an adapter.
``test_ocr_conformance.py`` binds them to the Bedrock model over a stub client, to both
recognizers over stub engines, and to the two shipped doubles -- so one assertion set proves that
an adapter which quietly narrowed its behaviour, or a double that quietly widened its own, fails
here rather than a package away in step 6.

The Protocol annotation on every seam is also a static conformance check. No port is
``runtime_checkable`` and nothing calls ``isinstance``, so returning a concrete adapter from a
method annotated ``-> VisionLanguageModel`` is what makes ``ty`` the gate.

Nothing here constructs an AWS client
------------------------------------
Not once, not in a fixture, not behind a skip. Both AWS-backed adapters take their client as a
constructor argument -- :class:`~rmspec.ocr._bedrock.BedrockRuntimeClient` is one method wide and
:class:`~rmspec.ocr.textract.DocumentTextDetector` is one method wide -- so every binding below
enters the contract with a stub, and a suite that had to build a ``bedrock-runtime`` or
``textract`` client would carry a credential chain, a network stack and an account. Neither does
anything here import ``Vision``: the Apple binding's :class:`~rmspec.ocr.apple_vision.LineReader`
is one callable, and the native module that imports the framework is reached only by name, inside
the two calls that need it.

The reference page, which every binding materialises for itself
--------------------------------------------------------------
Three pages, and their pixels are **identical**. That is the design and not laziness:
:meth:`~rmspec.domain.ports.ocr.RasterImage.digest` deliberately excludes ``page_ref``, so one
cache row is legitimately shared by two pages with the same pixels -- and an implementation that
attributed a reading by anything other than the ``page_ref`` it was handed would still pass a
suite whose pages differed in their bytes. Here it cannot: the only thing distinguishing
:data:`PAGE_ONE` from :data:`PAGE_TWO` is the field the attribution must come from.

* :data:`PAGE_ONE`, :data:`PAGE_TWO` -- two pages a recognizer reads :data:`INKED_TEXT` off.
* :data:`PAGE_THREE` -- the page whose raster arrives through
  :meth:`~rmspec.domain.ports.ocr.RasterImage.from_raster` from the export slice's twin.

The page identifiers are the ones ``packages/rmspec-device/tests/device_contracts.py`` uses, so a
later application-layer test can hand a device-slice bundle's page straight to a recognizer
double without a translation table.

What is asserted about the ``from_raster`` conversion, and what is not
--------------------------------------------------------------------
``ports/ocr.py`` names ``RasterImage.from_raster(x).digest() == x.digest()`` as the one assertion
the shared adapter contract suite must make, because "that one assertion is what catches drift
between the two digest bodies". It is **already made**, over four dimension pairs and both media,
by ``packages/rmspec-domain/tests/test_ports_ocr.py``'s
``test_the_two_raster_twins_produce_equal_digests_for_equal_fields`` and
``test_the_two_raster_twins_also_agree_on_jpeg_pixels`` -- and made against the only producer this
package could reach, since ``rmspec-ocr`` may not import ``rmspec.export`` and the export slice's
twin is the type its rasterizer returns. Restating it here would be a second, weaker copy: one
value pair against their five, with the same two types.

:meth:`TextRecognizerContract.test_a_raster_adopted_from_the_export_slices_twin_reads_as_the_same_page`
is the part that is genuinely adapter-side and not covered there: that an adapter *reads* an
adopted raster no differently from one built directly. A digest can agree while a reader still
behaves differently, because a reader touches ``data`` and ``page_ref`` and never calls
``digest`` at all.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final

import pytest

from rmspec.domain.errors import (
    ModelAccessDenied,
    ModelError,
    ModelRejectedRequest,
    ModelResponseMalformed,
    ModelThrottled,
    ModelUnavailable,
    OcrError,
    RecognitionFailed,
)
from rmspec.domain.ports.export import ImageMedia as ExportImageMedia
from rmspec.domain.ports.export import RasterImage as ExportRasterImage
from rmspec.domain.ports.ocr import (
    Decoding,
    ImageMedia,
    RasterImage,
    ReasoningEffort,
    Recognition,
    StopReason,
    VisionRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rmspec.domain.ports.ocr import TextRecognizer, VisionLanguageModel

PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"
"""The eight bytes every PNG starts with, which the OCR slice's raster validates against."""

PNG_IEND: Final = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
"""The trailer the export slice's twin additionally requires, so one byte string satisfies both."""

PAGE_WIDTH: Final = 1620
PAGE_HEIGHT: Final = 2160
"""The Paper Pro's panel, in pixels. Recorded in the PNG header so the export twin accepts it."""

RENDER_DPI: Final = 229
"""The panel's measured density. Folded into every raster digest, so it travels with the bytes."""

PAGE_ONE: Final = "11111111-0000-4000-8000-00000000000a"
"""A page carrying ink. Same identifier ``device_contracts.py`` uses for the same page."""

PAGE_TWO: Final = "22222222-0000-4000-8000-00000000000b"
"""A second page carrying ink, whose pixels are byte-identical to :data:`PAGE_ONE`'s."""

PAGE_THREE: Final = "33333333-0000-4000-8000-00000000000c"
"""The page whose raster is adopted from the export slice's twin rather than built here."""

CONTRACT_PAGES: Final = (PAGE_ONE, PAGE_TWO, PAGE_THREE)
"""Every page a binding must be able to answer for. Published so a keyed double can script them
all, rather than each binding guessing which identifiers the assertions below reach for."""

INKED_TEXT: Final = "Sprint notes\nfold the surviving engines in sorted order"
"""What every recognizer reads off an inked page. Two lines, so a fold that dropped the separator
is visible, and no leading or trailing whitespace, so ``has_text`` is not the only witness."""

MEAN_CONFIDENCE: Final = 0.87
"""The confidence every recognizer reports for :data:`INKED_TEXT`. Neither 0.0 nor 1.0, so a
binding that fabricated either of the two values the port calls dishonest fails here."""

ANSWER_TEXT: Final = "Sprint notes\n\nFold the surviving engines in sorted order."
"""What every model answers for a request it was scripted for.

Deliberately not equal to :data:`INKED_TEXT`: a model that merged two recognizers' readings
returns punctuated, paragraphed prose, so a binding that echoed its input rather than answering
is visible here instead of passing on a string that happens to be the same."""

PARTIAL_TEXT: Final = "Sprint notes\n\nFold the surviving eng"
"""A body that stopped early. Non-empty on purpose: truncation with content is the case the port
says must arrive as data, and an empty one would be indistinguishable from a refusal."""

TRANSCRIBE_PROMPT: Final = "Transcribe the handwriting on this page."
OTHER_PROMPT: Final = "Transcribe the handwriting on this page, preserving line breaks."
"""Two prompts, so two requests digest differently without either being degenerate. The prompt
text itself is hashed by :meth:`~rmspec.domain.ports.ocr.VisionRequest.digest`, which is what
makes editing a prompt a mechanical cache miss."""

MAX_OUTPUT_TOKENS: Final = 2000
"""The budget the measured probe answered at. 24 was the budget that returned no text at all."""

ENDPOINT: Final = "https://bedrock-runtime.us-east-1.amazonaws.com"
"""What an unreachable-endpoint failure names. A URL, because that is what a reader must check."""


def page_png() -> bytes:
    """Return PNG bytes both raster twins accept: signature, ``IHDR``, and an ``IEND`` trailer.

    The export twin validates its declared size against the ``IHDR`` chunk and requires the
    trailer, while the OCR copy checks only the leading magic. One byte string satisfying both is
    what lets :func:`an_export_twin` be *field-for-field equal* to :func:`a_raster`, which in turn
    is what makes the adoption assertion an equality rather than a resemblance.

    Returns
    -------
    bytes
        A minimal PNG stream recording :data:`PAGE_WIDTH` by :data:`PAGE_HEIGHT`.
    """
    return (
        PNG_MAGIC
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + PAGE_WIDTH.to_bytes(4, "big")
        + PAGE_HEIGHT.to_bytes(4, "big")
        + PNG_IEND
    )


def a_raster(page_ref: str = PAGE_ONE) -> RasterImage:
    """Build one rendered page, identical to every other except for its page identity.

    Parameters
    ----------
    page_ref
        Which page these pixels are being read for.

    Returns
    -------
    RasterImage
        The raster. Its bytes do not depend on ``page_ref``, so an implementation that attributed
        a reading to anything but the field it was handed cannot pass.
    """
    return RasterImage(
        page_ref=page_ref,
        media=ImageMedia.PNG,
        data=page_png(),
        width=PAGE_WIDTH,
        height=PAGE_HEIGHT,
        render_dpi=RENDER_DPI,
    )


def an_export_twin(page_ref: str = PAGE_THREE) -> ExportRasterImage:
    """Build the export slice's field-for-field twin of :func:`a_raster`.

    Built by reading the OCR raster's own fields rather than by restating them, so the two cannot
    drift apart and leave the adoption assertion comparing two different pages.

    Parameters
    ----------
    page_ref
        Which page these pixels were rendered for.

    Returns
    -------
    ExportRasterImage
        The twin, which satisfies :class:`~rmspec.domain.ports.ocr.PageRasterLike` structurally
        with no import of the export *slice* -- only of the port module that declares the type its
        rasterizer returns.
    """
    mine = a_raster(page_ref)
    return ExportRasterImage(
        page_ref=mine.page_ref,
        media=ExportImageMedia(mine.media.value),
        data=mine.data,
        width=mine.width,
        height=mine.height,
        render_dpi=mine.render_dpi,
    )


def a_request(*, prompt: str = TRANSCRIBE_PROMPT) -> VisionRequest:
    """Build one multimodal completion request carrying one page.

    Parameters
    ----------
    prompt
        The user-turn instruction text, which :meth:`VisionRequest.digest` hashes.

    Returns
    -------
    VisionRequest
        The request. Frozen and hashable, which is what lets a double key its replies by it.
    """
    return VisionRequest(
        prompt=prompt,
        system="You transcribe handwriting and never invent a word.",
        images=(a_raster(),),
        decoding=Decoding(
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            reasoning=ReasoningEffort.MEDIUM,
        ),
    )


REFERENCE_REQUEST: Final = a_request()
"""The request every model assertion asks about unless it needs a second one."""

OTHER_REQUEST: Final = a_request(prompt=OTHER_PROMPT)
"""A second request, differing only in prompt text and therefore in digest."""

CONTRACT_REQUESTS: Final = (REFERENCE_REQUEST, OTHER_REQUEST)
"""Every request a binding must be able to answer. Published for the same reason
:data:`CONTRACT_PAGES` is: a request-keyed double has to know which keys to script."""

_MODEL_ERROR_BUILDERS: Final[Mapping[type[ModelError], Callable[[], ModelError]]] = {
    ModelAccessDenied: lambda: ModelAccessDenied(
        model_id="contract-model",
        remediation="enable contract-model in us-east-1 in the Bedrock console",
    ),
    ModelRejectedRequest: lambda: ModelRejectedRequest(
        model_id="contract-model",
        detail="ValidationException: the image is larger than this model accepts",
    ),
    ModelResponseMalformed: lambda: ModelResponseMalformed(
        model_id="contract-model",
        detail="response envelope has no readable body",
    ),
    ModelThrottled: lambda: ModelThrottled(model_id="contract-model", retry_after_s=2.0),
    ModelUnavailable: lambda: ModelUnavailable(
        endpoint=ENDPOINT,
        detail="the contract seeded an outage",
        retryable=True,
    ),
}
"""One builder per error :meth:`VisionLanguageModel.complete`'s ``Raises`` clause names.

Keyed by class rather than held as instances, because the seam a binding implements is "fail with
an error of *this* class" and each binding decides how to *cause* it -- the Bedrock adapter by
handing its stub client the botocore exception that translates to it, which puts the translation
on the path being asserted, and a double by scripting the error object itself.
"""

MODEL_ERROR_TYPES: Final = tuple(_MODEL_ERROR_BUILDERS)
"""The five, in a stable order so ``pytest-xdist`` distributes the same parameters everywhere."""


def a_model_error(error_type: type[ModelError], /) -> ModelError:
    """Build one instance of the model error class asked for.

    Parameters
    ----------
    error_type
        One of :data:`MODEL_ERROR_TYPES`.

    Returns
    -------
    ModelError
        A populated instance. Its fields carry a plausible remediation and detail, because a
        caller's report-and-stop branch prints them and an empty one would make that branch look
        exercised while printing nothing.
    """
    return _MODEL_ERROR_BUILDERS[error_type]()


class VisionLanguageModelContract:
    """Every assertion ``VisionLanguageModel`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    def answering(
        self,
        *requests: VisionRequest,
        text: str,
        stop_reason: StopReason,
    ) -> VisionLanguageModel:
        """Return a binding that answers each of ``requests`` with ``text`` and ``stop_reason``.

        Built with this implementation's *baseline* identity arguments, so that every binding
        this method and :meth:`failing` return shares one fingerprint and only
        :meth:`identity_variants` moves it.

        Parameters
        ----------
        *requests
            The requests to answer. A binding whose stub answers everything may ignore them; a
            keyed double must script exactly these, and the divergence between the two is what
            this shared suite exists to catch.
        text
            The answer, possibly empty.
        stop_reason
            Why generation stopped.

        Returns
        -------
        VisionLanguageModel
            The subject.
        """
        raise NotImplementedError

    def failing(self, error_type: type[ModelError], /) -> VisionLanguageModel:
        """Return a binding whose every completion fails with an error of that class.

        Each implementation decides how to *cause* the failure, because the cause is the half
        worth testing: a real adapter must translate its provider's own exception, and only a
        double can raise the domain error directly.

        Parameters
        ----------
        error_type
            One of :data:`MODEL_ERROR_TYPES`.

        Returns
        -------
        VisionLanguageModel
            The subject.
        """
        raise NotImplementedError

    def identity_variants(self) -> Mapping[str, VisionLanguageModel]:
        """Return one binding per identity-bearing constructor argument.

        Each differs from the baseline in exactly the argument its key names, and in nothing
        else. This is the seam that makes "no constant can pass" enforceable without the contract
        knowing what any binding's constructor looks like.

        Returns
        -------
        Mapping[str, VisionLanguageModel]
            Argument name to the binding that varied it. Must be non-empty: a binding with no
            identity-bearing argument could only have a constant fingerprint.
        """
        raise NotImplementedError

    def other_collaborator(self) -> VisionLanguageModel:
        """Return a binding with the baseline identity and a different collaborator.

        A second client for a real adapter, a second script for a double. Neither is part of the
        binding's identity, and the assertion that they are not is as load-bearing as the one
        above it: hashing a client's object identity would give two processes -- or one process
        that rebuilt its client -- different fingerprints for identical work, and every cache row
        either wrote would be unreachable by the other.

        Returns
        -------
        VisionLanguageModel
            The subject.
        """
        raise NotImplementedError

    def unreachable_stop_reasons(self) -> frozenset[StopReason]:
        """Return the stop reasons this binding's wire structurally cannot report.

        Empty for a double, which can report all four. The Bedrock binding overrides it with
        :attr:`~rmspec.domain.ports.ocr.StopReason.STOP_SEQUENCE`, because Chat Completions
        reports ``stop`` for a natural end and a stop-sequence hit alike and
        :class:`~rmspec.domain.ports.ocr.Decoding` has no stop-sequence field -- so stating it
        here is what makes the *absence* a claim about the wire rather than a gap in the suite.

        Returns
        -------
        frozenset[StopReason]
            The members this binding cannot produce.
        """
        return frozenset()

    @pytest.fixture
    def binding(self) -> VisionLanguageModel:
        """Return the reference binding: it answers :data:`ANSWER_TEXT` and completes normally.

        Returns
        -------
        VisionLanguageModel
            The subject, which ``ty`` checks against the Protocol through
            :meth:`answering`'s own annotation.
        """
        return self.answering(
            *CONTRACT_REQUESTS,
            text=ANSWER_TEXT,
            stop_reason=StopReason.COMPLETE,
        )

    # ── the fingerprint is derived, and moves when the binding does ─────────

    def test_the_fingerprint_is_known_before_the_first_call_and_never_moves(
        self,
        binding: VisionLanguageModel,
    ) -> None:
        """The fingerprint is known before the first call and never moves."""
        # Both halves are contract, not convenience. Known before the first call, because it is
        # half of a cache key and a key nobody can compute before paying cannot be looked up.
        # Stable for the process lifetime, because a value that moved mid-process would make
        # every row written earlier in the same run unreachable.
        before = binding.fingerprint
        assert before
        assert binding.fingerprint == before

        binding.complete(REFERENCE_REQUEST)
        assert binding.fingerprint == before

        binding.complete(OTHER_REQUEST)
        assert binding.fingerprint == before

    def test_the_fingerprint_moves_when_any_identity_bearing_argument_moves(
        self,
        binding: VisionLanguageModel,
    ) -> None:
        """The fingerprint moves when any identity bearing argument moves."""
        # The assertion ports/ocr.py says no constant can pass, and the reason it is written as a
        # loop over a per-binding seam rather than as a literal: the contract must be able to make
        # it without knowing what any implementation's constructor looks like.
        variants = self.identity_variants()
        assert variants, "a binding with no identity-bearing argument has a constant fingerprint"

        baseline = binding.fingerprint
        moved = {name: varied.fingerprint for name, varied in variants.items()}
        unmoved = sorted(name for name, value in moved.items() if value == baseline)
        assert not unmoved, f"varying {unmoved} left the fingerprint unchanged"

        # And no two variants collide, which a fingerprint folding its components on a separator
        # would allow the moment one of them contained that separator.
        assert len(set(moved.values())) == len(moved)

    def test_two_bindings_differing_only_in_their_collaborator_share_one_fingerprint(
        self,
        binding: VisionLanguageModel,
    ) -> None:
        """Two bindings differing only in their collaborator share one fingerprint."""
        assert self.other_collaborator().fingerprint == binding.fingerprint

    def test_a_binding_that_can_only_fail_still_publishes_its_fingerprint(self) -> None:
        """A binding that can only fail still publishes its fingerprint."""
        # `fingerprint` raises nothing, per the port. A binding that derived it lazily from a
        # first response would fail here, and would also have broken the rule above it.
        assert self.failing(ModelUnavailable).fingerprint

    # ── every echo is derived from the call it belongs to ───────────────────

    def test_a_completion_echoes_the_request_it_answered_and_the_binding_that_answered_it(
        self,
        binding: VisionLanguageModel,
    ) -> None:
        """A completion echoes the request it answered and the binding that answered it."""
        completion = binding.complete(REFERENCE_REQUEST)
        assert completion.request_digest == REFERENCE_REQUEST.digest()
        assert completion.model_fingerprint == binding.fingerprint

    def test_each_of_two_requests_is_echoed_with_its_own_digest(
        self,
        binding: VisionLanguageModel,
    ) -> None:
        """Each of two requests is echoed with its own digest."""
        # One instance, two calls, in this order. An implementation that hand-filled the echo
        # from a remembered value -- the defect `VisionCompletion.answering` exists to remove --
        # would answer the second call with the first call's digest and pass every assertion
        # above this one.
        first = binding.complete(REFERENCE_REQUEST)
        second = binding.complete(OTHER_REQUEST)
        assert first.request_digest == REFERENCE_REQUEST.digest()
        assert second.request_digest == OTHER_REQUEST.digest()
        assert first.request_digest != second.request_digest

    # ── emptiness, truncation and refusal are data ──────────────────────────

    def test_an_empty_answer_is_data_and_never_an_error(self) -> None:
        """An empty answer is data and never an error."""
        model = self.answering(*CONTRACT_REQUESTS, text="", stop_reason=StopReason.COMPLETE)
        completion = model.complete(REFERENCE_REQUEST)
        assert completion.text == ""
        assert completion.is_complete is True

    @pytest.mark.parametrize("reason", list(StopReason))
    def test_every_stop_reason_this_binding_can_report_arrives_as_data(
        self,
        reason: StopReason,
    ) -> None:
        """Every stop reason this binding can report arrives as data."""
        # Truncation and refusal are the two the port names explicitly: a half-transcribed page is
        # a wrong page, and a diagram extraction may legitimately accept one, so the adapter may
        # not decide. Raising instead would take the decision away from the one caller entitled to
        # make it -- and `is_complete` is a property so that decision is a visible branch.
        if reason in self.unreachable_stop_reasons():
            pytest.skip(f"this binding's wire cannot report {reason.value}")
        model = self.answering(*CONTRACT_REQUESTS, text=PARTIAL_TEXT, stop_reason=reason)
        completion = model.complete(REFERENCE_REQUEST)
        assert completion.stop_reason is reason
        assert completion.text == PARTIAL_TEXT
        assert completion.is_complete is (reason is StopReason.COMPLETE)

    def test_truncation_and_refusal_are_reportable_by_every_binding(self) -> None:
        """Truncation and refusal are reportable by every binding."""
        # Guard the guard: the skip above is a statement about a wire, and a binding that named
        # every member unreachable would skip the whole rule. These three may never be skipped.
        required = {StopReason.COMPLETE, StopReason.OUTPUT_LIMIT, StopReason.REFUSAL}
        assert not (required & self.unreachable_stop_reasons())

    # ── every provider failure the port names is that error and nothing else ─

    @pytest.mark.parametrize(
        "error_type",
        MODEL_ERROR_TYPES,
        ids=[error_type.__name__ for error_type in MODEL_ERROR_TYPES],
    )
    def test_every_provider_failure_the_port_names_arrives_as_that_error(
        self,
        error_type: type[ModelError],
    ) -> None:
        """Every provider failure the port names arrives as that error."""
        # Five and not four: an unreachable endpoint is none of an entitlement problem, a rate
        # limit, a rejected payload or an unreadable body, and without a name for it an adapter
        # must either let a transport exception cross the port or report an outage as a throttle.
        with pytest.raises(error_type) as caught:
            self.failing(error_type).complete(REFERENCE_REQUEST)
        assert isinstance(caught.value, ModelError)
        assert isinstance(caught.value, OcrError)
        assert str(caught.value)


class TextRecognizerContract:
    """Every assertion ``TextRecognizer`` makes about its implementations."""

    # ── seams a binding must provide ────────────────────────────────────────

    @pytest.fixture
    def recognizer(self) -> TextRecognizer:
        """Return a recognizer that reads :data:`INKED_TEXT` off every :data:`CONTRACT_PAGES` page.

        At :data:`MEAN_CONFIDENCE`, which is neither of the two values the port calls dishonest.

        Returns
        -------
        TextRecognizer
            The subject, which ``ty`` checks against the Protocol here.
        """
        raise NotImplementedError

    def blank_recognizer(self) -> TextRecognizer:
        """Return a recognizer that reads nothing off every :data:`CONTRACT_PAGES` page.

        Nothing, as an engine reports it -- no lines at all -- rather than as a hand-written empty
        string. 86 of the 194 measured page artifacts are zero bytes, so this is the common case
        and not an edge one.

        Returns
        -------
        TextRecognizer
            The subject.
        """
        raise NotImplementedError

    def failing_recognizer(self, *, retryable: bool) -> TextRecognizer:
        """Return a recognizer whose every read fails with that retryability.

        Parameters
        ----------
        retryable
            Whether the failure it raises says retrying could produce a different answer. The
            only distinction any caller acts on, which is why one error with a flag replaced the
            transport-named error tree an earlier design proposed.

        Returns
        -------
        TextRecognizer
            The subject.
        """
        raise NotImplementedError

    def revision_variants(self) -> Mapping[str, TextRecognizer]:
        """Return one recognizer per identity-bearing constructor argument.

        Each differs from the baseline in exactly the argument its key names. ``TextRecognizer``
        publishes no ``fingerprint`` -- an earlier design's did, and "a ``fingerprint`` member is
        a constant in every double" was fatal to it -- so engine revision lives inside the slug
        and this seam is what proves the slug moves with it.

        Returns
        -------
        Mapping[str, TextRecognizer]
            Argument name to the recognizer that varied it. Must be non-empty.
        """
        raise NotImplementedError

    def unreachable_retryability(self) -> frozenset[bool]:
        """Return the ``retryable`` values this engine's failures structurally cannot carry.

        Empty for anything with an endpoint. The Apple binding overrides it with ``True``: it runs
        on-device with no quota, no endpoint and no clock, so every failure it can report gives
        the same answer again on the same bytes, and reporting one as retryable would spend the
        caller's retry budget on a certainty.

        Returns
        -------
        frozenset[bool]
            The values this binding cannot produce.
        """
        return frozenset()

    # ── the slug is the identity the app keys cache rows on ─────────────────

    def test_the_provider_id_is_a_non_empty_slug_that_never_moves(
        self,
        recognizer: TextRecognizer,
    ) -> None:
        """The provider id is a non empty slug that never moves."""
        before = recognizer.provider_id
        assert before
        recognizer.recognize(a_raster())
        assert recognizer.provider_id == before

    def test_the_provider_id_moves_when_the_engine_revision_moves(
        self,
        recognizer: TextRecognizer,
    ) -> None:
        """The provider id moves when the engine revision moves."""
        # The app folds this exact string into its cache key, so a bumped revision must
        # invalidate the rows the older reading behaviour wrote rather than silently reuse them.
        variants = self.revision_variants()
        assert variants, "a recognizer with no revision cannot invalidate anything it wrote"

        baseline = recognizer.provider_id
        moved = {name: varied.provider_id for name, varied in variants.items()}
        unmoved = sorted(name for name, value in moved.items() if value == baseline)
        assert not unmoved, f"varying {unmoved} left the provider slug unchanged"
        assert len(set(moved.values())) == len(moved)

    # ── a reading is attributed to the page it was read from ────────────────

    @pytest.mark.parametrize("page_ref", CONTRACT_PAGES)
    def test_a_reading_is_attributed_to_the_raster_that_was_read(
        self,
        recognizer: TextRecognizer,
        page_ref: str,
    ) -> None:
        """A reading is attributed to the raster that was read."""
        # The anti-misattribution assertion, and it is non-vacuous because every page in
        # CONTRACT_PAGES carries byte-identical pixels: `page_ref` is the only thing that could
        # have told the recognizer which page this was.
        reading = recognizer.recognize(a_raster(page_ref))
        assert reading.page_ref == page_ref
        assert reading.provider_id == recognizer.provider_id

    def test_pages_with_identical_pixels_are_still_attributed_separately(
        self,
        recognizer: TextRecognizer,
    ) -> None:
        """Pages with identical pixels are still attributed separately."""
        # `RasterImage.digest` excludes `page_ref` on purpose, so these two rasters are one cache
        # input and two pages. A reading that carried the digest's idea of identity rather than
        # the raster's would report one of these twice.
        one = a_raster(PAGE_ONE)
        two = a_raster(PAGE_TWO)
        assert one.digest() == two.digest()
        assert recognizer.recognize(one).page_ref == PAGE_ONE
        assert recognizer.recognize(two).page_ref == PAGE_TWO

    def test_one_instance_attributes_every_page_correctly_under_a_fan_out(
        self,
        recognizer: TextRecognizer,
    ) -> None:
        """One instance attributes every page correctly under a fan out."""
        # The port mandates that one instance tolerate concurrent reads, because the app fans
        # recognizers out. What a fan-out exposes is per-call state kept on the instance, and it
        # shows up as one page's reading attributed to another -- which is exactly what these
        # identical pixels make impossible to hide.
        pages = [page_ref for page_ref in CONTRACT_PAGES for _ in range(3)]
        with ThreadPoolExecutor(max_workers=len(pages)) as pool:
            readings = list(pool.map(lambda ref: recognizer.recognize(a_raster(ref)), pages))
        assert [reading.page_ref for reading in readings] == pages

    def test_a_raster_adopted_from_the_export_slices_twin_reads_as_the_same_page(
        self,
        recognizer: TextRecognizer,
    ) -> None:
        """A raster adopted from the export slice's twin reads as the same page."""
        # The digest half of this obligation is already discharged, over five value pairs, by
        # `test_the_two_raster_twins_produce_equal_digests_for_equal_fields` in
        # packages/rmspec-domain/tests/test_ports_ocr.py -- see this module's docstring. What is
        # adapter-side and uncovered there is that a *reader* is indifferent to the conversion: a
        # reader touches `data` and `page_ref` and never calls `digest`, so agreeing digests
        # cannot prove it.
        adopted = RasterImage.from_raster(an_export_twin(PAGE_THREE))
        direct = a_raster(PAGE_THREE)
        assert adopted == direct
        assert recognizer.recognize(adopted) == recognizer.recognize(direct)

    # ── a blank page is a success, and its confidence is not a number ───────

    def test_an_inked_page_reports_a_confidence_inside_the_ports_range(
        self,
        recognizer: TextRecognizer,
    ) -> None:
        """An inked page reports a confidence inside the port's range."""
        # Character-weighted, never line-weighted: both legacy engines averaged per line, so a
        # page holding one crisp word and ninety-nine characters of scribble reported 0.5 where
        # the truth was 0.01. Both recognizers here fold two lines of equal confidence, so the
        # answer is that confidence exactly -- which a line-weighted fold also gives, and which is
        # why `test_the_mean_is_character_weighted_not_line_weighted` lives in each adapter's own
        # suite over a page shaped to separate them.
        reading = recognizer.recognize(a_raster())
        assert reading.text == INKED_TEXT
        assert reading.has_text is True
        assert reading.mean_confidence is not None
        assert 0.0 <= reading.mean_confidence <= 1.0
        assert reading.mean_confidence == pytest.approx(MEAN_CONFIDENCE)

    def test_a_blank_page_is_a_successful_empty_reading(self) -> None:
        """A blank page is a successful empty reading."""
        # Never an error. The app raises `NoTextRecognized` and skips the completion call rather
        # than paying for a blank page, and it can only do that if the engine reported success.
        reading = self.blank_recognizer().recognize(a_raster())
        assert isinstance(reading, Recognition)
        assert reading.text == ""
        assert reading.has_text is False
        assert reading.page_ref == PAGE_ONE

    def test_a_blank_reading_reports_no_confidence_rather_than_zero(self) -> None:
        """A blank reading reports no confidence rather than zero."""
        # `None`, not 0.0. A required float forced this case to fabricate 0.0, which reads as a
        # garbage reading, or 1.0, which reads as a perfect one -- and the number is what a caller
        # uses to decide whether to trust a transcription.
        assert self.blank_recognizer().recognize(a_raster()).mean_confidence is None

    # ── failure names the engine and one actionable distinction ─────────────

    @pytest.mark.parametrize("retryable", [True, False])
    def test_a_failure_names_the_engine_and_whether_retrying_could_help(
        self,
        *,
        retryable: bool,
    ) -> None:
        """A failure names the engine and whether retrying could help."""
        # There is deliberately no "engine unavailable" member to test for: a missing optional
        # package is a composition failure that names the package and its extra, and a degraded
        # set of engines is a visible binding in the composition root.
        if retryable in self.unreachable_retryability():
            pytest.skip(f"this engine's failures cannot carry retryable={retryable}")
        recognizer = self.failing_recognizer(retryable=retryable)
        with pytest.raises(RecognitionFailed) as caught:
            recognizer.recognize(a_raster())
        assert caught.value.provider_id == recognizer.provider_id
        assert caught.value.retryable is retryable
        assert caught.value.detail

    def test_an_unretryable_failure_is_reportable_by_every_engine(self) -> None:
        """An unretryable failure is reportable by every engine."""
        # Guard the guard, as above: the skip is a claim about an engine, and a binding that
        # named both values unreachable would skip the whole rule. Permanent failure is the one
        # every engine can produce, so it may never be skipped.
        assert False not in self.unreachable_retryability()
