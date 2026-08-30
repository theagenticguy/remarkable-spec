"""Turn what a user typed into exactly one document, and report what else it matched.

Relocates the policy of legacy ``cli/_resolve.py``, which is worth keeping verbatim:
three stages tried in order -- an exact 36-character uuid, then a uuid prefix of eight
or more hexadecimal characters, then a case-insensitive substring of the visible name.
The first stage that matches anything wins. On several matches it does **not** fail:
the candidates are ranked by ``(page_count, last_modified)`` descending and the first
is taken.

Four changes from legacy, each of which the legacy behaviour argues for
----------------------------------------------------------------------
**It returns its ambiguity instead of printing it.** Legacy took a ``rich.Console``
and printed a warning from four sites *inside* resolution, which is the direct cause
of four polluted ``--json`` modes downstream: a machine-readable mode emitted human
prose before its JSON and was unpipeable. So the result carries
:attr:`ResolveDocumentResult.chosen` plus :attr:`ResolveDocumentResult.also_matched`,
and the CLI decides whether to warn, on which stream, and in which format. Nothing in
this module writes anywhere.

**``--strict`` is one check at the boundary, not a mode here.** There is no ``strict``
field on the request. A non-empty ``also_matched`` is the data a strict boundary needs,
and :class:`~rmspec.domain.errors.AmbiguousDocument` already carries
``tuple[DocumentCandidate, ...]``, so the CLI raises it from what it was handed. A mode
flag here would mean two code paths through one policy, tested twice, with the second
reachable only by a flag.

**It skips trashed documents.** Legacy skipped folders but not the trash, so a deleted
document could shadow a live one of the same name -- the caller renders the wrong
document and nothing says so. Two notes keep this filter honest. Folder skipping has no
analogue here at all: :class:`~rmspec.domain.ports.device.DeviceListing` returns
``documents`` and ``folders`` as separate tuples, so a folder identifier structurally
cannot reach this policy. And the trash filter is a **no-op over USB**: measured on
firmware 3.27.3.0, ``GET /documents/`` omits trashed entries at every depth and no entry
ever carries a ``parent`` of ``"trash"``, so a USB catalog accurately reports
``trashed=False`` on everything it returns. The filter is load-bearing over SSH and over
the local mirror, both of which do see the trash. It is not dead code; it is code whose
one transport cannot exercise it, which is why the port made ``trashed`` a real field
instead of a wire sentinel each adapter re-derived.

A query that matches only a trashed document is therefore
:class:`~rmspec.domain.errors.DocumentNotFound`, and that is the intended answer: the
trash is not the library. It is not reported as a degradation because
``DegradationKind`` is closed and has no member meaning "excluded because deleted" --
adding one is a reviewed change to the domain, not something this module may decide.

**Ambiguity is reported even when the caller did not ask about it.** A result that can
hide a second match is how the wrong document gets rendered silently, so
``also_matched`` has no default and a ``DegradationKind.AMBIGUOUS_AUTO_RESOLVED``
degradation is recorded whenever it is non-empty. Both spellings are deliberate: a
caller may branch on the candidates, and a caller that only summarises degradations
still learns that a choice was made for it.

Why an incomplete listing is not answered with "not found"
---------------------------------------------------------
``DeviceListing.skipped`` carries every entry the transport saw and could not represent.
Those become ``DegradationKind.CATALOG_ENTRY_SKIPPED`` degradations on the result,
because a listing that quietly omitted entries must not resolve as though it were
complete.

When nothing matched *and* entries were skipped, the outcome is different, because the
document asked for may be one of the omitted ones -- and then
:class:`~rmspec.domain.errors.DocumentNotFound` is a lie: it asserts a fact about the
library's contents that an incomplete enumeration does not license. This module raises
:class:`~rmspec.domain.errors.DocumentStoreUnavailable` instead, naming how many entries
were unreadable and why, which is a distinct outcome with a distinct exit status --
``EX_UNAVAILABLE`` (69) rather than ``EX_NOINPUT`` (66) -- so a caller can tell "your
query is wrong" from "the library could not be fully read".

The closer match in the tree is unavailable rather than unwanted.
:class:`~rmspec.domain.ports.device.DeviceCatalog` states that an identifier appearing
in ``skipped`` raises ``MalformedDeviceMetadata`` from ``get_document`` and "is never
``DeviceDocumentNotFound``: the entry exists, it just cannot be represented" -- the same
judgement this module is making. But ``MalformedDeviceMetadata`` requires a
``TransportKind``, which the application layer does not know and must not learn: ports
are addresses, not transports, and inventing a value for it is exactly the
``region="n/a"`` mistake :mod:`rmspec.domain.errors` already removed from
``ModelAccessDenied``. So the honest error this layer *can* raise is the whole-store
one, and the reasons travel in its ``detail``.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Final, NoReturn

from pydantic import BaseModel

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import (
    Degradation,
    DegradationKind,
    DocumentCandidate,
    DocumentNotFound,
    DocumentStoreUnavailable,
    UsageError,
)
from rmspec.domain.ports.device import DeviceDocument

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rmspec.domain.ports.device import DeviceCatalog, SkippedEntry

__all__ = ["ResolveDocument", "ResolveDocumentRequest", "ResolveDocumentResult"]

_STORE: Final = "device catalog"
"""How this layer names what it read, since it cannot know which transport served it."""

_UNIDENTIFIED: Final = "<entry with no recoverable identifier>"
"""Subject for a skipped entry whose ``uuid`` the transport could not recover."""

_UUID_LENGTH: Final = 36
"""Length of a canonical hyphenated uuid, which is what stage one requires exactly."""

_MIN_PREFIX_LENGTH: Final = 8
"""Shortest query stage two will treat as a uuid prefix.

