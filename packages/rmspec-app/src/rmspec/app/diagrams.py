"""Decide whether a page holds a diagram and, when it does, extract its Mermaid.

One seam, and it is :class:`~rmspec.domain.ports.ocr.VisionLanguageModel`. There is no
Mermaid adapter behind this use case and there must not be one:
:mod:`rmspec.domain.ports.ocr` records that three were proposed -- ``MermaidSyntaxChecker``,
``MermaidLinter``, ``MermaidValidator`` -- and all three were dropped, because Mermaid
validity is a Node toolchain (``mmdc`` plus headless Chromium) that merely happened to live
in the legacy ``ocr/diagram.py`` file, and no Python extra can supply an npm binary. A
dependency whose absence cannot be expressed as the "missing package, install this extra"
composition failure this architecture requires is a dependency this layer must not acquire.

One idea from those proposals is kept, and it lives on the value rather than on a port:
*"I did not actually check this" must be a representable value rather than a keyword-prefix
match masquerading as a real parse.* :class:`MermaidCheck` is that value. This module can
only ever produce :attr:`MermaidCheck.UNCHECKED`, for Mermaid it obtained, and
:attr:`MermaidCheck.NOT_APPLICABLE`, for a page that produced none. The two verdict members
exist so that a layer which really does run ``mmdc`` -- the CLI, which is the layer allowed
to import adapters -- can re-stamp the field without this vocabulary having to grow, and so
that a reader of a result never has to guess whether "valid" meant "validated".

The first token of a Mermaid document *is* its diagram type, and that is the only thing this
module reads out of a body: :attr:`~rmspec.domain.models.DiagramArtifact.diagram_kind` is
``"flowchart"`` because ``flowchart TD`` begins with ``flowchart``. That is a fact about
where Mermaid puts its type, not a verdict on the code -- which is the distinction the
dropped ports' keyword-prefix match blurred.

Two legacy defects are requirements here
----------------------------------------
**An unannotated page of a PDF-backed document must not crash.** ``diagram_cmd.py:144-149``
omits the ``None`` guard its ``ocr`` sibling has, so it hands a ``None`` path to the parser
and raises ``TypeError``; the ``hash_file(None)`` failure one line earlier is swallowed by
the bare ``except Exception`` at ``:232``, so the cache silently disables itself first. Both
halves are structurally impossible here. A page with no ink is decided before anything is
rendered, and reported twice -- as a ``PAGE_NOT_ANNOTATED`` degradation and as a
:attr:`DiagramSkipReason.NO_INK` entry that keeps the page in the result. And there is no
run-wide hash step left to fail: fingerprints are asked for one page at a time, only for a
page that is about to be worked on, and
:meth:`~rmspec.domain.ports.formats.DocumentRepository.page_fingerprint` answers
:data:`~rmspec.domain.ports.formats.ABSENT_ARTIFACT_FINGERPRINT` for an absent artifact
rather than raising, so one unannotated page cannot disable caching for its neighbours.
**62 of 92 real ``.rm`` files in the reference corpus are zero bytes** -- empty stubs for
unannotated pages -- so this is the common case, not an edge one. An empty stub arrives here
as a page whose ``content`` is ``None`` or whose content is blank, and both are "no ink";
nothing in this layer stats a file, because nothing in this layer may touch one.

**The conditional cache reuse is gone, and that is the correct reading of it.** Legacy
reused a row only when its Mermaid was non-empty *or* its classification was text -- it
retried a "this is a diagram" verdict carrying no code rather than serving it. The condition
was right about its data and is now unrepresentable:
:class:`~rmspec.domain.models.DiagramArtifact`'s validator refuses a diagram-bearing
``content_kind`` with no Mermaid *at construction*, so no such row can be stored by anyone,
and a runtime ``if`` re-checking it would be a branch no cache could take. The guard moved
from a query predicate to a construction-time invariant, which is strictly stronger: a
predicate on the read leaves the bad row in the table for the next reader to serve. What is
kept deliberately is the *effect* the predicate had -- an extraction that produced no usable
code is retried rather than served -- and this module reaches it from two directions. An
answer with no readable Mermaid yields no artifact, so nothing is stored. And a completion
that did not finish is never stored either, which is the precondition
:class:`~rmspec.domain.ports.persistence.DiagramCache` states on itself, because
``DiagramArtifact`` has no ``truncated`` field to carry that fact into a hit.

The collaborator this module does not own
----------------------------------------
Decode, render and rasterize belong to one owner, and it is not this use case. A second
implementation of that pipeline is how two callers come to render different pixels for the
same page and then disagree about a cache key whose ``raster_digest`` is those pixels. So
the pipeline arrives as a collaborator, :class:`PageRasterizer`, and everything this module
needs from it comes back together: the pixels and the ``render_digest`` that produced them.
Both are cache-key components, and a caller that computed the digest itself from a style,
screen and palette it was handed separately would be the second source of truth for what
was rendered.

:class:`PageRasterizer` and :class:`RasterizedPage` are declared here, as narrow
Protocol-shaped expectations, rather than imported: they describe a sibling *use case*
(``RenderPages``), not a port, so :mod:`rmspec.domain.ports` is the wrong home for them, and
naming the concrete class would make this module depend on its construction. They are
structural, so one binding satisfies this expectation and
:class:`~rmspec.app.page_annotations.PageRasterizer`'s wider one at the same time -- there is one
pipeline, declared twice at the narrowest shape each caller needs.

Why two public names are missing from ``__all__``
-------------------------------------------------
:class:`MermaidCheck` and :class:`DiagramSkipReason` are public names of this module and are
deliberately not re-exported. ``test_app_public_surface`` sweeps every non-model class in
``rmspec.app.__all__`` as a use case and asserts keyword-only collaborators over its
``__init__``, which a ``StrEnum`` cannot satisfy; listing either enum would therefore fail
three of that file's gates for a bookkeeping reason. Import them from this module, the way
``from rmspec.app import selection`` is already sanctioned.

``Decoding`` is fixed here, not taken from the caller
-----------------------------------------------------
:data:`_DECODING` is a small budget at temperature zero with no latent reasoning -- the
opposite of a transcription merge, which is exactly why
:class:`~rmspec.domain.ports.ocr.Decoding` is a per-call value rather than a constructor
argument of the model binding. It is this use case's policy and not a request field: a
caller free to raise the temperature of a diagram extraction would be free to make the same
page answer differently on two runs, and the only record of that would be a cache key that
happens to differ. The prompt is policy crossing the port as data for the same reason, and
:meth:`~rmspec.domain.ports.ocr.VisionRequest.digest` hashes its bytes, so editing either
constant below mechanically invalidates every row they produced.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from rmspec.app._degradations import DegradationLog
from rmspec.app.selection import PageSelection
from rmspec.domain.errors import Degradation, DegradationKind
from rmspec.domain.models import DiagramArtifact, DiagramCacheKey, DocumentId, PageContentKind
from rmspec.domain.ports.ocr import (
    Decoding,
    RasterImage,
    ReasoningEffort,
    StopReason,
    VisionRequest,
)

if TYPE_CHECKING:
    from datetime import datetime

    from rmspec.domain.models import DocumentSummary, Page, PageId
    from rmspec.domain.ports.formats import DocumentRepository
    from rmspec.domain.ports.ocr import PageRasterLike, VisionCompletion, VisionLanguageModel
    from rmspec.domain.ports.persistence import DiagramCache

__all__ = [
    "ExtractDiagrams",
    "ExtractDiagramsRequest",
    "ExtractDiagramsResult",
    "PageDiagram",
]

_SYSTEM: Final = (
    "You read one page of handwritten notes, rendered as an image, and decide whether it "
    "holds a diagram. You answer in the fixed shape you are given and never describe the "
    "page in prose."
)
"""System turn. Separate from the prompt so the two are separately framed in the digest."""

_PROMPT: Final = """\
Classify this page and, if it holds a diagram, transcribe the diagram as Mermaid.

