"""Read one document's pages through four tiers, paying only for the tiers it must.

The tiering is policy, so it lives here
---------------------------------------
Adapters supply signals and this use case decides which of them to believe.
:mod:`rmspec.domain.ports.ocr` dropped its ``RecognizerEnsemble`` for exactly this
reason: an ensemble port would have put "which reading wins" behind a boundary where
no test could see it, and every question this module answers -- whether to pay, which
text wins a tie, what identifies the row that gets written -- is a decision, not a
technology.

Four tiers, and what each one costs
-----------------------------------
0. **The device's own reading, free.** :meth:`~rmspec.domain.ports.ocr.HandwrittenTextIndex.lookup`
   answers with three states, not two, and this module keeps all three apart.
   ``None`` is *not indexed*; ``text == ""`` is *indexed and found nothing*; text is a
   prior worth trusting. Collapsing the first two into "the index answered, so skip the
   paid read" is the defect the port exists to prevent, and it is the **normal** case:
   measured on this tablet, ``TestNb``'s page was written after the last index build and
   has no row at all.
1. **Textract, and Apple Vision when bound.** Fanned out, per-recognizer failures
   collected. Every recognizer failing is
   :class:`~rmspec.domain.errors.AllRecognizersFailed`; one failing is data on the
   result, because one engine's outage must not discard another's output.
2. **A vision read of the raster**, by the ``reader`` binding.
3. **A merge and adjudication** of tiers 0, 1, 2 and the image itself, by the
   ``adjudicator`` binding.

Tier 2 is never a terminal tier. It is a *reading*, not a verdict, and tier 3 always
adjudicates it, so :attr:`TranscribedPage.tier_reached` takes the values 0 (nothing
paid: a cache hit), 1 (the short-circuit) and 3 (the full path) and never 2.

Why tier 0 is consulted before the cache is
-------------------------------------------
:class:`~rmspec.domain.ports.persistence.OcrCache` says the lookup "saves the
recognizer and model calls, never the render", so the key must exist before any
recognizer runs. The key's recognizer component includes the index's ``provider_id``
when tier 0 was consulted, so tier 0 has to run before the key is built. It is free, so
that ordering costs nothing: render, consult the index, build the key, look up, and only
then start spending.

Two keys, and why that is not a second source of truth
------------------------------------------------------
:attr:`~rmspec.domain.models.OcrCacheKey.recognizers` is documented as the engines
"whose readings were folded in", and partial failure is tolerated, so the set that
*survived* is only known after tier 1 -- after the calls the cache exists to save. So
the lookup happens under the **bound** recognizer set and the write happens under the
**surviving** one. On a healthy run they are the same tuple and the next run hits. On a
run where one engine failed they differ, and that is the point: the degraded run's
output is stored where a healthy run will not find it, so a page read by one engine can
never be served to a caller who asked for two.

Both tuples are ``sorted()``. Sorting is what
:attr:`~rmspec.domain.ports.ocr.TextRecognizer.provider_id` asks for -- "binding order,
never completion order" -- because a fan-out that finishes in a different sequence would
otherwise digest to a different key for identical work and miss every row it wrote.

Where two model bindings land in a one-model key
-----------------------------------------------
:class:`~rmspec.domain.models.OcrCacheKey` has one ``model_fingerprint`` and this use
case has two bindings, so both identities are folded into the field whose documented
meaning each one satisfies:

* ``model_fingerprint`` is the **adjudicator's** fingerprint, which is literally what
  that field says it is: "the binding that merged the readings".
* The **reader's** fingerprint joins the ``recognizers`` tuple, prefixed for legibility.
  That field is already the list of every source whose reading was folded in -- the
  design extends it to the index's slug for the same reason -- and a tier-2 vision read
  is a source whose reading was folded in. Its identity is its fingerprint, which is the
  only identity a :class:`~rmspec.domain.ports.ocr.VisionLanguageModel` publishes.

Dropping either identity would be a row that looks valid and was produced by a
different model, which is the defect the whole key exists to make unrepresentable.

Why ``request_digest`` is a digest over both prompts
----------------------------------------------------
:attr:`~rmspec.domain.models.OcrCacheKey.request_digest` is documented as
``VisionRequest.digest``, and the tier-3 request cannot supply it: its prompt embeds the
tier-0, tier-1 and tier-2 texts, so it does not exist until after everything the cache
was meant to save has been paid for. The tier-2 read request *does* exist before any
payment -- its prompt is fixed policy and its only variable is the image -- so this
module folds that request's own digest together with the tier-3 system text, prompt
template and decoding into one length-framed digest. It is a superset of what the field
documents, never a subset: every input to either paid call changes it. Using the read
request's digest alone would be the cheaper lie, and it would serve stale rows after a
merge-prompt edit.

The prompts are hashed rather than versioned, which is
:class:`~rmspec.domain.ports.ocr.VisionRequest`'s own rule: "there is no
``prompt_revision`` field ... hashing the prompt bytes is strictly stronger than a
version integer someone has to remember to bump".

``page_hash`` versus ``raster_digest``: a rewrite no longer costs a re-transcription
-----------------------------------------------------------------------------------
Measured: the tablet rewrote one page from 18,813 to 24,534 bytes with the ink
unchanged, so ``page_hash`` moves while ``raster_digest`` stays equal, and a byte-level
rewrite used to cost a full re-transcription of a page nobody edited.

Neither remedy the design first floated would work.
:meth:`~rmspec.domain.ports.persistence.OcrCache.superseded` cannot express it: it
matches on **equal** ``page_hash`` -- "the page itself did not change and something
upstream of it did" -- which is precisely the component that moved, and it returns a key
rather than an artifact, "diagnostic only ... so it can never become a fallback". And
demoting ``page_hash`` is unavailable by construction:
:class:`~rmspec.domain.models.OcrCacheKey` is frozen with the field required, so a use
case could only "demote" it by writing something other than the page's hash into it,
which is a component that lies -- worse than the miss it avoids.

So the domain grew the member instead:
:meth:`~rmspec.domain.ports.persistence.OcrCache.equivalent_raster` matches every
component *except* ``page_hash`` -- the set
:attr:`~rmspec.domain.models.OcrCacheKey.raster_identity` names -- and returns the
artifact. :meth:`_stored` tries it after :meth:`~rmspec.domain.ports.persistence.OcrCache.get`
misses and before a single tier is paid for, and reports
:attr:`~rmspec.domain.errors.DegradationKind.CACHE_HIT_RASTER_EQUIVALENT` when it
answers, because the row served is not the row the key names. A truncated equivalent row
is read as a miss on exactly the same rule as a truncated exact one.

Nothing is written back under the new ``page_hash``. A rewrite therefore reports its
degradation on every run, which is the honest reading: the pixels have one transcription
and the bytes have had two names, and a caller that wants the second name to stop
appearing can prune the first.
:attr:`~rmspec.domain.errors.DegradationKind.CACHE_MISS_KEY_CHANGED` still reports the
misses ``superseded`` *can* see, and the two are consulted in that order -- an
equivalent raster is a hit and ends the lookup, a superseded key is a miss and only
explains one.

A short-circuited reading is cached too, and says so
----------------------------------------------------
It used not to be. :class:`~rmspec.domain.models.OcrArtifact` stored text, confidence,
truncation and a timestamp and no provenance, so a row could not say which tier produced
it and a reader of a hit had to assume one. Storing a short-circuited reading made that
assumption wrong: the row would be served as though the adjudicator named in its key had
merged it, when tier 0 and tier 1 agreed and no model ran at all. So the write happened
on the tier-3 path only -- and a page that short-circuits re-paid its recognizers on
every run, which is the cost tier 0 exists to avoid.

:class:`~rmspec.domain.models.OcrProvenance` removes the assumption. Both terminal paths
now write, each recording its own
:attr:`~rmspec.domain.models.OcrProvenance.tier_reached`,
:attr:`~rmspec.domain.models.OcrProvenance.short_circuited`, the sources that actually
reached the text, and -- for a short-circuit -- the measured agreement. The key is
unchanged and still names the adjudicator: it is the identity of the *lookup*, so it has
to digest the same way whether or not the merge turned out to be needed. The artifact is
the identity of the *work*. :meth:`_from_row` reads the second, not the first, so a hit
on a short-circuited row reports no merging model and the contributors that were really
folded in, and it reaches the caller on
:attr:`TranscribedPage.cached_provenance`.

One thing this does not fix, and it is stated rather than hidden:
:attr:`TranscribePagesRequest.agreement_threshold` is a request field and not a cache-key
component, so a run with a stricter threshold can hit a row that short-circuited under a
lenient one. Folding the threshold into the key would invalidate every stored row to
record a value only one of the four tiers reads. So the row carries the measured
agreement instead and the caller compares -- the same bargain
:attr:`~rmspec.domain.models.OcrArtifact.truncated` strikes, and for the same reason: no
key can protect a caller from a flag it declines to read.

Truncation is data on the way in and a refusal to write
-------------------------------------------------------
:attr:`~rmspec.domain.ports.ocr.StopReason.OUTPUT_LIMIT` never raises here. A truncated
body travels back on :attr:`TranscribedPage.truncated` and no row is written, which is
how the Bedrock contract's "never cache a non-stop response" is honoured without the
adapter raising. A stored row that *is* truncated -- written by something else, since
this module writes none -- is read as a miss and recomputed, which is
``OcrCache.get``'s own instruction: "the caller re-decides from the flag exactly as it
decided on the fresh path".

The degradation member this module needs and the domain does not have
--------------------------------------------------------------------
Tier 0 degrades and never fails: :class:`~rmspec.domain.errors.StoreUnavailableError`
and its :class:`~rmspec.domain.errors.StoreSchemaMismatchError` subclass become
:class:`~rmspec.domain.errors.Degradation` records and the run continues, because tier 0
is a free prior and a torn index should cost the prior and nothing else. The reader
raises those at all because the index is xochitl's live database and reMarkable's own
documentation says not to read files while it runs.

It is reported as
:attr:`~rmspec.domain.errors.DegradationKind.DEVICE_INDEX_UNAVAILABLE`, which exists
because this module and :mod:`rmspec.app.search` independently reached for
``CATALOG_ENTRY_SKIPPED`` and each said in prose that it did not fit. It does not: that
member means an enumeration omitted an entry it could not read, so a reader looks again
for that document, whereas this means a whole source was unavailable and nothing is
missing from the answer except a cross-check nobody was paying for. Adding a member is a
reviewed change to the domain and not something this module may decide, which is the
rule :mod:`rmspec.app.render` states for the same
situation.

The pipeline is a collaborator, not a copy
------------------------------------------
Decode, render and rasterize belong to :class:`~rmspec.app.render.RenderPages`, and a
second implementation of them is how two commands come to render different pixels for
one page and then disagree about a ``raster_digest``. So it arrives as
:class:`PageRenderPipeline`, declared here as a narrow structural expectation for the
reason :mod:`rmspec.app.diagrams` gives about its own: it describes a sibling *use case*
rather than a port, so :mod:`rmspec.domain.ports` is the wrong home for it, and naming
the concrete class would make this module depend on that class's construction.
"""

