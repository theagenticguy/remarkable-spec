"""Finding 1: a zero-byte ``.rm`` is a page with no ink, not a parse failure.

62 of the 92 ``.rm`` files in the reference corpus are zero bytes -- the stubs the
firmware writes for the unannotated pages of a PDF-backed document. Legacy
``parse_rm_file`` raised a bare ``EOFError`` with an empty message on every one, so two
thirds of a real corpus crashed a naive page loop and the exception said nothing about
why.

The fix is not "swallow the error": it is that the stub, an absent artifact and a
truncated artifact are three *different* values a caller can act on. This module is the
one place that distinction is asserted, so it cannot be diluted by being spread across
the codec and repository suites.

The hard half of that claim is the truncated artifact, and it is the half that was
broken. ``rmscene``'s block reader treats an end-of-file inside the 4-byte block-length
field as a clean end of iteration, so a cut landing on -- or within three bytes of -- a
block boundary raised nothing, produced no ``UnreadableBlock``, and returned the stub's
exact value: ``PageContent()``, ``is_blank`` true, zero defects. Roughly one arbitrary
cut point in ten, and *every* cut a writer that flushes whole blocks makes. The suite
below therefore walks every block boundary of a real one-stroke file and its next three
bytes, and asserts only what matters to a caller: the result is never the stub's value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import formats_fixtures as ff
import pytest

from rmspec.domain.errors import CorruptPageData
from rmspec.domain.models import PageContent, PageDefectCode, PageId
from rmspec.domain.ports.formats import ABSENT_ARTIFACT_FINGERPRINT
from rmspec.formats import SceneCodec, fingerprint_bytes

if TYPE_CHECKING:
    from pathlib import Path

SHA256_OF_NOTHING = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
"""The digest of zero bytes, spelled out because it is a persisted cache key."""

STUB = ff.PageSpec(uuid="page-stub", state=ff.PageState.STUB)
ABSENT = ff.PageSpec(uuid="page-absent", state=ff.PageState.ABSENT)
BROKEN = ff.PageSpec(uuid="page-broken", state=ff.PageState.UNDECODABLE)
INKED = ff.PageSpec(uuid="page-inked", state=ff.PageState.INKED)

PDF = ff.DocumentSpec(uuid="pdf-doc", pages=(STUB, ABSENT, BROKEN, INKED))


def test_the_codec_reads_a_stub_as_a_blank_page_and_never_raises():
    content = SceneCodec().decode_page(b"", "page-stub")

    assert content == PageContent(), "empty layers with no defects is the domain's own 'blank'"


def test_the_codec_still_refuses_a_non_empty_artifact_that_will_not_decode():
    """Empty and corrupt must not collapse into one another."""
    with pytest.raises(CorruptPageData):
        SceneCodec().decode_page(b"\x00", "page-broken")


def test_the_three_states_are_three_different_pages(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, PDF)

    pages = {page.page_id.uuid: page for page in repository.load(PDF.doc_id).pages}

    stub = pages["page-stub"]
    assert stub.is_readable, "the file exists and decoded; there is simply no ink"
    assert stub.content is not None
    assert stub.content.is_blank
    assert stub.all_defects == ()

    absent = pages["page-absent"]
    assert not absent.is_readable
    assert [defect.code for defect in absent.all_defects] == [PageDefectCode.ARTIFACT_ABSENT]

    broken = pages["page-broken"]
    assert not broken.is_readable
    assert [defect.code for defect in broken.all_defects] == [PageDefectCode.CONTENT_UNDECODABLE]


def test_the_three_states_have_three_different_cache_keys(tmp_path: Path):
    """Aliasing the stub to the sentinel would make an annotated page look cached.

    The sentinel is defined as staying valid for as long as the page has no artifact. A
    stub *has* one, so it stays on the content-hash path and its key changes the instant
    ink is added -- which is what a cached OCR row for that page depends on.
    """
    repository = ff.build_xochitl(tmp_path, PDF)
    fingerprints = repository.page_fingerprints(PDF.doc_id)
    by_uuid = {page_id.uuid: value for page_id, value in fingerprints.items()}

    assert by_uuid["page-stub"] == SHA256_OF_NOTHING
    assert by_uuid["page-absent"] == ABSENT_ARTIFACT_FINGERPRINT
    assert len(set(by_uuid.values())) == 4, "four pages, four distinct keys"


def test_a_stub_that_gets_written_on_changes_its_cache_key(tmp_path: Path):
    repository = ff.build_xochitl(tmp_path, PDF)
    before = repository.page_fingerprint(PDF.doc_id, PageId(uuid="page-stub"))

    (tmp_path / PDF.uuid / "page-stub.rm").write_bytes(ff.inked_scene())
    after = repository.page_fingerprint(PDF.doc_id, PageId(uuid="page-stub"))

    assert before == SHA256_OF_NOTHING
    assert after != before
    assert after == fingerprint_bytes(ff.inked_scene())


def test_a_document_that_is_mostly_stubs_loads_with_nothing_raised(tmp_path: Path):
    """The 62-of-92 shape, which is where the legacy loop died once per page."""
    pages = tuple(
        ff.PageSpec(
            uuid=f"page-{index:03d}",
            state=ff.PageState.INKED if index % 3 == 0 else ff.PageState.STUB,
        )
        for index in range(92)
    )
    spec = ff.DocumentSpec(uuid="big-pdf", pages=pages)
    repository = ff.build_xochitl(tmp_path, spec)

    document = repository.load(spec.doc_id)

    assert len(document.pages) == 92
    assert document.defective_pages == (), "a stub is not a defect"
    assert sum(page.stroke_count for page in document.pages) == 31


def test_the_stub_is_not_reported_as_an_absent_artifact(tmp_path: Path):
    """The two are one branch in some designs; keeping them apart is the whole finding."""
    repository = ff.build_xochitl(tmp_path, PDF)

    stub = repository.load_page(PDF.doc_id, PageId(uuid="page-stub"))

    assert PageDefectCode.ARTIFACT_ABSENT not in {defect.code for defect in stub.all_defects}


# ─────────────────── the third value: a truncated artifact ───────────────────

BLOCK_BOUNDARIES = (43, 76, 91, 124, 160, 184, 227, 262)
"""Every frame boundary of :func:`formats_fixtures.inked_scene`, header first.

