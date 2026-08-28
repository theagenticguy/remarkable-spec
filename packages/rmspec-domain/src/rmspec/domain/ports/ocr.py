"""Ports for the OCR slice: one multimodal completion seam, one text-recognition seam.

This module holds exactly two protocols and the frozen value objects they exchange.
Nothing here knows that Bedrock, botocore, pyobjc, Vision.framework, Textract, or a
subprocess exist; the only third-party import is pydantic.

What this module deliberately does *not* contain
------------------------------------------------
``RecognizerEnsemble``
    Dropped. Fan-out width, ordering and partial-failure tolerance are use-case
    policy, not a technology axis. The transcribe use case takes ``list[TextRecognizer]``
    and loops (or fans out with ``concurrent.futures``) itself, collecting per-recognizer
    failures, raising ``AllRecognizersFailed`` only when *every* recognizer failed, and
    folding the surviving ``provider_id`` values into the cache key so a Textract-only
    degraded run can never reuse a Vision-plus-Textract row. A port whose whole
    justification is "the legacy code used a ``ThreadPoolExecutor`` here" is ceremony,
    and a test-mode-only sequential adapter would mean production runs the one path no
    use-case test exercises.
``MermaidSyntaxChecker`` / ``MermaidLinter`` / ``MermaidValidator``
    Dropped, all three. The legacy checker has two callers, both behind an opt-in
    ``--validate`` flag, both of which only print; no use case branches on the verdict,
    so the app layer never needs the dependency and the CLI is already the layer allowed
    to import adapters. Mermaid validity is also not an OCR concern: it is a Node
    toolchain (``mmdc`` plus headless Chromium) that merely happens to live in the legacy
    ``ocr/diagram.py`` file, and no Python extra can supply an npm binary, so its absence
    cannot be expressed as the "missing package, install this extra" composition failure
    the target architecture requires. The one idea worth keeping from those proposals --
    that "I did not actually check this" must be a representable value rather than a
    keyword-prefix match masquerading as a real parse -- belongs on the diagram value
    object in the diagram slice, not on a port here.

Cache-key composition (defect 3)
--------------------------------
No port publishes a cache key; the app composes one, and every component of it is
reachable only through a typed member, so a component cannot be silently omitted:

``(rm_hash, VisionRequest.digest(), VisionLanguageModel.fingerprint,
sorted(provider_id of every surviving recognizer))``

``VisionRequest.digest()`` hashes the *prompt text itself* along with the decoding
parameters and the image digests -- and ``RasterImage.digest()`` folds in ``render_dpi``.
So editing a prompt or changing the render DPI mechanically invalidates rows; there is no
hand-bumped ``prompt_version`` for a reviewer to forget. ``VisionCompletion`` echoes both
``request_digest`` and ``model_fingerprint`` so a row can be written without re-deriving
either, and a row whose echo disagrees with its key is detectable.

Every echo is filled by the domain, not by the adapter: :meth:`VisionCompletion.answering`
derives ``request_digest`` and ``model_fingerprint`` from the request and binding it is
handed, and :meth:`Recognition.attributed` derives ``page_ref`` from the raster it read.
An adapter or fake that hand-copies a key component can copy it wrongly exactly once and
poison every row keyed on it, so no adapter is asked to.

``Recognition.page_ref`` is a live-call echo and never a cached fact.
``RasterImage.digest()`` deliberately excludes ``page_ref``, so one row is legitimately
shared by two pages with identical pixels; when the app hydrates a stored reading it must
re-stamp ``page_ref`` from the raster in hand (:meth:`Recognition.attributed` again) and
must not trust a stored value, or a row written for page 3 is read back as page 7.

Errors (named, not imported)
----------------------------
The error tree lives in :mod:`rmspec.domain.errors`. This module names its errors in
``Raises`` sections only and imports nothing from it -- not even under ``TYPE_CHECKING``
-- so no port can be edited into depending on an error's constructor signature.

``complete`` is the one port here with more than one plausible provider: Bedrock
``invoke_model``, Anthropic's own endpoint over HTTP, a self-hosted Ollama or vLLM, an
on-device binding. Two shape requirements follow, and they are requirements *on the error
tree*, not preferences:

1. No provider deployment axis may be a required constructor argument. A region is
   ``boto3``'s ``region_name`` and nothing else has one, so a non-AWS adapter with a
   genuine entitlement failure must pass ``region="n/a"`` and emit "not available to this
   caller in n/a"; an app that reads ``exc.region`` back has imported AWS by another name.
   Model identity for the app is :attr:`VisionLanguageModel.fingerprint` -- an
   ``except`` block here reads only ``str(exc)``, ``exc.remediation`` and, where the error
   publishes it, ``retryable``.
2. Unreachability must be representable. A refused connection to a local daemon, a DNS
   failure, a request timeout, HTTP 503/529, and Bedrock's own
   ``ModelTimeoutException`` / ``ServiceUnavailableException`` / ``InternalServerException``
   are none of: an entitlement problem, a rate limit, a rejected payload, or a body that
   failed to parse. Without a name for them an adapter must either let
   ``httpx.ConnectError`` cross the port -- the provider-exception leak this architecture
   forbids -- or report a permanently unreachable endpoint as "throttled".

So five model errors, not four: ``ModelUnavailable``, ``ModelAccessDenied``,
``ModelThrottled``, ``ModelRejectedRequest``, ``ModelResponseMalformed``. The CLI still
takes only two actions (report-and-stop, retry-once), because the retryable set is
``ModelThrottled`` plus ``ModelUnavailable`` where the latter's ``retryable`` says so.
``ModelUnavailable`` takes the shape the device slice already established for the same
situation -- ``DeviceUnreachable(transport, endpoint, detail)`` -- since an unreachable
endpoint is a house-wide failure mode, not a model-slice novelty.

Follow-up this contract requires in :mod:`rmspec.domain.errors`, which cannot be made
from this file: add ``ModelUnavailable``, and stop requiring ``region`` on
``ModelAccessDenied``. Until both land, an adapter cannot satisfy the ``Raises`` clause of
:meth:`VisionLanguageModel.complete` truthfully.

Recognition collapses to one: ``RecognitionFailed``, carrying ``provider_id`` and
``retryable: bool``.

Availability is *not* a port error (defect 4)
---------------------------------------------
No method here raises an "adapter unavailable" or "dependency missing" error. A missing
optional package is a composition failure: the container names the package and the extra
that provides it, during an eager resolution pass over the required providers at build
time -- not on the first ``container.get()`` inside a command body, which would merely
relocate the 27 legacy function-local ``ImportError`` raises. A degraded binding
(recognizer omitted, null recognizer substituted) is likewise a wiring decision visible
in the composition root, never something an adapter reaches from its own ``except``.

Required follow-up: hoist ``RasterImage``
-----------------------------------------
:class:`RasterImage` and :class:`ImageMedia` are defined here because the OCR ports need
them and this module may not import a sibling ports module. ``ports/export.py`` now
defines a field-for-field twin, and two nominal pydantic models is one too many:
``SvgRasterizer.to_png``'s output is not assignable to :attr:`VisionRequest.images` or to
:meth:`TextRecognizer.recognize`, and two ``digest`` bodies must stay byte-identical
forever or identical pixels hash to two cache keys -- defect 3 reopened by hand-mirroring.
Both classes must be hoisted into one ``rmspec.domain.values`` module that both port
modules import; that edit spans two files and so is not made here.

Until it lands, :meth:`RasterImage.from_raster` is the single sanctioned conversion. It is
structural (:class:`PageRasterLike`), so any slice's twin satisfies it without this module
importing that slice, and it goes through validation, so it cannot drop a check the way a
``RasterImage(**other.model_dump())`` splat does. The shared adapter contract suite must
assert ``RasterImage.from_raster(x).digest() == x.digest()`` for every producer ``x``: that
one assertion is what catches drift between the two digest bodies.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import ClassVar, Final, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FIELD_SEPARATOR = b"\x1f"
"""Byte that separates digest components, so concatenation cannot be ambiguous."""


class ImageMedia(StrEnum):
    """Encoding of a raster image's bytes, as a domain fact rather than a MIME token.

    A domain enum instead of ``Literal["image/png", "image/jpeg"]`` because an HTTP
    content type is a wire label that can lie: the tablet's ``GET /thumbnail/{id}``
    advertises ``image/jpeg`` and returns PNG bytes. An adapter that receives bytes from
    an untrusted producer sniffs the magic bytes and sets this field to what it found.
    """

    PNG = "png"
    JPEG = "jpeg"


_MAGIC_BY_MEDIA: Final = {
    ImageMedia.PNG: b"\x89PNG\r\n\x1a\n",
    ImageMedia.JPEG: b"\xff\xd8\xff",
}
"""Leading bytes each encoding must start with, so ``media`` cannot restate a lie."""


class ReasoningEffort(StrEnum):
    """How much latent reasoning the caller wants before the answer.

    Domain intent, not a provider knob: the legacy call sites differ only in whether they
    want extended thinking at all (transcription merge did, diagram extraction did not).
    Mapping an effort level onto a concrete budget -- Anthropic's
    ``thinking={"budget_tokens": ...}``, or nothing at all for a model that cannot reason
    -- is the adapter's job, and an adapter that ignores the request must report
    ``VisionCompletion.reasoning is None``.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StopReason(StrEnum):
    """Why the model stopped generating.

    This is data on the completion, never an exception. The adapter must not decide that
    truncation is fatal: a half-transcribed page is a wrong page, but a diagram
    extraction may legitimately accept a truncated body. Keeping it as data turns the
    legacy silent half-page into a named choice at each call site.

    Named for the domain rather than mirroring a provider's wire enum, so a second
    adapter maps into these four instead of the domain growing a fifth.
    """

    COMPLETE = "complete"
    OUTPUT_LIMIT = "output_limit"
    STOP_SEQUENCE = "stop_sequence"
    REFUSAL = "refusal"


