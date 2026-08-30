"""Match a term against transcribed page text, saying of every hit where it came from.

Two sources, and they are not equally trustworthy
-------------------------------------------------
**The mirror's own rows** are ours. Each :class:`~rmspec.domain.models.PageText` was written
by a transcription this tool ran, is keyed by ``(doc_uuid, page_uuid)`` so a re-run replaces
it rather than accumulating, is dropped when its page departs, and carries
:class:`~rmspec.domain.models.TextProvenance` -- which recognizers produced it, which model
merged them, at what render DPI, and when. A hit here can explain itself all the way down.

**The device's handwriting index** is a free prior. The tablet already ran a recognizer over
every page it indexed, so consulting it costs nothing and can turn up a page this tool has
never paid to read. But it *lags the tablet*: measured, a page written since the last index
build has no row at all. So a miss in the index is not evidence of absence, and a match found
only there is a lead rather than a fact -- it carries no provenance, because the index does
not record how it read anything, only which generation of itself it was.

Attribution is therefore not decoration, it is the feature. A merged list that cannot say
where a line came from is a list a user cannot act on: "the phrase is on page 4" invites a
different next step depending on whether we transcribed page 4 last Tuesday at 226 DPI or
the tablet's own recognizer glanced at it at some unknown time. Every
:class:`TextMatch` names its :class:`MatchSource`, and a validator makes the two
attributions unforgeable -- a mirror match must carry provenance and no index generation, a
device match the reverse.

**Corroboration is reported, not collapsed.** When both sources match the same page, that is
two independent recognizers agreeing, which is worth more than either alone -- so both
readings are returned, each flagged :attr:`TextMatch.corroborated`. Deduplicating to one row
would throw away the second reading, which is the evidence, and would leave the survivor
looking like an ordinary single-source hit. Their texts may differ in wording; that is the
comparison a reader wants, not a defect to hide.

Three refusals
--------------
**1. The OCR cache is not a search index and is not consulted here.** It takes no
constructor argument, which is the enforceable form of that sentence. The legacy tree
searched by globbing ``.ocr.txt`` sidecars keyed on filename and never invalidated them, so
an edited page served its stale transcription to every subsequent search, forever.
:class:`~rmspec.domain.ports.persistence.OcrCache` is keyed by a digest that folds every
input affecting the answer -- page hash, render digest, raster digest, request digest, model
fingerprint -- precisely so it *cannot* be enumerated by content: it offers ``get``, ``put``
and ``superseded``, all exact-key, and no listing. Extracted text lives on the page in
:class:`~rmspec.domain.ports.persistence.DocumentSyncStore` for exactly this reason, and
that model's own comment says it: "A cache is an exact-key lookup and must never double as a
browse."

**2. No index is invented in this layer.** Matching is a case-insensitive substring test over
rows the store hands back -- the same test
:class:`~rmspec.app.resolve.ResolveDocument` uses on document names, so the two commands
cannot disagree about what "contains" means. There is no inverted index, no tokenizer, no
stemming, no ranking model, and results come back in store order rather than by relevance,
because a relevance score computed here would be a ranking model nobody could tune or
explain. This is honest at today's scale -- a mirror is one local SQLite file and a few
thousand pages -- and it does not pretend to be more.

If real search is wanted, **that is a persistence-layer change, not a bigger loop here.**
Concretely: an FTS5 table over ``page_text`` maintained inside ``record_page_text`` so it
cannot drift from the rows it indexes, plus one new port method returning matched pages, with
its ranking and its tokenizer named in the port's contract and pinned by the port's own
contract test. Half of that built here -- a tokenizer in the app layer over rows fetched in
full -- would be slower than the substring scan it replaced and would put a ranking rule
where no contract test can see it.

**3. The device index cannot be enumerated, and that is a feature.**
:class:`~rmspec.domain.ports.ocr.HandwrittenTextIndex` offers ``lookup`` by page ref and
nothing else, so this use case can only ask about pages it already knows. Page refs come from
the mirror's recorded pages -- ``Page.ref`` is the page uuid, which is what the index keys on,
so the two need no translation table. The consequence worth stating: **search covers the
pages the mirror has recorded, and no others.** A page on the tablet that was never pulled is
invisible here even if the tablet has indexed it, because nothing in this layer can name it.

What a miss means, and how the two kinds are told apart
------------------------------------------------------
"No rows matched" and "nothing has been transcribed yet" are different answers with different
next actions, and answering both with an empty list is the defect. :attr:`SearchTextResult.outcome`
names which happened, from the two ports between them:

* :attr:`SearchOutcome.NOTHING_SYNCED` -- the scope holds no recorded pages at all. Next
  action: pull. Nothing has been *seen*, let alone read.
* :attr:`SearchOutcome.NOTHING_TRANSCRIBED` -- pages are recorded and not one of them has
  text from either source. Next action: run ``ocr``. This is the answer a user who has never
  transcribed anything deserves, instead of being told their words are not in their notes.
* :attr:`SearchOutcome.NO_MATCH` -- text was searched and none of it contained the term. Next
  action: try another term. This is the only outcome that says anything about the term.
* :attr:`SearchOutcome.MATCHED` -- at least one hit.

Presence decides, not content: a ``PageText`` row whose text is empty means "we read this
page and it held nothing", and an ``IndexedHandwriting`` row whose text is empty means the
tablet indexed the page and found nothing. Both are transcriptions that happened, so both
move a scope out of ``NOTHING_TRANSCRIBED``. Only a page with no row from either source is
untranscribed.

On the ``NOTHING_TRANSCRIBED`` path only, the audit log is consulted for evidence that a
transcription was ever attempted, reported as
:attr:`SearchTextResult.recent_ocr_attempt`. It is deliberately three-valued and deliberately
weak: ``None`` means the question did not arise, ``False`` means no ``OCR`` operation appears
in the last :data:`_AUDIT_PROBE_LIMIT` recorded operations, and neither ``False`` nor ``True``
is proof, because ``recent`` is bounded and the log may be pruned. It is the difference
between "you have never run this" and "you ran it and it produced nothing", which is worth a
sentence to a confused user and is not worth a lie. This is the first use case that *reads*
the audit log rather than appending to it -- searching is a read, and
:class:`~rmspec.domain.models.SyncOperation` rules reads out of the history.

One source degrades, and it is the free one
-------------------------------------------
``StoreUnavailableError`` and ``StoreSchemaMismatchError`` from the device index become a
:class:`~rmspec.domain.errors.Degradation` and the search finishes on the mirror's rows
alone. The index reader raises them because it is reading xochitl's live database -- a torn
read of a file the tablet is still writing can otherwise answer with another page's
handwriting and no error at all, which is why the reader integrity-checks and refuses. A free
prior that cannot be read costs the caller nothing but the prior, so failing the whole search
over it would be trading an answer for nothing.

The first fault disables the index for the rest of the run and records exactly one
degradation: a torn database faults on every page, and four hundred identical degradations
would bury the ones that mean something. ``StoreSchemaMismatchError`` is a subclass of
``StoreUnavailableError``, so one ``except`` clause is not an omission.

The mirror and the audit log do **not** degrade. Their failures propagate, because they are
not free priors -- they are the answer. That asymmetry is the whole rule: a source degrades
when losing it costs a bonus, and propagates when losing it costs the result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final, Self

from pydantic import BaseModel, Field, model_validator

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import (
    Degradation,
    DegradationKind,
    DocumentNotFound,
    StoreUnavailableError,
    UsageError,
)
from rmspec.domain.models import SyncOperation, TextProvenance

if TYPE_CHECKING:
    from rmspec.domain.models import PageText, SyncedDocument, SyncedPage
    from rmspec.domain.ports.ocr import HandwrittenTextIndex, IndexedHandwriting
    from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog

__all__ = [
    "SearchText",
    "SearchTextRequest",
    "SearchTextResult",
    "TextMatch",
]

_MIRROR_STORE: Final = "local mirror"
"""Store identity used when reporting that a scoped search named an untracked document."""

_AUDIT_PROBE_LIMIT: Final = 50
"""How far back to look for evidence that a transcription was ever attempted.

