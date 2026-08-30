"""Enumerate the library once, as a hierarchy the CLI can render flat or as a tree.

Replaces five legacy invocation paths and three separate tree renderers: ``ls``,
``ls --tree``, ``tree``, ``device ls`` and ``device ls --tree`` (``ls_cmd.py:242``,
``tree_cmd.py:163``, ``device_cmd.py:236-271``). One use case, because a tree is a
*rendering* of a hierarchy and not a different query: the same
:meth:`~rmspec.domain.ports.device.DeviceCatalog.list_documents` call answers both, and
which one a user sees is decided by a flag the CLI owns. Three renderers over one query is
how they drifted -- two of them reported page counts the third did not, and only one of them
listed folders at all.

Folders and documents stay separate all the way out
---------------------------------------------------
:class:`~rmspec.domain.ports.device.DeviceFolder` and
:class:`~rmspec.domain.ports.device.DeviceDocument` are separate domain types on purpose: a
folder carries no ``page_count`` and no ``file_type`` because a folder has neither, and the
port keeps them in separate tuples so "a folder identifier structurally cannot reach a port
that pulls or renders bytes". Flattening them into one node type with optional fields would
undo that for the convenience of a renderer, and the renderer is the layer that can most
easily tell them apart -- it has two branches either way, one per glyph. So
:class:`CatalogFolder` holds a folder, the documents directly inside it, and the folders
directly under it, each in the type it already had.

Nesting is real, and a naive walk of it does not terminate
----------------------------------------------------------
Measured on the attached device: 9 folders at the root, 30 at depth 1, 2 at depth 2, so a
depth-1 renderer under-reports and a fixed two-level one is one user action from being
wrong. The legacy walkers instead treated *any* parent they could not find as the root,
which is worse than wrong output: combined with a queue seeded from the root's folders, that
silent fallback re-enqueues those folders forever and the command hangs.

This module refuses the fallback and reports the same fact instead. Every folder's ancestor
chain is walked with a visited set, and a chain that revisits an identifier or names a
parent the listing does not hold ends the walk with that whole chain *unrooted* -- reported
in :attr:`ListDocumentsResult.unrooted_folders` rather than moved to the root. The visited
set is load-bearing, not defensive: two folders naming each other as parent make the upward
walk cycle, and without it a test does not fail, it hangs.

Nothing is lost either way. A document whose parent is unrooted is reported in
:attr:`ListDocumentsResult.unrooted_documents`, so it is still in the output and still not
claimed to be somewhere it is not.

The trash, and why the flag is a no-op over one transport
--------------------------------------------------------
:attr:`ListDocumentsRequest.include_trashed` is honoured against every transport, and over
the USB web API it can make no difference, which is worth stating so that a reader does not
delete it as dead code. Measured on firmware 3.27.3.0, ``GET /documents/`` omits trashed
entries at every depth and no entry ever carries a ``parent`` of ``"trash"``, so a USB
catalog reports ``trashed=False`` on everything it returns and that is *accurate* -- it never
returns a trashed one. The flag is load-bearing over SSH and over the local mirror, both of
which see the trash. It is not dead code; it is code whose one transport cannot exercise it.

Including the trash puts trashed entries at the root, and that is the port's doing rather
than a choice here: the firmware overwrites an entry's ``parent`` with the literal
``"trash"``, so ``DeviceDocument.parent_uuid`` "never names a phantom folder" and the
original location is simply gone. Excluding the trash, symmetrically, can orphan a live
document whose folder was trashed -- it is reported as unrooted, not silently promoted.

Ordering is the port's, not this module's
-----------------------------------------
Every tuple below is in the order the transport enumerated. Sorting by name, by date, or
folders-before-documents is a rendering decision, and a use case that sorted would force the
CLI to re-sort for the other view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import Degradation, DegradationKind
from rmspec.domain.ports.device import DeviceDocument, DeviceFolder

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from rmspec.domain.ports.device import DeviceCatalog

__all__ = ["CatalogFolder", "ListDocuments", "ListDocumentsRequest", "ListDocumentsResult"]

_UNIDENTIFIED: Final = "<entry with no recoverable identifier>"
"""Subject for a skipped entry whose ``uuid`` the transport could not recover.