Answer in exactly this shape and nothing else.

Line one is one word, upper case:
TEXT if the page holds only prose, notes or lists;
DIAGRAM if it holds only a diagram;
MIXED if it holds both.

For DIAGRAM or MIXED, follow line one with one fenced Mermaid block, opened with
```mermaid and closed with ```. Begin the block with the Mermaid diagram type, so that
a flowchart starts with the word flowchart and a sequence diagram starts with the word
sequenceDiagram.

For TEXT, write nothing at all after line one.
"""
"""User turn. Its bytes are hashed into every cache key, so editing it invalidates rows."""

_DECODING: Final = Decoding(
    max_output_tokens=1024,
    temperature=0.0,
    reasoning=ReasoningEffort.NONE,
)
"""A small budget at temperature zero: a page's diagram is short and must not vary."""

_FENCE: Final = "```"
"""The closing fence, and the prefix of the opening one."""

_MERMAID_FENCE: Final = "```mermaid"
"""The opening fence the answer contract requires, which is also how the body is found."""

_VERDICTS: Final = {
    "TEXT": PageContentKind.TEXT,
    "DIAGRAM": PageContentKind.DIAGRAM,
    "MIXED": PageContentKind.MIXED,
}
"""The three answer tokens and the content kind each one names. Nothing else is accepted."""