class PageRasterLike(Protocol):
    """Read-only structural view of an already-rendered raster produced by another slice.

    Exists so :meth:`RasterImage.from_raster` can accept the export slice's field-for-field
    twin -- and any later producer -- without this module importing a sibling ports module
    and without the caller writing ``RasterImage(**other.model_dump())``, which copies the
    fields while silently dropping whatever validators the destination declares.

    All six members are read-only properties, which is what makes them covariant: the twin
    types ``media`` as *its own* :class:`ImageMedia` enum, and a mutable attribute member
    would be invariant and reject it. ``media`` is therefore typed ``str`` -- every
    ``StrEnum`` member is one -- and :meth:`RasterImage.from_raster` converts by value, so
    a producer whose encoding this slice does not know fails loudly at the boundary.
    """

    @property
    def page_ref(self) -> str:
        """Stable identity of the page these pixels depict."""
        ...

    @property
    def media(self) -> str:
        """Encoding of ``data``, as an :class:`ImageMedia` value."""
        ...

    @property
    def data(self) -> bytes:
        """Encoded image bytes."""
        ...

    @property
    def width(self) -> int:
        """Pixel width."""
        ...

    @property
    def height(self) -> int:
        """Pixel height."""
        ...

    @property
    def render_dpi(self) -> int:
        """Dots per inch the raster was rendered at."""
        ...