from __future__ import annotations

import difflib
import hashlib
import string
from collections.abc import Mapping
from operator import itemgetter
from typing import TYPE_CHECKING, Final, NamedTuple, Protocol, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from rmspec.app._degradations import DegradationLog
from rmspec.app.render import RenderPagesRequest
from rmspec.domain.errors import (
    AllRecognizersFailed,
    Degradation,
    DegradationKind,
    NoTextRecognized,
    RasterizationFailed,
    RecognitionFailed,
    StoreUnavailableError,
)
from rmspec.domain.models import (
    OcrArtifact,
    OcrCacheKey,
    OcrProvenance,
    PageText,
    TextProvenance,
)
from rmspec.domain.ports.ocr import Decoding, RasterImage, ReasoningEffort, VisionRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.app.render import RenderedPageArtifact, RenderPagesResult
    from rmspec.domain.ports.ocr import (
        HandwrittenTextIndex,
        Recognition,
        TextRecognizer,
        VisionLanguageModel,
    )
    from rmspec.domain.ports.persistence import OcrCache

__all__ = [
    "TranscribePages",
    "TranscribePagesRequest",
    "TranscribePagesResult",
    "TranscribedPage",
]

_DEFAULT_AGREEMENT: Final = 0.90
"""Default similarity at which tier 0 and a tier-1 reading are held to agree.

A request field rather than a constant, because no single threshold is right for every
hand: 0.90 is the design's measured starting point, not a law.
"""

_FRAME_BYTES: Final = 8
"""Width of the length prefix in :func:`_framed`, matching the domain's own framing."""

_PIPELINE: Final = "the render pipeline"
"""Backend named by :class:`~rmspec.domain.errors.RasterizationFailed` when no pixels came."""

_NO_PIXELS: Final = "pixels were requested for this page and the pipeline returned none"
"""Detail of that error. Unreachable through ``RenderPages``, which rasterizes whenever
``raster_dpi`` is set, and reachable through any other binding of the Protocol."""

_READER_PREFIX: Final = "vision-read:"
"""Prefix that makes the reader binding legible in a stored key's recognizer tuple."""

_PUNCTUATION: Final = str.maketrans("", "", string.punctuation)
"""Translation table that strips punctuation before agreement is measured."""

_READ_SYSTEM: Final = (
    "You transcribe handwriting from images of notebook pages. You reproduce what is "
    "written and never explain, summarise, or complete it."
)
"""System turn for the tier-2 read."""

_READ_PROMPT: Final = (
    "Transcribe every legible word of handwriting in this page image. Preserve line "
    "breaks and the reading order you see. Do not add commentary, headings, or notes "
    "about legibility. If the page holds no handwriting, answer with nothing at all."
)
"""User turn for the tier-2 read. Fixed policy, so the request digest is stable per page."""