Only ever read on the ``NOTHING_TRANSCRIBED`` path, and only to turn "your notes do not
contain that" into "you have not transcribed anything yet". Deliberately shallow: the
question is whether OCR has been run at all recently, and a deeper read would cost more to
answer a question that is already only evidence.
"""


class MatchSource(StrEnum):
    """Which source produced one match.

    Closed, and public at this module rather than re-exported: the CLI branches on it to
    label a hit, and a third source would be a third member here rather than a boolean
    somewhere.
    """

    MIRROR = "mirror"
    """A :class:`~rmspec.domain.models.PageText` row this tool wrote, with full provenance."""

    DEVICE_INDEX = "device_index"
    """The tablet's own handwriting index: free, unprovenanced, and behind the tablet."""


class SearchOutcome(StrEnum):
    """What a search found, distinguishing the kinds of nothing.

    Closed, because each member implies a different next action -- pull, transcribe, or try
    another term -- and a caller that cannot tell them apart gives the same unhelpful advice
    to three different users.
    """

    MATCHED = "matched"
    """At least one page matched."""

    NO_MATCH = "no_match"
    """Text was searched and none of it contained the term. The only outcome about the term."""

    NOTHING_TRANSCRIBED = "nothing_transcribed"
    """Pages are recorded and none has text from either source. Run ``ocr``."""

    NOTHING_SYNCED = "nothing_synced"
    """The scope holds no recorded pages at all. Pull first."""


