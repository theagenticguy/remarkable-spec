"""The xochitl on-disk layout, as data.

One document ``DOC`` in a xochitl root is spread across five names, and this module
is the only place in the workspace that spells any of them:

``DOC.metadata``
    Required. Its presence is what makes ``DOC`` a document in the store.
``DOC.content``
    Optional here, though the tablet always writes one: it carries the page list,
    the source kind and the layout facts.
``DOC.pagedata``
    Optional. One template name per line, aligned to the page list by position.
``DOC/``
    The directory holding one ``PAGE.rm`` scene artifact per annotated page.
``DOC.thumbnails/``
    Per-page JPEG previews. Not modelled: nothing in this workspace reads them.

Deliberately not modelled
-------------------------
The device also writes ``DOC.local`` and ``DOC.failure`` sidecars (see
``specs/device/3.27.3.0/filesystem.json``), which record sync state rather than what
a page *is*. Surfacing either one needs a new ``PageDefectCode`` member or a new
metadata field, both reviewed domain changes, so this step names them here and
leaves them alone rather than quietly widening scope. They cannot be mistaken for a
document by :func:`catalog_uuids`, which matches ``.metadata`` and nothing else.

Paths are built by explicit concatenation, never by ``Path.with_suffix``
-----------------------------------------------------------------------
The legacy loader used ``base.with_suffix(".metadata")``, which *replaces* the last
dot-segment of the stem instead of appending. ``DocumentId.uuid`` admits ``.`` in its
character class, so a dotted identifier would have had its tail eaten and produced a
filename that does not exist -- a page that silently reads as absent. Every canonical
uuid is dot-free, so the two agree on every real document; the f-string is what makes
that agreement unconditional.

Every function here raises ``OSError`` unchanged. Translating it into
``DocumentStoreUnavailable`` is the repository's job, so there is exactly one place
in the package where a filesystem failure becomes a domain error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "CONTENT_SUFFIX",
    "METADATA_SUFFIX",
    "PAGEDATA_SUFFIX",
    "SCENE_SUFFIX",
    "catalog_uuids",
    "content_path",
    "metadata_path",
    "page_dir",
    "page_path",
    "pagedata_path",
]

METADATA_SUFFIX: Final = ".metadata"
"""Suffix of the sidecar whose presence defines a document in the store."""

CONTENT_SUFFIX: Final = ".content"
"""Suffix of the sidecar carrying the page list and the layout facts."""

PAGEDATA_SUFFIX: Final = ".pagedata"
"""Suffix of the newline-delimited template list, aligned to the page list."""

SCENE_SUFFIX: Final = ".rm"
"""Suffix of one page's scene artifact, inside the document's own directory."""


def metadata_path(root: Path, doc_uuid: str, /) -> Path:
    """Locate one document's ``.metadata`` sidecar.

    Parameters
    ----------
    root
        The xochitl root directory.
    doc_uuid
        The document's identifier, exactly as the store spells it.

    Returns
    -------
    Path
        ``root/DOC.metadata``. Not probed: nothing here touches the filesystem.
    """
    return root / f"{doc_uuid}{METADATA_SUFFIX}"


def content_path(root: Path, doc_uuid: str, /) -> Path:
    """Locate one document's ``.content`` sidecar.

    Parameters
    ----------
    root
        The xochitl root directory.
    doc_uuid
        The document's identifier, exactly as the store spells it.

    Returns
    -------
    Path
        ``root/DOC.content``.
    """
    return root / f"{doc_uuid}{CONTENT_SUFFIX}"


def pagedata_path(root: Path, doc_uuid: str, /) -> Path:
    """Locate one document's ``.pagedata`` sidecar.

    Parameters
    ----------
    root
        The xochitl root directory.
    doc_uuid
        The document's identifier, exactly as the store spells it.

    Returns
    -------
    Path
        ``root/DOC.pagedata``.
    """
    return root / f"{doc_uuid}{PAGEDATA_SUFFIX}"


def page_dir(root: Path, doc_uuid: str, /) -> Path:
    """Locate the directory holding one document's scene artifacts.

    Parameters
    ----------
    root
        The xochitl root directory.
    doc_uuid
        The document's identifier, exactly as the store spells it.

    Returns
    -------
    Path
        ``root/DOC``. Absent for a folder, and for a document nothing was ever
        drawn on.
    """
    return root / doc_uuid


def page_path(root: Path, doc_uuid: str, page_uuid: str, /) -> Path:
    """Locate one page's scene artifact.

    Parameters
    ----------
    root
        The xochitl root directory.
    doc_uuid
        The owning document's identifier, exactly as the store spells it.
    page_uuid
        The page's identifier, exactly as the ``.content`` page list spells it.

    Returns
    -------
    Path
        ``root/DOC/PAGE.rm``.
    """
    return page_dir(root, doc_uuid) / f"{page_uuid}{SCENE_SUFFIX}"


def catalog_uuids(root: Path, /) -> tuple[str, ...]:
    """List the identifier of every document the store holds.

    Uses ``iterdir`` rather than ``glob`` on purpose: ``glob`` swallows the
    ``OSError`` from an unreadable or absent directory and yields nothing, which
    would turn "this store cannot be read" into "this store is empty".

    Parameters
    ----------
    root
        The xochitl root directory.

    Returns
    -------
    tuple[str, ...]
        One identifier per ``.metadata`` sidecar, sorted, so a listing is stable
        across filesystems that do not order directory entries.

    Raises
    ------
    OSError
        If *root* does not exist, is not a directory, or cannot be listed. The
        caller translates it; this module does not.
    """
    return tuple(
        sorted(
            entry.name.removesuffix(METADATA_SUFFIX)
            for entry in root.iterdir()
            if entry.name.endswith(METADATA_SUFFIX)
        )
    )