_READ_DECODING: Final = Decoding(
    max_output_tokens=4096,
    temperature=0.0,
    reasoning=ReasoningEffort.NONE,
)
"""Decoding for the tier-2 read: deterministic, no latent reasoning, one page's budget.

Per-call rather than fixed at the binding, which is why
:class:`~rmspec.domain.ports.ocr.Decoding` is a request value: this and
:data:`_MERGE_DECODING` disagree about every field and are served by the same adapter.
"""

_MERGE_SYSTEM: Final = (
    "You adjudicate between several transcriptions of one handwritten page, using the "
    "page image as the deciding evidence. You answer with the final transcription and "
    "nothing else."
)
"""System turn for the tier-3 merge."""

_MERGE_INSTRUCTIONS: Final = (
    "Several readings of one handwritten page follow, then the page itself. Produce the "
    "single most accurate transcription of the page. Prefer what the image supports over "
    "what any reading claims, keep the reading order you see, and do not add commentary "
    "or notes about disagreement between the readings."
)
"""Instruction block of the tier-3 prompt, and one of the components of the key's
``request_digest``: editing this text invalidates every row it produced."""

_MERGE_DECODING: Final = Decoding(
    max_output_tokens=8192,
    temperature=0.0,
    reasoning=ReasoningEffort.HIGH,
)
"""Decoding for the tier-3 merge: a large budget with extended thinking, which is the
call site :class:`~rmspec.domain.ports.ocr.Decoding` names as wanting exactly that."""

_TIER0_LABEL: Final = "Reading from the device's own handwriting index:"
_TIER2_LABEL: Final = "Reading from a vision model:"

_PRIOR_UNBOUND: Final = "(the device's handwriting index was not available to this run)"
_PRIOR_UNAVAILABLE: Final = "(the device's handwriting index could not be read)"
_PRIOR_UNINDEXED: Final = "(the device has never indexed this page)"
_PRIOR_BLANK: Final = "(the device indexed this page and found no text)"
"""The four accounts tier 0 can give of itself.

Three of them are the three states
:meth:`~rmspec.domain.ports.ocr.HandwrittenTextIndex.lookup` distinguishes, and they
reach the adjudicator as different prompts rather than being collapsed into one absence.
"the device indexed this page and found nothing" is evidence about the page; "the device
has never seen this page" is evidence about the index.
"""

_INDEX_SKIPPED: Final = (
    "the device handwriting index is unusable, so tier 0's free prior was skipped for "
    "this page and it was read from pixels alone"
)
"""Detail of the degradation a torn index produces. See this module's docstring for why
its ``kind`` is the catalog member and which member the domain is missing."""


