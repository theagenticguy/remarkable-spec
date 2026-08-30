"""Every branch of :class:`~rmspec.app.selection.PageSelection`, and the work cap.

Two properties here are decisions rather than details, so both are asserted rather than
described: an explicitly named index the document does not have raises
:class:`~rmspec.domain.errors.PageNotFound` instead of being skipped, and a selection
larger than the cap raises :class:`~rmspec.domain.errors.UsageError` rather than being
truncated.

The 0-based convention is asserted too, not just documented. ``of(0)`` resolving to
``(0,)`` and ``of(1)`` failing on a one-page document is the pair of facts that pins the
index base; a future edit that "helpfully" subtracted one would break both.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rmspec.app.selection import PageSelection
from rmspec.domain.errors import InvalidSettingError, PageNotFound, UsageError

DOC = "d3b38661-0000-4000-8000-000000000001"
NO_CAP = 1000


def _resolve(
    selection: PageSelection,
    page_count: int,
    *,
    max_pages: int = NO_CAP,
) -> tuple[int, ...]:
    return selection.resolve_against(page_count, document_uuid=DOC, max_pages=max_pages)


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so that frozen-ness is a runtime fact, not a type error.

    A direct ``selection.limit = 3`` is rejected by the type gate before the test can
    prove pydantic rejects it at runtime, and this repository allows no ``type: ignore``
    to get past that. Going through :func:`setattr` on an ``object`` keeps both gates
    honest: ty sees a legal call, and pydantic still raises.
    """
    setattr(target, field, value)


# ───────────────────────────── the three constructors ─────────────────────────────


def test_all_names_neither_indices_nor_a_limit():
    selection = PageSelection.all()
    assert selection.indices is None
    assert selection.limit is None


def test_of_stores_the_indices_ascending_and_without_repeats():
    assert PageSelection.of(3, 1, 3, 0).indices == (0, 1, 3)


def test_of_with_no_arguments_is_an_empty_explicit_selection():
    """Distinct from ``all()``: it names no pages rather than every page."""
    selection = PageSelection.of()
    assert selection.indices == ()
    assert _resolve(selection, 5) == ()


def test_first_stores_a_limit_and_no_indices():
    selection = PageSelection.first(2)
    assert selection.limit == 2
    assert selection.indices is None


def test_a_selection_is_frozen():
    with pytest.raises(ValidationError, match="frozen"):
        _assign(PageSelection.all(), "limit", 3)


def test_a_selection_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        PageSelection.model_validate({"pages": (1,)})


def test_explicit_indices_and_a_limit_together_are_refused():
    """Two selections at once resolve to neither, so the state is unconstructible."""
    with pytest.raises(ValidationError, match="never both"):
        PageSelection(indices=(0,), limit=1)


def test_a_negative_index_is_refused_by_a_constraint_rather_than_an_error_class():
    with pytest.raises(ValidationError):
        PageSelection.of(-1)


def test_a_non_positive_limit_is_refused_by_a_constraint():
    with pytest.raises(ValidationError):
        PageSelection.first(0)


# ───────────────────────────── resolution, 0-based ─────────────────────────────


def test_all_resolves_to_every_index_in_document_order():
    assert _resolve(PageSelection.all(), 3) == (0, 1, 2)


def test_all_over_a_document_with_no_pages_resolves_to_nothing():
    assert _resolve(PageSelection.all(), 0) == ()


def test_index_zero_is_the_first_page():
    assert _resolve(PageSelection.of(0), 1) == (0,)


def test_of_resolves_to_exactly_the_named_indices():
    assert _resolve(PageSelection.of(2, 0), 3) == (0, 2)


def test_the_last_valid_index_is_page_count_minus_one():
    assert _resolve(PageSelection.of(2), 3) == (2,)


def test_first_takes_a_leading_run():
    assert _resolve(PageSelection.first(2), 5) == (0, 1)


def test_first_above_the_page_count_is_a_bound_rather_than_an_assertion():
    """Asking for the first five pages of a two-page document yields those two pages."""
    assert _resolve(PageSelection.first(5), 2) == (0, 1)


def test_first_over_a_document_with_no_pages_resolves_to_nothing():
    assert _resolve(PageSelection.first(5), 0) == ()


# ───────────────────── an out-of-range index raises, never skips ─────────────────────


def test_an_index_at_the_page_count_is_page_not_found():
    with pytest.raises(PageNotFound) as caught:
        _resolve(PageSelection.of(3), 3)
    assert caught.value.document_uuid == DOC
    assert caught.value.page_count == 3
    assert "index 3" in caught.value.message


def test_one_bad_index_among_good_ones_fails_the_whole_selection():
    """Legacy rendered the good pages and exited 0, which reads as a complete artifact."""
    with pytest.raises(PageNotFound):
        _resolve(PageSelection.of(0, 1, 99), 3)


def test_any_explicit_index_over_a_document_with_no_pages_is_page_not_found():
    with pytest.raises(PageNotFound):
        _resolve(PageSelection.of(0), 0)


# ───────────────────────────── the work cap ─────────────────────────────


def test_a_selection_at_the_cap_is_allowed():
    assert _resolve(PageSelection.all(), 10, max_pages=10) == tuple(range(10))


def test_a_selection_over_the_cap_is_refused_before_any_work():
    """The measured case: one 432-page document must not become 432 model calls."""
    with pytest.raises(UsageError) as caught:
        _resolve(PageSelection.all(), 432, max_pages=50)
    assert "432" in caught.value.message
    assert "50" in caught.value.message
    assert caught.value.remediation == "retry with at most 50 pages"


def test_the_cap_is_not_a_truncation():
    with pytest.raises(UsageError):
        _resolve(PageSelection.of(0, 1, 2), 3, max_pages=2)


def test_the_cap_counts_pages_rather_than_mentions():
    """``--pages 2,2`` is one page of work, so deduplication happens before the cap."""
    assert _resolve(PageSelection.of(1, 1), 3, max_pages=1) == (1,)


def test_a_limit_below_the_cap_passes_even_when_the_document_is_huge():
    """A caller works a big document deliberately this way, rather than by accident."""
    assert _resolve(PageSelection.first(3), 432, max_pages=50) == (0, 1, 2)


def test_an_out_of_range_index_outranks_the_cap():
    """The selection is invalid whatever the cap is, and that is the more specific fact."""
    with pytest.raises(PageNotFound):
        _resolve(PageSelection.of(99), 3, max_pages=1)


def test_a_non_positive_cap_blames_the_wiring_rather_than_the_command_line():
    with pytest.raises(InvalidSettingError) as caught:
        _resolve(PageSelection.all(), 3, max_pages=0)
    assert caught.value.setting == "max_pages"
    assert caught.value.value == "0"


def test_a_non_positive_cap_is_refused_before_the_selection_is_even_expanded():
    """Otherwise an unsatisfiable cap would surface as PageNotFound about a valid index."""
    with pytest.raises(InvalidSettingError):
        _resolve(PageSelection.of(99), 3, max_pages=-1)