class TextMatch(BaseModel, frozen=True, extra="forbid"):
    """One page whose text contained the term, and the provenance of that text.

    The attribution fields are constrained against :attr:`source` by a validator, so a match
    that claims the mirror's authority without the mirror's provenance is unconstructible
    rather than merely unlikely.
    """

    doc_uuid: str = Field(min_length=1)
    """Identifier of the document the page belongs to."""

    doc_name: str
    """The document's name as the mirror recorded it. Empty is legal on the tablet."""

    page_uuid: str = Field(min_length=1)
    """The page's own identifier, which is also the ref the device index is keyed by."""

    page_index: int = Field(ge=0)
    """Zero-based position of the page in its document."""

    source: MatchSource
    """Which source produced this reading."""

    text: str
    """The reading that matched, in full.

    Not a snippet: windowing text around the term is a display decision, and the CLI is the
    only layer that knows how wide the terminal is.
    """

    provenance: TextProvenance | None
    """How the text was produced, or ``None`` for a device-index reading.

    Set exactly when :attr:`source` is :attr:`MatchSource.MIRROR`. The index records nothing
    about how it read a page, and inventing a provenance for it would make the field lie in
    the one place a reader consults to decide how much to trust a hit.
    """

    index_generation: int | None
    """The device index's generation counter, or ``None`` for a mirror reading.

    Set exactly when :attr:`source` is :attr:`MatchSource.DEVICE_INDEX`. One value for the
    whole index database, so two readings carrying different generations came from two
    different snapshots of an index that lags the tablet.
    """

    corroborated: bool
    """Whether the other source also matched this page.

    ``True`` on *both* rows of a corroborated page, so either row alone says so. Two matches
    for one page are two independent readings agreeing, not a duplicate: they are returned
    rather than collapsed because the second reading is the corroboration.
    """

    @model_validator(mode="after")
    def _check_the_attribution_matches_the_source(self) -> Self:
        """Reject a match whose evidence does not belong to the source it names.

        Returns
        -------
        TextMatch
            The validated model.

        Raises
        ------
        ValueError
            If a mirror match carries no provenance or a device match carries one, or if the
            index generation is present on anything but a device match.
        """
        if (self.provenance is not None) != (self.source is MatchSource.MIRROR):
            msg = (
                f"a {self.source.value!r} match must carry "
                f"{'provenance' if self.source is MatchSource.MIRROR else 'no provenance'}; "
                f"the mirror knows how its text was produced and the device index does not"
            )
            raise ValueError(msg)
        if (self.index_generation is not None) != (self.source is MatchSource.DEVICE_INDEX):
            wanted = "an index generation" if self.source is MatchSource.DEVICE_INDEX else "none"
            msg = (
                f"a {self.source.value!r} match must carry {wanted}; only the device index "
                f"has a generation, and only it lags the tablet"
            )
            raise ValueError(msg)
        return self


class SearchTextRequest(BaseModel, frozen=True, extra="forbid"):
    """One term, and optionally one document to look in."""

    query: str
    """The term to look for, matched case-insensitively as a substring.

    An unconstrained ``str`` whose blankness is a :class:`~rmspec.domain.errors.UsageError`
    rather than a schema constraint, for the reason
    :attr:`~rmspec.app.resolve.ResolveDocumentRequest.query` is: the domain names that
    condition, so the domain's error is raised for it. Surrounding whitespace is not part of
    a term and is stripped before matching.
    """

    doc_uuid: str | None = None
    """Restrict the search to one document, or ``None`` to search every recorded document.

    The store's own two entry points, not a query language: it offers a per-document read
    and a whole-mirror read, and this is which of them to use. A uuid the mirror does not
    track is :class:`~rmspec.domain.errors.DocumentNotFound` rather than an empty result,
    because naming an absent document is a different mistake from finding nothing in a
    present one.
    """