class MermaidCheck(StrEnum):
    """Whether anything verified the Mermaid a page produced.

    The one idea worth keeping from the three dropped Mermaid ports, as a value rather than
    as a seam. Its whole purpose is that "I did not actually check this" is sayable: a
    reader of :attr:`PageDiagram.check` can tell Mermaid nobody validated from Mermaid a
    validator approved, and no keyword-prefix match anywhere can dress the first up as the
    second.

    :mod:`rmspec.app.diagrams` produces exactly two of the four members. The other two are
    for whichever layer can actually run a Mermaid toolchain -- the CLI -- to re-stamp;
    they are unreachable from here by construction, because this layer has no validator and
    the ports module records why it may never acquire one.
    """

    NOT_APPLICABLE = "not_applicable"
    """The page produced no Mermaid, so there is nothing to check."""

    UNCHECKED = "unchecked"
    """Mermaid was produced and nothing has validated it. What this layer always reports."""

    VALID = "valid"
    """A Mermaid toolchain parsed it. Only a layer that ran one may write this."""

    INVALID = "invalid"
    """A Mermaid toolchain rejected it. Only a layer that ran one may write this."""


class DiagramSkipReason(StrEnum):
    """Why a page yielded no diagram artifact, as a branchable value rather than a log line.

    Four members, closed, and every one of them is a state the legacy command either
    crashed on or served silently. None of them is a
    :class:`~rmspec.domain.errors.DegradationKind`: that enum is closed and has no member
    meaning "the model's answer could not be read", and adding one is a reviewed change to
    the domain rather than something this module may decide. ``NO_INK`` is the exception
    and it *does* have a member -- ``PAGE_NOT_ANNOTATED`` -- so it is reported both ways.
    """

    NO_INK = "no_ink"
    """The page carries no annotation artifact, or carries one that draws nothing.

    The common case for a PDF-backed document, and the one the legacy command crashed on.
    Decided before any render, so an unannotated page costs neither pixels nor a model call.
    """

    UNREADABLE_VERDICT = "unreadable_verdict"
    """The answer's first non-blank line was not one of the three verdict tokens.

    A refusal lands here too: a refused completion is a successful call whose body does not
    answer the question, which is data rather than an exception.
    """

    DIAGRAM_WITHOUT_CODE = "diagram_without_code"
    """The answer claimed a diagram and carried no Mermaid body.

    The state legacy's conditional cache read was protecting against, now unstorable:
    :class:`~rmspec.domain.models.DiagramArtifact` refuses the pairing at construction, so
    the page yields nothing and the next run retries it.
    """

    TEXT_WITH_CODE = "text_with_code"
    """The answer claimed text and carried a Mermaid body, so it contradicts itself.

    Not resolved in either direction on purpose. Trusting the verdict throws away code the
    model produced; trusting the body overrides the only classification available. An
    answer that cannot be believed is reported as one.
    """


