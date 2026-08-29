"""The pymupdf shim: guarded import, closed documents, and no MuPDF exception escaping."""

from __future__ import annotations

import ast
import pathlib
from typing import TYPE_CHECKING

import pytest
from export_support import (
    LEGACY_RM2_BOX_PT,
    PAPER_PRO_BOX_PT,
    US_LETTER_BOX_PT,
    build_pdf,
    png_header_size,
)

from rmspec.domain.ports.export import PixelSize
from rmspec.export import _pymupdf
from rmspec.export._geometry import millimetres_from_points

if TYPE_CHECKING:
    from pathlib import Path


def _spool(tmp_path: Path, data: bytes, stem: str = "doc") -> Path:
    target = tmp_path / f"{stem}.pdf"
    target.write_bytes(data)
    return target


def test_the_backend_is_named_for_its_errors() -> None:
    assert _pymupdf.BACKEND == "pymupdf"


def test_the_only_pymupdf_import_sits_inside_a_warning_guard() -> None:
    # Not a style preference. pymupdf 1.27.1's SWIG bindings emit a DeprecationWarning during C
    # type initialisation, and escalating it segfaults the interpreter rather than raising --
    # measured, `python -W error -c "import pymupdf"` exits 139 while the same interpreter with
    # this guard exits 0. The workspace runs pytest with filterwarnings = ["error"], so an
    # unguarded module-scope import would take down collection, which reports a bogus coverage
    # number instead of a red build. Asserted structurally because the failure mode cannot be
    # provoked in-process: once pymupdf is imported, re-importing it emits nothing.
    source = pathlib.Path(_pymupdf.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=_pymupdf.__file__)
    guarded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        calls = [
            item.context_expr for item in node.items if isinstance(item.context_expr, ast.Call)
        ]
        guards = [
            call
            for call in calls
            if isinstance(call.func, ast.Attribute) and call.func.attr == "catch_warnings"
        ]
        if not guards:
            continue
        guarded.extend(
            statement.lineno
            for statement in ast.walk(node)
            if isinstance(statement, ast.Import)
            and any(alias.name == "pymupdf" for alias in statement.names)
        )
    every_import = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Import) and any(alias.name == "pymupdf" for alias in node.names)
    ]
    assert every_import, "the shim no longer imports pymupdf at all"
    assert guarded == every_import, (
        f"pymupdf is imported outside a warnings.catch_warnings block at lines "
        f"{sorted(set(every_import) - set(guarded))}"
    )


def test_page_count_and_sizes_agree_with_what_was_built(tmp_path: Path) -> None:
    boxes = [LEGACY_RM2_BOX_PT, US_LETTER_BOX_PT, PAPER_PRO_BOX_PT]
    path = _spool(tmp_path, build_pdf(boxes))
    assert _pymupdf.page_count(path) == 3
    sizes = _pymupdf.page_sizes(path)
    assert len(sizes) == 3
    for size, (width_pt, height_pt) in zip(sizes, boxes, strict=True):
        assert size.width_mm == pytest.approx(millimetres_from_points(width_pt))
        assert size.height_mm == pytest.approx(millimetres_from_points(height_pt))


def test_a_rotated_page_reports_the_rotated_rect_not_the_media_box(tmp_path: Path) -> None:
    path = _spool(tmp_path, build_pdf([US_LETTER_BOX_PT], rotations=[90]))
    size = _pymupdf.page_sizes(path)[0]
    assert size.width_mm == pytest.approx(millimetres_from_points(US_LETTER_BOX_PT[1]))
    assert size.height_mm == pytest.approx(millimetres_from_points(US_LETTER_BOX_PT[0]))


@pytest.mark.parametrize(
    "function",
    ["page_count", "page_sizes", "page_texts"],
)
def test_every_reader_function_wraps_an_unopenable_document(tmp_path: Path, function: str) -> None:
    path = _spool(tmp_path, b"definitely not a pdf")
    with pytest.raises(_pymupdf.PdfBackendError, match="could not open document"):
        getattr(_pymupdf, function)(path)


def test_a_measurement_failure_inside_an_open_document_is_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _spool(tmp_path, build_pdf([US_LETTER_BOX_PT]))

    def explode(*_args: object, **_kwargs: object) -> object:
        msg = "rect exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(_pymupdf, "physical_size_from_points", explode)
    with pytest.raises(_pymupdf.PdfBackendError, match="could not measure pages"):
        _pymupdf.page_sizes(path)


def test_a_text_extraction_failure_is_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _spool(tmp_path, build_pdf([US_LETTER_BOX_PT], texts=["hello"]))
    monkeypatch.setattr(_pymupdf, "_TEXT_MODE", "not-a-mode")
    with pytest.raises(_pymupdf.PdfBackendError, match="could not extract text"):
        _pymupdf.page_texts(path)


def test_a_blob_that_is_not_a_pdf_is_wrapped() -> None:
    with pytest.raises(_pymupdf.PdfBackendError, match="could not measure a"):
        _pymupdf.blob_page_sizes(b"nope")


def test_merge_preserves_order_and_page_boxes() -> None:
    first = build_pdf([(100.0, 200.0)])
    second = build_pdf([(300.0, 400.0)])
    merged = _pymupdf.blob_page_sizes(_pymupdf.merge((first, second)))
    assert [round(size.width_mm, 4) for size in merged] == [
        round(millimetres_from_points(100.0), 4),
        round(millimetres_from_points(300.0), 4),
    ]


def test_merge_wraps_an_unreadable_part() -> None:
    with pytest.raises(_pymupdf.PdfBackendError, match="could not merge 2 parts"):
        _pymupdf.merge((build_pdf([(10.0, 10.0)]), b"garbage"))


def test_rasterize_hits_the_requested_size_exactly(tmp_path: Path) -> None:
    path = _spool(tmp_path, build_pdf([US_LETTER_BOX_PT]))
    target = PixelSize(width_px=1620, height_px=2096)
    data = _pymupdf.rasterize(path, page_index=0, target=target)
    assert png_header_size(data) == target


def test_rasterize_wraps_a_render_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _spool(tmp_path, build_pdf([US_LETTER_BOX_PT]))
    monkeypatch.setattr(
        _pymupdf,
        "scale_matrix_for",
        lambda *_args, **_kwargs: (0.0, 0.0),
    )
    with pytest.raises(_pymupdf.PdfBackendError, match="could not rasterize page 0"):
        _pymupdf.rasterize(path, page_index=0, target=PixelSize(width_px=10, height_px=10))


def test_the_error_carries_the_backend_name_in_its_message() -> None:
    error = _pymupdf.PdfBackendError("detail here")
    assert str(error) == "pymupdf: detail here"
    assert error.detail == "detail here"


def test_reading_many_documents_does_not_leak_descriptors(tmp_path: Path) -> None:
    # MuPDF reads lazily from its backing file, so a Document that outlives the function that
    # opened it is a descriptor leak. A 92-page corpus times three reader methods is well past
    # macOS's 256 soft limit, which is why every function here opens inside a `with` block.
    paths = [
        _spool(tmp_path, build_pdf([US_LETTER_BOX_PT]), stem=f"doc-{index:03d}")
        for index in range(120)
    ]
    for path in paths:
        assert _pymupdf.page_count(path) == 1
        assert len(_pymupdf.page_texts(path)) == 1
        assert len(_pymupdf.page_sizes(path)) == 1
