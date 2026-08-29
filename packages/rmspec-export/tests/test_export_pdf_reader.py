"""The PDF reader: exact fit-to-box sizing, one text entry per page, three typed failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from export_support import (
    LEGACY_RM2_BOX_PT,
    PAPER_PRO_BOX_PT,
    US_LETTER_BOX_PT,
    build_pdf,
    check_reader,
    png_header_size,
)

from rmspec.domain.errors import (
    PdfPageOutOfRange,
    PdfSourceUnreadable,
    RasterizationFailed,
)
from rmspec.domain.ports.export import PdfSourceRef, PixelSize
from rmspec.export import _pillow, _pymupdf
from rmspec.export.pdf_reader import PyMuPdfPageReader

if TYPE_CHECKING:
    from pathlib import Path

    from rmspec.export.sources import PdfSourceRegistry

#: pymupdf.PDF_ENCRYPT_AES_256. Spelled as its value because the constant is re-exported
#: dynamically and ty cannot resolve it as an attribute of the package.
AES_256_ENCRYPTION = 5

#: An identifier no registry ever minted. Bound to a name rather than repeated inline.
UNMINTED_REFERENCE = "never-minted"

PAPER_PRO_BOX = PixelSize(width_px=1620, height_px=2160)
RM2_BOX = PixelSize(width_px=1404, height_px=1872)


def _reader(registry: PdfSourceRegistry) -> PyMuPdfPageReader:
    return PyMuPdfPageReader(registry=registry)


def _register(registry: PdfSourceRegistry, data: bytes) -> PdfSourceRef:
    return registry.for_bytes(data)


def test_the_real_adapter_satisfies_the_contract(registry: PdfSourceRegistry) -> None:
    data = build_pdf(
        [LEGACY_RM2_BOX_PT, US_LETTER_BOX_PT, PAPER_PRO_BOX_PT],
        texts=["first page", "", "third page"],
    )
    check_reader(
        _reader(registry),
        source=_register(registry, data),
        expected_pages=3,
        box=PAPER_PRO_BOX,
        oversample_values=(1, 2),
    )


def test_page_texts_returns_a_placeholder_for_a_text_free_page(
    registry: PdfSourceRegistry,
) -> None:
    # A 92-page loop must never have a hole: an adapter that skipped an undecodable page would
    # shift every later page's text one slot up.
    data = build_pdf([US_LETTER_BOX_PT] * 3, texts=["a", "", "c"])
    texts = _reader(registry).page_texts(_register(registry, data))
    assert len(texts) == 3
    assert texts[1].strip() == ""
    assert "a" in texts[0]
    assert "c" in texts[2]


@pytest.mark.parametrize("oversample", [1, 2, 3])
def test_pixel_size_equals_the_domain_formula_exactly(
    registry: PdfSourceRegistry,
    oversample: int,
) -> None:
    data = build_pdf([US_LETTER_BOX_PT])
    background = _reader(registry).rasterize_page(
        _register(registry, data),
        page_index=0,
        box=PAPER_PRO_BOX,
        oversample=oversample,
    )
    expected = PixelSize.fit_within(background.page_size, PAPER_PRO_BOX, oversample=oversample)
    assert background.pixel_size == expected
    assert png_header_size(background.data) == expected


def test_at_oversample_two_the_raster_exceeds_the_box(registry: PdfSourceRegistry) -> None:
    # Reading "fits inside box" as scale-to-box produces a half-size background that registers
    # plausibly and wrongly, and nothing else in the suite would notice.
    data = build_pdf([US_LETTER_BOX_PT])
    background = _reader(registry).rasterize_page(
        _register(registry, data),
        page_index=0,
        box=RM2_BOX,
        oversample=2,
    )
    assert background.pixel_size.width_px > RM2_BOX.width_px
    assert background.pixel_size.height_px > RM2_BOX.height_px


def test_a_rotated_page_reports_its_orientation_resolved_size(
    registry: PdfSourceRegistry,
) -> None:
    data = build_pdf([US_LETTER_BOX_PT], rotations=[90])
    background = _reader(registry).rasterize_page(
        _register(registry, data),
        page_index=0,
        box=PAPER_PRO_BOX,
        oversample=1,
    )
    assert background.page_size.width_mm > background.page_size.height_mm


def test_an_index_past_the_end_names_the_real_page_count(registry: PdfSourceRegistry) -> None:
    ref = _register(registry, build_pdf([US_LETTER_BOX_PT] * 2))
    with pytest.raises(PdfPageOutOfRange) as caught:
        _reader(registry).rasterize_page(ref, page_index=5, box=PAPER_PRO_BOX, oversample=1)
    assert caught.value.page_count == 2
    assert caught.value.page_index == 5


def test_a_negative_index_is_out_of_range_not_a_python_wraparound(
    registry: PdfSourceRegistry,
) -> None:
    ref = _register(registry, build_pdf([US_LETTER_BOX_PT] * 2))
    with pytest.raises(PdfPageOutOfRange):
        _reader(registry).rasterize_page(ref, page_index=-1, box=PAPER_PRO_BOX, oversample=1)


@pytest.mark.parametrize("method", ["page_count", "page_texts"])
def test_an_unknown_token_is_unreadable(registry: PdfSourceRegistry, method: str) -> None:
    reader = _reader(registry)
    with pytest.raises(PdfSourceUnreadable) as caught:
        getattr(reader, method)(PdfSourceRef(token=UNMINTED_REFERENCE))
    assert caught.value.source == UNMINTED_REFERENCE


def test_a_non_pdf_is_unreadable(registry: PdfSourceRegistry) -> None:
    ref = _register(registry, b"not a pdf at all")
    with pytest.raises(PdfSourceUnreadable):
        _reader(registry).page_count(ref)


def test_a_missing_file_is_unreadable(registry: PdfSourceRegistry, tmp_path: Path) -> None:
    ref = registry.for_path(tmp_path / "absent.pdf")
    with pytest.raises(PdfSourceUnreadable):
        _reader(registry).page_texts(ref)


def test_an_encrypted_document_is_unreadable_without_a_password_prompt(
    registry: PdfSourceRegistry,
) -> None:
    # One error for missing, corrupt and encrypted: the caller does the same thing about each,
    # and splitting encryption out would mirror one library's needs_pass flag into the domain.
    plain = build_pdf([US_LETTER_BOX_PT])
    with _pymupdf.pymupdf.open(stream=plain, filetype="pdf") as document:
        encrypted = bytes(
            document.tobytes(
                encryption=AES_256_ENCRYPTION,
                owner_pw="owner",
                user_pw="user",
            )
        )
    ref = _register(registry, encrypted)
    with pytest.raises(PdfSourceUnreadable, match="encrypted"):
        _reader(registry).page_count(ref)


def test_an_unreadable_source_during_rasterization_is_unreadable(
    registry: PdfSourceRegistry,
) -> None:
    ref = _register(registry, b"%PDF-1.7 truncated")
    with pytest.raises(PdfSourceUnreadable):
        _reader(registry).rasterize_page(ref, page_index=0, box=PAPER_PRO_BOX, oversample=1)


def test_a_zero_byte_pdf_is_unreadable_rather_than_an_empty_document(
    registry: PdfSourceRegistry,
) -> None:
    # The export-side mirror of the 62 zero-byte .rm stubs, and the opposite verdict on purpose.
    # An empty .rm stub is a page with no ink because the format says a page with no blocks is
    # empty; an empty *PDF* has no header, no trailer and no page tree, so there is no document
    # to report a count for. Both are pinned so neither answer can drift into the other.
    ref = _register(registry, b"")
    reader = _reader(registry)
    for call in (reader.page_count, reader.page_texts):
        with pytest.raises(PdfSourceUnreadable):
            call(ref)


def test_a_valid_pdf_holding_no_pages_reports_zero_rather_than_failing(
    registry: PdfSourceRegistry,
) -> None:
    # Hand-built, because pymupdf will not serialise a page-less document. A document that opens
    # and holds nothing is not a failure: the count is 0, the text tuple is empty, and page 0 is
    # out of range -- which is the error that names the real count instead of blaming the file.
    ref = _register(
        registry,
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R /Size 3 >>\n%%EOF\n",
    )
    reader = _reader(registry)
    assert reader.page_count(ref) == 0
    assert reader.page_texts(ref) == ()
    with pytest.raises(PdfPageOutOfRange) as caught:
        reader.rasterize_page(ref, page_index=0, box=PAPER_PRO_BOX, oversample=1)
    assert caught.value.page_count == 0


def test_the_backend_diagnosis_reaches_the_typed_error_instead_of_a_file_descriptor(
    registry: PdfSourceRegistry,
) -> None:
    # MuPDF's C core wrote "MuPDF error: format error: ..." straight to a descriptor, below
    # anything Python can capture, while returning a document. The core's device is off and the
    # store is drained onto the error, so the text lands where an except clause can read it.
    ref = _register(registry, b"%PDF-1.7\ngarbage that is not a cross-reference table\n")
    with pytest.raises(PdfSourceUnreadable) as caught:
        _reader(registry).page_count(ref)
    assert "mupdf said:" in caught.value.detail
    assert "\n" not in caught.value.detail, "one line, so a CLI can print it in a table cell"


def test_one_documents_diagnosis_does_not_leak_onto_the_next_documents_error(
    registry: PdfSourceRegistry,
    tmp_path: Path,
) -> None:
    # The store is process-global. Without the entry drain, the repair notes from the broken
    # document above would be appended to an unrelated later failure and send a reader hunting
    # for corruption in a file that is merely absent.
    reader = _reader(registry)
    with pytest.raises(PdfSourceUnreadable):
        reader.page_count(_register(registry, b"%PDF-1.7\nnot a cross-reference table\n"))
    with pytest.raises(PdfSourceUnreadable) as caught:
        reader.page_count(registry.for_path(tmp_path / "absent.pdf"))
    assert "cross-reference" not in caught.value.detail
    assert "xref" not in caught.value.detail


def test_a_render_failure_on_a_readable_page_is_a_rasterization_failure(
    registry: PdfSourceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = _register(registry, build_pdf([US_LETTER_BOX_PT]))

    def explode(*_args: object, **_kwargs: object) -> bytes:
        msg = "pixmap died"
        raise _pymupdf.PdfBackendError(msg)

    monkeypatch.setattr(_pymupdf, "rasterize", explode)
    with pytest.raises(RasterizationFailed) as caught:
        _reader(registry).rasterize_page(ref, page_index=0, box=PAPER_PRO_BOX, oversample=1)
    assert caught.value.backend == "pymupdf"
    assert caught.value.detail == "pixmap died"


def test_a_backend_sizing_its_own_way_is_refused(
    registry: PdfSourceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _pymupdf.rasterize

    def one_pixel_short(path: Path, *, page_index: int, target: PixelSize) -> bytes:
        shrunk = PixelSize(width_px=target.width_px - 1, height_px=target.height_px)
        return original(path, page_index=page_index, target=shrunk)

    monkeypatch.setattr(_pymupdf, "rasterize", one_pixel_short)
    ref = _register(registry, build_pdf([US_LETTER_BOX_PT]))
    with pytest.raises(RasterizationFailed, match="disagrees with PNG header"):
        _reader(registry).rasterize_page(ref, page_index=0, box=PAPER_PRO_BOX, oversample=1)


@pytest.mark.parametrize("oversample", [0, -1])
def test_a_bad_oversample_is_a_caller_bug_raised_before_the_document_is_opened(
    registry: PdfSourceRegistry,
    tmp_path: Path,
    oversample: int,
) -> None:
    # The ref points at nothing, so if the check happened after resolution this would raise
    # PdfSourceUnreadable instead and the caller bug would be reported as a broken document.
    ref = registry.for_path(tmp_path / "absent.pdf")
    with pytest.raises(ValueError, match="oversample must be at least 1"):
        _reader(registry).rasterize_page(
            ref,
            page_index=0,
            box=PAPER_PRO_BOX,
            oversample=oversample,
        )


def test_the_pillow_correction_runs_when_the_backend_rounds_differently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forced, because the anisotropic zoom hit the domain figure in every measured combination
    # and an untested correction is a correction that fails the first time a real page needs it.
    calls: list[PixelSize] = []
    original = _pillow.resize_png_exact

    def recording(data: bytes, *, target: PixelSize) -> bytes:
        calls.append(target)
        return original(data, target=target)

    monkeypatch.setattr(_pymupdf, "resize_png_exact", recording)
    monkeypatch.setattr(
        _pymupdf,
        "scale_matrix_for",
        lambda target, width_pt, height_pt: (
            (target.width_px + 1) / width_pt,
            target.height_px / height_pt,
        ),
    )
    spool = tmp_path / "doc.pdf"
    spool.write_bytes(build_pdf([US_LETTER_BOX_PT]))
    target = PixelSize(width_px=200, height_px=300)
    produced = _pymupdf.rasterize(spool, page_index=0, target=target)
    assert calls == [target]
    assert png_header_size(produced) == target
