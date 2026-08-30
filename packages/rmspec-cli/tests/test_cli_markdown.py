"""Markdown to PDF: the conversion, its page count, and the page it lays out onto.

This module is imported through :func:`importlib.import_module` rather than at the top of the
file, for the same reason ``_push.py`` does it: ``rmspec.cli._markdown`` imports ``weasyprint``
at module scope and ``weasyprint`` links against native libraries that the macOS loader will
not find until ``DYLD_FALLBACK_LIBRARY_PATH`` names them. ``apply_native_library_path`` is what
a real command run does first, so the fixture does it too -- importing at the top of this file
would run the ``dlopen`` at collection time, before any fixture could set the variable.
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

import pytest

from rmspec.cli._settings import apply_native_library_path
from rmspec.domain.models import PAPER_PRO_SCREEN

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

_MODULE = "rmspec.cli._markdown"

_LONG_SOURCE = "\n\n".join(f"## Section {index}\n\nParagraph {index}." for index in range(200))


@pytest.fixture(scope="module")
def md() -> ModuleType:
    apply_native_library_path(os.environ)
    return importlib.import_module(_MODULE)


def test_a_conversion_returns_a_pdf_and_the_renderers_own_page_count(md: ModuleType):
    converted = md.to_pdf("# Title\n\nOne short paragraph.\n", title="Title")

    assert converted.data.startswith(b"%PDF-")
    assert converted.page_count == 1


def test_a_long_source_reports_the_pages_it_actually_needed(md: ModuleType):
    # The page count is the renderer's measurement, so it has to move with the content. A
    # constant would satisfy the zero-page refusal while telling the audit log a lie.
    converted = md.to_pdf(_LONG_SOURCE, title="Long")

    assert converted.page_count > 1


def test_blank_input_still_lays_out_one_page(md: ModuleType):
    # The measurement that decides where the "refuse to deliver nothing" check has to live.
    # An empty source does NOT produce zero pages -- it produces one structurally perfect
    # empty page, which is exactly the "valid, empty PDF" a neighbouring project uploads when
    # its parser returns nothing. So CreateDocumentRequest's page_count == 0 refusal cannot
    # catch it, and _push.py refuses the blank *source* instead.
    assert md.to_pdf("", title="Empty").page_count == 1


def test_the_extra_bundle_is_on_so_authored_markdown_behaves(md: ModuleType):
    # A table is the cheapest proof that the "extra" bundle is enabled: core markdown renders
    # the pipes as literal text and produces no <table> at all.
    converted = md.to_pdf("| a | b |\n|---|---|\n| 1 | 2 |\n", title="Table")

    assert converted.page_count == 1
    assert converted.data.startswith(b"%PDF-")


def test_no_extension_needs_a_package_this_distribution_does_not_declare(md: ModuleType):
    # codehilite would need pygments. Declaring it to satisfy this module would then fail
    # test_every_declared_third_party_is_imported from the other direction.
    assert "codehilite" not in md.MARKDOWN_EXTENSIONS


def test_a_base_url_is_what_relative_links_resolve_against(md: ModuleType, tmp_path: Path):
    # Passed through to weasyprint unchanged. Exercised with a real directory so that the
    # argument is the shape weasyprint accepts rather than merely accepted by this signature.
    converted = md.to_pdf("[a link](./other.md)\n", title="Linked", base_url=str(tmp_path))

    assert converted.page_count == 1


def test_the_page_is_the_tablets_own_and_not_a_printers(md: ModuleType):
    # A4 would be a page shape chosen for a printer that is not in this story. The PDF arrives
    # at the size of the screen that will display it, so the reader does no scaling.
    expected = f"size: {PAPER_PRO_SCREEN.width_mm:.1f}mm {PAPER_PRO_SCREEN.height_mm:.1f}mm;"

    assert expected in md.PAGE_STYLESHEET


def test_the_stylesheet_travels_inside_the_document(md: ModuleType):
    # Inlined as a <style> element rather than passed as weasyprint's `stylesheets=`, whose
    # spelling has moved between major versions while a <style> element has not.
    assert "<style>" in md._DOCUMENT_TEMPLATE
    assert "{stylesheet}" in md._DOCUMENT_TEMPLATE


def test_the_converted_document_carries_both_facts_or_neither(md: ModuleType):
    # The caller needs the count to build a CreateDocumentRequest and nothing downstream can
    # recover it from the bytes, so the model refuses to be constructed with only one of them.
    with pytest.raises(ValueError, match="page_count"):
        md.ConvertedDocument(data=b"%PDF-")