class SearchTextResult(BaseModel, frozen=True, extra="forbid"):
    """Every match, attributed, plus what an absence of matches actually means.

    No field has a default, so a construction site cannot omit the outcome that makes an
    empty :attr:`matches` interpretable.
    """

    outcome: SearchOutcome
    """Which of the four things happened. The field that makes an empty result actionable."""

    query: str
    """The term as it was matched, after whitespace was stripped.

    Echoed because the result may be forwarded past the caller that built the request, and
    because a reader comparing hits against the term should see the term that was used.
    """

    matches: tuple[TextMatch, ...]
    """Every matching reading, in a total order.

    Documents in the store's contract order -- case-folded name, then uuid -- pages by index
    then uuid, and within one page the mirror's reading before the device index's. Ours
    first because it is the one with provenance; total so that two runs over one mirror
    cannot disagree.
    """

    pages_searched: int = Field(ge=0)
    """How many recorded pages were examined.

    What makes :attr:`SearchOutcome.NO_MATCH` concrete: "none of 412 pages" is an answer,
    where an empty list is a shrug. Counts pages, not readings, so a page consulted in both
    sources counts once.
    """

    recent_ocr_attempt: bool | None
    """Whether an ``OCR`` operation appears in recent history. Evidence, never proof.

    ``None`` when the question did not arise -- any outcome but
    :attr:`SearchOutcome.NOTHING_TRANSCRIBED`. Otherwise whether the last
    :data:`_AUDIT_PROBE_LIMIT` recorded operations include one, which distinguishes "you
    have never transcribed anything" from "you transcribed and it found nothing". The log is
    bounded and prunable, so ``False`` is the absence of evidence rather than evidence of
    absence.
    """

    degradations: tuple[Degradation, ...]
    """Every substitution this search made instead of failing.

    At most one: ``DEVICE_INDEX_UNAVAILABLE`` when the device index could not be read, after
    which it is not consulted again. The mirror and the audit log do not degrade.
    """


class _DeviceReadings:
    """One search's use of the device index: consult it until it faults, then stop.

    A small object rather than a threaded boolean, so the "disable on first fault, record
    exactly one degradation" rule lives in one place and the search loop reads as a loop.
    Private, holds no state beyond this call, and never reaches a constructor argument.
    """

    __slots__ = ("_available", "_index", "_log")

    def __init__(self, index: HandwrittenTextIndex, log: DegradationLog) -> None:
        self._index = index
        self._log = log
        self._available = True

    def of(self, page_ref: str, /) -> IndexedHandwriting | None:
        """Return the index's reading of one page, or ``None`` when there is none to have.

        Parameters
        ----------
        page_ref
            The page's ref, which is its uuid -- what both the index and ``Page.ref`` key on.

        Returns
        -------
        IndexedHandwriting | None
            The row, or ``None`` when the index has no row for this page, when a previous
            lookup faulted, or when this lookup faulted. All three are "no free prior for
            this page"; only the last two are reported, and only once.
        """
        if not self._available:
            return None
        try:
            return self._index.lookup(page_ref)
        except StoreUnavailableError as failure:
            self._available = False
            self._log.record(
                Degradation(
                    kind=DegradationKind.DEVICE_INDEX_UNAVAILABLE,
                    subject=self._index.provider_id,
                    detail=(
                        f"the device's handwriting index could not be read, so it was not "
                        f"consulted for any further page and this search covers only "
                        f"transcriptions this tool produced: {failure.detail}"
                    ),
                )
            )
            return None


def _contains(term: str, text: str, /) -> bool:
    """Report whether one reading contains the term, case-insensitively.

    Parameters
    ----------
    term
        The search term, already stripped and known non-empty.
    text
        One reading of one page. Empty is legal and never matches.

    Returns
    -------
    bool
        Whether ``term`` appears in ``text``, compared under ``str.casefold`` -- the same
        test :class:`~rmspec.app.resolve.ResolveDocument` applies to document names, so the
        two commands cannot disagree about what containment means.
    """
    return term.casefold() in text.casefold()