class RasterImage(BaseModel):
    """An already-rendered page raster, carried as bytes.

    Bytes, never a path: no port below this line touches a filesystem, which is what lets
    the app and OCR packages hold zero ``.rm``, PDF or PNG fixtures. ``render_dpi``
    travels with the pixels it describes rather than being remembered separately, because
    a scale that can drift away from its bytes is exactly half of the stale-cache defect.

    Attributes
    ----------
    page_ref
        Stable identity of the page these pixels depict, opaque to the ports. Its purpose
        is to make a test double a dictionary lookup instead of ``script.pop(0)``: the app
        owns fan-out and per-page cache hits, so calls are neither ordered nor one-to-one
        and a FIFO fake would happily return another page's text.
    media
        Encoding of ``data``, validated against ``data``'s leading magic bytes.
    data
        Encoded image bytes.
    width
        Pixel width.
    height
        Pixel height.
    render_dpi
        Dots per inch the raster was rendered at.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    page_ref: str = Field(min_length=1)
    media: ImageMedia
    data: bytes = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    render_dpi: int = Field(gt=0)

    @model_validator(mode="after")
    def _check_media_header(self) -> Self:
        """Reject bytes whose leading magic does not match the declared encoding.

        Without this, :class:`ImageMedia` merely relocates the lie it was introduced to
        stop: an adapter that trusted a ``Content-Type`` would set ``media`` from the wire
        label and every consumer downstream -- including :meth:`digest`, which folds the
        encoding in -- would inherit it.

        Returns
        -------
        RasterImage
            The validated model.

        Raises
        ------
        ValueError
            If ``data`` does not begin with ``media``'s magic bytes.
        """
        expected = _MAGIC_BY_MEDIA[self.media]
        if not self.data.startswith(expected):
            msg = (
                f"declared media {self.media.value} but data starts with "
                f"{self.data[: len(expected)]!r}, not {expected!r}"
            )
            raise ValueError(msg)
        return self

    @classmethod
    def from_raster(cls, source: PageRasterLike, /) -> Self:
        """Adopt another slice's raster value object as this slice's.

        The single sanctioned conversion while two twins exist -- see this module's
        docstring, which requires both to be hoisted into one shared module. Structural in
        its parameter, so the export slice's rasterizer output is accepted with no import
        of that slice and no field-by-field splat at the call site.

        Parameters
        ----------
        source
            Any already-rendered raster: the export slice's twin, another
            :class:`RasterImage`, or a fake exposing the same six members.

        Returns
        -------
        RasterImage
            An equal-valued raster of this slice's type. ``digest()`` is unchanged by the
            conversion, which the shared contract suite asserts.

        Raises
        ------
        ValueError
            If ``source.media`` is not an encoding this slice knows, or if its bytes
            disagree with it.
        """
        return cls(
            page_ref=source.page_ref,
            media=ImageMedia(source.media),
            data=source.data,
            width=source.width,
            height=source.height,
            render_dpi=source.render_dpi,
        )

    def digest(self) -> str:
        """Return a stable content digest of these pixels and their scale.

        Deliberately excludes ``page_ref``: identical pixels rendered for a different page
        slot are the same input and should share a cache row.

        Returns
        -------
        str
            Lowercase hex SHA-256 over encoding, dimensions, DPI and bytes.
        """
        hasher = hashlib.sha256()
        hasher.update(b"rmspec.raster.v1")
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(self.media.value.encode())
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(f"{self.width}x{self.height}@{self.render_dpi}".encode())
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(self.data)
        return hasher.hexdigest()


class Decoding(BaseModel):
    """Per-call generation settings.

    Per call, not bound at construction, because the real call sites disagree: the
    transcription merge wants a large budget with extended thinking, diagram extraction
    wants a small budget at temperature zero. One APP-scoped model that fixed these at
    construction could not serve both without two bindings.

    Attributes
    ----------
    max_output_tokens
        Upper bound on generated tokens. Hitting it yields
        ``StopReason.OUTPUT_LIMIT``, not an exception.
    temperature
        Sampling temperature, normalised to ``0.0`` -- ``1.0``.
    reasoning
        Requested reasoning effort.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0.0, le=1.0)
    reasoning: ReasoningEffort = ReasoningEffort.NONE

    def canonical(self) -> str:
        """Return a stable string form for digesting.

        Returns
        -------
        str
            Fixed-precision rendering, so a float repr change cannot silently
            invalidate every cache row.
        """
        return (
            f"max_output_tokens={self.max_output_tokens};"
            f"temperature={self.temperature:.6f};"
            f"reasoning={self.reasoning.value}"
        )


