"""The composition root's backend probe, including both of its negative branches."""

from __future__ import annotations

import pytest

from rmspec.domain.errors import MissingDependencyError
from rmspec.export import _cairo, _pymupdf
from rmspec.export.availability import EXTRA, FEATURE, require_backends


def test_both_backends_pass_on_a_working_install() -> None:
    require_backends()


def test_the_extra_named_is_the_one_a_user_can_install() -> None:
    assert EXTRA == "render"
    assert FEATURE


def test_a_cairo_that_imported_but_cannot_render_is_a_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is the shape of a real failure, not a hypothetical: cairocffi dlopens during import
    # and raises OSError, which is not an ImportError, so a check that only caught ImportError
    # would let a broken libcairo through and surface as a raw traceback from a command body.
    def explode(*_args: object, **_kwargs: object) -> bytes:
        msg = "no library called cairo-2 was found"
        raise OSError(msg)

    monkeypatch.setattr(_cairo, "render_png", explode)
    with pytest.raises(MissingDependencyError) as caught:
        require_backends()
    assert caught.value.package == "cairosvg"
    assert caught.value.extra == "render"
    assert "uv sync --extra render" in (caught.value.remediation or "")


def test_a_cairo_error_is_also_a_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> bytes:
        msg = "surface died"
        raise _cairo.CairoError(msg)

    monkeypatch.setattr(_cairo, "render_png", explode)
    with pytest.raises(MissingDependencyError) as caught:
        require_backends()
    assert caught.value.package == "cairosvg"


def test_a_mupdf_core_that_cannot_build_a_document_is_a_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode() -> int:
        msg = "core not loaded"
        raise _pymupdf.PdfBackendError(msg)

    monkeypatch.setattr(_pymupdf, "probe_backend", explode)
    with pytest.raises(MissingDependencyError) as caught:
        require_backends()
    assert caught.value.package == "pymupdf"


def test_the_probe_really_builds_a_one_page_document() -> None:
    assert _pymupdf.probe_backend() == 1


def test_the_probe_reports_a_broken_core_rather_than_leaking_the_backend_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        msg = "mupdf is gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(_pymupdf.pymupdf, "open", explode)
    with pytest.raises(_pymupdf.PdfBackendError, match="backend probe failed"):
        _pymupdf.probe_backend()