class SearchText:
    """Find a term in transcribed page text, saying of every hit which source produced it.

    Three collaborators, all Protocols: the mirror whose rows are ours, the device's own
    handwriting index as a free prior, and the audit log -- read, never appended to, and only
    when a scope turns out to hold no text at all.

    The index is required rather than optional. A deployment with no index binds one whose
    lookups raise ``StoreUnavailableError``, which is reported as the degradation it is; a
    null index answering "no row" for every page would be indistinguishable from an index
    that genuinely has not caught up, which is the one confusion this use case exists to
    prevent.

    Notes
    -----
    Cost is one store read for the scope plus two per document -- its recorded pages and its
    recorded text -- and one index lookup per page. That is more round trips than a single
    query would be, and they are all local::

        hits = searcher.search(SearchTextRequest(query="retention"))
        ours = [hit for hit in hits.matches if hit.source is MatchSource.MIRROR]
    """

    def __init__(
        self,
        *,
        store: DocumentSyncStore,
        index: HandwrittenTextIndex,
        audit: SyncAuditLog,
    ) -> None:
        self._store = store
        self._index = index
        self._audit = audit

    def search(self, request: SearchTextRequest, /) -> SearchTextResult:
        """Match the term against every recorded page in scope, from both sources.

        Parameters
        ----------
        request
            The term, and the document to restrict to when there is one.

        Returns
        -------
        SearchTextResult
            Every attributed match in store order, how many pages were examined, what an
            absence of matches means, and the one substitution this search can make.

        Raises
        ------
        UsageError
            The term is blank. Raised before either store is touched.
        DocumentNotFound
            ``request.doc_uuid`` names a document the mirror does not track.
        StoreUnavailableError
            The mirror, or the audit log, could not be read. Only the device index degrades.
        StoredRecordUnreadableError
            A stored row could not be reconstructed.
        """
        term = request.query.strip()
        if not term:
            raise UsageError(
                subject="an empty search term",
                requirement="a word or phrase to look for in transcribed page text",
            )
        log = DegradationLog()
        readings = _DeviceReadings(self._index, log)
        matches: list[TextMatch] = []
        pages_searched = 0
        transcribed = False
        for document in self._scope(request.doc_uuid):
            recorded = {row.page_uuid: row for row in self._store.page_texts(document.uuid)}
            for page in self._store.pages(document.uuid):
                pages_searched += 1
                ours = recorded.get(page.page_uuid)
                theirs = readings.of(page.page_uuid)
                transcribed = transcribed or ours is not None or theirs is not None
                matches.extend(_page_matches(document, page, ours, theirs, term))
        outcome = _outcome(matches, pages_searched=pages_searched, transcribed=transcribed)
        return SearchTextResult(
            outcome=outcome,
            query=term,
            matches=tuple(matches),
            pages_searched=pages_searched,
            recent_ocr_attempt=self._ocr_attempted(outcome),
            degradations=log.frozen(),
        )

    def _scope(self, doc_uuid: str | None, /) -> list[SyncedDocument]:
        """Return the documents to search, in the store's contract order.

        Parameters
        ----------
        doc_uuid
            One document to restrict to, or ``None`` for every recorded document.

        Returns
        -------
        list[SyncedDocument]
            The whole mirror, or exactly one row. Folder rows are not filtered out: a folder
            has no recorded pages and no recorded text, so it contributes nothing, and a
            branch that skips rows already skipped by having nothing to offer is a branch
            that can only rot.

        Raises
        ------
        DocumentNotFound
            The named document is not tracked. Absence of a *named* document is a mistake to
            report, where absence of matches is a result to return.
        """
        if doc_uuid is None:
            return self._store.list_documents()
        document = self._store.get_document(doc_uuid)
        if document is None:
            raise DocumentNotFound(query=doc_uuid, store=_MIRROR_STORE)
        return [document]

    def _ocr_attempted(self, outcome: SearchOutcome, /) -> bool | None:
        """Look for evidence that a transcription was ever attempted, if that is the question.

        Parameters
        ----------
        outcome
            What the search found.

        Returns
        -------
        bool | None
            ``None`` unless the outcome is :attr:`SearchOutcome.NOTHING_TRANSCRIBED`, in
            which case whether an ``OCR`` operation appears in the last
            :data:`_AUDIT_PROBE_LIMIT` recorded operations. Never consulted otherwise, so a
            search that found something costs no audit read.
        """
        if outcome is not SearchOutcome.NOTHING_TRANSCRIBED:
            return None
        recent = self._audit.recent(limit=_AUDIT_PROBE_LIMIT)
        return any(record.entry.operation is SyncOperation.OCR for record in recent)