class RasterizedPage(Protocol):
    """One page's pixels and the render identity that produced them, as read-only members.

    Read-only properties, which is what makes them covariant: a concrete result model whose
    ``raster`` field is either slice's raster twin satisfies this, exactly as
    :class:`~rmspec.domain.ports.ocr.PageRasterLike` is satisfied by both twins today.
    """

    @property
    def raster(self) -> PageRasterLike:
        """The pixels, in whichever raster twin the producer holds.

        Structural rather than nominal so that the export slice's
        :class:`~rmspec.domain.ports.export.RasterImage` -- what a rasterizer returns -- is
        accepted without this module choosing which twin the pipeline uses.
        :meth:`~rmspec.domain.ports.ocr.RasterImage.from_raster` is the one sanctioned
        conversion into the type a :class:`~rmspec.domain.ports.ocr.VisionRequest` accepts.
        """
        ...

    @property
    def render_digest(self) -> str:
        """``RenderStyle.digest`` for the render that produced :attr:`raster`.

        Comes back with the pixels rather than being recomputed here, because a caller that
        derived it from a style, screen and palette handed to it separately would be a
        second source of truth for what was rendered.
        """
        ...


class PageRasterizer(Protocol):
    """The decode-render-rasterize pipeline, at the width this use case needs it.

    A sibling use case (``RenderPages``) owns this pipeline; this Protocol is the narrowest
    statement of what a diagram extraction needs from it, so that no second copy of it
    exists here. Structural, so the one binding also satisfies
    :class:`rmspec.app.page_annotations.PageRasterizer`, which needs the same call plus an
    underlay.
    """

    def raster_for(self, doc_id: DocumentId, page_id: PageId, /) -> RasterizedPage:
        """Render and rasterize one page's ink.

        Parameters
        ----------
        doc_id
            The document the page belongs to.
        page_id
            The page to rasterize.

        Returns
        -------
        RasterizedPage
            The pixels and the render digest that produced them.
        """
        ...