Spelled out rather than computed, because a cut *on* one of these is the case
``rmscene`` accepts in silence: the walk below re-derives them from the bytes and
asserts they are these, so the constant cannot rot without the suite saying so.
"""


def test_the_spelled_out_block_boundaries_are_the_files_real_ones():
    raw = ff.inked_scene()
    walked = [43]
    while walked[-1] < len(raw):
        position = walked[-1]
        walked.append(position + 8 + int.from_bytes(raw[position : position + 4], "little"))

    assert tuple(walked[:-1]) == BLOCK_BOUNDARIES
    assert walked[-1] == len(raw), "the whole fixture is framed exactly, so cuts are the only lie"


@pytest.mark.parametrize("boundary", BLOCK_BOUNDARIES)
@pytest.mark.parametrize("overshoot", [0, 1, 2, 3])
def test_a_cut_at_or_just_past_a_block_boundary_is_never_the_stubs_value(
    boundary: int, overshoot: int
):
    """The confirmed defect, over all 32 offsets it covered.

    Either outcome is acceptable to a caller -- a refusal, or content carrying a defect.
    What is not acceptable is the stub's value, because a CLI then reports "0 defective
    pages" while ink is missing, a renderer emits a blank SVG, and an OCR result of "no
    text" is cached under the truncated file's own content hash as authoritative.
    """
    raw = ff.inked_scene()[: boundary + overshoot]
    assert raw, "a cut that produced nothing would be testing the stub instead"

    outcome = decoded_or_refusal(raw)

    if isinstance(outcome, CorruptPageData):
        assert outcome.page_uuid == "page-cut"
        assert outcome.detail
    else:
        assert outcome != PageContent(), "indistinguishable from a zero-byte stub"


def decoded_or_refusal(raw: bytes) -> PageContent | CorruptPageData:
    """Decode one artifact, returning a refusal as a value rather than raising it.

    Both outcomes are acceptable for a truncated artifact, so the test above has to be
    able to branch on which one happened. Returning the error keeps the assertions out of
    an ``except`` block, where they would be unreachable if no exception were raised.

    Parameters
    ----------
    raw
        The artifact's bytes.

    Returns
    -------
    PageContent | CorruptPageData
        What the codec produced, or the refusal it raised.
    """
    try:
        return SceneCodec().decode_page(raw, "page-cut")
    except CorruptPageData as err:
        return err


def test_a_header_with_no_blocks_at_all_is_refused_rather_than_called_blank():
    """A page the tablet wrote is at least 160 bytes; 43 is a flushed header."""
    with pytest.raises(CorruptPageData, match="partial write"):
        SceneCodec().decode_page(ff.VALID_HEADER, "page-cut")


def test_a_truncated_tail_is_refused_even_though_the_parser_accepted_it():
    with pytest.raises(CorruptPageData, match="framing ends at byte 227 of 230"):
        SceneCodec().decode_page(ff.inked_scene()[:230], "page-cut")


def test_a_declared_block_length_that_runs_off_the_end_is_refused():
    """Framing overshoots the file, and the parser is the one that noticed first."""
    with pytest.raises(CorruptPageData):
        SceneCodec().decode_page(ff.TRUNCATED_SCENE, "page-cut")


def test_a_file_that_stops_inside_the_preamble_is_refused():
    with pytest.raises(CorruptPageData, match="no page info block"):
        SceneCodec().decode_page(ff.inked_scene()[:91], "page-cut")


def test_a_page_whose_artifact_is_a_partial_write_is_a_defective_page(tmp_path: Path):
    """End to end: the repository must not report this document as fully readable."""
    spec = ff.DocumentSpec(uuid="cut-doc", pages=(STUB, ff.PageSpec(uuid="page-cut")))
    repository = ff.build_xochitl(tmp_path, spec)
    (tmp_path / spec.uuid / "page-cut.rm").write_bytes(ff.inked_scene()[:43])

    document = repository.load(spec.doc_id)
    pages = {page.page_id.uuid: page for page in document.pages}

    assert [page.page_id.uuid for page in document.defective_pages] == ["page-cut"]
    assert [defect.code for defect in pages["page-cut"].all_defects] == [
        PageDefectCode.CONTENT_UNDECODABLE
    ]
    assert pages["page-stub"].all_defects == (), "and the real stub is still not a defect"
    assert pages["page-stub"] != pages["page-cut"], "two byte-identical values was the bug"
