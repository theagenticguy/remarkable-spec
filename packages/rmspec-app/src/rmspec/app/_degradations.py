"""The one way a use case reports a substitution it made instead of failing.

Private to this package. Every use case that can substitute a value threads one
:class:`DegradationLog` through its own body and hands
:meth:`DegradationLog.frozen` to the ``degradations`` field of its result model, so
the reporting shape is identical across use cases rather than each one carrying its
own list, its own ordering rule, and its own chance of forgetting to convert it.

Deliberately not a port. ``Degradation``'s own docstring rules out a sink: "There is
no sink port: a write-through collector would have put a test double's buffer into
the contract." This class is the local alternative -- an ordinary object a use case
constructs, fills, and drops. Nothing observes it, nothing injects it, and it never
reaches a constructor argument.

It records rather than deduplicates, and it preserves insertion order. Two
degradations of the same kind about two different pages are two facts, and the order
they were discovered in is the order a reader wants them summarised in; collapsing
either would be this layer deciding something the CLI is better placed to decide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rmspec.domain.errors import Degradation

__all__ = ["DegradationLog"]


class DegradationLog:
    """An append-only accumulator of the substitutions one use-case call made.

    Total and boring on purpose: two methods, no failure modes, no configuration.
    A fresh log is empty, :meth:`record` appends, and :meth:`frozen` snapshots. It
    holds no reference to the result model it will end up inside, so a use case may
    build one before it knows whether it will succeed.

    Examples
    --------
    >>> from rmspec.domain.errors import Degradation, DegradationKind
    >>> log = DegradationLog()
    >>> log.frozen()
    ()
    >>> log.record(
    ...     Degradation(
    ...         kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
    ...         subject="d3b38661",
    ...         detail="metadata is not JSON",
    ...     )
    ... )
    >>> len(log.frozen())
    1
    """

    __slots__ = ("_recorded",)

    def __init__(self) -> None:
        self._recorded: list[Degradation] = []

    def record(self, degradation: Degradation, /) -> None:
        """Append one substitution to the log.

        Parameters
        ----------
        degradation
            What was substituted, already built from the closed
            :class:`~rmspec.domain.errors.DegradationKind` vocabulary. Building it
            at the call site rather than from loose keyword arguments here is what
            keeps this class from growing an opinion about which kinds exist.
        """
        self._recorded.append(degradation)

    def frozen(self) -> tuple[Degradation, ...]:
        """Snapshot the log as the immutable tuple a result model carries.

        Returns
        -------
        tuple[Degradation, ...]
            Every recorded substitution, in the order it was recorded. A copy, so a
            later :meth:`record` cannot reach back into a result already returned --
            which is the whole reason a use case may hand this out and keep going.
        """
        return tuple(self._recorded)
