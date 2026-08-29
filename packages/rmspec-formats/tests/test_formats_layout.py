"""The xochitl on-disk layout: filenames, and what a catalog scan sees.

Cheap tests over one module, kept separate because these are the names the firmware
chose and a typo in one of them is a document that silently does not exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rmspec.formats import layout

if TYPE_CHECKING:
    from collections.abc import Callable

DOC = "3f6a-1"
PAGE = "9b2c-7"

BUILDERS: list[tuple[Callable[[Path, str], Path], str]] = [
    (layout.metadata_path, layout.METADATA_SUFFIX),
    (layout.content_path, layout.CONTENT_SUFFIX),
    (layout.pagedata_path, layout.PAGEDATA_SUFFIX),
    (layout.page_dir, ""),
]
"""Every document-level path builder, with the suffix it appends."""


@pytest.mark.parametrize(("build", "suffix"), BUILDERS)
def test_a_document_artifact_is_named_by_appending_its_suffix(
    build: Callable[[Path, str], Path], suffix: str, tmp_path: Path
):
    assert build(tmp_path, DOC) == tmp_path / f"{DOC}{suffix}"


def test_a_page_artifact_lives_in_the_document_directory(tmp_path: Path):
    assert layout.page_path(tmp_path, DOC, PAGE) == tmp_path / DOC / f"{PAGE}.rm"


@given(
    doc=st.text(alphabet="abc.-0", min_size=1, max_size=8).filter(lambda name: set(name) != {"."})
)
def test_a_dotted_identifier_keeps_its_whole_name(doc: str):
    """``Path.with_suffix`` -- what the legacy loader used -- would eat the last segment.

    ``DocumentId``'s character class admits ``.``, so a dotted identifier would have
    produced a filename that does not exist and a page that silently reads as absent.

    An identifier of nothing *but* dots is excluded, and that exclusion is the domain's
    rather than this module's: ``pathlib`` collapses ``root / "."`` to ``root``, so such a
    name could name a directory instead of an entry -- which is why ``DocumentId`` and
    ``PageId`` both refuse it at construction and no such identifier reaches here.
    """
    root = Path("/store")

    for build, suffix in BUILDERS:
        assert build(root, doc).name == f"{doc}{suffix}"
    assert layout.page_path(root, doc, doc).name == f"{doc}.rm"


def test_the_catalog_is_every_metadata_stem_sorted(tmp_path: Path):
    for name in ("b.metadata", "a.metadata", "c.metadata"):
        (tmp_path / name).touch()

    assert layout.catalog_uuids(tmp_path) == ("a", "b", "c")


def test_the_catalog_ignores_everything_that_is_not_a_metadata_sidecar(tmp_path: Path):
    (tmp_path / "doc.metadata").touch()
    for noise in ("doc.content", "doc.pagedata", "doc.local", "doc.failure", "doc.metadata.bak"):
        (tmp_path / noise).touch()
    (tmp_path / "doc").mkdir()
    (tmp_path / "doc.thumbnails").mkdir()

    assert layout.catalog_uuids(tmp_path) == ("doc",)


def test_a_directory_named_like_a_sidecar_is_still_a_catalog_entry(tmp_path: Path):
    """The scan matches on the name, not on the entry type, exactly as ``glob`` did."""
    (tmp_path / "odd.metadata").mkdir()

    assert layout.catalog_uuids(tmp_path) == ("odd",)


def test_an_empty_store_has_an_empty_catalog(tmp_path: Path):
    assert layout.catalog_uuids(tmp_path) == ()


def test_a_store_that_cannot_be_listed_raises_oserror_rather_than_reporting_empty(
    tmp_path: Path,
):
    """``glob`` swallows this and yields nothing, which reads as "the store is empty"."""
    missing = tmp_path / "not-there"

    with pytest.raises(OSError, match="No such file"):
        layout.catalog_uuids(missing)


def test_a_store_that_is_a_file_raises_oserror(tmp_path: Path):
    not_a_directory = tmp_path / "file"
    not_a_directory.write_bytes(b"")

    with pytest.raises(OSError, match="Not a directory"):
        layout.catalog_uuids(not_a_directory)
