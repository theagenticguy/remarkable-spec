"""Parsers for reMarkable on-disk formats: v6 ``.rm``, ``.metadata``, ``.content``, ``.pagedata``.

This is the only distribution in the workspace that imports ``rmscene``, and no
``rmscene`` type appears in any signature exported from here -- the two halves of the
same rule, both asserted by tests rather than written down. The one place the parser is
bound is :class:`rmspec.formats.scene_codec.SceneCodec`; everything else in the
workspace reaches a parsed page through
``rmspec.domain.ports.formats.DocumentRepository``.

Composition surface
-------------------
Four names, because that is what a container needs to build:

:class:`SceneCodec`
    The ``PageCodec`` adapter. Takes no arguments.
:class:`AppendOnlySceneWriter`
    The ``SceneAppender`` adapter: ink onto a page that already exists. Takes no arguments.
    Named for its design decision -- it never re-encodes the bytes it was handed, so the
    original is a literal prefix of what it returns. A container that only reads pages does
    not resolve it.
:class:`XochitlDocumentRepository`
    The ``DocumentRepository`` adapter over a local xochitl root. Takes ``root`` and a
    ``PageCodec``, both keyword-only.
:func:`fingerprint_bytes`
    The unsalted SHA-256 that keys every cached OCR and diagram row already on disk.
    Exported because it is a compatibility constraint, not an implementation detail.

The two supporting modules -- :mod:`rmspec.formats.layout` for the on-disk names and
:mod:`rmspec.formats.page_index` for the ``.content`` page walk -- stay addressed by
module path. They are the adapter's own vocabulary, and re-exporting them here would
invite a caller to sequence the xochitl layout itself, which is the coarse repository's
whole reason for existing.

Importing this package has no side effects. In particular it does not touch the
``rmscene`` logger: that suppression is scoped to one decode and restored afterwards.
A missing dependency is a composition-time failure raised while the container is built
(``MissingDependencyError``), never an ``ImportError`` from inside a method body -- which
is what the legacy tree's 27 function-local imports were working around.
"""

from __future__ import annotations

from rmspec.formats.fingerprint import fingerprint_bytes
from rmspec.formats.repository import XochitlDocumentRepository
from rmspec.formats.scene_codec import AppendOnlySceneWriter, SceneCodec

__all__ = [
    "AppendOnlySceneWriter",
    "SceneCodec",
    "XochitlDocumentRepository",
    "fingerprint_bytes",
]