Spelled identically to :mod:`rmspec.app.resolve`'s constant of the same name, because it
reports the same fact about the same port field, and ``test_app_catalog`` asserts the two
stay equal. Two use cases naming one condition two ways is how a user learns that
``rmspec ls`` and ``rmspec render`` disagree about the same broken entry.
"""


class CatalogFolder(BaseModel, frozen=True, extra="forbid"):
    """One folder, what is directly inside it, and what is directly under it.

    Recursive, and deliberately shallow in what it adds: the folder is the port's own
    :class:`~rmspec.domain.ports.device.DeviceFolder`, the documents are the port's own
    :class:`~rmspec.domain.ports.device.DeviceDocument` values, and this type contributes
    only the containment edge the port left implicit in ``parent_uuid``.

    A node reachable from here is rooted by construction: an unrooted folder never appears
    in a subtree, because the walk that decided it was unrooted is the walk that would have
    had to place it.
    """

    folder: DeviceFolder
    """The folder itself, exactly as the transport reported it."""

    documents: tuple[DeviceDocument, ...]
    """The documents directly in this folder, in the order the transport enumerated."""

    folders: tuple[CatalogFolder, ...]
    """The folders directly under this one, each with its own contents."""


class ListDocumentsRequest(BaseModel, frozen=True, extra="forbid"):
    """What the caller wants enumerated. Not how it will be displayed."""

    include_trashed: bool = False
    """Report entries the user deleted on the tablet.

    ``False`` -- the default, and what every legacy path did -- reports the library. ``True``
    reports the trash as well, at the root, because the firmware does not preserve a trashed
    entry's original parent. A no-op over the USB web API, which cannot see the trash at all;
    see this module's docstring for why that does not make it dead code.
    """


class ListDocumentsResult(BaseModel, frozen=True, extra="forbid"):
    """One enumeration, as a flat view and a hierarchical one over the same values.

    The two views hold the same :class:`~rmspec.domain.ports.device.DeviceDocument` objects:
    :attr:`documents` is every document this enumeration represented, and
    :attr:`root_documents`, the subtrees, and :attr:`unrooted_documents` partition it. A CLI
    rendering ``ls`` reads the flat tuple; a CLI rendering ``ls --tree`` walks the folders;
    neither has to reconstruct what the other was given.

    No field has a default, following the reason
    :class:`~rmspec.domain.ports.device.DeviceListing` gives for the same decision: a caller
    cannot construct this without stating what could not be placed, so an unrooted entry
    cannot be forgotten at a construction site and default to "the library is a clean tree".
    """

    documents: tuple[DeviceDocument, ...]
    """Every document this enumeration represented, in transport order, trash filter applied.

    Flat and complete, wherever in the tree the document sits. This is what a non-tree
    listing renders, so producing it does not require walking the hierarchy.
    """

    root_documents: tuple[DeviceDocument, ...]
    """The documents at the library root, in transport order."""

    root_folders: tuple[CatalogFolder, ...]
    """The folders at the library root, each carrying its own subtree."""

    unrooted_folders: tuple[DeviceFolder, ...]
    """Folders whose ancestor chain does not reach the root, in transport order.

    Either the chain names a parent this listing does not hold, or it cycles. Reported here
    rather than placed at the root, which is what the legacy walkers did and what made them
    non-terminating.
    """

    unrooted_documents: tuple[DeviceDocument, ...]
    """Documents whose parent folder is missing, trashed-out, or unrooted itself.

    Still reported, because dropping a document is the failure this whole result shape
    exists to avoid; just not claimed to be at the root.
    """

    degradations: tuple[Degradation, ...]
    """One ``CATALOG_ENTRY_SKIPPED`` per entry the transport could not represent.

    An unrooted entry is *not* here: it was not omitted, and
    :class:`~rmspec.domain.errors.DegradationKind` is closed with no member meaning "the
    hierarchy could not place this". Naming one is a reviewed change to the domain rather
    than something this module may decide.
    """


def _rooted_folders(folders: Mapping[str, DeviceFolder]) -> frozenset[str]:
    """Return the identifiers of every folder whose ancestor chain reaches the root.

    Walks upward from each folder with a visited set. The set is what makes this total: two
    folders naming each other as parent cycle forever without it, which is how the legacy
    walkers hung rather than failed.

    A chain that ends at ``None`` roots every folder on it. A chain that revisits an
    identifier, or names a parent this listing does not hold, roots none of them -- the
    entries below a missing folder have no known place either.

    Parameters
    ----------
    folders
        Every folder this enumeration will represent, by identifier.

    Returns
    -------
    frozenset[str]
        The identifiers that can be placed in a tree.
    """
    rooted: set[str] = set()
    for start in folders:
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = start
        while current is not None and current not in seen and current in folders:
            seen.add(current)
            chain.append(current)
            current = folders[current].parent_uuid
        if current is None:
            rooted.update(chain)
    return frozenset(rooted)


def _grouped[T](entries: Iterable[tuple[str | None, T]], /) -> dict[str, list[T]]:
    """Group entries by the parent identifier each one names, dropping the rootless ones.

    Parameters
    ----------
    entries
        ``(parent_uuid, entry)`` pairs, in transport order.

    Returns
    -------
    dict[str, list[T]]
        Entries by parent identifier, each list in transport order. Entries whose parent is
        ``None`` are absent, because they belong to the root rather than to a folder.
    """
    grouped: dict[str, list[T]] = {}
    for parent, entry in entries:
        if parent is not None:
            grouped.setdefault(parent, []).append(entry)
    return grouped


def _subtree(
    folder: DeviceFolder,
    /,
    *,
    folders_by_parent: Mapping[str, list[DeviceFolder]],
    documents_by_parent: Mapping[str, list[DeviceDocument]],
) -> CatalogFolder:
    """Build one folder's node, and every node under it.

    Recursion terminates without a visited set of its own, and the argument is worth stating
    because ``folders_by_parent`` is *not* pre-filtered to the rooted folders. Descent starts
    at a folder that names no parent, which is rooted by definition; a child of a rooted
    folder is itself rooted, since its chain is the parent's chain with one link in front. So
    everything reachable here is rooted, every folder names exactly one parent, and the
    reachable folders therefore form a forest. A cycle is unreachable from any root, which is
    the same fact :func:`_rooted_folders` reports as unrootedness.

    Parameters
    ----------
    folder
        The folder to build a node for.
    folders_by_parent
        Child folders by parent identifier, in transport order.
    documents_by_parent
        Documents by parent identifier, in transport order.

    Returns
    -------
    CatalogFolder
        The folder, its documents, and its subtrees.
    """
    return CatalogFolder(
        folder=folder,
        documents=tuple(documents_by_parent.get(folder.uuid, ())),
        folders=tuple(
            _subtree(
                child,
                folders_by_parent=folders_by_parent,
                documents_by_parent=documents_by_parent,
            )
            for child in folders_by_parent.get(folder.uuid, ())
        ),
    )


class ListDocuments:
    """Enumerate the library once and return it as a placed hierarchy.

    Pure policy over records. It reads a
    :class:`~rmspec.domain.ports.device.DeviceCatalog` once per call -- one command is one
    handshake -- and decides; it opens no file, writes to no stream, sorts nothing, and holds
    no state between calls.

    Notes
    -----
    Both renderings come from one call, which is the whole point of replacing three
    walkers::

        result = lister.list_documents(ListDocumentsRequest())
        for document in result.documents:  # ``ls``
            print(document.name)
        for node in result.root_folders:   # ``ls --tree``
            render(node, indent=0)
    """

    def __init__(self, *, catalog: DeviceCatalog) -> None:
        self._catalog = catalog

    def list_documents(self, request: ListDocumentsRequest, /) -> ListDocumentsResult:
        """Enumerate the library and place every entry the listing represented.

        Parameters
        ----------
        request
            Whether deleted entries are part of this enumeration.

        Returns
        -------
        ListDocumentsResult
            The flat document tuple, the rooted hierarchy, whatever could not be placed, and
            one degradation per entry the transport could not represent.

        Raises
        ------
        DeviceUnreachable
            Raised by the catalog and never degraded here: an unreachable tablet must not
            enumerate as an empty library.
        DeviceAuthFailed
            Raised by the catalog.
        DeviceProtocolError
            Raised by the catalog.
        """
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
        keep = request.include_trashed
        documents = tuple(doc for doc in listing.documents if keep or not doc.trashed)
        folders = tuple(folder for folder in listing.folders if keep or not folder.trashed)
        rooted = _rooted_folders({folder.uuid: folder for folder in folders})
        folders_by_parent = _grouped((folder.parent_uuid, folder) for folder in folders)
        placed_documents = _grouped((doc.parent_uuid, doc) for doc in documents)
        return ListDocumentsResult(
            documents=documents,
            root_documents=tuple(doc for doc in documents if doc.parent_uuid is None),
            root_folders=tuple(
                _subtree(
                    folder,
                    folders_by_parent=folders_by_parent,
                    documents_by_parent=placed_documents,
                )
                for folder in folders
                if folder.parent_uuid is None
            ),
            unrooted_folders=tuple(folder for folder in folders if folder.uuid not in rooted),
            unrooted_documents=tuple(
                doc
                for doc in documents
                if doc.parent_uuid is not None and doc.parent_uuid not in rooted
            ),
            degradations=log.frozen(),
        )