def _page_matches(
    document: SyncedDocument,
    page: SyncedPage,
    ours: PageText | None,
    theirs: IndexedHandwriting | None,
    term: str,
    /,
) -> tuple[TextMatch, ...]:
    """Build the matches one page contributes, flagging corroboration on both of them.

    A free function rather than a method: it reads its five arguments and no state, and
    keeping it out of the class is what stops a reader looking for state it does not have.

    Parameters
    ----------
    document
        The mirror's row for the owning document, which is where the name comes from.
    page
        The mirror's row for the page, which is where the index comes from.
    ours
        The mirror's text for this page, or ``None`` when it has none.
    theirs
        The device index's reading of this page, or ``None`` when it has none to give.
    term
        The stripped search term.

    Returns
    -------
    tuple[TextMatch, ...]
        Empty, one match, or two. Two means both sources matched: both are returned, both
        flagged :attr:`TextMatch.corroborated`, because the second reading is the evidence
        that the first is right and collapsing them would discard it.
    """
    mirror = ours if ours is not None and _contains(term, ours.text) else None
    device = theirs if theirs is not None and _contains(term, theirs.text) else None
    both = mirror is not None and device is not None
    found: list[TextMatch] = []
    if mirror is not None:
        found.append(
            _match(
                document,
                page,
                source=MatchSource.MIRROR,
                text=mirror.text,
                provenance=mirror.provenance,
                index_generation=None,
                corroborated=both,
            )
        )
    if device is not None:
        found.append(
            _match(
                document,
                page,
                source=MatchSource.DEVICE_INDEX,
                text=device.text,
                provenance=None,
                index_generation=device.generation,
                corroborated=both,
            )
        )
    return tuple(found)


def _match(
    document: SyncedDocument,
    page: SyncedPage,
    /,
    *,
    source: MatchSource,
    text: str,
    provenance: TextProvenance | None,
    index_generation: int | None,
    corroborated: bool,
) -> TextMatch:
    """Build one attributed match from the mirror's rows for the document and the page.

    Parameters
    ----------
    document
        The owning document's mirror row.
    page
        The page's mirror row.
    source
        Which source produced the reading.
    text
        The reading.
    provenance
        How it was produced, for a mirror reading only.
    index_generation
        Which snapshot of the index produced it, for a device reading only.
    corroborated
        Whether the other source matched this page too.

    Returns
    -------
    TextMatch
        The match. Identity and position come from the mirror even for a device reading, so
        a hit from the free prior is still nameable and still sorts with everything else.
    """
    return TextMatch(
        doc_uuid=document.uuid,
        doc_name=document.visible_name,
        page_uuid=page.page_uuid,
        page_index=page.page_index,
        source=source,
        text=text,
        provenance=provenance,
        index_generation=index_generation,
        corroborated=corroborated,
    )


def _outcome(
    matches: list[TextMatch], /, *, pages_searched: int, transcribed: bool
) -> SearchOutcome:
    """Decide which of the four things a search found.

    Parameters
    ----------
    matches
        Every match found.
    pages_searched
        How many recorded pages were examined.
    transcribed
        Whether any examined page had text from either source, judged on a row's presence
        rather than on its contents: a row with empty text is a transcription that happened.

    Returns
    -------
    SearchOutcome
        ``MATCHED`` when anything matched; else ``NOTHING_SYNCED`` when no page was
        examined, ``NOTHING_TRANSCRIBED`` when pages were examined and none had text, and
        ``NO_MATCH`` only when text really was searched -- the one answer that says
        something about the term rather than about the state of the mirror.
    """
    if matches:
        return SearchOutcome.MATCHED
    if not pages_searched:
        return SearchOutcome.NOTHING_SYNCED
    if not transcribed:
        return SearchOutcome.NOTHING_TRANSCRIBED
    return SearchOutcome.NO_MATCH