Eight is the length of a uuid's first hyphen-free group. Below it, a hexadecimal string
is likelier to be part of a document's name than an identifier -- ``cafe`` and ``1234``
are the cases that motivate the floor -- so a shorter query falls through to the
substring stage instead of matching an identifier the user never typed.
"""

_UUID_CHARACTERS: Final = frozenset("0123456789abcdef-")
"""Characters a uuid prefix may contain, hyphen included.

The hyphen belongs here because any uuid prefix longer than eight characters contains
one: the ninth character of ``d3b38661-...`` is a hyphen. Reading "eight or more
hexadecimal characters" as forbidding it would make every prefix of length nine to
thirteen unmatchable, which is the length a human actually pastes.
"""

_UNDATED: Final = datetime.datetime.min.replace(tzinfo=datetime.UTC)
"""Tie-break floor for a document whose modification time the transport did not report."""

_UNCOUNTED: Final = -1
"""Tie-break floor for a page count the transport did not report.

``DeviceDocument.page_count`` is ``Field(ge=0)``, so ``-1`` cannot collide with a real
count. A document of unknown length therefore loses the ranking to one of known length,
which preserves the legacy ordering's intent -- more pages wins -- and is the only total
order available over a field that is genuinely optional.
"""


class ResolveDocumentRequest(BaseModel, frozen=True, extra="forbid"):
    """What the caller typed, and nothing else.

    One field on purpose. A ``strict`` flag would belong here if strictness were a mode
    of this use case, and it is not: it is one check the CLI performs on
    :attr:`ResolveDocumentResult.also_matched`.
    """

    query: str
    """A document name substring, a full uuid, or a uuid prefix, as the user typed it.

    Unconstrained by pydantic on purpose. A blank query is a
    :class:`~rmspec.domain.errors.UsageError` raised by :meth:`ResolveDocument.resolve`,
    because that error's own docstring names "an empty search query" as one of its cases
    -- and a ``pydantic.ValidationError`` here would be a second error vocabulary the CLI
    must render for a condition the domain already named. Where the domain names a
    condition, this package raises the domain's error; where the domain deliberately
    declined to name one, a pydantic constraint keeps the state unconstructible instead.
    """


class ResolveDocumentResult(BaseModel, frozen=True, extra="forbid"):
    """One document, plus everything the caller needs in order to trust the choice.

    No field has a default, following the reason
    :class:`~rmspec.domain.ports.device.DeviceListing` gives for the same decision: a
    caller cannot construct this without stating what else matched and what was
    substituted, so neither can be forgotten at a construction site.
    """

    chosen: DeviceDocument
    """The document that won, ranked by ``(page_count, last_modified)`` descending."""

    also_matched: tuple[DocumentCandidate, ...]
    """Every other document the query matched, in the same ranked order.

    Empty when the query was unambiguous. Non-empty is the whole input a ``--strict``
    boundary needs: the CLI raises :class:`~rmspec.domain.errors.AmbiguousDocument`,
    which already carries exactly this tuple's element type.
    """

    degradations: tuple[Degradation, ...]
    """Every substitution this resolution made instead of failing.

    Two kinds arise here: ``CATALOG_ENTRY_SKIPPED`` once per entry the listing could not
    represent, and ``AMBIGUOUS_AUTO_RESOLVED`` once when a choice was made among several
    matches.
    """


def _is_uuid_prefix(query: str) -> bool:
    """Report whether a query is long enough and shaped like the head of a uuid.

    Parameters
    ----------
    query
        The already-stripped query.

    Returns
    -------
    bool
        ``True`` when the query is at least :data:`_MIN_PREFIX_LENGTH` characters long
        and every character of it, case-folded, is in :data:`_UUID_CHARACTERS`.
    """
    return len(query) >= _MIN_PREFIX_LENGTH and set(query.lower()) <= _UUID_CHARACTERS


def _rank(document: DeviceDocument) -> tuple[int, datetime.datetime]:
    """Build the sort key that decides which of several matches is returned.

    Parameters
    ----------
    document
        A matched document.

    Returns
    -------
    tuple[int, datetime.datetime]
        Page count then last-modified instant, each with an unreported value replaced by
        a floor so that the key is total. Sorted descending, this is legacy's ordering.
    """
    count = _UNCOUNTED if document.page_count is None else document.page_count
    modified = _UNDATED if document.last_modified is None else document.last_modified
    return (count, modified)


def _matches(query: str, documents: Sequence[DeviceDocument]) -> tuple[DeviceDocument, ...]:
    """Apply the three resolution stages in order and return the first one that hit.

    Parameters
    ----------
    query
        The already-stripped query.
    documents
        The live documents to search, with the trash already excluded.

    Returns
    -------
    tuple[DeviceDocument, ...]
        Every document matched by the first stage that matched anything, in listing
        order. Empty when no stage matched.
    """
    if len(query) == _UUID_LENGTH:
        exact = tuple(doc for doc in documents if doc.uuid == query)
        if exact:
            return exact
    if _is_uuid_prefix(query):
        folded = query.lower()
        prefixed = tuple(doc for doc in documents if doc.uuid.lower().startswith(folded))
        if prefixed:
            return prefixed
    needle = query.casefold()
    return tuple(doc for doc in documents if needle in doc.name.casefold())


def _refuse(query: str, skipped: tuple[SkippedEntry, ...], *, represented: int) -> NoReturn:
    """Raise the kind of "nothing matched" the listing's completeness licenses.

    Parameters
    ----------
    query
        The stripped query that matched nothing.
    skipped
        The listing's skipped entries. Only their number and their reasons are used.
    represented
        How many documents the listing did represent.

    Raises
    ------
    DocumentNotFound
        The listing was complete, so nothing matching it is a fact about the library.
    DocumentStoreUnavailable
        The listing omitted entries, so the query may name one of them and the honest
        answer is that the library could not be fully read.
    """
    if not skipped:
        raise DocumentNotFound(query=query, store=_STORE)
    reasons = ", ".join(sorted({entry.reason.value for entry in skipped}))
    raise DocumentStoreUnavailable(
        store=_STORE,
        detail=(
            f"{len(skipped)} of {represented + len(skipped)} entries could not be "
            f"represented ({reasons}), so whether {query!r} is one of them is unknown"
        ),
    )


class ResolveDocument:
    """Resolve one user-supplied selector to one document, without printing anything.

    Pure policy over records. It reads a
    :class:`~rmspec.domain.ports.device.DeviceCatalog` once per call and decides; it
    opens no file, writes to no stream, and holds no state between calls. Legacy got
    this wrong by taking a ``rich.Console`` as a constructor argument, which is why this
    class takes exactly one collaborator and that collaborator is a Protocol.

    Notes
    -----
    A resolution that had to choose reports the choice twice, as candidates and as a
    degradation, and the caller decides which to act on::

        result = resolver.resolve(ResolveDocumentRequest(query="notes"))
        if strict and result.also_matched:
            raise AmbiguousDocument(query="notes", candidates=result.also_matched)
    """

    def __init__(self, *, catalog: DeviceCatalog) -> None:
        self._catalog = catalog

    def resolve(self, request: ResolveDocumentRequest, /) -> ResolveDocumentResult:
        """Resolve the request's query to exactly one document.

        Parameters
        ----------
        request
            The query, as the user typed it. Surrounding whitespace is stripped, because
            a shell-quoted trailing space is not part of a search term.

        Returns
        -------
        ResolveDocumentResult
            The chosen document, every other match, and every substitution made.

        Raises
        ------
        UsageError
            The query is empty or only whitespace, so there is nothing to match. Raised
            before the catalog is read, so a typo costs no device round trip.
        DocumentNotFound
            No live document matched and the listing was complete.
        DocumentStoreUnavailable
            No live document matched *and* the listing omitted entries, so the answer is
            unknown rather than negative. See this module's docstring.
        DeviceUnreachable
            Raised by the catalog and never degraded here: an unreachable tablet must not
            resolve as an empty library.
        DeviceAuthFailed
            Raised by the catalog.
        DeviceProtocolError
            Raised by the catalog.
        """
        query = request.query.strip()
        if not query:
            raise UsageError(
                subject="an empty document query",
                requirement="a document name, a uuid, or a uuid prefix",
            )
        listing = self._catalog.list_documents()
        log = DegradationLog()
        for entry in listing.skipped:
            log.record(
                Degradation(
                    kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
                    subject=entry.uuid or _UNIDENTIFIED,
                    detail=f"{entry.reason.value}: {entry.detail}",
                )
            )
        live = tuple(doc for doc in listing.documents if not doc.trashed)
        ranked = sorted(_matches(query, live), key=_rank, reverse=True)
        if not ranked:
            _refuse(query, listing.skipped, represented=len(listing.documents))
        chosen, *rest = ranked
        also_matched = tuple(DocumentCandidate(uuid=doc.uuid, name=doc.name) for doc in rest)
        if also_matched:
            log.record(
                Degradation(
                    kind=DegradationKind.AMBIGUOUS_AUTO_RESOLVED,
                    subject=query,
                    detail=(
                        f"{len(ranked)} documents matched; ranked by page count then "
                        f"last modified, both descending"
                    ),
                    substituted=chosen.uuid,
                )
            )
        return ResolveDocumentResult(
            chosen=chosen,
            also_matched=also_matched,
            degradations=log.frozen(),
        )
