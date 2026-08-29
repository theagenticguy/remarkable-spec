"""The source registry: opaque tokens, owned temporaries, and a lifetime that ends."""

from __future__ import annotations

import errno
import tempfile
from typing import TYPE_CHECKING, Self

import pytest
from export_support import US_LETTER_BOX_PT, build_pdf

from rmspec.domain.ports.export import PdfSourceRef
from rmspec.export.sources import PdfSourceRegistry, SourceMissingError

#: An identifier no registry ever minted. Bound to a name rather than repeated inline.
UNMINTED_REFERENCE = "never-minted"

if TYPE_CHECKING:
    from pathlib import Path


def test_two_registrations_of_one_path_mint_two_tokens(
    registry: PdfSourceRegistry, tmp_path: Path
) -> None:
    # A token identifies a registration, not a location, so nothing above can infer that two
    # refs name the same bytes and cache across them.
    document = tmp_path / "doc.pdf"
    document.write_bytes(build_pdf([US_LETTER_BOX_PT]))
    first = registry.for_path(document)
    second = registry.for_path(document)
    assert first.token != second.token
    assert registry.resolve(first) == registry.resolve(second) == document


def test_a_token_contains_no_trace_of_its_path(
    registry: PdfSourceRegistry, tmp_path: Path
) -> None:
    # PdfSourceUnreadable puts the token into a human-readable message, so a path-shaped token
    # would leak the store's layout into error output.
    document = tmp_path / "secret-directory" / "private-notes.pdf"
    document.parent.mkdir()
    document.write_bytes(build_pdf([US_LETTER_BOX_PT]))
    token = registry.for_path(document).token
    assert "/" not in token
    assert "\\" not in token
    assert "secret-directory" not in token
    assert "private-notes" not in token
    assert ":" not in token, "a scheme prefix would be a parseable token"
    assert token.isalnum()


def test_bytes_are_spooled_to_a_real_file(registry: PdfSourceRegistry) -> None:
    data = build_pdf([US_LETTER_BOX_PT])
    resolved = registry.resolve(registry.for_bytes(data))
    assert resolved.is_file()
    assert resolved.read_bytes() == data


class _RefusingSpool:
    """A ``NamedTemporaryFile`` stand-in that creates its file and then refuses the write.

    ``ENOSPC`` while spooling is the failure :meth:`PdfSourceRegistry.for_bytes` is most likely
    to meet, because the payload it exists for is a whole annotated PDF pulled over SSH.
    """

    def __init__(self, path: Path) -> None:
        self.name = str(path)
        path.touch()

    def __enter__(self) -> Self:
        """Enter the block, as the real wrapper does.

        Returns
        -------
        _RefusingSpool
            This stand-in.
        """
        return self

    def __exit__(self, *_exception: object) -> None:
        """Leave the block without suppressing anything."""

    def write(self, _data: bytes) -> int:
        """Refuse the bytes.

        Parameters
        ----------
        _data
            Ignored.

        Returns
        -------
        int
            Never returns.

        Raises
        ------
        OSError
            Always, with ``ENOSPC``.
        """
        raise OSError(errno.ENOSPC, "no space left on device")


def test_a_spool_that_fails_mid_write_is_still_owned_and_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The file is created by NamedTemporaryFile before a byte is written, so registering only
    # after the with-block made close()'s promise false for exactly the case that produces an
    # orphan: the handle closed, the file survived, and nothing owned it.
    orphan = tmp_path / "spool.pdf"

    def refusing(*, prefix: str, suffix: str, delete: bool) -> _RefusingSpool:
        assert prefix
        assert suffix == ".pdf"
        assert delete is False
        return _RefusingSpool(orphan)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", refusing)
    registry = PdfSourceRegistry()
    with pytest.raises(OSError, match="no space left on device"):
        registry.for_bytes(b"%PDF-1.7\n%%EOF\n")
    assert orphan.is_file(), "the temporary outlives the failed write"
    monkeypatch.undo()
    registry.close()
    assert not orphan.exists(), "and close() owns it anyway"


def test_an_unknown_token_does_not_resolve(registry: PdfSourceRegistry) -> None:
    with pytest.raises(SourceMissingError, match="no source is registered"):
        registry.resolve(PdfSourceRef(token=UNMINTED_REFERENCE))


def test_closing_removes_spooled_files_and_forgets_every_token(tmp_path: Path) -> None:
    document = tmp_path / "doc.pdf"
    document.write_bytes(build_pdf([US_LETTER_BOX_PT]))
    registry = PdfSourceRegistry()
    spooled_ref = registry.for_bytes(b"%PDF-1.7\n%%EOF\n")
    path_ref = registry.for_path(document)
    spooled = registry.resolve(spooled_ref)
    registry.close()
    assert not spooled.exists()
    assert document.exists(), "a path the registry did not spool is not deleted"
    for ref in (spooled_ref, path_ref):
        with pytest.raises(SourceMissingError):
            registry.resolve(ref)


def test_closing_is_idempotent(registry: PdfSourceRegistry) -> None:
    registry.for_bytes(b"%PDF-1.7\n%%EOF\n")
    registry.close()
    registry.close()


def test_the_context_manager_closes_on_the_way_out() -> None:
    with PdfSourceRegistry() as registry:
        ref = registry.for_bytes(b"%PDF-1.7\n%%EOF\n")
        spooled = registry.resolve(ref)
        assert spooled.is_file()
    assert not spooled.exists()
    with pytest.raises(SourceMissingError):
        registry.resolve(ref)


def test_the_context_manager_closes_even_when_the_body_raises() -> None:
    registry = PdfSourceRegistry()
    ref = registry.for_bytes(b"%PDF-1.7\n%%EOF\n")
    spooled = registry.resolve(ref)
    boom = RuntimeError("body failed")
    with pytest.raises(RuntimeError, match="body failed"), registry:
        raise boom
    assert not spooled.exists()


def test_a_registry_serves_no_stale_answer_after_a_document_is_re_pulled() -> None:
    # The port's bounded-memoisation clause with an owner: the first invocation's refs stop
    # resolving when it ends, so a two-page count cannot be served for a re-pulled three-page
    # document.
    two_pages = build_pdf([US_LETTER_BOX_PT] * 2)
    with PdfSourceRegistry() as first:
        stale_ref = first.for_bytes(two_pages)
        assert first.resolve(stale_ref).is_file()
    with PdfSourceRegistry() as second, pytest.raises(SourceMissingError):
        second.resolve(stale_ref)
