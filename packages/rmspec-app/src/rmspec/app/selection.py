"""Which pages of a document a use case works on, and the cap that bounds the work.

Public, because the CLI constructs one: a command parses ``--pages`` or ``--limit``
into a :class:`PageSelection` and hands it to a use case inside a request model. The
use cases never build one from raw integers, and none of them reimplements the
arithmetic -- :meth:`PageSelection.resolve_against` is the single place a selection
meets a concrete page count.

Indices are 0-based. All of them. Everywhere in this module
-------------------------------------------------------------
``PageSelection.of(0)`` is the first page of the document, ``resolve_against``
returns 0-based indices, and every index in a :class:`PageSelection` indexes
``DocumentSourceBundle.pages`` directly with no adjustment.

The CLI is **1-based**, because "page 1" is what a human means, and it converts at
its own boundary by subtracting one. That conversion is the single most likely
off-by-one in the whole surface, so it is stated here in the module that owns the
other half of it: nothing in :mod:`rmspec.app` ever adds or subtracts one, and a
command that forgets to is off by a page on every artifact it writes. A CLI turning
a non-positive page number into a negative index gets
``pydantic.ValidationError`` from the ``ge=0`` constraint below rather than a
silently wrapped index -- but the command is expected to have raised
:class:`~rmspec.domain.errors.UsageError` about its own flag before it gets there,
because that is the layer that knows the user typed ``--pages 0``.

Two behaviours here are decisions rather than details
----------------------------------------------------
**A requested index outside the document raises**
:class:`~rmspec.domain.errors.PageNotFound`, never a silent skip. Legacy rendered
nothing and exited 0, which is indistinguishable from a document that rendered
correctly and is the failure mode that costs a user the most: they only find out
when they open the artifact. This applies to :meth:`PageSelection.of` alone.
:meth:`PageSelection.first` is a *bound*, not an assertion -- "the first five pages"
of a two-page document is those two pages, and raising there would refuse a request
that names no page at all.

**The work cap is checked at the entry boundary, before any expensive work.**
:meth:`PageSelection.resolve_against` takes ``max_pages`` and raises
:class:`~rmspec.domain.errors.UsageError` when the resolved selection exceeds it. It
is not a truncation, and it is not a check inside a loop. The reason is measured:
the attached tablet holds a 432-page document (``d3b38661-...``), and
``rmspec ocr`` over it without a cap is 432 model calls and 432 rasterizations
before anything on stdout tells the user what they just bought. Legacy dodged this
by defaulting its ``ocr`` command to **the last page only**, which is not a cap but
a surprising default -- a user who asks for a document and receives one page has
been silently given the wrong answer. v0.2.0 caps instead: the whole selection or a
refusal that names the number.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, Field, model_validator

from rmspec.domain.errors import InvalidSettingError, PageNotFound, UsageError

__all__ = ["PageSelection"]


def _ascending_without_repeats(indices: tuple[int, ...]) -> tuple[int, ...]:
    """Normalize an explicit index tuple to ascending order with no repeats.

    Two decisions, both of which are visible in output artifacts if they go the
    other way. Sorting means page order in a rendered PDF or a transcript is
    document order rather than the order the arguments happened to arrive in, so
    ``--pages 3,1`` and ``--pages 1,3`` cannot produce differently ordered files.
    Deduplicating means ``--pages 2,2`` transcribes page 2 once, so a repeated
    argument cannot be billed twice by a paid recognizer -- and it means the work
    cap counts pages rather than mentions.

    Parameters
    ----------
    indices
        The 0-based indices as the caller supplied them.

    Returns
    -------
    tuple[int, ...]
        The same indices, ascending and unique.
    """
    return tuple(sorted(set(indices)))


_PageIndices = Annotated[
    tuple[Annotated[int, Field(ge=0)], ...],
    AfterValidator(_ascending_without_repeats),
]
"""An explicit set of 0-based page indices, normalized ascending and unique."""


class PageSelection(BaseModel, frozen=True, extra="forbid"):
    """The pages of one document a caller asked for, before any document is known.

    A selection is a *request*, not a result: it carries no page count and cannot
    tell whether it is satisfiable. :meth:`resolve_against` is where it meets a real
    document, which is also the only place it can fail.

    Three states, spelled by the three constructors and by nothing else:

    * every page -- :meth:`all`, both fields ``None``;
    * an explicit set of indices -- :meth:`of`, ``indices`` set;
    * a leading bound -- :meth:`first`, ``limit`` set.

    Construct through the classmethods. Direct construction works and validates the
    same way, because the CLI may want to build one from parsed flags without
    branching, but a caller that sets both fields is describing two different
    selections at once and the model refuses it.

    Notes
    -----
    A negative index and a non-positive ``limit`` are rejected by pydantic
    constraints rather than by a named error class. That follows the rule
    :mod:`rmspec.domain.errors` states about itself -- several proposed error
    classes were deleted because "a pydantic constraint already makes the state
    unconstructible" -- and it is why this model's failures are
    ``pydantic.ValidationError`` while every failure of :meth:`resolve_against` is a
    named member of the domain error tree.
    """

    indices: _PageIndices | None = None
    """The 0-based indices the caller named, or ``None`` when it named none.

    Normalized to ascending order with repeats removed, so this tuple is also the
    order the pages will be worked in.
    """

    limit: int | None = Field(default=None, gt=0)
    """Work at most this many leading pages, or ``None`` for no bound.

    A bound, not an assertion: a limit above the document's page count yields every
    page rather than raising.
    """

    @model_validator(mode="after")
    def _check_exactly_one_rule(self) -> Self:
        """Reject a selection that states an explicit set and a bound at once.

        Returns
        -------
        Self
            The validated selection.

        Raises
        ------
        ValueError
            Both ``indices`` and ``limit`` were given, which describes two
            selections and resolves to neither.
        """
        if self.indices is not None and self.limit is not None:
            msg = "a selection names explicit indices or a leading limit, never both"
            raise ValueError(msg)
        return self

    @classmethod
    def all(cls) -> Self:
        """Select every page of whatever document this is resolved against.

        Returns
        -------
        Self
            A selection with no explicit indices and no bound.
        """
        return cls()

    @classmethod
    def of(cls, *indices: int) -> Self:
        """Select exactly the named pages, by 0-based index.

        Every index must exist in the document this is resolved against;
        :meth:`resolve_against` raises :class:`~rmspec.domain.errors.PageNotFound`
        if one does not.

        Parameters
        ----------
        *indices
            0-based page indices. Order and repeats do not matter -- the selection
            is stored ascending and unique.

        Returns
        -------
        Self
            A selection naming those pages.
        """
        return cls(indices=tuple(indices))

    @classmethod
    def first(cls, count: int, /) -> Self:
        """Select at most the first ``count`` pages, in document order.

        Parameters
        ----------
        count
            How many leading pages to take. A count above the document's page count
            yields every page; it is a bound rather than an assertion, so it never
            raises :class:`~rmspec.domain.errors.PageNotFound`.

        Returns
        -------
        Self
            A selection bounded to that many leading pages.
        """
        return cls(limit=count)

    def resolve_against(
        self,
        page_count: int,
        /,
        *,
        document_uuid: str,
        max_pages: int,
    ) -> tuple[int, ...]:
        """Turn this selection into the concrete 0-based indices to work on.

        The single entry boundary for page work. Every failure a selection can have
        surfaces here, before the caller has rendered, rasterized, or called a
        model: an index the document does not have, and a selection larger than the
        run is allowed to pay for.

        Parameters
        ----------
        page_count
            How many pages the document actually has, from the source bundle rather
            than from device metadata -- ``DeviceDocument.page_count`` is whatever
            the device recorded and may be ``None``.
        document_uuid
            The document being resolved against.
            :class:`~rmspec.domain.errors.PageNotFound` names the document it is
            about, so this method has to be told which one; that is the only reason
            the parameter exists.
        max_pages
            The most pages this run may work on. Comes from settings, so a run can
            raise it deliberately; it has no default here because a silent default
            cap is the same surprise as legacy's silent last-page-only default.

        Returns
        -------
        tuple[int, ...]
            The indices to work on, ascending, each one a valid index into the
            document's ordered pages. Empty only when the document has no pages.

        Raises
        ------
        InvalidSettingError
            ``max_pages`` is not positive, so no selection could ever satisfy it.
            Raised rather than reported as a usage failure because a cap of zero is
            a wiring mistake and blaming the user's command line for it sends them
            to fix the wrong thing.
        PageNotFound
            An explicitly named index is not a page of this document. Never a
            silent skip.
        UsageError
            The resolved selection is larger than ``max_pages``. Raised before any
            page is read, and the message names both numbers.
        """
        if max_pages < 1:
            raise InvalidSettingError(
                setting="max_pages",
                value=str(max_pages),
                requirement="a positive number of pages",
            )
        resolved = self._concrete(page_count, document_uuid=document_uuid)
        if len(resolved) > max_pages:
            raise UsageError(
                subject=f"a selection of {len(resolved)} pages",
                requirement=f"at most {max_pages} pages",
            )
        return resolved

    def _concrete(self, page_count: int, /, *, document_uuid: str) -> tuple[int, ...]:
        """Expand this selection against a page count, ignoring the work cap.

        Parameters
        ----------
        page_count
            How many pages the document has.
        document_uuid
            The document being resolved against, for the error message.

        Returns
        -------
        tuple[int, ...]
            The indices this selection names, ascending.

        Raises
        ------
        PageNotFound
            An explicitly named index is not a page of this document.
        """
        if self.indices is not None:
            for index in self.indices:
                if index >= page_count:
                    raise PageNotFound(
                        document_uuid=document_uuid,
                        page=f"index {index}",
                        page_count=page_count,
                    )
            return self.indices
        if self.limit is not None:
            return tuple(range(min(self.limit, page_count)))
        return tuple(range(page_count))
