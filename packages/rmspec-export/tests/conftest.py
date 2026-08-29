"""Fixtures for the export suite. Everything is built in code; no binary fixture is committed.

Builders, doubles and the port-contract suites live in :mod:`export_support`; this file holds
only fixtures, so a test that needs a builder without a fixture can import it directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from export_support import (
    LEGACY_RM2_BOX_PT,
    PAPER_PRO_BOX_PT,
    US_LETTER_BOX_PT,
    make_page,
)

from rmspec.domain.ports.export import SvgPageSet
from rmspec.export.sink import FilesystemArtifactSink
from rmspec.export.sources import PdfSourceRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from rmspec.domain.ports.export import SvgPage


class PageFactory(Protocol):
    """Callable building an :class:`SvgPage` for a test."""

    def __call__(
        self,
        page_ref: str,
        box_pt: tuple[float, float] = ...,
        *,
        ink: bool = ...,
    ) -> SvgPage:
        """Build the page.

        Parameters
        ----------
        page_ref
            Stable page identity.
        box_pt
            Declared box in points.
        ink
            Whether the page carries a stroke.

        Returns
        -------
        SvgPage
            The page.
        """
        ...


@pytest.fixture
def page() -> PageFactory:
    """Return a factory for legacy-shaped SVG pages.

    Returns
    -------
    PageFactory
        Factory taking a page reference, an optional box in points and an ``ink`` flag.
    """

    def build(
        page_ref: str,
        box_pt: tuple[float, float] = LEGACY_RM2_BOX_PT,
        *,
        ink: bool = True,
    ) -> SvgPage:
        return make_page(page_ref, box_pt, ink=ink)

    return build


@pytest.fixture
def three_distinct_pages(page: PageFactory) -> SvgPageSet:
    """Three pages of three *different* sizes.

    Different on purpose: the port's page-count and per-page-size read-back is what catches a
    composer that drops pages, and identical sizes would make the size half of that comparison
    vacuous.

    Parameters
    ----------
    page
        The page factory.

    Returns
    -------
    SvgPageSet
        The set, in order.
    """
    return SvgPageSet(
        pages=(
            page("page-000", LEGACY_RM2_BOX_PT),
            page("page-001", US_LETTER_BOX_PT),
            page("page-002", PAPER_PRO_BOX_PT),
        )
    )


@pytest.fixture
def registry() -> Iterator[PdfSourceRegistry]:
    """Provide a request-scoped source registry that is closed afterwards.

    Yields
    ------
    PdfSourceRegistry
        The registry.
    """
    with PdfSourceRegistry() as open_registry:
        yield open_registry


@pytest.fixture
def sink(tmp_path: Path) -> FilesystemArtifactSink:
    """Provide a committing sink writing into a fresh directory.

    Parameters
    ----------
    tmp_path
        pytest's per-test temporary directory.

    Returns
    -------
    FilesystemArtifactSink
        The sink, with overwriting off and dry-run off.
    """
    return FilesystemArtifactSink(destination=tmp_path / "out", overwrite=False, dry_run=False)
