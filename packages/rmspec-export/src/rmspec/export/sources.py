"""Mint and resolve :class:`~rmspec.domain.ports.export.PdfSourceRef` tokens.

The port inverts the dependency deliberately: no use case can honestly produce a filesystem
path, so "whichever adapter already knows where a document's bytes live" mints an opaque
token and the PDF reader adapter is the only component permitted to turn it back into a file.
This module is that adapter's half. It is *not* a new port -- nothing above imports it except
the composition root, which is where refs are minted.

Tokens are opaque, and that is enforced rather than described
-----------------------------------------------------------
A token is a bare :func:`uuid.uuid4` hex string. It is not a path, not a
``file:<path>`` scheme, and not anything a caller can parse, split or build -- the port's own
docstring forbids all three. It matters beyond tidiness:
:class:`~rmspec.domain.errors.PdfSourceUnreadable` puts the token straight into a human-
readable message, so a path-shaped token would leak the store's layout into error output.

Lifetime is the invocation's, which is what bounds the memoisation rule
---------------------------------------------------------------------
The registry is ``REQUEST``-scoped and owns any temporary it spools. :meth:`close` removes
every spooled file and forgets every token, so a ref cannot outlive the invocation that
minted it and a page count cannot be served for a document that has since been re-pulled.
That is the port's "bounded memoisation" clause with an owner instead of a hope, and it is
why :class:`~rmspec.export.pdf_reader.PyMuPdfPageReader` keeps no open document of its own.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self

from rmspec.domain.ports.export import PdfSourceRef

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["PdfSourceRegistry", "SourceMissingError", "SourceResolver"]

_SPOOL_PREFIX = "rmspec-pdf-"


class SourceResolver(Protocol):
    """The single capability the PDF reader needs from the registry: token to location.

    In-package and deliberately narrower than :class:`PdfSourceRegistry`, which also *mints*
    tokens and owns spooled temporaries. Annotating
    :class:`~rmspec.export.pdf_reader.PyMuPdfPageReader` against this instead of the concrete
    class keeps minting out of the reader's reach and makes a token source substitutable in a
    test without widening :data:`rmspec.export.__all__`.
    """

    def resolve(self, ref: PdfSourceRef) -> Path:
        """Turn a token back into a filesystem location.

        Parameters
        ----------
        ref
            The token to resolve.

        Returns
        -------
        pathlib.Path
            Where the document's bytes are.

        Raises
        ------
        SourceMissingError
            The token is unknown, or its invocation has ended.
        """
        ...


class SourceMissingError(Exception):
    """A token names no source this registry can resolve.

    Private to this package. :class:`~rmspec.export.pdf_reader.PyMuPdfPageReader` translates
    it into :class:`~rmspec.domain.errors.PdfSourceUnreadable`, which is the one error the
    port names for a missing, unreadable or already-released source.

    Attributes
    ----------
    detail
        Human-readable cause. Never contains a path for an unknown token, because for an
        unknown token there is no path to name.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PdfSourceRegistry:
    r"""Request-scoped minter and sole resolver of PDF source tokens.

    Usable as a context manager, which is the recommended wiring: the ``with`` block is the
    invocation, and leaving it removes every spooled temporary.

    Examples
    --------
    >>> with PdfSourceRegistry() as registry:
    ...     ref = registry.for_bytes(b"%PDF-1.7\n%%EOF\n")
    ...     registry.resolve(ref).is_file()
    True
    """

    def __init__(self) -> None:
        self._paths: dict[str, Path] = {}
        self._spooled: set[Path] = set()

    def __enter__(self) -> Self:
        """Enter the invocation scope.

        Returns
        -------
        PdfSourceRegistry
            This registry.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the invocation scope, releasing every spooled temporary."""
        self.close()

    def for_path(self, path: Path) -> PdfSourceRef:
        """Mint a token for a PDF that already exists on this filesystem.

        Parameters
        ----------
        path
            Location of the document. Not validated here: whether it opens is the reader's
            question, and answering it twice would give two components an opinion about
            readability.

        Returns
        -------
        PdfSourceRef
            A fresh opaque token. Two calls for the same path mint two different tokens,
            because a token identifies a registration rather than a location.
        """
        ref = PdfSourceRef(token=uuid.uuid4().hex)
        self._paths[ref.token] = path
        return ref

    def for_bytes(self, data: bytes) -> PdfSourceRef:
        """Mint a token for PDF bytes held in memory, spooling them to a temporary.

        This is the call the sync path needs: it holds a payload pulled over SSH and no
        location at all, and a use case may not write a temporary file itself.

        Parameters
        ----------
        data
            The whole PDF.

        Returns
        -------
        PdfSourceRef
            A fresh opaque token whose backing temporary this registry owns and removes on
            :meth:`close`.

        Raises
        ------
        OSError
            The temporary could not be created or written. Ownership is taken as soon as the
            file exists and *before* the write, so the failure this method is most likely to
            see -- ``ENOSPC`` while spooling a large annotated PDF, which is the payload it
            exists for -- still leaves a file :meth:`close` will remove. Registering after the
            ``with`` block made the class docstring's promise false for exactly that case: the
            handle closed, the file survived, and nothing owned it.
        """
        with tempfile.NamedTemporaryFile(
            prefix=_SPOOL_PREFIX,
            suffix=".pdf",
            delete=False,
        ) as spooled:
            path = Path(spooled.name)
            self._spooled.add(path)
            spooled.write(data)
        ref = PdfSourceRef(token=uuid.uuid4().hex)
        self._paths[ref.token] = path
        return ref

    def resolve(self, ref: PdfSourceRef) -> Path:
        """Turn a token back into a filesystem location.

        Parameters
        ----------
        ref
            The token to resolve.

        Returns
        -------
        pathlib.Path
            Where the document's bytes are.

        Raises
        ------
        SourceMissingError
            The token was never minted by this registry, or the registry has been closed.
        """
        path = self._paths.get(ref.token)
        if path is None:
            msg = "no source is registered under this reference"
            raise SourceMissingError(msg)
        return path

    def close(self) -> None:
        """Release every spooled temporary and forget every token.

        Idempotent. After it returns, :meth:`resolve` raises for every token this registry
        ever minted -- including tokens for paths it did not spool, because a ref that
        outlives its invocation is what the port's memoisation clause forbids.
        """
        for path in self._spooled:
            path.unlink(missing_ok=True)
        self._spooled.clear()
        self._paths.clear()