def _framed(tag: bytes, /, *parts: str) -> str:
    """Digest several strings so that no part can be mistaken for another.

    Parameters
    ----------
    tag
        Domain-separating prefix, so this digest cannot collide with another that
        happens to fold the same strings.
    *parts
        The components, each length-prefixed before it is folded in -- the rule
        :func:`rmspec.domain._digest.digest_of` follows, restated here rather than
        reaching into another package's private module.

    Returns
    -------
    str
        Lowercase hex SHA-256 over the framed stream.
    """
    digest = hashlib.sha256(tag)
    for part in parts:
        encoded = part.encode()
        digest.update(len(encoded).to_bytes(_FRAME_BYTES, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _normalised(text: str, /) -> str:
    """Reduce a reading to the form agreement is measured on.

    Casefolded, stripped of punctuation, and with every run of whitespace collapsed to
    one space, so that two engines differing only in capitalisation, commas or line
    wrapping are seen to agree.

    Parameters
    ----------
    text
        A reading, as some engine produced it.

    Returns
    -------
    str
        The comparable form.
    """
    return " ".join(text.casefold().translate(_PUNCTUATION).split())


def _agreement(left: str, right: str, /) -> float:
    """Measure how closely two readings agree.

    Parameters
    ----------
    left
        One reading.
    right
        The other.

    Returns
    -------
    float
        :meth:`difflib.SequenceMatcher.ratio` over the normalised forms, in ``0.0`` --
        ``1.0``. Measured rather than guessed at, which is the whole point: a
        short-circuit that fired on a hunch would skip two paid tiers for the wrong
        reason and no test could see it happen.
    """
    return difflib.SequenceMatcher(None, _normalised(left), _normalised(right)).ratio()


def _agreeing(
    prior: str,
    readings: Sequence[Recognition],
    /,
    *,
    threshold: float,
) -> tuple[float, Recognition] | None:
    """Find the tier-1 reading that agrees closely enough with tier 0 to stop here.

    Parameters
    ----------
    prior
        Tier 0's text. Blank means there is nothing to agree with, so no short-circuit
        is possible however good the tier-1 readings are.
    readings
        The tier-1 readings that survived. Blank ones are skipped: an empty string
        agrees with nothing, and ``SequenceMatcher`` would score it against an empty
        normalised prior as perfect agreement.
    threshold
        The similarity at or above which the two are held to agree.

    Returns
    -------
    tuple[float, Recognition] | None
        The measured agreement and the reading that achieved it, or ``None`` when none
        reaches the threshold. Ties go to the earliest binding, because :func:`max`
        returns the first maximal element and the recognizers are held in binding order.
        The score travels with the reading because it is written into
        :attr:`~rmspec.domain.models.OcrProvenance.agreement`: the threshold is not a
        cache-key component, so the measurement is the only thing a later run can compare
        its own threshold against.
    """
    if not prior.strip():
        return None
    scored = [
        (_agreement(prior, reading.text), reading) for reading in readings if reading.text.strip()
    ]
    agreed = [pair for pair in scored if pair[0] >= threshold]
    if not agreed:
        return None
    return max(agreed, key=itemgetter(0))


def _has_text(readings: Sequence[Recognition], /) -> bool:
    """Report whether any surviving reading holds text.

    Parameters
    ----------
    readings
        The tier-1 readings that survived.

    Returns
    -------
    bool
        ``True`` when at least one reading is not blank.
    """
    return any(reading.text.strip() for reading in readings)


def _mean_confidence(readings: Sequence[Recognition], /) -> float | None:
    """Average whatever confidence the surviving engines reported.

    Parameters
    ----------
    readings
        The tier-1 readings that survived.

    Returns
    -------
    float | None
        The mean of the reported values, or ``None`` when no engine reported one --
        which is the honest answer rather than a fabricated ``0.0`` that reads as a
        garbage reading or a ``1.0`` that reads as a perfect one, the distinction
        :attr:`~rmspec.domain.ports.ocr.Recognition.mean_confidence` was made optional
        for.
    """
    reported = [
        reading.mean_confidence for reading in readings if reading.mean_confidence is not None
    ]
    if not reported:
        return None
    return sum(reported) / len(reported)


def _pixels(artifact: RenderedPageArtifact, /) -> RasterImage:
    """Adopt a rendered page's pixels as the type a vision request accepts.

    Parameters
    ----------
    artifact
        One rendered page, whose ``raster`` is the export slice's twin of this slice's
        raster value object.

    Returns
    -------
    RasterImage
        The same pixels in this slice's type, via
        :meth:`~rmspec.domain.ports.ocr.RasterImage.from_raster`, which is the one
        sanctioned conversion between the twins.

    Raises
    ------
    RasterizationFailed
        The page came back with no pixels although
        :attr:`~rmspec.app.render.RenderPagesRequest.raster_dpi` is required to be set.
        Zero pixels is a failure rather than a success with an empty image, which is
        what that error already says about zero-length output.
    """
    if artifact.raster is None:
        raise RasterizationFailed(
            backend=_PIPELINE,
            detail=_NO_PIXELS,
            page_ref=artifact.page_ref,
        )
    return RasterImage.from_raster(artifact.raster)


def _read_request(raster: RasterImage, /) -> VisionRequest:
    """Build the tier-2 read, whose only variable is the image.

    Parameters
    ----------
    raster
        The page's pixels.

    Returns
    -------
    VisionRequest
        The read request. Its :meth:`~rmspec.domain.ports.ocr.VisionRequest.digest` is
        computable before any payment, which is what makes a cache lookup possible at
        all; see this module's docstring.
    """
    return VisionRequest(
        prompt=_READ_PROMPT,
        decoding=_READ_DECODING,
        system=_READ_SYSTEM,
        images=(raster,),
    )


def _identity_digest(read_digest: str, /) -> str:
    """Fold every input to both paid calls into the key's ``request_digest``.

    Parameters
    ----------
    read_digest
        :meth:`~rmspec.domain.ports.ocr.VisionRequest.digest` of the tier-2 read, which
        already covers its system text, its prompt, its decoding and the image.

    Returns
    -------
    str
        A digest that additionally covers the tier-3 system text, instruction block and
        decoding. A superset of what
        :attr:`~rmspec.domain.models.OcrCacheKey.request_digest` documents, never a
        subset -- see this module's docstring.
    """
    return _framed(
        b"rmspec.app.transcribe.identity.v1",
        read_digest,
        _MERGE_SYSTEM,
        _MERGE_INSTRUCTIONS,
        _MERGE_DECODING.canonical(),
    )


def _merge_request(
    raster: RasterImage,
    /,
    *,
    account: str,
    readings: Sequence[Recognition],
    read: str,
) -> VisionRequest:
    """Build the tier-3 merge over every reading and the page itself.

    Parameters
    ----------
    raster
        The page's pixels, which are the deciding evidence.
    account
        What tier 0 has to say: its text, or which of the three silences it is in.
    readings
        The tier-1 readings that survived, each labelled with the engine that produced
        it so the adjudicator can weigh them.
    read
        The tier-2 reading.

    Returns
    -------
    VisionRequest
        The merge request.
    """
    blocks = [_MERGE_INSTRUCTIONS, f"{_TIER0_LABEL}\n{account}"]
    blocks.extend(f"Reading from {reading.provider_id}:\n{reading.text}" for reading in readings)
    blocks.append(f"{_TIER2_LABEL}\n{read}")
    return VisionRequest(
        prompt="\n\n".join(blocks),
        decoding=_MERGE_DECODING,
        system=_MERGE_SYSTEM,
        images=(raster,),
    )


def _provenance(
    *,
    text: str,
    recognizers: tuple[str, ...],
    fingerprint: str | None,
    render_dpi: int,
    extracted_at: AwareDatetime,
) -> TextProvenance:
    """Describe how one page's text came to exist.

    Parameters
    ----------
    text
        The text itself, which decides whether a model may be named:
        :class:`~rmspec.domain.models.PageText` refuses blank text that claims a merging
        model, because "a merged reading with no text means the merge failed and the
        failure was written down as a success".
    recognizers
        The sources that contributed, in the order they were folded.
    fingerprint
        The adjudicator's fingerprint when a merge produced the text, else ``None``.
    render_dpi
        Resolution the page was rasterised at.
    extracted_at
        When the extraction ran.

    Returns
    -------
    TextProvenance
        The provenance, with no model named for blank text.
    """
    return TextProvenance(
        recognizers=recognizers,
        model_fingerprint=fingerprint if text.strip() else None,
        render_dpi=render_dpi,
        extracted_at=extracted_at,
    )


class _Prior(NamedTuple):
    """Tier 0's answer, reduced to what the later tiers need of it.

    Three fields rather than an ``IndexedHandwriting | None``, because the three states
    the port distinguishes are used in three different places: the text drives the
    short-circuit, the slug drives the cache key, and the account drives the merge
    prompt.
    """

    text: str
    """The device's reading, or ``""`` for every state that is not a reading."""

    slug: str | None
    """The index's ``provider_id`` when it was consulted and answered, else ``None``.

    ``None`` covers both "no index was bound" and "the index faulted", which are the two
    states in which this run had no free prior available to it at all. A run that was
    merely told "no row" *was* informed, and its key says so.
    """

    account: str
    """The tier-0 section of the merge prompt: the reading itself, or which of the four
    silences this is. Always populated, so the merge prompt needs no branch."""


class PageRenderPipeline(Protocol):
    """The decode-render-rasterize pipeline, at the width this use case needs it.

    A sibling use case (:class:`~rmspec.app.render.RenderPages`) owns this pipeline;
    this Protocol is the narrowest statement of what a transcription needs from it, so
    that no second copy of it exists here. Structural, so the one binding satisfies
    this without this module depending on how that class is constructed.

    Deliberately kept out of ``__all__``: ``_use_cases()`` in
    ``test_app_public_surface`` treats every exported non-model class as a use case and
    asserts keyword-only collaborators over its ``__init__``, which no ``Protocol`` can
    satisfy. Import it from this module.
    """

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        """Render, and rasterize, the requested pages of one document.

        Parameters
        ----------
        request
            The document, the page selection, and every input that changes a pixel.

        Returns
        -------
        RenderPagesResult
            One artifact per selected page, and the render digest they share.
        """
        ...


class TranscribePagesRequest(BaseModel, frozen=True, extra="forbid"):
    """One render request, one instant, and the one threshold that is policy.

    The render inputs are not restated field by field: they arrive as the
    :class:`~rmspec.app.render.RenderPagesRequest` this use case will hand to the
    pipeline, so the render identity folded into the cache key is provably the identity
    of the render that happened, rather than a second copy of eight fields that can
    drift from it.
    """

    render: RenderPagesRequest
    """The render to transcribe, whole. Its ``raster_dpi`` must be set; see below."""

    now: AwareDatetime
    """The instant this run records as its extraction time.

    A field rather than a clock read, because
    :attr:`~rmspec.domain.models.TextProvenance.extracted_at` and
    :attr:`~rmspec.domain.models.OcrArtifact.created_at` are required and the domain
    declares no clock port -- "Required, so no clock lives in the domain". One instant
    for the whole run, so every page of one command shares a timestamp.
    """

    agreement_threshold: float = Field(default=_DEFAULT_AGREEMENT, ge=0.0, le=1.0)
    """Similarity at or above which tier 0 and a tier-1 reading are held to agree.

    A field with a default rather than a constant, because one threshold cannot be right
    for every hand. Bounded by pydantic rather than validated in the body: a ratio
    outside ``0.0`` -- ``1.0`` is a threshold no measurement can meet or miss, and the
    domain names no error for it.
    """

    @model_validator(mode="after")
    def _check_pixels_were_asked_for(self) -> Self:
        """Refuse a transcription of a render that produces no pixels.

        Every tier below tier 0 reads pixels, and
        :attr:`~rmspec.domain.models.OcrCacheKey.raster_digest` is a required key
        component, so a request whose render is SVG-only describes work that cannot be
        done or keyed. The domain names no error for it, so the state is
        unconstructible instead -- convention 5's rule for a condition the domain
        deliberately declined to name.

        Returns
        -------
        TranscribePagesRequest
            The validated request.

        Raises
        ------
        ValueError
            If the render was not asked to rasterize.
        """
        if self.render.raster_dpi is None:
            msg = (
                "render.raster_dpi is None, so this render produces no pixels; every "
                "transcription tier reads pixels and the cache key requires their digest"
            )
            raise ValueError(msg)
        return self


class TranscribedPage(BaseModel, frozen=True, extra="forbid"):
    """One page's text, how it was produced, and what producing it cost.

    :attr:`tier_reached` and :attr:`short_circuited` exist because an agent wants to
    know whether it paid, and because a short-circuit rate is the only way to tell
    whether tier 0 is earning its keep.
    """

    page: PageText
    """The text, its identity, and its provenance, in the model the store persists.

    Carried as a :class:`~rmspec.domain.models.PageText` rather than as loose ``text``
    and ``provenance`` fields so that the edge which records it does not reassemble one
    from parts it could mismatch.
    """

    tier_reached: int = Field(ge=0, le=3)
    """The highest tier this run reached.

    ``0`` when nothing was paid for -- a cache hit -- ``1`` when tier 0 and tier 1
    agreed, and ``3`` for the full path. Never ``2``: tier 2 is a reading and tier 3
    always adjudicates it, so it is not a tier a run can stop at.
    """

    short_circuited: bool
    """Whether tier 0 and a tier-1 reading agreed closely enough to skip tiers 2 and 3."""

    cached: bool
    """Whether the text came from :class:`~rmspec.domain.ports.persistence.OcrCache`.

    Required, and not inferable from :attr:`tier_reached` alone by a reader who does not
    know that ``0`` is unreachable on a fresh page.
    """

    cached_provenance: OcrProvenance | None
    """The served row's own account of which tier produced it, or ``None``.

    ``None`` exactly when :attr:`cached` is ``False``. On a hit this is how a caller learns
    what :attr:`tier_reached` cannot tell it: that field is ``0`` for every hit, because it
    describes what *this* run paid, while this describes what the *row* cost when it was
    made. A row that short-circuited says so, names no merging model, and carries the
    agreement it was accepted at -- which is what a caller with a stricter
    :attr:`TranscribePagesRequest.agreement_threshold` compares against, since the threshold
    is not a cache-key component and no key can make it one cheaply.
    """

    truncated: bool
    """Whether either paid completion stopped at a limit rather than on its own terms.

    Data, never an exception, and the reason no row was written for this page. Always
    ``False`` for a cache hit, because a truncated row is read as a miss.
    """

    recognizer_failures: Mapping[str, str]
    """Why each tier-1 engine that failed did so, keyed by its ``provider_id``.

    Empty on the happy path and on a cache hit. Partial failure travels here rather than
    raising, because one engine's outage must not discard another's output; only a total
    one raises :class:`~rmspec.domain.errors.AllRecognizersFailed`.
    """

    mean_confidence: float | None
    """Mean of whatever confidence the surviving engines reported, or ``None``."""


class TranscribePagesResult(BaseModel, frozen=True, extra="forbid"):
    """Every page that was transcribed, and the render identity they share."""

    document_uuid: str = Field(min_length=1)
    """The document that was transcribed."""

    pages: tuple[TranscribedPage, ...]
    """The transcribed pages, in ascending document order."""

    render_digest: str = Field(min_length=1)
    """:meth:`~rmspec.domain.ports.render.RenderStyle.digest` for this whole call.

    Taken from the pipeline's own result rather than recomputed, so this value and the
    one folded into every cache key are one value.
    """

    degradations: tuple[Degradation, ...]
    """Every substitution this run made instead of failing: a torn tier-0 index, a cache
    row that exists for this page under different inputs, and a cache row reused because
    the page's bytes were rewritten while its pixels were not."""


class TranscribePages:
    """Transcribe the selected pages of one document through the four tiers.

    Six collaborators, every one of them keyword-only, and one of them --
    :class:`PageRenderPipeline` -- a sibling use case rather than a port, because the
    decode-render-rasterize pipeline has one owner and this is not it.

    Notes
    -----
    The two model bindings are separate collaborators because they are separate
    decisions: a per-page reader can be a cheap fast model while the adjudicator that
    reconciles four readings is not, and :class:`~rmspec.domain.ports.ocr.Decoding`
    exists as a per-call value precisely so one adapter class can serve both.
    """

    def __init__(
        self,
        *,
        pipeline: PageRenderPipeline,
        recognizers: Sequence[TextRecognizer],
        index: HandwrittenTextIndex | None,
        reader: VisionLanguageModel,
        adjudicator: VisionLanguageModel,
        cache: OcrCache,
    ) -> None:
        self._pipeline = pipeline
        self._recognizers = tuple(recognizers)
        self._index = index
        self._reader = reader
        self._adjudicator = adjudicator
        self._cache = cache

    def transcribe(self, request: TranscribePagesRequest, /) -> TranscribePagesResult:
        """Transcribe the requested pages of the requested document.

        Parameters
        ----------
        request
            The render to transcribe, the extraction instant, and the agreement
            threshold.

        Returns
        -------
        TranscribePagesResult
            One transcription per rendered page in ascending document order, the render
            digest they share, and every substitution the run made.

        Raises
        ------
        AllRecognizersFailed
            Every bound recognizer failed on one page, so there was no reading to merge.
            Partial failure never reaches here.
        NoTextRecognized
            Every recognizer succeeded, every reading was empty, and tier 0 had no text
            either. Raised before any model call, so a blank page cannot cost a token.
        RasterizationFailed
            A page came back from the pipeline with no pixels, or was raised by the
            pipeline's own rasterizer.
        InvalidSettingError
            Raised by the pipeline for a non-positive page cap.
        PageNotFound
            Raised by the pipeline for a selection naming a page the document lacks.
        UsageError
            Raised by the pipeline when the selection exceeds the page cap.
        MalformedDocument
            Raised by the pipeline for an unusable page identifier.
        DeviceDocumentNotFound
            Raised by the pipeline's bundle source.
        MalformedDeviceMetadata
            Raised by the pipeline's bundle source.
        DeviceTransferInterrupted
            Raised by the pipeline's bundle source.
        DeviceUnreachable
            Raised by the pipeline's bundle source.
        DeviceAuthFailed
            Raised by the pipeline's bundle source.
        DeviceProtocolError
            Raised by the pipeline's bundle source.
        CorruptPageData
            Raised by the pipeline's codec.
        UnsupportedPageFormat
            Raised by the pipeline's codec.
        UnsupportedPenType
            Raised by the pipeline's renderer.
        BackgroundUnreadable
            Raised by the pipeline's renderer.
        RecognitionFailed
            Never raised: a failing engine is recorded on
            :attr:`TranscribedPage.recognizer_failures` and the survivors are used.
        ModelUnavailable
            Raised by either model binding.
        ModelAccessDenied
            Raised by either model binding.
        ModelThrottled
            Raised by either model binding.
        ModelRejectedRequest
            Raised by either model binding.
        ModelResponseMalformed
            Raised by either model binding.
        StoreUnavailableError
            Never raised: a torn tier-0 index is a degradation, and
            :class:`~rmspec.domain.ports.persistence.OcrCache` is total by contract.
        """
        rendered = self._pipeline.render(request.render)
        log = DegradationLog()
        pages = tuple(
            self._one_page(
                artifact,
                request=request,
                document_uuid=rendered.document_uuid,
                render_digest=rendered.render_digest,
                log=log,
            )
            for artifact in rendered.pages
        )
        return TranscribePagesResult(
            document_uuid=rendered.document_uuid,
            pages=pages,
            render_digest=rendered.render_digest,
            degradations=log.frozen(),
        )

    def _one_page(
        self,
        artifact: RenderedPageArtifact,
        /,
        *,
        request: TranscribePagesRequest,
        document_uuid: str,
        render_digest: str,
        log: DegradationLog,
    ) -> TranscribedPage:
        """Take one rendered page through as many tiers as it needs.

        Parameters
        ----------
        artifact
            The rendered page: its pixels, its scene hash, and its position.
        request
            The whole request, for the instant and the agreement threshold.
        document_uuid
            The owning document, taken from the pipeline's own result so that this value
            and :attr:`TranscribePagesResult.document_uuid` are one value.
        render_digest
            The pipeline's render identity, one component of the cache key.
        log
            Where a torn index and a superseded cache row are recorded.

        Returns
        -------
        TranscribedPage
            The page's text and what producing it cost.

        Raises
        ------
        AllRecognizersFailed
            Every bound recognizer failed on this page.
        NoTextRecognized
            Nothing on this page holds text, checked before any model call.
        """
        raster = _pixels(artifact)
        prior = self._prior(artifact.page_ref, log=log)
        read_request = _read_request(raster)
        request_digest = _identity_digest(read_request.digest())
        bound = tuple(recognizer.provider_id for recognizer in self._recognizers)
        lookup = self._key(
            artifact,
            render_digest=render_digest,
            raster=raster,
            folded=self._folded(bound, prior=prior),
            request_digest=request_digest,
        )
        stored = self._stored(lookup, page_ref=artifact.page_ref, log=log)
        if stored is not None:
            return self._from_row(
                stored,
                artifact=artifact,
                raster=raster,
                key=lookup,
                document_uuid=document_uuid,
            )
        readings, failures = self._recognise(raster)
        if failures and not readings:
            raise AllRecognizersFailed(failures=failures)
        if not _has_text(readings) and not prior.text.strip():
            raise NoTextRecognized(
                page_ref=raster.page_ref,
                providers=tuple(reading.provider_id for reading in readings),
            )
        return self._decide(
            artifact,
            raster=raster,
            prior=prior,
            readings=readings,
            failures=failures,
            read_request=read_request,
            request=request,
            key=lookup,
            document_uuid=document_uuid,
        )

    def _decide(
        self,
        artifact: RenderedPageArtifact,
        /,
        *,
        raster: RasterImage,
        prior: _Prior,
        readings: tuple[Recognition, ...],
        failures: Mapping[str, str],
        read_request: VisionRequest,
        request: TranscribePagesRequest,
        key: OcrCacheKey,
        document_uuid: str,
    ) -> TranscribedPage:
        """Short-circuit on agreement, or pay for tiers 2 and 3.

        Parameters
        ----------
        artifact
            The rendered page.
        raster
            Its pixels.
        prior
            Tier 0's answer.
        readings
            The tier-1 readings that survived.
        failures
            Why each failing engine failed.
        read_request
            The tier-2 request, already built for the key's digest and sent as-is.
        request
            The whole request, for the instant and the threshold.
        key
            The lookup key, whose recognizer tuple is rewritten for the write when an
            engine failed. Both terminal paths write, so both receive it.
        document_uuid
            The owning document.

        Returns
        -------
        TranscribedPage
            The page's text and what producing it cost.
        """
        agreed = _agreeing(prior.text, readings, threshold=request.agreement_threshold)
        if agreed is not None:
            agreement, agreeing = agreed
            return self._short_circuit(
                artifact,
                raster=raster,
                prior=prior,
                agreeing=agreeing,
                agreement=agreement,
                readings=readings,
                failures=failures,
                now=request.now,
                key=key,
                document_uuid=document_uuid,
            )
        return self._merged(
            artifact,
            raster=raster,
            prior=prior,
            readings=readings,
            failures=failures,
            read_request=read_request,
            now=request.now,
            key=key,
            document_uuid=document_uuid,
        )

    def _short_circuit(
        self,
        artifact: RenderedPageArtifact,
        /,
        *,
        raster: RasterImage,
        prior: _Prior,
        agreeing: Recognition,
        agreement: float,
        readings: tuple[Recognition, ...],
        failures: Mapping[str, str],
        now: AwareDatetime,
        key: OcrCacheKey,
        document_uuid: str,
    ) -> TranscribedPage:
        """Accept tier 0's reading, which agreed with a tier-1 engine, and stop.

        Tier 0's text wins on agreement rather than the recognizer's. The tablet's
        recognizer read the *strokes*; every other tier read pixels a rasterizer
        produced after throwing the stroke data away, so when the two agree the reading
        with more information behind it is preferred -- and it is the free one.
        Provenance lists both.

        The row is written, under the same key a merge would have used. That is not a row
        that lies: the key is the identity of the lookup and names the adjudicator this
        run was configured with, while
        :class:`~rmspec.domain.models.OcrProvenance` on the artifact says which tier
        actually produced the text and how closely the two readings agreed. Without the
        write, a page that short-circuits re-pays its recognizers on every run.

        Parameters
        ----------
        artifact
            The rendered page.
        raster
            Its pixels, for the resolution the provenance records.
        prior
            Tier 0's answer, whose text is the answer.
        agreeing
            The tier-1 reading that agreed.
        agreement
            How closely it agreed, recorded on the row because the threshold is not a
            cache-key component.
        readings
            Every surviving reading, for the confidence average and the write key.
        failures
            Why each failing engine failed.
        now
            The extraction instant.
        key
            The lookup key, rewritten here over the surviving engines for the write.
        document_uuid
            The owning document.

        Returns
        -------
        TranscribedPage
            The page, at tier 1, with a row written and its provenance recorded.
        """
        both = (prior.slug, agreeing.provider_id)
        contributors = tuple(slug for slug in both if slug is not None)
        confidence = _mean_confidence(readings)
        self._write(
            key,
            folded=self._folded(tuple(reading.provider_id for reading in readings), prior=prior),
            text=prior.text,
            confidence=confidence,
            now=now,
            provenance=OcrProvenance(
                tier_reached=1,
                short_circuited=True,
                contributors=contributors,
                agreement=agreement,
            ),
        )
        return TranscribedPage(
            page=PageText(
                doc_uuid=document_uuid,
                page_uuid=artifact.page_ref,
                page_index=artifact.page_index,
                text=prior.text,
                provenance=_provenance(
                    text=prior.text,
                    recognizers=contributors,
                    fingerprint=None,
                    render_dpi=raster.render_dpi,
                    extracted_at=now,
                ),
            ),
            tier_reached=1,
            short_circuited=True,
            cached=False,
            cached_provenance=None,
            truncated=False,
            recognizer_failures=failures,
            mean_confidence=confidence,
        )

    def _merged(
        self,
        artifact: RenderedPageArtifact,
        /,
        *,
        raster: RasterImage,
        prior: _Prior,
        readings: tuple[Recognition, ...],
        failures: Mapping[str, str],
        read_request: VisionRequest,
        now: AwareDatetime,
        key: OcrCacheKey,
        document_uuid: str,
    ) -> TranscribedPage:
        """Pay for a vision read, then for a merge over every reading and the image.

        Parameters
        ----------
        artifact
            The rendered page.
        raster
            Its pixels.
        prior
            Tier 0's answer, which reaches the adjudicator as text or as one of three
            accounts of its own silence.
        readings
            The tier-1 readings that survived.
        failures
            Why each failing engine failed.
        read_request
            The tier-2 request, built once so that the key digest and the call agree.
        now
            The extraction instant.
        key
            The lookup key, rewritten here over the surviving engines for the write.
        document_uuid
            The owning document.

        Returns
        -------
        TranscribedPage
            The page, at tier 3, with a row written only when both completions stopped
            on the model's own terms.
        """
        read = self._reader.complete(read_request)
        merge = self._adjudicator.complete(
            _merge_request(
                raster,
                account=prior.account,
                readings=readings,
                read=read.text,
            )
        )
        confidence = _mean_confidence(readings)
        truncated = not (read.is_complete and merge.is_complete)
        if not truncated:
            self._write(
                key,
                folded=self._folded(
                    tuple(reading.provider_id for reading in readings), prior=prior
                ),
                text=merge.text,
                confidence=confidence,
                now=now,
                provenance=OcrProvenance(
                    tier_reached=3,
                    short_circuited=False,
                    contributors=self._fold_order(prior, readings),
                ),
            )
        return TranscribedPage(
            page=PageText(
                doc_uuid=document_uuid,
                page_uuid=artifact.page_ref,
                page_index=artifact.page_index,
                text=merge.text,
                provenance=_provenance(
                    text=merge.text,
                    recognizers=self._fold_order(prior, readings),
                    fingerprint=self._adjudicator.fingerprint,
                    render_dpi=raster.render_dpi,
                    extracted_at=now,
                ),
            ),
            tier_reached=3,
            short_circuited=False,
            cached=False,
            cached_provenance=None,
            truncated=truncated,
            recognizer_failures=failures,
            mean_confidence=confidence,
        )

    def _from_row(
        self,
        stored: OcrArtifact,
        /,
        *,
        artifact: RenderedPageArtifact,
        raster: RasterImage,
        key: OcrCacheKey,
        document_uuid: str,
    ) -> TranscribedPage:
        """Rehydrate a cached transcription without paying for a single tier.

        Parameters
        ----------
        stored
            The cached artifact. Never truncated: :meth:`_stored` reads a truncated row
            as a miss.
        artifact
            The rendered page, for its identity.
        raster
            Its pixels, for the resolution the provenance records.
        key
            The key that hit. It names the sources the *lookup* was built from, which is
            what a row written before
            :class:`~rmspec.domain.models.OcrProvenance` existed has instead of a
            recorded contributor list.
        document_uuid
            The owning document.

        Returns
        -------
        TranscribedPage
            The page, at tier 0, with the row's own creation time as its extraction
            time rather than this run's instant, and the row's own provenance on
            :attr:`TranscribedPage.cached_provenance`.

        Notes
        -----
        The merging model is named from the row rather than from this run's binding: a
        short-circuited row had no merge, so claiming one would be the exact
        misattribution that used to make such a row unwritable. The contributor list comes
        from the row when it has one and falls back to the key's sorted tuple when it does
        not, which is all an older build ever recorded and precisely what this method used
        to report unconditionally.
        """
        recorded = stored.provenance
        return TranscribedPage(
            page=PageText(
                doc_uuid=document_uuid,
                page_uuid=artifact.page_ref,
                page_index=artifact.page_index,
                text=stored.text,
                provenance=_provenance(
                    text=stored.text,
                    recognizers=recorded.contributors or key.recognizers,
                    fingerprint=None if recorded.short_circuited else key.model_fingerprint,
                    render_dpi=raster.render_dpi,
                    extracted_at=stored.created_at,
                ),
            ),
            tier_reached=0,
            short_circuited=False,
            cached=True,
            cached_provenance=recorded,
            truncated=False,
            recognizer_failures={},
            mean_confidence=stored.mean_confidence,
        )

    def _prior(self, page_ref: str, /, *, log: DegradationLog) -> _Prior:
        """Consult the device's own reading of one page, for free, and never fail.

        Parameters
        ----------
        page_ref
            The page to look up, which matches the ``.rm`` filename exactly and so needs
            no translation table.
        log
            Where a torn index is recorded.

        Returns
        -------
        _Prior
            The three states the port distinguishes, plus the unbound and faulted cases,
            reduced to text, slug and account.
        """
        if self._index is None:
            return _Prior(text="", slug=None, account=_PRIOR_UNBOUND)
        try:
            row = self._index.lookup(page_ref)
        except StoreUnavailableError as error:
            log.record(
                Degradation(
                    kind=DegradationKind.DEVICE_INDEX_UNAVAILABLE,
                    subject=page_ref,
                    detail=f"{_INDEX_SKIPPED} ({error})",
                )
            )
            return _Prior(text="", slug=None, account=_PRIOR_UNAVAILABLE)
        slug = self._index.provider_id
        if row is None:
            return _Prior(text="", slug=slug, account=_PRIOR_UNINDEXED)
        if not row.text.strip():
            return _Prior(text="", slug=slug, account=_PRIOR_BLANK)
        return _Prior(text=row.text, slug=slug, account=row.text)

    def _recognise(
        self,
        raster: RasterImage,
        /,
    ) -> tuple[tuple[Recognition, ...], dict[str, str]]:
        """Fan the bound recognizers out over one page and keep both outcomes.

        Parameters
        ----------
        raster
            The page's pixels, passed to every engine as one value object.

        Returns
        -------
        tuple[tuple[Recognition, ...], dict[str, str]]
            The readings that came back, in binding order, and why each engine that
            failed did so, keyed by the binding's own ``provider_id`` rather than by the
            one the error reports, so two engines cannot collide on one key.
        """
        readings: list[Recognition] = []
        failures: dict[str, str] = {}
        for recognizer in self._recognizers:
            try:
                readings.append(recognizer.recognize(raster))
            except RecognitionFailed as error:
                failures[recognizer.provider_id] = error.detail
        return tuple(readings), failures

    def _folded(self, slugs: tuple[str, ...], /, *, prior: _Prior) -> tuple[str, ...]:
        """Compose the key's recognizer component out of every source that was folded in.

        Parameters
        ----------
        slugs
            The recognizer slugs: the bound set for a lookup, the surviving set for a
            write.
        prior
            Tier 0's answer, whose slug joins the tuple exactly when the index was
            consulted and answered -- so a run that had the free prior cannot reuse a
            row written by one that did not.

        Returns
        -------
        tuple[str, ...]
            Sorted, which is what the port asks for: binding order, never completion
            order, because a fan-out that finishes in a different sequence would
            otherwise digest to a different key for identical work.
        """
        folded = [*slugs, f"{_READER_PREFIX}{self._reader.fingerprint}"]
        if prior.slug is not None:
            folded.append(prior.slug)
        return tuple(sorted(folded))

    def _fold_order(
        self,
        prior: _Prior,
        readings: tuple[Recognition, ...],
        /,
    ) -> tuple[str, ...]:
        """List the sources that contributed to a merged reading, in fold order.

        Parameters
        ----------
        prior
            Tier 0's answer, listed first when it had text to contribute.
        readings
            The tier-1 readings that survived, in binding order.

        Returns
        -------
        tuple[str, ...]
            What :attr:`~rmspec.domain.models.TextProvenance.recognizers` documents:
            fold order rather than the key's sorted order, because a reader of a search
            result wants to know how the text was built.
        """
        order: list[str] = []
        if prior.slug is not None and prior.text.strip():
            order.append(prior.slug)
        order.extend(reading.provider_id for reading in readings)
        order.append(f"{_READER_PREFIX}{self._reader.fingerprint}")
        return tuple(order)

    def _key(
        self,
        artifact: RenderedPageArtifact,
        /,
        *,
        render_digest: str,
        raster: RasterImage,
        folded: tuple[str, ...],
        request_digest: str,
    ) -> OcrCacheKey:
        """Build the complete key for one page under one set of folded sources.

        Parameters
        ----------
        artifact
            The rendered page, whose ``page_hash`` is the source component.
        render_digest
            The pipeline's render identity.
        raster
            The pixels, whose own digest carries the resolution.
        folded
            Every source whose reading was folded in, sorted.
        request_digest
            The digest over both paid calls' fixed inputs.

        Returns
        -------
        OcrCacheKey
            The key, with the adjudicator's fingerprint as the model component.
        """
        return OcrCacheKey(
            page_hash=artifact.page_hash,
            render_digest=render_digest,
            raster_digest=raster.digest(),
            recognizers=folded,
            model_fingerprint=self._adjudicator.fingerprint,
            request_digest=request_digest,
        )

    def _stored(
        self,
        key: OcrCacheKey,
        /,
        *,
        page_ref: str,
        log: DegradationLog,
    ) -> OcrArtifact | None:
        """Look one page up in the cache, and explain a miss when the store can.

        Parameters
        ----------
        key
            The lookup key.
        page_ref
            The page, named by a degradation.
        log
            Where a superseded row and a reused equivalent raster are recorded.

        Returns
        -------
        OcrArtifact | None
            The cached transcription, or ``None`` on a miss. A truncated row is a miss:
            a half-transcribed page is a wrong page, and the port's own instruction is
            that the caller "re-decides from the flag exactly as it decided on the fresh
            path". Both lookups apply that rule, because a truncated row is no more
            servable when the pixels matched than when the whole key did.
        """
        stored = self._cache.get(key)
        if stored is not None and not stored.truncated:
            return stored
        equivalent = self._cache.equivalent_raster(key)
        if equivalent is not None and not equivalent.truncated:
            log.record(
                Degradation(
                    kind=DegradationKind.CACHE_HIT_RASTER_EQUIVALENT,
                    subject=page_ref,
                    detail=(
                        "this page's stored bytes were rewritten and its pixels were not, "
                        "so the reading cached under the older bytes was reused instead of "
                        "being recomputed"
                    ),
                    substituted=equivalent.created_at.isoformat(),
                )
            )
            return equivalent
        other = self._cache.superseded(key)
        if other is not None:
            log.record(
                Degradation(
                    kind=DegradationKind.CACHE_MISS_KEY_CHANGED,
                    subject=page_ref,
                    detail=(
                        f"a cached transcription of this page exists under key "
                        f"{other.digest}, produced under different inputs, so it was "
                        f"recomputed"
                    ),
                )
            )
        return None

    def _write(
        self,
        key: OcrCacheKey,
        /,
        *,
        folded: tuple[str, ...],
        text: str,
        confidence: float | None,
        now: AwareDatetime,
        provenance: OcrProvenance,
    ) -> None:
        """Store one terminal reading under the surviving sources, with its provenance.

        Parameters
        ----------
        key
            The lookup key, reused for every component except the recognizer tuple.
        folded
            The surviving sources, which differ from the lookup key's bound set exactly
            when an engine failed -- so a degraded run's output is stored where a
            healthy run will not find it.
        text
            The accepted transcription: tier 0's on a short-circuit, the merge's otherwise.
        confidence
            Mean recognizer confidence, or ``None``.
        now
            The creation instant.
        provenance
            Which tier produced ``text`` and out of what. The reason both terminal paths
            may write at all: without it a short-circuited row would be indistinguishable
            from a merged one and would be read as adjudicated by a model that never ran.
        """
        self._cache.put(
            OcrCacheKey(
                page_hash=key.page_hash,
                render_digest=key.render_digest,
                raster_digest=key.raster_digest,
                recognizers=folded,
                model_fingerprint=key.model_fingerprint,
                request_digest=key.request_digest,
            ),
            OcrArtifact(
                text=text,
                mean_confidence=confidence,
                truncated=False,
                created_at=now,
                provenance=provenance,
            ),
        )