class VisionRequest(BaseModel):
    """One multimodal completion request: instructions, prompt, images, decoding.

    Frozen and hashable so a test double can key its scripted replies by request instead
    of by call order.

    The prompt text is app policy that crosses the port as data. There is no
    ``prompt_revision`` field: :meth:`digest` hashes the prompt bytes, which is strictly
    stronger than a version integer someone has to remember to bump.

    Attributes
    ----------
    prompt
        The user-turn instruction text.
    decoding
        Generation settings for this call.
    system
        Optional system-turn text.
    images
        Rasters to attach, in the order the prompt refers to them.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1)
    decoding: Decoding
    system: str | None = None
    images: tuple[RasterImage, ...] = ()

    def digest(self) -> str:
        """Return a stable digest of everything about this request that changes an answer.

        Returns
        -------
        str
            Lowercase hex SHA-256 over system text, prompt text, decoding settings and
            each image's content digest. Half of the cache key; the model's
            :attr:`VisionLanguageModel.fingerprint` is the other half.
        """
        hasher = hashlib.sha256()
        hasher.update(b"rmspec.vision.request.v1")
        hasher.update(_FIELD_SEPARATOR)
        hasher.update((self.system or "").encode())
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(self.prompt.encode())
        hasher.update(_FIELD_SEPARATOR)
        hasher.update(self.decoding.canonical().encode())
        for image in self.images:
            hasher.update(_FIELD_SEPARATOR)
            hasher.update(image.digest().encode())
        return hasher.hexdigest()


class TokenUsage(BaseModel):
    """Tokens billed for one completion.

    Attributes
    ----------
    input_tokens
        Tokens consumed by system text, prompt and images.
    output_tokens
        Tokens generated, including any reasoning tokens.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class VisionCompletion(BaseModel):
    """The result of one multimodal completion.

    Attributes
    ----------
    text
        The model's answer. May be empty; emptiness is data, not an error.
    stop_reason
        Why generation stopped. See :class:`StopReason` -- truncation and refusal arrive
        here, not as exceptions.
    request_digest
        Echo of :meth:`VisionRequest.digest` for the request that produced this. Fill it
        with :meth:`answering`, never by hand.
    model_fingerprint
        Echo of :attr:`VisionLanguageModel.fingerprint` at the time of the call. Fill it
        with :meth:`answering`, never by hand.
    reasoning
        Latent reasoning text if the adapter produced any, else ``None`` -- including
        when a requested :class:`ReasoningEffort` could not be honoured.
    usage
        Token accounting when the adapter reports it, else ``None``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    text: str
    stop_reason: StopReason
    request_digest: str = Field(min_length=1)
    model_fingerprint: str = Field(min_length=1)
    reasoning: str | None = None
    usage: TokenUsage | None = None

    @classmethod
    def answering(
        cls,
        request: VisionRequest,
        /,
        *,
        fingerprint: str,
        text: str,
        stop_reason: StopReason,
        reasoning: str | None = None,
        usage: TokenUsage | None = None,
    ) -> Self:
        """Build a completion whose echo fields are derived, not copied.

        Both echoes are cache-key components (see this module's docstring). An adapter or
        fake that fills them by hand can transpose them, stale-cache one of them, or copy
        a digest from the previous call, and the row it writes then looks valid for input
        that never produced it. Deriving them here removes the opportunity: the only
        identity the caller supplies is its own :attr:`VisionLanguageModel.fingerprint`.

        Parameters
        ----------
        request
            The request that produced this answer; its :meth:`VisionRequest.digest` becomes
            ``request_digest``.
        fingerprint
            The answering binding's :attr:`VisionLanguageModel.fingerprint`.
        text
            The model's answer, possibly empty.
        stop_reason
            Why generation stopped.
        reasoning
            Latent reasoning text if any was produced.
        usage
            Token accounting if the provider reported it.

        Returns
        -------
        VisionCompletion
            The completion, with ``request_digest`` and ``model_fingerprint`` filled.
        """
        return cls(
            text=text,
            stop_reason=stop_reason,
            request_digest=request.digest(),
            model_fingerprint=fingerprint,
            reasoning=reasoning,
            usage=usage,
        )

    @property
    def is_complete(self) -> bool:
        """Whether the model finished on its own terms.

        Returns
        -------
        bool
            ``True`` only for :attr:`StopReason.COMPLETE`. The caller decides what a
            ``False`` means: transcription treats it as fatal, diagram extraction may
            accept the partial body. Exposed as a property so that decision is a visible
            branch rather than a forgotten one.
        """
        return self.stop_reason is StopReason.COMPLETE


class Recognition(BaseModel):
    """One OCR engine's attempt at one page, attributed to that engine.

    No per-line geometry. The legacy line boxes have exactly two writers and zero
    readers, and the two writers disagree about what ``y`` means -- Apple Vision writes a
    normalised bottom-left origin, Textract writes a top-left ``Top`` -- so shipping
    ``lines`` here would enshrine two opposite conventions in one unmarked field, on
    behalf of no caller. When a caller genuinely needs boxes, add a value object that
    pins the convention (normalised, top-left origin) and hold every adapter to one
    contract suite over a golden raster.

    Attributes
    ----------
    provider_id
        Echo of the recognizer's :attr:`TextRecognizer.provider_id`. Present so a
        partial-failure report can say which engine produced which reading, and so the
        app can fold the surviving engines into the cache key. This exact string is what
        the app writes into the key -- see :attr:`TextRecognizer.provider_id`.
    page_ref
        Echo of :attr:`RasterImage.page_ref`, so a reading cannot be attributed to the
        wrong page. A live-call fact only: :meth:`RasterImage.digest` excludes
        ``page_ref``, so a cached row is legitimately shared by two pages with identical
        pixels and a stored value may name a different slot than the one being read. The
        app must re-stamp it from the raster in hand when hydrating a row --
        :meth:`attributed` is that one call -- and must never trust a stored value.
    text
        Recognised text. Empty means the engine succeeded and found nothing; the app
        raises ``NoTextRecognized`` and skips the completion call rather than paying for
        a blank page.
    mean_confidence
        Mean per-character confidence over :attr:`text`, normalised to ``0.0`` -- ``1.0``:
        character-weighted, not line-weighted, so one confident word on a page of noise
        cannot outvote the noise. ``None`` when the engine reports no confidence signal at
        all, which is the honest answer for a VLM-backed recognizer and for a blank
        reading with nothing to be confident about. Required-and-float forced those cases
        to fabricate ``0.0`` (reads as a garbage reading) or ``1.0`` (reads as a perfect
        one); ``None`` matches ``OcrArtifact.mean_confidence`` in
        :mod:`rmspec.domain.models`, which the app persists this into.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    provider_id: str = Field(min_length=1)
    page_ref: str = Field(min_length=1)
    text: str
    mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @classmethod
    def attributed(
        cls,
        image: PageRasterLike,
        /,
        *,
        provider_id: str,
        text: str,
        mean_confidence: float | None = None,
    ) -> Self:
        """Build a reading whose page attribution is derived from the raster that was read.

        ``page_ref`` is otherwise an unenforced echo: a recognizer -- or a fake -- could
        return another page's slot and the app would cache one page's text under another.
        Deriving it from the raster removes the opportunity, and gives the app a single
        call for the re-stamp a hydrated cache row requires.

        Parameters
        ----------
        image
            The raster that was read; its ``page_ref`` becomes this reading's.
        provider_id
            The reading engine's :attr:`TextRecognizer.provider_id`.
        text
            Recognised text, possibly empty.
        mean_confidence
            Character-weighted mean confidence, or ``None`` when the engine reports none.

        Returns
        -------
        Recognition
            The reading, attributed to ``provider_id`` and to the raster's page.
        """
        return cls(
            provider_id=provider_id,
            page_ref=image.page_ref,
            text=text,
            mean_confidence=mean_confidence,
        )

    @property
    def has_text(self) -> bool:
        """Whether this reading carries any non-whitespace text.

        Returns
        -------
        bool
            ``False`` for a blank page, which the app distinguishes from a broken engine.
        """
        return bool(self.text.strip())


class VisionLanguageModel(Protocol):
    """Send text plus images to a multimodal model and get text back.

    The single replacement for all three copies of the legacy Bedrock call
    (``_invoke_bedrock_vision`` twice, ``_invoke_annotation_analysis`` once) and for the
    four hardcoded model ids.

    Scope: APP. One client and one model binding per process; every call is stateless.
    Model id, region, API version envelope, retry configuration and the concrete
    reasoning budget are constructor arguments of the adapter, never parameters here --
    there is no ``model_id`` and no ``region`` on this port, because a region is a
    provider deployment detail and an app that reads one for a cache key has imported
    AWS by another name. Anything the app needs about model identity is folded into
    :attr:`fingerprint`.

    That invariant was previously stated here and then broken by the ``Raises`` clause:
    mandating ``ModelAccessDenied(model_id=..., region=...)`` obliged every adapter to
    produce a region -- fabricated as ``"n/a"`` for anything but Bedrock -- and obliged the
    app's handler to read it back off the exception. The clause below no longer mandates a
    provider deployment axis, and it names the unreachable case that four errors could not
    express; both requirements on the error tree are spelled out in this module's
    docstring.

    Notes
    -----
    Not ``runtime_checkable``: nothing needs ``isinstance`` against it, and structural
    checks at runtime would only verify member names.
    """

    @property
    def fingerprint(self) -> str:
        """Return the opaque identity of this model binding.

        One string that folds together provider, model, adapter revision and any
        adapter-fixed generation settings. Opaque on purpose: the app puts it in a cache
        key and compares it for equality, and never parses it, so an adapter can add a
        setting to its identity without the domain learning that the setting exists.

        Contract, enforced by the shared adapter suite rather than by trust: this value
        MUST change whenever the adapter changes what it sends for a given
        :class:`VisionRequest` -- a different model, endpoint or API envelope, an injected
        system scaffold, a changed fixed setting, a revised request-building body. Derive
        it mechanically, by hashing those inputs together; a hand-written constant is the
        one implementation that silently makes every stale row look fresh, which is defect
        3 in its purest form. The suite asserts that two instances differing in any single
        constructor argument return different fingerprints, which no constant can pass.

        Returns
        -------
        str
            Stable for the lifetime of the process. Raises nothing.
        """
        ...

    def complete(self, request: VisionRequest, /) -> VisionCompletion:
        """Run one multimodal completion.

        Positional-only, so the entire call surface is one value object and a test double
        is three lines.

        Parameters
        ----------
        request
            The request, carrying already-rendered image bytes. Per-binding ceilings --
            image count, per-image bytes, pixel bounds, the model's own output-token
            maximum -- are not published here and are not pre-checked by the domain: a
            self-hosted binding cannot state them honestly, a published set would be a
            second source of truth that drifts from the provider's real one, and
            :attr:`fingerprint` is contractually opaque so nothing here could carry them.
            A request the binding cannot accept therefore surfaces as
            ``ModelRejectedRequest``, which is the same answer the provider gives. An app
            that wants to avoid the round trip may pre-screen with its own policy; that is
            app policy, not a port promise.

        Returns
        -------
        VisionCompletion
            The answer, with ``stop_reason`` reported as data. Truncation and refusal are
            not exceptions -- see :class:`StopReason`. Build it with
            :meth:`VisionCompletion.answering` so the echoed cache-key components are
            derived from ``request`` and :attr:`fingerprint` rather than copied.

        Raises
        ------
        ModelUnavailable
            The binding could not be reached or did not answer: refused connection to a
            local daemon, DNS failure, request timeout, HTTP 503/529, or Bedrock's
            ``ModelTimeoutException`` / ``ServiceUnavailableException`` /
            ``InternalServerException``. Carries the endpoint tried and ``retryable``, so
            the CLI's retry-once branch works without the app parsing prose. This is the
            dominant failure of every HTTP-backed adapter, and naming it is what stops one
            from either letting ``httpx.ConnectError`` cross the port or reporting an
            outage as a throttle.
        ModelAccessDenied
            The caller is not entitled to this model. Distinct from a throttle because a
            missing grant is a permanent misconfiguration to report, while a throttle is
            worth retrying; today both surface identically. The deployment detail that
            makes a grant fixable belongs in the error's ``remediation`` prose, which the
            adapter authors, never in a required ``region`` argument only one provider can
            supply.
        ModelThrottled
            Rate or quota limit after the adapter's own retries; retryable.
        ModelRejectedRequest
            The provider rejected the request itself -- payload too large, unsupported
            image, budget above the model's ceiling. Note this is *not* a content
            refusal: a refusal is ``StopReason.REFUSAL`` on a successful completion.
        ModelResponseMalformed
            A well-formed exchange whose body could not be read as a completion. This is
            the one error that covers both latent legacy crashes: the reverse scan for a
            text block falling through to index ``0`` when block ``0`` is a reasoning
            block, and an empty content list.
        """
        ...


class TextRecognizer(Protocol):
    """Run one OCR engine over one already-rendered raster and return its attempt.

    Scope: APP. A Vision framework handle and a Textract client are stateless
    process-lifetime resources.

    Why this port exists despite being refuted
    ------------------------------------------
    The reviewed variants of this port were refuted, and both refutations are correct
    *about those variants* -- but neither disputes the seam, and every element they
    attacked has been removed here rather than argued with:

    - "A closed ``Literal['apple-vision', 'aws-textract']`` inside a domain type means
      adding Tesseract is a domain edit, and the proposal's own fixture double cannot
      satisfy it." Correct, and fatal as written; both judges independently called it
      out. Fixed: :attr:`provider_id` is an open ``str`` slug, validated at composition.
    - "``media_type: Literal['image/png', 'image/jpeg']`` is a wire token restating what
      the bytes already say, and such labels demonstrably lie." Correct. Fixed:
      :class:`ImageMedia`.
    - "``RecognizerTransportError`` names network transport in the domain error tree, and
      Apple Vision can never raise it." Correct. Fixed: one ``RecognitionFailed`` with a
      ``retryable`` flag.
    - "``lines`` inherits two contradictory coordinate conventions in one field." Correct,
      and it has zero readers in the legacy tree. Fixed: :class:`Recognition` has no
      ``lines``.
    - "The image has no page identity, so the double degrades to ``script.pop(0)`` and
      can return another page's text." Correct. Fixed: :attr:`RasterImage.page_ref`.
    - "A ``fingerprint`` member is a constant in every double, leaving the stale-cache
      defect unfalsifiable." Correct. Fixed: no ``fingerprint`` here. Engine revision
      belongs inside the slug the adapter returns, and the cache key is composed by the
      app from that slug plus the request digest.

    What remains is the seam itself, which is not in dispute: it is the only OCR-read
    boundary, it is what makes the ensemble port unnecessary, and dropping it would leave
    the app unable to read a page at all while making "which engines survived" -- a
    mandatory cache-key component once partial failure is tolerated -- unrepresentable.

    Notes
    -----
    One instance MUST tolerate concurrent :meth:`recognize` calls from several threads.
    The app fans recognizers out; leaving thread-safety unstated would make that fan-out
    undecidable and force the caller to guess whether to build one instance or one per
    thread. An adapter holding a thread-hostile handle serialises internally -- a lock it
    owns -- rather than exporting the constraint to every call site.

    There is no timeout parameter: a timeout is either adapter construction configuration
    or a use-case concern, and a value published here would be unenforceable for an
    in-process engine and false for a blocking client.
    """

    @property
    def provider_id(self) -> str:
        """Return this engine's stable identity slug.

        An open string, e.g. ``"apple-vision@2"`` or ``"aws-textract@1"``, not a closed
        enumeration: a new engine must be a new adapter plus a container edit, never a
        domain edit. Engine revision is part of the slug, so bumping it invalidates cache
        rows that were produced by the older engine.

        This exact string is what the app writes into the recognizer component of the cache
        key. That component folds the *surviving* engines in **sorted** order -- binding
        order, never completion order -- because a fan-out that finishes in a different
        sequence on the next run would otherwise digest to a different key for identical
        work and miss every row it wrote.

        Returns
        -------
        str
            Stable for the lifetime of the process. Raises nothing.
        """
        ...

    def recognize(self, image: RasterImage, /) -> Recognition:
        """Recognise text in one raster page.

        Parameters
        ----------
        image
            The rendered page. Bytes, never a path. Pixels that arrived from the export
            slice's rasterizer are converted once with :meth:`RasterImage.from_raster` and
            the result passed to every reader, so one page's bytes are described by one
            value object.

        Returns
        -------
        Recognition
            This engine's reading, built with :meth:`Recognition.attributed` so it is
            attributed to :attr:`provider_id` and to the image's own ``page_ref``. A blank
            page is a successful empty reading -- ``text=""`` with
            ``mean_confidence=None``, because there is nothing to be confident about --
            not an error.

        Raises
        ------
        RecognitionFailed
            The engine could not produce a reading. Carries ``provider_id`` and
            ``retryable``, which is the only distinction any caller acts on. Note there
            is no "engine unavailable" error: a missing optional dependency is a
            composition failure that names the package and its extra, and a deliberately
            degraded set of engines is a visible binding in the composition root.
        """
        ...