class PageDiagram(BaseModel, frozen=True, extra="forbid"):
    """What this pass made of one page, including the pages it made nothing of.

    Every selected page appears in the result, in selection order, so a caller can address
    "page 7" and mean it. A page that yielded nothing carries :attr:`skipped` saying why,
    which follows the rule :class:`~rmspec.domain.models.Page` states about its own absent
    content: a value with nothing in it has to say why it has nothing in it.
    """

    page_index: int = Field(ge=0)
    """Zero-based position of the page in the document, as the selection resolved it."""

    page_ref: str = Field(min_length=1)
    """The page's own identity, for a message or a filename. Never parsed."""

    artifact: DiagramArtifact | None
    """What was extracted, or ``None`` when nothing was. No default: a construction site
    that could omit it could report a page as text by forgetting to say otherwise."""

    skipped: DiagramSkipReason | None
    """Why there is no artifact, or ``None`` when there is one."""

    check: MermaidCheck
    """Whether anything validated this page's Mermaid. Always
    :attr:`MermaidCheck.UNCHECKED` or :attr:`MermaidCheck.NOT_APPLICABLE` from this layer --
    see :class:`MermaidCheck`."""

    stop_reason: StopReason | None
    """Why the model stopped, verbatim, or ``None`` when no model was called -- a cache hit
    or a page with no ink. Carried rather than interpreted: the domain declares four
    reasons and this layer folds none of them together."""

    served_from_cache: bool
    """Whether this entry came from a stored row rather than from a paid call."""

    @property
    def truncated(self) -> bool:
        """Whether generation stopped at the output limit rather than on its own terms.

        Returns
        -------
        bool
            ``True`` only for :attr:`~rmspec.domain.ports.ocr.StopReason.OUTPUT_LIMIT`.
            Derived rather than stored, because a second boolean beside
            :attr:`stop_reason` is a second answer to one question -- and a stored
            ``truncated`` would have to lie about a refusal, which stops for a reason that
            is not a limit.
        """
        return self.stop_reason is StopReason.OUTPUT_LIMIT

    @model_validator(mode="after")
    def _check_absence_is_explained(self) -> Self:
        """Reject an entry that carries no artifact and no reason for carrying none.

        Returns
        -------
        PageDiagram
            The validated model.

        Raises
        ------
        ValueError
            If both :attr:`artifact` and :attr:`skipped` are ``None``, which reads as a
            page that produced nothing for no reason.
        """
        if self.artifact is None and self.skipped is None:
            msg = (
                f"page {self.page_ref} has no diagram artifact and no reason explaining it; "
                f"name a {DiagramSkipReason.__name__}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_the_check_matches_the_mermaid(self) -> Self:
        """Reject a verdict about Mermaid that is not there, or silence about Mermaid that is.

        Returns
        -------
        PageDiagram
            The validated model.

        Raises
        ------
        ValueError
            If Mermaid is present and :attr:`check` is
            :attr:`MermaidCheck.NOT_APPLICABLE`, or Mermaid is absent and :attr:`check`
            claims anything else. Either way the field would be describing a body that
            does not exist, which is the confusion it was introduced to remove.
        """
        has_mermaid = self.artifact is not None and self.artifact.mermaid is not None
        if has_mermaid and self.check is MermaidCheck.NOT_APPLICABLE:
            msg = f"page {self.page_ref} carries mermaid, so its check cannot be not_applicable"
            raise ValueError(msg)
        if not has_mermaid and self.check is not MermaidCheck.NOT_APPLICABLE:
            msg = (
                f"page {self.page_ref} carries no mermaid, so its check must be "
                f"{MermaidCheck.NOT_APPLICABLE.value!r}, not {self.check.value!r}"
            )
            raise ValueError(msg)
        return self


class ExtractDiagramsRequest(BaseModel, frozen=True, extra="forbid"):
    """Which pages of which document to examine, how many are affordable, and when.

    ``now`` is a field rather than a clock read because the domain requires
    :attr:`~rmspec.domain.models.DiagramArtifact.created_at` and declares no clock port --
    "Required, so no clock lives in the domain". One instant supplied for the whole run
    means every artifact a run produces shares a timestamp, and no test has to freeze
    anything.
    """

    doc_id: DocumentId
    """The document to examine. Already resolved -- see :mod:`rmspec.app.resolve`."""

    pages: PageSelection
    """Which pages, 0-based. The CLI converts from the 1-based numbers a human types."""

    max_pages: int
    """The most pages this run may examine. Unconstrained here on purpose: a non-positive
    cap is an :class:`~rmspec.domain.errors.InvalidSettingError` raised by
    :meth:`~rmspec.app.selection.PageSelection.resolve_against`, and a pydantic constraint
    here would answer a condition the domain has already named."""

    now: AwareDatetime
    """The instant to stamp on every artifact this run produces."""


class ExtractDiagramsResult(BaseModel, frozen=True, extra="forbid"):
    """One entry per examined page, and everything this pass substituted instead of failing."""

    pages: tuple[PageDiagram, ...]
    """One entry per selected page, in ascending page order, unannotated pages included."""

    degradations: tuple[Degradation, ...]
    """``PAGE_NOT_ANNOTATED`` per page with no ink, and ``CACHE_MISS_KEY_CHANGED`` per page
    whose stored row was produced under different inputs."""


def _verdict(text: str, /) -> PageContentKind | None:
    """Read the content kind the answer's first non-blank line names.

    Parameters
    ----------
    text
        The completion body, verbatim.

    Returns
    -------
    PageContentKind | None
        The kind that token names, or ``None`` when the first non-blank line is not one of
        the three tokens and when there is no non-blank line at all.
    """
    for line in text.splitlines():
        token = line.strip()
        if token:
            return _VERDICTS.get(token.upper())
    return None


def _mermaid(text: str, /) -> str | None:
    """Read the body of the answer's fenced Mermaid block.

    Parameters
    ----------
    text
        The completion body, verbatim.

    Returns
    -------
    str | None
        The block's contents, stripped, or ``None`` when the answer opens no Mermaid fence.
        A body whose closing fence never arrived -- what a completion cut short at the
        output limit looks like -- is returned as far as it got, because a partial diagram
        is data the caller may accept and is exactly what
        :class:`~rmspec.domain.ports.ocr.StopReason` keeps as data rather than an error.
    """
    opened = text.find(_MERMAID_FENCE)
    if opened < 0:
        return None
    body = text[opened + len(_MERMAID_FENCE) :]
    closed = body.find(_FENCE)
    if closed < 0:
        return body.strip()
    return body[:closed].strip()


def _read_answer(
    completion: VisionCompletion, /, *, now: datetime
) -> DiagramArtifact | DiagramSkipReason:
    """Turn one completion body into an artifact, or into the reason there is none.

    A total function over the answer contract, and the only place this module looks at model
    output. It reads two things: which of three tokens the first line is, and what sits
    inside the Mermaid fence. It reads nothing else out of the body and asserts nothing
    about the body's validity -- that is :class:`MermaidCheck`'s job, and the answer is
    always "nobody checked".

    Parameters
    ----------
    completion
        The model's answer.
    now
        The instant to stamp on an artifact.

    Returns
    -------
    DiagramArtifact | DiagramSkipReason
        The artifact when the answer is self-consistent, otherwise the reason it is not.
        A union rather than an optional, so the caller cannot forget to say why.
    """
    verdict = _verdict(completion.text)
    body = _mermaid(completion.text)
    if verdict is None:
        return DiagramSkipReason.UNREADABLE_VERDICT
    if verdict is PageContentKind.TEXT:
        if body:
            return DiagramSkipReason.TEXT_WITH_CODE
        return DiagramArtifact(content_kind=verdict, mermaid=None, created_at=now)
    if not body:
        return DiagramSkipReason.DIAGRAM_WITHOUT_CODE
    return DiagramArtifact(
        content_kind=verdict,
        mermaid=body,
        diagram_kind=body.split(maxsplit=1)[0],
        created_at=now,
    )


def _check_for(artifact: DiagramArtifact, /) -> MermaidCheck:
    """Report what this layer knows about an artifact's Mermaid, which is nothing.

    Parameters
    ----------
    artifact
        The artifact just produced or just read back.

    Returns
    -------
    MermaidCheck
        :attr:`MermaidCheck.UNCHECKED` when there is Mermaid, because this layer has no
        validator and the ports module records why it may never have one, and
        :attr:`MermaidCheck.NOT_APPLICABLE` when there is none to check.
    """
    if artifact.mermaid is None:
        return MermaidCheck.NOT_APPLICABLE
    return MermaidCheck.UNCHECKED


def _no_ink_detail(page: Page, /) -> str:
    """Say what "no ink" meant for one page, without naming a file.

    Parameters
    ----------
    page
        The page that yielded nothing.

    Returns
    -------
    str
        Whether the store held no scene artifact at all -- the zero-byte stub, and the
        majority of the reference corpus -- or held one that draws nothing. The two are
        different facts about the tablet, and a reader of a warning wants to know which.
    """
    if page.content is None:
        return "the store holds no scene artifact for this page, so there is nothing to read"
    return "the page's scene artifact decodes to no visible ink"


class ExtractDiagrams:
    """Examine selected pages of one document and extract the Mermaid they hold.

    Four collaborators, every one of them a Protocol, and one of them --
    :class:`PageRasterizer` -- a sibling use case rather than a port, because the pipeline
    it names must have exactly one implementation.

    Notes
    -----
    The order of operations is fixed by the cache key rather than by taste. A key contains
    ``raster_digest`` and ``request_digest``, so the page must be rendered and the request
    built before a lookup exists -- which is
    :class:`~rmspec.domain.ports.persistence.DiagramCache`'s own statement that "this cache
    saves the model call, never the render". A page with no ink is the one path that skips
    all of it::

        result = extractor.extract(
            ExtractDiagramsRequest(doc_id=doc, pages=PageSelection.all(), max_pages=20, now=t)
        )
        mermaid = [p.artifact.mermaid for p in result.pages if p.artifact is not None]
    """

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        rasterizer: PageRasterizer,
        model: VisionLanguageModel,
        cache: DiagramCache,
    ) -> None:
        self._repository = repository
        self._rasterizer = rasterizer
        self._model = model
        self._cache = cache

    def extract(self, request: ExtractDiagramsRequest, /) -> ExtractDiagramsResult:
        """Examine every selected page and report what each one holds.

        Parameters
        ----------
        request
            The document, the pages, the cap, and the instant to stamp.

        Returns
        -------
        ExtractDiagramsResult
            One entry per selected page in ascending order -- including pages with no ink,
            which are reported rather than omitted -- and every substitution made.

        Raises
        ------
        InvalidSettingError
            ``max_pages`` is not positive, raised by
            :meth:`~rmspec.app.selection.PageSelection.resolve_against` before any page is
            read.
        PageNotFound
            An explicitly selected index is not a page of this document.
        UsageError
            The selection is larger than ``max_pages``. Raised before the first render, so
            an oversized request costs nothing.
        DocumentNotFound
            Raised by the repository.
        MalformedDocument
            Raised by the repository.
        DocumentStoreUnavailable
            Raised by the repository.
        UnsupportedPenType
            Raised by the render pipeline for a stroke whose pen it does not implement.
        RasterizationFailed
            Raised by the render pipeline.
        ModelUnavailable
            Raised by the model binding, and never degraded here: a page that could not be
            examined must not be reported as a page holding no diagram.
        ModelAccessDenied
            Raised by the model binding.
        ModelThrottled
            Raised by the model binding.
        ModelRejectedRequest
            Raised by the model binding.
        ModelResponseMalformed
            Raised by the model binding. Distinct from an answer this module cannot read,
            which is :attr:`DiagramSkipReason.UNREADABLE_VERDICT` and not an error.
        """
        summary = self._repository.summary(request.doc_id)
        indices = request.pages.resolve_against(
            summary.page_count,
            document_uuid=request.doc_id.uuid,
            max_pages=request.max_pages,
        )
        log = DegradationLog()
        pages = tuple(self._examine(request, summary, index, log=log) for index in indices)
        return ExtractDiagramsResult(pages=pages, degradations=log.frozen())

    def _examine(
        self,
        request: ExtractDiagramsRequest,
        summary: DocumentSummary,
        index: int,
        *,
        log: DegradationLog,
    ) -> PageDiagram:
        """Examine one page: skip it, serve it from the cache, or pay for it.

        Parameters
        ----------
        request
            The whole request, for the document identity and the stamp.
        summary
            The document's summary, which is where the page identity at ``index`` comes
            from.
        index
            The 0-based page position to examine.
        log
            The run's degradation log.

        Returns
        -------
        PageDiagram
            This page's entry, whichever of the three paths produced it.
        """
        page_id = summary.pages[index]
        page = self._repository.load_page(request.doc_id, page_id)
        content = page.content
        if content is None or content.is_blank:
            log.record(
                Degradation(
                    kind=DegradationKind.PAGE_NOT_ANNOTATED,
                    subject=page.ref,
                    detail=_no_ink_detail(page),
                )
            )
            return PageDiagram(
                page_index=index,
                page_ref=page.ref,
                artifact=None,
                skipped=DiagramSkipReason.NO_INK,
                check=MermaidCheck.NOT_APPLICABLE,
                stop_reason=None,
                served_from_cache=False,
            )
        rendered = self._rasterizer.raster_for(request.doc_id, page_id)
        image = RasterImage.from_raster(rendered.raster)
        vision = VisionRequest(
            prompt=_PROMPT,
            decoding=_DECODING,
            system=_SYSTEM,
            images=(image,),
        )
        key = DiagramCacheKey(
            page_hash=self._repository.page_fingerprint(request.doc_id, page_id),
            render_digest=rendered.render_digest,
            raster_digest=image.digest(),
            model_fingerprint=self._model.fingerprint,
            request_digest=vision.digest(),
        )
        stored = self._cache.get(key)
        if stored is not None:
            return PageDiagram(
                page_index=index,
                page_ref=page.ref,
                artifact=stored,
                skipped=None,
                check=_check_for(stored),
                stop_reason=None,
                served_from_cache=True,
            )
        superseded = self._cache.superseded(key)
        if superseded is not None:
            log.record(
                Degradation(
                    kind=DegradationKind.CACHE_MISS_KEY_CHANGED,
                    subject=page.ref,
                    detail=(
                        f"a stored row for this page was produced under other inputs "
                        f"({superseded.digest}), so the page was examined again"
                    ),
                    substituted=key.digest,
                )
            )
        completion = self._model.complete(vision)
        read = _read_answer(completion, now=request.now)
        if isinstance(read, DiagramSkipReason):
            return PageDiagram(
                page_index=index,
                page_ref=page.ref,
                artifact=None,
                skipped=read,
                check=MermaidCheck.NOT_APPLICABLE,
                stop_reason=completion.stop_reason,
                served_from_cache=False,
            )
        if completion.is_complete:
            self._cache.put(key, read)
        return PageDiagram(
            page_index=index,
            page_ref=page.ref,
            artifact=read,
            skipped=None,
            check=_check_for(read),
            stop_reason=completion.stop_reason,
            served_from_cache=False,
        )
